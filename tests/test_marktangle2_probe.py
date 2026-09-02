"""MARKTANGLE-2's instrument, on sequences whose answers are known by construction.

Every failure mode pinned here has an ancestor: the small-n mirage, the lookahead
bug (MLBWX's fake +5.5c), pooling observations that are not one sequence, a
control that is not the same book pointed the other way, and — the one this
experiment exists to forbid — a position size that remembers the last loss.
"""

from __future__ import annotations

import contextlib
import io
import math
import pathlib
import random
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import marktangle2_package as pkg  # noqa: E402
import marktangle2_probe as m2  # noqa: E402

T0 = 1_700_000_000


def _mk(series, suffix, i, result, stype="greater", fs=None, day=86400):
    ev = f"{series}-{i:05d}"
    return {"ticker": f"{ev}-{suffix}", "event": ev, "close": T0 + i * day, "result": result,
            "vol": 100.0, "strike_type": stype, "floor_strike": fs, "cap_strike": None}


def _rows(series, suffix, text, **kw):
    return [_mk(series, suffix, i, "yes" if c == "Y" else "no", **kw) for i, c in enumerate(text)]


# ===========================================================================
# The frozen constants are the pre-registration
# ===========================================================================


def test_the_bars_are_the_registered_ones():
    assert m2.EDGE_BAR_C == 3.0
    assert m2.TRAIN_FRAC == 0.70
    assert m2.FLOOR_TRAIN_POINTS == 500
    assert m2.FLOOR_HOLDOUT_TRADES == 100
    assert m2.MIRROR_DELTA_C == 3.0
    assert m2.MIN_FAMILY_N == 40
    assert m2.DECISION_MIN == 60


def test_the_probe_is_allowlisted_on_the_ops_channel():
    import ops_runner
    assert "marktangle2_probe" in ops_runner.ALLOWED_SCRIPTS


# ===========================================================================
# Structural classification — market structure, never behaviour
# ===========================================================================


@pytest.mark.parametrize("series,expected", [
    ("KXBTCD", ("B", "CRYPTO_DAILY:BTC")),
    ("KXETHD", ("B", "CRYPTO_DAILY:ETH")),
    ("KXBTC", None),                       # the intraday series is not a daily threshold
    ("KXHIGHNY", ("A", "WEATHER_HIGH")),
    ("KXLOWCHI", ("A", "WEATHER_LOW")),
    ("KXUSLTOTAL", ("A", "SOCCER_TOTAL")),
    ("KXNBATOTAL", ("A", "BASKETBALL_TOTAL")),
    ("KXLIGAMXSPREAD", ("A", "SOCCER_SPREAD")),
    ("KXZZZTOTAL", None),                  # unknown league: reported, never pooled
    ("KXMVECROSSCATEGORY", None),
    ("KXSCOTUSCASE", None),
])
def test_series_classifier_is_structural(series, expected):
    assert m2.classify_series(series) == expected


def test_weather_class_completes_with_bucket_vs_threshold():
    bucket = m2.classify_family("KXHIGHNY|B82.5", _rows("KXHIGHNY", "B82.5", "YN" * 5, stype="between", fs=82.5))
    thresh = m2.classify_family("KXHIGHNY|T90", _rows("KXHIGHNY", "T90", "YN" * 5, stype="greater", fs=90))
    assert bucket["cls"] == "WEATHER_HIGH_BUCKET" and not bucket["threshold"]
    assert thresh["cls"] == "WEATHER_HIGH_THRESHOLD" and thresh["threshold"]


def test_crypto_bucket_markets_are_not_track_b_and_strike_falls_back_to_the_suffix():
    assert m2.classify_family("KXBTCD|B60000", _rows("KXBTCD", "B60000", "YN" * 5, stype="between")) is None
    fam = m2.classify_family("KXBTCD|T59999.99", _rows("KXBTCD", "T59999.99", "YN" * 5, stype="", fs=None))
    assert fam["track"] == "B" and fam["strike"] == 59999.99 and fam["yes_is_above"]
    less = m2.classify_family("KXBTCD|T59999.99", _rows("KXBTCD", "T59999.99", "YN" * 5, stype="less", fs=None))
    assert less["yes_is_above"] is False


# ===========================================================================
# Families and sequences
# ===========================================================================


def test_same_close_ties_are_dropped_and_the_floor_is_enforced():
    rows = _rows("KXUSLTOTAL", "3", "Y" * 45)
    rows.append(dict(rows[10], event="KXUSLTOTAL-00010B", ticker="KXUSLTOTAL-00010B-3"))  # same close as row 10
    fams, funnel = m2.build_families(rows + _rows("KXUSLTOTAL", "2", "YN" * 10))
    assert set(fams) == {"KXUSLTOTAL|3"}
    assert funnel["tie_rows_dropped"] == 1 and funnel["below_floor"] == 1


def test_prediction_points_carry_the_streak_and_never_the_answer():
    rows = _rows("KXUSLTOTAL", "3", "YYNYYY")
    fam = {"track": "A", "cls": "SOCCER_TOTAL", "strike": 3.0, "yes_is_above": True, "threshold": True}
    pts = m2.prediction_points("KXUSLTOTAL|3", rows, fam, None)
    assert [(p["prev"], p["k"]) for p in pts] == [
        ("yes", 1), ("yes", 2), ("no", 1), ("yes", 1), ("yes", 2)]
    assert [p["result"] for p in pts] == ["yes", "no", "yes", "yes", "yes"]
    assert all(p["decision"] == p["close"] - 3600 for p in pts)


def test_z_dir_is_positive_when_spot_favours_continuation_in_both_strike_types():
    h0 = (T0 // 3600) * 3600
    hourly = {t: 66000.0 for t in range(h0 - 86400 * 40, h0 + 86400 * 40, 3600)}
    daily = {}
    px = 60000.0
    for d in range(-40, 40):
        px *= math.exp(0.01 * (1 if d % 2 else -1))
        daily[((T0 + d * 86400) // 86400) * 86400] = px
    spot = m2.SpotSeries(hourly, daily)
    rows = _rows("KXBTCD", "T64999.99", "YYNN", stype="greater", fs=64999.99)
    fam = m2.classify_family("KXBTCD|T64999.99", rows)
    pts = m2.prediction_points("KXBTCD|T64999.99", rows, fam, spot)
    # spot 66000 is ABOVE the strike: continuation for a YES state, reversal for NO
    assert pts[0]["prev"] == "yes" and pts[0]["z_dir"] > 0
    assert pts[2]["prev"] == "no" and pts[2]["z_dir"] < 0
    rows_less = _rows("KXBTCD", "T64999.99", "YYNN", stype="less", fs=None)
    rows_less = [dict(r, cap_strike=64999.99) for r in rows_less]
    fam_less = m2.classify_family("KXBTCD|T64999.99", rows_less)
    pts_less = m2.prediction_points("KXBTCD|T64999.99", rows_less, fam_less, spot)
    assert pts_less[0]["prev"] == "yes" and pts_less[0]["z_dir"] < 0


def test_spot_uses_only_a_completed_hourly_candle_and_vol_only_prior_days():
    hourly = {}
    decision = T0 + 12 * 3600 + 1800          # 12:30 UTC
    hourly[(decision // 3600) * 3600] = 999.0  # the hour in progress — must not be used
    hourly[(decision // 3600) * 3600 - 3600] = 100.0
    daily = {((T0 + d * 86400) // 86400) * 86400: 100.0 * math.exp(0.02 * d) for d in range(-30, 3)}
    spot = m2.SpotSeries(hourly, daily)
    assert spot.spot_at(decision) == 100.0
    # constant log return -> zero variance -> no vol estimate, by construction
    assert spot.sigma_daily(decision) is None
    daily2 = dict(daily)
    daily2[((T0 - 5 * 86400) // 86400) * 86400] = 50.0
    assert m2.SpotSeries(hourly, daily2).sigma_daily(decision) > 0


def test_split_is_chronological_and_boundary_ties_go_to_holdout():
    pts = [{"decision": T0 + (i // 2) * 3600, "family": f"f{i % 2}", "ticker": f"t{i}"} for i in range(20)]
    train, hold, cut = m2.split_by_time(pts)
    assert all(p["decision"] < cut for p in train) and all(p["decision"] >= cut for p in hold)
    assert max(p["decision"] for p in train) < min(p["decision"] for p in hold)
    assert 12 <= len(train) <= 14 and all(p["split"] == "train" for p in train)


# ===========================================================================
# Models
# ===========================================================================


def _pts(prev_results, family="f", cls="SOCCER_TOTAL"):
    out = []
    for i, (prev, k, res) in enumerate(prev_results):
        out.append({"family": family, "cls": cls, "track": "A", "series": "S", "ticker": f"t{i}",
                    "event": f"e{i}", "close": T0 + i * 3600, "decision": T0 + i * 3600 - 3600,
                    "prev": prev, "k": k, "result": res, "strike": None, "spot": None,
                    "sigma": None, "z_dir": None, "split": "train"})
    return out


def test_a0_shrinks_the_family_rate_toward_the_class_rate():
    train = _pts([("yes", 1, "yes")] * 30 + [("yes", 1, "no")] * 10, family="a") + \
        _pts([("yes", 1, "no")] * 40, family="b")
    pred = m2.fit_a0(train)
    cls_rate = 30 / 80
    assert pred({"family": "a"}) == pytest.approx((30 + m2.SHRINK_M * cls_rate) / (40 + m2.SHRINK_M))
    assert pred({"family": "unseen"}) == pytest.approx(cls_rate)


def test_a1_conditions_on_the_previous_outcome_per_direction():
    train = _pts([("yes", 1, "yes")] * 40 + [("no", 1, "no")] * 40)
    pred = m2.fit_a1(train)
    assert pred({"family": "f", "prev": "yes"}) > 0.9
    assert pred({"family": "f", "prev": "no"}) < 0.1


def test_streak_table_keeps_direction_separate_and_pools_beyond_the_cap():
    train = _pts([("yes", 7, "no")] * 20 + [("yes", 9, "no")] * 20 + [("no", 7, "no")] * 20)
    pred = m2.fit_streak_table(train, m2.k_bucket_a)
    assert ("yes", m2.MAX_K_A) in pred.table and ("yes", 7) not in pred.table
    assert pred({"prev": "yes", "k": 12}) < 0.5 and pred({"prev": "no", "k": 12}) < 0.5


def test_logistic_recovers_a_planted_coefficient():
    rng = random.Random(3)
    rows = []
    for _ in range(4000):
        x = rng.uniform(-2, 2)
        p = 1 / (1 + math.exp(-(0.5 + 1.5 * x)))
        rows.append(([1.0, x], -1, 1 if rng.random() < p else 0))
    beta, se = m2.fit_logistic(rows, 2, 0, 0.0, 0.0)
    assert beta[0] == pytest.approx(0.5, abs=0.15) and beta[1] == pytest.approx(1.5, abs=0.2)
    assert 0 < se[1] < 0.2


def _serial_class(rng, p_rev_by_k, families=6, n=250):
    pts = []
    for f in range(families):
        prev, run = "yes", 1
        for i in range(n):
            r = ("no" if prev == "yes" else "yes") if rng.random() < p_rev_by_k(run) else prev
            pts.append({"family": f"S|{f}", "cls": "SOCCER_TOTAL", "track": "A", "series": "S",
                        "ticker": f"S-{f}-{i}", "event": f"S-{f}-{i}", "close": T0 + i * 86400 + f,
                        "decision": T0 + i * 86400 + f - 3600, "prev": prev, "k": run, "result": r,
                        "strike": None, "spot": None, "sigma": None, "z_dir": None, "split": "train"})
            run = run + 1 if r == prev else 1
            prev = r
    return pts


def test_a3_interaction_sign_follows_the_planted_dependence():
    rng = random.Random(5)
    reverting = m2.fit_a3(_serial_class(rng, lambda k: min(0.9, 0.4 + 0.1 * k)))
    persisting = m2.fit_a3(_serial_class(rng, lambda k: max(0.1, 0.6 - 0.1 * k)))
    assert reverting.beta[3] < 0, "reversal rising with k must be a negative prev_dir x ln(k)"
    assert persisting.beta[3] > 0
    assert len(reverting.family_effects) == 6


def test_b3_abstains_without_spot_and_uses_distance_when_present():
    rng = random.Random(9)
    pts = []
    for i in range(400):
        z = rng.uniform(-3, 3)
        prev = "yes" if i % 2 else "no"
        cont = rng.random() < 1 / (1 + math.exp(-2 * z))
        pts.append({"family": "B|1", "cls": "CRYPTO_DAILY:BTC", "track": "B", "series": "B",
                    "ticker": f"b{i}", "event": f"b{i}", "close": T0 + i * 86400,
                    "decision": T0 + i * 86400 - 3600, "prev": prev, "k": 1 + i % 4,
                    "result": prev if cont else ("no" if prev == "yes" else "yes"),
                    "strike": 1.0, "spot": 1.0, "sigma": 0.02, "z_dir": z, "split": "train"})
    pred = m2.fit_b3(pts)
    assert pred.beta[3] > 1.0
    assert pred({"prev": "yes", "k": 1, "z_dir": None}) is None
    assert pred({"prev": "yes", "k": 1, "z_dir": 2.5}) > 0.9
    assert pred({"prev": "no", "k": 1, "z_dir": 2.5}) < 0.1
    assert m2.fit_b3(pts[:10]) is None


# ===========================================================================
# Execution and economics
# ===========================================================================


def test_side_economics_prices_the_taker_side_with_worst_case_fee_and_slippage():
    q = {"bid": 40.0, "ask": 44.0}
    e = m2.side_economics(0.70, q)
    assert e["side"] == "yes" and e["price"] == 44.0 and e["fee"] == 2  # ceil(7*.44*.56)=2
    assert e["edge"] == pytest.approx(70 - 44 - 2 - m2.SLIPPAGE_C)
    e = m2.side_economics(0.20, q)
    assert e["side"] == "no" and e["price"] == 60.0


def test_liquidity_screen_refuses_wide_or_crossed_quotes():
    assert m2.side_economics(0.7, {"bid": 30.0, "ask": 45.0}) is None
    assert m2.side_economics(0.7, {"bid": 50.0, "ask": 45.0}) is None
    assert m2.side_economics(0.7, {"bid": 0.0, "ask": 3.0}) is None


def test_the_mirror_is_the_other_side_of_the_same_book():
    q = {"bid": 40.0, "ask": 44.0}
    e = m2.side_economics(0.70, q)
    o = m2.opposite(e, q)
    assert o["side"] == "no" and o["price"] == 60.0 and o["p_win"] == pytest.approx(0.30)


def test_settlement_pnl():
    leg = {"side": "yes", "price": 44.0, "fee": 2}
    assert m2.settle(leg, "yes") == (56.0, 56.0 - 2 - m2.SLIPPAGE_C)
    assert m2.settle(leg, "no") == (-44.0, -44.0 - 2 - m2.SLIPPAGE_C)


def test_kelly_units_depend_on_edge_only_and_are_capped():
    a = m2.kelly_units(10.0, 50.0, 0.65)
    assert a == m2.kelly_units(10.0, 50.0, 0.65), "same inputs, same size — no memory"
    assert 1 <= a <= m2.KELLY_CAP_UNITS
    assert m2.kelly_units(60.0, 20.0, 0.95) == m2.KELLY_CAP_UNITS
    assert m2.kelly_units(0.5, 50.0, 0.51) == 1


def _sim_setup():
    rng = random.Random(1)
    pts = _serial_class(rng, lambda k: 0.5, families=2, n=60)
    for p in pts:
        p["split"] = "holdout"
    quotes = {p["ticker"]: {"bid": 48.0, "ask": 52.0} for p in pts}
    return pts, quotes


def test_simulation_sizes_every_trade_at_one_unit_and_the_mirror_matches_it():
    pts, quotes = _sim_setup()
    pred = lambda p: 0.80 if p["prev"] == "yes" else 0.20  # noqa: E731
    trades, stats = m2.simulate(pts, pred, "A1", quotes, mirror=False)
    mirror, _ = m2.simulate(pts, pred, "A1", quotes, mirror=True)
    assert trades and all(t["size"] == 1 for t in trades)
    assert [t["market_ticker"] for t in trades] == [t["market_ticker"] for t in mirror]
    assert all(a["side"] != b["side"] for a, b in zip(trades, mirror, strict=True))
    assert all(t["arm"] == "A1_mirror" for t in mirror)
    assert stats["n_pred"] == len(pts) and stats["n_priced"] == len(pts)


def test_no_trade_under_the_edge_bar_and_size_never_reads_the_previous_outcome():
    pts, quotes = _sim_setup()
    trades, _ = m2.simulate(pts, lambda p: 0.53, "A0", quotes, mirror=False)
    assert trades == [], "53% vs 52c ask is inside fee + slippage + bar"
    pred = lambda p: 0.80 if p["prev"] == "yes" else 0.20  # noqa: E731
    trades, _ = m2.simulate(pts, pred, "A1", quotes, mirror=False)
    losses_then = [b["size"] for a, b in zip(trades, trades[1:], strict=False) if a["net_pnl"] < 0]
    wins_then = [b["size"] for a, b in zip(trades, trades[1:], strict=False) if a["net_pnl"] > 0]
    assert set(losses_then) == set(wins_then) == {1}


def test_economics_drawdown_streak_and_robustness():
    trades = [{"net_pnl": v, "gross_pnl": v, "fee": 0, "slippage": 0, "exec_price": 50.0,
               "edge": 3.0, "side": "yes", "timestamp": f"2026-01-{i + 1:02d}", "family": fam}
              for i, (v, fam) in enumerate([(10, "a"), (-5, "b"), (-5, "b"), (-5, "b"), (30, "a"), (1, "c")])]
    e = m2.economics(trades)
    assert e["net"] == 26 and e["mdd"] == 15 and e["worst_streak"] == 3 and e["n"] == 6
    r = m2.robustness(trades)
    assert r["top_family"] == "a" and r["net_ex_top_family"] == -14
    assert r["top_trades_removed"] == 1 and r["net_ex_top_trades"] == -4


def _res(n, net, brier, mirror_ev=-5.0, ex_fam=1.0, ex_top=1.0):
    trades = [{"net_pnl": net / n}] * n if n else []
    return {"stats": {"brier": brier}, "econ": {"n": n, "net": net, "ev": (net / n) if n else None},
            "mirror": {"econ": {"n": n, "ev": mirror_ev}},
            "robust": {"net_ex_top_family": ex_fam, "net_ex_top_trades": ex_top}, "trades": trades}


def test_grade_holds_below_floors_passes_on_every_clause_and_fails_on_the_mirror():
    base = _res(120, 0.0, 0.25)
    v, why = m2.grade("A3", _res(50, 500.0, 0.20), base, _res(50, 500.0, 0.2)["mirror"], 1000, 0.9)
    assert v == "HOLD" and any("holdout trades" in r for r in why)
    v, _ = m2.grade("A3", _res(150, 500.0, 0.20), base, _res(150, 500.0, 0.2)["mirror"], 400, 0.9)
    assert v == "HOLD"
    v, _ = m2.grade("A3", _res(150, 500.0, 0.20), base, _res(150, 500.0, 0.2)["mirror"], 1000, 0.3)
    assert v == "HOLD"
    good = _res(150, 600.0, 0.20)
    assert m2.grade("A3", good, base, good["mirror"], 1000, 0.9)[0] == "PASS"
    tied = _res(150, 600.0, 0.20, mirror_ev=3.0)   # +4c vs +3c: not a 3c separation
    v, why = m2.grade("A3", tied, base, tied["mirror"], 1000, 0.9)
    assert v == "FAIL" and any("mirror" in r for r in why if r.startswith("failed"))
    concentrated = _res(150, 600.0, 0.20, ex_fam=-1.0)
    assert m2.grade("A3", concentrated, base, concentrated["mirror"], 1000, 0.9)[0] == "FAIL"


# ===========================================================================
# The whole run: deterministic, complete, splittable
# ===========================================================================


def _synthetic_universe():
    rng = random.Random(7)
    markets = {}
    for suffix in ("2", "3"):
        prev, run = "yes", 0
        rows = []
        for i in range(200):
            r = ("no" if prev == "yes" else "yes") if rng.random() < 0.45 + 0.05 * min(run, 5) else prev
            run = run + 1 if r == prev else 1
            prev = r
            rows.append(_mk("KXUSLTOTAL", suffix, i, r))
        markets.setdefault("KXUSLTOTAL", []).extend(rows)
    price, path = 60000.0, []
    for _ in range(200):
        price *= math.exp(rng.gauss(0, 0.02))
        path.append(price)
    for strike in (55000, 60000):
        markets.setdefault("KXBTCD", []).extend(
            _mk("KXBTCD", f"T{strike - 0.01}", i, "yes" if sp > strike else "no", "greater", strike - 0.01)
            for i, sp in enumerate(path))
    markets["KXSCOTUSCASE"] = _rows("KXSCOTUSCASE", "1", "YN" * 25)
    hourly, daily = {}, {}
    for i, sp in enumerate(path):
        day = T0 + i * 86400
        daily[(day // 86400) * 86400] = sp
        for h in range(24):
            hourly[(day // 3600) * 3600 - h * 3600] = sp
    spot = m2.SpotSeries(hourly, daily)
    qrng = random.Random(3)
    quotes = {}
    for rows in markets.values():
        for r in rows:
            mid = qrng.uniform(20, 80)
            quotes[r["ticker"]] = {"bid": math.floor(mid) - 2.0, "ask": math.floor(mid) + 2.0, "ts": r["close"] - 3600}
    return markets, spot, quotes


def _run_once():
    markets, spot, quotes = _synthetic_universe()
    calls = []

    def fetch(series, ticker, close):
        calls.append(ticker)
        return quotes.get(ticker)
    out = m2.run(markets, lambda a, s, e: spot, fetch, 10_000, "abc123", {"max_fetch": 10_000})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fps = m2.emit_package(out)
    return out, fps, buf.getvalue(), calls


def test_two_identical_runs_give_identical_fingerprints_and_a_complete_package():
    out, fps, text, calls = _run_once()
    _out2, fps2, _text2, _calls2 = _run_once()
    assert fps == fps2
    names = ["MARKTANGLE_2_DATA_REPORT.md", "MARKTANGLE_2_TRACK_A.md", "MARKTANGLE_2_TRACK_B.md",
             "MARKTANGLE_2_SUMMARY.md", "MARKTANGLE_2_TRADES.csv"]
    for n in names:
        assert f"### BEGIN {n}" in text and f"### END {n}" in text
    assert set(out["results"]) == {"SOCCER_TOTAL", "CRYPTO_DAILY:BTC"}
    assert out["unclassified"] == {"KXSCOTUSCASE": 1}
    assert "TRACK A VERDICT" in text and "TRACK B VERDICT" in text
    # holdout quotes are fetched before any train quote
    holdout = {p["ticker"] for r in out["results"].values() for p in r["hold"]}
    first_train = next(i for i, t in enumerate(calls) if t not in holdout)
    assert all(t in holdout for t in calls[:first_train])


def test_trades_csv_carries_every_required_column_and_splits_back_into_files(tmp_path):
    _out, fps, text, _ = _run_once()
    sections, printed = pkg.split_sections(text)
    assert printed == fps
    csv = sections["MARKTANGLE_2_TRADES.csv"]
    header = csv.splitlines()[0].split(",")
    assert header == m2.CSV_COLUMNS
    for col in ("market_ticker", "series", "family", "timestamp", "track", "arm", "prior_outcome",
                "streak_direction", "streak_length", "strike", "spot", "z_dir", "model_prob_yes",
                "market_yes_bid", "exec_price", "edge", "fee", "side", "size", "resolution",
                "gross_pnl", "net_pnl", "split"):
        assert col in header
    rows = [line.split(",") for line in csv.splitlines()[1:]]
    assert rows and all(r[header.index("size")] == "1" for r in rows)
    assert {r[header.index("split")] for r in rows} <= {"train", "holdout"}
    written = pkg.write_package(sections, tmp_path)
    assert {p.name for p in written} == set(sections)
    assert (tmp_path / "MARKTANGLE_2_TRADES.csv").read_text() == csv
    result = tmp_path / "result.txt"
    result.write_text(text)
    assert pkg.main([str(result), str(tmp_path / "pkg")]) == 0


def test_holdout_is_graded_on_train_fitted_models_only():
    """The holdout economics of a class must not change when the holdout outcomes
    are shuffled in the TRAIN-fitting sense: refitting on train with a different
    holdout gives the same train-fitted predictor."""
    markets, spot, quotes = _synthetic_universe()
    out = m2.run({"KXUSLTOTAL": markets["KXUSLTOTAL"]}, lambda a, s, e: None,
                 lambda s, t, c: quotes.get(t), 10_000, "x", {})
    r = out["results"]["SOCCER_TOTAL"]
    pred = r["arms"]["A3"]["model"]
    flipped = [dict(p, result="no" if p["result"] == "yes" else "yes") for p in r["hold"]]
    assert [pred(p) for p in flipped] == [pred(p) for p in r["hold"]]
