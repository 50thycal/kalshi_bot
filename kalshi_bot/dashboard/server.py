"""Stdlib HTTP server for the read-only evolution fleet dashboard.

Routes:
  GET /                          -> the single-page dashboard (static HTML)
  GET /healthz                   -> "ok" (liveness)
  GET /api/fleet                 -> generation header, cohort totals, health, history
  GET /api/bots                  -> the leaderboard (server-filtered + server-sorted)
  GET /api/bots/<uuid>           -> one bot: strategy, performance, lineage
  GET /api/bots/<uuid>/trades    -> one bot's trades (paginated)
  GET /api/events                -> categorised activity (cursor-paginated)
  GET /api/announcements         -> operator announcements (paginated)

No write path exists: only GET and HEAD are served, and every handler opens a
short-lived read-only session. DB/query errors return a valid JSON error envelope
(with a generic message — never a stack trace) so the page degrades gracefully
instead of leaking internals.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..db import session_scope
from ..evo.config import get_evo_settings
from . import data
from .events import CATEGORIES, DEFAULT_CATEGORY

logger = logging.getLogger("kalshi_bot.dashboard")

_INDEX = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")

# Agent identifiers are uuid4 strings; reject anything else before it reaches SQL.
_UUID_RE = re.compile(r"^[A-Za-z0-9._:-]{4,64}$")
_BOT_PATH_RE = re.compile(r"^/api/bots/(?P<uuid>[^/]+)(?P<sub>/trades)?$")


def _one(params: dict, key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def _int(params: dict, key: str, default: int | None = None) -> int | None:
    raw = _one(params, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _choice(params: dict, key: str, allowed, default=None):
    value = _one(params, key)
    return value if value in allowed else default


def _dt(params: dict, key: str) -> datetime | None:
    raw = _one(params, key)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "kalshi-evo-dashboard/0.2"

    def log_message(self, fmt, *args):  # quieter than the default stderr spam
        logger.info("dashboard %s", fmt % args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload, default=str).encode("utf-8"), "application/json")

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        if path == "/":
            self._send(200, _INDEX.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/healthz":
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return
        if not path.startswith("/api/"):
            self._json({"error": "not found"}, 404)
            return

        try:
            handled = self._route_api(path, params)
        except Exception:  # noqa: BLE001 — never leak internals to a public URL
            logger.exception("dashboard request failed: %s", path)
            self._json({
                "error": "dashboard temporarily unavailable",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }, 503)
            return
        if not handled:
            self._json({"error": "not found"}, 404)

    def _route_api(self, path: str, params: dict) -> bool:
        settings = get_evo_settings()

        if path == "/api/fleet":
            with session_scope() as session:
                self._json(data.build_fleet(session, settings))
            return True

        if path == "/api/bots":
            with session_scope() as session:
                self._json(data.build_bots(
                    session, settings,
                    generation=_int(params, "generation"),
                    status=_one(params, "status"),
                    family=_one(params, "family"),
                    strategy=_one(params, "strategy"),
                    profitability=_choice(
                        params, "profitability", ("profitable", "unprofitable", "flat")),
                    activity=_choice(params, "activity", ("active", "inactive")),
                    sort=_choice(params, "sort", data.BOT_SORTS, data.DEFAULT_BOT_SORT),
                    order=_choice(params, "order", ("asc", "desc"), "desc"),
                    limit=_int(params, "limit"),
                ))
            return True

        if path == "/api/events":
            with session_scope() as session:
                self._json(data.build_events(
                    session,
                    category=_choice(params, "category", CATEGORIES, DEFAULT_CATEGORY),
                    agent_uuid=self._uuid_param(params, "bot"),
                    generation=_int(params, "generation"),
                    since=_dt(params, "since"),
                    until=_dt(params, "until"),
                    search=_one(params, "q"),
                    limit=_int(params, "limit", 40),
                    cursor=_one(params, "cursor"),
                ))
            return True

        if path == "/api/announcements":
            with session_scope() as session:
                self._json(data.build_announcements(
                    session,
                    limit=_int(params, "limit", 20),
                    offset=_int(params, "offset", 0),
                ))
            return True

        match = _BOT_PATH_RE.match(path)
        if match:
            uuid = match.group("uuid")
            if not _UUID_RE.match(uuid):
                self._json({"error": "not found"}, 404)
                return True
            with session_scope() as session:
                if match.group("sub") == "/trades":
                    payload = data.build_bot_trades(
                        session, settings, uuid,
                        status=_choice(params, "status", ("all", "open", "closed"), "all"),
                        sort=_choice(
                            params, "sort", ("recent", "pnl", "size", "hold", "market"), "recent"),
                        limit=_int(params, "limit", 100),
                        offset=_int(params, "offset", 0),
                        scope=_choice(params, "scope", ("generation", "lifetime"), "generation"),
                    )
                else:
                    payload = data.build_bot_detail(session, settings, uuid)
                self._json(payload if payload is not None else {"error": "bot not found"},
                           200 if payload is not None else 404)
            return True

        return False

    def _uuid_param(self, params: dict, key: str) -> str | None:
        value = _one(params, key)
        return value if value and _UUID_RE.match(value) else None


def serve(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    logger.info("dashboard listening on http://%s:%d", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
