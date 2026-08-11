"""The fleet has no null hypothesis, so "best agent" has never meant "any good".

Fitness ranks agents against each other. When every agent is -EV the winner is
merely the least-bad, and it reproduces — the population optimizes toward losing
slightly less than its peers, forever, with nothing in the system able to say so.
Live evidence: all 10 active strategies with positions are net negative, and the
fleet had already retired the only three books that were positive.

A control arm fixes that by putting an absolute reference beside the ranking:

  null              holds cash. P&L is identically 0.00. The bar.
  market_cheap      takes the cheap side of every admitted candidate.
  market_expensive  takes the expensive side.

Their AVERAGE is the side-neutral cost of participating with no opinion; the GAP
between them is the favorite-longshot bias in our own universe. An agent that
cannot beat them has found nothing, however well it ranks.

Controls are not competitors: `status='control'` keeps them out of the live set,
out of reproduction, out of retirement and out of the population cap by
construction, because every one of those paths selects on `status='active'`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import kalshi_bot.evo.models as em
from kalshi_bot.evo import controls, paper, strategy_runner
from kalshi_bot.evo.cognition import ScriptedCognition
from kalshi_bot.evo.cohorts import active_agents, ensure_current_cohort, reconcile_population
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import bootstrap_founders, maybe_finalize_cohort
from kalshi_bot.evo.marketdata import Quote, StaticMarketData


def _quote(ticker="T1", yes_bid=44, no_bid=53, hours=5.0):
    return Quote(
        ticker=ticker, captured_at=datetime.now(timezone.utc), status="active",
        yes_bid=yes_bid, yes_ask=100 - no_bid, no_bid=no_bid, no_ask=100 - yes_bid,
        yes_levels=[(yes_bid, 500)], no_levels=[(no_bid, 500)],
        volume=500, open_interest=1000,
        close_time=datetime.now(timezone.utc) + timedelta(hours=hours),
    )


def _noop_cognition():
    return ScriptedCognition(lambda ctx: {"journal": {"decision": "hold"},
                                          "actions": [{"type": "no_action"}]})


# --- they exist, and they are not competitors -------------------------------


def test_ensure_controls_creates_the_three_and_is_idempotent(evo_session, evo_settings):
    cohort = ensure_current_cohort(evo_session, evo_settings)
    made = controls.ensure_controls(evo_session, evo_settings, cohort)
    assert {a.first_name for a in made} == set(controls.CONTROL_NAMES)

    again = controls.ensure_controls(evo_session, evo_settings, cohort)
    assert again == []  # runs every cycle; must never duplicate
    assert len(controls.control_agents(evo_session)) == 3


def test_controls_are_invisible_to_the_population(evo_session, evo_settings):
    """THE containment property. A control that counted toward the cap would
    silently shrink the fleet; one that could be selected would breed."""
    cohort = ensure_current_cohort(evo_session, evo_settings)
    controls.ensure_controls(evo_session, evo_settings, cohort)
    bootstrap_founders(evo_session, evo_settings)

    live = active_agents(evo_session, evo_settings)
    assert len(live) == evo_settings.effective_population_size()
    assert not any(a.status == controls.CONTROL_STATUS for a in live)

    reconcile_population(evo_session, evo_settings)
    assert len(controls.control_agents(evo_session)) == 3, "reconcile suspended a control"


def test_a_control_can_never_be_retired_or_reproduce(evo_session, evo_settings):
    """Cohort finalization must not see them at all — not as a retirement
    candidate, not as a parent, not in the ranking."""
    settings = EvoSettings(_env_file=None, population_size=30, max_active_agents=4,
                           fill_latency_ms=0, routine_heartbeats_per_day=1)
    cohort = ensure_current_cohort(evo_session, settings)
    controls.ensure_controls(evo_session, settings, cohort)
    bootstrap_founders(evo_session, settings)
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc)
    outcome = maybe_finalize_cohort(
        evo_session, settings, cohort, md=StaticMarketData(),
        cognition=_noop_cognition(), now=ends + timedelta(minutes=1),
    )
    control_uuids = {a.agent_uuid for a in controls.control_agents(evo_session)}
    assert not (control_uuids & set(outcome["retired"]))
    assert not (control_uuids & set(outcome["children"]))
    for a in controls.control_agents(evo_session):
        assert a.status == controls.CONTROL_STATUS


def test_controls_take_no_heartbeats_so_they_cost_no_tokens(evo_session, evo_settings):
    """A benchmark that burned LLM budget would be a tax on the experiment. They
    run through the strategy runner only — there is no cognition in the loop."""
    cohort = ensure_current_cohort(evo_session, evo_settings)
    controls.ensure_controls(evo_session, evo_settings, cohort)
    bootstrap_founders(evo_session, evo_settings)
    from kalshi_bot.evo.heartbeats import due_routine_heartbeats

    due = due_routine_heartbeats(
        evo_session, evo_settings, active_agents(evo_session, evo_settings))
    control_uuids = {a.agent_uuid for a in controls.control_agents(evo_session)}
    assert due, "founders should be due, or this test proves nothing"
    assert not ({agent.agent_uuid for agent, *_ in due} & control_uuids)


# --- what they actually trade -----------------------------------------------


def test_the_market_controls_trade_and_the_null_does_not(evo_session, evo_settings):
    """The whole point: cheap and expensive both take a side of the same candidate,
    while null stays in cash — so the three together bracket every agent."""
    cohort = ensure_current_cohort(evo_session, evo_settings)
    controls.ensure_controls(evo_session, evo_settings, cohort)
    md = StaticMarketData()
    md.set_quote(_quote(ticker="T1"))
    strategy_runner.run_cycle(evo_session, evo_settings, md, cohort_id=cohort.id,
                              candidate_tickers=["T1"])
    paper.process_open_orders(evo_session, evo_settings, md)

    by_agent = {}
    for pos in evo_session.scalars(
        select(em.EvoPosition).where(em.EvoPosition.ledger == f"cohort:{cohort.id}")
    ):
        by_agent[pos.agent_uuid] = pos
    named = {a.agent_uuid: a.first_name for a in controls.control_agents(evo_session)}

    sides = {named[au]: p.side for au, p in by_agent.items() if au in named}
    assert sides.get(controls.MARKET_CHEAP) == "yes"      # yes costs 47, no costs 56
    assert sides.get(controls.MARKET_EXPENSIVE) == "no"
    assert controls.NULL not in sides, "the null control must never open a position"


def test_the_null_control_stays_exactly_flat(evo_session, evo_settings):
    """Its real job is as an accounting canary: 0.00 is the only correct answer, so
    any drift here is a bookkeeping bug rather than a trading result."""
    cohort = ensure_current_cohort(evo_session, evo_settings)
    controls.ensure_controls(evo_session, evo_settings, cohort)
    md = StaticMarketData()
    md.set_quote(_quote(ticker="T1"))
    for _ in range(3):
        strategy_runner.run_cycle(evo_session, evo_settings, md, cohort_id=cohort.id,
                                  candidate_tickers=["T1"])
        paper.process_open_orders(evo_session, evo_settings, md)

    bench = controls.benchmark(evo_session, cohort.id)
    assert bench[controls.NULL]["n_positions"] == 0
    assert bench[controls.NULL]["realized_usd"] == 0.0
    assert bench[controls.NULL]["cents_per_position"] is None


def test_controls_are_funded_like_everyone_else(evo_session, evo_settings):
    """Same starting capital and the same cohort ledger, or the comparison is not
    apples to apples."""
    cohort = ensure_current_cohort(evo_session, evo_settings)
    controls.ensure_controls(evo_session, evo_settings, cohort)
    for agent in controls.control_agents(evo_session):
        pf = evo_session.scalar(
            select(em.EvoPortfolio).where(
                em.EvoPortfolio.agent_uuid == agent.agent_uuid,
                em.EvoPortfolio.ledger == f"cohort:{cohort.id}",
            )
        )
        assert pf is not None
        assert float(pf.cash_usd) == evo_settings.starting_capital_usd


# --- the number they produce ------------------------------------------------


def test_benchmark_reports_per_position_cents_not_totals(evo_session, evo_settings):
    """Controls trade far more than a typical agent, so totals are not comparable.
    Per-position cents is scale-free and is what a strategy must beat."""
    cohort = ensure_current_cohort(evo_session, evo_settings)
    controls.ensure_controls(evo_session, evo_settings, cohort)
    agent = next(a for a in controls.control_agents(evo_session)
                 if a.first_name == controls.MARKET_CHEAP)
    ledger = paper.cohort_ledger(cohort.id)
    for i, pnl in enumerate((-0.50, -0.30, 0.20)):
        evo_session.add(em.EvoPosition(
            agent_uuid=agent.agent_uuid, ledger=ledger, market_ticker=f"T{i}",
            side="yes", quantity=0, avg_price_cents=50, status="settled",
            realized_pnl_usd=pnl, fees_usd=0.0,
        ))
    evo_session.flush()

    bench = controls.benchmark(evo_session, cohort.id)[controls.MARKET_CHEAP]
    assert bench["n_positions"] == 3
    assert bench["realized_usd"] == -0.6
    # -$0.60 over 3 positions of 5 contracts = -4.0c per contract
    assert bench["cents_per_position"] == -20.0


def test_benchmark_exposes_the_side_neutral_cost_and_the_longshot_gap(
    evo_session, evo_settings
):
    """The two numbers the control arm exists to produce, derived rather than eyeballed."""
    cohort = ensure_current_cohort(evo_session, evo_settings)
    controls.ensure_controls(evo_session, evo_settings, cohort)
    ledger = paper.cohort_ledger(cohort.id)
    by_name = {a.first_name: a for a in controls.control_agents(evo_session)}
    for name, pnl in ((controls.MARKET_CHEAP, -1.00), (controls.MARKET_EXPENSIVE, -0.20)):
        evo_session.add(em.EvoPosition(
            agent_uuid=by_name[name].agent_uuid, ledger=ledger, market_ticker=f"X{name}",
            side="yes", quantity=0, avg_price_cents=50, status="settled",
            realized_pnl_usd=pnl, fees_usd=0.0,
        ))
    evo_session.flush()

    summary = controls.benchmark_summary(evo_session, cohort.id)
    assert summary["participation_cents"] == -60.0   # mean of -100 and -20
    assert summary["longshot_gap_cents"] == -80.0    # cheap minus expensive


def test_the_dashboard_shows_the_benchmark_beside_the_roster(evo_session, evo_settings):
    """A control arm nobody can see does not do its job — the roster has to carry the
    bar next to the ranking, or 'top bot' still reads as 'good bot'."""
    from kalshi_bot.dashboard import data as dash

    cohort = ensure_current_cohort(evo_session, evo_settings)
    controls.ensure_controls(evo_session, evo_settings, cohort)
    bootstrap_founders(evo_session, evo_settings)
    payload = dash.build_fleet(evo_session, evo_settings)

    assert payload["benchmark"] is not None
    assert set(payload["benchmark"]["controls"]) == set(controls.CONTROL_NAMES)
    # ...and the controls must not be mistaken for fleet members in the roster count
    assert payload["throttle"]["total"] == evo_settings.effective_population_size()
