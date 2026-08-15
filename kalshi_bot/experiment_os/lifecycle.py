"""Lifecycle enums and the legal-transition validator — pure logic, no database.

The spec's state machine (`docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md` §7) in executable
form. Everything here is deliberately side-effect free so the same rules can be applied
by the service layer, by tests, and later by CI/enforcement checks without a session.

The load-bearing rules, verbatim from the spec:

  * Legal forward path: IDEA → PROBE → PAPER → LIVE_CANARY → PRODUCTION. Every arrow is
    a gate; skipping a stage is illegal.
  * PASS/FAIL/HOLD/BLOCKED_* are GATE VERDICTS, not lifecycle states.
  * Any active state may enter PAUSED; PAUSED may return only to the state it paused
    from, and the resume is explicitly audited.
  * RETIRED is terminal. A revived concept creates a successor experiment/version that
    references the retired predecessor — it never reopens the old record.
  * No silent rollback: a PAPER experiment that changes its scientific rules does not
    "go back to PROBE" on the same version; that is a new version or successor.
  * PAPER → LIVE_CANARY and LIVE_CANARY → PRODUCTION (and any real-money risk increase)
    require explicit operator approval.
"""

from __future__ import annotations

import enum


class LifecycleState(enum.StrEnum):
    """Canonical experiment lifecycle states (spec §7)."""

    IDEA = "IDEA"
    PROBE = "PROBE"
    PAPER = "PAPER"
    LIVE_CANARY = "LIVE_CANARY"
    PRODUCTION = "PRODUCTION"
    PAUSED = "PAUSED"
    RETIRED = "RETIRED"


# The legal forward path, in order. Adjacency in this tuple is the ONLY legal forward move.
FORWARD_PATH: tuple[LifecycleState, ...] = (
    LifecycleState.IDEA,
    LifecycleState.PROBE,
    LifecycleState.PAPER,
    LifecycleState.LIVE_CANARY,
    LifecycleState.PRODUCTION,
)

# States an experiment actively operates in (may be paused from; may be retired from).
ACTIVE_STATES: frozenset[LifecycleState] = frozenset(FORWARD_PATH)

# Transitions that require explicit operator approval (spec §27): entering real money,
# and expanding it. Recorded on the audit row as `approved_by`.
APPROVAL_REQUIRED: frozenset[tuple[LifecycleState, LifecycleState]] = frozenset(
    {
        (LifecycleState.PAPER, LifecycleState.LIVE_CANARY),
        (LifecycleState.LIVE_CANARY, LifecycleState.PRODUCTION),
    }
)


class GateVerdict(enum.StrEnum):
    """Gate evaluation statuses (spec §11.1) — verdicts, never lifecycle states."""

    PASS = "PASS"
    FAIL = "FAIL"
    HOLD = "HOLD"
    BLOCKED_DATA = "BLOCKED_DATA"
    BLOCKED_INTEGRITY = "BLOCKED_INTEGRITY"
    BLOCKED_PLATFORM = "BLOCKED_PLATFORM"


class ArmRole(enum.StrEnum):
    """Pre-declared branch roles within an experiment version (spec §5.4)."""

    TREATMENT = "treatment"
    CONTROL = "control"
    SECONDARY = "secondary"
    BENCHMARK = "benchmark"
    NO_TRADE_CONTROL = "no_trade_control"


class DeploymentKind(enum.StrEnum):
    """What a deployment concretely is (spec §5.6). A paper twin is its own kind so the
    live/twin pair is a first-class, queryable relationship rather than a tag suffix."""

    PAPER = "paper"
    LIVE = "live"
    PAPER_TWIN = "paper_twin"
    PROBE = "probe"


class RevisionStatus(enum.StrEnum):
    """Platform revision lifecycle (spec §16.2)."""

    PENDING = "pending"
    ACTIVE = "active"
    RETIRED = "retired"


class ImpactClass(enum.StrEnum):
    """Platform change impact classes (spec §18)."""

    I0_OBSERVABILITY_ONLY = "I0"
    I1_EXACTLY_NORMALIZABLE = "I1"
    I2_SAMPLE_BOUNDARY = "I2"
    I3_EXPERIMENT_DEFINITION = "I3"
    I4_SAFETY_CRITICAL = "I4"


class ImpactAction(enum.StrEnum):
    """Per-experiment action assigned when a platform revision lands (spec §17)."""

    NO_ACTION = "NO_ACTION"
    RECOMPUTE = "RECOMPUTE"
    NEW_EPOCH = "NEW_EPOCH"
    NEW_EXPERIMENT_VERSION = "NEW_EXPERIMENT_VERSION"
    PAUSE = "PAUSE"
    RETIRE = "RETIRE"


class LegacyClass(enum.StrEnum):
    """Migration classification for pre-Experiment-OS strategies (spec §22.1)."""

    ACTIVE_LIVE = "ACTIVE_LIVE"
    ACTIVE_PAPER = "ACTIVE_PAPER"
    ACTIVE_PROBE_OR_HOLD = "ACTIVE_PROBE_OR_HOLD"
    RETIRED_OR_KILLED = "RETIRED_OR_KILLED"
    HISTORICAL_UNTRACKED = "HISTORICAL_UNTRACKED"
    EVO_LEGACY = "EVO_LEGACY"


class MigrationIntegrity(enum.StrEnum):
    """How trustworthy a legacy reconstruction is (spec §23). No tool may silently
    upgrade an uncertain record to VERIFIED — the level is always caller-stated."""

    A_VERIFIED = "A"
    B_PARTIAL = "B"
    C_CONTEXT_ONLY = "C"
    D_UNTRACKED = "D"


class EvidenceClass(enum.StrEnum):
    """How historical rows may be used after migration (spec §22.3): certified evidence
    can inform a promotion gate; context-only can never satisfy one."""

    CERTIFIED_LEGACY_EVIDENCE = "certified_legacy_evidence"
    CONTEXT_ONLY = "context_only"


class EnforcementMode(enum.StrEnum):
    """System-wide enforcement posture (spec §4.2). The foundation release runs OFF:
    schema/read path exists, nothing is enforced at runtime. The explicit enforcement
    cutover (recorded, not inferred) ships with the enforcement PR."""

    OFF = "OFF"
    WARN = "WARN"
    NEW_ONLY = "NEW_ONLY"
    STRICT = "STRICT"


# The foundation release's posture. Later PRs move this to a recorded, DB-backed value;
# until then it is a constant so callers can already branch on it honestly.
CURRENT_ENFORCEMENT_MODE = EnforcementMode.OFF


class IllegalTransition(ValueError):
    """The requested lifecycle transition violates the spec's state machine."""


class OperatorApprovalRequired(IllegalTransition):
    """The transition is legal in shape but needs explicit operator approval
    (`approved_by`) before it may be recorded."""


def _coerce(state: LifecycleState | str, label: str) -> LifecycleState:
    try:
        return LifecycleState(state)
    except ValueError:
        raise IllegalTransition(f"{label} state {state!r} is not a lifecycle state") from None


def validate_transition(
    current: LifecycleState | str,
    target: LifecycleState | str,
    *,
    paused_from: LifecycleState | str | None = None,
    approved_by: str | None = None,
) -> None:
    """Raise IllegalTransition (or OperatorApprovalRequired) unless current → target is legal.

    `paused_from` is the state the experiment paused from (required context when
    current is PAUSED — an experiment may only resume to exactly that state).
    `approved_by` is the operator identity for approval-required transitions.
    Returns None on success; this function never mutates anything.
    """
    cur = _coerce(current, "current")
    tgt = _coerce(target, "target")

    if cur is LifecycleState.RETIRED:
        raise IllegalTransition(
            "RETIRED is terminal: a revived concept creates a successor experiment or "
            "version referencing the retired predecessor; it does not reopen the record"
        )

    if cur == tgt:
        raise IllegalTransition(f"self-transition {cur.value} → {tgt.value} is not a transition")

    # Retirement is legal from every non-retired state, including PAUSED — a clean
    # failure is useful research and must always be recordable.
    if tgt is LifecycleState.RETIRED:
        return

    if tgt is LifecycleState.PAUSED:
        if cur in ACTIVE_STATES:
            return
        raise IllegalTransition(f"cannot pause from {cur.value}")

    if cur is LifecycleState.PAUSED:
        if paused_from is None:
            raise IllegalTransition(
                "cannot resume: the state this experiment paused from is unknown "
                "(paused_from is not recorded)"
            )
        origin = _coerce(paused_from, "paused_from")
        if tgt is origin:
            return
        raise IllegalTransition(
            f"PAUSED may return only to the state it paused from ({origin.value}), "
            f"not {tgt.value}"
        )

    # Both states are on the forward path from here.
    ci, ti = FORWARD_PATH.index(cur), FORWARD_PATH.index(tgt)
    if ti < ci:
        raise IllegalTransition(
            f"no silent rollback: {cur.value} → {tgt.value} would rewrite history. "
            "A changed scientific rule is a new Experiment Version or successor "
            "experiment; a changed environment is a new epoch."
        )
    if ti != ci + 1:
        raise IllegalTransition(
            f"{cur.value} → {tgt.value} skips a lifecycle stage; every arrow is a gate "
            f"(legal next stage: {FORWARD_PATH[ci + 1].value})"
        )
    if (cur, tgt) in APPROVAL_REQUIRED and not approved_by:
        raise OperatorApprovalRequired(
            f"{cur.value} → {tgt.value} requires explicit operator approval "
            "(pass approved_by)"
        )
