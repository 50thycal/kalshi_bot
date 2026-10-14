"""Tests for scripts/mmsell_lookalike_probe.py: the cell entry, family pooling, the gate and an
end-to-end run against a mocked public API."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


lp = _load("mmsell_lookalike_probe")


def test_family_pools_cities_by_longest_prefix():
    assert lp.family_of("KXHIGHNY") == "KXHIGH"
    assert lp.family_of("KXHIGHTDAL") == "KXHIGHT"
    assert lp.family_of("KXRAINWKND") == "KXRAINWKND"
    assert lp.family_of("KXSOMETHING") == "KXSOMETHING"


def test_first_entry_is_the_earliest_in_cell_hour_and_never_looks_ahead():
    close = 100_000
    cs = [
        {"end_period_ts": close - 12 * 3600, "yes_bid": {"close": 5}, "yes_ask": {"close": 7}},
        {"end_period_ts": close - 3600, "yes_bid": {"close": 40}, "yes_ask": {"close": 45}},
    ]
    assert lp.first_entry(cs, close, 5, 10, 7) == (12, 7)
    # Above the maxyes ceiling: no entry.
    cs[0]["yes_ask"] = {"close": 8}
    assert lp.first_entry(cs, close, 5, 10, 7) is None


def test_quotes_read_every_candle_shape_including_archive_dollar_strings():
    close = 100_000
    shapes = [
        {"yes_bid": {"close": 5}, "yes_ask": {"close": 7}},
        {"yes_bid": {"close": "0.05"}, "yes_ask": {"close": "0.07"}},
        {"yes_bid": {"close_dollars": "0.05"}, "yes_ask": {"close_dollars": "0.07"}},
        {"yes_bid_dollars": {"close": "0.05"}, "yes_ask_dollars": {"close": "0.07"}},
        {"yes_bid_close": 5, "yes_ask_close": 7},
        {"yes_bid_close_dollars": "0.05", "yes_ask_close_dollars": "0.07"},
    ]
    for shape in shapes:
        cs = [{"end_period_ts": close - 12 * 3600, **shape}]
        assert lp.first_entry(cs, close, 5, 10, 7) == (12, 7.0), shape


def test_gate_promotes_kills_and_holds():
    def rows(n, days, pnl, loss_every=0):
        out = []
        for i in range(n):
            loss = loss_every and i % loss_every == 0
            out.append((6.0, bool(loss), -94.0 if loss else pnl, f"d{i % days}", f"2026-0{1 + i % 3}"))
        return out

    strong = lp.summarize(rows(150, 40, 6.0, loss_every=50))
    assert lp.verdict(strong) == "PROMOTE"
    losing = lp.summarize(rows(120, 40, 6.0, loss_every=8))
    assert lp.verdict(losing) == "KILL"
    thin = lp.summarize(rows(20, 10, 6.0))
    assert lp.verdict(thin) == "HOLD"
    assert lp.verdict({"n": 0}) == "NO-DATA"


def test_end_to_end_with_mocked_api(monkeypatch, capsys):
    close_iso = "2026-01-15T23:00:00Z"
    close = lp._ts(close_iso)

    def market(series, city, result):
        return {"ticker": f"{series}-26JAN15-{city}", "close_time": close_iso, "result": result,
                "open_time": "2026-01-13T00:00:00Z"}

    listings = {
        "KXHIGHNY": [market("KXHIGHNY", "T40", "no")],
        "KXHIGHCHI": [market("KXHIGHCHI", "T30", "yes")],
        "KXRAIN": [market("KXRAIN", "NYC", "no")],
    }
    in_cell = [{"end_period_ts": close - h * 3600, "yes_bid": {"close": 5}, "yes_ask": {"close": 7}}
               for h in (1, 6)]

    def fake_get(url: str):
        if "/candlesticks" in url:
            return {"candlesticks": in_cell} if "/series/" in url else {"candlesticks": []}
        if "/historical/markets?" in url:
            return {"markets": [], "cursor": ""}
        if "/series?" in url:
            return {"series": [{"ticker": "KXHIGHNY"}, {"ticker": "KXHIGHCHI"},
                               {"ticker": "KXOTHER"}], "cursor": ""}
        for series, mkts in listings.items():
            if f"series_ticker={series}&" in url:
                return {"markets": mkts, "cursor": ""}
        return {"markets": [], "cursor": ""}

    monkeypatch.setattr(lp.xl, "_get", fake_get)
    assert lp.main(["--prefixes", "KXHIGH"]) == 0
    out = capsys.readouterr().out
    assert "KXOTHER" not in out
    family = [ln for ln in out.splitlines() if ln.strip().startswith("KXHIGH ")][0]
    # NY wins +7, CHI loses -93 on the same day: two entries, one family-day.
    assert " 2     1 " in family and "-43.00" in family
    assert "NOT REPRODUCED" in out  # one control market cannot meet the control bar


def test_probe_is_allowlisted_on_the_ops_channel():
    ops_runner = _load("ops_runner")
    assert "mmsell_lookalike_probe" in ops_runner.ALLOWED_SCRIPTS
