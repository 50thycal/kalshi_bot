"""Experiment OS write path — validated, audited helpers (spec §32).

Every consequential write goes through a helper here so the invariants hold in one
place instead of per-script:

  * a NEW experiment always receives the current, complete platform snapshot;
  * epochs cannot open without a frozen version and a complete snapshot;
  * lifecycle transitions run through the legal-transition validator and always append
    an audit row (money-adjacent promotions additionally require operator approval and
    a PASS gate result);
  * frozen versions, registered gates with evidence, state transitions, gate results
    and legacy evidence are immutable — enforced by a session flush guard, not
    convention;
  * legacy imports record exactly what history supports (integrity level A–D visible),
    and are the only path allowed to skip the snapshot requirement.

Helpers follow repository.py conventions: take an active session, flush, return the
ORM object; commit/rollback belongs to the caller. Nothing here is invoked by the
trading runtime in the foundation release (enforcement mode OFF).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import event, func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session as SASession

from .lifecycle import (
    ArmRole,
    DeploymentKind,
    EvidenceClass,
    GateVerdict,
    ImpactClass,
    LegacyClass,
    LifecycleState,
    MigrationIntegrity,
    validate_transition,
)
from .models import (
    STANDARD_PLATFORM_COMPONENTS,
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_hash(obj) -> str:
    """Reproducible sha256 over a JSON-serializable object (spec §15 fingerprints)."""
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class ExperimentOsError(ValueError):
    """Base for experiment-OS validation failures."""


class MissingPlatformSnapshot(ExperimentOsError):
    """A new experiment/epoch needs a complete platform snapshot and none can be
    resolved (no components registered, or a component has no active revision)."""


class IncompletePlatformSnapshot(ExperimentOsError):
    """The snapshot does not cover every registered platform component."""


class ImmutableRecord(ExperimentOsError):
    """An append-only or frozen record was modified or deleted."""


class GateSpecError(ExperimentOsError):
    """A structured gate spec failed shape validation."""


# ---------------------------------------------------------------------------
# Append-only / frozen-record flush guard
# ---------------------------------------------------------------------------
#
# History is never rewritten (spec §31): the guard rejects UPDATE/DELETE on audit
# rows and on frozen contracts at flush time, so no code path — service, script, or
# test — can silently mutate them through the ORM. Installed on the Session class at
# import; it inspects only experiment-OS types, so every other table is untouched.
# (Core bulk update()/delete() statements bypass ORM flush events; nothing in this
# repo issues them against these tables, and Postgres-level protection can come with
# the enforcement PR if ever needed.)

_APPEND_ONLY = (
    ExperimentStateTransition,
    ExperimentGateResult,
    ExperimentLegacyEvidence,
    PlatformSnapshotItem,
)

# Mutable-field allowlists for records that are otherwise immutable.
_GATE_MUTABLE = {"evidence_started_at", "superseded_by_gate_id", "notes"}
_INTEGRITY_MUTABLE = {"resolved_at", "resolution"}
_SNAPSHOT_MUTABLE = {"label", "description"}
# Platform revisions: identity is immutable from creation; once a revision has been
# activated, its SEMANTIC declaration (fingerprint, compatibility/normalization
# judgment, safety class, reason, PR reference, description) freezes too — history
# depends on it. Only the lifecycle remains mutable: status (active → retired),
# retired_at, and activated_at — the last stays editable because an initially
# uncertain activation boundary is *established* later from measurement (spec §17.4),
# which is a lifecycle fact, not a semantic edit.
_REVISION_IDENTITY = {"component_id", "version", "created_at"}
_REVISION_LIFECYCLE = {"status", "activated_at", "retired_at"}


def _changed_fields(obj) -> set[str]:
    insp = sa_inspect(obj)
    return {attr.key for attr in insp.attrs if attr.history.has_changes()}


def _pre_flush_value(obj, field: str):
    """The value `field` held before this flush's modifications (committed value)."""
    hist = sa_inspect(obj).attrs[field].history
    if hist.has_changes():
        return hist.deleted[0] if hist.deleted else None
    return getattr(obj, field)


def _guard_dirty(obj) -> None:
    changed = _changed_fields(obj)
    if not changed:
        return
    if isinstance(obj, _APPEND_ONLY):
        raise ImmutableRecord(
            f"{type(obj).__name__} rows are append-only; changing {sorted(changed)} "
            "would rewrite history"
        )
    if isinstance(obj, ExperimentVersion):
        if _pre_flush_value(obj, "frozen_at") is not None:
            raise ImmutableRecord(
                f"experiment version {obj.id} is frozen; a changed contract is a NEW "
                f"version, not an edit (attempted: {sorted(changed)})"
            )
    elif isinstance(obj, ExperimentGate):
        if _pre_flush_value(obj, "evidence_started_at") is not None:
            illegal = changed - {"superseded_by_gate_id", "notes"}
            if illegal:
                raise ImmutableRecord(
                    f"gate {obj.id} has accumulated evidence since "
                    f"{_pre_flush_value(obj, 'evidence_started_at')}; a pre-registered "
                    "gate may not be edited in place — record the gap and register a "
                    f"new gate on a new version (attempted: {sorted(illegal)})"
                )
        else:
            illegal = changed - _GATE_MUTABLE
            if illegal:
                raise ImmutableRecord(
                    f"gate {obj.id}: {sorted(illegal)} are immutable from registration "
                    "(delete and re-register while no evidence has started)"
                )
    elif isinstance(obj, ExperimentIntegrityEvent):
        illegal = changed - _INTEGRITY_MUTABLE
        if illegal:
            raise ImmutableRecord(
                f"integrity event {obj.id} is append-only apart from resolution "
                f"(attempted: {sorted(illegal)})"
            )
    elif isinstance(obj, PlatformSnapshot):
        illegal = changed - _SNAPSHOT_MUTABLE
        if illegal:
            raise ImmutableRecord(
                f"platform snapshot {obj.id} is content-addressed and immutable "
                f"(attempted: {sorted(illegal)})"
            )
    elif isinstance(obj, PlatformRevision):
        illegal = changed & _REVISION_IDENTITY
        if illegal:
            raise ImmutableRecord(
                f"platform revision {obj.id} identity is immutable "
                f"(attempted: {sorted(illegal)})"
            )
        was_activated = (
            _pre_flush_value(obj, "status") != "pending"
            or _pre_flush_value(obj, "activated_at") is not None
        )
        if was_activated:
            illegal = changed - _REVISION_LIFECYCLE
            if illegal:
                raise ImmutableRecord(
                    f"platform revision {obj.id} has been activated; its semantic "
                    "declaration is frozen — a changed judgment is a NEW revision "
                    f"(attempted: {sorted(illegal)})"
                )


def _guard_deleted(obj) -> None:
    if isinstance(obj, _APPEND_ONLY + (PlatformSnapshot,)):
        raise ImmutableRecord(f"{type(obj).__name__} rows may not be deleted")
    if isinstance(obj, ExperimentGate) and _pre_flush_value(obj, "evidence_started_at"):
        raise ImmutableRecord(
            f"gate {obj.id} has accumulated evidence and may not be deleted"
        )
    if isinstance(obj, ExperimentVersion) and _pre_flush_value(obj, "frozen_at"):
        raise ImmutableRecord(f"frozen experiment version {obj.id} may not be deleted")
    if isinstance(obj, ExperimentStateTransition):
        raise ImmutableRecord("state transitions may not be deleted")


@event.listens_for(SASession, "before_flush")
def _experiment_os_flush_guard(session, flush_context, instances) -> None:
    for obj in session.deleted:
        _guard_deleted(obj)
    for obj in session.dirty:
        if session.is_modified(obj, include_collections=False):
            _guard_dirty(obj)


# ---------------------------------------------------------------------------
# Platform registry (spec §16)
# ---------------------------------------------------------------------------


def ensure_standard_components(session) -> list[PlatformComponent]:
    """Seed the standard component set; idempotent. Returns all registered components."""
    for key in STANDARD_PLATFORM_COMPONENTS:
        register_platform_component(session, key)
    return list(session.scalars(select(PlatformComponent).order_by(PlatformComponent.key)))


def register_platform_component(session, key: str, description: str | None = None):
    """Get-or-create a platform component by key."""
    obj = session.scalar(select(PlatformComponent).where(PlatformComponent.key == key))
    if obj is None:
        obj = PlatformComponent(key=key, description=description, created_at=_now())
        session.add(obj)
        session.flush()
    return obj


def register_platform_revision(
    session,
    component: PlatformComponent | str,
    *,
    version: str,
    description: str | None = None,
    reason: str | None = None,
    fingerprint: str | None = None,
    backward_compatibility: str | None = None,
    normalization_available: bool | None = None,
    safety_class: str | None = None,
    pr_ref: str | None = None,
    activate: bool = False,
    activated_at: datetime | None = None,
    boundary_unknown: bool = False,
) -> PlatformRevision:
    """Register an immutable revision of one component; optionally activate it."""
    if boundary_unknown and activated_at is not None:
        raise ExperimentOsError(
            "boundary_unknown contradicts an explicit activated_at — pass one"
        )
    if isinstance(component, str):
        component = register_platform_component(session, component)
    existing = session.scalar(
        select(PlatformRevision).where(
            PlatformRevision.component_id == component.id,
            PlatformRevision.version == version,
        )
    )
    if existing is not None:
        raise ExperimentOsError(
            f"platform revision {component.key}:{version} already exists — revisions "
            "are immutable; register a new version"
        )
    rev = PlatformRevision(
        component_id=component.id,
        version=version,
        status="pending",
        description=description,
        reason=reason,
        fingerprint=fingerprint,
        backward_compatibility=backward_compatibility,
        normalization_available=normalization_available,
        safety_class=safety_class,
        pr_ref=pr_ref,
        created_at=_now(),
    )
    session.add(rev)
    session.flush()
    if activate:
        activate_platform_revision(
            session, rev, activated_at=activated_at, boundary_unknown=boundary_unknown
        )
    return rev


def activate_platform_revision(
    session,
    revision: PlatformRevision,
    *,
    activated_at: datetime | None = None,
    boundary_unknown: bool = False,
) -> PlatformRevision:
    """Activate a revision at a (preferably measured) runtime boundary, retiring the
    previously-active revision of the same component.

    When the real activation boundary is NOT known, pass `boundary_unknown=True`:
    the revision goes active with `activated_at` (and the prior revision's
    `retired_at`) left NULL, keeping the uncertainty explicit instead of stamping a
    fabricated import/merge timestamp. Establish the boundary later — from
    measurement — with establish_activation_boundary(). Calling with neither an
    explicit `activated_at` nor `boundary_unknown` uses now(), which is only correct
    when the activation is genuinely happening now."""
    if revision.status == "retired":
        raise ExperimentOsError(
            f"platform revision {revision.id} is retired and cannot be re-activated"
        )
    if boundary_unknown:
        if activated_at is not None:
            raise ExperimentOsError(
                "boundary_unknown contradicts an explicit activated_at — pass one"
            )
        at = None
    else:
        at = activated_at or _now()
    prior = session.scalars(
        select(PlatformRevision).where(
            PlatformRevision.component_id == revision.component_id,
            PlatformRevision.status == "active",
            PlatformRevision.id != revision.id,
        )
    ).all()
    for old in prior:
        old.status = "retired"
        old.retired_at = at
    revision.status = "active"
    revision.activated_at = at
    session.flush()
    return revision


def establish_activation_boundary(
    session, revision: PlatformRevision, *, activated_at: datetime
) -> PlatformRevision:
    """Set the measured activation boundary on a revision that went active with an
    unknown one (spec §17.4: gates over the boundary stay BLOCKED_PLATFORM until it
    is established). Also closes the predecessor's open retired_at, since the same
    instant ends the old revision. Refuses to move an already-established boundary —
    that would be rewriting history; if the measurement was wrong, record an
    integrity event and register a corrected revision."""
    if revision.status != "active":
        raise ExperimentOsError(
            f"revision {revision.id} is {revision.status}, not active"
        )
    if revision.activated_at is not None:
        raise ExperimentOsError(
            f"revision {revision.id} already has a measured boundary "
            f"({revision.activated_at}) — it cannot be moved"
        )
    revision.activated_at = activated_at
    predecessor = session.scalar(
        select(PlatformRevision)
        .where(
            PlatformRevision.component_id == revision.component_id,
            PlatformRevision.status == "retired",
            PlatformRevision.retired_at.is_(None),
        )
        .order_by(PlatformRevision.created_at.desc())
        .limit(1)
    )
    if predecessor is not None:
        predecessor.retired_at = activated_at
    session.flush()
    return revision


def _active_revision_set(session) -> tuple[list[tuple[PlatformComponent, PlatformRevision]], list[str]]:
    """(component, active revision) pairs for all components, plus keys missing one."""
    components = session.scalars(
        select(PlatformComponent).order_by(PlatformComponent.key)
    ).all()
    pairs: list[tuple[PlatformComponent, PlatformRevision]] = []
    missing: list[str] = []
    for comp in components:
        rev = session.scalar(
            select(PlatformRevision).where(
                PlatformRevision.component_id == comp.id,
                PlatformRevision.status == "active",
            )
        )
        if rev is None:
            missing.append(comp.key)
        else:
            pairs.append((comp, rev))
    return pairs, missing


def _snapshot_fingerprint(pairs: list[tuple[PlatformComponent, PlatformRevision]]) -> str:
    return canonical_hash(sorted((c.key, r.version) for c, r in pairs))


def get_active_platform_snapshot(session) -> PlatformSnapshot | None:
    """The snapshot row matching the CURRENT active revision set, or None if the
    current set is incomplete or has not been snapshotted yet. Pure read."""
    pairs, missing = _active_revision_set(session)
    if missing or not pairs:
        return None
    return session.scalar(
        select(PlatformSnapshot).where(
            PlatformSnapshot.fingerprint == _snapshot_fingerprint(pairs)
        )
    )


def resolve_active_platform_snapshot(
    session, *, label: str | None = None, description: str | None = None
) -> PlatformSnapshot:
    """The snapshot for the current active revision set, created if it does not exist.

    This is how a new experiment/epoch automatically inherits the current fee model,
    fill model, taxonomy, etc. instead of copying assumptions by hand. Raises
    MissingPlatformSnapshot when the registry cannot produce a complete snapshot."""
    pairs, missing = _active_revision_set(session)
    if not pairs and not missing:
        raise MissingPlatformSnapshot(
            "no platform components are registered — seed the registry "
            "(ensure_standard_components + revisions) before creating experiments"
        )
    if missing:
        raise MissingPlatformSnapshot(
            f"components with no ACTIVE revision: {missing} — a complete platform "
            "snapshot requires an active revision for every registered component"
        )
    fp = _snapshot_fingerprint(pairs)
    snap = session.scalar(select(PlatformSnapshot).where(PlatformSnapshot.fingerprint == fp))
    if snap is not None:
        return snap
    snap = PlatformSnapshot(
        fingerprint=fp, label=label, description=description, created_at=_now()
    )
    session.add(snap)
    session.flush()
    for comp, rev in pairs:
        session.add(
            PlatformSnapshotItem(
                snapshot_id=snap.id, component_id=comp.id, revision_id=rev.id
            )
        )
    session.flush()
    return snap


def snapshot_missing_components(session, snapshot: PlatformSnapshot) -> list[str]:
    """Registered component keys the snapshot does NOT pin (empty = complete)."""
    covered = set(
        session.scalars(
            select(PlatformSnapshotItem.component_id).where(
                PlatformSnapshotItem.snapshot_id == snapshot.id
            )
        )
    )
    stmt = select(PlatformComponent.key).order_by(PlatformComponent.key)
    if covered:
        stmt = stmt.where(PlatformComponent.id.not_in(covered))
    return list(session.scalars(stmt))


def _require_complete(session, snapshot: PlatformSnapshot) -> PlatformSnapshot:
    missing = snapshot_missing_components(session, snapshot)
    if missing:
        raise IncompletePlatformSnapshot(
            f"snapshot {snapshot.id} does not pin: {missing}"
        )
    return snapshot


# ---------------------------------------------------------------------------
# Experiments (spec §5.2, §22)
# ---------------------------------------------------------------------------


def create_experiment(
    session,
    *,
    key: str,
    origin: str,
    title: str | None = None,
    family: str | None = None,
    hypothesis: str | None = None,
    mechanism: str | None = None,
    counterparty: str | None = None,
    falsification: str | None = None,
    universe: str | None = None,
    docs: dict | None = None,
    notes: str | None = None,
    predecessor: Experiment | None = None,
    snapshot: PlatformSnapshot | None = None,
    actor: str = "operator",
    now: datetime | None = None,
) -> Experiment:
    """Create a NEW experiment in IDEA, pinned to the current complete platform snapshot.

    The snapshot requirement is the point: a new experiment inherits the active shared
    platform baseline (fee/fill/taxonomy/...) from the registry rather than copying
    assumptions by hand. Legacy history goes through import_legacy_experiment instead."""
    if session.scalar(select(Experiment).where(Experiment.key == key)) is not None:
        raise ExperimentOsError(f"experiment key {key!r} already exists")
    if not origin:
        raise ExperimentOsError("origin is required (operator | idea_model | evo | ...)")
    snap = snapshot or resolve_active_platform_snapshot(session)
    _require_complete(session, snap)
    at = now or _now()
    exp = Experiment(
        key=key,
        title=title,
        origin=origin,
        family=family,
        hypothesis=hypothesis,
        mechanism=mechanism,
        counterparty=counterparty,
        falsification=falsification,
        universe=universe,
        docs_json=docs,
        notes=notes,
        state=LifecycleState.IDEA.value,
        platform_snapshot_id=snap.id,
        predecessor_experiment_id=predecessor.id if predecessor is not None else None,
        created_at=at,
        updated_at=at,
    )
    session.add(exp)
    session.flush()
    session.add(
        ExperimentStateTransition(
            experiment_id=exp.id,
            from_state=None,
            to_state=LifecycleState.IDEA.value,
            occurred_at=at,
            actor=actor,
            reason="experiment created",
            created_at=at,
        )
    )
    session.flush()
    return exp


def import_legacy_experiment(
    session,
    *,
    key: str,
    state: LifecycleState | str,
    legacy_class: LegacyClass | str,
    migration_integrity: MigrationIntegrity | str,
    origin: str | None = None,
    title: str | None = None,
    family: str | None = None,
    hypothesis: str | None = None,
    docs: dict | None = None,
    notes: str | None = None,
    verdict: str | None = None,
    imported_at: datetime | None = None,
    retired_at: datetime | None = None,
    paused_from: LifecycleState | str | None = None,
    predecessor: Experiment | None = None,
) -> Experiment:
    """Import a pre-Experiment-OS strategy with exactly the metadata history supports.

    Deliberately does NOT require a platform snapshot, does NOT create versions, arms,
    epochs, or gates, and records the caller-stated migration integrity level (A–D) —
    nothing is reconstructed here, so nothing can be invented here. `verdict` becomes
    the audit-row reason (e.g. the registry's kill verdict for a retired book).
    A PAUSED import should state `paused_from` (the state it shelved out of) so a
    later resume is legal; without it the record can only retire."""
    if session.scalar(select(Experiment).where(Experiment.key == key)) is not None:
        raise ExperimentOsError(f"experiment key {key!r} already exists")
    st = LifecycleState(state)
    lc = LegacyClass(legacy_class)
    mi = MigrationIntegrity(migration_integrity)
    pf = LifecycleState(paused_from).value if paused_from is not None else None
    if pf is not None and st is not LifecycleState.PAUSED:
        raise ExperimentOsError("paused_from is only meaningful when importing PAUSED")
    at = imported_at or _now()
    exp = Experiment(
        key=key,
        title=title,
        origin=origin,
        family=family,
        hypothesis=hypothesis,
        docs_json=docs,
        notes=notes,
        state=st.value,
        paused_from_state=pf,
        predecessor_experiment_id=predecessor.id if predecessor is not None else None,
        legacy_class=lc.value,
        migration_integrity=mi.value,
        imported_at=at,
        # Real historical dates only — never defaulted to import time (that would be
        # invented history). Unknown stays NULL.
        retired_at=retired_at if st is LifecycleState.RETIRED else None,
        created_at=at,
        updated_at=at,
    )
    session.add(exp)
    session.flush()
    session.add(
        ExperimentStateTransition(
            experiment_id=exp.id,
            from_state=None,
            to_state=st.value,
            occurred_at=at,
            actor="import",
            reason=verdict or f"legacy import ({lc.value}, integrity {mi.value})",
            created_at=at,
        )
    )
    session.flush()
    return exp


# ---------------------------------------------------------------------------
# Versions and arms (spec §5.3–5.4, §9)
# ---------------------------------------------------------------------------

# Contract fields that materially define the tested question — the fingerprint basis.
_MATERIAL_CONTRACT_FIELDS = (
    "hypothesis",
    "universe_selector",
    "universe_exclusions",
    "entry_rule",
    "exit_rule",
    "sizing_rule",
    "execution_style",
    "independent_variable",
    "held_constant_json",
    "control_required",
    "control_exemption_reason",
    "metrics_json",
    "sample_json",
    "costs_json",
    "risk_json",
)


def create_experiment_version(
    session,
    experiment: Experiment,
    *,
    hypothesis: str | None = None,
    mechanism: str | None = None,
    counterparty: str | None = None,
    falsification: str | None = None,
    universe_selector: str | None = None,
    universe_exclusions: str | None = None,
    entry_rule: str | None = None,
    exit_rule: str | None = None,
    sizing_rule: str | None = None,
    execution_style: str | None = None,
    independent_variable: str | None = None,
    held_constant: list[str] | None = None,
    control_required: bool = True,
    control_exemption_reason: str | None = None,
    metrics: dict | None = None,
    sample: dict | None = None,
    costs: dict | None = None,
    risk: dict | None = None,
    provenance: dict | None = None,
    monitoring: dict | None = None,
    docs: dict | None = None,
    change_reason: str | None = None,
    now: datetime | None = None,
) -> ExperimentVersion:
    """Create the next (draft) version of the scientific contract for an experiment.

    The previous latest version, if any, becomes the predecessor. Freeze with
    freeze_version() before opening an epoch."""
    if experiment.state == LifecycleState.RETIRED.value:
        raise ExperimentOsError(
            f"experiment {experiment.key} is RETIRED (terminal) — a revived concept is "
            "a successor experiment referencing this one, not a new version"
        )
    prev = session.scalar(
        select(ExperimentVersion)
        .where(ExperimentVersion.experiment_id == experiment.id)
        .order_by(ExperimentVersion.version.desc())
        .limit(1)
    )
    if prev is not None and change_reason is None:
        raise ExperimentOsError(
            "a successor version needs change_reason: what changed in the scientific "
            "contract (spec: a version change means a different question)"
        )
    ver = ExperimentVersion(
        experiment_id=experiment.id,
        version=(prev.version + 1) if prev is not None else 1,
        created_at=now or _now(),
        hypothesis=hypothesis,
        mechanism=mechanism,
        counterparty=counterparty,
        falsification=falsification,
        universe_selector=universe_selector,
        universe_exclusions=universe_exclusions,
        entry_rule=entry_rule,
        exit_rule=exit_rule,
        sizing_rule=sizing_rule,
        execution_style=execution_style,
        independent_variable=independent_variable,
        held_constant_json=held_constant,
        control_required=control_required,
        control_exemption_reason=control_exemption_reason,
        metrics_json=metrics,
        sample_json=sample,
        costs_json=costs,
        risk_json=risk,
        provenance_json=provenance,
        monitoring_json=monitoring,
        docs_json=docs,
        predecessor_version_id=prev.id if prev is not None else None,
        change_reason=change_reason,
    )
    session.add(ver)
    session.flush()
    return ver


def add_arm(
    session,
    version: ExperimentVersion,
    *,
    arm_key: str,
    role: ArmRole | str,
    description: str | None = None,
    params: dict | None = None,
    strategy_tag: str | None = None,
) -> ExperimentArm:
    """Declare an arm on a DRAFT version (arms freeze with the version)."""
    if version.frozen_at is not None:
        raise ImmutableRecord(
            f"version {version.version} is frozen — a changed arm set is a new version"
        )
    role_v = ArmRole(role)
    arm = ExperimentArm(
        version_id=version.id,
        arm_key=arm_key,
        role=role_v.value,
        description=description,
        params_json=params,
        strategy_tag=strategy_tag,
        created_at=_now(),
    )
    session.add(arm)
    session.flush()
    return arm


_CONTROL_ROLES = {ArmRole.CONTROL.value, ArmRole.NO_TRADE_CONTROL.value}


def freeze_version(
    session, version: ExperimentVersion, *, now: datetime | None = None
) -> ExperimentVersion:
    """Freeze the contract: validates arms/control, computes the fingerprint and the
    immutable pre-registration hash. After this the version cannot change."""
    if version.frozen_at is not None:
        raise ImmutableRecord(f"version {version.version} is already frozen")
    arms = session.scalars(
        select(ExperimentArm)
        .where(ExperimentArm.version_id == version.id)
        .order_by(ExperimentArm.arm_key)
    ).all()
    if not arms:
        raise ExperimentOsError("cannot freeze a version with no arms")
    if (
        version.control_required
        and not version.control_exemption_reason
        and not any(a.role in _CONTROL_ROLES for a in arms)
    ):
        raise ExperimentOsError(
            "control_required is set but no control arm is declared — declare one or "
            "record an explicit control_exemption_reason"
        )
    contract = {f: getattr(version, f) for f in _MATERIAL_CONTRACT_FIELDS}
    contract["arms"] = [
        {"arm_key": a.arm_key, "role": a.role, "params": a.params_json} for a in arms
    ]
    gates = session.scalars(
        select(ExperimentGate)
        .where(ExperimentGate.version_id == version.id)
        .order_by(ExperimentGate.gate_key)
    ).all()
    # The freeze is the pre-registration lock: every gate registered so far must
    # resolve unambiguously against the FINAL arm set — a gate that would need
    # its scope guessed at evaluation time is refused now, not reinterpreted later.
    from .evaluator import validate_gate_scopes  # lazy: evaluator imports service

    for gate in gates:
        problems = validate_gate_scopes(session, version, gate.spec_json)
        if problems:
            raise GateSpecError(
                f"gate {gate.gate_key!r} does not resolve against the final arm "
                f"set: {'; '.join(problems)}"
            )
    version.fingerprint = canonical_hash(contract)
    version.pre_registration_hash = canonical_hash(
        {"contract": version.fingerprint, "gates": [g.spec_hash for g in gates]}
    )
    version.contract_json = contract
    version.frozen_at = now or _now()
    session.flush()
    return version


# ---------------------------------------------------------------------------
# Epochs (spec §5.5)
# ---------------------------------------------------------------------------


def open_epoch(
    session,
    version: ExperimentVersion,
    *,
    reason: str,
    started_at: datetime | None = None,
    snapshot: PlatformSnapshot | None = None,
    impact_class: ImpactClass | str | None = None,
    notes: str | None = None,
) -> ExperimentEpoch:
    """Open a new operating interval for a FROZEN version, pinned to a complete snapshot.

    One open epoch per version at a time: close_epoch() the previous interval first —
    evidence accumulating under two snapshots at once is exactly the ambiguity epochs
    exist to prevent."""
    if version.frozen_at is None:
        raise ExperimentOsError(
            f"version {version.version} is not frozen — an epoch operates an immutable "
            "contract"
        )
    owner = session.get(Experiment, version.experiment_id)
    if owner is not None and owner.state == LifecycleState.RETIRED.value:
        raise ExperimentOsError(
            f"experiment {owner.key} is RETIRED — no new operating interval may open"
        )
    open_prev = session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == version.id,
            ExperimentEpoch.ended_at.is_(None),
        )
    )
    if open_prev is not None:
        raise ExperimentOsError(
            f"epoch {open_prev.epoch_number} of version {version.version} is still "
            "open — close it first (one operating interval at a time)"
        )
    snap = snapshot or resolve_active_platform_snapshot(session)
    _require_complete(session, snap)
    ic = ImpactClass(impact_class).value if impact_class is not None else None
    last_num = session.scalar(
        select(func.max(ExperimentEpoch.epoch_number)).where(
            ExperimentEpoch.version_id == version.id
        )
    )
    epoch = ExperimentEpoch(
        version_id=version.id,
        epoch_number=(last_num or 0) + 1,
        platform_snapshot_id=snap.id,
        started_at=started_at or _now(),
        reason=reason,
        impact_class=ic,
        notes=notes,
        created_at=_now(),
    )
    session.add(epoch)
    session.flush()
    return epoch


def close_epoch(
    session, epoch: ExperimentEpoch, *, ended_at: datetime | None = None
) -> ExperimentEpoch:
    if epoch.ended_at is not None:
        raise ExperimentOsError(f"epoch {epoch.epoch_number} is already closed")
    epoch.ended_at = ended_at or _now()
    session.flush()
    return epoch


# ---------------------------------------------------------------------------
# Deployments (spec §5.6, §13)
# ---------------------------------------------------------------------------

_DEPLOYABLE_STAGES = {
    LifecycleState.PROBE.value,
    LifecycleState.PAPER.value,
    LifecycleState.LIVE_CANARY.value,
    LifecycleState.PRODUCTION.value,
}
_LIVE_STAGES = {LifecycleState.LIVE_CANARY.value, LifecycleState.PRODUCTION.value}


def register_deployment(
    session,
    epoch: ExperimentEpoch,
    *,
    deployment_key: str,
    stage: LifecycleState | str,
    kind: DeploymentKind | str,
    arms: dict[str, str | None],
    twin_of: ExperimentDeployment | None = None,
    config: dict | None = None,
    code_fingerprint: str | None = None,
    started_at: datetime | None = None,
    notes: str | None = None,
) -> ExperimentDeployment:
    """Register a concrete deployment of arms within an epoch.

    `arms` maps arm_key → concrete strategy tag (None when the deployment has no
    per-arm tag). A PAPER_TWIN must name the LIVE deployment it shadows, in the same
    epoch — the twin relationship is structural, not a tag-suffix convention."""
    stage_v = LifecycleState(stage).value
    if stage_v not in _DEPLOYABLE_STAGES:
        raise ExperimentOsError(f"{stage_v} is not a deployable stage")
    epoch_version = session.get(ExperimentVersion, epoch.version_id)
    owner = session.get(Experiment, epoch_version.experiment_id)
    if owner is not None and owner.state == LifecycleState.RETIRED.value:
        raise ExperimentOsError(
            f"experiment {owner.key} is RETIRED — no new deployment may register"
        )
    kind_v = DeploymentKind(kind).value
    if kind_v == DeploymentKind.LIVE.value and stage_v not in _LIVE_STAGES:
        raise ExperimentOsError("a live deployment runs at LIVE_CANARY or PRODUCTION")
    if kind_v == DeploymentKind.PAPER_TWIN.value:
        if twin_of is None:
            raise ExperimentOsError("a paper twin must reference its live deployment")
        if twin_of.kind != DeploymentKind.LIVE.value:
            raise ExperimentOsError("twin_of must be a LIVE deployment")
        if twin_of.epoch_id != epoch.id:
            raise ExperimentOsError(
                "a twin starts at the same effective boundary as its live deployment "
                "— same epoch required"
            )
    elif twin_of is not None:
        raise ExperimentOsError("twin_of is only valid on a paper_twin deployment")
    if not arms:
        raise ExperimentOsError("a deployment runs at least one arm")
    version_arms = {
        a.arm_key: a
        for a in session.scalars(
            select(ExperimentArm).where(ExperimentArm.version_id == epoch.version_id)
        )
    }
    unknown = sorted(set(arms) - set(version_arms))
    if unknown:
        raise ExperimentOsError(
            f"arms {unknown} are not declared on this epoch's version"
        )
    if session.scalar(
        select(ExperimentDeployment).where(
            ExperimentDeployment.deployment_key == deployment_key
        )
    ):
        raise ExperimentOsError(f"deployment key {deployment_key!r} already exists")
    dep = ExperimentDeployment(
        epoch_id=epoch.id,
        deployment_key=deployment_key,
        stage=stage_v,
        kind=kind_v,
        twin_of_deployment_id=twin_of.id if twin_of is not None else None,
        config_json=config,
        code_fingerprint=code_fingerprint,
        started_at=started_at or _now(),
        notes=notes,
        created_at=_now(),
    )
    session.add(dep)
    session.flush()
    for arm_key, tag in arms.items():
        session.add(
            ExperimentDeploymentArm(
                deployment_id=dep.id,
                arm_id=version_arms[arm_key].id,
                strategy_tag=tag,
                created_at=_now(),
            )
        )
    session.flush()
    return dep


def end_deployment(
    session, deployment: ExperimentDeployment, *, ended_at: datetime | None = None
) -> ExperimentDeployment:
    if deployment.ended_at is not None:
        raise ExperimentOsError(f"deployment {deployment.deployment_key} already ended")
    deployment.ended_at = ended_at or _now()
    session.flush()
    return deployment


# ---------------------------------------------------------------------------
# Gates and gate results (spec §11)
# ---------------------------------------------------------------------------

_GATE_KINDS = {"promotion", "kill", "monitoring"}
_GATE_OPS = {">", ">=", "<", "<=", "==", "!="}
_CLAUSE_LIST_KEYS = ("pass_all", "fail_any", "hold_if")


def _validate_clause(clause, where: str) -> None:
    if not isinstance(clause, dict):
        raise GateSpecError(f"{where}: clause must be a mapping, got {type(clause).__name__}")
    metric = clause.get("metric")
    if not metric or not isinstance(metric, str):
        raise GateSpecError(f"{where}: clause needs a string 'metric'")
    op = clause.get("op")
    if op not in _GATE_OPS:
        raise GateSpecError(f"{where}: op {op!r} not in {sorted(_GATE_OPS)}")
    if not isinstance(clause.get("value"), (int, float)) or isinstance(
        clause.get("value"), bool
    ):
        raise GateSpecError(f"{where}: clause needs a numeric 'value'")


def validate_gate_spec(spec: dict) -> None:
    """Structural validation: the spec must be executable data, not prose (spec §11).

    Shape: {"sample": {arm_key: clause}, "pass_all": [clause...], "fail_any": [...],
    "hold_if": [...]} where a clause is {"metric", "op", "value", + optional arm /
    treatment / control keys}. At least one decision list is required."""
    if not isinstance(spec, dict):
        raise GateSpecError("gate spec must be a mapping")
    unknown = set(spec) - {"sample", *_CLAUSE_LIST_KEYS, "description"}
    if unknown:
        raise GateSpecError(f"unknown gate spec keys: {sorted(unknown)}")
    if not any(spec.get(k) for k in _CLAUSE_LIST_KEYS):
        raise GateSpecError(
            f"gate spec needs at least one decision list of {_CLAUSE_LIST_KEYS}"
        )
    sample = spec.get("sample")
    if sample is not None:
        if not isinstance(sample, dict):
            raise GateSpecError("'sample' must map arm_key → clause")
        for arm_key, clause in sample.items():
            _validate_clause(clause, f"sample[{arm_key}]")
    for key in _CLAUSE_LIST_KEYS:
        clauses = spec.get(key)
        if clauses is None:
            continue
        if not isinstance(clauses, list):
            raise GateSpecError(f"'{key}' must be a list of clauses")
        for i, clause in enumerate(clauses):
            _validate_clause(clause, f"{key}[{i}]")


def register_gate(
    session,
    version: ExperimentVersion,
    *,
    gate_key: str,
    kind: str,
    spec: dict,
    from_state: LifecycleState | str | None = None,
    to_state: LifecycleState | str | None = None,
    registered_at: datetime | None = None,
    notes: str | None = None,
) -> ExperimentGate:
    """Pre-register a structured gate on a version. The spec is hashed immediately —
    that hash is the immutable pre-registration."""
    if kind not in _GATE_KINDS:
        raise ExperimentOsError(f"gate kind {kind!r} not in {sorted(_GATE_KINDS)}")
    validate_gate_spec(spec)
    fs = LifecycleState(from_state).value if from_state is not None else None
    ts = LifecycleState(to_state).value if to_state is not None else None
    if kind == "promotion":
        if fs is None or ts is None:
            raise ExperimentOsError("a promotion gate names from_state and to_state")
        # The promotion a gate guards must itself be a legal forward move (approval is
        # a transition-time concern, not a registration-time one).
        validate_transition(fs, ts, approved_by="registration-shape-check")
    if version.frozen_at is not None:
        # Arms are final on a frozen version, so full scope validation runs at
        # registration (gates registered pre-freeze are validated by the freeze).
        from .evaluator import validate_gate_scopes  # lazy: evaluator imports service

        problems = validate_gate_scopes(session, version, spec)
        if problems:
            raise GateSpecError(
                f"gate {gate_key!r} does not resolve against the frozen arm set: "
                f"{'; '.join(problems)}"
            )
    gate = ExperimentGate(
        version_id=version.id,
        gate_key=gate_key,
        kind=kind,
        from_state=fs,
        to_state=ts,
        spec_json=spec,
        spec_hash=canonical_hash(spec),
        registered_at=registered_at or _now(),
        notes=notes,
        created_at=_now(),
    )
    session.add(gate)
    session.flush()
    return gate


def mark_gate_evidence_started(
    session, gate: ExperimentGate, *, at: datetime | None = None
) -> ExperimentGate:
    """One-way: eligible observations have begun; the gate is immutable from here."""
    if gate.evidence_started_at is not None:
        raise ExperimentOsError(
            f"gate {gate.gate_key} evidence already started at {gate.evidence_started_at}"
        )
    gate.evidence_started_at = at or _now()
    session.flush()
    return gate


def record_gate_result(
    session,
    gate: ExperimentGate,
    *,
    verdict: GateVerdict | str,
    computed_at: datetime | None = None,
    computed_by: str = "manual",
    epoch: ExperimentEpoch | None = None,
    evidence_start: datetime | None = None,
    evidence_end: datetime | None = None,
    sample: dict | None = None,
    metrics: dict | None = None,
    snapshot: PlatformSnapshot | None = None,
    metric_revision: str | None = None,
    evidence_ref: str | None = None,
    explanation: str | None = None,
) -> ExperimentGateResult:
    """Record one immutable gate evaluation (manually computed in the foundation
    release; the structured evaluator writes the same rows later)."""
    v = GateVerdict(verdict)
    version = session.get(ExperimentVersion, gate.version_id)
    if epoch is not None and epoch.version_id != version.id:
        raise ExperimentOsError("gate result epoch belongs to a different version")
    result = ExperimentGateResult(
        gate_id=gate.id,
        experiment_id=version.experiment_id,
        version_id=version.id,
        epoch_id=epoch.id if epoch is not None else None,
        verdict=v.value,
        evidence_start=evidence_start,
        evidence_end=evidence_end,
        sample_json=sample,
        metrics_json=metrics,
        platform_snapshot_id=(
            snapshot.id
            if snapshot is not None
            else (epoch.platform_snapshot_id if epoch is not None else None)
        ),
        metric_revision=metric_revision,
        computed_at=computed_at or _now(),
        computed_by=computed_by,
        evidence_ref=evidence_ref,
        explanation=explanation,
        created_at=_now(),
    )
    session.add(result)
    session.flush()
    return result


# ---------------------------------------------------------------------------
# Lifecycle transitions (spec §7–8, §27)
# ---------------------------------------------------------------------------

# Stage entries with hard structural prerequisites from day one. Promotions into real
# money additionally require a PASS gate result — no transition on a good-looking
# number alone.
_NEEDS_PASS_RESULT = {LifecycleState.LIVE_CANARY.value, LifecycleState.PRODUCTION.value}


def _validate_promotion_result(
    session,
    experiment: Experiment,
    current: LifecycleState,
    target: LifecycleState,
    gate_result: ExperimentGateResult,
) -> None:
    """A PASS is not a reusable permission slip. Before a result may authorize a
    real-money promotion it must be THE result for THIS promotion:

      * from the promotion gate registered for exactly (current → target);
      * on the experiment's CURRENT version;
      * bound to the CURRENT operating epoch (a NULL-epoch result never promotes);
      * carrying that epoch's pinned platform snapshot, which must still BE the
        active snapshot (a platform change since pinning stales the epoch);
      * the LATEST result recorded for that gate — a newer FAIL/HOLD/BLOCKED
        invalidates an older PASS, which can never be cherry-picked back.
    """
    if gate_result.experiment_id != experiment.id:
        raise ExperimentOsError("gate result belongs to a different experiment")
    if gate_result.verdict != GateVerdict.PASS.value:
        raise ExperimentOsError(
            f"gate result verdict is {gate_result.verdict}, not PASS — a gate "
            "verdict cannot be argued past"
        )
    gate = session.get(ExperimentGate, gate_result.gate_id)
    if (
        gate.kind != "promotion"
        or gate.from_state != current.value
        or gate.to_state != target.value
    ):
        raise ExperimentOsError(
            f"result is from gate {gate.gate_key!r} ({gate.kind} "
            f"{gate.from_state}→{gate.to_state}), not the promotion gate for "
            f"{current.value} → {target.value}"
        )
    version = session.scalar(
        select(ExperimentVersion)
        .where(ExperimentVersion.experiment_id == experiment.id)
        .order_by(ExperimentVersion.version.desc())
        .limit(1)
    )
    if version is None or gate.version_id != version.id:
        raise ExperimentOsError(
            "result is not from the experiment's CURRENT version — a stale "
            "version's PASS never authorizes a current promotion"
        )
    epoch = session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == version.id,
            ExperimentEpoch.ended_at.is_(None),
        )
    ) or session.scalar(
        select(ExperimentEpoch)
        .where(ExperimentEpoch.version_id == version.id)
        .order_by(ExperimentEpoch.epoch_number.desc())
        .limit(1)
    )
    if epoch is None:
        raise ExperimentOsError("the current version has no operating epoch")
    if gate_result.epoch_id is None:
        raise ExperimentOsError(
            "the result is not bound to an epoch — an epoch-less PASS never "
            "authorizes a promotion"
        )
    if gate_result.epoch_id != epoch.id:
        raise ExperimentOsError(
            f"result is bound to epoch #{gate_result.epoch_id}, not the current "
            f"operating epoch (#{epoch.id}) — stale-epoch evidence never promotes"
        )
    if gate_result.platform_snapshot_id != epoch.platform_snapshot_id:
        raise ExperimentOsError(
            "result was computed under a different platform snapshot than the "
            "epoch pins — re-evaluate under the pinned snapshot"
        )
    active = get_active_platform_snapshot(session)
    if active is None or active.id != epoch.platform_snapshot_id:
        raise ExperimentOsError(
            "the platform has changed since this epoch pinned its snapshot — "
            "classify the impact / open a new epoch and re-evaluate before promoting"
        )
    newest = session.scalar(
        select(ExperimentGateResult)
        .where(ExperimentGateResult.gate_id == gate.id)
        .order_by(
            ExperimentGateResult.computed_at.desc(), ExperimentGateResult.id.desc()
        )
        .limit(1)
    )
    if newest is not None and newest.id != gate_result.id:
        raise ExperimentOsError(
            f"result #{gate_result.id} has been superseded by result #{newest.id} "
            f"({newest.verdict}) — only the gate's latest result can authorize"
        )


def transition_experiment(
    session,
    experiment: Experiment,
    to_state: LifecycleState | str,
    *,
    actor: str,
    reason: str | None = None,
    approved_by: str | None = None,
    gate_result: ExperimentGateResult | None = None,
    occurred_at: datetime | None = None,
    version: ExperimentVersion | None = None,
    epoch: ExperimentEpoch | None = None,
) -> ExperimentStateTransition:
    """Validate, apply, and audit one lifecycle transition. Never automated: callers
    are operators/scripts/tests, not the trading loop (foundation release)."""
    target = LifecycleState(to_state)
    current = LifecycleState(experiment.state)
    validate_transition(
        current,
        target,
        paused_from=experiment.paused_from_state,
        approved_by=approved_by,
    )

    if target is LifecycleState.PAPER:
        frozen = session.scalar(
            select(ExperimentVersion).where(
                ExperimentVersion.experiment_id == experiment.id,
                ExperimentVersion.frozen_at.is_not(None),
            )
        )
        if frozen is None and current is not LifecycleState.PAUSED:
            raise ExperimentOsError(
                "PAPER entry requires a frozen Experiment Version (arms, contract and "
                "pre-registration locked)"
            )
    if target.value in _NEEDS_PASS_RESULT and current is not LifecycleState.PAUSED:
        if gate_result is None:
            raise ExperimentOsError(
                f"{current.value} → {target.value} requires the PASS gate result that "
                "justified it (record_gate_result first)"
            )
        _validate_promotion_result(session, experiment, current, target, gate_result)

    at = occurred_at or _now()
    row = ExperimentStateTransition(
        experiment_id=experiment.id,
        from_state=current.value,
        to_state=target.value,
        occurred_at=at,
        actor=actor,
        approved_by=approved_by,
        reason=reason,
        gate_result_id=gate_result.id if gate_result is not None else None,
        version_id=version.id if version is not None else None,
        epoch_id=epoch.id if epoch is not None else None,
        created_at=at,
    )
    session.add(row)
    if target is LifecycleState.PAUSED:
        experiment.paused_from_state = current.value
    else:
        experiment.paused_from_state = None
    if target is LifecycleState.RETIRED:
        experiment.retired_at = at
    experiment.state = target.value
    experiment.updated_at = at
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Integrity events and legacy evidence
# ---------------------------------------------------------------------------


def record_integrity_event(
    session,
    experiment: Experiment,
    *,
    kind: str,
    description: str | None = None,
    severity: str = "warning",
    detected_at: datetime | None = None,
    version: ExperimentVersion | None = None,
    epoch: ExperimentEpoch | None = None,
    deployment: ExperimentDeployment | None = None,
    details: dict | None = None,
) -> ExperimentIntegrityEvent:
    ev = ExperimentIntegrityEvent(
        experiment_id=experiment.id,
        version_id=version.id if version is not None else None,
        epoch_id=epoch.id if epoch is not None else None,
        deployment_id=deployment.id if deployment is not None else None,
        kind=kind,
        severity=severity,
        detected_at=detected_at or _now(),
        description=description,
        details_json=details,
        created_at=_now(),
    )
    session.add(ev)
    session.flush()
    return ev


def attach_legacy_evidence(
    session,
    experiment: Experiment,
    *,
    label: str,
    evidence_class: EvidenceClass | str,
    source: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    summary: dict | None = None,
    version: ExperimentVersion | None = None,
    notes: str | None = None,
) -> ExperimentLegacyEvidence:
    """Attach historical performance exactly as recorded. CONTEXT_ONLY evidence can
    never satisfy a clean promotion gate (the evaluator enforces the class later; the
    class itself is stamped here and immutable)."""
    ec = EvidenceClass(evidence_class)
    ev = ExperimentLegacyEvidence(
        experiment_id=experiment.id,
        version_id=version.id if version is not None else None,
        label=label,
        evidence_class=ec.value,
        source=source,
        window_start=window_start,
        window_end=window_end,
        summary_json=summary,
        notes=notes,
        created_at=_now(),
    )
    session.add(ev)
    session.flush()
    return ev
