"""Evo paper engine, listeners, strategy DSL, sandbox and strategy runner."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot.evo import listeners as ln
from kalshi_bot.evo import models as em
from kalshi_bot.evo import paper, sandbox, strategy_runner
from kalshi_bot.evo.marketdata import Quote, StaticMarketData, quote_from_kalshi
from kalshi_bot.evo.strategy_spec import entry_signal, exit_signal, validate_spec
from kalshi_bot.models import (
    BackfillWeatherCandle,
    BackfillWeatherMarket,
    CryptoLadderSnapshot,
    CryptoSpotCandle,
    MmSellPositionTick,
    PaperTrade,
)
from kalshi_bot.paper.engine import kalshi_fee

AU = "a" * 36
NOW = datetime.now(timezone.utc)


def _quote(ticker="T1", yes_bid=44, no_bid=53, yes_depth=50, no_depth=30,
           hours=5.0, status="active", result="", volume=500, captured=None,
           no_levels=None, yes_levels=None):
    yes_ask = 100 - no_bid
    return Quote(
        ticker=ticker, captured_at=captured or datetime.now(timezone.utc),
        status=status, result=result,
        yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=100 - yes_bid,
        yes_levels=yes_levels if yes_levels is not None else [(yes_bid, yes_depth)],
        no_levels=no_levels if no_levels is not None else [(no_bid, no_depth)],
        volume=volume, open_interest=1000,
        close_time=datetime.now(timezone.utc) + timedelta(hours=hours),
    )


def _fund(session, settings, cohort_id=1):
    paper.ensure_portfolio(session, AU, paper.LIFETIME, 1000.0)
    paper.ensure_portfolio(session, AU, paper.cohort_ledger(cohort_id), 1000.0)


# --- marketdata -------------------------------------------------------------


def test_quote_from_kalshi_derives_asks_and_sorts_levels():
    market = {"ticker": "X", "status": "active", "yes_bid": 40, "close_time": None}
    ob = {"orderbook": {"yes": [[38, 10], [40, 5]], "no": [[55, 7], [57, 3]]}}
    q = quote_from_kalshi(market, ob)
    assert q.yes_levels[0] == (40, 5) and q.no_levels[0] == (57, 3)
    assert q.yes_ask == 100 - 57 and q.no_ask == 100 - 40
    # taker buy YES consumes NO bids at 100-no_bid
    assert q.taker_levels("yes")[0] == (43, 3)
    assert q.exit_levels("yes")[0] == (40, 5)


# --- order validation -------------------------------------------------------


def test_order_validation_rejections(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    for i, (kwargs, frag) in enumerate((
        (dict(side="up", action="buy", quantity=1), "invalid side"),
        (dict(side="yes", action="buy", quantity=0), "invalid quantity"),
        (dict(side="yes", action="buy", quantity=1, style="amm"), "invalid style"),
        (dict(side="yes", action="buy", quantity=1, style="maker"), "limit price"),
        (dict(side="yes", action="sell", quantity=5), "cannot sell"),
        (dict(side="yes", action="buy", quantity=5000, limit_price_cents=99),
         "insufficient collateral"),
    )):
        order, err = paper.place_order(
            evo_session, evo_settings, agent_uuid=AU, cohort_id=1,
            idem_key=f"k-reject-{i}", market_ticker="T1", **kwargs,
        )
        assert order is None and frag in err, (kwargs, err)
    rejected = list(
        evo_session.scalars(select(em.EvoOrder).where(em.EvoOrder.status == "rejected"))
    )
    assert len(rejected) == 6  # every rejection leaves an auditable row


def test_order_idempotency(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    o1, err = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="same",
        market_ticker="T1", side="yes", action="buy", quantity=5, limit_price_cents=50,
    )
    o2, err = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="same",
        market_ticker="T1", side="yes", action="buy", quantity=5, limit_price_cents=50,
    )
    assert o1.id == o2.id
    assert evo_session.scalar(select(em.EvoOrder.id).where(
        em.EvoOrder.idem_key == "same", em.EvoOrder.id != o1.id)) is None


# --- fills ------------------------------------------------------------------


def test_taker_fill_walks_depth_with_exact_fees(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    md.set_quote(_quote(no_levels=[(53, 30), (52, 100)]))  # asks: 47x30 then 48x100
    order, err = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="t1",
        market_ticker="T1", side="yes", action="buy", quantity=40, limit_price_cents=48,
    )
    assert err is None
    paper.process_open_orders(evo_session, evo_settings, md)
    fills = list(evo_session.scalars(select(em.EvoFill).order_by(em.EvoFill.id)))
    assert [(f.price_cents, f.quantity) for f in fills] == [(47, 30), (48, 10)]
    assert float(fills[0].fee_usd) == kalshi_fee(47, 30)
    assert float(fills[1].fee_usd) == kalshi_fee(48, 10)
    assert order.status == "filled"
    # both ledgers identical
    cash_c = float(paper.get_portfolio(evo_session, AU, "cohort:1").cash_usd)
    cash_l = float(paper.get_portfolio(evo_session, AU, paper.LIFETIME).cash_usd)
    assert cash_c == cash_l
    expected = 1000.0 - (30 * 47 + 10 * 48) / 100.0 - kalshi_fee(47, 30) - kalshi_fee(48, 10)
    assert abs(cash_c - expected) < 1e-9


def test_taker_respects_limit_price_partial(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    md.set_quote(_quote(no_levels=[(53, 10), (50, 100)]))  # asks: 47x10, 50x100
    order, _ = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="t2",
        market_ticker="T1", side="yes", action="buy", quantity=40, limit_price_cents=47,
    )
    paper.process_open_orders(evo_session, evo_settings, md)
    assert order.status == "partial" and order.filled_quantity == 10


def test_maker_requires_trade_through_and_haircut(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    order, _ = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="m1",
        market_ticker="T1", side="yes", action="buy", quantity=20, style="maker",
        limit_price_cents=40,
    )
    # touch at exactly 40: NO fill
    md.set_quote(_quote(yes_bid=38, no_bid=60))  # ask = 40
    paper.process_open_orders(evo_session, evo_settings, md)
    assert order.filled_quantity == 0
    # strict trade-through at 39: fill with 25% quantity haircut
    md.set_quote(_quote(yes_bid=37, no_bid=61))  # ask = 39 < 40
    paper.process_open_orders(evo_session, evo_settings, md)
    assert order.filled_quantity == int(20 * 0.75)
    fill = evo_session.scalar(select(em.EvoFill))
    assert fill.liquidity == "maker" and fill.price_cents == 40


def test_stale_data_fails_closed(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    md.set_quote(_quote(captured=datetime.now(timezone.utc) - timedelta(hours=2)))
    order, _ = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="s1",
        market_ticker="T1", side="yes", action="buy", quantity=5, limit_price_cents=60,
    )
    paper.process_open_orders(evo_session, evo_settings, md)
    assert order.status == "open" and order.filled_quantity == 0
    assert evo_session.scalar(select(em.EvoDataHealthEvent)) is not None


def test_no_quote_at_all_records_health_event_and_leaves_order_open(evo_session, evo_settings):
    """Distinct from the stale-quote case: a ticker that never resolves to a quote
    at all (e.g. an agent used a series prefix like "KXHIGH" instead of a real,
    specific tradable market ticker) would otherwise leave the order open forever
    with zero operator-visible signal. Must record a health event, not fail silently."""
    _fund(evo_session, evo_settings)
    md = StaticMarketData()  # no quote ever set for T1
    order, _ = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="nq1",
        market_ticker="T1", side="yes", action="buy", quantity=5,
    )
    paper.process_open_orders(evo_session, evo_settings, md)
    assert order.status == "open" and order.filled_quantity == 0
    event = evo_session.scalar(select(em.EvoDataHealthEvent))
    assert event is not None and event.kind == "no_quote"
    assert event.detail_json["order_id"] == order.id


def test_no_quote_backstop_rejects_unfillable_order_after_window(evo_session, evo_settings):
    """An order that never fills and can get NO quote for longer than the configured
    window is unfillable (invalid/untradable ticker) — auto-reject it with a reason the
    agent will see, instead of leaving it open forever. This is what finally clears a
    stuck order placed on a bare series prefix like "KXHIGH"."""
    _fund(evo_session, evo_settings)
    md = StaticMarketData()  # never a quote for T1
    order, _ = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="nqb1",
        market_ticker="KXHIGH", side="yes", action="buy", quantity=5,
    )
    # backdate creation past the reject window
    order.created_at = datetime.now(timezone.utc) - timedelta(
        hours=evo_settings.order_no_quote_reject_hours + 1
    )
    evo_session.flush()
    paper.process_open_orders(evo_session, evo_settings, md)
    assert order.status == "rejected"
    assert "no live quote" in order.reject_reason
    assert order.filled_quantity == 0


def test_no_quote_backstop_spares_partially_filled_order(evo_session, evo_settings):
    """Fill-aware: a real market that briefly loses its book must NOT be auto-rejected
    if it already has fills — only never-filled orders are treated as unfillable."""
    _fund(evo_session, evo_settings)
    md = StaticMarketData()  # no quote this cycle
    order, _ = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="nqb2",
        market_ticker="T1", side="yes", action="buy", quantity=5, limit_price_cents=60,
    )
    order.filled_quantity = 2
    order.status = "partial"
    order.created_at = datetime.now(timezone.utc) - timedelta(
        hours=evo_settings.order_no_quote_reject_hours + 5
    )
    evo_session.flush()
    paper.process_open_orders(evo_session, evo_settings, md)
    assert order.status == "partial"  # spared


def test_recent_orders_surfaces_open_and_rejected(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    open_order, _ = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="ro-open",
        market_ticker="T1", side="yes", action="buy", quantity=5, limit_price_cents=50,
    )
    # a rejected order (sell with nothing held) leaves an auditable row with a reason
    paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="ro-rej",
        market_ticker="T1", side="yes", action="sell", quantity=9,
    )
    rows = paper.recent_orders(evo_session, AU)
    by_status = {r["status"]: r for r in rows}
    assert "open" in by_status and by_status["open"]["order_id"] == open_order.id
    assert "rejected" in by_status and "cannot sell" in by_status["rejected"]["reject_reason"]


def test_spec_fingerprint_stable_and_recent_runs_counts_repeats(evo_session, evo_settings):
    spec_a = {"name": "wx_a", "universe": {"series_prefixes": ["KXHIGH"]}}
    spec_b = {"name": "wx_b", "universe": {"series_prefixes": ["KXLOW"]}}
    # identical specs must fingerprint identically regardless of key order
    assert sandbox.spec_fingerprint(spec_a) == sandbox.spec_fingerprint(dict(spec_a))
    assert sandbox.spec_fingerprint(spec_a) != sandbox.spec_fingerprint(spec_b)
    for spec in (spec_a, spec_a, spec_b):  # spec_a run twice
        evo_session.add(em.EvoSandboxRun(
            agent_uuid=AU, kind="backtest", dataset="backfill_weather",
            params_json={"spec": spec},
            result_json={"n_trades": 100, "win_rate": 0.17, "total_pnl_usd": -22.0,
                         "per_trade_usd": -0.22},
        ))
    evo_session.flush()
    runs = sandbox.recent_runs(evo_session, AU)
    fp_a = sandbox.spec_fingerprint(spec_a)
    a_runs = [r for r in runs if r["fingerprint"] == fp_a]
    assert len(a_runs) == 2 and all(r["times_run_recently"] == 2 for r in a_runs)


def test_terminal_market_expires_order(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    md.set_quote(Quote(ticker="T1", captured_at=datetime.now(timezone.utc),
                       status="settled", result="yes"))
    order, _ = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="e1",
        market_ticker="T1", side="yes", action="buy", quantity=5, limit_price_cents=60,
    )
    paper.process_open_orders(evo_session, evo_settings, md)
    assert order.status == "expired"


def test_settlement_and_void(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    md.set_quote(_quote(ticker="W", no_levels=[(53, 100)]))
    md.set_quote(_quote(ticker="L", no_levels=[(53, 100)]))
    md.set_quote(_quote(ticker="V", no_levels=[(53, 100)]))
    for i, t in enumerate(("W", "L", "V")):
        paper.place_order(
            evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key=f"st{i}",
            market_ticker=t, side="yes", action="buy", quantity=10, limit_price_cents=47,
        )
    paper.process_open_orders(evo_session, evo_settings, md)
    cash_before = float(paper.get_portfolio(evo_session, AU, "cohort:1").cash_usd)
    md.set_quote(Quote(ticker="W", captured_at=NOW, status="settled", result="yes"))
    md.set_quote(Quote(ticker="L", captured_at=NOW, status="settled", result="no"))
    md.set_quote(Quote(ticker="V", captured_at=NOW, status="settled", result="void"))
    counts = paper.mark_and_settle(evo_session, md)
    # W + L settle in BOTH ledgers (cohort + lifetime) = 4; V voids in both = 2
    assert counts["settled"] == 4 and counts["voided"] == 2
    pf = paper.get_portfolio(evo_session, AU, "cohort:1")
    # W pays 10 contracts * $1; L pays 0; V returns cost 4.70; no settlement fees
    assert abs(float(pf.cash_usd) - (cash_before + 10.0 + 0.0 + 4.70)) < 1e-9
    w_pos = evo_session.scalar(
        select(em.EvoPosition).where(
            em.EvoPosition.market_ticker == "W", em.EvoPosition.ledger == "cohort:1"
        )
    )
    assert w_pos.status == "settled" and w_pos.resolved_value_cents == 100


def test_carryover_normalizes_to_exactly_1000(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    md.set_quote(_quote(ticker="C", no_levels=[(53, 500)], yes_levels=[(60, 500)],
                        yes_bid=60, hours=100))
    paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="c1",
        market_ticker="C", side="yes", action="buy", quantity=100, limit_price_cents=47,
    )
    paper.process_open_orders(evo_session, evo_settings, md)
    res = paper.carry_over_positions(
        evo_session, evo_settings, agent_uuid=AU, old_cohort_id=1, new_cohort_id=2, md=md
    )
    assert res["scale"] == 1.0
    assert abs(paper.nav(evo_session, AU, "cohort:2") - 1000.0) < 1e-9
    new_pos = evo_session.scalar(
        select(em.EvoPosition).where(em.EvoPosition.ledger == "cohort:2")
    )
    assert float(new_pos.avg_price_cents) == 60.0  # boundary mark is the new basis
    # lifetime position is untouched (continuity)
    lt_pos = evo_session.scalar(
        select(em.EvoPosition).where(em.EvoPosition.ledger == paper.LIFETIME)
    )
    assert lt_pos.status == "open" and float(lt_pos.avg_price_cents) == 47.0


def test_carryover_scales_when_positions_exceed_baseline(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    # buy 2000 @ 47 = $940 cost, then mark at 95 => $1900 > $1000 baseline
    md.set_quote(_quote(ticker="B", no_levels=[(53, 5000)], hours=100))
    paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="b1",
        market_ticker="B", side="yes", action="buy", quantity=2000, limit_price_cents=47,
        max_position_cost_usd=2000,
    )
    paper.process_open_orders(evo_session, evo_settings, md)
    md.set_quote(_quote(ticker="B", yes_bid=95, no_bid=3, yes_levels=[(95, 5000)],
                        no_levels=[(3, 5000)], hours=100))
    res = paper.carry_over_positions(
        evo_session, evo_settings, agent_uuid=AU, old_cohort_id=1, new_cohort_id=2, md=md
    )
    assert res["scale"] < 1.0
    assert abs(paper.nav(evo_session, AU, "cohort:2") - 1000.0) < 0.01
    transfer = evo_session.scalar(select(em.EvoPositionTransfer))
    assert transfer.kind == "carryover" and transfer.scale_factor == res["scale"]


def test_liquidate_agent_idempotent(evo_session, evo_settings):
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    md.set_quote(_quote(ticker="R", no_levels=[(53, 500)], hours=100))
    paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="r1",
        market_ticker="R", side="yes", action="buy", quantity=50, limit_price_cents=47,
    )
    paper.process_open_orders(evo_session, evo_settings, md)
    v1 = paper.liquidate_agent(evo_session, agent_uuid=AU, cohort_id=1, md=md)
    assert v1 > 0
    v2 = paper.liquidate_agent(evo_session, agent_uuid=AU, cohort_id=1, md=md)
    assert v2 == 0.0  # second call is a no-op
    assert not paper.open_positions(evo_session, AU, "cohort:1")
    assert not paper.open_positions(evo_session, AU, paper.LIFETIME)


# --- listeners --------------------------------------------------------------


def test_condition_validation():
    ok = {"all": [{"metric": "yes_ask", "op": "<=", "value": 30}], "ticker": "T"}
    assert ln.validate_condition(ok) is None
    assert ln.validate_condition({"all": []})
    assert ln.validate_condition({"all": [{"metric": "nope", "op": "<", "value": 1}]})
    assert ln.validate_condition(
        {"all": [{"metric": "yes_ask", "op": "~", "value": 1}]})
    assert ln.validate_condition(
        {"all": [{"metric": "yes_ask", "op": "<", "value": 1}] * 9})
    assert ln.validate_condition(
        {"any": [{"metric": "delta:mid:600", "op": ">", "value": 5}]}) is None
    assert ln.validate_condition({"any": [{"metric": "new_market:KXBTC"}]}) is None


def _mk_listener(session, settings, condition, effect="event", cooldown=60):
    row, err = ln.create_listener(
        session, settings, owner_uuid=AU, name="l1", condition=condition,
        effect=effect, cooldown_seconds=cooldown,
    )
    assert err is None, err
    return row


def test_listener_fires_dedupes_and_cools_down(evo_session, evo_settings):
    md = StaticMarketData()
    md.set_quote(_quote(ticker="T1", yes_bid=20, no_bid=75))  # ask 25
    listener = _mk_listener(
        evo_session, evo_settings,
        {"all": [{"metric": "yes_ask", "op": "<=", "value": 30}], "ticker": "T1"},
    )
    now = datetime.now(timezone.utc)
    ctx = ln.ListenerContext(evo_session, evo_settings, md, now=now)
    counts = ln.evaluate_all(ctx)
    assert counts["fired"] == 1 and listener.trigger_count == 1
    # same minute => dedup; within cooldown => cooled
    counts = ln.evaluate_all(ln.ListenerContext(evo_session, evo_settings, md, now=now))
    assert counts["fired"] == 0
    later = now + timedelta(seconds=listener.cooldown_seconds + 61)
    counts = ln.evaluate_all(ln.ListenerContext(evo_session, evo_settings, md, now=later))
    assert counts["fired"] == 1


def test_listener_fails_closed_on_missing_data(evo_session, evo_settings):
    md = StaticMarketData()  # no quote at all
    _mk_listener(
        evo_session, evo_settings,
        {"all": [{"metric": "yes_ask", "op": "<=", "value": 99}], "ticker": "MISSING"},
    )
    counts = ln.evaluate_all(ln.ListenerContext(evo_session, evo_settings, md))
    assert counts["fired"] == 0


def test_listener_opportunity_effect(evo_session, evo_settings):
    md = StaticMarketData()
    md.set_quote(_quote(ticker="T1"))
    _mk_listener(
        evo_session, evo_settings,
        {"all": [{"metric": "volume", "op": ">", "value": 1}], "ticker": "T1"},
        effect="opportunity",
    )
    ln.evaluate_all(ln.ListenerContext(evo_session, evo_settings, md))
    opp = evo_session.scalar(select(em.EvoOpportunity))
    assert opp is not None and opp.source == "listener" and opp.market_ticker == "T1"


def test_listener_cap_and_ownership(evo_session, evo_settings):
    cond = {"all": [{"metric": "volume", "op": ">", "value": 0}], "ticker": "T"}
    for _ in range(evo_settings.max_listeners_per_agent):
        _mk_listener(evo_session, evo_settings, cond)
    row, err = ln.create_listener(
        evo_session, evo_settings, owner_uuid=AU, name="over", condition=cond,
    )
    assert row is None and "cap" in err
    first = evo_session.scalar(select(em.EvoListener))
    row, err = ln.remove_listener(evo_session, "b" * 36, first.id)
    assert row is None and "another agent" in err


# --- strategy DSL -----------------------------------------------------------


BASE_SPEC = {
    "name": "test_edge",
    "family": "testfam",
    "universe": {"series_prefixes": ["T"], "min_volume": 10, "max_spread_cents": 15,
                 "min_hours_to_close": 0.1, "max_hours_to_close": 500},
    "entry": {"side": "yes", "style": "taker", "min_price_cents": 20,
              "max_price_cents": 80, "size_contracts": 5},
    "exit": {"mode": "settlement"},
    "risk": {"max_concurrent_positions": 5, "max_per_event": 1,
             "max_cost_per_position_usd": 50},
}


def test_spec_validation():
    spec, err = validate_spec(BASE_SPEC)
    assert err is None and spec.name == "test_edge"
    bad = {**BASE_SPEC, "entry": {**BASE_SPEC["entry"], "side": "maybe"}}
    assert validate_spec(bad)[1] is not None
    bad = {**BASE_SPEC, "surprise_field": 1}
    assert validate_spec(bad)[1] is not None
    assert validate_spec(BASE_SPEC, max_bytes=10)[1] is not None


def test_spec_validation_hints_field_misplaced_on_wrong_section():
    """Regression for a live incident: agents repeatedly submitted
    max_spread_cents under "entry" (it's a universe field) and got a bare
    pydantic 'extra_forbidden' rejection with no pointer to the fix. Same for
    the reverse (entry's max_price_cents submitted under "universe")."""
    bad = {**BASE_SPEC, "entry": {**BASE_SPEC["entry"], "max_spread_cents": 10}}
    _, err = validate_spec(bad)
    assert err is not None
    assert "'max_spread_cents' belongs under 'universe', not 'entry'" in err

    bad = {**BASE_SPEC, "universe": {**BASE_SPEC["universe"], "max_price_cents": 80}}
    _, err = validate_spec(bad)
    assert "'max_price_cents' belongs under 'entry', not 'universe'" in err


def test_spec_validation_hints_section_nested_one_level_too_deep():
    """Regression for a live incident: an agent nested {"universe": {...}} as a
    field INSIDE "entry" instead of as its own top-level key next to it."""
    bad = {**BASE_SPEC, "entry": {**BASE_SPEC["entry"], "universe": {"series_prefixes": ["X"]}}}
    _, err = validate_spec(bad)
    assert err is not None
    assert "'universe' is a top-level spec section" in err
    assert "not nested inside it" in err


def test_spec_validation_no_bogus_hint_for_genuinely_unknown_field():
    """A field that doesn't exist anywhere in the schema (e.g. min_open_interest,
    seen live repeatedly — there's no bare open_interest field, only the
    entry.conditions metric) should NOT get a fabricated 'belongs under X' hint."""
    bad = {**BASE_SPEC, "universe": {**BASE_SPEC["universe"], "min_open_interest": 500}}
    _, err = validate_spec(bad)
    assert err is not None
    assert "belongs under" not in err
    assert "top-level spec section" not in err


def test_entry_signal_gates():
    spec, _ = validate_spec(BASE_SPEC)
    assert entry_signal(spec, _quote()) is not None
    assert entry_signal(spec, _quote(ticker="OTHER")) is None  # universe
    assert entry_signal(spec, _quote(volume=1)) is None  # volume floor
    assert entry_signal(spec, _quote(yes_bid=10, no_bid=60)) is None  # spread 30 > 15
    assert entry_signal(spec, _quote(hours=0.01)) is None  # too close
    assert entry_signal(spec, _quote(no_bid=10)) is None  # price 90 > band
    intent = entry_signal(spec, _quote())
    assert intent == {"side": "yes", "style": "taker", "limit_price_cents": 47,
                      "quantity": 5}


def test_maker_entry_rests_inside_bid():
    spec, _ = validate_spec({**BASE_SPEC, "entry": {**BASE_SPEC["entry"],
                                                    "style": "maker",
                                                    "maker_offset_cents": 1}})
    intent = entry_signal(spec, _quote(yes_bid=44, no_bid=53))  # ask 47
    assert intent["style"] == "maker" and intent["limit_price_cents"] == 45


def test_exit_signal_modes():
    spec, _ = validate_spec({**BASE_SPEC, "exit": {"mode": "tp_sl",
                                                   "take_profit_cents": 10,
                                                   "stop_loss_cents": 8}})
    q = _quote(yes_bid=60, no_bid=39)
    assert exit_signal(spec, q, side="yes", entry_price_cents=45, held_hours=1) == "tp"
    assert exit_signal(spec, q, side="yes", entry_price_cents=70, held_hours=1) == "sl"
    assert exit_signal(spec, q, side="yes", entry_price_cents=55, held_hours=1) is None
    timed, _ = validate_spec({**BASE_SPEC, "exit": {"mode": "timed", "max_hold_hours": 2}})
    assert exit_signal(timed, q, side="yes", entry_price_cents=45, held_hours=3) == "timed"


# --- sandbox ----------------------------------------------------------------


def _seed_backfill(session, n=6):
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for i in range(n):
        result = "yes" if i % 2 == 0 else "no"
        ticker = f"TBACK-26MAY{i:02d}-B50"
        session.add(BackfillWeatherMarket(
            market_ticker=ticker, result=result, candles_fetched=True,
            target_date=f"2026-05-{i + 1:02d}", close_time=base + timedelta(days=i, hours=12),
        ))
        for h in range(6):
            session.add(BackfillWeatherCandle(
                market_ticker=ticker,
                end_period_ts=base + timedelta(days=i, hours=h),
                period_minutes=60, price_close=45 + h,
                yes_bid_close=44 + h, yes_ask_close=46 + h, volume=100,
            ))
    session.flush()


def test_backtest_runs_and_charges_budget(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    _seed_backfill(evo_session)
    spec = {**BASE_SPEC, "universe": {**BASE_SPEC["universe"],
                                      "series_prefixes": ["TBACK"]}}
    result, err = sandbox.run_backtest(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        spec_doc=spec,
    )
    assert err is None
    assert result["n_trades"] == 6 and result["wins"] == 3
    assert result["provenance"] == "kalshi_rest_backfill"
    run = evo_session.scalar(select(em.EvoSandboxRun))
    assert run.kind == "backtest" and run.result_json["n_trades"] == 6
    from kalshi_bot.evo import budgets

    used = evo_settings.weekly_sandbox_runs - budgets.remaining(
        evo_session, agent.agent_uuid, cohort.id, "sandbox_runs"
    )
    assert used == 1


def _seed_mmsell(session):
    """Two settled mmsell markets, each with a captured orderbook tick path. mmsell trades
    the NO side, and resolved_value is that HELD side's settlement value: rv=100 -> NO paid
    out -> market resolved NO; rv=0 -> market resolved YES."""
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    for i, resolved in enumerate((100, 0)):  # market0 -> NO, market1 -> YES
        ticker = f"KXBTCD-26JUN0{i}-T60000"
        session.add(PaperTrade(
            market_ticker=ticker, strategy="mmsell", status="settled",
            assumed_price=45, resolved_value=resolved, side="no", quantity=5,
            created_at=base + timedelta(days=i),
            closed_at=base + timedelta(days=i, hours=2),
        ))
        for h in range(3):
            session.add(MmSellPositionTick(
                market_ticker=ticker,
                captured_at=base + timedelta(days=i, minutes=h * 20),
                yes_bid=40, yes_ask=45, no_bid=55, no_ask=60, mid=42.5, volume=100,
            ))
    session.flush()


def test_mmsell_backtest_replays_ticks_to_settlement(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    _seed_mmsell(evo_session)
    spec = {**BASE_SPEC, "universe": {**BASE_SPEC["universe"],
                                      "series_prefixes": ["KXBTCD"]}}
    result, err = sandbox.run_backtest(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        spec_doc=spec, dataset="mmsell",
    )
    assert err is None
    # one trade per settled market, held to settlement. Spec buys YES; only the market that
    # resolved YES (the NO-side rv=0 one) wins — proving side-adjusted settlement.
    assert result["n_trades"] == 2 and result["wins"] == 1
    assert result["dataset"] == "mmsell"
    assert result["provenance"] == "mmsell_live_ticks"
    run = evo_session.scalar(
        select(em.EvoSandboxRun).where(em.EvoSandboxRun.dataset == "mmsell")
    )
    assert run is not None and run.provenance == "mmsell_live_ticks"


def test_backtest_persist_false_returns_result_without_writing(
    evo_session, evo_settings, evo_agent
):
    agent, cohort = evo_agent
    _seed_mmsell(evo_session)
    spec = {**BASE_SPEC, "universe": {**BASE_SPEC["universe"],
                                      "series_prefixes": ["KXBTCD"]}}
    result, err = sandbox.run_backtest(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        spec_doc=spec, dataset="mmsell", charge_budget=False, persist=False,
    )
    assert err is None and result["n_trades"] == 2  # replay still runs
    # ...but nothing is persisted (safe on a read-only DB, as the ops probe uses it)
    assert evo_session.scalar(
        select(em.EvoSandboxRun).where(em.EvoSandboxRun.dataset == "mmsell")
    ) is None


def _seed_crypto(session):
    """Two crypto ladder markets + a spot path; outcome is DERIVED from spot vs strike.
    Spot at close = 65000: T64000 (greater) -> YES, T66000 (greater) -> NO."""
    close_t = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
    for m in (-2, -1, 0):
        session.add(CryptoSpotCandle(
            product="BTC-USD", minute_ts=close_t + timedelta(minutes=m), close=65000.0))
    for ticker, floor in (("KXBTCD-26JUL2020-T64000", 64000.0),
                          ("KXBTCD-26JUL2020-T66000", 66000.0)):
        for mins in (60, 30, 0):
            session.add(CryptoLadderSnapshot(
                series="KXBTCD", event_ticker="KXBTCD-26JUL2020", market_ticker=ticker,
                captured_at=close_t - timedelta(minutes=mins), strike_type="greater",
                floor_strike=floor, cap_strike=None, yes_bid_cents=48.0, yes_ask_cents=52.0,
                mid_cents=50.0, volume=100.0, minutes_to_close=float(mins),
            ))
    session.flush()


def test_settle_crypto_rules():
    from kalshi_bot.evo.sandbox import _settle_crypto
    assert _settle_crypto("greater", 64000, None, 65000) == "yes"
    assert _settle_crypto("greater", 66000, None, 65000) == "no"
    assert _settle_crypto("less", None, 66000, 65000) == "yes"
    assert _settle_crypto("less", None, 64000, 65000) == "no"
    assert _settle_crypto("between", 64000, 66000, 65000) == "yes"
    assert _settle_crypto("between", 64000, 64900, 65000) == "no"
    assert _settle_crypto("greater", 64000, None, None) is None  # no spot
    assert _settle_crypto("greater", None, None, 65000) is None  # no strike


def test_crypto_backtest_derives_settlement_from_spot(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    _seed_crypto(evo_session)
    spec = {**BASE_SPEC, "universe": {**BASE_SPEC["universe"],
                                      "series_prefixes": ["KXBTCD"]}}
    result, err = sandbox.run_backtest(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        spec_doc=spec, dataset="crypto",
    )
    assert err is None
    # spec buys YES; only the T64000 market (spot 65000 > 64000 -> YES) wins
    assert result["n_trades"] == 2 and result["wins"] == 1
    assert result["dataset"] == "crypto"
    assert result["provenance"] == "crypto_ladder_spot_settled"


def test_market_result_from_trade_is_side_adjusted():
    # resolved_value is the HELD side's settlement value, not the market's YES value.
    from kalshi_bot.evo.sandbox import _market_result_from_trade
    assert _market_result_from_trade("no", 100) == "no"    # NO paid out -> market NO
    assert _market_result_from_trade("no", 0) == "yes"     # NO lost -> market YES
    assert _market_result_from_trade("yes", 100) == "yes"
    assert _market_result_from_trade("yes", 0) == "no"
    assert _market_result_from_trade(None, 100) is None    # unknown side -> undecidable
    assert _market_result_from_trade("no", 50) is None     # non-terminal value


def test_backtest_rejects_unknown_dataset(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    result, err = sandbox.run_backtest(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        spec_doc=BASE_SPEC, dataset="bogus",
    )
    assert result is None and err is not None and "unknown dataset" in err


def test_walkforward_split(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    _seed_backfill(evo_session)
    spec = {**BASE_SPEC, "universe": {**BASE_SPEC["universe"],
                                      "series_prefixes": ["TBACK"]}}
    result, err = sandbox.run_walkforward(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        spec_doc=spec, split_date="2026-05-04",
    )
    assert err is None
    assert result["in_sample"]["n_trades"] + result["out_of_sample"]["n_trades"] >= 6


def test_strategy_lifecycle(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    row, err = sandbox.save_strategy(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, spec_doc=BASE_SPEC,
    )
    assert err is None and row.revision == 1 and row.status == "validated"
    row2, _ = sandbox.save_strategy(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, spec_doc=BASE_SPEC,
    )
    assert row2.revision == 2
    active, err = sandbox.activate_strategy(evo_session, agent.agent_uuid, row.id)
    assert err is None and active.status == "active"
    active2, err = sandbox.activate_strategy(evo_session, agent.agent_uuid, row2.id)
    assert err is None
    evo_session.refresh(row)
    assert row.status == "inactive"  # sibling deactivated
    other, err = sandbox.activate_strategy(evo_session, "b" * 36, row.id)
    assert other is None and "another agent" in err


# --- strategy runner --------------------------------------------------------


def test_strategy_runner_entry_dedup_and_exante_opportunities(
    evo_session, evo_settings, evo_agent
):
    agent, cohort = evo_agent
    spec = {**BASE_SPEC, "universe": {**BASE_SPEC["universe"], "series_prefixes": ["T"]}}
    row, _ = sandbox.save_strategy(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, spec_doc=spec,
    )
    sandbox.activate_strategy(evo_session, agent.agent_uuid, row.id)
    md = StaticMarketData()
    md.set_quote(_quote(ticker="T1"))
    counts = strategy_runner.run_cycle(
        evo_session, evo_settings, md, cohort_id=cohort.id, candidate_tickers=["T1"],
    )
    assert counts["orders"] == 1
    opp = evo_session.scalar(
        select(em.EvoOpportunity).where(em.EvoOpportunity.agent_uuid == agent.agent_uuid)
    )
    assert opp is not None and opp.acted
    # same day, second cycle: opportunity + order deduped
    counts = strategy_runner.run_cycle(
        evo_session, evo_settings, md, cohort_id=cohort.id, candidate_tickers=["T1"],
    )
    assert counts["orders"] == 0
    orders = list(
        evo_session.scalars(
            select(em.EvoOrder).where(em.EvoOrder.agent_uuid == agent.agent_uuid)
        )
    )
    assert len(orders) == 1


def test_strategy_runner_tp_exit_once(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    spec = {**BASE_SPEC,
            "exit": {"mode": "tp_sl", "take_profit_cents": 5}}
    row, _ = sandbox.save_strategy(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, spec_doc=spec,
    )
    sandbox.activate_strategy(evo_session, agent.agent_uuid, row.id)
    md = StaticMarketData()
    md.set_quote(_quote(ticker="T1", no_levels=[(53, 100)]))  # entry at 47
    strategy_runner.run_cycle(
        evo_session, evo_settings, md, cohort_id=cohort.id, candidate_tickers=["T1"],
    )
    paper.process_open_orders(evo_session, evo_settings, md)
    # price rallies: bid 55 >= entry 47 + tp 5
    md.set_quote(_quote(ticker="T1", yes_bid=55, no_bid=42, yes_levels=[(55, 100)],
                        no_levels=[(42, 100)]))
    counts = strategy_runner.run_cycle(
        evo_session, evo_settings, md, cohort_id=cohort.id, candidate_tickers=[],
    )
    assert counts["exits"] == 1
    counts = strategy_runner.run_cycle(
        evo_session, evo_settings, md, cohort_id=cohort.id, candidate_tickers=[],
    )
    assert counts["exits"] == 0  # idempotent
    paper.process_open_orders(evo_session, evo_settings, md)
    pos = evo_session.scalar(
        select(em.EvoPosition).where(
            em.EvoPosition.ledger == f"cohort:{cohort.id}",
            em.EvoPosition.market_ticker == "T1",
        )
    )
    assert pos.status == "closed" and float(pos.realized_pnl_usd) > 0


# The EXACT orderbook payload the live Kalshi elections API returned for
# KXHIGHNY-26JUL27-B85.5, captured via scripts/evo_order_probe.py.
_LIVE_ORDERBOOK_B85_5 = {
    "orderbook_fp": {
        "no_dollars": [["0.8100", "1.00"], ["0.8400", "53.00"], ["0.8800", "10.05"],
                       ["0.9200", "129.00"], ["0.9300", "52.00"]],
        "yes_dollars": [["0.0100", "1530.00"], ["0.0600", "6.00"]],
    }
}


def test_live_orderbook_shape_fills_a_marketable_order(evo_session, evo_settings):
    """End-to-end guard on the real incident: order 38 was a taker buy of 10 YES
    at a 7c limit while the live book had 52 contracts available at exactly 7c
    (no_bid 93 -> taker cost 100-93), and it sat unfilled for hours. The parser
    read an "orderbook"/"yes"/"no" shape the API no longer sends, so every level
    list was empty and evaluate_order could never fill anything at any price.
    This drives the production fill path with the captured payload."""
    _fund(evo_session, evo_settings)
    market = {"ticker": "KXHIGHNY-26JUL27-B85.5", "status": "active",
              "yes_bid_dollars": "0.0600", "yes_ask_dollars": "0.0700",
              "no_bid_dollars": "0.9300",
              "close_time": (NOW + timedelta(hours=5)).isoformat().replace("+00:00", "Z")}
    quote = quote_from_kalshi(market, _LIVE_ORDERBOOK_B85_5)

    order, err = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="live-shape",
        market_ticker=market["ticker"], side="yes", action="buy", quantity=10,
        style="taker", limit_price_cents=7,
    )
    assert order is not None, err

    status = paper.evaluate_order(evo_session, evo_settings, order, quote)
    assert status == "filled", f"marketable order did not fill: {status}"
    assert order.filled_quantity == 10
    fill = evo_session.scalar(
        select(em.EvoFill).where(em.EvoFill.order_id == order.id)
    )
    assert fill is not None and fill.price_cents == 7  # 100 - 93c no_bid


def test_one_lot_maker_order_fills_on_trade_through(evo_session, evo_settings):
    """Regression: the adverse-selection haircut used int(), so a 1-contract
    maker order computed int(1 * 0.75) == 0 and hit the `qty <= 0` early return —
    unfillable BY CONSTRUCTION no matter how far the market traded through it.
    Found live: order 44 rested at a 51c limit while the market taker cost was
    22c (a 29c trade-through) and still never filled. Agents place 1-lots
    routinely, so this silently swallowed a whole class of orders."""
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    order, err = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="m-onelot",
        market_ticker="T1", side="yes", action="buy", quantity=1, style="maker",
        limit_price_cents=51,
    )
    assert order is not None, err

    # market taker cost for YES = 100 - no_bid = 22c, far below the 51c limit
    md.set_quote(_quote(yes_bid=20, no_bid=78))
    paper.process_open_orders(evo_session, evo_settings, md)
    assert order.status == "filled" and order.filled_quantity == 1
    fill = evo_session.scalar(select(em.EvoFill).where(em.EvoFill.order_id == order.id))
    assert fill is not None and fill.liquidity == "maker" and fill.price_cents == 51


def test_maker_haircut_still_scales_larger_orders(evo_session, evo_settings):
    """The 1-lot floor must not disable the haircut itself: a 10-lot still takes
    the full 25% adverse-selection cut (10 -> 7), unchanged."""
    _fund(evo_session, evo_settings)
    md = StaticMarketData()
    order, _ = paper.place_order(
        evo_session, evo_settings, agent_uuid=AU, cohort_id=1, idem_key="m-tenlot",
        market_ticker="T1", side="yes", action="buy", quantity=10, style="maker",
        limit_price_cents=51,
    )
    md.set_quote(_quote(yes_bid=20, no_bid=78))
    paper.process_open_orders(evo_session, evo_settings, md)
    assert order.filled_quantity == 7
