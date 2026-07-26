"""End-to-end pipeline tests for the evolutionary agent system.

These are the "does the whole machine actually turn" tests the operator asked
for: drive a real agent decision through the production heartbeat lifecycle
(context assembly -> cognition -> server-side action execution) and confirm the
full paper-trading pipeline behind it — order -> fill -> position -> settlement
with the exact realized P&L — plus the strategy path (agent saves + activates a
strategy, the strategy runner then trades it). The only seam replaced is the LLM
itself (ScriptedCognition supplies the agent's decided actions); everything below
that — budgets, action dispatch, place_order, the fill simulator, settlement — is
the same code the live evo worker runs. So a green run here means an agent CAN
take a trade and run a strategy end to end in production.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot.evo import models as em
from kalshi_bot.evo import paper, strategy_runner
from kalshi_bot.evo.cognition import ScriptedCognition
from kalshi_bot.evo.heartbeats import run_heartbeat
from kalshi_bot.evo.marketdata import Quote, StaticMarketData

NOW = datetime.now(timezone.utc)


def _live_quote(ticker, *, yes_bid=44, no_bid=53, depth=100, hours=5.0):
    """A tradable, two-sided quote. A taker buy of YES lifts the NO bids, so it
    fills at 100 - no_bid cents (=47 with the defaults)."""
    return Quote(
        ticker=ticker, captured_at=NOW, status="active",
        yes_bid=yes_bid, yes_ask=100 - no_bid, no_bid=no_bid, no_ask=100 - yes_bid,
        yes_levels=[(yes_bid, depth)], no_levels=[(no_bid, depth)],
        volume=500, open_interest=1000,
        close_time=NOW + timedelta(hours=hours),
    )


def _script(actions):
    """A ScriptedCognition callable that emits a fixed action list once."""
    def script(ctx):
        return {"journal": {"decision": "e2e", "confidence": 0.6}, "actions": actions}
    return ScriptedCognition(script)


# --- full agent-driven trade lifecycle -------------------------------------


def test_agent_heartbeat_trade_to_settlement_end_to_end(evo_session, evo_settings, evo_agent):
    """An agent decides to trade in a real heartbeat; the intent becomes an order,
    the order fills, a position opens, the market settles YES, and realized P&L is
    booked at exactly qty*(100-entry)/100."""
    agent, cohort = evo_agent
    ticker = "KXHIGHNY-26JUL24-B81.5"
    md = StaticMarketData()
    md.set_quote(_live_quote(ticker))

    # 1) The agent's decision drives the production heartbeat: assemble_context ->
    #    cognition -> execute_actions -> paper.place_order.
    hb = run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="e2e-trade", md=md,
        cognition=_script([{
            "type": "submit_trade_intent", "market_ticker": ticker,
            "side": "yes", "action": "buy", "quantity": 10, "style": "taker",
            "limit_price_cents": 55, "thesis": "e2e smoke",
        }]),
    )
    assert hb is not None and hb.status == "completed"
    intent = next(o for o in hb.actions_json if o["type"] == "submit_trade_intent")
    assert intent["ok"] is True and isinstance(intent["order_id"], int)

    order = evo_session.get(em.EvoOrder, intent["order_id"])
    assert order is not None and order.market_ticker == ticker and order.status == "open"

    # 2) The fill simulator fills the resting taker order.
    paper.process_open_orders(evo_session, evo_settings, md)
    evo_session.refresh(order)
    assert order.status == "filled"

    pos = evo_session.scalar(
        select(em.EvoPosition).where(
            em.EvoPosition.market_ticker == ticker,
            em.EvoPosition.ledger == f"cohort:{cohort.id}",
        )
    )
    assert pos is not None and pos.quantity == 10 and float(pos.avg_price_cents) == 47.0

    # 3) The market settles YES; the agent held YES, so it pays 100c/contract.
    md.set_quote(Quote(ticker=ticker, captured_at=NOW, status="settled", result="yes"))
    pf_before = float(paper.get_portfolio(evo_session, agent.agent_uuid,
                                          f"cohort:{cohort.id}").realized_pnl_usd)
    counts = paper.mark_and_settle(evo_session, md)
    assert counts["settled"] >= 2  # settles in BOTH the cohort and lifetime ledgers

    evo_session.refresh(pos)
    assert pos.status == "settled" and pos.resolved_value_cents == 100
    # realized = qty * (value_cents - avg_price_cents) / 100 = 10 * (100-47)/100
    assert abs(float(pos.realized_pnl_usd) - 5.30) < 1e-9
    pf_after = float(paper.get_portfolio(evo_session, agent.agent_uuid,
                                         f"cohort:{cohort.id}").realized_pnl_usd)
    assert abs((pf_after - pf_before) - 5.30) < 1e-9


def test_agent_heartbeat_cancel_order_echoes_order_id(evo_session, evo_settings, evo_agent):
    """A canceled order must be traceable: the cancel_order action outcome echoes
    the order_id it acted on (regression guard for the bare {"ok": true} bug that
    made reconstructing what an agent did require correlating timestamps by hand)."""
    agent, cohort = evo_agent
    ticker = "KXHIGHNY-26JUL24-B81.5"
    md = StaticMarketData()
    md.set_quote(_live_quote(ticker))

    hb1 = run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="e2e-cancel-place", md=md,
        cognition=_script([{
            "type": "submit_trade_intent", "market_ticker": ticker,
            "side": "yes", "action": "buy", "quantity": 5, "style": "maker",
            "limit_price_cents": 30, "thesis": "resting maker to cancel",
        }]),
    )
    order_id = next(o for o in hb1.actions_json
                    if o["type"] == "submit_trade_intent")["order_id"]

    hb2 = run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="e2e-cancel-do", md=md,
        cognition=_script([{"type": "cancel_order", "order_id": order_id}]),
    )
    cancel = next(o for o in hb2.actions_json if o["type"] == "cancel_order")
    assert cancel["ok"] is True and cancel["order_id"] == order_id

    order = evo_session.get(em.EvoOrder, order_id)
    assert order.status == "canceled"


# --- full agent-driven strategy lifecycle ----------------------------------


STRAT_SPEC = {
    "name": "e2e_edge",
    "family": "e2efam",
    "universe": {"series_prefixes": ["T"], "min_volume": 10, "max_spread_cents": 15,
                 "min_hours_to_close": 0.1, "max_hours_to_close": 500},
    "entry": {"side": "yes", "style": "taker", "min_price_cents": 20,
              "max_price_cents": 80, "size_contracts": 5},
    "exit": {"mode": "settlement"},
    "risk": {"max_concurrent_positions": 5, "max_per_event": 1,
             "max_cost_per_position_usd": 50},
}


def test_agent_saves_activates_strategy_and_runner_trades_end_to_end(
    evo_session, evo_settings, evo_agent
):
    """An agent saves a strategy and activates it — both through real heartbeat
    actions — then the strategy runner discovers a matching live market and places
    (and fills) a trade off that strategy, with no further agent decision."""
    agent, cohort = evo_agent
    md = StaticMarketData()
    md.set_quote(_live_quote("T1"))

    # 1) Agent saves the strategy via the action path.
    hb_save = run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="e2e-save", md=md,
        cognition=_script([{"type": "save_strategy", "spec": STRAT_SPEC}]),
    )
    save = next(o for o in hb_save.actions_json if o["type"] == "save_strategy")
    assert save["ok"] is True
    strategy_id = save["strategy_id"]

    # 2) Agent activates it via the action path (id learned from the save outcome).
    hb_act = run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="e2e-activate", md=md,
        cognition=_script([{"type": "activate_strategy", "strategy_id": strategy_id}]),
    )
    act = next(o for o in hb_act.actions_json if o["type"] == "activate_strategy")
    assert act["ok"] is True
    strat = evo_session.get(em.EvoStrategy, strategy_id)
    assert strat.status == "active"

    # 3) The strategy runner (no agent in the loop) finds T1 and trades the strategy.
    counts = strategy_runner.run_cycle(
        evo_session, evo_settings, md, cohort_id=cohort.id, candidate_tickers=["T1"],
    )
    assert counts["orders"] == 1

    paper.process_open_orders(evo_session, evo_settings, md)
    order = evo_session.scalar(
        select(em.EvoOrder).where(
            em.EvoOrder.agent_uuid == agent.agent_uuid,
            em.EvoOrder.market_ticker == "T1",
        )
    )
    assert order is not None and order.status == "filled"
    pos = evo_session.scalar(
        select(em.EvoPosition).where(
            em.EvoPosition.market_ticker == "T1",
            em.EvoPosition.ledger == f"cohort:{cohort.id}",
        )
    )
    assert pos is not None and pos.quantity == 5


def test_activate_strategy_with_non_numeric_id_rejects_cleanly(
    evo_session, evo_settings, evo_agent
):
    """Regression for a live incident: an agent passed a strategy NAME
    ("energy_maker_tight_spread_v1") instead of the numeric id from a prior
    save_strategy result. The int() coercion isn't guarded here, so the bare
    ValueError escaped to the generic action-execution catch-all and surfaced
    as an opaque "internal error: ValueError: invalid literal for int() with
    base 10: ..." — the heartbeat survived (caught, not a crash) but the agent
    got no actionable signal for what it did wrong. It should reject cleanly
    with a message the agent can act on next heartbeat."""
    agent, cohort = evo_agent
    md = StaticMarketData()
    hb = run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="e2e-bad-activate", md=md,
        cognition=_script([{"type": "activate_strategy",
                             "strategy_id": "energy_maker_tight_spread_v1"}]),
    )
    act = next(o for o in hb.actions_json if o["type"] == "activate_strategy")
    assert act.get("ok") is not True
    assert "strategy_id" in act["rejected"]
    assert "internal error" not in act["rejected"]
