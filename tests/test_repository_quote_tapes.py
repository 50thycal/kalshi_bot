"""The per-book prior-quote readers behind the live hot-market check.

`is_hot_entry` is a pure decision function with the tape injected, so these cover the other half:
that each book's reader finds the right row in its OWN table and normalizes it to the same
`(no_bid_cents, captured_at)` shape. The normalization is the part worth pinning — mmsell tapes
the NO side directly while theta's ladder only stores the YES side, so theta's reader has to
derive `no_bid = 100 - yes_ask` exactly the way the entry itself does. Getting that inverted would
silently compare a ~93c no-bid against a ~7c yes-ask and call every entry hot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.models import Base

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _naive(dt):
    """sqlite drops tzinfo on round-trip where Postgres (TIMESTAMP WITH TIME ZONE) keeps it. The
    readers return the column verbatim either way; is_hot_entry normalizes with its own _aware(),
    so timestamps are compared here tz-insensitively rather than pinning a sqlite artifact."""
    return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo else dt


TICKER = "KXBTCD-26AUG0212-T99"


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def _ladder(session, ticker, *, at, yes_ask):
    session.add(m.CryptoLadderSnapshot(
        captured_at=at, event_ticker="KXBTCD-26AUG0212", market_ticker=ticker,
        yes_bid_cents=(yes_ask - 2) if yes_ask is not None else None,
        yes_ask_cents=yes_ask, mid_cents=None))
    session.flush()


def _tick(session, ticker, *, at, no_bid):
    session.add(m.MmSellCandidateTick(
        market_ticker=ticker, captured_at=at, no_bid=no_bid, no_ask=no_bid + 1,
        yes_bid=100 - (no_bid + 1), yes_ask=100 - no_bid, mid=None))
    session.flush()


# --------------------------------------------------------------------------- mmsell


def test_mmsell_reader_returns_the_newest_row_before_the_cutoff():
    s = _session()
    _tick(s, TICKER, at=NOW - timedelta(minutes=30), no_bid=80)
    _tick(s, TICKER, at=NOW - timedelta(minutes=5), no_bid=93)
    _tick(s, TICKER, at=NOW - timedelta(seconds=1), no_bid=99)   # this cycle's own row
    no_bid, at = repo.latest_mmsell_no_bid_before(s, TICKER, before=NOW - timedelta(seconds=30))
    assert no_bid == 93                                          # not 99 (excluded), not 80 (older)
    assert _naive(at) == _naive(NOW - timedelta(minutes=5))


def test_mmsell_reader_is_scoped_to_its_own_ticker():
    s = _session()
    _tick(s, "OTHER", at=NOW - timedelta(minutes=5), no_bid=10)
    assert repo.latest_mmsell_no_bid_before(s, TICKER, before=NOW) == (None, None)


def test_mmsell_reader_returns_none_pair_when_there_is_no_tape():
    s = _session()
    assert repo.latest_mmsell_no_bid_before(s, TICKER, before=NOW) == (None, None)


# --------------------------------------------------------------------------- theta


def test_theta_reader_derives_the_no_bid_from_the_yes_ask():
    """The ladder stores YES; theta's maker entry rests on NO. 100 - 7 = 93."""
    s = _session()
    _ladder(s, TICKER, at=NOW - timedelta(minutes=5), yes_ask=7.0)
    no_bid, at = repo.latest_theta_no_bid_before(s, TICKER, before=NOW)
    assert no_bid == 93
    assert _naive(at) == _naive(NOW - timedelta(minutes=5))


def test_theta_reader_returns_the_newest_row_before_the_cutoff():
    s = _session()
    _ladder(s, TICKER, at=NOW - timedelta(minutes=20), yes_ask=20.0)
    _ladder(s, TICKER, at=NOW - timedelta(minutes=5), yes_ask=7.0)
    _ladder(s, TICKER, at=NOW - timedelta(seconds=1), yes_ask=40.0)  # this cycle's own row
    assert repo.latest_theta_no_bid_before(
        s, TICKER, before=NOW - timedelta(seconds=30))[0] == 93


def test_theta_reader_is_scoped_to_its_own_ticker():
    s = _session()
    _ladder(s, "OTHER", at=NOW - timedelta(minutes=5), yes_ask=7.0)
    assert repo.latest_theta_no_bid_before(s, TICKER, before=NOW) == (None, None)


def test_theta_reader_returns_none_pair_when_there_is_no_tape():
    s = _session()
    assert repo.latest_theta_no_bid_before(s, TICKER, before=NOW) == (None, None)


def test_theta_reader_keeps_the_timestamp_when_the_price_is_missing():
    """A row with no yes_ask still proves the market WAS seen then — is_hot_entry needs that to
    tell 'quiet during the window' (hot) apart from 'no tape at all' (calm)."""
    s = _session()
    _ladder(s, TICKER, at=NOW - timedelta(minutes=5), yes_ask=None)
    no_bid, at = repo.latest_theta_no_bid_before(s, TICKER, before=NOW)
    assert no_bid is None and _naive(at) == _naive(NOW - timedelta(minutes=5))


def test_the_two_readers_agree_on_shape_for_the_same_market_state():
    """Same underlying quote (yes 5/7 -> no-bid 93) taped by either book must read identically,
    which is what lets one decision function serve both."""
    s = _session()
    _tick(s, TICKER, at=NOW - timedelta(minutes=5), no_bid=93)
    _ladder(s, TICKER, at=NOW - timedelta(minutes=5), yes_ask=7.0)
    assert (repo.latest_mmsell_no_bid_before(s, TICKER, before=NOW)
            == repo.latest_theta_no_bid_before(s, TICKER, before=NOW))
