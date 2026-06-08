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

from . import repository as repo
from .config import Settings, get_settings
from .db import create_all, init_engine, session_scope
from .kalshi.client import KalshiClient
from .kalshi.errors import AuthError
from .logging_config import configure_logging, log_event
from .paper.engine import PaperCycleSummary, PaperTradingEngine
from .risk.manager import RiskManager
from .scanner.scanner import MarketScanner, ScanSummary
from .weather.forecast import NwsForecastClient
from .weather.tracker import WeatherCycleSummary, WeatherTracker

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

    # Weather mode runs its own focused pipeline instead of the broad scanner.
    weather = settings.bot_mode == "weather"
    forecast_client = NwsForecastClient(settings.nws_user_agent) if weather else None
    weather_engine = PaperTradingEngine(client, settings, scanner.risk) if weather else None
    weather_tracker = (
        WeatherTracker(client, settings, forecast_client) if weather else None
    )
    if weather and settings.paper_abandon_foreign_on_start:
        try:
            with session_scope() as session:
                n = repo.abandon_open_paper_trades(session, keep_prefixes=("weather",))
            log_event(logger, logging.INFO, "abandoned non-weather paper positions", count=n)
        except Exception:  # noqa: BLE001
            logger.exception("failed to abandon foreign paper positions")

    signal_module.signal(signal_module.SIGTERM, _handle_signal)
    signal_module.signal(signal_module.SIGINT, _handle_signal)

    exit_code = 0
    try:
        while True:
            try:
                if weather:
                    _run_weather_cycle(settings, client, weather_engine, weather_tracker)
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
        if forecast_client is not None:
            forecast_client.close()
    return exit_code


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


def _run_weather_cycle(settings, client, engine, tracker) -> None:
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


def _log_weather(summary: WeatherCycleSummary) -> None:
    log_event(
        logger,
        logging.INFO,
        "weather cycle",
        events_seen=summary.events_seen,
        tracked=summary.tracked,
        forecasts_stored=summary.forecasts_stored,
        opened=summary.opened,
        skipped_no_book=summary.skipped_no_book,
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
