"""Twin-resolved providers: coverage, the tail ratio, and the two gap reads.

Three findings from the design work are load-bearing here and each is pinned.

**The paired gap is not adverse selection.** Pairing live against its twin
market-by-market collapses the variance ~15x, which makes it look like the
answer to every power problem. It isn't: it conditions on live having FILLED,
and a maker's adverse selection operates through *which orders fill*. Measured
on theta4, the paired gap read -0.22c against an unpaired +5.83c — six cents
apart and opposite in sign. Both are computed here, deliberately, under names
that say which is which.

**The twin is the measurement instrument.** `live_orders` carries no
`model_probability`, and a live position exited early under TP/SL carries a P&L
sign that is not a settlement outcome. Both the modeled probability and the
tail-hit outcome therefore come from the twin, which holds to settlement on the
same market.

**Missing model data is not evidence.** Below the pre-registered coverage
threshold the tail ratio is MISSING, not a value computed on whatever survived.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os.metrics import (
    MIN_TWIN_MODEL_COVERAGE_PCT,
    MetricScope,
    compute_metric,
)
from kalshi_bot.models import Fill, LiveOrder, PaperTrade, Position

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
_SEQ = itertools.count()

LIVE_TAG, TWIN_TAG = "Lbook", "Lbook_pt"


def _scope(kind="live", tags=(LIVE_TAG,), deployments=("bk-live",)):
    return MetricScope(
        experiment_key="bk", version=1, epoch_number=1, arm_key="treatment",
        deployment_kind=kind, strategy_tags=tuple(tags),
        deployment_keys=tuple(deployments), window_start=T0,
        window_end=T0 + timedelta(days=30),
        platform_snapshot_fingerprint="f" * 64,
    )


def _armed(s):
    """A live deployment with its structural twin, through the service layer."""
    from kalshi_bot.experiment_os import service as svc

    exp = svc.create_experiment(s, key="bk", origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="v",
        control_exemption_reason="twin is the control",
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=LIVE_TAG)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    live = svc.register_deployment(
        s, epoch, deployment_key="bk-live", stage="LIVE_CANARY", kind="live",
        arms={"treatment": LIVE_TAG}, started_at=T0, _sanctioned_canary=True,
    )
    svc.register_deployment(
        s, epoch, deployment_key="bk-twin", stage="LIVE_CANARY", kind="paper_twin",
        arms={"treatment": TWIN_TAG}, twin_of=live, started_at=T0,
    )
    s.commit()


def _live(s, ticker, *, contracts=1, realized=0.10, filled=True, side="no"):
    """A live entry. `side="no"` is the real convention for these books: they SELL
    the YES tail by buying NO, so the tail hits when YES resolves."""
    oid = f"o-{next(_SEQ)}"
    s.add(LiveOrder(kalshi_order_id=oid, market_ticker=ticker, strategy=LIVE_TAG,
                    action="buy", side=side, quantity=contracts,
                    status="filled" if filled else "canceled",
                    created_at=T0 + timedelta(hours=1)))
    if not filled:
        return ticker
    s.add(Fill(kalshi_fill_id=f"f-{next(_SEQ)}", kalshi_order_id=oid,
               market_ticker=ticker, quantity=contracts, price=40, fee=0.01,
               filled_at=T0 + timedelta(hours=1)))
    s.add(Position(market_ticker=ticker, captured_at=T0 + timedelta(days=2),
                   quantity=0, realized_pnl=realized))
    return ticker


def _twin(s, ticker, *, pnl=0.10, qty=1, model_p=0.08, resolved=100,
          status="settled", side="no"):
    """The twin leg. On a NO position `resolved_value == 0` means the position
    lost, i.e. YES resolved, i.e. the sold tail HIT."""
    s.add(PaperTrade(market_ticker=ticker, strategy=TWIN_TAG, status=status,
                     side=side, pnl=pnl, quantity=qty, model_probability=model_p,
                     resolved_value=resolved, created_at=T0 + timedelta(hours=1)))


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", [
    "twin_mirror_coverage_pct", "twin_model_coverage_pct",
    "realized_tail_hit_ratio_vs_modeled", "twin_live_gap_cents",
    "twin_live_paired_gap_cents",
])
def test_every_twin_metric_refuses_a_non_live_scope(xos_session, xos_platform, key):
    s = xos_session
    _armed(s)
    mv = compute_metric(s, key, _scope(kind="paper"))
    assert mv.missing is True
    assert mv.provenance["addressing_error"] is True


def test_twin_metrics_are_missing_without_a_registered_twin(xos_session, xos_platform):
    from kalshi_bot.experiment_os import service as svc

    s = xos_session
    exp = svc.create_experiment(s, key="lonely", origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="v",
        control_exemption_reason="test")
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag="Lonely")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="i", started_at=T0)
    svc.register_deployment(s, epoch, deployment_key="lonely-live",
                            stage="LIVE_CANARY", kind="live",
                            arms={"treatment": "Lonely"}, started_at=T0,
                            _sanctioned_canary=True)
    s.commit()
    mv = compute_metric(s, "twin_model_coverage_pct",
                        _scope(tags=("Lonely",), deployments=("lonely-live",)))
    assert mv.missing is True
    assert "no paper_twin deployment is registered" in mv.reason


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_mirror_coverage_counts_markets_ENTERED_not_settled(xos_session, xos_platform):
    """The mirror fires at entry, so a mirror that never fired is invisible in the
    settled set — which is where a 25%-coverage twin hid in production."""
    s = xos_session
    _armed(s)
    for i in range(4):
        _live(s, f"MK-{i}")
    _twin(s, "MK-0")           # only one of four mirrored
    _live(s, "MK-X", filled=False)   # entered, never filled — still entered
    s.commit()

    mv = compute_metric(s, "twin_mirror_coverage_pct", _scope())
    assert mv.provenance["live_markets_entered"] == 5
    assert mv.value == 20.0


def test_model_coverage_denominator_is_the_evidence_set(xos_session, xos_platform):
    """Of the evidence about to decide the gate, how much carries the model
    information the decision requires."""
    s = xos_session
    _armed(s)
    for i in range(4):
        _live(s, f"MC-{i}")
    _twin(s, "MC-0")
    _twin(s, "MC-1")
    _twin(s, "MC-2", model_p=None)   # twin traded it but recorded no model p
    s.commit()

    mv = compute_metric(s, "twin_model_coverage_pct", _scope())
    assert mv.value == 50.0, "2 of 4 settled live markets carry a modeled p"
    assert mv.provenance["uncovered"] == 2


# ---------------------------------------------------------------------------
# The tail ratio
# ---------------------------------------------------------------------------


def _covered_book(s, *, n=20, hits=2, model_p=0.08):
    _armed(s)
    for i in range(n):
        t = _live(s, f"TL-{i}")
        _twin(s, t, model_p=model_p, resolved=0 if i < hits else 100,
              pnl=-0.9 if i < hits else 0.1)
    s.commit()


def test_tail_ratio_is_observed_hits_over_summed_modeled_probability(xos_session,
                                                                     xos_platform):
    s = xos_session
    _covered_book(s, n=20, hits=2, model_p=0.08)
    mv = compute_metric(s, "realized_tail_hit_ratio_vs_modeled", _scope())
    assert mv.provenance["observed"] == 2
    assert mv.provenance["expected"] == pytest.approx(1.6, abs=1e-6)
    assert mv.value == pytest.approx(1.25, abs=1e-4)
    assert mv.n == 20, "n counts MARKETS"


def test_tail_ratio_exposes_the_exact_keys_the_poisson_bound_reads(xos_session,
                                                                   xos_platform):
    """The clause form this metric exists to serve."""
    from kalshi_bot.experiment_os.bounds import compute_bound

    s = xos_session
    _covered_book(s, n=20, hits=2)
    mv = compute_metric(s, "realized_tail_hit_ratio_vs_modeled", _scope())
    br = compute_bound(estimate=mv.value, stderr=mv.stderr,
                       provenance=mv.provenance,
                       spec={"direction": "lower", "confidence": 0.99,
                             "method": "poisson_exact"})
    assert br.missing is False
    assert br.value is not None and br.value < mv.value


def test_the_outcome_comes_from_settlement_not_the_live_pnl_sign(xos_session,
                                                                 xos_platform):
    """A live position exited early under TP/SL loses money without the tail
    hitting. Reading the live P&L sign would inflate O against an unchanged E."""
    s = xos_session
    _armed(s)
    for i in range(20):
        # every live position LOST money (early exits), but the tail resolved
        # out-of-the-money on all of them
        t = _live(s, f"EX-{i}", realized=-0.05)
        _twin(s, t, resolved=100, pnl=0.10)
    s.commit()

    mv = compute_metric(s, "realized_tail_hit_ratio_vs_modeled", _scope())
    assert mv.provenance["observed"] == 0, "no tail hit — only early exits"
    assert mv.value == 0.0


def test_tail_ratio_is_MISSING_below_the_coverage_threshold(xos_session, xos_platform):
    """Missing model data is not evidence against the experiment."""
    s = xos_session
    _armed(s)
    for i in range(10):
        t = _live(s, f"LC-{i}")
        if i < 5:                     # 50% coverage
            _twin(s, t, resolved=0)
    s.commit()

    mv = compute_metric(s, "realized_tail_hit_ratio_vs_modeled", _scope())
    assert mv.missing is True
    assert mv.value is None
    assert "below the pre-registered" in mv.reason
    assert mv.provenance["coverage_pct"] == 50.0


def test_uncovered_markets_are_excluded_never_imputed(xos_session, xos_platform):
    """Imputing the book's mean pulls R toward 1 — toward PASSING."""
    s = xos_session
    _armed(s)
    for i in range(20):
        t = _live(s, f"IM-{i}")
        if i < 19:                    # 95% coverage, above the threshold
            _twin(s, t, model_p=0.08, resolved=0 if i < 2 else 100)
    s.commit()

    mv = compute_metric(s, "realized_tail_hit_ratio_vs_modeled", _scope())
    assert mv.provenance["covered_markets"] == 19
    assert mv.provenance["excluded_uncovered"] == 1
    assert mv.provenance["expected"] == pytest.approx(19 * 0.08, abs=1e-6)


def test_the_coverage_threshold_is_the_preregistered_one():
    assert MIN_TWIN_MODEL_COVERAGE_PCT == 90.0


# ---------------------------------------------------------------------------
# The two gaps measure different things — the finding that decided the design
# ---------------------------------------------------------------------------


def test_unpaired_and_paired_gaps_disagree_when_the_twin_holds_markets_live_missed(
        xos_session, xos_platform):
    """The production shape, in miniature.

    On the markets both books hold, live and twin realize the same thing — the
    paired gap is ~0. The twin ALSO holds quiet winners live never filled, and
    those are exactly the adverse selection, which only the unpaired gap sees."""
    s = xos_session
    _armed(s)
    for i in range(4):                       # shared markets, identical outcomes
        t = _live(s, f"SH-{i}", contracts=1, realized=0.10)
        _twin(s, t, pnl=0.10, qty=1)
    for i in range(4):                       # twin-only winners live never filled
        _twin(s, f"MISS-{i}", pnl=0.50, qty=1)
    s.commit()

    paired = compute_metric(s, "twin_live_paired_gap_cents", _scope())
    unpaired = compute_metric(s, "twin_live_gap_cents", _scope())
    assert paired.value == pytest.approx(0.0, abs=1e-6)
    assert unpaired.value > 15.0, "the missed winners are the adverse selection"
    assert "NOT adverse selection" in paired.provenance["basis"]


def test_paired_gap_needs_both_legs_settled_on_the_same_market(xos_session,
                                                               xos_platform):
    s = xos_session
    _armed(s)
    _live(s, "ONLY-LIVE")
    _twin(s, "ONLY-TWIN")
    s.commit()
    mv = compute_metric(s, "twin_live_paired_gap_cents", _scope())
    assert mv.value is None
    assert "BOTH legs settled" in mv.reason


def test_both_gaps_are_lower_is_better():
    """A gap IS adverse selection, so a positive delta on one means the treatment
    is WORSE — the opposite reading from live_cents_per_contract."""
    from kalshi_bot.experiment_os.metrics import REGISTRY

    for k in ("twin_live_gap_cents", "twin_live_paired_gap_cents"):
        assert REGISTRY[k].direction == "lower_better"
        assert REGISTRY[k].positive_means == "worse"


def test_live_settled_markets_counts_markets_not_contracts(xos_session, xos_platform):
    s = xos_session
    _armed(s)
    _live(s, "M1", contracts=5)
    _live(s, "M2", contracts=3)
    s.commit()
    assert compute_metric(s, "live_settled_markets", _scope()).value == 2.0
    assert compute_metric(s, "live_settled_contracts", _scope()).value == 8.0


# ---------------------------------------------------------------------------
# The twin must be a mirror of the same market AND the same side
#
# `resolved_value` is the settlement value FOR THAT TRADE'S SIDE, not a property
# of the market. Read as a P&L classification it inverts silently whenever the
# twin holds the other side — turning every win into a "tail hit" and doubling
# the numerator against an unchanged denominator.
# ---------------------------------------------------------------------------


def test_the_outcome_is_the_markets_settlement_not_the_twins_pnl_label(
        xos_session, xos_platform):
    """Same market, same settlement, twin on the OPPOSITE side.

    The twin's own trade won (`resolved_value == 100` on its YES), and the live
    NO position lost — the tail DID hit. Copying the twin's P&L label would score
    this as a miss."""
    s = xos_session
    _armed(s)
    for i in range(20):
        t = _live(s, f"SD-{i}", side="no")
        # twin holds YES; YES resolved, so the live NO lost -> tail hit
        _twin(s, t, side="yes", resolved=100, pnl=0.9)
    s.commit()

    mv = compute_metric(s, "realized_tail_hit_ratio_vs_modeled", _scope())
    # every market is excluded as a side mismatch rather than mis-scored
    assert mv.provenance["excluded_side_mismatch"] == 20
    assert mv.missing is True, "an unmirrored twin is not evidence"


def test_a_matched_side_reads_the_market_outcome_correctly(xos_session,
                                                           xos_platform):
    """Both legs on NO. `resolved_value == 0` means the NO lost, i.e. YES
    resolved, i.e. the sold tail hit."""
    s = xos_session
    _armed(s)
    for i in range(20):
        t = _live(s, f"OK-{i}", side="no")
        _twin(s, t, side="no", resolved=0 if i < 3 else 100)
    s.commit()

    mv = compute_metric(s, "realized_tail_hit_ratio_vs_modeled", _scope())
    assert mv.provenance["observed"] == 3
    assert mv.provenance["excluded_side_mismatch"] == 0
    assert "not the twin trade's P&L classification" in mv.provenance["outcome_basis"]


def test_a_market_entered_on_both_sides_is_ambiguous_not_guessed(xos_session,
                                                                 xos_platform):
    s = xos_session
    _armed(s)
    for i in range(19):
        t = _live(s, f"AM-{i}", side="no")
        _twin(s, t, side="no", resolved=100)
    both = _live(s, "AM-BOTH", side="no")
    s.add(LiveOrder(kalshi_order_id=f"o-{next(_SEQ)}", market_ticker=both,
                    strategy=LIVE_TAG, action="buy", side="yes", quantity=1,
                    status="filled", created_at=T0 + timedelta(hours=2)))
    _twin(s, both, side="no", resolved=100)
    s.commit()

    mv = compute_metric(s, "realized_tail_hit_ratio_vs_modeled", _scope())
    assert mv.provenance["excluded_side_ambiguous"] == 1


def test_a_yes_side_book_inverts_the_tail_definition(xos_session, xos_platform):
    """The tail hits when the side the LIVE book sold loses — which is the YES
    outcome for a NO book and the NO outcome for a YES book."""
    s = xos_session
    _armed(s)
    for i in range(20):
        t = _live(s, f"YS-{i}", side="yes")
        # both on YES; resolved_value 0 means the YES lost -> for a YES book that
        # IS the tail hitting
        _twin(s, t, side="yes", resolved=0 if i < 4 else 100)
    s.commit()

    mv = compute_metric(s, "realized_tail_hit_ratio_vs_modeled", _scope())
    assert mv.provenance["observed"] == 4
