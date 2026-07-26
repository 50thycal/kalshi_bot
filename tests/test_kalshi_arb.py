"""Tests for scripts/kalshi_arb.py — strike parsing + the arb math it feeds.

The scanner's false-positive incidents so far: parsing bugs that scrambled a correctly-priced
ladder into a fake vertical arb (dropped minus signs and K/M/B units in Jul; spelled-out
'trillion' in the Musk net-worth ladder; a bare leading-dot percent strike like '.7%' misread as
strike=7 in the Treasury-spread ladder, KX10Y2Y), and two *coverage* bugs in the gap guard (an
illiquid leg silently dropped from a range ladder turned a Dutch book into a subset sum over
worthless tails, KXXRP-26JUL2617; a fixed absolute tolerance that didn't scale down to a
meme-coin ladder's own tiny bucket widths, KXSHIBA). These lock all five down.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))  # kalshi_arb imports xvenue_leadlag as a sibling


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


arb = _load("kalshi_arb")


def test_nums_scales_spelled_out_and_lettered_units():
    assert arb._nums("Above $700 billion") == [700e9]
    assert arb._nums("Above $1.00 trillion") == [1e12]
    assert arb._nums("Above $1.1M") == [1.1e6]
    assert arb._nums("Above 6,000") == [6000.0]


def test_nums_keeps_negative_strikes():
    assert arb._nums("Above -0.3%") == [-0.3]


def test_nums_does_not_treat_stray_words_as_units():
    """'to' must not read as tera and 'mph' must not read as mega — the lookahead's job."""
    assert arb._nums("5 to 10") == [5.0, 10.0]
    assert arb._nums("3 mph") == [3.0]


def test_nums_parses_a_bare_leading_dot_decimal():
    """The real KX10Y2Y-26DEC31 subtitle: 'Above .7%' with no leading zero. The old regex
    required the match to start at a digit, so '.' was skipped and only '7' matched -- a strike
    of 0.7 misread as 7, an order-of-magnitude scramble that inverted the ladder's true order."""
    assert arb._nums("Above .7%") == [0.7]
    assert arb._nums("Above .85") == [0.85]
    assert arb._nums("Above 1.00%") == [1.0]  # digit-first form must still work unchanged


def test_musk_networth_ladder_parses_monotone():
    """The real KXMUSKNW ladder: billions then trillions. Mis-scaling 'trillion' inverted it
    and produced a bogus +$0.65 MONO-VERTICAL arb."""
    subs = ["Above $700 billion", "Above $750 billion", "Above $900 billion",
            "Above $1.00 trillion", "Above $1.30 trillion", "Above $1.70 trillion"]
    strikes = [arb._parse_bucket(s)[1] for s in subs]
    assert all(k[0] == "ge" for k in (arb._parse_bucket(s) for s in subs))
    assert strikes == sorted(strikes)


def test_scan_event_finds_no_arb_in_correctly_priced_musk_ladder():
    """Descending prices over an ascending strike ladder = coherent = no vertical arb."""
    prices = [(0.69, 0.71), (0.23, 0.25), (0.03, 0.04), (0.01, 0.02), (0.00, 0.01), (0.00, 0.01)]
    subs = ["Above $700 billion", "Above $750 billion", "Above $900 billion",
            "Above $1.00 trillion", "Above $1.30 trillion", "Above $1.70 trillion"]
    event = {
        "event_ticker": "KXMUSKNW-TEST", "title": "Elon Musk net worth", "mutually_exclusive": False,
        "markets": [
            {"yes_bid_dollars": yb, "yes_ask_dollars": ya, "yes_sub_title": s, "ticker": f"T{i}"}
            for i, (s, (yb, ya)) in enumerate(zip(subs, prices, strict=True))
        ],
    }
    out = arb.scan_event(event, fee_buf=0.0, max_close=0)
    assert "ARB" not in out
    assert out["mono_best"] < 0


def test_treasury_spread_ladder_parses_monotone_and_scans_clean():
    """The real KX10Y2Y-26DEC31 ladder (Above .7%/.8%/.9%/1.00%) -- the bare-leading-dot bug
    misread '.7' as strike 7, scrambling this coherent ladder into a fake +$0.22 MONO-VERTICAL
    (printed as 'strikes 1/7' -- a rounding artifact of the real 0.7/1.00 strikes misparsed)."""
    subs = ["Above .7%", "Above .8%", "Above .9%", "Above 1.00%"]
    strikes = [arb._parse_bucket(s)[1] for s in subs]
    assert strikes == [0.7, 0.8, 0.9, 1.0]
    event = {
        "event_ticker": "KX10Y2Y-26DEC31-TEST", "title": "Treasury spread", "mutually_exclusive": False,
        "markets": [
            {"yes_bid_dollars": yb, "yes_ask_dollars": ya, "yes_sub_title": s, "ticker": f"T{i}"}
            for i, (s, (yb, ya)) in enumerate(zip(
                subs, [(0.65, 0.67), (0.45, 0.47), (0.25, 0.27), (0.10, 0.12)], strict=True))
        ],
    }
    out = arb.scan_event(event, fee_buf=0.0, max_close=0)
    assert "ARB" not in out
    assert out["mono_best"] < 0


def test_tiles_exhaustively_scales_to_a_meme_coin_ladder():
    """The real KXSHIBA-26JUL27 shape: strikes in millionths of a dollar. A whole ~14-bucket-wide
    span ($0.000002-$0.000009) is missing, but the absolute gap (~7e-6) is tiny in dollar terms
    -- the old fixed tol=0.02 passed this as tiled, reading a fake +$0.70 BUY-ALL-YES on 15 legs
    that were all worthless tails. The tolerance must scale to the ladder's OWN bucket width."""
    subs = [
        "$0.0000009 or below", "$0.000001 to 0.000001499", "$0.0000015 to 0.000001999",
        # a ~14-bucket-wide span is missing here ($0.000002 - $0.000009)
        "$0.000009 to 0.000009499", "$0.0000095 to 0.000009999",
        "$0.0000145 or above",
    ]
    mkts = [{"parse": arb._parse_bucket(s)} for s in subs]
    assert all(m["parse"] for m in mkts)
    assert arb._tiles_exhaustively(mkts) is False


def test_tiles_exhaustively_still_passes_a_gapless_meme_coin_ladder():
    """The guard must not become so sensitive it flags a genuinely complete fine-grained ladder
    -- only real drops, regardless of the ladder's own scale."""
    subs = [
        "$0.0000009 or below", "$0.000001 to 0.000001499", "$0.0000015 to 0.000001999",
        "$0.000002 to 0.000002499", "$0.0000025 to 0.000002999",
        "$0.000003 or above",
    ]
    mkts = [{"parse": arb._parse_bucket(s)} for s in subs]
    assert arb._tiles_exhaustively(mkts) is True


def test_scan_event_flags_a_genuine_vertical_arb():
    """A truly incoherent ladder — the high strike bid above the low strike ask — must fire."""
    event = {
        "event_ticker": "KXFAKE-TEST", "title": "broken ladder", "mutually_exclusive": False,
        "markets": [
            {"yes_bid_dollars": 0.10, "yes_ask_dollars": 0.12, "yes_sub_title": "Above 100",
             "ticker": "A"},
            {"yes_bid_dollars": 0.80, "yes_ask_dollars": 0.82, "yes_sub_title": "Above 200",
             "ticker": "B"},
            {"yes_bid_dollars": 0.05, "yes_ask_dollars": 0.07, "yes_sub_title": "Above 300",
             "ticker": "C"},
        ],
    }
    out = arb.scan_event(event, fee_buf=0.0, max_close=0)
    assert out["ARB"][0] == "MONO-VERTICAL"
    assert out["ARB"][1] > 0


def _range_event(bucket_prices, ticker="KXFAKE-RANGE"):
    """Build a mutually_exclusive range-bucket event: bucket_prices is a list of
    (subtitle, yes_bid, yes_ask)."""
    return {
        "event_ticker": ticker, "title": "range test", "mutually_exclusive": True,
        "markets": [
            {"yes_bid_dollars": yb, "yes_ask_dollars": ya, "yes_sub_title": sub, "ticker": f"T{i}"}
            for i, (sub, yb, ya) in enumerate(bucket_prices)
        ],
    }


def test_tiles_exhaustively_true_for_a_full_partition():
    mkts = [
        {"parse": arb._parse_bucket("$10 or below")},
        {"parse": arb._parse_bucket("$10 to $19.99")},
        {"parse": arb._parse_bucket("$20 to $29.99")},
        {"parse": arb._parse_bucket("$30 or above")},
    ]
    assert arb._tiles_exhaustively(mkts) is True


def test_tiles_exhaustively_false_when_a_middle_bucket_is_missing():
    """The exact KXXRP-26JUL2617 shape: a whole band silently dropped between two retained
    legs — a gap far wider than one bucket width."""
    mkts = [
        {"parse": arb._parse_bucket("$10 or below")},
        {"parse": arb._parse_bucket("$10 to $19.99")},
        # $20-$29.99 missing here
        {"parse": arb._parse_bucket("$30 to $39.99")},
        {"parse": arb._parse_bucket("$40 or above")},
    ]
    assert arb._tiles_exhaustively(mkts) is False


def test_tiles_exhaustively_false_without_both_open_tails():
    """No 'or below' / 'or above' leg means the retained set can't be a full partition even if
    the ranges it does have are contiguous."""
    mkts = [
        {"parse": arb._parse_bucket("$10 to $19.99")},
        {"parse": arb._parse_bucket("$20 to $29.99")},
    ]
    assert arb._tiles_exhaustively(mkts) is False


def test_scan_event_suppresses_arb_when_range_ladder_has_a_gap():
    """The real failure: KXXRP-26JUL2617 kept 19 legs (all bid=0/ask=1c, the entire
    $0.88-$1.30 band silently missing from the illiquid-quote filter) and Sum(yes_ask)=0.19
    read as a bogus +$0.62 BUY-ALL-YES. Every retained leg being a worthless tail is the
    tell — the true probability mass sits in the gap, so the subset sum is not a Dutch book."""
    event = _range_event([
        ("$0.6199999 or below", 0.00, 0.01),
        ("$0.62 to $0.6399", 0.00, 0.01),
        ("$0.86 to $0.8799", 0.00, 0.01),
        # the $0.88-$1.2999 band is entirely missing, exactly like the live incident
        ("$1.3 to $1.3199", 0.00, 0.01),
        ("$1.37991 or above", 0.00, 0.01),
    ], ticker="KXXRP-TEST")
    out = arb.scan_event(event, fee_buf=0.0, max_close=0)
    assert out["gapped"] is True
    assert "ARB" not in out
    assert out["sum_ask"] < 0.2   # the subset sum still reads as tiny -- exactly why it's dangerous


def test_scan_event_still_flags_a_genuine_gapless_range_arb():
    """The guard must not eat real hits: a fully-tiled range ladder priced under $1 must still
    fire BUY-ALL-YES."""
    event = _range_event([
        ("$10 or below", 0.10, 0.12),
        ("$10 to $19.99", 0.20, 0.22),
        ("$20 to $29.99", 0.25, 0.27),
        ("$30 or above", 0.30, 0.32),
    ])
    out = arb.scan_event(event, fee_buf=0.0, max_close=0)
    assert out["gapped"] is False
    assert out["ARB"][0] == "BUY-ALL-YES"
    assert out["ARB"][1] > 0


def test_named_candidate_set_is_unverifiable_not_gapped():
    """Election-style candidate names have no numeric structure to tile — the guard must not
    misclassify them as 'gapped' (which would suppress legitimate flag-trusting behavior); it
    reports them as simply unverifiable, same as before this guard existed."""
    event = {
        "event_ticker": "KXCANDS-TEST", "title": "candidates", "mutually_exclusive": True,
        "markets": [
            {"yes_bid_dollars": 0.05, "yes_ask_dollars": 0.06, "yes_sub_title": "Alice",
             "ticker": "A"},
            {"yes_bid_dollars": 0.05, "yes_ask_dollars": 0.06, "yes_sub_title": "Bob",
             "ticker": "B"},
            {"yes_bid_dollars": 0.05, "yes_ask_dollars": 0.06, "yes_sub_title": "Carol",
             "ticker": "C"},
        ],
    }
    out = arb.scan_event(event, fee_buf=0.0, max_close=0)
    assert out["gapped"] is False
    assert out["ARB"][0] == "BUY-ALL-YES"
