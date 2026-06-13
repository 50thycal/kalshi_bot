"""Tests for scripts/weather_backfill_edges.py — pure edge math."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


be = _load("weather_backfill_edges")


def _b(ticker, low, high, bid, ask):
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    return be.Bkt(ticker, low, high, bid, ask, mid)


def test_bucket_mid_and_value_in_bucket():
    assert be.bucket_mid_f(74, 75) == 74.5
    assert be.bucket_mid_f(None, 73) == 73        # "X or below" -> edge
    assert be.bucket_mid_f(95, None) == 95        # "X or above" -> edge
    assert be.value_in_bucket(74.4, 74, 75) and not be.value_in_bucket(76.0, 74, 75)


def test_pearson_basic():
    assert abs(be.pearson([1, 2, 3, 4], [2, 4, 6, 8]) - 1.0) < 1e-9
    assert abs(be.pearson([1, 2, 3, 4], [4, 3, 2, 1]) + 1.0) < 1e-9
    assert be.pearson([1, 1, 1], [1, 2, 3]) is None  # zero variance
    assert be.pearson([1], [1]) is None              # too few


def test_implied_mean_and_ladder_sums():
    buckets = [_b("A", None, 73, 4, 6), _b("B", 74, 75, 54, 56), _b("C", 76, 77, 19, 21)]
    # weighted mean ~ dominated by B (mid 55) at 74.5
    im = be.implied_mean_f(buckets)
    assert 73.5 < im < 75.0
    sm, sb, sa = be.ladder_sums(buckets)
    assert sm == 5 + 55 + 20 and sb == 4 + 54 + 19 and sa == 6 + 56 + 21
    assert be.ladder_sums([_b("A", 1, 2, 5, 7)]) is None  # too sparse


def test_overround_detects_sellable_credit():
    # A degenerate ladder whose BIDS sum to >100 (would be a free sell-the-ladder arb).
    over = [_b("A", None, 73, 40, 42), _b("B", 74, 75, 40, 42), _b("C", 76, 77, 40, 42)]
    # htc 10h -> falls in the 6-12 bin
    ev = be.Event("NYC", "high", "2026-06-10", "B", 74.5, [be.Cycle(10.0, over)])
    bins, sellable = be.overround([ev], fees=True)
    assert len(bins[(6, 12)]) == 1 and bins[(6, 12)][0] == 123  # sum_mid (3 x mid 41)
    n, cred, net = sellable[(6, 12)]
    # sum_bid=120, minus 100 payout, minus 3 leg fees -> still a credit
    assert n == 1 and cred == 1 and net > 0


def test_persistence_strategy_and_correlation():
    # Two-day series, same (city, kind). Yesterday landed in bucket B (74.5); today the
    # open ladder is identical and B wins again -> the "yesterday bucket" entry wins.
    ladder = [_b("A", None, 73, 4, 6), _b("B", 74, 75, 50, 52), _b("C", 76, 77, 30, 32)]
    d1 = be.Event("NYC", "high", "2026-06-09", "B", 74.5, [be.Cycle(24.0, ladder)])
    d2 = be.Event("NYC", "high", "2026-06-10", "B", 74.5, [be.Cycle(24.0, ladder)])
    res = be.persistence([d1, d2], fees=True)
    # persistence strategy bought B (contains yesterday's 74.5) at ask 52, won
    pn, pw, ppnl = res["strat"]["pers"]
    assert pn == 1 and pw == 1
    assert ppnl == 100.0 - 52.0 - be.fee_cents(52.0)
    # favorite is also B (highest mid) -> same here
    assert res["strat"]["fav"][0] == 1


def test_nearest_cycle_within_tolerance():
    ev = be.Event("NYC", "high", "2026-06-10", "B", 74.5,
                  [be.Cycle(24.0, []), be.Cycle(11.0, []), be.Cycle(2.0, [])])
    assert be._nearest_cycle(ev, 12.0, 3.0).htc == 11.0   # closest within tol
    assert be._nearest_cycle(ev, 12.0, 0.5) is None        # nothing close enough
    assert be._nearest_cycle(be.Event("X", "high", None, "B", None, []), 12.0, 3.0) is None


def test_longshot_sells_cheap_loser_and_buys_winning_favorite():
    # At htc~12 one cheap longshot (mid 4) that LOSES and one heavy favorite (mid 92)
    # that WINS. Selling the longshot (buy NO at 100-bid) and buying the favorite YES
    # should both book a positive harvest.
    cheap = _b("LONG", 80, 81, 3, 5)        # mid 4  -> longshot band (3,5)
    favor = _b("FAV", 74, 75, 91, 93)       # mid 92 -> favorite band (90,95)
    ev = be.Event("NYC", "high", "2026-06-10", "FAV", 74.5, [be.Cycle(12.0, [cheap, favor])])
    res = be.longshot([ev], fees=True, target_htc=12.0, tol=3.0)
    assert res["n_ev"] == 1

    # longshot (3,5): n=1, the YES bucket lost, so NO harvest = (100 - no_cost) - fee.
    n, yes_wins, no_pnl = res["ls"][(3, 5)]
    no_cost = 100 - 3
    assert n == 1 and yes_wins == 0
    assert no_pnl == 100.0 - no_cost - be.fee_cents(no_cost)

    # favorite (90,95): n=1, the YES bucket won, so YES harvest = (100 - ask) - fee.
    n, yes_wins, yes_pnl = res["fv"][(90, 95)]
    assert n == 1 and yes_wins == 1
    assert yes_pnl == 100.0 - 93.0 - be.fee_cents(93.0)


def test_longshot_skips_events_without_a_near_cycle():
    far = _b("X", 74, 75, 3, 5)
    ev = be.Event("NYC", "high", "2026-06-10", "X", 74.5, [be.Cycle(40.0, [far])])
    res = be.longshot([ev], fees=True, target_htc=12.0, tol=3.0)
    assert res["n_ev"] == 0
    assert all(cell[0] == 0 for cell in res["ls"].values())
    assert all(cell[0] == 0 for cell in res["fv"].values())


def test_convergence_momentum_and_favorite_horizons():
    # 3 buckets across h24/h12/h6. A rises early->mid (+20) and keeps rising; A wins.
    # So at h12 the biggest riser (A) should win and the biggest faller (B) should lose,
    # and the move autocorrelation should be positive (moves trend).
    def cyc(htc, a, b, c):
        return be.Cycle(htc, [_b("A", 74, 75, a - 1, a + 1),
                              _b("B", 76, 77, b - 1, b + 1),
                              _b("C", 72, 73, c - 1, c + 1)])
    ev = be.Event("NYC", "high", "2026-06-10", "A", 74.5,
                  [cyc(24.0, 30, 50, 20), cyc(12.0, 50, 40, 25), cyc(6.0, 65, 30, 22)])
    res = be.convergence([ev], fees=True, early=24.0, mid=12.0, late=6.0, tol=4.0)
    assert res["n_ev"] == 1
    assert res["move_corr"] is not None and res["move_corr"] > 0   # moves trend (momentum)

    # riser at h12 is A (delta +20), which wins; bought YES at ask 51.
    n, wins, pnl = res["mom"]["riser"]
    assert n == 1 and wins == 1
    assert pnl == 100.0 - 51.0 - be.fee_cents(51.0)
    # faller at h12 is B (delta -10), which loses.
    assert res["mom"]["faller"] == [1, 0, -(40 + 1) - be.fee_cents(41.0)]
    # favorite at h12 is A (mid 50) and wins.
    assert res["mom"]["fav"][1] == 1
    # favorite-by-horizon table is populated at all three horizons.
    assert all(res["fav_settle"][k][0] == 1 for k in ("early", "mid", "late"))


def test_convergence_skips_event_missing_early_or_mid():
    # Only a late cycle -> no early/mid pair, so n_ev stays 0 (favorite table may still fill).
    ev = be.Event("NYC", "high", "2026-06-10", "A", 74.5,
                  [be.Cycle(6.0, [_b("A", 74, 75, 49, 51)])])
    res = be.convergence([ev], fees=True, tol=4.0)
    assert res["n_ev"] == 0
    assert res["move_corr"] is None


def test_implied_dist_normalizes_and_weights():
    # mids 10/80/10 over temps 71/72.5/74 -> normalized [.1,.8,.1], mean 72.5, var 0.45.
    buckets = [_b("L", None, 71, 9, 11), _b("C", 72, 73, 79, 81), _b("H", 74, None, 9, 11)]
    mean, sd, pts, ps = be._implied_dist(buckets)
    assert abs(mean - 72.5) < 1e-9
    assert abs(sd - (0.45 ** 0.5)) < 1e-9
    assert abs(sum(ps) - 1.0) < 1e-9
    assert be._implied_dist([_b("A", 1, 2, 5, 7)]) is None  # too sparse


def test_distshape_z_pit_and_sigma_bands():
    # Same ladder; the CENTER bucket (72.5) wins -> outcome sits on the implied mean.
    buckets = [_b("L", None, 71, 9, 11), _b("C", 72, 73, 79, 81), _b("H", 74, None, 9, 11)]
    ev = be.Event("NYC", "high", "2026-06-10", "C", 72.5, [be.Cycle(12.0, buckets)])
    res = be.distshape([ev], fees=True, target_htc=12.0, tol=3.0)
    assert res["n_ev"] == 1
    assert abs(res["mean_z"]) < 1e-9          # actual == implied mean -> z 0
    assert res["sd_z"] is None                # one event -> no dispersion estimate

    # center bucket -> ~0 sigma band, wins; the two edge buckets -> >1.5 sigma, lose.
    n, wins, sump, yn, ypnl, nn, npnl = res["bands"][(0.0, 0.5)]
    assert n == 1 and wins == 1 and sump == 80.0
    assert yn == 1 and ypnl == 100.0 - 81.0 - be.fee_cents(81.0)
    n2, wins2, sump2, *_ = res["bands"][(1.5, 99.0)]
    assert n2 == 2 and wins2 == 0 and sump2 == 20.0


def test_distshape_underdispersed_when_outcomes_hit_tails():
    # Confident ladder (center 90c) but every outcome lands in an edge bucket ->
    # standardized outcomes spread wider than 1 (underdispersed: tails too cheap).
    def ladder():
        return [_b("L", None, 71, 4, 6), _b("C", 72, 73, 89, 91), _b("H", 74, None, 4, 6)]
    evs = [be.Event("NYC", "high", f"2026-06-1{i}", tk, val, [be.Cycle(12.0, ladder())])
           for i, (tk, val) in enumerate([("L", 71), ("H", 74), ("L", 71), ("H", 74)])]
    res = be.distshape(evs, fees=True, target_htc=12.0, tol=3.0)
    assert res["n_ev"] == 4
    assert res["sd_z"] is not None and res["sd_z"] > 1.0   # underdispersed
    assert res["pit_lo"] + res["pit_hi"] > 0.2             # outcomes pile in the tails


def _city_evt(city, kind, d, wmid):
    """A one-favorite event: dominant center bucket W at wmid, two cheap edge buckets."""
    return be.Event(city, kind, d, "W", wmid, [be.Cycle(12.0, [
        _b("W", wmid - 0.5, wmid + 0.5, 55, 57),
        _b("L", None, wmid - 2, 9, 11),
        _b("H", wmid + 2, None, 9, 11),
    ])])


def test_shift_date_and_city_lon_ordering():
    assert be._shift_date("2026-06-10", 1) == "2026-06-11"
    assert be._shift_date("2026-06-30", 1) == "2026-07-01"   # month rollover
    assert be._shift_date("not-a-date", 1) is None
    # west -> east by longitude
    assert be.CITY_LON["LAX"] < be.CITY_LON["DEN"] < be.CITY_LON["CHI"] < be.CITY_LON["NYC"]


def test_leadlag_detects_constructed_west_to_east_signal():
    # NYC anomaly[d] mirrors LAX anomaly[d-1] exactly -> perfect downwind lag-1 corr.
    lax = [_city_evt("LAX", "high", f"2026-06-{i:02d}", v)
           for i, v in [(1, 70), (2, 80), (3, 70), (4, 80)]]
    nyc = [_city_evt("NYC", "high", f"2026-06-{i:02d}", v)
           for i, v in [(2, 70), (3, 80), (4, 70), (5, 80)]]
    res = be.leadlag(lax + nyc, fees=True, htc=12.0, tol=4.0)
    assert res["dn"][1] is not None and res["dn"][1] > 0.9   # downwind lag-1 ~ +1
    assert res["dn_n"][1] == 4
    assert res["dn"][0] is not None and res["dn"][0] < 0     # same-day is anticorrelated here
    # the tradeable lead-shift actually placed trades at lag 1
    assert res["trade"][1.0][0] > 0 and res["fav"][0] > 0


def test_leadlag_ignores_unknown_cities():
    evs = [_city_evt("ZZZ", "high", "2026-06-01", 70), _city_evt("YYY", "high", "2026-06-02", 80)]
    res = be.leadlag(evs, fees=True)
    assert res["dn_n"][1] == 0 and res["trade"][1.0][0] == 0


def test_diurnal_couples_high_and_low_same_city_day():
    # Same city; high & low share the day's anomaly (warm airmass lifts both) -> coupling ~+1.
    hi = [_city_evt("AUS", "high", f"2026-06-{i:02d}", v)
          for i, v in [(1, 70), (2, 80), (3, 70), (4, 80)]]
    lo = [_city_evt("AUS", "low", f"2026-06-{i:02d}", v)
          for i, v in [(1, 50), (2, 60), (3, 50), (4, 60)]]
    res = be.diurnal(hi + lo, fees=True, htc=12.0, tol=4.0)
    assert res["n_pair"] == 4
    assert res["coupling"] is not None and res["coupling"] > 0.9
    # the tradeable shift placed trades on the high side, plus the favorite baseline
    assert res["trade_real"][1.0][0] > 0 and res["fav"][0] > 0


def test_diurnal_needs_both_high_and_low():
    # Only highs -> no (high,low) pair on any day.
    hi = [_city_evt("AUS", "high", "2026-06-01", 70), _city_evt("AUS", "high", "2026-06-02", 80)]
    res = be.diurnal(hi, fees=True)
    assert res["n_pair"] == 0 and res["fav"][0] == 0


def test_ops_runner_allowlists_backfill_edges(tmp_path, monkeypatch):
    import json

    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = _load("ops_runner")
    assert "weather_backfill_edges" in runner.ALLOWED_SCRIPTS
    req.write_text(json.dumps({"type": "script", "name": "weather_backfill_edges", "args": []}))
    assert runner.main() == 1
