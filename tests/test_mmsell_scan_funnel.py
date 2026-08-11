"""The mmsell scan funnel — event-window filtering, pagination, and its telemetry.

On 2026-08-08 the books were entering ~2 series/day, down from 32, while Kalshi listed 9,656
sports markets. Cause: the scan ranked ALL open events by volume and cut to the top 150 BEFORE
applying the entry window, so the budget went to 2028 election futures and season championships
that the window then threw away.

Two classes of regression are pinned here:
  * the ORDERING — window filter must precede the volume cut, or the cap silently selects for
    long-dated markets instead of bounding cost;
  * the OBSERVABILITY — the funnel counters must be persisted somewhere queryable. They existed
    before, inside a log line, and Railway's log endpoint drops structured fields, so the
    starvation had to be inferred from candidate-tick volume and a live market survey instead of
    measured in one query.
"""

from __future__ import annotations

from kalshi_bot.config import Settings
from kalshi_bot.mmsell.tracker import MmSellTracker


def _settings(**over):
    base = dict(_env_file=None, kalshi_api_key_id="k", kalshi_private_key="p",
                database_url="sqlite://", bot_mode="weather")
    base.update(over)
    return Settings(**base)


def _mkt(hours_to_close: float | None):
    from datetime import datetime, timedelta, timezone
    if hours_to_close is None:
        return {"ticker": "T", "close_time": None}
    when = datetime.now(timezone.utc) + timedelta(hours=hours_to_close)
    return {"ticker": "T", "close_time": when.isoformat().replace("+00:00", "Z")}


def test_event_with_a_market_in_window_is_eligible():
    s = _settings()
    assert MmSellTracker._event_has_window_market({"markets": [_mkt(5)]}, s) is True
    assert MmSellTracker._event_has_window_market({"markets": [_mkt(300)]}, s) is True


def test_long_dated_futures_are_rejected_before_the_volume_cut():
    """The exact population that starved the scan: 2028 nominations, season championships and
    end-of-year crypto all close far beyond htcmax, and were consuming top-150 slots."""
    s = _settings()
    for hours in (400, 24 * 90, 24 * 400, 24 * 900):
        assert MmSellTracker._event_has_window_market({"markets": [_mkt(hours)]}, s) is False


def test_market_closing_too_soon_is_rejected():
    s = _settings()
    assert MmSellTracker._event_has_window_market({"markets": [_mkt(0.25)]}, s) is False


def test_one_eligible_market_carries_the_whole_event():
    """Events are the scan unit; an event is worth scanning if ANY of its markets is tradeable."""
    s = _settings()
    ev = {"markets": [_mkt(24 * 400), _mkt(24 * 300), _mkt(6)]}
    assert MmSellTracker._event_has_window_market(ev, s) is True


def test_unparseable_close_time_is_kept_not_dropped():
    """A parse failure is a DATA problem, not a trading decision. Keeping the event lets the
    per-market gate reject it a moment later; dropping it here would silently shrink the
    universe whenever Kalshi changed a timestamp format."""
    s = _settings()
    assert MmSellTracker._event_has_window_market({"markets": [_mkt(None)]}, s) is True
    assert MmSellTracker._event_has_window_market({"markets": [{"ticker": "T"}]}, s) is True
    # ...but a mix keeps the real signal: one parseable-and-out-of-window market is not enough
    # to admit the event on its own.
    assert MmSellTracker._event_has_window_market({"markets": [_mkt(24 * 400)]}, s) is False


def test_event_with_no_markets_is_not_eligible():
    s = _settings()
    assert MmSellTracker._event_has_window_market({"markets": []}, s) is False
    assert MmSellTracker._event_has_window_market({}, s) is False


def test_page_cap_is_configurable_and_above_the_old_hard_coded_40():
    """40 pages x limit=200 = 8,000 events, which Kalshi's open universe now MEETS exactly — so
    the scan was truncating before ranking, arbitrarily (pages arrive in Kalshi's order)."""
    s = _settings()
    assert s.mmsell_event_pages > 40
    assert s.mmsell_event_pages * 200 > 8000
    assert _settings(mmsell_event_pages=7).mmsell_event_pages == 7


def test_summary_carries_the_whole_funnel():
    """Every stage between 'the API returned events' and 'we opened a position' must be counted,
    or a future starvation is again only inferable."""
    from kalshi_bot.mmsell.tracker import MmSellCycleSummary

    summ = MmSellCycleSummary()
    for field in ("pages_fetched", "pagination_exhausted", "events_fetched",
                  "events_out_of_window", "events_eligible", "events_dropped_by_cap",
                  "events_seen", "markets_considered", "in_band", "opened", "skipped_htc"):
        assert hasattr(summ, field), field
    # pagination_exhausted defaults False so an un-run scan never claims a full universe.
    assert summ.pagination_exhausted is False


def test_telemetry_write_never_breaks_the_cycle():
    """A diagnostic must not be able to stop trading.

    Covers BOTH system_events writes the method makes — the scan funnel and the quote-parity
    row — because the second was added later and a bare `return` on the first would have left
    it untested."""
    from kalshi_bot.mmsell.quote_parity import BandProbe, QuoteParityAccumulator
    from kalshi_bot.mmsell.tracker import MmSellCycleSummary

    class _Boom:
        def add(self, *a, **k):
            raise RuntimeError("db down")

        def flush(self):
            raise RuntimeError("db down")

    class _Client:
        def transient_counts(self):
            return {429: 3, 502: 1}

    class _Self:
        client = _Client()

    summ = MmSellCycleSummary()
    summ.quote_parity = QuoteParityAccumulator(bands=(BandProbe("wide", 5.0, 40.0),))
    MmSellTracker._record_scan_telemetry(_Self(), _Boom(), summ)


def test_telemetry_survives_a_client_without_transient_counts():
    """The counter is read off the client with getattr, so an older/stubbed client (or one
    whose snapshot raises) must still produce a funnel row rather than lose the whole cycle's
    telemetry to an AttributeError."""
    from kalshi_bot.mmsell.tracker import MmSellCycleSummary

    written: list[dict] = []

    class _Session:
        def add(self, row):
            written.append(getattr(row, "raw_json", {}) or {})

        def flush(self):
            pass

    class _Angry:
        def transient_counts(self):
            raise RuntimeError("boom")

    class _NoCounter:
        pass

    for client in (_Angry(), _NoCounter()):
        written.clear()

        class _Self:
            pass

        _Self.client = client
        MmSellTracker._record_scan_telemetry(_Self(), _Session(), MmSellCycleSummary())
        assert written, f"no telemetry row written for {type(client).__name__}"
        assert written[0]["transient"] == {}
