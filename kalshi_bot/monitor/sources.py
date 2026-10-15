"""Systems that run OUTSIDE Experiment OS, registered so the monitor watches them.

Experiment OS experiments are included automatically. Everything else that has to
be alive for the north star to move registers here: one entry, a read-only probe,
and its own `expect`. Adding a source is adding one `Source(...)` to `SOURCES`.

Every probe is a READ and must degrade, never raise: a probe that cannot answer
returns UNKNOWN with the reason, because "could not tell" and "healthy" must never
look alike. Probes return only public-safe facts (codes, counts, timestamps) —
the snapshot is printed into the public ops result.

The autonomous desks are watched read-only through the desk service's sanitized
diagnostic (`scripts/desk_diag.py`, the same projection the `desks` ops request
prints). They stay outside Experiment OS (DEC-018); nothing here brings them in.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text

#: Health vocabulary shared with experiments.
OK, STALE, ERROR, UNKNOWN = "OK", "STALE", "ERROR", "UNKNOWN"


@dataclass(frozen=True)
class Source:
    key: str
    title: str
    kind: str                       # worker | collector | desk | service
    owner_role: str
    expect: dict = field(default_factory=dict)
    probe: Callable[..., dict] | None = None


def _utc(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _age_hours(now: datetime, then) -> float | None:
    then = _utc(then)
    # Clamped at zero: the database's clock and this process's can disagree by
    # seconds, and "-0.1h ago" reads as a bug rather than as "just now".
    return None if then is None else max(0.0, round((now - then).total_seconds() / 3600.0, 2))


def _freshness(now: datetime, latest, max_hours: float, *, what: str) -> dict:
    age = _age_hours(now, latest)
    if age is None:
        return {"health": ERROR, "last_seen": None, "detail": f"no {what} at all"}
    health = OK if age <= max_hours else STALE
    return {"health": health, "last_seen": str(_utc(latest)), "age_hours": age,
            "detail": f"newest {what} {age}h ago (allowed {max_hours}h)"}


def _scalar(session, sql: str):
    try:
        return session.execute(text(sql)).scalar()
    except Exception as exc:  # noqa: BLE001 — absent table on this deployment
        session.rollback()
        raise _ProbeUnavailable(type(exc).__name__) from exc


class _ProbeUnavailable(Exception):
    pass


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def probe_worker(session, now, environ, expect) -> dict:
    """`bot_runs` is the worker's per-cycle row — the same signal and threshold as
    the 15-minute heartbeat workflow (scripts/worker_heartbeat.py)."""
    latest = _scalar(session, "SELECT max(started_at) FROM bot_runs "
                              "WHERE started_at > now() - interval '7 days'")
    return _freshness(now, latest, expect["max_silence_hours"], what="worker cycle")


def probe_incentive_programs(session, now, environ, expect) -> dict:
    """The incentive-program poll stamps `last_seen_at` on every row it re-observes;
    a silent poll leaves the live incentive book reading stale terms."""
    latest = _scalar(session, "SELECT max(last_seen_at) FROM incentive_programs")
    return _freshness(now, latest, expect["max_silence_hours"], what="program observation")


def probe_desks(session, now, environ, expect) -> dict:
    """One GET of the desk service's sanitized diagnostic, re-sanitized by the same
    schema the `desks` ops request uses. Without the diagnostic token (any run
    outside the ops runner) the answer is UNKNOWN, never a guess."""
    token = (environ.get("DESKS_DIAGNOSTIC_TOKEN") or "").strip()
    if not token:
        return {"health": UNKNOWN, "detail": "DESKS_DIAGNOSTIC_TOKEN is not set for this run "
                "(the ops runner receives it only once the operator adds the GitHub secret; "
                "docs/desks/DIAGNOSTICS.md)"}
    try:
        desk_diag = _import_desk_diag()
        url = desk_diag.endpoint(
            (environ.get(desk_diag.URL_ENV) or "").strip() or desk_diag.DEFAULT_URL, False)
        report = desk_diag.sanitize(desk_diag.fetch(url, token))
    except Exception as exc:  # noqa: BLE001 — never print exception text: it may echo a URL
        return {"health": UNKNOWN, "detail": f"desk diagnostic failed "
                f"({getattr(exc, 'code', type(exc).__name__)})"}
    desks = {}
    worst = OK
    for name, desk in (report.get("desks") or {}).items():
        state = desk.get("health")
        desks[name] = {
            "health": state, "ready": desk.get("ready"), "paused": desk.get("paused"),
            "last_completed_at": desk.get("last_completed_at"),
            "recent_failures": desk.get("recent_failures"),
            "unreviewed_settlements": desk.get("unreviewed_settlements"),
        }
        if state in ("needs_operator", "unrecognized"):
            worst = ERROR
        elif state != "healthy" and worst == OK:
            worst = STALE
    worker = ((report.get("service") or {}).get("worker") or {})
    verdict = report.get("verdict")
    if verdict == "diagnostic_incomplete":
        worst = UNKNOWN
    elif verdict == "attention" and worst == OK:
        worst = STALE
    return {"health": worst, "last_seen": worker.get("last_tick"),
            "detail": f"desk service verdict {verdict}; round "
                      f"{(report.get('service') or {}).get('round_id')}",
            "desks": desks, "attention": list(report.get("attention") or [])[:10]}


def probe_catalog(session, now, environ, expect) -> dict:
    """Liveness of the standalone market-catalog service (its own volume, not
    Postgres, so only its public `/health` is reachable)."""
    base = (environ.get("MONITOR_CATALOG_URL") or "").strip()
    if not base:
        return {"health": UNKNOWN, "detail": "MONITOR_CATALOG_URL not set; the catalog "
                "lives on its own volume and has no Postgres footprint to read"}
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(base.rstrip("/") + "/health", timeout=10) as resp:  # noqa: S310
            body = json.loads(resp.read(4096))
    except Exception as exc:  # noqa: BLE001
        return {"health": ERROR, "detail": f"/health unreachable ({type(exc).__name__})"}
    ok = body.get("status") == "ok"
    return {"health": OK if ok else ERROR, "last_seen": str(now) if ok else None,
            "detail": "liveness only: /health answered " + str(body.get("status"))}


def _import_desk_diag():
    scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import desk_diag  # noqa: PLC0415

    return desk_diag


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

SOURCES: tuple[Source, ...] = (
    Source("worker", "Trading worker (bot_runs heartbeat)", "worker", "LIVE_OPS",
           {"max_silence_hours": 0.5}, probe_worker),
    Source("incentive-programs", "Liquidity-incentive program poll", "collector",
           "LIVE_OPS", {"max_silence_hours": 6}, probe_incentive_programs),
    Source("desks", "Autonomous desks (ChatGPT + Claude, outside XOS — DEC-018)", "desk",
           "AUTONOMOUS_DESK", {}, probe_desks),
    Source("market-catalog", "Market catalog service", "service", "LIVE_OPS",
           {}, probe_catalog),
)


def run_probes(session, now: datetime, environ=None,
               sources: tuple[Source, ...] = SOURCES) -> list[dict]:
    environ = os.environ if environ is None else environ
    out = []
    for src in sources:
        row = {"key": src.key, "title": src.title, "kind": src.kind,
               "owner_role": src.owner_role, "expect": dict(src.expect)}
        try:
            row.update(src.probe(session, now, environ, src.expect))
        except _ProbeUnavailable as exc:
            row.update({"health": UNKNOWN, "detail": f"not readable here ({exc})"})
        except Exception as exc:  # noqa: BLE001 — one bad probe must not sink the snapshot
            session.rollback()
            row.update({"health": UNKNOWN, "detail": f"probe failed ({type(exc).__name__})"})
        out.append(row)
    return out
