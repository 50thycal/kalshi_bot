"""scripts/mmsell_supply_forecast.fetch_settled_deep — reading past Kalshi's historical cutoff
without overstating coverage (docs/handoffs/HANDOFF-kalshi-historical-cutoff.md).

The live tier ends at the cutoff; the historical tier holds the rest, in an order this repo has
not verified. So the archive's rows are always kept, but it EXTENDS the reported reach only when
it was read to exhaustion. No network: a fake getter plays Kalshi."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
import urllib.error

import pytest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("mmsell_supply_forecast",
                                               _SCRIPTS / "mmsell_supply_forecast.py")
sf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sf)  # type: ignore[union-attr]

DAY = 86_400
NOW = 1_800_000_000


def _iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _m(tk: str, close: int) -> dict:
    return {"ticker": tk, "close_time": _iso(close), "result": "no", "volume": 10}


class Fake:
    def __init__(self, live, hist, page_size=2, fail_hist=False):
        self.tiers = {"/markets": live, "/historical/markets": hist}
        self.page_size, self.fail_hist = page_size, fail_hist
        self.calls: list[str] = []

    def __call__(self, path, params):
        self.calls.append(path)
        if path == "/historical/cutoff":
            return {}
        if path == "/historical/markets" and self.fail_hist:
            raise urllib.error.HTTPError("https://x", 503, "err", None, None)  # type: ignore[arg-type]
        rows = self.tiers[path]
        i = int(params.get("cursor") or 0)
        nxt = i + self.page_size
        return {"markets": [dict(r) for r in rows[i:nxt]],
                "cursor": str(nxt) if nxt < len(rows) else ""}


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(sf, "SCAN", sf.ks.ScanCount())
    monkeypatch.setattr(sf.ks.time, "sleep", lambda s: None)
    monkeypatch.setattr(sf.ks, "_CUTOFF_CACHE", {})

    orig = sf.ks.iter_series_markets

    def go(fake, max_pages=10, since=NOW - 100 * DAY):
        monkeypatch.setattr(sf.ks, "iter_series_markets",
                            lambda series, **kw: orig(series, get=fake, **kw))
        return sf.fetch_settled_deep("S", max_pages, 0, since)
    return go


def test_live_reaching_since_never_reads_the_archive(run):
    live = [_m("S-1", NOW - 10 * DAY), _m("S-2", NOW - 50 * DAY), _m("S-3", NOW - 120 * DAY)]
    fake = Fake(live, [_m("S-9", NOW - 200 * DAY)])
    rows, reach = run(fake)
    assert [r["ticker"] for r in rows] == ["S-1", "S-2"]
    assert reach == NOW - 120 * DAY
    assert "/historical/markets" not in fake.calls


def test_an_exhausted_archive_extends_reach_past_the_wall(run):
    live = [_m("S-1", NOW - 10 * DAY), _m("S-2", NOW - 60 * DAY)]            # wall at 60d
    hist = [_m("S-3", NOW - 70 * DAY), _m("S-4", NOW - 90 * DAY), _m("S-5", NOW - 150 * DAY)]
    rows, reach = run(Fake(live, hist))
    assert {r["ticker"]: r["source"] for r in rows} == {
        "S-1": "live", "S-2": "live", "S-3": "historical", "S-4": "historical"}
    assert reach == NOW - 150 * DAY
    assert sf.SCAN.counts == {"live": 2, "historical": 3}


def test_a_page_capped_archive_keeps_its_rows_but_not_the_reach(run):
    live = [_m("S-1", NOW - 10 * DAY), _m("S-2", NOW - 60 * DAY)]
    hist = [_m(f"S-h{i}", NOW - (61 + i) * DAY) for i in range(10)]
    rows, reach = run(Fake(live, hist), max_pages=2)
    assert sum(r["source"] == "historical" for r in rows) == 4
    assert reach == NOW - 60 * DAY, "an unordered partial archive read is not a date range"
    assert sf.SCAN.truncated["historical"] is True


def test_a_failed_archive_read_is_counted_and_never_extends_reach(run):
    live = [_m("S-1", NOW - 10 * DAY), _m("S-2", NOW - 60 * DAY)]
    rows, reach = run(Fake(live, [_m("S-3", NOW - 70 * DAY)], fail_hist=True))
    assert [r["ticker"] for r in rows] == ["S-1", "S-2"]
    assert reach == NOW - 60 * DAY
    assert sf.SCAN.errors["historical"] == 1
