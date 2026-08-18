"""Durable research findings about experiment *contracts* — NOT lifecycle truth.

Experiment OS is the source of truth for what an experiment IS: its versions,
epochs, arms, deployments, gates and recorded gate results. This module is not
that, and must never be read as that.

It is a small, explicit, hand-registered record of defects a human investigation
*proved* about a registered contract — the class of problem the evaluator cannot
discover for itself, because from its side the contract is well-formed and simply
resolves to no evidence. The imported live-canary gates are the worked example:
their clauses address ``deployment_kind="paper"`` while their epochs hold only
``live`` and ``paper_twin``, so every clause resolves to an EMPTY scope. The
evaluator reports BLOCKED_DATA and names the missing providers, which is true and
incomplete — implementing every one of those providers would not make the gate
evaluable, because the addressing is what is wrong.

Four rules keep this honest:

1. **A finding never changes a verdict.** Nothing here feeds the evaluator, the
   gate runner, enforcement, admission, or any transition. It is display-only, so
   a wrong entry can mislead a reader but cannot authorize or block anything.
2. **Findings are registered by hand**, against a specific ``(experiment,
   version)``, citing the research document that proves them. Nothing is inferred
   from the shape of a gate. A heuristic that guessed "this looks malformed"
   would be a second, unreviewed opinion competing with the canonical contract —
   and would eventually be wrong about a contract that is merely unusual.
3. **Findings expire structurally.** Each binds to the version it was proven
   against, so when a corrected successor Version is created the finding stops
   applying on its own. Nothing has to remember to delete it, and it cannot
   linger as a permanent scare line over an experiment that was already fixed.
4. **Findings are not a to-do list with authority.** ``owner`` names the role
   that would do the work; it does not schedule it, and it does not approve it.

This is deliberately an interim mechanism. When the pinned investigation/issue
workflow exists, these become tickets with real state and this module goes away.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContractFinding:
    """One proven defect in a registered experiment contract.

    ``version`` is the version the defect was proven against — not "the current
    version". A finding applies only while that version is the operating one."""

    experiment_key: str
    version: int
    headline: str
    detail: tuple[str, ...]
    owner: str
    #: Why implementing the evaluator's named blockers would not be sufficient.
    #: Present when the finding is independent of the evaluator's own verdict,
    #: which is the whole reason this registry exists.
    independent_of_evaluator: bool
    evidence_doc: str
    proven_at: str
    proven_by: str


# Registered findings. Every entry cites a merged research document; nothing here
# is asserted from a session's working memory.
CONTRACT_FINDINGS: tuple[ContractFinding, ...] = (
    ContractFinding(
        experiment_key="mmsell-scheduled-settle-live",
        version=1,
        headline="imported gate addresses deployment_kind=paper; epoch has no paper deployment",
        detail=(
            'all 4 clauses omit deployment_kind, so it defaults to "paper"',
            "epoch holds kind=live + kind=paper_twin only — no paper deployment",
            "every clause therefore resolves to an EMPTY scope",
            "providers alone WILL NOT unblock this Version",
        ),
        owner="Research Lab — corrected native successor Version required",
        independent_of_evaluator=True,
        evidence_doc="docs/RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md",
        proven_at="2026-08-17",
        proven_by="Research Lab (canonical evaluator._arm_scope, local mirror)",
    ),
    ContractFinding(
        experiment_key="theta4-fat-tail",
        version=1,
        headline="imported gate addresses deployment_kind=paper AND decides on the wrong basis",
        detail=(
            'all 3 clauses omit deployment_kind, so it defaults to "paper"',
            "epoch holds kind=live + kind=paper_twin only — no paper deployment",
            "every clause therefore resolves to an EMPTY scope",
            "separately: 2 of 3 clauses are PAPER metrics (settled_trades,",
            "pnl_cents_per_trade) on a LIVE_CANARY -> PRODUCTION gate, so they",
            "would answer the wrong question even with a paper deployment present",
            "separately: twin armed 2026-08-12 vs live 2026-07-30 (13 days apart),",
            "so no contemporaneous twin-vs-live control exists over v1",
            "providers alone WILL NOT unblock this Version",
        ),
        owner="Research Lab — corrected native successor Version required (basis change, not a repair)",
        independent_of_evaluator=True,
        evidence_doc="docs/RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md",
        proven_at="2026-08-17",
        proven_by="Research Lab (canonical evaluator._arm_scope, local mirror)",
    ),
)


def findings_for(experiment_key: str, version: int | None) -> list[ContractFinding]:
    """Findings that apply to this experiment at this version.

    A ``None`` version (a legacy stub with no version at all) matches nothing:
    a finding is a claim about a specific registered contract, and there is no
    contract to make a claim about."""
    if version is None:
        return []
    return [
        f for f in CONTRACT_FINDINGS
        if f.experiment_key == experiment_key and f.version == version
    ]


def as_json(finding: ContractFinding) -> dict:
    """Render one finding for a report payload."""
    return {
        "experiment": finding.experiment_key,
        "version": finding.version,
        "headline": finding.headline,
        "detail": list(finding.detail),
        "owner": finding.owner,
        "independent_of_evaluator": finding.independent_of_evaluator,
        "evidence_doc": finding.evidence_doc,
        "proven_at": finding.proven_at,
        "proven_by": finding.proven_by,
    }
