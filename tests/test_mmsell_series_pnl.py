"""Per-series loss detector — the read that was missing when KXNFLSPREAD lost $166.55.

WHY THIS EXISTS. `mmsell_market_types` aggregates 400 series into 15 contract types;
`mmsell_universe_review` ranks series by coverage; no gate scores a series at all. So a single
graduated series gave back a third of the family's 30-day paper P&L across 382 markets and
nothing in the repo would have said so. `scripts/mmsell_series_pnl.py` is that read.

What must never break, in the order in which breaking it would matter:

  * **`edge` must mean the same thing here as in `mmsell_market_types`.** It is the only column
    comparable across series, and two definitions of it competing in two reports is worse than
    not having it — a reader would compare the numbers.
  * **the premium is the TAIL's price, not `assumed_price`.** mmsell sells a cheap YES tail by
    BUYING NO, so a 93c row is a 7c tail. Reading the column raw puts every ordinary entry in
    the "21c+" band and makes the cheap-band cut — the live regime — vanish. That is exactly the
    bug this investigation hit on its first query.
  * **the independence denominator is the CONTEST.** 382 KXNFLSPREAD markets are 44 preseason
    games, and 2 of those games carried 48% of the loss. Counting markets as independent bets
    overstates n by ~9x on a nested ladder.
  * **it stays a report.** Nothing here may return a verdict, a gate result or a promotion.
"""

from __future__ import annotations

import importlib.util
import pathlib

from scripts import ops_runner


def _script():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mmsell_series_pnl.py"
    spec = importlib.util.spec_from_file_location("mmsell_series_pnl", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _script()


# ------------------------------------------------------------------ the grouping keys


def test_series_is_the_ticker_prefix():
    assert mod.series_of("KXNFLSPREAD-26AUG13GBPIT-GB3") == "KXNFLSPREAD"


def test_contest_groups_every_rung_and_both_sides_of_one_game():
    """The whole point: one blowout resolves a nested two-sided ladder at one instant."""
    game = {
        mod.contest_of("KXNFLSPREAD-26AUG13GBPIT-GB3"),
        mod.contest_of("KXNFLSPREAD-26AUG13GBPIT-GB10"),
        mod.contest_of("KXNFLSPREAD-26AUG13GBPIT-PIT7"),
    }
    assert game == {"KXNFLSPREAD:26AUG13GBPIT"}


def test_contest_does_not_group_across_series():
    """Deliberately weaker than the worker's `contest_key_of`, which is regime-namespaced.
    Under-counting correlation is safe here; inventing it is not."""
    assert mod.contest_of("KXNFLSPREAD-26AUG13GBPIT-GB3") != \
        mod.contest_of("KXNFLTOTAL-26AUG13GBPIT-40")


def test_a_ticker_with_no_event_token_is_its_own_contest():
    assert mod.contest_of("KXPAYROLLS") == "KXPAYROLLS"


# ------------------------------------------------------------------ break-even and edge


def test_breakeven_matches_the_market_types_definition():
    """One definition of `edge` across both reports, pinned on shared fixtures."""
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mmsell_market_types.py"
    spec = importlib.util.spec_from_file_location("mmsell_market_types", path)
    types_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(types_mod)

    for pnls in ([6.5] * 19 + [-93.5], [7.0] * 8 + [-93.0] * 2, [5.0, -95.0, 5.0, 5.0]):
        wins = sum(1 for p in pnls if p > 0)
        theirs = types_mod._breakeven(sorted(pnls), wins)
        be, edge = mod.breakeven(pnls)
        assert be == theirs["be_loss"]
        assert edge == theirs["edge_pp"]


def test_edge_is_negative_when_the_cell_does_not_pay_for_its_tail():
    # 6.6c premium breaks even at a 6.6% loss rate; this cell loses 20% of the time.
    pnls = [6.6] * 16 + [-93.4] * 4
    be, edge = mod.breakeven(pnls)
    assert round(100 * be, 1) == 6.6
    assert edge < 0


def test_a_cell_with_no_losses_has_no_edge_rather_than_a_perfect_one():
    assert mod.breakeven([6.0, 6.0, 6.0]) == (None, None)


# ------------------------------------------------------------------ the cell summary


def _trade(ticker, pnl_c, book="mmsell10", entry_c=6.5, live=False):
    return {"ticker": ticker, "series": mod.series_of(ticker), "contest": mod.contest_of(ticker),
            "book": book, "live": live, "entry_c": entry_c, "pnl_c": pnl_c}


def test_contests_are_counted_separately_from_markets():
    rows = [_trade(f"KXNFLSPREAD-26AUG13GBPIT-GB{i}", 6.5) for i in range(10)]
    rows += [_trade("KXNFLSPREAD-26AUG15CLECHI-CLE3", 6.5)]
    s = mod.summarize(rows)
    assert (s["n"], s["mkts"], s["contests"]) == (11, 11, 2)


def test_worst3_share_finds_a_concentrated_loss():
    """Two catastrophic games against a long tail of small winners."""
    rows = [_trade("KXNFLSPREAD-26AUG13GBPIT-GB3", -93.4),
            _trade("KXNFLSPREAD-26AUG15CLECHI-CLE3", -93.4)]
    rows += [_trade(f"KXNFLSPREAD-26AUG2{i}AAABBB-X", 6.5) for i in range(10)]
    s = mod.summarize(rows)
    assert s["total"] < 0
    assert s["worst3_share"] > 0.9


def test_worst3_share_is_absent_on_a_profitable_cell():
    """A share of a positive total would read as a loss attribution. There is nothing to attribute."""
    s = mod.summarize([_trade(f"KXA-{i}-X", 6.5) for i in range(5)])
    assert s["worst3_share"] is None


def test_live_lists_only_the_books_that_placed_real_orders():
    rows = [_trade("KXNFLSPREAD-26AUG13GBPIT-GB3", -93.4, book="Cmmsell10", live=True),
            _trade("KXNFLSPREAD-26AUG13GBPIT-GB7", 6.5, book="mmsell5", live=False)]
    assert mod.summarize(rows)["live"] == ["Cmmsell10"]


# ------------------------------------------------------------------ it must be runnable


def test_the_script_is_allowlisted_on_the_ops_channel():
    assert "mmsell_series_pnl" in ops_runner.ALLOWED_SCRIPTS


def test_it_is_a_report_and_says_so():
    """A per-series P&L GATE on this book would fire constantly on noise (roadmap §1). The
    docstring is where the next session learns that, so it is load-bearing text."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mmsell_series_pnl.py").read_text()
    assert "REPORT, NOT A GATE" in src
    assert "authorizes nothing" in src
