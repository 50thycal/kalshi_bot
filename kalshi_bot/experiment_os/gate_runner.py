"""Persisted gate evaluation — the writer that turns evidence into official results.

Before this module, Experiment OS could say what the evidence *would* imply (the
Control Tower's dry run) but had never recorded an official verdict, so no
promotion gate could authorize anything. This closes that gap:

    evidence → canonical evaluator → immutable GateResult → Control Tower reads
             → operator may choose to transition

## What this is NOT

It is not a second evaluator. Every verdict comes from `evaluator.evaluate_gate`,
the same function the Control Tower dry-runs and the same one
`service.transition_experiment` re-runs before a money promotion.

It is not a promoter. **Automatic evaluation is allowed; automatic promotion is
not.** This module never calls `transition_experiment`, `arm_live_canary`, or any
lifecycle write. Recording `PAPER→LIVE_CANARY = PASS` is a fact; performing
`PAPER→LIVE_CANARY` remains an operator act bound by the PR 4 rules. A recorded
FAIL likewise retires nothing.

## Authority — one writer, deliberately

PR #230's lesson was that a process must only make observations it is
authoritative to make. Evaluation reads canonical database state, so unlike the
live-config check its *correctness* does not depend on which process runs it —
but its *write volume* does. Two workers evaluating the same gates would race and
double-write, so exactly one process is designated:

  * the **live trading worker**, when `EXPERIMENT_OS_EVALUATE_GATES` is on
    (default off, so a deploy stays inert); and
  * an explicit operator run of `cli evaluate-gates`, which needs a writable
    DATABASE_URL and refuses a read-only one.

The evo worker is neither, and `_may_write` refuses it. The ops channel is
read-only by design, so its `evaluate-gates` requests are dry-run only.

## Dedupe — decision points, not a heartbeat

A periodic evaluator would otherwise write an identical HOLD every cycle. A new
result is recorded only when the evaluation is *semantically* different from the
last one: verdict, blocking reasons, or the binding (epoch, platform snapshot,
metric revision, gate spec) changed — or the sample grew enough to be a real
progress step. Metric values drift continuously and are deliberately NOT part of
the fingerprint; a HOLD whose ¢/trade moved by 0.01 is not a new decision point.

Dedupe cannot stale a promotion, because promotion freshness does not come from
this writer: `transition_experiment` re-evaluates synchronously at authorization
(PR 4) and that fresh result becomes the authorizing one. This module records
history; the promotion path proves currency.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import read
from .evaluator import evaluate_gate, latest_result, persist_outcome
from .lifecycle import LifecycleState
from .metrics import METRICS_ENGINE_REVISION
from .models import ExperimentGate
from .service import ExperimentOsError, canonical_hash

logger = logging.getLogger(__name__)

# Lifecycle states whose gates are worth evaluating. IDEA has no evidence,
# RETIRED is terminal history, and PAUSED is deliberately excluded: a paused
# experiment is not accumulating, so re-evaluating it only rewrites the verdict
# that paused it. Resuming re-opens evaluation naturally.
EVALUABLE_STATES: frozenset[str] = frozenset({
    LifecycleState.PROBE.value,
    LifecycleState.PAPER.value,
    LifecycleState.LIVE_CANARY.value,
    LifecycleState.PRODUCTION.value,
})

# A HOLD trail should show progress without spamming. Write again when the
# largest clause sample has grown by this much since the last recorded result.
PROGRESS_STEP_ABS = 25
PROGRESS_STEP_REL = 0.25


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotAuthorized(ExperimentOsError):
    """This process may not write gate results."""


def _may_write(settings) -> tuple[bool, str]:
    """Only the designated writer persists. Returns (allowed, reason)."""
    if settings is None:
        return True, "explicit operator run"
    mode = (getattr(settings, "bot_mode", None) or "").lower()
    if mode != "live":
        return False, f"bot_mode={mode!r} is not the designated evaluator (live)"
    if not getattr(settings, "experiment_os_evaluate_gates", False):
        return False, "EXPERIMENT_OS_EVALUATE_GATES is off"
    return True, "live worker with evaluation enabled"


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def eligible_gates(session) -> list[dict]:
    """Every gate worth evaluating right now, from canonical state.

    Eligible = a non-terminal experiment in an evaluable state, on its current
    frozen version, with an OPEN epoch, whose gate's evidence has started."""
    out: list[dict] = []
    for exp in read.list_experiments(session):
        if exp.state not in EVALUABLE_STATES:
            continue
        ver = read.latest_version(session, exp)
        if ver is None or ver.frozen_at is None:
            continue
        epoch = read.open_epoch_for(session, ver)
        if epoch is None:
            continue  # no operating interval — nothing to evaluate over
        for gate in read.gates_for(session, ver):
            if gate.evidence_started_at is None:
                continue  # pre-registered but not started; not a verdict yet
            if gate.superseded_by_gate_id is not None:
                continue
            out.append({
                "experiment": exp, "version": ver, "epoch": epoch, "gate": gate,
            })
    return out


# ---------------------------------------------------------------------------
# Semantic dedupe
# ---------------------------------------------------------------------------


def evaluation_fingerprint(outcome, gate: ExperimentGate, epoch) -> str:
    """Deterministic identity of an evaluation's MEANING.

    Deliberately excludes the evidence window and raw metric values: both move
    every cycle, and neither on its own makes a new decision point. Includes the
    binding, because a result computed under a different epoch, snapshot, metric
    revision or gate spec is a different claim even at the same verdict."""
    return canonical_hash({
        "gate_id": gate.id,
        "spec_hash": gate.spec_hash,
        "epoch_id": epoch.id,
        "snapshot": outcome.platform_snapshot_fingerprint,
        "metric_revision": METRICS_ENGINE_REVISION,
        "verdict": outcome.verdict,
        "blocking": sorted(outcome.blocking_reasons or []),
        # Which clauses are undecidable/missing is semantic; their values are not.
        "clause_shape": sorted(
            f"{c.list_name}|{c.clause.get('metric')}|{c.scope}|"
            f"{c.passed}|{int(bool(c.missing))}"
            for c in outcome.clauses
        ),
    })


def _max_n(outcome) -> int:
    return max((c.n or 0 for c in outcome.clauses), default=0)


def should_record(session, gate: ExperimentGate, outcome, epoch) -> tuple[bool, str]:
    """Is this evaluation a new decision point worth persisting?"""
    prior = latest_result(session, gate)
    if prior is None:
        return True, "first recorded result for this gate"

    fingerprint = evaluation_fingerprint(outcome, gate, epoch)
    prior_fp = ((prior.metrics_json or {}).get("fingerprint")
                if isinstance(prior.metrics_json, dict) else None)
    if prior_fp is None:
        # Recorded before fingerprints existed (or by hand). Only re-record on a
        # real change, so adopting this module does not rewrite the whole trail.
        if prior.verdict != outcome.verdict:
            return True, f"verdict {prior.verdict} → {outcome.verdict} (unfingerprinted prior)"
        return False, "unchanged verdict vs an unfingerprinted prior result"
    if prior_fp != fingerprint:
        if prior.verdict != outcome.verdict:
            return True, f"verdict {prior.verdict} → {outcome.verdict}"
        return True, "binding or blocking state changed"

    prior_n = 0
    if isinstance(prior.sample_json, dict):
        prior_n = max((v.get("n") or 0 for v in prior.sample_json.values()
                       if isinstance(v, dict)), default=0)
    if not prior_n and isinstance(prior.metrics_json, dict):
        prior_n = max((c.get("n") or 0
                       for c in prior.metrics_json.get("clauses", [])
                       if isinstance(c, dict)), default=0)
    now_n = _max_n(outcome)
    step = max(PROGRESS_STEP_ABS, int(prior_n * PROGRESS_STEP_REL))
    if now_n - prior_n >= step:
        return True, f"sample progressed {prior_n} → {now_n} (step {step})"
    return False, f"unchanged (n {prior_n} → {now_n}, needs +{step})"


# ---------------------------------------------------------------------------
# The cycle
# ---------------------------------------------------------------------------


def run_evaluation_cycle(
    session,
    *,
    settings=None,
    dry_run: bool = False,
    only_experiment: str | None = None,
    only_gate: str | None = None,
) -> dict:
    """Evaluate every eligible gate; persist the ones that are new decision points.

    Never raises for a single bad gate — one broken experiment must not stop the
    others, and nothing here may stop trading. Returns a full accounting."""
    if not dry_run:
        allowed, why = _may_write(settings)
        if not allowed:
            raise NotAuthorized(
                f"this process may not persist gate results: {why}. Run the "
                "designated live worker with EXPERIMENT_OS_EVALUATE_GATES=true, "
                "or use dry_run=True."
            )

    summary: dict = {
        "dry_run": dry_run, "started_at": str(_now()),
        "considered": 0, "evaluated": 0, "written": 0, "skipped_unchanged": 0,
        "errors": 0, "verdicts": {}, "written_rows": [], "skipped": [],
        "failures": [],
    }
    for item in eligible_gates(session):
        exp, gate, epoch = item["experiment"], item["gate"], item["epoch"]
        if only_experiment and exp.key != only_experiment:
            continue
        if only_gate and gate.gate_key != only_gate:
            continue
        summary["considered"] += 1
        label = f"{exp.key}/{gate.gate_key}"
        try:
            # Always evaluate as a DRY RUN first: the decision to persist is
            # ours, not the evaluator's, and this keeps the read identical to
            # the Control Tower's.
            outcome = evaluate_gate(session, gate, epoch=epoch, persist=False)
            summary["evaluated"] += 1
            summary["verdicts"][outcome.verdict] = (
                summary["verdicts"].get(outcome.verdict, 0) + 1
            )
            record, reason = should_record(session, gate, outcome, epoch)
            if not record:
                summary["skipped_unchanged"] += 1
                summary["skipped"].append({"gate": label, "verdict": outcome.verdict,
                                           "reason": reason})
                continue
            if dry_run:
                summary["written_rows"].append(
                    {"gate": label, "verdict": outcome.verdict, "reason": reason,
                     "would_write": True})
                continue
            # Persist THE OUTCOME WE JUDGED, not a fresh re-evaluation that could
            # differ because evidence moved between the two calls. The dedupe
            # fingerprint goes in at creation — gate results are append-only, so
            # it cannot be stamped on afterwards.
            persisted = persist_outcome(
                session, gate, outcome, computed_by="system", epoch=epoch,
                extra_metrics={"fingerprint": evaluation_fingerprint(outcome, gate, epoch)},
            )
            session.commit()  # narrow boundary: one gate, one transaction
            summary["written"] += 1
            summary["written_rows"].append(
                {"gate": label, "verdict": persisted.verdict,
                 "result_id": persisted.result_id, "reason": reason})
            logger.info(
                "recorded gate result",
                extra={"extra_fields": {"gate": label, "verdict": persisted.verdict,
                                        "result_id": persisted.result_id,
                                        "reason": reason}},
            )
        except Exception as exc:  # noqa: BLE001 — one gate must not stop the rest
            session.rollback()
            summary["errors"] += 1
            summary["failures"].append({"gate": label, "error": str(exc)[:300]})
            logger.error(
                "gate evaluation failed",
                extra={"extra_fields": {"gate": label, "error": str(exc)}},
            )
    summary["finished_at"] = str(_now())
    return summary


# ---------------------------------------------------------------------------
# Worker hook
# ---------------------------------------------------------------------------


def run_scheduled_evaluation(session, settings) -> dict | None:
    """Cycle hook: evaluate at a bounded cadence. Never raises.

    Returns None when this process is not the designated writer or the cadence
    has not elapsed — the overwhelmingly common case, so it is cheap.

    An inert evaluator says so ONCE per process. Silence is indistinguishable
    from "running and finding nothing", which is how the first production deploy
    of this module looked while its hook was wired into a cycle function the live
    worker never calls. One line at startup makes that visible."""
    global _LAST_RUN, _ANNOUNCED
    allowed, why = _may_write(settings)
    if not allowed:
        if not _ANNOUNCED:
            _ANNOUNCED = True
            logger.info(
                "gate evaluation inert on this process",
                extra={"extra_fields": {"reason": why}},
            )
        return None
    interval = int(getattr(settings, "experiment_os_evaluate_interval_minutes", 60) or 60)
    now = _now()
    if _LAST_RUN is not None and (now - _LAST_RUN).total_seconds() < interval * 60:
        return None
    _LAST_RUN = now
    try:
        summary = run_evaluation_cycle(session, settings=settings)
        # Always log a completed cycle, not only a productive one: at most one
        # line per interval, and "considered=0" is itself the diagnosis when a
        # gate that should be evaluated never is.
        logger.info(
            "gate evaluation cycle",
            extra={"extra_fields": {k: summary[k] for k in
                                    ("considered", "evaluated", "written",
                                     "skipped_unchanged", "errors", "verdicts")}},
        )
        return summary
    except Exception as exc:  # noqa: BLE001 — evaluation must never stop trading
        logger.error(
            "gate evaluation cycle failed (trading unaffected)",
            extra={"extra_fields": {"error": str(exc)}},
        )
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


_LAST_RUN: datetime | None = None
_ANNOUNCED: bool = False


def reset_for_tests() -> None:
    global _LAST_RUN, _ANNOUNCED
    _LAST_RUN = None
    _ANNOUNCED = False


__all__ = [
    "EVALUABLE_STATES", "NotAuthorized", "eligible_gates", "evaluation_fingerprint",
    "should_record", "run_evaluation_cycle", "run_scheduled_evaluation",
    "reset_for_tests",
]
