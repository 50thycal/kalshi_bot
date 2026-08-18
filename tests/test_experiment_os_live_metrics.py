"""`live_settled_contracts` / `live_cents_per_contract` / `twin_live_winrate_gap_pp`.

These are the first providers that read real-money execution, and they exist to
decide LIVE_CANARY -> PRODUCTION. Two things must be true of them, and both are
pinned here rather than argued:

  * **Addressing is obeyed, never repaired.** Two imported live-canary gates
    address `deployment_kind="paper"` on epochs holding only live + paper_twin.
    A provider that inferred "they probably meant live" would make those gates
    appear to work, hide the defect a corrected Version exists to fix, and let a
    promotion turn on evidence the registered contract never asked for. So an
    impossible scope returns MISSING, and MISSING is not zero.
  * **The rate is per CONTRACT.** `scripts/mmsell_live.py` divides realized P&L
    by settled POSITIONS and labels the result `live_$/ct`. That is only per
    contract while every position is a 1-lot. Parity on 1-lots and deliberate
    divergence on multi-lots are both asserted below.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from kalshi_bot.experiment_os.metrics import MetricScope, compute_metric
from kalshi_bot.models import Fill, LiveOrder, PaperTrade, Position

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
_SEQ = itertools.count()


def _scope(*, kind="live", tags=("Lmm-live",), arm="treatment", deployments=()):
    return MetricScope(
        experiment_key="mmsell-live", version=1, epoch_number=1, arm_key=arm,
        deployment_kind=kind, strategy_tags=tuple(tags),
        deployment_keys=tuple(deployments), window_start=T0,
        window_end=T0 + timedelta(days=30),
        platform_snapshot_fingerprint="f" * 64,
    )


def _live_market(s, *, tag="Lmm-live", contracts=1, realized=None, at=None,
                 ticker=None, closed=True, fills=None):
    """One live market: a buy order, its fill(s), and a position snapshot.

    `fills` overrides the fill split so a 5-contract position can be filled as
    one 5-lot or five 1-lots — the denominator must not care."""
    ticker = ticker or f"MKT-{next(_SEQ)}"
    oid = f"ord-{next(_SEQ)}"
    s.add(LiveOrder(kalshi_order_id=oid, market_ticker=ticker, strategy=tag,
                    action="buy", side="yes", quantity=contracts, status="filled",
                    created_at=at or (T0 + timedelta(hours=1))))
    for q in (fills if fills is not None else [contracts]):
        s.add(Fill(kalshi_fill_id=f"f-{next(_SEQ)}", kalshi_order_id=oid,
                   market_ticker=ticker, quantity=q, price=40, fee=0.01,
                   filled_at=at or (T0 + timedelta(hours=1))))
    s.add(Position(market_ticker=ticker, captured_at=T0 + timedelta(days=2),
                   quantity=0 if closed else contracts,
                   realized_pnl=realized, unrealized_pnl=None))
    return ticker


def _paper(s, tag, *, pnl, status="settled", at=None):
    s.add(PaperTrade(market_ticker=f"P-{next(_SEQ)}", strategy=tag, status=status,
                     pnl=pnl, quantity=1,
                     created_at=at or (T0 + timedelta(hours=1))))


# ---------------------------------------------------------------------------
# Addressing: the provider refuses an impossible scope
# ---------------------------------------------------------------------------


def test_live_metric_on_a_paper_scope_is_missing_not_zero(xos_session):
    """The malformed-contract case. `paper` is not a live deployment kind, and
    answering 0 would be a confident wrong answer a `>=` floor reads as real."""
    s = xos_session
    _live_market(s, contracts=2, realized=1.0)
    s.commit()

    mv = compute_metric(s, "live_settled_contracts", _scope(kind="paper"))
    assert mv.missing is True
    assert mv.value is None
    assert "deployment_kind='live'" in mv.reason
    assert "'paper'" in mv.reason
    assert mv.provenance["addressing_error"] is True


def test_live_metric_on_a_paper_twin_scope_is_also_missing(xos_session):
    """A twin runs paper mechanics under a live book's shadow. It still places no
    real orders, so live execution metrics are undefined over it."""
    s = xos_session
    mv = compute_metric(s, "live_cents_per_contract", _scope(kind="paper_twin"))
    assert mv.missing is True
    assert "deployment_kind='live'" in mv.reason


def test_provider_does_not_substitute_the_live_deployment(xos_session):
    """The whole point: real live evidence EXISTS, and a paper-addressed clause
    still does not get it."""
    s = xos_session
    _live_market(s, contracts=10, realized=5.0)
    s.commit()

    live = compute_metric(s, "live_settled_contracts", _scope(kind="live"))
    paper = compute_metric(s, "live_settled_contracts", _scope(kind="paper"))
    assert live.value == 10.0 and live.missing is False
    assert paper.missing is True and paper.value is None


def test_live_scope_with_no_tags_is_empty_not_an_addressing_error(xos_session):
    """A correctly addressed scope that simply has no live tags is a different
    condition from a misaddressed one, and says so."""
    s = xos_session
    mv = compute_metric(s, "live_settled_contracts", _scope(kind="live", tags=()))
    assert mv.missing is True
    assert "no live deployment tags" in mv.reason
    assert "addressing_error" not in mv.provenance


# ---------------------------------------------------------------------------
# Per contract, not per position
# ---------------------------------------------------------------------------


def test_parity_with_the_reference_script_on_single_contract_positions(xos_session):
    """On 1-lots the honest definition and the script's per-position average are
    the same number (the script reports dollars; this reports cents)."""
    s = xos_session
    pnls = [0.30, -0.10, 0.05, -0.25]
    for p in pnls:
        _live_market(s, contracts=1, realized=p)
    s.commit()

    mv = compute_metric(s, "live_cents_per_contract", _scope())
    script_dollars_per_position = sum(pnls) / len(pnls)
    assert mv.value == round(script_dollars_per_position * 100.0, 4)
    assert compute_metric(s, "live_settled_contracts", _scope()).value == 4.0


def test_deliberate_divergence_from_the_script_on_multi_contract_positions(
        xos_session):
    """A 5-lot losing $1.00 is five contracts losing 20c, not one -100c
    observation. The script's per-position average says the latter; this provider
    says the former, and that difference is the reason it exists."""
    s = xos_session
    _live_market(s, contracts=5, realized=-1.00)
    _live_market(s, contracts=1, realized=0.20)
    s.commit()

    mv = compute_metric(s, "live_cents_per_contract", _scope())
    # honest: total -0.80 over 6 contracts
    assert mv.value == round(-0.80 * 100.0 / 6, 4)
    # the script's shortcut: average of (-1.00, 0.20) over 2 POSITIONS
    script = (-1.00 + 0.20) / 2 * 100.0
    assert mv.value != round(script, 4), "the divergence must be real, not nominal"
    assert compute_metric(s, "live_settled_contracts", _scope()).value == 6.0
    assert mv.provenance["per_contract_basis"].startswith("total realized P&L")


def test_contract_count_is_independent_of_how_an_order_was_filled(xos_session):
    """Five 1-lot fills and one 5-lot fill are the same five contracts."""
    s = xos_session
    _live_market(s, contracts=5, realized=1.0, fills=[5])
    a = compute_metric(s, "live_settled_contracts", _scope()).value
    s.rollback()

    _live_market(s, contracts=5, realized=1.0, fills=[1, 1, 1, 1, 1])
    s.commit()
    b = compute_metric(s, "live_settled_contracts", _scope()).value
    assert a == b == 5.0


# ---------------------------------------------------------------------------
# What is excluded, and why it is counted rather than dropped
# ---------------------------------------------------------------------------


def test_open_positions_are_excluded_from_both_sides_of_the_rate(xos_session):
    """Realized P&L exists only for closed positions. Counting open contracts in
    the denominator would make the rate worsen simply because a position opened."""
    s = xos_session
    _live_market(s, contracts=2, realized=1.00)          # settled
    _live_market(s, contracts=8, realized=None, closed=False)  # still open
    s.commit()

    n = compute_metric(s, "live_settled_contracts", _scope())
    rate = compute_metric(s, "live_cents_per_contract", _scope())
    assert n.value == 2.0
    assert rate.value == round(1.00 * 100.0 / 2, 4)
    assert n.provenance["excluded_still_open_markets"] == 1


def test_a_market_two_arms_traded_is_excluded_and_counted(xos_session):
    """`positions` is keyed by market, not strategy, so a shared market's P&L
    cannot be split. The reference script attributes it fully to BOTH strategies;
    for an A/B promotion gate that is double-counting."""
    s = xos_session
    shared = _live_market(s, tag="Lmm-live", contracts=3, realized=2.0)
    s.add(LiveOrder(kalshi_order_id=f"ord-{next(_SEQ)}", market_ticker=shared,
                    strategy="Lmm-other", action="buy", side="yes", quantity=1,
                    status="filled", created_at=T0 + timedelta(hours=2)))
    _live_market(s, tag="Lmm-live", contracts=1, realized=0.50)
    s.commit()

    mv = compute_metric(s, "live_settled_contracts", _scope())
    assert mv.value == 1.0, "the contested market must not be counted"
    assert mv.provenance["excluded_contested_markets"] == 1


def test_a_closed_but_unpriced_position_is_not_a_zero(xos_session):
    s = xos_session
    _live_market(s, contracts=4, realized=None, closed=True)
    s.commit()

    n = compute_metric(s, "live_settled_contracts", _scope())
    rate = compute_metric(s, "live_cents_per_contract", _scope())
    assert n.value == 0.0
    assert n.provenance["excluded_unpriced_markets"] == 1
    assert rate.value is None and "no settled live contracts" in rate.reason


def test_only_the_newest_position_snapshot_decides(xos_session):
    """`positions` is append-only. An older quantity=0 row may simply predate a
    re-entry, and reading it would report a still-open position as settled."""
    s = xos_session
    ticker = _live_market(s, contracts=2, realized=1.0)   # closed at T0+2d
    s.add(Position(market_ticker=ticker, captured_at=T0 + timedelta(days=3),
                   quantity=2, realized_pnl=1.0))          # re-entered later
    s.commit()

    mv = compute_metric(s, "live_settled_contracts", _scope())
    assert mv.value == 0.0
    assert mv.provenance["excluded_still_open_markets"] == 1


def test_entry_fills_only_so_the_denominator_is_not_doubled(xos_session):
    """A position is entered by buys and closed by sells; both produce fills."""
    s = xos_session
    ticker = _live_market(s, contracts=3, realized=0.60)
    sell = f"ord-{next(_SEQ)}"
    s.add(LiveOrder(kalshi_order_id=sell, market_ticker=ticker,
                    strategy="Lmm-live", action="sell", side="yes", quantity=3,
                    status="filled", created_at=T0 + timedelta(hours=5)))
    s.add(Fill(kalshi_fill_id=f"f-{next(_SEQ)}", kalshi_order_id=sell,
               market_ticker=ticker, quantity=3, price=55, fee=0.01,
               filled_at=T0 + timedelta(hours=5)))
    s.commit()

    assert compute_metric(s, "live_settled_contracts", _scope()).value == 3.0


# ---------------------------------------------------------------------------
# twin_live_winrate_gap_pp — the adverse-selection read
# ---------------------------------------------------------------------------


def _armed_pair(s, *, key="mmsell-live", live_tag="Lmm-live", twin_tag="Lmm-pt"):
    """A registered live deployment with its structural twin.

    Built through the service layer so the twin is a real
    `twin_of_deployment_id` edge, not a tag-suffix convention — which is exactly
    what the provider resolves against."""
    from kalshi_bot.experiment_os import service as svc

    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="live execution holds up", independent_variable="exec",
        control_exemption_reason="execution twin is the control",
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=live_tag)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    live = svc.register_deployment(
        s, epoch, deployment_key=f"{key}-live", stage="LIVE_CANARY", kind="live",
        arms={"treatment": live_tag}, started_at=T0, _sanctioned_canary=True,
    )
    svc.register_deployment(
        s, epoch, deployment_key=f"{key}-twin", stage="LIVE_CANARY",
        kind="paper_twin", arms={"treatment": twin_tag}, twin_of=live,
        started_at=T0,
    )
    s.commit()
    return exp, ver, epoch, live


def test_twin_gap_is_twin_win_pct_minus_live_win_pct(xos_session, xos_platform):
    s = xos_session
    _armed_pair(s)
    # live: 1 win of 4 settled markets = 25%
    for pnl in (0.50, -0.10, -0.20, -0.30):
        _live_market(s, tag="Lmm-live", contracts=1, realized=pnl)
    # twin: 3 wins of 4 settled paper trades = 75%
    for pnl in (0.05, 0.05, 0.05, -0.05):
        _paper(s, "Lmm-pt", pnl=pnl)
    s.commit()

    mv = compute_metric(s, "twin_live_winrate_gap_pp",
                        _scope(deployments=("mmsell-live-live",)))
    assert mv.value == 50.0, "paper looks 50pp better than live — adverse selection"
    assert mv.provenance["live_win_pct"] == 25.0
    assert mv.provenance["twin_win_pct"] == 75.0
    assert mv.provenance["twin_tags"] == ["Lmm-pt"]


def test_twin_gap_n_binds_on_the_smaller_leg(xos_session, xos_platform):
    """A sample floor must bind on the leg that limits the comparison, not on
    whichever side happens to have more evidence."""
    s = xos_session
    _armed_pair(s)
    for pnl in (0.50, -0.10):
        _live_market(s, tag="Lmm-live", contracts=1, realized=pnl)
    for _ in range(20):
        _paper(s, "Lmm-pt", pnl=0.05)
    s.commit()

    mv = compute_metric(s, "twin_live_winrate_gap_pp",
                        _scope(deployments=("mmsell-live-live",)))
    assert mv.n == 2


def test_twin_gap_is_missing_when_no_twin_is_registered(xos_session, xos_platform):
    """theta4's live arm ran 13 days before its twin existed. Over a window with
    no registered twin there is no comparison to make, and inventing one from a
    similarly-named tag would fabricate a control."""
    from kalshi_bot.experiment_os import service as svc

    s = xos_session
    exp = svc.create_experiment(s, key="no-twin", origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="v",
        control_exemption_reason="test shape",
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag="Lnt")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key="no-twin-live", stage="LIVE_CANARY", kind="live",
        arms={"treatment": "Lnt"}, started_at=T0, _sanctioned_canary=True,
    )
    _live_market(s, tag="Lnt", contracts=1, realized=0.5)
    s.commit()

    mv = compute_metric(s, "twin_live_winrate_gap_pp",
                        _scope(tags=("Lnt",), deployments=("no-twin-live",)))
    assert mv.missing is True
    assert "no paper_twin deployment is registered" in mv.reason


def test_twin_gap_is_undefined_not_zero_when_a_leg_has_no_evidence(xos_session, xos_platform):
    """Two books with no settled trades are not two books that agree."""
    s = xos_session
    _armed_pair(s)
    for pnl in (0.50, -0.10):
        _live_market(s, tag="Lmm-live", contracts=1, realized=pnl)
    s.commit()  # twin has nothing settled

    mv = compute_metric(s, "twin_live_winrate_gap_pp",
                        _scope(deployments=("mmsell-live-live",)))
    assert mv.value is None
    assert mv.missing is False, "no evidence is an empty answer, not a broken one"
    assert "undefined without settled evidence on both legs" in mv.reason


def test_twin_gap_refuses_a_paper_addressed_clause(xos_session, xos_platform):
    s = xos_session
    _armed_pair(s)
    s.commit()
    mv = compute_metric(s, "twin_live_winrate_gap_pp",
                        _scope(kind="paper", deployments=("mmsell-live-live",)))
    assert mv.missing is True
    assert mv.provenance["addressing_error"] is True


def test_every_live_provider_records_its_own_revision(xos_session):
    """A gate result binds to the implementation that produced it, so adding
    these providers cannot silently share an identity with the era before them."""
    s = xos_session
    _live_market(s, contracts=1, realized=0.1)
    s.commit()
    for key in ("live_settled_contracts", "live_cents_per_contract"):
        mv = compute_metric(s, key, _scope())
        assert mv.provenance["provider_revision"] == "live_exec_v1"


# ---------------------------------------------------------------------------
# The read-only metric probe
# ---------------------------------------------------------------------------


def test_metric_probe_reports_value_and_provenance(xos_session, xos_platform, capsys):
    """A provider must be verifiable against real data BEFORE a gate depends on
    it. Without this the only way to exercise one is for some registered gate to
    happen to use it — which is backwards, since the point of verifying is to
    decide whether a gate should."""
    from kalshi_bot.experiment_os import cli

    s = xos_session
    _armed_pair(s, key="probe-exp", live_tag="Lprobe", twin_tag="Lprobe-pt")
    _live_market(s, tag="Lprobe", contracts=4, realized=0.80)
    s.commit()

    args = type("A", (), {"metric": "live_cents_per_contract", "key": "probe-exp",
                          "arm": "treatment", "kind": "live"})()
    assert cli.cmd_metric(s, args) == 0
    out = capsys.readouterr().out
    assert "value:       20.0" in out          # 0.80 USD over 4 contracts
    assert "Lprobe" in out
    assert "provider_revision: live_exec_v1" in out


def test_metric_probe_shows_the_addressing_refusal_rather_than_hiding_it(
        xos_session, xos_platform, capsys):
    """Asking for a live metric at kind=paper is a legitimate question with a
    real answer. Quietly answering a different one would defeat the reason to
    run the probe."""
    from kalshi_bot.experiment_os import cli

    s = xos_session
    _armed_pair(s, key="probe-addr", live_tag="Laddr", twin_tag="Laddr-pt")
    _live_market(s, tag="Laddr", contracts=4, realized=0.80)
    s.commit()

    args = type("A", (), {"metric": "live_cents_per_contract", "key": "probe-addr",
                          "arm": "treatment", "kind": "paper"})()
    assert cli.cmd_metric(s, args) == 0
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert "deployment_kind='live'" in out
    assert "addressing_error: True" in out


def test_metric_probe_rejects_an_unknown_metric(xos_session, xos_platform):
    from kalshi_bot.experiment_os import cli

    args = type("A", (), {"metric": "not_a_metric", "key": "x",
                          "arm": None, "kind": "paper"})()
    assert cli.cmd_metric(xos_session, args) == 1
