"""Authenticated desk API. The existing public ops branch is never a write transport."""
from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from pydantic import ValidationError

from .contracts import Decision, DeskError, utcnow

MAX_BODY = 256_000
MAX_CHUNK_LINE = 128
MAX_TRAILER_BYTES = 8_192
_INDEX = Path(__file__).parent / "static" / "index.html"


class InvalidBodySize(ValueError):
    """Request framing is unsupported, malformed, empty, or over the body limit."""


def _read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise InvalidBodySize
        data.extend(chunk)
    return bytes(data)


def _read_chunked(stream):
    """Decode a strictly bounded HTTP/1.1 chunked body from ``BaseHTTPRequestHandler``."""
    body = bytearray()
    while True:
        line = stream.readline(MAX_CHUNK_LINE + 1)
        if not line or len(line) > MAX_CHUNK_LINE or not line.endswith(b"\r\n"):
            raise InvalidBodySize
        size_text = line[:-2].split(b";", 1)[0]
        try:
            if not size_text or any(c not in b"0123456789abcdefABCDEF" for c in size_text):
                raise ValueError
            size = int(size_text, 16)
        except ValueError:
            raise InvalidBodySize from None
        if size > MAX_BODY - len(body):
            raise InvalidBodySize
        if size == 0:
            trailer_bytes = 0
            while True:
                trailer = stream.readline(MAX_CHUNK_LINE + 1)
                trailer_bytes += len(trailer)
                if (not trailer or len(trailer) > MAX_CHUNK_LINE
                        or trailer_bytes > MAX_TRAILER_BYTES or not trailer.endswith(b"\r\n")):
                    raise InvalidBodySize
                if trailer == b"\r\n":
                    if not body:
                        raise InvalidBodySize
                    return bytes(body)
        body.extend(_read_exact(stream, size))
        if _read_exact(stream, 2) != b"\r\n":
            raise InvalidBodySize


def handler_for(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DeskService/1"

        def log_message(self, *_args):
            pass  # no tokens, query strings, account data or payloads in access logs

        def send(self, status, body, content_type="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body, default=str, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def role(self):
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return None
            token = header[7:]
            if not token.isascii():
                return None
            for role in ("operator", "chatgpt", "claude", "diagnostic"):
                expected = getattr(service.settings, f"{role}_token").get_secret_value()
                if expected and hmac.compare_digest(token, expected):
                    return role
            return None

        def diagnostics(self, query):
            """Sanitized read-only report for any role; the only route `diagnostic` reaches."""
            pairs = parse_qsl(query, keep_blank_values=True)
            if len(pairs) > 1 or any(k != "market_probe" or v not in ("0", "1") for k, v in pairs):
                return self.send(400, {"error": "invalid_query"})
            from .diagnostics import build_report
            try:
                return self.send(200, build_report(service, probe=dict(pairs).get("market_probe", "1") == "1"))
            except Exception:
                return self.send(503, {"error": "diagnostics_unavailable"})

        def browse(self, path, query):
            """Read-only public market discovery for any authenticated role (DEC-024)."""
            if service.browser is None:
                raise DeskError("market_browse_unavailable")
            pairs = parse_qsl(query, keep_blank_values=False)
            if len(pairs) > 20 or any(len(k) > 40 or len(v) > 200 for k, v in pairs):
                raise DeskError("invalid_query")
            params = dict(pairs)
            parts = path.strip("/").split("/")[1:]
            browser = service.browser
            if parts == ["markets"]:
                return browser.markets(params)
            if parts == ["categories"]:
                return browser.categories()
            if parts == ["events"]:
                return browser.events(params)
            if parts == ["series"]:
                return browser.series(params)
            if len(parts) == 2 and parts[0] == "markets":
                return browser.market(parts[1])
            if len(parts) == 3 and parts[0] == "markets" and parts[2] == "orderbook":
                return browser.orderbook(parts[1], params.get("depth", 10))
            if len(parts) == 3 and parts[0] == "markets" and parts[2] == "trades":
                return browser.trades(parts[1], params.get("limit", 50), params.get("cursor"))
            if len(parts) == 2 and parts[0] == "events":
                return browser.event(parts[1])
            if len(parts) == 2 and parts[0] == "series":
                return browser.series_detail(parts[1])
            return None

        def do_GET(self):  # noqa: N802
            split = urlsplit(self.path)
            path = split.path
            if path == "/":
                return self.send(200, _INDEX.read_bytes(), "text/html; charset=utf-8")
            if path == "/healthz":
                return self.send(200, {"service": "desks", "status": "up"})
            role = self.role()
            if not role:
                return self.send(401, {"error": "authentication_required"})
            if path == "/api/diagnostics":
                return self.diagnostics(split.query)
            if role == "diagnostic":
                # Least privilege: no status, decisions, schema or market browsing.
                return self.send(403, {"error": "role_forbidden"})
            if path.split("/")[1:3] in (["api", "markets"], ["api", "events"], ["api", "series"],
                                        ["api", "categories"]):
                try:
                    result = self.browse(path, split.query)
                    return self.send(200, result) if result is not None else self.send(404, {"error": "not_found"})
                except DeskError as exc:
                    status = {"market_not_found": 404, "market_data_unavailable": 503,
                              "market_data_too_large": 503, "market_browse_unavailable": 503,
                              "market_data_timeout": 503, "market_data_rate_limited": 429}.get(exc.code, 400)
                    return self.send(status, {"error": exc.code})
                except Exception:
                    return self.send(503, {"error": "market_data_unavailable"})
            try:
                if path in ("/api/status", "/api/context"):
                    return self.send(200, service.status())
                if path == "/api/research/schema":
                    from .research import ResearchOutput
                    return self.send(200, ResearchOutput.model_json_schema())
                if path.startswith("/api/decisions/"):
                    row = service.store.get_decision(path.rsplit("/", 1)[-1])
                    return self.send(200 if row else 404, row or {"error": "not_found"})
                return self.send(404, {"error": "not_found"})
            except Exception:
                return self.send(503, {"error": "status_unavailable"})

        def do_POST(self):  # noqa: N802
            role = self.role()
            if not role:
                return self.send(401, {"error": "authentication_required"})
            if role == "diagnostic":
                return self.send(403, {"error": "role_forbidden"})  # never a writer
            try:
                if self.headers.get_content_type() != "application/json":
                    return self.send(415, {"error": "json_required"})
                lengths = self.headers.get_all("Content-Length", [])
                encodings = [part.strip().lower() for value in
                             self.headers.get_all("Transfer-Encoding", [])
                             for part in value.split(",") if part.strip()]
                if encodings:
                    # Never accept ambiguous CL+TE framing or stacked transfer codings.
                    if lengths or encodings != ["chunked"]:
                        raise InvalidBodySize
                    raw = _read_chunked(self.rfile)
                else:
                    if len(lengths) != 1:
                        raise InvalidBodySize
                    try:
                        length = int(lengths[0])
                    except ValueError:
                        raise InvalidBodySize from None
                    if length <= 0 or length > MAX_BODY:
                        raise InvalidBodySize
                    raw = _read_exact(self.rfile, length)
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise DeskError("object_required")
                result = self.mutate(urlsplit(self.path).path, role, body)
                self.send(200, result if result is not None else {"ok": True})
            except InvalidBodySize:
                self.send(413, {"error": "invalid_body_size"})
            except PermissionError:
                self.send(403, {"error": "role_forbidden"})
            except (ValidationError, ValueError, KeyError) as exc:
                code = exc.code if isinstance(exc, DeskError) else "invalid_request"
                self.send(409 if isinstance(exc, DeskError) else 400, {"error": code})
            except Exception:
                self.send(503, {"error": "operation_unavailable"})

        def mutate(self, path, role, body):
            now = utcnow()
            if path == "/api/round/preflight":
                if role != "operator":
                    raise PermissionError
                return service.check_launch(now, refresh=True)
            if path == "/api/round/start":
                if role != "operator":
                    raise PermissionError
                return service.start(now)
            if path == "/api/alerts/test":
                if role != "operator":
                    raise PermissionError
                if service.notifier is None:
                    raise DeskError("alert_channel_unavailable")
                return service.notifier.test_delivery(now)
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "desks"]:
                raise DeskError("unknown_route")
            desk, action = parts[2:]
            if desk not in ("chatgpt", "claude") or role not in (desk, "operator"):
                raise PermissionError
            if action in ("pause", "resume"):
                if role != "operator":
                    raise PermissionError
                if action == "pause":
                    reason = str(body.get("reason", "operator pause"))[:500]
                    return service.store.pause(desk, reason)
                return service.store.resume(desk)
            if action == "ready":
                return service.store.ready(desk, now)
            if action == "continue":
                return service.continue_research(desk, now)
            if action == "decisions":
                decision = Decision.model_validate(body)
                if decision.desk_id != desk:
                    raise PermissionError
                return service.submit(decision)
            if action == "publications":
                kind = body.get("kind")
                if kind not in {"candidate", "rejection", "lesson", "postmortem", "paper", "source", "handoff"}:
                    raise DeskError("invalid_publication_kind")
                if not isinstance(body.get("payload"), dict):
                    raise DeskError("publication_object_required")
                if kind == "postmortem":
                    from .research import Postmortem
                    review = Postmortem.model_validate(body["payload"])
                    target = service.store.get_decision(review.decision_id)
                    if not target or target["desk_id"] != desk or not target.get("settled"):
                        raise DeskError("postmortem_requires_own_settlement")
                    body["payload"] = review.model_dump(mode="json")
                return {"record_id": service.store.publish(desk, kind, body["payload"], now,
                                                           record_id=body.get("record_id"))}
            if action == "claim":
                worker = str(body.get("worker_id", ""))
                if not 1 <= len(worker) <= 100:
                    raise DeskError("worker_id_required")
                return service.supervisor.claim_external(desk, worker, now)
            if action == "complete":
                return service.supervisor.complete_external(
                    body["job_id"], body["claim_token"], body["payload"], body["model_id"], now,
                    desk_id=desk)
            if action == "source":
                return service.supervisor.fetch_external_source(
                    body["job_id"], body["claim_token"], desk, body["url"], now)
            raise DeskError("unknown_route")

    return Handler


def make_server(service, address=None):
    return ThreadingHTTPServer(address or (service.settings.host, service.settings.port),
                               handler_for(service))
