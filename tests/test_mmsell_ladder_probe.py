"""The mmsell ladder probe (scripts/mmsell_ladder_probe) — event-shape classification and
overround, pinned independently of the network.

The probe's whole point is that overround is only defined on a PARTITION event (mutually
exclusive, exhaustive outcomes). Kalshi's sports "ladders" are mostly NESTED THRESHOLDS whose
YES legs are a survival function, so summing their mids yields ~n/2 and carries no overround
information. These tests pin that distinction, because misclassifying a threshold ladder as a
partition is exactly the mistake that would make the filter look like it works.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

_SPEC = importlib.util.spec_from_file_location(
    "mmsell_ladder_probe", _SCRIPTS / "mmsell_ladder_probe.py")
lp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lp)  # type: ignore[union-attr]


def _mk(yes_bid, yes_ask, sub=""):
    return {"yes_bid": yes_bid, "yes_ask": yes_ask, "yes_sub_title": sub}


def test_mid_needs_two_sided_quote():
    assert lp._mid(_mk(4, 6)) == 5.0
    assert lp._mid(_mk(None, 6)) is None
    assert lp._mid(_mk(0, 0)) is None


def test_overround_sums_yes_mids():
    ev = {"markets": [_mk(9, 11), _mk(29, 31), _mk(64, 66)]}
    assert lp.overround(ev) == 10.0 + 30.0 + 65.0        # 105 -> 5% overround
    assert lp.overround({"markets": [_mk(9, 11)]}) is None


def test_explicit_mutex_flag_wins_over_geometry():
    # Subtitles read like thresholds, but the payload says mutually exclusive -> PARTITION.
    ev = {"mutually_exclusive": True,
          "markets": [_mk(9, 11, "over 6.5"), _mk(29, 31, "over 7.5"), _mk(64, 66, "over 8.5")]}
    assert lp.classify_event(ev) == "PARTITION"
    ev["mutually_exclusive"] = False
    assert lp.classify_event(ev) == "THRESHOLD"


def test_threshold_ladder_detected_without_a_flag():
    ev = {"markets": [_mk(9, 11, "Over 6.5 runs scored"),
                      _mk(29, 31, "Over 7.5 runs scored"),
                      _mk(64, 66, "Over 8.5 runs scored")]}
    assert lp.classify_event(ev) == "THRESHOLD"


def test_partition_detected_without_a_flag():
    ev = {"markets": [_mk(9, 11, "$110,000 to $111,999"),
                      _mk(29, 31, "$112,000 to $113,999"),
                      _mk(54, 56, "$114,000 to $115,999")]}
    assert lp.classify_event(ev) == "PARTITION"


def test_binary_and_thin_shapes():
    assert lp.classify_event({"markets": [_mk(40, 42), _mk(58, 60)]}) == "BINARY"
    assert lp.classify_event({"markets": [_mk(40, 42)]}) == "THIN"
    assert lp.classify_event({"markets": [_mk(None, None), _mk(1, 2)]}) == "THIN"


def test_candidate_legs_applies_band_and_maxyes():
    ev = {"markets": [
        _mk(4, 6),      # mid 5, ask 6  -> candidate
        _mk(6, 9),      # mid 7.5, ask 9 -> in band but ask above the 7c ceiling
        _mk(1, 2),      # mid 1.5 -> below band
        _mk(40, 42),    # mid 41 -> above band
    ]}
    legs = lp.candidate_legs(ev, 5, 10, 7)
    assert [lp._mid(m) for m in legs] == [5.0]


def test_bucket_count_edges():
    assert lp._bucket_count(2) == "a.2 (binary)"
    assert lp._bucket_count(4) == "b.3-4"
    assert lp._bucket_count(16) == "e.16+"
