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


def _lookup_returning(no_bid, captured_at):
    def _lookup(_session, _ticker, *, before):
        return no_bid, captured_at
    return _lookup


def _hot(lookup, ticker="T", no_bid=90):
    return is_hot_entry(
        None, ticker, no_bid, lookup=lookup,
        move_cents=MOVE_CENTS, lookback_minutes=LOOKBACK_MINUTES, now=NOW)


# --------------------------------------------------------------------------- is_hot_entry
#
# `lookup` is injected (see live/sizing.py), so these drive it directly rather than
# monkeypatching a repository function — the decision logic is what is under test here, and the
# real per-book readers are covered in test_repository_quote_tapes.py.


def test_not_hot_with_no_prior_quote_at_all():
    """A brand-new candidate has no tape to judge by — must behave like a calm market, the same
    'don't fire on thin history' convention the anchor set's volatility gate already uses. A
    market's first-ever entry must never be penalized for lacking history it couldn't have had."""
    assert _hot(_lookup_returning(None, None)) is False


def test_hot_when_the_only_quote_predates_the_lookback_window():
    """The exact KXFEDMENTION shape: a quote exists, but it is older than the lookback window —
    the ticker went quiet right when we'd want a comparison, which is the signal itself."""
    assert _hot(_lookup_returning(90, NOW - timedelta(minutes=31))) is True   # same price, just old


def test_hot_when_recent_quote_shows_a_big_move():
    assert _hot(_lookup_returning(85, NOW - timedelta(minutes=10))) is True   # 90-85 = 5 >= 5


def test_not_hot_when_recent_quote_shows_a_small_move():
    assert _hot(_lookup_returning(87, NOW - timedelta(minutes=10))) is False  # 90-87 = 3 < 5


def test_not_hot_at_exactly_the_lookback_boundary():
    """Just inside the window (29m59s old) with a calm price -> not hot; confirms the boundary
    check compares against the window edge, not an off-by-one."""
    assert _hot(_lookup_returning(90, NOW - timedelta(minutes=29, seconds=59))) is False


def test_hot_check_fails_soft_and_enters():
    def _boom(*_a, **_k):
        raise RuntimeError("db down")
    assert _hot(_boom) is False


def test_a_quote_with_no_usable_price_is_not_hot():
    """A tape row that exists and is recent but carries no price cannot be compared; that is a
    reason to behave normally, not to price defensively."""
    assert _hot(_lookup_returning(None, NOW - timedelta(minutes=5))) is False


def test_lookup_is_required_so_a_book_cannot_inherit_another_books_tape():
    """The whole point of injecting it: theta silently reading mmsell's candidate tape would be a
    real (and very quiet) mispricing bug."""
    import pytest
    with pytest.raises(TypeError):
        is_hot_entry(None, "T", 90, move_cents=5, lookback_minutes=30, now=NOW)


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
