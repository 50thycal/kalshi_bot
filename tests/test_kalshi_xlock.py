"""Tests for scripts/kalshi_xlock.py — the P1 parlay-containment matcher, the P2 touch-vs-
terminal direction (a bug caught and fixed in docs/XLOCK_THESIS.md before any probe ran — this
locks the corrected direction down), and the P3 coverage census bucketing.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))  # kalshi_xlock imports kalshi_arb + xvenue_leadlag as siblings


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


arb = _load("kalshi_arb")
xk = _load("kalshi_xlock")

NOW = int(time.time())


def _iso(days_from_now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW + days_from_now * 86400))


def _mkt(sub, yb, ya, ticker="T", close_days=10.0):
    return {"yes_bid_dollars": yb, "yes_ask_dollars": ya, "yes_sub_title": sub,
            "ticker": ticker, "close_time": _iso(close_days)}


# --- P1: rules-text decomposition + matching --------------------------------------------


def test_combo_legs_from_rules_splits_on_and_and_ampersand():
    assert xk.combo_legs_from_rules("Team A wins AND Team B wins") == ["Team A wins", "Team B wins"]
    assert xk.combo_legs_from_rules("R House & R Senate") == ["R House", "R Senate"]


def test_combo_legs_from_rules_does_not_split_inside_a_word():
    """'Netherlands' contains 'and' with no surrounding whitespace — must not split there."""
    parts = xk.combo_legs_from_rules("Netherlands wins group A and Spain wins group B")
    assert parts == ["Netherlands wins group A", "Spain wins group B"]


def test_combo_legs_from_rules_returns_empty_for_a_single_condition():
    assert xk.combo_legs_from_rules("just one condition, no conjunction") == []


def test_match_leg_requires_a_unique_hit_from_a_different_series():
    candidates = [{"series": "CONTROLH", "sub": "Republicans control the House", "title": "", "tk": "A"}]
    assert xk.match_leg("Republicans control the House", candidates, "KXBALANCEPOWERCOMBO") is not None
    # ambiguous (2 hits) -> dropped, never guessed
    two = candidates + [{"series": "CONTROLH", "sub": "Republicans control the House",
                         "title": "", "tk": "B"}]
    assert xk.match_leg("Republicans control the House", two, "KXBALANCEPOWERCOMBO") is None
    # same series as the combo itself -> not a real external leg, dropped
    same_series = [{"series": "KXBALANCEPOWERCOMBO", "sub": "Republicans control the House",
                    "title": "", "tk": "A"}]
    assert xk.match_leg("Republicans control the House", same_series, "KXBALANCEPOWERCOMBO") is None
    # no hit at all
    assert xk.match_leg("Republicans control the House", [], "KXBALANCEPOWERCOMBO") is None


def test_scan_p1_flags_a_combo_priced_above_its_cheaper_leg():
    """A 2-leg combo (a real parlay always ANDs >=2 conditions): combo_yes_bid exceeding EITHER
    component's yes_ask is a lock on that leg -- sell the combo, buy the cheaper leg."""
    events = [
        {"series_ticker": "KXCOMBO-TEST", "title": "combo",
         "markets": [{"ticker": "COMBO1", "yes_bid_dollars": 0.40, "yes_ask_dollars": 0.42,
                      "yes_sub_title": "combo leg"}]},
        {"series_ticker": "KXLEGA", "title": "leg a",
         "markets": [{"ticker": "LEGA1", "yes_bid_dollars": 0.10, "yes_ask_dollars": 0.12,
                      "yes_sub_title": "Condition A true", "close_time": _iso(10)}]},
        {"series_ticker": "KXLEGB", "title": "leg b",
         "markets": [{"ticker": "LEGB1", "yes_bid_dollars": 0.50, "yes_ask_dollars": 0.52,
                      "yes_sub_title": "Condition B true", "close_time": _iso(10)}]},
    ]
    all_legs = xk.flatten_noncombo_legs(events, max_close=0)

    def fake_market_detail(ticker):
        return ({"rules_primary": "Condition A true AND Condition B true", "rules_secondary": None}
                if ticker == "COMBO1" else None)
    orig = xk.market_detail
    xk.market_detail = fake_market_detail
    try:
        r = xk.scan_p1(events, all_legs, max_combo_detail=10)
    finally:
        xk.market_detail = orig
    assert r["combo_candidates"] == 1
    assert r["decomposed"] == 1
    assert r["matched_pairs"] == 1
    # only leg A's ask (0.12) is undercut by the combo's bid (0.40); leg B's ask (0.52) isn't.
    assert len(r["hits"]) == 1
    h = r["hits"][0]
    assert h["combo_ticker"] == "COMBO1" and h["leg_ticker"] == "LEGA1"
    assert abs(h["profit"] - (0.40 - 0.12 - arb.fee(0.40) - arb.fee(0.12))) < 1e-9


def test_scan_p1_drops_a_combo_when_a_component_is_unmatched():
    """No open market matches the second AND-part -> the whole combo is dropped, not partially
    guessed."""
    events = [
        {"series_ticker": "KXCOMBO-TEST", "title": "combo",
         "markets": [{"ticker": "COMBO1", "yes_bid_dollars": 0.40, "yes_ask_dollars": 0.42,
                      "yes_sub_title": "combo leg"}]},
        {"series_ticker": "KXLEGA", "title": "leg a",
         "markets": [{"ticker": "LEGA1", "yes_bid_dollars": 0.10, "yes_ask_dollars": 0.12,
                      "yes_sub_title": "Condition A true", "close_time": _iso(10)}]},
    ]
    all_legs = xk.flatten_noncombo_legs(events, max_close=0)

    def fake_market_detail(ticker):
        return {"rules_primary": "Condition A true AND Condition B true"} if ticker == "COMBO1" else None
    orig = xk.market_detail
    xk.market_detail = fake_market_detail
    try:
        r = xk.scan_p1(events, all_legs, max_combo_detail=10)
    finally:
        xk.market_detail = orig
    assert r["decomposed"] == 1
    assert r["matched_pairs"] == 0
    assert r["hits"] == []


# --- P2: touch-vs-terminal direction ----------------------------------------------------


def _p2_events(touch_yb, touch_ya, term_yb, term_ya):
    return [
        {"category": "Crypto", "series_ticker": "KXBTCMAXY", "mutually_exclusive": False,
         "markets": [_mkt("$100,000 or above", touch_yb, touch_ya, "TOUCH")]},
        {"category": "Crypto", "series_ticker": "KXBTCD", "mutually_exclusive": False,
         "markets": [_mkt("$100,000 or above", term_yb, term_ya, "TERM")]},
    ]


def test_p2_coherent_market_does_not_fire():
    """Touch (containing event) priced richer than terminal (contained event) is the coherent,
    expected ordering -- no lock."""
    events = _p2_events(touch_yb=0.40, touch_ya=0.42, term_yb=0.20, term_ya=0.22)
    r = xk.scan_p2(events, max_gap_hours=36.0)
    assert r["matched"] == 1
    assert r["hits"] == []


def test_p2_fires_only_in_the_terminal_bid_minus_touch_ask_direction():
    """The correct, riskless lock is sell-terminal/buy-touch (terminal_bid - touch_ask). The
    reverse construction (sell-touch/buy-terminal) is NOT an arb -- it has an uncapped loss
    branch (touched K, pulled back below it by close) -- and must never be what fires."""
    events = _p2_events(touch_yb=0.10, touch_ya=0.12, term_yb=0.30, term_ya=0.32)
    r = xk.scan_p2(events, max_gap_hours=36.0)
    assert len(r["hits"]) == 1
    h = r["hits"][0]
    assert h["terminal"]["ticker"] == "TERM" and h["touch"]["ticker"] == "TOUCH"
    expected = 0.30 - 0.12 - arb.fee(0.30) - arb.fee(0.12)
    assert abs(h["profit"] - expected) < 1e-9


def test_p2_ignores_non_crypto_and_range_bucket_legs():
    events = [
        {"category": "Sports", "series_ticker": "KXBTCMAXY", "mutually_exclusive": False,
         "markets": [_mkt("$100,000 or above", 0.10, 0.12, "NOTCRYPTO")]},
        {"category": "Crypto", "series_ticker": "KXBTCD", "mutually_exclusive": True,
         "markets": [_mkt("$100,000 or above", 0.30, 0.32, "PARTITIONLEG")]},
    ]
    r = xk.scan_p2(events, max_gap_hours=36.0)
    assert r["touch_n"] == 0 and r["terminal_n"] == 0
    assert r["hits"] == []


def test_series_asset_extracts_the_underlying_symbol():
    assert xk._series_asset("KXBTCD") == "BTC"
    assert xk._series_asset("KXETHD") == "ETH"
    assert xk._series_asset("KXBTCMAXY") == "BTC"
    assert xk._series_asset("KXHYPEMAXMON") == "HYPE"
    # 'DOGE' starts with the same letter as the 'D' terminal suffix -- must not truncate to ''
    assert xk._series_asset("KXDOGED") == "DOGE"
    assert xk._series_asset("KXNOTCRYPTOSERIES") is None


def test_p2_does_not_pair_two_different_underlyings():
    """The actual live false positive from the probe's first ops run: KXSOLD (SOL, terminal)
    matched KXHYPEMAXMON (HYPE, touch) purely on shared strike + close time, with no asset
    check at all -- reported a fake +$0.41 lead between two unrelated cryptocurrencies. Asset
    alignment must be a hard precondition, not an afterthought."""
    events = [
        {"category": "Crypto", "series_ticker": "KXHYPEMAXMON", "mutually_exclusive": False,
         "markets": [_mkt("$75 or above", 0.06, 0.08, "KXHYPEMAXMON-HYPE-26JUL31-7500",
                          close_days=5.0)]},
        {"category": "Crypto", "series_ticker": "KXSOLD", "mutually_exclusive": False,
         "markets": [_mkt("$74.9999 or above", 0.52, 0.54, "KXSOLD-26JUL3117-T74.9999",
                          close_days=5.7)]},
    ]
    r = xk.scan_p2(events, max_gap_hours=36.0)
    assert r["matched"] == 0
    assert r["hits"] == []


# --- P3: coverage census -----------------------------------------------------------------


def test_census_buckets_each_skip_reason_independently():
    events = [
        {"series_ticker": "KXMVE-FOO",
         "markets": [_mkt("a", 0.1, 0.2, close_days=10)] * 3},                    # kxmve
        {"series_ticker": "KXFOO", "markets": [_mkt("a", 0.1, 0.2, close_days=10)]},  # raw<3
        {"series_ticker": "KXBAR",
         "markets": [_mkt("a", 0.1, 0.2, close_days=300)] * 3},                  # horizon
        {"series_ticker": "KXBAZ", "markets": [
            _mkt("a", 0.0, 0.0, close_days=10), _mkt("b", 0.0, 0.0, close_days=10),
            _mkt("c", 0.1, 0.2, close_days=10)]},                                # illiquid<3
        {"series_ticker": "KXQUX", "mutually_exclusive": True, "markets": [
            _mkt("$10 or below", 0.30, 0.32, close_days=10),
            _mkt("$10 to $19.99", 0.30, 0.32, close_days=10),
            _mkt("$20 or above", 0.30, 0.31, close_days=10)]},                   # mece clean
        {"series_ticker": "KXGAP", "mutually_exclusive": True, "markets": [
            _mkt("$10 or below", 0.00, 0.01, close_days=10),
            _mkt("$50 or above", 0.00, 0.01, close_days=10),
            _mkt("$10 to $19.99", 0.00, 0.01, close_days=10)]},                  # mece gapped
        {"series_ticker": "KXMONO", "markets": [
            _mkt("Above 100", 0.69, 0.71, close_days=10),
            _mkt("Above 200", 0.23, 0.25, close_days=10),
            _mkt("Above 300", 0.01, 0.02, close_days=10)]},                      # mono, coherent
        {"series_ticker": "KXNONE", "markets": [
            _mkt("Alice", 0.1, 0.2, close_days=10), _mkt("Bob", 0.1, 0.2, close_days=10),
            _mkt("Carol", 0.1, 0.2, close_days=10)]},                            # no structure
        {"series_ticker": "KXARBFLAG", "markets": [
            _mkt("Above 100", 0.10, 0.12, close_days=10),
            _mkt("Above 200", 0.80, 0.82, close_days=10),
            _mkt("Above 300", 0.05, 0.07, close_days=10)]},                      # genuine mono arb
    ]
    max_close = int(NOW + 45 * 86400)
    c = xk.census(events, max_close)
    assert c["total_events"] == 9
    assert c["kxmve_excluded"] == 1
    assert c["raw_lt3_legs"] == 1
    assert c["horizon_excluded"] == 1
    assert c["illiquid_filtered_lt3"] == 1
    assert c["evaluated"] == 5
    assert c["mece_clean"] == 1
    assert c["mece_gapped"] == 1
    assert c["mono_evaluated"] == 2   # KXMONO (coherent) + KXARBFLAG (incoherent)
    assert c["no_structure"] == 1
    assert c["flagged_arb"] == 1      # only KXARBFLAG's ladder is actually incoherent
