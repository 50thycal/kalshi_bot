"""Shadow collector (docs/LIQUIDITY_INCENTIVE_THESIS.md): the state machine driven directly —
frames in, commands out, rows in SQLite. No socket, no orders."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.liquidity_incentive import collector as c
from kalshi_bot.liquidity_incentive import fills as fm
from kalshi_bot.liquidity_incentive import programs as pg
from kalshi_bot.liquidity_incentive import quotes as qp
from kalshi_bot.liquidity_incentive.readonly import IncentiveReadOnlyKalshi

T0 = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self, start=T0):
        self.now = start

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


def _program(pid="prog_1", ticker="KXT-A", reward=1_000_000, target="1000.00", disc=5000,
             start=T0 - timedelta(hours=1), end=T0 + timedelta(hours=23)):
    return {"id": pid, "market_id": "m1", "market_ticker": ticker, "incentive_type": "liquidity",
            "incentive_description": "Rest size near the touch.", "start_date": start.isoformat(),
            "end_date": end.isoformat(), "period_reward": reward, "paid_out": False,
            "discount_factor_bps": disc, "target_size_fp": target}


class _ReadClient:
    """Only the GET surface the shadow collector is allowed."""

    def __init__(self, programs=None, market=None, series=None):
        self.programs = programs if programs is not None else [_program()]
        self.market = market or {"market": {"title": "Test", "status": "active",
                                            "event_ticker": "KXT", "close_time": (T0 + timedelta(days=1)).isoformat()}}
        self.series = series or {"series": {"fee_type": "quadratic", "fee_multiplier": 1.0}}
        self.ws_url = "wss://example.test/trade-api/ws/v2"
        self.calls: list[str] = []

    def iter_incentive_programs(self, **kw):
        self.calls.append("programs")
        if isinstance(self.programs, Exception):
            raise self.programs
        yield from self.programs

    def get_market(self, ticker):
        self.calls.append(f"market:{ticker}")
        return self.market

    def get_series(self, series_ticker):
        self.calls.append(f"series:{series_ticker}")
        return self.series

    def get_orderbook(self, ticker, depth=None):
        return {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}

    def ws_headers(self):
        return {}


def _db(settings):
    db.init_engine(settings.database_url)
    db.create_all()


def _state(settings, client=None, clock=None, **overrides):
    for k, v in overrides.items():
        setattr(settings, k, v)
    return c.ShadowState(client or _ReadClient(), settings, db.session_scope, clock=clock or _Clock())


def _snapshot(ticker, yes, no, sid=1, seq=1):
    return {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
            "msg": {"market_ticker": ticker,
                    "yes_dollars_fp": [[f"{p / 100:.2f}", f"{q:.2f}"] for p, q in yes],
                    "no_dollars_fp": [[f"{p / 100:.2f}", f"{q:.2f}"] for p, q in no]}}


def _delta(ticker, side, price, delta, sid=1, seq=2):
    return {"type": "orderbook_delta", "sid": sid, "seq": seq,
            "msg": {"market_ticker": ticker, "side": side, "price_dollars": f"{price / 100:.2f}",
                    "delta_fp": f"{delta:.2f}", "ts_ms": 1}}


def _trade(ticker, yes_px, count, taker, tid, sid=2, seq=1):
    return {"type": "trade", "sid": sid, "seq": seq,
            "msg": {"market_ticker": ticker, "trade_id": tid, "yes_price_dollars": f"{yes_px / 100:.2f}",
                    "no_price_dollars": f"{(100 - yes_px) / 100:.2f}", "count_fp": f"{count:.2f}",
                    "taker_outcome_side": taker, "ts_ms": 2}}


def _subscribed(channel, sid, cid):
    return {"type": "subscribed", "id": cid, "msg": {"channel": channel, "sid": sid}}


# ------------------------------------------------------------- structural guardrails


def test_the_shadow_client_has_no_write_methods():
    writes = ("place", "create", "cancel", "amend", "upgrade", "post", "put", "delete")
    names = [n for n, member in inspect.getmembers(IncentiveReadOnlyKalshi)
             if not n.startswith("_") and callable(member)]
    assert names, "wrapper exposes nothing?"
    for name in names:
        assert not any(w in name.lower() for w in writes), name
        assert name.startswith(("get_", "iter_", "ws_")), name


def test_collector_module_never_names_a_write_endpoint_or_trading_table():
    src = inspect.getsource(c)
    for bad in ("place_order", "create_events_order", "cancel_order", "cancel_events_order",
                "create_v1_order", "/portfolio/orders", "live_orders", "paper_trades", "LiveOrder",
                "PaperTrade"):
        assert bad not in src, bad


def test_every_policy_and_tier_is_pre_registered():
    assert qp.POLICIES == ("A_break_even", "B_reward_efficient", "C_conservative")
    assert qp.CAPITAL_TIERS_USD == (25, 50, 100, 250, 500)


# ------------------------------------------------------------- discovery


def test_discovery_versions_terms_and_marks_disappearance(settings):
    _db(settings)
    client = _ReadClient()
    with db.session_scope() as s:
        r = pg.run_discovery(client, s, now=T0)
        assert r.new_terms == 1 and r.changed_terms == 0
        row = s.scalars(select(m.IncentiveProgram)).one()
        assert row.period_reward_usd == 100 and row.target_size == 1000 and row.discount_factor_bps == 5000
        assert row.fee_rule_json["maker_rate"] == 0.0 and row.fee_rule_json["fee_type"] == "quadratic"
        assert row.market_title == "Test" and row.series_ticker == "KXT"
    # same terms again: last_seen bumps, no new row
    with db.session_scope() as s:
        r = pg.run_discovery(client, s, now=T0 + timedelta(minutes=5))
        assert r.new_terms == 0 and s.scalar(select(m.IncentiveProgram.id).where(
            m.IncentiveProgram.last_seen_at == T0 + timedelta(minutes=5))) is not None
    # reward changes: old row superseded, new row inserted, both kept
    client.programs = [_program(reward=2_000_000)]
    with db.session_scope() as s:
        r = pg.run_discovery(client, s, now=T0 + timedelta(minutes=10))
        assert r.changed_terms == 1
        rows = s.scalars(select(m.IncentiveProgram).order_by(m.IncentiveProgram.id)).all()
        assert len(rows) == 2 and rows[0].superseded_at is not None and rows[1].period_reward_usd == 200
        assert len(pg.current_programs(s)) == 1
    # program vanishes
    client.programs = []
    with db.session_scope() as s:
        r = pg.run_discovery(client, s, now=T0 + timedelta(minutes=15))
        assert r.disappeared == 1 and pg.current_programs(s) == []
        cycles = s.scalars(select(m.IncentiveDiscoveryCycle)).all()
        assert len(cycles) == 4 and cycles[0].total_period_reward_usd == 100


def test_discovery_failure_is_a_cycle_row_not_an_exception(settings):
    _db(settings)
    client = _ReadClient(programs=RuntimeError("boom"))
    with db.session_scope() as s:
        r = pg.run_discovery(client, s, now=T0)
        assert r.errors == 1
        cyc = s.scalars(select(m.IncentiveDiscoveryCycle)).one()
        assert cyc.errors == 1 and "boom" in cyc.notes_json["fetch"]


def test_fee_rule_from_series_variants():
    assert pg.fee_rule_from_series({"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 2.0}).maker_rate == 0.035
    assert pg.fee_rule_from_series({"fee_type": "quadratic", "fee_multiplier": 1.0}).maker_rate == 0.0
    assert pg.fee_rule_from_series(None).source == "default_schedule"


# ------------------------------------------------------------- the shadow book, end to end


def _bring_up(settings, clock, client=None, **overrides):
    st = _state(settings, client=client, clock=clock, **overrides)
    cmds = st.refresh_programs(force=True)
    assert cmds == [] and "KXT-A" in st.markets          # not connected yet -> no commands
    cmds = st.on_connected()
    channels = [cmd["params"]["channels"][0] for cmd in cmds]
    assert channels == ["market_lifecycle_v2", "orderbook_delta", "trade"]
    assert cmds[1]["params"]["use_yes_price"] is True
    st.handle_message(_subscribed("market_lifecycle_v2", 9, cmds[0]["id"]))
    st.handle_message(_subscribed("orderbook_delta", 1, cmds[1]["id"]))
    st.handle_message(_subscribed("trade", 2, cmds[2]["id"]))
    return st


def test_end_to_end_quote_fill_mark_outcome(settings):
    _db(settings)
    clock = _Clock()
    st = _bring_up(settings, clock, liquidity_incentive_capital_tiers="25,100")
    # Book: yes 46 x 900, no 52 (yes-scale 48) x 900. Target 1000 -> our size makes it qualify.
    st.handle_message(_snapshot("KXT-A", yes=[(46, 900), (40, 50)], no=[(48, 900), (60, 30)]))
    st.tick()
    with db.session_scope() as s:
        quotes = s.scalars(select(m.IncentiveShadowQuote).order_by(m.IncentiveShadowQuote.id)).all()
        snap = s.scalars(select(m.IncentiveMarketSnapshot)).one()
    assert len(quotes) == 3 * 2                              # 3 policies x 2 tiers
    a25 = next(q for q in quotes if q.policy == qp.POLICY_BREAK_EVEN and q.capital_tier_usd == 25)
    assert (a25.yes_bid, a25.no_bid, a25.pair_cost_cents, a25.qty_per_side) == (46, 52, 98, 25)
    assert a25.no_bid_yes_scale == 48 and a25.yes_queue_ahead == 900 and a25.no_queue_ahead == 900
    # 950 resting + our 25 is under Target Size 1000 -> the snapshot would not qualify at $25;
    # at $100 our 102 contracts carry the side over the target and the reward rate is positive.
    assert a25.est_reward_per_hour_usd == 0.0
    a100 = next(q for q in quotes if q.policy == qp.POLICY_BREAK_EVEN and q.capital_tier_usd == 100)
    assert a100.qty_per_side == 102 and a100.est_reward_per_hour_usd > 0
    assert a25.pair_edge_cents == 2.0 and a25.ended_at is None
    assert snap.best_yes_bid == 46 and snap.best_no_bid == 52 and snap.est_reference_price == 46
    assert snap.est_yes_meets_target is False                  # 950 < 1000 without us
    c_25 = next(q for q in quotes if q.policy == qp.POLICY_CONSERVATIVE and q.capital_tier_usd == 25)
    assert (c_25.yes_bid, c_25.no_bid) == (45, 51)
    # A taker sells 30 YES at 46: optimistic fills our yes leg; queue models still have 900 ahead.
    clock.tick(10)
    st.handle_message(_trade("KXT-A", 46, 30, "no", "t1"))
    with db.session_scope() as s:
        fills = s.scalars(select(m.IncentiveShadowFill)).all()
        assert {f.fill_model for f in fills} == {fm.MODEL_OPTIMISTIC}
        assert all(f.side == "yes" and f.is_full for f in fills)
        assert s.scalar(select(m.IncentiveTradeEvent.trade_id)) == "t1"
        ev = s.scalars(select(m.IncentiveShadowEvent).where(m.IncentiveShadowEvent.kind == "trade_hit")).all()
        assert len(ev) == 4          # A and B at 46 (two tiers each); C rests at 45, not reached
    assert len(st.pending_marks) == 4 * len(c.MARK_HORIZONS_SECONDS)
    # Marks come due against the live book.
    clock.tick(5)
    st.tick()
    with db.session_scope() as s:
        marks = s.scalars(select(m.IncentiveShadowMark)).all()
        assert {mk.horizon_seconds for mk in marks} == {1, 5}
        assert all(mk.mark_bid_cents == 46 and mk.pnl_at_bid_usd == 0 for mk in marks)
    # A replayed trade id is ignored; a queue-clearing sweep fills the conservative models.
    st.handle_message(_trade("KXT-A", 46, 30, "no", "t1"))
    st.handle_message(_trade("KXT-A", 45, 30, "no", "t2", seq=2))    # a sweep through our level
    pair = st.markets["KXT-A"].pairs[(qp.POLICY_BREAK_EVEN, 25)]
    assert pair.yes_leg.is_full(fm.MODEL_CONSERVATIVE) and pair.yes_leg.is_full(fm.MODEL_QUEUE_AWARE)
    assert not pair.no_leg.filled[fm.MODEL_OPTIMISTIC]
    # Market moves 2 ticks: legitimate cancel -> outcomes written per model, new pair placed.
    clock.tick(60)
    st.handle_message(_delta("KXT-A", "yes", 46, -900, seq=2))
    st.handle_message(_delta("KXT-A", "yes", 44, 900, seq=3))
    st.tick()
    with db.session_scope() as s:
        ended = s.scalars(select(m.IncentiveShadowQuote).where(m.IncentiveShadowQuote.ended_at.isnot(None))).all()
        assert ended and all(q.end_reason == c.END_MARKET_MOVED for q in ended)
        outcomes = s.scalars(select(m.IncentiveShadowOutcome).where(
            m.IncentiveShadowOutcome.quote_id == a25.id)).all()
        assert {o.fill_model for o in outcomes} == set(fm.FILL_MODELS)
        opt = next(o for o in outcomes if o.fill_model == fm.MODEL_OPTIMISTIC)
        assert opt.outcome == fm.OUTCOME_YES_ONLY and opt.single_leg_side == "yes" and opt.single_leg_qty == 25
        assert opt.paired_pnl_usd == 0 and opt.fees_usd == 0 and opt.est_reward_usd >= 0
        assert opt.settled_at is None
        reopened = s.scalars(select(m.IncentiveShadowQuote).where(m.IncentiveShadowQuote.ended_at.is_(None))).all()
        assert len(reopened) == 6 and any(q.yes_bid == 44 for q in reopened)
    assert a25.id in st.pending_settlements
    # Settlement: the market resolves YES -> the single yes leg at 46 pays 54c x 25.
    st.handle_message({"type": "market_lifecycle_v2", "sid": 9, "seq": 1,
                       "msg": {"market_ticker": "KXT-A", "event_type": "settled", "result": "yes"}})
    with db.session_scope() as s:
        opt = s.scalars(select(m.IncentiveShadowOutcome).where(
            m.IncentiveShadowOutcome.quote_id == a25.id,
            m.IncentiveShadowOutcome.fill_model == fm.MODEL_OPTIMISTIC)).one()
        assert opt.settlement_result == "yes" and float(opt.settlement_pnl_usd) == 13.5
        assert opt.net_after_settlement_usd is not None
        # the reopened pairs ended with the market
        assert not s.scalars(select(m.IncentiveShadowQuote).where(m.IncentiveShadowQuote.ended_at.is_(None))).all()
    assert a25.id not in st.pending_settlements


def test_seq_gap_invalidates_book_and_requests_snapshot(settings):
    _db(settings)
    clock = _Clock()
    st = _bring_up(settings, clock)
    st.handle_message(_snapshot("KXT-A", yes=[(46, 900)], no=[(48, 900)], seq=5))
    assert st.markets["KXT-A"].book.valid
    cmds = st.handle_message(_delta("KXT-A", "yes", 46, 1, seq=9))
    assert cmds and cmds[0]["params"]["action"] == "get_snapshot"
    assert not st.markets["KXT-A"].book.valid
    with db.session_scope() as s:
        kinds = set(s.scalars(select(m.IncentiveCollectorEvent.kind)).all())
    assert {c.EV_SEQ_GAP, c.EV_SNAPSHOT_REQUESTED} <= kinds


def test_program_end_and_collector_stop_end_pairs_with_reasons(settings):
    _db(settings)
    clock = _Clock()
    st = _bring_up(settings, clock, liquidity_incentive_capital_tiers="50")
    st.handle_message(_snapshot("KXT-A", yes=[(46, 900)], no=[(48, 900)]))
    st.tick()
    # Program vanishes from the listing -> pairs end, market unsubscribed.
    st.client.programs = []
    cmds = st.refresh_programs(clock.tick(400))
    assert any(cmd["params"].get("action") == "delete_markets" for cmd in cmds)
    with db.session_scope() as s:
        reasons = set(s.scalars(select(m.IncentiveShadowQuote.end_reason)).all())
    assert reasons == {c.END_PROGRAM_GONE}
    # Bring it back and stop the collector.
    st.client.programs = [_program()]
    st.refresh_programs(clock.tick(400))
    st.handle_message(_snapshot("KXT-A", yes=[(46, 900)], no=[(48, 900)], seq=7))
    st.tick(clock.tick(61))
    st.stop()
    with db.session_scope() as s:
        open_rows = s.scalars(select(m.IncentiveShadowQuote).where(m.IncentiveShadowQuote.ended_at.is_(None))).all()
        assert open_rows == []
        assert c.END_COLLECTOR_STOP in set(s.scalars(select(m.IncentiveShadowQuote.end_reason)).all())


def test_market_cap_tracks_highest_reward_first(settings):
    _db(settings)
    client = _ReadClient(programs=[_program("p1", "KXT-A", reward=100_000),
                                   _program("p2", "KXT-B", reward=5_000_000)])
    st = _state(settings, client=client, liquidity_incentive_max_markets=1)
    st.refresh_programs(force=True)
    assert set(st.markets) == {"KXT-B"}


def test_start_shadow_is_off_by_default(settings):
    assert settings.liquidity_incentive_shadow_enabled is False
    assert c.start_shadow(object(), settings) is None


def test_ops_allowlists_carry_the_instrument():
    import sys
    sys.path.insert(0, "scripts")
    import ops_runner
    import railway_env
    assert "liquidity_incentive_report" in ops_runner.ALLOWED_SCRIPTS
    assert "LIQUIDITY_INCENTIVE_SHADOW_ENABLED" in railway_env.ALLOWED_VARS
