"""scripts/limm_market_ranker.py — the reward measure, ranking rules and an end-to-end run on
synthetic rows, without the DB."""

from __future__ import annotations

import datetime as dt
import importlib.util
import math
import pathlib
import sys
import types

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_market_ranker", _SCRIPTS / "limm_market_ranker.py")
rk = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rk)  # type: ignore[union-attr]

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 8, 12, tzinfo=UTC)


def test_reward_is_per_contract():
    start, end = NOW - dt.timedelta(days=10), NOW + dt.timedelta(days=20)
    assert math.isclose(rk.pool_per_day(300.0, start, end), 10.0)
    assert rk.leg_qty(1) == 1000 and rk.leg_qty(2) == 500 and rk.leg_qty(45) == 22
    # same field, same pool: a 1c bid holds 1000 contracts, a 50c bid 20
    r1, s1 = rk.side_reward_day(10.0, 1, 9000)
    r50, s50 = rk.side_reward_day(10.0, 50, 9000)
    assert math.isclose(s1, 0.1) and math.isclose(r1, 0.5)
    assert r50 < r1 / 40
    assert rk.side_reward_day(10.0, None, 100) is None
    assert rk.side_reward_day(None, 5, 100) is None


def _row(tk, yb, nb, fy, fn, my=True, mn=True, close_days=30, pool=300.0):
    return {"ticker": tk, "yb": yb, "nb": nb, "fy": fy, "fn": fn, "my": my, "mn": mn,
            "series": tk.split("-")[0], "event": tk.rsplit("-", 1)[0], "pool": pool,
            "start": NOW - dt.timedelta(days=10), "end": NOW + dt.timedelta(days=20),
            "close": NOW + dt.timedelta(days=close_days), "title": None}


def test_rank_picks_better_side_and_demotes_flagged():
    rows = [_row("KXA-E1-M", 3, 96, 50000, 2000),       # yes 333 vs 50k; no 10 vs 2k
            _row("KXB-E1-M", 2, 97, 1000, 1000),        # yes 500 vs 1k -> best
            _row("KXC-E1-M", 2, 97, 10, 10, my=False),  # would be best, but not qualifying
            _row("KXD-E1-M", 2, 97, 10, 10, close_days=1)]  # inside the 48 h step-aside
    r = rk.rank_rows(rows, NOW)
    assert [x["ticker"] for x in r][:2] == ["KXB-E1-M", "KXA-E1-M"]
    assert r[0]["side"] == "yes" and r[1]["side"] == "yes"
    assert "not-qualifying" in r[2]["flags"] or "not-qualifying" in r[3]["flags"]
    assert any("inside-step-aside" in x["flags"] for x in r)
    s = rk.by_series(r)
    assert s[0]["series"] == "KXB" and s[0]["quotable"] == 1


def test_jumps_and_clock():
    h = [NOW + dt.timedelta(hours=i) for i in range(6)]
    assert rk.mid(5, 93) == 6.0 and rk.mid(None, 4) is None
    series = [(h[0], 10.0), (h[1], 11.0), (h[2], 25.0), (h[3], 24.0), (h[5], 50.0)]
    j = rk.find_jumps(series)
    assert j == [h[2], h[5]]                 # h3->h5 is a 2 h gap: compared; 24->50 is a jump
    j2 = rk.find_jumps(series + [(h[5] + dt.timedelta(hours=3), 90.0)])
    assert len(j2) == 2                      # h5->h8 is a 3 h gap: not compared
    fri = dt.datetime(2026, 11, 6, 15, tzinfo=UTC)      # a Friday
    clock = rk.reprice_clock([fri, fri + dt.timedelta(days=7, hours=1), fri + dt.timedelta(days=28),
                              dt.datetime(2026, 11, 3, 4, tzinfo=UTC)])
    assert clock["n"] == 4 and clock["hour"] in (15, 16) and clock["clear"]
    assert rk.WEEKDAYS[clock["weekday"]] == "Fri" and clock["weekday_share"] == 0.75
    assert rk.step_aside_text(clock).startswith("pull 1") and "mostly Fri" in rk.step_aside_text(clock)
    assert rk.step_aside_text(rk.reprice_clock([])) == "no repricing seen"
    scattered = rk.reprice_clock([NOW + dt.timedelta(hours=5 * i) for i in range(5)])
    assert not scattered["clear"] and rk.step_aside_text(scattered) == "no clear clock"


def test_turnover_and_clock_text():
    assert rk.turnover(500.0, 1000.0) == 0.5 and rk.turnover(None, 10) is None
    assert rk.turnover(5.0, 0) is None
    assert rk.clock_text({"n": 0}) == "-"
    assert rk.clock_text({"n": 1}) == "too few (1 jump)"
    assert rk.clock_text({"n": 2}) == "too few (2 jumps)"
    c = rk.reprice_clock([dt.datetime(2026, 11, 6, 15, tzinfo=UTC)] * 3)
    assert rk.clock_text(c) == "15Z±1 100%, Fri 100% of 3"


def test_risk_and_discount():
    assert rk.risk_grade(None) == "?" and rk.risk_grade(0.5) == "LOW"
    assert rk.risk_grade(2.0) == "MED" and rk.risk_grade(4.0) == "HIGH"
    assert rk.discount(None) == 1.0 and rk.discount(9000) == 0.9


def test_main_end_to_end(monkeypatch, capsys):
    raw = []
    for i, (yb, nb, fy, fn) in enumerate([(2, 97, 1000, 1000), (3, 96, 50000, 2000)]):
        raw.append((f"KXS{i}-E-M", NOW, yb, nb, fy, fn, True, True, f"KXS{i}", f"KXS{i}-E",
                    300.0, NOW - dt.timedelta(days=10), NOW + dt.timedelta(days=20),
                    NOW + dt.timedelta(days=30), 1000.0, "t", 9000))
    hist = []
    k = 0                                    # KXS0 steps up 9c once a day at 15:00Z; KXS1 is flat
    for d in range(10):
        for hh in range(24):
            at = NOW - dt.timedelta(days=10 - d) + dt.timedelta(hours=hh)
            k += int(at.hour == 15)
            yb = 5 + 9 * k
            hist.append(("KXS0-E-M", at, yb, 98 - yb))
            hist.append(("KXS1-E-M", at, 3, 96))
    vol = [("KXS0-E-M", NOW, 100.0, 4), ("KXS1-E-M", NOW, 0.0, 0)]

    class Cur:
        q = None

        def execute(self, sql, params):
            self.q = ("hist" if "date_trunc('hour'" in sql and "ANY" in sql
                      else "vol" if "incentive_trade_events" in sql else "now")

        def fetchall(self):
            return {"now": raw, "hist": hist, "vol": vol}[self.q]

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
    assert rk.main(["--now", NOW.isoformat(), "--json"]) == 0
    out = capsys.readouterr().out
    assert "live liquidity markets 2 (2 quotable)" in out
    assert out.index("KXS0-E-M") < out.index("KXS1-E-M")
    s0 = [ln for ln in out.splitlines() if ln.strip().startswith("KXS0 ")][0]
    s1 = [ln for ln in out.splitlines() if ln.strip().startswith("KXS1 ")][0]
    assert "HIGH" in s0 and "15Z" in s0 and "LOW" in s1
    assert "JSON {" in out


def test_ranker_is_ops_allowlisted():
    import ops_runner
    assert "limm_market_ranker" in ops_runner.ALLOWED_SCRIPTS
