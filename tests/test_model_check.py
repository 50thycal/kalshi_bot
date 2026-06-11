"""Tests for scripts/weather_model_check.py — the ensemble-vs-market grading math."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kalshi_bot.paper.engine import kalshi_fee

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass field resolution needs the module registered
    spec.loader.exec_module(mod)
    return mod


mc = _load("weather_model_check")

T0 = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


# --- probability math -----------------------------------------------------------


def test_member_bucket_prob_raw_mirrors_rounding():
    # sigma=0: indicator with the same rounding semantics as forecast_in_bucket
    assert mc.member_bucket_prob(74.4, 74, 75, 0) == 1.0
    assert mc.member_bucket_prob(75.4, 74, 75, 0) == 1.0
    assert mc.member_bucket_prob(76.0, 74, 75, 0) == 0.0
    assert mc.member_bucket_prob(60.0, None, 73, 0) == 1.0  # "73 or below"
    assert mc.member_bucket_prob(80.0, 78, None, 0) == 1.0  # "78 or above"


def test_member_bucket_prob_kernel():
    # A member centered in the bucket keeps most mass there; tails leak symmetrically.
    p_in = mc.member_bucket_prob(74.5, 74, 75, 1.0)
    assert 0.6 < p_in < 0.9
    # Full ladder (open-ended ends) sums to ~1 for any member.
    ladder = [(None, 73.0), (74.0, 75.0), (76.0, 77.0), (78.0, None)]
    total = sum(mc.member_bucket_prob(75.2, lo, hi, 2.0) for lo, hi in ladder)
    assert abs(total - 1.0) < 1e-9


def test_model_blend_weighs_models_equally():
    ladder = [(None, 73.0), (74.0, 75.0), (76.0, 77.0), (78.0, None)]
    members = {"gfs": [74.0, 74.0, 74.0], "ecmwf": [78.0, 78.0]}  # 3 vs 2 members
    probs = mc.model_bucket_probs(members, ladder, sigma=0)
    # Equal model weight: 0.5/0.5 despite member-count imbalance.
    assert abs(probs[1] - 0.5) < 1e-3
    assert abs(probs[3] - 0.5) < 1e-3
    assert abs(sum(probs) - 1.0) < 1e-9
    assert mc.model_bucket_probs({}, ladder, sigma=0) is None


def test_normalize_mids():
    probs = mc.normalize_mids([45.0, 35.0, 10.0, 10.0])
    assert abs(probs[0] - 0.45) < 1e-6 and abs(sum(probs) - 1.0) < 1e-9
    probs = mc.normalize_mids([None, 50.0, 50.0])  # missing mid -> floored, not crash
    assert probs[0] < 0.01 and abs(sum(probs) - 1.0) < 1e-9
    assert mc.normalize_mids([5.0, None, None]) is None  # degenerate ladder


def test_brier_logloss_and_fee():
    assert abs(mc.brier([1.0, 0.0], 0) - 0.0) < 1e-9
    assert abs(mc.brier([0.0, 1.0], 0) - 2.0) < 1e-9  # worst case
    assert abs(mc.log_loss([0.5, 0.5], 0) - math.log(2)) < 1e-9
    assert mc.log_loss([0.0, 1.0], 0) == -math.log(mc.PROB_FLOOR)  # floored, finite
    for price in (1, 25, 37, 50, 56, 75, 99):
        assert mc.fee_cents(price) == kalshi_fee(price, 1, True) * 100


# --- snapshot / ensemble selection ------------------------------------------------


def _ladder_rows(captured_at, htc, mids):
    subs = ["73° or below", "74° to 75°", "76° to 77°", "78° or above"]
    ranges = [(None, 73.0), (74.0, 75.0), (76.0, 77.0), (78.0, None)]
    return [
        {
            "event_ticker": "KXHIGHNY-26JUN10",
            "market_ticker": f"KXHIGHNY-26JUN10-B{i}",
            "city": "NYC",
            "kind": "high",
            "subtitle": subs[i],
            "low_f": ranges[i][0],
            "high_f": ranges[i][1],
            "yes_bid_cents": mids[i] - 1.0,
            "yes_ask_cents": mids[i] + 1.0,
            "mid_cents": mids[i],
            "hours_to_close": htc,
            "captured_at": captured_at,
        }
        for i in range(4)
    ]


def test_cluster_and_window_pick():
    rows = (
        _ladder_rows(T0, 21.0, [10, 35, 45, 10])
        + _ladder_rows(T0 + timedelta(hours=1.2), 19.8, [10, 35, 45, 10])
        + _ladder_rows(T0 + timedelta(hours=2.0), 19.0, [10, 35, 45, 10])
    )
    cycles = mc.cluster_cycles(rows)
    assert len(cycles) == 3 and all(len(c.buckets) == 4 for c in cycles)
    # First crossing of the 20h window is the 19.8h snapshot, not the later 19.0h one.
    assert mc.pick_cycle_for_window(cycles, 20.0).hours_to_close == 19.8
    # No snapshot anywhere near 8h -> window missing (19.0 is 11h early).
    assert mc.pick_cycle_for_window(cycles, 8.0) is None


def test_pick_ensembles_no_lookahead():
    rows = [
        {"model": "gfs", "members_json": [74.0], "captured_at": T0 - timedelta(hours=1)},
        {"model": "gfs", "members_json": [75.0], "captured_at": T0 - timedelta(minutes=5)},
        {"model": "gfs", "members_json": [99.0], "captured_at": T0 + timedelta(minutes=5)},
        {"model": "ecmwf", "members_json": [74.5], "captured_at": T0 - timedelta(hours=7)},
    ]
    picked = mc.pick_ensembles_at(rows, T0)
    assert picked == {"gfs": [75.0]}  # latest at-or-before T0; future + stale excluded


# --- EV + end-to-end grading -------------------------------------------------------


def test_ev_trades_sides_and_threshold():
    buckets = [
        mc.Bucket("A", "74° to 75°", 74.0, 75.0, 36.0, 37.0, 36.5),
        mc.Bucket("B", "76° to 77°", 76.0, 77.0, 44.0, 45.0, 44.5),
    ]
    probs = [0.70, 0.20]
    trades = mc.ev_trades(probs, buckets, winner_idx=0, min_edge_cents=5.0)
    sides = {(t[0], t[1]) for t in trades}
    assert ("yes", "74° to 75°") in sides  # underpriced winner -> buy YES
    assert ("no", "76° to 77°") in sides  # overpriced loser -> buy NO
    yes = next(t for t in trades if t[0] == "yes")
    assert yes[3] == 100.0 - (37.0 + mc.fee_cents(37.0))  # won at the ask, net of fee
    no = next(t for t in trades if t[0] == "no")
    assert no[3] == 100.0 - (56.0 + mc.fee_cents(56.0))  # bucket lost -> NO pays out
    # Tight prices -> nothing clears a huge threshold.
    assert mc.ev_trades(probs, buckets, 0, min_edge_cents=80.0) == []


def test_grade_settled_end_to_end():
    settlements = [{
        "event_ticker": "KXHIGHNY-26JUN10",
        "city": "NYC",
        "target_date": "2026-06-10",
        "kind": "high",
        "winning_ticker": "KXHIGHNY-26JUN10-B1",  # the 74-75 bucket
        "winning_subtitle": "74° to 75°",
        "actual_low_f": 74.0,
        "actual_high_f": 75.0,
    }]
    mids = [8.0, 35.0, 45.0, 12.0]  # market favorite is 76-77 (wrong)
    cycle_times = {20: (T0, 19.8), 14: (T0 + timedelta(hours=6), 13.8), 8: (T0 + timedelta(hours=12), 7.9)}
    ladder_rows, ens_rows = [], []
    for _, (ts, htc) in cycle_times.items():
        ladder_rows += _ladder_rows(ts, htc, mids)
        for model, members in (("gfs", [74.2, 74.8, 75.1, 74.5]), ("ecmwf", [74.9, 75.3, 74.1])):
            ens_rows.append({"model": model, "members_json": members,
                             "captured_at": ts - timedelta(minutes=10)})
    ladders = {"KXHIGHNY-26JUN10": ladder_rows}
    ensembles = {("NYC", "2026-06-10", "high"): ens_rows}

    graded, ready = mc.grade_settled(
        settlements, ladders, ensembles, windows=[20.0, 14.0, 8.0],
        sigma=1.5, min_edge_cents=5.0,
    )
    assert len(graded) == 3
    assert ready.gradeable == 1 and ready.with_ladder == 1 and ready.with_ensemble == 1
    for g in graded:
        assert g.ens_hit and not g.mkt_hit  # members cluster on the winner; market favored 76-77
        assert g.ens_brier < g.mkt_brier
        assert set(g.per_model_brier) == {"gfs", "ecmwf"}
        assert any(t[0] == "yes" and t[1] == "74° to 75°" and t[3] > 0 for t in g.trades)

    # Same data through the sensitivity sweep: raw counts (sigma=0) still beat the market.
    sens = mc.sigma_sensitivity(settlements, ladders, ensembles, [20.0, 14.0, 8.0], [0.0, 2.0], 5.0)
    assert [s[1] for s in sens] == [3, 3]
    assert all(s[2] < s[3] for s in sens)


def test_grade_settled_skips_uncovered_events():
    settlements = [{
        "event_ticker": "KXHIGHCHI-26JUN09", "city": "CHI", "target_date": "2026-06-09",
        "kind": "high", "winning_ticker": "X", "winning_subtitle": "80° to 81°",
        "actual_low_f": 80.0, "actual_high_f": 81.0,
    }]
    graded, ready = mc.grade_settled(settlements, {}, {}, [20.0], 1.5, 5.0)
    assert graded == [] and ready.gradeable == 0
    assert ready.skipped.get("no ladder snapshots") == 1


def test_event_target_date_parse():
    assert mc.event_target_date("KXLOWTNYC-26JUN08") == "2026-06-08"
    assert mc.event_target_date("KXHIGHNY-26DEC31-B74.5") == "2026-12-31"
    assert mc.event_target_date("NONSENSE") is None


# --- ops runner dispatch -------------------------------------------------------------


def test_ops_runner_script_allowlist(tmp_path, monkeypatch):
    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = _load("ops_runner")

    req.write_text(json.dumps({"type": "script", "name": "evil_script"}))
    assert runner.main() == 1  # not allowlisted

    req.write_text(json.dumps({"type": "noop"}))
    assert runner.main() == 0

    # Allowlisted script dispatches for real; with no DB URL it exits 1 cleanly.
    req.write_text(json.dumps({"type": "script", "name": "weather_model_check", "args": []}))
    assert runner.main() == 1
