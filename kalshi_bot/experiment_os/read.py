"""Experiment OS read path — the one shared query surface (spec §32).

The strategy status loop, dashboards, generated registry views, and (later) evo all
consume experiment state through these helpers rather than each reinterpreting the
tables. Everything here is read-only: plain selects, no session mutation.

The two lineage directions the spec makes load-bearing:

  * downward — experiment_tree(): everything about one experiment, nested;
  * upward — strategy_tag_lineage(): from a concrete strategy tag (the operational
    join key into paper_trades/live_orders) back to deployment → arm → epoch →
    version → experiment → platform snapshot, with no archaeology.
"""

from __future__ import annotations

from sqlalchemy import func, select

from . import issue_policy
from .lifecycle import LifecycleState
from .models import (
    Experiment,
    ExperimentArm,
    ExperimentDeployment,
    ExperimentDeploymentArm,
    ExperimentEpoch,
    ExperimentGate,
    ExperimentGateResult,
    ExperimentIntegrityEvent,
    ExperimentIssue,
    ExperimentIssueEvent,
    ExperimentIssueEvidence,
    ExperimentIssueLink,
    ExperimentLegacyEvidence,
    ExperimentOsIssueCommand,
    ExperimentStateTransition,
    ExperimentVersion,
    PlatformComponent,
    PlatformRevision,
    PlatformSnapshot,
    PlatformSnapshotItem,
)

# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------


def get_experiment(session, key: str) -> Experiment | None:
    return session.scalar(select(Experiment).where(Experiment.key == key))


def list_experiments(
    session,
    *,
    state: LifecycleState | str | None = None,
    family: str | None = None,
    origin: str | None = None,
    legacy: bool | None = None,
) -> list[Experiment]:
    """Filterable experiment listing. `legacy=True` → imported records only;
    `legacy=False` → native new-system records only; None → both."""
    stmt = select(Experiment).order_by(Experiment.key)
    if state is not None:
        stmt = stmt.where(Experiment.state == LifecycleState(state).value)
    if family is not None:
        stmt = stmt.where(Experiment.family == family)
    if origin is not None:
        stmt = stmt.where(Experiment.origin == origin)
    if legacy is True:
        stmt = stmt.where(Experiment.legacy_class.is_not(None))
    elif legacy is False:
        stmt = stmt.where(Experiment.legacy_class.is_(None))
    return list(session.scalars(stmt))


def active_experiments(session) -> list[Experiment]:
    """Everything not RETIRED (PAUSED counts as active-but-held)."""
    return list(
        session.scalars(
            select(Experiment)
            .where(Experiment.state != LifecycleState.RETIRED.value)
            .order_by(Experiment.key)
        )
    )


def transitions_for(session, experiment: Experiment) -> list[ExperimentStateTransition]:
    return list(
        session.scalars(
            select(ExperimentStateTransition)
            .where(ExperimentStateTransition.experiment_id == experiment.id)
            .order_by(
                ExperimentStateTransition.occurred_at, ExperimentStateTransition.id
            )
        )
    )


def versions_for(session, experiment: Experiment) -> list[ExperimentVersion]:
    return list(
        session.scalars(
            select(ExperimentVersion)
            .where(ExperimentVersion.experiment_id == experiment.id)
            .order_by(ExperimentVersion.version)
        )
    )


def latest_version(session, experiment: Experiment) -> ExperimentVersion | None:
    return session.scalar(
        select(ExperimentVersion)
        .where(ExperimentVersion.experiment_id == experiment.id)
        .order_by(ExperimentVersion.version.desc())
        .limit(1)
    )


def arms_for(session, version: ExperimentVersion) -> list[ExperimentArm]:
    return list(
        session.scalars(
            select(ExperimentArm)
            .where(ExperimentArm.version_id == version.id)
            .order_by(ExperimentArm.arm_key)
        )
    )


def epochs_for(session, version: ExperimentVersion) -> list[ExperimentEpoch]:
    return list(
        session.scalars(
            select(ExperimentEpoch)
            .where(ExperimentEpoch.version_id == version.id)
            .order_by(ExperimentEpoch.epoch_number)
        )
    )


def open_epoch_for(session, version: ExperimentVersion) -> ExperimentEpoch | None:
    return session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == version.id,
            ExperimentEpoch.ended_at.is_(None),
        )
    )


def deployments_for(session, epoch: ExperimentEpoch) -> list[ExperimentDeployment]:
    return list(
        session.scalars(
            select(ExperimentDeployment)
            .where(ExperimentDeployment.epoch_id == epoch.id)
            .order_by(ExperimentDeployment.deployment_key)
        )
    )


def deployment_arms(
    session, deployment: ExperimentDeployment
) -> list[tuple[ExperimentArm, str | None]]:
    """(arm, concrete strategy tag) pairs for one deployment."""
    rows = session.execute(
        select(ExperimentArm, ExperimentDeploymentArm.strategy_tag)
        .join(
            ExperimentDeploymentArm,
            ExperimentDeploymentArm.arm_id == ExperimentArm.id,
        )
        .where(ExperimentDeploymentArm.deployment_id == deployment.id)
        .order_by(ExperimentArm.arm_key)
    ).all()
    return [(arm, tag) for arm, tag in rows]


def twin_for(session, live_deployment: ExperimentDeployment) -> ExperimentDeployment | None:
    """The paper twin shadowing a live deployment, if one is registered."""
    return session.scalar(
        select(ExperimentDeployment).where(
            ExperimentDeployment.twin_of_deployment_id == live_deployment.id
        )
    )


def gates_for(session, version: ExperimentVersion) -> list[ExperimentGate]:
    return list(
        session.scalars(
            select(ExperimentGate)
            .where(ExperimentGate.version_id == version.id)
            .order_by(ExperimentGate.gate_key)
        )
    )


def gate_results_for(session, gate: ExperimentGate) -> list[ExperimentGateResult]:
    return list(
        session.scalars(
            select(ExperimentGateResult)
            .where(ExperimentGateResult.gate_id == gate.id)
            .order_by(ExperimentGateResult.computed_at, ExperimentGateResult.id)
        )
    )


def integrity_events_for(session, experiment: Experiment) -> list[ExperimentIntegrityEvent]:
    return list(
        session.scalars(
            select(ExperimentIntegrityEvent)
            .where(ExperimentIntegrityEvent.experiment_id == experiment.id)
            .order_by(ExperimentIntegrityEvent.detected_at)
        )
    )


def legacy_evidence_for(session, experiment: Experiment) -> list[ExperimentLegacyEvidence]:
    return list(
        session.scalars(
            select(ExperimentLegacyEvidence)
            .where(ExperimentLegacyEvidence.experiment_id == experiment.id)
            .order_by(ExperimentLegacyEvidence.id)
        )
    )


# ---------------------------------------------------------------------------
# Platform registry
# ---------------------------------------------------------------------------


def snapshot_contents(session, snapshot: PlatformSnapshot) -> list[tuple[str, str]]:
    """Sorted (component key, revision version) pairs pinned by a snapshot."""
    rows = session.execute(
        select(PlatformComponent.key, PlatformRevision.version)
        .join(
            PlatformSnapshotItem,
            PlatformSnapshotItem.component_id == PlatformComponent.id,
        )
        .join(PlatformRevision, PlatformRevision.id == PlatformSnapshotItem.revision_id)
        .where(PlatformSnapshotItem.snapshot_id == snapshot.id)
        .order_by(PlatformComponent.key)
    ).all()
    return [(k, v) for k, v in rows]


def experiments_using_revision(
    session, revision: PlatformRevision, *, active_only: bool = True
) -> list[Experiment]:
    """Every experiment with an epoch whose pinned snapshot includes this revision —
    the affected-experiment query behind the systemic change protocol (spec §17)."""
    stmt = (
        select(Experiment)
        .join(ExperimentVersion, ExperimentVersion.experiment_id == Experiment.id)
        .join(ExperimentEpoch, ExperimentEpoch.version_id == ExperimentVersion.id)
        .join(
            PlatformSnapshotItem,
            PlatformSnapshotItem.snapshot_id == ExperimentEpoch.platform_snapshot_id,
        )
        .where(PlatformSnapshotItem.revision_id == revision.id)
        .distinct()
        .order_by(Experiment.key)
    )
    if active_only:
        stmt = stmt.where(Experiment.state != LifecycleState.RETIRED.value)
    return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


def strategy_tag_lineage(session, strategy_tag: str) -> list[dict]:
    """Resolve a concrete strategy tag to its full research lineage.

    One row per (deployment, arm) the tag is attached to:
    experiment / version / arm / epoch / deployment / platform snapshot — the exact
    chain spec §14 requires every observation to be traceable through."""
    rows = session.execute(
        select(
            Experiment,
            ExperimentVersion,
            ExperimentArm,
            ExperimentEpoch,
            ExperimentDeployment,
            PlatformSnapshot,
        )
        .join(ExperimentVersion, ExperimentVersion.experiment_id == Experiment.id)
        .join(ExperimentEpoch, ExperimentEpoch.version_id == ExperimentVersion.id)
        .join(ExperimentDeployment, ExperimentDeployment.epoch_id == ExperimentEpoch.id)
        .join(
            ExperimentDeploymentArm,
            ExperimentDeploymentArm.deployment_id == ExperimentDeployment.id,
        )
        .join(ExperimentArm, ExperimentArm.id == ExperimentDeploymentArm.arm_id)
        .join(
            PlatformSnapshot,
            PlatformSnapshot.id == ExperimentEpoch.platform_snapshot_id,
        )
        .where(ExperimentDeploymentArm.strategy_tag == strategy_tag)
        .order_by(ExperimentDeployment.deployment_key)
    ).all()
    return [
        {
            "strategy_tag": strategy_tag,
            "experiment_key": exp.key,
            "experiment_state": exp.state,
            "version": ver.version,
            "arm_key": arm.arm_key,
            "arm_role": arm.role,
            "epoch_number": epoch.epoch_number,
            "deployment_key": dep.deployment_key,
            "deployment_stage": dep.stage,
            "deployment_kind": dep.kind,
            "platform_snapshot_fingerprint": snap.fingerprint,
        }
        for exp, ver, arm, epoch, dep, snap in rows
    ]


def arm_strategy_tags(
    session, epoch: ExperimentEpoch, arm_key: str | None, kind: str = "paper"
) -> tuple[str, ...]:
    """Concrete tags backing one arm (None = all arms) in one epoch, one kind."""
    tags: list[str] = []
    for dep in deployments_for(session, epoch):
        if dep.kind != kind:
            continue
        for arm, tag in deployment_arms(session, dep):
            if (arm_key is None or arm.arm_key == arm_key) and tag:
                tags.append(tag)
    return tuple(sorted(set(tags)))


def experiment_scoreboard(session, experiment: Experiment) -> dict:
    """Current evidence + gate standing for one experiment, from structured state.

    Per-arm universal paper metrics over the CURRENT epoch's evidence window, plus
    each gate's registered floors and its LATEST recorded result. Metrics are
    computed on demand (the immutable copies live inside gate results); this read
    never writes."""
    from datetime import datetime, timezone  # local: keep read import-light

    from .evaluator import missing_metrics as _missing_metrics
    from .metrics import MetricScope, compute_metric

    board: dict = {
        "key": experiment.key,
        "state": experiment.state,
        "legacy_class": experiment.legacy_class,
        "integrity": experiment.migration_integrity,
        "arms": [],
        "gates": [],
    }
    ver = latest_version(session, experiment)
    if ver is None:
        board["note"] = "no version (minimal legacy stub)"
        return board
    epoch = open_epoch_for(session, ver) or (
        epochs_for(session, ver)[-1] if epochs_for(session, ver) else None
    )
    if epoch is None:
        board["note"] = "no operating epoch"
        return board
    snap = session.get(PlatformSnapshot, epoch.platform_snapshot_id)
    now = datetime.now(timezone.utc)
    end = min(now, epoch.ended_at) if epoch.ended_at is not None else now
    board["version"] = ver.version
    board["epoch"] = epoch.epoch_number
    board["window"] = [str(epoch.started_at), str(end)]
    board["platform_snapshot"] = snap.fingerprint[:16]

    for arm in arms_for(session, ver):
        tags = arm_strategy_tags(session, epoch, arm.arm_key, "paper")
        scope = MetricScope(
            experiment_key=experiment.key,
            version=ver.version,
            epoch_number=epoch.epoch_number,
            arm_key=arm.arm_key,
            deployment_kind="paper",
            strategy_tags=tags,
            deployment_keys=(),
            window_start=epoch.started_at,
            window_end=end,
            platform_snapshot_fingerprint=snap.fingerprint,
        )
        row = {"arm": arm.arm_key, "role": arm.role, "tags": list(tags)}
        for key in ("settled_trades", "pnl_cents_per_trade", "win_rate_pct",
                    "open_trades", "entries"):
            mv = compute_metric(session, key, scope)
            row[key] = mv.value
        board["arms"].append(row)

    for gate in gates_for(session, ver):
        latest = session.scalar(
            select(ExperimentGateResult)
            .where(ExperimentGateResult.gate_id == gate.id)
            .order_by(
                ExperimentGateResult.computed_at.desc(), ExperimentGateResult.id.desc()
            )
            .limit(1)
        )
        board["gates"].append(
            {
                "gate_key": gate.gate_key,
                "kind": gate.kind,
                "from_state": gate.from_state,
                "to_state": gate.to_state,
                "evidence_started_at": str(gate.evidence_started_at)
                if gate.evidence_started_at
                else None,
                "floors": gate.spec_json.get("sample"),
                "latest_result": None
                if latest is None
                else {
                    "verdict": latest.verdict,
                    "computed_at": str(latest.computed_at),
                    "computed_by": latest.computed_by,
                    "explanation": latest.explanation,
                    # The recorded cause, carried forward verbatim: a reader must
                    # never have to guess why a gate is blocked.
                    "blocking_reasons": list(
                        (latest.metrics_json or {}).get("blocking_reasons") or []
                    ),
                    "missing_metrics": _missing_metrics(
                        (latest.metrics_json or {}).get("clauses")
                    ),
                    # The recorded per-clause detail, carried forward so a reader
                    # can see WHAT a verdict claims without recomputing anything.
                    "clauses": list((latest.metrics_json or {}).get("clauses") or []),
                },
            }
        )
    return board


def experiment_tree(session, experiment: Experiment) -> dict:
    """The whole experiment as one nested, JSON-serializable structure — the
    inspect_experiment read the CLI and later surfaces render."""
    tree: dict = {
        "key": experiment.key,
        "title": experiment.title,
        "state": experiment.state,
        "paused_from_state": experiment.paused_from_state,
        "origin": experiment.origin,
        "family": experiment.family,
        "hypothesis": experiment.hypothesis,
        "legacy_class": experiment.legacy_class,
        "migration_integrity": experiment.migration_integrity,
        "platform_snapshot_id": experiment.platform_snapshot_id,
        "predecessor_experiment_id": experiment.predecessor_experiment_id,
        "retired_at": experiment.retired_at,
        "docs": experiment.docs_json,
        "versions": [],
        "transitions": [
            {
                "from": t.from_state,
                "to": t.to_state,
                "occurred_at": t.occurred_at,
                "actor": t.actor,
                "approved_by": t.approved_by,
                "reason": t.reason,
                "gate_result_id": t.gate_result_id,
            }
            for t in transitions_for(session, experiment)
        ],
        "integrity_events": [
            {
                "kind": e.kind,
                "severity": e.severity,
                "detected_at": e.detected_at,
                "description": e.description,
                "resolved_at": e.resolved_at,
            }
            for e in integrity_events_for(session, experiment)
        ],
        "legacy_evidence": [
            {
                "label": e.label,
                "evidence_class": e.evidence_class,
                "source": e.source,
                "summary": e.summary_json,
            }
            for e in legacy_evidence_for(session, experiment)
        ],
    }
    for ver in versions_for(session, experiment):
        vnode: dict = {
            "version": ver.version,
            "frozen_at": ver.frozen_at,
            "fingerprint": ver.fingerprint,
            "pre_registration_hash": ver.pre_registration_hash,
            "independent_variable": ver.independent_variable,
            "held_constant": ver.held_constant_json,
            "change_reason": ver.change_reason,
            "arms": [
                {
                    "arm_key": a.arm_key,
                    "role": a.role,
                    "strategy_tag": a.strategy_tag,
                    "params": a.params_json,
                }
                for a in arms_for(session, ver)
            ],
            "gates": [],
            "epochs": [],
        }
        for gate in gates_for(session, ver):
            vnode["gates"].append(
                {
                    "gate_key": gate.gate_key,
                    "kind": gate.kind,
                    "from_state": gate.from_state,
                    "to_state": gate.to_state,
                    "spec": gate.spec_json,
                    "spec_hash": gate.spec_hash,
                    "evidence_started_at": gate.evidence_started_at,
                    "results": [
                        {
                            "verdict": r.verdict,
                            "computed_at": r.computed_at,
                            "computed_by": r.computed_by,
                            "sample": r.sample_json,
                            "metrics": r.metrics_json,
                            "explanation": r.explanation,
                        }
                        for r in gate_results_for(session, gate)
                    ],
                }
            )
        for epoch in epochs_for(session, ver):
            snap = session.get(PlatformSnapshot, epoch.platform_snapshot_id)
            enode = {
                "epoch_number": epoch.epoch_number,
                "started_at": epoch.started_at,
                "ended_at": epoch.ended_at,
                "reason": epoch.reason,
                "impact_class": epoch.impact_class,
                "platform_snapshot_fingerprint": snap.fingerprint if snap else None,
                "platform_snapshot": snapshot_contents(session, snap) if snap else [],
                "deployments": [
                    {
                        "deployment_key": d.deployment_key,
                        "stage": d.stage,
                        "kind": d.kind,
                        "twin_of_deployment_id": d.twin_of_deployment_id,
                        "started_at": d.started_at,
                        "ended_at": d.ended_at,
                        "arms": [
                            {"arm_key": a.arm_key, "strategy_tag": tag}
                            for a, tag in deployment_arms(session, d)
                        ],
                    }
                    for d in deployments_for(session, epoch)
                ],
            }
            vnode["epochs"].append(enode)
        tree["versions"].append(vnode)
    return tree


# ---------------------------------------------------------------------------
# Issue workflow — READ surface (docs/EXPERIMENT_OS_ISSUES.md)
# ---------------------------------------------------------------------------
#
# These live here, not in `issues.py`, for one structural reason: the Experiment
# Control Tower must display open investigations and it must remain incapable of
# writing. It already imports `read`; if it imported the issue SERVICE to get a
# listing, "the Tower cannot mutate state" would rest on nobody calling the wrong
# function rather than on the Tower not having it. `issues.py` re-exports
# everything below, so the service API is still complete in one place.


def get_issue(session, ref) -> ExperimentIssue | None:
    """Look up by `issue_key` (`XOS-000123`, case-insensitive) or raw id."""
    if isinstance(ref, ExperimentIssue):
        return ref
    text = str(ref).strip()
    if issue_policy.parse_issue_key(text) is not None:
        return session.scalar(
            select(ExperimentIssue).where(ExperimentIssue.issue_key == text.upper())
        )
    if text.isdigit():
        return session.get(ExperimentIssue, int(text))
    return session.scalar(
        select(ExperimentIssue).where(ExperimentIssue.issue_key == text)
    )


def list_issues(
    session,
    *,
    status: str | None = None,
    open_only: bool = False,
    owner_role: str | None = None,
    classification: str | None = None,
    severity: str | None = None,
    priority: str | None = None,
    detector: str | None = None,
    experiment_id: int | None = None,
    version_id: int | None = None,
    limit: int | None = None,
) -> list[ExperimentIssue]:
    """Query issues, worst-first (priority, then severity, then oldest).

    `open_only` means "still live work" — every status except RESOLVED,
    CLOSED_NO_ACTION and DUPLICATE. That is what the Control Tower, recurrence
    matching and duplicate suggestion all mean by open, so it is defined once."""
    stmt = select(ExperimentIssue)
    if status is not None:
        stmt = stmt.where(ExperimentIssue.status == str(status))
    if open_only:
        stmt = stmt.where(ExperimentIssue.status.in_(sorted(issue_policy.OPEN_STATUSES)))
    if owner_role is not None:
        stmt = stmt.where(ExperimentIssue.current_owner_role == str(owner_role))
    if classification is not None:
        stmt = stmt.where(ExperimentIssue.classification == str(classification))
    if severity is not None:
        stmt = stmt.where(ExperimentIssue.severity == str(severity))
    if priority is not None:
        stmt = stmt.where(ExperimentIssue.priority == str(priority))
    if detector is not None:
        stmt = stmt.where(ExperimentIssue.detector == str(detector))
    if experiment_id is not None:
        stmt = stmt.where(ExperimentIssue.experiment_id == experiment_id)
    if version_id is not None:
        stmt = stmt.where(ExperimentIssue.version_id == version_id)
    rows = list(session.scalars(stmt))
    rows.sort(
        key=lambda i: (
            issue_policy.priority_rank(i.priority),
            issue_policy.severity_rank(i.severity),
            i.opened_at,
        )
    )
    return rows[:limit] if limit else rows


def open_issue_by_fingerprint(session, fingerprint: str) -> ExperimentIssue | None:
    """The OPEN issue already covering this exact problem scope, if any.

    Open ones only, deliberately: a RESOLVED issue must never suppress a
    genuinely recurring anomaly (§9). "We fixed that once" is not evidence that
    it is fixed now, and a report that hid the recurrence would be worse than one
    that never had tickets at all."""
    return session.scalar(
        select(ExperimentIssue)
        .where(
            ExperimentIssue.detector_fingerprint == fingerprint,
            ExperimentIssue.status.in_(sorted(issue_policy.OPEN_STATUSES)),
        )
        .order_by(ExperimentIssue.opened_at)
    )


def issue_events(session, issue: ExperimentIssue) -> list[ExperimentIssueEvent]:
    return list(
        session.scalars(
            select(ExperimentIssueEvent)
            .where(ExperimentIssueEvent.issue_id == issue.id)
            .order_by(ExperimentIssueEvent.occurred_at, ExperimentIssueEvent.id)
        )
    )


def issue_evidence(session, issue: ExperimentIssue) -> list[ExperimentIssueEvidence]:
    return list(
        session.scalars(
            select(ExperimentIssueEvidence)
            .where(ExperimentIssueEvidence.issue_id == issue.id)
            .order_by(ExperimentIssueEvidence.captured_at, ExperimentIssueEvidence.id)
        )
    )


def issue_links(session, issue: ExperimentIssue) -> list[ExperimentIssueLink]:
    return list(
        session.scalars(
            select(ExperimentIssueLink)
            .where(ExperimentIssueLink.issue_id == issue.id)
            .order_by(ExperimentIssueLink.created_at, ExperimentIssueLink.id)
        )
    )


def issue_scope_label(session, issue: ExperimentIssue) -> str:
    """A compact human scope: `mmsell-live v1/e1 · promo-gate`, or `system-wide`.

    Assembled from the foreign keys rather than from stored text, so it cannot
    drift from the lineage it claims to describe."""
    parts: list[str] = []
    if issue.experiment_id is not None:
        exp = session.get(Experiment, issue.experiment_id)
        parts.append(exp.key if exp is not None else f"experiment:{issue.experiment_id}")
    if issue.version_id is not None:
        ver = session.get(ExperimentVersion, issue.version_id)
        tail = f"v{ver.version}" if ver is not None else f"version:{issue.version_id}"
        if issue.epoch_id is not None:
            epoch = session.get(ExperimentEpoch, issue.epoch_id)
            if epoch is not None:
                tail += f"/e{epoch.epoch_number}"
        parts.append(tail)
    if issue.gate_id is not None:
        gate = session.get(ExperimentGate, issue.gate_id)
        parts.append(gate.gate_key if gate is not None else f"gate:{issue.gate_id}")
    if issue.deployment_id is not None:
        dep = session.get(ExperimentDeployment, issue.deployment_id)
        if dep is not None:
            parts.append(dep.deployment_key)
    if issue.platform_revision_id is not None:
        rev = session.get(PlatformRevision, issue.platform_revision_id)
        if rev is not None:
            parts.append(f"revision:{rev.version}")
    return " · ".join(parts) if parts else "system-wide"


def issue_summary(session, issue: ExperimentIssue) -> dict:
    """The compact row both read surfaces render."""
    latest = None
    evidence = issue_evidence(session, issue)
    if evidence:
        newest = evidence[-1]
        latest = f"{newest.evidence_type}: {newest.summary[:80]}"
    return {
        "issue_key": issue.issue_key,
        "title": issue.title,
        "status": issue.status,
        "classification": issue.classification,
        "severity": issue.severity,
        "priority": issue.priority,
        "owner_role": issue.current_owner_role,
        "opened_by_role": issue.opened_by_role,
        "opened_at": str(issue.opened_at) if issue.opened_at else None,
        "scope": issue_scope_label(session, issue),
        "experiment_id": issue.experiment_id,
        "version_id": issue.version_id,
        "detector": issue.detector,
        "detector_fingerprint": issue.detector_fingerprint,
        "occurrence_count": issue.occurrence_count,
        "disposition": issue.disposition,
        "latest_evidence": latest,
    }


def issue_detail(session, issue: ExperimentIssue) -> dict:
    """The whole investigation as JSON: row, append-only history, evidence, links."""
    out = issue_summary(session, issue)
    out.update({
        "problem_statement": issue.problem_statement,
        "evidence_summary": issue.evidence_summary,
        "proposed_fix": issue.proposed_fix,
        "validation_plan": issue.validation_plan,
        "resolution_summary": issue.resolution_summary,
        "resolved_at": str(issue.resolved_at) if issue.resolved_at else None,
        "requires_new_version": issue.requires_new_version,
        "requires_new_epoch": issue.requires_new_epoch,
        "requires_platform_revision": issue.requires_platform_revision,
        "requires_pause_or_stand_down": issue.requires_pause_or_stand_down,
        "duplicate_of_issue_id": issue.duplicate_of_issue_id,
        "supersedes_issue_id": issue.supersedes_issue_id,
        "discovered_from_issue_id": issue.discovered_from_issue_id,
        "first_observed_at": str(issue.first_observed_at)
        if issue.first_observed_at else None,
        "last_observed_at": str(issue.last_observed_at)
        if issue.last_observed_at else None,
        "details_json": issue.details_json,
        "events": [
            {
                "event_type": e.event_type,
                "occurred_at": str(e.occurred_at),
                "actor": e.actor,
                "actor_role": e.actor_role,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "from_owner_role": e.from_owner_role,
                "to_owner_role": e.to_owner_role,
                "from_classification": e.from_classification,
                "to_classification": e.to_classification,
                "reason": e.reason,
                "payload": e.payload_json,
            }
            for e in issue_events(session, issue)
        ],
        "evidence": [
            {
                "evidence_type": e.evidence_type,
                "summary": e.summary,
                "source_ref": e.source_ref,
                "captured_at": str(e.captured_at),
                "captured_by": e.captured_by,
                "content_hash": e.content_hash,
            }
            for e in issue_evidence(session, issue)
        ],
        "links": [
            {
                "link_type": row.link_type,
                "reference": row.reference,
                "label": row.label,
                "created_at": str(row.created_at),
            }
            for row in issue_links(session, issue)
        ],
    })
    return out


def contract_defect_findings(
    session, experiment: Experiment, version_number: int | None
) -> list[dict]:
    """Proven contract defects that apply to this experiment AT THIS VERSION.

    The database-backed successor to the hand-written `findings.py` registry, and
    it keeps that registry's two load-bearing properties:

      * **Bound to the version it was proven against.** A corrected successor
        Version drops the finding automatically — nothing has to remember to
        delete it, and it can never linger as a permanent scare line over a book
        that was already fixed. A `None` version matches nothing: a finding is a
        claim about a specific registered contract.
      * **Display-only.** Nothing here feeds the evaluator, the gate runner,
        enforcement, admission or any transition, so a wrong entry can mislead a
        reader but cannot authorize or block anything.

    Only OPEN issues are returned. A resolved defect stays queryable through the
    normal issue surfaces — it just stops being reported as an active blocker."""
    if version_number is None:
        return []
    out: list[dict] = []
    for issue in list_issues(
        session, open_only=True, experiment_id=experiment.id,
        detector=issue_policy.DETECTOR_CONTRACT_DEFECT,
    ):
        if issue.version_id is None:
            continue
        ver = session.get(ExperimentVersion, issue.version_id)
        if ver is None or ver.version != version_number:
            continue
        details = issue.details_json or {}
        evidence_doc = details.get("evidence_doc")
        if not evidence_doc:
            docs = [
                e.source_ref for e in issue_evidence(session, issue)
                if e.evidence_type == "RESEARCH_DOCUMENT" and e.source_ref
            ]
            evidence_doc = docs[0] if docs else None
        out.append({
            "issue_key": issue.issue_key,
            "experiment": experiment.key,
            "version": version_number,
            "headline": issue.title,
            "detail": list(details.get("detail") or []),
            "owner": details.get("owner_label")
            or f"{issue.current_owner_role}"
            + (f" — {issue.disposition}" if issue.disposition else ""),
            # Whether clearing the evaluator's own named blocker would still
            # leave this Version unevaluable. The whole reason the second
            # blocker class exists, so it is never inferred.
            "independent_of_evaluator": bool(
                details.get("independent_of_evaluator", True)
            ),
            "evidence_doc": evidence_doc,
            "proven_at": details.get("proven_at"),
            "proven_by": details.get("proven_by"),
            "status": issue.status,
            "disposition": issue.disposition,
        })
    return out


def open_issue_counts(session) -> dict[str, int]:
    """Open-issue counts by classification — a one-line health figure."""
    rows = session.execute(
        select(ExperimentIssue.classification, func.count())
        .where(ExperimentIssue.status.in_(sorted(issue_policy.OPEN_STATUSES)))
        .group_by(ExperimentIssue.classification)
    ).all()
    return {str(k): int(n) for k, n in rows}


# ---------------------------------------------------------------------------
# Issue-command receipts (the worker transport's ledger)
# ---------------------------------------------------------------------------

# What a receipt may print. METADATA ONLY: identity, action, actor, timing,
# outcome. `payload_json` is deliberately absent — it holds whatever prose the
# author wrote, and this surface is read by tools that print everything they are
# given. `result_json` IS printed because the executor generates it: bounded
# identifiers and states, never the submitted text. What proves the payload is
# `payload_hash` plus the key NAMES, which come from a fixed vocabulary.


def issue_command(session, command_id: str) -> ExperimentOsIssueCommand | None:
    """One receipt by its command id."""
    return session.execute(
        select(ExperimentOsIssueCommand).where(
            ExperimentOsIssueCommand.command_id == str(command_id)
        )
    ).scalar_one_or_none()


def issue_commands(
    session, *, limit: int = 20, status: str | None = None,
    issue_key: str | None = None,
) -> list[ExperimentOsIssueCommand]:
    """Recent receipts, newest first."""
    stmt = select(ExperimentOsIssueCommand)
    if status:
        stmt = stmt.where(ExperimentOsIssueCommand.status == str(status).upper())
    if issue_key:
        stmt = stmt.where(ExperimentOsIssueCommand.issue_key == str(issue_key))
    stmt = stmt.order_by(ExperimentOsIssueCommand.id.desc()).limit(int(limit))
    return list(session.execute(stmt).scalars())


def issue_command_summary(row: ExperimentOsIssueCommand) -> dict:
    """A receipt as safe-to-print metadata (see the note above)."""
    payload = row.payload_json or {}
    return {
        "command_id": row.command_id,
        "action": row.action,
        "actor": row.actor,
        "actor_role": row.actor_role,
        "status": row.status,
        "issue_key": row.issue_key,
        "schema_version": row.schema_version,
        "payload_hash": row.payload_hash,
        # Names only. The vocabulary is fixed in issue_commands.ACTIONS, so a key
        # name can never be author-supplied text.
        "payload_fields": sorted(payload) if isinstance(payload, dict) else [],
        "requested_at": row.requested_at,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "result": row.result_json,
        "error": row.error,
    }
