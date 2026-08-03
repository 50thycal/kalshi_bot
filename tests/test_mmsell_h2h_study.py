"""The head-to-head study (scripts/mmsell_h2h_study) — classification, P&L and break-even maths,
pinned without the network.

The clocked/unclocked split IS the hypothesis under test, so misclassifying a series would make
the result meaningless in a way no amount of extra data would reveal. These tests pin it.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

_SPEC = importlib.util.spec_from_file_location(
    "mmsell_h2h_study", _SCRIPTS / "mmsell_h2h_study.py")
h2h = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(h2h)  # type: ignore[union-attr]


# ------------------------------------------------------------------ classification

def test_unclocked_sports_classified():
    assert h2h.classify("KXMLBGAME") == ("baseball", False)
    assert h2h.classify("KXATPMATCH") == ("tennis", False)
    assert h2h.classify("KXT20MATCH") == ("cricket", False)


def test_clocked_sports_classified():
    assert h2h.classify("KXNBAGAME") == ("basketball", True)
    assert h2h.classify("KXWCGAME") == ("soccer", True)
    assert h2h.classify("KXNFLGAME") == ("football", True)


def test_longest_prefix_wins():
    # KXATPCHALLENGERMATCH must not be swallowed by the KXATPMATCH entry (it isn't a prefix, but
    # the resolver must be prefix-length ordered for cases like it).
    assert h2h.classify("KXATPCHALLENGERMATCH") == ("tennis", False)


def test_non_h2h_series_rejected():
    # These are the cells the roadmap found healthy — they must never enter this study.
    for s in ("KXMLBTOTAL", "KXMLBSPREAD", "KXBTCD", "KXWCGOAL", "KXWCMENTION", ""):
        assert h2h.classify(s) is None


# ------------------------------------------------------------------ P&L maths

def test_sell_yes_pnl_win_keeps_premium_less_fee():
    # sell at 6c, tail misses -> +6 - fee(6). fee = ceil(7*0.06*0.94) = ceil(0.39) = 1
    assert h2h.sell_yes_pnl(6.0, settled_yes=False) == 6.0 - 1.0


def test_sell_yes_pnl_loss_is_the_whole_other_side():
    assert h2h.sell_yes_pnl(6.0, settled_yes=True) == -(100.0 - 6.0) - 1.0


def test_break_even_matches_the_roadmap_number():
    # A ~6.5c sell against a ~94c risk breaks even near 5.5% — the roadmap's headline bar.
    be = h2h.break_even_loss_rate(6.5)
    assert 5.0 < be < 6.0


def test_break_even_rises_with_price():
    assert h2h.break_even_loss_rate(3.0) < h2h.break_even_loss_rate(10.0)


# ------------------------------------------------------------------ entry selection

def _seq(*rows):
    """rows: (minute, yes_bid, yes_ask)"""
    return list(rows)


def test_first_entry_picks_first_in_band_inside_window():
    close_min = 1000
    seq = _seq((800, 20, 22), (960, 5, 7), (980, 4, 6))
    idx, saw = h2h.first_entry(seq, close_min, 3, 10, htc_max_h=6.0)
    assert idx == 1 and saw is True


def test_first_entry_rejects_outside_the_window():
    close_min = 1000
    # in band, but 10h before close -> outside a 6h in-play window
    seq = _seq((400, 5, 7),)
    idx, saw = h2h.first_entry(seq, close_min, 3, 10, htc_max_h=6.0)
    assert idx is None and saw is True   # saw the band, just not in the window


def test_first_entry_reports_band_never_seen():
    idx, saw = h2h.first_entry(_seq((990, 40, 42)), 1000, 3, 10, htc_max_h=6.0)
    assert idx is None and saw is False


def test_first_entry_skips_one_sided_quotes():
    idx, _saw = h2h.first_entry(_seq((990, None, 6), (995, 4, 6)), 1000, 3, 10, htc_max_h=6.0)
    assert idx == 1


# ------------------------------------------------------------------ aggregation

def _e(px, settled_yes, htc=1.0):
    return {"sell_px": px, "settled_yes": settled_yes, "htc": htc,
            "pnl": h2h.sell_yes_pnl(px, settled_yes)}


def test_summarize_counts_losses_and_mean():
    es = [_e(6.0, False), _e(6.0, False), _e(6.0, True)]
    st = h2h.summarize(es)
    assert st["n"] == 3 and st["losses"] == 1
    assert round(st["loss_pct"], 2) == round(100 / 3, 2)
    assert round(st["mean"], 4) == round((5.0 + 5.0 - 95.0) / 3, 4)


def test_summarize_implied_is_the_average_sell_price():
    st = h2h.summarize([_e(5.0, False), _e(9.0, False)])
    assert st["implied_pct"] == 7.0


def test_summarize_empty_is_none_not_a_crash():
    assert h2h.summarize([]) is None


def test_wilson_interval_brackets_the_point_estimate():
    lo, hi = h2h.wilson(3, 27)
    assert lo < 3 / 27 < hi
    assert 0.03 < lo < 0.05 and 0.25 < hi < 0.30   # the roadmap's [3.9%, 28.1%]
