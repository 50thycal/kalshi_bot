"""The BOT_MODE=evo worker cycle (spec rollout Stage 6 — shadow-only operation).

Called from kalshi_bot.main's existing loop, so signal handling, RUN_ONCE and the
sleep interval are shared with every other mode (set SCAN_INTERVAL_SECONDS=60 for
the evo service; see docs/EVO_RUNBOOK.md).

Cycle order (all deterministic work first, cognition last):
  1. bootstrap: config version, model prices, graveyard seed, builtin sources,
     current cohort, founders up to population target
  2. cohort boundary check -> finalization (locked, once)
  3. market universe scan (bounded) -> new-market detection
  4. listener evaluation (deterministic)
  5. active-strategy execution (deterministic entries/exits)
  6. open-order fill evaluation + position marks/settlements
  7. due heartbeats (LLM or degraded), bounded per cycle
  8. daily snapshots + interim fitness (hourly)

There is NO order-placement path here: the Kalshi client is used read-only, and
under BOT_MODE=evo its place_order() self-guard refuses anyway."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from ..db import session_scope
from ..logging_config import log_event
from ..weather.cities import CITIES
from . import listeners as listeners_mod
from . import paper as papermod
from . import strategy_runner
from .announcements import seed_announcements
from .cognition import LlmCognition
from .cohorts import (
    active_agents,
    cohort_is_over,
    ensure_current_cohort,
    reanchor_open_cohort,
)
from .config import EvoSettings, get_evo_settings
from .constitution import ensure_config_version
from .datasources import seed_builtin_sources
from .evolution import bootstrap_founders, maybe_finalize_cohort
from .fitness import evaluate_cohort
from .genomes import current_genome
from .graveyard_seed import seed_graveyard
from .heartbeats import run_due_heartbeats
from .llm import LlmClient, seed_model_prices
from .marketdata import LiveMarketData
from .models import EvoStrategy
from .strategy_spec import validate_spec

logger = logging.getLogger(__name__)

# Concrete Kalshi weather series (per-city daily high + low). This is the DEFAULT
# live universe the fleet scans: founder trading genomes ship with empty
# universe.series_prefixes, so without a default the scan surfaces nothing and agents
# never see a tradable ticker. Weather is also the only dataset the sandbox can
# backtest (backfill_weather), so these are exactly the markets agents can both trade
# live AND validate.
_WEATHER_SERIES: tuple[str, ...] = tuple(
    s for c in CITIES for s in (c.series_high, c.series_low) if s
)


class EvoRuntime:
    """Long-lived per-process state for the evo loop."""

    def __init__(self, kalshi_client, settings: EvoSettings | None = None) -> None:
        self.settings = settings or get_evo_settings()
        self.md = LiveMarketData(kalshi_client)
        self.llm = LlmClient(self.settings)
        self.cognition = LlmCognition(self.llm)
        self._known_tickers: set[str] = set()
        self._last_interim_fitness: datetime | None = None
        self._bootstrapped = False

    def close(self) -> None:
        self.llm.close()


def _universe_prefixes(session, settings: EvoSettings) -> list[str]:
    """Union of active agents' universe series prefixes (bounded)."""
    prefixes: set[str] = set()
    for strategy in session.scalars(
        select(EvoStrategy).where(EvoStrategy.status == "active")
    ):
        spec, err = validate_spec(strategy.spec_json)
        if not err:
            prefixes.update(p.upper() for p in spec.universe.series_prefixes[:8])
    for agent in active_agents(session, settings):
        trading = current_genome(session, agent.agent_uuid, "trading")
        doc = (trading.document_json if trading else {}) or {}
        prefixes.update(
            str(p).upper() for p in (doc.get("universe") or {}).get("series_prefixes", [])[:8]
        )
    return sorted(prefixes)[:40]


def _universe_series(session, settings: EvoSettings) -> list[str]:
    """Concrete Kalshi series to scan for live tradable markets this cycle. When
    agents have declared universe prefixes (e.g. "KXHIGH", or a specific series),
    expand them against the known weather series and keep any unrecognized ones as
    direct series queries; with nothing declared (the founder default), scan the full
    weather set so agents always have real, backtestable tickers to consider."""
    prefixes = _universe_prefixes(session, settings)
    if not prefixes:
        return list(_WEATHER_SERIES)
    matched = [s for s in _WEATHER_SERIES if any(s.startswith(p) for p in prefixes)]
    extra = [
        p
        for p in prefixes
        if p not in matched and not any(s.startswith(p) for s in _WEATHER_SERIES)
    ]
    return (matched + extra) or list(_WEATHER_SERIES)


def _scan_universe(runtime: EvoRuntime, session) -> list[str]:
    """Bounded open-market scan over the fleet's live universe, plus any tickers the
    fleet already holds. Each concrete series is queried directly (series_ticker=) —
    the same targeted path every other tracker in the repo uses; an untargeted
    open-market listing would never surface the weather markets among Kalshi's full
    open book. A data failure for one series is contained so the rest still scan."""
    settings = runtime.settings
    series_list = _universe_series(session, settings)
    cap = settings.markets_per_cycle
    per_series = max(1, cap // max(1, len(series_list)))
    tickers: list[str] = []
    for series in series_list:
        if len(tickers) >= cap:
            break
        try:
            markets = runtime.md.list_markets(
                status="open", series_ticker=series, max_markets=per_series,
            )
        except Exception:  # noqa: BLE001 — one series failing must not stop the rest
            markets = []
        for m in markets:
            t = str(m.get("ticker", ""))
            if t and t not in tickers:
                tickers.append(t)
    for pos in papermod.all_open_positions(session):
        if pos.market_ticker not in tickers:
            tickers.append(pos.market_ticker)
    return tickers[:cap]


def run_evo_cycle(runtime: EvoRuntime) -> None:
    """One full cycle; each phase is contained so one failure never stops the rest
    (matching the repo's ride-along failure-containment convention)."""
    settings = runtime.settings
    if not settings.enabled:
        logger.info("evo loop disabled (EVO_ENABLED=false) — infrastructure pause")
        return
    runtime.md.begin_cycle()
    now = datetime.now(timezone.utc)

    # One-time seeding in its OWN transaction, isolated from cohort/founder
    # bootstrap below: the in-memory _bootstrapped flag is only set to True
    # AFTER this block commits successfully, so a failure in a LATER phase this
    # cycle can never strand it "done" with nothing actually persisted (which
    # would silently starve every heartbeat of pricing forever, since founder
    # bootstrap retries every cycle but this block would not).
    if not runtime._bootstrapped:
        with session_scope() as session:
            ensure_config_version(session, settings)
            seed_model_prices(session, settings)
            seed_graveyard(session)
            seed_builtin_sources(session)
            seed_announcements(session)
        runtime._bootstrapped = True

    with session_scope() as session:
        ensure_config_version(session, settings)
        cohort = ensure_current_cohort(session, settings, now=now)
        bootstrap_founders(session, settings, cognition=runtime.cognition, md=runtime.md,
                           now=now)

    # Heal the legacy calendar-snapped cohort window so the population gets a full
    # week. Its OWN transaction (idempotent + cheap): a failure in founder
    # bootstrap above must never roll back this fix, and this must land before the
    # boundary check below reads ends_at.
    with session_scope() as session:
        reanchor_open_cohort(session, settings)

    # cohort boundary (its own transaction: the transition lock must commit even
    # if later phases fail)
    with session_scope() as session:
        cohort = ensure_current_cohort(session, settings, now=now)
        if cohort_is_over(cohort, now=now):
            outcome = maybe_finalize_cohort(
                session, settings, cohort, md=runtime.md, cognition=runtime.cognition,
                now=now,
            )
            if outcome:
                log_event(logger, logging.INFO, "evo cohort finalized",
                          cohort=outcome["cohort"], retired=len(outcome["retired"]),
                          children=len(outcome["children"]),
                          wildcard=bool(outcome["wildcard"]))

    with session_scope() as session:
        cohort = ensure_current_cohort(session, settings, now=now)
        tickers = _scan_universe(runtime, session)
        new_tickers = set(tickers) - runtime._known_tickers
        runtime._known_tickers.update(tickers)

        ctx = listeners_mod.ListenerContext(session, settings, runtime.md, now=now)
        ctx.set_new_tickers(new_tickers)
        listener_counts = listeners_mod.evaluate_all(ctx)

        strat_counts = strategy_runner.run_cycle(
            session, settings, runtime.md, cohort_id=cohort.id,
            candidate_tickers=tickers,
        )
        order_counts = papermod.process_open_orders(session, settings, runtime.md)
        settle_counts = papermod.mark_and_settle(session, runtime.md)

        hb_counts = run_due_heartbeats(
            session, settings, cohort=cohort, cognition=runtime.cognition,
            md=runtime.md, now=now, candidate_tickers=tickers,
        )

        # daily snapshots + hourly interim fitness
        day_label = f"daily:{now.strftime('%Y-%m-%d')}"
        agents = active_agents(session, settings)
        for agent in agents:
            papermod.snapshot_portfolio(
                session, agent.agent_uuid, papermod.cohort_ledger(cohort.id), day_label
            )
            papermod.snapshot_portfolio(
                session, agent.agent_uuid, papermod.LIFETIME, day_label
            )
        if (
            runtime._last_interim_fitness is None
            or (now - runtime._last_interim_fitness).total_seconds() >= 3600
        ):
            evaluate_cohort(
                session, settings, cohort.id,
                [a.agent_uuid for a in agents], kind="interim", now=now,
            )
            runtime._last_interim_fitness = now

        log_event(
            logger, logging.INFO,
            f"evo cycle: agents={len(agents)} tickers={len(tickers)} "
            f"listeners_fired={listener_counts['fired']} "
            f"orders={strat_counts['orders']} fills={order_counts['filled']} "
            f"settled={settle_counts['settled']} heartbeats={hb_counts['run']}",
            cohort=cohort.number,
            agents=len(agents),
            tickers=len(tickers),
            listeners=listener_counts,
            strategy=strat_counts,
            orders=order_counts,
            settlement=settle_counts,
            heartbeats=hb_counts,
        )
