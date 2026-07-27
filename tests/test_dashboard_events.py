"""Dashboard activity feed: category filtering, heartbeat condensation, bot and
generation filters, search, and cursor pagination beyond the recent-event limit."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot.dashboard import data as dash
from kalshi_bot.dashboard import events as ev
from kalshi_bot.evo import budgets, llm, paper
from kalshi_bot.evo import models as em
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.models import Base

NOW = datetime.now(timezone.utc)


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def _fixture(n=2):
    session = _session()
    settings = EvoSettings(_env_file=None)
    llm.seed_model_prices(session, settings)
    cohort = ensure_current_cohort(session, settings)
    agents = []
    for i in range(n):
        a = create_agent(session, settings, cohort, random.Random(i),
                         origin="founder", slot_key=f"founder:{i}")
        budgets.ensure_budgets(session, a.agent_uuid, cohort.id, settings)
        agents.append(a)
    return session, settings, cohort, agents


def _heartbeats(session, cohort, agent, n, *, status="completed", kind="routine", start=0):
    for i in range(n):
        session.add(em.EvoHeartbeat(
            agent_uuid=agent.agent_uuid, cohort_id=cohort.id, kind=kind,
            idem_key=f"hb:{agent.agent_uuid[:6]}:{status}:{start + i}", status=status,
            created_at=NOW - timedelta(minutes=start + i),
        ))
    session.flush()


def _closed_trade(session, cohort, agent, ticker, pnl, *, when=None, seq=1):
    session.add(em.EvoPosition(
        agent_uuid=agent.agent_uuid, ledger=paper.cohort_ledger(cohort.id),
        market_ticker=ticker, side="yes", open_seq=seq, quantity=10,
        avg_price_cents=40, status="settled", opened_at=NOW - timedelta(hours=2),
        closed_at=when or NOW, realized_pnl_usd=pnl, fees_usd=0.1,
        resolved_value_cents=100 if pnl > 0 else 0,
    ))
    session.flush()


def _kinds(payload):
    return [e["kind"] for e in payload["events"]]


# ---------------------------------------------------------------------------
# The default view is Important, and heartbeats do not dominate it
# ---------------------------------------------------------------------------


def test_important_is_the_default_and_excludes_routine_heartbeats():
    session, settings, cohort, (a, b) = _fixture()
    _heartbeats(session, cohort, a, 60)          # a flood of routine beats
    _closed_trade(session, cohort, b, "MKT", 3.0)

    payload = dash.build_events(session)
    assert payload["category"] == "important"
    kinds = _kinds(payload)
    assert "heartbeat_completed" not in kinds
    assert any(k.startswith("trade_") for k in kinds)
    # the same 60 beats ARE reachable, just under System
    system = dash.build_events(session, category="system", limit=100)
    assert sum(1 for k in _kinds(system) if k == "heartbeat_completed") >= 40


def test_sixty_heartbeats_cannot_bury_a_single_trade():
    """The v0.1 regression: one merged feed cut to the latest N rows, so a burst of
    heartbeats pushed every trade off the page."""
    session, settings, cohort, (a, b) = _fixture()
    _closed_trade(session, cohort, b, "OLDTRADE", 2.0, when=NOW - timedelta(minutes=90))
    _heartbeats(session, cohort, a, 60)          # all NEWER than the trade

    kinds = _kinds(dash.build_events(session, limit=20))
    assert any(k.startswith("trade_") for k in kinds)


def test_heartbeat_failures_are_important_but_degraded_beats_are_not():
    session, settings, cohort, (a, b) = _fixture()
    _heartbeats(session, cohort, a, 30, status="degraded")
    _heartbeats(session, cohort, b, 1, status="error")

    kinds = _kinds(dash.build_events(session, category="important", limit=50))
    assert "heartbeat_error" in kinds
    assert "heartbeat_degraded" not in kinds     # systemic + noisy -> health panel
    assert "heartbeat_degraded" in _kinds(dash.build_events(session, category="system", limit=80))


def test_fleet_health_condenses_heartbeats_into_a_coverage_summary():
    session, settings, cohort, (a, b) = _fixture()
    _heartbeats(session, cohort, a, 3)                        # fresh: 0-2 minutes ago
    _heartbeats(session, cohort, b, 1, start=600)             # 10h ago — past the window
    _heartbeats(session, cohort, b, 2, status="degraded", start=601)

    health = ev.fleet_health(
        session, settings, [a.agent_uuid, b.agent_uuid], now=NOW)
    assert health["total"] == 2
    assert health["checked_in"] == 1                          # only a is inside the window
    assert health["stale_bots"] == 1
    assert len(health["latest_per_bot"]) == 2                 # one row per bot, not 6
    assert health["last_24h"]["completed"] == 4
    assert health["last_24h"]["degraded"] == 2
    assert health["expected_interval_minutes"] == 240         # 6 routine beats/day
    # the stale bot's row is the newest of ITS beats, not the newest row inserted
    stale = next(r for r in health["latest_per_bot"] if r["uuid"] == b.agent_uuid)
    assert stale["fresh"] is False and stale["status"] == "completed"


def test_fleet_health_handles_a_bot_that_never_checked_in():
    session, settings, cohort, (a, b) = _fixture()
    health = ev.fleet_health(session, settings, [a.agent_uuid, b.agent_uuid], now=NOW)
    assert health["checked_in"] == 0
    assert health["never_checked_in"] == 2
    assert health["last_heartbeat_at"] is None


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def test_each_category_returns_only_its_own_streams():
    session, settings, cohort, (a, b) = _fixture()
    _heartbeats(session, cohort, a, 4)
    _closed_trade(session, cohort, a, "MKT", 1.5)
    session.add(em.EvoGenome(agent_uuid=a.agent_uuid, kind="trading", revision=2,
                             document_json={"strategy_family": "weather"},
                             changed_fields_json=["entry_rules"], material=True))
    session.flush()

    assert set(_kinds(dash.build_events(session, category="trades", limit=50))) <= {
        "trade_settled", "trade_closed", "trade_liquidated", "trade_transferred",
        "order_open", "order_filled", "order_partial", "order_canceled",
        "order_expired", "order_rejected", "fill"}
    assert set(_kinds(dash.build_events(session, category="strategy", limit=50))) <= {
        "strategy_mutation", "strategy_rollback", "strategy_activated",
        "listener_created", "experiment_concluded"}
    assert "generation_started" in _kinds(dash.build_events(session, category="evolution", limit=50))
    assert "heartbeat_completed" in _kinds(dash.build_events(session, category="system", limit=50))
    # "all" is the union
    every = _kinds(dash.build_events(session, category="all", limit=200))
    assert "heartbeat_completed" in every and "trade_settled" in every


def test_unknown_category_falls_back_to_important():
    session, settings, cohort, agents = _fixture()
    assert dash.build_events(session, category="nonsense")["category"] == "important"


def test_announcements_live_in_their_own_category():
    session, settings, cohort, agents = _fixture()
    from kalshi_bot.evo import announcements as announce

    announce.seed_announcements(session)
    ann = dash.build_events(session, category="announcements", limit=50)
    assert ann["events"] and all(
        e["category"] == "announcements" for e in ann["events"])
    # ...and never crowd out the Important view
    assert not any(e["category"] == "announcements"
                   for e in dash.build_events(session, category="important", limit=50)["events"])


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_filter_by_bot_is_applied_server_side():
    session, settings, cohort, (a, b) = _fixture()
    _closed_trade(session, cohort, a, "AAA", 1.0)
    _closed_trade(session, cohort, b, "BBB", -1.0)

    only_a = dash.build_events(session, category="trades", agent_uuid=a.agent_uuid, limit=50)
    markets = {e["market"] for e in only_a["events"] if e["market"]}
    assert "AAA" in markets and "BBB" not in markets
    assert all(e["agent_uuid"] in (a.agent_uuid, None) for e in only_a["events"])


def test_filter_by_generation():
    session, settings, cohort, (a, b) = _fixture()
    b.generation = 2
    session.flush()
    _closed_trade(session, cohort, a, "GEN1", 1.0)
    _closed_trade(session, cohort, b, "GEN2", 1.0)

    gen2 = dash.build_events(session, category="trades", generation=2, limit=50)
    assert {e["market"] for e in gen2["events"] if e["market"]} == {"GEN2"}
    gen1 = dash.build_events(session, category="trades", generation=1, limit=50)
    assert "GEN1" in {e["market"] for e in gen1["events"] if e["market"]}


def test_filter_by_date_range():
    session, settings, cohort, (a, b) = _fixture()
    _closed_trade(session, cohort, a, "OLD", 1.0, when=NOW - timedelta(days=5))
    _closed_trade(session, cohort, a, "NEW", 1.0, when=NOW, seq=2)

    recent = dash.build_events(session, category="trades", since=NOW - timedelta(days=1), limit=50)
    assert {e["market"] for e in recent["events"] if e["market"]} == {"NEW"}
    older = dash.build_events(session, category="trades", until=NOW - timedelta(days=1), limit=50)
    assert "OLD" in {e["market"] for e in older["events"] if e["market"]}


def test_search_matches_market_bot_and_text():
    session, settings, cohort, (a, b) = _fixture()
    _closed_trade(session, cohort, a, "KXHIGHNY-26JUL27-B85", 1.0)
    _closed_trade(session, cohort, b, "KXBTC-DEC", -1.0)

    hit = dash.build_events(session, category="trades", search="kxhighny", limit=50)
    assert {e["market"] for e in hit["events"]} == {"KXHIGHNY-26JUL27-B85"}
    by_name = dash.build_events(session, category="trades", search=a.display_name.split(" ")[0],
                                limit=50)
    assert by_name["events"]
    assert dash.build_events(session, category="trades", search="zzz-no-match")["events"] == []


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_cursor_pagination_walks_past_the_first_page_without_gaps_or_repeats():
    session, settings, cohort, (a, b) = _fixture()
    _heartbeats(session, cohort, a, 50)

    seen, cursor, pages = [], None, 0
    while True:
        page = dash.build_events(session, category="system", limit=10, cursor=cursor)
        seen.extend(e["id"] for e in page["events"])
        cursor = page["next_cursor"]
        pages += 1
        if not cursor or pages > 12:
            break
    assert pages > 1                       # more than one page of history
    assert len(seen) == len(set(seen))     # no event returned twice
    heartbeat_ids = {f"heartbeat:{h}" for h in range(1, 51)}
    assert heartbeat_ids <= set(seen)      # every one of the 50 is reachable


def test_events_are_ordered_newest_first_across_streams():
    session, settings, cohort, (a, b) = _fixture()
    _heartbeats(session, cohort, a, 5)
    _closed_trade(session, cohort, b, "MKT", 1.0)
    page = dash.build_events(session, category="all", limit=50)
    stamps = [e["at"] for e in page["events"]]
    assert stamps == sorted(stamps, reverse=True)


def test_limit_is_clamped_and_page_never_exceeds_it():
    session, settings, cohort, (a, b) = _fixture()
    _heartbeats(session, cohort, a, 40)
    assert len(dash.build_events(session, category="system", limit=5)["events"]) == 5
    assert dash.build_events(session, category="system", limit=10_000)["limit"] == ev.MAX_LIMIT


def test_bad_cursor_is_ignored_rather_than_erroring():
    session, settings, cohort, (a, b) = _fixture()
    _heartbeats(session, cohort, a, 3)
    page = dash.build_events(session, category="system", cursor="not-a-cursor")
    assert page["events"]


# ---------------------------------------------------------------------------
# Event content
# ---------------------------------------------------------------------------


def test_significant_pnl_is_called_out():
    session, settings, cohort, (a, b) = _fixture()
    _closed_trade(session, cohort, a, "BIG", 25.0)
    _closed_trade(session, cohort, b, "SMALL", 0.5)
    by_market = {e["market"]: e for e in dash.build_events(session, category="trades", limit=50)["events"]
                 if e["market"]}
    assert by_market["BIG"]["title"] == "Significant win"
    assert by_market["BIG"]["severity"] == "good"
    assert by_market["SMALL"]["title"] == "Trade closed"


def test_a_resting_order_is_not_described_as_an_opened_position():
    """Regression for a live incident: the Trades feed described EVERY order with
    status in (filled, partial, open) as "opened a {side} position". A resting,
    never-filled order (0 fills, no position row, no capital deployed) therefore read
    as a completed trade — and contradicted the same page's "no open positions"."""
    session, settings, cohort, (agent, _) = _fixture()
    session.add_all([
        em.EvoOrder(agent_uuid=agent.agent_uuid, cohort_id=cohort.id, idem_key="k-open",
                    market_ticker="KXHIGHNY-26JUL27-B85.5", side="yes", action="buy",
                    style="taker", quantity=10, filled_quantity=0,
                    limit_price_cents=7, status="open"),
        em.EvoOrder(agent_uuid=agent.agent_uuid, cohort_id=cohort.id, idem_key="k-filled",
                    market_ticker="KXHIGHNY-26JUL27-T81", side="yes", action="buy",
                    style="taker", quantity=5, filled_quantity=5,
                    limit_price_cents=20, status="filled"),
    ])
    session.flush()

    by_market = {
        e["market"]: e
        for e in dash.build_events(session, category="trades", limit=50)["events"]
        if e["stream"] == "order"
    }
    resting = by_market["KXHIGHNY-26JUL27-B85.5"]["description"]
    assert "position" not in resting, f"resting order called a position: {resting!r}"
    assert "placed a buy order" in resting and "unfilled" in resting

    filled = by_market["KXHIGHNY-26JUL27-T81"]["description"]
    assert "bought yes" in filled


def test_rejected_orders_are_important():
    session, settings, cohort, (agent, _) = _fixture()
    session.add(em.EvoOrder(
        agent_uuid=agent.agent_uuid, cohort_id=cohort.id, idem_key="k-rej",
        market_ticker="BAD", side="yes", action="buy", style="taker", quantity=1,
        filled_quantity=0, limit_price_cents=50, status="rejected",
        reject_reason="unfillable: no live quote",
    ))
    session.flush()
    important = dash.build_events(session, category="important", limit=50)["events"]
    rejected = [e for e in important if e["kind"] == "order_rejected"]
    assert rejected and rejected[0]["severity"] == "bad"
    # the reject_reason itself is operator-facing detail, not published verbatim
    assert "unfillable" not in rejected[0]["description"]


def test_evolution_lifecycle_events_are_important():
    session, settings, cohort, (a, b) = _fixture()
    session.add(em.EvoRetirement(agent_uuid=b.agent_uuid, cohort_id=cohort.id,
                                 reason="bottom_group", final_rank=9))
    session.flush()
    kinds = _kinds(dash.build_events(session, category="important", limit=50))
    assert "bot_eliminated" in kinds
    assert "bot_born_founder" in kinds
    assert "generation_started" in kinds


def test_empty_database_returns_an_empty_page_not_an_error():
    session = _session()
    page = dash.build_events(session)
    assert page == {"events": [], "next_cursor": None, "category": "important", "limit": 40}
