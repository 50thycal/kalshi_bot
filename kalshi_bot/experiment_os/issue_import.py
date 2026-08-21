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


# ---------------------------------------------------------------------------
# Reconciliation with the operator's later decision
# ---------------------------------------------------------------------------
#
# The findings above were proven against Version 1 of two LIVE canaries, and the
# remedy recorded at the time was "a corrected native successor Version". That
# disposition is now STALE, and importing it unreconciled would put two tickets
# in front of the next reader asking for work the operator has already declined.
#
# What actually happened, per merged research:
#
#   * both live canaries were stood down and remain stood down
#     (`RESEARCH_LIVE_FILL_SELECTION_STUDY.md`: "All three live books remain
#     stood down");
#   * the proposed successor live-v2 contracts were WITHDRAWN
#     (`RESEARCH_SUCCESSOR_GATE_DESIGN.md`, WITHDRAWN 2026-08-21 banner): MMSELL
#     Design D is not to be frozen because treatment and control differ in
#     universe, entry-price band and settle mode at once, which no sample size
#     repairs; theta4 v2 is not to be created because the book carries two
#     independent failures and needs research before another canary.
#
# So the defect stays TRUE of the historical Version — it is not withdrawn, and
# the issue keeps its original evidence and Version binding — but no repair and
# no replacement Version follows from it. That is precisely CLOSED_NO_ACTION
# with disposition NO_ACTION, and `requires_pause_or_stand_down = true` because
# the contract remains stood down. Recording it any other way would either erase
# a proven finding or schedule work that has been declined.

#: The merged artifacts that record the stand-down and the withdrawal. Every one
#: is a real file in this repository, checked by test — a citation that does not
#: resolve is an assertion, and that rule does not relax for a closure.
WITHDRAWAL_EVIDENCE_DOC = "docs/RESEARCH_SUCCESSOR_GATE_DESIGN.md"
STAND_DOWN_EVIDENCE_DOC = "docs/RESEARCH_LIVE_FILL_SELECTION_STUDY.md"

#: Per-book: the research that explains why THAT successor was withdrawn.
WITHDRAWAL_CAUSE_DOC: dict[str, str] = {
    "mmsell-scheduled-settle-live": "docs/RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md",
    "theta4-fat-tail": "docs/RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md",
}

#: The PR that merged the WITHDRAWN banner, for anyone tracing the decision back
#: to its review rather than to a file that could later be edited.
WITHDRAWAL_PR_REF = "50thycal/kalshi_bot#251"

#: Stamped into `details_json` when reconciliation has run. The idempotency key:
#: a second run finds it and does nothing, so a repeat can never reopen a closed
#: issue, reset it to ACTION_REQUIRED, restore NEW_VERSION, or duplicate events.
RECONCILIATION_KEY = "withdrawn-live-v2-2026-08-21"

CLOSURE_EXPLANATION = (
    "The defect remains true of historical Version 1, but that live contract was "
    "stood down and its proposed successor live Version was withdrawn. It will "
    "not be repaired in place or replaced by the rejected live-v2 design. "
    "Subsequent MMSELL/theta work proceeds as separate paper research."
)


def _already_reconciled(issue) -> bool:
    return bool((issue.details_json or {}).get("reconciled") == RECONCILIATION_KEY)


def reconcile_withdrawn_successors(session, *, now: datetime | None = None) -> dict:
    """Record the withdrawal decision on each migrated finding and close it.

    Idempotent by `RECONCILIATION_KEY`: a second run reports `already_reconciled`
    and writes nothing at all — no reopen, no status reset, no restored
    disposition, no duplicated evidence or events.

    The path through the workflow is deliberate and reads correctly in the
    history: ACTION_REQUIRED (a successor Version was the plan) →
    INVESTIGATING (that plan was withdrawn — the documented back edge, whose
    whole purpose is a proposed remedy that turned out to be wrong) →
    CLOSED_NO_ACTION. `close_no_action` is not `resolve`: nothing was fixed, and
    collapsing the two would make every real resolution less trustworthy.

    Changes NO experiment state, gate, verdict, Epoch, Version, Platform
    Revision or exposure. It does not restart a canary and does not create the
    withdrawn v2 contracts — recording that they will not be created is the
    opposite of creating them."""
    stamp = now or _now()
    report: dict = {"reconciled": [], "already_reconciled": [], "skipped": []}

    for finding in LEGACY_CONTRACT_FINDINGS:
        key = finding["experiment_key"]
        exp = session.scalar(select(Experiment).where(Experiment.key == key))
        if exp is None:
            report["skipped"].append(
                {"experiment": key, "reason": "experiment is not registered"}
            )
            continue
        ver = session.scalar(
            select(ExperimentVersion).where(
                ExperimentVersion.experiment_id == exp.id,
                ExperimentVersion.version == finding["version"],
            )
        )
        if ver is None:
            report["skipped"].append(
                {"experiment": key, "reason": f"version {finding['version']} absent"}
            )
            continue
        issue = _existing_issue(session, finding_fingerprint(exp.id, ver.id))
        if issue is None:
            report["skipped"].append(
                {"experiment": key, "reason": "finding has not been imported yet"}
            )
            continue
        if _already_reconciled(issue):
            report["already_reconciled"].append(
                {"experiment": key, "issue_key": issue.issue_key,
                 "status": issue.status, "disposition": issue.disposition}
            )
            continue

        cause_doc = WITHDRAWAL_CAUSE_DOC[key]
        # 1. The decision, as citable evidence — not as prose in a status field.
        ix.add_issue_evidence(
            session, issue,
            evidence_type=policy.IssueEvidenceType.RESEARCH_DOCUMENT.value,
            summary=(
                "operator withdrew the proposed successor live Version; the live "
                "canary remains stood down"
            ),
            source_ref=WITHDRAWAL_EVIDENCE_DOC,
            captured_at=stamp, captured_by=MIGRATION_ACTOR, actor_role="SYSTEM",
        )
        for link_ref, label in (
            (WITHDRAWAL_EVIDENCE_DOC, "WITHDRAWN 2026-08-21 — v2 designs not to be frozen"),
            (STAND_DOWN_EVIDENCE_DOC, "live books remain stood down"),
            (cause_doc, "why this successor was withdrawn"),
        ):
            ix.add_issue_link(
                session, issue,
                link_type=policy.IssueLinkType.RESEARCH_DOC.value,
                reference=link_ref, label=label,
                created_by=MIGRATION_ACTOR, actor_role="SYSTEM",
            )
        ix.add_issue_link(
            session, issue, link_type=policy.IssueLinkType.PR.value,
            reference=WITHDRAWAL_PR_REF, label="PR that merged the withdrawal",
            created_by=MIGRATION_ACTOR, actor_role="SYSTEM",
        )

        # 2. The proposed remedy was withdrawn, so the ticket returns to
        #    investigation before it can be closed. This is the back edge doing
        #    exactly the job it was added for.
        ix.set_issue_status(
            session, issue, status=policy.IssueStatus.INVESTIGATING.value,
            actor=MIGRATION_ACTOR, actor_role="SYSTEM",
            reason=(
                "the proposed corrected successor Version was WITHDRAWN by the "
                f"operator (see {WITHDRAWAL_EVIDENCE_DOC}); the remedy recorded "
                "at migration no longer applies"
            ),
        )

        # 3. Replace the stale disposition. NEW_VERSION -> NO_ACTION, and the
        #    requirement flags become determined FALSE rather than staying
        #    unknown: "we decided this is not needed" is a finding.
        ix.record_disposition(
            session, issue,
            disposition=policy.IssueDisposition.NO_ACTION.value,
            actor=MIGRATION_ACTOR, actor_role="SYSTEM",
            reason=CLOSURE_EXPLANATION,
            requires_new_version=False,
            requires_new_epoch=False,
            requires_platform_revision=False,
            # TRUE and staying true: the contract is stood down, and that is the
            # standing operational state this issue is closed against.
            requires_pause_or_stand_down=True,
        )

        # 4. Close with no action — NOT resolved. Nothing was fixed.
        ix.close_no_action(
            session, issue, reason=CLOSURE_EXPLANATION,
            actor=MIGRATION_ACTOR, actor_role="SYSTEM",
        )

        details = dict(issue.details_json or {})
        details["reconciled"] = RECONCILIATION_KEY
        details["withdrawal_evidence_doc"] = WITHDRAWAL_EVIDENCE_DOC
        details["stand_down_evidence_doc"] = STAND_DOWN_EVIDENCE_DOC
        details["withdrawal_cause_doc"] = cause_doc
        details["withdrawal_pr"] = WITHDRAWAL_PR_REF
        issue.details_json = details
        session.flush()

        report["reconciled"].append({
            "experiment": key, "issue_key": issue.issue_key,
            "status": issue.status, "disposition": issue.disposition,
        })

    return report


def import_and_reconcile(session, *, now: datetime | None = None) -> dict:
    """Import the historical findings AND settle them against the current
    decision, in one idempotent operation.

    This is the whole production action. Run it as many times as you like: the
    import no-ops once the issues exist, and the reconciliation no-ops once they
    carry the reconciliation key."""
    stamp = now or _now()
    return {
        "import": import_contract_findings(session, now=stamp),
        "reconcile": reconcile_withdrawn_successors(session, now=stamp),
    }


def plan(session) -> dict:
    """A READ-ONLY preview: what would import/reconcile do right now?

    Exists because the ops channel is read-only against Postgres, so the usual
    `--dry-run` (which writes and rolls back) cannot run there at all. This
    answers the same question with plain selects, so the production dry run is a
    real check rather than a promise."""
    out: list[dict] = []
    for finding in LEGACY_CONTRACT_FINDINGS:
        key = finding["experiment_key"]
        row: dict = {"experiment": key, "version": finding["version"]}
        exp = session.scalar(select(Experiment).where(Experiment.key == key))
        if exp is None:
            row["action"] = "SKIP"
            row["reason"] = "experiment is not registered in Experiment OS"
            out.append(row)
            continue
        ver = session.scalar(
            select(ExperimentVersion).where(
                ExperimentVersion.experiment_id == exp.id,
                ExperimentVersion.version == finding["version"],
            )
        )
        if ver is None:
            row["action"] = "SKIP"
            row["reason"] = f"version {finding['version']} does not exist"
            out.append(row)
            continue
        row["experiment_id"] = exp.id
        row["version_id"] = ver.id
        issue = _existing_issue(session, finding_fingerprint(exp.id, ver.id))
        if issue is None:
            row["action"] = "IMPORT_THEN_CLOSE_NO_ACTION"
            row["reason"] = "no issue carries this finding's fingerprint yet"
        elif _already_reconciled(issue):
            row["action"] = "NO_OP"
            row["issue_key"] = issue.issue_key
            row["status"] = issue.status
            row["disposition"] = issue.disposition
            row["reason"] = "already imported and reconciled"
        else:
            row["action"] = "RECONCILE_ONLY"
            row["issue_key"] = issue.issue_key
            row["status"] = issue.status
            row["disposition"] = issue.disposition
            row["reason"] = "imported but not yet settled against the withdrawal"
        out.append(row)
    return {"plan": out, "reconciliation_key": RECONCILIATION_KEY}


def run_boot_reconciliation(session) -> dict | None:
    """The flag-gated worker hook (`EXPERIMENT_OS_RECONCILE_FINDINGS_ON_BOOT`).

    The worker is the only process holding a writable DATABASE_URL, so this is
    the one path that can actually execute in production — the same mechanism
    the legacy import and the enforcement cutover already use. Bounded to this
    one operation, idempotent, and incapable of stopping the worker: `main.py`
    guards the call and the result is returned for logging, never raised."""
    return import_and_reconcile(session)
