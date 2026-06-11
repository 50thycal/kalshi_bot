"""Tests for scripts/weather_entry_study.py — calibration, bands, obs entry, limits."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


st = _load("weather_entry_study")

T0 = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def _ladder_rows(captured_at, htc, asks):
    ranges = [(None, 73.0), (74.0, 75.0), (76.0, 77.0), (78.0, None)]
    return [
        {
            "event_ticker": "KXHIGHNY-26JUN10",
            "market_ticker": f"KXHIGHNY-26JUN10-B{i}",
            "low_f": ranges[i][0],
            "high_f": ranges[i][1],
            "yes_bid_cents": asks[i] - 2.0,
            "yes_ask_cents": asks[i],
            "mid_cents": asks[i] - 1.0,
            "hours_to_close": htc,
            "captured_at": captured_at,
        }
        for i in range(4)
    ]


SETTLE = {
    "event_ticker": "KXHIGHNY-26JUN10",
    "city": "NYC",
    "target_date": "2026-06-10",
    "kind": "high",
    "winning_ticker": "KXHIGHNY-26JUN10-B1",  # the 74-75 bucket
}


def test_band_of():
    assert st.band_of(1.0) == (1, 20)
    assert st.band_of(55.0) == (40, 60)
    assert st.band_of(99.0) == (80, 99)
    assert st.band_of(0.5) is None


def test_calibration_counts_wins_per_band():
    asks = [10.0, 36.0, 46.0, 12.0]  # winner (B1) priced in the 20-40 band (mid 35)
    ladders = {"KXHIGHNY-26JUN10": _ladder_rows(T0, 19.8, asks)}
    cal = st.calibration([SETTLE], ladders)
    cell = cal[(20.0, (20, 40))]
    assert cell[0] == 1 and cell[2] == 1  # one bucket in band, and it won
    # The 40-60 band bucket (mid 45) lost.
    cell = cal[(20.0, (40, 60))]
    assert cell[0] == 1 and cell[2] == 0
    # h14/h8 windows have no snapshot -> only h20 keys exist.
    assert all(w == 20.0 for (w, _b) in cal)


def test_obs_confirmed_entry_buys_running_max_bucket():
    # Snapshot at 21:00Z (17:00 NYC). Running max 74.6 -> bucket B1 (74-75), ask 36.
    snap_t = T0 + timedelta(hours=9)
    ladders = {"KXHIGHNY-26JUN10": _ladder_rows(snap_t, 6.0, [10.0, 36.0, 46.0, 12.0])}
    obs = {("NYC", "2026-06-10"): [
        {"captured_at": snap_t - timedelta(minutes=10), "running_max_f": 74.6,
         "running_min_f": 60.0},
    ]}
    n, wins, pnl, asks = st.obs_confirmed_entries(
        [SETTLE], ladders, obs, kind="high", min_local_hour=15, ask_cap=85.0,
    )
    assert (n, wins) == (1, 1)
    assert pnl == 100.0 - 36.0 - st.fee_cents(36.0)
    # Before the local cutoff hour the same obs is ignored.
    n2, *_ = st.obs_confirmed_entries(
        [SETTLE], ladders, obs, kind="high", min_local_hour=18, ask_cap=85.0,
    )
    assert n2 == 0
    # Ask cap below the bucket's ask -> no entry.
    n3, *_ = st.obs_confirmed_entries(
        [SETTLE], ladders, obs, kind="high", min_local_hour=15, ask_cap=30.0,
    )
    assert n3 == 0


def test_obs_confirmed_can_lose_when_overtaken():
    # Running max says B1, but B2 actually won (temperature kept climbing).
    snap_t = T0 + timedelta(hours=9)
    ladders = {"KXHIGHNY-26JUN10": _ladder_rows(snap_t, 6.0, [10.0, 36.0, 46.0, 12.0])}
    obs = {("NYC", "2026-06-10"): [
        {"captured_at": snap_t - timedelta(minutes=10), "running_max_f": 74.6,
         "running_min_f": 60.0},
    ]}
    settle = dict(SETTLE, winning_ticker="KXHIGHNY-26JUN10-B2")
    n, wins, pnl, _ = st.obs_confirmed_entries(
        [settle], ladders, obs, kind="high", min_local_hour=15, ask_cap=85.0,
    )
    assert (n, wins) == (1, 0)
    assert pnl == -36.0 - st.fee_cents(36.0)


def test_limit_entry_fills_only_through_limit():
    trade = {
        "market_ticker": "KXHIGHNY-26JUN10-B1",
        "strategy": "weather_fav_h20",
        "assumed_price": 40,
        "resolved_value": 100,
        "pnl": 0.58,
        "created_at": T0,
    }
    # Ask path after entry dips to 38 -> a 40-2=38 limit fills; a 40-5=35 limit doesn't.
    asks = {"KXHIGHNY-26JUN10-B1": [
        (T0 + timedelta(minutes=15), 41.0),
        (T0 + timedelta(minutes=30), 38.0),
        (T0 + timedelta(minutes=45), 44.0),
    ]}
    n, fills, pnl = st.limit_entry([trade], asks, k=2.0)
    assert (n, fills) == (1, 1)
    assert pnl == 100.0 - 38.0 - st.fee_cents(38.0)
    n, fills, pnl = st.limit_entry([trade], asks, k=5.0)
    assert (n, fills, pnl) == (1, 0, 0.0)


def test_ops_runner_allowlists_entry_study(tmp_path, monkeypatch):
    import json

    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = _load("ops_runner")
    assert "weather_entry_study" in runner.ALLOWED_SCRIPTS
    req.write_text(json.dumps({"type": "script", "name": "weather_entry_study", "args": []}))
    assert runner.main() == 1  # dispatches; exits 1 cleanly with no DB URL
