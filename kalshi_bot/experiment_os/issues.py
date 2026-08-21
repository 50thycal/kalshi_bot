"""Experiment OS issue workflow — the durable write path for investigations.

Documented end to end in `docs/EXPERIMENT_OS_ISSUES.md`; the rules live in
`issue_policy.py` so the read-only Control Tower can apply the same ones without
importing anything that writes.

## The one invariant that makes this safe to use freely

**No function in this module transitions an experiment, evaluates or alters a
gate, changes a recorded verdict, opens an epoch, creates a Version, registers or
activates a Platform Revision, arms a live canary, or changes real-money
exposure.** It imports no helper that can. `record_disposition` writes down that
one of those is *required*; the canonical service and its approval rules remain
the only way any of them happens, and `add_issue_link` is how the resulting
canonical record gets attached afterwards.

That is what lets an issue be opened on a suspicion. A workflow object that could
move an experiment would have to be as carefully guarded as the transition
itself, and nobody would open one.

## Cached state vs history

The issue row caches `status`, `current_owner_role`, `classification`,
`occurrence_count` and `resolved_at` so listings are one query. The append-only
`experiment_issue_events` table is the authority, and every consequential
mutation here writes its event **in the same transaction** as the change — the
same relationship `experiments.state` has to `experiment_state_transitions`.
Events, evidence and links are flush-guarded against UPDATE/DELETE.

Helpers follow the repository convention: take an active session, flush, return
the ORM object; commit/rollback belongs to the caller.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from . import issue_policy as policy
from . import read
from . import service as _service  # noqa: F401 — installs the append-only flush guard
from .issue_policy import (  # re-exported: routing is policy, callers should not re-derive it
    IssueClassification,
    IssueDisposition,
    IssueEventType,
    IssueEvidenceType,
    IssueLinkType,
    IssueOwnerRole,
    IssuePriority,
    IssueSeverity,
    IssueStatus,
    issue_fingerprint,
    recommend_owner,
)
from .models import (
    Experiment,
    ExperimentDeployment,
    ExperimentEpoch,
    ExperimentGate,
    ExperimentGateResult,
    ExperimentIntegrityEvent,
    ExperimentIssue,
    ExperimentIssueEvent,
    ExperimentIssueEvidence,
    ExperimentIssueLink,
    ExperimentVersion,
    PlatformRevision,
)
from .service import ExperimentOsError

__all__ = [
    "CandidateNotAdoptable",
    "IssueClassification",
    "IssueDisposition",
    "IssueError",
    "IssueEventType",
    "IssueEvidenceType",
    "IssueLinkType",
    "IssueOwnerRole",
    "IssuePriority",
    "IssueScopeError",
    "IssueSeverity",
    "IssueStatus",
    "add_issue_evidence",
    "add_issue_link",
    "assign_issue",
    "classify_issue",
    "close_no_action",
    "create_issue",
    "find_open_issue_by_fingerprint",
    "get_issue",
    "issue_fingerprint",
    "list_issues",
    "mark_duplicate",
    "open_child_issue",
    "open_issue_from_candidate",
    "propose_issue_fix",
    "recommend_owner",
    "record_disposition",
    "record_recurrence",
    "record_validation_plan",
    "record_validation_result",
    "reopen_issue",
    "resolve_issue",
    "set_issue_status",
    "suggest_duplicates",
    "transfer_issue",
    "triage_issue",
]

ISSUE_KEY_PREFIX = policy.ISSUE_KEY_PREFIX


class IssueError(ExperimentOsError):
    """An issue-workflow rule was violated."""


class IssueScopeError(IssueError):
    """The supplied lineage is internally inconsistent — e.g. a version that does
    not belong to the named experiment. Refused rather than stored: an issue whose
    scope is wrong points every later reader at the wrong contract."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id_of(value):
    """Accept an ORM object or a raw id, uniformly."""
    if value is None:
        return None
    return getattr(value, "id", value)


def _require_text(value, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise IssueError(f"{field} is required and may not be blank")
    return text


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _next_issue_key(session) -> str:
    """The next `XOS-000123`.

    Zero-padded to a fixed width so lexical order equals numeric order — the
    listing sorts, the CLI accepts it as a word, and nothing has to parse a
    title to identify a ticket. The unique constraint is the real guarantee; a
    concurrent writer loses the insert rather than silently sharing a key."""
    highest = 0
    for (key,) in session.execute(
        select(ExperimentIssue.issue_key).where(
            ExperimentIssue.issue_key.like(f"{ISSUE_KEY_PREFIX}%")
        )
    ):
        number = policy.parse_issue_key(key or "")
        if number is not None:
            highest = max(highest, number)
    return policy.format_issue_key(highest + 1)


# ---------------------------------------------------------------------------
# Lineage resolution + validation (§3.2)
# ---------------------------------------------------------------------------


def _resolve_scope(session, ids: dict) -> dict:
    """Fill in implied ancestors and refuse any combination that disagrees.

    Ancestors are DERIVED, not required: the lineage chain
    `deployment → epoch → version → experiment` is NOT NULL all the way up, so
    naming a gate already determines its version and experiment. Deriving them
    keeps the foreign keys populated (canonical identity belongs in a column, not
    in `details_json`) without asking every caller to restate what the database
    already knows. Anything the caller *did* state is validated against the
    derivation, so a mistake is a refusal rather than a silently corrected row.
    """
    out = dict(ids)

    def _load(model, key, label):
        pk = out.get(key)
        if pk is None:
            return None
        obj = session.get(model, pk)
        if obj is None:
            raise IssueScopeError(f"{label} {pk} does not exist")
        return obj

    def _bind(key, derived, owner_label, child_label):
        """Set `key` from a derivation, or check the caller's value matches."""
        stated = out.get(key)
        if stated is None:
            out[key] = derived
        elif stated != derived:
            raise IssueScopeError(
                f"{child_label} belongs to {owner_label} {derived}, not the "
                f"{owner_label} {stated} this issue was given"
            )

    result = _load(ExperimentGateResult, "gate_result_id", "gate result")
    if result is not None:
        _bind("gate_id", result.gate_id, "gate", "gate result")
        _bind("version_id", result.version_id, "version", "gate result")
        _bind("experiment_id", result.experiment_id, "experiment", "gate result")
        if result.epoch_id is not None:
            _bind("epoch_id", result.epoch_id, "epoch", "gate result")

    gate = _load(ExperimentGate, "gate_id", "gate")
    if gate is not None:
        _bind("version_id", gate.version_id, "version", "gate")

    deployment = _load(ExperimentDeployment, "deployment_id", "deployment")
    if deployment is not None:
        _bind("epoch_id", deployment.epoch_id, "epoch", "deployment")

    epoch = _load(ExperimentEpoch, "epoch_id", "epoch")
    if epoch is not None:
        _bind("version_id", epoch.version_id, "version", "epoch")

    version = _load(ExperimentVersion, "version_id", "version")
    if version is not None:
        _bind("experiment_id", version.experiment_id, "experiment", "version")

    integrity = _load(ExperimentIntegrityEvent, "integrity_event_id", "integrity event")
    if integrity is not None:
        _bind("experiment_id", integrity.experiment_id, "experiment", "integrity event")

    if out.get("experiment_id") is not None:
        _load(Experiment, "experiment_id", "experiment")
    if out.get("platform_revision_id") is not None:
        _load(PlatformRevision, "platform_revision_id", "platform revision")
    return out


# ---------------------------------------------------------------------------
# Event writing
# ---------------------------------------------------------------------------


def _event(
    session,
    issue: ExperimentIssue,
    event_type: str,
    *,
    actor: str | None,
    actor_role: str | None,
    reason: str | None = None,
    payload: dict | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    from_owner_role: str | None = None,
    to_owner_role: str | None = None,
    from_classification: str | None = None,
    to_classification: str | None = None,
    occurred_at: datetime | None = None,
) -> ExperimentIssueEvent:
    """Append one immutable history row. Always called in the SAME transaction as
    the mutation it describes, so cached state cannot outrun its explanation."""
    row = ExperimentIssueEvent(
        issue_id=issue.id,
        event_type=policy.coerce(IssueEventType, event_type, "event type"),
        actor=actor,
        actor_role=actor_role,
        occurred_at=occurred_at or _now(),
        from_status=from_status,
        to_status=to_status,
        from_owner_role=from_owner_role,
        to_owner_role=to_owner_role,
        from_classification=from_classification,
        to_classification=to_classification,
        reason=reason,
        payload_json=payload,
    )
    session.add(row)
    issue.updated_at = _now()
    session.flush()
    return row


def _touch(issue: ExperimentIssue) -> None:
    issue.updated_at = _now()


# ---------------------------------------------------------------------------
# Create / read
# ---------------------------------------------------------------------------


def create_issue(
    session,
    *,
    title: str,
    opened_by_role: str,
    problem_statement: str | None = None,
    opened_by_actor: str | None = None,
    classification: str | None = None,
    severity: str | None = None,
    priority: str | None = None,
    current_owner_role: str | None = None,
    owner_rationale: str | None = None,
    experiment=None,
    version=None,
    epoch=None,
    deployment=None,
    gate=None,
    gate_result=None,
    platform_revision=None,
    integrity_event=None,
    detector: str | None = None,
    detector_fingerprint: str | None = None,
    anomaly_kind: str | None = None,
    gate_verdict: str | None = None,
    first_observed_at: datetime | None = None,
    evidence_summary: str | None = None,
    discovered_from_issue=None,
    supersedes_issue=None,
    details_json: dict | None = None,
    now: datetime | None = None,
) -> ExperimentIssue:
    """Open a durable issue.

    `opened_by_role` may be a detector-only role (`EXPERIMENT_CONTROL_TOWER`,
    `SYSTEM`); `current_owner_role` may not — remediation always belongs to a
    role that can actually do the work. When no owner is given, the routing
    policy recommends one and the recommendation is recorded on the CREATED
    event; when one IS given it is used as stated and, if it disagrees with the
    recommendation, the disagreement is recorded too rather than resolved
    silently.

    Classification defaults to the policy's recommendation, which is frequently
    `UNCLASSIFIED` — that is the correct answer at creation time for a problem
    whose cause nobody has established yet (§2.3), not a gap to be filled in.

    Every lineage argument accepts an ORM object or a raw id; `experiment` also
    accepts an experiment key. Ancestors are derived and any stated value is
    validated (see `_resolve_scope`). NOTHING here transitions an experiment,
    touches a gate, or changes exposure.
    """
    stamp = now or _now()
    title = _require_text(title, "title")
    opened_role = policy.validate_opening_role(opened_by_role)

    if isinstance(experiment, str):
        exp_obj = session.scalar(select(Experiment).where(Experiment.key == experiment))
        if exp_obj is None:
            raise IssueScopeError(f"no experiment with key {experiment!r}")
        experiment = exp_obj

    scope = _resolve_scope(
        session,
        {
            "experiment_id": _id_of(experiment),
            "version_id": _id_of(version),
            "epoch_id": _id_of(epoch),
            "deployment_id": _id_of(deployment),
            "gate_id": _id_of(gate),
            "gate_result_id": _id_of(gate_result),
            "platform_revision_id": _id_of(platform_revision),
            "integrity_event_id": _id_of(integrity_event),
        },
    )

    recommendation = recommend_owner(
        detector=detector,
        anomaly_kind=anomaly_kind,
        gate_verdict=gate_verdict or (details_json or {}).get("gate_verdict"),
    )
    owner = (
        policy.validate_owner_role(current_owner_role)
        if current_owner_role is not None
        else recommendation.owner_role
    )
    resolved_classification = (
        policy.coerce(IssueClassification, classification, "classification")
        if classification is not None
        else recommendation.classification
    )
    resolved_severity = (
        policy.coerce(IssueSeverity, severity, "severity")
        if severity is not None
        else recommendation.severity
    )
    resolved_priority = (
        policy.coerce(IssuePriority, priority, "priority")
        if priority is not None
        else recommendation.priority
    )

    issue = ExperimentIssue(
        issue_key=_next_issue_key(session),
        title=title,
        problem_statement=problem_statement,
        classification=resolved_classification,
        status=IssueStatus.OPEN.value,
        severity=resolved_severity,
        priority=resolved_priority,
        current_owner_role=owner,
        opened_by_role=opened_role,
        opened_by_actor=opened_by_actor,
        opened_at=stamp,
        updated_at=stamp,
        detector=detector,
        detector_fingerprint=detector_fingerprint,
        first_observed_at=first_observed_at or stamp,
        last_observed_at=first_observed_at or stamp,
        occurrence_count=1,
        evidence_summary=evidence_summary,
        duplicate_of_issue_id=None,
        supersedes_issue_id=_id_of(supersedes_issue),
        discovered_from_issue_id=_id_of(discovered_from_issue),
        details_json=details_json,
        **scope,
    )
    session.add(issue)
    session.flush()

    payload = {
        "recommended_owner": recommendation.as_json(),
        "scope": read.issue_scope_label(session, issue),
        "anomaly_kind": anomaly_kind,
    }
    if current_owner_role is not None and owner != recommendation.owner_role:
        # An override is legitimate — the policy recommends, it does not decide.
        # Recording the disagreement keeps the routing rules honest: a rule that
        # is overridden every time is a rule that is wrong.
        payload["owner_override"] = {
            "chosen": owner,
            "recommended": recommendation.owner_role,
            "rationale": owner_rationale,
        }
    _event(
        session, issue, IssueEventType.CREATED.value,
        actor=opened_by_actor, actor_role=opened_role,
        reason=owner_rationale or recommendation.rationale,
        payload=payload, to_status=IssueStatus.OPEN.value,
        to_owner_role=owner, to_classification=resolved_classification,
        occurred_at=stamp,
    )
    return issue


class CandidateNotAdoptable(IssueError):
    """A fingerprint could not be resolved to exactly one open ticket candidate.

    Deliberately distinct from a generic error so the CLI can say WHICH of the
    four refusals happened — unknown/stale, ambiguous, or already covered are
    different facts about the world and lead to different next actions."""


def open_issue_from_candidate(
    session,
    fingerprint: str,
    *,
    actor: str,
    opened_by_role: str,
    candidates: list[dict] | None = None,
    evaluate: bool = True,
    now: datetime | None = None,
) -> ExperimentIssue:
    """Adopt a Control Tower candidate into a durable issue, scope intact.

    This closes the gap that made the documented workflow unusable end to end.
    The Tower detects an anomaly and computes its deterministic fingerprint and
    exact lineage; opening a ticket by hand could not carry either, so the new
    issue never matched the candidate that caused it and the Tower went on
    reporting the same anomaly as UNTICKETED forever. Adoption copies the
    candidate's own detector, fingerprint, anomaly kind, scope, verdict and
    routing, so the ticket covers the thing it was opened for.

    The candidate set comes from the read-only Tower path, so what is adopted is
    what the report currently shows — not a fingerprint a caller typed from
    memory. A stale one is refused rather than resurrected as a ticket for an
    anomaly that has since cleared.

    Writes an issue, its CREATED event and one CONTROL_TOWER evidence row. It
    performs no lifecycle, gate, Version, Epoch, Platform Revision, exposure or
    live action — like every other function in this module.
    """
    fingerprint = _require_text(fingerprint, "fingerprint")
    if candidates is None:
        from . import control_tower  # lazy: heavy, and only this path needs it

        candidates = control_tower.detected_candidates(session, evaluate=evaluate)

    matches = [c for c in candidates if c.get("fingerprint") == fingerprint]
    if not matches:
        raise CandidateNotAdoptable(
            f"no candidate with fingerprint {fingerprint[:16]}… is currently "
            "detected. Either it is not a fingerprint this report produces, or "
            "the anomaly has cleared since it was read — run `issue candidates` "
            "for the current set. A cleared anomaly must not become a ticket"
        )
    if len(matches) > 1:
        scopes = ", ".join(sorted(c["scope"] for c in matches))
        raise CandidateNotAdoptable(
            f"fingerprint {fingerprint[:16]}… matches {len(matches)} candidates "
            f"({scopes}); refusing to guess which one to open"
        )

    candidate = matches[0]
    if candidate.get("covered_by"):
        raise CandidateNotAdoptable(
            f"{candidate['covered_by']} ({candidate.get('covering_status')}) "
            f"already covers this anomaly, owned by "
            f"{candidate.get('covering_owner')}. Update that investigation "
            "instead of opening a second ticket for one problem"
        )

    issue = create_issue(
        session,
        title=candidate["title"],
        problem_statement=candidate.get("detail"),
        opened_by_role=opened_by_role,
        opened_by_actor=actor,
        # The candidate's OWN routing, not a fresh opinion: the report the
        # operator read and the ticket they opened must agree.
        classification=candidate["recommended_classification"],
        severity=candidate["severity"],
        priority=candidate["priority"],
        current_owner_role=candidate["recommended_owner"],
        owner_rationale=candidate["rationale"],
        # Exact lineage, straight from the detection.
        experiment=candidate.get("experiment_id"),
        version=candidate.get("version_id"),
        epoch=candidate.get("epoch_id"),
        deployment=candidate.get("deployment_id"),
        gate=candidate.get("gate_id"),
        detector=candidate["detector"],
        detector_fingerprint=candidate["fingerprint"],
        anomaly_kind=candidate.get("anomaly_kind"),
        gate_verdict=candidate.get("gate_verdict"),
        evidence_summary=candidate.get("detail"),
        details_json={
            "adopted_from_candidate": True,
            "candidate_scope": candidate.get("scope"),
            # `anomaly_kind` and the verdict have no column of their own: the
            # kind is encoded in the fingerprint and both are on the CREATED
            # event, but neither is readable without work. Adoption's whole
            # point is that the ticket carries what was actually seen.
            "anomaly_kind": candidate.get("anomaly_kind"),
            "gate_verdict": candidate.get("gate_verdict"),
            "requires_operator_triage": candidate.get("requires_operator_triage"),
        },
        now=now,
    )
    add_issue_evidence(
        session, issue,
        evidence_type=IssueEvidenceType.CONTROL_TOWER.value,
        summary=(
            f"detected by {candidate['detector']} at {candidate['scope']}"
            + (f": {candidate['detail']}" if candidate.get("detail") else "")
        ),
        source_ref="experiment_os.cli control-tower (UNTICKETED ANOMALIES)",
        captured_by=actor, actor_role=opened_by_role, captured_at=now,
    )
    return issue


def open_child_issue(
    session, parent: ExperimentIssue, *, title: str, opened_by_role: str, **kwargs
) -> ExperimentIssue:
    """Open an independently actionable problem discovered while investigating
    `parent` (§6.1).

    Use this when one investigation uncovers two problems with genuinely
    different scopes and remedies — a stale-config OPS repair and a toxic
    selection rule, say. Do NOT use it for a change of ownership: a transfer is
    the same problem moving, and splitting it there would scatter one
    investigation across two half-histories."""
    child = create_issue(
        session, title=title, opened_by_role=opened_by_role,
        discovered_from_issue=parent, **kwargs,
    )
    _event(
        session, parent, IssueEventType.LINK_ADDED.value,
        actor=kwargs.get("opened_by_actor"), actor_role=opened_by_role,
        reason=f"split out an independently actionable problem: {child.issue_key}",
        payload={"child_issue": child.issue_key, "child_issue_id": child.id},
    )
    add_issue_link(
        session, parent, link_type=IssueLinkType.SUCCESSOR_ISSUE.value,
        reference=child.issue_key, label=title,
        created_by=kwargs.get("opened_by_actor"),
    )
    return child


def get_issue(session, ref) -> ExperimentIssue | None:
    """Look up by `issue_key` (`XOS-000123`) or raw id — see `read.get_issue`."""
    return read.get_issue(session, ref)


def _require_issue(session, ref) -> ExperimentIssue:
    issue = get_issue(session, ref)
    if issue is None:
        raise IssueError(f"no issue {ref!r}")
    return issue


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
    experiment=None,
    version=None,
    limit: int | None = None,
) -> list[ExperimentIssue]:
    """Query issues, worst-first. Validates the enum filters, then delegates to
    `read.list_issues` — a mistyped status should be a named refusal here rather
    than an empty result the caller reads as "nothing is wrong"."""
    return read.list_issues(
        session,
        status=policy.coerce(IssueStatus, status, "status")
        if status is not None else None,
        open_only=open_only,
        owner_role=str(owner_role) if owner_role is not None else None,
        classification=policy.coerce(
            IssueClassification, classification, "classification"
        ) if classification is not None else None,
        severity=policy.coerce(IssueSeverity, severity, "severity")
        if severity is not None else None,
        priority=policy.coerce(IssuePriority, priority, "priority")
        if priority is not None else None,
        detector=detector,
        experiment_id=_id_of(experiment),
        version_id=_id_of(version),
        limit=limit,
    )


def find_open_issue_by_fingerprint(session, fingerprint: str) -> ExperimentIssue | None:
    """The OPEN issue already covering this exact problem scope, if any.

    Open ones only: a RESOLVED issue must not suppress a genuinely recurring
    anomaly (§9). Reopening one on recurrence is an explicit command, never
    something a report does while rendering."""
    return read.open_issue_by_fingerprint(session, fingerprint)


def issue_events(session, issue) -> list[ExperimentIssueEvent]:
    return read.issue_events(session, _require_issue(session, issue))


def issue_evidence(session, issue) -> list[ExperimentIssueEvidence]:
    return read.issue_evidence(session, _require_issue(session, issue))


def issue_links(session, issue) -> list[ExperimentIssueLink]:
    return read.issue_links(session, _require_issue(session, issue))


# ---------------------------------------------------------------------------
# Evidence + links
# ---------------------------------------------------------------------------


def add_issue_evidence(
    session,
    issue,
    *,
    evidence_type: str,
    summary: str,
    source_ref: str | None = None,
    captured_by: str | None = None,
    captured_at: datetime | None = None,
    payload_json: dict | None = None,
    content_hash: str | None = None,
    actor_role: str | None = None,
) -> ExperimentIssueEvidence:
    """Cite evidence: reference + summary, not a wholesale copy.

    `source_ref` should be something another session can independently go and
    look at — an ops result id, a document path, a gate result id, a PR. Note
    that no evidence type covers unresolved chat: session prose is not durable,
    not addressable and not reviewable, so it cannot be the basis of a finding."""
    issue = _require_issue(session, issue)
    row = ExperimentIssueEvidence(
        issue_id=issue.id,
        evidence_type=policy.coerce(IssueEvidenceType, evidence_type, "evidence type"),
        summary=_require_text(summary, "evidence summary"),
        source_ref=source_ref,
        captured_at=captured_at or _now(),
        captured_by=captured_by,
        payload_json=payload_json,
        content_hash=content_hash,
    )
    session.add(row)
    session.flush()
    _event(
        session, issue, IssueEventType.EVIDENCE_ADDED.value,
        actor=captured_by, actor_role=actor_role,
        reason=row.summary[:500],
        payload={
            "evidence_id": row.id,
            "evidence_type": row.evidence_type,
            "source_ref": source_ref,
            "content_hash": content_hash,
        },
    )
    return row


def add_issue_link(
    session,
    issue,
    *,
    link_type: str,
    reference: str,
    label: str | None = None,
    created_by: str | None = None,
    actor_role: str | None = None,
) -> ExperimentIssueLink:
    """Attach a supporting or canonical reference.

    This is how a remedy's canonical record gets attached AFTER it exists — the
    new Version, the registered Platform Revision, the PR that implemented the
    operational repair. Linking one records what happened; it never performs it."""
    issue = _require_issue(session, issue)
    row = ExperimentIssueLink(
        issue_id=issue.id,
        link_type=policy.coerce(IssueLinkType, link_type, "link type"),
        reference=_require_text(reference, "link reference"),
        label=label,
        created_by=created_by,
    )
    session.add(row)
    session.flush()
    _event(
        session, issue, IssueEventType.LINK_ADDED.value,
        actor=created_by, actor_role=actor_role,
        reason=label or f"{row.link_type} {row.reference}",
        payload={"link_id": row.id, "link_type": row.link_type,
                 "reference": row.reference},
    )
    return row


# ---------------------------------------------------------------------------
# Workflow moves
# ---------------------------------------------------------------------------


def set_issue_status(
    session, issue, *, status: str, actor: str, actor_role: str,
    reason: str | None = None, event_type: str | None = None,
    payload: dict | None = None,
) -> ExperimentIssue:
    """Move the issue through the workflow, validating the transition."""
    issue = _require_issue(session, issue)
    target = policy.coerce(IssueStatus, status, "status")
    policy.validate_issue_status_transition(issue.status, target)
    previous = issue.status
    issue.status = target
    _touch(issue)
    _event(
        session, issue, event_type or IssueEventType.STATUS_CHANGED.value,
        actor=actor, actor_role=actor_role, reason=reason, payload=payload,
        from_status=previous, to_status=target,
    )
    return issue


def triage_issue(
    session,
    issue,
    *,
    actor: str,
    actor_role: str,
    reason: str,
    severity: str | None = None,
    priority: str | None = None,
    classification: str | None = None,
    owner_role: str | None = None,
) -> ExperimentIssue:
    """First look: set severity/priority, optionally classify and assign, and
    move OPEN → TRIAGE.

    Triage is allowed to leave the classification `UNCLASSIFIED`. Forcing a
    classification here is exactly the premature certainty §2.3 warns about — the
    point of triage is to decide who looks first, not to decide what is wrong."""
    issue = _require_issue(session, issue)
    reason = _require_text(reason, "triage reason")
    if severity is not None:
        issue.severity = policy.coerce(IssueSeverity, severity, "severity")
    if priority is not None:
        issue.priority = policy.coerce(IssuePriority, priority, "priority")
    if classification is not None and (
        policy.coerce(IssueClassification, classification, "classification")
        != issue.classification
    ):
        classify_issue(
            session, issue, classification=classification,
            actor=actor, actor_role=actor_role,
            reason=f"set during triage: {reason}",
        )
    if owner_role is not None and str(owner_role) != issue.current_owner_role:
        assign_issue(
            session, issue, owner_role=owner_role, actor=actor,
            actor_role=actor_role, reason=f"assigned during triage: {reason}",
        )
    return set_issue_status(
        session, issue, status=IssueStatus.TRIAGE.value, actor=actor,
        actor_role=actor_role, reason=reason,
        event_type=IssueEventType.TRIAGED.value,
        payload={"severity": issue.severity, "priority": issue.priority},
    )


def classify_issue(
    session, issue, *, classification: str, actor: str, actor_role: str, reason: str
) -> ExperimentIssue:
    """Change the classification. The rationale is MANDATORY.

    A classification change is a change of story about what is wrong, and the
    reason it changed is usually the most useful line in the whole history —
    "runtime verified healthy, so this is no longer an OPS question". Recording
    the new value without it leaves the next reader unable to tell whether the
    system learned something or somebody guessed differently."""
    issue = _require_issue(session, issue)
    target = policy.coerce(IssueClassification, classification, "classification")
    reason = _require_text(reason, "classification-change reason")
    if target == issue.classification:
        raise IssueError(
            f"issue {issue.issue_key} is already classified {target}; "
            "re-recording it would add history without a change"
        )
    previous = issue.classification
    issue.classification = target
    _touch(issue)
    _event(
        session, issue, IssueEventType.CLASSIFIED.value,
        actor=actor, actor_role=actor_role, reason=reason,
        from_classification=previous, to_classification=target,
    )
    return issue


def assign_issue(
    session, issue, *, owner_role: str, actor: str, actor_role: str, reason: str
) -> ExperimentIssue:
    """Set the owning role directly (initial assignment or correction).

    For a mid-investigation handoff use `transfer_issue`, which additionally
    requires the evidence that justifies moving the problem to another role."""
    issue = _require_issue(session, issue)
    target = policy.validate_owner_role(owner_role)
    reason = _require_text(reason, "assignment reason")
    previous = issue.current_owner_role
    issue.current_owner_role = target
    _touch(issue)
    _event(
        session, issue, IssueEventType.ASSIGNED.value,
        actor=actor, actor_role=actor_role, reason=reason,
        from_owner_role=previous, to_owner_role=target,
    )
    return issue


def transfer_issue(
    session,
    issue,
    *,
    to_owner_role: str,
    actor: str,
    actor_role: str,
    reason: str,
    classification: str | None = None,
    evidence_waiver_reason: str | None = None,
) -> ExperimentIssue:
    """Hand the SAME problem to another role, with rationale and evidence.

    A transfer is not a duplicate and not a new ticket: one investigation follows
    the problem, and the original detector, the original classification and every
    intermediate finding stay visible in the history. That is the whole point of
    the Live Ops → Research Lab path — the diagnosis that cleared the runtime is
    the evidence the next owner needs, and reopening a fresh ticket would throw
    it away.

    Evidence is required because "transfer" without it is just moving a problem
    somebody has not looked at. Pass `evidence_waiver_reason` for the genuine
    exception (an immediate mis-routing at creation, say); it is recorded."""
    issue = _require_issue(session, issue)
    target = policy.validate_owner_role(to_owner_role)
    reason = _require_text(reason, "transfer reason")
    if target == issue.current_owner_role:
        raise IssueError(
            f"issue {issue.issue_key} is already owned by {target}"
        )
    if not issue_evidence(session, issue) and not (evidence_waiver_reason or "").strip():
        raise IssueError(
            f"transferring {issue.issue_key} to {target} requires recorded "
            "evidence for the handoff (add_issue_evidence), or an explicit "
            "evidence_waiver_reason"
        )
    if classification is not None and (
        policy.coerce(IssueClassification, classification, "classification")
        != issue.classification
    ):
        classify_issue(
            session, issue, classification=classification, actor=actor,
            actor_role=actor_role, reason=f"reclassified on transfer: {reason}",
        )
    previous = issue.current_owner_role
    issue.current_owner_role = target
    _touch(issue)
    _event(
        session, issue, IssueEventType.TRANSFERRED.value,
        actor=actor, actor_role=actor_role, reason=reason,
        from_owner_role=previous, to_owner_role=target,
        payload={"evidence_waiver_reason": evidence_waiver_reason}
        if evidence_waiver_reason
        else None,
    )
    return issue


def propose_issue_fix(
    session,
    issue,
    *,
    proposed_fix: str,
    actor: str,
    actor_role: str,
    reason: str | None = None,
    advance: bool = True,
) -> ExperimentIssue:
    """Record the proposed remedy, and (by default) move to ACTION_REQUIRED.

    `advance=False` records the proposal without moving the workflow, for the
    case where a fix is sketched before the investigation is finished."""
    issue = _require_issue(session, issue)
    issue.proposed_fix = _require_text(proposed_fix, "proposed fix")
    _touch(issue)
    _event(
        session, issue, IssueEventType.FIX_PROPOSED.value,
        actor=actor, actor_role=actor_role, reason=reason,
        payload={"proposed_fix": issue.proposed_fix},
    )
    if advance and issue.status == IssueStatus.INVESTIGATING.value:
        set_issue_status(
            session, issue, status=IssueStatus.ACTION_REQUIRED.value,
            actor=actor, actor_role=actor_role,
            reason=reason or "a remedy has been proposed",
        )
    return issue


def record_disposition(
    session,
    issue,
    *,
    disposition: str,
    actor: str,
    actor_role: str,
    reason: str,
    requires_new_version: bool | None = None,
    requires_new_epoch: bool | None = None,
    requires_platform_revision: bool | None = None,
    requires_pause_or_stand_down: bool | None = None,
    version_and_epoch_rationale: str | None = None,
) -> ExperimentIssue:
    """Record WHAT MUST HAPPEN — and perform none of it.

    This is the load-bearing separation of the whole design. `NEW_VERSION`,
    `NEW_EPOCH`, `PLATFORM_REVISION`, `PAUSE_OR_STAND_DOWN` and `RETIRE` all name
    canonical actions with their own services, approval rules and audit; writing
    one here schedules nothing and authorizes nothing. The canonical record gets
    attached afterwards with `add_issue_link`, which is how you can later tell
    "we decided this" apart from "this happened".

    The four `requires_*` flags stay tri-state: NULL means nobody has determined
    it yet. A disposition may not contradict its own flag, so recording
    `NEW_VERSION` while the system still says a new Version is not required (or
    is unknown) is refused rather than quietly overriding one of the two.
    """
    issue = _require_issue(session, issue)
    target = policy.coerce(IssueDisposition, disposition, "disposition")
    reason = _require_text(reason, "disposition rationale")

    for field, value in (
        ("requires_new_version", requires_new_version),
        ("requires_new_epoch", requires_new_epoch),
        ("requires_platform_revision", requires_platform_revision),
        ("requires_pause_or_stand_down", requires_pause_or_stand_down),
    ):
        if value is not None:
            setattr(issue, field, bool(value))

    required_flag = policy.DISPOSITION_REQUIRES.get(target)
    if required_flag is not None and getattr(issue, required_flag) is not True:
        current = getattr(issue, required_flag)
        raise IssueError(
            f"disposition {target} requires {required_flag}=True; it is currently "
            f"{'unknown (NULL)' if current is None else current}. Determine and "
            "record the requirement rather than implying it from the disposition"
        )

    # A new Version and a new Epoch answer different questions — a changed
    # scientific contract vs the same contract under a changed world. Asserting
    # both as THE single remedy usually means the two have been conflated, so it
    # takes an explicit explanation of why this case genuinely needs both.
    if (
        issue.requires_new_version is True
        and issue.requires_new_epoch is True
        and not (version_and_epoch_rationale or "").strip()
    ):
        raise IssueError(
            "a new Version and a new Epoch may not both be the single remedy "
            "without an explicit explanation: a Version is a changed scientific "
            "contract, an Epoch is the same contract under a changed environment. "
            "Pass version_and_epoch_rationale if this case really needs both"
        )

    if target == IssueDisposition.PLATFORM_REVISION.value:
        if issue.current_owner_role != IssueOwnerRole.PLATFORM_CHANGE_REVIEW.value:
            raise IssueError(
                f"disposition PLATFORM_REVISION must be owned by "
                f"{IssueOwnerRole.PLATFORM_CHANGE_REVIEW.value}; issue "
                f"{issue.issue_key} is owned by {issue.current_owner_role}. "
                "Transfer it first — a shared-semantic change is that role's "
                "subject and follows the Platform Impact engine"
            )
    if target == IssueDisposition.DUPLICATE.value and issue.duplicate_of_issue_id is None:
        raise IssueError(
            "disposition DUPLICATE requires the surviving issue; use mark_duplicate"
        )

    issue.disposition = target
    _touch(issue)
    _event(
        session, issue, IssueEventType.DISPOSITION_DECIDED.value,
        actor=actor, actor_role=actor_role, reason=reason,
        payload={
            "disposition": target,
            "requires_new_version": issue.requires_new_version,
            "requires_new_epoch": issue.requires_new_epoch,
            "requires_platform_revision": issue.requires_platform_revision,
            "requires_pause_or_stand_down": issue.requires_pause_or_stand_down,
            "version_and_epoch_rationale": version_and_epoch_rationale,
            "note": "recorded only — no canonical action is performed by this call",
        },
    )
    return issue


def record_validation_plan(
    session,
    issue,
    *,
    validation_plan: str,
    actor: str,
    actor_role: str,
    reason: str | None = None,
    advance: bool = True,
) -> ExperimentIssue:
    """State, before the fix lands, what would demonstrate it worked.

    Written first on purpose: a validation criterion chosen after seeing the
    result is not a check, it is a description."""
    issue = _require_issue(session, issue)
    issue.validation_plan = _require_text(validation_plan, "validation plan")
    _touch(issue)
    _event(
        session, issue, IssueEventType.VALIDATION_PLANNED.value,
        actor=actor, actor_role=actor_role, reason=reason,
        payload={"validation_plan": issue.validation_plan},
    )
    if advance and issue.status == IssueStatus.ACTION_REQUIRED.value:
        set_issue_status(
            session, issue, status=IssueStatus.VALIDATING.value,
            actor=actor, actor_role=actor_role,
            reason=reason or "validation plan recorded",
        )
    return issue


def record_validation_result(
    session,
    issue,
    *,
    passed: bool,
    summary: str,
    actor: str,
    actor_role: str,
    source_ref: str | None = None,
    evidence_type: str = IssueEvidenceType.OPS_RESULT.value,
    payload_json: dict | None = None,
) -> ExperimentIssueEvent:
    """Record what validation actually showed — including a failure.

    A failed validation is a real outcome, not a dead end: it stays recorded and
    the issue returns to ACTION_REQUIRED/INVESTIGATING through the documented
    back edges. Only a PASSED validation satisfies `resolve_issue`."""
    issue = _require_issue(session, issue)
    summary = _require_text(summary, "validation summary")
    add_issue_evidence(
        session, issue, evidence_type=evidence_type,
        summary=f"validation {'PASSED' if passed else 'FAILED'}: {summary}",
        source_ref=source_ref, captured_by=actor, actor_role=actor_role,
        payload_json=payload_json,
    )
    return _event(
        session, issue, IssueEventType.VALIDATION_RECORDED.value,
        actor=actor, actor_role=actor_role, reason=summary,
        payload={"passed": bool(passed), "source_ref": source_ref},
    )


def _has_passing_validation(session, issue: ExperimentIssue) -> bool:
    for ev in issue_events(session, issue):
        if ev.event_type != IssueEventType.VALIDATION_RECORDED.value:
            continue
        if (ev.payload_json or {}).get("passed") is True:
            return True
    return False


def resolve_issue(
    session,
    issue,
    *,
    resolution_summary: str,
    actor: str,
    actor_role: str,
    validation_waiver_reason: str | None = None,
    now: datetime | None = None,
) -> ExperimentIssue:
    """Close the investigation as fixed.

    Two things are required and neither is negotiable: a resolution summary (a
    resolved issue with no account of what was done is indistinguishable from an
    abandoned one), and either a PASSED validation result or an explicit,
    recorded waiver rationale. The waiver exists because some remedies genuinely
    cannot be validated directly; making it explicit means the gap is visible in
    the history instead of implied by its absence.

    Link the canonical record that implemented the remedy — the new Version, the
    registered Platform Revision, the PR, the integrity-event resolution — with
    `add_issue_link` before or after; resolving does not create one."""
    issue = _require_issue(session, issue)
    summary = _require_text(resolution_summary, "resolution summary")
    waiver = (validation_waiver_reason or "").strip()
    if not waiver and not _has_passing_validation(session, issue):
        raise IssueError(
            f"cannot resolve {issue.issue_key}: no PASSED validation result is "
            "recorded. Record one with record_validation_result(passed=True), or "
            "pass validation_waiver_reason stating why this remedy cannot be "
            "validated directly"
        )
    stamp = now or _now()
    issue.resolution_summary = summary
    issue.resolved_at = stamp
    _touch(issue)
    set_issue_status(
        session, issue, status=IssueStatus.RESOLVED.value, actor=actor,
        actor_role=actor_role, reason=summary,
        event_type=IssueEventType.RESOLVED.value,
        payload={
            "resolution_summary": summary,
            "validation_waiver_reason": waiver or None,
            "resolved_at": str(stamp),
        },
    )
    return issue


def close_no_action(
    session, issue, *, reason: str, actor: str, actor_role: str
) -> ExperimentIssue:
    """Close without a remedy — not a defect, not worth acting on, or overtaken.

    Distinct from RESOLVED on purpose: "we looked and there is nothing to do" and
    "we fixed it" are different findings, and collapsing them makes the second
    one untrustworthy. `resolved_at` stays NULL because nothing was resolved."""
    issue = _require_issue(session, issue)
    reason = _require_text(reason, "close reason")
    if issue.disposition is None:
        issue.disposition = IssueDisposition.NO_ACTION.value
    return set_issue_status(
        session, issue, status=IssueStatus.CLOSED_NO_ACTION.value, actor=actor,
        actor_role=actor_role, reason=reason,
        event_type=IssueEventType.CLOSED_NO_ACTION.value,
    )


def mark_duplicate(
    session, issue, *, duplicate_of, actor: str, actor_role: str, reason: str
) -> ExperimentIssue:
    """Confirm this issue duplicates another. Never automatic.

    A deterministic fingerprint match is exact recurrence and is handled by
    `find_open_issue_by_fingerprint`; anything softer than that is a SUGGESTION
    (`suggest_duplicates`) that a human or the owning role confirms here.
    Auto-merging on text similarity would silently destroy the investigation
    history of whichever ticket lost."""
    issue = _require_issue(session, issue)
    target = _require_issue(session, duplicate_of)
    reason = _require_text(reason, "duplicate rationale")
    if target.id == issue.id:
        raise IssueError(f"{issue.issue_key} cannot be a duplicate of itself")

    # Walk the survivor's own duplicate chain: pointing A→B while B→A (or any
    # longer loop) leaves no ticket carrying the work.
    seen = {issue.id}
    cursor = target
    while cursor is not None:
        if cursor.id in seen:
            raise IssueError(
                f"marking {issue.issue_key} a duplicate of {target.issue_key} "
                "would create a duplicate cycle"
            )
        seen.add(cursor.id)
        cursor = (
            session.get(ExperimentIssue, cursor.duplicate_of_issue_id)
            if cursor.duplicate_of_issue_id
            else None
        )

    issue.duplicate_of_issue_id = target.id
    issue.disposition = IssueDisposition.DUPLICATE.value
    _touch(issue)
    return set_issue_status(
        session, issue, status=IssueStatus.DUPLICATE.value, actor=actor,
        actor_role=actor_role, reason=reason,
        event_type=IssueEventType.MARKED_DUPLICATE.value,
        payload={"duplicate_of": target.issue_key,
                 "duplicate_of_issue_id": target.id},
    )


def suggest_duplicates(
    session, issue, *, limit: int = 5
) -> list[dict]:
    """Heuristic duplicate SUGGESTIONS — never a merge (§2.6).

    Exact recurrence is the fingerprint's job. This is the softer question: does
    an open issue look like the same problem? It returns candidates with the
    reason each was suggested so a human can confirm one via `mark_duplicate`,
    and it deliberately has no write path of its own."""
    issue = _require_issue(session, issue)
    words = {w for w in re.findall(r"[a-z0-9]+", (issue.title or "").lower())
             if len(w) > 3}
    out: list[dict] = []
    for other in list_issues(session, open_only=True):
        if other.id == issue.id:
            continue
        reasons: list[str] = []
        if (
            issue.detector_fingerprint
            and other.detector_fingerprint == issue.detector_fingerprint
        ):
            reasons.append("identical detector fingerprint (exact recurrence)")
        if (
            issue.experiment_id is not None
            and other.experiment_id == issue.experiment_id
            and other.detector == issue.detector
            and issue.detector is not None
        ):
            reasons.append("same detector on the same experiment")
        other_words = {w for w in re.findall(r"[a-z0-9]+", (other.title or "").lower())
                       if len(w) > 3}
        overlap = words & other_words
        if words and len(overlap) >= max(2, len(words) // 2):
            reasons.append(f"title overlap: {', '.join(sorted(overlap))}")
        if reasons:
            out.append({
                "issue_key": other.issue_key,
                "title": other.title,
                "status": other.status,
                "owner_role": other.current_owner_role,
                "reasons": reasons,
                "confirmation_required": True,
            })
    return out[:limit]


def reopen_issue(
    session, issue, *, reason: str, actor: str, actor_role: str,
    owner_role: str | None = None,
) -> ExperimentIssue:
    """Reopen a RESOLVED or CLOSED_NO_ACTION issue into INVESTIGATING.

    The prior resolution is NOT deleted: the REOPENED event carries the previous
    `resolution_summary` and `resolved_at`, so "we thought this was fixed on the
    14th, and it came back" stays readable. Only the cached `resolved_at` clears,
    because the issue is once again open work."""
    issue = _require_issue(session, issue)
    reason = _require_text(reason, "reopen reason")
    prior_resolution = issue.resolution_summary
    prior_resolved_at = issue.resolved_at
    issue.resolved_at = None
    _touch(issue)
    set_issue_status(
        session, issue, status=IssueStatus.INVESTIGATING.value, actor=actor,
        actor_role=actor_role, reason=reason,
        event_type=IssueEventType.REOPENED.value,
        payload={
            "prior_resolution_summary": prior_resolution,
            "prior_resolved_at": str(prior_resolved_at) if prior_resolved_at else None,
        },
    )
    if owner_role is not None and str(owner_role) != issue.current_owner_role:
        assign_issue(
            session, issue, owner_role=owner_role, actor=actor,
            actor_role=actor_role, reason=f"reassigned on reopen: {reason}",
        )
    return issue


def record_recurrence(
    session, issue, *, actor: str, actor_role: str,
    observed_at: datetime | None = None, note: str | None = None,
) -> ExperimentIssue:
    """Record that the same problem was observed again (§8).

    An EXPLICIT write, and the only thing that may advance `occurrence_count` or
    `last_observed_at`. A Control Tower read must never do this: a report that
    mutates counters as a side effect of being rendered makes the counter a
    measure of how often somebody ran the report."""
    issue = _require_issue(session, issue)
    stamp = observed_at or _now()
    issue.occurrence_count = int(issue.occurrence_count or 1) + 1
    issue.last_observed_at = stamp
    if issue.first_observed_at is None:
        issue.first_observed_at = stamp
    _touch(issue)
    _event(
        session, issue, IssueEventType.RECURRENCE_OBSERVED.value,
        actor=actor, actor_role=actor_role, reason=note,
        payload={"occurrence_count": issue.occurrence_count,
                 "observed_at": str(stamp)},
        occurred_at=stamp,
    )
    return issue


# ---------------------------------------------------------------------------
# Rendering helpers (shared by the CLI and the Control Tower)
# ---------------------------------------------------------------------------


def issue_summary(session, issue) -> dict:
    """A compact row for listings — see `read.issue_summary`."""
    return read.issue_summary(session, _require_issue(session, issue))


def issue_detail(session, issue) -> dict:
    """The full investigation as JSON — see `read.issue_detail`."""
    return read.issue_detail(session, _require_issue(session, issue))


def issue_count_by(session, column: str) -> dict:
    """Open-issue counts grouped by one column."""
    field = {
        "status": ExperimentIssue.status,
        "classification": ExperimentIssue.classification,
        "owner": ExperimentIssue.current_owner_role,
        "severity": ExperimentIssue.severity,
        "priority": ExperimentIssue.priority,
    }.get(column)
    if field is None:
        raise IssueError(f"cannot group issues by {column!r}")
    rows = session.execute(
        select(field, func.count())
        .where(ExperimentIssue.status.in_(sorted(policy.OPEN_STATUSES)))
        .group_by(field)
    ).all()
    return {str(k): int(n) for k, n in rows}
