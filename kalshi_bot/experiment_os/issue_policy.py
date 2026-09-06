"""Issue-workflow enums, the status machine, routing policy and fingerprints.

Pure logic, no database — the same relationship `lifecycle.py` has to the
experiment state machine. The Control Tower (read-only, imports no write helper)
and the issue service both need these rules, and neither may own a private copy.

## What an issue is, and what it is emphatically not

An issue is the durable workflow object for a *problem*: what was observed, who
is investigating, what the evidence says, what remedy was decided, how it was
validated, and how it ended. Experiment OS remains the sole authority for
experiment identity, versions, epochs, arms, deployments, lifecycle state, gates,
gate results, platform snapshots/revisions, integrity events and enforcement.

An issue therefore **never** changes any of those. Opening, routing, accepting or
resolving one authorizes nothing: no transition, no gate, no verdict, no epoch,
no Version, no Platform Revision, no live arming, no change in real-money
exposure. It may *record* that one of those is required (`disposition`) and link
to the canonical record once it exists — which is the opposite of performing it.

Two consequences are load-bearing and easy to lose:

  * **Classification is not ownership.** `classification` says what kind of
    problem this is; `current_owner_role` says who is working it. A DATA problem
    can legitimately sit with Live Ops while it is being diagnosed.
  * **Uncertainty is a state, not a defect.** `UNCLASSIFIED` exists so a problem
    can be opened honestly before anyone knows whether the runtime is broken or
    the science is wrong. `recommend_owner` returns it rather than guessing,
    because a premature classification is how the wrong role spends a week.
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
from dataclasses import dataclass


class IssueClassification(enum.StrEnum):
    """What KIND of problem this is (§3.3) — never who owns it."""

    #: Hypothesis, selection rule, experiment design, frozen contract, gate basis,
    #: version semantics, or scientific interpretation.
    STRATEGY = "STRATEGY"
    #: Missing/corrupt/insufficient evidence, missing metric provider, missing
    #: experiment-specific transform, a provenance problem NOT yet proven to be a
    #: shared semantic change.
    DATA = "DATA"
    #: Contamination, broken lineage, mixed snapshots, invalid twin boundary,
    #: contract/deployment mismatch — evidence cannot be trusted.
    INTEGRITY = "INTEGRITY"
    #: A CONFIRMED shared-semantic change requiring Platform Change Review.
    #: Touching Experiment OS code is not this. Read `PLATFORM_SEMANTICS`.
    PLATFORM = "PLATFORM"
    #: Runtime, collector, deployment, order, execution, configuration, worker,
    #: admission, or real-money operational failure.
    OPS = "OPS"
    #: Insufficient evidence to distinguish the above. A legitimate resting state.
    UNCLASSIFIED = "UNCLASSIFIED"


#: The shared semantics whose change is the ONLY thing that makes an issue
#: `PLATFORM` (§2.4). A missing metric provider, a missing experiment-specific
#: transform, a broken collector, a wiring bug or a malformed experiment contract
#: are none of these — routing them to Platform Change Review invites a Platform
#: Revision that no evidence calls for.
PLATFORM_SEMANTICS: tuple[str, ...] = (
    "FEE_MODEL",
    "FILL_MODEL",
    "MARKET_TAXONOMY",
    "EXECUTION_SEMANTICS",
    "SETTLEMENT_INTERPRETATION",
    "RISK_SEMANTICS",
    "SHARED_DATA_PROVENANCE",
    "KALSHI_API_SEMANTICS",
    "SHARED_METRIC_DEFINITION",
    "EXPERIMENT_ENGINE_SEMANTICS",
)


class IssueOwnerRole(enum.StrEnum):
    """Roles that may OWN remediation (§3.4).

    `EXPERIMENT_CONTROL_TOWER` is deliberately absent: it is read-only, so it can
    detect and open a problem but can never be the role that fixes one. There is
    no generic `FIXER`/`INVESTIGATION` role either — an issue routes work to the
    existing role that owns the problem, which is the whole point of the design.
    """

    LIVE_OPS = "LIVE_OPS"
    RESEARCH_LAB = "RESEARCH_LAB"
    PLATFORM_CHANGE_REVIEW = "PLATFORM_CHANGE_REVIEW"
    LEGACY_MIGRATION = "LEGACY_MIGRATION"
    TASK_SPECIFIC = "TASK_SPECIFIC"
    OPERATOR = "OPERATOR"


#: Contexts an issue may be opened FROM. A superset of the owner roles: the
#: Control Tower detects most anomalies and `SYSTEM` covers automated detection,
#: but neither may become the owner.
DETECTOR_ONLY_ROLES: frozenset[str] = frozenset({"EXPERIMENT_CONTROL_TOWER", "SYSTEM"})
OPENING_ROLES: frozenset[str] = frozenset(
    {r.value for r in IssueOwnerRole} | set(DETECTOR_ONLY_ROLES)
)

#: Names that must never appear as an owner, with the reason, so the refusal
#: explains itself instead of reading as an enum typo.
REFUSED_OWNER_ROLES: dict[str, str] = {
    "EXPERIMENT_CONTROL_TOWER": (
        "the Control Tower is read-only and cannot own remediation — it detects "
        "and hands off"
    ),
    "SYSTEM": "automated detection is not a role that can do the work",
    "FIXER": (
        "there is no generic fixer role; route the issue to the existing role that "
        "owns the problem"
    ),
    "INVESTIGATION": (
        "there is no generic investigation role; ownership names a real session role"
    ),
}


class IssueStatus(enum.StrEnum):
    """Workflow status (§3.5).

    Deliberately disjoint from both lifecycle states (`PAPER`, `PAUSED`,
    `RETIRED`) and gate verdicts (`PASS`, `HOLD`, `BLOCKED_DATA`): an issue's
    workflow position is not an experiment's position and not a verdict, and
    sharing a word is how the two get confused in a report.
    """

    OPEN = "OPEN"
    TRIAGE = "TRIAGE"
    INVESTIGATING = "INVESTIGATING"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    VALIDATING = "VALIDATING"
    RESOLVED = "RESOLVED"
    CLOSED_NO_ACTION = "CLOSED_NO_ACTION"
    DUPLICATE = "DUPLICATE"


#: Statuses at which an issue is still live work — what "open" means everywhere
#: (Control Tower coverage, fingerprint recurrence, duplicate detection).
OPEN_STATUSES: frozenset[str] = frozenset(
    {
        IssueStatus.OPEN.value,
        IssueStatus.TRIAGE.value,
        IssueStatus.INVESTIGATING.value,
        IssueStatus.ACTION_REQUIRED.value,
        IssueStatus.VALIDATING.value,
    }
)

#: Terminal-ish statuses. `DUPLICATE` is genuinely terminal; the other two are
#: reopenable, which is why "closed" is not the same as "may never come back".
CLOSED_STATUSES: frozenset[str] = frozenset(
    {
        IssueStatus.RESOLVED.value,
        IssueStatus.CLOSED_NO_ACTION.value,
        IssueStatus.DUPLICATE.value,
    }
)

# The legal status machine.
#
# The forward path is the handoff's: OPEN → TRIAGE → INVESTIGATING →
# ACTION_REQUIRED → VALIDATING → RESOLVED, with early exits to
# CLOSED_NO_ACTION/DUPLICATE and an explicit reopen.
#
# Three BACK EDGES are added beyond that shape, and they are not an oversight:
# without them a FAILED validation is unrecordable — the ticket can neither be
# resolved (validation failed) nor moved anywhere else, so the only way to
# proceed would be to lie about the outcome or open a second ticket for the same
# problem. Both are worse than a documented reverse arrow. These are workflow
# moves, not lifecycle rollbacks: every one writes an event, so the earlier
# proposal, disposition and failed validation all remain visible.
_LEGAL_STATUS: dict[str, frozenset[str]] = {
    IssueStatus.OPEN.value: frozenset(
        {
            IssueStatus.TRIAGE.value,
            IssueStatus.CLOSED_NO_ACTION.value,
            IssueStatus.DUPLICATE.value,
        }
    ),
    IssueStatus.TRIAGE.value: frozenset(
        {
            IssueStatus.INVESTIGATING.value,
            IssueStatus.CLOSED_NO_ACTION.value,
            IssueStatus.DUPLICATE.value,
        }
    ),
    IssueStatus.INVESTIGATING.value: frozenset(
        {
            IssueStatus.ACTION_REQUIRED.value,
            IssueStatus.CLOSED_NO_ACTION.value,
            IssueStatus.DUPLICATE.value,
        }
    ),
    IssueStatus.ACTION_REQUIRED.value: frozenset(
        {
            IssueStatus.VALIDATING.value,
            IssueStatus.DUPLICATE.value,
            IssueStatus.INVESTIGATING.value,  # back edge: the proposed fix was wrong
        }
    ),
    IssueStatus.VALIDATING.value: frozenset(
        {
            IssueStatus.RESOLVED.value,
            IssueStatus.ACTION_REQUIRED.value,  # back edge: validation FAILED
            IssueStatus.INVESTIGATING.value,  # back edge: validation reopened the question
        }
    ),
    # Reopen. Not a fresh ticket: the prior resolution stays in the event history.
    IssueStatus.RESOLVED.value: frozenset({IssueStatus.INVESTIGATING.value}),
    IssueStatus.CLOSED_NO_ACTION.value: frozenset({IssueStatus.INVESTIGATING.value}),
    # A confirmed duplicate is terminal — the surviving ticket carries the work.
    IssueStatus.DUPLICATE.value: frozenset(),
}


class IssueSeverity(enum.StrEnum):
    """Present IMPACT (§3.6) — what this is doing right now."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IssuePriority(enum.StrEnum):
    """Work ORDERING (§3.6) — deliberately separate from severity, because a
    high-impact problem nobody can act on yet is not the next thing to do."""

    P3 = "P3"
    P2 = "P2"
    P1 = "P1"
    P0 = "P0"


#: Worst-first ordering. Reports and listings sort by these rather than
#: alphabetically, because "CRITICAL" sorting above "HIGH" by luck of the
#: alphabet stops being true the moment an enum member is renamed.
SEVERITY_ORDER: tuple[str, ...] = (
    IssueSeverity.CRITICAL.value,
    IssueSeverity.HIGH.value,
    IssueSeverity.MEDIUM.value,
    IssueSeverity.LOW.value,
    IssueSeverity.INFO.value,
)
PRIORITY_ORDER: tuple[str, ...] = (
    IssuePriority.P0.value,
    IssuePriority.P1.value,
    IssuePriority.P2.value,
    IssuePriority.P3.value,
)


def severity_rank(value: str | None) -> int:
    """Sort key: 0 is worst. An unknown value sorts last, never first."""
    try:
        return SEVERITY_ORDER.index(str(value))
    except ValueError:
        return len(SEVERITY_ORDER)


def priority_rank(value: str | None) -> int:
    """Sort key: 0 is most urgent. An unknown value sorts last, never first."""
    try:
        return PRIORITY_ORDER.index(str(value))
    except ValueError:
        return len(PRIORITY_ORDER)


class IssueDisposition(enum.StrEnum):
    """The recorded remedy DECISION (§5) — recorded, never performed.

    `NEW_VERSION`, `NEW_EPOCH`, `PLATFORM_REVISION`, `PAUSE_OR_STAND_DOWN` and
    `RETIRE` all name canonical actions that continue to run through their own
    services and approval rules. Recording one here says "this is what must
    happen"; the issue then links the canonical record produced after it does.
    """

    NONE = "NONE"
    OPS_REPAIR = "OPS_REPAIR"
    DATA_REPAIR = "DATA_REPAIR"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    NEW_VERSION = "NEW_VERSION"
    NEW_EPOCH = "NEW_EPOCH"
    PLATFORM_REVISION = "PLATFORM_REVISION"
    PAUSE_OR_STAND_DOWN = "PAUSE_OR_STAND_DOWN"
    RETIRE = "RETIRE"
    NO_ACTION = "NO_ACTION"
    DUPLICATE = "DUPLICATE"


#: Dispositions that REQUIRE the matching boolean to be true. The booleans are
#: tri-state on purpose (NULL = not yet known, which is not the same as "no"), so
#: this mapping is what stops a disposition from being recorded while the system
#: still says the thing it requires is unknown or false.
DISPOSITION_REQUIRES: dict[str, str] = {
    IssueDisposition.NEW_VERSION.value: "requires_new_version",
    IssueDisposition.NEW_EPOCH.value: "requires_new_epoch",
    IssueDisposition.PLATFORM_REVISION.value: "requires_platform_revision",
    IssueDisposition.PAUSE_OR_STAND_DOWN.value: "requires_pause_or_stand_down",
}


class IssueEventType(enum.StrEnum):
    """Append-only workflow history (§4.1). Every consequential mutation writes
    exactly one of these in the same transaction as the change it describes."""

    CREATED = "CREATED"
    TRIAGED = "TRIAGED"
    CLASSIFIED = "CLASSIFIED"
    ASSIGNED = "ASSIGNED"
    TRANSFERRED = "TRANSFERRED"
    STATUS_CHANGED = "STATUS_CHANGED"
    EVIDENCE_ADDED = "EVIDENCE_ADDED"
    FIX_PROPOSED = "FIX_PROPOSED"
    DISPOSITION_DECIDED = "DISPOSITION_DECIDED"
    VALIDATION_PLANNED = "VALIDATION_PLANNED"
    VALIDATION_RECORDED = "VALIDATION_RECORDED"
    RESOLVED = "RESOLVED"
    CLOSED_NO_ACTION = "CLOSED_NO_ACTION"
    MARKED_DUPLICATE = "MARKED_DUPLICATE"
    REOPENED = "REOPENED"
    LINK_ADDED = "LINK_ADDED"
    RECURRENCE_OBSERVED = "RECURRENCE_OBSERVED"


class IssueEvidenceType(enum.StrEnum):
    """What a piece of evidence IS (§4.2).

    Note what is missing: chat. Unresolved session prose is not evidence — it is
    not durable, not addressable, and not reviewable. `MANUAL_OBSERVATION` is the
    honest place for a human's direct observation, and it still needs a summary
    and a source reference somebody else can check.
    """

    CONTROL_TOWER = "CONTROL_TOWER"
    GATE_RESULT = "GATE_RESULT"
    OPS_RESULT = "OPS_RESULT"
    LOG = "LOG"
    DATABASE_QUERY = "DATABASE_QUERY"
    RESEARCH_DOCUMENT = "RESEARCH_DOCUMENT"
    PR = "PR"
    COMMIT = "COMMIT"
    INTEGRITY_EVENT = "INTEGRITY_EVENT"
    PLATFORM_REVIEW = "PLATFORM_REVIEW"
    MANUAL_OBSERVATION = "MANUAL_OBSERVATION"
    OTHER = "OTHER"


class IssueLinkType(enum.StrEnum):
    """Supporting or multiple references (§4.3).

    Foreign-key columns on the issue itself remain preferable for PRIMARY scope —
    canonical identity belongs in a foreign key, not in a free-text reference.
    This table is for the second gate, the third PR, the successor issue.
    """

    PR = "PR"
    COMMIT = "COMMIT"
    GITHUB_ISSUE = "GITHUB_ISSUE"
    RESEARCH_DOC = "RESEARCH_DOC"
    OPS_RESULT = "OPS_RESULT"
    EXPERIMENT = "EXPERIMENT"
    VERSION = "VERSION"
    EPOCH = "EPOCH"
    DEPLOYMENT = "DEPLOYMENT"
    GATE = "GATE"
    GATE_RESULT = "GATE_RESULT"
    INTEGRITY_EVENT = "INTEGRITY_EVENT"
    PLATFORM_REVISION = "PLATFORM_REVISION"
    SUCCESSOR_ISSUE = "SUCCESSOR_ISSUE"
    OTHER = "OTHER"


class IssuePolicyError(ValueError):
    """An issue-workflow rule was violated (bad enum value, illegal transition)."""


class IllegalIssueTransition(IssuePolicyError):
    """The requested status change is not legal in the issue state machine."""


def validate_issue_status_transition(current: str, target: str) -> None:
    """Raise `IllegalIssueTransition` unless `current → target` is legal."""
    cur = coerce(IssueStatus, current, "status")
    tgt = coerce(IssueStatus, target, "status")
    if cur == tgt:
        raise IllegalIssueTransition(
            f"self-transition {cur} → {tgt} is not a transition"
        )
    allowed = _LEGAL_STATUS[cur]
    if tgt not in allowed:
        legal = ", ".join(sorted(allowed)) or "(none — this status is terminal)"
        raise IllegalIssueTransition(
            f"illegal issue transition {cur} → {tgt}; legal from {cur}: {legal}"
        )


def coerce(enum_cls: type[enum.StrEnum], value, label: str) -> str:
    """Validate `value` against `enum_cls`, returning its canonical string.

    Centralised so every refusal names the field AND the legal set — a bare
    ValueError from an enum constructor tells the caller nothing about which of
    six enums it just failed."""
    try:
        return enum_cls(value).value
    except ValueError:
        legal = ", ".join(sorted(m.value for m in enum_cls))
        raise IssuePolicyError(
            f"{label} {value!r} is not valid; legal values: {legal}"
        ) from None


def validate_owner_role(value) -> str:
    """Validate an OWNER role, refusing the read-only and generic roles by name."""
    text = str(value)
    if text in REFUSED_OWNER_ROLES:
        raise IssuePolicyError(
            f"{text} may not own an issue: {REFUSED_OWNER_ROLES[text]}"
        )
    return coerce(IssueOwnerRole, value, "owner role")


def validate_opening_role(value) -> str:
    """Validate a DETECTING/opening role — owner roles plus the read-only ones."""
    text = str(value)
    if text not in OPENING_ROLES:
        legal = ", ".join(sorted(OPENING_ROLES))
        raise IssuePolicyError(
            f"opening role {text!r} is not valid; legal values: {legal}"
        )
    return text


# ---------------------------------------------------------------------------
# Issue identity
# ---------------------------------------------------------------------------

#: `XOS-000123`. Zero-padded to a fixed width so lexical order equals numeric
#: order, and short enough to type as a CLI argument. Identity is never the
#: title: a title is a description, and descriptions get rewritten.
ISSUE_KEY_PREFIX = "XOS-"
ISSUE_KEY_RE = re.compile(r"^XOS-(\d{6,})$")


def format_issue_key(number: int) -> str:
    return f"{ISSUE_KEY_PREFIX}{int(number):06d}"


def parse_issue_key(text: str) -> int | None:
    """The numeric part of an issue key, or None if this is not one."""
    match = ISSUE_KEY_RE.match((text or "").strip().upper())
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Detector fingerprints (§8)
# ---------------------------------------------------------------------------
#
# Detector names are constants rather than free strings so the Control Tower's
# candidate generation and an issue opened by hand agree on what "the same
# problem" is. A fingerprint that only matches when two callers happen to type
# the same word is not a fingerprint.

DETECTOR_RESOLVER_DEGRADED = "enforcement.resolver_degraded"
DETECTOR_UNSTAMPED_LINEAGE = "enforcement.unstamped_lineage"
DETECTOR_INTEGRITY_EVENT = "integrity.unresolved_event"
DETECTOR_PLATFORM_IMPACT = "platform.unresolved_impact"
DETECTOR_REVISION_UNSAFE = "platform.revision_unsafe_to_activate"
DETECTOR_COLLECTOR_HEALTH = "ops.collector_health"
DETECTOR_MISSING_TWIN = "ops.live_deployment_without_twin"
DETECTOR_ZERO_EVIDENCE = "experiment.zero_evidence"
#: The ESCALATION of zero evidence, and a different claim. `zero_evidence` says
#: "this book has produced nothing", which is also true of every experiment on
#: the day it is registered. This one says "an ARMED arm has produced nothing for
#: longer than a settlement day, no gate is blocked to explain it, and something
#: else was demonstrably trading in the same window" — i.e. the silence is
#: specific to this arm rather than to the day. Registration is not
#: configuration: an arm can be a perfectly formed Experiment OS object and still
#: be absent from what the worker actually runs (2026-09-05,
#: `mmsell-correlation-cap`). Both detectors fire; see the Control Tower's
#: `_silent_arms` for why the weaker one is deliberately not suppressed.
DETECTOR_ARMED_BUT_SILENT = "experiment.armed_but_silent"
DETECTOR_GATE_BLOCKED = "gate.blocked"
DETECTOR_CONTRACT_DEFECT = "contract.defect"
#: Not a detector at all: the fixed source value for a problem a PERSON found —
#: Live Ops watching the runtime, Research Lab reading evidence, an integrity
#: review, or an operator noticing something the Tower's detector surface does
#: not cover. It is recorded so the provenance of every issue is legible, and it
#: deliberately carries NO fingerprint: fabricating one would make a manual
#: ticket look like it covers a candidate that was never detected, which is the
#: exact confusion `open_issue_from_candidate` exists to prevent.
DETECTOR_MANUAL = "manual.reported"

#: The exact, ordered fingerprint components. Nothing else may enter the hash:
#: a fingerprint identifies a PROBLEM SCOPE, so a volatile value (current P&L,
#: an age in minutes, a row count) would make the same recurring anomaly hash
#: differently every run and defeat recurrence detection entirely.
FINGERPRINT_FIELDS: tuple[str, ...] = (
    "detector",
    "experiment_id",
    "version_id",
    "epoch_id",
    "deployment_id",
    "gate_id",
    "anomaly_kind",
)


def issue_fingerprint(
    *,
    detector: str,
    experiment_id: int | None = None,
    version_id: int | None = None,
    epoch_id: int | None = None,
    deployment_id: int | None = None,
    gate_id: int | None = None,
    anomaly_kind: str | None = None,
) -> str:
    """Deterministic sha256 over the fixed scope components.

    Keyword-only and closed over `FINGERPRINT_FIELDS`: a caller cannot smuggle an
    extra component in, which is what keeps two independent detections of the
    same problem hashing alike. Same recipe as `service.canonical_hash` (sorted
    keys, tight separators) and pinned to it by test."""
    payload = {
        "detector": str(detector),
        "experiment_id": experiment_id,
        "version_id": version_id,
        "epoch_id": epoch_id,
        "deployment_id": deployment_id,
        "gate_id": gate_id,
        "anomaly_kind": anomaly_kind,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Routing policy (§6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnerRecommendation:
    """A routing recommendation that states its own confidence.

    `classification == UNCLASSIFIED` is a real answer, not a failure to compute
    one: it means the evidence cannot yet distinguish a broken runtime from a
    wrong selection rule. `requires_operator_triage` is stronger still — even the
    owner is a guess, so an operator should look before anyone spends time.
    """

    owner_role: str
    classification: str
    severity: str
    priority: str
    rationale: str
    #: Suggested remedy where the condition already determines one (a proven
    #: malformed frozen contract is always a new Version, never an in-place fix).
    disposition: str | None = None
    requires_operator_triage: bool = False

    def as_json(self) -> dict:
        return {
            "owner_role": self.owner_role,
            "classification": self.classification,
            "severity": self.severity,
            "priority": self.priority,
            "rationale": self.rationale,
            "disposition": self.disposition,
            "requires_operator_triage": self.requires_operator_triage,
        }


_LIVE_OPS = IssueOwnerRole.LIVE_OPS.value
_RESEARCH = IssueOwnerRole.RESEARCH_LAB.value
_PCR = IssueOwnerRole.PLATFORM_CHANGE_REVIEW.value
_MIGRATION = IssueOwnerRole.LEGACY_MIGRATION.value


def recommend_owner(
    *,
    detector: str | None = None,
    anomaly_kind: str | None = None,
    gate_verdict: str | None = None,
    integrity_cause: str | None = None,
    runtime_verified_healthy: bool | None = None,
    shared_semantic_confirmed: bool = False,
    malformed_frozen_contract: bool = False,
    selection_rule_question: bool = False,
    new_hypothesis: bool = False,
    legacy_history_problem: bool = False,
    unexpected_live_exposure: bool = False,
) -> OwnerRecommendation:
    """Recommend an initial owner, classification, severity and priority.

    Explicit and side-effect free so it can be tested directly and so the Control
    Tower and the service cannot disagree about routing. It RECOMMENDS: a caller
    may always override with a rationale, and `create_issue` records the override
    as such rather than silently substituting its own opinion.

    Precedence runs strongest-evidence-first. Real-money exposure outranks
    everything because being wrong about it is the expensive direction; a
    CONFIRMED shared-semantic change outranks the gate verdict because the
    verdict is a symptom of it; and every "we do not know yet" path lands on Live
    Ops for operational diagnosis with `UNCLASSIFIED` intact (§2.3) rather than
    on a guessed classification.
    """
    # 1. Real money first. Unexpected exposure or an inability to drain is the
    #    one condition where a wrong routing costs actual dollars.
    if unexpected_live_exposure:
        return OwnerRecommendation(
            _LIVE_OPS, IssueClassification.OPS.value,
            IssueSeverity.CRITICAL.value, IssuePriority.P0.value,
            "unexpected real-money exposure or an unmanageable live position — "
            "Live Ops owns real-money safety",
        )

    # 2. A shared semantic that actually changed. This is the ONLY route into
    #    Platform Change Review by classification, and it must be confirmed or
    #    concretely proposed — never "it touches Experiment OS code".
    if shared_semantic_confirmed:
        return OwnerRecommendation(
            _PCR, IssueClassification.PLATFORM.value,
            IssueSeverity.HIGH.value, IssuePriority.P1.value,
            "a confirmed/proposed change to shared experiment semantics — the "
            "Platform Impact engine is the procedure",
            disposition=IssueDisposition.PLATFORM_REVISION.value,
        )

    if legacy_history_problem:
        return OwnerRecommendation(
            _MIGRATION, IssueClassification.DATA.value,
            IssueSeverity.LOW.value, IssuePriority.P3.value,
            "unmapped or incorrect legacy history — Legacy Migration is the "
            "transitional owner",
        )

    # 3. A PROVEN defect in a frozen contract. Never an in-place repair: a frozen
    #    version is immutable, so the remedy is a corrected successor Version.
    if malformed_frozen_contract:
        return OwnerRecommendation(
            _RESEARCH, IssueClassification.STRATEGY.value,
            IssueSeverity.HIGH.value, IssuePriority.P1.value,
            "a frozen experiment contract is defective — a frozen Version cannot "
            "be repaired in place; a corrected native successor Version is the remedy",
            disposition=IssueDisposition.NEW_VERSION.value,
        )

    # 4. Evaluator verdicts. BLOCKED_DATA is the one most often mis-routed:
    #    an unimplemented metric provider changes no shared semantic.
    verdict = (gate_verdict or "").upper()
    if verdict == "BLOCKED_PLATFORM":
        return OwnerRecommendation(
            _PCR, IssueClassification.PLATFORM.value,
            IssueSeverity.HIGH.value, IssuePriority.P1.value,
            "BLOCKED_PLATFORM — evidence comparability across a platform revision "
            "is what is blocking, which is Platform Change Review's subject",
        )
    if verdict == "BLOCKED_DATA":
        return OwnerRecommendation(
            _RESEARCH, IssueClassification.DATA.value,
            IssueSeverity.MEDIUM.value, IssuePriority.P2.value,
            "BLOCKED_DATA — a canonical metric provider or experiment-specific "
            "evidence transform has to be implemented (Research Lab, or "
            "task-specific Experiment OS metrics work). NOT a Platform Revision: "
            "writing a provider that never existed changes no shared semantic",
        )
    if verdict == "BLOCKED_INTEGRITY":
        return _route_integrity(
            integrity_cause, anomaly_kind,
            severity=IssueSeverity.HIGH.value, priority=IssuePriority.P1.value,
            label="BLOCKED_INTEGRITY",
        )

    # 5. Detector-driven routing for anomalies the Control Tower already finds.
    if detector == DETECTOR_INTEGRITY_EVENT:
        return _route_integrity(
            integrity_cause, anomaly_kind,
            severity=IssueSeverity.HIGH.value, priority=IssuePriority.P1.value,
            label="unresolved integrity event",
        )
    if detector in (DETECTOR_PLATFORM_IMPACT, DETECTOR_REVISION_UNSAFE):
        return OwnerRecommendation(
            _PCR, IssueClassification.PLATFORM.value,
            IssueSeverity.HIGH.value, IssuePriority.P1.value,
            "an unresolved platform-impact disposition or an unsafe pending "
            "revision — the Platform Impact engine owns both",
        )
    if detector == DETECTOR_COLLECTOR_HEALTH:
        # A stalled collector starves evidence and fails no gate, which is exactly
        # why it needs a ticket rather than a line in a report nobody re-reads.
        stale = (anomaly_kind or "").upper() == "STALE"
        return OwnerRecommendation(
            _LIVE_OPS, IssueClassification.OPS.value,
            IssueSeverity.MEDIUM.value,
            IssuePriority.P1.value if stale else IssuePriority.P2.value,
            f"collector {anomaly_kind or 'health'} — Live Ops owns collector "
            "health; starved evidence fails no gate",
            disposition=IssueDisposition.OPS_REPAIR.value,
        )
    if detector == DETECTOR_MISSING_TWIN:
        return OwnerRecommendation(
            _LIVE_OPS, IssueClassification.OPS.value,
            IssueSeverity.HIGH.value, IssuePriority.P1.value,
            "a live deployment without a valid twin — the live/paper comparison "
            "that detects adverse selection is missing; Live Ops verifies the "
            "arming and boundary",
        )
    if detector in (DETECTOR_RESOLVER_DEGRADED, DETECTOR_UNSTAMPED_LINEAGE):
        return OwnerRecommendation(
            _LIVE_OPS, IssueClassification.OPS.value,
            IssueSeverity.HIGH.value, IssuePriority.P1.value,
            "lineage resolution degraded or something traded without lineage — "
            "Live Ops owns runtime admission and the resolver",
        )
    if detector == DETECTOR_CONTRACT_DEFECT:
        return OwnerRecommendation(
            _RESEARCH, IssueClassification.STRATEGY.value,
            IssueSeverity.HIGH.value, IssuePriority.P1.value,
            "a proven defect in a registered contract — only a corrected native "
            "successor Version clears it",
            disposition=IssueDisposition.NEW_VERSION.value,
        )

    # 5b. Armed but silent. Same classification logic as zero evidence — the
    #     cause is genuinely unknown and Live Ops looks first — but a materially
    #     stronger signal, so it is not left at LOW/P2 beside a day-one book.
    #     It carries no disposition: "the worker never saw this arm" and "the
    #     filters ate every candidate" both fit, and they need opposite remedies.
    if detector == DETECTOR_ARMED_BUT_SILENT:
        if runtime_verified_healthy:
            return OwnerRecommendation(
                _RESEARCH, IssueClassification.STRATEGY.value,
                IssueSeverity.MEDIUM.value, IssuePriority.P2.value,
                "the runtime is verified healthy and the arm is still producing "
                "nothing while its reference trades — the question is now whether "
                "the selection rule can ever fire",
            )
        return OwnerRecommendation(
            _LIVE_OPS, IssueClassification.UNCLASSIFIED.value,
            IssueSeverity.MEDIUM.value, IssuePriority.P1.value,
            "an ARMED arm has collected nothing past the silence threshold while "
            "a sibling arm or the rest of the platform traded, and no gate is "
            "blocked to explain it. Registration is not configuration: check "
            "first that the runtime actually carries this tag (env overrides, "
            "book allowlists, admission), then filters and opportunity. "
            "Classification stays UNCLASSIFIED until that answer exists",
            requires_operator_triage=False,
        )

    # 6. Zero evidence: the worked example of diagnosis before classification.
    #    The Tower can see the absence; it cannot tell whether the runtime is
    #    broken, enforcement rejected the tag, filters ate every candidate, or
    #    there is genuinely nothing qualifying. Live Ops answers that first.
    if detector == DETECTOR_ZERO_EVIDENCE:
        if runtime_verified_healthy:
            return OwnerRecommendation(
                _RESEARCH, IssueClassification.STRATEGY.value,
                IssueSeverity.LOW.value, IssuePriority.P2.value,
                "runtime verified healthy and candidate production works, yet no "
                "qualifying opportunities — the question is now whether the "
                "selection rule is scientifically appropriate",
            )
        return OwnerRecommendation(
            _LIVE_OPS, IssueClassification.UNCLASSIFIED.value,
            IssueSeverity.LOW.value, IssuePriority.P2.value,
            "a registered experiment with zero evidence and unknown cause — Live "
            "Ops performs the operational diagnosis first (runtime/config/wiring, "
            "enforcement rejection, filters, or genuinely absent opportunities). "
            "Classification stays UNCLASSIFIED until that answer exists",
            requires_operator_triage=False,
        )

    # 7. Explicitly scientific questions, once nothing operational is in doubt.
    if new_hypothesis:
        return OwnerRecommendation(
            _RESEARCH, IssueClassification.STRATEGY.value,
            IssueSeverity.INFO.value, IssuePriority.P3.value,
            "a new hypothesis or successor experiment — Research Lab owns the "
            "idea → probe → registered experiment path",
            disposition=IssueDisposition.RESEARCH_ONLY.value,
        )
    if selection_rule_question:
        if runtime_verified_healthy is False:
            return OwnerRecommendation(
                _LIVE_OPS, IssueClassification.OPS.value,
                IssueSeverity.MEDIUM.value, IssuePriority.P1.value,
                "a selection-rule question was raised, but the runtime is NOT "
                "verified healthy — the operational cause must be excluded before "
                "the criteria can be judged",
            )
        return OwnerRecommendation(
            _RESEARCH, IssueClassification.STRATEGY.value,
            IssueSeverity.LOW.value, IssuePriority.P2.value,
            "a selection-rule / evidence-design question — Research Lab owns the "
            "experiment contract and its scientific basis",
        )

    # 8. Genuinely ambiguous. Live Ops diagnoses operationally FIRST, and the
    #    classification stays UNCLASSIFIED so nobody reads the routing as a claim
    #    that the problem is operational.
    return OwnerRecommendation(
        _LIVE_OPS, IssueClassification.UNCLASSIFIED.value,
        IssueSeverity.MEDIUM.value, IssuePriority.P2.value,
        "insufficient evidence to classify — route to Live Ops for operational "
        "diagnosis first, or leave with the operator to triage. The owner here is "
        "who looks first, NOT a claim that the cause is operational",
        requires_operator_triage=True,
    )


#: Integrity-event kinds whose cause is already known from the kind itself. Kept
#: small and explicit: guessing a cause from a kind name is how a runtime bug
#: gets routed to Platform Change Review and sits there.
_INTEGRITY_KIND_CAUSE: dict[str, str] = {
    "EXPERIMENT_CONFIG_DRIFT": "shared_semantics",
    "HELD_CONSTANT_CHANGED": "shared_semantics",
}

#: The three causes §6 recognises for an integrity problem, and who owns each.
INTEGRITY_CAUSE_OWNER: dict[str, tuple[str, str]] = {
    "runtime": (_LIVE_OPS, IssueClassification.OPS.value),
    "contract": (_RESEARCH, IssueClassification.STRATEGY.value),
    "shared_semantics": (_PCR, IssueClassification.PLATFORM.value),
}


def _route_integrity(
    cause: str | None, anomaly_kind: str | None, *,
    severity: str, priority: str, label: str,
) -> OwnerRecommendation:
    """Route an integrity problem BY CAUSE, never by the word 'integrity'.

    The same verdict has three different owners depending on what produced it,
    so an unknown cause returns Live Ops for diagnosis with the classification
    left INTEGRITY — trustworthy-evidence is what is broken — rather than
    picking one of the three and being right a third of the time."""
    resolved = cause or _INTEGRITY_KIND_CAUSE.get((anomaly_kind or "").upper())
    if resolved in INTEGRITY_CAUSE_OWNER:
        owner, classification = INTEGRITY_CAUSE_OWNER[resolved]
        return OwnerRecommendation(
            owner, classification, severity, priority,
            f"{label} caused by {resolved.replace('_', ' ')} — routed by cause",
        )
    return OwnerRecommendation(
        _LIVE_OPS, IssueClassification.INTEGRITY.value, severity, priority,
        f"{label} with an undetermined cause — Live Ops establishes whether "
        "runtime/execution/collector malfunction explains it; if not, the cause "
        "is an experiment-contract problem (Research Lab) or a shared semantic "
        "change (Platform Change Review). Do not guess",
        requires_operator_triage=True,
    )
