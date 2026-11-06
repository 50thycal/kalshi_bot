"""scripts/kalshi_settled.py — the live-then-historical settlement read.

Acceptance (docs/handoffs/HANDOFF-kalshi-historical-cutoff.md, check 1): a live 404 falls
through to the historical tier; a market neither tier has is counted as `not found`, never
dropped; any other error is raised, not swallowed. No network: a fake getter plays Kalshi."""

from __future__ import annotations

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
