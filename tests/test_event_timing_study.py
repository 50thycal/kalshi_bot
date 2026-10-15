"""Tests for scripts/event_timing_study.py: start sources, windows, the acceptance rule, the
pre-registered bar and an offline end-to-end pass (docs/MMSELL_EVENT_TIMING.md)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


et = _load("event_timing_study")
UTC = timezone.utc


def test_ticker_start_is_eastern_time_converted_to_utc():
    # 19:40 EDT on 2 Sep 2026 = 23:40 UTC
    assert et.ticker_start("KXMLBTOTAL-26SEP021940MILCHC") == datetime(2026, 9, 2, 23, 40,
                                                                       tzinfo=UTC)
    # a player prop's market ticker carries the same event block
    assert et.ticker_start("KXMLBHR-26JUL261920NYYPHI-PHITTURNER7-1") == datetime(
        2026, 7, 26, 23, 20, tzinfo=UTC)
    # team code starting with digits after the time block (100 Thieves)
    assert et.ticker_start("KXVALORANTGAME-26AUG092000100TLOUD") == datetime(
        2026, 8, 10, 0, 0, tzinfo=UTC)


def test_ticker_start_none_for_date_only_or_placeholder():
    assert et.ticker_start("KXNFLTOTAL-26AUG13INDNE-60") is None
    assert et.ticker_start("KXNPBGAME-26JUL150000FUKHOK-FUK") is None
    assert et.ticker_start("KXATPMATCH-26JUL13FAUDAM-DAM") is None


def test_family_of():
    assert et.family_of("KXMLBTOTAL") == "MLB"
    assert et.family_of("KXMLBHR") == "MLB"
    assert et.family_of("KXNFLSPREAD") == "NFL"
    assert et.family_of("KXNCAAFTOTAL") == "NCAAF"
    assert et.family_of("KXATPCHALLENGERMATCH") == "tennis"
    assert et.family_of("KXITFWMATCH") == "tennis"
    assert et.family_of("KXT20MATCH") == "other-sports"      # cricket, not tennis
    assert et.family_of("KXMLSGAME") == "soccer"
    assert et.family_of("KXWCGOAL") == "soccer"
    assert et.family_of("KXUFCMOV") == "other-sports"


def test_team_code_and_matching_with_aliases():
    assert et.team_code("KXMLBKS-26AUG081915CLECWS") == "CLECWS"
    assert et.team_code("KXNFLTOTAL-26AUG13INDNE") == "INDNE"
    assert et.code_matches("CLECWS", "CLE", "CWS")
    assert et.code_matches("CLECWS", "CWS", "CLE")
    assert et.code_matches("OAKSEA", "SEA", "ATH")          # alias
    assert not et.code_matches("CLECWS", "CLE", "DET")


def test_windows_are_half_open_on_hours_from_start():
    assert et.window_of(-30) == "pre>24h"
    assert et.window_of(-24) == "pre6-24h"
    assert et.window_of(-6.01) == "pre6-24h"
    assert et.window_of(-6) == "pre<6h"
    assert et.window_of(-0.01) == "pre<6h"
    assert et.window_of(0) == "in-play"
    assert et.window_of(3) == "in-play"


def test_calibrate_offset_needs_enough_tight_offsets():
    d, how = et.calibrate_offset([180.0] * 19 + [175.0])
    assert d == 180.0 and how.startswith("family D +3.00h")
    assert et.calibrate_offset([180.0] * 19)[0] is None                  # 19 < 20 checkable
    assert et.calibrate_offset([180.0] * 10 + [0.0] * 10)[0] is None     # scattered


def test_internal_ok_rejects_early_resolution_and_placeholders():
    assert et.internal_ok(40, 40, [2.0] * 40)[0]
    assert not et.internal_ok(30, 40, [2.0] * 40)[0]       # 75% resolved after derived start
    assert not et.internal_ok(40, 40, [15.0] * 40)[0]      # placeholder: lag far too long
    assert not et.internal_ok(0, 0, [])[0]


def _mk(ev, occ, close=None):
    return {"ticker": f"{ev}-X", "event_ticker": ev, "occurrence_datetime": occ.isoformat(),
            "expected_expiration_time": occ.isoformat(),
            "close_time": (close or occ).isoformat(),
            "expiration_time": (occ + timedelta(days=14)).isoformat()}


def test_sources_precedence_and_pooled_offset_for_tennis():
    markets, tickers = {}, []
    for i in range(25):   # MLB: HHMM in the ticker, occurrence = start + 3h -> D = 3h
        start = datetime(2026, 8, 1 + i, 23, 10, tzinfo=UTC)
        ev = f"KXMLBGAME-26AUG{1 + i:02d}1910NYYBOS"
        m = _mk(ev, start + timedelta(hours=3))
        markets[m["ticker"]] = m
        tickers.append(m["ticker"])
    for i in range(10):   # tennis: date only; match ends ~2h after the derived start
        start = datetime(2026, 8, 1 + i, 15, 0, tzinfo=UTC)
        ev = f"KXATPMATCH-26AUG{1 + i:02d}AAABBB{i}"
        m = _mk(ev, start + timedelta(hours=3), close=start + timedelta(hours=2))
        markets[m["ticker"]] = m
        tickers.append(m["ticker"])
    src = et.resolve_sources(markets, tickers, {})
    assert src["MLB"]["D"] == 180.0 and src["MLB"]["D_source"] == "family"
    assert src["tennis"]["D"] == 180.0 and src["tennis"]["D_source"] == "pooled"
    st, how = et.start_of(tickers[0], markets, src)
    assert how == "ticker-hhmm" and st == datetime(2026, 8, 1, 23, 10, tzinfo=UTC)
    st, how = et.start_of(tickers[-1], markets, src)
    assert how == "occurrence-D(pooled)" and st == datetime(2026, 8, 10, 15, 0, tzinfo=UTC)


def test_pooled_offset_rejected_when_resolutions_precede_derived_start():
    markets, tickers = {}, []
    for i in range(25):
        start = datetime(2026, 8, 1 + i, 23, 10, tzinfo=UTC)
        m = _mk(f"KXMLBGAME-26AUG{1 + i:02d}1910NYYBOS", start + timedelta(hours=3))
        markets[m["ticker"]] = m
        tickers.append(m["ticker"])
    for i in range(10):   # resolves 5h BEFORE the derived start: occurrence is not end-ish here
        occ = datetime(2026, 8, 1 + i, 20, 0, tzinfo=UTC)
        m = _mk(f"KXATPMATCH-26AUG{1 + i:02d}AAABBB{i}", occ, close=occ - timedelta(hours=8))
        markets[m["ticker"]] = m
        tickers.append(m["ticker"])
    src = et.resolve_sources(markets, tickers, {})
    assert src["tennis"]["D"] is None and "rejected" in src["tennis"]["why"]
    assert et.start_of(tickers[-1], markets, src) == (None, "none")


def test_external_schedule_is_a_start_source():
    markets, tickers = {}, []
    refs = {"NFL": {}}
    for i in range(3):
        start = datetime(2026, 9, 10 + i, 0, 20, tzinfo=UTC)
        ev = f"KXNFLTOTAL-26SEP{9 + i:02d}INDNE"
        m = _mk(ev, start + timedelta(hours=3))
        markets[m["ticker"]] = m
        tickers.append(m["ticker"])
        refs["NFL"].setdefault(f"2026-09-{9 + i:02d}", []).append((start, "NE", "IND"))
    src = et.resolve_sources(markets, tickers, refs)
    assert src["NFL"]["ext_matched"] == 3
    st, how = et.start_of(tickers[0], markets, src)
    assert how == "external" and st == datetime(2026, 9, 10, 0, 20, tzinfo=UTC)


def _rows(n, cents, day0=0, win="pre<6h"):
    return [{"pnl": cents / 100.0, "qty": 1.0, "day": f"2026-08-{(day0 + i) % 28 + 1:02d}",
             "win": win} for i in range(n)]


def test_verdict_signal_needs_live_bar_and_paper_direction():
    live = {"pre>24h": _rows(40, 1), "pre6-24h": _rows(40, 1), "pre<6h": _rows(40, 1),
            "in-play": _rows(60, -6)}
    paper_same = {"pre>24h": _rows(40, 2), "pre6-24h": _rows(40, 2), "pre<6h": _rows(40, 2),
                  "in-play": _rows(40, 0)}
    v, lines = et.verdict(live, paper_same, n_boot=200)
    assert v == "SIGNAL"
    assert any("in-play" in ln and "SIGNAL" in ln for ln in lines)
    paper_opp = {**paper_same, "in-play": _rows(40, 5)}
    v, lines = et.verdict(live, paper_opp, n_boot=200)
    assert v == "NO SIGNAL"
    assert any("paper does not confirm" in ln for ln in lines)
    thin_paper = {**paper_same, "in-play": _rows(5, 0)}
    _, lines = et.verdict(live, thin_paper, n_boot=200)
    inplay = next(ln for ln in lines if ln.strip().startswith("in-play"))
    assert "n=5 < 30" in inplay and "-> no" in inplay       # paper too thin to confirm


def test_verdict_no_signal_below_bar_or_thin_window():
    flat = {"pre>24h": _rows(40, 1), "pre6-24h": _rows(40, 1), "pre<6h": _rows(40, 1),
            "in-play": _rows(40, 0)}
    assert et.verdict(flat, flat, n_boot=200)[0] == "NO SIGNAL"
    thin = {**flat, "in-play": _rows(10, 1)}
    v, lines = et.verdict(thin, flat, n_boot=200)
    assert v == "NO SIGNAL" and any("not testable" in ln for ln in lines)


def test_end_to_end_offline(monkeypatch, capsys):
    markets, live, paper = {}, [], []
    for i in range(30):
        day = 1 + i % 25
        start = datetime(2026, 8, day, 23, 5, tzinfo=UTC)
        ev = f"KXMLBTOTAL-26AUG{day:02d}1905NYY{'BOS' if i % 2 else 'TOR'}{i}"
        tk = f"{ev}-9"
        markets[tk] = {"ticker": tk, "event_ticker": ev,
                       "occurrence_datetime": (start + timedelta(hours=3)).isoformat(),
                       "expected_expiration_time": (start + timedelta(hours=3)).isoformat(),
                       "close_time": (start + timedelta(hours=2)).isoformat(),
                       "expiration_time": (start + timedelta(hours=72)).isoformat()}
        entry = start + timedelta(hours=1 if i % 3 == 0 else -3)
        live.append({"ticker": tk, "strat": "Fmmsell10", "order": entry - timedelta(hours=4),
                     "entry": entry, "pnl": -0.05 if i % 3 == 0 else 0.02, "qty": 1.0,
                     "meta_close": start + timedelta(hours=72)})
        paper.append({"ticker": tk, "order": entry, "entry": entry, "pnl": 0.01, "qty": 1.0,
                      "meta_close": start + timedelta(hours=72)})
    monkeypatch.setattr(et, "load_rows", lambda: (live, paper))
    monkeypatch.setattr(et, "fetch_series_category", lambda s: {x: "Sports" for x in s})
    monkeypatch.setattr(et, "fetch_markets", lambda t: markets)
    monkeypatch.setattr(et, "reference_starts", lambda fam, dates: {})
    assert et.main(["--boot", "100"]) == 0
    out = capsys.readouterr().out
    assert "STEP 1" in out and "MLB" in out and "ticker-hhmm 100%" in out
    assert "family D +3.00h" in out
    assert "VERDICT:" in out
    assert "in-play" in out
    # --sources prints no P&L
    assert et.main(["--sources"]) == 0
    out = capsys.readouterr().out
    assert "STEP 1" in out and "VERDICT" not in out and "c/contract" not in out


def test_allowlisted_and_reads_the_read_only_url():
    import ops_runner

    assert "event_timing_study" in ops_runner.ALLOWED_SCRIPTS
    assert "DATABASE_URL_RO" in (SCRIPTS / "event_timing_study.py").read_text()
