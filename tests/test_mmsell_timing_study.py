"""The mmsell entry-timing study (scripts/mmsell_timing_study).

The whole design rests on one finding: `hours_to_close` is a valid clock for scheduled/discrete
markets and a fiction for in-play ones (Kalshi sets a sports market's close_time to a far-future
fallback, so UFC reads 335h-to-close on a fight that resolves in 0.4h). Get the clock wrong and
every in-play trade files under "24-72h" and the study measures nothing while looking fine.

So the tests pin:
  * the per-mode clock mapping — the finding itself;
  * bucketing, including that a negative/None clock value drops out as coverage loss rather than
    silently flooring into the shortest bucket, where it would read as a fake late-entry edge;
  * that the grids are well-formed (ascending, first bucket open at zero), since a mis-ordered
    grid mislabels every cell without erroring.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

_SPEC = importlib.util.spec_from_file_location(
    "mmsell_timing_study", SCRIPTS / "mmsell_timing_study.py")
ts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ts)  # type: ignore[union-attr]


def test_each_mode_is_scored_on_the_right_clock():
    """This mapping IS the finding. in_play must never be scored on hours_to_close."""
    assert ts.CLOCK[ts.mt.IN_PLAY] == "hold"
    assert ts.CLOCK[ts.mt.SCHEDULED] == "htc"
    assert ts.CLOCK[ts.mt.DISCRETE] == "htc"


def test_clock_hours_picks_the_field_the_mode_mapping_names():
    inplay = {"mode": ts.mt.IN_PLAY, "hold_h": 1.4, "htc": 335.0}
    sched = {"mode": ts.mt.SCHEDULED, "hold_h": 30.6, "htc": 31.0}
    # The in-play row must report 1.4, NOT the 335 that close_time claims.
    assert ts.clock_hours(inplay) == 1.4
    assert ts.clock_hours(sched) == 31.0


def test_window_grids_are_well_formed():
    for mode, grid in ts.WINDOWS.items():
        lows = [lo for _, lo in grid]
        assert lows[0] == 0.0, f"{mode} grid must open at zero"
        assert lows == sorted(lows), f"{mode} grid must ascend"
        assert len(set(lows)) == len(lows), f"{mode} grid has duplicate bounds"
        assert len({lbl for lbl, _ in grid}) == len(grid), f"{mode} grid has duplicate labels"


def test_bucketing_is_lower_bound_inclusive():
    m = ts.mt.SCHEDULED
    assert ts.bucket_of(0.0, m) == "<2h"
    assert ts.bucket_of(1.9, m) == "<2h"
    assert ts.bucket_of(2.0, m) == "2-4h"      # boundary belongs to the upper cell
    assert ts.bucket_of(7.99, m) == "4-8h"
    assert ts.bucket_of(8.0, m) == "8-24h"
    assert ts.bucket_of(72.0, m) == "72h+"
    assert ts.bucket_of(5000.0, m) == "72h+"


def test_in_play_grid_resolves_the_sub_hour_detail():
    m = ts.mt.IN_PLAY
    # The in-play book actually enters with 0.4-1.5h of contest left, so the interesting
    # structure is BELOW one hour — a scheduled-style grid would put it all in one cell.
    assert ts.bucket_of(0.1, m) == "<0.25h"
    assert ts.bucket_of(0.4, m) == "0.25-0.5h"
    assert ts.bucket_of(0.6, m) == "0.5-1h"
    assert ts.bucket_of(1.5, m) == "1-2h"
    assert ts.bucket_of(20.0, m) == "12h+"


def test_missing_or_impossible_clock_drops_out_instead_of_flooring():
    """An entry cannot post-date its own resolution. Flooring a negative into the shortest
    bucket would manufacture exactly the late-entry edge the study is trying to test."""
    m = ts.mt.IN_PLAY
    assert ts.bucket_of(None, m) is None
    assert ts.bucket_of(-0.5, m) is None
    assert ts.bucket_of(1.0, "no_such_mode") is None
    assert ts.clock_hours({"mode": ts.mt.SCHEDULED, "hold_h": 3.0, "htc": None}) is None


def test_taxonomy_is_imported_not_redeclared():
    """A third copy of the type table would let a type mean one thing in the census and another
    here. The study must use the census module's classifier."""
    import mmsell_market_types as canonical
    assert ts.mt is canonical
    assert ts.mt.classify("KXUFCFIGHT") == ("h2h", ts.mt.IN_PLAY)
    assert ts.mt.classify("KXBTCD") == ("price_strike", ts.mt.SCHEDULED)
