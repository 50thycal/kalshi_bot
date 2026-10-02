"""Ops `{"type":"desks"}`: one read-only desk-service diagnostic for a public result.

    {"type": "desks", "id": "desk-diag-20261002-1"}
    {"type": "desks", "id": "desk-diag-20261002-2", "market_probe": false, "format": "json"}

The runner calls `GET <desk service>/api/diagnostics` ONCE with the dedicated
diagnostic-role token and prints a re-sanitized projection to
`ops/results/<id>.txt`. That role can read only this one sanitized endpoint on the
desk service: it cannot read `/api/status`, browse markets, claim, continue,
capture, complete, decide, pause, resume, preflight or start. The operator and
desk tokens never reach GitHub Actions.

Defence in depth: the service already sanitizes its report, and this client trusts
none of it. Every field is projected through a fixed schema (codes, counts,
booleans, bounded numbers, timestamps); anything else becomes a placeholder. The
response body, response headers and exception text are never printed.

Stdlib only: the ops runner's default path installs nothing but psycopg.

Exit 0: the diagnostic completed (the desk itself may still need attention — read
VERDICT). Exit 1: the diagnostic itself failed or was incomplete, or the request
was refused. A failed diagnostic is never reported as a healthy desk.
"""
from __future__ import annotations

import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit

DEFAULT_URL = "https://desk-service-production.up.railway.app"
TOKEN_ENV = "DESKS_DIAGNOSTIC_TOKEN"
URL_ENV = "DESKS_DIAGNOSTIC_URL"
SCHEMA = "desk-diagnostics/1"
TIMEOUT_SECONDS = 20          # the service's own market probe is capped at 5 s
MAX_RESPONSE_BYTES = 256_000
ALLOWED_KEYS = {"type", "id", "market_probe", "format", "actor", "purpose", "workstream", "issue"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")

FAILURES = {
    "invalid_request": "The request was refused before any network call (see the reason above).",
    "not_configured": f"The {TOKEN_ENV} secret is not available to the ops runner. "
                      "Provisioning steps: docs/desks/DIAGNOSTICS.md.",
    "invalid_configuration": "The configured desk service URL is not a plain HTTPS base URL.",
    "authentication_refused": "The desk service refused the diagnostic token (401/403). Check that "
                              "DESKS_DIAGNOSTIC_TOKEN matches on Railway and in GitHub.",
    "endpoint_not_deployed": "The desk service has no /api/diagnostics route (404): the deployed "
                             "revision predates this feature.",
    "redirect_refused": "The service redirected; configure the final service URL directly.",
    "request_rejected": "The service rejected the diagnostic query (400).",
    "service_rate_limited": "The desk service itself answered 429.",
    "service_error": "The desk service answered with a server error (5xx).",
    "unexpected_status": "The desk service answered with an unexpected HTTP status.",
    "timeout": f"No response within {TIMEOUT_SECONDS}s.",
    "connection_failed": "The desk service could not be reached (DNS, TLS or connection failure).",
    "response_too_large": "The diagnostic response exceeded the size limit.",
    "invalid_response": "The service did not return a valid diagnostic report.",
    "redaction_guard": "The rendered report contained a configured secret and was withheld.",
}


class DiagError(Exception):
    def __init__(self, code: str, http_status: int | None = None):
        super().__init__(code)
        self.code, self.http_status = code, http_status


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def validate_request(req: dict, results_dir: str | None = None) -> dict:
    """The validated options, or DiagError('invalid_request') with a printed reason."""
    def refuse(reason):
        print(f"refused: {reason}", file=sys.stderr)
        raise DiagError("invalid_request")

    if not isinstance(req, dict):
        refuse("request must be a JSON object")
    extra = sorted(set(req) - ALLOWED_KEYS)
    if extra:
        refuse(f"unknown field(s) {extra}; allowed: {sorted(ALLOWED_KEYS)}")
    rid = req.get("id")
    if not isinstance(rid, str) or not ID_PATTERN.match(rid):
        refuse("a unique 'id' of 3-64 characters [A-Za-z0-9._-] is required, so the report "
               "lands at ops/results/<id>.txt and no other session's report is mistaken for yours")
    if results_dir and os.path.exists(os.path.join(results_dir, rid + ".txt")):
        refuse(f"id {rid!r} already has a result on the transport; choose a new id")
    probe = req.get("market_probe", True)
    if not isinstance(probe, bool):
        refuse("'market_probe' must be true or false")
    fmt = req.get("format", "text")
    if fmt not in ("text", "json"):
        refuse("'format' must be 'text' or 'json'")
    return {"id": rid, "market_probe": probe, "format": fmt}


def endpoint(url: str, market_probe: bool) -> str:
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == "https" and parts.hostname and parts.username is None
                 and parts.password is None and not parts.query and not parts.fragment
                 and parts.path in ("", "/") and url.isascii()
                 and not any(ord(c) <= 32 for c in url))
        parts.port  # noqa: B018 — raises on a malformed port
    except ValueError:
        valid = False
    if not valid:
        raise DiagError("invalid_configuration")
    return url.rstrip("/") + "/api/diagnostics?market_probe=" + ("1" if market_probe else "0")


# ---------------------------------------------------------------------------
# One bounded request
# ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(url: str, token: str, *, opener=None) -> dict:
    """Exactly one GET. Never retried: a throttled or failing service is reported, not hammered."""
    opener = opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, method="GET", headers={
        "Authorization": "Bearer " + token, "Accept": "application/json",
        "Accept-Encoding": "identity", "User-Agent": "kalshi-ops-desk-diagnostics/1"})
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if status in (401, 403):
            raise DiagError("authentication_refused", status) from None
        if 300 <= status < 400:
            raise DiagError("redirect_refused", status) from None
        code = {404: "endpoint_not_deployed", 400: "request_rejected",
                429: "service_rate_limited"}.get(status)
        raise DiagError(code or ("service_error" if status >= 500 else "unexpected_status"), status) from None
    except TimeoutError:
        raise DiagError("timeout") from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        raise DiagError("timeout" if isinstance(reason, TimeoutError)
                        else "connection_failed") from None
    except (OSError, ValueError):
        raise DiagError("connection_failed") from None
    if status != 200:
        raise DiagError("unexpected_status", status)
    if len(body) > MAX_RESPONSE_BYTES:
        raise DiagError("response_too_large", status)
    if content_type != "application/json":
        raise DiagError("invalid_response", status)

    def reject_constant(_value):
        raise ValueError

    try:
        payload = json.loads(body, parse_constant=reject_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise DiagError("invalid_response", status) from None
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise DiagError("invalid_response", status)
    return payload


# ---------------------------------------------------------------------------
# Re-sanitization: a fixed projection, never a pass-through
# ---------------------------------------------------------------------------

_CODE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_SECRETISH = re.compile(r"[0-9a-f]{16,}|[0-9]{8,}")
_JOB_ID = re.compile(r"^(session|research)-[A-Za-z0-9_-]{1,100}-(chatgpt|claude)-[A-Za-z0-9]{1,40}$")
_ROUND = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
REDACTED = "[redacted]"


def code(value):
    if value is None:
        return None
    if isinstance(value, str) and _CODE.match(value) and not _SECRETISH.search(value):
        return value
    return REDACTED


def compound(value):
    """`a:b` codes (e.g. `trading_paused:unknown_order_status`), each part a code."""
    if not isinstance(value, str) or len(value) > 160:
        return REDACTED
    parts = value.split(":")
    return REDACTED if len(parts) > 2 or REDACTED in map(code, parts) else value


def flag(value):
    return value if isinstance(value, bool) else None


def number(low, high):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or not low <= value <= high:
            return None
        return int(value)
    return check


def stamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.isoformat() if parsed.tzinfo is not None else None


def enum(*allowed):
    return lambda value: value if value in allowed else (None if value is None else REDACTED)


def pattern(regex):
    return lambda value: value if isinstance(value, str) and regex.match(value) else REDACTED


def listof(item, limit):
    return lambda value: [item(v) for v in value[:limit]] if isinstance(value, list) else []


COUNT = number(0, 10_000_000)
SECONDS = number(-86_400, 10 * 365 * 86_400)


def obj(schema):
    def project(value):
        if not isinstance(value, dict):
            return None
        if set(value) == {"error"}:
            return {"error": code(value["error"])}       # a section the service could not build
        return {key: check(value.get(key)) for key, check in schema.items() if key in value}
    return project


DESK = obj({
    "health": enum("healthy", "starting", "recovering", "needs_operator", "unrecognized"),
    "activity": code, "reasons": listof(compound, 20), "ready": flag, "paused": flag,
    "pause_category": code, "book_status": code, "completed_cycles": COUNT,
    "pending_jobs": COUNT, "recent_failures": COUNT, "last_completed_at": stamp,
    "unreviewed_settlements": COUNT,
})
JOB = obj({
    "job_id": pattern(_JOB_ID), "desk": enum("chatgpt", "claude"),
    "state": enum("queued", "claimed", "running", "retry", "publishing", "completed", "failed", "unrecognized"),
    "error_category": code, "created_at": stamp, "updated_at": stamp, "lease_active": flag,
})
RETRY_AFTER = obj({"present": flag, "valid": flag, "seconds": number(0, 86_400)})
_PROBE_FIELDS = {
    "outcome": code, "target": code, "request_sent": flag, "cached": flag,
    "cached_age_seconds": number(0, 86_400), "cooldown_remaining_seconds": number(0, 86_400),
    "http_status": number(100, 599), "latency_ms": number(0, 600_000), "retry_after": RETRY_AFTER,
}
PROBE = obj({**_PROBE_FIELDS, "previous": obj(_PROBE_FIELDS)})
REPORT = obj({
    "schema": enum(SCHEMA),
    "generated_at": stamp,
    "verdict": enum("healthy", "attention", "diagnostic_incomplete"),
    "attention": listof(compound, 40),
    "diagnostic": obj({"read_only": flag, "failed_sections": listof(code, 10)}),
    "service": obj({
        "research_mode": code, "alert_mode": code, "account_mode": code, "live_enabled": flag,
        "round_id": pattern(_ROUND), "round_started": flag, "started_at": stamp,
        "worker": obj({"last_tick": stamp, "last_tick_age_seconds": SECONDS,
                       "monitor_fresh": flag, "error": code}),
    }),
    "desks": obj({"chatgpt": DESK, "claude": DESK}),
    "readiness": obj({"ready": flag, "blockers": listof(compound, 50), "note": enum(
        "cached launch checks; nothing was refreshed")}),
    "jobs": obj({"limit_per_desk": COUNT, "recent": listof(JOB, 20)}),
    "board": obj({
        "progressive_board": obj({
            "state": code, "pages_seen": COUNT, "completed_passes": COUNT, "cached_markets": COUNT,
            "sample_markets": COUNT, "continuation_pending": flag, "snapshot_as_of": stamp,
            "snapshot_age_seconds": SECONDS,
            "scan_lease": obj({"active": flag, "seconds_remaining": number(0, 86_400)}),
        }),
        "whole_board_index": obj({
            "state": code, "scan_in_progress": flag, "as_of": stamp, "age_seconds": SECONDS,
            "ttl_seconds": COUNT, "events_scanned": COUNT, "pages": COUNT, "markets": COUNT,
            "complete": flag,
        }),
    }),
    "market_probe": PROBE,
})


def sanitize(payload: dict) -> dict:
    report = REPORT(payload) or {}
    if report.get("schema") != SCHEMA or report.get("verdict") in (None, REDACTED):
        raise DiagError("invalid_response", 200)
    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt(value):
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "none"
    return str(value)


def render(report: dict, elapsed_ms: int) -> str:
    lines = []
    add = lines.append
    verdict = report["verdict"]
    complete = verdict != "diagnostic_incomplete"
    add("DESK SERVICE DIAGNOSTIC (read-only)")
    add("=" * 72)
    add("DIAGNOSTIC : " + ("OK" if complete else "INCOMPLETE — some sections could not be read"))
    add("VERDICT    : " + verdict.upper())
    add("ATTENTION  : " + _fmt(report.get("attention")))
    diag = report.get("diagnostic") or {}
    if diag.get("failed_sections"):
        add("FAILED SECTIONS: " + _fmt(diag["failed_sections"]))
    add(f"report generated {report.get('generated_at')} · round trip {elapsed_ms} ms")
    add("Nothing was claimed, continued, captured, completed, paused, resumed, refreshed or traded.")

    def section(title, value, rows):
        add("")
        add(title)
        add("-" * 72)
        if not isinstance(value, dict):
            add("  (not reported)")
            return
        if "error" in value:
            add(f"  SECTION FAILED: {value['error']}")
            return
        for label, item in rows(value):
            add(f"  {label:<28} {_fmt(item)}")

    section("SERVICE", report.get("service"), lambda s: [
        ("research / alert / account", f"{s.get('research_mode')} / {s.get('alert_mode')} / {s.get('account_mode')}"),
        ("live enabled", s.get("live_enabled")),
        ("round", s.get("round_id")),
        ("round started", s.get("round_started")),
        ("worker last tick", (s.get("worker") or {}).get("last_tick")),
        ("worker tick age (s)", (s.get("worker") or {}).get("last_tick_age_seconds")),
        ("monitor fresh", (s.get("worker") or {}).get("monitor_fresh")),
        ("worker error", (s.get("worker") or {}).get("error")),
    ])

    def desk_rows(d):
        rows = []
        for desk in ("chatgpt", "claude"):
            item = d.get(desk)
            if not isinstance(item, dict):
                rows.append((desk, "(not reported)"))
                continue
            rows += [
                (f"{desk} health", f"{item.get('health')} ({item.get('activity')})"),
                (f"{desk} reasons", item.get("reasons")),
                (f"{desk} ready / paused", f"{_fmt(item.get('ready'))} / {_fmt(item.get('paused'))}"
                 + (f" ({item['pause_category']})" if item.get("pause_category") else "")),
                (f"{desk} cycles / pending / fail", f"{_fmt(item.get('completed_cycles'))} / "
                 f"{_fmt(item.get('pending_jobs'))} / {_fmt(item.get('recent_failures'))}"),
                (f"{desk} last completed", item.get("last_completed_at")),
                (f"{desk} unreviewed settles", item.get("unreviewed_settlements")),
            ]
        return rows

    section("DESKS", report.get("desks"), desk_rows)
    section("READINESS (cached; not refreshed)", report.get("readiness"), lambda r: [
        ("launch ready", r.get("ready")), ("blockers", r.get("blockers"))])

    jobs = report.get("jobs")
    add("")
    add("RECENT RESEARCH JOBS")
    add("-" * 72)
    if isinstance(jobs, dict) and "error" in jobs:
        add(f"  SECTION FAILED: {jobs['error']}")
    elif not isinstance(jobs, dict) or not jobs.get("recent"):
        add("  (none in this round)")
    else:
        for job in jobs["recent"]:
            if not isinstance(job, dict):
                continue
            lease = " lease-active" if job.get("lease_active") else ""
            add(f"  {_fmt(job.get('desk')):<8} {_fmt(job.get('state')):<10} "
                f"{_fmt(job.get('error_category')):<34}{lease}")
            add(f"           {_fmt(job.get('job_id'))}")
            add(f"           created {_fmt(job.get('created_at'))} · updated {_fmt(job.get('updated_at'))}")

    def board_rows(b):
        rows = []
        pb = b.get("progressive_board")
        if isinstance(pb, dict) and "pages_seen" in pb:
            lease = pb.get("scan_lease") or {}
            rows += [("claim board pages / passes", f"{_fmt(pb.get('pages_seen'))} / {_fmt(pb.get('completed_passes'))}"),
                     ("claim board cached markets", pb.get("cached_markets")),
                     ("claim board sample size", pb.get("sample_markets")),
                     ("claim board snapshot age (s)", pb.get("snapshot_age_seconds")),
                     ("claim board continuation", pb.get("continuation_pending")),
                     ("scan lease active", f"{_fmt(lease.get('active'))}"
                      + (f" ({lease.get('seconds_remaining')}s left)" if lease.get("active") else ""))]
        else:
            rows.append(("claim board", (pb or {}).get("state") or (pb or {}).get("error") or "-"))
        wi = b.get("whole_board_index")
        if isinstance(wi, dict) and wi.get("state") == "cached":
            rows += [("browse index age (s)", f"{_fmt(wi.get('age_seconds'))} (ttl {_fmt(wi.get('ttl_seconds'))})"),
                     ("browse index coverage", f"{_fmt(wi.get('markets'))} markets · {_fmt(wi.get('events_scanned'))} "
                      f"events · {_fmt(wi.get('pages'))} pages · complete={_fmt(wi.get('complete'))}"),
                     ("browse scan in progress", wi.get("scan_in_progress"))]
        else:
            rows.append(("browse index", (wi or {}).get("state") or (wi or {}).get("error") or "-"))
        return rows

    section("BOARD / CACHE", report.get("board"), board_rows)

    def probe_rows(p):
        retry = p.get("retry_after") or {}
        retry_text = ("absent" if retry.get("present") is False else
                      f"{retry.get('seconds')}s" if retry.get("valid") else
                      "present but invalid (not echoed)" if retry.get("present") else "-")
        rows = [("outcome", p.get("outcome")), ("request sent now", p.get("request_sent")),
                ("HTTP status", p.get("http_status")), ("latency (ms)", p.get("latency_ms")),
                ("Retry-After", retry_text)]
        if p.get("cached"):
            rows.append(("cached result age (s)", p.get("cached_age_seconds")))
        if p.get("cooldown_remaining_seconds") is not None:
            rows.append(("cooldown remaining (s)", p.get("cooldown_remaining_seconds")))
        previous = p.get("previous")
        if isinstance(previous, dict):
            rows.append(("previous probe", f"{previous.get('outcome')} HTTP {_fmt(previous.get('http_status'))}"))
        return rows

    section("PUBLIC MARKET GET (from desk-service egress; single attempt)", report.get("market_probe"), probe_rows)
    return "\n".join(lines)


def render_failure(error: DiagError, elapsed_ms: int) -> str:
    lines = ["DESK SERVICE DIAGNOSTIC (read-only)", "=" * 72,
             f"DIAGNOSTIC : FAILED — {error.code}",
             "VERDICT    : UNKNOWN — this is NOT a health result; the desk was not assessed",
             f"DETAIL     : {FAILURES.get(error.code, 'unclassified failure')}"]
    if error.http_status is not None:
        lines.append(f"HTTP STATUS: {error.http_status}")
    lines.append(f"elapsed {elapsed_ms} ms · one attempt, no retry")
    return "\n".join(lines)


def _guard(text: str, secrets: list[str]) -> str:
    if any(s and len(s) >= 8 and s in text for s in secrets):
        return render_failure(DiagError("redaction_guard"), 0)
    return text


def run(req: dict, *, environ=None, opener=None, results_dir=None) -> int:
    environ = os.environ if environ is None else environ
    token = (environ.get(TOKEN_ENV) or "").strip()
    started = time.monotonic()
    fmt = req.get("format") if req.get("format") in ("text", "json") else "text"
    try:
        options = validate_request(req, results_dir)
        if not token:
            raise DiagError("not_configured")
        if len(token) > 4096 or not token.isascii() or any(ord(c) <= 32 for c in token):
            raise DiagError("invalid_configuration")
        url = endpoint((environ.get(URL_ENV) or "").strip() or DEFAULT_URL, options["market_probe"])
        report = sanitize(fetch(url, token, opener=opener))
    except DiagError as error:
        elapsed = int((time.monotonic() - started) * 1000)
        if fmt == "json":
            text = json.dumps({"diagnostic": "failed", "failure": error.code,
                               "http_status": error.http_status, "verdict": "unknown"}, sort_keys=True)
        else:
            text = render_failure(error, elapsed)
        print(_guard(text, [token]))
        return 1
    elapsed = int((time.monotonic() - started) * 1000)
    text = (json.dumps({"diagnostic": "ok" if report["verdict"] != "diagnostic_incomplete" else "incomplete",
                        "round_trip_ms": elapsed, "report": report}, indent=2, sort_keys=True)
            if options["format"] == "json" else render(report, elapsed))
    guarded = _guard(text, [token])
    print(guarded)
    if guarded != text:
        return 1
    return 0 if report["verdict"] != "diagnostic_incomplete" else 1


def main(argv=None) -> int:
    """`python scripts/desk_diag.py request.json` — the runner calls `run()` directly."""
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: desk_diag.py <request.json>", file=sys.stderr)
        return 2
    with open(argv[0]) as fh:
        return run(json.load(fh))


if __name__ == "__main__":
    raise SystemExit(main())
