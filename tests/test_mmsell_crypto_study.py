"""The crypto cheap-tail backtest math (scripts/mmsell_crypto_study) — the replay, the
volatility measure, and the strangle pairing that decide whether the BTC/ETH tail-sell can
carry a larger "anchor" allocation.

Pins the two things most likely to silently produce a wrong answer:
  * the stop TRIGGERS on the yes-BID by default with a K-consecutive confirm, and EXITS by
    paying the ASK. An ask-triggered stop is what made a previous candle backtest return
    -93c/100%-exit for every rule (thin books throw spurious high ask prints at these prices);
    a MID-triggered stop is contaminated the same way whenever the book is wide, which is why
    the bid — real buying interest at that level — is the default.
  * a strangle's two legs are MUTUALLY EXCLUSIVE — both can never lose. If that invariant ever
    breaks, the whole "collect two premiums, risk one" argument is void.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))  # module imports kalshi_flb / xvenue_leadlag as siblings
_SPEC = importlib.util.spec_from_file_location(
    "mmsell_crypto_study", _SCRIPTS / "mmsell_crypto_study.py")
cs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cs)  # type: ignore[union-attr]


# ---------------------------------------------------------------- fees / vol

def test_fee_matches_paper_engine():
    assert cs.fee_c(6) == 1.0      # ceil(0.07*0.06*0.94*100)=1 -> 1c
    assert cs.fee_c(40) == 2.0     # ceil(1.68)=2 -> 2c
    assert cs.fee_c(15) == 1.0


def test_mid_range_is_peak_to_trough():
    assert cs.mid_range([6, 8, 5, 9]) == 4
    assert cs.mid_range([7]) == 0.0
    assert cs.mid_range([]) == 0.0
    assert cs.mid_range([6, None, 10]) == 4


# ------------------------------------------------------------------- replay

def test_hold_keeps_premium_when_longshot_misses():
    pnl, kind = cs.replay_short(6, 0, [(7, 6, 8), (5, 4, 6)])
    assert kind == "settle" and pnl == 6 - 0 - 1        # +5c


def test_hold_loses_near_full_stake_when_longshot_hits():
    pnl, kind = cs.replay_short(6, 100, [(20, 18, 22), (80, 78, 82)])
    assert kind == "settle" and pnl == 6 - 100 - 1      # -95c, the tail a stop targets


def test_confirmed_stop_fires_and_pays_the_ask():
    # mid crosses 30 for 2 consecutive minutes -> exit paying the ASK (40), not the mid.
    pnl, kind = cs.replay_short(6, 100, [(10, 9, 11), (35, 33, 37), (38, 36, 40)], stop_l=30, stop_k=2)
    assert kind == "stop"
    assert pnl == 6 - 40 - cs.fee_c(6) - cs.fee_c(40)   # -37c, far better than -95c holding
    assert pnl > (6 - 100 - 1)


def test_k2_confirm_defeats_the_single_print_whipsaw():
    # ONE spurious minute above the level then back down: K=2 must hold (and win).
    spike = [(10, 9, 11), (35, 33, 37), (12, 11, 13)]
    pnl2, kind2 = cs.replay_short(6, 0, spike, stop_l=30, stop_k=2)
    assert kind2 == "settle" and pnl2 == 5
    # K=1 whipsaws out of that same winner for a needless loss -- why the confirm exists.
    pnl1, kind1 = cs.replay_short(6, 0, spike, stop_l=30, stop_k=1)
    assert kind1 == "stop" and pnl1 < 0


def test_stop_never_triggers_on_a_high_ask_alone():
    # The regression guard for the artifact that broke the previous backtest: a wide ask (60)
    # with a calm MID (8) must NOT fire the stop.
    pnl, kind = cs.replay_short(6, 0, [(8, 7, 60), (8, 7, 55), (7, 6, 58)], stop_l=30, stop_k=2)
    assert kind == "settle" and pnl == 5


def test_volatility_exit_fires_on_range():
    pnl, kind = cs.replay_short(6, 100, [(6, 5, 7), (8, 7, 9), (14, 13, 15)], vol_w=15, vol_v=6)
    assert kind == "vol"
    assert pnl == 6 - 15 - cs.fee_c(6) - cs.fee_c(15)


def test_volatility_exit_ignores_calm_tape():
    pnl, kind = cs.replay_short(6, 0, [(6, 5, 7), (7, 6, 8), (6, 5, 7), (7, 6, 8)], vol_w=15, vol_v=6)
    assert kind == "settle" and pnl == 5


def test_bid_trigger_ignores_a_wide_book_that_fools_the_mid():
    """A wide book (bid 8, ask 62 -> mid 35) is the exact contamination that destroyed the
    mid-triggered K=1 rows live: the MID clears a 30c stop while no real buyer is above 8c.
    The default BID trigger must hold; the MID trigger fires. This is the whole reason for the
    trigger parameter."""
    wide = [(35, 8, 62), (35, 8, 62)]
    assert cs.replay_short(6, 0, wide, stop_l=30, stop_k=2, trigger="bid")[1] == "settle"
    assert cs.replay_short(6, 0, wide, stop_l=30, stop_k=2, trigger="mid")[1] == "stop"


def test_bid_trigger_still_fires_on_genuine_buying():
    # A real bid climbing through the level must stop out under either trigger.
    real = [(32, 30, 34), (36, 34, 38)]
    assert cs.replay_short(6, 100, real, stop_l=30, stop_k=2, trigger="bid")[1] == "stop"


def test_none_entries_in_path_are_skipped():
    pnl, kind = cs.replay_short(6, 100, [(None, None, None), (35, 33, 37), (38, 36, 40)],
                                stop_l=30, stop_k=2)
    assert kind == "stop"


# ----------------------------------------------------------------- strangle

def test_event_key_drops_the_strike_segment():
    assert cs.event_key("KXBTCD-26JUL1917-T64749.99") == "KXBTCD-26JUL1917"
    assert cs.event_key("KXETHD-26JUL28-T4000") == "KXETHD-26JUL28"
    assert cs.event_key("SINGLE") == "SINGLE"


def test_strangle_collects_both_premiums_when_neither_tail_hits():
    # high strike: yes 6c settles 0 (not exceeded). low strike: yes 94c settles 100 (held above).
    total, lost = cs.pair_strangle((6, 0), (94, 100))
    assert lost == 0
    assert total == 10.0            # +5c from each leg


def test_strangle_loses_only_one_leg_when_a_tail_breaks():
    # BTC crashes through the LOW strike: that leg's cheap NO settles 100 -> big loss; the high
    # strike still expires worthless -> keeps its premium.
    total, lost = cs.pair_strangle((6, 0), (94, 0))
    assert lost == 1
    assert total == 5.0 + (6 - 100 - 1)      # +5 - 95 = -90c


def test_strangle_legs_are_mutually_exclusive():
    """The invariant the whole 'collect two, risk one' case rests on: for any real settlement,
    both legs can never lose. yes settles either 0 or 100 for BOTH strikes of one event."""
    for settle in (0.0, 100.0):
        _total, lost = cs.pair_strangle((6, settle), (94, settle))
        assert lost <= 1, f"both legs lost at settle={settle} — pairing argument is void"
