"""Control arm: an absolute reference beside the relative ranking.

Fitness ranks agents against each other. That is fine when someone is winning and
useless when nobody is: with every agent -EV the top-ranked one is merely the
least-bad, and it is exactly the agent that reproduces. The population then
optimizes toward losing slightly less than its peers, and nothing in the system
can say so out loud. Live, all ten active strategies holding positions were net
negative and the fleet had already retired the only three that were positive.

Three controls, none of which competes:

  null              holds cash. P&L is identically 0.00 -- the bar every strategy
                    must clear before "it works" means anything. Its second job is
                    as an accounting canary: a non-zero null is a bookkeeping bug.
  market_cheap      takes the cheap side of every admitted candidate, holds to
                    settlement.
  market_expensive  the same, on the expensive side.

Why two market controls rather than one: always buying the cheap side is not a
neutral policy, it systematically buys longshots, which carries its own known
bias. Their MEAN is the side-neutral cost of participating with no opinion; their
DIFFERENCE measures the favorite-longshot bias in our own universe -- a number we
have never had and one that matters, since much of the fleet is longshot buys.

Design constraints that make the comparison honest:

  * same universe -- they consume the orchestrator's own per-cycle candidate list,
    not a separate scan;
  * same capital, size and fee model as any agent;
  * taker only, hold to settlement -- no maker fills (fill selection would muddy
    the baseline) and no exit rule, so the benchmark measures ENTRY selection and
    an agent that wins on exits still reads as beating it;
  * compared on cents per position, never totals -- a control trades far more
    often than a typical agent, so only the scale-free number is meaningful;
  * `status='control'`, which keeps them out of the live set, the population cap,
    heartbeats, ranking, retirement and reproduction BY CONSTRUCTION, because
    every one of those paths selects on `status='active'`.

They take no heartbeats, so they cost nothing in LLM tokens.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select

from . import paper as papermod
from .audit import audit
from .cohorts import join_cohort
from .config import EvoSettings
from .models import EvoAgent, EvoCohort, EvoPosition, EvoStrategy
from .sandbox import activate_strategy, save_strategy

logger = logging.getLogger(__name__)

CONTROL_STATUS = "control"

NULL = "null"
MARKET_CHEAP = "market_cheap"
MARKET_EXPENSIVE = "market_expensive"
CONTROL_NAMES = (NULL, MARKET_CHEAP, MARKET_EXPENSIVE)

# Deliberately wide: the control must keep trading to stay a live benchmark, so it
# is not gated on price band, series, spread or volume. max_concurrent_positions is
# bounded well above a typical agent's but far below the point where it could tie up
# its whole book and stop trading (50 x ~$2.50 leaves most of the $1000 free).
_MARKET_SPEC = {
    "family": "control",
    "universe": {"series_prefixes": [], "min_volume": 0, "max_spread_cents": 99,
                 "min_hours_to_close": 0.0, "max_hours_to_close": 24 * 30},
    "entry": {"style": "taker", "min_price_cents": 1, "max_price_cents": 99,
              "size_contracts": 5},
    "exit": {"mode": "settlement"},
    "risk": {"max_concurrent_positions": 50, "max_per_event": 1,
             "max_cost_per_position_usd": 50.0},
}

_DESCRIPTIONS = {
    NULL: "CONTROL — holds cash and never trades. The absolute bar: a strategy that "
          "does not beat 0.00 has found nothing.",
    MARKET_CHEAP: "CONTROL — takes the cheap side of every admitted candidate and holds "
                  "to settlement. The cost of participating with no opinion.",
    MARKET_EXPENSIVE: "CONTROL — takes the expensive side of every admitted candidate "
                      "and holds to settlement. Paired with market_cheap to separate "
                      "the cost of trading from the favorite-longshot bias.",
}

_SIDES = {MARKET_CHEAP: "cheap", MARKET_EXPENSIVE: "expensive"}


def _spec_for(name: str) -> dict:
    return {
        **_MARKET_SPEC,
        "name": f"control_{name}",
        "description": _DESCRIPTIONS[name],
        "entry": {**_MARKET_SPEC["entry"], "side": _SIDES[name]},
    }


def control_agents(session) -> list[EvoAgent]:
    return list(
        session.scalars(
            select(EvoAgent)
            .where(EvoAgent.status == CONTROL_STATUS)
            .order_by(EvoAgent.id)
        )
    )


def ensure_controls(
    session, settings: EvoSettings, cohort: EvoCohort
) -> list[EvoAgent]:
    """Idempotently create/fund the control arm and join it to `cohort`.

    Safe to call every cycle: returns only the agents created THIS call. Joining
    each cohort is what keeps a control funded and comparable in the window the
    fleet is currently being scored over."""
    existing = {a.first_name: a for a in control_agents(session)}
    created: list[EvoAgent] = []
    for name in CONTROL_NAMES:
        agent = existing.get(name)
        if agent is None:
            agent = EvoAgent(
                agent_uuid=str(uuid.uuid4()),
                first_name=name,
                surname="control",
                display_name=f"{name} (control)",
                historical_name=f"{name} (control)",
                agent_code=f"CTL-{name[:8].upper()}",
                founder_uuid="",
                parent_uuid=None,
                generation=0,
                origin="control",
                birth_cohort_id=cohort.id,
                status=CONTROL_STATUS,
            )
            agent.founder_uuid = agent.agent_uuid
            session.add(agent)
            session.flush()
            papermod.ensure_portfolio(session, agent.agent_uuid, papermod.LIFETIME,
                                      settings.starting_capital_usd)
            created.append(agent)
            audit(session, "control_created", agent_uuid=agent.agent_uuid, name=name,
                  cohort_id=cohort.id)
        join_cohort(session, agent, cohort, settings)
        if name in _SIDES:
            _ensure_active_strategy(session, settings, agent, name)
    return created


def _ensure_active_strategy(
    session, settings: EvoSettings, agent: EvoAgent, name: str
) -> None:
    """One fixed, never-mutated spec per market control. A benchmark that drifted
    would stop being a benchmark, so this is written once and only re-activated."""
    existing = session.scalar(
        select(EvoStrategy).where(
            EvoStrategy.agent_uuid == agent.agent_uuid,
            EvoStrategy.name == f"control_{name}",
        )
    )
    if existing is None:
        row, err = save_strategy(
            session, settings, agent_uuid=agent.agent_uuid, spec_doc=_spec_for(name)
        )
        if err or row is None:  # pragma: no cover - the spec is a constant
            logger.warning("evo control spec rejected", extra={
                "extra_fields": {"control": name, "error": err}})
            return
        existing = row
    if existing.status != "active":
        activate_strategy(session, agent.agent_uuid, existing.id)


def benchmark(session, cohort_id: int) -> dict[str, dict]:
    """Per-control results on this cohort's ledger.

    `cents_per_position` is the comparison number: controls trade far more often
    than a typical agent, so totals are not comparable across them and only the
    scale-free per-position figure means anything."""
    ledger = papermod.cohort_ledger(cohort_id)
    by_uuid = {a.agent_uuid: a.first_name for a in control_agents(session)}
    out = {
        name: {"n_positions": 0, "realized_usd": 0.0, "fees_usd": 0.0,
               "cents_per_position": None}
        for name in CONTROL_NAMES
    }
    if not by_uuid:
        return out
    rows = session.execute(
        select(
            EvoPosition.agent_uuid,
            func.count(EvoPosition.id),
            func.sum(EvoPosition.realized_pnl_usd),
            func.sum(EvoPosition.fees_usd),
        )
        .where(
            EvoPosition.agent_uuid.in_(list(by_uuid)),
            EvoPosition.ledger == ledger,
            EvoPosition.status.in_(("closed", "settled", "liquidated")),
        )
        .group_by(EvoPosition.agent_uuid)
    )
    for agent_uuid, n, realized, fees in rows:
        name = by_uuid.get(agent_uuid)
        if name is None:  # pragma: no cover - in() guarantees membership
            continue
        realized = float(realized or 0.0)
        out[name] = {
            "n_positions": int(n),
            "realized_usd": round(realized, 4),
            "fees_usd": round(float(fees or 0.0), 4),
            "cents_per_position": round(100.0 * realized / n, 2) if n else None,
        }
    return out


def benchmark_summary(session, cohort_id: int) -> dict:
    """The two numbers the control arm exists to produce.

    participation_cents — the side-neutral cost of trading with no opinion (the mean
        of the two market controls). A strategy below this is worse than no signal.
    longshot_gap_cents  — cheap minus expensive. How much the favorite-longshot bias
        is worth in our own universe, which no book has ever measured directly.
    Either is None until both controls have settled positions; a half-measured mean
    would read as a real number and be wrong."""
    bench = benchmark(session, cohort_id)
    cheap = bench[MARKET_CHEAP]["cents_per_position"]
    expensive = bench[MARKET_EXPENSIVE]["cents_per_position"]
    both = cheap is not None and expensive is not None
    return {
        "controls": bench,
        "participation_cents": round((cheap + expensive) / 2.0, 2) if both else None,
        "longshot_gap_cents": round(cheap - expensive, 2) if both else None,
    }
