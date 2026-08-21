"""One-way migration of the interim contract-findings registry into durable issues.

`findings.py` was always explicitly temporary: a hand-written tuple of defects a
research investigation had *proved* about a registered contract, rendered by the
Control Tower because the evaluator cannot discover that class of problem for
itself. It had no state, no history, no evidence records and no way to be worked
— which is exactly what the issue workflow adds.

This module carries the two registered findings across, once, into
`experiment_issues`. It is the "separately invoked importer" the schema migration
(`e1a2b3c4d5f6`) points at, rather than an Alembic data step: resolving each
finding to a real Experiment and Version by key is ORM work against a schema the
migration is in the middle of creating, and no migration in this repo does ORM
work.

Four properties are load-bearing:

  * **Idempotent.** Keyed on a deterministic migration fingerprint, so a re-run
    finds the existing issue and changes nothing. Safe to run on every deploy.
  * **Nothing is fabricated.** The evidence citation, `proven_at` and
    `proven_by` are copied verbatim from the registered finding. Where the
    finding recorded nothing, nothing is invented — an unknown stays unknown
    (spec §22's rule, applied to research provenance).
  * **Bound to the exact Experiment and Version** the defect was proven against,
    by foreign key. That is what makes a corrected successor Version drop the
    finding automatically, and what keeps the historical issue queryable
    afterwards.
  * **Changes no experiment state and no gate verdict.** It creates issue rows.
    An absent experiment or version is SKIPPED with a reason, never created:
    inventing an experiment to hang a finding on would be the opposite of the
    point.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import issue_policy as policy
from . import issues as ix
from .models import Experiment, ExperimentVersion

#: The two findings from the retired registry, preserved verbatim. This is a
#: migration payload, not a live registry: nothing new is ever added here — a
#: newly proven defect is opened directly as an issue by Research Lab.
LEGACY_CONTRACT_FINDINGS: tuple[dict, ...] = (
    {
        "experiment_key": "mmsell-scheduled-settle-live",
        "version": 1,
        "headline": (
            "imported gate addresses deployment_kind=paper; epoch has no paper "
            "deployment"
        ),
        "detail": (
            'all 4 clauses omit deployment_kind, so it defaults to "paper"',
            "epoch holds kind=live + kind=paper_twin only — no paper deployment",
            "every clause therefore resolves to an EMPTY scope",
            "providers alone WILL NOT unblock this Version",
        ),
        "owner_label": "Research Lab — corrected native successor Version required",
        "independent_of_evaluator": True,
        "evidence_doc": "docs/RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md",
        "proven_at": "2026-08-17",
        "proven_by": "Research Lab (canonical evaluator._arm_scope, local mirror)",
    },
    {
        "experiment_key": "theta4-fat-tail",
        "version": 1,
        "headline": (
            "imported gate addresses deployment_kind=paper AND decides on the "
            "wrong basis"
        ),
        "detail": (
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
        "owner_label": (
            "Research Lab — corrected native successor Version required "
            "(basis change, not a repair)"
        ),
        "independent_of_evaluator": True,
        "evidence_doc": "docs/RESEARCH_LIVE_CANARY_CONTRACT_DEFECT.md",
        "proven_at": "2026-08-17",
        "proven_by": "Research Lab (canonical evaluator._arm_scope, local mirror)",
    },
)

#: Recorded as the opening actor so the provenance of these rows is obvious in
#: the event history: they were migrated, not observed live.
MIGRATION_ACTOR = "findings-registry-migration"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def finding_fingerprint(experiment_id: int, version_id: int) -> str:
    """The deterministic identity of a migrated contract defect.

    Scoped to (detector, experiment, version) exactly — the same triple the
    finding itself was bound to — so re-running the importer resolves to the
    same hash and finds the row it already created."""
    return policy.issue_fingerprint(
        detector=policy.DETECTOR_CONTRACT_DEFECT,
        experiment_id=experiment_id,
        version_id=version_id,
        anomaly_kind="frozen_contract_addressing",
    )


def _existing_issue(session, fingerprint: str):
    """Any issue with this fingerprint, open or not.

    Deliberately broader than `find_open_issue_by_fingerprint`: if the defect was
    migrated and then RESOLVED by a corrected Version, re-running the importer
    must not resurrect it as a new open ticket. Recurrence detection is a
    different question with a different, explicit command."""
    from .models import ExperimentIssue

    return session.scalar(
        select(ExperimentIssue)
        .where(ExperimentIssue.detector_fingerprint == fingerprint)
        .order_by(ExperimentIssue.opened_at)
    )


def import_contract_findings(session, *, now: datetime | None = None) -> dict:
    """Migrate the registered findings into durable issues. Idempotent.

    Returns a report of what happened per finding: `created`, `already_present`,
    or `skipped` with the reason. Nothing is committed — the caller owns the
    transaction, per repository convention."""
    stamp = now or _now()
    report: dict = {"created": [], "already_present": [], "skipped": []}

    for finding in LEGACY_CONTRACT_FINDINGS:
        key = finding["experiment_key"]
        exp = session.scalar(select(Experiment).where(Experiment.key == key))
        if exp is None:
            report["skipped"].append({
                "experiment": key,
                "reason": "experiment is not registered in Experiment OS",
            })
            continue
        ver = session.scalar(
            select(ExperimentVersion).where(
                ExperimentVersion.experiment_id == exp.id,
                ExperimentVersion.version == finding["version"],
            )
        )
        if ver is None:
            report["skipped"].append({
                "experiment": key,
                "reason": f"version {finding['version']} does not exist",
            })
            continue

        fingerprint = finding_fingerprint(exp.id, ver.id)
        existing = _existing_issue(session, fingerprint)
        if existing is not None:
            report["already_present"].append({
                "experiment": key,
                "issue_key": existing.issue_key,
                "status": existing.status,
            })
            continue

        issue = ix.create_issue(
            session,
            title=finding["headline"],
            problem_statement="\n".join(finding["detail"]),
            opened_by_role="SYSTEM",
            opened_by_actor=MIGRATION_ACTOR,
            # STRATEGY, not DATA: the evaluator's BLOCKED_DATA is true and
            # incomplete. Implementing every provider it names would leave this
            # Version exactly as unevaluable, because the ADDRESSING is what is
            # wrong — and only a corrected contract fixes addressing.
            classification=policy.IssueClassification.STRATEGY.value,
            severity=policy.IssueSeverity.HIGH.value,
            priority=policy.IssuePriority.P1.value,
            current_owner_role=policy.IssueOwnerRole.RESEARCH_LAB.value,
            owner_rationale=finding["owner_label"],
            experiment=exp,
            version=ver,
            detector=policy.DETECTOR_CONTRACT_DEFECT,
            detector_fingerprint=fingerprint,
            anomaly_kind="frozen_contract_addressing",
            first_observed_at=stamp,
            evidence_summary=(
                f"proven {finding['proven_at']} by {finding['proven_by']}; "
                f"see {finding['evidence_doc']}"
            ),
            details_json={
                "detail": list(finding["detail"]),
                "owner_label": finding["owner_label"],
                "independent_of_evaluator": finding["independent_of_evaluator"],
                "evidence_doc": finding["evidence_doc"],
                "proven_at": finding["proven_at"],
                "proven_by": finding["proven_by"],
                "migrated_from": "kalshi_bot/experiment_os/findings.py",
            },
            now=stamp,
        )
        # The finding's authority is the research document that proved it — a
        # citation that does not resolve is an assertion, which is why the
        # document is a first-class evidence row and not just a string.
        ix.add_issue_evidence(
            session, issue,
            evidence_type=policy.IssueEvidenceType.RESEARCH_DOCUMENT.value,
            summary=(
                f"contract defect proven against v{finding['version']} — "
                f"{finding['headline']}"
            ),
            source_ref=finding["evidence_doc"],
            captured_at=stamp,
            captured_by=finding["proven_by"],
            actor_role="SYSTEM",
        )
        # Walk it to the state the investigation is actually in: the defect is
        # proven and the remedy is known, so it is ACTION_REQUIRED — not OPEN
        # (nobody has looked) and not RESOLVED (nothing has been fixed).
        ix.triage_issue(
            session, issue, actor=MIGRATION_ACTOR, actor_role="SYSTEM",
            reason="migrated from the interim contract-findings registry",
        )
        ix.set_issue_status(
            session, issue, status=policy.IssueStatus.INVESTIGATING.value,
            actor=MIGRATION_ACTOR, actor_role="SYSTEM",
            reason="the defect was already proven before migration",
        )
        ix.propose_issue_fix(
            session, issue,
            proposed_fix=(
                "author and freeze a corrected native successor Version whose "
                "gate addresses the deployment kinds the epoch actually holds"
            ),
            actor=MIGRATION_ACTOR, actor_role="SYSTEM",
            reason=finding["owner_label"],
        )
        ix.record_disposition(
            session, issue,
            disposition=policy.IssueDisposition.NEW_VERSION.value,
            actor=MIGRATION_ACTOR, actor_role="SYSTEM",
            requires_new_version=True,
            reason=(
                "a frozen Version cannot be repaired in place; the corrected "
                "contract is a new native successor Version. Recorded only — "
                "this creates no Version and changes no experiment state"
            ),
        )
        report["created"].append({
            "experiment": key,
            "version": finding["version"],
            "issue_key": issue.issue_key,
            "fingerprint": fingerprint,
        })

    return report
