"""Authenticated desk API. The existing public ops branch is never a write transport."""
from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError

from .contracts import Decision, DeskError, utcnow

MAX_BODY = 256_000
_INDEX = Path(__file__).parent / "static" / "index.html"


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
            for role in ("operator", "chatgpt", "claude"):
                expected = getattr(service.settings, f"{role}_token").get_secret_value()
                if hmac.compare_digest(token, expected):
                    return role
            return None

        def do_GET(self):  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/":
                return self.send(200, _INDEX.read_bytes(), "text/html; charset=utf-8")
            if path == "/healthz":
                return self.send(200, {"service": "desks", "status": "up"})
            if not self.role():
                return self.send(401, {"error": "authentication_required"})
            try:
                if path in ("/api/status", "/api/context"):
                    return self.send(200, service.status())
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
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY or self.headers.get("Transfer-Encoding"):
                    return self.send(413, {"error": "invalid_body_size"})
                if self.headers.get_content_type() != "application/json":
                    return self.send(415, {"error": "json_required"})
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise DeskError("object_required")
                result = self.mutate(urlsplit(self.path).path, role, body)
                self.send(200, result if result is not None else {"ok": True})
            except PermissionError:
                self.send(403, {"error": "role_forbidden"})
            except (ValidationError, ValueError, KeyError) as exc:
                code = exc.code if isinstance(exc, DeskError) else "invalid_request"
                self.send(409 if isinstance(exc, DeskError) else 400, {"error": code})
            except Exception:
                self.send(503, {"error": "operation_unavailable"})

        def mutate(self, path, role, body):
            now = utcnow()
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
                return service.submit(decision, now)
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
