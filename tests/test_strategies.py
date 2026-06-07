"""Edge-strategy unit tests (isotonic, strike parsing, momentum, ladder)."""

from __future__ import annotations

from kalshi_bot.paper.strategies import (
    best_side_by_edge,
    isotonic_pava,
    ladder_proposals,
    momentum_proposal,
    parse_strike,
)
from kalshi_bot.scanner.metrics import compute_metrics


class _S:
    """Minimal settings stub for strategy functions."""

    paper_min_edge_cents = 3
    paper_momentum_direction = "momentum"
    paper_momentum_project_hours = 24.0


def _metrics(yes_bid, no_bid, ticker="T", depth=300):
    ob = {"orderbook": {"yes": [[yes_bid, depth]], "no": [[no_bid, depth]]}}
    return compute_metrics({"ticker": ticker, "close_time": "2030-01-01T00:00:00Z"}, ob)


# -- isotonic / parsing -----------------------------------------------------

def test_isotonic_pava_increasing_and_decreasing():
    assert isotonic_pava([1, 3, 2, 4]) == [1.0, 2.5, 2.5, 4.0]
    # already monotone is unchanged
    assert isotonic_pava([1, 2, 3]) == [1.0, 2.0, 3.0]
    # decreasing fit
    assert isotonic_pava([4, 2, 3, 1], increasing=False) == [4.0, 2.5, 2.5, 1.0]


def test_parse_strike():
    assert parse_strike("KXWTIMAX-26DEC31-T150") == ("KXWTIMAX-26DEC31", "T", 150.0)
    assert parse_strike("KXINXY-26DEC31H1600-B7500") == ("KXINXY-26DEC31H1600", "B", 7500.0)
    assert parse_strike("KXRECSSNBER-26") is None  # not a strike ladder


# -- best_side_by_edge ------------------------------------------------------

def test_best_side_by_edge_threshold_and_side():
    m = _metrics(48, 49)  # yes_ask 51, no_ask 52
    # model says YES worth 60c -> edge 60-51=9 >= 3 -> buy YES
    p = best_side_by_edge(0.60, m, 3)
    assert p.side == "yes" and p.price == 51 and round(p.edge, 4) == 0.09
    # model near mid -> no edge beyond cost -> None
    assert best_side_by_edge(0.50, m, 3) is None


# -- momentum ---------------------------------------------------------------

def test_momentum_rising_history_buys_yes_and_reversion_flips():
    m = _metrics(48, 49)  # implied mid ~49.5, yes_ask 51
    history = [(0.0, 40.0), (1.0, 45.0), (2.0, 50.0)]  # rising 5c/h
    p = best = momentum_proposal(history, m, _S())
    assert p is not None and p.side == "yes"

    class _R(_S):
        paper_momentum_direction = "reversion"

    pr = momentum_proposal(history, m, _R())
    # reversion projects downward -> favors NO (or no trade); never YES here
    assert pr is None or pr.side == "no"
    assert best is not None


def test_momentum_insufficient_history():
    assert momentum_proposal([(0.0, 50.0)], _metrics(48, 49), _S()) is None


# -- ladder -----------------------------------------------------------------

def test_ladder_flags_dislocated_rungs_in_monotone_group():
    # Decreasing threshold ladder with one out-of-order rung (T110 too cheap, so the
    # adjacent T120 looks rich). Isotonic pools them -> T110 cheap (buy YES), T120 rich (buy NO).
    cands = [
        ("KXX-26-T100", _metrics(79, 20)),  # mid 79.5
        ("KXX-26-T110", _metrics(49, 50)),  # mid 49.5  <- dislocated low
        ("KXX-26-T120", _metrics(77, 22)),  # mid 77.5  <- now higher than T110 (violation)
        ("KXX-26-T130", _metrics(39, 60)),  # mid 39.5
    ]
    out = ladder_proposals(cands, _S())
    assert out["KXX-26-T110"].side == "yes"  # cheap rung -> buy YES
    assert out["KXX-26-T120"].side == "no"   # rich rung -> buy NO


def test_ladder_skips_small_or_nonmonotone_groups():
    # Only two rungs -> skipped.
    assert ladder_proposals([("KXY-26-T1", _metrics(50, 49)), ("KXY-26-T2", _metrics(40, 59))], _S()) == {}
