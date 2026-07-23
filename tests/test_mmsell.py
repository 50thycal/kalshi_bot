from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.paper.engine import PaperTradingEngine
from kalshi_bot.risk.manager import RiskManager


def _mkt(ticker, sub, yes_bid_c, yes_ask_c, vol=500, hours=48):
    close = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return {
        "ticker": ticker,
        "yes_sub_title": sub,
        "close_time": close,
        "volume_fp": f"{vol}.0",
        "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
        "yes_ask_dollars": f"{yes_ask_c / 100:.4f}",
    }


def _event(markets):
    return {"event_ticker": "KXTEAM-26", "series_ticker": "KXTEAM", "markets": markets}


def _ob(yes_bid_c, yes_ask_c):
    # book: yes bids at yes_bid; no bids at (100 - yes_ask) -> derives yes_ask
    no_bid_c = 100 - yes_ask_c
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", "300"]],
        "no_dollars": [[f"{no_bid_c / 100:.4f}", "300"]],
    }}


class FakeClient:
    def __init__(self, events, books, market_state=None):
        self._events = events
        self._books = books
        self._state = market_state or {}

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]

    def get_market(self, ticker):
        return {"market": self._state[ticker]}


def _setup(settings):
    settings.bot_mode = "mmsell"
    settings.mmsell_variants = ""  # control-only unless a test opts in
    db.init_engine(settings.database_url)
    db.create_all()


def test_enters_buy_no_at_no_bid_for_cheap_yes(settings):
    _setup(settings)
    # a cheap longshot: yes 18/20 (mid 19, in 5-40 band) -> sell yes == buy no at 100-20 = 80
    ev = _event([_mkt("KXTEAM-26-A", "Team A wins", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    tracker = MmSellTracker(client, settings)

    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.opened == 1

    with db.session_scope() as session:
        trades = session.scalars(select(m.PaperTrade)).all()
        assert len(trades) == 1
        t = trades[0]
        assert t.strategy == "mmsell" and t.side == "no" and t.action == "buy"
        assert t.assumed_price == 80          # maker buys NO at the no-bid (= 100 - yes_ask)


def test_skips_out_of_band_and_dedups(settings):
    _setup(settings)
    ev = _event([
        _mkt("KXTEAM-26-A", "A", 18, 20),        # mid 19 -> in band
        _mkt("KXTEAM-26-B", "B", 55, 57),        # mid 56 -> out of band (favorite)
        _mkt("KXTEAM-26-C", "C", 1, 2),          # mid 1.5 -> below lo (penny) -> skip
    ])
    books = {"KXTEAM-26-A": _ob(18, 20), "KXTEAM-26-B": _ob(55, 57), "KXTEAM-26-C": _ob(1, 2)}
    client = FakeClient([ev], books)

    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.opened == 1                       # only the in-band cheap one

    # second pass -> dedup, nothing new
    with db.session_scope() as session:
        s2 = MmSellTracker(client, settings).run_once(session)
        assert s2.opened == 0
        assert session.scalar(select(func.count()).select_from(m.PaperTrade)) == 1


def test_hold_to_settlement_no_timeout_then_settle_pnl(settings):
    _setup(settings)
    settings.paper_max_hold_hours = 0.0           # would force-close a non-holding book instantly
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    engine = PaperTradingEngine(client, settings, RiskManager(settings))

    with db.session_scope() as session:
        MmSellTracker(client, settings).run_once(session)

    # market still open -> manage must NOT timeout-close the mmsell position (holds to settlement)
    client._state["KXTEAM-26-A"] = {"status": "active", "result": ""}
    with db.session_scope() as session:
        engine.manage_open_positions(session)
        assert session.scalar(
            select(func.count()).select_from(m.PaperTrade).where(m.PaperTrade.status == "open")
        ) == 1

    # now it settles NO (the longshot missed) -> buy-no at 80 wins +0.20 gross, minus entry fee
    client._state["KXTEAM-26-A"] = {"status": "settled", "result": "no"}
    with db.session_scope() as session:
        engine.manage_open_positions(session)
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.status == "settled"
        assert t.pnl > 0.15                        # +0.20 gross less a small entry fee


def test_settles_no_loss_when_longshot_hits(settings):
    _setup(settings)
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    engine = PaperTradingEngine(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        MmSellTracker(client, settings).run_once(session)
    client._state["KXTEAM-26-A"] = {"status": "settled", "result": "yes"}   # longshot HIT
    with db.session_scope() as session:
        engine.manage_open_positions(session)
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.status == "settled"
        assert t.pnl < -0.75                       # buy-no at 80 loses the 80c


def test_fill_assumption_fits_column_width(settings):
    """Regression: a real prod row ('$62,500 or above' @ 16c, mid 14c, no@84c) built a 66-char
    fill_assumption against a String(64) column -> psycopg StringDataRightTruncation, which
    silently dropped the row (and every mmsell entry) on Postgres. sqlite doesn't enforce the
    column width, so this asserts the STRING ITSELF stays within bounds, not just that the
    insert succeeds locally."""
    _setup(settings)
    # the exact subtitle from the production traceback
    ev = _event([_mkt("KXBTCD-26JUL0317-T62499.99", "$62,500 or above", 14, 16, vol=5000)])
    client = FakeClient([ev], {"KXBTCD-26JUL0317-T62499.99": _ob(14, 16)})
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.opened == 1
    with db.session_scope() as session:
        t = session.scalars(select(m.PaperTrade)).one()
        assert t.fill_assumption is not None
        assert len(t.fill_assumption) <= 64


def test_skips_configured_series(settings):
    _setup(settings)
    settings.mmsell_skip_series = "KXTEAM"         # skip our own series
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.opened == 0 and summ.events_seen == 0


def test_variant_spec_parses_skip_and_only(settings):
    settings.mmsell_variants = "mmsell4:lo=5,hi=10,skip=WC+ATP;mmsell5:lo=5,hi=12,only=TOTAL+SPREAD"
    vl = {v["tag"]: v for v in settings.mmsell_variant_list}
    assert vl["mmsell4"]["skip"] == ["WC", "ATP"] and vl["mmsell4"]["only"] == []
    assert vl["mmsell5"]["only"] == ["TOTAL", "SPREAD"] and vl["mmsell5"]["skip"] == []


def test_book_admits_series_helper():
    adm = MmSellTracker._book_admits_series
    assert adm({"skip": ["WC"], "only": []}, "KXWCGOAL") is False        # skip substring hit
    assert adm({"skip": ["WC"], "only": []}, "KXMLBASGHR") is True       # skip miss
    assert adm({"skip": [], "only": ["TOTAL", "SPREAD"]}, "KXWNBATOTAL") is True   # allow hit
    assert adm({"skip": [], "only": ["TOTAL"]}, "KXATPMATCH") is False   # allow miss -> dropped
    assert adm({"skip": [], "only": []}, "KXANYTHING") is True           # no filter -> admit


def test_variant_skip_series_filter(settings):
    _setup(settings)
    settings.mmsell_variants = "mmsell9:lo=5,hi=40,skip=TEAM"   # variant drops our KXTEAM series
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.per_book.get("mmsell") == 1        # control trades it
    assert "mmsell9" not in summ.per_book          # the skip variant does not


def test_variant_only_series_filter(settings):
    _setup(settings)
    settings.mmsell_variants = "mmsell9:lo=5,hi=40,only=TEAM"   # variant trades ONLY KXTEAM-like series
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.per_book.get("mmsell") == 1        # control trades it
    assert summ.per_book.get("mmsell9") == 1       # allow-listed variant trades the matching series


def test_variant_only_filter_excludes_nonmatching(settings):
    _setup(settings)
    settings.mmsell_variants = "mmsell9:lo=5,hi=40,only=NOTPRESENT"  # nothing matches -> variant idle
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.per_book.get("mmsell") == 1        # control still trades
    assert "mmsell9" not in summ.per_book          # allow-list misses -> no trade


def test_variant_spec_parses_maxyes(settings):
    settings.mmsell_variants = "mmsell10:lo=5,hi=10,maxyes=7"
    v = {x["tag"]: x for x in settings.mmsell_variant_list}["mmsell10"]
    assert v["maxyes"] == 7.0


def test_maxyes_ceiling_blocks_rich_entry_admits_cheap(settings):
    _setup(settings)
    # market: yes 8/10 -> we sell yes at the ask 10c (== buy NO at 90c). maxyes=7 must block it.
    settings.mmsell_variants = "mmsell10:lo=5,hi=40,maxyes=7"
    rich = _event([_mkt("KXTEAM-26-A", "A", 8, 10)])
    client = FakeClient([rich], {"KXTEAM-26-A": _ob(8, 10)})
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.per_book.get("mmsell") == 1        # control (no ceiling) trades the 10c sell
    assert "mmsell10" not in summ.per_book         # 10c > maxyes 7 -> ceiling blocks it

    # a cheaper market: yes 5/6 -> sell yes at 6c <= 7c -> the ceiling admits it
    _setup(settings)
    settings.mmsell_variants = "mmsell10:lo=5,hi=40,maxyes=7"
    cheap = _event([_mkt("KXTEAM-26-B", "B", 5, 6)])
    client = FakeClient([cheap], {"KXTEAM-26-B": _ob(5, 6)})
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.per_book.get("mmsell10") == 1      # 6c <= maxyes 7 -> admitted


def test_abandon_foreign_keeps_mmsell_and_weather(settings):
    """The ride-along startup abandon must keep BOTH weather and mmsell paper positions."""
    from kalshi_bot import repository as repo
    _setup(settings)
    with db.session_scope() as session:
        for strat in ("weather_fav_h8", "mmsell", "momentum", "buy_favorite"):
            repo.create_paper_trade(
                session, signal_id=None, ticker=f"T-{strat}", strategy=strat, side="no",
                action="buy", assumed_price=80, quantity=1,
                fill_assumption="x", entry_fee=0.0,
            )
            repo.open_paper_position_for_trade(
                session, ticker=f"T-{strat}", strategy=strat, side="no", quantity=1, avg_price=80,
            )
        n = repo.abandon_open_paper_trades(session, keep_prefixes=("weather", "mmsell"))
    assert n == 2                                  # momentum + buy_favorite abandoned
    with db.session_scope() as session:
        kept = sorted(
            t.strategy for t in session.scalars(
                select(m.PaperTrade).where(m.PaperTrade.status == "open")
            ).all()
        )
    assert kept == ["mmsell", "weather_fav_h8"]


def test_repo_layer_clamps_overlong_fill_assumption(settings):
    """Defense-in-depth for the String(64) overflow that dropped every mmsell row: even if a
    call site passes an over-length note (any book, any subtitle/price width), the repo layer
    clamps it so the insert can never be rejected. Complements the tracker-level truncation
    above by testing the guard at the single insertion point every book shares. (sqlite doesn't
    enforce VARCHAR width, so assert the stored length.)"""
    from kalshi_bot import repository as repo

    _setup(settings)
    overlong = "[somebook] " + "x" * 200
    with db.session_scope() as session:
        repo.create_paper_trade(
            session, signal_id=None, ticker="KXTEAM-26-A", strategy="mmsell", side="no",
            action="buy", assumed_price=80, quantity=1, fill_assumption=overlong, entry_fee=0.0,
        )
        repo.record_no_fill(
            session, signal_id=None, ticker="KXTEAM-26-B", strategy="mmsell", side="no",
            action="buy", assumed_price=80, fill_assumption=overlong,
        )
    with db.session_scope() as session:
        for t in session.scalars(select(m.PaperTrade)).all():
            assert len(t.fill_assumption) <= 64


def test_mmsell_variant_spec_parsing(settings):
    settings.mmsell_entry_lo_cents = 5
    settings.mmsell_entry_hi_cents = 40
    settings.mmsell_variants = (
        "mmsell1:lo=5,hi=20;mmsell2:lo=10,hi=20;mmsell:hi=10;bad:hi=10;mmsell3:lo=30,hi=10"
    )
    vs = settings.mmsell_variant_list
    tags = [v["tag"] for v in vs]
    # 'mmsell' (control collision), 'bad' (wrong prefix), inverted-band mmsell3 all rejected
    assert tags == ["mmsell1", "mmsell2"]
    assert vs[0]["lo"] == 5 and vs[0]["hi"] == 20
    assert vs[1]["lo"] == 10 and vs[1]["hi"] == 20
    # unset keys inherit the base knobs
    assert vs[0]["htcmax"] == settings.mmsell_max_hours_to_close


def test_mmsell_variants_gate_by_band(settings):
    """A 15c-mid market: control (5-40) + mmsell1 (5-20) + mmsell2 (10-20) all take it.
    A 30c-mid market: only the control (5-40) takes it; the narrowed variants skip."""
    _setup(settings)
    settings.mmsell_variants = "mmsell1:lo=5,hi=20;mmsell2:lo=10,hi=20"
    ev = _event([
        _mkt("KXTEAM-26-A", "cheap", 14, 16),   # mid 15 -> all three
        _mkt("KXTEAM-26-B", "mid", 29, 31),      # mid 30 -> control only
    ])
    books = {"KXTEAM-26-A": _ob(14, 16), "KXTEAM-26-B": _ob(29, 31)}
    with db.session_scope() as session:
        summ = MmSellTracker(client=FakeClient([ev], books), settings=settings).run_once(session)
    assert summ.per_book == {"mmsell": 2, "mmsell1": 1, "mmsell2": 1}
    with db.session_scope() as session:
        by_strat = {}
        for t in session.scalars(select(m.PaperTrade)).all():
            by_strat.setdefault(t.strategy, set()).add(t.market_ticker)
            assert t.assumed_price == (84 if t.market_ticker.endswith("A") else 69)
            assert len(t.fill_assumption) <= 64 and t.fill_assumption.startswith(f"[{t.strategy}]")
        assert by_strat["mmsell"] == {"KXTEAM-26-A", "KXTEAM-26-B"}
        assert by_strat["mmsell1"] == {"KXTEAM-26-A"}
        assert by_strat["mmsell2"] == {"KXTEAM-26-A"}


def test_mmsell_variant_dedup_is_per_book(settings):
    _setup(settings)
    settings.mmsell_variants = "mmsell1:lo=5,hi=20"
    ev = _event([_mkt("KXTEAM-26-A", "cheap", 14, 16)])
    books = {"KXTEAM-26-A": _ob(14, 16)}
    with db.session_scope() as session:
        summ = MmSellTracker(client=FakeClient([ev], books), settings=settings).run_once(session)
    assert summ.per_book == {"mmsell": 1, "mmsell1": 1}
    with db.session_scope() as session:  # second pass dedups both books
        s2 = MmSellTracker(client=FakeClient([ev], books), settings=settings).run_once(session)
    assert s2.opened == 0 and s2.already_open == 2


def test_captures_candidate_tick_for_in_band_market(settings):
    """The fill-realism collection: every IN-BAND candidate is taped once per cycle (opened or not),
    off the orderbook the entry scan already fetched, with the orderbook fields the fill replay needs."""
    _setup(settings)
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])   # mid 19 in band; no_bid 80
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    with db.session_scope() as session:
        MmSellTracker(client, settings).run_once(session)
    with db.session_scope() as session:
        rows = session.scalars(select(m.MmSellCandidateTick)).all()
        assert len(rows) == 1
        r = rows[0]
        assert r.market_ticker == "KXTEAM-26-A" and r.series == "KXTEAM"
        assert r.yes_bid == 18 and r.yes_ask == 20 and r.no_bid == 80 and r.mid == 19
        assert r.hours_to_close is not None and r.captured_at is not None


def test_candidate_capture_taped_even_when_no_open(settings):
    """A candidate already holding a position is still taped (pre-entry path is for ALL in-band
    candidates, not just fresh opens) — the second cycle dedups the TRADE but re-tapes the tick."""
    _setup(settings)
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    with db.session_scope() as session:
        MmSellTracker(client, settings).run_once(session)
    with db.session_scope() as session:
        s2 = MmSellTracker(client, settings).run_once(session)
        assert s2.opened == 0 and s2.already_open == 1   # trade dedups
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(m.MmSellCandidateTick)) == 2  # tick re-taped


def test_candidate_capture_can_be_disabled(settings):
    _setup(settings)
    settings.mmsell_capture_candidates = False
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.opened == 1                              # trading unaffected
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(m.MmSellCandidateTick)) == 0


def test_candidate_capture_is_per_cycle_capped(settings):
    _setup(settings)
    settings.mmsell_candidate_capture_max = 1            # only the first in-band candidate is taped
    ev = _event([
        _mkt("KXTEAM-26-A", "A", 18, 20),               # both in band
        _mkt("KXTEAM-26-B", "B", 14, 16),
    ])
    books = {"KXTEAM-26-A": _ob(18, 20), "KXTEAM-26-B": _ob(14, 16)}
    with db.session_scope() as session:
        summ = MmSellTracker(client=FakeClient([ev], books), settings=settings).run_once(session)
    assert summ.opened == 2                              # cap does not gate trading
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(m.MmSellCandidateTick)) == 1


def test_candidate_capture_is_fail_soft(settings, monkeypatch):
    """A capture failure must never break the trading loop — the position still opens."""
    from kalshi_bot.mmsell import tracker as tr

    _setup(settings)

    def _boom(*a, **k):
        raise RuntimeError("capture blew up")

    monkeypatch.setattr(tr.repo, "insert_mmsell_candidate_tick", _boom)
    ev = _event([_mkt("KXTEAM-26-A", "A", 18, 20)])
    client = FakeClient([ev], {"KXTEAM-26-A": _ob(18, 20)})
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings).run_once(session)
    assert summ.opened == 1                              # trade opened despite the capture error
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(m.MmSellCandidateTick)) == 0
