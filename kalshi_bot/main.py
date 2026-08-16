"""Worker entrypoint.

Steps:
  1. Load config (fail-closed).
  2. Connect to Postgres.
  3. Build the Kalshi client; verify exchange status + account balance.
  4. Run scan cycles (markets -> order books -> snapshots -> signals -> risk),
     logging a ranked candidate list each cycle.
  5. Exit after one cycle (RUN_ONCE) or loop every SCAN_INTERVAL_SECONDS.

Fail-closed everywhere: bad config, DB, or Kalshi auth aborts before anything
trade-like happens. (No orders are placed in the Scanner MVP regardless.)
"""

from __future__ import annotations

import logging
import signal as signal_module
import sys
import time
import traceback

from . import repository as repo
from .config import Settings, get_settings
from .db import create_all, init_engine, session_scope
from .freeze.tracker import FreezeTracker
from .kalshi.client import KalshiClient
from .kalshi.errors import AuthError
from .live.executor import LiveExecutor
from .logging_config import configure_logging, log_event
from .mmsell.history import RegimeHistoryCapture
from .mmsell.tracker import MmSellTracker
from .paper.engine import PaperCycleSummary, PaperTradingEngine
from .pin15.tracker import Pin15Tracker
from .risk.manager import RiskManager
from .scanner.scanner import MarketScanner, ScanSummary
from .tfav.tracker import TfavTracker
from .theta.tracker import ThetaTracker
from .twin import TwinHarness
from .wcprop.tracker import WcPropTracker
from .weather.backfill import WeatherBackfill
from .weather.ensemble import OpenMeteoEnsembleClient
from .weather.forecast import NwsForecastClient, OpenMeteoForecastClient
from .weather.polymarket import PolymarketClient
from .weather.tracker import WeatherCycleSummary, WeatherTracker
from .weather.validation import WeatherValidationBackfill
from .xgame.tracker import XGameTracker

logger = logging.getLogger("kalshi_bot.main")

_shutdown = False


def _handle_signal(signum, _frame) -> None:
    global _shutdown
    _shutdown = True
    logger.info(
        "received shutdown signal; will exit after current cycle",
        extra={"extra_fields": {"signal": signum}},
    )


def run() -> int:
    # 1) Settings (fail-closed).
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        configure_logging("INFO")
        logger.error(
            "invalid configuration; refusing to start",
            extra={"extra_fields": {"error": str(exc)}},
        )
        return 1

    configure_logging(settings.log_level)
    log_event(logger, logging.INFO, "starting kalshi bot", **settings.redacted_summary())

    # 2) Database.
    try:
        init_engine(settings.database_url)
        create_all()  # safety net; Alembic migrations are the source of truth
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "database initialization failed; refusing to start",
            extra={"extra_fields": {"error": str(exc)}},
        )
        return 1

    # 2b) Experiment OS legacy import (flag-gated, idempotent, read-mostly; runs
    # once and no-ops thereafter). MUST NOT be able to stop the worker: the hook
    # swallows its own errors, and this guard covers even the session/commit path.
    if settings.experiment_os_import_on_boot:
        try:
            from .experiment_os.importer import run_boot_import

            with session_scope() as session:
                run_boot_import(session)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "experiment OS import hook failed; continuing to trade",
                extra={"extra_fields": {"error": str(exc)}},
            )

    # 2c) Experiment OS enforcement: record an operator-declared cutover (no-op
    # unless EXPERIMENT_OS_ENFORCEMENT_MODE names a mode we are not already in,
    # and only after readiness passes), then load the recorded mode (OFF when
    # never recorded), and when enforcement is on, check the live books'
    # registered config for drift. Guarded like 2b — enforcement state must never
    # stop the worker; per-mode blocking happens inside the write helpers, per tag.
    try:
        from .experiment_os import enforcement as xos_enforcement
        from .experiment_os.lifecycle import EnforcementMode

        with session_scope() as session:
            cutover = xos_enforcement.run_boot_cutover(session, settings)
            if cutover is not None:
                log_event(
                    logger,
                    logging.INFO if cutover.get("ok") else logging.ERROR,
                    "experiment OS enforcement cutover",
                    **{k: v for k, v in cutover.items() if k != "readiness"},
                )
            xos_enforcement.refresh(session)
            mode = xos_enforcement.current_mode(session)
            log_event(
                logger, logging.INFO, "experiment OS enforcement",
                mode=mode.value,
                system_version=xos_enforcement.ENFORCEMENT_SYSTEM_VERSION,
            )
            if mode is not EnforcementMode.OFF:
                drift = xos_enforcement.runtime_config_check(session, settings)
                if drift:
                    logger.error(
                        "experiment OS config drift detected at boot",
                        extra={"extra_fields": {"drift": drift}},
                    )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "experiment OS enforcement boot check failed; continuing",
            extra={"extra_fields": {"error": str(exc)}},
        )

    # 3) Kalshi client.
    try:
        client = KalshiClient(settings)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "failed to build Kalshi client; refusing to start",
            extra={"extra_fields": {"error": str(exc)}},
        )
        return 1

    scanner = MarketScanner(client, settings, RiskManager(settings))

    # Read-only API shape probe (zero orders, zero money): logs the STRUCTURE of the live
    # portfolio responses so the live reconciler's parsing can be confirmed before any order.
    if settings.live_shape_probe:
        _probe_api_shapes(client)

    # Which Kalshi API tier are we actually on? Read-only, once per start. The scan's request
    # budget — and whether the 429s the client now counts are expected or a fault — is decided
    # by this number, and nothing in the system knew it until now.
    api_limits = _probe_api_limits(client)
    # ...and, only when explicitly asked, request the free permanent ADVANCED grant. No-ops once
    # the account is off `basic`, so the switch is safe to leave set. Re-records on success so
    # the stored tier matches reality rather than the pre-upgrade read above.
    upgraded = _maybe_upgrade_api_tier(client, settings, api_limits)
    if upgraded is not None:
        _record_api_limits(upgraded)

    # Weather AND live modes both run the focused weather pipeline; live adds a real-money
    # executor that mirrors allowlisted paper entries into orders (inert until configured).
    live = settings.bot_mode == "live"
    weather = settings.bot_mode == "weather"
    mmsell = settings.bot_mode == "mmsell"
    evo = settings.bot_mode == "evo"
    # Evolutionary agent population (paper/shadow only; docs/EVOLUTIONARY_AGENT_SYSTEM.md).
    # Runs as its OWN Railway service so LLM-heartbeat latency never touches the trading
    # loop. The Kalshi client is read-only here (place_order self-guards on bot_mode).
    evo_runtime = None
    if evo:
        from .evo.orchestrator import EvoRuntime

        evo_runtime = EvoRuntime(client)
    weather_like = weather or live
    # The live executor (real-money order placement) exists only in live mode; it mirrors
    # allowlisted paper entries into orders. Built here so the ride-along mmsell tracker can
    # receive it (its NO-maker live path). INERT until the switches + LIVE_STRATEGIES are set.
    live_executor = LiveExecutor(client, settings, scanner.risk) if live else None
    # Live/paper parallel-run harness (docs/LIVE_PAPER_TWIN.md): for every strategy armed for real
    # money, run a FRESH paper book beside it — started at the same instant and parameterized to
    # the LIVE knobs — so the only difference between the two is the fill assumption paper cannot
    # test. Live mode only, and each twin is gated on its live tag actually being armed.
    twin_harness = TwinHarness(settings) if live else None
    if twin_harness is not None and twin_harness.enabled:
        log_event(logger, logging.INFO, "live/paper twins configured",
                  pairs=[f"{sp.live_tag}->{sp.twin_tag}" for sp in twin_harness.specs],
                  armed=[sp.twin_tag for sp in twin_harness.active_specs()])
        # LIVE_PAPER_TWINS outranks LIVE_PAPER_TWIN_SUFFIX, and the loser is otherwise silent:
        # bumping the suffix to cut a fresh epoch is a NO-OP while an explicit entry names the old
        # tag, and the worker just keeps writing the previous epoch. Say so at startup — this is
        # the one moment the disagreement is cheap to notice.
        for live_tag, explicit, derived in settings.live_paper_twin_shadowed_pairs:
            log_event(logger, logging.WARNING, "twin suffix is being ignored for this book",
                      live_tag=live_tag, using=explicit, suffix_would_give=derived,
                      fix="clear this book's entry from LIVE_PAPER_TWINS to follow the suffix")
    # A live book dropped from LIVE_STRATEGIES (retired, e.g. mmsell10 -> mmsell10a/mmsell10b)
    # otherwise leaves its dashboard pair open forever with no explanation — see
    # repo.reconcile_stale_twin_epochs. Startup-only: retirement is a deliberate, infrequent
    # config change that always comes with a redeploy, not a mid-cycle condition to watch for.
    if live:
        with session_scope() as _twin_session:
            retired = repo.reconcile_stale_twin_epochs(
                _twin_session, live_strategy_prefixes=settings.live_strategy_list,
                configured_pairs=settings.live_paper_twin_pairs)
        if retired:
            log_event(logger, logging.INFO, "live/paper twin epochs retired (dropped from LIVE_STRATEGIES)",
                      twin_tags=retired)
    mmsell_engine = PaperTradingEngine(client, settings, scanner.risk) if mmsell else None
    mmsell_tracker = MmSellTracker(client, settings) if mmsell else None
    # mmsell can ALSO ride along as a paper book inside the weather/live cycle (its positions
    # are settled/marked by the shared weather_engine, which manages ALL open paper trades). In
    # live mode it also mirrors allowlisted entries into real resting maker NO-buys via the executor.
    mmsell_paper = weather_like and settings.mmsell_paper_enabled
    mmsell_paper_tracker = (
        MmSellTracker(client, settings, live_executor=live_executor, twin_harness=twin_harness)
        if mmsell_paper else None
    )
    # theta rides along the same way (paper): model-anchored tail-selling on the hourly
    # crypto ladders; the shared weather_engine settles/marks its <1h positions. In live mode
    # it also mirrors allowlisted entries into real resting maker NO-buys via the executor,
    # and runs its own live/paper twin(s) the same way mmsell does.
    theta_paper = weather_like and settings.theta_enabled
    theta_tracker = (
        ThetaTracker(client, settings, live_executor=live_executor, twin_harness=twin_harness)
        if theta_paper else None
    )
    # tfav rides along the same way (paper): the MIRROR of theta — buy model-underpriced
    # favorites (taker buy YES) on the same crypto ladders, held to settlement.
    tfav_paper = weather_like and settings.tfav_enabled
    tfav_tracker = TfavTracker(client, settings) if tfav_paper else None
    # pin15 rides along the same way (paper): endgame settlement-average observation-pin on the
    # 15-minute crypto up/down markets — a TAKER buy of the drift-favored side 2-3min before close,
    # held to settlement by the shared weather_engine. Forward-test of docs/PIN15_THESIS.md.
    pin15_paper = weather_like and settings.pin15_enabled
    pin15_tracker = Pin15Tracker(client, settings) if pin15_paper else None
    # freeze rides along (paper): exchange-closure pin on the commodity hub — a TAKER buy of
    # the favorite while the CBOT/ICE source is dark and the outcome is mechanically decided.
    # Ships with its own open-window control arm; see kalshi_bot/freeze/tracker.py.
    freeze_paper = weather_like and settings.freeze_enabled
    freeze_tracker = FreezeTracker(client, settings) if freeze_paper else None
    # wcprop rides along (paper): ride the World Cup winner-ladder's lag after a decisive
    # match result (cross-market coherence forward-test). Timed exit via the shared engine.
    wcprop_paper = weather_like and settings.wcprop_enabled
    wcprop_tracker = WcPropTracker(client, settings) if wcprop_paper else None
    # xgame rides along as a cross-venue in-play tape COLLECTOR and (when xgame_book_enabled)
    # a paper BOOK that trades the PM-lead/Kalshi-lag gap (docs/IDEA_MODEL_20260704.md); the
    # book manages its own fast converge/timeout exit.
    xgame_tracker = (
        XGameTracker(client, settings) if weather_like and settings.xgame_enabled else None
    )
    forecast_client = NwsForecastClient(settings.nws_user_agent) if weather_like else None
    ensemble_client = (
        OpenMeteoEnsembleClient() if weather_like and settings.weather_ensemble_enabled else None
    )
    polymarket_client = (
        PolymarketClient() if weather_like and settings.weather_polymarket_enabled else None
    )
    hrrr_client = (
        OpenMeteoForecastClient() if weather_like and settings.weather_hrrr_enabled else None
    )
    weather_engine = PaperTradingEngine(client, settings, scanner.risk) if weather_like else None
    weather_tracker = (
        WeatherTracker(client, settings, forecast_client, ensemble_client, polymarket_client,
                       hrrr=hrrr_client, live_executor=live_executor)
        if weather_like else None
    )
    weather_backfill = (
        WeatherBackfill(client, settings)
        if weather_like and settings.weather_backfill_enabled
        else None
    )
    validation_backfill = (
        WeatherValidationBackfill(settings)
        if weather_like and settings.weather_validation_enabled
        else None
    )
    # Rides along wherever the mmsell book runs. Kalshi discards settled markets after ~70 days,
    # so this is the only way the Sept-Nov regimes are still measurable in October
    # (docs/MMSELL_SEASONAL_FORECAST.md).
    regime_history = (
        RegimeHistoryCapture(client, settings)
        if weather_like and settings.mmsell_history_enabled
        else None
    )

    # Live mode is stricter: a real balance MUST be available (fail-closed, refuse to start).
    if live:
        try:
            bal = client.get_balance()
            if bal is None or bal.get("balance") is None:
                logger.error("live mode requires an available balance; refusing to start")
                return 1
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("live balance check failed; refusing to start",
                         extra={"extra_fields": {"error": str(exc)}})
            return 1
        with session_scope() as session:
            live_executor.recover(session)

    if weather_like and settings.paper_abandon_foreign_on_start:
        try:
            keep = ("weather",)
            if mmsell_paper:
                keep += ("mmsell",)
            if theta_paper:
                keep += ("theta",)
            if tfav_paper:
                keep += ("tfav",)
            if pin15_paper:
                keep += ("pin15",)
            if freeze_paper:
                keep += ("freeze",)
            if wcprop_paper:
                keep += ("wcprop",)
            if xgame_tracker is not None and settings.xgame_book_enabled:
                keep += ("xgame",)
            with session_scope() as session:
                n = repo.abandon_open_paper_trades(session, keep_prefixes=keep)
            log_event(logger, logging.INFO, "abandoned foreign paper positions",
                      count=n, kept=keep)
        except Exception:  # noqa: BLE001
            logger.exception("failed to abandon foreign paper positions")

    if mmsell and settings.paper_abandon_foreign_on_start:
        try:
            with session_scope() as session:
                n = repo.abandon_open_paper_trades(session, keep_prefixes=("mmsell",))
            log_event(logger, logging.INFO, "abandoned non-mmsell paper positions", count=n)
        except Exception:  # noqa: BLE001
            logger.exception("failed to abandon foreign paper positions")

    signal_module.signal(signal_module.SIGTERM, _handle_signal)
    signal_module.signal(signal_module.SIGINT, _handle_signal)

    exit_code = 0
    try:
        while True:
            # Experiment OS per-cycle maintenance, for EVERY mode. This lives in the
            # loop body, not inside one cycle function, because `_run_cycle` is only
            # the scanner fallback — a hook placed there silently never runs under
            # BOT_MODE=live/weather/mmsell/evo, which is exactly how the first
            # deploy of the gate evaluator recorded nothing in production.
            _experiment_os_cycle(settings)
            try:
                if live:
                    _run_live_cycle(
                        settings, client, weather_engine, weather_tracker, live_executor,
                        weather_backfill, validation_backfill, mmsell_paper_tracker,
                        theta_tracker, xgame_tracker, tfav_tracker, wcprop_tracker,
                        pin15_tracker, freeze_tracker, regime_history=regime_history,
                    )
                elif weather:
                    _run_weather_cycle(
                        settings, client, weather_engine, weather_tracker, weather_backfill,
                        validation_backfill, mmsell_paper_tracker, theta_tracker,
                        xgame_tracker, tfav_tracker, wcprop_tracker, pin15_tracker,
                        freeze_tracker, regime_history=regime_history,
                    )
                elif mmsell:
                    _run_mmsell_cycle(settings, client, mmsell_engine, mmsell_tracker)
                elif evo:
                    from .evo.orchestrator import run_evo_cycle

                    run_evo_cycle(evo_runtime)
                else:
                    _run_cycle(settings, client, scanner)
            except AuthError:
                logger.error("kalshi authentication failure; shutting down (fail-closed)")
                exit_code = 1
                break
            except Exception:  # noqa: BLE001 - keep the worker alive across cycle errors
                logger.exception("scan cycle crashed")
                if settings.run_once:
                    exit_code = 1
                    break

            if settings.run_once or _shutdown:
                break
            if not _interruptible_sleep(settings.scan_interval_seconds):
                break
    finally:
        client.close()
        if evo_runtime is not None:
            evo_runtime.close()
        if forecast_client is not None:
            forecast_client.close()
        if ensemble_client is not None:
            ensemble_client.close()
        if polymarket_client is not None:
            polymarket_client.close()
        if xgame_tracker is not None:
            xgame_tracker.pm.close()
        if tfav_tracker is not None:
            tfav_tracker.spot.close()
        if pin15_tracker is not None:
            pin15_tracker.spot.close()
    return exit_code


def _experiment_os_cycle(settings: Settings) -> None:
    """Per-cycle Experiment OS maintenance, run once per loop iteration in EVERY mode.

    Two independent, individually guarded steps — neither may ever stop trading:

    1. Refresh the enforcement snapshot (mode + tag→lineage map) so this cycle's
       writes stamp and admit against current state. A failed refresh keeps the
       previous snapshot: stale-but-safe.
    2. Evaluate gates and persist results, at a bounded cadence and only on the
       designated writer (`EXPERIMENT_OS_EVALUATE_GATES` on the live worker).
       Records verdicts; never promotes.

    Called from the main loop rather than from a cycle function on purpose: the
    cycle function differs per BOT_MODE, and the live worker never calls
    `_run_cycle` at all."""
    try:
        from .experiment_os import enforcement as xos_enforcement

        with session_scope() as session:
            xos_enforcement.refresh(session)
    except Exception:  # noqa: BLE001 — enforcement refresh must never stop the cycle
        logger.exception("experiment OS enforcement refresh failed")

    try:
        from .experiment_os import gate_runner as xos_gates

        with session_scope() as session:
            xos_gates.run_scheduled_evaluation(session, settings)
    except Exception:  # noqa: BLE001 — evaluation must never stop the cycle
        logger.exception("experiment OS gate evaluation failed")


def _interruptible_sleep(seconds: int) -> bool:
    """Sleep up to `seconds`, returning False early if shutdown was requested."""
    for _ in range(max(0, seconds)):
        if _shutdown:
            return False
        time.sleep(1)
    return not _shutdown


def _run_cycle(settings: Settings, client: KalshiClient, scanner: MarketScanner) -> None:
    account_state: dict | None = None

    # Connectivity proof: exchange status + balance.
    status = client.get_exchange_status()  # AuthError propagates -> hard fail
    log_event(
        logger,
        logging.INFO,
        "exchange status",
        **{k: status.get(k) for k in ("exchange_active", "trading_active") if k in status},
    )

    try:
        balance = client.get_balance()
        cents = balance.get("balance")
        account_state = {"cash_balance": (cents / 100.0) if cents is not None else None}
        log_event(
            logger,
            logging.INFO,
            "account balance fetched",
            cash_balance=account_state["cash_balance"],
        )
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "balance fetch failed; risk will treat balance as unavailable",
            extra={"extra_fields": {"error": str(exc)}},
        )

    with session_scope() as session:
        run_row = repo.start_bot_run(session, settings.bot_mode)
        try:
            paper_engine = None
            if settings.bot_mode == "paper":
                paper_engine = PaperTradingEngine(client, settings, scanner.risk)
                paper_engine.manage_open_positions(session)

            summary = scanner.run_once(
                session, account_state=account_state, paper_engine=paper_engine
            )
            if account_state is not None:
                repo.insert_account_snapshot(
                    session, cash_balance=account_state.get("cash_balance")
                )
            repo.finish_bot_run(
                session,
                run_row,
                status="completed",
                markets_scanned=summary.markets_scanned,
                candidates_found=summary.candidates_found,
            )
            _log_ranked(summary)
            if paper_engine is not None:
                # Count after entries so the summary reflects the post-scan state.
                paper_engine.summary.open_positions = repo.count_open_paper_positions(session)
                _log_paper(paper_engine.summary)
                log_event(logger, logging.INFO, "paper portfolio", **repo.paper_cycle_stats(session))
        except Exception as exc:
            repo.finish_bot_run(
                session,
                run_row,
                status="error",
                markets_scanned=0,
                candidates_found=0,
                error_message=str(exc)[:500],
            )
            repo.log_system_event(
                session,
                level="ERROR",
                component="scanner",
                message="scan cycle failed",
                raw={"error": str(exc)},
            )
            raise


def _log_ranked(summary: ScanSummary) -> None:
    # Top categories seen across events — used to tune TARGET_CATEGORIES against live data.
    top_categories = dict(
        sorted(summary.category_counts.items(), key=lambda kv: -kv[1])[:20]
    )
    log_event(
        logger,
        logging.INFO,
        "scan complete",
        events_scanned=summary.events_scanned,
        markets_scanned=summary.markets_scanned,
        targets_considered=summary.targets_considered,
        snapshots_written=summary.snapshots_written,
        candidates_found=summary.candidates_found,
        filtered_low_volume=summary.filtered_low_volume,
        filtered_low_oi=summary.filtered_low_oi,
        filtered_not_open=summary.filtered_not_open,
        filtered_no_orderbook=summary.filtered_no_orderbook,
        categories=top_categories,
    )
    for rank, c in enumerate(summary.candidates[:25], start=1):
        log_event(
            logger,
            logging.INFO,
            "ranked candidate",
            rank=rank,
            ticker=c.ticker,
            label=c.label,
            score=c.score,
            spread=c.spread,
            midpoint=c.midpoint,
            top_depth=c.top_depth,
            volume=c.volume,
            risk_approved=c.risk_approved,
            risk_reasons=c.risk_reasons,
            title=c.title[:80],
        )


def _run_validation_backfill(validation_backfill) -> None:
    """Materialize the forecast->settlement dataset for newly/late-settled events, off the
    trading path and in its own session — a failure here must never stop the books."""
    if validation_backfill is None:
        return
    try:
        with session_scope() as v_session:
            vsum = validation_backfill.run_once(v_session)
        log_event(
            logger,
            logging.INFO,
            "weather validation",
            events_materialized=vsum.events_materialized,
            rows_written=vsum.rows_written,
            pending=vsum.pending,
            errors=vsum.errors,
        )
    except Exception:  # noqa: BLE001 — validation must never stop the books
        logger.exception("weather validation cycle failed")


def _run_weather_cycle(
    settings, client, engine, tracker, backfill=None, validation_backfill=None,
    mmsell_tracker=None, theta_tracker=None, xgame_tracker=None, tfav_tracker=None,
    wcprop_tracker=None, pin15_tracker=None, freeze_tracker=None, regime_history=None,
) -> None:
    status = client.get_exchange_status()  # AuthError propagates -> hard fail
    log_event(
        logger,
        logging.INFO,
        "exchange status",
        **{k: status.get(k) for k in ("exchange_active", "trading_active") if k in status},
    )
    with session_scope() as session:
        run_row = repo.start_bot_run(session, settings.bot_mode)
        try:
            engine.manage_open_positions(session)  # settle/mark (weather holds to settlement)
            summary = tracker.run_once(session)
            repo.finish_bot_run(
                session,
                run_row,
                status="completed",
                markets_scanned=summary.events_seen,
                candidates_found=summary.tracked,
            )
            _log_weather(summary)
            log_event(logger, logging.INFO, "paper portfolio", **repo.paper_cycle_stats(session))
        except Exception as exc:
            repo.finish_bot_run(
                session, run_row, status="error", markets_scanned=0, candidates_found=0,
                error_message=str(exc)[:500],
            )
            raise
    if backfill is not None:
        try:
            with session_scope() as bf_session:
                bsum = backfill.run_once(bf_session)
            log_event(
                logger,
                logging.INFO,
                "weather backfill",
                markets_enumerated=bsum.markets_enumerated,
                markets_fetched=bsum.markets_fetched,
                candles_stored=bsum.candles_stored,
                errors=bsum.errors,
                pending=bsum.pending,
            )
        except Exception:  # noqa: BLE001 — history backfill must never stop the books
            logger.exception("weather backfill cycle failed")
    _run_validation_backfill(validation_backfill)
    _run_mmsell_book(settings, mmsell_tracker)
    _run_regime_history(regime_history)
    _run_theta_book(settings, theta_tracker)
    _run_tfav_book(settings, tfav_tracker)
    _run_pin15_book(settings, pin15_tracker)
    _run_freeze_book(settings, freeze_tracker)
    _run_wcprop_book(settings, wcprop_tracker)
    _run_xgame_collector(settings, xgame_tracker)


_mmsell_last_run = {"ts": 0.0}


def _wire_mmsell_live(tracker, account_state) -> None:
    """Give the ride-along mmsell tracker the account state so its live mirror can pass the real
    balance to the entry gate (live cycle only; no-op if the tracker isn't set)."""
    if tracker is not None:
        tracker._account_state = account_state


def _wire_theta_live(tracker, account_state) -> None:
    """Give the ride-along theta tracker the account state so its live mirror can pass the real
    balance to the entry gate (live cycle only; no-op if the tracker isn't set). Called
    unconditionally every live cycle (like _wire_mmsell_live), not just when the throttled
    _run_theta_book fires — the balance must stay fresh regardless of theta's own cadence."""
    if tracker is not None:
        tracker._account_state = account_state


def _run_regime_history(capture) -> None:
    """Capture Kalshi's settled market history for the mmsell regime series.

    Kalshi discards settled markets after ~70 days, so this is the only path by which the
    Sept-Nov regimes stay measurable once the season has passed (docs/MMSELL_SEASONAL_FORECAST.md).
    Never raises into the cycle: losing a capture pass costs a little history, while raising here
    would stop the trading books."""
    if capture is None:
        return
    try:
        with session_scope() as session:
            summ = capture.run_once(session)
        log_event(
            logger, logging.INFO, "regime history capture",
            enumerated=summ.enumerated_this_cycle, series=summ.series_enumerated,
            new_markets=summ.markets_enumerated, markets_fetched=summ.markets_fetched,
            candles_stored=summ.candles_stored, errors=summ.errors, pending=summ.pending,
        )
    except Exception:  # noqa: BLE001 — history capture must never stop the books
        logger.exception("regime history capture cycle failed")


def _run_mmsell_book(settings, tracker) -> None:
    """Ride-along mmsell paper book inside the weather/live cycle: throttled entry scan only
    (the shared weather_engine already settles/marks mmsell positions). Never raises into the
    cycle — its failures must not disturb the weather books or the live executor."""
    if tracker is None:
        return
    now = time.monotonic()
    if now - _mmsell_last_run["ts"] < settings.mmsell_interval_minutes * 60.0:
        return
    _mmsell_last_run["ts"] = now
    try:
        with session_scope() as session:
            summ = tracker.run_once(session)
        log_event(
            logger, logging.INFO, "mmsell book",
            events=summ.events_seen, considered=summ.markets_considered, in_band=summ.in_band,
            opened=summ.opened, twin_opened=summ.twin_opened,
            already_open=summ.already_open, capped=summ.capped, per_book=summ.per_book,
        )
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001 — ride-along book must never stop the cycle
        # Embed the error in the MESSAGE (not just exc_info) so it survives Railway's log
        # view, which only surfaces the base message string (see _probe_api_shapes).
        tb = traceback.extract_tb(exc.__traceback__)
        where = f"{tb[-1].filename.rsplit('/', 1)[-1]}:{tb[-1].lineno}" if tb else "?"
        logger.error(
            "mmsell ride-along book failed (weather/live unaffected): %s: %s at %s",
            type(exc).__name__, exc, where, exc_info=True,
        )


_theta_last_run = {"ts": 0.0}


def _run_theta_book(settings, tracker) -> None:
    """Ride-along theta paper book (model-anchored crypto tail-selling): throttled scan +
    spot refresh + ladder snapshots + entries. The shared weather_engine settles/marks its
    positions. Never raises into the cycle."""
    if tracker is None:
        return
    now = time.monotonic()
    if now - _theta_last_run["ts"] < settings.theta_interval_minutes * 60.0:
        return
    _theta_last_run["ts"] = now
    try:
        with session_scope() as session:
            summ = tracker.run_once(session)
        log_event(
            logger, logging.INFO, "theta book",
            products_ok=summ.products_ok, markets=summ.markets_seen,
            snapshots=summ.snapshot_rows, in_window=summ.in_window, in_band=summ.in_band,
            model_priced=summ.model_priced, edged=summ.edged, opened=summ.opened,
            twin_opened=summ.twin_opened,
            already_open=summ.already_open, capped=summ.capped,
            no_model=summ.skipped_no_model, illiquid=summ.skipped_illiquid,
            per_series=summ.per_series, per_book=summ.per_book,
        )
    except AuthError:
        raise
    except Exception:  # noqa: BLE001 — ride-along book must never stop the cycle
        logger.exception("theta ride-along book failed (weather/live unaffected)")


_tfav_last_run = {"ts": 0.0}


def _run_tfav_book(settings, tracker) -> None:
    """Ride-along tfav paper book (the theta mirror — buy model-underpriced crypto favorites):
    throttled spot refresh + scan + taker YES entries. The shared weather_engine settles/marks
    its positions (held to settlement). Never raises into the cycle."""
    if tracker is None:
        return
    now = time.monotonic()
    if now - _tfav_last_run["ts"] < settings.tfav_interval_minutes * 60.0:
        return
    _tfav_last_run["ts"] = now
    try:
        with session_scope() as session:
            summ = tracker.run_once(session)
        log_event(
            logger, logging.INFO, "tfav book",
            products_ok=summ.products_ok, markets=summ.markets_seen,
            in_window=summ.in_window, in_band=summ.in_band, model_priced=summ.model_priced,
            edged=summ.edged, opened=summ.opened, already_open=summ.already_open,
            capped=summ.capped, no_model=summ.skipped_no_model, illiquid=summ.skipped_illiquid,
            per_series=summ.per_series, per_book=summ.per_book,
        )
    except AuthError:
        raise
    except Exception:  # noqa: BLE001 — ride-along book must never stop the cycle
        logger.exception("tfav ride-along book failed (weather/live unaffected)")


_pin15_last_run = {"ts": 0.0}


def _run_pin15_book(settings, tracker) -> None:
    """Ride-along pin15 paper book (15-min crypto endgame observation-pin): a TAKER buy of the
    drift-favored side 2-3min before close, held to settlement (the shared weather_engine settles/
    marks it). Runs EVERY cycle by default (interval 0) — it must be frequent to land in the short
    entry window. Never raises into the cycle."""
    if tracker is None:
        return
    now = time.monotonic()
    if now - _pin15_last_run["ts"] < settings.pin15_interval_minutes * 60.0:
        return
    _pin15_last_run["ts"] = now
    try:
        with session_scope() as session:
            summ = tracker.run_once(session)
        log_event(
            logger, logging.INFO, "pin15 book",
            products_ok=summ.products_ok, markets=summ.markets_seen, in_window=summ.in_window,
            priced=summ.priced, pinned=summ.pinned, opened=summ.opened,
            already_open=summ.already_open, capped=summ.capped,
            no_spot=summ.skipped_no_spot, illiquid=summ.skipped_illiquid,
            per_series=summ.per_series,
        )
    except AuthError:
        raise
    except Exception:  # noqa: BLE001 — ride-along book must never stop the cycle
        logger.exception("pin15 ride-along book failed (weather/live unaffected)")


_freeze_last_run = {"ts": 0.0}


def _run_freeze_book(settings, tracker) -> None:
    """Ride-along FREEZE paper book (commodity-hub exchange-closure pin): a TAKER buy of the
    favorite on grain/soft markets whose settlement source is currently dark, held to settlement
    by the shared weather_engine. Runs on a slow cadence — dark windows are hours long, so there
    is nothing to gain from spinning. Never raises into the cycle."""
    if tracker is None:
        return
    now = time.monotonic()
    if now - _freeze_last_run["ts"] < settings.freeze_interval_minutes * 60.0:
        return
    _freeze_last_run["ts"] = now
    try:
        with session_scope() as session:
            summ = tracker.run_once(session)
        log_event(
            logger, logging.INFO, "freeze book",
            markets=summ.markets_seen, commodity=summ.commodity,
            eligible=summ.freeze_eligible, pinned=summ.pinned, opened=summ.opened,
            already_open=summ.already_open, capped=summ.capped,
            illiquid=summ.skipped_illiquid, no_discount=summ.skipped_discount,
            out_of_band=summ.skipped_price,
            per_book=summ.per_book, per_commodity=summ.per_commodity,
        )
    except AuthError:
        raise
    except Exception:  # noqa: BLE001 — ride-along book must never stop the cycle
        logger.exception("freeze ride-along book failed (weather/live unaffected)")


_wcprop_last_run = {"ts": 0.0}


def _run_wcprop_book(settings, tracker) -> None:
    """Ride-along wcprop paper book (World Cup winner-ladder lag): throttled settled-match
    trigger + winner-ladder move scan + taker entries. Positions are timed out at
    wcprop_hold_minutes by the shared weather_engine. Never raises into the cycle."""
    if tracker is None:
        return
    now = time.monotonic()
    if now - _wcprop_last_run["ts"] < settings.wcprop_interval_minutes * 60.0:
        return
    _wcprop_last_run["ts"] = now
    try:
        with session_scope() as session:
            summ = tracker.run_once(session)
        log_event(
            logger, logging.INFO, "wcprop book",
            recent_settled=summ.recent_settled, triggered=summ.triggered,
            winner_rungs=summ.winner_rungs, moved=summ.moved, opened=summ.opened,
            already_open=summ.already_open, capped=summ.capped,
            illiquid=summ.skipped_illiquid, wide=summ.skipped_spread, per_side=summ.per_side,
        )
    except AuthError:
        raise
    except Exception:  # noqa: BLE001 — ride-along book must never stop the cycle
        logger.exception("wcprop ride-along book failed (weather/live unaffected)")


_xgame_last_run = {"ts": 0.0}


def _run_xgame_collector(settings, tracker) -> None:
    """Ride-along XGAME tape collector + paper book. The paper book's fast converge/timeout
    EXIT runs every cycle (cheap — only open positions — and the seconds-scale edge can't wait
    on the poll throttle); tape polling, discovery and ENTRIES run on the throttled cadence.
    Never raises into the cycle; a dead collector/book must not disturb the weather books."""
    if tracker is None:
        return
    # exit management first, every cycle (the shared paper engine skips 'xgame' on purpose)
    if settings.xgame_book_enabled:
        try:
            with session_scope() as session:
                ex = tracker.manage_open_positions(session)
            if ex.exits_converged or ex.exits_timeout:
                log_event(
                    logger, logging.INFO, "xgame book exits",
                    converged=ex.exits_converged, timeout=ex.exits_timeout,
                    pnl=round(ex.book_pnl, 4),
                )
        except AuthError:
            raise
        except Exception:  # noqa: BLE001 — exit management must never stop the cycle
            logger.exception("xgame book exit management failed (weather/live unaffected)")
    now = time.monotonic()
    if now - _xgame_last_run["ts"] < settings.xgame_interval_minutes * 60.0:
        return
    _xgame_last_run["ts"] = now
    try:
        with session_scope() as session:
            summ = tracker.run_once(session)
        # Embed the health counts in the MESSAGE (not just extra_fields) so they survive
        # Railway's log view, which only surfaces the base message string (same reason
        # the mmsell error path does). For a collect-only book whose whole job is these
        # counts, they must be visible to confirm discovery is seeing both venues.
        log_event(
            logger, logging.INFO,
            f"xgame collector: kal_games={summ.kalshi_games} pm_games={summ.pm_games} "
            f"matched_new={summ.matched_new} active={summ.matches_active} "
            f"polled={summ.polled} skipped_window={summ.skipped_window} "
            f"kal_rows={summ.kalshi_rows} pm_rows={summ.pm_rows} "
            f"signals={summ.signals} opened={summ.opened} "
            f"ended={summ.ended} errors={summ.errors}",
            kalshi_games=summ.kalshi_games, pm_games=summ.pm_games,
            matched_new=summ.matched_new, active=summ.matches_active,
            polled=summ.polled, skipped_window=summ.skipped_window,
            kalshi_rows=summ.kalshi_rows, pm_rows=summ.pm_rows,
            signals=summ.signals, opened=summ.opened, already_open=summ.already_open,
            capped=summ.capped, ended=summ.ended, errors=summ.errors,
            per_match=dict(sorted(summ.per_match.items(), key=lambda kv: -kv[1])[:8]),
        )
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001 — ride-along collector must never stop the cycle
        tb = traceback.extract_tb(exc.__traceback__)
        where = f"{tb[-1].filename.rsplit('/', 1)[-1]}:{tb[-1].lineno}" if tb else "?"
        logger.error(
            "xgame collector failed (weather/live unaffected): %s: %s at %s",
            type(exc).__name__, exc, where, exc_info=True,
        )


def _run_mmsell_cycle(settings, client, engine, tracker) -> None:
    """Market-making SELL book: settle/mark open positions, then scan for new maker-sell
    entries (buy NO at the no-bid on overpriced cheap contracts), held to settlement."""
    status = client.get_exchange_status()  # AuthError propagates -> hard fail
    log_event(
        logger, logging.INFO, "exchange status",
        **{k: status.get(k) for k in ("exchange_active", "trading_active") if k in status},
    )
    with session_scope() as session:
        run_row = repo.start_bot_run(session, settings.bot_mode)
        try:
            engine.manage_open_positions(session)   # settle/mark (mmsell holds to settlement)
            summary = tracker.run_once(session)
            repo.finish_bot_run(
                session, run_row, status="completed",
                markets_scanned=summary.markets_considered, candidates_found=summary.opened,
            )
            log_event(
                logger, logging.INFO, "mmsell cycle",
                events=summary.events_seen, considered=summary.markets_considered,
                in_band=summary.in_band, opened=summary.opened,
                already_open=summary.already_open, capped=summary.capped,
                illiquid=summary.skipped_illiquid, out_of_window=summary.skipped_htc,
                top_series=dict(sorted(summary.per_series.items(),
                                       key=lambda kv: -kv[1])[:8]),
            )
            log_event(logger, logging.INFO, "paper portfolio", **repo.paper_cycle_stats(session))
        except Exception as exc:
            repo.finish_bot_run(
                session, run_row, status="error", markets_scanned=0, candidates_found=0,
                error_message=str(exc)[:500],
            )
            raise


def _api_shape(obj, depth: int = 0):
    """Structure of a response with VALUES redacted to their type — safe to log (no secrets,
    balances or order ids leak; only the shape the parser keys off is shown)."""
    if isinstance(obj, dict):
        return {k: _api_shape(v, depth + 1) for k, v in obj.items()} if depth < 3 else "{...}"
    if isinstance(obj, list):
        return [_api_shape(obj[0], depth + 1)] if obj else []
    return type(obj).__name__


def _probe_api_shapes(client) -> None:
    """Log the shape of each live portfolio read endpoint (read-only; no orders placed).
    Confirms the container keys the reconciler parses: balance / orders / fills /
    market_positions. Per-element field names only appear once there is real history."""
    import json
    for name, fn in (("balance", client.get_balance), ("orders", client.get_orders),
                     ("fills", client.get_fills), ("positions", client.get_positions),
                     ("settlements", client.get_settlements)):
        try:
            shape = json.dumps(_api_shape(fn()))[:600]
            # Embed the shape in the MESSAGE (not extra fields) so it survives Railway's
            # log view, which only surfaces the base message string.
            logger.info("api shape probe [%s]: %s", name, shape)
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("api shape probe failed [%s]: %s", name, exc)


def _tier_of(limits) -> str:
    """The tier name out of a limits payload, lowercased; '' when absent or oddly shaped.

    Kalshi's field is `usage_tier` (measured 2026-08-12); the alternatives are carried because
    this payload has been renamed before and an unrecognised shape must read as UNKNOWN rather
    than silently as 'not basic' — the upgrade decision below turns on this string, and the safe
    failure is to skip the write."""
    if not isinstance(limits, dict):
        return ""
    for key in ("usage_tier", "tier", "api_usage_level", "level", "name"):
        value = limits.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _maybe_upgrade_api_tier(client, settings, limits) -> dict | None:
    """Request the permanent ADVANCED Kalshi grant, if the operator asked for it AND we are
    still on basic. Returns the re-read limits on success, else None.

    This is the only account-mutating call the worker makes outside of order placement, so it
    is guarded three ways rather than one:

      1. `KALSHI_UPGRADE_API_TIER` must be set — nothing fires by default;
      2. the CURRENT tier must read exactly `basic`. An unknown or unreadable tier does NOT
         qualify: if we cannot confirm we are on basic, we do not write. That also makes the
         switch safe to leave on forever, since it no-ops the moment the grant lands, instead
         of re-POSTing on every deploy;
      3. the result is VERIFIED by re-reading `/account/limits`, because Kalshi returns 201 with
         an empty body — without the re-read we would log an upgrade we never actually got.

    Eligibility (≥1 of the last 100 orders placed via API) is Kalshi's, not ours, and a failure
    there is a 403. That is reported as a warning and never raised: an account that cannot
    upgrade should keep trading on basic, not refuse to start."""
    if not getattr(settings, "kalshi_upgrade_api_tier", False):
        return None
    tier = _tier_of(limits)
    if tier != "basic":
        log_event(logger, logging.INFO, "kalshi api tier upgrade skipped",
                  reason=("already upgraded" if tier else "current tier unknown"),
                  current_tier=tier or "unknown")
        return None

    log_event(logger, logging.INFO, "requesting kalshi ADVANCED api tier", current_tier=tier)
    try:
        client.upgrade_api_usage_level()
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        # 403 = the eligibility rule was not met. Either way the bot trades on unchanged.
        logger.warning("kalshi api tier upgrade REFUSED (still on %s): %s", tier, exc)
        return None

    try:
        after = client.get_account_limits()
    except Exception as exc:  # noqa: BLE001
        logger.warning("kalshi api tier upgrade sent, but re-read failed — tier UNCONFIRMED: %s",
                       exc)
        return None
    new_tier = _tier_of(after)
    if new_tier and new_tier != "basic":
        log_event(logger, logging.INFO, "kalshi api tier UPGRADED", was=tier, now=new_tier)
    else:
        # Accepted but unchanged is a real outcome worth naming: it means the grant did not
        # apply, and the scan is still running against the same ceiling it was before.
        logger.warning("kalshi api tier upgrade accepted but tier still reads %r", new_tier or "?")
    return after


def _probe_api_limits(client) -> dict | None:
    """Record this account's Kalshi API TIER and rate limits once, at startup (read-only).

    We have been reasoning about the mmsell scan's ~6-25 req/sec burst against an assumed Basic
    bucket (200 tokens/sec, 10 per request -> 20 reads/sec) without ever knowing the real tier.
    `GET /account/limits` is authoritative but needs auth, so neither the sandbox nor the ops
    runner can ask it — only the worker can, which is why this lives here.

    Written to `system_events` as well as the log because Railway retains roughly half an hour
    of log lines: a startup-only line would be gone long before anyone asked. Persisted, the
    tier sits next to the 429 counters the scan telemetry records, so `mmsell_quote_parity` can
    report "we hit N rate limits against a ceiling of X" instead of just the numerator.

    Fail-soft in both directions: a tier probe must never be what stops the bot, and an account
    whose plan does not expose the endpoint should degrade to a warning, not a crash.

    Returns the payload so the upgrade path can decide off the SAME read that was recorded,
    rather than issuing a second one and deciding off a different answer."""
    try:
        limits = client.get_account_limits()
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("api limits probe failed (tier unknown): %s", exc)
        return None
    _record_api_limits(limits)
    return limits


def _record_api_limits(limits) -> None:
    """Log + persist one limits payload. Called again after an upgrade so the stored row shows
    the tier we ended on, not the one we started with — `mmsell_quote_parity` reads the newest
    row, and a stale one would report the old ceiling beside the new 429 counts."""
    import json

    # In the MESSAGE, not just extra_fields — Railway's log view drops structured fields.
    logger.info("api limits probe: %s", json.dumps(limits)[:600])
    try:
        with session_scope() as session:
            repo.log_system_event(
                session, level="INFO", component="kalshi_limits",
                message="kalshi account api limits", raw=limits if isinstance(limits, dict) else {},
            )
    except Exception:  # noqa: BLE001
        logger.exception("api limits persist failed (probe logged; cycle unaffected)")


def _fetch_account_state(client) -> dict:
    """Build {cash_balance: dollars|None}; AuthError propagates (hard-fail)."""
    try:
        bal = client.get_balance()
        cents = bal.get("balance") if isinstance(bal, dict) else None
        return {"cash_balance": (cents / 100.0) if cents is not None else None}
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("live balance fetch failed; risk treats balance as unavailable",
                       extra={"extra_fields": {"error": str(exc)}})
        return {"cash_balance": None}


def _run_live_cycle(
    settings, client, engine, tracker, executor, backfill=None, validation_backfill=None,
    mmsell_tracker=None, theta_tracker=None, xgame_tracker=None, tfav_tracker=None,
    wcprop_tracker=None, pin15_tracker=None, freeze_tracker=None, regime_history=None,
) -> None:
    """Live cycle: reconcile Kalshi truth, manage exits, settle/mark paper, then run the
    tracker (which mirrors allowlisted entries into real orders)."""
    status = client.get_exchange_status()  # AuthError propagates -> hard fail
    log_event(logger, logging.INFO, "exchange status",
              **{k: status.get(k) for k in ("exchange_active", "trading_active") if k in status})
    account_state = _fetch_account_state(client)
    executor.reset_summary()
    tracker._account_state = account_state
    with session_scope() as session:
        run_row = repo.start_bot_run(session, settings.bot_mode)
        try:
            executor.reconcile(session, account_state)  # Kalshi truth first
            executor.manage_exits(session)
            executor.run_probe(session)                  # isolated fractional buy/sell probe (off by default)
            executor.close_mmsell_positions(session)      # one-shot end-of-strategy closeout (off by default)
            executor.close_theta_positions(session)       # one-shot end-of-strategy closeout (off by default)
            engine.manage_open_positions(session)       # paper settle/mark (shadow record)
            summary = tracker.run_once(session)         # mirrors entries for allowlisted books
            repo.finish_bot_run(
                session, run_row, status="completed",
                markets_scanned=summary.events_seen, candidates_found=summary.tracked,
            )
            _log_weather(summary)
            s = executor.summary
            log_event(logger, logging.INFO, "live cycle",
                      placed=s.placed, risk_blocked=s.risk_blocked, rejected=s.rejected,
                      new_fills=s.new_fills, positions=s.positions_snapshot,
                      timed_out=s.timed_out_canceled, exits_placed=s.exits_placed,
                      realized_today=s.realized_today)
        except Exception as exc:
            repo.finish_bot_run(
                session, run_row, status="error", markets_scanned=0, candidates_found=0,
                error_message=str(exc)[:500],
            )
            raise
    if backfill is not None:
        try:
            with session_scope() as bf_session:
                backfill.run_once(bf_session)
        except Exception:  # noqa: BLE001
            logger.exception("weather backfill cycle failed")
    _run_validation_backfill(validation_backfill)
    _wire_mmsell_live(mmsell_tracker, account_state)  # live-only: pass real balance to the mirror
    _wire_theta_live(theta_tracker, account_state)    # live-only: pass real balance to the mirror
    _run_mmsell_book(settings, mmsell_tracker)
    _run_regime_history(regime_history)
    _run_theta_book(settings, theta_tracker)
    _run_tfav_book(settings, tfav_tracker)
    _run_pin15_book(settings, pin15_tracker)
    _run_freeze_book(settings, freeze_tracker)
    _run_wcprop_book(settings, wcprop_tracker)
    _run_xgame_collector(settings, xgame_tracker)


def _log_weather(summary: WeatherCycleSummary) -> None:
    log_event(
        logger,
        logging.INFO,
        "weather cycle",
        events_seen=summary.events_seen,
        tracked=summary.tracked,
        forecasts_stored=summary.forecasts_stored,
        obs_stored=summary.obs_stored,
        ensembles_stored=summary.ensembles_stored,
        bucket_snaps=summary.bucket_snaps,
        opened=summary.opened,
        skipped_no_book=summary.skipped_no_book,
        settlements_captured=summary.settlements_captured,
        open_positions=summary.open_positions,
        per_window=summary.per_window,
    )


def _log_paper(summary: PaperCycleSummary) -> None:
    log_event(
        logger,
        logging.INFO,
        "paper cycle",
        considered=summary.considered,
        opened=summary.opened,
        no_fill=summary.no_fill,
        already_open=summary.already_open,
        risk_blocked=summary.risk_blocked,
        marked=summary.marked,
        closed_settled=summary.closed_settled,
        closed_timeout=summary.closed_timeout,
        closed_tp=summary.closed_tp,
        closed_sl=summary.closed_sl,
        closed_void=summary.closed_void,
        realized_pnl=round(summary.realized_pnl, 4),
        open_positions=summary.open_positions,
        fillability_rate=summary.fillability_rate,
        per_strategy=summary.per_strategy,
    )


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
