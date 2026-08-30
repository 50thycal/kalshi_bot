"""PERP-V1 Probe 1 — the tape collector.

These tests exist for the properties that make the tape trustworthy rather than
merely present: that a number is never guessed, that a gap is recorded as a gap,
that the collector cannot kill a trading cycle, and that it places nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from kalshi_bot.kalshi.errors import AuthError
from kalshi_bot.models import (
    PerpCollectorCycle,
    PerpFundingObservation,
    PerpMarketSnapshot,
    PerpOrderbookSnapshot,
)
from kalshi_bot.perps.collector import (
    PerpsCollector,
    _as_datetime,
    _depth,
    _funding_key,
    _funding_rows,
    _nested_price,
    _num,
    _payload_shape,
    _premium_bps,
)

UTC = timezone.utc


class _Client:
    """A stand-in for KalshiClient with only the perp reads the collector uses."""

    def __init__(self, markets=None, book=None, funding=None, fail=None):
        self._markets = markets if markets is not None else []
        self._book = book if book is not None else {"orderbook": {"bids": [], "asks": []}}
        self._funding = funding if funding is not None else {}
        self._fail = fail or {}
        self.calls: list[str] = []

    def _maybe_fail(self, what):
        exc = self._fail.get(what)
        if exc is not None:
            raise exc

    def get_perp_markets(self, *, limit=None, cursor=None):
        self.calls.append("markets")
        self._maybe_fail("markets")
        return {"markets": self._markets}

    def get_perp_orderbook(self, ticker):
        self.calls.append(f"book:{ticker}")
        self._maybe_fail("book")
        return self._book

    def get_perp_funding_history(self, *, start_date, end_date, ticker=None):
        self.calls.append("funding")
        self._maybe_fail("funding")
        return self._funding


@pytest.fixture
def settings(monkeypatch):
    from kalshi_bot.config import Settings

    return Settings(
        kalshi_api_key_id="k", kalshi_private_key="p", database_url="sqlite://",
        perps_collector_enabled=True, perps_funding_enabled=False,
    )


@pytest.fixture
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from kalshi_bot.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


MARKET = {
    "ticker": "KXBTCPERP",
    "status": "active",
    # Kalshi returns these as decimal STRINGS, which is why the parser exists.
    "bid": "99950.5", "ask": "99960.5", "price": "99955.0",
    "open_interest": "1200", "open_interest_notional_value_dollars": "119946000",
    "volume": "340", "volume_24h": "9100",
    # Nested objects whose inner shape the survey never read.
    "reference_price": {"price": "99900.0"},
    "settlement_mark_price": {"price": "99955.0"},
}


# --- number handling: the part that must never guess ----------------------

def test_decimal_strings_parse_because_that_is_what_kalshi_returns():
    assert _num("99950.5") == pytest.approx(99950.5)
    assert _num(3) == 3.0
    assert _num(None) is None
    assert _num("not a number") is None


def test_a_bool_is_never_a_number():
    """float(True) is 1.0. A bool sliding into a price column is a silent
    corruption; a NULL is a visible gap."""
    assert _num(True) is None
    assert _num(False) is None


def test_a_nested_price_object_yields_its_number_or_nothing():
    assert _nested_price({"price": "12.5"}) == pytest.approx(12.5)
    assert _nested_price(7) == 7.0
    # An object with no recognised key resolves to None rather than to whichever
    # number happened to be in it — being wrong about the index price is worse
    # than not having it.
    assert _nested_price({"unexpected_key": 3.0}) is None
    assert _nested_price(None) is None


def test_premium_is_none_rather_than_infinite_on_a_zero_index():
    """A silently-infinite premium is arm A's entry signal."""
    assert _premium_bps(100.0, 0) is None
    assert _premium_bps(100.0, None) is None
    assert _premium_bps(None, 100.0) is None
    assert _premium_bps(101.0, 100.0) == pytest.approx(100.0)


# --- the snapshot ---------------------------------------------------------

def test_a_market_poll_writes_the_measured_fields_and_the_raw_payload(
    session, settings
):
    collector = PerpsCollector(_Client(markets=[MARKET]), settings)
    at = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    collector.run_once(session, now=at)

    row = session.scalars(select(PerpMarketSnapshot)).one()
    assert row.ticker == "KXBTCPERP"
    assert row.bid == pytest.approx(99950.5)
    assert row.reference_price == pytest.approx(99900.0)
    # (99955 - 99900) / 99900 * 10_000
    assert row.premium_bps == pytest.approx(5.505, rel=1e-3)
    # The raw payload survives, because the nested objects hold fields this
    # project has never read.
    assert row.raw_json == MARKET
    assert row.reference_price_json == {"price": "99900.0"}


def test_an_unreadable_index_leaves_premium_null_not_zero(session, settings):
    """A missing premium and a zero premium are opposite claims: one is 'we do
    not know', the other is 'the perp is exactly at its index'."""
    market = dict(MARKET, reference_price={"shape_we_have_never_seen": 1})
    PerpsCollector(_Client(markets=[market]), settings).run_once(session)
    row = session.scalars(select(PerpMarketSnapshot)).one()
    assert row.reference_price is None
    assert row.premium_bps is None
    assert row.raw_json["reference_price"] == {"shape_we_have_never_seen": 1}


# --- the order book -------------------------------------------------------

def test_depth_reads_both_plausible_level_shapes():
    assert _depth([[100.0, 5], [99.0, 3]]) == pytest.approx(8.0)
    assert _depth([{"price": 100.0, "size": 5}]) == pytest.approx(5.0)
    assert _depth([]) is None
    assert _depth("nonsense") is None


def test_an_empty_book_is_not_a_balanced_one(session, settings):
    """depth_imbalance must be NULL, not 0.5, when there is nothing on either
    side — arm C would read the difference as a signal."""
    collector = PerpsCollector(
        _Client(markets=[MARKET], book={"orderbook": {"bids": [], "asks": []}}), settings
    )
    collector.run_once(session)
    book = session.scalars(select(PerpOrderbookSnapshot)).one()
    assert book.depth_imbalance is None


def test_depth_imbalance_is_the_bid_share(session, settings):
    collector = PerpsCollector(
        _Client(markets=[MARKET],
                book={"orderbook": {"bids": [[99.0, 30]], "asks": [[101.0, 10]]}}),
        settings,
    )
    collector.run_once(session)
    book = session.scalars(select(PerpOrderbookSnapshot)).one()
    assert book.depth_imbalance == pytest.approx(0.75)
    assert book.best_bid == pytest.approx(99.0)
    assert book.best_ask == pytest.approx(101.0)


# --- funding: shape unknown, so store everything --------------------------

def test_funding_rows_are_found_without_knowing_the_envelope_key():
    """No run has read a 200 from this endpoint, so the collector must not index
    a key it cannot know exists."""
    assert _funding_rows({"funding_rates": [{"rate": 1}]}) == [{"rate": 1}]
    assert _funding_rows([{"rate": 2}]) == [{"rate": 2}]
    assert _funding_rows({"nothing": "useful"}) == []
    assert _funding_rows(None) == []


def test_identical_funding_records_share_a_key_and_corrected_ones_do_not():
    """Overlapping fetches are deliberate; double-counting a funding period would
    land straight in arm B's carry."""
    row = {"ticker": "KXBTCPERP", "rate": "0.0001", "ts": 1788000000}
    assert _funding_key(row) == _funding_key(dict(row))
    assert _funding_key(row) != _funding_key(dict(row, rate="0.0002"))
    # An explicit id wins over the hash.
    assert _funding_key({"id": "abc", "rate": 1}) == "abc"


def test_funding_timestamps_parse_from_epoch_seconds_millis_and_iso():
    assert _as_datetime(1788000000).year == 2026
    assert _as_datetime(1788000000000).year == 2026  # millis
    assert _as_datetime("2026-08-30T12:00:00Z") == datetime(2026, 8, 30, 12, tzinfo=UTC)
    assert _as_datetime("not a date") is None
    assert _as_datetime(True) is None


def test_funding_is_stored_whole_even_though_its_shape_is_unverified(
    session, settings
):
    settings.perps_funding_enabled = True
    payload = {"funding_rates": [
        {"ticker": "KXBTCPERP", "funding_rate": "0.00012", "settled_at": 1788000000},
    ]}
    PerpsCollector(_Client(markets=[MARKET], funding=payload), settings).run_once(session)
    row = session.scalars(select(PerpFundingObservation)).one()
    assert row.ticker == "KXBTCPERP"
    assert row.funding_rate == pytest.approx(0.00012)
    assert row.observed_at is not None
    assert row.raw_json == payload["funding_rates"][0]


def test_a_zero_row_funding_parse_records_the_envelope_shape(session, settings):
    """Production returns 200 here and the parser finds nothing in it.

    An empty market-wide RATES feed and an empty account PAYMENTS ledger look
    identical from the row count and lead to opposite conclusions about arm B, so
    the cycle keeps the envelope's shape when it stores nothing.
    """
    settings.perps_funding_enabled = True
    payload = {"settlements": [], "cursor": ""}
    PerpsCollector(_Client(markets=[MARKET], funding=payload), settings).run_once(session)
    cycle = session.scalars(select(PerpCollectorCycle)).one()
    assert cycle.funding_rows == 0
    assert cycle.errors == 0
    assert cycle.notes_json["funding_shape"] == {
        "type": "object", "keys": ["cursor", "settlements"], "list_lengths": {"settlements": 0},
    }


def test_a_successful_funding_parse_records_no_shape(session, settings):
    settings.perps_funding_enabled = True
    payload = {"funding_rates": [{"ticker": "KXBTCPERP", "rate": "0.0001"}]}
    PerpsCollector(_Client(markets=[MARKET], funding=payload), settings).run_once(session)
    cycle = session.scalars(select(PerpCollectorCycle)).one()
    assert (cycle.notes_json or {}).get("funding_shape") is None


def test_payload_shape_never_carries_a_value():
    """This endpoint is authenticated and may be returning our own account
    history; `notes_json` is read through the PUBLIC ops channel."""
    shape = _payload_shape({"balance": "1234.56", "rows": [{"secret": "x"}]})
    blob = repr(shape)
    assert "1234.56" not in blob and "secret" not in blob and "x" not in blob
    assert shape["keys"] == ["balance", "rows"]
    assert shape["list_lengths"] == {"rows": 1}
    assert _payload_shape([1, 2, 3]) == {"type": "array", "length": 3}
    assert _payload_shape(None) == {"type": "NoneType"}



# --- the coverage denominator --------------------------------------------

def test_every_cycle_writes_a_telemetry_row(session, settings):
    """`perp_data_coverage_pct` gates every arm, and rows that were never written
    look exactly like a market that did not exist — unless the attempt is
    recorded."""
    PerpsCollector(_Client(markets=[MARKET]), settings).run_once(session)
    cycle = session.scalars(select(PerpCollectorCycle)).one()
    assert cycle.markets_seen == 1
    assert cycle.market_snapshots == 1
    assert cycle.errors == 0
    assert cycle.finished_at is not None


def test_a_failed_listing_still_records_a_cycle_with_the_error(session, settings):
    collector = PerpsCollector(
        _Client(fail={"markets": RuntimeError("kalshi is down")}), settings
    )
    collector.run_once(session)
    cycle = session.scalars(select(PerpCollectorCycle)).one()
    assert cycle.errors == 1
    assert "kalshi is down" in cycle.notes_json["markets"]
    assert session.scalars(select(PerpMarketSnapshot)).all() == []


def test_one_bad_orderbook_does_not_lose_the_rest_of_the_cycle(session, settings):
    collector = PerpsCollector(
        _Client(markets=[MARKET], fail={"book": RuntimeError("book blew up")}), settings
    )
    cycle = collector.run_once(session)
    # The market snapshot still landed.
    assert session.scalars(select(PerpMarketSnapshot)).all()
    assert cycle.errors == 1
    assert cycle.orderbook_snapshots == 0


def test_auth_failure_is_the_one_error_that_propagates(session, settings):
    """The worker treats AuthError as fail-closed. A collector that swallowed it
    would mask a credential problem affecting every book in the process."""
    collector = PerpsCollector(
        _Client(fail={"markets": AuthError("bad key")}), settings
    )
    with pytest.raises(AuthError):
        collector.run_once(session)


# --- what it must not do --------------------------------------------------

def test_the_collector_can_only_read(session, settings):
    """No order path, no position, no strategy tag. The client stand-in exposes
    only reads, so any write attempt would fail here — and the methods the
    collector actually calls are asserted so a later edit cannot quietly add one.
    """
    client = _Client(markets=[MARKET])
    PerpsCollector(client, settings).run_once(session)
    assert client.calls == ["markets", "book:KXBTCPERP"]


def test_an_asset_filter_restricts_the_universe(session, settings):
    settings.perps_assets = "BTC"
    markets = [MARKET, dict(MARKET, ticker="KXDOGEPERP")]
    PerpsCollector(_Client(markets=markets), settings).run_once(session)
    tickers = [r.ticker for r in session.scalars(select(PerpMarketSnapshot))]
    assert tickers == ["KXBTCPERP"]
