"""Dashboard web-service entrypoint: `python -m kalshi_bot.dashboard`.

Reads DATABASE_URL and PORT from the environment only — it does NOT construct the
full fail-closed Settings, so the dashboard service needs no Kalshi credentials
(it never calls Kalshi or the LLM). Railway sets PORT and generates the public
URL. Fails closed if DATABASE_URL is missing.
"""

from __future__ import annotations

import logging
import os
import sys

from ..config import normalize_database_url
from ..db import init_engine
from ..logging_config import configure_logging
from .server import serve


def main() -> int:
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    log = logging.getLogger("kalshi_bot.dashboard")

    database_url = normalize_database_url(os.environ.get("DATABASE_URL", ""))
    if not database_url:
        log.error("DATABASE_URL is not set; dashboard cannot start")
        return 2

    init_engine(database_url)
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("DASHBOARD_PORT", "8080")))
    serve(host, port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
