"""`clean_pairs` / `pair_win_rate_95lb_pct` — the A5 strangle's unit of evidence.

A5 is a two-sided position: one cheap-YES leg and one cheap-NO leg on the same
event. One settlement can never lose both, which is the entire thesis — so the
unit of evidence is the PAIR, not the trade, and the gate's confidence-interval
math assumes every counted observation is a real pair.

`docs/MMSELL_ANCHOR_SET.md` records what happens when it isn't: before the
2026-08-14 pairing boundary a single event opened four same-side legs on four
strikes of one game. Those are positively correlated — the exact risk the strangle
exists to avoid — and counting them as pairs inflates n with observations that do
not carry the hedge.

What must never happen, pinned here:

  * a one-sided event, a same-side multi-leg event, or a half-settled event
    counted as a clean pair;
  * a censored (part-settled) pair scored as a loss;
  * an empty sample producing a confident bound instead of "undefined";
  * a normal-approximation interval, which at 100% observed would pass a bar the
    exact bound fails.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from kalshi_bot.experiment_os.metrics import (
    MetricScope,
    binomial_lower_bound_95,
    compute_metric,
)
from kalshi_bot.models import MmSellSettlementMeta, PaperTrade

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _scope(tags=("mmsellA5",), arm="strangle", kind="paper"):
    return MetricScope(
        experiment_key="mmsell-anchor-strangle", version=1, epoch_number=1,
        arm_key=arm, deployment_kind=kind, strategy_tags=tuple(tags),
        deployment_keys=(), window_start=T0,
        window_end=T0 + timedelta(days=30),
        platform_snapshot_fingerprint="f" * 64,
    )


_SEQ = itertools.count()


def _leg(s, event, *, side, pnl, status="settled", tag="mmsellA5", at=None,
         ticker=None):
    """One leg on one event. The event mapping goes through
    mmsell_settlement_meta, exactly as the tracker's own pairing cap does."""
    ticker = ticker or f"{event}-{side}-{next(_SEQ)}"
    s.add(MmSellSettlementMeta(market_ticker=ticker, event_ticker=event,
                               close_time=T0 + timedelta(days=1)))
    s.add(PaperTrade(
        market_ticker=ticker, strategy=tag, status=status, side=side,
        pnl=pnl, quantity=1, created_at=at or (T0 + timedelta(hours=1)),
    ))


def _pairs(s):
    return compute_metric(s, "clean_pairs", _scope())


def _bound(s):
    return compute_metric(s, "pair_win_rate_95lb_pct", _scope())


# ---------------------------------------------------------------------------
# What counts as a pair
# ---------------------------------------------------------------------------


def test_one_leg_each_side_is_a_pair(xos_session):
    s = xos_session
    _leg(s, "EVT1", side="yes", pnl=0.06)
    _leg(s, "EVT1", side="no", pnl=0.07)
    s.commit()
    mv = _pairs(s)
    assert mv.value == 1.0 and mv.missing is False
    assert mv.provenance["clean_pairs"] == 1
    assert mv.provenance["event_source"] == "mmsell_settlement_meta.event_ticker"


def test_a_one_sided_event_is_not_a_pair_and_is_counted(xos_session):
    s = xos_session
    _leg(s, "EVT-ONE", side="no", pnl=0.06)  # no mirror leg ever taken
    s.commit()
    mv = _pairs(s)
    assert mv.value == 0.0
    assert mv.provenance["one_sided_events"] == 1
    assert mv.provenance["events_seen"] == 1


def test_same_side_multi_leg_events_are_refused(xos_session):
    """The recorded failure: four cheap-NO legs on four strikes of one game. Even
    with a mirror leg present, that is not one hedged pair."""
    s = xos_session
    for _ in range(4):
        _leg(s, "EVT-LADDER", side="no", pnl=0.05)
    _leg(s, "EVT-LADDER", side="yes", pnl=0.05)
    s.commit()
    mv = _pairs(s)
    assert mv.value == 0.0
    assert mv.provenance["multi_leg_events"] == 1
    assert mv.provenance["one_sided_events"] == 0


def test_a_half_settled_pair_is_censored_not_a_loss(xos_session):
    """One leg still open means the PAIR's outcome is unknown. Scoring it as a loss
    would be the missing-is-not-zero error, on the unit that decides the gate."""
    s = xos_session
    _leg(s, "EVT-OPEN", side="yes", pnl=0.06)
    _leg(s, "EVT-OPEN", side="no", pnl=None, status="open")
    s.commit()
    mv = _pairs(s)
    assert mv.value == 0.0
    assert mv.provenance["incomplete_pairs_censored"] == 1
    assert _bound(s).value is None  # no complete pairs -> no bound at all


def test_stop_closed_legs_still_settle_a_pair(xos_session):
    """`closed_sl` carries real P&L — the mmsellA1-A3 reading error was dropping it."""
    s = xos_session
    _leg(s, "EVT-STOP", side="yes", pnl=0.06)
    _leg(s, "EVT-STOP", side="no", pnl=-0.30, status="closed_sl")
    s.commit()
    assert _pairs(s).value == 1.0
    assert _bound(s).n == 1


def test_a_pair_wins_on_combined_pnl_not_per_leg(xos_session):
    """The thesis is that two premiums outweigh at most one loss. A pair with a
    losing leg still wins if the pair nets positive."""
    s = xos_session
    _leg(s, "EVT-NET", side="yes", pnl=0.06)
    _leg(s, "EVT-NET", side="no", pnl=-0.04)   # net +0.02 -> a win
    _leg(s, "EVT-LOSS", side="yes", pnl=0.06)
    _leg(s, "EVT-LOSS", side="no", pnl=-0.30)  # net -0.24 -> a loss
    s.commit()
    assert _pairs(s).value == 2.0
    mv = _bound(s)
    assert mv.provenance["pair_wins"] == 1 and mv.n == 2


# ---------------------------------------------------------------------------
# Zero / undefined discipline
# ---------------------------------------------------------------------------


def test_no_evidence_gives_a_meaningful_zero_count_and_no_bound(xos_session):
    s = xos_session
    count = _pairs(s)
    assert count.value == 0.0 and count.missing is False   # counts are zero
    bound = _bound(s)
    assert bound.value is None and bound.missing is False  # rates are undefined
    assert "undefined, not 0" in bound.reason


def test_trades_with_no_event_mapping_are_counted_not_guessed(xos_session):
    """A ticker absent from mmsell_settlement_meta has no resolvable event. It is
    surfaced, never string-parsed into one."""
    s = xos_session
    s.add(PaperTrade(market_ticker="ORPHAN-TICKER", strategy="mmsellA5",
                     status="settled", side="no", pnl=0.05, quantity=1,
                     created_at=T0 + timedelta(hours=1)))
    s.commit()
    assert _pairs(s).provenance["trades_without_event_mapping"] == 1


# ---------------------------------------------------------------------------
# The bound itself — exact, because the approximation error IS the decision
# ---------------------------------------------------------------------------


def test_the_bound_reproduces_the_documented_backtest_number():
    """docs/MMSELL_ANCHOR_SET.md: the strangle backtest was 23/23 — a 100% observed
    win rate — and its 95% lower bound is 87.8%, which is why it FAILED the 93.9%
    bar. That failure is the reason A5 exists as a paper book at all."""
    assert round(binomial_lower_bound_95(23, 23), 1) == 87.8


def test_the_bound_matches_the_closed_form_at_a_perfect_record():
    """For wins == n, Clopper-Pearson reduces to alpha ** (1/n). Independent check
    of the bisection against exact arithmetic."""
    for n in (5, 23, 82, 150):
        exact = 100.0 * 0.05 ** (1.0 / n)
        assert abs(binomial_lower_bound_95(n, n) - exact) < 1e-3


def test_a_perfect_record_still_fails_the_gate_until_the_sample_is_large():
    """The whole point of an exact bound: 100% observed does not clear 93.9% until
    n is big enough. A normal approximation gives zero width at 100% and would
    wrongly pass at any n."""
    assert binomial_lower_bound_95(23, 23) < 93.9      # the backtest's failure
    assert binomial_lower_bound_95(82, 82) > 93.9      # the gate's registered floor


def test_the_bound_is_monotonic_and_bounded():
    assert binomial_lower_bound_95(0, 10) == 0.0
    assert binomial_lower_bound_95(10, 10) > binomial_lower_bound_95(9, 10)
    assert binomial_lower_bound_95(50, 100) < binomial_lower_bound_95(90, 100)
    assert binomial_lower_bound_95(1, 0) is None


# ---------------------------------------------------------------------------
# Scope discipline: the boundary belongs to the GATE, not the provider
# ---------------------------------------------------------------------------


def test_the_evidence_window_excludes_pre_boundary_pairs(xos_session):
    """The pairing boundary is a hard floor carried by the gate's
    `evidence_started_at`. The provider honours the window it is given and does not
    hard-code a date — so moving the boundary is a contract change, not a code
    change."""
    s = xos_session
    _leg(s, "EVT-OLD", side="yes", pnl=0.06, at=T0 - timedelta(days=5))
    _leg(s, "EVT-OLD", side="no", pnl=0.06, at=T0 - timedelta(days=5))
    _leg(s, "EVT-NEW", side="yes", pnl=0.06)
    _leg(s, "EVT-NEW", side="no", pnl=0.06)
    s.commit()
    assert _pairs(s).value == 1.0  # window starts at T0; the old pair is outside


def test_another_books_legs_never_enter_this_scope(xos_session):
    s = xos_session
    _leg(s, "EVT-SHARED", side="yes", pnl=0.06, tag="mmsellA5")
    _leg(s, "EVT-SHARED", side="no", pnl=0.06, tag="mmsell10")  # a different book
    s.commit()
    mv = _pairs(s)
    assert mv.value == 0.0            # A5 holds only one side of that event
    assert mv.provenance["one_sided_events"] == 1


# ---------------------------------------------------------------------------
# End to end: the gate that has been BLOCKED_DATA can now render a verdict
# ---------------------------------------------------------------------------


def _a5_gate(s):
    """The registered A5 shape, as imported."""
    from kalshi_bot.experiment_os import service as svc

    exp = svc.create_experiment(s, key="mmsell-anchor-strangle", origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="two-sided strangle", independent_variable="strangle",
        now=T0, control_exemption_reason="absolute pair-rate bound, no control arm",
    )
    svc.add_arm(s, ver, arm_key="strangle", role="treatment", strategy_tag="mmsellA5")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key="a5-paper-1", stage="PAPER", kind="paper",
        arms={"strangle": "mmsellA5"}, started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    gate = svc.register_gate(
        s, ver, gate_key="paper_keep", kind="promotion",
        spec={
            "sample": {"strangle": {"metric": "clean_pairs", "op": ">=", "value": 82}},
            "pass_all": [{"metric": "pair_win_rate_95lb_pct", "arm": "strangle",
                          "op": ">", "value": 93.9}],
            "fail_any": [{"metric": "pair_win_rate_95lb_pct", "arm": "strangle",
                          "op": "<=", "value": 93.9}],
        },
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
    )
    svc.mark_gate_evidence_started(s, gate, at=T0)
    s.commit()
    return gate


def test_the_strangle_gate_evaluates_instead_of_blocking(xos_session, xos_platform):
    from kalshi_bot.experiment_os import evaluator

    s = xos_session
    gate = _a5_gate(s)

    # Thin sample: a real verdict, and the right one — underpowered is HOLD.
    for i in range(10):
        _leg(s, f"E{i}", side="yes", pnl=0.06)
        _leg(s, f"E{i}", side="no", pnl=0.06)
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "HOLD"          # not BLOCKED_DATA any more
    assert not out.blocking_reasons

    # Powered and perfect: clears the registered floor and the registered bound.
    for i in range(10, 90):
        _leg(s, f"E{i}", side="yes", pnl=0.06)
        _leg(s, f"E{i}", side="no", pnl=0.06)
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "PASS"
    by_metric = {c.clause["metric"]: c for c in out.clauses}
    assert by_metric["clean_pairs"].value == 90.0
    assert by_metric["pair_win_rate_95lb_pct"].value > 93.9


# ---------------------------------------------------------------------------
# Provider-specific revisions: a narrow blast radius, and no shared identity
# between "provider missing" and "provider implemented"
# ---------------------------------------------------------------------------


def test_every_value_records_the_implementation_that_produced_it(xos_session):
    from kalshi_bot.experiment_os.metrics import UNPROVIDED_REVISION

    s = xos_session
    _leg(s, "EVT-REV", side="yes", pnl=0.06)
    _leg(s, "EVT-REV", side="no", pnl=0.06)
    s.commit()
    assert _pairs(s).provenance["provider_revision"] == "pair_metrics_v1"
    assert compute_metric(
        s, "settled_trades", _scope()
    ).provenance["provider_revision"] == "universal_v1"
    assert compute_metric(
        s, "realizable_cents_per_trade", _scope()
    ).provenance["provider_revision"] == "fill_model_v1"
    assert compute_metric(
        s, "live_cents_per_contract", _scope(kind="live")
    ).provenance["provider_revision"] == "live_exec_v1"
    # And a metric with no implementation is NOT recorded as some version of one.
    assert compute_metric(
        s, "realized_tail_hit_ratio_vs_modeled", _scope()
    ).provenance["provider_revision"] == UNPROVIDED_REVISION


def test_unprovided_and_implemented_never_share_an_identity(xos_session,
                                                            xos_platform):
    """The reason this mechanism exists. A result recorded while clean_pairs had
    no provider, and one computed by pair_metrics_v1, must be distinguishable in
    the recorded evidence — not collapsed into one engine-wide revision string."""
    from kalshi_bot.experiment_os.metrics import UNPROVIDED_REVISION, provider_revisions

    blocked_era = [{"clause": {"metric": "clean_pairs"},
                    "provenance": {"provider_revision": UNPROVIDED_REVISION}}]
    implemented = [{"clause": {"metric": "clean_pairs"},
                    "provenance": {"provider_revision": "pair_metrics_v1"}}]
    assert provider_revisions(blocked_era) != provider_revisions(implemented)


def test_the_fingerprint_binds_to_the_providers_actually_used(xos_session,
                                                              xos_platform):
    """Changing one provider must re-record the gates that READ it and leave every
    other gate's standing result alone — the whole point of not bumping a global
    constant for every new provider."""
    from kalshi_bot.experiment_os import evaluator, gate_runner
    from kalshi_bot.experiment_os import metrics as met
    from kalshi_bot.experiment_os import service as svc

    s = xos_session
    exp = svc.create_experiment(s, key="exp-rev", origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
        control_exemption_reason="single-arm test shape",
    )
    svc.add_arm(s, ver, arm_key="strangle", role="treatment", strategy_tag="mmsellA5")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(s, epoch, deployment_key="rev-paper-1", stage="PAPER",
                            kind="paper", arms={"strangle": "mmsellA5"}, started_at=T0)
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    pair_gate = svc.register_gate(
        s, ver, gate_key="paper_keep", kind="promotion",
        spec={"pass_all": [{"metric": "clean_pairs", "arm": "strangle",
                            "op": ">=", "value": 0}]},
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
    )
    svc.mark_gate_evidence_started(s, pair_gate, at=T0)
    plain_gate = svc.register_gate(
        s, ver, gate_key="plain", kind="kill",
        spec={"pass_all": [{"metric": "settled_trades", "arm": "strangle",
                            "op": ">=", "value": 0}]},
        registered_at=T0,
    )
    svc.mark_gate_evidence_started(s, plain_gate, at=T0)
    _leg(s, "EVT-FP", side="yes", pnl=0.06)
    _leg(s, "EVT-FP", side="no", pnl=0.06)
    s.commit()

    def fp(gate):
        out = evaluator.evaluate_gate(s, gate, epoch=epoch, persist=False)
        return gate_runner.evaluation_fingerprint(out, gate, epoch)

    pair_before, plain_before = fp(pair_gate), fp(plain_gate)

    # Ship "pair_metrics_v2" — a change to the pair implementation only.
    bumped = met.REGISTRY["clean_pairs"]
    met.REGISTRY["clean_pairs"] = replace(bumped, revision="pair_metrics_v2")
    try:
        assert fp(pair_gate) != pair_before      # the gate that reads it re-records
        assert fp(plain_gate) == plain_before    # every other gate is untouched
    finally:
        met.REGISTRY["clean_pairs"] = bumped


def test_recorded_results_carry_the_provider_revisions(xos_session, xos_platform):
    from kalshi_bot.experiment_os import evaluator
    from kalshi_bot.experiment_os.models import ExperimentGateResult

    s = xos_session
    _leg(s, "EVT-REC", side="yes", pnl=0.06)
    _leg(s, "EVT-REC", side="no", pnl=0.06)
    s.commit()
    gate = _a5_gate(s)
    out = evaluator.evaluate_gate(s, gate)
    s.commit()
    row = s.get(ExperimentGateResult, out.result_id)
    assert row.metrics_json["provider_revisions"]["clean_pairs"] == "pair_metrics_v1"
