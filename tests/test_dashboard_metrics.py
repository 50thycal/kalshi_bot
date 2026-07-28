"""Dashboard aggregation: P&L reconciliation, win rate, best/worst trade, the trade
table, generation history and the deterministic strategy description.

Every trade here is created by driving the real paper engine (place_order ->
process_open_orders -> mark_and_settle), so the numbers under test are the numbers
the deployed worker actually writes.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kalshi_bot.dashboard import data as dash
from kalshi_bot.dashboard import metrics
from kalshi_bot.dashboard.strategy_view import describe_spec, summarize_spec
from kalshi_bot.evo import budgets, llm, paper
from kalshi_bot.evo import models as em
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.marketdata import Quote, StaticMarketData
from kalshi_bot.models import Base

NOW = datetime.now(timezone.utc)


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def _quote(ticker, *, yes_bid=44, no_bid=53, depth=100, hours=5.0, status="active", result=""):
    return Quote(
        ticker=ticker, captured_at=datetime.now(timezone.utc), status=status, result=result,
        yes_bid=yes_bid, yes_ask=100 - no_bid, no_bid=no_bid, no_ask=100 - yes_bid,
        yes_levels=[(yes_bid, depth)], no_levels=[(no_bid, depth)],
        volume=500, open_interest=1000,
        close_time=datetime.now(timezone.utc) + timedelta(hours=hours),
    )


def _fixture(n=1):
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    llm.seed_model_prices(session, settings)
    cohort = ensure_current_cohort(session, settings)
    agents = []
    for i in range(n):
        a = create_agent(session, settings, cohort, random.Random(i),
                         origin="founder", slot_key=f"founder:{i}")
        budgets.ensure_budgets(session, a.agent_uuid, cohort.id, settings)
        paper.ensure_portfolio(session, a.agent_uuid, paper.LIFETIME,
                               settings.starting_capital_usd)
        agents.append(a)
    return session, settings, cohort, agents


def _buy(session, settings, md, agent, cohort, ticker, *, qty=10, limit=47, key=None):
    order, err = paper.place_order(
        session, settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        idem_key=key or f"buy:{agent.agent_uuid[:6]}:{ticker}",
        market_ticker=ticker, side="yes", action="buy", quantity=qty,
        limit_price_cents=limit,
    )
    assert err is None, err
    paper.process_open_orders(session, settings, md)
    return order


def _settle(session, md, ticker, result):
    md.set_quote(Quote(ticker=ticker, captured_at=datetime.now(timezone.utc),
                       status="settled", result=result))
    paper.mark_and_settle(session, md)


# ---------------------------------------------------------------------------
# Reconciliation: bot totals must equal trade-level totals
# ---------------------------------------------------------------------------


def test_bot_totals_reconcile_with_trade_rows_and_the_portfolio():
    session, settings, cohort, (agent,) = _fixture()
    md = StaticMarketData()
    for t in ("WIN", "LOSE"):
        md.set_quote(_quote(t))
        _buy(session, settings, md, agent, cohort, t)
    md.set_quote(_quote("HOLD"))
    _buy(session, settings, md, agent, cohort, "HOLD")

    _settle(session, md, "WIN", "yes")
    _settle(session, md, "LOSE", "no")
    md.set_quote(_quote("HOLD", yes_bid=60, no_bid=37))
    paper.mark_and_settle(session, md)

    ledger = paper.cohort_ledger(cohort.id)
    perf = metrics.bot_performance(
        session, [agent.agent_uuid], ledger, default_capital=1000.0)[agent.agent_uuid]
    rows = metrics.trade_rows(session, agent.agent_uuid, ledger)["trades"]

    assert perf["trades_total"] == 3
    assert perf["trades_closed"] == 2 and perf["trades_open"] == 1

    # 1. the bot's realized P&L is exactly the sum of its closed trade rows
    row_realized = sum(r["realized_pnl_usd"] for r in rows if not r["is_open"])
    assert abs(perf["realized_pnl_usd"] - row_realized) < 0.01

    # 2. ...and matches what the paper engine independently booked on the portfolio
    pf = paper.get_portfolio(session, agent.agent_uuid, ledger)
    assert abs(perf["realized_pnl_usd"] - float(pf.realized_pnl_usd)) < 0.01

    # 3. unrealized is exactly the open rows
    row_unrealized = sum(r["unrealized_pnl_usd"] for r in rows if r["is_open"])
    assert abs(perf["unrealized_pnl_usd"] - row_unrealized) < 0.01

    # 4. the headline total is NAV-based and the fee residual closes the identity
    assert abs(perf["equity_usd"] - paper.nav(session, agent.agent_uuid, ledger)) < 0.01
    assert abs(
        perf["realized_pnl_usd"] + perf["unrealized_pnl_usd"]
        - perf["fee_drag_usd"] - perf["total_pnl_usd"]
    ) < 0.01
    # entry fees are real money and are never silently dropped
    assert perf["fee_drag_usd"] > 0
    assert perf["fees_usd"] > 0


def test_fleet_totals_are_the_sum_of_bot_totals():
    session, settings, cohort, agents = _fixture(n=3)
    md = StaticMarketData()
    for i, agent in enumerate(agents):
        ticker = f"M{i}"
        md.set_quote(_quote(ticker))
        _buy(session, settings, md, agent, cohort, ticker)
    _settle(session, md, "M0", "yes")
    _settle(session, md, "M1", "no")
    md.set_quote(_quote("M2", yes_bid=50, no_bid=47))
    paper.mark_and_settle(session, md)

    fleet = dash.build_fleet(session, settings)["summary"]
    bots = dash.build_bots(session, settings)["bots"]
    assert fleet["bots"] == 3
    for key in ("realized_pnl_usd", "unrealized_pnl_usd", "total_pnl_usd", "fees_usd"):
        assert abs(fleet[key] - sum(b["performance"][key] for b in bots)) < 0.01
    assert fleet["trades_closed"] == sum(b["performance"]["trades_closed"] for b in bots)
    assert fleet["wins"] == 1 and fleet["losses"] == 1
    assert fleet["win_rate"] == 50.0
    assert fleet["best_bot"]["total_pnl_usd"] >= fleet["worst_bot"]["total_pnl_usd"]


def test_the_two_ledgers_are_never_summed_together():
    """cohort:<id> and lifetime hold a mirror of the same fills. Counting both would
    double every trade — each aggregation takes exactly one ledger."""
    session, settings, cohort, (agent,) = _fixture()
    md = StaticMarketData()
    md.set_quote(_quote("T"))
    _buy(session, settings, md, agent, cohort, "T")
    _settle(session, md, "T", "yes")

    cohort_perf = metrics.bot_performance(
        session, [agent.agent_uuid], paper.cohort_ledger(cohort.id),
        default_capital=1000.0)[agent.agent_uuid]
    lifetime_perf = metrics.bot_performance(
        session, [agent.agent_uuid], paper.LIFETIME,
        default_capital=1000.0)[agent.agent_uuid]
    assert cohort_perf["trades_closed"] == 1
    assert lifetime_perf["trades_closed"] == 1
    total_rows = session.scalar(select(em.EvoPosition).where(em.EvoPosition.status == "settled"))
    assert total_rows is not None
    # the detail view exposes both, labelled, rather than adding them up
    detail = dash.build_bot_detail(session, settings, agent.agent_uuid)
    assert detail["performance"]["trades_closed"] == 1
    assert detail["lifetime_performance"]["trades_closed"] == 1
    assert detail["ledger"] == paper.cohort_ledger(cohort.id)


# ---------------------------------------------------------------------------
# Win rate / best / worst
# ---------------------------------------------------------------------------


def test_win_rate_excludes_open_trades_and_best_worst_come_from_closed_only():
    session, settings, cohort, (agent,) = _fixture()
    md = StaticMarketData()
    # two winners at different sizes, one loser, one still open
    for ticker, qty in (("BIGWIN", 30), ("SMALLWIN", 5), ("LOSS", 20), ("OPEN", 10)):
        md.set_quote(_quote(ticker))
        _buy(session, settings, md, agent, cohort, ticker, qty=qty)
    _settle(session, md, "BIGWIN", "yes")
    _settle(session, md, "SMALLWIN", "yes")
    _settle(session, md, "LOSS", "no")
    md.set_quote(_quote("OPEN", yes_bid=44, no_bid=53))
    paper.mark_and_settle(session, md)

    ledger = paper.cohort_ledger(cohort.id)
    perf = metrics.bot_performance(
        session, [agent.agent_uuid], ledger, default_capital=1000.0)[agent.agent_uuid]

    assert perf["trades_closed"] == 3 and perf["trades_open"] == 1
    assert perf["wins"] == 2 and perf["losses"] == 1
    # 2 of 3 CLOSED trades won — the open one is not counted either way
    assert perf["win_rate"] == round(2 / 3 * 100, 1)
    assert perf["best_trade"]["market"] == "BIGWIN"
    assert perf["worst_trade"]["market"] == "LOSS"
    assert perf["best_trade"]["realized_pnl_usd"] > 0
    assert perf["worst_trade"]["realized_pnl_usd"] < 0
    # avg/median are over closed trades only
    assert abs(perf["avg_trade_usd"] - perf["realized_pnl_usd"] / 3) < 0.01
    assert perf["median_trade_usd"] is not None
    assert perf["avg_hold_hours"] is not None


def test_a_voided_market_is_a_breakeven_trade_not_a_win():
    session, settings, cohort, (agent,) = _fixture()
    md = StaticMarketData()
    md.set_quote(_quote("VOID"))
    _buy(session, settings, md, agent, cohort, "VOID")
    _settle(session, md, "VOID", "void")

    perf = metrics.bot_performance(
        session, [agent.agent_uuid], paper.cohort_ledger(cohort.id),
        default_capital=1000.0)[agent.agent_uuid]
    assert perf["trades_closed"] == 1
    assert perf["wins"] == 0 and perf["losses"] == 0 and perf["breakeven"] == 1
    assert perf["win_rate"] == 0.0


def test_unmarked_open_positions_are_flagged_not_treated_as_zero():
    session, settings, cohort, (agent,) = _fixture()
    md = StaticMarketData()
    md.set_quote(_quote("T"))
    _buy(session, settings, md, agent, cohort, "T")
    # never marked: unrealized_pnl_usd stays NULL on the position rows
    perf = metrics.bot_performance(
        session, [agent.agent_uuid], paper.cohort_ledger(cohort.id),
        default_capital=1000.0)[agent.agent_uuid]
    assert perf["trades_open"] == 1
    assert perf["unmarked_open"] == 1
    assert perf["unrealized_complete"] is False
    fleet = dash.build_fleet(session, settings)["summary"]
    assert fleet["unrealized_complete"] is False


# ---------------------------------------------------------------------------
# Trade table
# ---------------------------------------------------------------------------


def test_trade_rows_carry_entry_exit_and_attribution():
    session, settings, cohort, (agent,) = _fixture()
    md = StaticMarketData()
    md.set_quote(_quote("SETTLED"))
    _buy(session, settings, md, agent, cohort, "SETTLED")
    md.set_quote(_quote("STILLOPEN"))
    _buy(session, settings, md, agent, cohort, "STILLOPEN")
    _settle(session, md, "SETTLED", "yes")
    md.set_quote(_quote("STILLOPEN", yes_bid=52, no_bid=45))
    paper.mark_and_settle(session, md)

    ledger = paper.cohort_ledger(cohort.id)
    page = metrics.trade_rows(session, agent.agent_uuid, ledger)
    rows = {r["market"]: r for r in page["trades"]}
    assert page["total"] == 2 and page["has_more"] is False

    done = rows["SETTLED"]
    assert done["is_open"] is False and done["status"] == "settled"
    assert done["entry_price_cents"] == 47
    assert done["exit_price_cents"] == 100          # resolved value, stored on the row
    assert done["exit_reason"] == "settled at expiry"
    assert done["realized_pnl_usd"] > 0
    assert done["unrealized_pnl_usd"] is None       # a closed trade has no unrealized
    assert done["hold_hours"] is not None
    assert done["strategy_attributed"] is True      # matched to its entry order

    live = rows["STILLOPEN"]
    assert live["is_open"] is True and live["realized_pnl_usd"] is None
    assert live["unrealized_pnl_usd"] is not None
    assert live["mark_cents"] == 52
    assert live["exit_at"] is None and live["exit_reason"] is None


def test_trade_rows_filter_sort_and_paginate():
    session, settings, cohort, (agent,) = _fixture()
    md = StaticMarketData()
    for i in range(5):
        md.set_quote(_quote(f"T{i}"))
        _buy(session, settings, md, agent, cohort, f"T{i}", qty=5 + i)
    for i in range(3):
        _settle(session, md, f"T{i}", "yes" if i % 2 == 0 else "no")

    ledger = paper.cohort_ledger(cohort.id)
    assert metrics.trade_rows(session, agent.agent_uuid, ledger, status="closed")["total"] == 3
    assert metrics.trade_rows(session, agent.agent_uuid, ledger, status="open")["total"] == 2

    first = metrics.trade_rows(session, agent.agent_uuid, ledger, limit=2)
    assert len(first["trades"]) == 2 and first["has_more"] is True
    rest = metrics.trade_rows(session, agent.agent_uuid, ledger, limit=2, offset=2)
    assert rest["offset"] == 2 and len(rest["trades"]) == 2
    last = metrics.trade_rows(session, agent.agent_uuid, ledger, limit=2, offset=4)
    assert last["has_more"] is False
    # no row appears twice across the pages
    seen = [t["position_id"] for t in first["trades"] + rest["trades"] + last["trades"]]
    assert len(seen) == len(set(seen)) == 5

    by_pnl = metrics.trade_rows(session, agent.agent_uuid, ledger, status="closed", sort="pnl")
    values = [t["realized_pnl_usd"] for t in by_pnl["trades"]]
    assert values == sorted(values, reverse=True)


def test_exit_reason_reflects_a_strategy_stop_loss():
    session, settings, cohort, (agent,) = _fixture()
    md = StaticMarketData()
    md.set_quote(_quote("SL"))
    _buy(session, settings, md, agent, cohort, "SL")
    # sell it back out through an order carrying the strategy's exit reason
    md.set_quote(_quote("SL", yes_bid=30, no_bid=67))
    paper.place_order(
        session, settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        idem_key="exit:1", market_ticker="SL", side="yes", action="sell",
        quantity=10, intent={"exit_reason": "sl"},
    )
    paper.process_open_orders(session, settings, md)

    rows = metrics.trade_rows(
        session, agent.agent_uuid, paper.cohort_ledger(cohort.id), status="closed")["trades"]
    assert len(rows) == 1
    assert rows[0]["status"] == "closed"
    assert rows[0]["exit_reason"] == "stop-loss"
    # the exit price is recovered from the sell fills
    assert rows[0]["exit_price_cents"] == 30
    assert rows[0]["realized_pnl_usd"] < 0


# ---------------------------------------------------------------------------
# Generations
# ---------------------------------------------------------------------------


def test_generation_history_counts_promotions_and_eliminations():
    session, settings, cohort, agents = _fixture(n=3)
    members = list(session.scalars(
        select(em.EvoCohortMember).where(em.EvoCohortMember.cohort_id == cohort.id)))
    for member, group, rank in zip(members, ("top", "middle", "bottom"), (1, 2, 3), strict=True):
        member.final_group, member.final_rank = group, rank
    session.flush()

    row = metrics.generation_history(session)[0]
    assert row["number"] == cohort.number
    assert row["bots"] == 3
    assert row["promoted"] == 1 and row["survived"] == 2 and row["eliminated"] == 1
    assert row["finalized"] is True
    # no boundary snapshots were taken, so the P&L series stays null rather than 0
    assert row["fleet_pnl_usd"] is None and row["snapshot_bots"] == 0


def test_generation_history_uses_boundary_snapshots_for_pnl():
    session, settings, cohort, agents = _fixture(n=3)
    ledger = paper.cohort_ledger(cohort.id)
    for agent, nav in zip(agents, (1200.0, 1000.0, 800.0), strict=True):
        pf = paper.get_portfolio(session, agent.agent_uuid, ledger)
        pf.cash_usd = nav
        paper.snapshot_portfolio(session, agent.agent_uuid, ledger, f"boundary:{cohort.number}")
    session.flush()

    row = metrics.generation_history(session)[0]
    assert row["snapshot_bots"] == 3
    assert row["fleet_pnl_usd"] == 0.0          # +200, 0, -200 against $1,000 each
    assert row["median_bot_pnl_usd"] == 0.0
    assert row["best_bot_pnl_usd"] == 200.0
    assert row["worst_bot_pnl_usd"] == -200.0


def test_leaderboard_can_be_scoped_to_an_older_generation():
    session, settings, cohort, agents = _fixture(n=2)
    older = em.EvoCohort(number=cohort.number + 1, starts_at=NOW, ends_at=NOW,
                         status="open", rng_seed=1)
    session.add(older)
    session.flush()
    session.add(em.EvoCohortMember(cohort_id=older.id, agent_uuid=agents[0].agent_uuid,
                                   starting_capital=1000.0))
    session.flush()

    scoped = dash.build_bots(session, settings, generation=older.number)
    assert scoped["generation"] == older.number
    assert scoped["total"] == 1
    assert scoped["bots"][0]["uuid"] == agents[0].agent_uuid
    assert dash.build_bots(session, settings, generation=cohort.number)["total"] == 2


# ---------------------------------------------------------------------------
# Legacy / partially-populated data
# ---------------------------------------------------------------------------


def test_bot_with_no_portfolio_row_reports_the_baseline_not_zero_equity():
    """A bot that never joined this ledger (legacy rows, mid-migration) must not be
    reported as having lost its entire stake."""
    session, settings, cohort, (agent,) = _fixture()
    perf = metrics.bot_performance(
        session, [agent.agent_uuid], "cohort:9999", default_capital=1000.0)[agent.agent_uuid]
    assert perf["equity_usd"] == 1000.0
    assert perf["total_pnl_usd"] == 0.0
    assert perf["trades_total"] == 0


def test_position_with_missing_timestamps_does_not_break_aggregation():
    session, settings, cohort, (agent,) = _fixture()
    ledger = paper.cohort_ledger(cohort.id)
    session.add(em.EvoPosition(
        agent_uuid=agent.agent_uuid, ledger=ledger, market_ticker="LEGACY", side="yes",
        open_seq=1, quantity=5, avg_price_cents=40, status="closed",
        opened_at=NOW, closed_at=None, realized_pnl_usd=1.25, fees_usd=0,
    ))
    session.flush()
    perf = metrics.bot_performance(
        session, [agent.agent_uuid], ledger, default_capital=1000.0)[agent.agent_uuid]
    assert perf["trades_closed"] == 1
    assert perf["realized_pnl_usd"] == 1.25
    assert perf["avg_hold_hours"] is None     # unknown, not 0
    rows = metrics.trade_rows(session, agent.agent_uuid, ledger)["trades"]
    assert rows[0]["exit_at"] is None
    assert rows[0]["exit_price_cents"] is None


# ---------------------------------------------------------------------------
# Strategy description (deterministic, no LLM)
# ---------------------------------------------------------------------------


SPEC = {
    "name": "cheap-weather-fade",
    "family": "weather",
    "description": "Fade overpriced tails in daily-high markets.",
    "universe": {"series_prefixes": ["KXHIGHNY"], "min_volume": 100,
                 "max_spread_cents": 4, "min_hours_to_close": 1, "max_hours_to_close": 12},
    "entry": {"side": "cheap", "style": "maker", "maker_offset_cents": 1,
              "min_price_cents": 5, "max_price_cents": 35, "size_contracts": 8,
              "conditions": [{"metric": "spread", "op": "<=", "value": 3},
                             {"metric": "volume", "op": ">", "value": 250}]},
    "exit": {"mode": "tp_sl", "take_profit_cents": 12, "stop_loss_cents": 8},
    "risk": {"max_concurrent_positions": 4, "max_per_event": 1,
             "max_cost_per_position_usd": 25},
}


def test_strategy_summary_is_plain_english_and_deterministic():
    first = summarize_spec(SPEC)
    assert summarize_spec(SPEC) == first          # same spec, same words, every load
    assert "whichever side is cheaper" in first
    assert "KXHIGHNY" in first
    assert "5¢ and 35¢" in first
    assert "spread ≤ 3¢" in first and "volume > 250" in first
    assert "8 contracts" in first
    assert "takes profit at +12¢ and stops out at −8¢" in first
    assert "at most 4 positions" in first


def test_strategy_sections_render_parameters_readably():
    d = describe_spec(SPEC)
    titles = [s["title"] for s in d["sections"]]
    assert titles == ["Market filters", "Entry rules", "Sizing & exit", "Risk limits"]
    flat = {i["label"]: i["value"] for s in d["sections"] for i in s["items"]}
    assert flat["Maximum spread"] == "4¢"
    assert flat["Entry price band"] == "5¢ – 35¢"
    assert flat["Size per entry"] == "8 contracts"
    assert flat["Max cost per position"] == "$25"
    assert flat["Order style"] == "maker"
    # the raw spec stays available for debugging, but is never the only view
    assert d["raw"] == SPEC


def test_settlement_only_strategy_reads_naturally():
    spec = {**SPEC, "exit": {"mode": "settlement"}, "entry": {**SPEC["entry"], "conditions": []}}
    text = summarize_spec(spec)
    assert "holds to settlement" in text
    assert "when" not in text.split("contracts at a time")[0]


def test_empty_spec_says_so_instead_of_rendering_nothing():
    d = describe_spec({})
    assert d["sections"] == [] and d["raw"] is None
    assert "No declarative strategy is active" in d["summary"]


def test_bot_detail_renders_an_armed_strategy():
    session, settings, cohort, (agent,) = _fixture()
    session.add(em.EvoStrategy(agent_uuid=agent.agent_uuid, name="cheap-weather-fade",
                               revision=2, spec_json=SPEC, status="active"))
    session.flush()
    view = dash.build_bot_detail(session, settings, agent.agent_uuid)["strategy"]
    assert view["active_strategy"]["name"] == "cheap-weather-fade"
    assert view["active_strategy"]["revision"] == 2
    assert "whichever side is cheaper" in view["spec"]["summary"]
    assert view["spec"]["sections"]
    # and the leaderboard row carries the same one-liner
    row = next(b for b in dash.build_bots(session, settings)["bots"]
               if b["uuid"] == agent.agent_uuid)
    assert row["strategy_name"] == "cheap-weather-fade"
    assert row["strategy_summary"] == view["spec"]["summary"]
