"""The mmsell LIVE "hot market" defensive-pricing check (live/sizing.py: is_hot_entry,
maker_no_price(hot=...)).

Root cause this exists to fix: KXFEDMENTION-26JUL-PROJ's no-bid moved 73c -> 94c in 32 minutes on
2026-07-29 while absent from the candidate tape (out of the trading band), and the live order
rested into that move got exchange-canceled with zero fill (docs: the ops investigation that
found this). The fix never excludes a market — every candidate is still entered — a "hot" one is
just priced more defensively.

Mirrors the fixture style of test_mmsell_anchor_set.py's volatility-gate tests (monkeypatch the
repository lookup rather than seed real rows) and test_mmsell_live.py's live-entry tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kalshi_bot.live.sizing import is_hot_entry, maker_no_price

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
MOVE_CENTS = 5
LOOKBACK_MINUTES = 30


@dataclass
class _Tick:
    captured_at: datetime
    no_bid: int | None


def _hot(ticker="T", no_bid=90):
    return is_hot_entry(
        None, ticker, no_bid,
        move_cents=MOVE_CENTS, lookback_minutes=LOOKBACK_MINUTES, now=NOW)


# --------------------------------------------------------------------------- is_hot_entry


def test_not_hot_with_no_tick_history_at_all(monkeypatch):
    """A brand-new candidate has no tape to judge by — must behave like a calm market, the same
    'don't fire on thin history' convention the anchor set's volatility gate already uses. A
    market's first-ever entry must never be penalized for lacking history it couldn't have had."""
    monkeypatch.setattr("kalshi_bot.repository.latest_candidate_tick_before",
                        lambda *_a, **_k: None)
    assert _hot() is False


def test_hot_when_the_only_tick_predates_the_lookback_window(monkeypatch):
    """The exact KXFEDMENTION shape: a tick exists, but it is older than the lookback window —
    the ticker went quiet right when we'd want a comparison, which is the signal itself."""
    tick = _Tick(captured_at=NOW - timedelta(minutes=31), no_bid=90)  # same price, just old
    monkeypatch.setattr("kalshi_bot.repository.latest_candidate_tick_before",
                        lambda *_a, **_k: tick)
    assert _hot() is True


def test_hot_when_recent_tick_shows_a_big_move(monkeypatch):
    tick = _Tick(captured_at=NOW - timedelta(minutes=10), no_bid=85)  # 90 - 85 = 5 >= threshold 5
    monkeypatch.setattr("kalshi_bot.repository.latest_candidate_tick_before",
                        lambda *_a, **_k: tick)
    assert _hot() is True


def test_not_hot_when_recent_tick_shows_a_small_move(monkeypatch):
    tick = _Tick(captured_at=NOW - timedelta(minutes=10), no_bid=87)  # 90 - 87 = 3 < threshold 5
    monkeypatch.setattr("kalshi_bot.repository.latest_candidate_tick_before",
                        lambda *_a, **_k: tick)
    assert _hot() is False


def test_not_hot_at_exactly_the_lookback_boundary(monkeypatch):
    """Just inside the window (29m59s old) with a calm price -> not hot; confirms the boundary
    check compares against the window edge, not an off-by-one."""
    tick = _Tick(captured_at=NOW - timedelta(minutes=29, seconds=59), no_bid=90)
    monkeypatch.setattr("kalshi_bot.repository.latest_candidate_tick_before",
                        lambda *_a, **_k: tick)
    assert _hot() is False


def test_hot_check_fails_soft_and_enters(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr("kalshi_bot.repository.latest_candidate_tick_before", _boom)
    assert _hot() is False


# --------------------------------------------------------------------------- maker_no_price(hot=)


class _M:
    def __init__(self, no_bid=90, no_ask=94):
        self.best_no_bid = no_bid
        self.best_no_ask = no_ask


def test_hot_price_uses_the_defensive_offset_not_the_normal_one():
    # a calm entry with offset=2 would improve to 92; a hot entry ignores that and uses -3
    assert maker_no_price(_M(no_bid=90), None, 2, hot=False) == 92
    assert maker_no_price(_M(no_bid=90), None, -3, hot=True) == 87


def test_hot_price_can_rest_below_the_no_bid():
    """Unlike the calm-path offset (clamped to >=0 — never worse than joining the queue), the
    defensive offset is normally negative and must be allowed to go below the current no-bid."""
    assert maker_no_price(_M(no_bid=50, no_ask=60), None, -5, hot=True) == 45


def test_calm_price_offset_is_floored_at_zero_but_hot_is_not():
    assert maker_no_price(_M(no_bid=90, no_ask=99), None, -5, hot=False) == 90  # floored, not 85


def test_hot_price_still_capped_at_no_ask_and_bounds():
    # a pathological config must not place an unpriceable order
    assert maker_no_price(_M(no_bid=90, no_ask=94), None, 50, hot=True) == 94
    assert maker_no_price(_M(no_bid=5, no_ask=10), None, -500, hot=True) == 1
