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

from sqlalchemy import select

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
    ExperimentLegacyEvidence,
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
