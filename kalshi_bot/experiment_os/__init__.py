"""Experiment OS — the repository-wide experiment lifecycle layer (foundation, PR 1).

Design contract: docs/EXPERIMENT_OS_SPEC — see `docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md`.
Implementation notes + the compatibility boundary with the existing books:
`docs/EXPERIMENT_OS_FOUNDATION.md`.

This package is the canonical identity/state layer for experiments:

    Experiment → ExperimentVersion → ExperimentArm
                                   → ExperimentEpoch (pins a PlatformSnapshot)
                                        → ExperimentDeployment → strategy tags

plus structured gates, append-only state transitions/audit, integrity events,
legacy evidence, and the platform component/revision/snapshot registry.

Foundation-release scope (enforcement mode OFF): nothing in the trading runtime
reads or writes these tables yet. The state machine validates and audits
transitions but automates nothing — no promotion, no real-money action, no
change to existing paper/live books. Enforcement arrives with the later
enforcement PR; legacy import with the migration PR.
"""

from .lifecycle import (  # noqa: F401
    ACTIVE_STATES,
    ArmRole,
    DeploymentKind,
    EnforcementMode,
    EvidenceClass,
    GateVerdict,
    IllegalTransition,
    ImpactAction,
    ImpactClass,
    LegacyClass,
    LifecycleState,
    MigrationIntegrity,
    OperatorApprovalRequired,
    RevisionStatus,
    validate_transition,
)
