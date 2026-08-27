"""An existing Evo agent invokes the historical search, and decides for itself.

This is the test that the search capability is a *capability* rather than a second
organism. It drives a real agent through a real heartbeat, has it call
`search_strategy_space`, and then checks two things that pull in opposite directions:

  * the agent got back evidence it can act on — a scored base, a ranked neighbourhood,
    refusals with reasons, and a document its own `save_strategy` accepts;
  * running the search changed **nothing** about the agent. No genome revision, no
    strategy, no fitness row, no birth, no retirement. Whether the better variant is
    adopted or ignored is decided in a later heartbeat, by the agent, through the
    organism's own action path.

The corpus is a seeded weather backfill with a deliberate structure: markets priced
below ~45c are systematically underpriced and markets above ~65c are systematically
overpriced, so an entry ceiling that is too high is a real, findable defect. The agent's
starting strategy has that defect.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo import budgets, llm, sandbox
from kalshi_bot.evo import models as em
from kalshi_bot.evo.cognition import ACTION_PROTOCOL, ScriptedCognition
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.constitution import PERMITTED_ACTIONS
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.genomes import default_trading, write_genome_revision
from kalshi_bot.evo.heartbeats import run_heartbeat
from kalshi_bot.evo.marketdata import StaticMarketData
from kalshi_bot.evo.search import genome as g
from kalshi_bot.evo.search import proving, search
from kalshi_bot.evo.search.models import EvoSearchCandidate, EvoSearchRun, EvoSearchTrade
from kalshi_bot.models import BackfillWeatherCandle, BackfillWeatherMarket, Base

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
WINDOW = ("2026-01-01", "2026-03-01")
DIMENSIONS = ["entry.max_price_cents", "entry.min_price_cents"]

#: The strategy the agent starts with. Its entry ceiling is 90c, which reaches deep into
#: the overpriced end of the seeded corpus — the defect the search exists to surface.
STRATEGY_NAME = "ny-high-band"


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def _u(*parts: object) -> float:
    """Deterministic pseudo-uniform, so the corpus is the same on every machine."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:7], "big") / float(1 << 56)


def _seed_weather(session, *, days: int = 60, per_day: int = 3) -> None:
    """A settled corpus with a price-dependent edge.

    Each market has a true probability `p`. Cheap markets trade 12c BELOW it and
    expensive ones 12c ABOVE, so buying yes is +EV at the bottom of the book and -EV at
    the top. Nothing about that is visible in a single market's quote — it is only
    findable by comparing entry bands, which is exactly the question the search answers.
    """
    for day in range(days):
        date = (EPOCH + timedelta(days=day)).date().isoformat()
        close = EPOCH + timedelta(days=day, hours=20)
        for k in range(per_day):
            ticker = f"KXHIGHNY-{day:03d}{k}"
            p = 0.10 + 0.85 * _u("p", day, k)
            skew = 12.0 if p < 0.55 else -12.0
            mid = max(4.0, min(95.0, round(100 * p - skew)))
            session.add(
                BackfillWeatherMarket(
                    market_ticker=ticker,
                    event_ticker=f"KXHIGHNY-{day:03d}",
                    series_ticker="KXHIGHNY",
                    city="NY",
                    kind="high",
                    target_date=date,
                    result=("yes" if _u("outcome", day, k) < p else "no"),
                    open_time=close - timedelta(hours=8),
                    close_time=close,
                    candles_fetched=True,
                    candle_count=4,
                )
            )
            for hour in range(4):
                session.add(
                    BackfillWeatherCandle(
                        market_ticker=ticker,
                        end_period_ts=close - timedelta(hours=6 - hour),
                        period_minutes=60,
                        price_open=mid,
                        price_high=mid + 2,
                        price_low=mid - 2,
                        price_close=mid,
                        yes_bid_close=mid - 1,
                        yes_ask_close=mid + 1,
                        volume=500,
                    )
                )
    session.flush()


def _spec(max_price_cents: int = 90) -> dict:
    doc, err = g.normalize(
        g.spec_document(
            name=STRATEGY_NAME,
            family="weather-band",
            universe={
                "series_prefixes": ["KXHIGHNY"],
                "max_hours_to_close": 48,
                "max_spread_cents": 6,
            },
            entry={
                "side": "yes",
                "style": "taker",
                "min_price_cents": 5,
                "max_price_cents": max_price_cents,
                "size_contracts": 5,
            },
            exit_={"mode": "settlement"},
            risk={"max_concurrent_positions": 10, "max_cost_per_position_usd": 60.0},
        )
    )
    assert err is None, err
    return doc


def _agent(session, settings, *, with_strategy: bool = True):
    llm.seed_model_prices(session, settings)
    cohort = ensure_current_cohort(session, settings)
    agent = create_agent(
        session, settings, cohort, random.Random(1),
        origin="founder", slot_key="founder:0",
    )
    budgets.ensure_budgets(session, agent.agent_uuid, cohort.id, settings)
    if with_strategy:
        row, err = sandbox.save_strategy(
            session, settings, agent_uuid=agent.agent_uuid, spec_doc=_spec()
        )
        assert row is not None, err
        sandbox.activate_strategy(session, agent.agent_uuid, row.id)
    return agent, cohort


def _search_action(**overrides) -> dict:
    action = {
        "type": "search_strategy_space",
        "dataset": "backfill_weather",
        "date_from": WINDOW[0],
        "date_to": WINDOW[1],
        "dimensions": DIMENSIONS,
        "neighbourhood": 8,
    }
    action.update(overrides)
    return action


def _beat(session, settings, agent, cohort, slot: str, actions):
    """One heartbeat that emits `actions` (a list, or a callable of the context)."""
    cog = ScriptedCognition(
        lambda ctx: {
            "journal": {"decision": "search the neighbourhood of my entry band"},
            "actions": actions(ctx) if callable(actions) else actions,
        }
    )
    return run_heartbeat(
        session, settings, agent=agent, cohort=cohort, kind="routine",
        slot_id=slot, cognition=cog, md=StaticMarketData(),
    )


def _organism_state(session, agent_uuid: str) -> dict:
    """Everything the search must not touch."""
    def count(model, *where):
        return session.scalar(
            select(func.count()).select_from(model).where(*where)
        )

    return {
        "genomes": count(em.EvoGenome, em.EvoGenome.agent_uuid == agent_uuid),
        "strategies": count(em.EvoStrategy, em.EvoStrategy.agent_uuid == agent_uuid),
        "fitness": count(em.EvoFitness, em.EvoFitness.agent_uuid == agent_uuid),
        "births": count(em.EvoBirth, em.EvoBirth.child_uuid == agent_uuid),
        "retirements": count(em.EvoRetirement, em.EvoRetirement.agent_uuid == agent_uuid),
        # Cohort finalization is per-cohort, not per-agent: a search must not cause one.
        "transitions": count(em.EvoTransition),
        "orders": count(em.EvoOrder, em.EvoOrder.agent_uuid == agent_uuid),
        "positions": count(em.EvoPosition, em.EvoPosition.agent_uuid == agent_uuid),
        "agents": count(em.EvoAgent),
    }


# ---------------------------------------------------------------------------
# The agent asks, and gets evidence
# ---------------------------------------------------------------------------


def test_an_agent_invokes_the_search_and_receives_evidence():
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)

    hb = _beat(session, settings, agent, cohort, "s1", [_search_action()])
    outcome = hb.actions_json[0]
    assert outcome.get("ok"), outcome
    evidence = outcome["result"]

    # The base is measured on the same footing as its variants, so "did anything beat
    # it" is a like-for-like comparison rather than an assumption.
    assert evidence["base"]["search_score"] is not None
    assert evidence["base"]["n_trades"] > 0
    assert evidence["summary"]["ranked"] >= 1

    # Every ranked variant explains itself: what changed, what it did, and which
    # components moved the score.
    top = evidence["candidates"][0]
    assert top["rank"] == 1
    assert top["changes"] and top["why"]
    assert top["search_score"] > evidence["base"]["search_score"]

    # The defect the corpus was built around is the one the search surfaced.
    assert any("entry.max_price_cents" in c for c in top["changes"])
    assert "decide whether that is a reason to revise" in evidence["summary"]["finding"]

    # Refusals are evidence about the search space, not something to drop.
    for refusal in evidence["refused"]:
        assert refusal["stage"] and refusal["reason"]


def test_the_run_is_attributable_to_the_agent_that_asked():
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)

    hb = _beat(session, settings, agent, cohort, "s1", [_search_action()])
    run = session.scalar(select(EvoSearchRun))
    assert run is not None
    assert run.agent_uuid == agent.agent_uuid
    assert run.cohort_id == cohort.id
    assert run.heartbeat_id == hb.id
    # The organism's own view of this agent at the moment it asked.
    assert run.genome_revision == agent.trading_genome_rev
    assert run.dataset == "backfill_weather"
    assert [run.window_start, run.window_end] == list(WINDOW)

    # Candidates and their tapes hang off the run, so "why did this variant do better?"
    # is answerable with trades rather than a number.
    n_candidates = session.scalar(
        select(func.count()).select_from(EvoSearchCandidate)
        .where(EvoSearchCandidate.run_id == run.id)
    )
    assert n_candidates == run.proposals_made + 1  # every proposal, admitted or not
    assert session.scalar(select(func.count()).select_from(EvoSearchTrade)) > 0


def test_the_search_is_charged_to_the_agents_own_sandbox_budget():
    """A search is several replays. It spends the same budget a hand-written backtest
    spends, so an agent cannot buy unlimited compute by phrasing a question as a
    search."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)
    before = budgets.remaining(session, agent.agent_uuid, cohort.id, "sandbox_runs")

    _beat(session, settings, agent, cohort, "s1", [_search_action(neighbourhood=4)])

    after = budgets.remaining(session, agent.agent_uuid, cohort.id, "sandbox_runs")
    assert before - after == 5  # the base replay plus four variants


# ---------------------------------------------------------------------------
# ...and the search decides nothing
# ---------------------------------------------------------------------------


def test_running_a_search_changes_nothing_about_the_agent():
    """The structural claim. A search writes `evo_search_*` and nothing else: no genome,
    no strategy, no fitness, no birth, no retirement, no order."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)
    before = _organism_state(session, agent.agent_uuid)
    trading_rev, cognitive_rev = agent.trading_genome_rev, agent.cognitive_genome_rev

    hb = _beat(session, settings, agent, cohort, "s1", [_search_action()])
    assert hb.actions_json[0].get("ok")
    # The search found a better variant — and still did not apply it.
    assert hb.actions_json[0]["result"]["summary"]["best_beats_base"]

    assert _organism_state(session, agent.agent_uuid) == before
    session.refresh(agent)
    assert (agent.trading_genome_rev, agent.cognitive_genome_rev) == (
        trading_rev, cognitive_rev
    )
    assert agent.status == "active"


def test_the_agent_adopts_the_better_variant_itself():
    """CHOOSE, end to end through the agent's own action protocol.

    Three heartbeats, no test-side shortcuts: the agent searches, then saves the winner,
    then activates it by the integer id the save handed back. Every step is an ordinary
    permitted action executed by `execute_actions` against the agent's own budgets and
    audit — nothing on the search side has the authority to do any of it."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)

    first = _beat(session, settings, agent, cohort, "s1", [_search_action()])
    evidence = first.actions_json[0]["result"]
    winner = evidence["candidates"][0]["document"]
    assert winner["entry"]["max_price_cents"] < 90  # the ceiling the base got wrong

    # Heartbeat 2: the agent decides, saves, and says why. Saving is NOT deploying, and
    # the outcome says so — it hands back the id the next action needs.
    second = _beat(
        session, settings, agent, cohort, "s2",
        [
            {"type": "save_strategy", "spec": winner},
            {
                "type": "revise_belief",
                "title": "my entry ceiling was too high",
                "new_belief": "Above ~65c this book is systematically overpriced.",
                "evidence_for": f"search run {evidence['run_id']}",
                "confidence": 0.6,
            },
        ],
    )
    saved = second.actions_json[0]
    assert saved.get("ok"), saved
    assert saved["status"] == "validated"  # saved, not yet trading
    assert "NOT trading yet" in saved["next"]
    strategy_id = saved["strategy_id"]

    # Heartbeat 3: the agent deploys it, through the action protocol.
    third = _beat(
        session, settings, agent, cohort, "s3",
        [{"type": "activate_strategy", "strategy_id": strategy_id}],
    )
    activated = third.actions_json[0]
    assert activated.get("ok"), activated
    assert activated["status"] == "active"

    rows = session.scalars(
        select(em.EvoStrategy)
        .where(em.EvoStrategy.agent_uuid == agent.agent_uuid)
        .order_by(em.EvoStrategy.revision)
    ).all()
    assert [r.revision for r in rows] == [1, 2]
    assert rows[0].spec_json["entry"]["max_price_cents"] == 90
    assert rows[1].spec_json["entry"]["max_price_cents"] == winner["entry"]["max_price_cents"]
    assert (rows[0].status, rows[1].status) == ("inactive", "active")
    # It is the AGENT's heartbeats that carry the change, not the search run.
    assert rows[1].heartbeat_id == second.id
    # And the search the agent acted on is still attributable to the heartbeat it ran in.
    assert session.get(EvoSearchRun, evidence["run_id"]).heartbeat_id == first.id


def test_the_agent_may_decline_the_better_variant():
    """REJECT. The same evidence, and the agent does not act on it. Nothing in the
    system adopts it on the agent's behalf — one window is one window, and an agent that
    reads a +0.09 score as too thin to move on is behaving correctly."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)

    hb = _beat(session, settings, agent, cohort, "s1", [_search_action()])
    assert hb.actions_json[0]["result"]["summary"]["best_beats_base"]

    _beat(
        session, settings, agent, cohort, "s2",
        [{
            "type": "note_episode",
            "title": "held the entry band",
            "detail": "One window, 129 trades. Not enough to move a live band yet.",
        }],
    )

    rows = session.scalars(
        select(em.EvoStrategy).where(em.EvoStrategy.agent_uuid == agent.agent_uuid)
    ).all()
    assert [r.revision for r in rows] == [1]
    assert rows[0].spec_json["entry"]["max_price_cents"] == 90
    assert rows[0].status == "active"
    # The evidence survives the refusal: the agent can revisit it, and can see that it
    # already asked this question.
    assert search.recent_searches(session, agent.agent_uuid)[0]["dimensions"] == DIMENSIONS


# ---------------------------------------------------------------------------
# Defaults and boundaries
# ---------------------------------------------------------------------------


def test_a_search_defaults_to_the_agents_own_active_strategy():
    """Omitting `spec` searches around what the agent is actually running — read from
    `evo_strategies`, which is where the organism keeps executable strategies. The
    trading genome is policy prose and forbids extra keys, so there is nothing
    replayable inside it to search."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)

    hb = _beat(session, settings, agent, cohort, "s1", [_search_action()])
    assert hb.actions_json[0].get("ok"), hb.actions_json[0]

    run = session.scalar(select(EvoSearchRun))
    assert run.base_strategy_name == STRATEGY_NAME
    assert run.base_genome_hash == g.genome_hash(_spec())


def test_an_agent_with_no_saved_strategy_is_told_what_to_do():
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings, with_strategy=False)
    _seed_weather(session)

    hb = _beat(session, settings, agent, cohort, "s1", [_search_action()])
    assert "save_strategy first" in hb.actions_json[0]["rejected"]
    assert session.scalar(select(func.count()).select_from(EvoSearchRun)) == 0


def test_an_agent_cannot_search_a_synthetic_corpus():
    """The proving fixtures run through the real replay loop, but they are not history.
    `sandbox.DATASETS` is what the agent-facing path validates against and registering a
    corpus deliberately does not extend it, so no agent can read a fixture as evidence."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    proving.register()
    assert proving.DATASET in sandbox.available_datasets()
    assert proving.DATASET not in sandbox.DATASETS

    hb = _beat(
        session, settings, agent, cohort, "s1",
        [_search_action(dataset=proving.DATASET)],
    )
    assert "unknown dataset" in hb.actions_json[0]["rejected"]


def test_the_action_is_permitted_and_documented():
    assert "search_strategy_space" in PERMITTED_ACTIONS
    assert "search_strategy_space" in ACTION_PROTOCOL
    # The agent is told, in the protocol it reads every heartbeat, that this measures
    # rather than decides. If that sentence goes, the framing goes with it.
    assert "MEASURING TOOL, not a decision" in ACTION_PROTOCOL


# ---------------------------------------------------------------------------
# The agent's own hypothesis, not the tool's
# ---------------------------------------------------------------------------


def test_an_agent_names_the_variant_it_wants_tested():
    """Deterministic perturbation is the fallback, not the intelligence.

    An agent with a thesis — "above 70c this book is systematically overpriced" — says so
    in `proposals`, and gets that exact variant measured with its hypothesis recorded
    against the result. Nothing steps a gene on its behalf."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)

    thesis = "above ~65c this book is systematically overpriced"
    hb = _beat(
        session, settings, agent, cohort, "s1",
        [_search_action(
            neighbourhood=2,
            dimensions=None,
            proposals=[
                {"path": "entry.max_price_cents", "value": 65, "hypothesis": thesis},
                {"path": "entry.max_price_cents", "value": 45, "hypothesis": thesis},
            ],
        )],
    )
    evidence = hb.actions_json[0]["result"]
    assert hb.actions_json[0].get("ok"), hb.actions_json[0]

    tested = {
        c["document"]["entry"]["max_price_cents"]: c for c in evidence["candidates"]
    }
    assert set(tested) == {65, 45}, "the tool tested something other than what was asked"
    for candidate in tested.values():
        assert candidate["hypothesis"] == thesis  # recorded against the result

    # The agent's thesis is the correct one on this corpus, and both named variants beat
    # the base — but the search still only says so as a finding.
    assert all(c["search_score"] > evidence["base"]["search_score"] for c in tested.values())
    assert "decide whether that is a reason to revise" in evidence["summary"]["finding"]


def test_a_named_proposal_outside_the_gene_surface_is_refused_not_ignored():
    """Silently dropping it would look like the search ran the test and found nothing."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)

    hb = _beat(
        session, settings, agent, cohort, "s1",
        [_search_action(proposals=[{"path": "entry.vibes", "value": 3}])],
    )
    assert "not a gene on the mutation surface" in hb.actions_json[0]["rejected"]
    # Refused before anything was written: no half-finished run.
    assert session.scalar(select(func.count()).select_from(EvoSearchRun)) == 0


def test_naming_the_value_a_gene_already_has_is_refused():
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)

    hb = _beat(
        session, settings, agent, cohort, "s1",
        [_search_action(proposals=[{"path": "entry.max_price_cents", "value": 90}])],
    )
    assert "tests nothing" in hb.actions_json[0]["rejected"]


def test_a_named_proposal_is_gated_like_any_other():
    """Naming a mutation is not a way past the gates: the same five run on it."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)

    hb = _beat(
        session, settings, agent, cohort, "s1",
        # 95c floor against a 90c ceiling: a legal value for the gene, incoherent with
        # the rest of the genome.
        [_search_action(neighbourhood=1,
                        proposals=[{"path": "entry.min_price_cents", "value": 95}])],
    )
    evidence = hb.actions_json[0]["result"]
    assert evidence["summary"]["proposals_admitted"] == 0
    assert evidence["refused"][0]["stage"] == "compatibility"
    assert "inverted" in evidence["refused"][0]["reason"]


# ---------------------------------------------------------------------------
# A refused search costs nothing
# ---------------------------------------------------------------------------


def _sandbox_runs(session, agent, cohort) -> float:
    return budgets.remaining(session, agent.agent_uuid, cohort.id, "sandbox_runs")


def test_a_refused_search_leaves_the_budget_untouched():
    """`run_search` validates the whole call before replaying anything, so a refusal
    must not move the organism's budget ledger either. Charging for a search that
    wrote nothing and replayed nothing would make 'refused before anything is written'
    false in the one ledger the agent actually feels."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)
    before = _sandbox_runs(session, agent, cohort)

    refusals = [
        # not a gene at all
        _search_action(proposals=[{"path": "entry.vibes", "value": 3}]),
        # a value the gene already holds — tests nothing
        _search_action(proposals=[{"path": "entry.max_price_cents", "value": 90}]),
        # an explicit base spec that does not cohere (min above max)
        _search_action(spec={
            "name": "broken-band", "family": "x",
            "entry": {"min_price_cents": 95, "max_price_cents": 20},
        }),
        # a fixture corpus, which is never readable as evidence
        _search_action(dataset="synthetic:proving"),
    ]
    for i, action in enumerate(refusals):
        hb = _beat(session, settings, agent, cohort, f"r{i}", [action])
        assert hb.actions_json[0].get("rejected"), hb.actions_json[0]
        assert _sandbox_runs(session, agent, cohort) == before, action

    # ...and nothing was written either.
    assert session.scalar(select(func.count()).select_from(EvoSearchRun)) == 0


def test_an_agent_with_no_saved_strategy_is_not_charged():
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings, with_strategy=False)
    _seed_weather(session)
    before = _sandbox_runs(session, agent, cohort)

    hb = _beat(session, settings, agent, cohort, "s1", [_search_action()])
    assert "save_strategy first" in hb.actions_json[0]["rejected"]
    assert _sandbox_runs(session, agent, cohort) == before


def test_a_search_is_charged_for_the_replays_that_actually_ran():
    """Not for the neighbourhood that was requested. Proposals refused at the gates
    never reach the replay engine, so charging for them would bill an agent for work
    the tool declined to do."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)
    before = _sandbox_runs(session, agent, cohort)

    hb = _beat(session, settings, agent, cohort, "s1", [_search_action(neighbourhood=8)])
    outcome = hb.actions_json[0]
    assert outcome.get("ok"), outcome
    summary = outcome["result"]["summary"]

    replayed = summary["candidates_replayed"]
    assert replayed == summary["proposals_admitted"] + 1  # the base plus what survived
    assert outcome["sandbox_runs_charged"] == replayed
    assert before - _sandbox_runs(session, agent, cohort) == replayed
    # This run refuses some proposals, so the charge is strictly below the request.
    assert summary["proposals_made"] > summary["proposals_admitted"]
    assert replayed < 8 + 1


def test_a_search_it_cannot_afford_is_refused_before_it_runs():
    """Affordability is checked against the worst case, so an agent cannot start a
    search it could not pay for and discover that halfway through."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings)
    _seed_weather(session)

    # Spend the budget down to less than the requested neighbourhood + 1.
    remaining = _sandbox_runs(session, agent, cohort)
    budgets.spend(session, agent.agent_uuid, cohort.id, "sandbox_runs", remaining - 3)
    assert _sandbox_runs(session, agent, cohort) == 3

    hb = _beat(session, settings, agent, cohort, "s1", [_search_action(neighbourhood=8)])
    assert hb.actions_json[0]["rejected"] == "sandbox-run budget exhausted"
    assert _sandbox_runs(session, agent, cohort) == 3  # nothing spent
    assert session.scalar(select(func.count()).select_from(EvoSearchRun)) == 0


# ---------------------------------------------------------------------------
# Which strategy a search resolves to
# ---------------------------------------------------------------------------


def _save(session, settings, agent, max_price: int, *, name: str = STRATEGY_NAME):
    doc = _spec(max_price)
    doc["name"] = name
    row, err = sandbox.save_strategy(
        session, settings, agent_uuid=agent.agent_uuid, spec_doc=doc
    )
    assert row is not None, err
    return row


def test_a_named_strategy_resolves_to_the_revision_actually_running():
    """`evo_strategies` versions by (agent, name, revision), so the NEWEST row under a
    named strategy can be one the agent saved and never deployed — or deployed and then
    replaced. Matching on name alone would hand the search a spec the agent is not
    running, and every variant would then be measured against the wrong base."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings, with_strategy=False)

    running = _save(session, settings, agent, 70)
    sandbox.activate_strategy(session, agent.agent_uuid, running.id)
    # A later revision of the SAME name, saved but never activated.
    drafted = _save(session, settings, agent, 40)
    assert drafted.revision > running.revision and drafted.status == "validated"

    # Point the trading genome at the name, the way an adopting agent does.
    genome, err = write_genome_revision(
        session, agent, "trading",
        dict(default_trading(), active_strategy_name=STRATEGY_NAME),
    )
    assert genome is not None, err

    own = search.current_strategy(session, agent.agent_uuid)
    assert own.strategy_name == STRATEGY_NAME
    assert own.document["entry"]["max_price_cents"] == 70  # the running one, not the newer
    assert own.genome_revision == genome.revision


def test_a_named_strategy_that_is_no_longer_runnable_falls_through():
    """If every revision of the named strategy has been taken out of service, the name
    is stale. Fall back to what is actually deployed rather than searching around a
    strategy the agent explicitly stopped running."""
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings, with_strategy=False)

    stale = _save(session, settings, agent, 70, name="retired-band")
    sandbox.activate_strategy(session, agent.agent_uuid, stale.id)
    sandbox.deactivate_strategy(session, agent.agent_uuid, stale.id, reason="retired")
    assert session.get(em.EvoStrategy, stale.id).status == "inactive"

    live = _save(session, settings, agent, 55, name="live-band")
    sandbox.activate_strategy(session, agent.agent_uuid, live.id)

    genome, err = write_genome_revision(
        session, agent, "trading",
        dict(default_trading(), active_strategy_name="retired-band"),
    )
    assert genome is not None, err

    own = search.current_strategy(session, agent.agent_uuid)
    assert own.strategy_name == "live-band"
    assert own.document["entry"]["max_price_cents"] == 55


def test_with_no_named_strategy_the_active_one_wins_over_a_merely_validated_one():
    session = _session()
    settings = EvoSettings(_env_file=None, fill_latency_ms=0)
    agent, cohort = _agent(session, settings, with_strategy=False)

    live = _save(session, settings, agent, 60, name="deployed")
    sandbox.activate_strategy(session, agent.agent_uuid, live.id)
    _save(session, settings, agent, 30, name="never-deployed")  # validated only

    own = search.current_strategy(session, agent.agent_uuid)
    assert own.strategy_name == "deployed"
    assert own.document["entry"]["max_price_cents"] == 60
