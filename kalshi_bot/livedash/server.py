"""Stdlib HTTP server for the read-only live-vs-paper comparison dashboard.

Routes:
  GET /                              the single-page dashboard
  GET /healthz                       "ok"
  GET /api/runs[?view=selector]      paired runs + unpaired live strategies
                                     (view=selector: the picker's pairs only, no P&L)
  GET /api/runs/<twin_tag>           header, positions, comparison, divergence
  GET /api/runs/<twin_tag>/series     P&L overlay + price/execution series
  GET /api/runs/<twin_tag>/orders     live + paper orders, paired
  GET /api/runs/<twin_tag>/events     the paired-run event timeline

Read-only by construction: only `do_GET`/`do_HEAD` exist, so every other verb gets
a 501 from BaseHTTPRequestHandler, and the data layer contains no write. There is
deliberately no endpoint that can place, cancel, close, pause or reconfigure
anything — this page cannot touch the live book it observes.

Errors return a generic JSON envelope, never a stack trace or a connection string.

Every request is timed and the duration is logged, because the one question this
service could not answer about itself was "which route is slow". A route above
`SLOW_REQUEST_MS` logs at WARNING so a regression announces itself in the ordinary
log stream instead of waiting for an operator to notice a blank card.

A client that goes away mid-response is NOT a request failure. Writing the body of
an already-successful response raises `BrokenPipeError`, and the old handler caught
that alongside real errors, logged "request failed", and then tried to send a 503
into the same dead socket -- which threw again and escaped into socketserver's
traceback dump. The result was a log that reported a successful response as a 503
server error, with a stack trace attached, for what is a normal browser navigation.
Disconnects are now recognised and logged as such, and nothing is ever written to a
response that has already started.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..db import session_scope
from . import data
from .events import CATEGORIES, DEFAULT_CATEGORY, ENVIRONMENTS

logger = logging.getLogger("kalshi_bot.livedash")

_INDEX = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")

# Twin tags are String(24) strategy tags: word characters, dash and dot only.
_TAG_RE = re.compile(r"^[A-Za-z0-9._-]{1,24}$")
_RUN_PATH_RE = re.compile(r"^/api/runs/(?P<tag>[^/]+)(?P<sub>/series|/orders|/events)?$")

# Above this, a route is slow enough that an operator notices, so it is logged at
# WARNING rather than INFO. The page makes six requests on a cold load; anything
# here is a second of somebody waiting at a skeleton.
SLOW_REQUEST_MS = 2000

# The socket errors that mean "the client left", as opposed to "this request is
# broken". They are the normal outcome of a reload, a navigation, a closed tab or
# an upstream proxy giving up, and none of them is this service's fault.
DISCONNECTED = (BrokenPipeError, ConnectionResetError)


def _one(params: dict, key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    return values[0].strip() or None


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


class LiveDashHandler(BaseHTTPRequestHandler):
    server_version = "kalshi-livedash/1.0"

    #: Set as soon as a response begins. A second response on the same request is
    #: not a fallback, it is a protocol violation: the status line and headers have
    #: already gone out, so a 503 written after them is appended to the body the
    #: client is reading. This is the flag the old error path did not have.
    _responded = False
    _bytes_sent = 0

    def log_message(self, fmt, *args):
        logger.info("livedash %s", fmt % args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self._responded = True
        self._bytes_sent = len(body)
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
        started = time.perf_counter()
        try:
            self._dispatch(path, params)
        finally:
            self._log_timing(path, started)

    def _dispatch(self, path: str, params: dict) -> None:
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
            if not self._route(path, params):
                self._json({"error": "not found"}, 404)
        except DISCONNECTED:
            # The client is gone. There is nobody to send a 503 to, and trying is
            # what turned a routine disconnect into a stack trace in the log.
            self.close_connection = True
            data.log_pending_stages()
            logger.info("livedash client disconnected: %s", path)
        except Exception:  # noqa: BLE001 — never leak internals to a public URL
            # How far the build got before it threw, first: on a failing route the
            # stage line is the most useful thing there is, and it is the one route
            # that never reached its own `log()`.
            data.log_pending_stages()
            logger.exception("livedash request failed: %s", path)
            if self._responded:
                # The failure happened while writing a response that had already
                # started, so the status the client saw is settled. Say so instead
                # of writing a second one on top of it.
                self.close_connection = True
                return
            try:
                self._json({
                    "error": "dashboard temporarily unavailable",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }, 503)
            except DISCONNECTED:
                self.close_connection = True

    def _log_timing(self, path: str, started: float) -> None:
        """One line per request saying how long it took.

        Without this the service could report *that* a route failed and never how
        long it spent before doing so, which is the whole of what an operator
        waiting at a blank card needs to know."""
        elapsed_ms = (time.perf_counter() - started) * 1000
        level = logging.WARNING if elapsed_ms >= SLOW_REQUEST_MS else logging.INFO
        logger.log(level, "livedash timing %s %s %.0fms %dB",
                   path, "sent" if self._responded else "no-response",
                   elapsed_ms, self._bytes_sent)

    def _route(self, path: str, params: dict) -> bool:
        if path == "/api/runs":
            # `view=selector` is the cheap half: which pairs exist, so the picker can be
            # rendered before the per-run P&L columns have been reconstructed.
            with session_scope() as session:
                self._json(data.build_runs(
                    session, limit=_int(params, "limit", 50),
                    summaries=_one(params, "view") != "selector",
                ))
            return True

        match = _RUN_PATH_RE.match(path)
        if not match:
            return False
        tag = unquote(match.group("tag"))
        if not _TAG_RE.match(tag):
            self._json({"error": "not found"}, 404)
            return True
        sub = match.group("sub")

        with session_scope() as session:
            if sub == "/series":
                payload = data.build_series(
                    session, tag, since=_dt(params, "since"), until=_dt(params, "until"),
                    ticker=self._tag_param(params, "ticker"),
                    max_points=max(20, min(_int(params, "points", 300) or 300, 1000)),
                )
            elif sub == "/orders":
                payload = data.build_orders(
                    session, tag,
                    environment=_choice(params, "env", ENVIRONMENTS),
                    limit=_int(params, "limit", 100), offset=_int(params, "offset", 0),
                )
            elif sub == "/events":
                payload = data.build_events(
                    session, tag,
                    category=_choice(params, "category", CATEGORIES, DEFAULT_CATEGORY),
                    environment=_choice(params, "env", ENVIRONMENTS),
                    limit=_int(params, "limit", 50), cursor=_one(params, "cursor"),
                )
            else:
                payload = data.build_run(
                    session, tag, incumbent=_one(params, "incumbent") == "1")
            self._json(payload if payload is not None else {"error": "run not found"},
                       200 if payload is not None else 404)
        return True

    def _tag_param(self, params: dict, key: str) -> str | None:
        """Market tickers are longer than strategy tags but equally restricted."""
        value = _one(params, key)
        if value and re.match(r"^[A-Za-z0-9._-]{1,128}$", value):
            return value
        return None


def serve(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), LiveDashHandler)
    logger.info("livedash listening on http://%s:%d", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
