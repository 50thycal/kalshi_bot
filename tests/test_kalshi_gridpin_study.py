"""GRIDPIN full probe: the pure logic on inputs whose answers are known by construction
(docs/GRIDPIN_THESIS.md). Pinned: no lookahead (an hour's value is usable only LAG minutes after
the hour ends, and quotes are never backward-filled), the decided-side rules, the P2 control, the
P4 tape proxy, the pre-registered verdicts and the decision rule, and a stubbed end-to-end main()."""

from __future__ import annotations

import datetime as dt
import io
import pathlib
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import kalshi_gridpin_census as gc  # noqa: E402
import kalshi_gridpin_study as s  # noqa: E402
import ops_runner  # noqa: E402

CDT = dt.timezone(dt.timedelta(hours=-5))
DAY = dt.date(2026, 10, 20)


def _ts(h: int, m: int = 0) -> float:
    return dt.datetime.combine(DAY, dt.time(h, m), CDT).timestamp()


def _path(peak_he: int = 17, peak: float = 70_000.0) -> list[tuple[float, float]]:
    """24 hourly values rising to `peak` at HE `peak_he`, then falling."""
    rows = [(he, "N", peak - 1_000.0 * abs(he - peak_he)) for he in range(1, 25)]
    return s.hour_path(rows, DAY, CDT)


def test_allowlisted():
    assert "kalshi_gridpin_study" in ops_runner.ALLOWED_SCRIPTS


def test_fee_and_pnl():
    assert s.fee_cents(50) == 2
    assert s.fee_cents(97) == 1
    assert s.fee_cents(99) == 1
    assert s.net_pnl_c(97, True) == 2
    assert s.net_pnl_c(90, False) == -91


def test_hour_path_counts_absolute_hours_across_dst():
    # 2026-11-01 is the fall-back day: 25 hours, the repeated HE2 flagged Y sorts second
    try:
        from zoneinfo import ZoneInfo
        ct = ZoneInfo("America/Chicago")
    except Exception:  # noqa: BLE001
        return
    d = dt.date(2026, 11, 1)
    rows = [(h, "N", float(h)) for h in range(1, 25)] + [(2, "Y", 2.5)]
    p = s.hour_path(rows, d, ct)
    assert len(p) == 25
    assert [mw for _e, mw in p[:4]] == [1.0, 2.0, 2.5, 3.0]
    start = dt.datetime.combine(d, dt.time(0), ct).timestamp()
    assert p[-1][0] - start == 25 * 3600


def test_parse_np6346_hourly_keeps_hour_and_flag():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", "OperDay,HourEnding,TOTAL,DSTFlag\n10/20/2026,17:00,70000.5,N\n"
                             "10/20/2026,24:00,60000,N\nbad,01:00,1,N\n")
    assert s.parse_np6346_hourly(buf.getvalue()) == {
        DAY: [(17, "N", 70000.5), (24, "N", 60000.0)]}


def test_running_max_respects_the_publication_lag():
    p = _path()
    lag = 90 * 60
    # HE17 (the peak) ends 17:00 and is usable only from 18:30
    assert s.running_max_at(p, _ts(18, 29), lag) == 69_000.0
    assert s.running_max_at(p, _ts(18, 30), lag) == 70_000.0
    assert s.running_max_at(p, _ts(1, 0), lag) is None


def test_decided_side_rules():
    assert s.decided_side(69_500, "greater", 70_000, 12) == "yes"
    assert s.decided_side(70_000, "greater", 70_000, 22) is None       # not strictly above
    assert s.decided_side(71_000, "greater", 70_000, 22) == "no"        # exactly +1,000 MW
    assert s.decided_side(70_999, "greater", 70_000, 22) is None
    assert s.decided_side(72_000, "greater", 70_000, 20) is None        # before 21:00 CT
    assert s.decided_side(72_000, "greater", None, 22) is None
    assert s.decided_side(60_000, "between", 70_000, 22) is None


def test_first_decision_yes_waits_for_publication():
    t, m = s.first_decision(_path(), 69_500, "greater", 90 * 60, CDT, _ts(0), _ts(23, 55), "yes")
    assert t == _ts(18, 30) and m == 70_000.0


def test_first_decision_no_starts_at_21_ct():
    t, m = s.first_decision(_path(), 71_500, "greater", 90 * 60, CDT, _ts(0), _ts(23, 55), "no")
    assert t == _ts(21, 0) and m == 70_000.0


def test_side_ask_and_candles():
    assert s.side_ask_c(0.10, 0.95, "yes") == 95.0
    assert abs(s.side_ask_c(0.10, 0.95, "no") - 90.0) < 1e-9
    assert s.side_ask_c(0.0, 1.0, "yes") is None and s.side_ask_c(0.0, 1.0, "no") is None
    data = {"candlesticks": [
        {"end_period_ts": 20, "yes_bid": {"close_dollars": "0.10"}, "yes_ask": {"close": 95}},
        {"end_period_ts": 10, "yes_bid": {"close_dollars": "0.20"},
         "yes_ask": {"close_dollars": "0.30"}},
        {"yes_bid": {}}]}
    assert s.parse_candles(data) == [(10.0, 0.20, 0.30), (20.0, 0.10, 0.95)]


def test_first_entry_never_backfills_a_quote():
    cs = [(_ts(18, 0), 0.05, 0.90), (_ts(18, 40), 0.05, 0.99), (_ts(19, 0), 0.05, 0.96)]
    # the 90c quote at 18:00 precedes the 18:30 decision and must not be used
    assert s.first_entry(cs, "yes", _ts(18, 30), _ts(23, 55), 0, 97) == (_ts(19, 0), 96.0)
    assert s.first_entry(cs, "yes", _ts(19, 1), _ts(23, 55), 0, 97) == (None, None)


def _rung(ticker, strike, result):
    return {"ticker": ticker, "day": DAY, "status": "settled", "result": result,
            "strike": float(strike), "strike_type": "greater", "close_ts": _ts(23, 55),
            "volume": 10.0}


def test_build_entries_scores_by_result_only():
    paths = {DAY: _path()}
    rungs = [_rung("Y", 69_500, "yes"), _rung("N", 72_000, "no"), _rung("U", 70_500, "no")]
    candles = {
        "Y": [(_ts(18, 0), 0.05, 0.80), (_ts(18, 31), 0.05, 0.95)],
        "N": [(_ts(20, 59), 0.02, 0.10), (_ts(21, 5), 0.06, 0.10)],
        "U": [(_ts(19, 0), 0.10, 0.90)],
    }
    got = {(e["ticker"], e["side"]): e for e in s.build_entries(rungs, paths, candles, CDT, 5400)}
    assert set(got) == {("Y", "yes"), ("N", "no")}
    assert got[("Y", "yes")]["ask"] == 95.0 and got[("Y", "yes")]["pnl"] == 4
    assert abs(got[("N", "no")]["ask"] - 94.0) < 1e-9 and got[("N", "no")]["won"]


def test_build_control_takes_undecided_rungs_in_the_same_hour():
    paths = {DAY: _path()}
    rungs = [_rung("U", 70_500, "no"), _rung("D", 69_500, "yes")]
    candles = {"U": [(_ts(19, 10), 0.10, 0.12)],                # NO ask 90c, undecided
               "D": [(_ts(19, 20), 0.05, 0.90)]}                 # decided YES -> excluded
    p1_band = [{"day": DAY, "hour": 19}]
    ctrl = s.build_control(rungs, paths, candles, CDT, 5400, p1_band)
    assert [(c["ticker"], c["side"], c["won"]) for c in ctrl] == [("U", "no", True)]


def test_tape_and_trade_parse():
    t = {"created_time": "2026-10-20T23:31:00Z", "taker_side": "no", "yes_price_dollars": "0.08",
         "count_fp": "7.00"}
    ts, side, px, cnt = s.parse_trade(t)
    assert side == "no" and abs(px - 92.0) < 1e-9 and cnt == 7.0
    trades = [(100.0, "no", 92.0, 7.0), (100.0, "no", 95.0, 9.0), (100.0, "yes", 50.0, 3.0),
              (5000.0, "no", 90.0, 4.0)]
    assert s.tape_depth(trades, "no", 120.0, 93.0, 1800) == 7.0
    assert s.parse_trade({"taker_side": "yes"}) is None


def _sum(n, mean, acc):
    return {"n": n, "mean": mean, "acc": acc, "sd": 1.0}


def test_verdicts():
    assert s.p1_verdict(_sum(59, 9.0, 1.0))[0] == "HOLD (accrual)"
    assert s.p1_verdict(_sum(60, 2.0, 0.98))[0] == "PASS"
    assert s.p1_verdict(_sum(60, 0.5, 1.0))[0] == "KILL"
    assert s.p1_verdict(_sum(60, 5.0, 0.94))[0] == "KILL"
    assert s.p1_verdict(_sum(60, 1.5, 1.0))[0] == "HOLD"
    assert s.p2_verdict(_sum(20, 3.0, 1), _sum(20, 1.5, 1))[0] == "PASS"
    assert s.p2_verdict(_sum(20, 3.0, 1), _sum(20, 1.6, 1))[0] == "KILL"
    assert s.p2_verdict(_sum(19, 3.0, 1), _sum(20, 0.0, 1))[0] == "UNTESTABLE"
    assert s.p3_verdict(_sum(0, None, None))[0] == "UNTESTABLE"
    assert s.p3_verdict(_sum(20, 3.0, 1))[0] == "PASS"
    assert s.p3_verdict(_sum(20, 2.9, 1))[0] == "FAIL"
    assert s.p4_verdict([5, 5, 6], 30, 60)[0] == "PASS"
    assert s.p4_verdict([5, 4, 4], 30, 60)[0] == "FAIL"
    assert s.p4_verdict([9, 9], 29, 60)[0] == "FAIL"


def test_decision_rule():
    assert s.decision("PASS", "PASS", "UNTESTABLE", "PASS")[0] == "PROPOSE PAPER"
    assert s.decision("PASS", "PASS", "PASS", "PASS")[0] == "PROPOSE PAPER"
    assert s.decision("PASS", "PASS", "FAIL", "PASS")[0] == "HOLD"
    assert s.decision("PASS", "PASS", "UNTESTABLE", "FAIL")[0] == "HOLD"
    assert s.decision("KILL", "PASS", "UNTESTABLE", "PASS")[0] == "KILL"
    assert s.decision("PASS", "KILL", "UNTESTABLE", "PASS")[0] == "KILL"
    assert s.decision("HOLD (accrual)", "UNTESTABLE", "UNTESTABLE", "PASS")[0] == "HOLD"


def test_main_end_to_end_with_stubbed_sources(monkeypatch, capsys):
    """A stubbed run: Kalshi events/candles/trades and ERCOT NP6-346, no network."""
    monkeypatch.setattr(gc, "_ct", lambda: CDT)
    buf = io.BytesIO()
    lines = ["OperDay,HourEnding,TOTAL,DSTFlag"] + [
        f"10/20/2026,{he:02d}:00,{70000 - 1000 * abs(he - 17)},N" for he in range(1, 25)]
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", "\n".join(lines) + "\n")
    blob = buf.getvalue()
    close = dt.datetime.fromtimestamp(_ts(23, 55), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    markets = [{"ticker": "KXTXERCOTPEAKD-26OCT21-T69500", "status": "settled", "result": "yes",
                "floor_strike": 69500, "strike_type": "greater", "close_time": close,
                "volume_fp": "100"}]
    candle = {"candlesticks": [{"end_period_ts": int(_ts(18, 31)),
                                "yes_bid": {"close_dollars": "0.05"},
                                "yes_ask": {"close_dollars": "0.95"}}]}
    trade = {"trades": [{"created_time": dt.datetime.fromtimestamp(
        _ts(18, 35), dt.timezone.utc).isoformat(), "taker_side": "yes",
        "yes_price_dollars": "0.95", "count_fp": "6"}]}

    def fake_fetch(url, *, binary=False, timeout=45):
        if "IceDocListJsonWS" in url:
            return {"ListDocsByRptTypeRes": {"DocumentList": [
                {"Document": {"FriendlyName": "x_csv", "DocID": "1"}}]}}
        if "mirDownload" in url:
            return blob
        if "/events?" in url:
            return {"events": [{"event_ticker": "KXTXERCOTPEAKD-26OCT21",
                                "title": "Peak electricity demand on Oct 20, 2026",
                                "markets": markets}], "cursor": ""}
        if "candlesticks" in url:
            return candle
        if "/markets/trades" in url:
            return trade
        return None

    monkeypatch.setattr(gc, "fetch", fake_fetch)
    monkeypatch.setattr(s.time, "sleep", lambda *_a: None)
    assert s.main([]) == 0
    out = capsys.readouterr().out
    assert "reproduces 1/1 results" in out
    assert "n=1    mean +4.00c" in out
    assert "P1: HOLD (accrual)" in out
    assert "median tape depth 6" in out
    assert "KXTXERCOTPEAKD-26OCT21-T69500,yes,18:31,95,18:30,70000,69500,yes,+4" in out
