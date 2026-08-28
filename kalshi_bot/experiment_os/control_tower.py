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

from ..models import PaperTrade
from . import issue_policy, metrics, read
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

# Collector → (timestamp column, nominal cadence minutes, why it may be idle).
# Freshness is the one health signal the retired loop checkers owned that
# Experiment OS cannot express: a stalled collector starves evidence without
# failing any gate. Stale is >3x cadence, matching the threshold those checkers
# used. The fourth field documents collectors that are legitimately not part of
# every deployment, so a long silence is not read as a fault.
COLLECTORS: tuple[tuple[str, str, int, str | None], ...] = (
    ("weather_forecasts", "captured_at", 15, None),
    ("weather_observations", "captured_at", 15, None),
    ("weather_ensembles", "captured_at", 60, None),
    ("weather_bucket_snapshots", "captured_at", 5, None),
    ("crypto_spot_candles", "minute_ts", 5, None),
    ("crypto_ladder_snapshots", "captured_at", 5, None),
    ("mmsell_position_ticks", "captured_at", 5, None),
    ("market_snapshots", "captured_at", 60,
     "scanner-mode table; the live worker does not write it"),
)

# Silence longer than this is not a stall — it means the collector is not part of
# the deployment that is running. Reported as INACTIVE, which is informational.
INACTIVE_AFTER_DAYS = 7

# The statuses that are actually collector-health problems. INACTIVE is NOT one:
# it says "not expected here", and treating it as an outage trains a reader to
# invent causes for it (a fresh session read market_snapshots INACTIVE as "dead
# for 69 days" and blamed it for six unrelated BLOCKED_DATA gates).
COLLECTOR_WARNING_STATUSES: frozenset[str] = frozenset({"STALE", "EMPTY", "UNAVAILABLE"})

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
    # Open integrity events that record a state rather than report a failure —
    # an intentional stand-down is the case that exists. Kept OUT of `anomalies`
    # on purpose: it is explained by construction, so it needs no diagnosis.
    recorded_state: list[str] = field(default_factory=list)
    by_state: dict[str, list[dict]] = field(default_factory=dict)
    data_health: list[dict] = field(default_factory=list)
    portfolio: dict = field(default_factory=dict)
    ready_due: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    retired_recent: list[dict] = field(default_factory=list)
    # The window `retired_recent` covers, carried so the render states it rather
    # than leaving the reader to call it "this cycle".
    retired_days: int = 7
    # Retired records with no `retired_at` at all — their date is unknown, not
    # recent. Named so the window's coverage is honest about what it cannot place.
    retired_undated: list[str] = field(default_factory=list)
    blocked: list[dict] = field(default_factory=list)
    # Gates whose decision turns on the live-fill projection. Carried so the render
    # can state what the verdict actually claims, beside what paper observed.
    realizable_context: list[dict] = field(default_factory=list)
    # Proven contract defects, now DURABLE ISSUES bound to the version they were
    # proven against (`read.contract_defect_findings`). A SECOND, independent
    # blocker class: research output, not evaluator output, and never lifecycle
    # truth. Kept in its own field precisely so it cannot be mistaken for a verdict.
    contract_findings: list[dict] = field(default_factory=list)
    # Open investigations (docs/EXPERIMENT_OS_ISSUES.md) — displayed, never
    # created here. An issue's status is workflow, not a verdict and not
    # lifecycle state, so nothing in this list can change what a gate says.
    open_issues: list[dict] = field(default_factory=list)
    # Deterministic ticket CANDIDATES: anomalies this report already understands
    # that no open issue currently covers. A candidate is a recommendation to
    # open an issue — rendering one writes nothing (spec §2.7).
    issue_candidates: list[dict] = field(default_factory=list)
    # The same detections that an open issue ALREADY covers, each carrying
    # `covered_by`. Kept rather than dropped so a caller adopting a candidate by
    # fingerprint can tell "already ticketed" apart from "never detected" — two
    # very different answers that would otherwise both look like "not found".
    # Not rendered: the covering issue is already listed above.
    covered_candidates: list[dict] = field(default_factory=list)


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
    """The `at risk` column: resting-order notional plus held-position exposure.

    Delegates to the canonical metrics provider (`live_open_exposure_usd` reads the
    same function), so the number an operator sees here and the number a keep/stop
    gate decides on cannot drift apart."""
    return metrics.live_open_exposure(session, tags)


def _gate_view(session, board_gate: dict, gate_obj, evaluate: bool) -> dict:
    """Latest RECORDED verdict plus, optionally, a fresh DRY-RUN evaluation.

    The dry run is what makes the Tower useful on a system where gates have never
    been evaluated: it reports what the canonical evaluator says right now without
    recording anything. It is explicitly labelled so nobody mistakes it for a
    recorded, promotion-authorizing result — only a persisted evaluator PASS can
    authorize a transition (PR 3/PR 4 binding rules)."""
    out = dict(board_gate)
    out["gate_id"] = getattr(gate_obj, "id", None)
    out["live_verdict"] = None
    out["live_explanation"] = None
    out["live_blocking_reasons"] = []
    out["live_missing_metrics"] = []
    out["live_clauses"] = []
    if not evaluate or gate_obj is None:
        return out
    if gate_obj.evidence_started_at is None:
        out["live_verdict"] = "NOT_STARTED"
        out["live_explanation"] = "gate registered but evidence has not started"
        return out
    try:
        from .evaluator import evaluate_gate, missing_metrics

        outcome = evaluate_gate(session, gate_obj, persist=False)  # DRY RUN
        out["live_verdict"] = outcome.verdict
        out["live_explanation"] = outcome.explanation
        # Carry the evaluator's OWN account of why, so no surface has to infer a
        # cause from what happens to be near it in the report.
        out["live_clauses"] = [c.as_json() for c in outcome.clauses]
        out["live_blocking_reasons"] = list(outcome.blocking_reasons or [])
        out["live_missing_metrics"] = missing_metrics(out["live_clauses"])
    except Exception as exc:  # noqa: BLE001
        out["live_verdict"] = "EVAL_ERROR"
        out["live_explanation"] = str(exc)
    return out


# Who owns unblocking each verdict. A block is not automatically a platform
# problem: BLOCKED_DATA is usually a metric provider nobody has written yet,
# which is experiment/metrics work, and routing it to Platform Change Review
# invites a Platform Revision that no evidence calls for.
BLOCK_OWNER: dict[str, str] = {
    "BLOCKED_DATA": (
        "Research Lab (or task-specific Experiment OS metrics work) — a metric "
        "provider or evidence transform has to be implemented. NOT a Platform "
        "Revision unless a shared semantic actually changed"
    ),
    "BLOCKED_PLATFORM": (
        "Platform Change Review — evidence comparability across a platform "
        "revision is what is blocking"
    ),
    "BLOCKED_INTEGRITY": (
        "route by cause: runtime/live-execution/collector malfunction → Live Ops; "
        "shared semantic or config change → Platform Change Review; experiment "
        "definition or evidence-contract problem → Research Lab"
    ),
}

# Integrity kinds with an unambiguous owner. Anything else keeps the explicit
# three-way decision above rather than guessing.
INTEGRITY_OWNER: dict[str, str] = {
    "EXPERIMENT_CONFIG_DRIFT": (
        "Platform Change Review (deployed config no longer matches the registered "
        "arm) — Live Ops if the runtime config is what is wrong"
    ),
    "HELD_CONSTANT_CHANGED": (
        "Platform Change Review — something the experiment held constant moved"
    ),
}


def _blocked_gate_entry(view: dict, gate: dict) -> dict | None:
    """One blocked gate, with the evaluator's cause and the owning role.

    Prefers the RECORDED result's reasons when the recorded verdict is the blocked
    one — that is the official record — and otherwise the dry run's."""
    recorded = gate.get("latest_result") or {}
    rec_verdict = recorded.get("verdict")
    live_verdict = gate.get("live_verdict")
    if rec_verdict and str(rec_verdict).startswith("BLOCKED"):
        verdict, source = rec_verdict, "recorded"
        reasons = list(recorded.get("blocking_reasons") or [])
        metrics = list(recorded.get("missing_metrics") or [])
    elif live_verdict and str(live_verdict).startswith("BLOCKED"):
        verdict, source = live_verdict, "dry-run"
        reasons = list(gate.get("live_blocking_reasons") or [])
        metrics = list(gate.get("live_missing_metrics") or [])
    else:
        return None
    return {
        "experiment": view["key"],
        "experiment_id": view.get("experiment_id"),
        "version_id": view.get("version_id"),
        "gate_id": gate.get("gate_id"),
        "gate_key": gate.get("gate_key"),
        "verdict": verdict,
        "source": source,
        "missing_metrics": metrics,
        "reasons": reasons,
        "owner": BLOCK_OWNER.get(verdict, "Research Lab"),
    }


def _experiment_view(session, exp: Experiment, *, evaluate: bool) -> dict:
    board = read.experiment_scoreboard(session, exp)
    ver = read.latest_version(session, exp)
    view: dict = {
        "key": exp.key,
        # Canonical ids, carried so a ticket candidate can be fingerprinted and
        # scoped to real foreign keys rather than to display strings.
        "experiment_id": exp.id,
        "version_id": ver.id if ver is not None else None,
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
    # Bound to the OPERATING version, so a corrected successor Version drops the
    # finding automatically instead of it having to be remembered and deleted.
    # Now read from durable issues rather than a hand-written registry — same
    # binding rule, same display-only status, but with real workflow state and a
    # history behind it.
    view["contract_findings"] = read.contract_defect_findings(
        session, exp, board.get("version")
    )

    gate_objs = {g.gate_key: g for g in (read.gates_for(session, ver) if ver else [])}
    for g in board.get("gates", []):
        view["gates"].append(
            _gate_view(session, g, gate_objs.get(g.get("gate_key")), evaluate)
        )

    epoch = read.open_epoch_for(session, ver) if ver else None
    view["epoch_id"] = epoch.id if epoch is not None else None
    if epoch is not None:
        live_tags: list[str] = []
        for dep in read.deployments_for(session, epoch):
            twin = read.twin_for(session, dep) if dep.kind == "live" else None
            tags = [tag for _arm, tag in read.deployment_arms(session, dep) if tag]
            view["deployments"].append({
                "key": dep.deployment_key,
                "deployment_id": dep.id,
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
                                             "integrity_event_id": ev.id,
                                             "description": ev.description})

    transitions = read.transitions_for(session, exp)
    if transitions:
        last = transitions[-1]
        view["last_transition"] = {
            "to": last.to_state, "at": str(last.occurred_at),
            "actor": last.actor, "reason": (last.reason or "")[:160],
        }

    # The Tower's own count of observed evidence, so "zero evidence" is derived
    # from the same numbers the report prints rather than from a second query.
    view["total_entries"] = sum(
        int(a.get("entries") or 0) for a in view["arms"]
    )
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
    """Collector freshness, with each status carrying whether it is a PROBLEM.

    `warning` is the field to act on, not the status string: only STALE, EMPTY and
    UNAVAILABLE mean something may be wrong. INACTIVE means the collector is not
    part of the running deployment, and is reported for completeness only."""
    out: list[dict] = []
    now = _now()
    for table, column, cadence, idle_note in COLLECTORS:
        try:
            row = session.execute(
                text(f"SELECT max({column}) AS latest, count(*) AS n FROM {table}")  # noqa: S608
            ).first()
        except Exception:  # noqa: BLE001 — table absent on this deployment
            session.rollback()
            out.append({"collector": table, "status": "UNAVAILABLE",
                        "warning": True, "note": "table not present"})
            continue
        latest = _utc(row[0]) if row and row[0] else None
        if latest is None:
            out.append({"collector": table, "status": "EMPTY", "age_min": None,
                        "warning": True, "note": idle_note,
                        "rows": int(row[1] or 0) if row else 0})
            continue
        age_min = (now - latest).total_seconds() / 60.0
        # A stall is hours; months means the collector is simply not part of the
        # current deployment (e.g. market_snapshots only runs in scanner mode).
        # Calling that STALE every run trains the reader to ignore the column.
        if age_min > INACTIVE_AFTER_DAYS * 24 * 60:
            status = "INACTIVE"
        elif age_min > 3 * cadence:
            status = "STALE"
        else:
            status = "fresh"
        out.append({"collector": table, "status": status,
                    "warning": status in COLLECTOR_WARNING_STATUSES,
                    "age_min": round(age_min, 1), "cadence_min": cadence,
                    "age_days": round(age_min / 1440.0, 1),
                    "note": idle_note if status == "INACTIVE" else None,
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
    rep = TowerReport(generated_at=_now(), retired_days=retired_days)
    rep.enforcement, rep.platform, rep.anomalies = _system_section(session)

    for state in ACTIVE_ORDER:
        rep.by_state[state] = []
    for exp in read.list_experiments(session):
        if exp.state == LifecycleState.RETIRED.value:
            continue
        rep.by_state.setdefault(exp.state, []).append(
            _experiment_view(session, exp, evaluate=evaluate)
        )

    # Select on when the book ACTUALLY died (`retired_at`), not on when its
    # RETIRED transition row was written. The legacy import stamped every
    # transition at one instant, so filtering on the transition puts the entire
    # graveyard inside any window — a stated "last 7 days" that is wrong is worse
    # than an unstated one. Undated legacy records (retired_at NULL) fall back to
    # the transition and are labelled, so the guess is never silent.
    cutoff = _now() - timedelta(days=retired_days)
    for exp in read.list_experiments(session, state=LifecycleState.RETIRED.value):
        transitions = read.transitions_for(session, exp)
        retired_at = _utc(exp.retired_at)
        when = retired_at or (_utc(transitions[-1].occurred_at) if transitions else None)
        if when is None or when < cutoff:
            continue
        rep.retired_recent.append({
            "key": exp.key,
            "at": str(when),
            "dated": retired_at is not None,
            "reason": (transitions[-1].reason if transitions else None) or "",
        })
    rep.retired_recent.sort(key=lambda r: r["at"], reverse=True)
    rep.retired_undated = [
        exp.key
        for exp in read.list_experiments(session, state=LifecycleState.RETIRED.value)
        if exp.retired_at is None
    ]

    rep.data_health = _data_health(session)
    rep.portfolio = _portfolio(session)
    _derive_actions(rep)
    # Last: ticket candidates are derived from the anomalies above, so they
    # can never introduce a finding the rest of the report does not already
    # make. Still a pure read — see `_issue_sections`.
    _issue_sections(session, rep)
    return rep


def _derive_actions(rep: TowerReport) -> None:
    """READY/DUE + recommendations. Every recommendation names the role that owns
    the write — the Control Tower is read-only and hands off explicitly."""
    from . import enforcement as enf
    for state, views in rep.by_state.items():
        for v in views:
            for g in v["gates"]:
                verdict = g.get("live_verdict") or (
                    (g.get("latest_result") or {}).get("verdict")
                )
                recorded_v = (g.get("latest_result") or {}).get("verdict")
                if verdict == "PASS" and g.get("kind") == "promotion":
                    # Say which KIND of PASS this is. Hard-coding "(dry-run)"
                    # told a reader that a real recorded PASS still needed one —
                    # the same misreading in reverse, on the one line where the
                    # distinction decides whether there is a decision to make.
                    if recorded_v == "PASS":
                        ctx = _realizable_context(v, g)
                        claim = (
                            " The claim is exactly: this registered gate's clauses "
                            "are satisfied — not that the book is broadly profitable, "
                            "that other diagnostics are positive, or that historical "
                            "cautions have lapsed."
                        )
                        if ctx and ctx["any_sign_disagreement"]:
                            claim += (
                                " CAUTION: it passes on the calibrated live-fill "
                                "projection while observed paper P&L is negative — "
                                "see REALIZABLE-PROJECTION CONTEXT."
                            )
                        rep.ready_due.append(
                            f"GATE PASS (RECORDED, {g['latest_result']['computed_by']}"
                            f" @ {g['latest_result']['computed_at']}) {v['key']} · "
                            f"{g['gate_key']} {g.get('from_state')}→"
                            f"{g.get('to_state')} — this result CAN authorize the "
                            "transition. Promotion remains an operator act "
                            "(re-evaluated at authorization); it is not automatic."
                            + claim
                        )
                    else:
                        rep.ready_due.append(
                            f"GATE PASS (dry-run only) {v['key']} · {g['gate_key']} "
                            f"{g.get('from_state')}→{g.get('to_state')} — no "
                            "RECORDED evaluator PASS exists, so nothing is "
                            "authorized yet; hand to Research Lab (paper) or the "
                            "operator (live promotion)"
                        )
                elif verdict == "HORIZON_EXHAUSTED":
                    # NOT a HOLD. A HOLD means "wait for more evidence"; past the
                    # horizon more evidence will never come, and reading this as
                    # "keep waiting" is how a decision gets deferred forever.
                    rec = g.get("latest_result") or {}
                    rep.ready_due.append(
                        f"HORIZON EXHAUSTED "
                        f"({'recorded' if recorded_v == 'HORIZON_EXHAUSTED' else 'dry-run'})"
                        f" {v['key']} · {g['gate_key']} — the contract's "
                        "pre-registered evidence horizon is spent. This is NOT a "
                        "hold: no further authorization look accrues, and nothing "
                        "will change by waiting. An OPERATOR DECISION is required "
                        "— promote, kill, or register a successor Version. "
                        + (rec.get("explanation") or "")[:400]
                    )
                elif verdict == "FAIL" and g.get("kind") == "promotion":
                    rep.ready_due.append(
                        f"GATE FAIL ({'recorded' if recorded_v == 'FAIL' else 'dry-run'})"
                        f" {v['key']} · {g['gate_key']} — kill/retire candidate; "
                        "decision belongs to Research Lab"
                    )
                elif verdict and verdict.startswith("BLOCKED"):
                    entry = _blocked_gate_entry(v, g)
                    cause = _block_cause_line(entry) if entry else ""
                    extra = [f for f in (v.get("contract_findings") or [])
                             if f.get("independent_of_evaluator")]
                    rep.ready_due.append(
                        f"{verdict} {v['key']} · {g['gate_key']}"
                        + (f" — {cause}" if cause else "")
                        + " — this experiment's numbers cannot be interpreted "
                        "until it clears"
                        + (" — AND a contract defect is proven against "
                           f"v{extra[0]['version']}: clearing the block above "
                           "does NOT make this Version evaluable" if extra else "")
                    )
            for g in v["gates"]:
                recorded = (g.get("latest_result") or {}).get("verdict")
                live = g.get("live_verdict")
                if (recorded or live or "").startswith("BLOCKED"):
                    # BLOCKED EVIDENCE owns this gate. Repeating it here as
                    # "never recorded" adds a second line about a gate that
                    # cannot authorize anything either way.
                    continue
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
            for f in v.get("contract_findings") or []:
                rep.contract_findings.append(f)
                rep.recommendations.append(
                    f"{v['key']}: CONTRACT DEFECT proven against v{f['version']} "
                    f"— {f['headline']}; owner: {f['owner']}"
                )
            for g in v["gates"]:
                entry = _blocked_gate_entry(v, g)
                if entry is not None:
                    # An evaluator block and a proven contract defect are
                    # INDEPENDENT. Carrying the second onto the first stops the
                    # evaluator's cause from reading as the whole diagnosis.
                    entry["also_contract_defect"] = [
                        f for f in (v.get("contract_findings") or [])
                        if f.get("independent_of_evaluator")
                    ]
                    rep.blocked.append(entry)
                ctx = _realizable_context(v, g)
                if ctx is not None:
                    rep.realizable_context.append(ctx)
            for ev in v["integrity_events"]:
                kind = ev.get("kind") or ""
                if kind in enf.NON_BLOCKING_INTEGRITY_KINDS:
                    # Its cause is already recorded, so "route by cause" is not a
                    # question anyone needs to answer. Asking it is exactly how a
                    # deliberate pause starts reading as an unexplained failure.
                    rep.recorded_state.append(
                        f"{v['key']}: {kind} — {ev.get('description') or 'recorded state'}"
                    )
                    continue
                owner = INTEGRITY_OWNER.get(
                    kind, BLOCK_OWNER["BLOCKED_INTEGRITY"]
                )
                rep.recommendations.append(
                    f"{v['key']}: unresolved integrity event "
                    f"[{kind or 'unknown'}] — {owner}"
                )
            if state == LifecycleState.LIVE_CANARY.value:
                for d in v["deployments"]:
                    if d["kind"] == "live" and not d["twin"]:
                        rep.recommendations.append(
                            f"{v['key']}: live deployment {d['key']} has no twin — "
                            "Live Ops"
                        )

    # Only real collector problems become an action. INACTIVE deliberately does
    # not: it means "not part of this deployment", and recommending an
    # investigation for it manufactures work and invites false causal stories.
    bad = [c for c in rep.data_health if c.get("warning")]
    if bad:
        rep.recommendations.append(
            "collector health: "
            + ", ".join(f"{c['collector']}({c['status']})" for c in bad)
            + " — Live Ops owns collector health; starved evidence fails no gate"
        )

    # Group blocked gates by owner so the handoff is one line per role, and the
    # cause is the evaluator's, not an inference from nearby output.
    by_owner: dict[str, list[str]] = {}
    for b in rep.blocked:
        label = f"{b['experiment']}·{b['gate_key']}"
        if b["missing_metrics"]:
            label += f" ({', '.join(b['missing_metrics'])})"
        by_owner.setdefault(b["owner"], []).append(label)
    for owner, labels in by_owner.items():
        rep.recommendations.append(
            f"blocked evidence — {', '.join(sorted(set(labels)))} → {owner}"
        )

    if rep.platform.get("unresolved_impacts"):
        rep.recommendations.append(
            "unresolved platform-impact dispositions — Platform Change Review"
        )


# ---------------------------------------------------------------------------
# Investigations: open issues + deterministic ticket candidates (spec §9)
# ---------------------------------------------------------------------------
#
# READ-ONLY, structurally. Everything below queries `read` (plain selects) and
# `issue_policy` (pure logic). Nothing here imports the issue service, so the
# Tower cannot open, update or dedupe a ticket even by mistake — and in
# particular it never advances `occurrence_count`, because a counter that moves
# when a report is rendered measures how often somebody ran the report.
#
# A candidate is a RECOMMENDATION to open an issue, matched to existing open
# issues by deterministic fingerprint. Candidates cover only anomalies the Tower
# already understands and already prints elsewhere; nothing here invents a new
# class of finding. Contract defects in particular get NO candidate detector —
# a defect in a registered contract is proven by research and recorded as an
# issue, and a heuristic that guessed "this gate looks malformed" would be a
# second, unreviewed opinion competing with the canonical contract.


def _candidate(
    *, detector: str, title: str, scope: str, anomaly_kind: str | None = None,
    experiment: str | None = None, experiment_id: int | None = None,
    version_id: int | None = None, epoch_id: int | None = None,
    deployment_id: int | None = None, gate_id: int | None = None,
    gate_verdict: str | None = None, integrity_cause: str | None = None,
    detail: str | None = None,
) -> dict:
    """One ticket candidate, carrying its own fingerprint and routing."""
    rec = issue_policy.recommend_owner(
        detector=detector, anomaly_kind=anomaly_kind,
        gate_verdict=gate_verdict, integrity_cause=integrity_cause,
    )
    return {
        "detector": detector,
        "anomaly_kind": anomaly_kind,
        # Carried so an ADOPTED issue records the verdict that caused it, and so
        # re-deriving the routing from the stored issue gives the same answer.
        "gate_verdict": gate_verdict,
        "fingerprint": issue_policy.issue_fingerprint(
            detector=detector, experiment_id=experiment_id, version_id=version_id,
            epoch_id=epoch_id, deployment_id=deployment_id, gate_id=gate_id,
            anomaly_kind=anomaly_kind,
        ),
        "title": title,
        "scope": scope,
        "detail": detail,
        "experiment": experiment,
        "experiment_id": experiment_id,
        "version_id": version_id,
        "epoch_id": epoch_id,
        "deployment_id": deployment_id,
        "gate_id": gate_id,
        "recommended_owner": rec.owner_role,
        "recommended_classification": rec.classification,
        "severity": rec.severity,
        "priority": rec.priority,
        "rationale": rec.rationale,
        "requires_operator_triage": rec.requires_operator_triage,
        # Filled in by the matcher below.
        "covered_by": None,
        "covering_status": None,
        "covering_owner": None,
    }


def _detect_candidates(session, rep: TowerReport) -> list[dict]:
    """Every anomaly this report understands, expressed as a ticket candidate."""
    out: list[dict] = []
    enf = rep.enforcement or {}

    if enf.get("resolver_degraded_alarms_24h"):
        out.append(_candidate(
            detector=issue_policy.DETECTOR_RESOLVER_DEGRADED,
            title="lineage resolver degraded in the last 24h",
            scope="system-wide",
            detail=f"{enf['resolver_degraded_alarms_24h']} alarm(s) — lineage "
                   "decisions came from a stale snapshot",
        ))
    for table, rows in (enf.get("post_cutover_unstamped") or {}).items():
        for row in rows:
            out.append(_candidate(
                detector=issue_policy.DETECTOR_UNSTAMPED_LINEAGE,
                anomaly_kind=f"{table}:{row['tag']}",
                title=f"post-cutover rows without lineage in {table}",
                scope=f"tag {row['tag']!r}",
                detail=f"{row['rows']} row(s) — something is trading outside "
                       "Experiment OS",
            ))

    for imp in (rep.platform.get("unresolved_impacts") or []):
        exp = read.get_experiment(session, imp["experiment"])
        out.append(_candidate(
            detector=issue_policy.DETECTOR_PLATFORM_IMPACT,
            anomaly_kind=f"{imp['component']}:{imp['revision']}",
            title=f"unresolved platform impact {imp['component']}:{imp['revision']}",
            scope=imp["experiment"],
            experiment=imp["experiment"],
            experiment_id=exp.id if exp is not None else None,
            detail=f"[{imp['impact_class']}/{imp['action']}] status={imp['status']} "
                   "— this experiment's evidence is blocked until it is applied",
        ))
    for rev in (rep.platform.get("pending_revisions") or []):
        if rev.get("activation_safe"):
            continue
        out.append(_candidate(
            detector=issue_policy.DETECTOR_REVISION_UNSAFE,
            anomaly_kind=f"{rev['component']}:{rev['version']}",
            title=f"pending revision {rev['component']}:{rev['version']} is not "
                  "safe to activate",
            scope="platform",
            detail=f"unaccounted: {rev['unaccounted']}",
        ))

    # Collector health. INACTIVE deliberately produces NO candidate: it means
    # "not part of this deployment", and manufacturing a ticket for it is how a
    # scanner-mode table ends up with an investigation nobody can close.
    for col in rep.data_health:
        if not col.get("warning"):
            continue
        out.append(_candidate(
            detector=issue_policy.DETECTOR_COLLECTOR_HEALTH,
            anomaly_kind=f"{col['collector']}:{col['status']}",
            title=f"collector {col['collector']} is {col['status']}",
            scope="system-wide",
            detail=col.get("note") or "starved evidence fails no gate",
        ))

    from . import enforcement as enf_mod

    for views in rep.by_state.values():
        for v in views:
            # An event that RECORDS A STATE rather than reporting a failure —
            # an intentional stand-down is the case that exists — gets no
            # candidate. Its cause is already recorded, so there is nothing to
            # diagnose, and manufacturing a ticket for it is exactly how a
            # deliberate pause starts reading as an unexplained failure.
            recorded_state = {
                ev.get("kind") for ev in v["integrity_events"]
                if ev.get("kind") in enf_mod.NON_BLOCKING_INTEGRITY_KINDS
            }
            for ev in v["integrity_events"]:
                if ev.get("kind") in enf_mod.NON_BLOCKING_INTEGRITY_KINDS:
                    continue
                out.append(_candidate(
                    detector=issue_policy.DETECTOR_INTEGRITY_EVENT,
                    anomaly_kind=ev.get("kind") or "unknown",
                    title=f"unresolved integrity event [{ev.get('kind') or 'unknown'}]",
                    scope=v["key"],
                    experiment=v["key"], experiment_id=v.get("experiment_id"),
                    version_id=v.get("version_id"), epoch_id=v.get("epoch_id"),
                    detail=ev.get("description"),
                ))
            for d in v["deployments"]:
                if d["kind"] == "live" and not d["twin"]:
                    out.append(_candidate(
                        detector=issue_policy.DETECTOR_MISSING_TWIN,
                        title=f"live deployment {d['key']} has no paper twin",
                        scope=f"{v['key']} · {d['key']}",
                        experiment=v["key"], experiment_id=v.get("experiment_id"),
                        version_id=v.get("version_id"), epoch_id=v.get("epoch_id"),
                        deployment_id=d.get("deployment_id"),
                        detail="the live/paper comparison that detects adverse "
                               "selection is missing",
                    ))
            # Zero evidence: a registered book that is deployed and has produced
            # nothing. The Tower can see the absence and cannot explain it — the
            # candidate exists precisely to route that diagnosis, not to assert
            # a cause (spec §2.3).
            if (
                v["state"] in _EVIDENCE_EXPECTED_STATES
                and v["deployments"]
                and any(d["tags"] for d in v["deployments"])
                and not v.get("total_entries")
                # ...unless the absence is already EXPLAINED. A book that was
                # deliberately stood down has produced nothing for a recorded
                # reason; opening an investigation into it would be asking a
                # question the system has already answered.
                and not recorded_state
            ):
                out.append(_candidate(
                    detector=issue_policy.DETECTOR_ZERO_EVIDENCE,
                    title=f"{v['key']} is deployed but has produced zero evidence",
                    scope=v["key"],
                    experiment=v["key"], experiment_id=v.get("experiment_id"),
                    version_id=v.get("version_id"), epoch_id=v.get("epoch_id"),
                    detail="cause unknown: broken runtime/config/wiring, "
                           "enforcement rejection, filters eliminating every "
                           "candidate, or genuinely no qualifying opportunities",
                ))

    for b in rep.blocked:
        out.append(_candidate(
            detector=issue_policy.DETECTOR_GATE_BLOCKED,
            anomaly_kind=b["verdict"],
            gate_verdict=b["verdict"],
            title=f"{b['gate_key']} is {b['verdict']}",
            scope=f"{b['experiment']} · {b['gate_key']}",
            experiment=b["experiment"], experiment_id=b.get("experiment_id"),
            version_id=b.get("version_id"), gate_id=b.get("gate_id"),
            detail=(", ".join(b["missing_metrics"]) or (b["reasons"][0]
                    if b["reasons"] else None)),
        ))
    return out


#: States in which a deployed experiment is expected to be producing evidence.
#: IDEA/PROBE are excluded: an idea that has traded nothing is not an anomaly.
_EVIDENCE_EXPECTED_STATES: frozenset[str] = frozenset({
    LifecycleState.PAPER.value,
    LifecycleState.LIVE_CANARY.value,
    LifecycleState.PRODUCTION.value,
})


def detected_candidates(session, *, evaluate: bool = True) -> list[dict]:
    """Every ticket candidate the Tower currently detects, covered or not.

    A PURE READ, and the resolution surface for adopting a candidate into a real
    issue (`issues.open_issue_from_candidate`). Both lists are returned together
    and un-deduplicated on purpose: the caller must be able to distinguish a
    fingerprint that is already ticketed from one that was never detected, and to
    refuse an ambiguous fingerprint rather than silently pick one of two."""
    rep = build_report(session, evaluate=evaluate)
    return [*rep.issue_candidates, *rep.covered_candidates]


def _issue_sections(session, rep: TowerReport) -> None:
    """Populate `open_issues` and `issue_candidates`. Writes nothing."""
    try:
        open_issues = read.list_issues(session, open_only=True)
    except Exception as exc:  # noqa: BLE001 — a read must degrade, not abort
        rep.anomalies.append(f"issue read failed: {exc}")
        return

    candidates = _detect_candidates(session, rep)
    by_fingerprint = {c["fingerprint"]: c for c in candidates}

    for issue in open_issues:
        summary = read.issue_summary(session, issue)
        # An open issue whose fingerprint is among today's candidates is not just
        # open — the anomaly is happening RIGHT NOW. That distinction is the one
        # a reader needs: "under investigation" and "still occurring" are
        # different states and only one of them is urgent.
        summary["currently_recurring"] = (
            issue.detector_fingerprint in by_fingerprint
            if issue.detector_fingerprint else False
        )
        summary["age_days"] = round(
            (_now() - _utc(issue.opened_at)).total_seconds() / 86400.0, 1
        ) if issue.opened_at else None
        rep.open_issues.append(summary)

    covered = {
        i.detector_fingerprint for i in open_issues if i.detector_fingerprint
    }
    for cand in candidates:
        if cand["fingerprint"] in covered:
            match = next(
                i for i in open_issues
                if i.detector_fingerprint == cand["fingerprint"]
            )
            cand["covered_by"] = match.issue_key
            cand["covering_status"] = match.status
            cand["covering_owner"] = match.current_owner_role
            rep.covered_candidates.append(cand)
            continue
        rep.issue_candidates.append(cand)

    # Routing: an existing ticket's CURRENT owner wins over the policy's initial
    # recommendation — the ticket may legitimately have been transferred, and
    # re-recommending the original role would undo that in the reader's head.
    for cand in candidates:
        if cand["covered_by"]:
            rep.recommendations.append(
                f"{cand['scope']}: {cand['title']} — covered by "
                f"{cand['covered_by']} ({cand['covering_status']}), owner "
                f"{cand['covering_owner']}"
            )
        else:
            rep.recommendations.append(
                f"{cand['scope']}: {cand['title']} — NO open issue covers this; "
                f"recommended owner {cand['recommended_owner']} "
                f"[{cand['recommended_classification']}/{cand['severity']}/"
                f"{cand['priority']}]. Opening it is a WRITE: hand off to that "
                "role (the Control Tower cannot open one)"
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


def _signed(v) -> str:
    return "n/a" if v is None else f"{float(v):+.3f}c/trade"


def _central(dt: datetime) -> str:
    """Report in America/Chicago — the convention every retired checker used, and
    the one the operator reads in."""
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo("America/Chicago")).strftime(
            "%Y-%m-%d %H:%M %Z")
    except Exception:  # noqa: BLE001
        return dt.strftime("%Y-%m-%d %H:%M UTC")




def _render_investigations(rep: TowerReport) -> list[str]:
    """OPEN INVESTIGATIONS + UNTICKETED ANOMALIES.

    Placed after SYSTEM/INTEGRITY and before any performance section, for the
    same reason integrity precedes performance everywhere else in this report:
    an open investigation is a statement about whether the numbers below can be
    trusted, and reading it afterwards is reading it too late.

    Neither section can change a verdict. An issue's status is workflow state —
    a RESOLVED ticket does not make a gate evaluable, and an OPEN one does not
    make a PASS provisional."""
    lines: list[str] = [
        "",
        "=== OPEN INVESTIGATIONS ===",
        # Stated whether or not there are rows: a reader who arrives at an empty
        # section still needs to know what a full one would and would not mean.
        "    Durable Experiment OS issues. Ticket status is WORKFLOW state: it "
        "never changes a gate verdict,",
        "    a lifecycle state or a recorded result.",
    ]
    if rep.open_issues:
        lines.append(
            "    `recurring` = the anomaly is still being detected right now, not "
            "merely under investigation."
        )
        rows = []
        for i in rep.open_issues:
            rows.append([
                i["issue_key"],
                i["severity"],
                i["priority"],
                i["classification"],
                i["owner_role"],
                i["status"] + (" · recurring" if i.get("currently_recurring") else ""),
                i["scope"],
                f"{i['age_days']}d" if i.get("age_days") is not None else "-",
                (i.get("latest_evidence") or "(no evidence recorded)")[:60],
            ])
        lines += _table(
            ["issue", "severity", "pri", "classification", "owner", "status",
             "scope", "age", "latest evidence"],
            rows,
        )
        for i in rep.open_issues:
            lines.append(f"    {i['issue_key']}  {i['title']}")
    else:
        lines.append("    (none open)")

    lines += ["", "=== UNTICKETED ANOMALIES ==="]
    if rep.issue_candidates:
        lines.append(
            "    Anomalies above that NO open issue covers. Each is a ticket "
            "CANDIDATE — this report cannot open one"
        )
        lines.append(
            "    (it is read-only); hand off to the recommended owner, who "
            "ADOPTS it with `issue open-candidate <fingerprint>`."
        )
        lines.append(
            "    Adoption, not `issue create`: only it carries the fingerprint "
            "printed under each anomaly, so the"
        )
        lines.append(
            "    ticket covers this anomaly instead of leaving it listed here "
            "forever."
        )
        lines.append(
            "    The recommended owner is who looks FIRST. Where the "
            "classification reads UNCLASSIFIED, the cause is"
        )
        lines.append(
            "    genuinely not yet established — that is the honest state, not a "
            "gap to be filled in by guessing."
        )
        rows = []
        for c in rep.issue_candidates:
            rows.append([
                c["detector"],
                c["severity"],
                c["priority"],
                c["recommended_classification"],
                c["recommended_owner"],
                c["scope"],
                c["title"][:56],
            ])
        lines += _table(
            ["detector", "severity", "pri", "would-classify", "recommended owner",
             "scope", "anomaly"],
            rows,
        )
        for c in rep.issue_candidates:
            if c.get("detail"):
                lines.append(f"    {c['scope']} · {c['detector']}: {c['detail']}")
            if c.get("requires_operator_triage"):
                lines.append(
                    f"        operator triage suggested — {c['rationale']}"
                )
            # The exact argument adoption needs. Printing it here is the whole
            # difference between an instruction and an actionable one: the
            # fingerprint is deterministic but not something anyone can retype
            # from the scope, and adoption refuses a fingerprint it cannot match.
            lines.append(f"        adopt: issue open-candidate {c['fingerprint']}")
    else:
        lines.append(
            "    (none — every anomaly this report detects is covered by an open "
            "issue, or there are none)"
        )
    return lines



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
    if rep.recorded_state:
        lines.append(
            "    RECORDED STATE (not an anomaly — explained by construction, blocks "
            "no evaluation):"
        )
        lines += [f"        {n}" for n in rep.recorded_state]
    deps = enf.get("deployments") or {}
    lines.append(
        f"    active deployments: {deps.get('total', 0)} "
        f"(grandfathered {deps.get('grandfathered', 0)}, native {deps.get('native', 0)})"
    )

    lines += _render_investigations(rep)

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
                    exp_.get("open_positions", 0),
                    f"${exp_.get('total_usd', 0):.2f}",
                    _verdict_of(v),
                ])
            lines += _table(["experiment", "live", "twin", "boundary",
                             "resting", "held", "at risk", "gate"], rows)
            lines.append(
                "    `at risk` = resting-order notional + held-position exposure. "
                "A stood-down book drains its"
            )
            lines.append(
                "    resting orders within a cycle; its HELD positions do not go "
                "away and are still real money."
            )
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

    lines += ["", "=== BLOCKED EVIDENCE ==="]
    if rep.blocked or rep.contract_findings:
        lines.append(
            "    An experiment can carry MORE THAN ONE blocker class. The "
            "evaluator's verdict and a proven contract"
        )
        lines.append(
            "    defect are independent: clearing one does not clear the other. "
            "Read every class listed for a book"
        )
        lines.append("    before deciding what would unblock it.")
    if rep.blocked:
        lines.append("")
        lines.append(
            "    EVALUATOR BLOCK — cause is the canonical evaluator's, not an "
            "inference. Do not attribute a block"
        )
        lines.append("    to anything not named here.")
        for b in rep.blocked:
            lines.append(f"    {b['experiment']} · {b['gate_key']}")
            lines.append(f"        {b['verdict']} ({b['source']})")
            if b["missing_metrics"]:
                lines.append("            missing providers:")
                lines += [f"              - {m}" for m in b["missing_metrics"]]
            elif b["reasons"]:
                lines.append(f"            {b['reasons'][0]}")
            for reason in b["reasons"][:4]:
                lines.append(f"        · {reason}")
            lines.append(f"        owner: {b['owner']}")
            for f in b.get("also_contract_defect") or []:
                lines.append(
                    f"        ALSO: CONTRACT DEFECT proven against v{f['version']} "
                    "— the block above is NOT the whole"
                )
                lines.append(
                    "        diagnosis; clearing it would leave this Version "
                    "structurally unevaluable. See below."
                )
    else:
        lines.append("    (none)")

    if rep.contract_findings:
        lines += ["", "    CONTRACT DEFECT — proven by research, NOT evaluator "
                  "output and NOT lifecycle state.",
                  "    Nothing here changes a verdict; the registered gate still "
                  "decides. Each finding is a durable",
                  "    Experiment OS issue bound to the version it was proven "
                  "against, and stops applying on its own",
                  "    once a corrected Version exists — the historical issue "
                  "remains queryable either way."]
        for f in rep.contract_findings:
            head = f"    {f['experiment']} · v{f['version']}"
            if f.get("issue_key"):
                head += f"  [{f['issue_key']} {f.get('status') or 'OPEN'}]"
            lines.append(head)
            lines.append(f"        {f['headline']}")
            for d in f["detail"]:
                lines.append(f"            {d}")
            lines.append(f"        owner: {f['owner']}")
            if f.get("proven_at") or f.get("proven_by"):
                lines.append(
                    f"        proven: {f.get('proven_at') or 'date unrecorded'} by "
                    f"{f.get('proven_by') or 'unrecorded'}"
                )
            if f.get("evidence_doc"):
                lines.append(f"        evidence: {f['evidence_doc']}")
            if f.get("issue_key"):
                lines.append(
                    f"        investigation: {f['issue_key']} — full history via "
                    f"`issue show {f['issue_key']}`"
                )

    if rep.realizable_context:
        lines += ["", "=== REALIZABLE-PROJECTION CONTEXT ===",
                  "    These gates decide on the live-fill projection, NOT on paper "
                  "profitability. Both numbers are shown so the",
                  "    verdict is read as the claim it is."]
        for ctx in rep.realizable_context:
            head = f"    {ctx['experiment']} · {ctx['gate_key']}"
            if ctx.get("kind") == "promotion":
                head += f"  [promotion {ctx.get('from_state')}→{ctx.get('to_state')}]"
            lines.append(head)
            lines.append(f"        verdict: {ctx['verdict']} ({ctx['source']})")
            for r in ctx["rows"]:
                if r["missing"]:
                    lines.append(
                        f"        {r['scope']}: realizable UNMEASURED — no trusted "
                        f"calibration cell (covered {r['covered_n']}/{r['total_n']})"
                    )
                    continue
                lines.append(
                    f"        {r['scope']}:"
                    f"  realizable projection {_signed(r['projected_cents'])}"
                    f"  ·  observed paper {_signed(r['observed_paper_cents'])}"
                    f"  ·  calibration coverage {r['covered_n']}/{r['total_n']}"
                    + (f" (calib {r['calibration_version']})"
                       if r.get("calibration_version") else "")
                )
            lines.append(
                "        Interpretation: the gate is satisfied on the calibrated "
                "live-fill projection."
            )
            lines.append(
                "        This is NOT a claim that observed paper P&L was positive."
            )
            obs = ctx.get("observed_paper") or {}
            if obs.get("phrase"):
                lines.append(f"        Describe paper as: {obs['phrase']}.")
            if ctx["any_sign_disagreement"]:
                lines.append(
                    "        CAUTION: calibrated realizable projection and observed "
                    "paper P&L disagree in sign."
                )
                lines.append(
                    "        The registered gate passes on realizable economics, not "
                    "paper profitability. Informational —"
                )
                lines.append(
                    "        the verdict is unchanged; only the pre-registered gate "
                    "decides."
                )

    lines += ["", "=== DATA COLLECTORS ==="]
    active = [c for c in rep.data_health if c["status"] != "INACTIVE"]
    inactive = [c for c in rep.data_health if c["status"] == "INACTIVE"]
    lines += _table(["collector", "status", "age_min", "cadence"],
                    [[c["collector"], c["status"], c.get("age_min"),
                      c.get("cadence_min")] for c in active])
    if inactive:
        lines += [
            "",
            f"    NOT ACTIVE IN THIS DEPLOYMENT (no data for >{INACTIVE_AFTER_DAYS}d) "
            "— informational, NOT an outage:",
        ]
        for c in inactive:
            note = f" — {c['note']}" if c.get("note") else ""
            lines.append(
                f"        {c['collector']}: INACTIVE, last write "
                f"{c.get('age_days')}d ago{note}"
            )
    lines += [
        "",
        "    COLLECTOR LEGEND: fresh = running normally · STALE = overdue, may be "
        "stalled · EMPTY = no rows · UNAVAILABLE = table absent ·",
        "    INACTIVE = not expected to be active in the current deployment.",
        "    Only STALE / EMPTY / UNAVAILABLE warrant collector-health "
        "investigation (Live Ops). INACTIVE is informational and is never, on its",
        "    own, the cause of a blocked gate — see BLOCKED EVIDENCE for the "
        "evaluator's actual reasons.",
    ]

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
    # State the window. "Recently" with no number was read as "this cycle", which
    # turned a 7-day lookback into a claim that 14 books died in one run.
    lines += ["", f"=== RECENTLY RETIRED — last {rep.retired_days} days ===",
              "    Dated by the experiment's retired_at (when the book actually "
              "died), not by when its record was written."]
    if rep.retired_recent:
        for r in rep.retired_recent:
            mark = "" if r.get("dated") else "  [no retired_at — transition date]"
            lines.append(f"    - {r['key']} @ {r['at']}{mark}: {r['reason']}")
    else:
        lines.append(f"    (none retired in the last {rep.retired_days} days)")
    if rep.retired_undated:
        lines.append(
            f"    {len(rep.retired_undated)} retired record(s) carry NO retired_at "
            "— undated legacy history, not recent: "
            + ", ".join(sorted(rep.retired_undated))
        )
    lines += ["", "=== RECOMMENDED NEXT ACTIONS ==="]
    lines += [f"    - {r}" for r in rep.recommendations] or [
        "    - none; continue accumulating evidence"]
    lines += [
        "",
        "Control Tower is READ ONLY. Route the write by what is actually blocking:",
        "    BLOCKED_DATA (missing metric provider / analysis / evidence transform)"
        " → Research Lab or task-specific Experiment OS metrics work.",
        "        This is NOT automatically a Platform Revision.",
        "    BLOCKED_PLATFORM → Platform Change Review (evidence comparability "
        "across a revision).",
        "    BLOCKED_INTEGRITY → by cause: runtime/live/collector → Live Ops; "
        "shared semantic or config → Platform Change Review;",
        "        experiment definition or evidence contract → Research Lab.",
        "    New hypothesis or successor experiment → Research Lab.",
        "    Recommend Platform Change Review only when shared platform semantics "
        "actually changed or need to change.",
    ]
    return "\n".join(lines)


REALIZABLE_METRIC = "realizable_cents_per_trade"


def _realizable_context(view: dict, gate: dict) -> dict | None:
    """What a verdict resting on the live-fill projection actually claims.

    `realizable_cents_per_trade` answers "what could a maker with this price mix
    realistically capture", projected from live-calibrated fill cells. Observed
    paper P&L answers "what did the paper engine record under its assumed-fill
    mechanics". They are different questions, and a gate registered on the first
    can pass while the second is negative — which is exactly what
    mmsell-price-ceiling does (+1.269c projected, -1.625c observed, 185/185
    covered). Rendered as a bare `PASS · realizable +1.269c`, a reader turns that
    into "this strategy is profitable", which is stronger than the evidence
    licenses.

    Every number here is lifted from the evaluator's own recorded clauses —
    including the observed paper figure, which the provider already computes over
    the identical scope and stores as `optimistic_cents_per_trade`. Nothing is
    recomputed: a second calculation would be a second answer."""
    recorded = gate.get("latest_result") or {}
    source = "recorded"
    clauses = (recorded.get("clauses") if isinstance(recorded, dict) else None) or []
    if not clauses:
        clauses = gate.get("live_clauses") or []
        source = "dry-run"
    rows: list[dict] = []
    for c in clauses:
        if not isinstance(c, dict) or (c.get("clause") or {}).get("metric") != REALIZABLE_METRIC:
            continue
        prov = c.get("provenance") or {}
        observed = prov.get("optimistic_cents_per_trade")
        projected = c.get("value")
        rows.append({
            "list": c.get("list"),
            "scope": c.get("scope"),
            "projected_cents": projected,
            "observed_paper_cents": observed,
            "covered_n": prov.get("covered_n"),
            "total_n": prov.get("total_n"),
            "calibration_version": prov.get("fill_calibration_version"),
            "missing": bool(c.get("missing")),
            "passed": c.get("passed"),
            # The caution this whole block exists for. Informational only — it
            # never changes a verdict, because the registered gate decides.
            "sign_disagreement": (
                projected is not None and observed is not None
                and projected > 0 > observed
            ),
        })
    if not rows:
        return None
    return {
        "experiment": view["key"],
        "experiment_id": view.get("experiment_id"),
        "version_id": view.get("version_id"),
        "gate_id": gate.get("gate_id"),
        "gate_key": gate.get("gate_key"),
        "verdict": recorded.get("verdict") or gate.get("live_verdict"),
        "source": source,
        "kind": gate.get("kind"),
        "from_state": gate.get("from_state"),
        "to_state": gate.get("to_state"),
        "rows": rows,
        "any_sign_disagreement": any(r["sign_disagreement"] for r in rows),
        "observed_paper": _observed_paper_summary(rows),
    }


# Below this magnitude a per-trade figure is not evidence of a direction: on the
# books here it is a fraction of one tick, inside the noise of any realistic
# sample. Named so "near-flat" is a stated threshold, not a vibe.
NEAR_FLAT_CENTS = 0.5


def _observed_paper_summary(rows: list[dict]) -> dict:
    """How to describe observed paper P&L ACROSS arms, in one honest phrase.

    A gate's rows are per-arm, and arms routinely disagree. Left to summarize a
    +0.04c arm and a -0.39c arm by hand, a reader collapses them into "negative"
    — which is not what the data says and is not recoverable by whoever reads the
    summary next. So the characterization is computed here, once, from the same
    clause values the table prints, and the render states it verbatim."""
    vals = [(r["scope"], r["observed_paper_cents"]) for r in rows
            if not r["missing"] and r["observed_paper_cents"] is not None]
    if not vals:
        return {"sign": "unknown", "phrase": "", "values": []}
    nums = [float(v) for _s, v in vals]
    near_flat = max(abs(n) for n in nums) < NEAR_FLAT_CENTS
    detail = ", ".join(f"{_signed(v)} on {sc}" for sc, v in vals)
    if all(n > 0 for n in nums):
        sign = "positive"
        phrase = f"observed paper results are positive on every arm: {detail}"
    elif all(n < 0 for n in nums):
        sign = "negative"
        phrase = f"observed paper results are negative on every arm: {detail}"
    else:
        sign = "mixed"
        phrase = (
            "observed paper results are MIXED IN SIGN across arms: " + detail
            + " — do not describe this as simply negative or simply positive"
        )
    if near_flat:
        phrase += (
            f" (all arms near-flat: every |value| < {NEAR_FLAT_CENTS:.1f}c/trade)"
        )
    return {"sign": sign, "near_flat": near_flat, "phrase": phrase,
            "values": [[sc, float(v)] for sc, v in vals]}


def _block_cause_line(entry: dict | None) -> str:
    """The one-line cause: the missing providers if any, else the first reason."""
    if not entry:
        return ""
    if entry["missing_metrics"]:
        return "missing provider: " + ", ".join(entry["missing_metrics"])
    if entry["reasons"]:
        return entry["reasons"][0]
    return ""


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
