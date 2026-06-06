"""Structured JSON logging to stdout for Railway, with secret redaction."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_SECRET_KEYS = {
    "private_key",
    "kalshi_private_key",
    "signature",
    "authorization",
    "kalshi-access-signature",
    "kalshi-access-key",
    "api_key",
    "api_key_id",
    "password",
    "secret",
}


def _redact(data: dict) -> dict:
    out: dict = {}
    for k, v in data.items():
        out[k] = "***" if str(k).lower() in _SECRET_KEYS else v
    return out


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(_redact(extra))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    # Keep third-party loggers from leaking request internals at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, level: int, message: str, **fields) -> None:
    """Emit a structured log line with arbitrary (redacted) key/value fields."""
    logger.log(level, message, extra={"extra_fields": fields})
