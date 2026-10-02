"""Read-only, sanitized desk-service diagnostics (`GET /api/diagnostics`).

Built so a development session can diagnose the desk service through the public
`ops` channel (`{"type":"desks"}`) without anyone copying Railway console output.
Every value in the report is a code, a count, a boolean, a bounded number or a
timestamp — the projection is deliberately narrow because the ops result is
published on a public branch:

* no credentials, connection strings, claim tokens, worker IDs or exchange keys;
* no cash, balances, P&L, decisions, publications or research payloads;
* no raw exception text, raw API bodies or raw response headers.

Nothing here writes. It never claims, continues, captures, completes, pauses,
resumes, refreshes preflight/isolation checks, rebuilds a market index or places
an order, and it opens no authenticated exchange session. The only network call
is one bounded public market GET (`MarketProbe`), single attempt, cached for a
minute and suppressed during an observed rate-limit cooldown so that diagnosing
throttling cannot add to it.
"""
from __future__ import annotations

import math
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import func, inspect, select

from .contracts import utcnow
from .markets import PUBLIC_BASE

SCHEMA = "desk-diagnostics/1"
DESKS = ("chatgpt", "claude")
JOBS_PER_DESK = 8
MONITOR_FRESH_SECONDS = 180

_CODE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
# Long digit or hex runs never belong in a code; refusing them keeps an identifier,
# token or account number from riding out inside something that looks like one.
_SECRETISH = re.compile(r"[0-9a-f]{16,}|[0-9]{8,}")
_JOB_ID = re.compile(r"^(session|research)-[A-Za-z0-9_-]{1,100}-(chatgpt|claude)-[A-Za-z0-9]{1,40}$")
_ROUND = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
JOB_STATES = {"queued", "claimed", "running", "retry", "publishing", "completed", "failed"}
HEALTH_STATES = {"healthy", "starting", "recovering", "needs_operator"}


def safe_code(value) -> str | None:
    """An enumerated snake_case code, or a fixed placeholder; never free text."""
    if value is None:
        return None
    if isinstance(value, str) and _CODE.match(value) and not _SECRETISH.search(value):
        return value
    return "unrecognized"


def pause_category(reason) -> str | None:
    """Pause reasons may be operator free text; only system codes are reported."""
    if reason is None:
        return None
    if isinstance(reason, str) and reason.startswith("carried from "):
        return "carried_from_prior_round"
    code = safe_code(reason)
    return "operator_text_redacted" if code == "unrecognized" else code


def safe_blocker(value) -> str:
    if isinstance(value, str) and value.startswith("trading_paused:"):
        return "trading_paused:" + (pause_category(value.split(":", 1)[1]) or "unknown")
    return safe_code(value) or "unrecognized"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def safe_time(value) -> str | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return _utc(value).isoformat()


def age_seconds(value, now: datetime) -> int | None:
    stamp = safe_time(value)
    if stamp is None:
        return None
    return int((now - datetime.fromisoformat(stamp)).total_seconds())


def sanitize_retry_after(header, now: datetime | None = None) -> dict:
    """Retry-After as a bounded integer of seconds; the raw header is never echoed."""
    if header is None:
        return {"present": False, "valid": None, "seconds": None}
    seconds = None
    text = str(header).strip()[:64]
    try:
        number = float(text)
        if math.isfinite(number):
            seconds = number
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - (now or utcnow())).total_seconds()
        except (TypeError, ValueError, OverflowError, IndexError):
            seconds = None
    if seconds is None or seconds < -60 or seconds > 86_400:
        return {"present": True, "valid": False, "seconds": None}
    return {"present": True, "valid": True, "seconds": max(0, math.ceil(seconds))}


class MarketProbe:
    """One bounded, unauthenticated public market GET from the service's own egress.

    Single attempt (never retried), short timeouts, no redirects, no proxies from the
    environment, response body never read. The result is reused for `min_interval`
    seconds, and after an HTTP 429 no request is sent until the server's Retry-After
    (at least `cooldown_floor`, at most an hour) has passed. Concurrent diagnostics
    share one in-flight probe.
    """

    URL = PUBLIC_BASE + "/markets?limit=1&status=open"

    def __init__(self, *, transport=None, clock=time.monotonic, timeout=5.0,
                 min_interval=60.0, cooldown_floor=120.0, max_cooldown=3600.0):
        self.transport, self.clock, self.timeout = transport, clock, timeout
        self.min_interval, self.cooldown_floor, self.max_cooldown = min_interval, cooldown_floor, max_cooldown
        self._lock = threading.Lock()
        self._last = None          # (monotonic time, result)
        self._cooldown_until = None

    def __call__(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        with self._lock:
            tick = self.clock()
            if self._cooldown_until is not None and tick < self._cooldown_until:
                return {"outcome": "skipped_rate_limit_cooldown", "request_sent": False,
                        "cooldown_remaining_seconds": math.ceil(self._cooldown_until - tick),
                        "previous": self._last[1] if self._last else None}
            if self._last is not None and tick - self._last[0] < self.min_interval:
                return {**self._last[1], "request_sent": False, "cached": True,
                        "cached_age_seconds": int(tick - self._last[0])}
            result = self._probe(now)
            self._last = (self.clock(), result)
            if result["outcome"] == "rate_limited":
                wait = result["retry_after"]["seconds"] or 0
                wait = min(self.max_cooldown, max(self.cooldown_floor, wait))
                self._cooldown_until = self.clock() + wait
            return {**result, "request_sent": True, "cached": False}

    def _probe(self, now: datetime) -> dict:
        started = time.monotonic()
        result = {"target": "public_market_list", "http_status": None, "latency_ms": None,
                  "retry_after": sanitize_retry_after(None)}
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=min(3.0, self.timeout)),
                              follow_redirects=False, trust_env=False, transport=self.transport,
                              headers={"User-Agent": "KalshiDeskDiagnostics/1",
                                       "Accept": "application/json"}) as client:
                with client.stream("GET", self.URL) as response:
                    result["latency_ms"] = int((time.monotonic() - started) * 1000)
                    result["http_status"] = int(response.status_code)
                    result["retry_after"] = sanitize_retry_after(response.headers.get("retry-after"), now)
        except httpx.TimeoutException:
            return {**result, "outcome": "timeout",
                    "latency_ms": int((time.monotonic() - started) * 1000)}
        except httpx.HTTPError:
            return {**result, "outcome": "connection_failure",
                    "latency_ms": int((time.monotonic() - started) * 1000)}
        status = result["http_status"]
        outcome = "ok" if status == 200 else "rate_limited" if status == 429 else "http_error"
        return {**result, "outcome": outcome}


def _section(report, name, build):
    try:
        report[name] = build()
    except Exception:
        # Exception text can carry URLs, SQL or credentials; only the section name leaves.
        report[name] = {"error": "section_unavailable"}
        report["diagnostic"]["failed_sections"].append(name)


def _service_section(service, now):
    settings = service.settings
    snapshot = service.store.snapshot(now)
    last_tick = service.last_tick
    tick_age = int((now - _utc(last_tick)).total_seconds()) if last_tick else None
    return {
        "research_mode": safe_code(settings.research_mode),
        "alert_mode": safe_code(settings.alert_mode),
        "account_mode": safe_code(settings.account_mode),
        "live_enabled": bool(settings.live_enabled),
        "round_id": snapshot.get("round_id") if _ROUND.match(str(snapshot.get("round_id"))) else "unrecognized",
        "round_started": bool(snapshot.get("started_at")),
        "started_at": safe_time(snapshot.get("started_at")),
        "worker": {
            "last_tick": safe_time(last_tick),
            "last_tick_age_seconds": tick_age,
            "monitor_fresh": tick_age is not None and -5 <= tick_age <= MONITOR_FRESH_SECONDS,
            "error": safe_code(service.last_error),
        },
    }


def _desks_section(service, now):
    snapshot = service.store.snapshot(now)
    health = service.supervisor.status(now).get("health", {})
    result = {}
    for desk in DESKS:
        row = next((r for r in snapshot.get("desks", []) if r.get("desk_id") == desk), {})
        item = health.get(desk, {})
        status = item.get("status")
        result[desk] = {
            "health": status if status in HEALTH_STATES else "unrecognized",
            "activity": safe_code(item.get("activity")),
            "reasons": [safe_blocker(r) for r in (item.get("reasons") or [])[:20]],
            "ready": row.get("ready") is True,
            "paused": row.get("paused") is True,
            "pause_category": pause_category(row.get("pause_reason")),
            "book_status": safe_code(row.get("status")),
            "completed_cycles": int(item.get("completed_cycles") or 0),
            "pending_jobs": int(item.get("pending_jobs") or 0),
            "recent_failures": int(item.get("recent_failures") or 0),
            "last_completed_at": safe_time(item.get("last_completed_at")),
            "unreviewed_settlements": int(item.get("unreviewed_settlements") or 0),
        }
    return result


def _readiness_section(service, now):
    # refresh=False: the cached view only. A refresh would call the exchange.
    readiness = service.check_launch(now)
    blockers = [safe_blocker(b) for b in readiness.get("blockers", [])[:50]]
    return {"ready": readiness.get("ready") is True, "blockers": blockers,
            "note": "cached launch checks; nothing was refreshed"}


def _jobs_section(service, now):
    from .research_models import ResearchJob

    round_id = service.supervisor.round_id
    rows = []
    with service.store._tx() as session:
        for desk in DESKS:
            query = (select(ResearchJob.job_id, ResearchJob.desk_id, ResearchJob.state, ResearchJob.error,
                            ResearchJob.created_at, ResearchJob.updated_at, ResearchJob.lease_until)
                     .where(ResearchJob.round_id == round_id, ResearchJob.desk_id == desk)
                     .order_by(ResearchJob.created_at.desc(), ResearchJob.job_id.desc())
                     .limit(JOBS_PER_DESK))
            for job_id, desk_id, state, error, created, updated, lease in session.execute(query):
                lease_active = lease is not None and _utc(lease) > now and state in {"claimed", "running", "publishing"}
                rows.append({
                    "job_id": job_id if _JOB_ID.match(str(job_id)) else "unrecognized",
                    "desk": desk_id,
                    "state": state if state in JOB_STATES else "unrecognized",
                    "error_category": safe_code(error),
                    "created_at": safe_time(created),
                    "updated_at": safe_time(updated),
                    "lease_active": lease_active,
                })
    return {"limit_per_desk": JOBS_PER_DESK, "recent": rows}


def _board_section(service, now):
    from .research_models import ResearchBoard, ResearchMarket

    round_id = service.supervisor.round_id
    result = {"progressive_board": None, "whole_board_index": None}
    if inspect(service.store.engine).has_table(ResearchBoard.__tablename__):
        with service.store._tx() as session:
            board = session.get(ResearchBoard, round_id)
            cached = session.scalar(select(func.count()).select_from(ResearchMarket)
                                    .where(ResearchMarket.round_id == round_id)) or 0
            if board is not None:
                coverage = (board.snapshot or {}).get("coverage", {}) if isinstance(board.snapshot, dict) else {}
                lease = board.lease_until
                lease_active = lease is not None and _utc(lease) > now
                result["progressive_board"] = {
                    "pages_seen": int(board.pages_seen or 0),
                    "completed_passes": int(board.completed_passes or 0),
                    "cached_markets": int(cached),
                    "sample_markets": len((board.snapshot or {}).get("markets") or []),
                    "continuation_pending": bool(board.cursor),
                    "snapshot_as_of": safe_time(coverage.get("as_of")),
                    "snapshot_age_seconds": age_seconds(coverage.get("as_of"), now),
                    "scan_lease": {"active": lease_active,
                                   "seconds_remaining": int((_utc(lease) - now).total_seconds()) if lease_active else 0},
                }
    else:
        result["progressive_board"] = {"state": "not_initialized"}
    browser = service.browser
    if browser is not None:
        index = getattr(browser, "_index", None)  # read the reference; never trigger a build
        lock = getattr(browser, "_lock", None)
        scanning = bool(lock.locked()) if lock is not None else False
        if index is None:
            result["whole_board_index"] = {"state": "not_built", "scan_in_progress": scanning}
        else:
            coverage = index.get("coverage", {})
            result["whole_board_index"] = {
                "state": "cached", "scan_in_progress": scanning,
                "as_of": safe_time(index.get("as_of")),
                "age_seconds": age_seconds(index.get("as_of"), now),
                "ttl_seconds": int(coverage.get("ttl_seconds") or 0),
                "events_scanned": int(coverage.get("events_scanned") or 0),
                "pages": int(coverage.get("pages") or 0),
                "markets": int(coverage.get("markets") or 0),
                "complete": coverage.get("complete") is True,
            }
    else:
        result["whole_board_index"] = {"state": "unavailable"}
    return result


def _attention(report) -> list[str]:
    flags = []
    service = report.get("service", {})
    worker = service.get("worker", {}) if isinstance(service, dict) else {}
    if worker and not worker.get("monitor_fresh"):
        flags.append("monitor_stale")
    if worker.get("error"):
        flags.append("worker_error:" + worker["error"])
    desks = report.get("desks", {})
    for desk in DESKS:
        item = desks.get(desk) if isinstance(desks, dict) else None
        if not isinstance(item, dict) or "health" not in item:
            continue
        if item["paused"]:
            flags.append(f"{desk}_paused")
        if item["health"] in ("recovering", "needs_operator", "unrecognized"):
            flags.append(f"{desk}_research_{item['health']}")
    jobs = report.get("jobs", {})
    for desk in DESKS:
        latest = next((j for j in jobs.get("recent", []) if j["desk"] == desk), None) if isinstance(jobs, dict) else None
        if latest and latest["state"] == "failed":
            flags.append(f"{desk}_latest_job_failed:{latest['error_category'] or 'unknown'}")
    probe = report.get("market_probe") or {}
    outcome = probe.get("outcome")
    if outcome == "rate_limited" or outcome == "skipped_rate_limit_cooldown":
        flags.append("public_market_rate_limited")
    elif outcome in ("timeout", "connection_failure", "http_error"):
        flags.append("public_market_probe_" + outcome)
    return flags


def build_report(service, *, probe: bool = True, now: datetime | None = None) -> dict:
    """The sanitized diagnostic report. Read-only; a failing section never fails the rest."""
    now = now or utcnow()
    report = {"schema": SCHEMA, "generated_at": now.isoformat(),
              "diagnostic": {"read_only": True, "failed_sections": []}}
    _section(report, "service", lambda: _service_section(service, now))
    _section(report, "desks", lambda: _desks_section(service, now))
    _section(report, "readiness", lambda: _readiness_section(service, now))
    _section(report, "jobs", lambda: _jobs_section(service, now))
    _section(report, "board", lambda: _board_section(service, now))
    prober = getattr(service, "market_probe", None)
    if not probe:
        report["market_probe"] = {"outcome": "skipped_by_request", "request_sent": False}
    elif prober is None:
        report["market_probe"] = {"outcome": "unavailable", "request_sent": False}
    else:
        _section(report, "market_probe", lambda: prober(now))
    failed = report["diagnostic"]["failed_sections"]
    report["attention"] = _attention(report)
    report["verdict"] = "diagnostic_incomplete" if failed else ("attention" if report["attention"] else "healthy")
    return report
