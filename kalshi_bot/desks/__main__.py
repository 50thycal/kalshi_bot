"""Dedicated service: python -m kalshi_bot.desks init|status|serve|tick."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading

from .config import DeskSettings
from .contracts import utcnow
from .exchange import KalshiDeskExchange
from .execution import DeskExecutor
from .notifications import AlertNotifier
from .research import HTTPProvider, ProviderConfig
from .server import make_server
from .service import DeskService
from .store import DeskStore
from .supervisor import Supervisor


def build_service(settings):
    url = settings.database_url.get_secret_value()
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    store = DeskStore(url)
    store.initialize(settings.round_id, utcnow())
    providers = {}
    executors = {}
    for desk in ("chatgpt", "claude"):
        provider = getattr(settings, f"{desk}_provider")
        # Never even instantiate a paid provider under the accepted $0 budget.
        if provider != "external" and settings.monthly_research_budget_usd > 0:
            providers[desk] = HTTPProvider(ProviderConfig(
                provider=provider, model=getattr(settings, f"{desk}_model"),
                api_key=getattr(settings, f"{desk}_model_key").get_secret_value(),
                input_usd_per_million=getattr(settings, f"{desk}_input_usd_per_million"),
                output_usd_per_million=getattr(settings, f"{desk}_output_usd_per_million"),
            ))
        key = getattr(settings, f"{desk}_kalshi_key_id").get_secret_value()
        private = getattr(settings, f"{desk}_kalshi_private_key").get_secret_value()
        account = getattr(settings, f"{desk}_subaccount")
        if key and private and account:
            exchange = KalshiDeskExchange(settings.kalshi_base_url, key, private, account)
            executors[desk] = DeskExecutor(
                store, exchange, desk, live_enabled=settings.live_enabled,
                isolation_verified=False,
                existing_workers_isolated=settings.existing_workers_isolated)
    supervisor = Supervisor(store, providers=providers,
                            interval_seconds=settings.research_interval_seconds,
                            monthly_budget_usd=settings.monthly_research_budget_usd,
                            external_runners_verified=settings.external_runners_verified,
                            research_mode=settings.research_mode)
    notifier = AlertNotifier(store, settings.alert_webhook_url.get_secret_value())
    service = DeskService(settings, store, supervisor, executors, notifier=notifier)
    supervisor.submit_decision = service.submit
    return service


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "status", "serve", "tick"))
    args = parser.parse_args(argv)
    try:
        # Railway provides PORT; DESKS_PORT can override it explicitly.
        overrides = {"port": int(os.environ["PORT"])} if "PORT" in os.environ and "DESKS_PORT" not in os.environ else {}
        settings = DeskSettings(**overrides)
        service = build_service(settings)
    except Exception:
        parser.exit(2, "Desk configuration unavailable or invalid; check DESKS_ settings.\n")
    if args.command == "init":
        print(json.dumps({"round_id": settings.round_id, "initialized": True,
                          "live_started": False}))
        return
    if args.command in ("status", "tick"):
        value = service.status() if args.command == "status" else service.tick()
        print(json.dumps(value, default=str))
        return
    stop = threading.Event()
    logging.basicConfig(level=logging.WARNING)
    server = make_server(service)

    def worker():
        while not stop.is_set():
            try:
                service.tick()
            except Exception:
                service.last_error = "worker_cycle_failed"
                logging.warning("desk worker cycle failed; trading readiness unchanged")
            stop.wait(settings.tick_seconds)

    def shutdown(_signum, _frame):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, shutdown)
    thread = threading.Thread(target=worker, name="desk-worker", daemon=True)
    thread.start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
