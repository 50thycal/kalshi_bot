"""Tests for the inline-quote pre-filter experiment (docs/MMSELL_QUOTE_PARITY.md).

The accumulator decides whether the mmsell scan may trust the event page's inline quote to
skip most orderbook fetches. The number that decides it is the FALSE NEGATIVE count — an
in-band market the pre-filter would reject, whose orderbook therefore never gets fetched and
which silently stops existing for every book. These tests pin that arithmetic, because getting
it wrong in the optimistic direction would read as "safe to ship" on a filter that quietly
starves the live book.
"""

from __future__ import annotations

import math

from kalshi_bot.mmsell.quote_parity import (
    MARGINS,
    BandProbe,
    QuoteParityAccumulator,
    admits,
    excursion,
)

WIDE = BandProbe(name="wide", lo=5.0, hi=40.0, maxyes=None)
TIGHT = BandProbe(name="tight", lo=5.0, hi=10.0, maxyes=7.0)


def _acc(*bands: BandProbe) -> QuoteParityAccumulator:
    return QuoteParityAccumulator(bands=bands or (WIDE, TIGHT))


# --- the band decision mirrors the entry scan -------------------------------------------


def test_admits_gates_midpoint_on_the_band():
    assert admits(20.0, 30.0, 5.0, 40.0, None)
    assert not admits(4.0, 6.0, 5.0, 40.0, None)     # below lo
    assert not admits(41.0, 45.0, 5.0, 40.0, None)   # above hi


def test_admits_caps_the_cheap_side_on_maxyes():
    """`maxyes` caps the ACTUAL entry price (the yes-ask = 100 - no_bid), not the midpoint —
    the same asymmetry the tracker applies. A market can sit inside the band on its mid and
    still be refused for costing too much."""
    assert admits(6.0, 7.0, 5.0, 10.0, 7.0)
    assert not admits(6.0, 8.0, 5.0, 10.0, 7.0)      # mid in band, ask over the ceiling


def test_missing_quote_is_not_a_decision():
    assert not admits(None, 7.0, 5.0, 40.0, None)
    assert not admits(6.0, None, 5.0, 10.0, 7.0)     # maxyes needs an ask to judge
    assert admits(6.0, None, 5.0, 40.0, None)        # ...but a band with no ceiling does not


def test_margin_widens_both_the_band_and_the_ceiling():
    assert not admits(42.0, 44.0, 5.0, 40.0, None)
    assert admits(42.0, 44.0, 5.0, 40.0, None, margin=2.0)
    assert not admits(6.0, 9.0, 5.0, 10.0, 7.0)
    assert admits(6.0, 9.0, 5.0, 10.0, 7.0, margin=2.0)


def test_excursion_is_the_margin_that_would_have_saved_the_candidate():
    assert excursion(20.0, None, 5.0, 40.0, None) == 0.0     # inside: no margin needed
    assert excursion(3.0, None, 5.0, 40.0, None) == 2.0      # 2c below lo
    assert excursion(43.5, None, 5.0, 40.0, None) == 3.5     # 3.5c above hi
    # The binding constraint is whichever is worse — here the ceiling, not the band.
    assert excursion(6.0, 10.0, 5.0, 10.0, 7.0) == 3.0
    assert math.isinf(excursion(None, None, 5.0, 40.0, None))


# --- the accumulator --------------------------------------------------------------------


def test_one_sided_orderbook_is_excluded_from_every_denominator():
    """A market whose orderbook is not two-sided is one the scan itself skips. With no ground
    truth there is nothing to validate a pre-filter against, so counting it would dilute the
    agreement rate with markets the question does not apply to."""
    acc = _acc()
    acc.observe(ob_bid=None, ob_ask=30.0, inline_bid=10.0, inline_ask=12.0)
    out = acc.as_dict()
    assert out["compared"] == 0
    assert out["ob_not_two_sided"] == 1
    assert out["bands"]["wide"]["ob_in"] == 0


def test_perfect_agreement_costs_no_margin_and_misses_nothing():
    acc = _acc(WIDE)
    for bid in (6, 12, 20, 33):
        acc.observe(ob_bid=float(bid), ob_ask=bid + 2.0,
                    inline_bid=float(bid), inline_ask=bid + 2.0)
    out = acc.as_dict()
    band = out["bands"]["wide"]
    assert out["compared"] == 4
    assert band["ob_in"] == 4
    assert band["worst_margin"] == 0.0
    for m in MARGINS:
        key = f"{m:g}"
        assert band["miss_at"][key] == 0
        assert band["fetch_at"][key] == 4
    assert out["mid_hist"] == {"0": 4}
    assert out["max_mid_delta"] == 0.0


def test_a_stale_inline_quote_shows_up_as_a_false_negative_at_margin_zero():
    """The failure this whole experiment exists to catch: the orderbook says the market is in
    band, the inline quote says it is not, so a margin-0 pre-filter never fetches it and the
    candidate disappears. It must be counted as a MISS, and the margin that recovers it must
    be reported."""
    acc = _acc(WIDE)
    # Truth: mid 6.0 -> in the 5-40 band. Inline is stale by 3c: mid 3.0 -> rejected.
    acc.observe(ob_bid=5.0, ob_ask=7.0, inline_bid=2.0, inline_ask=4.0)
    band = acc.as_dict()["bands"]["wide"]

    assert band["ob_in"] == 1
    assert band["miss_at"]["0"] == 1     # margin 0 loses it
    assert band["miss_at"]["1"] == 1     # 1c short: lo-1 = 4.0, still above the inline mid
    assert band["miss_at"]["2"] == 0     # lo-2 = 3.0 == the inline mid: recovered
    assert band["miss_at"]["3"] == 0
    # ...which is exactly what worst_margin reports: the smallest margin that misses nothing.
    assert band["worst_margin"] == 2.0
    assert band["miss_no_quote"] == 0


def test_an_absent_inline_quote_is_unrecoverable_at_any_margin():
    """A market with no inline quote cannot be rescued by widening the band — no margin makes
    a missing number in-band. It is counted apart from `worst_margin` so the margin curve is
    never read as covering it."""
    acc = _acc(WIDE)
    acc.observe(ob_bid=5.0, ob_ask=7.0, inline_bid=None, inline_ask=None)
    band = acc.as_dict()["bands"]["wide"]

    assert band["ob_in"] == 1
    assert band["miss_no_quote"] == 1
    assert band["worst_margin"] == 0.0            # untouched by the unrecoverable case
    assert band["miss_at"]["5"] == 1              # still a miss at the widest margin
    assert acc.as_dict()["inline_missing"] == 1


def test_a_false_positive_costs_a_fetch_but_loses_nothing():
    """The benign direction: the pre-filter admits a market the orderbook then rejects. That
    is one wasted orderbook call — exactly what the scan does today for every market — so it
    must NOT be counted as a miss."""
    acc = _acc(WIDE)
    acc.observe(ob_bid=44.0, ob_ask=46.0, inline_bid=20.0, inline_ask=22.0)
    out = acc.as_dict()
    band = out["bands"]["wide"]

    assert band["ob_in"] == 0
    assert band["fetch_at"]["0"] == 1
    assert band["miss_at"]["0"] == 0


def test_bands_are_scored_independently():
    """A market can be in the wide band and out of the tight one; each band's decision table
    has to stand alone, since it is the TIGHT band that mirrors the live book."""
    acc = _acc(WIDE, TIGHT)
    acc.observe(ob_bid=19.0, ob_ask=21.0, inline_bid=19.0, inline_ask=21.0)
    out = acc.as_dict()

    assert out["bands"]["wide"]["ob_in"] == 1
    assert out["bands"]["tight"]["ob_in"] == 0
    assert out["bands"]["tight"]["fetch_at"]["0"] == 0


def test_ask_disagreement_alone_can_break_the_tight_band():
    """The tight band gates on the ask, and the ask is the DERIVED side (ours is 100 - no_bid).
    A market whose mid matches perfectly can still be lost on the ceiling — so the ask
    histogram is tracked separately rather than folded into the midpoint's."""
    acc = _acc(TIGHT)
    # Truth: ask 7.0 == maxyes -> admitted. Inline ask 9.0 -> over the ceiling.
    acc.observe(ob_bid=5.0, ob_ask=7.0, inline_bid=7.0, inline_ask=9.0)
    out = acc.as_dict()
    band = out["bands"]["tight"]

    assert band["ob_in"] == 1
    assert band["miss_at"]["0"] == 1
    assert band["worst_margin"] == 2.0
    assert out["max_ask_delta"] == 2.0
    assert out["mid_hist"] == {"2": 1}      # mid moved 2c as well, but the ask is what bound


def _script():
    """Load the ops read script by path — it runs on a GitHub runner that never installs this
    package, so it lives outside it and cannot be imported normally."""
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mmsell_quote_parity.py"
    spec = importlib.util.spec_from_file_location("mmsell_quote_parity_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_transient_totals_survive_a_worker_restart():
    """The 429 counters are cumulative since PROCESS start, so a restart drops them back toward
    zero. Differencing first-vs-last across a restart would subtract to a negative and report
    fewer rate limits than actually happened — the failure direction that matters, since the
    whole point of the counter is to notice when we are over Kalshi's tier."""
    totals = _script()._transient_totals([
        {"at": 1, "429": 5, "502": 1},
        {"at": 2, "429": 9, "502": 2},     # +4 / +1
        {"at": 3, "429": 2},               # restart: counter reset, this row IS the growth
        {"at": 4, "429": 6},               # +4
    ])
    assert totals["429"] == 9 + 2 + 4      # 9 pre-restart, then 2 and +4 after
    assert totals["502"] == 2


def test_merge_takes_the_worst_margin_not_the_average():
    """The safe margin over a population is its WORST single case. Averaging cycles would
    report a margin that silently misses candidates on the bad ones."""
    merged = _script()._merge([
        {"compared": 10, "bands": {"tight": {"ob_in": 4, "worst_margin": 1.0,
                                             "fetch_at": {"0": 3}, "miss_at": {"0": 1}}}},
        {"compared": 10, "bands": {"tight": {"ob_in": 6, "worst_margin": 4.0,
                                             "fetch_at": {"0": 5}, "miss_at": {"0": 2}}}},
    ])
    band = merged["bands"]["tight"]
    assert merged["compared"] == 20
    assert band["ob_in"] == 10
    assert band["worst_margin"] == 4.0
    assert band["fetch_at"]["0"] == 8
    assert band["miss_at"]["0"] == 3


def test_output_is_json_round_trippable_and_bounded():
    """The whole thing is persisted into a system_events JSON column every cycle, and a scan
    sees thousands of markets — the payload must be plain types and must not grow with them."""
    import json

    acc = _acc()
    for i in range(500):
        acc.observe(ob_bid=float(i % 40), ob_ask=float(i % 40) + 2,
                    inline_bid=float(i % 40), inline_ask=float(i % 40) + 2)
    out = acc.as_dict()
    blob = json.dumps(out)
    assert json.loads(blob) == out
    assert len(blob) < 4000, "parity payload should be a fixed-size summary, not per-market rows"
    assert out["compared"] == 500
