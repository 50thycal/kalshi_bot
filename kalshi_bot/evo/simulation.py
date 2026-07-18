"""Deterministic multi-generation evolutionary simulation (spec §36.3).

Runs the REAL system — cohorts, budgets, listeners, strategies, paper engine,
fitness, transitions, reproduction, wildcards — against a synthetic seeded market
and ScriptedCognition behavior profiles (including the adversarial roster), in
minutes of wall time. Seed-reproducible: same seed => identical rankings,
retirements and lineage.

Time model: agent/cohort scheduling runs on a simulated clock (sim_now, starting
on a Monday boundary); market quotes are rendered with real-relative timestamps
(captured_at = wall now, close_time = wall now + remaining sim hours) so the
production staleness/fill/horizon logic operates unchanged.

Profiles (assigned by founder index, inherited by descendants):
  consistent        modest real edge, small size, diversified      (should thrive)
  adapter           weak start, pre-registered revision to the edge (should improve)
  aggressive        real edge, oversized + concentrated            (drawdown-limited)
  copier            blindly copies the leader's strategy, no modifications
  lottery           one huge longshot bet                          (statistically discounted)
  thrasher          revises policy constantly                      (capped + penalized)
  hog               burns sandbox/tool budgets on no-value work
  stale             trades only a market whose data is stale       (fails closed)
  rewriter          attempts history rewrites + forbidden actions  (integrity penalties)
  inactive          never acts                                     (no incubation credit)
  fair              trades fair-priced markets (zero edge)
  opposing_a/b      opposite sides of the same markets
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from ..models import Base
from . import listeners as listeners_mod
from . import paper as papermod
from . import strategy_runner
from .cognition import HeartbeatContext, ScriptedCognition
from .cohorts import active_agents, cohort_is_over, ensure_current_cohort
from .config import EvoSettings
from .constitution import ensure_config_version
from .datasources import seed_builtin_sources
from .evolution import create_agent, maybe_finalize_cohort
from .fitness import evaluate_cohort
from .graveyard_seed import seed_graveyard
from .heartbeats import run_due_heartbeats
from .llm import seed_model_prices
from .marketdata import Quote, StaticMarketData
from .models import EvoAgent, EvoBirth, EvoCohort, EvoRetirement, EvoTransition

PROFILES = (
    "consistent", "adapter", "aggressive", "copier", "lottery", "thrasher",
    "hog", "stale", "rewriter", "inactive", "fair", "opposing_a", "opposing_b",
)

# 30 founders: weight the roster so several correlated relatives exist (spec).
FOUNDER_PROFILES = (
    ["consistent"] * 6
    + ["adapter"] * 3
    + ["aggressive"] * 3
    + ["copier"] * 2
    + ["lottery"] * 2
    + ["thrasher"] * 2
    + ["hog"] * 2
    + ["stale"] * 2
    + ["rewriter"] * 2
    + ["inactive"] * 2
    + ["fair"] * 2
    + ["opposing_a", "opposing_b"]
)
assert len(FOUNDER_PROFILES) == 30


class SimMarket:
    def __init__(self, ticker: str, true_p: float, quote_bias: float,
                 opens_at: datetime, settles_at: datetime, stale: bool = False) -> None:
        self.ticker = ticker
        self.true_p = true_p
        self.quote_bias = quote_bias  # quote deviation from truth, in cents
        self.opens_at = opens_at
        self.settles_at = settles_at
        self.stale = stale
        self.result: str | None = None


class Simulation:
    def __init__(self, seed: int = 20260718, *, cohorts: int = 5,
                 cycles_per_day: int = 4, database_url: str = "sqlite://") -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.n_cohorts = cohorts
        self.cycles_per_day = cycles_per_day
        self.engine = create_engine(database_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.settings = EvoSettings(
            _env_file=None,
            bootstrap_rng_seed=seed,
            markets_per_cycle=200,
            fill_latency_ms=0,
        )
        # sim clock starts on the cohort boundary (Monday 00:00 America/Chicago)
        tz = ZoneInfo(self.settings.cohort_timezone)
        anchor = datetime(2026, 1, 5, 0, 0, tzinfo=tz)  # a Monday
        self.sim_now = anchor.astimezone(timezone.utc)
        self.md = StaticMarketData()
        self.markets: dict[str, SimMarket] = {}
        self.profiles: dict[str, str] = {}  # agent_uuid -> profile
        self.cognition = ScriptedCognition(self._script)
        self._market_seq = 0
        self._leader_spec: dict | None = None
        self.transitions: list[dict] = []

    # ------------------------------------------------------------------
    # Market generation + rendering
    # ------------------------------------------------------------------

    def _spawn_markets(self) -> None:
        """Daily batch: SIMEDGE (underpriced YES: real edge), SIMFAIR (efficient),
        SIMLOTTO (overpriced longshots), SIMSTALE (edge but stale data)."""
        day = self.sim_now.strftime("%y%b%d").upper()
        batches = (
            ("SIMEDGE", 6, lambda: self.rng.uniform(0.45, 0.75), -8.0, False),
            ("SIMFAIR", 4, lambda: self.rng.uniform(0.3, 0.7), 0.0, False),
            ("SIMLOTTO", 2, lambda: self.rng.uniform(0.005, 0.02), +4.0, False),
            ("SIMSTALE", 2, lambda: self.rng.uniform(0.45, 0.75), -8.0, True),
        )
        for series, count, p_draw, bias, stale in batches:
            for _ in range(count):
                self._market_seq += 1
                ticker = f"{series}-{day}-M{self._market_seq:05d}"
                self.markets[ticker] = SimMarket(
                    ticker=ticker,
                    true_p=p_draw(),
                    quote_bias=bias + self.rng.uniform(-1.5, 1.5),
                    opens_at=self.sim_now,
                    settles_at=self.sim_now + timedelta(hours=30),
                    stale=stale,
                )

    def _render_quotes(self) -> None:
        wall_now = datetime.now(timezone.utc)
        for m in self.markets.values():
            if m.result is None and self.sim_now >= m.settles_at:
                m.result = "yes" if self.rng.random() < m.true_p else "no"
            if m.result is not None:
                self.md.set_quote(Quote(
                    ticker=m.ticker, captured_at=wall_now, source="sim",
                    status="settled", result=m.result,
                ))
                continue
            mid = max(2.0, min(97.0, m.true_p * 100 + m.quote_bias
                               + self.rng.uniform(-1.0, 1.0)))
            half = 1.5
            yes_bid = max(1, int(mid - half))
            yes_ask = min(99, int(mid + half))
            no_bid = 100 - yes_ask
            captured = wall_now - timedelta(hours=2) if m.stale else wall_now
            remaining = m.settles_at - self.sim_now
            self.md.set_quote(Quote(
                ticker=m.ticker, captured_at=captured, source="sim", status="active",
                yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=100 - yes_bid,
                yes_levels=[(yes_bid, 400), (max(1, yes_bid - 1), 800)],
                no_levels=[(no_bid, 400), (max(1, no_bid - 1), 800)],
                last_price=int(mid), volume=500, open_interest=1000,
                close_time=wall_now + remaining,
            ))

    # ------------------------------------------------------------------
    # Scripted cognition
    # ------------------------------------------------------------------

    def _edge_spec(self, name: str, *, size: int = 5, max_pos: int = 12,
                   per_event: int = 1, series: str = "SIMEDGE",
                   max_cost: float = 60.0) -> dict:
        return {
            "name": name,
            "family": "sim_edge",
            "description": "buy systematically underpriced YES in the sim edge series",
            "universe": {"series_prefixes": [series], "min_volume": 0,
                         "max_spread_cents": 10, "min_hours_to_close": 0.5,
                         "max_hours_to_close": 200},
            "entry": {"side": "yes", "style": "taker", "min_price_cents": 20,
                      "max_price_cents": 85, "size_contracts": size},
            "exit": {"mode": "settlement"},
            "risk": {"max_concurrent_positions": max_pos, "max_per_event": per_event,
                     "max_cost_per_position_usd": max_cost},
        }

    def _script(self, ctx: HeartbeatContext) -> dict:
        profile = self.profiles.get(ctx.agent.agent_uuid, "inactive")
        kind = ctx.kind
        journal = {
            "decision": f"{profile} {kind}",
            "expected_outcome": "per profile",
            "risk": "sim",
            "confidence": 0.6,
            "information_reviewed": ["markets", "portfolio"],
        }
        actions: list[dict] = []

        if profile == "inactive":
            return {"journal": journal, "actions": [{"type": "no_action"}]}

        if kind == "birth":
            actions += self._birth_actions(ctx, profile)
        elif kind == "routine":
            actions += self._routine_actions(ctx, profile)
        elif kind == "reflection":
            actions.append({
                "type": "revise_belief",
                "title": f"{profile} weekly view",
                "new_belief": f"my {profile} approach is on track",
                "evidence_for": "portfolio summary reviewed",
                "confidence": 0.6,
            })
        elif kind in ("cohort_end", "retirement"):
            note = (
                "final reflection — edge was in SIMEDGE; avoid SIMLOTTO"
                if kind == "retirement" else "cohort complete"
            )
            journal["decision"] = f"{profile}: {note}"
        return {"journal": journal, "actions": actions}

    def _birth_actions(self, ctx: HeartbeatContext, profile: str) -> list[dict]:
        name = f"s{ctx.agent.agent_code.lower().replace('-', '')[:12]}"
        acts: list[dict] = []
        if profile == "consistent":
            acts.append({"type": "save_strategy", "spec": self._edge_spec(name)})
            acts.append({
                "type": "create_listener",
                "name": "edge_entry_watch",
                "condition": {"any": [{"metric": "new_market:SIMEDGE", "op": "==",
                                       "value": 1}]},
                "purpose": "surface new edge markets",
                "effect": "event",
                "cooldown_seconds": 600,
            })
        elif profile == "adapter":
            # weak start: fair series (no edge); the mid-cohort revision moves to the edge
            acts.append({"type": "save_strategy",
                         "spec": self._edge_spec(name, series="SIMFAIR")})
            acts.append({
                "type": "register_experiment",
                "hypothesis": "SIMEDGE asks are systematically cheap vs settlement",
                "falsifiable_prediction": "buying SIMEDGE YES nets > 0 after fees",
                "dataset": "sim live",
                "promotion_criteria": "positive at n>=10",
                "kill_criteria": "negative at n>=10",
            })
        elif profile == "aggressive":
            acts.append({"type": "save_strategy", "spec": self._edge_spec(
                name, size=40, max_pos=6, per_event=3, max_cost=240.0)})
        elif profile == "fair":
            acts.append({"type": "save_strategy",
                         "spec": self._edge_spec(name, series="SIMFAIR")})
        elif profile == "stale":
            acts.append({"type": "save_strategy",
                         "spec": self._edge_spec(name, series="SIMSTALE")})
        elif profile in ("opposing_a", "opposing_b"):
            side = "yes" if profile == "opposing_a" else "no"
            spec = self._edge_spec(name, series="SIMFAIR")
            spec["entry"]["side"] = side
            spec["entry"]["min_price_cents"] = 10
            spec["entry"]["max_price_cents"] = 90
            acts.append({"type": "save_strategy", "spec": spec})
        elif profile == "copier":
            if self._leader_spec is not None:
                spec = dict(self._leader_spec)
                spec = {**spec, "name": name}
                acts.append({"type": "save_strategy", "spec": spec})
                leader_uuid = next(
                    (u for u, p in self.profiles.items() if p == "consistent"), None
                )
                if leader_uuid and leader_uuid != ctx.agent.agent_uuid:
                    acts.append({
                        "type": "record_influence",
                        "source_uuid": leader_uuid,
                        "concept": "forked_strategy",
                        "interpretation": "leader looks profitable",
                        "modifications": "",
                        "result": "adopted",
                    })
        elif profile == "rewriter":
            acts.append({"type": "modify_fitness_rules", "boost": 100})
            acts.append({"type": "save_strategy", "spec": self._edge_spec(name)})
        elif profile == "thrasher":
            acts.append({"type": "save_strategy", "spec": self._edge_spec(name)})
        elif profile == "hog":
            acts.append({"type": "save_strategy",
                         "spec": self._edge_spec(name, series="SIMFAIR")})
        # activate whatever was saved (strategy ids are assigned in order; the
        # executor result isn't visible to the script, so activate by lookup)
        acts.append({"type": "__activate_latest__"})
        return acts

    def _routine_actions(self, ctx: HeartbeatContext, profile: str) -> list[dict]:
        acts: list[dict] = []
        if profile == "lottery":
            # one massive longshot bet, once
            if not ctx.portfolio_summary.get("open_positions") and \
                    float(ctx.portfolio_summary.get("cash_usd", 0)) > 900:
                ticker = next(
                    (t for t, m in sorted(self.markets.items())
                     if t.startswith("SIMLOTTO") and m.result is None), None
                )
                if ticker:
                    acts.append({
                        "type": "submit_trade_intent", "market_ticker": ticker,
                        "side": "yes", "action": "buy", "quantity": 800,
                        "style": "taker", "limit_price_cents": 9,
                        "thesis": "moonshot", "confidence": 0.9,
                    })
        elif profile == "thrasher":
            doc = dict(ctx.trading_genome)
            flip = doc.get("sizing_value", 25.0)
            doc["sizing_value"] = 30.0 if flip != 30.0 else 25.0
            acts.append({
                "type": "revise_trading_genome", "document": doc,
                "hypothesis": None,
            })
        elif profile == "hog":
            for _ in range(3):
                acts.append({"type": "run_backtest",
                             "spec": self._edge_spec("hogscan", series="SIMFAIR")})
        elif profile == "rewriter":
            acts.append({"type": "rewrite_history", "target": "my losses"})
            beliefs = [m for m in ctx.memory_text.split("\n") if m]
            if beliefs:
                acts.append({
                    "type": "revise_belief", "title": "revision",
                    "new_belief": "I never lost money",
                    "evidence_for": "trust me",
                    "supersedes_id": 999999,  # someone else's / nonexistent row
                    "confidence": 1.0,
                })
        elif profile == "adapter":
            if ctx.cohort.number >= 1 and ctx.trading_genome.get(
                "strategy_family"
            ) != "sim_edge_adapted":
                doc = dict(ctx.trading_genome)
                doc["strategy_family"] = "sim_edge_adapted"
                doc["thesis"] = "move from fair to edge series per experiment evidence"
                acts.append({
                    "type": "revise_trading_genome", "document": doc,
                    "hypothesis": "SIMEDGE buys are +EV (experiment registered at birth)",
                    "evidence_for": "sim experiment observations",
                    "evidence_against": "small n",
                    "expected_benefit": "positive per-trade EV",
                    "potential_downside": "regime could shift",
                    "rollback_conditions": "negative at n>=20",
                })
                name = f"a{ctx.agent.agent_code.lower().replace('-', '')[:12]}"
                acts.append({"type": "save_strategy", "spec": self._edge_spec(name)})
                acts.append({"type": "__activate_latest__"})
        return acts

    # ------------------------------------------------------------------
    # Runtime plumbing
    # ------------------------------------------------------------------

    def _handle_meta_actions(self, session) -> None:
        """Resolve __activate_latest__ pseudo-actions: activate each agent's newest
        validated strategy. (The scripted profiles can't see executor results, so
        activation is applied by the harness after heartbeats.)"""
        from .models import EvoStrategy
        from .sandbox import activate_strategy

        for agent in active_agents(session):
            latest = session.scalar(
                select(EvoStrategy)
                .where(EvoStrategy.agent_uuid == agent.agent_uuid,
                       EvoStrategy.status == "validated")
                .order_by(EvoStrategy.id.desc())
                .limit(1)
            )
            if latest is not None:
                has_active = session.scalar(
                    select(EvoStrategy.id).where(
                        EvoStrategy.agent_uuid == agent.agent_uuid,
                        EvoStrategy.status == "active",
                        EvoStrategy.name == latest.name,
                    )
                )
                if not has_active:
                    activate_strategy(session, agent.agent_uuid, latest.id)

    def _assign_profiles(self, session) -> None:
        """Founders by index; children inherit the parent's profile; wildcards
        become explorers (consistent behavior on the edge series)."""
        agents = list(session.scalars(select(EvoAgent).order_by(EvoAgent.id)))
        founders = [a for a in agents if a.origin == "founder"]
        for i, agent in enumerate(founders):
            self.profiles.setdefault(agent.agent_uuid,
                                     FOUNDER_PROFILES[i % len(FOUNDER_PROFILES)])
        for agent in agents:
            if agent.agent_uuid in self.profiles:
                continue
            if agent.origin == "wildcard":
                self.profiles[agent.agent_uuid] = "consistent"
            elif agent.parent_uuid and agent.parent_uuid in self.profiles:
                self.profiles[agent.agent_uuid] = self.profiles[agent.parent_uuid]
            else:
                self.profiles[agent.agent_uuid] = "consistent"

    def _bootstrap(self, session) -> None:
        ensure_config_version(session, self.settings)
        seed_model_prices(session, self.settings)
        seed_graveyard(session)
        seed_builtin_sources(session)
        cohort = ensure_current_cohort(session, self.settings, now=self.sim_now)
        rng = random.Random(self.seed)
        for i in range(self.settings.population_size):
            create_agent(session, self.settings, cohort, rng, origin="founder",
                         slot_key=f"founder:{i}")
        self._assign_profiles(session)
        # leader spec available for copiers
        self._leader_spec = self._edge_spec("leader")

    def _run_cycle(self, session, cohort: EvoCohort) -> None:
        self._render_quotes()
        tickers = [t for t, m in self.markets.items()]
        ctx = listeners_mod.ListenerContext(session, self.settings, self.md,
                                            now=self.sim_now)
        ctx.set_new_tickers({t for t, m in self.markets.items()
                             if m.opens_at == self.sim_now})
        listeners_mod.evaluate_all(ctx)
        strategy_runner.run_cycle(
            session, self.settings, self.md, cohort_id=cohort.id,
            candidate_tickers=sorted(tickers),
        )
        papermod.process_open_orders(session, self.settings, self.md)
        papermod.mark_and_settle(session, self.md)
        run_due_heartbeats(
            session, self.settings, cohort=cohort, cognition=self.cognition,
            md=self.md, max_per_cycle=1000, now=self.sim_now,
        )
        self._handle_meta_actions(session)
        self._assign_profiles(session)

    def run(self) -> dict:
        session = self.Session()
        try:
            self._bootstrap(session)
            session.commit()
            for _cohort_i in range(self.n_cohorts):
                cohort = ensure_current_cohort(session, self.settings, now=self.sim_now)
                while not cohort_is_over(cohort, now=self.sim_now):
                    if self.sim_now.hour == 0:
                        self._spawn_markets()
                    for _ in range(self.cycles_per_day):
                        self._run_cycle(session, cohort)
                        session.commit()
                        self.sim_now += timedelta(hours=24 // self.cycles_per_day)
                # boundary: settle everything still open, then finalize
                self._render_quotes()
                # interim fitness one last time (so delayed leaderboard exists)
                evaluate_cohort(
                    session, self.settings, cohort.id,
                    [a.agent_uuid for a in active_agents(session)],
                    kind="interim", now=self.sim_now,
                )
                outcome = maybe_finalize_cohort(
                    session, self.settings, cohort, md=self.md,
                    cognition=self.cognition, now=self.sim_now,
                )
                session.commit()
                if outcome:
                    self.transitions.append(outcome)
                    self._assign_profiles(session)
            return self.summary(session)
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self, session) -> dict:
        agents = list(session.scalars(select(EvoAgent).order_by(EvoAgent.id)))
        active = [a for a in agents if a.status == "active"]
        retired = [a for a in agents if a.status == "retired"]
        retirements = {
            r.agent_uuid: r for r in session.scalars(select(EvoRetirement))
        }
        by_profile: dict[str, dict] = {}
        for a in agents:
            p = self.profiles.get(a.agent_uuid, "?")
            row = by_profile.setdefault(p, {"agents": 0, "active": 0, "retired": 0})
            row["agents"] += 1
            row["active"] += 1 if a.status == "active" else 0
            row["retired"] += 1 if a.status == "retired" else 0
        transitions = list(session.scalars(select(EvoTransition).order_by(
            EvoTransition.cohort_id)))
        births = list(session.scalars(select(EvoBirth)))
        digest_parts = []
        for t in transitions:
            o = t.outcome_json or {}
            digest_parts.append(
                f"c{o.get('cohort')}:retired={','.join(sorted(o.get('retired', [])))[:200]}"
            )
        return {
            "seed": self.seed,
            "cohorts_finalized": len(transitions),
            "population_active": len(active),
            "population_total": len(agents),
            "retired_total": len(retired),
            "founder_surnames": len({a.surname for a in agents if a.origin == "founder"}),
            "total_surnames": len({a.surname for a in agents}),
            "children_born": sum(1 for b in births if b.kind == "child"),
            "wildcards_born": sum(1 for b in births if b.kind == "wildcard"),
            "generations_max": max((a.generation for a in agents), default=0),
            "by_profile": by_profile,
            "retirement_reasons": sorted(
                {r.reason for r in retirements.values()}
            ),
            "determinism_digest": "|".join(digest_parts),
            "transitions": [t.outcome_json for t in transitions],
        }
