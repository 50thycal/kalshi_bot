"""The monitor snapshot: one JSON document for everything that is running.

    xos monitor-snapshot            (read-only; allowlisted on the ops channel)

Two halves, kept apart on purpose:

* `build_snapshot(session)` READS. It asks the Control Tower for its report — the
  same `build_report` that `xos control-tower` renders, so lifecycle, gate
  verdicts, blocked gates, silent arms, collector freshness, open issues, ticket
  candidates and the paper north-star number are the Tower's, never re-derived.
  It then reads only what the Tower does not model: per-arm last evidence and
  entries per day, live realized P&L through the canonical
  `live_realized_pnl_usd` provider, the declared `expect` contract, and the
  external sources (`sources.SOURCES`).

* `assemble(tower, extras, external, now)` is PURE. It turns those inputs into
  the snapshot. Tests drive it directly, and a bootstrap can feed it a saved
  `xos control-tower --json` result.

The snapshot is a VIEW. It is never written anywhere durable by this code, it
authorizes nothing, and its health labels change no lifecycle state. Its output is
printed into the public ops result, so it carries only what the Tower already
prints there plus counts and timestamps.
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

SCHEMA = "monitor-snapshot/1"

# Health vocabulary, worst first. The order is the page's sort order and the
# rule for an experiment with several findings: the worst one names it.
ERROR, SILENT, STALE, BLOCKED, OK = "ERROR", "SILENT", "STALE", "BLOCKED", "OK"
PAUSED, IDLE, UNKNOWN = "PAUSED", "IDLE", "UNKNOWN"
HEALTH_ORDER: tuple[str, ...] = (ERROR, SILENT, STALE, BLOCKED, UNKNOWN, OK, PAUSED, IDLE)

#: States expected to produce evidence (the Tower's own set).
EVIDENCE_STATES = frozenset({"PAPER", "LIVE_CANARY", "PRODUCTION"})

#: A gate whose floors are this close (estimated days) is "verdict due soon".
VERDICT_SOON_DAYS = 7.0

#: Ready/due lines that need the OPERATOR, not a role: the hard-stop tier.
_OWNER_MARKERS = ("OPERATOR DECISION", "GATE PASS (RECORDED", "HORIZON EXHAUSTED")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _worst(healths) -> str:
    present = set(healths)
    return next((h for h in HEALTH_ORDER if h in present), OK)


# ---------------------------------------------------------------------------
# READ half
# ---------------------------------------------------------------------------


def build_snapshot(session, *, evaluate: bool = True, environ=None,
                   now: datetime | None = None) -> dict:
    """Read everything once and assemble the snapshot. Pure read."""
    from ..experiment_os.control_tower import build_report
    from . import sources

    rep = build_report(session, evaluate=evaluate)
    now = now or rep.generated_at
    tower = json.loads(json.dumps(dataclasses.asdict(rep), default=str))
    extras = _read_extras(session, rep, now)
    # Probes are timed at the moment they run, not at the report's start: the
    # Tower takes a while, and rows written meanwhile would read as negative ages.
    external = sources.run_probes(session, _now(), environ=environ)
    return assemble(tower, extras, external, now)


def _read_extras(session, rep, now: datetime) -> dict:
    from ..experiment_os import read
    from ..experiment_os.lifecycle import LifecycleState
    from .expectations import package_expectations, resolve_expect

    try:
        packages = package_expectations()
    except Exception:  # noqa: BLE001 — a package import failing must not blank the monitor
        packages = {}
    out: dict = {"experiments": {}, "closed_total": None}
    for views in rep.by_state.values():
        for v in views:
            exp = read.get_experiment(session, v["key"])
            ver = read.latest_version(session, exp) if exp is not None else None
            expect, source = resolve_expect(
                v["key"], getattr(ver, "monitoring_json", None), packages)
            arms = [_arm_extras(session, row, now) for row in v.get("arm_activity") or []]
            live = _live_realized(session, exp, ver, now) if exp is not None else {}
            out["experiments"][v["key"]] = {
                "expect": expect, "expect_source": source, "arms": arms, **live,
            }
    try:
        out["closed_total"] = len(
            read.list_experiments(session, state=LifecycleState.RETIRED.value))
    except Exception:  # noqa: BLE001
        session.rollback()
    return out


def _arm_extras(session, row: dict, now: datetime) -> dict:
    """Last evidence and entries for one (arm x kind), read on the SAME substrate
    the Tower's activity metric uses for that kind: paper_trades for paper and
    paper_twin, live entry orders (buys PLACED) for live."""
    from ..models import LiveOrder, PaperTrade

    tags = list(row.get("tags") or [])
    out = {"arm": row["arm"], "kind": row["kind"], "tags": tags,
           "last_evidence_at": None, "entries_24h": None, "entries_7d": None}
    if not tags:
        return out
    if row["kind"] == "live":
        model, filt = LiveOrder, (LiveOrder.strategy.in_(tags), LiveOrder.action == "buy")
    else:
        model, filt = PaperTrade, (PaperTrade.strategy.in_(tags),)
    try:
        latest = session.scalar(select(func.max(model.created_at)).where(*filt))
        n24 = session.scalar(select(func.count()).select_from(model).where(
            *filt, model.created_at >= now - timedelta(hours=24)))
        n7 = session.scalar(select(func.count()).select_from(model).where(
            *filt, model.created_at >= now - timedelta(days=7)))
    except Exception:  # noqa: BLE001 — a read must degrade, not abort
        session.rollback()
        return out
    out.update(last_evidence_at=str(_utc(latest)) if latest else None,
               entries_24h=int(n24 or 0), entries_7d=int(n7 or 0))
    return out


def _live_realized(session, exp, ver, now: datetime) -> dict:
    """Live realized dollars through the canonical provider, per live arm, over the
    current epoch and over the last 30 days (positions ENTERED in the window and
    since settled — the provider's own semantics, the same window rule the Tower's
    paper number uses)."""
    from ..experiment_os import read
    from ..experiment_os.metrics import MetricScope, compute_metric
    from ..experiment_os.models import PlatformSnapshot

    if ver is None:
        return {}
    epoch = read.open_epoch_for(session, ver)
    if epoch is None:
        return {}
    snap = session.get(PlatformSnapshot, epoch.platform_snapshot_id)
    start = _utc(epoch.started_at)
    windows = {"epoch": start, "30d": max(start, now - timedelta(days=30))}
    totals = {"live_realized_epoch_usd": None, "live_realized_30d_usd": None}
    missing: list[str] = []
    for arm in read.arms_for(session, ver):
        tags = read.arm_strategy_tags(session, epoch, arm.arm_key, "live")
        if not tags:
            continue
        for label, win_start in windows.items():
            mv = compute_metric(session, "live_realized_pnl_usd", MetricScope(
                experiment_key=exp.key, version=ver.version,
                epoch_number=epoch.epoch_number, arm_key=arm.arm_key,
                deployment_kind="live", strategy_tags=tags, deployment_keys=(),
                window_start=win_start, window_end=now,
                platform_snapshot_fingerprint=snap.fingerprint if snap else "",
            ))
            key = f"live_realized_{label}_usd"
            if mv.missing:
                missing.append(f"{arm.arm_key}:{label}")
                continue
            totals[key] = round((totals[key] or 0.0) + float(mv.value or 0.0), 2)
    if missing:
        totals["live_realized_missing"] = missing
    return totals


# ---------------------------------------------------------------------------
# PURE half
# ---------------------------------------------------------------------------


def assemble(tower: dict, extras: dict | None, external: list[dict] | None,
             now: datetime | str) -> dict:
    """The snapshot, from a Control Tower report (as JSON) plus the monitor's own
    reads. Pure: no session, no clock, no environment."""
    now = _utc(now)
    extras = extras or {}
    ex_by_key = extras.get("experiments") or {}
    blocked_by_exp: dict[str, list[dict]] = {}
    for b in tower.get("blocked") or []:
        blocked_by_exp.setdefault(b["experiment"], []).append(b)
    silent_by_exp: dict[str, list[dict]] = {}
    for s in tower.get("silent_arms") or []:
        silent_by_exp.setdefault(s["experiment"], []).append(s)
    issues = tower.get("open_issues") or []
    candidates = tower.get("issue_candidates") or []

    experiments = []
    for state, views in (tower.get("by_state") or {}).items():
        for v in views:
            experiments.append(_experiment_row(
                v, state, ex_by_key.get(v["key"]) or {}, blocked_by_exp.get(v["key"], []),
                silent_by_exp.get(v["key"], []), issues, candidates, now))
    experiments.sort(key=lambda e: (HEALTH_ORDER.index(e["health"]), e["key"]))

    external = list(external or []) + _collector_rows(tower, now)

    portfolio = tower.get("portfolio") or {}
    live_30d = [e["live_realized_30d_usd"] for e in experiments
                if e.get("live_realized_30d_usd") is not None]
    live_30d_total = round(sum(live_30d), 2) if live_30d else (0.0 if extras else None)
    north = portfolio.get("north_star_usd_per_month") or 100.0
    state_of = {e["key"]: e["stage"] for e in experiments}
    needs_you = [line for line in tower.get("ready_due") or []
                 if any(m in line for m in _OWNER_MARKERS)
                 and not _already_there(line, state_of)]
    needs_you += [f"{i['issue_key']}: {i.get('title')}" for i in issues
                  if i.get("status") == "ACTION_REQUIRED"]
    needs_you += [f"untriaged candidate needs operator triage: {c['title']}"
                  for c in candidates if c.get("requires_operator_triage")]

    counts = {h: 0 for h in HEALTH_ORDER}
    for e in experiments:
        counts[e["health"]] += 1
    by_state = tower.get("by_state") or {}
    summary = {
        "active": sum(len(vs) for s, vs in by_state.items() if s != "PAUSED"),
        "paused": len(by_state.get("PAUSED") or []),
        # Experiments with at least one BLOCKED gate — the Tower's `blocked` list,
        # counted per experiment, whatever else is also wrong with them.
        "blocked": len(blocked_by_exp),
        "blocked_gates": len(tower.get("blocked") or []),
        "closed_recent": len(tower.get("retired_recent") or []),
        "closed_recent_days": tower.get("retired_days"),
        "closed_total": extras.get("closed_total"),
        "live": sum(1 for e in experiments if e["kind"] == "live"),
        "paper": sum(1 for e in experiments if e["kind"] == "paper"),
        "health": {h: n for h, n in counts.items() if n},
        "realized": {
            "paper_30d_usd": portfolio.get("paper_realized_30d_usd"),
            "live_30d_usd": live_30d_total,
            "north_star_usd_per_month": north,
            # The Tower's gap, verbatim: it is computed on the PAPER basis.
            "tower_gap_paper_basis_usd": portfolio.get("gap_to_north_star_usd"),
            "live_pct_of_goal": (round(100.0 * live_30d_total / north, 1)
                                 if live_30d_total is not None else None),
            "note": "paper = Control Tower portfolio (every paper_trades row settled, "
                    "entered in 30d); live = canonical live_realized_pnl_usd over each "
                    "ACTIVE experiment's live arms (entered in 30d, settled). Real money "
                    "is the live number.",
        },
        "needs_you": len(needs_you),
        "open_issues": len(issues),
        "untracked_problems": len(candidates),
        "external_unhealthy": sum(1 for x in external
                                  if x.get("health") not in (OK, "INACTIVE")),
    }
    snapshot = {
        "schema": SCHEMA,
        "generated_at": str(now),
        "tower_generated_at": tower.get("generated_at"),
        "enforcement": {k: (tower.get("enforcement") or {}).get(k)
                        for k in ("mode", "cutover_id", "effective_at")},
        "summary": summary,
        "needs_you": needs_you,
        "experiments": experiments,
        "external": external,
        "issues": [_issue_row(i) for i in issues],
        "untracked": [_candidate_row(c) for c in candidates],
        "anomalies": list(tower.get("anomalies") or []),
        "retired_recent": list(tower.get("retired_recent") or []),
    }
    snapshot["details"] = _details(snapshot)
    return snapshot


def _already_there(line: str, state_of: dict[str, str]) -> bool:
    """A recorded PASS for a transition the experiment has ALREADY made is history,
    not a decision: the Tower lists it because the result still exists."""
    for key, state in state_of.items():
        if f" {key} · " in line and f"→{state}" in line:
            return True
    return False


def _experiment_row(v, state, ex, blocked, silent, issues, candidates, now) -> dict:
    deployments = v.get("deployments") or []
    kinds = {d["kind"] for d in deployments}
    kind = ("live" if "live" in kinds else "paper" if kinds & {"paper", "paper_twin"}
            else "probe" if "probe" in kinds else "none")
    tags = sorted({t for d in deployments for t in d.get("tags") or []})
    expect = ex.get("expect") or {}
    reasons: list[str] = []
    healths: list[str] = []

    from ..experiment_os.enforcement import RECORDED_STATE_INTEGRITY_KINDS

    stood_down = [ev for ev in v.get("integrity_events") or []
                  if ev.get("kind") in RECORDED_STATE_INTEGRITY_KINDS]
    for ev in v.get("integrity_events") or []:
        if ev.get("kind") not in RECORDED_STATE_INTEGRITY_KINDS:
            healths.append(ERROR)
            reasons.append(f"unresolved integrity event {ev.get('kind')}")
    for g in v.get("gates") or []:
        if g.get("live_verdict") == "EVAL_ERROR":
            healths.append(ERROR)
            reasons.append(f"gate {g.get('gate_key')} failed to evaluate")
    for row in v.get("arm_activity") or []:
        if row.get("collected") is None and row.get("missing_reason"):
            healths.append(ERROR)
            reasons.append(f"arm {row['arm']}/{row['kind']}: activity metric unanswerable")
    for s in silent:
        healths.append(SILENT)
        reasons.append(f"arm {s['arm']}/{s['kind']} armed {s['hours_silent']}h with zero "
                       "evidence" + (f" (covered by {s['covered_by']})"
                                     if s.get("covered_by") else ""))

    arms = ex.get("arms") or []
    last_evidence = max((a["last_evidence_at"] for a in arms if a.get("last_evidence_at")),
                        default=None, key=lambda t: _utc(t))
    if state in EVIDENCE_STATES and not stood_down:
        silent_keys = {(s["arm"], s["kind"]) for s in silent}
        max_h = expect.get("max_silence_hours")
        rate = expect.get("min_entries_per_day")
        for a in arms:
            if (a["arm"], a["kind"]) in silent_keys or not a.get("last_evidence_at"):
                continue  # never traded: the Tower's silent-arm rule owns that case
            age = (now - _utc(a["last_evidence_at"])).total_seconds() / 3600.0
            if max_h is not None and age > max_h:
                healths.append(STALE)
                reasons.append(f"arm {a['arm']}/{a['kind']} last evidence {age:.0f}h ago "
                               f"(expect ≤ {max_h}h)")
            elif rate is not None and a.get("entries_7d") is not None \
                    and a["entries_7d"] / 7.0 < rate:
                healths.append(STALE)
                reasons.append(f"arm {a['arm']}/{a['kind']} averaging "
                               f"{a['entries_7d'] / 7.0:.1f}/day (expect ≥ {rate})")
    if blocked:
        healths.append(BLOCKED)
        reasons.append("blocked gate(s): " + ", ".join(
            f"{b['gate_key']} {b['verdict']}" for b in blocked))

    if state == "PAUSED":
        health = PAUSED
    elif state in ("IDEA", "PROBE"):
        health = IDLE if not healths else _worst(healths)
    else:
        health = _worst(healths) if healths else OK
    if stood_down and health == OK:
        reasons.append("stood down (recorded state): " + ", ".join(
            ev.get("kind") for ev in stood_down))

    paper_realized = None
    for a in v.get("arms") or []:
        if a.get("settled_trades") is not None and a.get("pnl_cents_per_trade") is not None:
            paper_realized = (paper_realized or 0.0) + (
                float(a["settled_trades"]) * float(a["pnl_cents_per_trade"]) / 100.0)

    exp_id = v.get("experiment_id")
    return {
        "key": v["key"],
        "title": v.get("title"),
        "stage": state,
        "kind": kind,
        "family": v.get("family"),
        "version": v.get("version"),
        "epoch": v.get("epoch"),
        "tags": tags,
        "health": health,
        "health_reasons": reasons,
        "owner_role": "LIVE_OPS" if kind == "live" else "RESEARCH_LAB",
        "last_evidence_at": last_evidence,
        "entries_24h": _sum(a.get("entries_24h") for a in arms),
        "entries_7d": _sum(a.get("entries_7d") for a in arms),
        "arms": [{**a, **_arm_paper(v, a)} for a in arms],
        "settled": _sum(a.get("settled_trades") for a in v.get("arms") or []),
        "paper_realized_epoch_usd": (round(paper_realized, 2)
                                     if paper_realized is not None else None),
        "live_realized_epoch_usd": ex.get("live_realized_epoch_usd"),
        "live_realized_30d_usd": ex.get("live_realized_30d_usd"),
        "exposure": v.get("exposure"),
        "gates": [_gate_row(g, now) for g in v.get("gates") or []],
        "next_verdict": _next_verdict(v.get("gates") or [], now),
        "open_issues": sorted({i["issue_key"] for i in issues
                               if exp_id is not None and i.get("experiment_id") == exp_id}),
        "untracked": sum(1 for c in candidates if c.get("experiment") == v["key"]),
        "expect": expect,
        "expect_source": ex.get("expect_source"),
        "last_transition": v.get("last_transition"),
    }


def _arm_paper(v, a) -> dict:
    for row in v.get("arms") or []:
        if row.get("arm") == a["arm"] and a["kind"] == "paper":
            return {"settled": row.get("settled_trades"),
                    "cents_per_trade": row.get("pnl_cents_per_trade")}
    return {}


def _sum(values):
    vals = [float(x) for x in values if x is not None]
    if not vals:
        return None
    total = sum(vals)
    return int(total) if total.is_integer() else round(total, 2)


def _gate_row(g: dict, now) -> dict:
    rec = g.get("latest_result") or {}
    return {
        "gate_key": g.get("gate_key"), "kind": g.get("kind"),
        "recorded": rec.get("verdict"), "recorded_at": rec.get("computed_at"),
        "dry_run": g.get("live_verdict"),
        "floor": _floor_progress(g, now),
    }


def _floor_progress(g: dict, now) -> dict | None:
    """How far the gate's sample floors are, from the dry run's own clauses.

    The ESTIMATE is linear in elapsed evidence time — a reading aid, never a
    verdict and never a date anyone promised. HOLD on thin sample stays correct
    whatever this says."""
    clauses = [c for c in g.get("live_clauses") or [] if c.get("list") == "sample"]
    fractions = []
    for c in clauses:
        target = (c.get("clause") or {}).get("value")
        value = c.get("value")
        if isinstance(target, (int, float)) and target > 0 and isinstance(value, (int, float)):
            fractions.append(min(1.0, value / target))
    if not fractions:
        return None
    progress = min(fractions)
    started = _utc(g.get("evidence_started_at"))
    eta = None
    if started is not None and 0 < progress < 1:
        elapsed = (now - started).total_seconds() / 86400.0
        eta = round(elapsed * (1.0 / progress - 1.0), 1)
    return {"progress": round(progress, 3), "eta_days": 0.0 if progress >= 1 else eta}


def _next_verdict(gates: list[dict], now) -> dict | None:
    best = None
    for g in gates:
        fp = _floor_progress(g, now)
        if not fp or fp["eta_days"] is None:
            continue
        if best is None or fp["eta_days"] < best["eta_days"]:
            best = {"gate_key": g.get("gate_key"), **fp,
                    "eta_date": str((now + timedelta(days=fp["eta_days"])).date())}
    return best


def _collector_rows(tower: dict, now) -> list[dict]:
    """The Tower's collector freshness, restated as external sources (verbatim
    status; INACTIVE stays informational, exactly as the Tower treats it)."""
    out = []
    for c in tower.get("data_health") or []:
        status = c.get("status")
        health = (OK if status == "fresh" else "INACTIVE" if status == "INACTIVE"
                  else STALE if status == "STALE" else ERROR)
        out.append({"key": f"collector:{c['collector']}", "title": c["collector"],
                    "kind": "collector", "owner_role": "LIVE_OPS", "health": health,
                    "last_seen": c.get("latest"), "age_hours": (
                        round(c["age_min"] / 60.0, 2) if c.get("age_min") is not None
                        else None),
                    "detail": f"Control Tower: {status}"
                              + (f" — {c['note']}" if c.get("note") else "")})
    return out


def _issue_row(i: dict) -> dict:
    return {k: i.get(k) for k in ("issue_key", "title", "status", "severity", "priority",
                                  "owner_role", "scope", "opened_at", "age_days",
                                  "currently_recurring")}


def _candidate_row(c: dict) -> dict:
    return {k: c.get(k) for k in ("fingerprint", "detector", "title", "scope", "experiment",
                                  "recommended_owner", "severity", "priority",
                                  "requires_operator_triage")}


def _details(s: dict) -> list[dict]:
    """The short 'worth knowing' list: each item one line, worst first."""
    out: list[dict] = []
    for e in s["experiments"]:
        if e["health"] in (ERROR, SILENT, STALE):
            out.append({"level": "act", "text": f"{e['key']} is {e['health']}: "
                        + "; ".join(r for r in e["health_reasons"]
                                    if not r.startswith("blocked gate"))})
    for x in s["external"]:
        if x.get("health") in (ERROR, STALE):
            out.append({"level": "act", "text": f"{x['title']} is {x['health']}: "
                        f"{x.get('detail')}"})
    for line in s["needs_you"]:
        out.append({"level": "you", "text": line[:300]})
    for e in s["experiments"]:
        nv = e.get("next_verdict")
        if nv and nv["eta_days"] is not None and nv["eta_days"] <= VERDICT_SOON_DAYS:
            out.append({"level": "soon", "text": f"{e['key']} · {nv['gate_key']}: sample "
                        f"floor {int(nv['progress'] * 100)}% — estimated ~{nv['eta_date']}"})
    if s["untracked"]:
        owners: dict[str, int] = {}
        for c in s["untracked"]:
            owners[c["recommended_owner"]] = owners.get(c["recommended_owner"], 0) + 1
        out.append({"level": "info", "text": f"{len(s['untracked'])} detected problem(s) "
                    "with no open issue → " + ", ".join(f"{o} {n}" for o, n in
                                                       sorted(owners.items()))})
    for r in s["retired_recent"]:
        out.append({"level": "info", "text": f"retired {r['key']} ({r['at'][:10]})"})
    for x in s["external"]:
        if x.get("health") == UNKNOWN:
            out.append({"level": "info", "text": f"{x['title']}: not assessed — "
                        f"{x.get('detail')}"})
    return out


def main_json(session, *, evaluate: bool = True) -> str:
    return json.dumps(build_snapshot(session, evaluate=evaluate, environ=os.environ),
                      indent=2, default=str)
