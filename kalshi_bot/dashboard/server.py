"""Stdlib HTTP server for the read-only dashboard.

Routes:
  GET /               -> the single-page dashboard (static HTML)
  GET /api/dashboard  -> the JSON snapshot (data.build_dashboard_data)
  GET /healthz        -> "ok" (liveness)

No write path exists. Each request opens a short-lived session, reads, and
closes it; nothing is ever added or committed. DB/query errors return a valid
JSON error envelope (with a generic message — never a stack trace) so the page
degrades gracefully instead of leaking internals.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..db import session_scope
from ..evo.config import get_evo_settings
from . import data

logger = logging.getLogger("kalshi_bot.dashboard")

_INDEX = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "kalshi-evo-dashboard/0.1"

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

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            self._send(200, _INDEX.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/healthz":
            self._send(200, b"ok", "text/plain; charset=utf-8")
        elif path == "/api/dashboard":
            self._send_dashboard()
        else:
            self._send(404, b'{"error":"not found"}', "application/json")

    def _send_dashboard(self) -> None:
        try:
            settings = get_evo_settings()
            with session_scope() as session:
                payload = data.build_dashboard_data(session, settings)
        except Exception:  # noqa: BLE001 — never leak internals to a public URL
            logger.exception("dashboard data build failed")
            body = json.dumps({
                "error": "dashboard temporarily unavailable",
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }).encode("utf-8")
            self._send(503, body, "application/json")
            return
        self._send(200, json.dumps(payload, default=str).encode("utf-8"), "application/json")


def serve(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    logger.info("dashboard listening on http://%s:%d", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
