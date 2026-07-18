"""Deterministic execution of ACTIVE declarative strategies each orchestrator
cycle (no LLM involved): entry signals -> ex-ante opportunity records -> orders,
and non-settlement exits. Heartbeats decide WHAT to run; this layer runs it
continuously between heartbeats, using the exact same interpreter as backtests.

Ex-ante honesty (spec §23.4): every admitted entry signal writes an
evo_opportunities row whether or not an order is ultimately placed — an agent
cannot retroactively call an unwanted signal invalid."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from . import paper as papermod
from .config import EvoSettings
from .marketdata import MarketData
from .models import EvoAgent, EvoOpportunity, EvoOrder, EvoStrategy
from .strategy_spec import StrategySpec, entry_signal, exit_signal, validate_spec

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day() -> str:
    return _now().strftime("%Y%m%d")


def _record_opportunity(
    session, agent_uuid: str, cohort_id: int, ticker: str, strategy: EvoStrategy
) -> EvoOpportunity | None:
    dedup = f"strat:{strategy.id}:{ticker}:{_day()}"
    existing = session.scalar(
        select(EvoOpportunity).where(
            EvoOpportunity.agent_uuid == agent_uuid, EvoOpportunity.dedup_key == dedup
        )
    )
    if existing is not None:
        return None  # already recorded today
    opp = EvoOpportunity(
        agent_uuid=agent_uuid,
        cohort_id=cohort_id,
        dedup_key=dedup,
        market_ticker=ticker,
        source="strategy",
        detail_json={"strategy": strategy.name, "revision": strategy.revision},
    )
    session.add(opp)
    session.flush()
    return opp


def run_cycle(
    session,
    settings: EvoSettings,
    md: MarketData,
    *,
    cohort_id: int,
    candidate_tickers: list[str],
    max_entries_per_agent: int = 5,
) -> dict[str, int]:
    """One pass: for every active agent's active strategies, evaluate entries over
    the candidate tickers and exits over open positions."""
    counts = {"strategies": 0, "signals": 0, "orders": 0, "exits": 0, "blocked": 0}
    active_agents = {
        a.agent_uuid: a
        for a in session.scalars(select(EvoAgent).where(EvoAgent.status == "active"))
    }
    strategies = list(
        session.scalars(select(EvoStrategy).where(EvoStrategy.status == "active"))
    )
    ledger_by_agent = {au: papermod.cohort_ledger(cohort_id) for au in active_agents}
    for strategy in strategies:
        au = strategy.agent_uuid
        if au not in active_agents:
            continue
        spec, err = validate_spec(strategy.spec_json)
        if err:
            continue
        counts["strategies"] += 1
        entries_this_cycle = 0
        open_pos = papermod.open_positions(session, au, ledger_by_agent[au])
        open_by_ticker = {(p.market_ticker, p.side) for p in open_pos}
        open_events = {p.market_ticker.rsplit("-", 1)[0] for p in open_pos}
        for ticker in candidate_tickers:
            if entries_this_cycle >= max_entries_per_agent:
                break
            if not spec.universe.admits_ticker(ticker):
                continue
            quote = md.get_quote(ticker)
            if quote is None or quote.age_seconds() > settings.stale_data_seconds:
                continue
            intent = entry_signal(spec, quote)
            if intent is None:
                continue
            counts["signals"] += 1
            opp = _record_opportunity(session, au, cohort_id, ticker, strategy)
            if opp is None:
                continue  # one opportunity (and at most one entry) per strategy/ticker/day
            if (ticker, intent["side"]) in open_by_ticker:
                opp.no_trade_reason = "already holding this side"
                counts["blocked"] += 1
                continue
            if len(open_pos) >= spec.risk.max_concurrent_positions:
                opp.no_trade_reason = "max concurrent positions"
                counts["blocked"] += 1
                continue
            event = ticker.rsplit("-", 1)[0]
            per_event = sum(1 for e in open_events if e == event)
            if per_event >= spec.risk.max_per_event:
                opp.no_trade_reason = "max per event"
                counts["blocked"] += 1
                continue
            order, err = papermod.place_order(
                session, settings,
                agent_uuid=au,
                cohort_id=cohort_id,
                idem_key=f"strat:{strategy.id}:{ticker}:{intent['side']}:{_day()}",
                market_ticker=ticker,
                side=intent["side"],
                action="buy",
                quantity=int(intent["quantity"]),
                style=intent["style"],
                limit_price_cents=intent["limit_price_cents"],
                strategy_id=strategy.id,
                opportunity_id=opp.id,
                intent={"strategy": strategy.name, "revision": strategy.revision},
                max_position_cost_usd=spec.risk.max_cost_per_position_usd,
            )
            if order is not None:
                opp.acted = True
                counts["orders"] += 1
                entries_this_cycle += 1
            else:
                opp.no_trade_reason = (err or "order rejected")[:200]
                counts["blocked"] += 1
        # exits for non-settlement modes
        if spec.exit.mode != "settlement":
            for pos in open_pos:
                quote = md.get_quote(pos.market_ticker)
                if quote is None or quote.is_terminal():
                    continue
                opened = pos.opened_at if pos.opened_at.tzinfo else pos.opened_at.replace(
                    tzinfo=timezone.utc
                )
                held_hours = (_now() - opened).total_seconds() / 3600.0
                reason = exit_signal(
                    spec, quote, side=pos.side,
                    entry_price_cents=float(pos.avg_price_cents), held_hours=held_hours,
                )
                if reason is None:
                    continue
                qty = int(float(pos.quantity))
                if qty <= 0:
                    continue
                exit_exists = session.scalar(
                    select(EvoOrder.id).where(
                        EvoOrder.idem_key
                        == f"exit:{strategy.id}:{pos.id}:{reason}"
                    )
                )
                if exit_exists:
                    continue
                order, err = papermod.place_order(
                    session, settings,
                    agent_uuid=au, cohort_id=cohort_id,
                    idem_key=f"exit:{strategy.id}:{pos.id}:{reason}",
                    market_ticker=pos.market_ticker, side=pos.side, action="sell",
                    quantity=qty, style="taker",
                    strategy_id=strategy.id,
                    intent={"exit_reason": reason},
                )
                if order is not None:
                    counts["exits"] += 1
    return counts
