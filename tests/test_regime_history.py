"""The mmsell settled-history capture (kalshi_bot/mmsell/history.py).

This job exists because Kalshi serves only a rolling ~70-day window of settled markets, so
history it fails to store is gone permanently — there is no re-run that recovers it. The tests
therefore concentrate on the ways it could silently capture LESS than it should:

  * re-enumeration must not re-fetch candles it already has (the queue would never drain);
  * the candle queue must drain OLDEST-first, so the rows closest to the wall (an ended season
    that will never produce another market) are attempted before replaceable daily ones;
  * a market whose candles Kalshi no longer serves must not wedge the queue behind it;
  * a failing series, or a failing candle fetch, must not abort the rest of the pass;
  * unsettled/void and thin markets must be skipped, since they cannot label a backtest;
  * the worker's regime map is a deliberate duplicate of the ops script's and must not drift.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot import models as m
from kalshi_bot.config import Settings
from kalshi_bot.kalshi.errors import KalshiAPIError
from kalshi_bot.mmsell.history import RegimeHistoryCapture
from kalshi_bot.mmsell.regimes import regime_of

UTC = timezone.utc


def _settings(**over):
    base = dict(
        _env_file=None,
        kalshi_api_key_id="k", kalshi_private_key="pem",
        database_url="sqlite://", bot_mode="weather",
        mmsell_history_series="KXNFLGAME",
        mmsell_history_markets_per_cycle=10,
        mmsell_history_min_volume=100.0,
        mmsell_history_period_minutes=60,
        mmsell_history_capture_hours=336.0,
    )
    base.update(over)
    return Settings(**base)


class FakeClient:
    """Minimal stand-in for KalshiClient: serves a settled-market list and candle pages."""

    def __init__(self, markets=None, candles=None, fail_series=(), fail_candles=()):
        self._markets = markets or []
        self._candles = candles or {}
        self._fail_series = set(fail_series)
        self._fail_candles = set(fail_candles)
        self.candle_calls: list[str] = []
        self.historical_calls: list[str] = []

    def iter_markets(self, *, status, series_ticker, page_size=1000, max_markets=None, **_kw):
        assert status == "settled"
        if series_ticker in self._fail_series:
            raise RuntimeError(f"boom {series_ticker}")
        for mk in self._markets:
            if mk.get("series") == series_ticker:
                yield mk

    def get_market_candlesticks(self, series, ticker, *, start_ts, end_ts, period_interval):
        self.candle_calls.append(ticker)
        if ticker in self._fail_candles:
            raise RuntimeError("candle boom")
        return {"candlesticks": self._candles.get(ticker, [])}

    def get_historical_market_candlesticks(self, ticker, *, start_ts, end_ts, period_interval):
        self.historical_calls.append(ticker)
        return {"candlesticks": self._candles.get(ticker, [])}


def _market(ticker, *, series="KXNFLGAME", result="no", volume=500.0, close_days_ago=1):
    close = datetime.now(UTC) - timedelta(days=close_days_ago)
    return {
        "ticker": ticker, "series": series, "result": result, "volume_fp": volume,
        "title": f"title {ticker}", "open_interest_fp": 10.0,
        "open_time": (close - timedelta(days=3)).isoformat().replace("+00:00", "Z"),
        "close_time": close.isoformat().replace("+00:00", "Z"),
    }


def _candle(ts_epoch, bid=5.0, ask=7.0):
    """Kalshi's real candle shape: prices are DOLLAR STRINGS ('0.0600'), not floats — verified
    against the live API. price_to_cents treats a bare float as already-cents, so a float fixture
    would silently round 0.05 to 0 and the test would pass on data the API never sends."""
    return {
        "end_period_ts": ts_epoch,
        "price": {"open": "0.0600", "high": "0.0700", "low": "0.0500", "close": "0.0600"},
        "yes_bid": {"close": f"{bid / 100.0:.4f}"},
        "yes_ask": {"close": f"{ask / 100.0:.4f}"},
        "volume": 12,
    }


# ------------------------------------------------------------------ enumeration


def test_enumerates_and_stores_settled_markets_with_their_regime(session):
    client = FakeClient(markets=[_market("KXNFLGAME-A-DAL"), _market("KXNFLGAME-B-PHI")])
    cap = RegimeHistoryCapture(client, _settings(), sleep_s=0)

    summary = cap.run_once(session)

    assert summary.markets_enumerated == 2
    rows = session.query(m.BackfillRegimeMarket).all()
    assert {r.market_ticker for r in rows} == {"KXNFLGAME-A-DAL", "KXNFLGAME-B-PHI"}
    assert {r.regime for r in rows} == {"NFL"}          # stamped at capture time
    assert {r.series_ticker for r in rows} == {"KXNFLGAME"}
    assert all(r.event_ticker == r.market_ticker.rsplit("-", 1)[0] for r in rows)


def test_skips_unsettled_and_thin_markets(session):
    """A market with no yes/no result cannot label a backtest, and one under the volume floor is
    one the book would never have entered — storing either just inflates the candle queue."""
    client = FakeClient(markets=[
        _market("KXNFLGAME-VOID", result=""),
        _market("KXNFLGAME-THIN", volume=5.0),
        _market("KXNFLGAME-GOOD"),
    ])
    cap = RegimeHistoryCapture(client, _settings(), sleep_s=0)

    cap.run_once(session)

    assert [r.market_ticker for r in session.query(m.BackfillRegimeMarket).all()] == [
        "KXNFLGAME-GOOD"]


def test_reenumeration_does_not_requeue_already_captured_markets(session):
    """The window slides, so enumeration re-runs forever over an overlapping range. If a re-seen
    market reset candles_fetched, the capture would re-fetch the same candles every pass and
    never reach fresh settlements."""
    client = FakeClient(
        markets=[_market("KXNFLGAME-A-DAL")],
        candles={"KXNFLGAME-A-DAL": [_candle(1_800_000_000)]},
    )
    cap = RegimeHistoryCapture(client, _settings(), sleep_s=0)
    cap.run_once(session)
    assert client.candle_calls == ["KXNFLGAME-A-DAL"]

    cap._last_enumerate = None         # force a second enumeration pass
    summary = cap.run_once(session)

    assert summary.markets_enumerated == 0             # nothing new
    assert client.candle_calls == ["KXNFLGAME-A-DAL"]  # and no re-fetch
    assert summary.pending == 0


def test_enumeration_is_throttled_between_cycles(session):
    client = FakeClient(markets=[_market("KXNFLGAME-A-DAL")])
    cap = RegimeHistoryCapture(client, _settings(mmsell_history_enumerate_minutes=360.0),
                               sleep_s=0)

    first = cap.run_once(session)
    second = cap.run_once(session)

    assert first.enumerated_this_cycle is True   # a fresh worker captures immediately, and
    #                                             time.monotonic()'s arbitrary zero point must
    #                                             not be mistaken for "just enumerated"
    assert second.enumerated_this_cycle is False
    assert second.series_enumerated == 0


def test_one_failing_series_does_not_sink_the_others(session):
    client = FakeClient(
        markets=[_market("KXNBAGAME-A-BOS", series="KXNBAGAME")],
        fail_series={"KXNFLGAME"},
    )
    cap = RegimeHistoryCapture(
        client, _settings(mmsell_history_series="KXNFLGAME,KXNBAGAME"), sleep_s=0)

    summary = cap.run_once(session)

    assert summary.errors == 1
    assert summary.markets_enumerated == 1
    assert session.query(m.BackfillRegimeMarket).one().regime == "NBA"


# ------------------------------------------------------------------ candles


def test_stores_candles_and_marks_the_market_done(session):
    client = FakeClient(
        markets=[_market("KXNFLGAME-A-DAL")],
        candles={"KXNFLGAME-A-DAL": [_candle(1_800_000_000), _candle(1_800_003_600, 6.0, 8.0)]},
    )
    cap = RegimeHistoryCapture(client, _settings(), sleep_s=0)

    summary = cap.run_once(session)

    assert summary.markets_fetched == 1
    assert summary.candles_stored == 2
    stored = session.query(m.BackfillRegimeCandle).all()
    assert len(stored) == 2
    # bid AND ask are load-bearing: the mmsell entry filter needs the ask, not just a mid.
    assert sorted(c.yes_ask_close for c in stored) == [7.0, 8.0]
    assert sorted(c.yes_bid_close for c in stored) == [5.0, 6.0]
    assert {c.period_minutes for c in stored} == {60}
    assert session.query(m.BackfillRegimeMarket).one().candle_count == 2


def test_market_with_no_candles_is_marked_done_not_left_to_wedge_the_queue(session):
    """Kalshi ages candles out too. Since the queue drains oldest-first, an expired block sits at
    its HEAD — so retiring a zero-candle market is what stops it from wedging everything behind
    it. This is the guard that makes oldest-first safe."""
    client = FakeClient(markets=[_market("KXNFLGAME-GONE")], candles={})
    cap = RegimeHistoryCapture(client, _settings(), sleep_s=0)

    summary = cap.run_once(session)

    assert summary.pending == 0
    row = session.query(m.BackfillRegimeMarket).one()
    assert row.candles_fetched is True
    assert row.candle_count == 0


def test_candle_failure_leaves_the_market_pending_for_retry(session):
    """A transport error is not evidence the history is gone — unlike an empty response, it must
    be retried rather than marked done."""
    client = FakeClient(
        markets=[_market("KXNFLGAME-A-DAL")], fail_candles={"KXNFLGAME-A-DAL"})
    cap = RegimeHistoryCapture(client, _settings(), sleep_s=0)

    summary = cap.run_once(session)

    assert summary.errors == 1
    assert summary.markets_fetched == 0
    assert summary.pending == 1
    assert session.query(m.BackfillRegimeMarket).one().candles_fetched is False


def test_falls_back_to_the_historical_endpoint_on_404(session):
    """The live candle endpoint 404s once a market ages past Kalshi's cutoff — which is exactly
    the case this capture is racing, so the fallback is the load-bearing path over time."""
    class Fallback404(FakeClient):
        def get_market_candlesticks(self, series, ticker, **_kw):
            self.candle_calls.append(ticker)
            raise KalshiAPIError(404, "gone", "/candlesticks")

    client = Fallback404(
        markets=[_market("KXNFLGAME-OLD")],
        candles={"KXNFLGAME-OLD": [_candle(1_800_000_000)]},
    )
    cap = RegimeHistoryCapture(client, _settings(), sleep_s=0)

    summary = cap.run_once(session)

    assert client.historical_calls == ["KXNFLGAME-OLD"]
    assert summary.candles_stored == 1


def test_per_cycle_chunk_bounds_the_api_budget(session):
    markets = [_market(f"KXNFLGAME-{i}", close_days_ago=i + 1) for i in range(5)]
    client = FakeClient(markets=markets, candles={mk["ticker"]: [_candle(1_800_000_000)]
                                                  for mk in markets})
    cap = RegimeHistoryCapture(client, _settings(mmsell_history_markets_per_cycle=2), sleep_s=0)

    summary = cap.run_once(session)

    assert summary.markets_fetched == 2
    assert summary.pending == 3
    # OLDEST settlement first — the rows closest to Kalshi's wall, i.e. the ones about to become
    # unrecoverable. _market(close_days_ago=i+1) makes KXNFLGAME-4 the oldest.
    assert client.candle_calls == ["KXNFLGAME-4", "KXNFLGAME-3"]


def test_repeated_series_in_config_are_not_enumerated_twice():
    s = _settings(mmsell_history_series="KXNFLGAME, kxnflgame ,KXNBAGAME")
    assert s.mmsell_history_series_list == ["KXNFLGAME", "KXNBAGAME"]


# ------------------------------------------------------------------ the duplicated regime map


def test_worker_regime_map_matches_the_ops_script():
    """kalshi_bot/mmsell/regimes.py duplicates scripts/mmsell_seasonal.py because ops scripts run
    on a runner that never installs this package. If the two drift, the capture files history
    under one label and the backtest reads it under another."""
    scripts = pathlib.Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(
        "mmsell_seasonal", scripts / "mmsell_seasonal.py")
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spec and ms)

    from kalshi_bot.mmsell import regimes

    assert regimes.REGIMES == ms.REGIMES
    for series in ("KXNFLGAME", "KXNCAABBGAME", "KXSENATEMID", "KXBTCD", "KXWCGOAL", "KXZZZ"):
        assert regime_of(series) == ms.regime(series), series


@pytest.fixture()
def session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    m.Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# --- contest keys (XOS-000020) ----------------------------------------------
#
# `event_ticker_of` returns SERIES x contest, so one baseball game is up to five
# distinct "events". Every concentration cap keys on that, which is how Dmmsell10
# came to hold 6 markets across 5 series on one NYYLAA game. `contest_key_of` is
# the identifier those five share, and the whole cap rests on it grouping the way
# the money does.


def test_one_game_has_one_contest_key_across_every_series():
    """The exact tickers Dmmsell10 held on 2026-09-02, verbatim from live_orders."""
    from kalshi_bot.mmsell.regimes import contest_key_of

    nyylaa = [
        "KXMLBF5TOTAL-26SEP022138NYYLAA-5",
        "KXMLBHR-26SEP022138NYYLAA-AARONJUDGE1",
        "KXMLBSPREAD-26SEP022138NYYLAA-NYY3",
        "KXMLBTEAMTOTAL-26SEP022138NYYLAA-NYY6",
        "KXMLBTOTAL-26SEP022138NYYLAA-8",
    ]

    keys = {contest_key_of(t) for t in nyylaa}

    assert keys == {"MLB:26SEP022138NYYLAA"}, (
        "five series on one game must collapse to ONE contest — this grouping is "
        "the entire cap"
    )


def test_different_games_never_share_a_contest_key():
    """The cap must not refuse an unrelated game. Same series, same date, two
    matchups; and the same matchup at two start times is two contests."""
    from kalshi_bot.mmsell.regimes import contest_key_of

    assert contest_key_of("KXMLBTOTAL-26SEP022138NYYLAA-8") != contest_key_of(
        "KXMLBTOTAL-26SEP022210STLLAD-13"
    )
    assert contest_key_of("KXMLBTOTAL-26SEP022138NYYLAA-8") != contest_key_of(
        "KXMLBTOTAL-26SEP031915NYYLAA-8"
    )


def test_two_UNRELATED_releases_sharing_a_date_are_NOT_one_contest():
    """The failure this design is shaped around, and the reason grouping is
    restricted to sports. `KXPAYROLLS-26SEP` and `KXCPI-26SEP` share the token
    "26SEP" and share no result whatever. Grouping them would silently refuse
    independent entries — a cap that rejects good trades invisibly is worse than
    the gap it closes."""
    from kalshi_bot.mmsell.regimes import contest_key_of

    payrolls = contest_key_of("KXPAYROLLS-26SEP")
    cpi = contest_key_of("KXCPI-26SEP")

    assert payrolls != cpi
    # And outside the grouped regimes the key IS the event ticker, so switching
    # the cap on cannot change behaviour there at all.
    assert payrolls == "KXPAYROLLS-26SEP"
    assert cpi == "KXCPI-26SEP"


def test_ungrouped_regimes_keep_exactly_todays_event_identity():
    """Every existing cap keys on `event_ticker_of`. Outside the grouped regimes
    the contest key must equal it, or enabling the cap would quietly re-scope the
    caps that already exist."""
    from kalshi_bot.mmsell.regimes import contest_key_of, event_ticker_of

    for ticker in ("KXBTC-26SEP0217-B120", "KXPAYROLLS-26SEP", "KXRT-26SEP-X",
                   "KXTRUMPSAY-26SEP02-HELLO"):
        assert contest_key_of(ticker) == event_ticker_of(ticker), ticker


def test_an_unrecognised_shape_groups_to_ITSELF_not_to_everything():
    """The dangerous failure is collapsing unrelated markets into one bucket. A
    ticker with no contest token must fall back to its own event, never to a
    shared empty key."""
    from kalshi_bot.mmsell.regimes import contest_key_of

    assert contest_key_of(None) is None
    assert contest_key_of("") is None
    assert contest_key_of("KXWEIRD") == "KXWEIRD"


def test_every_grouped_regime_is_a_REAL_regime_name():
    """A typo in the allowlist would silently disable grouping for that sport —
    the cap would look enabled and do nothing."""
    from kalshi_bot.mmsell.regimes import CONTEST_GROUPED_REGIMES, REGIMES

    known = {name for name, _ in REGIMES}

    assert CONTEST_GROUPED_REGIMES <= known, CONTEST_GROUPED_REGIMES - known
