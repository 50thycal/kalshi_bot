"""Tests for scripts/kalshi_arb.py — strike parsing + the arb math it feeds.

The scanner's only two false-positive incidents were both *parsing* bugs that scrambled a
correctly-priced ladder into a fake vertical arb (dropped minus signs and K/M/B units in Jul,
then spelled-out 'trillion' in the Musk net-worth ladder). These lock that down.
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
