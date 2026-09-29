"""GRIDPIN census: the pure logic on inputs whose answers are known by construction
(docs/GRIDPIN_THESIS.md). Pinned: the NP6-346 parser, rung grading, the event-date parse,
5-min -> hourly bucketing, and the pre-registered verdict order (KILL > BLOCKED_DATA > HOLD)."""

from __future__ import annotations

import datetime as dt
import io
import pathlib
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import kalshi_gridpin_census as g  # noqa: E402
import ops_runner  # noqa: E402


def test_allowlisted():
    assert "kalshi_gridpin_census" in ops_runner.ALLOWED_SCRIPTS


def test_event_date_is_the_operating_day_not_the_ticker_close_date():
    title = "Texas ERCOT peak electricity demand on Sep 27, 2026"
    assert g.event_date("KXTXERCOTPEAKD-26SEP28", title) == dt.date(2026, 9, 27)
    # no title -> ticker (UTC close date) minus one day
    assert g.event_date("KXTXERCOTPEAKD-26SEP28") == dt.date(2026, 9, 27)
    assert g.event_date("KXTXERCOTPEAKD-26OCT01") == dt.date(2026, 9, 30)
    assert g.event_date("KXTXERCOTPEAKD-26XXX02") is None
    assert g.event_date("junk") is None


def test_rung_grading():
    assert g.rung_result_from(80001.0, 80000.0, "greater") == "yes"
    assert g.rung_result_from(80000.0, 80000.0, "greater") == "no"
    assert g.rung_result_from(80000.0, 80000.0, "greater_or_equal") == "yes"
    assert g.rung_result_from(79000.0, 80000.0, "less") == "yes"
    assert g.rung_result_from(1.0, 2.0, "between") is None


def _zip(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("cdr.ACTUALSYSLOADFZNP6346.csv", csv_text)
    return buf.getvalue()


def test_parse_np6346():
    csv_text = ("OperDay,HourEnding,COAST,TOTAL,DSTFlag\n"
                "09/28/2026,01:00,1,60471.86,N\n"
                "09/28/2026,17:00,1,80123.40,N\n"
                "bad,02:00,1,x,N\n")
    got = g.parse_np6346(_zip(csv_text))
    assert got == {dt.date(2026, 9, 28): [60471.86, 80123.40]}


def test_hourly_means_need_all_twelve_intervals():
    t0 = dt.datetime(2026, 9, 29, 15, 0)
    full = [(t0 + dt.timedelta(minutes=5 * i), 100.0 + i) for i in range(12)]
    partial = [(dt.datetime(2026, 9, 29, 16, 0), 999.0)]
    got = g.hourly_means_from_5min(full + partial)
    assert got == {16: sum(100.0 + i for i in range(12)) / 12}


def _c0(days=60, vol=200, post=80):
    return {"days": days, "rungs_with_vol": vol, "post_peak_rungs": post}


def test_verdict_order_and_bars():
    ok1 = {"reachable": True, "rate": 0.99}
    ok2 = {"lag_min": 70.0, "flip_rate": 0.0}
    assert g.verdict(_c0(), ok1, ok2)[0] == "CENSUS CLEARS"
    assert g.verdict(_c0(), ok1, {"lag_min": 91.0, "flip_rate": 0.0})[0] == "KILL (premise)"
    assert g.verdict(_c0(), ok1, {"lag_min": 30.0, "flip_rate": 0.03})[0] == "KILL (premise)"
    # a KILL dominates a missing grading source
    assert g.verdict(_c0(), {"reachable": False}, {"lag_min": 200.0, "flip_rate": None})[0] \
        == "KILL (premise)"
    assert g.verdict(_c0(), {"reachable": False}, ok2)[0] == "BLOCKED_DATA"
    assert g.verdict(_c0(), {"reachable": True, "rate": 0.94}, ok2)[0] == "BLOCKED_DATA"
    assert g.verdict(_c0(), ok1, {"lag_min": None, "flip_rate": 0.0})[0] == "HOLD"
    assert g.verdict(_c0(days=28), ok1, ok2)[0] == "HOLD (accrual)"
    assert g.verdict(_c0(post=59), ok1, ok2)[0] == "HOLD (accrual)"
