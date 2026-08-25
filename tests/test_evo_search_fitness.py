"""The evaluator: components, evidence classification, and the adversarial cases.

These are the tests that keep raw P&L from deciding anything on its own.
"""

from __future__ import annotations

import pytest

from kalshi_bot.evo.search import fitness as f

WEIGHTS = f.resolve_weights(None)
SCALES = f.resolve_scales(None)


def _score(*, trade_cents, net_pnl, max_dd, capital=500.0, months=None, families=3,
           turnover=None, integrity=None, hhi=0.3):
    n = len(trade_cents)
    turnover = turnover if turnover is not None else max(1.0, abs(net_pnl) * 5)
    outcome = {
        "n_trades": n,
        "by_month": months or {"2026-01": {"n": n, "pnl": net_pnl}},
        "by_family": {f"KX{i}": {"n": 1, "pnl": 0.0} for i in range(families)},
        "realizable_cents_per_contract": None,
        "fill_coverage": 0.0,
    }
    ledger = {
        "realized_pnl_usd": net_pnl,
        "turnover_usd": turnover,
        "max_drawdown_usd": max_dd,
        "concentration_hhi": hhi,
        "concentration_top_family": 0.4,
        "return_on_capital": net_pnl / capital,
    }
    comps, score = f.compute(
        outcome=outcome, ledger=ledger, integrity=integrity or {},
        trade_cents=trade_cents, starting_capital_usd=capital,
        weights=WEIGHTS, scales=SCALES,
    )
    return comps, score


# ---------------------------------------------------------------------------
# Evidence classification
# ---------------------------------------------------------------------------


def test_thin_sample_is_insufficient_not_bad():
    cls, why = f.classify_evidence(
        run_status="completed", integrity={}, n_trades=5, min_trades=30
    )
    assert cls == f.EVIDENCE_INSUFFICIENT and "below the search minimum" in why


def test_broken_data_is_invalid_and_checked_before_sample_size():
    """A broken replay's trade count is not evidence, so calling it 'insufficient'
    would invite someone to fix it by widening the window."""
    cls, why = f.classify_evidence(
        run_status="completed",
        integrity={"data_broken": True, "data_broken_reason": "82 corrupt quotes"},
        n_trades=2,
        min_trades=30,
    )
    assert cls == f.EVIDENCE_INVALID and "corrupt quotes" in why


def test_truncated_replay_is_invalid():
    cls, _ = f.classify_evidence(
        run_status="completed", integrity={"truncated": True}, n_trades=500, min_trades=30
    )
    assert cls == f.EVIDENCE_INVALID


def test_a_failed_run_is_invalid():
    cls, _ = f.classify_evidence(
        run_status="refused", integrity={}, n_trades=0, min_trades=30
    )
    assert cls == f.EVIDENCE_INVALID


def test_adequate_sample_is_adequate():
    cls, _ = f.classify_evidence(
        run_status="completed", integrity={}, n_trades=200, min_trades=30
    )
    assert cls == f.EVIDENCE_ADEQUATE


# ---------------------------------------------------------------------------
# The edge lower bound
# ---------------------------------------------------------------------------


def test_lcb_shrinks_toward_zero_as_the_sample_thins():
    wide = [4.0, -2.0, 6.0, 1.0] * 50
    thin = [4.0, -2.0, 6.0, 1.0]
    lcb_wide, mean_wide, _ = f.edge_lower_bound(wide, z=1.645)
    lcb_thin, mean_thin, _ = f.edge_lower_bound(thin, z=1.645)
    assert mean_wide == pytest.approx(mean_thin)
    assert lcb_thin < lcb_wide, "the same mean off fewer trades must bound lower"


def test_a_single_trade_never_credits_its_own_mean():
    lcb, mean, _ = f.edge_lower_bound([80.0], z=1.645)
    assert mean == 80.0
    assert lcb <= 0.0, "one observation carries no dispersion estimate"


def test_empty_tape_bounds_at_zero():
    assert f.edge_lower_bound([], z=1.645) == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# The adversarial ordering
# ---------------------------------------------------------------------------


def test_reckless_does_not_outrank_steady():
    """Nearly double the P&L, bought with a drawdown that would end the account."""
    steady_trades = [14.0, -46.0, 54.0, 14.0] * 30
    reckless_trades = ([58.0] * 60) + ([-77.0] * 40)
    _, steady = _score(trade_cents=steady_trades, net_pnl=45.0, max_dd=25.0)
    _, reckless = _score(trade_cents=reckless_trades, net_pnl=90.0, max_dd=150.0)
    assert reckless > 0 and steady > reckless, (
        f"steady {steady:.4f} must beat reckless {reckless:.4f} despite half the P&L"
    )


def test_drawdown_beyond_tolerance_zeroes_its_component():
    comps, _ = _score(trade_cents=[5.0] * 100, net_pnl=50.0, max_dd=500.0, capital=500.0)
    assert comps["drawdown_control"].score == 0.0


def test_tail_component_prices_the_conditional_loss_not_the_worst_trade():
    mild = [2.0] * 90 + [-20.0] * 10
    severe = [2.0] * 90 + [-79.0] * 10
    comps_mild, _ = _score(trade_cents=mild, net_pnl=10.0, max_dd=10.0)
    comps_severe, _ = _score(trade_cents=severe, net_pnl=10.0, max_dd=10.0)
    assert comps_mild["tail_control"].score > comps_severe["tail_control"].score


def test_integrity_penalises_a_capital_breach():
    clean, _ = _score(trade_cents=[3.0] * 100, net_pnl=20.0, max_dd=10.0)
    breached, _ = _score(
        trade_cents=[3.0] * 100, net_pnl=20.0, max_dd=10.0,
        integrity={"capital_breached": True, "peak_exposure_usd": 900.0},
    )
    assert breached["integrity"].score < clean["integrity"].score


def test_stability_is_smoothed_so_one_good_month_is_not_a_strategy():
    one, _ = _score(
        trade_cents=[5.0] * 40, net_pnl=20.0, max_dd=5.0,
        months={"2026-01": {"n": 40, "pnl": 20.0}},
    )
    four, _ = _score(
        trade_cents=[5.0] * 40, net_pnl=20.0, max_dd=5.0,
        months={f"2026-0{i}": {"n": 10, "pnl": 5.0} for i in range(1, 5)},
    )
    assert four["stability"].score > one["stability"].score


# ---------------------------------------------------------------------------
# Score construction
# ---------------------------------------------------------------------------


def test_score_is_the_sum_of_its_persisted_contributions():
    comps, score = _score(trade_cents=[3.0] * 80, net_pnl=25.0, max_dd=12.0)
    payload = f.components_payload(comps)
    total = sum(c["contribution"] for c in payload.values())
    assert total == pytest.approx(score, abs=1e-5)


def test_zero_edge_scores_mid_scale_not_floor():
    comps, _ = _score(trade_cents=[0.0] * 80, net_pnl=0.0, max_dd=0.0)
    assert comps["edge_lcb"].score == pytest.approx(0.5, abs=0.05)


def test_weights_are_renormalized_so_programs_stay_comparable():
    weights = f.resolve_weights({"edge_lcb": 10.0})
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["edge_lcb"] > f.DEFAULT_WEIGHTS["edge_lcb"]


def test_unknown_or_invalid_weight_overrides_are_ignored():
    weights = f.resolve_weights({"not_a_component": 5.0, "edge_lcb": "banana"})
    assert "not_a_component" not in weights
    assert sum(weights.values()) == pytest.approx(1.0)


def test_all_zero_weights_fall_back_to_defaults():
    weights = f.resolve_weights({k: 0.0 for k in f.DEFAULT_WEIGHTS})
    assert weights == f.DEFAULT_WEIGHTS


def test_explain_names_the_components_that_moved_the_rank():
    comps, _ = _score(trade_cents=[3.0] * 80, net_pnl=25.0, max_dd=12.0)
    text = f.explain(f.components_payload(comps))
    assert "edge_lcb" in text or "drawdown_control" in text
    assert f.explain(None) == "no components recorded"
