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


def test_a_window_restricts_entry_to_its_own_hours():
    """`--timing` replays a POLICY that only buys inside the window: an early in-cell quote does
    not count for the late window, and a late one is found even when an early one exists."""
    close = 100_000
    cs = [
        {"end_period_ts": close - 36 * 3600, "yes_bid": {"close": 5}, "yes_ask": {"close": 7}},
        {"end_period_ts": close - 3 * 3600, "yes_bid": {"close": 4}, "yes_ask": {"close": 6}},
    ]
    assert lp.first_entry(cs, close, 4, 10, 7) == (36, 7)               # the shipped rule
    assert lp.first_entry(cs, close, 4, 10, 7, (24, 48)) == (36, 7)     # early
    assert lp.first_entry(cs, close, 4, 10, 7, (6, 24)) is None          # mid: nothing there
    assert lp.first_entry(cs, close, 4, 10, 7, (0, 6)) == (3, 6)        # late


def test_windows_are_disjoint_and_cover_the_replay_hours():
    covered = [h for h in lp.HOURS for _n, lo, hi in lp.WINDOWS if lo < h <= hi]
    assert sorted(covered) == sorted(lp.HOURS)


def test_day_paired_difference_and_reading():
    base = [lp._row(6.0, False, f"d{i}") for i in range(40)]
    better = [lp._row(6.0, False, f"d{i}") for i in range(40)]
    better = [(r[0], r[1], r[2] + 1.5 + (0.1 if i % 2 else -0.1), r[3], r[4])
              for i, r in enumerate(better)]
    d = lp.day_paired(better, base)
    assert d["days"] == 40 and abs(d["diff"] - 1.5) < 1e-9
    assert lp.timing_reading(d) == "BETTER"
    worse = [(r[0], r[1], r[2] - 3.0 + (0.1 if i % 2 else -0.1), r[3], r[4])
             for i, r in enumerate(base)]
    assert lp.timing_reading(lp.day_paired(worse, base)) == "WORSE"
    assert lp.timing_reading(lp.day_paired(base[:10], base)) == "THIN"
    # Only shared days are compared: a policy that bets on other days says nothing.
    assert lp.day_paired([lp._row(6.0, False, "x")], base)["days"] == 0


def test_feasibility_needs_a_quote_an_hour_before_close():
    close = 100_000
    late_only = [{"end_period_ts": close - 1800, "yes_bid": {"close": 5}, "yes_ask": {"close": 7}}]
    early = [{"end_period_ts": close - 5 * 3600, "yes_bid": {"close": 5},
              "yes_ask": {"close": 7}}]
    assert not lp.has_quote_by(late_only, close)
    assert lp.has_quote_by(early, close)


def test_timing_run_prints_windows_and_lifetime(monkeypatch, capsys):
    close_iso = "2026-01-15T23:00:00Z"
    close = lp._ts(close_iso)
    mkts = [{"ticker": "KXRAIN-26JAN15-NYC", "close_time": close_iso, "result": "no",
             "open_time": "2026-01-13T00:00:00Z"}]
    cs = [{"end_period_ts": close - h * 3600, "yes_bid": {"close": 5}, "yes_ask": {"close": 7}}
          for h in (3, 36)]

    def fake_get(url: str):
        if "/candlesticks" in url:
            return {"candlesticks": cs} if "/series/" in url else {"candlesticks": []}
        if "series_ticker=KXRAIN&" in url and "/historical/" not in url:
            return {"markets": mkts, "cursor": ""}
        return {"markets": [], "cursor": ""}

    monkeypatch.setattr(lp.xl, "_get", fake_get)
    assert lp.main(["--series", "KXRAIN", "--timing"]) == 0
    out = capsys.readouterr().out
    assert "D. LIFETIME" in out and "KXRAIN" in out and "FEASIBLE" in out
    assert "E. TIMING" in out
    for name in ("first", "early", "mid", "late"):
        assert f"KXRAIN {name}" in out


def test_minute_offsets_find_a_last_hour_entry_the_hourly_replay_cannot():
    """`--minutes`: a market listed 40 min before close has no hourly entry but a 30-minute one."""
    close = 100_000
    cs = [{"end_period_ts": close - m * 60, "yes_bid": {"close": 5}, "yes_ask": {"close": 7}}
          for m in (40, 30, 10)]
    assert lp.first_entry(cs, close, 5, 10, 7) is None
    hit = lp.first_entry(cs, close, 5, 10, 7, offsets=(10 / 60, 30 / 60), tol_s=600)
    assert hit is not None and abs(hit[0] - 0.5) < 1e-9 and hit[1] == 7
    # The tolerance is minutes-scale: a quote older than tol_s before the target never counts.
    stale = [{"end_period_ts": close - 50 * 60, "yes_bid": {"close": 5},
              "yes_ask": {"close": 7}}]
    assert lp.first_entry(stale, close, 5, 10, 7, offsets=(30 / 60,), tol_s=600) is None


def test_min_cents_raises_only_the_per_contract_bar():
    rows = [(6.0, i % 50 == 0, -94.0 if i % 50 == 0 else 6.0, f"d{i % 40}", f"2026-0{1 + i % 3}")
            for i in range(150)]
    s = lp.summarize(rows)
    assert s["cents"] > 2.0
    assert lp.verdict(s) == "PROMOTE"
    assert lp.verdict(s, min_cents=s["cents"] + 0.5) == "HOLD"


def test_minutes_mode_end_to_end(monkeypatch, capsys):
    close_iso = "2026-08-15T18:00:00Z"
    close = lp._ts(close_iso)
    mkts = [{"ticker": "KXTEMPNYCH-26AUG1514-T80", "close_time": close_iso, "result": "no",
             "open_time": "2026-08-15T17:20:00Z"}]
    seen = []

    def fake_get(url: str):
        if "/candlesticks" in url:
            seen.append(url)
            cs = [{"end_period_ts": close - m * 60, "yes_bid": {"close": 5},
                   "yes_ask": {"close": 7}} for m in (35, 15)]
            return {"candlesticks": cs} if "/series/" in url else {"candlesticks": []}
        if "series_ticker=KXTEMPNYCH&" in url and "/historical/" not in url:
            return {"markets": mkts, "cursor": ""}
        return {"markets": [], "cursor": ""}

    monkeypatch.setattr(lp.xl, "_get", fake_get)
    assert lp.main(["--series", "KXTEMPNYCH", "--no-control", "--minutes", "30,15,5",
                    "--min-cents", "4"]) == 0
    out = capsys.readouterr().out
    assert "LAST-HOUR MODE" in out and "period_interval=1" in seen[0]
    fam = [ln for ln in out.splitlines() if ln.strip().startswith("KXTEMP ")][0]
    assert " 1 " in fam and "+7.00" in fam


def test_probe_is_allowlisted_on_the_ops_channel():
    ops_runner = _load("ops_runner")
    assert "mmsell_lookalike_probe" in ops_runner.ALLOWED_SCRIPTS
