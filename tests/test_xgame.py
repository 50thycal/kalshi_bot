"""Tests for the XGAME tape collector (kalshi_bot/xgame/) and the three Phase-2 probe
scripts' pure logic (xgame_tape_study bars/shocks, xmarket_wc alignment, favbuy math)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.xgame.pm import PmGamesClient, norm_team
from kalshi_bot.xgame.tracker import XGameTracker, ticker_day

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

NOW = datetime.now(timezone.utc).replace(microsecond=0)
YES_TOKEN = "1234567890" * 7  # full-length clobTokenId (70 chars), never truncated
NO_TOKEN = "9876543210" * 7


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _setup(settings):
    db.init_engine(settings.database_url)
    db.create_all()


# ---------------------------------------------------------------- parsing helpers
def test_ticker_day_and_norm_team():
    assert ticker_day("KXWCGAME-26JUL04PORCRO-POR") == "2026-07-04"
    assert ticker_day("NODATE") == ""
    assert norm_team(" Portugal! ") == "portugal"


def test_pm_parse_market_extracts_full_token():
    mk = {
        "question": "Will Portugal win on 2026-07-04?",
        "conditionId": "0xc0ffee",
        "clobTokenIds": f'["{YES_TOKEN}", "{NO_TOKEN}"]',  # JSON string form
    }
    parsed = PmGamesClient._parse_market(mk)
    assert parsed == {
        "day": "2026-07-04",
        "team": "portugal",
        "condition_id": "0xc0ffee",
        "token_id": YES_TOKEN,
        "question": "Will Portugal win on 2026-07-04?",
    }
    assert PmGamesClient._parse_market({"question": "Portugal vs Croatia?"}) is None


# ---------------------------------------------------------------- fakes
def _k_market(ticker: str, team: str, close: datetime):
    return {
        "ticker": ticker,
        "yes_sub_title": f"Reg Time: {team}",
        "title": f"{team} moneyline",
        "close_time": close.isoformat(),
    }


def _k_trade(trade_id: str, price_c: float, at: datetime, side="yes", count=10):
    return {
        "trade_id": trade_id,
        "yes_price": price_c,
        "count": count,
        "taker_side": side,
        "created_time": at.isoformat(),
    }


def _pm_trade(tx: str, asset: str, price: float, at: datetime, side="BUY", size=25.0):
    return {
        "transactionHash": tx,
        "asset": asset,
        "conditionId": "0xc0ffee",
        "side": side,
        "size": size,
        "price": price,
        "timestamp": int(at.timestamp()),
    }


class FakeKalshi:
    def __init__(self, markets, trades):
        self._markets = markets  # series -> [market]
        self._trades = trades    # ticker -> [trade]
        self.trade_calls: list[dict] = []

    def get_markets(self, *, status="open", series_ticker=None, limit=200, cursor=None,
                    event_ticker=None, min_close_ts=None):
        return {"markets": self._markets.get(series_ticker, []), "cursor": ""}

    def get_market_trades(self, *, ticker, limit=1000, cursor=None, min_ts=None,
                          max_ts=None):
        self.trade_calls.append({"ticker": ticker, "min_ts": min_ts, "cursor": cursor})
        trades = self._trades.get(ticker, [])
        if min_ts is not None:
            trades = [t for t in trades
                      if datetime.fromisoformat(t["created_time"]).timestamp() >= min_ts]
        return {"trades": trades, "cursor": ""}


class FakePM:
    def __init__(self, games, trades):
        self._games = games      # [{day, team, condition_id, token_id, question}]
        self._trades = trades    # condition_id -> [trade]

    def game_markets(self, tags, pages):
        return list(self._games)

    def trades(self, condition_id, *, limit=500, offset=0):
        rows = self._trades.get(condition_id, [])
        return rows[offset:offset + limit]

    def close(self):
        pass


def _tracker(settings, kal_markets, kal_trades, pm_games, pm_trades):
    settings.xgame_series = "KXWCGAME"
    client = FakeKalshi(kal_markets, kal_trades)
    pm = FakePM(pm_games, pm_trades)
    return XGameTracker(client, settings, pm_client=pm)


PM_GAME = {"day": "2026-07-04", "team": "portugal", "condition_id": "0xc0ffee",
           "token_id": YES_TOKEN, "question": "Will Portugal win on 2026-07-04?"}


# ---------------------------------------------------------------- discovery
def test_discovery_creates_match_and_polls_both_tapes(settings):
    _setup(settings)
    tk = "KXWCGAME-26JUL04PORCRO-POR"
    close = NOW + timedelta(minutes=45)  # in the game window
    kal_trades = [
        _k_trade("kt-1", 62.0, NOW - timedelta(seconds=90)),
        _k_trade("kt-2", 66.0, NOW - timedelta(seconds=30), side="no"),
    ]
    pm_trades = [  # newest-first like the data-api; one trade on the complement token
        _pm_trade("0xaa", YES_TOKEN, 0.61, NOW - timedelta(seconds=20)),
        _pm_trade("0xbb", NO_TOKEN, 0.35, NOW - timedelta(seconds=80), side="SELL"),
    ]
    tracker = _tracker(settings, {"KXWCGAME": [_k_market(tk, "Portugal", close)]},
                       {tk: kal_trades}, [PM_GAME], {"0xc0ffee": pm_trades})
    with db.session_scope() as session:
        summ = tracker.run_once(session)

    assert summ.matched_new == 1
    assert summ.polled == 1 and summ.errors == 0
    assert summ.kalshi_rows == 2 and summ.pm_rows == 2
    with db.session_scope() as session:
        match = session.scalars(select(m.GameMarketMatch)).one()
        assert match.pm_token_id == YES_TOKEN          # full id stored
        assert match.team == "portugal" and match.day == "2026-07-04"
        assert match.kalshi_trades == 2 and match.pm_trades == 2
        assert match.kalshi_since_ts is not None and match.pm_since_ts is not None
        rows = session.scalars(select(m.GameTapeSnapshot)).all()
        by_venue = {}
        for r in rows:
            by_venue.setdefault(r.venue, []).append(r)
        assert len(by_venue["kalshi"]) == 2 and len(by_venue["polymarket"]) == 2
        # normalization: kalshi team_prob == yes price; PM complement token flipped
        probs = sorted(r.team_prob_cents for r in by_venue["polymarket"])
        assert probs == [61.0, 65.0]                   # 0.61 yes-token; 100 - 35 flip
        assert all(r.team_prob_cents == r.price_cents for r in by_venue["kalshi"])


def test_discovery_drops_ambiguous_and_respects_cap(settings):
    _setup(settings)
    settings.xgame_max_matches = 1
    close = NOW + timedelta(minutes=30)
    kal = [
        _k_market("KXWCGAME-26JUL04PORCRO-POR", "Portugal", close),
        _k_market("KXWCGAME-26JUL04FRAESP-FRA", "France", close),
        # duplicate (day, team) on kalshi -> ambiguous, dropped entirely
        _k_market("KXWCGAME-26JUL04PORBRA-POR", "Portugal", close),
    ]
    pm_games = [
        PM_GAME,
        {"day": "2026-07-04", "team": "france", "condition_id": "0xf1",
         "token_id": NO_TOKEN, "question": "Will France win on 2026-07-04?"},
    ]
    tracker = _tracker(settings, {"KXWCGAME": kal}, {}, pm_games, {})
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    # portugal is ambiguous on kalshi; france matches; cap=1 keeps it to one row anyway
    assert summ.matched_new == 1
    with db.session_scope() as session:
        match = session.scalars(select(m.GameMarketMatch)).one()
        assert match.team == "france"


def test_repoll_dedups_and_advances_high_water(settings):
    _setup(settings)
    tk = "KXWCGAME-26JUL04PORCRO-POR"
    close = NOW + timedelta(minutes=45)
    kal_trades = [_k_trade("kt-1", 62.0, NOW - timedelta(seconds=60))]
    pm_trades = [_pm_trade("0xaa", YES_TOKEN, 0.61, NOW - timedelta(seconds=60))]
    tracker = _tracker(settings, {"KXWCGAME": [_k_market(tk, "Portugal", close)]},
                       {tk: kal_trades}, [PM_GAME], {"0xc0ffee": pm_trades})
    with db.session_scope() as session:
        s1 = tracker.run_once(session)
    assert s1.kalshi_rows == 1 and s1.pm_rows == 1
    # second cycle: same tapes returned (inside the overlap window) -> no new rows
    tracker._last_discovery = 1e12  # skip re-discovery
    with db.session_scope() as session:
        s2 = tracker.run_once(session)
    assert s2.kalshi_rows == 0 and s2.pm_rows == 0
    with db.session_scope() as session:
        assert len(session.scalars(select(m.GameTapeSnapshot)).all()) == 2
        # the kalshi re-poll used the min_ts high-water mark (minus overlap)
    assert tracker.client.trade_calls[-1]["min_ts"] is not None


def test_window_skip_and_ended_transition(settings):
    _setup(settings)
    far = _k_market("KXWCGAME-26JUL09AAABBB-AAA", "Argentina", NOW + timedelta(hours=20))
    over = _k_market("KXWCGAME-26JUL01CCCDDD-CCC", "Croatia", NOW - timedelta(hours=5))
    pm_games = [
        {"day": "2026-07-09", "team": "argentina", "condition_id": "0xa1",
         "token_id": "111", "question": "Will Argentina win on 2026-07-09?"},
        {"day": "2026-07-01", "team": "croatia", "condition_id": "0xc2",
         "token_id": "222", "question": "Will Croatia win on 2026-07-01?"},
    ]
    tracker = _tracker(settings, {"KXWCGAME": [far, over]}, {}, pm_games, {})
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    # 20h out: > 6h pre-game -> skipped; 5h past close: > 2h grace -> ended, not polled
    assert summ.matched_new == 2
    assert summ.skipped_window == 1 and summ.ended == 1 and summ.polled == 0
    with db.session_scope() as session:
        by_team = {r.team: r for r in session.scalars(select(m.GameMarketMatch))}
        assert by_team["croatia"].status == "ended"
        assert by_team["argentina"].status == "active"


def test_insert_game_tape_trades_dedups_in_batch(settings):
    _setup(settings)
    with db.session_scope() as session:
        match, created = repo.upsert_game_match(
            session, sport="soccer", day="2026-07-04", team="portugal",
            kalshi_series="KXWCGAME", kalshi_ticker="T", kalshi_event_ticker="E",
            kalshi_title="t", pm_condition_id="0xc", pm_token_id=YES_TOKEN,
            pm_question="q", close_time=None,
        )
        assert created
        rows = [
            {"market_id": "T", "trade_id": "a", "traded_at": NOW, "price_cents": 50.0,
             "team_prob_cents": 50.0, "size": 1.0, "taker_side": "yes"},
            {"market_id": "T", "trade_id": "a", "traded_at": NOW, "price_cents": 50.0,
             "team_prob_cents": 50.0, "size": 1.0, "taker_side": "yes"},
        ]
        assert repo.insert_game_tape_trades(session, match.id, "kalshi", rows) == 1
        assert repo.insert_game_tape_trades(session, match.id, "kalshi", rows) == 0
        # same trade_id on the OTHER venue is a different row
        assert repo.insert_game_tape_trades(session, match.id, "polymarket", rows[:1]) == 1


# ---------------------------------------------------------------- xgame_tape_study
def test_tape_study_bars_and_shocks():
    ts = _load("xgame_tape_study")
    base = 1_000_000
    # leader (PM) jumps +5c at bar 6; follower (Kalshi) catches up over bars 8-9
    leader_rows = [(base + i * 10, 50.0) for i in range(6)]
    leader_rows += [(base + i * 10, 55.0) for i in range(6, 30)]
    follower_rows = [(base + i * 10, 50.0) for i in range(8)]
    follower_rows += [(base + 80, 52.0), (base + 90, 54.5)]
    follower_rows += [(base + i * 10, 54.5) for i in range(10, 30)]
    lead_bars = ts.build_bars(leader_rows, 10)
    foll_bars = ts.build_bars(follower_rows, 10)
    lead, foll = ts.align_bars(lead_bars, foll_bars)
    assert len(lead) == len(foll) == 30
    recs = ts.shock_records(lead, foll, shock_c=3.0, horizon_bars=6,
                            window_cap_bars=30, bar_s=10)
    assert len(recs) == 1
    r = recs[0]
    assert r["size"] == 5.0
    assert r["same_bar"] is False            # follower flat at the shock bar
    assert r["follow"] == 4.5                # 54.5 - 50 in shock direction
    assert r["window_s"] == 30               # >=80% of 5c (4c) first reached at bar 9
    assert r["follow_net"] == r["follow"] - 2.0 * ts.taker_fee_c(50.0)


def test_tape_study_window_censoring():
    ts = _load("xgame_tape_study")
    lead = [50.0] * 5 + [58.0] * 25          # +8c shock at bar 5
    foll = [50.0] * 30                        # never follows
    recs = ts.shock_records(lead, foll, 3.0, horizon_bars=6, window_cap_bars=10, bar_s=10)
    assert len(recs) == 1
    assert recs[0]["window_s"] is None and recs[0]["follow"] == 0.0
    s = ts.summarize(recs, window_cap_s=100)
    assert s["censored"] == 1 and s["med_window_s"] == 100


# ---------------------------------------------------------------- xmarket_wc helpers
def test_xmarket_wc_pin_and_windows():
    wc = _load("xmarket_wc")
    t0 = 1_000_000 // 60 * 60
    mids = {t0 + i * 60: 41.0 for i in range(5)}
    mids[t0 + 5 * 60] = 97.0      # premature spike...
    mids[t0 + 6 * 60] = 81.0      # ...un-pins (< pin - 5)
    for i in range(7, 12):
        mids[t0 + i * 60] = 98.0  # the real pin
    assert wc.pin_time(mids, 95.0) == t0 + 7 * 60
    # the loser-side mirror pins low at the same minute
    assert wc.pin_time_low({k: 100.0 - v for k, v in mids.items()}, 5.0) == t0 + 7 * 60
    # last_in_window never returns the T candle itself
    assert wc.last_in_window(mids, t0 + 7 * 60, t0 + 7 * 60) is None
    assert wc.last_in_window(mids, t0 + 7 * 60, t0 + 9 * 60) == 98.0
    q, ts = wc.at_or_before(mids, t0 + 6 * 60, 60)
    assert q == 81.0 and ts == t0 + 6 * 60
    q, ts = wc.first_after(mids, t0 + 6 * 60, 120)
    assert ts == t0 + 7 * 60


def test_xmarket_wc_no_pin_returns_none():
    wc = _load("xmarket_wc")
    t0 = 1_000_000 // 60 * 60
    mids = {t0 + i * 60: 41.0 for i in range(10)}
    assert wc.pin_time(mids, 95.0) is None


# ---------------------------------------------------------------- kalshi_favbuy_study
def test_favbuy_first_qualifying_and_pnl():
    fb = _load("kalshi_favbuy_study")
    snaps = [  # time-ordered, same market twice: first qualifying row wins
        {"market_ticker": "T1", "yes_ask_cents": 70.0, "minutes_to_close": 50.0},
        {"market_ticker": "T1", "yes_ask_cents": 72.0, "minutes_to_close": 40.0},
        {"market_ticker": "T2", "yes_ask_cents": 95.0, "minutes_to_close": 30.0},
    ]
    picked = fb.first_qualifying(snaps, lambda s: 65.0 <= s["yes_ask_cents"] <= 90.0)
    assert set(picked) == {"T1"} and picked["T1"]["yes_ask_cents"] == 70.0
    # win: 100 - 70 - fee(70) = 30 - 2 = 28; loss: -70 - 2
    assert fb.buy_pnl_c(70.0, 1) == 28.0
    assert fb.buy_pnl_c(70.0, 0) == -72.0
    assert fb.sell_pnl_c(20.0, 0) == 20.0 - fb.maker_fee_ceil_c(20.0)
    assert abs(fb._corr([1, 2, 3, 4], [2, 4, 6, 8]) - 1.0) < 1e-9
    assert fb._corr([1.0, 1.0], [2.0, 2.0]) != fb._corr([1.0, 1.0], [2.0, 2.0])  # nan
