"""Executable GateSpec evaluation (spec §11) — verdicts from structured state.

Answers "what does this experiment's pre-registered gate say about its current
evidence?" with one engine instead of per-book status logic. Every evaluation is
persisted as an immutable ExperimentGateResult carrying its full identity: gate,
version, epoch, platform snapshot, evidence window, metric-engine revision, and a
per-clause breakdown — a verdict can never outlive its context.

## Metric addressing — never guessed

A clause must resolve to explicit scopes before anything is computed:

  * `"arm": "<arm_key>"` — one declared arm;
  * `"arm": "*"` — every declared arm, individually (pass_all: each must pass;
    fail_any: any arm trips it);
  * `"scope": "experiment"` — the version as a whole (probe-instrument
    quantities, portfolio-level numbers); tags = the union of every arm's tags;
  * no arm at all — legal ONLY when the version declares exactly one arm (a
    unique referent is resolution, not inference);
  * delta/paired metrics — either `"treatment"`/`"control"` naming two declared
    arms, or `"arm"` (the treatment) plus an explicit
    `"external_control": {"experiment_key", "arm_key"}` reference for
    cross-experiment controls (the mmsellA4-vs-mmsell10 shape). External controls
    are first-class references — never smuggled into a metric name;
  * `"deployment_kind"` (default "paper") picks which of the arm's deployments
    back the value — live tags and twin tags are never silently mixed into a
    paper aggregate.

Anything ambiguous — an absolute clause with no arm on a multi-arm version, an
undeclared arm, a delta without a control, an unresolvable external reference, a
metric key outside the registry — refuses evaluation as BLOCKED_INTEGRITY naming
the exact ambiguity. Grandfathered gates are never quietly reinterpreted; fixing
one is a new gate on a new version.

## Verdict procedure (deterministic order, most-structural first)

  1. structural resolution failed        → BLOCKED_INTEGRITY
  2. platform comparability failed       → BLOCKED_PLATFORM
     (the epoch's pinned snapshot no longer matches the active revision set and
     the change touches the evidence window — or the change's activation boundary
     is unestablished, spec §17.4; no convenient timestamp is ever substituted)
  3. unresolved integrity events         → BLOCKED_INTEGRITY
  4. any required metric missing         → BLOCKED_DATA (missing ≠ zero)
  4a. the pre-registered evidence horizon is reached → HORIZON_EXHAUSTED
     (no further authorization look accrues; never auto-PASS, never auto-FAIL)
  4b. an ELIGIBLE early-safety clause true → FAIL (a fail_any clause carrying its
     own `min_evidence`, checked BEFORE the promotion floor — see below)
  5. sample floors unmet                 → HOLD (underpowered is HOLD, not FAIL)
  6. any eligible fail_any clause true   → FAIL
  7. all pass_all clauses true           → PASS
  8. otherwise                           → HOLD

`sample` is the PROMOTION evidence floor: how much evidence before a PASS may
authorize advancement. It is not the same question as how much evidence before
bad evidence may terminate, and using one number for both silently makes safety
clauses unreachable — a catastrophic failure at a fifth of the promotion floor
would sit at HOLD while real money kept trading. A `fail_any` clause may
therefore carry its own `min_evidence`, which makes it eligible on that floor
alone. A clause without `min_evidence` inherits the promotion floor, so no gate
frozen before this existed behaves differently.

A clause may also carry a `bound` — see `bounds.py`. The comparison is then
against the one-sided confidence bound, never the point estimate, and an
uncomputable bound is MISSING rather than a silent fallback to the estimate.

Evidence windows are hard-bounded: [max(epoch start, gate evidence start),
min(requested end, epoch end)]. Pre-epoch rows can never count after an I2
boundary, and pooling across snapshots is impossible by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from . import read
from . import service as svc
from .bounds import compute_bound, validate_bound_spec
from .lifecycle import GateVerdict
from .metrics import (
    METRICS_ENGINE_REVISION,
    PAIRED_METRICS,
    MetricScope,
    MetricValue,
    compute_metric,
    compute_paired_metric,
    is_delta_metric,
    provider_revisions,
    resolve_definition,
)
from .models import (
    Experiment,
    ExperimentEpoch,
    ExperimentGate,
    ExperimentGateResult,
    ExperimentIntegrityEvent,
    ExperimentVersion,
    PlatformComponent,
    PlatformImpactAction,
    PlatformRevision,
    PlatformSnapshot,
)

_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

_DEFAULT_KIND = "paper"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime) -> datetime:
    """Normalize for comparison: sqlite round-trips drop tzinfo; DB times are UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@dataclass
class ClauseOutcome:
    """One evaluated clause (or one arm-expansion of a clause)."""

    list_name: str  # sample | pass_all | fail_any | hold_if
    clause: dict
    scope: str  # human-readable scope label(s)
    value: float | None = None
    n: int = 0
    passed: bool | None = None  # None = undecidable (empty sample)
    missing: bool = False
    reason: str | None = None
    provenance: dict = field(default_factory=dict)
    #: The one-sided bound actually compared, when the clause carries `bound`.
    #: `value` stays the point estimate so both are visible in the record.
    bound_value: float | None = None
    #: fail_any only. False when the clause carries `min_evidence` that is not yet
    #: met, which makes it inert rather than false — an important difference: an
    #: ineligible clause has not been tested, it has not passed.
    eligible: bool = True

    def as_json(self) -> dict:
        return {
            "list": self.list_name,
            "clause": self.clause,
            "scope": self.scope,
            "value": self.value,
            "n": self.n,
            "passed": self.passed,
            "missing": self.missing,
            "reason": self.reason,
            "provenance": self.provenance,
            "bound_value": self.bound_value,
            "eligible": self.eligible,
        }


@dataclass
class EvaluationOutcome:
    verdict: str
    explanation: str
    evidence_start: datetime
    evidence_end: datetime
    epoch_number: int
    platform_snapshot_fingerprint: str
    clauses: list[ClauseOutcome] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    result_id: int | None = None  # set when persisted


class GateResolutionError(svc.ExperimentOsError):
    """Structural ambiguity — carries every exact problem found."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------


def _arm_scope(
    session,
    experiment: Experiment,
    version: ExperimentVersion,
    epoch: ExperimentEpoch,
    arm_key: str | None,
    kind: str,
    window: tuple[datetime, datetime],
    snapshot_fp: str,
) -> MetricScope:
    """The concrete tags backing one arm (or, with arm_key None, the whole
    experiment) within one epoch, for one deployment kind."""
    tags: list[str] = []
    dep_keys: list[str] = []
    for dep in read.deployments_for(session, epoch):
        if dep.kind != kind:
            continue
        for arm, tag in read.deployment_arms(session, dep):
            if (arm_key is None or arm.arm_key == arm_key) and tag:
                tags.append(tag)
                dep_keys.append(dep.deployment_key)
    return MetricScope(
        experiment_key=experiment.key,
        version=version.version,
        epoch_number=epoch.epoch_number,
        arm_key=arm_key,
        deployment_kind=kind,
        strategy_tags=tuple(sorted(set(tags))),
        deployment_keys=tuple(sorted(set(dep_keys))),
        window_start=window[0],
        window_end=window[1],
        platform_snapshot_fingerprint=snapshot_fp,
    )


def _external_scope(
    session,
    ref: dict,
    kind: str,
    window: tuple[datetime, datetime],
) -> tuple[MetricScope | None, str | None, str | None]:
    """Resolve an explicit external-control reference.

    Returns (scope, structural_problem, platform_problem). The external side is
    read over the SAME evidence window (the matched-window rule the registry's
    cross-book reads use); its own epoch's snapshot must match the evaluating
    epoch's, else the delta would pool evidence across incompatible snapshots."""
    exp_key = ref.get("experiment_key")
    arm_key = ref.get("arm_key")
    exp = read.get_experiment(session, exp_key) if exp_key else None
    if exp is None:
        return None, f"external_control experiment {exp_key!r} does not exist", None
    ver = read.latest_version(session, exp)
    if ver is None:
        return None, f"external_control {exp_key!r} has no version", None
    arms = {a.arm_key for a in read.arms_for(session, ver)}
    if arm_key not in arms:
        return (
            None,
            f"external_control arm {arm_key!r} is not declared on {exp_key!r} "
            f"(declared: {sorted(arms)})",
            None,
        )
    epoch = read.open_epoch_for(session, ver)
    if epoch is None:
        epochs = read.epochs_for(session, ver)
        epoch = epochs[-1] if epochs else None
    if epoch is None:
        return None, f"external_control {exp_key!r} has no operating epoch", None
    snap = session.get(PlatformSnapshot, epoch.platform_snapshot_id)
    scope = _arm_scope(session, exp, ver, epoch, arm_key, kind, window, snap.fingerprint)
    return scope, None, None


def _resolve_clause_scopes(
    session,
    experiment: Experiment,
    version: ExperimentVersion,
    epoch: ExperimentEpoch,
    arms: list,
    clause: dict,
    list_name: str,
    window: tuple[datetime, datetime],
    snapshot_fp: str,
    problems: list[str],
    platform_problems: list[str],
) -> list[tuple[dict, MetricScope, MetricScope | None]]:
    """Expand one clause into concrete (clause, treatment_scope, control_scope|None)
    evaluations. Structural problems are appended, not raised, so ONE evaluation
    reports every ambiguity at once."""
    where = f"{list_name} clause {clause.get('metric')!r}"
    metric = clause.get("metric") or ""
    kind = clause.get("deployment_kind", _DEFAULT_KIND)
    arm_keys = [a.arm_key for a in arms]

    if resolve_definition(metric) is None:
        problems.append(f"{where}: metric is not in the canonical registry")
        return []

    needs_pair = is_delta_metric(metric) or metric in PAIRED_METRICS

    external = clause.get("external_control")
    treatment_key = clause.get("treatment") or clause.get("arm")
    control_key = clause.get("control")

    if clause.get("scope") == "experiment":
        if treatment_key is not None or control_key is not None or external is not None:
            problems.append(
                f"{where}: scope 'experiment' excludes arm/treatment/control/"
                "external_control"
            )
            return []
        if needs_pair:
            problems.append(
                f"{where}: a delta/paired metric cannot be experiment-scoped — it "
                "needs two sides"
            )
            return []
        return [(
            clause,
            _arm_scope(session, experiment, version, epoch, None, kind, window,
                       snapshot_fp),
            None,
        )]

    if needs_pair:
        if external is not None and control_key is not None:
            problems.append(f"{where}: both control and external_control given — pick one")
            return []
        if external is None and control_key is None:
            problems.append(
                f"{where}: a delta/paired metric needs an explicit control — "
                "either 'treatment'/'control' arms or an 'external_control' reference"
            )
            return []
        if treatment_key is None:
            if len(arm_keys) == 1:
                treatment_key = arm_keys[0]
            else:
                problems.append(
                    f"{where}: no treatment arm named and the version declares "
                    f"{len(arm_keys)} arms — a scope cannot be inferred"
                )
                return []
    else:
        if external is not None or control_key is not None:
            problems.append(f"{where}: an absolute metric takes no control")
            return []
        if treatment_key is None:
            if len(arm_keys) == 1:
                treatment_key = arm_keys[0]  # unique referent — resolution, not inference
            else:
                problems.append(
                    f"{where}: no arm named and the version declares "
                    f"{len(arm_keys)} arms ({sorted(arm_keys)}) — scope must be explicit "
                    "('arm': '<key>' or 'arm': '*')"
                )
                return []

    # Expand the treatment side.
    if treatment_key == "*":
        treatment_keys = arm_keys
    else:
        if treatment_key not in arm_keys:
            problems.append(
                f"{where}: arm {treatment_key!r} is not declared on this version "
                f"(declared: {sorted(arm_keys)})"
            )
            return []
        treatment_keys = [treatment_key]

    control_scope: MetricScope | None = None
    if needs_pair:
        if external is not None:
            if not isinstance(external, dict) or not external.get("experiment_key") \
                    or not external.get("arm_key"):
                problems.append(
                    f"{where}: external_control must be "
                    "{'experiment_key': ..., 'arm_key': ...}"
                )
                return []
            control_scope, problem, plat = _external_scope(session, external, kind, window)
            if problem:
                problems.append(f"{where}: {problem}")
                return []
            if control_scope.platform_snapshot_fingerprint != snapshot_fp:
                platform_problems.append(
                    f"{where}: external control {control_scope.label()} is pinned to a "
                    "different platform snapshot — a cross-snapshot delta would pool "
                    "incomparable evidence"
                )
                return []
        else:
            if control_key not in arm_keys:
                problems.append(
                    f"{where}: control arm {control_key!r} is not declared "
                    f"(declared: {sorted(arm_keys)})"
                )
                return []
            control_scope = _arm_scope(
                session, experiment, version, epoch, control_key, kind, window, snapshot_fp
            )

    plans = []
    for tkey in treatment_keys:
        tscope = _arm_scope(
            session, experiment, version, epoch, tkey, kind, window, snapshot_fp
        )
        plans.append((clause, tscope, control_scope))
    return plans


def validate_gate_scopes(session, version: ExperimentVersion, spec: dict) -> list[str]:
    """Freeze-time structural validation: every clause and floor must resolve
    against the version's final arm set. Returns the list of problems (empty =
    clean). External references are shape-checked only — their existence is an
    evaluation-time concern (import order must not dictate registration order)."""
    problems: list[str] = []
    arms = read.arms_for(session, version)
    arm_keys = {a.arm_key for a in arms}
    for arm_key in (spec.get("sample") or {}):
        if arm_key not in arm_keys:
            problems.append(
                f"sample floor names arm {arm_key!r} which is not declared "
                f"(declared: {sorted(arm_keys)})"
            )
    horizon = spec.get("max_evidence_horizon")
    if horizon is not None:
        if not isinstance(horizon, dict) or not {"metric", "value"} <= horizon.keys():
            problems.append("max_evidence_horizon needs metric and value")
        elif resolve_definition(horizon.get("metric") or "") is None:
            problems.append(
                f"max_evidence_horizon metric {horizon.get('metric')!r} is not in "
                "the canonical registry"
            )
        else:
            floor_metrics = {
                f.get("metric") for f in (spec.get("sample") or {}).values()
                if isinstance(f, dict)
            }
            if floor_metrics and horizon["metric"] not in floor_metrics:
                problems.append(
                    f"max_evidence_horizon counts {horizon['metric']!r} but the "
                    f"promotion floor counts {sorted(m for m in floor_metrics if m)} "
                    "— a horizon in a different unit from the floor it bounds "
                    "cannot be reasoned about"
                )
            for f in (spec.get("sample") or {}).values():
                if (isinstance(f, dict) and f.get("metric") == horizon["metric"]
                        and isinstance(f.get("value"), (int, float))
                        and horizon["value"] <= f["value"]):
                    problems.append(
                        f"max_evidence_horizon ({horizon['value']}) is not above "
                        f"the promotion floor ({f['value']}) — the gate could "
                        "never render a verdict"
                    )
    for list_name in ("pass_all", "fail_any", "hold_if"):
        for clause in spec.get(list_name) or []:
            where = f"{list_name} clause {clause.get('metric')!r}"
            metric = clause.get("metric") or ""
            if resolve_definition(metric) is None:
                problems.append(f"{where}: metric is not in the canonical registry")
                continue
            if clause.get("bound") is not None:
                problems += [f"{where}: {m}"
                             for m in validate_bound_spec(clause["bound"])]
            me = clause.get("min_evidence")
            if me is not None:
                if list_name != "fail_any":
                    problems.append(
                        f"{where}: min_evidence is only meaningful on a fail_any "
                        "clause — a promotion clause's floor is the gate's `sample`"
                    )
                elif not isinstance(me, dict) or not {"metric", "op", "value"} <= me.keys():
                    problems.append(
                        f"{where}: min_evidence needs metric, op and value"
                    )
                elif resolve_definition(me.get("metric") or "") is None:
                    problems.append(
                        f"{where}: min_evidence metric {me.get('metric')!r} is not "
                        "in the canonical registry"
                    )
                elif me.get("op") not in _OPS:
                    problems.append(
                        f"{where}: min_evidence op {me.get('op')!r} is not supported"
                    )
            needs_pair = is_delta_metric(metric) or metric in PAIRED_METRICS
            external = clause.get("external_control")
            treatment = clause.get("treatment") or clause.get("arm")
            control = clause.get("control")
            if clause.get("scope") == "experiment":
                if treatment is not None or control is not None or external is not None:
                    problems.append(
                        f"{where}: scope 'experiment' excludes arm/treatment/"
                        "control/external_control"
                    )
                if needs_pair:
                    problems.append(
                        f"{where}: a delta/paired metric cannot be experiment-scoped"
                    )
                continue
            if needs_pair:
                if external is None and control is None:
                    problems.append(f"{where}: delta/paired metric needs an explicit control")
                if external is not None and (
                    not isinstance(external, dict)
                    or not external.get("experiment_key")
                    or not external.get("arm_key")
                ):
                    problems.append(f"{where}: malformed external_control reference")
                if control is not None and control not in arm_keys:
                    problems.append(f"{where}: control arm {control!r} not declared")
            elif external is not None or control is not None:
                problems.append(f"{where}: an absolute metric takes no control")
            if treatment is None and len(arm_keys) != 1:
                problems.append(
                    f"{where}: no arm named on a {len(arm_keys)}-arm version — "
                    "scope must be explicit"
                )
            elif treatment not in (None, "*") and treatment not in arm_keys:
                problems.append(f"{where}: arm {treatment!r} not declared")
    return problems


# ---------------------------------------------------------------------------
# Platform comparability (spec §17.4)
# ---------------------------------------------------------------------------


def platform_block_reasons(
    session, epoch: ExperimentEpoch, window_end: datetime
) -> list[str]:
    """Why evidence in this epoch/window cannot be interpreted under the current
    platform — empty when comparable.

    The epoch pinned a snapshot; if the active revision set has since changed for
    any component, the epoch is stale. The change is harmless to THIS evidence
    only when its activation boundary is established and lies beyond the window's
    end, OR when the experiment carries an APPLIED impact disposition for the new
    revision that licenses comparability (NO_ACTION: judged immaterial; RECOMPUTE:
    a named, registered normalization restores exactness — spec §18 I0/I1). An
    unestablished boundary always blocks (no convenient timestamp is ever
    substituted), and an established one inside the window blocks until a new
    epoch splits the sample or a disposition licenses it. An EXEMPTED record
    never licenses anything — declining to act is not a comparability claim."""
    pinned = dict(read.snapshot_contents(session, session.get(PlatformSnapshot,
                                                              epoch.platform_snapshot_id)))
    version_row = session.get(ExperimentVersion, epoch.version_id)
    experiment_id = version_row.experiment_id if version_row is not None else None
    licensed_revision_ids = set(
        session.scalars(
            select(PlatformImpactAction.revision_id).where(
                PlatformImpactAction.experiment_id == experiment_id,
                PlatformImpactAction.status == "applied",
                PlatformImpactAction.action.in_(("NO_ACTION", "RECOMPUTE")),
            )
        )
    ) if experiment_id is not None else set()
    reasons: list[str] = []
    active = session.execute(
        select(PlatformComponent.key, PlatformRevision.version,
               PlatformRevision.activated_at, PlatformRevision.id)
        .join(PlatformRevision, PlatformRevision.component_id == PlatformComponent.id)
        .where(PlatformRevision.status == "active")
    ).all()
    for key, version, activated_at, revision_id in active:
        pinned_version = pinned.get(key)
        if pinned_version is None or pinned_version == version:
            continue
        if revision_id in licensed_revision_ids:
            continue  # dispositioned: NO_ACTION judged it immaterial, or the
            #           applied RECOMPUTE's named normalizer restores comparability
        if activated_at is None:
            reasons.append(
                f"{key}: active revision {version!r} supersedes the epoch's pinned "
                f"{pinned_version!r} and its activation boundary is UNESTABLISHED — "
                "establish_activation_boundary() it (or open a new epoch) before "
                "evidence over this window can be interpreted"
            )
        elif _utc(activated_at) <= _utc(window_end):
            reasons.append(
                f"{key}: revision {version!r} activated at {activated_at} inside/"
                f"before the evidence window while the epoch pins {pinned_version!r} "
                "— the sample spans a platform boundary; open a new epoch (or "
                "classify the impact) rather than pooling across it"
            )
        # activated_at > window_end: the change postdates this evidence — fine.
    return reasons


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _unresolved_integrity(session, experiment, version, epoch) -> list[str]:
    rows = session.scalars(
        select(ExperimentIntegrityEvent).where(
            ExperimentIntegrityEvent.experiment_id == experiment.id,
            ExperimentIntegrityEvent.resolved_at.is_(None),
        )
    ).all()
    from .enforcement import NON_BLOCKING_INTEGRITY_KINDS

    reasons = []
    for ev in rows:
        if ev.version_id not in (None, version.id):
            continue
        if ev.epoch_id not in (None, epoch.id):
            continue
        if ev.kind in NON_BLOCKING_INTEGRITY_KINDS:
            # Records a STATE, not a contamination. An intentional stand-down
            # means no new evidence accrues; it does not mean the evidence already
            # gathered cannot be trusted. Blocking here would conflate "nothing is
            # running" with "what ran is suspect" — different claims about
            # different things, and only the second should stop a verdict.
            continue
        reasons.append(f"unresolved integrity event #{ev.id} [{ev.kind}]: "
                       f"{ev.description or 'no description'}")
    return reasons


def _min_evidence_state(session, clause: dict, scope: MetricScope) -> dict | None:
    """Whether a fail_any clause's own evidence floor is met, or None if it has one.

    A `fail_any` clause carrying `min_evidence` becomes eligible on its OWN floor,
    independently of the gate's promotion floor. That separation exists because a
    promotion floor answers "how much evidence before a PASS may authorize
    advancement", while a safety clause answers "how much evidence before bad
    evidence may terminate" — and a shared number silently makes the second
    unreachable. A clause without `min_evidence` inherits the promotion floor,
    which is the pre-existing behavior for every gate frozen so far."""
    spec = clause.get("min_evidence")
    if not spec:
        return None
    mv = compute_metric(session, spec["metric"], scope)
    met = (not mv.missing and mv.value is not None
           and bool(_OPS[spec["op"]](mv.value, spec["value"])))
    return {"metric": spec["metric"], "op": spec["op"], "value": spec["value"],
            "observed": mv.value, "met": met}


def _clause_sentence(c: ClauseOutcome) -> str:
    """One clause, stated so a reader never has to recompute anything.

    A bare `metric=1.34 > 1.0` says nothing about how much evidence stood behind
    it or which bound was taken. When a clause carries a bound, the sentence names
    the bound, the confidence, the point estimate, the evidence and the clause's
    own floor."""
    b = (c.provenance or {}).get("bound") or {}
    if b.get("computed_bound") is not None:
        conf = b.get("confidence")
        pct = f"{float(conf) * 100:g}%" if conf is not None else "?"
        side = "lower" if b.get("bound_direction") == "lower" else "upper"
        sent = (
            f"the {pct} {side} bound on {c.clause['metric']} "
            f"({_fmt(b['computed_bound'])}) {c.clause['op']} {c.clause['value']} "
            f"on {c.scope} — point estimate {_fmt(c.value)}, n={c.n}"
        )
        ui = b.get("uncertainty_inputs") or {}
        if "observed" in ui:
            sent += f" (observed {ui['observed']} against {_fmt(ui['expected'])} expected)"
        me = (c.provenance or {}).get("min_evidence")
        if me:
            sent += (f", eligible since {me['metric']} {me['op']} {me['value']} "
                     f"(now {_fmt(me['observed'])})")
        return sent
    return (f"{c.scope} {c.clause['metric']}={_fmt(c.value)} {c.clause['op']} "
            f"{c.clause['value']}")


def _evaluate_clause_plan(
    session, list_name: str, clause: dict,
    treatment: MetricScope, control: MetricScope | None,
) -> ClauseOutcome:
    metric = clause["metric"]
    if control is not None:
        mv: MetricValue = compute_paired_metric(session, metric, treatment, control)
        scope_label = f"{treatment.label()} vs {control.label()}"
    else:
        mv = compute_metric(session, metric, treatment)
        scope_label = treatment.label()

    prov = dict(mv.provenance or {})
    compared, bound_value = mv.value, None
    missing, reason = mv.missing, mv.reason

    bound_spec = clause.get("bound")
    if bound_spec is not None and not missing:
        br = compute_bound(estimate=mv.value, stderr=mv.stderr,
                           provenance=prov, spec=bound_spec)
        prov = prov | {"bound": br.provenance | {"computed_bound": br.value}}
        if br.missing:
            # A contract asked for a bound. Comparing the point estimate instead
            # would restore exactly the error rate the bound exists to remove, so
            # an uncomputable bound is MISSING.
            missing, reason = True, br.reason
        else:
            compared, bound_value = br.value, br.value

    passed: bool | None
    if missing or compared is None:
        passed = None
    else:
        passed = bool(_OPS[clause["op"]](compared, clause["value"]))

    eligible = True
    if list_name == "fail_any":
        me = _min_evidence_state(session, clause, treatment)
        if me is not None:
            eligible = me["met"]
            prov = prov | {"min_evidence": me}

    return ClauseOutcome(
        list_name=list_name, clause=clause, scope=scope_label,
        value=mv.value, n=mv.n, passed=passed,
        missing=missing, reason=reason, provenance=prov,
        bound_value=bound_value, eligible=eligible,
    )


def evaluate_gate(
    session,
    gate: ExperimentGate,
    *,
    window_end: datetime | None = None,
    epoch: ExperimentEpoch | None = None,
    persist: bool = True,
    computed_by: str = "system",
) -> EvaluationOutcome:
    """Evaluate one pre-registered gate over its epoch-bounded evidence window.

    Persists an immutable gate result (any verdict, including BLOCKED) unless
    persist=False (the CLI's dry-run). Never promotes anything — a verdict is a
    fact, not an action."""
    version = session.get(ExperimentVersion, gate.version_id)
    experiment = session.get(Experiment, version.experiment_id)
    if version.frozen_at is None:
        raise svc.ExperimentOsError("cannot evaluate a gate on an unfrozen version")
    if gate.evidence_started_at is None:
        raise svc.ExperimentOsError(
            f"gate {gate.gate_key!r} has no evidence_started_at — evidence has not "
            "begun; there is nothing to evaluate"
        )
    if epoch is None:
        epoch = read.open_epoch_for(session, version)
    if epoch is None:
        raise svc.ExperimentOsError(
            f"version {version.version} has no operating epoch to evaluate over"
        )
    end = _utc(window_end) if window_end is not None else _now()
    if epoch.ended_at is not None and _utc(epoch.ended_at) < end:
        end = _utc(epoch.ended_at)
    start = max(_utc(epoch.started_at), _utc(gate.evidence_started_at))
    snap = session.get(PlatformSnapshot, epoch.platform_snapshot_id)
    window = (start, end)

    outcome = EvaluationOutcome(
        verdict="", explanation="",
        evidence_start=start, evidence_end=end,
        epoch_number=epoch.epoch_number,
        platform_snapshot_fingerprint=snap.fingerprint,
    )
    arms = read.arms_for(session, version)
    spec = gate.spec_json

    # 1) structural resolution — collect EVERY problem before deciding.
    problems: list[str] = []
    platform_problems: list[str] = []
    plans: list[tuple[str, dict, MetricScope, MetricScope | None]] = []
    arm_keys = {a.arm_key for a in arms}
    for arm_key, floor_clause in (spec.get("sample") or {}).items():
        if arm_key not in arm_keys:
            problems.append(
                f"sample floor names arm {arm_key!r} which is not declared "
                f"(declared: {sorted(arm_keys)})"
            )
            continue
        kind = floor_clause.get("deployment_kind", _DEFAULT_KIND)
        if resolve_definition(floor_clause.get("metric") or "") is None:
            problems.append(
                f"sample[{arm_key}]: metric {floor_clause.get('metric')!r} is not in "
                "the canonical registry"
            )
            continue
        tscope = _arm_scope(
            session, experiment, version, epoch, arm_key, kind, window, snap.fingerprint
        )
        plans.append(("sample", floor_clause, tscope, None))
    horizon = spec.get("max_evidence_horizon")
    if horizon:
        for arm_key in (horizon.get("arms") or [None]):
            hscope = _arm_scope(
                session, experiment, version, epoch, arm_key,
                horizon.get("deployment_kind", _DEFAULT_KIND), window,
                snap.fingerprint,
            )
            plans.append(("horizon", {**horizon, "op": ">"}, hscope, None))
    for list_name in ("pass_all", "fail_any", "hold_if"):
        for clause in spec.get(list_name) or []:
            for plan in _resolve_clause_scopes(
                session, experiment, version, epoch, arms, clause, list_name,
                window, snap.fingerprint, problems, platform_problems,
            ):
                plans.append((list_name, *plan))

    if problems:
        outcome.verdict = GateVerdict.BLOCKED_INTEGRITY.value
        outcome.blocking_reasons = problems
        outcome.explanation = (
            "structurally ambiguous gate — evaluation refused rather than inferred: "
            + " | ".join(problems)
            + ". A mis-specified pre-registration is recorded, not reinterpreted; "
            "register a corrected gate on a new version."
        )
        return _finish(session, gate, experiment, version, epoch, outcome,
                       persist, computed_by)

    # 2) platform comparability — snapshot staleness AND unresolved impact
    # dispositions (a proposed classification, or an accepted material action not
    # yet applied, leaves the evidence uninterpretable — spec §17.4/§18).
    from .platform_impact import evidence_block_reasons  # lazy: imports service

    plat = (
        platform_block_reasons(session, epoch, end)
        + evidence_block_reasons(session, experiment)
        + platform_problems
    )
    if plat:
        outcome.verdict = GateVerdict.BLOCKED_PLATFORM.value
        outcome.blocking_reasons = plat
        outcome.explanation = "platform boundary blocks interpretation: " + " | ".join(plat)
        return _finish(session, gate, experiment, version, epoch, outcome,
                       persist, computed_by)

    # 3) recorded integrity events.
    integ = _unresolved_integrity(session, experiment, version, epoch)
    if integ:
        outcome.verdict = GateVerdict.BLOCKED_INTEGRITY.value
        outcome.blocking_reasons = integ
        outcome.explanation = (
            "unresolved integrity events block gate accumulation: " + " | ".join(integ)
        )
        return _finish(session, gate, experiment, version, epoch, outcome,
                       persist, computed_by)

    # 4) compute everything.
    for list_name, clause, tscope, cscope in plans:
        outcome.clauses.append(
            _evaluate_clause_plan(session, list_name, clause, tscope, cscope)
        )
    missing = [c for c in outcome.clauses if c.missing]
    if missing:
        outcome.verdict = GateVerdict.BLOCKED_DATA.value
        outcome.blocking_reasons = [
            f"{c.list_name} {c.clause.get('metric')!r} on {c.scope}: {c.reason}"
            for c in missing
        ]
        outcome.explanation = (
            "required metrics cannot be computed (missing is not zero): "
            + " | ".join(outcome.blocking_reasons)
        )
        return _finish(session, gate, experiment, version, epoch, outcome,
                       persist, computed_by)

    # 4a) EVIDENCE HORIZON. The horizon is the LAST PERMITTED LOOK, not a cutoff
    # before it. Three cases:
    #
    #   n <  horizon : evaluate normally
    #   n == horizon : evaluate normally — this IS the final permitted look, so a
    #                  PASS stands and a FAIL stands. Only an inconclusive result
    #                  becomes HORIZON_EXHAUSTED.
    #   n >  horizon : no further statistical look accrues at all.
    #
    # The `==` case matters most for an eligible safety clause: a failure that
    # only becomes demonstrable on the final pre-registered observation is a real
    # failure, and hiding it behind HORIZON_EXHAUSTED would discard the very
    # observation the contract paid for.
    horizon_clauses = [c for c in outcome.clauses if c.list_name == "horizon"]
    beyond = [c for c in horizon_clauses if c.passed is True]           # n > horizon
    at_horizon = [c for c in horizon_clauses
                  if c.value is not None and c.value == c.clause["value"]]

    def _horizon_explanation(cs, *, final_look: bool) -> str:
        standing = [
            f"{c.list_name}:{c.clause['metric']}="
            f"{_fmt(c.bound_value if c.bound_value is not None else c.value)}"
            f" {c.clause['op']} {c.clause['value']} -> "
            f"{'met' if c.passed else 'not met' if c.passed is False else 'undecidable'}"
            for c in outcome.clauses if c.list_name in ("pass_all", "fail_any")
        ]
        head = (
            "EVIDENCE HORIZON EXHAUSTED — OPERATOR DECISION REQUIRED. "
            + "; ".join(
                f"{c.scope} {c.clause['metric']}={_fmt(c.value)} "
                + ("is the final permitted look at the pre-registered horizon of "
                   if final_look else "is past the pre-registered horizon of ")
                + f"{c.clause['value']}"
                for c in cs
            )
        )
        return (
            head
            + (". The final permitted look was taken and did not decide."
               if final_look else
               ". No further statistical look accrues.")
            + " The contract's error rate was calibrated over a bounded number of "
            "evaluations; continuing to look past that is the multiple testing "
            "this horizon exists to stop. Nothing is auto-passed or auto-failed."
            + (" Clause standing: " + "; ".join(standing) if standing else "")
        )

    if beyond:
        outcome.verdict = GateVerdict.HORIZON_EXHAUSTED.value
        outcome.explanation = _horizon_explanation(beyond, final_look=False)
        return _finish(session, gate, experiment, version, epoch, outcome,
                       persist, computed_by)

    def _finish_statistical(oc):
        """Apply the final-look rule, then record.

        Used for every STATISTICAL verdict below. The BLOCKED_* paths above do not
        route through it: a blocked gate never took a look, so a horizon has
        nothing to say about it."""
        if at_horizon and oc.verdict not in (GateVerdict.PASS.value,
                                             GateVerdict.FAIL.value):
            oc.verdict = GateVerdict.HORIZON_EXHAUSTED.value
            oc.explanation = _horizon_explanation(at_horizon, final_look=True)
        return _finish(session, gate, experiment, version, epoch, oc,
                       persist, computed_by)

    # 4b) EARLY safety failure — a fail_any clause carrying its own `min_evidence`
    # is eligible on that floor alone, and is checked BEFORE the promotion floor.
    # Without this, one shared floor silently makes a safety clause unreachable:
    # a catastrophic failure at a fifth of the promotion floor would sit at HOLD
    # while real money kept trading. A clause with no `min_evidence` is not
    # included here and still waits for the promotion floor, so no gate frozen
    # before this change behaves differently.
    early = [c for c in outcome.clauses
             if c.list_name == "fail_any" and c.clause.get("min_evidence")
             and c.eligible and c.passed is True]
    if early:
        outcome.verdict = GateVerdict.FAIL.value
        outcome.explanation = "early safety failure: " + "; ".join(
            _clause_sentence(c) for c in early
        )
        return _finish_statistical(outcome)

    # 5) sample floors — underpowered is HOLD, never FAIL.
    floors = [c for c in outcome.clauses if c.list_name == "sample"]
    unmet = [c for c in floors if c.passed is not True]
    if unmet:
        outcome.verdict = GateVerdict.HOLD.value
        outcome.explanation = "sample floor not met: " + "; ".join(
            f"{c.scope} {c.clause['metric']}={_fmt(c.value)} vs {c.clause['op']} "
            f"{c.clause['value']}"
            for c in unmet
        )
        return _finish_statistical(outcome)

    # 6) kill clauses.
    fails = [c for c in outcome.clauses
             if c.list_name == "fail_any" and c.passed is True and c.eligible]
    if fails:
        outcome.verdict = GateVerdict.FAIL.value
        outcome.explanation = "fail condition met: " + "; ".join(
            _clause_sentence(c) for c in fails
        )
        return _finish_statistical(outcome)

    # 6b) explicit hold conditions (pre-declared revisit gates).
    holds = [c for c in outcome.clauses if c.list_name == "hold_if" and c.passed is True]
    if holds:
        outcome.verdict = GateVerdict.HOLD.value
        outcome.explanation = "hold condition met: " + "; ".join(
            f"{c.scope} {c.clause['metric']}={_fmt(c.value)}" for c in holds
        )
        return _finish_statistical(outcome)

    # 7) pass conditions — an undecidable clause (empty sample) cannot pass.
    passes = [c for c in outcome.clauses if c.list_name == "pass_all"]
    if passes and all(c.passed is True for c in passes):
        outcome.verdict = GateVerdict.PASS.value
        outcome.explanation = "all pass conditions met: " + "; ".join(
            f"{c.scope} {c.clause['metric']}={_fmt(c.value)}" for c in passes
        )
        return _finish_statistical(outcome)

    # 8) evidence sufficient but not decisive.
    outcome.verdict = GateVerdict.HOLD.value
    undecided = [c for c in passes if c.passed is not True]
    outcome.explanation = "holding: " + (
        "; ".join(
            f"{c.scope} {c.clause['metric']}={_fmt(c.value)} "
            f"(needs {c.clause['op']} {c.clause['value']}"
            + (f"; {c.reason}" if c.passed is None and c.reason else "")
            + ")"
            for c in undecided
        )
        or "no pass conditions registered"
    )
    return _finish_statistical(outcome)


def _fmt(v) -> str:
    return "undefined" if v is None else f"{v:g}"


def persist_outcome(
    session, gate, outcome: EvaluationOutcome, *, computed_by: str = "system",
    epoch=None, extra_metrics: dict | None = None,
) -> EvaluationOutcome:
    """Record an ALREADY-COMPUTED outcome as an immutable result.

    Exists so a caller can evaluate once (dry run), decide whether the result is
    a new decision point, and then persist *that exact outcome* — rather than
    re-evaluating and silently recording a different one because evidence moved
    in between. Same provenance as an evaluate_gate(persist=True) call.

    `extra_metrics` is merged into the metrics payload AT CREATION (the gate
    runner's dedupe fingerprint travels this way). It cannot be added afterwards:
    gate results are append-only, so a later edit is refused by the flush guard —
    correctly, since that would be rewriting recorded history."""
    version = session.get(ExperimentVersion, gate.version_id)
    experiment = session.get(Experiment, version.experiment_id)
    if epoch is None:
        epoch = read.open_epoch_for(session, version)
    return _finish(session, gate, experiment, version, epoch, outcome, True, computed_by,
                   extra_metrics=extra_metrics)


def _finish(
    session, gate, experiment, version, epoch,
    outcome: EvaluationOutcome, persist: bool, computed_by: str,
    extra_metrics: dict | None = None,
) -> EvaluationOutcome:
    if persist:
        floors = {
            c.clause.get("metric", "?") + "/" + c.scope: {"value": c.value, "n": c.n}
            for c in outcome.clauses
            if c.list_name == "sample"
        }
        result = svc.record_gate_result(
            session,
            gate,
            verdict=outcome.verdict,
            epoch=epoch,
            computed_by=computed_by,
            evidence_start=outcome.evidence_start,
            evidence_end=outcome.evidence_end,
            sample=floors or None,
            metrics={
                "clauses": [c.as_json() for c in outcome.clauses],
                "blocking_reasons": outcome.blocking_reasons,
                # WHICH provider implementations produced this verdict. Recorded
                # per metric, so a verdict binds to the implementations it used
                # rather than to one engine-wide constant — and so a result
                # computed while a provider was missing is never confusable with
                # one computed by the implementation.
                "provider_revisions": provider_revisions(
                    [c.as_json() for c in outcome.clauses]
                ),
                **(extra_metrics or {}),
            },
            metric_revision=METRICS_ENGINE_REVISION,
            evidence_ref=(
                f"{experiment.key}/v{version.version}/e{outcome.epoch_number} "
                f"[{outcome.evidence_start} .. {outcome.evidence_end}] "
                f"snapshot {outcome.platform_snapshot_fingerprint[:16]}"
            ),
            explanation=outcome.explanation,
        )
        outcome.result_id = result.id
    return outcome


def missing_metrics(clauses: list[dict] | None) -> list[str]:
    """The metric keys a recorded/computed outcome marked as MISSING.

    One extraction, shared by every surface, so "why is this gate blocked" is read
    out of the evaluator's own clause record rather than re-derived — a second
    opinion on the cause is exactly the drift Experiment OS exists to prevent.
    Accepts the `as_json()` clause shape, which is what gate results persist."""
    out: list[str] = []
    for c in clauses or []:
        if not isinstance(c, dict) or not c.get("missing"):
            continue
        metric = (c.get("clause") or {}).get("metric")
        if metric and metric not in out:
            out.append(str(metric))
    return sorted(out)


def latest_result(session, gate: ExperimentGate) -> ExperimentGateResult | None:
    return session.scalar(
        select(ExperimentGateResult)
        .where(ExperimentGateResult.gate_id == gate.id)
        .order_by(ExperimentGateResult.computed_at.desc(), ExperimentGateResult.id.desc())
        .limit(1)
    )
