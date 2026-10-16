"""Fill-level live attribution (WS-027 phase 1, `docs/LIVE_FILL_ATTRIBUTION.md`).

Fixtures are shaped like PRODUCTION rows, because the three facts the provider rests on
were measured there (ops `ws027-fills-1`, `ws027-recon-3`):

  * an mmsell maker NO-buy order's fill is reported `side=no, action=sell` at the NO price;
  * `fills.quantity` is rounded from Kalshi's fractional `count_fp`;
  * the settlement snapshot carries `value` (YES settlement, cents), which prices a scalar.

Acceptance check 1: fill attribution reconciles to `positions.realized_pnl` on every settled
shared market — two tags, different clip sizes and fill prices, YES and NO outcomes.
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import live_attribution as la
from kalshi_bot.experiment_os import platform_commands as pc
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.enforcement import NON_BLOCKING_INTEGRITY_KINDS
from kalshi_bot.experiment_os.metrics import MetricScope, compute_metric
from kalshi_bot.experiment_os.models import ExperimentIntegrityEvent
from kalshi_bot.live.executor import settlement_realized_pnl
from kalshi_bot.models import Fill, LiveOrder, Position

UTC = timezone.utc
T0 = datetime(2026, 10, 1, tzinfo=UTC)
_SEQ = itertools.count()


def _order(s, *, tag, ticker, fills, price, side="no", action="buy", status="filled",
           at=None, fill_side=None, fill_action="sell", fee=0.0, qty=None):
    """One order with its fills. `fills` is a list of count_fp values; the integer column
    is ROUNDED exactly as reconcile's `_to_count` stores it."""
    oid = f"ord-{next(_SEQ)}"
    s.add(LiveOrder(kalshi_order_id=oid, client_order_id=f"c-{oid}", market_ticker=ticker,
                    strategy=tag, side=side, action=action, limit_price=price,
                    quantity=qty if qty is not None else int(round(sum(fills))),
                    status=status,
                    created_at=at or T0 + timedelta(hours=1)))
    for q in fills:
        s.add(Fill(kalshi_fill_id=f"f-{next(_SEQ)}", kalshi_order_id=oid, market_ticker=ticker,
                   side=fill_side or side, action=fill_action, price=price,
                   quantity=int(round(q + 1e-9)) or 1, fee=fee,
                   raw_fill_json={"count_fp": f"{q:.2f}", "side": fill_side or side},
                   filled_at=at or T0 + timedelta(hours=1)))
    return oid


def _settle(s, ticker, *, result, no_count, no_cost, value=None, fee=0.0):
    """The settlement snapshot reconcile writes, with realized P&L from the SAME formula."""
    yes_value = value if value is not None else (100 if result == "yes" else 0)
    st = {"ticker": ticker, "market_result": result, "value": yes_value,
          "revenue": int(round(no_count * (100 - yes_value))), "no_count_fp": f"{no_count:.2f}",
          "yes_count_fp": "0.00", "no_total_cost_dollars": f"{no_cost:.4f}",
          "yes_total_cost_dollars": "0", "fee_cost": f"{fee:.6f}",
          "settled_time": (T0 + timedelta(days=3)).isoformat()}
    s.add(Position(market_ticker=ticker, captured_at=T0 + timedelta(days=3), side=result,
                   quantity=0, quantity_fp=0, realized_pnl=settlement_realized_pnl(st),
                   raw_json=st))


def _shared_market(s, *, result, a_fills=(3.0,), a_px=94, b_fills=(1.0,), b_px=92,
                   ticker=None):
    ticker = ticker or f"KXRAIN-{next(_SEQ)}"
    _order(s, tag="Kmm", ticker=ticker, fills=list(a_fills), price=a_px)
    _order(s, tag="Nmm", ticker=ticker, fills=list(b_fills), price=b_px,
           at=T0 + timedelta(hours=2))
    na, nb = sum(a_fills), sum(b_fills)
    _settle(s, ticker, result=result, no_count=na + nb,
            no_cost=(na * a_px + nb * b_px) / 100.0)
    return ticker


def _scope(tags, *, start=T0, end=T0 + timedelta(days=30)):
    return MetricScope(experiment_key="x", version=1, epoch_number=1, arm_key="a",
                       deployment_kind="live", strategy_tags=tuple(tags),
                       deployment_keys=(), window_start=start, window_end=end,
                       platform_snapshot_fingerprint="f" * 64)


def _activate(s):
    """Register + activate the reviewed revision (tests stand in for the CUTOVER)."""
    svc.ensure_standard_components(s)
    return svc.register_platform_revision(s, la.COMPONENT, version=la.VERSION,
                                          fingerprint=la.metrics_fingerprint(),
                                          activate=True, force=True,
                                          force_reason="unit test")


# ---------------------------------------------------------------------------
# Acceptance check 1 — reconciliation on shared markets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("result", ["yes", "no"])
@pytest.mark.parametrize("a_fills,a_px,b_fills,b_px", [
    ((3.0,), 94, (1.0,), 92),
    ((1.5, 0.75, 0.75), 93, (2.0, 1.0), 91),   # fractional fills, different clips
    ((1.0,), 95, (3.0,), 90),
])
def test_per_tag_pnl_sums_to_the_accounts_realized_pnl(xos_session, result, a_fills, a_px,
                                                       b_fills, b_px):
    s = xos_session
    t = _shared_market(s, result=result, a_fills=a_fills, a_px=a_px, b_fills=b_fills,
                       b_px=b_px)
    s.commit()
    rec = la.reconcile_market(s, t)
    assert rec.ok is True, rec
    win = result == "no"
    exp_a = sum(a_fills) * ((100 - a_px) if win else -a_px) / 100.0
    exp_b = sum(b_fills) * ((100 - b_px) if win else -b_px) / 100.0
    assert rec.per_tag_usd["Kmm"] == pytest.approx(exp_a, abs=1e-4)
    assert rec.per_tag_usd["Nmm"] == pytest.approx(exp_b, abs=1e-4)
    assert rec.attributed_usd == pytest.approx(rec.position_realized_usd, abs=1e-4)


def test_count_fp_is_the_quantity_not_the_rounded_column(xos_session):
    """KXRAIN-26OCT13-MIN: 1.50 + 0.75 + 0.75 stored as 2 + 1 + 1. The rounded column
    says 4 contracts; the account settled 3."""
    s = xos_session
    t = "KXRAIN-26OCT13-MIN"
    _order(s, tag="Kmm", ticker=t, fills=[1.5, 0.75, 0.75], price=94)
    _settle(s, t, result="yes", no_count=3, no_cost=2.82)
    s.commit()
    assert sum(f.quantity for f in s.query(Fill).all()) == 4
    rec = la.reconcile_market(s, t)
    assert rec.ok is True
    assert rec.per_tag_usd["Kmm"] == pytest.approx(-2.82)
    # Holdings are the tag's own net filled position — a settlement does not erase what
    # it bought, which is why the executor consults them only while the account holds.
    assert la.ledgers_for_market(s, t)["Kmm"].net_no == pytest.approx(3.0)


def test_a_scalar_settlement_is_priced_from_its_value(xos_session):
    s = xos_session
    t = "KXT20MATCH-X"
    _order(s, tag="Kmm", ticker=t, fills=[1.0], price=94)
    _settle(s, t, result="scalar", no_count=1, no_cost=0.94, value=50)
    s.commit()
    rec = la.reconcile_market(s, t)
    assert rec.ok is True
    assert rec.per_tag_usd["Kmm"] == pytest.approx(-0.44)


def test_direction_comes_from_the_order_not_the_fills_action(xos_session):
    """Kalshi reports a maker NO-buy as `action=sell`. Reading it would invert P&L."""
    s = xos_session
    t = _shared_market(s, result="no")
    s.commit()
    assert {f.action for f in s.query(Fill).all()} == {"sell"}
    assert la.reconcile_market(s, t).per_tag_usd["Kmm"] > 0


def test_a_fill_on_the_other_side_is_mapped_into_the_orders_frame(xos_session):
    """Incentive-book shape: a NO-sell order whose fill reads YES-buy at the complement."""
    s = xos_session
    t = "KXMKT-FRAME"
    _order(s, tag="Alimm", ticker=t, fills=[2.0], price=25, side="no", action="buy")
    _order(s, tag="Alimm", ticker=t, fills=[2.0], price=88, side="no", action="sell",
           fill_side="yes", fill_action="buy", at=T0 + timedelta(hours=3))
    s.commit()
    led = la.ledgers_for_market(s, t)["Alimm"]
    assert led.net_no == pytest.approx(0.0)
    # bought NO at 25, sold NO at 100 - 88 = 12: -0.26, with no settlement needed
    assert led.pnl_usd(None) == pytest.approx(-0.26)


def test_a_closeout_belongs_to_the_tag_it_closes_and_never_touches_the_partner(xos_session):
    s = xos_session
    t = "KXSHARED-CLOSE"
    _order(s, tag="Kmm", ticker=t, fills=[3.0], price=94)
    _order(s, tag="Nmm", ticker=t, fills=[3.0], price=93)
    # Nmm closes ITS OWN 3 contracts by buying YES at 10 (yes frame).
    _order(s, tag="Nmm_closeout", ticker=t, fills=[3.0], price=10, side="yes",
           action="buy", fill_action="buy", at=T0 + timedelta(hours=5))
    s.commit()
    held = la.holdings_by_tag(s, t)
    assert held == {"Kmm": pytest.approx(3.0)}
    led = la.ledgers_for_market(s, t)
    assert led["Nmm"].entry_contracts == pytest.approx(3.0)
    # paid 93 for NO, closed by paying 10 for YES: the pair redeems at 100 -> -0.09
    assert led["Nmm"].pnl_usd(None) == pytest.approx(-0.09)


# ---------------------------------------------------------------------------
# The provider switch: byte-identical until activation
# ---------------------------------------------------------------------------


def test_before_activation_the_shipped_provider_runs_and_excludes_the_shared_market(
        xos_session):
    s = xos_session
    _shared_market(s, result="no")
    s.commit()
    mv = compute_metric(s, "live_settled_markets", _scope(["Kmm"]))
    assert mv.value == 0.0
    assert mv.provenance["excluded_contested_markets"] == 1
    assert mv.provenance["provider_revision"] == "live_exec_v1"
    assert "live_attribution" not in mv.provenance


def test_after_activation_each_tag_is_priced_from_its_own_fills(xos_session):
    s = xos_session
    t = _shared_market(s, result="no", a_fills=(3.0,), a_px=94, b_fills=(1.0,), b_px=92)
    _activate(s)
    s.commit()
    a = compute_metric(s, "live_cents_per_contract", _scope(["Kmm"]))
    b = compute_metric(s, "live_cents_per_contract", _scope(["Nmm"]))
    assert a.value == pytest.approx(6.0) and a.n == 3
    assert b.value == pytest.approx(8.0) and b.n == 1
    for mv in (a, b):
        assert mv.provenance["provider_revision"] == la.PROVIDER_REVISION
        assert mv.provenance["live_attribution"] == "fill"
        assert mv.provenance["shared_markets_included"] == 1
        assert mv.provenance["excluded_contested_markets"] == 0
    pnl_a = compute_metric(s, "live_realized_pnl_usd", _scope(["Kmm"])).value
    pnl_b = compute_metric(s, "live_realized_pnl_usd", _scope(["Nmm"])).value
    assert pnl_a + pnl_b == pytest.approx(la.market_settlement(s, t).realized_usd)


def test_the_xos000040_market_is_split_not_double_counted(xos_session):
    """3 + 3 contracts on one market, YES: -$5.58 for the account, -$2.79 per tag."""
    s = xos_session
    _shared_market(s, result="yes", a_fills=(3.0,), a_px=93, b_fills=(3.0,), b_px=93)
    _activate(s)
    s.commit()
    for tag in ("Kmm", "Nmm"):
        loss = compute_metric(s, "live_max_realized_loss_usd", _scope([tag])).value
        assert loss == pytest.approx(2.79)


def test_an_unfilled_order_is_never_held_not_a_zero(xos_session):
    s = xos_session
    _order(s, tag="Kmm", ticker="KXNOFILL", fills=[], price=94, status="canceled", qty=1)
    _activate(s)
    s.commit()
    mv = compute_metric(s, "live_settled_markets", _scope(["Kmm"]))
    assert mv.value == 0.0
    assert mv.provenance["orders_that_never_held_a_position"] == 1


def test_shared_market_fill_rate_is_reported_per_tag(xos_session):
    s = xos_session
    t = "KXSHARED-Q"
    _order(s, tag="Kmm", ticker=t, fills=[3.0], price=94)
    _order(s, tag="Nmm", ticker=t, fills=[], price=94, status="canceled", qty=3)
    _activate(s)
    s.commit()
    mv = compute_metric(s, "live_fill_rate_pct", _scope(["Nmm"]))
    shared = mv.provenance["shared_markets"]
    assert shared["markets"] == 1 and shared["fill_rate_pct"] == 0.0
    mk = compute_metric(s, "live_fill_rate_pct", _scope(["Kmm"]))
    assert mk.provenance["shared_markets"]["fill_rate_pct"] == 100.0


# ---------------------------------------------------------------------------
# Mismatches are integrity events, never silently dropped
# ---------------------------------------------------------------------------


def _live_experiment(s, xos_platform, tag):
    exp = svc.create_experiment(s, key=f"exp-{tag}", origin="operator")
    ver = svc.create_experiment_version(s, exp, hypothesis="h", independent_variable="v",
                                        control_required=False,
                                        control_exemption_reason="fixture", now=T0)
    svc.add_arm(s, ver, arm_key="a", role="treatment", strategy_tag=tag)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(s, epoch, deployment_key=f"{tag}-live", stage="LIVE_CANARY",
                            kind="live", arms={"a": tag}, started_at=T0)
    return exp


def test_a_mismatch_is_recorded_once_and_does_not_block(xos_session, xos_platform):
    s = xos_session
    exp = _live_experiment(s, xos_platform, "Kmm")
    t = "KXMISMATCH"
    _order(s, tag="Kmm", ticker=t, fills=[3.0], price=94)
    _settle(s, t, result="yes", no_count=2, no_cost=1.88)   # the account saw only 2
    s.commit()
    now = T0 + timedelta(days=4)
    first = la.record_mismatches(s, now=now)
    assert len(first) == 1 and first[0]["ticker"] == t
    assert la.record_mismatches(s, now=now) == []  # not twice while unresolved
    ev = s.query(ExperimentIntegrityEvent).filter_by(experiment_id=exp.id).one()
    assert ev.kind == la.MISMATCH_KIND
    assert la.MISMATCH_KIND in NON_BLOCKING_INTEGRITY_KINDS


def test_a_fill_no_order_explains_is_a_mismatch(xos_session, xos_platform):
    s = xos_session
    _live_experiment(s, xos_platform, "Kmm")
    t = "KXORPHAN"
    _order(s, tag="Kmm", ticker=t, fills=[1.0], price=94)
    s.add(Fill(kalshi_fill_id="hand", kalshi_order_id="no-such-order", market_ticker=t,
               side="no", action="sell", price=94, quantity=1, fee=0))
    _settle(s, t, result="no", no_count=2, no_cost=1.88)
    s.commit()
    rec = la.reconcile_market(s, t)
    assert rec.ok is False and "match no live order" in rec.reason


# ---------------------------------------------------------------------------
# Activation is a CUTOVER, never a merge
# ---------------------------------------------------------------------------


def _env(cid, payload):
    return {"schema_version": 1, "command_id": cid, "action": "CUTOVER",
            "actor": "claude-code", "actor_role": "PLATFORM_CHANGE_REVIEW",
            "payload": payload}


def test_metrics_cutover_defers_on_a_worker_not_serving_the_reviewed_code(xos_session):
    env = _env("metrics-defer", {"revision": la.REVISION_REF,
                           "expect_metrics_fingerprint": "0" * 64})
    receipt = pc.run_boot_command(xos_session, json.dumps(env), now=T0)
    assert receipt["status"] == "DEFERRED"
    assert receipt["code"] == "METRICS_NOT_DEPLOYED"


def test_metrics_proof_cannot_activate_another_component(xos_session, xos_platform):
    s = xos_session
    rev = svc.register_platform_revision(s, "FEE_MODEL", version="x",
                                         fingerprint=la.metrics_fingerprint())
    env = _env("metrics-wrong", {"revision": "FEE_MODEL:x",
                           "expect_metrics_fingerprint": la.metrics_fingerprint()})
    assert pc.execute_envelope(s, env, now=T0)["status"] == "REJECTED"
    assert rev.status == "pending"


def test_metrics_cutover_activates_and_applies_the_accepted_recompute(xos_session,
                                                                      xos_platform):
    """The I1 path end to end through the transport: RECORD_IMPACTS carries the named
    normalizer, CUTOVER activates at the measured instant and APPLIES it, so the
    experiment is licensed rather than left BLOCKED_PLATFORM."""
    from kalshi_bot.experiment_os.models import PlatformImpactAction
    from kalshi_bot.experiment_os.platform_impact import licensed_revision_ids

    s = xos_session
    exp = _live_experiment(s, xos_platform, "Kmm")
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    rev = svc.register_platform_revision(s, la.COMPONENT, version=la.VERSION,
                                         fingerprint=la.metrics_fingerprint())
    impacts = {"schema_version": 1, "command_id": "metrics-impacts", "action": "RECORD_IMPACTS",
               "actor": "claude-code", "actor_role": "PLATFORM_CHANGE_REVIEW",
               "payload": {"revision": la.REVISION_REF, "impacts": [{
                   "experiment": exp.key, "impact_class": "I1", "action": "RECOMPUTE",
                   "normalization_ref": "metric:live_cents_per_contract",
                   "rationale": "exact recomputation from recorded fills"}]}}
    assert pc.execute_envelope(s, impacts, now=T0)["status"] == "SUCCEEDED"
    env = _env("metrics-cutover", {"revision": la.REVISION_REF,
                        "expect_metrics_fingerprint": la.metrics_fingerprint(),
                        "apply_accepted_recompute": True})
    assert pc.execute_envelope(s, env, now=T0 + timedelta(days=1))["status"] == "SUCCEEDED"
    assert rev.status == "active"
    assert la.fill_attribution_active(s) is True
    record = s.query(PlatformImpactAction).filter_by(experiment_id=exp.id).one()
    assert record.status == "applied"
    assert rev.id in licensed_revision_ids(s, exp.id)


def test_an_unnamed_normalizer_is_still_refused_through_the_transport(xos_session,
                                                                      xos_platform):
    s = xos_session
    exp = _live_experiment(s, xos_platform, "Kmm")
    svc.register_platform_revision(s, la.COMPONENT, version=la.VERSION)
    impacts = {"schema_version": 1, "command_id": "metrics-bad-norm", "action": "RECORD_IMPACTS",
               "actor": "claude-code", "actor_role": "PLATFORM_CHANGE_REVIEW",
               "payload": {"revision": la.REVISION_REF, "impacts": [{
                   "experiment": exp.key, "impact_class": "I1", "action": "RECOMPUTE",
                   "normalization_ref": "metric:no_such_metric",
                   "rationale": "x"}]}}
    assert pc.execute_envelope(s, impacts, now=T0)["status"] == "REJECTED"


# ---------------------------------------------------------------------------
# The pre-activation impact report (acceptance check 5)
# ---------------------------------------------------------------------------


def test_the_impact_report_is_allowlisted_and_reconciles_without_activating(xos_session,
                                                                             xos_platform):
    from kalshi_bot.experiment_os import attribution_report as report
    from kalshi_bot.experiment_os.cli import build_parser
    from scripts import ops_runner

    # Served as a canonical xos read: the ops `script` sandbox has no SQLAlchemy.
    assert "attribution-impact" in ops_runner.xos_allowlist()
    assert "live_attribution_impact" not in ops_runner.ALLOWED_SCRIPTS
    assert build_parser().parse_args(["attribution-impact"]).fn.__name__ \
        == "cmd_attribution_impact"
    s = xos_session
    _live_experiment(s, xos_platform, "Kmm")
    _shared_market(s, result="yes", a_fills=(3.0,), a_px=93, b_fills=(3.0,), b_px=93)
    t = "KXMISMATCH-R"
    _order(s, tag="Kmm", ticker=t, fills=[3.0], price=94)
    _settle(s, t, result="yes", no_count=2, no_cost=1.88)
    s.commit()
    with report._forced(True):
        assert la.fill_attribution_active(s) is True
    assert la.fill_attribution_active(s) is False, "the override must not leak"
    rec = report.reconciliation(s, days=36500)
    assert (rec["markets"], rec["ok"], rec["shared"]) == (2, 1, 1)
    assert [r.ticker for r in rec["mismatch"]] == [t]
    assert report.gate_impact(s) == []   # no live gate registered: nothing to move
    text = report.render(s, days=36500)
    assert "MISMATCH 1" in text and la.metrics_fingerprint() in text
