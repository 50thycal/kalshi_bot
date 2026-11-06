"""scripts/kalshi_settled.py — the live-then-historical settlement read.

Acceptance (docs/handoffs/HANDOFF-kalshi-historical-cutoff.md, check 1): a live 404 falls
through to the historical tier; a market neither tier has is counted as `not found`, never
dropped; any other error is raised, not swallowed. No network: a fake getter plays Kalshi."""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import pathlib
import re
import sys
import urllib.error

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("kalshi_settled", _SCRIPTS / "kalshi_settled.py")
ks = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ks)  # type: ignore[union-attr]


def _err(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", None, None)  # type: ignore[arg-type]


class FakeKalshi:
    """Two tiers of markets. `live`/`hist` map ticker -> market. Records every call."""

    def __init__(self, live=(), hist=(), *, hist_event_filter=True, fail=None):
        self.live = {m["ticker"]: m for m in live}
        self.hist = {m["ticker"]: m for m in hist}
        self.hist_event_filter = hist_event_filter
        self.fail = fail or {}            # path prefix -> HTTP code to raise
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path: str, params: dict) -> dict:
        self.calls.append((path, dict(params)))
        for prefix, code in self.fail.items():
            if path.startswith(prefix):
                raise _err(code)
        if path == "/historical/cutoff":
            return {"market_settled_ts": "runtime-value"}
        hist = path.startswith("/historical/")
        tier = self.hist if hist else self.live
        base = "/historical/markets" if hist else "/markets"
        if path.startswith(base + "/"):
            t = path[len(base) + 1:]
            if t not in tier:
                raise _err(404)
            return {"market": tier[t]}
        if path == base:
            if "event_ticker" in params:
                if hist and self.hist_event_filter is None:
                    return {"markets": list(tier.values()), "cursor": ""}  # filter ignored
                if hist and not self.hist_event_filter:
                    raise _err(400)
                ev = params["event_ticker"]
                return {"markets": [m for m in tier.values() if m["event_ticker"] == ev], "cursor": ""}
            if "tickers" in params:
                want = set(params["tickers"].split(","))
                return {"markets": [m for t, m in tier.items() if t in want], "cursor": ""}
        raise AssertionError(f"unexpected call {path} {params}")


def _m(ticker: str, result: str = "yes") -> dict:
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "result": result}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(ks, "_HIST_EVENT_FILTER", None)
    monkeypatch.setattr(ks, "_CUTOFF_CACHE", {})
    monkeypatch.setattr(ks.time, "sleep", lambda s: None)


# --- one market -------------------------------------------------------------------------------

def test_live_hit_never_touches_the_historical_tier():
    k = FakeKalshi(live=[_m("EV-A")], hist=[_m("EV-A", "no")])
    m, src = ks.market("EV-A", k)
    assert src == ks.LIVE and m["result"] == "yes"
    assert all(not p.startswith("/historical/") for p, _ in k.calls)


def test_live_404_falls_through_to_historical():
    k = FakeKalshi(hist=[_m("EV-A", "no")])
    m, src = ks.market("EV-A", k)
    assert src == ks.HISTORICAL and ks.result_of(m) == "no"


def test_both_404_is_not_found_not_dropped():
    m, src = ks.market("EV-GONE", FakeKalshi())
    assert (m, src) == (None, ks.NOT_FOUND)


@pytest.mark.parametrize("code", [400, 401, 403, 500])
def test_a_non_404_live_error_is_raised_not_swallowed(code):
    k = FakeKalshi(hist=[_m("EV-A")], fail={"/markets/": code})
    with pytest.raises(urllib.error.HTTPError) as info:
        ks.market("EV-A", k)
    assert info.value.code == code
    assert all(not p.startswith("/historical/") for p, _ in k.calls), "no fallback on non-404"


def test_a_non_404_historical_error_is_raised():
    k = FakeKalshi(fail={"/historical/markets/": 503})
    with pytest.raises(urllib.error.HTTPError):
        ks.market("EV-A", k)


# --- by ticker --------------------------------------------------------------------------------

def test_markets_by_ticker_answers_for_every_ticker_with_its_source():
    k = FakeKalshi(live=[_m("E1-A")], hist=[_m("E0-B", "no")])
    got = ks.markets_by_ticker(["E1-A", "E0-B", "E9-Z"], k)
    assert set(got) == {"E1-A", "E0-B", "E9-Z"}
    assert got["E1-A"][1] == ks.LIVE
    assert got["E0-B"][1] == ks.HISTORICAL
    assert got["E9-Z"] == (None, ks.NOT_FOUND)


def test_markets_by_ticker_batches():
    tickers = [f"E-{i:03d}" for i in range(250)]
    k = FakeKalshi(hist=[_m(t) for t in tickers])
    got = ks.markets_by_ticker(tickers, k)
    assert all(src == ks.HISTORICAL for _m_, src in got.values())
    hist_batches = [p for p in k.calls if p[0] == "/historical/markets"]
    assert len(hist_batches) == 3 and all(len(p[1]["tickers"].split(",")) <= 100 for p in hist_batches)


# --- by event ---------------------------------------------------------------------------------

def test_event_live_first():
    k = FakeKalshi(live=[_m("EV-A"), _m("EV-B", "no")])
    ms, src = ks.event_markets("EV", get=k)
    assert src == ks.LIVE and {m["ticker"] for m in ms} == {"EV-A", "EV-B"}


def test_event_past_the_cutoff_comes_from_historical():
    k = FakeKalshi(hist=[_m("EV-A"), _m("EV-B", "no"), _m("OTHER-C")])
    ms, src = ks.event_markets("EV", get=k)
    assert src == ks.HISTORICAL and {m["ticker"] for m in ms} == {"EV-A", "EV-B"}
    assert ks.historical_event_filter() is True


def test_event_filter_refused_falls_back_to_tickers_and_remembers():
    k = FakeKalshi(hist=[_m("EV-A"), _m("EV-B")], hist_event_filter=False)
    ms, src = ks.event_markets("EV", ["EV-A", "EV-B"], get=k)
    assert src == ks.HISTORICAL and {m["ticker"] for m in ms} == {"EV-A", "EV-B"}
    assert ks.historical_event_filter() is False
    k.calls.clear()
    ks.event_markets("EV", ["EV-A"], get=k)
    assert not any(p == "/historical/markets" and "event_ticker" in q for p, q in k.calls), \
        "a refused filter is not retried on every event"


def test_event_filter_refused_without_tickers_is_not_found_not_an_exception():
    k = FakeKalshi(hist=[_m("EV-A")], hist_event_filter=False)
    assert ks.event_markets("EV", get=k) == ([], ks.NOT_FOUND)


def test_an_ignored_event_filter_never_grades_another_events_markets():
    k = FakeKalshi(hist=[_m("OTHER-A"), _m("OTHER-B")], hist_event_filter=None)
    assert ks.event_markets("EV", get=k) == ([], ks.NOT_FOUND)
    assert ks.historical_event_filter() is False


def test_an_ignored_event_filter_switches_to_the_ticker_path():
    k = FakeKalshi(hist=[_m("OTHER-A"), _m("EV-A", "no")], hist_event_filter=None)
    ms, src = ks.event_markets("EV", ["EV-A"], get=k)
    assert src == ks.HISTORICAL and [m["ticker"] for m in ms] == ["EV-A"]
    assert ks.historical_event_filter() is False


def test_historical_event_pages_follow_the_cursor():
    pages = iter([{"markets": [_m("EV-A")], "cursor": "c1"}, {"markets": [_m("EV-B")], "cursor": ""}])

    def get(path, params):
        if path == "/markets":
            return {"markets": [], "cursor": ""}
        assert path == "/historical/markets" and params["event_ticker"] == "EV"
        page = next(pages)
        assert params["cursor"] == ("" if page["cursor"] == "c1" else "c1")
        return page

    ms, src = ks.event_markets("EV", get=get)
    assert src == ks.HISTORICAL and [m["ticker"] for m in ms] == ["EV-A", "EV-B"]


def test_event_neither_tier_is_not_found():
    assert ks.event_markets("EV", ["EV-A"], get=FakeKalshi()) == ([], ks.NOT_FOUND)


def test_event_other_errors_propagate():
    k = FakeKalshi(fail={"/historical/markets": 500})
    with pytest.raises(urllib.error.HTTPError):
        ks.event_markets("EV", get=k)


# --- the record -------------------------------------------------------------------------------

def test_result_of_only_yes_no():
    assert ks.result_of({"result": "YES"}) == "yes"
    assert ks.result_of({"result": "no"}) == "no"
    assert ks.result_of({"result": ""}) is None
    assert ks.result_of({"result": "void"}) is None
    assert ks.result_of(None) is None


def test_tally_line_and_unknown_source():
    t = ks.Tally()
    t.add(ks.LIVE, 3)
    t.add(ks.HISTORICAL)
    t.add(ks.NOT_FOUND)
    assert t.line() == "labels: live 3 / historical 1 / not found 1"
    assert t.total == 5 and t.not_found_share() == pytest.approx(0.2)
    with pytest.raises(ValueError):
        t.add("lost")


def test_report_prints_the_source_line_and_the_runtime_cutoff():
    out = io.StringIO()
    ks.report(ks.Tally(), get=FakeKalshi(), out=out)
    text = out.getvalue()
    assert "labels: live 0 / historical 0 / not found 0" in text
    assert "runtime-value" in text


def test_no_cutoff_date_is_hardcoded():
    src = (_SCRIPTS / "kalshi_settled.py").read_text()
    assert not re.search(r"20\d\d-\d\d-\d\d", src)
    assert not re.search(r"\b1[6-9]\d{8}\b", src), "no epoch-seconds literal"


# --- series scans -----------------------------------------------------------------------------

class FakeSeries:
    """`/markets` and `/historical/markets` filtered by series_ticker, paged by `page_size`.
    Records every call so a test can see which filters reached which tier."""

    def __init__(self, live=(), hist=(), *, page_size=2, fail=None):
        self.tiers = {"/markets": list(live), "/historical/markets": list(hist)}
        self.page_size = page_size
        self.fail = fail or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if path == "/historical/cutoff":
            return {"market_settled_ts": "runtime-value"}
        if path in self.fail:
            raise _err(self.fail[path])
        rows = [m for m in self.tiers[path] if m["ticker"].startswith(params["series_ticker"] + "-")]
        start = int(params.get("cursor") or 0)
        chunk = rows[start:start + self.page_size]
        nxt = start + self.page_size
        return {"markets": [dict(m) for m in chunk], "cursor": str(nxt) if nxt < len(rows) else ""}


def _sm(ticker, close="2026-01-01T00:00:00Z", result="yes"):
    return {"ticker": ticker, "close_time": close, "result": result}


def _iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_series_scan_reads_live_then_historical_and_tags_the_source():
    k = FakeSeries(live=[_sm("S-1"), _sm("S-2"), _sm("S-3")], hist=[_sm("S-4"), _sm("S-5")])
    count = ks.ScanCount()
    ms = ks.series_markets("S", get=k, count=count)
    assert [m["ticker"] for m in ms] == ["S-1", "S-2", "S-3", "S-4", "S-5"]
    assert [m["_source"] for m in ms] == ["live"] * 3 + ["historical"] * 2
    assert count.line() == "markets: live 3 / historical 2"
    assert ks.source_line(ms[:4]) == "markets: live 3 / historical 1"


def test_series_scan_sends_only_series_ticker_to_the_historical_tier():
    k = FakeSeries(live=[_sm("S-1")], hist=[_sm("S-2")])
    ks.series_markets("S", get=k, min_close_ts=0, mve_filter="exclude")
    live = [q for p, q in k.calls if p == "/markets"]
    hist = [q for p, q in k.calls if p == "/historical/markets"]
    assert live[0]["status"] == "settled" and live[0]["mve_filter"] == "exclude"
    assert live[0]["min_close_ts"] == 0
    assert hist and all(set(q) == {"series_ticker", "limit", "cursor"} for q in hist)


def test_series_scan_dedups_a_ticker_in_both_tiers():
    k = FakeSeries(live=[_sm("S-1")], hist=[_sm("S-1", result="no"), _sm("S-2")])
    ms = ks.series_markets("S", get=k)
    assert [(m["ticker"], m["_source"]) for m in ms] == [("S-1", "live"), ("S-2", "historical")]


def test_series_scan_applies_close_bounds_client_side_on_historical():
    k = FakeSeries(hist=[_sm("S-old", _iso(500)), _sm("S-in", _iso(1_500)),
                         _sm("S-new", _iso(2_500)), {"ticker": "S-noclose", "result": "yes"}])
    ms = ks.series_markets("S", get=k, min_close_ts=1_000, max_close_ts=2_000)
    assert [m["ticker"] for m in ms] == ["S-in"]


def test_series_scan_skips_historical_for_a_non_settled_status():
    k = FakeSeries(live=[_sm("S-1")], hist=[_sm("S-2")])
    ms = ks.series_markets("S", get=k, status="open")
    assert [m["ticker"] for m in ms] == ["S-1"]
    assert not any(p == "/historical/markets" for p, _ in k.calls)
    k2 = FakeSeries(hist=[_sm("S-2")])
    assert [m["ticker"] for m in ks.series_markets("S", get=k2, status=None)] == ["S-2"]


def test_series_scan_is_lazy_so_a_count_stop_never_reaches_historical():
    k = FakeSeries(live=[_sm("S-1"), _sm("S-2"), _sm("S-3")], hist=[_sm("S-4")])
    it = ks.iter_series_markets("S", get=k)
    assert [next(it)["ticker"] for _ in range(2)] == ["S-1", "S-2"]
    assert not any(p == "/historical/markets" for p, _ in k.calls)


def test_series_scan_respects_max_pages_per_tier_and_flags_truncation():
    k = FakeSeries(live=[_sm(f"S-{i}") for i in range(10)], hist=[_sm(f"S-h{i}") for i in range(10)])
    count = ks.ScanCount()
    ms = ks.series_markets("S", get=k, max_pages=2, count=count)
    assert len(ms) == 8   # 2 pages x 2 per page, per tier
    assert count.truncated == {"live": True, "historical": True}
    assert "page cap hit: live, historical" in count.line()
    exhausted = ks.ScanCount()
    ks.series_markets("S", get=FakeSeries(live=[_sm("S-1")], hist=[_sm("S-2")]), count=exhausted)
    assert exhausted.truncated == {"live": False, "historical": False}


class CutoffSeries(FakeSeries):
    def __init__(self, cutoff, **kw):
        super().__init__(**kw)
        self.cutoff_value = cutoff

    def __call__(self, path, params):
        if path == "/historical/cutoff":
            self.calls.append((path, dict(params)))
            if isinstance(self.cutoff_value, Exception):
                raise self.cutoff_value
            return {"market_settled_ts": self.cutoff_value}
        return super().__call__(path, params)


def test_cutoff_parses_seconds_milliseconds_and_iso():
    assert ks._as_unix(1_700_000_000) == 1_700_000_000
    assert ks._as_unix(1_700_000_000_000) == 1_700_000_000
    assert ks._as_unix("1700000000") == 1_700_000_000
    assert ks._as_unix(_iso(1_700_000_000)) == 1_700_000_000
    assert ks._as_unix("garbage") is None and ks._as_unix(None) is None


@pytest.mark.parametrize("cut", [5_000, "5000", _iso(5_000)])
def test_a_window_newer_than_the_runtime_cutoff_skips_the_archive(cut):
    k = CutoffSeries(cut, live=[_sm("S-1", _iso(6_000))], hist=[_sm("S-2", _iso(6_000))])
    ms = ks.series_markets("S", get=k, min_close_ts=5_500)
    assert [m["ticker"] for m in ms] == ["S-1"]
    assert not any(p == "/historical/markets" for p, _ in k.calls)


def test_a_window_reaching_past_the_cutoff_reads_the_archive():
    k = CutoffSeries(5_000, live=[_sm("S-1", _iso(6_000))], hist=[_sm("S-2", _iso(4_800))])
    ms = ks.series_markets("S", get=k, min_close_ts=4_000)
    assert [m["ticker"] for m in ms] == ["S-1", "S-2"]


@pytest.mark.parametrize("cut", [None, "garbage", _err(500)])
def test_an_unreadable_cutoff_never_skips_the_archive(cut):
    k = CutoffSeries(cut, hist=[_sm("S-2", _iso(9_000))])
    assert [m["ticker"] for m in ks.series_markets("S", get=k, min_close_ts=8_000)] == ["S-2"]


def test_series_scan_can_read_one_tier():
    k = FakeSeries(live=[_sm("S-1")], hist=[_sm("S-2")])
    assert [m["ticker"] for m in ks.series_markets("S", get=k, tiers=(ks.HISTORICAL,))] == ["S-2"]
    assert not any(p == "/markets" for p, _ in k.calls)
    k2 = FakeSeries(live=[_sm("S-1")], hist=[_sm("S-2")])
    assert [m["ticker"] for m in ks.series_markets("S", get=k2, tiers=(ks.LIVE,))] == ["S-1"]


def test_a_failed_tier_read_is_counted_never_silent():
    k = FakeSeries(live=[_sm("S-1")], hist=[_sm("S-2")], fail={"/historical/markets": 500})
    count = ks.ScanCount()
    ms = ks.series_markets("S", get=k, count=count)
    assert [m["ticker"] for m in ms] == ["S-1"]
    assert count.errors == {"live": 0, "historical": 1}
    assert "tier read errors: live 0 / historical 1" in count.line()


def test_report_scan_prints_the_line_and_the_cutoff():
    out = io.StringIO()
    count = ks.ScanCount()
    ks.series_markets("S", get=FakeSeries(hist=[_sm("S-1")]), count=count)
    ks.report_scan(count, get=FakeSeries(), out=out)
    assert "markets: live 0 / historical 1" in out.getvalue()
    assert "runtime-value" in out.getvalue()


# --- live-only scans (all-series /events) ------------------------------------------------------

def test_live_only_note_says_what_the_cutoff_cost():
    k = CutoffSeries(5_000)
    assert "ABSENT" in ks.live_only_note(6_000, exhausted=True, get=k)
    assert "dropped nothing" in ks.live_only_note(6_000, exhausted=False, get=k)
    assert "ABSENT" in ks.live_only_note(4_000, exhausted=False, get=k)
    assert "PARTIAL" in ks.live_only_note(6_000, exhausted=False, errors=2, get=k)


def test_live_only_note_with_an_unreadable_cutoff_never_claims_completeness():
    note = ks.live_only_note(6_000, exhausted=False, get=CutoffSeries(_err(500)))
    assert "cannot be confirmed" in note and "dropped nothing" not in note


def test_result_gaps_counts_events_and_markets():
    evs = [{"markets": [{"result": "yes"}, {"result": ""}]},
           {"markets": [{"result": "no"}]},
           {"markets": [{}, {"result": "void"}]}]
    assert ks.result_gaps(evs) == (2, 3)
