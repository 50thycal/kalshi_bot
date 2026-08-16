"""Experiment Control Tower — the canonical read-only view of the research portfolio.

One question, answered from structured state only:

    What experiments are running, where are they in the state machine, what does
    the current evidence say, and what requires attention?

## Why this module exists rather than a SQL report

Every number here comes from the canonical Experiment OS code path — `read`
(lineage + scoreboard), `evaluator` (gate verdicts, DRY-RUN), `enforcement`
(mode, lineage coverage, readiness) and `platform_impact` (revisions,
dispositions). Nothing is re-derived. A second implementation of "what does this
gate say" would be a second source of truth, which is exactly what the
Experiment OS cutover removed; the generations of status checkers this replaces
each reconstructed lifecycle state from strategy tags and hand-maintained
Markdown, and drifted from each other as a result.

The one thing the Control Tower adds beyond Experiment OS is **infrastructure
health that Experiment OS does not model**: data-collector freshness, which the
retired loop checkers watched and which no gate can express (a silent collector
starves an experiment's evidence without ever failing a gate).

## Read-only, structurally

The evaluator runs with `persist=False`, so even computing a verdict writes
nothing. This module imports no service write helper. It is safe to point at
`DATABASE_URL_RO`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from ..models import LiveOrder, PaperTrade
from . import read
from .lifecycle import LifecycleState
from .models import Experiment

# Lifecycle order for grouping. RETIRED is deliberately absent: it is summarized
# only on request (spec §6), never mixed into the operating picture.
ACTIVE_ORDER: tuple[str, ...] = (
    LifecycleState.IDEA.value,
    LifecycleState.PROBE.value,
    LifecycleState.PAPER.value,
    LifecycleState.LIVE_CANARY.value,
    LifecycleState.PRODUCTION.value,
    LifecycleState.PAUSED.value,
)

# Collector → (timestamp column, nominal cadence minutes). Freshness is the one
# health signal the retired loop checkers owned that Experiment OS cannot express:
# a stalled collector starves evidence without failing any gate. Stale is >3x
# cadence, matching the threshold those checkers used.
COLLECTORS: tuple[tuple[str, str, int], ...] = (
    ("weather_forecasts", "captured_at", 15),
    ("weather_observations", "captured_at", 15),
    ("weather_ensembles", "captured_at", 60),
    ("weather_bucket_snapshots", "captured_at", 5),
    ("crypto_spot_candles", "minute_ts", 5),
    ("crypto_ladder_snapshots", "captured_at", 5),
    ("mmsell_position_ticks", "captured_at", 5),
    ("market_snapshots", "captured_at", 60),
)

NORTH_STAR_USD_PER_MONTH = 100.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@dataclass
class TowerReport:
    generated_at: datetime
    enforcement: dict = field(default_factory=dict)
    platform: dict = field(default_factory=dict)
    anomalies: list[str] = field(default_factory=list)
    by_state: dict[str, list[dict]] = field(default_factory=dict)
    data_health: list[dict] = field(default_factory=list)
    portfolio: dict = field(default_factory=dict)
    ready_due: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    retired_recent: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# System / integrity — always computed FIRST (spec §6: before P&L interpretation)
# ---------------------------------------------------------------------------


def _system_section(session) -> tuple[dict, dict, list[str]]:
    from . import enforcement as enf
    from . import platform_impact as pi

    anomalies: list[str] = []
    report = enf.enforcement_report(session)

    mode = report.get("mode")
    if mode == "OFF":
        anomalies.append(
            "ENFORCEMENT OFF — new experimental activity is NOT required to carry "
            "Experiment OS lineage; treat every count below as possibly incomplete"
        )
    unstamped = report.get("post_cutover_unstamped") or {}
    for table, rows in unstamped.items():
        for row in rows:
            anomalies.append(
                f"POST-CUTOVER ROW WITHOUT LINEAGE: {table} tag {row['tag']!r} "
                f"x{row['rows']} — something is trading outside Experiment OS"
            )
    if report.get("resolver_degraded_alarms_24h"):
        anomalies.append(
            f"RESOLVER DEGRADED {report['resolver_degraded_alarms_24h']}x in 24h — "
            "lineage decisions came from a stale snapshot"
        )
    if report.get("unresolved_integrity_events"):
        anomalies.append(
            f"{report['unresolved_integrity_events']} unresolved integrity event(s) "
            "— affected experiments cannot be evaluated"
        )
    for canary in report.get("live_canaries") or []:
        if not canary.get("twin"):
            anomalies.append(
                f"LIVE deployment {canary['deployment']} has NO paper twin — the "
                "live/paper comparison that detects adverse selection is missing"
            )

    platform: dict = {}
    try:
        impacts = pi.unresolved_impacts(session)
        pending = pi.pending_revisions(session)
        platform = {"unresolved_impacts": impacts, "pending_revisions": pending}
        for imp in impacts:
            anomalies.append(
                f"UNRESOLVED PLATFORM IMPACT: {imp['component']}:{imp['revision']} → "
                f"{imp['experiment']} [{imp['impact_class']}/{imp['action']}] "
                f"status={imp['status']} — its evidence is blocked until applied"
            )
        for rev in pending:
            if not rev["activation_safe"]:
                anomalies.append(
                    f"PENDING REVISION {rev['component']}:{rev['version']} is NOT safe "
                    f"to activate — unaccounted: {rev['unaccounted']}"
                )
    except Exception as exc:  # noqa: BLE001 — a read must degrade, not abort
        platform = {"error": str(exc)}
        anomalies.append(f"platform-impact read failed: {exc}")

    return report, platform, anomalies


# ---------------------------------------------------------------------------
# Per-experiment view
# ---------------------------------------------------------------------------


def _live_exposure(session, tags: list[str]) -> dict:
    """Real money currently committed by these tags: resting + filled orders."""
    if not tags:
        return {"open_orders": 0, "contracts": 0, "notional_usd": 0.0}
    rows = session.execute(
        select(LiveOrder.status, func.count(), func.sum(LiveOrder.quantity),
               func.sum(LiveOrder.quantity * LiveOrder.limit_price))
        .where(LiveOrder.strategy.in_(tags))
        .group_by(LiveOrder.status)
    ).all()
    resting = {"pending", "resting", "open", "partially_filled"}
    open_orders = contracts = 0
    notional = 0.0
    for status, n, qty, cents in rows:
        if (status or "").lower() in resting:
            open_orders += int(n or 0)
            contracts += int(qty or 0)
            notional += float(cents or 0) / 100.0
    return {"open_orders": open_orders, "contracts": contracts,
            "notional_usd": round(notional, 2)}


def _gate_view(session, board_gate: dict, gate_obj, evaluate: bool) -> dict:
    """Latest RECORDED verdict plus, optionally, a fresh DRY-RUN evaluation.

    The dry run is what makes the Tower useful on a system where gates have never
    been evaluated: it reports what the canonical evaluator says right now without
    recording anything. It is explicitly labelled so nobody mistakes it for a
    recorded, promotion-authorizing result — only a persisted evaluator PASS can
    authorize a transition (PR 3/PR 4 binding rules)."""
    out = dict(board_gate)
    out["live_verdict"] = None
    out["live_explanation"] = None
    if not evaluate or gate_obj is None:
        return out
    if gate_obj.evidence_started_at is None:
        out["live_verdict"] = "NOT_STARTED"
        out["live_explanation"] = "gate registered but evidence has not started"
        return out
    try:
        from .evaluator import evaluate_gate

        outcome = evaluate_gate(session, gate_obj, persist=False)  # DRY RUN
        out["live_verdict"] = outcome.verdict
        out["live_explanation"] = outcome.explanation
    except Exception as exc:  # noqa: BLE001
        out["live_verdict"] = "EVAL_ERROR"
        out["live_explanation"] = str(exc)
    return out


def _experiment_view(session, exp: Experiment, *, evaluate: bool) -> dict:
    board = read.experiment_scoreboard(session, exp)
    ver = read.latest_version(session, exp)
    view: dict = {
        "key": exp.key,
        "title": exp.title,
        "state": exp.state,
        "origin": exp.origin,
        "family": exp.family,
        "legacy_class": exp.legacy_class,
        "grandfathered_import": exp.legacy_class is not None,
        "integrity": exp.migration_integrity,
        "version": board.get("version"),
        "epoch": board.get("epoch"),
        "platform_snapshot": board.get("platform_snapshot"),
        "window": board.get("window"),
        "note": board.get("note"),
        "arms": board.get("arms", []),
        "gates": [],
        "deployments": [],
        "integrity_events": [],
        "last_transition": None,
        "evidence_fresh_at": None,
        "exposure": None,
    }

    gate_objs = {g.gate_key: g for g in (read.gates_for(session, ver) if ver else [])}
    for g in board.get("gates", []):
        view["gates"].append(
            _gate_view(session, g, gate_objs.get(g.get("gate_key")), evaluate)
        )

    epoch = read.open_epoch_for(session, ver) if ver else None
    if epoch is not None:
        live_tags: list[str] = []
        for dep in read.deployments_for(session, epoch):
            twin = read.twin_for(session, dep) if dep.kind == "live" else None
            tags = [tag for _arm, tag in read.deployment_arms(session, dep) if tag]
            view["deployments"].append({
                "key": dep.deployment_key,
                "kind": dep.kind,
                "stage": dep.stage,
                "grandfathered": bool(dep.grandfathered),
                "tags": tags,
                "started_at": str(dep.started_at) if dep.started_at else None,
                "twin": twin.deployment_key if twin is not None else None,
                "twin_boundary_matches": bool(
                    twin is not None
                    and twin.started_at == dep.started_at
                    and twin.epoch_id == dep.epoch_id
                ),
            })
            if dep.kind == "live":
                live_tags.extend(tags)
        if live_tags:
            view["exposure"] = _live_exposure(session, live_tags)

    for ev in read.integrity_events_for(session, exp):
        if ev.resolved_at is None:
            view["integrity_events"].append({"kind": ev.kind,
                                             "description": ev.description})

    transitions = read.transitions_for(session, exp)
    if transitions:
        last = transitions[-1]
        view["last_transition"] = {
            "to": last.to_state, "at": str(last.occurred_at),
            "actor": last.actor, "reason": (last.reason or "")[:160],
        }

    all_tags = [t for d in view["deployments"] for t in d["tags"]]
    if all_tags:
        newest = session.scalar(
            select(func.max(PaperTrade.created_at)).where(PaperTrade.strategy.in_(all_tags))
        )
        view["evidence_fresh_at"] = str(newest) if newest else None
    return view


# ---------------------------------------------------------------------------
# Infrastructure + portfolio (what Experiment OS does not model)
# ---------------------------------------------------------------------------


def _data_health(session) -> list[dict]:
    out: list[dict] = []
    now = _now()
    for table, column, cadence in COLLECTORS:
        try:
            row = session.execute(
                text(f"SELECT max({column}) AS latest, count(*) AS n FROM {table}")  # noqa: S608
            ).first()
        except Exception:  # noqa: BLE001 — table absent on this deployment
            session.rollback()
            out.append({"collector": table, "status": "UNAVAILABLE",
                        "detail": "table not present"})
            continue
        latest = _utc(row[0]) if row and row[0] else None
        if latest is None:
            out.append({"collector": table, "status": "EMPTY", "age_min": None,
                        "rows": int(row[1] or 0) if row else 0})
            continue
        age_min = (now - latest).total_seconds() / 60.0
        # A stall is hours; months means the collector is simply not part of the
        # current deployment (e.g. market_snapshots only runs in scanner mode).
        # Calling that STALE every run trains the reader to ignore the column.
        if age_min > 7 * 24 * 60:
            status = "INACTIVE"
        elif age_min > 3 * cadence:
            status = "STALE"
        else:
            status = "fresh"
        out.append({"collector": table, "status": status,
                    "age_min": round(age_min, 1), "cadence_min": cadence,
                    "latest": str(latest)})
    return out


def _portfolio(session) -> dict:
    """Realized dollars — the repository's north star, which no single gate owns."""
    from .metrics import SETTLED_STATUSES

    since = _now() - timedelta(days=30)
    total = session.scalar(
        select(func.coalesce(func.sum(PaperTrade.pnl), 0.0)).where(
            PaperTrade.status.in_(SETTLED_STATUSES)
        )
    ) or 0.0
    last30 = session.scalar(
        select(func.coalesce(func.sum(PaperTrade.pnl), 0.0)).where(
            PaperTrade.status.in_(SETTLED_STATUSES), PaperTrade.created_at >= since
        )
    ) or 0.0
    return {
        "paper_realized_all_time_usd": round(float(total), 2),
        "paper_realized_30d_usd": round(float(last30), 2),
        "north_star_usd_per_month": NORTH_STAR_USD_PER_MONTH,
        "gap_to_north_star_usd": round(NORTH_STAR_USD_PER_MONTH - float(last30), 2),
        "note": "paper realized; live realized is reported per live canary",
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_report(session, *, evaluate: bool = True,
                 retired_days: int = 7) -> TowerReport:
    """The whole Control Tower view. Pure read; `evaluate` runs the canonical
    evaluator in DRY-RUN (persist=False) for a current verdict."""
    rep = TowerReport(generated_at=_now())
    rep.enforcement, rep.platform, rep.anomalies = _system_section(session)

    for state in ACTIVE_ORDER:
        rep.by_state[state] = []
    for exp in read.list_experiments(session):
        if exp.state == LifecycleState.RETIRED.value:
            continue
        rep.by_state.setdefault(exp.state, []).append(
            _experiment_view(session, exp, evaluate=evaluate)
        )

    cutoff = _now() - timedelta(days=retired_days)
    for exp in read.list_experiments(session, state=LifecycleState.RETIRED.value):
        transitions = read.transitions_for(session, exp)
        if transitions and _utc(transitions[-1].occurred_at) >= cutoff:
            rep.retired_recent.append({"key": exp.key,
                                       "at": str(transitions[-1].occurred_at),
                                       "reason": (transitions[-1].reason or "")[:120]})

    rep.data_health = _data_health(session)
    rep.portfolio = _portfolio(session)
    _derive_actions(rep)
    return rep


def _derive_actions(rep: TowerReport) -> None:
    """READY/DUE + recommendations. Every recommendation names the role that owns
    the write — the Control Tower is read-only and hands off explicitly."""
    for state, views in rep.by_state.items():
        for v in views:
            for g in v["gates"]:
                verdict = g.get("live_verdict") or (
                    (g.get("latest_result") or {}).get("verdict")
                )
                if verdict == "PASS" and g.get("kind") == "promotion":
                    rep.ready_due.append(
                        f"GATE PASS (dry-run) {v['key']} · {g['gate_key']} "
                        f"{g.get('from_state')}→{g.get('to_state')} — a RECORDED "
                        "evaluator PASS is still required to authorize; hand to "
                        "Research Lab (paper) or the operator (live promotion)"
                    )
                elif verdict == "FAIL" and g.get("kind") == "promotion":
                    rep.ready_due.append(
                        f"GATE FAIL {v['key']} · {g['gate_key']} — kill/retire "
                        "candidate; decision belongs to Research Lab"
                    )
                elif verdict and verdict.startswith("BLOCKED"):
                    rep.ready_due.append(
                        f"{verdict} {v['key']} · {g['gate_key']} — resolve the block "
                        "before any interpretation of this experiment's numbers"
                    )
            for g in v["gates"]:
                recorded = (g.get("latest_result") or {}).get("verdict")
                live = g.get("live_verdict")
                if recorded is None and live and not live.startswith("NOT_"):
                    rep.ready_due.append(
                        f"NEVER RECORDED {v['key']} · {g['gate_key']} — dry-run says "
                        f"{live}; no official result exists, so this gate cannot "
                        "authorize anything until an evaluation is persisted"
                    )
                elif recorded and live and recorded != live:
                    rep.ready_due.append(
                        f"DIVERGENCE {v['key']} · {g['gate_key']} — recorded "
                        f"{recorded}, dry-run now {live}; an official re-evaluation "
                        "is due"
                    )
            if v["integrity_events"]:
                rep.recommendations.append(
                    f"{v['key']}: unresolved integrity event(s) — Platform Change "
                    "Review (if a shared semantic changed) or Live Ops (if runtime)"
                )
            if state == LifecycleState.LIVE_CANARY.value:
                for d in v["deployments"]:
                    if d["kind"] == "live" and not d["twin"]:
                        rep.recommendations.append(
                            f"{v['key']}: live deployment {d['key']} has no twin — "
                            "Live Ops"
                        )

    stale = [c for c in rep.data_health if c["status"] == "STALE"]
    if stale:
        rep.recommendations.append(
            "data collectors not fresh: "
            + ", ".join(f"{c['collector']}({c['status']})" for c in stale)
            + " — Live Ops owns collector health; starved evidence fails no gate"
        )
    if rep.platform.get("unresolved_impacts"):
        rep.recommendations.append(
            "unresolved platform-impact dispositions — Platform Change Review"
        )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt(v, dash: str = "-") -> str:
    if v is None:
        return dash
    if isinstance(v, float):
        # Counts arrive as floats from the metric layer; show them as counts.
        return str(int(v)) if v.is_integer() else f"{v:.2f}"
    return str(v)


def _table(headers: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["    (none)"]
    cells = [[_fmt(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    out = ["    " + "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=False))]
    out.append("    " + "  ".join("-" * w for w in widths))
    out += ["    " + "  ".join(c.ljust(w) for c, w in zip(r, widths, strict=False))
            for r in cells]
    return out


def _central(dt: datetime) -> str:
    """Report in America/Chicago — the convention every retired checker used, and
    the one the operator reads in."""
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo("America/Chicago")).strftime(
            "%Y-%m-%d %H:%M %Z")
    except Exception:  # noqa: BLE001
        return dt.strftime("%Y-%m-%d %H:%M UTC")


def render(rep: TowerReport, *, session=None) -> str:
    enf = rep.enforcement
    snap = rep.platform.get("active_snapshot") or "-"
    lines: list[str] = [
        "SESSION: Experiment Control Tower",
        "MODE: READ ONLY",
        f"EXPERIMENT OS: {enf.get('system_version') or 'unrecorded'}",
        f"ENFORCEMENT: {enf.get('mode')}"
        + (f" since {enf.get('effective_at')}" if enf.get("effective_at") else "")
        + (f" (cutover {enf.get('cutover_id')})" if enf.get("cutover_id") else ""),
        f"PLATFORM SNAPSHOT: {snap}",
        f"AS OF: {_central(rep.generated_at)}",
        "",
        "GATE COLUMN: recorded/dry-run (* = they differ). Only a RECORDED "
        "evaluator result can authorize a transition.",
        "",
        "=== SYSTEM / INTEGRITY ===",
    ]
    if rep.anomalies:
        lines += [f"    !! {a}" for a in rep.anomalies]
    else:
        lines.append("    all clear — no enforcement, lineage, integrity or "
                     "platform anomalies")
    deps = enf.get("deployments") or {}
    lines.append(
        f"    active deployments: {deps.get('total', 0)} "
        f"(grandfathered {deps.get('grandfathered', 0)}, native {deps.get('native', 0)})"
    )

    for state in ACTIVE_ORDER:
        views = rep.by_state.get(state) or []
        lines += ["", f"=== {state} ({len(views)}) ==="]
        if not views:
            lines.append("    (none)")
            continue
        if state == LifecycleState.LIVE_CANARY.value:
            rows = []
            for v in views:
                live = next((d for d in v["deployments"] if d["kind"] == "live"), None)
                exp_ = v["exposure"] or {}
                rows.append([
                    v["key"], live["key"] if live else "-",
                    (live or {}).get("twin") or "NO TWIN",
                    "exact" if (live or {}).get("twin_boundary_matches") else "differs",
                    exp_.get("open_orders", 0),
                    f"${exp_.get('notional_usd', 0):.2f}",
                    _verdict_of(v),
                ])
            lines += _table(["experiment", "live", "twin", "boundary", "open",
                             "at risk", "gate"], rows)
            continue
        rows = []
        for v in views:
            treatment, control = _primary_arms(v["arms"])
            rows.append([
                v["key"],
                f"v{v['version']}/e{v['epoch']}" if v.get("version") else "-",
                (treatment or {}).get("arm", "-"),
                (treatment or {}).get("settled_trades"),
                (treatment or {}).get("pnl_cents_per_trade"),
                (control or {}).get("pnl_cents_per_trade"),
                _verdict_of(v),
                v.get("evidence_fresh_at") or "-",
            ])
        lines += _table(["experiment", "ver/epoch", "treatment", "n", "c/trade",
                         "control c/trade", "gate", "last evidence"], rows)

    lines += ["", "=== DATA COLLECTORS ==="]
    lines += _table(["collector", "status", "age_min", "cadence"],
                    [[c["collector"], c["status"], c.get("age_min"),
                      c.get("cadence_min")] for c in rep.data_health])

    p = rep.portfolio
    gap = p["gap_to_north_star_usd"]
    standing = (f"AHEAD by ${abs(gap):.2f}" if gap < 0
                else f"short by ${gap:.2f}")
    lines += ["", "=== PORTFOLIO (north star: $100/month realized) ===",
              f"    PAPER realized 30d: ${p['paper_realized_30d_usd']}  "
              f"(all-time ${p['paper_realized_all_time_usd']})  → {standing}",
              "    Paper assumes fills it would not always get; the north star is "
              "REAL money. Live realized is per-canary — see `mmsell_live` / "
              "`live_paper_parity`."]

    lines += ["", "=== READY / DUE ==="]
    lines += [f"    - {r}" for r in rep.ready_due] or ["    (none)"]
    if rep.retired_recent:
        lines += ["", "=== RECENTLY RETIRED ==="]
        lines += [f"    - {r['key']} @ {r['at']}: {r['reason']}"
                  for r in rep.retired_recent]
    lines += ["", "=== RECOMMENDED NEXT ACTIONS ==="]
    lines += [f"    - {r}" for r in rep.recommendations] or [
        "    - none; continue accumulating evidence"]
    lines += ["", "Control Tower is READ ONLY. Writes belong to: Research Lab "
              "(experiments), Platform Change Review (shared semantics), Live Ops "
              "(runtime/real money)."]
    return "\n".join(lines)


def _primary_arms(arms: list[dict]) -> tuple[dict | None, dict | None]:
    """The row's headline arm pair.

    Prefer the declared treatment/control roles. Many imported books declare
    arms named for the book itself (mmsell9 vs mmsell10) with no role literally
    called "treatment", so fall back to the best-evidenced arm rather than
    rendering a dash and hiding the experiment's actual numbers."""
    treatment = next((a for a in arms if a.get("role") == "treatment"), None)
    control = next((a for a in arms if a.get("role") == "control"), None)
    if treatment is None:
        scored = [a for a in arms if a is not control]
        treatment = max(
            scored, key=lambda a: (a.get("settled_trades") or 0), default=None
        )
    return treatment, control


def _gate_standing(g: dict) -> str:
    """One gate's standing as `recorded/dry-run`.

    The two are kept visibly distinct: only a RECORDED evaluator result can
    authorize anything, while the dry run says what the evidence implies right
    now. When they differ the dry run is shown with `*` — that is the signal that
    an official re-evaluation is due, not that a promotion is available."""
    recorded = (g.get("latest_result") or {}).get("verdict")
    live = g.get("live_verdict")
    if recorded and live and recorded != live:
        return f"{recorded}/{live}*"
    if recorded:
        return recorded
    if live:
        return f"none/{live}"
    return "not evaluated"


def _verdict_of(view: dict) -> str:
    """The promotion gate's current standing, or the most blocking verdict."""
    best = None
    for g in view.get("gates", []):
        standing = _gate_standing(g)
        if standing == "not evaluated":
            continue
        v = g.get("live_verdict") or (g.get("latest_result") or {}).get("verdict")
        if v and v.startswith("BLOCKED"):
            return standing
        if g.get("kind") == "promotion":
            best = standing
        elif best is None:
            best = standing
    if best:
        return best
    return "no gate" if not view.get("gates") else "not evaluated"


def active_snapshot_label(session) -> str:
    from .service import get_active_platform_snapshot

    snap = get_active_platform_snapshot(session)
    if snap is None:
        return "(none resolvable)"
    return f"{snap.fingerprint[:16]}…"


def report_text(session, *, evaluate: bool = True) -> str:
    rep = build_report(session, evaluate=evaluate)
    try:
        rep.platform["active_snapshot"] = active_snapshot_label(session)
    except Exception:  # noqa: BLE001
        pass
    return render(rep, session=session)


__all__ = ["TowerReport", "build_report", "render", "report_text", "ACTIVE_ORDER"]
