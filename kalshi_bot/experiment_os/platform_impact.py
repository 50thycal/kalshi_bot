"""Platform Change Impact Engine (spec §17–18) — shared platform changes become
explicit, reviewable research-boundary events instead of silent environmental drift.

The canonical workflow:

    register_platform_revision(...)                # pending
      → affected_experiments(revision)             # discovery from pinned snapshots
      → propose_impact(...) per affected experiment  # I0–I4 + required action
      → accept_impact(...)                         # review: classification freezes
      → service.activate_platform_revision(...)    # REFUSES while any affected
                                                   #   ACTIVE experiment is
                                                   #   unaccounted, or a blocking
                                                   #   (I4) disposition is unapplied
                                                   #   — unless explicitly forced
      → apply_no_action / apply_recompute /        # the mechanically safe
        apply_new_epoch / apply_new_version /      #   applications, each recording
        apply_pause / apply_retire                 #   what it produced

Class → action coherence is validated at proposal AND acceptance:

    I0  observability-only         → NO_ACTION
    I1  exactly normalizable       → RECOMPUTE, and the normalizer must EXIST and
                                     be NAMED (normalization_ref, validated against
                                     the metric registry) — never a magic boolean
    I2  sample/environment boundary→ NEW_EPOCH (or PAUSE/RETIRE)
    I3  scientific contract change → NEW_EXPERIMENT_VERSION (or PAUSE/RETIRE);
                                     an I3 may NOT masquerade as a new epoch
    I4  safety/invalidation        → PAUSE or RETIRE; blocks activation until
                                     APPLIED (not merely accepted)

What this engine never does: fabricate a successor scientific contract (I3 blocks
until the researcher authors and freezes one), substitute a convenient timestamp
for an unmeasured activation boundary (NEW_EPOCH refuses until the boundary is
established), mutate old gate results (staleness is structural: PR 3 binds results
to epoch+snapshot; PR 4 binds promotions to fresh re-evaluation), or promote
anything to real money.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from ..models import SystemEvent
from .lifecycle import ImpactAction, ImpactClass, LifecycleState
from .metrics import METRICS_ENGINE_REVISION, resolve_definition
from .models import (
    Experiment,
    ExperimentEpoch,
    ExperimentIntegrityEvent,
    ExperimentVersion,
    PlatformComponent,
    PlatformImpactAction,
    PlatformRevision,
    PlatformSnapshotItem,
)
from .service import (
    ExperimentOsError,
    carry_deployments_forward,
    close_epoch,
    open_deployments,
    open_epoch,
    resolve_active_platform_snapshot,
    transition_experiment,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime) -> datetime:
    """sqlite drops tzinfo — normalize before comparisons."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# Coherent action sets per impact class (spec §18). PAUSE/RETIRE are legal
# protective escalations for any material class.
_CLASS_ACTIONS: dict[str, frozenset[str]] = {
    ImpactClass.I0_OBSERVABILITY_ONLY.value: frozenset({ImpactAction.NO_ACTION.value}),
    ImpactClass.I1_EXACTLY_NORMALIZABLE.value: frozenset(
        {ImpactAction.RECOMPUTE.value, ImpactAction.PAUSE.value, ImpactAction.RETIRE.value}
    ),
    ImpactClass.I2_SAMPLE_BOUNDARY.value: frozenset(
        {ImpactAction.NEW_EPOCH.value, ImpactAction.PAUSE.value, ImpactAction.RETIRE.value}
    ),
    ImpactClass.I3_EXPERIMENT_DEFINITION.value: frozenset(
        {
            ImpactAction.NEW_EXPERIMENT_VERSION.value,
            ImpactAction.PAUSE.value,
            ImpactAction.RETIRE.value,
        }
    ),
    ImpactClass.I4_SAFETY_CRITICAL.value: frozenset(
        {ImpactAction.PAUSE.value, ImpactAction.RETIRE.value}
    ),
}

# Dispositions that leave evidence uninterpretable until APPLIED. NO_ACTION is
# resolved by acceptance itself; everything else changes the world (or the
# numbers) and blocks clean accumulation while pending.
_BLOCKING_WHEN_ACCEPTED = frozenset(
    {
        ImpactAction.RECOMPUTE.value,
        ImpactAction.NEW_EPOCH.value,
        ImpactAction.NEW_EXPERIMENT_VERSION.value,
        ImpactAction.PAUSE.value,
        ImpactAction.RETIRE.value,
    }
)


def component_of(session, revision: PlatformRevision) -> PlatformComponent:
    return session.get(PlatformComponent, revision.component_id)


def superseded_revision(session, revision: PlatformRevision) -> PlatformRevision | None:
    """The revision this one replaces: the component's currently-active revision
    while `revision` is pending; after activation, the one retired at its boundary."""
    if revision.status == "pending":
        return session.scalar(
            select(PlatformRevision).where(
                PlatformRevision.component_id == revision.component_id,
                PlatformRevision.status == "active",
                PlatformRevision.id != revision.id,
            )
        )
    return session.scalar(
        select(PlatformRevision)
        .where(
            PlatformRevision.component_id == revision.component_id,
            PlatformRevision.status == "retired",
            PlatformRevision.id != revision.id,
        )
        .order_by(PlatformRevision.created_at.desc())
        .limit(1)
    )


# ---------------------------------------------------------------------------
# Affected-experiment discovery (from pinned snapshots, never from docs)
# ---------------------------------------------------------------------------


def _current_epoch(session, experiment: Experiment) -> ExperimentEpoch | None:
    """The experiment's current operating interval: the open epoch of its latest
    version, else the latest closed epoch of that version, else None."""
    version = session.scalar(
        select(ExperimentVersion)
        .where(ExperimentVersion.experiment_id == experiment.id)
        .order_by(ExperimentVersion.version.desc())
        .limit(1)
    )
    if version is None:
        return None
    epoch = session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == version.id,
            ExperimentEpoch.ended_at.is_(None),
        )
    )
    if epoch is not None:
        return epoch
    return session.scalar(
        select(ExperimentEpoch)
        .where(ExperimentEpoch.version_id == version.id)
        .order_by(ExperimentEpoch.epoch_number.desc())
        .limit(1)
    )


def _pinned_revision_id(session, epoch: ExperimentEpoch, component_id: int) -> int | None:
    return session.scalar(
        select(PlatformSnapshotItem.revision_id).where(
            PlatformSnapshotItem.snapshot_id == epoch.platform_snapshot_id,
            PlatformSnapshotItem.component_id == component_id,
        )
    )


def affected_experiments(session, revision: PlatformRevision) -> dict:
    """Who depends on this component right now — resolved from pinned platform
    snapshots and epoch lineage, never by scanning strategy docs.

    Categories:
      active_on_superseded — active experiments whose CURRENT epoch pins a
                             different revision of this component: the set that
                             must be accounted for before activation;
      already_on_new       — current epoch already pins THIS revision;
      no_pinned_epoch      — active but pre-epoch (or legacy without epochs):
                             nothing pinned, nothing to invalidate — they will
                             inherit the new snapshot at their first/next epoch;
      retired              — historical only; never blocks activation.
    """
    out: dict[str, list[dict]] = {
        "active_on_superseded": [],
        "already_on_new": [],
        "no_pinned_epoch": [],
        "retired": [],
    }
    for exp in session.scalars(select(Experiment).order_by(Experiment.key)):
        entry: dict = {
            "experiment": exp,
            "key": exp.key,
            "state": exp.state,
            "legacy": exp.legacy_class,  # None ⇒ native
        }
        if exp.state == LifecycleState.RETIRED.value:
            out["retired"].append(entry)
            continue
        epoch = _current_epoch(session, exp)
        if epoch is None:
            out["no_pinned_epoch"].append(entry)
            continue
        pinned = _pinned_revision_id(session, epoch, revision.component_id)
        entry.update(
            {
                "version_id": epoch.version_id,
                "epoch_id": epoch.id,
                "epoch_number": epoch.epoch_number,
                "snapshot_id": epoch.platform_snapshot_id,
                "pinned_revision_id": pinned,
            }
        )
        if pinned == revision.id:
            out["already_on_new"].append(entry)
        elif pinned is None:
            # The component was registered after this epoch's snapshot: the
            # snapshot predates the component entirely. Treat as affected — the
            # comparability question still needs an explicit answer.
            out["active_on_superseded"].append(entry)
        else:
            out["active_on_superseded"].append(entry)
    return out


# ---------------------------------------------------------------------------
# Classification records
# ---------------------------------------------------------------------------


def _validate_classification(
    impact_class: str, action: str, rationale: str | None, normalization_ref: str | None
) -> None:
    allowed = _CLASS_ACTIONS.get(impact_class)
    if allowed is None:
        raise ExperimentOsError(f"unknown impact class {impact_class!r}")
    if action not in allowed:
        extra = ""
        if (
            impact_class == ImpactClass.I3_EXPERIMENT_DEFINITION.value
            and action == ImpactAction.NEW_EPOCH.value
        ):
            extra = (
                " — an I3 changes WHAT is being tested, not just the world around "
                "it; it may not masquerade as a new epoch"
            )
        raise ExperimentOsError(
            f"impact class {impact_class} does not admit action {action} "
            f"(allowed: {sorted(allowed)}){extra}"
        )
    if not (rationale or "").strip():
        raise ExperimentOsError(
            "an impact decision requires a rationale — no silent 'probably unaffected'"
        )
    needs_normalizer = action == ImpactAction.RECOMPUTE.value
    if needs_normalizer:
        if not normalization_ref:
            raise ExperimentOsError(
                "I1/RECOMPUTE requires normalization_ref naming the registered "
                "normalizer (e.g. 'metric:<key>' or 'metrics_engine:<revision>') — "
                "normalization_available is not permission; the normalizer must exist"
            )
        _validate_normalizer(normalization_ref)
    elif normalization_ref:
        _validate_normalizer(normalization_ref)


def _validate_normalizer(ref: str) -> None:
    """A normalization reference must name something that actually exists."""
    kind, _, name = ref.partition(":")
    if kind == "metric" and name:
        if resolve_definition(name) is None:
            raise ExperimentOsError(
                f"normalization_ref names metric {name!r}, which is not in the "
                "canonical metric registry — register the normalizing metric first"
            )
        return
    if kind == "metrics_engine" and name:
        if ref != METRICS_ENGINE_REVISION:
            raise ExperimentOsError(
                f"normalization_ref {ref!r} does not match the current metrics "
                f"engine revision ({METRICS_ENGINE_REVISION}) — the normalizer must "
                "be the code that will actually run"
            )
        return
    raise ExperimentOsError(
        f"normalization_ref {ref!r} is not a recognized reference "
        "('metric:<key>' or 'metrics_engine:<revision>')"
    )


def propose_impact(
    session,
    revision: PlatformRevision,
    experiment: Experiment,
    *,
    impact_class: ImpactClass | str,
    action: ImpactAction | str,
    rationale: str,
    decided_by: str,
    normalization_ref: str | None = None,
    blocks_activation: bool | None = None,
    details: dict | None = None,
    decided_at: datetime | None = None,
) -> PlatformImpactAction:
    """Record one experiment's proposed disposition for one platform revision.

    Discovery may PROPOSE candidates automatically; classification itself is
    always an explicit, validated, attributable decision — never invented."""
    ic = ImpactClass(impact_class).value
    act = ImpactAction(action).value
    _validate_classification(ic, act, rationale, normalization_ref)
    if experiment.state == LifecycleState.RETIRED.value:
        raise ExperimentOsError(
            f"experiment {experiment.key} is RETIRED — its evidence is history and "
            "needs no disposition (retired experiments never block activation)"
        )
    if revision.status == "retired":
        raise ExperimentOsError(
            f"revision {revision.id} is retired — classify against the revision "
            "actually being introduced"
        )
    existing = session.scalar(
        select(PlatformImpactAction).where(
            PlatformImpactAction.revision_id == revision.id,
            PlatformImpactAction.experiment_id == experiment.id,
        )
    )
    if existing is not None:
        raise ExperimentOsError(
            f"experiment {experiment.key} already has an impact record "
            f"(#{existing.id}, {existing.status}) for this revision — withdraw the "
            "proposal or record a new decision instead of overwriting"
        )
    epoch = _current_epoch(session, experiment)
    prior = superseded_revision(session, revision)
    if blocks_activation is None:
        blocks_activation = ic == ImpactClass.I4_SAFETY_CRITICAL.value
    record = PlatformImpactAction(
        revision_id=revision.id,
        superseded_revision_id=prior.id if prior is not None else None,
        prior_snapshot_id=epoch.platform_snapshot_id if epoch is not None else None,
        experiment_id=experiment.id,
        version_id=epoch.version_id if epoch is not None else None,
        epoch_id=epoch.id if epoch is not None else None,
        impact_class=ic,
        action=act,
        status="proposed",
        rationale=rationale,
        normalization_ref=normalization_ref,
        decided_by=decided_by,
        decided_at=decided_at or _now(),
        blocks_activation=blocks_activation,
        details_json=details,
        created_at=_now(),
    )
    session.add(record)
    session.flush()
    return record


def accept_impact(
    session, record: PlatformImpactAction, *, accepted_by: str, at: datetime | None = None
) -> PlatformImpactAction:
    """Review sign-off: the classification freezes (flush-guard enforced).
    An accepted NO_ACTION is complete in itself and settles as applied."""
    if record.status != "proposed":
        raise ExperimentOsError(
            f"impact record #{record.id} is {record.status}, not proposed"
        )
    _validate_classification(
        record.impact_class, record.action, record.rationale, record.normalization_ref
    )
    record.status = "accepted"
    record.accepted_by = accepted_by
    record.accepted_at = at or _now()
    if record.action == ImpactAction.NO_ACTION.value:
        record.status = "applied"
        record.applied_by = accepted_by
        record.applied_at = record.accepted_at
    session.flush()
    return record


def exempt_impact(
    session, record: PlatformImpactAction, *, actor: str, reason: str
) -> PlatformImpactAction:
    """Explicitly decline to act, with rationale — the audited escape hatch that
    keeps 'we chose not to handle this here' distinct from 'nobody looked'."""
    if record.status not in ("proposed", "accepted"):
        raise ExperimentOsError(f"impact record #{record.id} is already {record.status}")
    if not (reason or "").strip():
        raise ExperimentOsError("an exemption requires a reason")
    record.status = "exempted"
    record.applied_by = actor
    record.applied_at = _now()
    record.details_json = {**(record.details_json or {}), "exemption_reason": reason}
    session.flush()
    return record


# ---------------------------------------------------------------------------
# Activation gate (called by service.activate_platform_revision)
# ---------------------------------------------------------------------------


def activation_gate(session, revision: PlatformRevision) -> dict:
    """Is it safe to activate? Every affected ACTIVE experiment needs an
    accepted-or-beyond disposition, and every blocks_activation record (I4)
    must already be APPLIED."""
    affected = affected_experiments(session, revision)
    records = {
        r.experiment_id: r
        for r in session.scalars(
            select(PlatformImpactAction).where(
                PlatformImpactAction.revision_id == revision.id
            )
        )
    }
    unaccounted: list[str] = []
    for entry in affected["active_on_superseded"]:
        rec = records.get(entry["experiment"].id)
        if rec is None or rec.status == "proposed":
            unaccounted.append(entry["key"])
    blocking_unapplied = [
        f"#{r.id} {r.impact_class}/{r.action} on experiment_id={r.experiment_id}"
        for r in records.values()
        if r.blocks_activation and r.status not in ("applied", "exempted")
    ]
    return {
        "affected_active": [e["key"] for e in affected["active_on_superseded"]],
        "already_on_new": [e["key"] for e in affected["already_on_new"]],
        "retired_ignored": [e["key"] for e in affected["retired"]],
        "unaccounted": unaccounted,
        "blocking_unapplied": blocking_unapplied,
        "safe": not unaccounted and not blocking_unapplied,
    }


def record_forced_activation(
    session, revision: PlatformRevision, gate: dict, *, actor: str, reason: str
) -> None:
    """A forced activation is a durable, attributable override: a system event,
    plus one UNRESOLVED integrity event per unaccounted experiment — which keeps
    each of them gate-blocked until someone actually classifies the impact."""
    comp = component_of(session, revision)
    session.add(
        SystemEvent(
            created_at=_now(), level="error", component="experiment_os_platform",
            message=(
                f"platform revision {comp.key}:{revision.version} activation FORCED "
                f"by {actor} past {len(gate['unaccounted'])} unaccounted experiment(s) "
                f"and {len(gate['blocking_unapplied'])} unapplied blocking "
                f"disposition(s): {reason}"
            ),
            raw_json={"revision_id": revision.id, **{k: v for k, v in gate.items()
                                                     if k != "safe"}},
        )
    )
    for key in gate["unaccounted"]:
        exp = session.scalar(select(Experiment).where(Experiment.key == key))
        if exp is None:
            continue
        session.add(
            ExperimentIntegrityEvent(
                experiment_id=exp.id,
                kind="PLATFORM_ACTIVATION_FORCED",
                severity="error",
                detected_at=_now(),
                description=(
                    f"platform revision {comp.key}:{revision.version} was activated "
                    f"by {actor} WITHOUT an accepted impact disposition for this "
                    f"experiment ({reason}). Classify the impact "
                    "(propose_impact/accept_impact) and resolve this event; gate "
                    "evaluation is blocked meanwhile."
                ),
                details_json={"revision_id": revision.id},
                created_at=_now(),
            )
        )
    session.flush()


# ---------------------------------------------------------------------------
# Applying accepted dispositions (the mechanically safe part)
# ---------------------------------------------------------------------------


def _require_accepted(record: PlatformImpactAction, action: ImpactAction) -> None:
    if record.action != action.value:
        raise ExperimentOsError(
            f"impact record #{record.id} prescribes {record.action}, not {action.value}"
        )
    if record.status != "accepted":
        raise ExperimentOsError(
            f"impact record #{record.id} is {record.status} — only an ACCEPTED "
            "disposition may be applied"
        )


def _mark_applied(session, record: PlatformImpactAction, actor: str,
                  at: datetime | None = None) -> PlatformImpactAction:
    record.status = "applied"
    record.applied_by = actor
    record.applied_at = at or _now()
    session.flush()
    return record


def apply_recompute(
    session, record: PlatformImpactAction, *, actor: str
) -> PlatformImpactAction:
    """I1: the named normalizer licenses comparability. Nothing historical is
    mutated — prior gate results simply cannot authorize anything (PR 3 binds
    them to their epoch/snapshot; PR 4 requires a fresh evaluator run on the
    current metric revision at promotion). Applying records that the
    re-evaluation route is the only valid one from here."""
    _require_accepted(record, ImpactAction.RECOMPUTE)
    _validate_normalizer(record.normalization_ref or "")
    record.details_json = {
        **(record.details_json or {}),
        "recompute": {
            "normalizer": record.normalization_ref,
            "metrics_engine": METRICS_ENGINE_REVISION,
            "note": (
                "prior gate results remain immutable history; any authorization "
                "requires a fresh evaluation under the named normalizer"
            ),
        },
    }
    return _mark_applied(session, record, actor)


def apply_new_epoch(
    session,
    record: PlatformImpactAction,
    *,
    actor: str,
    boundary: datetime | None = None,
) -> ExperimentEpoch:
    """I2: close the current epoch at the MEASURED activation boundary and open a
    fresh one (same scientific contract) pinned to the new snapshot. Refuses an
    unestablished boundary — unknown means unknown, and evidence in the gap stays
    BLOCKED_PLATFORM until the boundary is measured."""
    _require_accepted(record, ImpactAction.NEW_EPOCH)
    revision = session.get(PlatformRevision, record.revision_id)
    if revision.status != "active":
        raise ExperimentOsError(
            f"revision {revision.id} is {revision.status} — activate it first so the "
            "new epoch can pin a snapshot that includes it"
        )
    measured = revision.activated_at
    if boundary is not None and measured is not None and _utc(boundary) != _utc(measured):
        raise ExperimentOsError(
            f"explicit boundary {boundary} contradicts the revision's measured "
            f"activation boundary {measured}"
        )
    at = measured or boundary
    if at is None:
        raise ExperimentOsError(
            "the activation boundary is UNESTABLISHED — establish_activation_boundary() "
            "from measurement first; no convenient timestamp is substituted"
        )
    experiment = session.get(Experiment, record.experiment_id)
    version = session.scalar(
        select(ExperimentVersion)
        .where(ExperimentVersion.experiment_id == experiment.id)
        .order_by(ExperimentVersion.version.desc())
        .limit(1)
    )
    if version is None or version.frozen_at is None:
        raise ExperimentOsError(
            f"experiment {experiment.key} has no frozen current version to re-epoch"
        )
    open_prev = session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == version.id,
            ExperimentEpoch.ended_at.is_(None),
        )
    )
    # Captured BEFORE the close, because closing an epoch ends the deployments in it.
    # These are the books that must keep running on the far side of the boundary: an
    # I2 cut means "same contract, fresh evidence", not "stop trading".
    continuing: list = []
    if open_prev is not None:
        if _utc(at) < _utc(open_prev.started_at):
            raise ExperimentOsError(
                f"boundary {at} predates the open epoch's start "
                f"({open_prev.started_at}) — that would create a negative interval; "
                "check which revision actually governed this epoch"
            )
        continuing = open_deployments(session, open_prev)
        close_epoch(session, open_prev, ended_at=at)
    comp = component_of(session, revision)
    snapshot = resolve_active_platform_snapshot(session)
    new_epoch = open_epoch(
        session,
        version,
        reason=(
            f"platform boundary: {comp.key} {revision.version} activated at {at} "
            f"(impact record #{record.id}: {record.rationale})"
        ),
        started_at=at,
        snapshot=snapshot,
        impact_class=record.impact_class,
    )
    pinned = _pinned_revision_id(session, new_epoch, revision.component_id)
    if pinned != revision.id:
        raise ExperimentOsError(
            f"the resolved active snapshot does not pin {comp.key}:{revision.version} "
            "— the platform registry is inconsistent; refusing to open a stale epoch"
        )
    # The books continue under the new interval. Without this the successor epoch
    # opens EMPTY, every tag on the old deployments stops resolving, and the whole
    # experiment goes dark at the moment a platform revision is applied — which is
    # exactly what happened to mmsell-type-tight on 2026-08-24 (XOS-000011).
    carry_deployments_forward(
        session, continuing, new_epoch,
        started_at=at,
        reason=f"{comp.key} {revision.version} boundary (impact record #{record.id})",
    )
    record.resulting_epoch_id = new_epoch.id
    _mark_applied(session, record, actor)
    return new_epoch


def apply_new_version(
    session,
    record: PlatformImpactAction,
    *,
    successor: ExperimentVersion,
    actor: str,
) -> PlatformImpactAction:
    """I3: the change altered WHAT is being tested. The engine never fabricates
    the new scientific contract — the researcher authors and freezes the
    successor version; this records that the contract landed. Old-version gate
    results cannot authorize the successor (PR 3 binds results to their version)."""
    _require_accepted(record, ImpactAction.NEW_EXPERIMENT_VERSION)
    if successor.experiment_id != record.experiment_id:
        raise ExperimentOsError(
            f"successor version {successor.id} belongs to a different experiment"
        )
    if successor.frozen_at is None:
        raise ExperimentOsError(
            "the successor version must be FROZEN — an unfrozen contract is still "
            "a draft, not a disposition"
        )
    if record.version_id is not None:
        prior = session.get(ExperimentVersion, record.version_id)
        if prior is not None and successor.version <= prior.version:
            raise ExperimentOsError(
                f"successor version v{successor.version} does not succeed the "
                f"impacted version v{prior.version}"
            )
    record.resulting_version_id = successor.id
    return _mark_applied(session, record, actor)


def apply_pause(
    session, record: PlatformImpactAction, *, actor: str, approved_by: str | None = None
) -> PlatformImpactAction:
    """I4 (or protective I2/I3): audited lifecycle pause."""
    _require_accepted(record, ImpactAction.PAUSE)
    experiment = session.get(Experiment, record.experiment_id)
    revision = session.get(PlatformRevision, record.revision_id)
    comp = component_of(session, revision)
    transition_experiment(
        session,
        experiment,
        LifecycleState.PAUSED,
        actor=actor,
        approved_by=approved_by,
        reason=(
            f"platform impact #{record.id}: {comp.key}:{revision.version} — "
            f"{record.rationale}"
        ),
    )
    record.details_json = {**(record.details_json or {}),
                           "paused_from": experiment.paused_from_state}
    return _mark_applied(session, record, actor)


def apply_retire(
    session, record: PlatformImpactAction, *, actor: str, approved_by: str | None = None
) -> PlatformImpactAction:
    """I4 terminal: audited retirement with the platform rationale preserved."""
    _require_accepted(record, ImpactAction.RETIRE)
    experiment = session.get(Experiment, record.experiment_id)
    revision = session.get(PlatformRevision, record.revision_id)
    comp = component_of(session, revision)
    transition_experiment(
        session,
        experiment,
        LifecycleState.RETIRED,
        actor=actor,
        approved_by=approved_by,
        reason=(
            f"platform impact #{record.id}: {comp.key}:{revision.version} — "
            f"{record.rationale}"
        ),
    )
    return _mark_applied(session, record, actor)


# ---------------------------------------------------------------------------
# Evidence blocking (evaluator integration) + drift classification
# ---------------------------------------------------------------------------


def licensed_revision_ids(session, experiment_id: int | None) -> set[int]:
    """Revisions whose introduction this experiment has APPLIED an I0/NO_ACTION or
    I1/RECOMPUTE disposition for — the comparability license of §18.

    NO_ACTION says the change was judged immaterial to this experiment; an applied
    RECOMPUTE says a named, registered normalizer restores exactness. Either way
    the pinned-vs-active snapshot difference for that revision is accounted for.
    Nothing else licenses anything: a PROPOSED record is an open question, an
    ACCEPTED-but-unapplied material action is unfinished work, and an EXEMPTED
    record is an audited refusal to act — declining to act is not a comparability
    claim."""
    if experiment_id is None:
        return set()
    return set(
        session.scalars(
            select(PlatformImpactAction.revision_id).where(
                PlatformImpactAction.experiment_id == experiment_id,
                PlatformImpactAction.status == "applied",
                PlatformImpactAction.action.in_(
                    (ImpactAction.NO_ACTION.value, ImpactAction.RECOMPUTE.value)
                ),
            )
        )
    )


def _licensed_transitions(session, experiment_id: int | None) -> set[frozenset[int]]:
    """The component TRANSITIONS this experiment has licensed, as unordered
    {new revision, superseded revision} pairs.

    `licensed_revision_ids` answers "is arriving at R accounted for?", which is the
    pinned-vs-active question. Comparing two epochs asks a different one: is the
    difference BETWEEN two pins accounted for? An applied disposition records both
    ends (`revision_id` + `superseded_revision_id`), so it answers that exactly —
    and only for the single step it names. A record with no recorded predecessor
    licenses no transition: a multi-step drift (r1 → r2 → r3) is not covered by a
    disposition about one of its steps, and is refused rather than assumed."""
    if experiment_id is None:
        return set()
    rows = session.execute(
        select(
            PlatformImpactAction.revision_id,
            PlatformImpactAction.superseded_revision_id,
        ).where(
            PlatformImpactAction.experiment_id == experiment_id,
            PlatformImpactAction.status == "applied",
            PlatformImpactAction.action.in_(
                (ImpactAction.NO_ACTION.value, ImpactAction.RECOMPUTE.value)
            ),
        )
    ).all()
    return {
        frozenset((rev_id, prior_id))
        for rev_id, prior_id in rows
        if prior_id is not None and rev_id is not None
    }


def _snapshot_pins(session, snapshot_id: int | None) -> dict[str, tuple[int, str]]:
    """{component key: (revision id, revision version)} pinned by one snapshot."""
    if snapshot_id is None:
        return {}
    rows = session.execute(
        select(PlatformComponent.key, PlatformRevision.id, PlatformRevision.version)
        .join(
            PlatformSnapshotItem,
            PlatformSnapshotItem.component_id == PlatformComponent.id,
        )
        .join(PlatformRevision, PlatformRevision.id == PlatformSnapshotItem.revision_id)
        .where(PlatformSnapshotItem.snapshot_id == snapshot_id)
    ).all()
    return {key: (rev_id, version) for key, rev_id, version in rows}


def _experiment_id_of_epoch(session, epoch: ExperimentEpoch | None) -> int | None:
    if epoch is None:
        return None
    version = session.get(ExperimentVersion, epoch.version_id)
    return version.experiment_id if version is not None else None


def cross_snapshot_block_reason(
    session, epoch: ExperimentEpoch | None, other: ExperimentEpoch | None
) -> str | None:
    """Why a delta between two differently-pinned epochs cannot be interpreted —
    `None` when it can.

    ## The composition rule (DEC: cross-scope comparability)

    > A cross-scope delta is comparable when, for EVERY component whose pinned
    > revision differs between the two epochs' snapshots, BOTH experiments hold an
    > APPLIED NO_ACTION or RECOMPUTE disposition for that exact transition.

    Two independent I0s do not compose automatically, and this function does not
    assume they do — it is the explicit design decision that they compose *for the
    difference each of them names*. An applied I0 on `r_new` (superseding `r_old`)
    is the claim "the r_old → r_new change does not materially affect my evidence".
    When both sides of a comparison make that claim about the same single step, the
    difference between their two worlds is immaterial to both, and pooling is not
    pooling across an unaccounted boundary. That is a strictly weaker statement
    than "the platform did not change", which is what a bare fingerprint equality
    demands and what strands otherwise-readable evidence.

    Its limits, deliberately:

      * an EXEMPTED, PROPOSED or ACCEPTED-but-unapplied record licenses nothing;
      * a component that differs with no disposition on EITHER side blocks, and
        the reason names it — one side's license is not a claim about the pair;
      * only the single transition a disposition names is licensed, so a
        multi-step difference blocks even when each step was separately
        dispositioned by one experiment (see `_licensed_transitions`);
      * a component pinned by only one of the two snapshots blocks: the snapshots
        are not comparable component-for-component, and no record spans it.

    This is the same license `platform_block_reasons` honours for pinned-vs-active
    staleness, asked of a pair instead of a single scope. Nothing here changes what
    a disposition means, or authorizes anything: it only decides whether a delta may
    be COMPUTED."""
    if epoch is None or other is None:
        return "one side of the comparison has no operating epoch to pin a snapshot"
    pins = _snapshot_pins(session, epoch.platform_snapshot_id)
    other_pins = _snapshot_pins(session, other.platform_snapshot_id)
    licensed = _licensed_transitions(session, _experiment_id_of_epoch(session, epoch))
    other_licensed = _licensed_transitions(
        session, _experiment_id_of_epoch(session, other)
    )
    blockers: list[str] = []
    for key in sorted(set(pins) | set(other_pins)):
        mine = pins.get(key)
        theirs = other_pins.get(key)
        if mine is None or theirs is None:
            blockers.append(
                f"{key} is pinned by only one of the two snapshots "
                f"({'this epoch' if mine is not None else 'the control'} only)"
            )
            continue
        if mine[0] == theirs[0]:
            continue
        transition = frozenset((mine[0], theirs[0]))
        if transition in licensed and transition in other_licensed:
            continue
        missing = [
            side
            for side, held in (("this epoch", licensed), ("the control", other_licensed))
            if transition not in held
        ]
        blockers.append(
            f"{key} differs (this epoch pins {mine[1]!r}, the control pins "
            f"{theirs[1]!r}) and no APPLIED NO_ACTION/RECOMPUTE disposition "
            f"licenses that transition for {' or '.join(missing)}"
        )
    if not blockers:
        return None
    return "; ".join(blockers)


def evidence_block_reasons(session, experiment: Experiment) -> list[str]:
    """Why this experiment's evidence cannot currently be interpreted, from the
    impact ledger: a PROPOSED record means the comparability question is open; an
    ACCEPTED material disposition means the world changed and the split/renorm/
    protective action has not been applied yet. NO_ACTION settles at acceptance;
    applied/exempted rows never block."""
    reasons: list[str] = []
    rows = session.execute(
        select(PlatformImpactAction, PlatformRevision, PlatformComponent)
        .join(PlatformRevision, PlatformRevision.id == PlatformImpactAction.revision_id)
        .join(PlatformComponent, PlatformComponent.id == PlatformRevision.component_id)
        .where(PlatformImpactAction.experiment_id == experiment.id)
    ).all()
    for record, revision, comp in rows:
        label = f"{comp.key}:{revision.version} impact #{record.id}"
        if record.status == "proposed":
            reasons.append(
                f"{label} is PROPOSED ({record.impact_class}/{record.action}) — "
                "accept or withdraw the classification before evidence over the "
                "boundary can be interpreted"
            )
        elif record.status == "accepted" and record.action in _BLOCKING_WHEN_ACCEPTED:
            reasons.append(
                f"{label} is ACCEPTED ({record.impact_class}/{record.action}) but "
                "not APPLIED — apply the disposition (or exempt it with rationale)"
            )
    return reasons


def blocked_experiment_ids(session) -> set[int]:
    """Experiments with an unresolved material impact — used by STRICT enforcement
    to stop their deployments' tags from accumulating uninterpretable evidence."""
    rows = session.execute(
        select(
            PlatformImpactAction.experiment_id,
            PlatformImpactAction.status,
            PlatformImpactAction.action,
        ).where(PlatformImpactAction.status.in_(("proposed", "accepted")))
    ).all()
    out: set[int] = set()
    for exp_id, status, action in rows:
        if status == "proposed" or action in _BLOCKING_WHEN_ACCEPTED:
            out.add(exp_id)
    return out


def classify_drift(
    session,
    event: ExperimentIntegrityEvent,
    *,
    revision: PlatformRevision,
    actor: str,
    note: str | None = None,
) -> ExperimentIntegrityEvent:
    """Promote a generic config-drift finding into the platform-change workflow:
    the drift was not noise but a platform-level semantic change, now represented
    by `revision`. Resolves the drift event with the reference; the impact
    records themselves are proposed/accepted through the normal engine calls."""
    if event.kind != "EXPERIMENT_CONFIG_DRIFT":
        raise ExperimentOsError(
            f"integrity event #{event.id} is {event.kind}, not EXPERIMENT_CONFIG_DRIFT"
        )
    if event.resolved_at is not None:
        raise ExperimentOsError(f"integrity event #{event.id} is already resolved")
    comp = component_of(session, revision)
    event.resolved_at = _now()
    event.resolution = (
        f"classified by {actor} as platform change {comp.key}:{revision.version} "
        f"(revision #{revision.id})" + (f" — {note}" if note else "")
    )
    session.flush()
    return event


# ---------------------------------------------------------------------------
# The review surface (one canonical read for the Platform Change Review session)
# ---------------------------------------------------------------------------


def revision_review(session, revision: PlatformRevision) -> dict:
    """Everything a reviewer needs about one platform change, in one read."""
    comp = component_of(session, revision)
    prior = superseded_revision(session, revision)
    gate = activation_gate(session, revision)
    affected = affected_experiments(session, revision)
    records = session.execute(
        select(PlatformImpactAction, Experiment)
        .join(Experiment, Experiment.id == PlatformImpactAction.experiment_id)
        .where(PlatformImpactAction.revision_id == revision.id)
        .order_by(Experiment.key)
    ).all()
    return {
        "component": comp.key,
        "revision": {
            "id": revision.id,
            "version": revision.version,
            "status": revision.status,
            "reason": revision.reason,
            "safety_class": revision.safety_class,
            "pr_ref": revision.pr_ref,
            "activated_at": str(revision.activated_at) if revision.activated_at else None,
            "boundary_established": revision.activated_at is not None,
        },
        "supersedes": (
            {"id": prior.id, "version": prior.version, "status": prior.status}
            if prior is not None
            else None
        ),
        "affected": {
            "active_on_superseded": [
                {k: v for k, v in e.items() if k != "experiment"}
                for e in affected["active_on_superseded"]
            ],
            "already_on_new": [e["key"] for e in affected["already_on_new"]],
            "no_pinned_epoch": [e["key"] for e in affected["no_pinned_epoch"]],
            "retired": [e["key"] for e in affected["retired"]],
        },
        "impact_records": [
            {
                "id": r.id,
                "experiment": exp.key,
                "impact_class": r.impact_class,
                "action": r.action,
                "status": r.status,
                "blocks_activation": r.blocks_activation,
                "normalization_ref": r.normalization_ref,
                "rationale": r.rationale,
                "decided_by": r.decided_by,
                "accepted_by": r.accepted_by,
                "resulting_epoch_id": r.resulting_epoch_id,
                "resulting_version_id": r.resulting_version_id,
            }
            for r, exp in records
        ],
        "activation": gate,
    }


def get_revision(session, ref: str | int) -> PlatformRevision | None:
    """Resolve 'COMPONENT:version' (or a numeric id) to a revision."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        return session.get(PlatformRevision, int(ref))
    comp_key, _, version = ref.partition(":")
    if not comp_key or not version:
        return None
    return session.scalar(
        select(PlatformRevision)
        .join(PlatformComponent, PlatformComponent.id == PlatformRevision.component_id)
        .where(PlatformComponent.key == comp_key, PlatformRevision.version == version)
    )


def unresolved_impacts(session) -> list[dict]:
    """All open dispositions across the system — the status-surface feed."""
    rows = session.execute(
        select(PlatformImpactAction, Experiment, PlatformRevision, PlatformComponent)
        .join(Experiment, Experiment.id == PlatformImpactAction.experiment_id)
        .join(PlatformRevision, PlatformRevision.id == PlatformImpactAction.revision_id)
        .join(PlatformComponent, PlatformComponent.id == PlatformRevision.component_id)
        .where(PlatformImpactAction.status.in_(("proposed", "accepted")))
        .order_by(PlatformComponent.key, Experiment.key)
    ).all()
    return [
        {
            "id": r.id,
            "component": comp.key,
            "revision": rev.version,
            "experiment": exp.key,
            "impact_class": r.impact_class,
            "action": r.action,
            "status": r.status,
            "blocks_activation": r.blocks_activation,
        }
        for r, exp, rev, comp in rows
    ]


def pending_revisions(session) -> list[dict]:
    rows = session.execute(
        select(PlatformRevision, PlatformComponent)
        .join(PlatformComponent, PlatformComponent.id == PlatformRevision.component_id)
        .where(PlatformRevision.status == "pending")
        .order_by(PlatformComponent.key, PlatformRevision.version)
    ).all()
    out = []
    for rev, comp in rows:
        gate = activation_gate(session, rev)
        out.append(
            {
                "component": comp.key,
                "version": rev.version,
                "created_at": str(rev.created_at),
                "activation_safe": gate["safe"],
                "unaccounted": gate["unaccounted"],
                "blocking_unapplied": gate["blocking_unapplied"],
            }
        )
    return out
