"""Live-vs-paper dashboard entrypoint: `python -m kalshi_bot.livedash`.

Reads DATABASE_URL and PORT from the environment only — it does NOT construct the
full fail-closed Settings, so this service needs no Kalshi credentials and no LLM
key. It never calls an exchange, and it never writes to the database.
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
    log = logging.getLogger("kalshi_bot.livedash")

    database_url = normalize_database_url(os.environ.get("DATABASE_URL", ""))
    if not database_url:
        log.error("DATABASE_URL is not set; livedash cannot start")
        return 2

    init_engine(database_url)
    host = os.environ.get("LIVEDASH_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("LIVEDASH_PORT", "8081")))
    serve(host, port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
