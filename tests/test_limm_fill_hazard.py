"""scripts/limm_fill_hazard.py — pins the pre-registered fill replay, markout, ratio estimate and
labels (docs/LIMM_FILL_HAZARD.md) without the DB."""

from __future__ import annotations

import datetime as dt
import importlib.util
import math
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_fill_hazard", _SCRIPTS / "limm_fill_hazard.py")
fh = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fh)  # type: ignore[union-attr]

UTC = dt.timezone.utc
H0 = dt.datetime(2026, 11, 2, 10, tzinfo=UTC)


def test_constants_match_the_preregistration():
    assert fh.SINCE == "2026-10-30T16:00:00+00:00"
    assert fh.UNTIL == "2026-11-13T15:00:00+00:00"
    assert (fh.MARK_HOURS, fh.UNMARKED_FRACTION) == (6, 0.5)
    assert (fh.MIN_MARKET_DAYS, fh.MIN_EVENTS, fh.NET_BAR_USD) == (20, 5, 0.50)
    assert fh.DECIDING == "conservative"


def test_yes_bid_is_hit_only_by_yes_sellers_at_or_through_our_price():
    # our YES bid at 5c, 100 contracts ahead
    t = [(H0, 5, 60, "no"), (H0 + dt.timedelta(minutes=5), 5, 60, "no")]
    got = fh.simulate_hour("yes", 5, 200, 100, t)
    assert got["conservative"] == 20          # 120 traded, 100 ahead
    assert got["optimistic"] == 200
    assert fh.simulate_hour("yes", 5, 200, 100, [(H0, 5, 500, "yes")]) == \
        {"conservative": 0.0, "optimistic": 0.0}          # a yes BUYER does not hit a yes bid
    assert fh.simulate_hour("yes", 5, 200, 100, [(H0, 6, 500, "no")])["optimistic"] == 0


def test_no_bid_lives_on_the_yes_scale():
    # our NO bid at 4c rests at yes 96; hit by a taker buying yes at >= 96
    got = fh.simulate_hour("no", 4, 100, 0, [(H0, 96, 30, "yes")])
    assert got["conservative"] == 30
    assert fh.simulate_hour("no", 4, 100, 0, [(H0, 95, 30, "yes")])["optimistic"] == 0


def test_a_print_through_our_level_clears_the_queue():
    got = fh.simulate_hour("yes", 5, 100, 1000, [(H0, 4, 40, "no")])
    assert got["conservative"] == 40


def test_markout_uses_the_bid_six_hours_after_the_resting_hour_with_fallback():
    marks = {H0 + dt.timedelta(hours=7): (2, 97)}
    assert fh.mark_bid(marks, "yes", H0) == 2
    assert fh.mark_bid({H0 + dt.timedelta(hours=9): (3, 96)}, "yes", H0) == 3
    assert fh.mark_bid({H0 + dt.timedelta(hours=10): (3, 96)}, "yes", H0) is None
    assert fh.mark_bid({H0 + dt.timedelta(hours=7): (None, 97)}, "yes", H0) == 0
    assert fh.markout_usd(200, 5, 2) == 6.0          # 200 x 3c
    assert fh.markout_usd(200, 5, 7) == -4.0         # the bid rose: a gain
    assert fh.markout_usd(200, 5, None) == 5.0       # unmarked: half price
    assert fh.markout_usd(0, 5, None) == 0.0


def test_a_fill_ends_the_market_window():
    hours = [{"hr": H0 + dt.timedelta(hours=i), "side": "yes", "price": 5, "qty": 200,
              "depth": 0, "reward": 0.10} for i in range(4)]
    trades = {H0 + dt.timedelta(hours=2): [(H0 + dt.timedelta(hours=2, minutes=1), 5, 50, "no")]}
    marks = {H0 + dt.timedelta(hours=8): (1, 98)}
    r = fh.run_market(hours, trades, marks, "conservative")
    assert r["fills"] == 1
    assert math.isclose(r["reward"], 0.20)            # hours 0 and 1, then the fill in hour 2
    assert math.isclose(r["loss"], 50 * 4 / 100)
    assert r["filled_at"] == H0 + dt.timedelta(hours=2)


def test_thin_hours_earn_no_reward_but_still_fill():
    hours = [{"hr": H0, "side": "yes", "price": 5, "qty": 200, "depth": 0, "reward": None}]
    trades = {H0 + dt.timedelta(hours=1): [(H0 + dt.timedelta(hours=1), 5, 10, "no")]}
    r = fh.run_market(hours, trades, {}, "conservative")
    assert r["reward"] == 0.0 and r["fills"] == 1 and r["unmarked"] == 1


def test_ratio_estimate_and_clustered_se():
    est = fh.ratio_cluster({"a": (10.0, 10), "b": (2.0, 10)})
    assert math.isclose(est["R"], 0.6)
    # residuals 10-6=4, 2-6=-4 -> sqrt(2/1 * 32) / 20
    assert math.isclose(est["se"], math.sqrt(64) / 20)
    assert math.isnan(fh.ratio_cluster({"a": (1.0, 2)})["se"])
    assert fh.ratio_cluster({}) is None


def test_labels():
    lead = {"R": 0.80, "se": 0.10}
    assert fh.label(30, 6, 1.0, lead) == "NET-LEAD"
    assert fh.label(19, 6, 1.0, lead) == "THIN"
    assert fh.label(30, 4, 1.0, lead) == "THIN"
    assert fh.label(30, 6, 1.0, {"R": 0.80, "se": 0.50}) == "NO-LEAD"   # LCB below 0
    assert fh.label(30, 6, 0.90, {"R": -0.10, "se": 0.05}) == "FILL-TRAP"
    assert fh.label(30, 6, 0.30, {"R": -0.10, "se": 0.05}) == "NO-LEAD"


def test_trade_time_prefers_exchange_ms():
    assert fh.trade_time(1_793_000_000_000, None) == dt.datetime.fromtimestamp(1_793_000_000, UTC)
    assert fh.trade_time(None, H0) == H0


def test_probe_is_ops_allowlisted():
    import ops_runner
    assert "limm_fill_hazard" in ops_runner.ALLOWED_SCRIPTS


def test_main_end_to_end_on_synthetic_rows(monkeypatch, capsys):
    """Four series (three at the floor) x 6 events x 4 market-days: one pays clean, one is filled into, one thin."""
    import types

    start, end = dt.datetime(2026, 10, 1, tzinfo=UTC), dt.datetime(2026, 12, 31, tzinfo=UTC)
    close = dt.datetime(2026, 12, 31, tzinfo=UTC)
    snaps, trades = [], []
    t0 = dt.datetime(2026, 11, 1, 0, tzinfo=UTC)
    for series, pool, fill in (("KXGOOD", 4000.0, False), ("KXTRAPMENTION", 4000.0, True),
                               ("KXSMALL", 20.0, False)):
        for e in range(6):
            tk = f"{series}-E{e}-M"
            for h in range(4 * 24):
                hr = t0 + dt.timedelta(hours=h)
                snaps.append((tk, hr, 5, 94, 100, 100, 1000.0, 1000.0, True, True, series,
                              f"{series}-E{e}", "liquidity", pool, start, end, close))
            if fill:
                at = t0 + dt.timedelta(hours=30, minutes=5)
                trades.append((tk, int(at.timestamp() * 1000), at, 5, 1000, "no"))
                for h in range(31, 4 * 24):   # bid collapses after the fill
                    hr = t0 + dt.timedelta(hours=h)
                    snaps.append((tk, hr, 1, 98, 100, 100, 1000.0, 1000.0, True, True, series,
                                  f"{series}-E{e}", "liquidity", pool, start, end, close))
    snaps.append(("KXTHIN-E0-M", t0, 5, 94, 100, 100, 1000.0, 1000.0, True, True, "KXTHIN",
                  "KXTHIN-E0", "liquidity", 4000.0, start, end, close))

    class Cur:
        def __init__(self):
            self.q = None

        def execute(self, sql, params):
            self.q = "trade" if "incentive_trade_events" in sql else "snap"

        def fetchall(self):
            if self.q == "trade":
                return trades
            # last row per (market, hour) wins, as DISTINCT ON ... ORDER BY at DESC would
            seen = {}
            for r in snaps:
                seen[(r[0], r[1])] = r
            return list(seen.values())

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def cursor(self):
            return Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda *a, **k: Conn()))
    monkeypatch.setenv("DATABASE_URL_RO", "postgresql://u:p@h/db")
    assert fh.main(["--since", "2026-11-01T00:00:00+00:00",
                    "--until", "2026-11-05T00:00:00+00:00"]) == 0
    out = capsys.readouterr().out
    line = {ln.split()[0]: ln for ln in out.splitlines() if ln.strip().startswith("KX")}
    assert line["KXGOOD"].rstrip().endswith("NET-LEAD")
    assert line["KXTRAPMENTION"].rstrip().endswith("FILL-TRAP")
    assert line["KXTHIN"].rstrip().endswith("THIN*") or line["KXTHIN"].rstrip().endswith("THIN")
    assert line["KXSMALL"].rstrip().endswith("NO-LEAD")
    assert "V0 instrument: OK — 3 series at the floor" in out
    assert "V1 better markets: NOMINATE KXGOOD" in out
    assert "V2 markets to avoid: NOMINATE removal KXTRAPMENTION" in out
