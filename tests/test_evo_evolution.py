"""Evo heartbeats, cognition, LLM budgets, peers, fitness, and the evolution
engine (finalization, retirement, reproduction, wildcards)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot.evo import budgets, heartbeats, llm, memory, paper, peers
from kalshi_bot.evo import models as em
from kalshi_bot.evo.cognition import ScriptedCognition, parse_output
from kalshi_bot.evo.cohorts import active_agents, ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.evolution import bootstrap_founders, create_agent, maybe_finalize_cohort
from kalshi_bot.evo.fitness import (
    component_scores,
    evaluate_cohort,
    group_by_fractions,
    historical_reliability,
)
from kalshi_bot.evo.marketdata import Quote, StaticMarketData

NOW = datetime.now(timezone.utc)


def _noop_cognition():
    return ScriptedCognition(lambda ctx: {"journal": {"decision": "hold"},
                                          "actions": [{"type": "no_action"}]})


def _md_with(ticker="T1", **kw):
    md = StaticMarketData()
    if ticker:
        md.set_quote(Quote(
            ticker=ticker, captured_at=datetime.now(timezone.utc), status="active",
            yes_bid=44, yes_ask=47, no_bid=53, no_ask=56,
            yes_levels=[(44, 100)], no_levels=[(53, 100)],
            volume=100, close_time=datetime.now(timezone.utc) + timedelta(hours=5), **kw,
        ))
    return md


# --- heartbeat scheduling / idempotency -------------------------------------


def test_routine_slots_deterministic_and_spread():
    day = datetime(2026, 7, 15, tzinfo=timezone.utc)
    s1 = heartbeats.routine_slots("agent-x", day, 6)
    s2 = heartbeats.routine_slots("agent-x", day, 6)
    assert s1 == s2 and len(s1) == 6
    hours = [at.hour for _, at in s1]
    assert len(set(h // 4 for h in hours)) == 6  # one slot per 4h block
    assert heartbeats.routine_slots("agent-y", day, 6) != s1  # jitter differs


def test_reflection_slots_scale_with_cadence():
    day = datetime(2026, 7, 15, tzinfo=timezone.utc)
    two = heartbeats.reflection_slots("agent-x", day, 2)  # every ~12h
    assert two == heartbeats.reflection_slots("agent-x", day, 2)  # deterministic
    assert len(two) == 2
    assert [sid for sid, _ in two] == ["20260715:0", "20260715:1"]
    # the two land in different halves of the day
    assert two[0][1] < day + timedelta(hours=12) <= two[1][1]
    assert heartbeats.reflection_slots("agent-y", day, 2) != two  # jitter differs
    # raising cadence adds slots rather than colliding with claimed slot ids
    four = heartbeats.reflection_slots("agent-x", day, 4)
    assert len(four) == 4 and four[0][0] == two[0][0]


def test_strategic_slots_are_epoch_anchored_every_48h():
    now = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
    slots = heartbeats.strategic_slots("agent-x", now, 48.0)
    assert slots == heartbeats.strategic_slots("agent-x", now, 48.0)  # deterministic
    assert len(slots) == 2  # current + previous period (restart recovery)
    (cur_id, cur_at), (prev_id, prev_at) = slots
    # consecutive periods: 48h apart, +/- the per-period jitter (<=2h each side)
    assert int(cur_id.split(":")[1]) - int(prev_id.split(":")[1]) == 1
    assert timedelta(hours=46) <= cur_at - prev_at <= timedelta(hours=50)
    # anchored to the epoch, so it does NOT drift when 'now' moves within a period
    later = heartbeats.strategic_slots("agent-x", now + timedelta(hours=3), 48.0)
    assert later == slots
    # ...and rolls over to a fresh, unclaimed slot id in the next period
    nxt = heartbeats.strategic_slots("agent-x", now + timedelta(hours=48), 48.0)
    assert nxt[0][0] != cur_id and nxt[1][0] == cur_id
    assert heartbeats.strategic_slots("agent-x", now, 0) == []  # 0 disables


def test_heartbeat_kind_maps_to_the_right_tier():
    from kalshi_bot.evo import cognition as cog

    settings = EvoSettings(_env_file=None)
    assert cog.alias_for_kind("routine") == "routine"
    assert cog.alias_for_kind("triggered") == "routine"  # unlisted -> cheapest tier
    assert cog.alias_for_kind("reflection") == "deep"
    # every high-stakes lifecycle beat rides the top tier
    for kind in ("strategic", "birth", "cohort_end", "retirement"):
        assert cog.alias_for_kind(kind) == "strategic", kind
        assert cog.is_enriched_kind(kind) is True
    assert cog.is_enriched_kind("routine") is False
    # each kind routes to its own tier's output ceiling (routine's is the
    # largest of the three despite being the cheapest tier — sized around a
    # reasoning model's observed overshoot rather than content richness, see
    # config.py's heartbeat_max_output_tokens comment)
    assert (cog.max_output_tokens_for(settings, "routine")
            == settings.heartbeat_max_output_tokens)
    assert (cog.max_output_tokens_for(settings, "reflection")
            == settings.reflection_max_output_tokens)
    assert (cog.max_output_tokens_for(settings, "birth")
            == settings.strategic_max_output_tokens)


def test_claim_heartbeat_idempotent(evo_session, evo_agent):
    agent, cohort = evo_agent
    hb = heartbeats.claim_heartbeat(
        evo_session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        kind="routine", slot_id="20260715:0",
    )
    assert hb is not None
    again = heartbeats.claim_heartbeat(
        evo_session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        kind="routine", slot_id="20260715:0",
    )
    assert again is None


def test_sweep_stale_marks_abandoned(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    hb = heartbeats.claim_heartbeat(
        evo_session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        kind="routine", slot_id="old",
    )
    hb.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    evo_session.flush()
    assert heartbeats.sweep_stale(evo_session, evo_settings) == 1
    assert hb.status == "abandoned"


def test_run_heartbeat_writes_journal_and_actions(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "note something", "expected_outcome": "memory row"},
        "actions": [{"type": "note_episode", "title": "hello", "detail": "world"}],
    })
    hb = heartbeats.run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="s1", cognition=cog, md=_md_with(),
    )
    assert hb.status == "completed"
    assert hb.actions_json[0]["ok"] is True
    journal = evo_session.get(em.EvoMemory, hb.journal_memory_id)
    assert journal.kind == "journal" and journal.immutable
    # second run of the same slot: claimed => None
    assert heartbeats.run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="s1", cognition=cog, md=_md_with(),
    ) is None


def test_degraded_heartbeat_still_journaled(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    def broken(ctx):
        raise RuntimeError("cognition down")
    hb = heartbeats.run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="s2", cognition=ScriptedCognition(broken), md=_md_with(),
    )
    assert hb.status == "degraded" and hb.journal_memory_id is not None


def test_triggered_heartbeats_consume_weekly_pool(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    # leave exactly ONE triggered-heartbeat slot in the weekly pool by consuming the rest
    # (exhaust via `used`, not by shrinking `allocated` below config — ensure_budgets now
    # tops a below-config allocation back up, so the old "set allocated=1" trick no longer holds)
    b = evo_session.scalar(select(em.EvoBudget).where(
        em.EvoBudget.agent_uuid == agent.agent_uuid,
        em.EvoBudget.resource == "triggered_heartbeats"))
    b.used = float(b.allocated) - 1
    evo_session.flush()
    listener = em.EvoListener(owner_uuid=agent.agent_uuid, name="l",
                              condition_json={"all": []}, effect="trigger_heartbeat")
    evo_session.add(listener)
    evo_session.flush()
    for i in range(2):
        evo_session.add(em.EvoListenerEvent(
            listener_id=listener.id, owner_uuid=agent.agent_uuid,
            dedup_key=f"k{i}", payload_json={"effect": "trigger_heartbeat"},
        ))
    evo_session.flush()
    events = list(evo_session.scalars(select(em.EvoListenerEvent)))
    hb1 = heartbeats.run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="triggered",
        slot_id=f"levent:{events[0].id}", cognition=_noop_cognition(), md=_md_with(),
        listener_event=events[0],
    )
    assert hb1 is not None
    hb2 = heartbeats.run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="triggered",
        slot_id=f"levent:{events[1].id}", cognition=_noop_cognition(), md=_md_with(),
        listener_event=events[1],
    )
    assert hb2 is None  # pool exhausted
    assert events[1].consumed  # event not left dangling


# --- cognition protocol -----------------------------------------------------


def test_parse_output_variants():
    j, a, err = parse_output('{"journal": {"decision": "x"}, "actions": [{"type": "no_action"}]}')
    assert err is None and j["decision"] == "x" and a[0]["type"] == "no_action"
    j, a, err = parse_output('```json\n{"journal": {}, "actions": []}\n```')
    assert err is None
    j, a, err = parse_output("I refuse to answer in JSON")
    assert err is not None and a == []
    many = {"journal": {}, "actions": [{"type": "no_action"}] * 50}
    import json as _json

    _, a, _ = parse_output(_json.dumps(many))
    assert len(a) == 12  # MAX_ACTIONS_PER_HEARTBEAT


def test_unpermitted_action_rejected_and_integrity_flagged(
    evo_session, evo_settings, evo_agent
):
    agent, cohort = evo_agent
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "cheat"},
        "actions": [{"type": "modify_fitness_rules"}, {"type": "made_up_thing"}],
    })
    hb = heartbeats.run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="cheat", cognition=cog, md=_md_with(),
    )
    assert all("rejected" in o for o in hb.actions_json)
    integrity = list(evo_session.scalars(
        select(em.EvoAuditEvent).where(em.EvoAuditEvent.severity == "integrity")
    ))
    assert len(integrity) == 1  # 'fitness' in the type => integrity; typo => plain reject


def test_material_revision_action_respects_daily_cap(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent

    def reviser(ctx):
        doc = dict(ctx.trading_genome)
        doc["sizing_value"] = doc.get("sizing_value", 25.0) + 1
        return {"journal": {"decision": "revise"},
                "actions": [{"type": "revise_trading_genome", "document": doc,
                             "hypothesis": "h", "rollback_conditions": "r"}]}

    results = []
    for i in range(3):
        hb = heartbeats.run_heartbeat(
            evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
            slot_id=f"rev{i}", cognition=ScriptedCognition(reviser), md=_md_with(),
        )
        results.append(hb.actions_json[0])
    assert results[0].get("ok")
    assert "cooldown" in results[1].get("rejected", "")  # cooldown before 2nd
    assert "cooldown" in results[2].get("rejected", "")


def test_trade_intent_action_places_order(evo_session, evo_settings, evo_agent):
    """Default style is taker; the limit (50) clears the live ask (47), so the
    order fills IMMEDIATELY as part of the action outcome — not on a later cycle."""
    agent, cohort = evo_agent
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "buy"},
        "actions": [{"type": "submit_trade_intent", "market_ticker": "T1",
                     "side": "yes", "action": "buy", "quantity": 5,
                     "limit_price_cents": 50, "thesis": "test", "confidence": 0.7}],
    })
    hb = heartbeats.run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="trade", cognition=cog, md=_md_with(),
    )
    assert hb.actions_json[0]["ok"]
    assert hb.actions_json[0]["status"] == "filled"
    order = evo_session.scalar(select(em.EvoOrder).where(
        em.EvoOrder.agent_uuid == agent.agent_uuid))
    assert order is not None and order.status == "filled"
    fact = evo_session.scalar(select(em.EvoMemory).where(
        em.EvoMemory.kind == "fact", em.EvoMemory.agent_uuid == agent.agent_uuid))
    assert fact is not None  # immutable trade-intent record


def test_trade_intent_rejected_up_front_for_untradable_ticker(
    evo_session, evo_settings, evo_agent
):
    """An order on a ticker with no live quote (e.g. a bare series prefix like
    "KXHIGH") is rejected AT SUBMISSION with a clear reason — and crucially no order
    row is created, so it can never become a stuck 'open' order with zero feedback."""
    agent, cohort = evo_agent
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "buy prefix"},
        "actions": [{"type": "submit_trade_intent", "market_ticker": "KXHIGH",
                     "side": "yes", "action": "buy", "quantity": 5,
                     "limit_price_cents": 50, "thesis": "oops", "confidence": 0.7}],
    })
    hb = heartbeats.run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="badtrade", cognition=cog, md=_md_with(ticker=None),
    )
    outcome = hb.actions_json[0]
    assert "rejected" in outcome and "series/event prefix" in outcome["rejected"]
    assert evo_session.scalar(select(em.EvoOrder).where(
        em.EvoOrder.agent_uuid == agent.agent_uuid)) is None  # no limbo order


# --- llm budgets ------------------------------------------------------------


def test_llm_pricing_and_no_key_fails_closed(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    assert llm.seed_model_prices(evo_session, evo_settings) == 3  # routine + deep + strategic
    assert llm.seed_model_prices(evo_session, evo_settings) == 0
    price = llm.get_price(evo_session, "routine")
    assert llm.compute_cost_usd(price, 1_000_000, 0, 0) == 1.0
    assert llm.compute_cost_usd(price, 1_000_000, 1_000_000, 0) == 0.1  # cached
    client = llm.LlmClient(evo_settings, api_key="")
    result = client.complete(
        evo_session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        heartbeat_id=None, alias="routine", system_blocks=[], user_content="hi",
        max_tokens=100,
    )
    assert result.error == "no ANTHROPIC_API_KEY"
    assert budgets.remaining(
        evo_session, agent.agent_uuid, cohort.id, "llm_cost_usd"
    ) == evo_settings.weekly_llm_ceiling_usd  # nothing spent
    client.close()


def test_llm_budget_ceiling_blocks_before_any_call(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    llm.seed_model_prices(evo_session, evo_settings)
    budgets.spend(evo_session, agent.agent_uuid, cohort.id, "llm_cost_usd",
                  evo_settings.weekly_llm_ceiling_usd, force=True)
    client = llm.LlmClient(evo_settings, api_key="test-key")  # available, but broke
    result = client.complete(
        evo_session, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        heartbeat_id=None, alias="routine", system_blocks=[], user_content="hi",
        max_tokens=100,
    )
    assert "ceiling" in result.error
    client.close()


# --- peers / delayed leaderboard --------------------------------------------


def test_delayed_leaderboard_hides_fresh_scores(evo_session, evo_agent):
    agent, cohort = evo_agent
    now = datetime.now(timezone.utc)
    evo_session.add(em.EvoFitness(
        cohort_id=cohort.id, agent_uuid=agent.agent_uuid, kind="interim",
        computed_at=now, visible_after=now + timedelta(hours=6),
        components_json={}, cohort_score=55.0, reliability_score=50.0,
        selection_score=54.0,
    ))
    evo_session.flush()
    assert peers.delayed_leaderboard(evo_session, cohort.id) == []
    board = peers.delayed_leaderboard(
        evo_session, cohort.id, now=now + timedelta(hours=6, minutes=1)
    )
    assert len(board) == 1 and board[0]["delayed_rank"] == 1


def test_peer_summary_exposes_thesis_not_private_state(evo_session, evo_agent):
    agent, _ = evo_agent
    agent.working_state_json = {"secret_plan": "buy everything"}
    memory.revise_belief(
        evo_session, agent.agent_uuid, title="private", new_belief="hidden",
        evidence_for="e", evidence_against=None, confidence_after=0.9, heartbeat_id=None,
    )
    summary = peers.peer_summary(evo_session, agent.agent_uuid)
    text = str(summary)
    assert "thesis" in summary and "secret_plan" not in text and "hidden" not in text


# --- fitness ----------------------------------------------------------------


def _metrics_base(**over):
    m = {
        "start": 1000.0, "nav": 1000.0, "net_pnl": 0.0, "n_trades": 0, "n_eff": 0,
        "trade_pnls": [], "daily_pnls": [], "turnover": 0.0, "max_event_frac": 0.0,
        "max_drawdown_frac": 0.0, "min_equity_frac": 1.0, "open_positions": 0,
        "n_opportunities": 0, "n_acted": 0, "n_justified_no_trade": 0,
        "n_orders": 0, "n_rejected_orders": 0, "brier_mean": None,
        "n_scored_forecasts": 0, "n_material_revisions": 0, "prereg_revisions": 0,
        "field_reversals": 0, "listener_useful": 0, "listener_useless": 0,
        "n_influences": 0, "blind_copies": 0, "n_journals": 0, "complete_journals": 0,
        "integrity_events": 0,
    }
    m.update(over)
    return m


def test_lottery_winner_scores_below_consistent(evo_settings):
    lottery = component_scores(_metrics_base(
        net_pnl=100.0, nav=1100.0, n_trades=1, n_eff=1, trade_pnls=[100.0],
        daily_pnls=[100.0], turnover=0.05,
    ), evo_settings)
    steady = component_scores(_metrics_base(
        net_pnl=100.0, nav=1100.0, n_trades=20, n_eff=20,
        trade_pnls=[5.0] * 20, daily_pnls=[20.0] * 5, turnover=1.0,
    ), evo_settings)
    assert steady["consistency"] > lottery["consistency"]
    assert steady["evidence"] > lottery["evidence"]
    assert steady["profit"] > lottery["profit"]  # shrinkage discounts n=1


def test_thrasher_scores_below_preregistered_adapter(evo_settings):
    thrasher = component_scores(_metrics_base(
        n_material_revisions=8, prereg_revisions=0, field_reversals=6,
    ), evo_settings)
    adapter = component_scores(_metrics_base(
        n_material_revisions=2, prereg_revisions=2, field_reversals=0,
        n_journals=10, complete_journals=10,
    ), evo_settings)
    assert adapter["adaptive"] > thrasher["adaptive"]


def test_group_fractions_deterministic():
    class Row:
        def __init__(self, i):
            self.i = i
    s = EvoSettings(_env_file=None)
    for n, expect in ((30, (9, 12, 9)), (10, (3, 4, 3))):
        rows = [Row(i) for i in range(n)]
        groups = group_by_fractions(rows, s)
        assert (len(groups["top"]), len(groups["middle"]), len(groups["bottom"])) == expect


def test_evaluate_cohort_integrity_penalty_and_ranks(evo_session, evo_settings):
    cohort = ensure_current_cohort(evo_session, evo_settings)
    rng = random.Random(5)
    agents = [
        create_agent(evo_session, evo_settings, cohort, rng, origin="founder",
                     slot_key=f"founder:{i}")
        for i in range(3)
    ]
    # give agent 0 three integrity events => disqualified (score 0)
    from kalshi_bot.evo.audit import integrity_violation

    for _ in range(3):
        integrity_violation(evo_session, agents[0].agent_uuid, "tamper")
    rows = evaluate_cohort(
        evo_session, evo_settings, cohort.id, [a.agent_uuid for a in agents],
        kind="interim",
    )
    by_uuid = {r.agent_uuid: r for r in rows}
    bad = by_uuid[agents[0].agent_uuid]
    assert bad.cohort_score == 0.0 and bad.penalties_json.get("disqualified")
    assert bad.rank == 3  # ranked last
    assert all(r.visible_after > r.computed_at for r in rows)


def test_historical_reliability_neutral_then_weighted(evo_session, evo_settings):
    assert historical_reliability(evo_session, evo_settings, "x" * 36, 99) == 50.0
    for cid, score in ((1, 80.0), (2, 60.0)):
        evo_session.add(em.EvoFitness(
            cohort_id=cid, agent_uuid="x" * 36, kind="final",
            computed_at=NOW, visible_after=NOW, components_json={},
            cohort_score=score, reliability_score=50, selection_score=score,
        ))
    evo_session.flush()
    rel = historical_reliability(evo_session, evo_settings, "x" * 36, 99)
    assert 50.0 < rel < 80.0
    # most recent cohort (60) weighted above the older 80
    assert abs(rel - 80.0) > abs(rel - 60.0) or rel < 70


# --- evolution engine -------------------------------------------------------


def _small_pop_settings(**over):
    return EvoSettings(
        _env_file=None, population_size=10, fill_latency_ms=0,
        routine_heartbeats_per_day=1, **over,
    )


def test_bootstrap_founders_idempotent_unique_surnames(evo_session):
    settings = _small_pop_settings()
    created = bootstrap_founders(evo_session, settings)
    assert len(created) == 10
    assert len({a.surname for a in created}) == 10
    assert bootstrap_founders(evo_session, settings) == []
    assert all(float(m.starting_capital) == 1000.0 for m in evo_session.scalars(
        select(em.EvoCohortMember)))


def test_finalization_full_cycle_and_idempotency(evo_session):
    settings = _small_pop_settings()
    bootstrap_founders(evo_session, settings)
    cohort = ensure_current_cohort(evo_session, settings)
    md = _md_with()
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc)
    later = ends + timedelta(minutes=1)
    outcome = maybe_finalize_cohort(
        evo_session, settings, cohort, md=md, cognition=_noop_cognition(), now=later,
    )
    assert outcome is not None
    assert len(outcome["retired"]) == 3
    assert len(outcome["survivors"]) == 7
    assert len(outcome["children"]) == 3
    # population restored to exactly 10 active
    assert len(active_agents(evo_session)) == 10
    # a cohort can never finalize twice
    again = maybe_finalize_cohort(
        evo_session, settings, cohort, md=md, cognition=_noop_cognition(), now=later,
    )
    assert again is None
    # retired agents preserved + graveyard entries + retirement rows
    for au in outcome["retired"]:
        agent = evo_session.scalar(select(em.EvoAgent).where(em.EvoAgent.agent_uuid == au))
        assert agent.status == "retired" and agent.historical_name
        assert evo_session.scalar(select(em.EvoRetirement).where(
            em.EvoRetirement.agent_uuid == au)) is not None
    # children: surname inherited, parent unchanged and active, genome + memory inherited
    for child_uuid in outcome["children"]:
        child = evo_session.scalar(select(em.EvoAgent).where(
            em.EvoAgent.agent_uuid == child_uuid))
        parent = evo_session.scalar(select(em.EvoAgent).where(
            em.EvoAgent.agent_uuid == child.parent_uuid))
        assert child.surname == parent.surname
        assert child.generation == parent.generation + 1
        assert parent.status == "active"
        genomes = list(evo_session.scalars(select(em.EvoGenome).where(
            em.EvoGenome.agent_uuid == child_uuid)))
        assert {g.kind for g in genomes} == {"cognitive", "trading"}
    # children start with neutral reliability (no inherited performance credit)
    for child_uuid in outcome["children"]:
        assert historical_reliability(evo_session, settings, child_uuid, 999) == 50.0
    # cohort ledger for the new cohort opens at exactly 1000 NAV
    next_id = evo_session.scalar(select(em.EvoCohort.id).where(
        em.EvoCohort.status == "open"))
    for au in outcome["survivors"]:
        assert abs(paper.nav(evo_session, au, f"cohort:{next_id}") - 1000.0) < 0.01


def test_wildcard_cohort_creates_new_surname_and_skips_lowest_parent(evo_session):
    settings = _small_pop_settings(wildcard_every_n_cohorts=2)
    bootstrap_founders(evo_session, settings)
    md = _md_with()
    # finalize cohort 1 (next = 2 => wildcard because 2 % 2 == 0)
    cohort = ensure_current_cohort(evo_session, settings)
    ends = cohort.ends_at if cohort.ends_at.tzinfo else cohort.ends_at.replace(
        tzinfo=timezone.utc)
    outcome = maybe_finalize_cohort(
        evo_session, settings, cohort, md=md, cognition=_noop_cognition(),
        now=ends + timedelta(minutes=1),
    )
    assert outcome["wildcard"] is not None
    assert outcome["skipped_parent"] is not None
    assert len(outcome["children"]) == 2  # 3 slots - 1 wildcard
    founder_surnames = {
        a.surname for a in evo_session.scalars(
            select(em.EvoAgent).where(em.EvoAgent.origin == "founder"))
    }
    wc = evo_session.scalar(select(em.EvoAgent).where(
        em.EvoAgent.agent_uuid == outcome["wildcard"]))
    assert wc.origin == "wildcard" and wc.surname not in founder_surnames
    assert wc.parent_uuid is None and wc.generation == 1
    assert len(active_agents(evo_session)) == 10


def test_create_agent_slot_idempotent(evo_session, evo_settings):
    cohort = ensure_current_cohort(evo_session, evo_settings)
    rng = random.Random(9)
    a1 = create_agent(evo_session, evo_settings, cohort, rng, origin="founder",
                      slot_key="dup")
    a2 = create_agent(evo_session, evo_settings, cohort, rng, origin="founder",
                      slot_key="dup")
    assert a1.agent_uuid == a2.agent_uuid
    assert evo_session.scalar(
        select(em.EvoAgent.id).where(em.EvoAgent.agent_uuid != a1.agent_uuid)
    ) is None
