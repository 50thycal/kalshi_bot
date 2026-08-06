"""The mmsell market-type census (scripts/mmsell_market_types) — the taxonomy that turns 118
traded series into a decision table by CONTRACT STRUCTURE rather than by sport or price.

Two things have to be pinned here, both independent of the DB:
  * the classifier — longest-prefix, so a specific series is never swallowed by a shorter one
    that happens to prefix it (KXWC1HTOTAL is a total, not the 1st-half winner), and an
    unknown series falls to `unclassified` rather than being guessed into a bucket;
  * the stats — percentile convention and per-contract normalization, since the whole point of
    the table is comparing p5 tails across types.
"""

from __future__ import annotations

import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "mmsell_market_types",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mmsell_market_types.py",
)
mt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mt)  # type: ignore[union-attr]


def test_series_of_strips_event_and_outcome():
    assert mt.series_of("KXMLBGAME-26AUG012110BOSLAD-BOS") == "KXMLBGAME"
    assert mt.series_of("KXBTCD-26AUG0117-T63249.99") == "KXBTCD"
    assert mt.series_of("") == ""


def test_longest_prefix_wins_over_shorter_sibling():
    # The traps the ordering-independent longest-prefix match exists for.
    assert mt.classify("KXWC1HTOTAL")[0] == "total"          # not h2h_period via KXWC1H
    assert mt.classify("KXWC1H")[0] == "h2h_period"
    assert mt.classify("KXWC1HBTTS")[0] == "game_prop"
    assert mt.classify("KXMLBHRDERBYMATCHUP")[0] == "h2h"    # not outright via KXMLBHRDERBY
    assert mt.classify("KXMLBHRDERBY")[0] == "outright"
    assert mt.classify("KXMLBHRDERBYOU")[0] == "total"
    assert mt.classify("KXATPCHALLENGERMATCH")[0] == "h2h"
    assert mt.classify("KXFEDMENTION")[0] == "mention"       # not econ_release via KXFED
    assert mt.classify("KXFED")[0] == "econ_release"


def test_settle_mode_split_is_the_timing_axis():
    assert mt.classify("KXMLBGAME")[1] == mt.IN_PLAY
    assert mt.classify("KXBTCD")[1] == mt.SCHEDULED
    assert mt.classify("KXTRUMPSAY")[1] == mt.DISCRETE


def test_unknown_series_is_flagged_not_guessed():
    assert mt.classify("KXSOMETHINGNEW") == mt.UNCLASSIFIED
    assert mt.classify("") == mt.UNCLASSIFIED


def test_taxonomy_table_has_no_duplicate_prefixes():
    prefixes = [p for p, _, _ in mt.SERIES_TYPES]
    assert len(prefixes) == len(set(prefixes))


def test_pctile_interpolates_and_handles_edges():
    vals = [0.0, 10.0, 20.0, 30.0, 40.0]
    assert mt.pctile(vals, 0.0) == 0.0
    assert mt.pctile(vals, 1.0) == 40.0
    assert mt.pctile(vals, 0.5) == 20.0
    assert mt.pctile(vals, 0.25) == 10.0
    assert mt.pctile([], 0.5) is None
    assert mt.pctile([7.0], 0.05) == 7.0


def _row(pnl_c, ticker="KXMLBGAME-A-X", book="mmsell", entry_c=6, hold_h=1.0):
    return {"pnl_c": pnl_c, "ticker": ticker, "book": book, "entry_c": entry_c, "hold_h": hold_h}


def test_summarize_reports_the_seller_shape():
    # 19 small wins and one catastrophic loss — the mmsell payoff, where the mean and the tail
    # disagree and win% says nothing.
    rows = [_row(5.0, ticker=f"T{i}") for i in range(19)] + [_row(-92.0, ticker="T19")]
    s = mt.summarize(rows)
    assert s["n"] == 20 and s["mkts"] == 20 and s["books"] == 1
    assert s["win"] == 0.95
    assert s["worst"] == -92.0
    assert s["p50"] == 5.0 and s["p95"] == 5.0      # the premium
    # The trap the loss_rate/avg_loss columns exist for: with 1 loss in 20, p5 interpolates
    # between the loss and the premium and prints POSITIVE despite a -92c trade in the cell.
    assert s["p5"] > 0
    assert s["loss_rate"] == 0.05 and s["avg_loss"] == -92.0
    assert round(s["mean"], 2) == round((19 * 5.0 - 92.0) / 20, 2)
    assert round(s["total"], 4) == round((19 * 5.0 - 92.0) / 100.0, 4)


def test_summarize_counts_distinct_markets_not_book_trades():
    # Same ticker traded by three books = 3 correlated trades, 1 independent market.
    rows = [_row(4.0, ticker="SAME", book=b) for b in ("mmsell", "mmsell1", "mmsell2")]
    s = mt.summarize(rows)
    assert s["n"] == 3 and s["mkts"] == 1 and s["books"] == 3


def test_summarize_empty():
    assert mt.summarize([]) == {"n": 0}


def test_breakeven_margin_normalizes_out_the_entry_price():
    # Two cells with the SAME 10% loss rate but different premiums. The cheap one is losing
    # money and the rich one is making it — which the raw loss rate cannot tell you and the
    # edge column can. This is the whole reason the column exists.
    cheap = [_row(5.0, ticker=f"c{i}") for i in range(9)] + [_row(-95.0, ticker="c9")]
    rich = [_row(20.0, ticker=f"r{i}") for i in range(9)] + [_row(-80.0, ticker="r9")]
    sc, sr = mt.summarize(cheap), mt.summarize(rich)
    assert sc["loss_rate"] == sr["loss_rate"] == 0.10
    assert sc["mean"] < 0 < sr["mean"]
    assert sc["edge_pp"] < 0 < sr["edge_pp"]
    # be% is W/(W+|L|): 5/(5+95)=5%, 20/(20+80)=20%.
    assert round(sc["be_loss"], 4) == 0.05
    assert round(sr["be_loss"], 4) == 0.20


def test_breakeven_undefined_without_both_outcomes():
    assert mt.summarize([_row(5.0, ticker="a"), _row(5.0, ticker="b")])["be_loss"] is None
    assert mt.summarize([_row(-9.0, ticker="a")])["edge_pp"] is None


def test_announcement_series_is_classified():
    # The one series the first census run reported as unclassified.
    assert mt.classify("KXNBATEAMANNOUNCE") == ("announcement", mt.DISCRETE)


# ------------------------------------------------------------------ the LINE within a type


def test_line_parses_the_handicap_or_total():
    assert mt.line_of("KXMLBTOTAL-26AUG021510KCCOL-12") == 12.0
    assert mt.line_of("KXMLBSPREAD-26AUG021515MILLAA-LAA3") == 3.0   # line sits after a team code
    assert mt.line_of("KXWNBATOTAL-26AUG02LAPDX-200") == 200.0
    assert mt.line_of("KXWCTEAMTOTAL-26JUL03ARGCPV-ARG3") == 3.0
    assert mt.line_of("KXWCSPREAD-26JUL03ARGCPV-ARG2") == 2.0


def test_line_is_an_allowlist_because_the_generic_parse_is_wrong_elsewhere():
    """A blanket 'trailing digits' rule silently mislabels other series. An exact-score suffix
    (ARG1CPV0) would yield 0, which is not a line at all; a player-prop suffix carries a jersey
    number. Series outside LINE_SERIES must report None rather than a wrong number."""
    assert mt.line_of("KXWCSCORE-26JUL03ARGCPV-ARG1CPV0") is None       # exact score, not a line
    assert mt.line_of("KXMLBHR-26AUG021920BOSLAD-BOSAMONASTERIO32-1") is None  # jersey number
    assert mt.line_of("KXMLBGAME-26AUG012110BOSLAD-BOS") is None        # h2h has no line
    assert mt.line_of("KXBTCD-26AUG0117-T63249.99") is None             # strike, not a line


def test_line_returns_none_rather_than_guessing():
    assert mt.line_of("") is None
    assert mt.line_of("KXMLBTOTAL") is None                 # no outcome token
    assert mt.line_of("KXMLBTOTAL-26AUG021510KCCOL") is None  # only two segments
    assert mt.line_of("KXMLBTOTAL-26AUG02KCCOL-OVER") is None  # no trailing digits


def test_line_series_are_all_in_the_taxonomy():
    """A series that can report a line but has no type would appear in the line table and
    nowhere else, which would read as a new bucket rather than a gap."""
    for ser in mt.LINE_SERIES:
        assert mt.classify(ser) != mt.UNCLASSIFIED, f"{ser} has a line rule but no type"
