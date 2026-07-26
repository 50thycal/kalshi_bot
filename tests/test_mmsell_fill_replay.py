"""The mmsell fill replay (scripts/mmsell_fill_replay) — the per-ticker fill proxy that decides
whether a settled candidate's resting buy-NO would have been lifted before close. Pins the fee
model and the crossing logic independently of the DB.

Fill proxy: a resting buy-NO at entry_no is treated as filled if any LATER no_ask observation is
<= entry_no (the book's touch crossed through our resting level). A miss books 0 P&L (no position),
not the optimistic settle P&L paper assumes.
"""

from __future__ import annotations

import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "mmsell_fill_replay",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mmsell_fill_replay.py",
)
fr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fr)  # type: ignore[union-attr]


def test_fee_matches_paper_formula():
    assert fr.fee_c(92) == 1     # ceil(7*0.92*0.08)=ceil(0.515)
    assert fr.fee_c(50) == 2     # ceil(7*0.25)=ceil(1.75)
    assert fr.fee_c(0) == 0 and fr.fee_c(100) == 0


def test_never_crosses_books_zero_not_optimistic():
    # entry_no=92, path stays above 92 the whole time (no_ask never drops to <=92) -> never filled.
    filled, pnl = fr.replay_fill(92, 100, [98, 95, 93])
    assert filled is False and pnl == 0.0


def test_crossing_level_marks_filled_and_settles():
    # no_ask drops to 90 (<=92) at some point -> filled; settles NO=100 (longshot missed).
    filled, pnl = fr.replay_fill(92, 100, [98, 95, 90, 93])
    assert filled is True
    assert pnl == 100 - 92 - fr.fee_c(92)


def test_filled_but_longshot_hits_loses_stake():
    filled, pnl = fr.replay_fill(92, 0, [98, 90])
    assert filled is True
    assert pnl == 0 - 92 - fr.fee_c(92)


def test_overshoot_still_counts_as_a_cross():
    # a big single-print sweep straight past our level (<=92) still counts -- no need to hit exactly.
    filled, pnl = fr.replay_fill(92, 100, [98, 40])
    assert filled is True and pnl == 100 - 92 - fr.fee_c(92)


def test_none_observations_are_skipped_not_treated_as_a_cross():
    filled, pnl = fr.replay_fill(92, 100, [None, None, 95])
    assert filled is False and pnl == 0.0


def test_empty_path_never_fills():
    filled, pnl = fr.replay_fill(92, 100, [])
    assert filled is False and pnl == 0.0


def test_cell_bucketing_matches_fill_model_bands():
    assert fr._cell(5) == "<=7c" and fr._cell(7) == "<=7c"
    assert fr._cell(8) == "8-11c" and fr._cell(11) == "8-11c"
    assert fr._cell(12) == ">=12c" and fr._cell(40) == ">=12c"


def test_opt_all_and_opt_path_are_computed_over_matching_populations(capsys):
    """Regression: a first draft compared replay_$/ct (over the tiny with-path subset) against
    opt_$/ct averaged over EVERY settled trade ever in the cell -- not the same trades, so a
    handful of replayable samples could look wildly better or worse than reality purely from
    population mismatch. opt_path must share replay's own denominator (with_path), and must be
    computed from the SAME trades -- pinned here with values chosen so all three numbers diverge.
    """
    # cell 8-11c: 3 trades w/ NO path, entry 92 (yes-equiv 8c), settle 100 -> +7.00c each.
    # 1 trade WITH a path, entry 89 (yes-equiv 11c), settle 0 (hit) -> optimistic -90.00c, but its
    # path never drops to <=89 -> the fill proxy says never filled -> replay books 0.00c for it.
    trades = [
        ("T-nopath-1", 92, 100, 10, 20),
        ("T-nopath-2", 92, 100, 10, 20),
        ("T-nopath-3", 92, 100, 10, 20),
        ("T-path", 89, 0, 10, 30),
    ]
    ticks = {"T-path": [(15, 95), (20, 93), (25, 90)]}   # all > 89 -> never crosses -> unfilled
    fr.report(_FakeCursor(trades, ticks))
    out = capsys.readouterr().out
    assert "1/4 settled" in out
    assert "8-11c" in out
    # opt_all blends all 4 trades: (7+7+7-90)/4 = -17.25c.
    assert "-17.25c" in out
    # opt_path is just the 1 replayable trade's own optimistic number: -90.00c.
    assert "-90.00c" in out
    # replay books 0 for it (fill proxy says the book never crossed our resting level).
    assert "+0.00c" in out
    # 0/1 replayable trades filled.
    assert "0%" in out


def test_report_handles_no_settled_trades(capsys):
    fr.report(_FakeCursor([], {}))
    out = capsys.readouterr().out
    assert "no settled mmsell control-book trades" in out


def test_report_handles_zero_path_coverage(capsys):
    # one settled trade, but no captured candidate ticks at all -> data-maturity message, no crash.
    trades = [("T-A", 92, 100, 10, 20)]
    fr.report(_FakeCursor(trades, {}))
    out = capsys.readouterr().out
    assert "0/1 settled" in out
    assert "Data-maturity wait" in out


def test_report_prints_cell_breakdown_with_replayable_path(capsys):
    # entry 92c (yes-equiv 8c -> "8-11c" cell); path crosses (no_ask 90 <= 92) -> filled, settles win.
    trades = [("T-A", 92, 100, 10, 30)]
    ticks = {"T-A": [(15, 95), (20, 90), (40, 85)]}   # last tick at ts=40 is AFTER closed_at=30
    fr.report(_FakeCursor(trades, ticks))
    out = capsys.readouterr().out
    assert "1/1 settled" in out
    assert "8-11c" in out


class _FakeCursor:
    """Mimics the two cur.execute()/fetchall() calls _load() makes, without a DB."""

    def __init__(self, trades, ticks_by_ticker):
        # trades: list of (ticker, assumed_price, resolved_value, created_at, closed_at)
        self._trades = trades
        self._ticks = ticks_by_ticker
        self._pending = None

    def execute(self, sql, params=None):
        self._pending = "trades" if "paper_trades" in sql else "ticks"

    def fetchall(self):
        if self._pending == "trades":
            return list(self._trades)
        rows = []
        for tk, pts in self._ticks.items():
            for ts, no_ask in pts:
                rows.append((tk, ts, no_ask))
        return rows
