"""DEPRECATED compatibility shim — contract findings are durable issues now.

This module was an explicitly interim mechanism: a small, hand-registered tuple
of defects a research investigation had *proved* about a registered contract —
the class of problem the evaluator cannot discover for itself, because from its
side the contract is well-formed and simply resolves to no evidence. Its own
docstring said it would go away "when the pinned investigation/issue workflow
exists". It does, so it has.

**Where the findings live now:** `experiment_issues`, as durable issues with
`detector = "contract.defect"`, bound by foreign key to the exact Experiment and
Version the defect was proven against.

  * read them: `read.contract_defect_findings(session, experiment, version)`,
    which is what the Control Tower renders;
  * the two historical entries were migrated by
    `experiment_os.issue_import.import_contract_findings` (idempotent);
  * register a NEW one by opening an issue — `issues.create_issue(...)` with
    that detector, citing the merged research document as evidence. Research Lab
    owns that write; see `docs/EXPERIMENT_OS_ISSUES.md`.

Everything the registry guaranteed is now enforced rather than conventional: a
finding still never changes a verdict (issues feed no evaluator, gate runner,
enforcement or transition), is still bound to one version so a corrected
successor drops it automatically, and still cites a document. What it gains is
workflow state, an append-only history, real evidence rows, and a way to be
worked and closed.

The module is kept as an importable shim, returning empty, so a stale import in
an unattended script degrades to "no findings" instead of an ImportError
mid-run. Nothing in the repository reads it any more; do not add to it.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass


@dataclass(frozen=True)
class ContractFinding:
    """Retained for type compatibility only. Nothing constructs these now."""

    experiment_key: str
    version: int
    headline: str
    detail: tuple[str, ...]
    owner: str
    independent_of_evaluator: bool
    evidence_doc: str
    proven_at: str
    proven_by: str


#: Intentionally empty. The registry moved to `experiment_issues`; leaving
#: entries here as well would render the same defect twice in the Control Tower,
#: from two sources that could disagree.
CONTRACT_FINDINGS: tuple[ContractFinding, ...] = ()


def findings_for(experiment_key: str, version: int | None) -> list[ContractFinding]:
    """Always empty — use `read.contract_defect_findings(session, exp, version)`."""
    warnings.warn(
        "experiment_os.findings is deprecated: contract findings are durable "
        "issues. Use read.contract_defect_findings(session, experiment, version).",
        DeprecationWarning,
        stacklevel=2,
    )
    return []


def as_json(finding: ContractFinding) -> dict:
    """Render one legacy finding for a report payload (unused; kept for imports)."""
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
