"""Enforcement (spec §4.2, §14, §33) — the recorded mode, lineage admission, drift,
readiness and observability layer.

This module is the ONE part of Experiment OS the trading worker imports (via the
repository write helpers), so it deliberately imports only models/lifecycle — no
service layer, no flush guard, nothing that could alter how the hot loop writes
its own tables. It must be safe to call on every entry of every cycle.

## Recorded enforcement state

The current mode is the newest row of `experiment_os_enforcement` — an explicit,
append-only, audited record (mode, effective instant, actor, reason, unique
cutover id, system version, readiness evidence). It is NEVER inferred from a
deploy, a merge, the first Experiment OS row, or the importer's run time. With no
row (or no table), the mode is OFF.

Moving to NEW_ONLY or STRICT requires a production-readiness report with
`ok=True` (the mechanical checklist below) or an explicit forced override, so an
incomplete baseline cannot be enforced by accident.

## Admission semantics per mode

Every new paper entry / live order resolves its strategy tag against the ACTIVE
deployment-arm links (open deployment, open epoch, non-retired experiment):

  OFF      — stamp lineage when the tag resolves uniquely; never block, never warn.
  WARN     — stamp; unknown/ambiguous tags are recorded (system_events, counters)
             but allowed.
  NEW_ONLY — stamp; a tag that resolves (native or grandfathered) is allowed; an
             UNKNOWN or AMBIGUOUS tag is BLOCKED (LineageBlocked) — no new
             experimental exposure without lineage.
  STRICT   — NEW_ONLY plus config-drifted deployments' tags are also blocked.

## Resolver outage (degraded state)

A refresh failure keeps the LAST GOOD snapshot and marks the state degraded:
already-known lineage keeps resolving from cache, so grandfathered and native
books are never shut down by a metadata outage — but unknown/ambiguous tags STAY
fail-closed under NEW_ONLY/STRICT. The invariant: degradation may preserve
continuity for previously known lineage; it may never create permission for
previously unknown lineage. Entering the degraded state records a loud
system_events error (surfaced by enforcement_report, readiness and the ops
status script). Residual honesty: if the very first refresh of a process fails,
there is no cached snapshot OR mode — the recorded-mode contract ("no readable
record ⇒ OFF") applies, degraded + alarmed; in practice the write path shares
the same database, so a total outage cannot accumulate rows either.

Blocking is per-tag: a refused NEW tag can only interrupt its own book's entry
path (cycle code isolates books), never a registered sibling's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..models import LiveOrder, PaperTrade, SystemEvent
from .lifecycle import EnforcementMode, LifecycleState
from .metrics import METRICS_ENGINE_REVISION
from .models import (
    Experiment,
    ExperimentDeployment,
    ExperimentDeploymentArm,
    ExperimentEpoch,
    ExperimentIntegrityEvent,
    ExperimentOsEnforcement,
    ExperimentVersion,
    PlatformImpactAction,
)

logger = logging.getLogger(__name__)

# The Experiment OS system version recorded on enforcement changes and reported by
# the observability surface. Bump alongside material enforcement-semantics changes.
ENFORCEMENT_SYSTEM_VERSION = "experiment_os:pr4+" + METRICS_ENGINE_REVISION

# Result identities allowed to authorize real-money promotions once enforcement is
# on (spec §27: no promotion on a good-looking number — or a hand-typed one).
TRUSTED_EVALUATORS = frozenset({"system"})
ALLOWED_METRIC_REVISIONS = frozenset({METRICS_ENGINE_REVISION})


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LineageBlocked(Exception):
    """A new experimental action was refused for missing/invalid lineage.

    Raised only under NEW_ONLY/STRICT for tags that cannot be resolved to a
    registered deployment arm — i.e. exactly the work that must not begin outside
    Experiment OS. Cycle code already isolates books, so this never propagates
    beyond the unregistered book's own entry path."""

    def __init__(self, tag: str, reason: str):
        self.tag = tag
        self.reason = reason
        super().__init__(f"strategy tag {tag!r}: {reason}")


# ---------------------------------------------------------------------------
# Recorded enforcement state
# ---------------------------------------------------------------------------


def current_enforcement(session) -> ExperimentOsEnforcement | None:
    """The authoritative newest enforcement record, or None (⇒ OFF)."""
    try:
        return session.scalar(
            select(ExperimentOsEnforcement)
            .order_by(
                ExperimentOsEnforcement.effective_at.desc(),
                ExperimentOsEnforcement.id.desc(),
            )
            .limit(1)
        )
    except Exception:  # noqa: BLE001 — table missing on an unmigrated DB ⇒ OFF
        session.rollback()
        return None


def current_mode(session) -> EnforcementMode:
    row = current_enforcement(session)
    return EnforcementMode(row.mode) if row is not None else EnforcementMode.OFF


def record_enforcement_change(
    session,
    *,
    mode: EnforcementMode | str,
    actor: str,
    reason: str,
    cutover_id: str,
    effective_at: datetime | None = None,
    readiness: dict | None = None,
    force: bool = False,
) -> ExperimentOsEnforcement:
    """Record an explicit enforcement change — the one answer to "when did
    Experiment OS begin enforcing?".

    NEW_ONLY/STRICT require a readiness report with ok=True, or force=True with a
    reason that says why the checklist is being overridden. The change is
    append-only and audited; effective_at is the caller's explicit instant (or the
    recording instant), never something inferred later."""
    m = EnforcementMode(mode)
    if not actor or not reason or not cutover_id:
        raise ValueError("enforcement changes require actor, reason and cutover_id")
    if m in (EnforcementMode.NEW_ONLY, EnforcementMode.STRICT):
        if readiness is None:
            raise ValueError(
                f"{m.value} requires the production-readiness report "
                "(production_readiness(session)) — do not enforce an unverified baseline"
            )
        if not readiness.get("ok") and not force:
            failing = [k for k, v in (readiness.get("checks") or {}).items()
                       if not v.get("ok")]
            raise ValueError(
                f"readiness is NOT ok (failing: {failing}) — fix the baseline or "
                "pass force=True with a reason that owns the override"
            )
    prior = current_enforcement(session)
    row = ExperimentOsEnforcement(
        cutover_id=cutover_id,
        mode=m.value,
        preceding_mode=prior.mode if prior is not None else None,
        effective_at=effective_at or _now(),
        actor=actor,
        reason=reason,
        system_version=ENFORCEMENT_SYSTEM_VERSION,
        readiness_json=readiness,
        created_at=_now(),
    )
    session.add(row)
    session.add(
        SystemEvent(
            created_at=_now(),
            level="info",
            component="experiment_os_enforcement",
            message=(
                f"enforcement mode → {m.value} (cutover {cutover_id}, actor {actor}"
                + (", FORCED past readiness" if force and readiness and
                   not readiness.get("ok") else "")
                + f"): {reason}"
            ),
            raw_json={"cutover_id": cutover_id, "mode": m.value,
                      "readiness_ok": bool(readiness and readiness.get("ok"))},
        )
    )
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Tag → lineage resolver (cached per cycle by refresh())
# ---------------------------------------------------------------------------


@dataclass
class _ResolverState:
    mode: EnforcementMode
    effective_at: datetime | None
    # tag → list of (arm_link_id, deployment_id, grandfathered, drifted,
    #                impact_blocked)
    tag_map: dict[str, list[tuple[int, int, bool, bool, bool]]]
    loaded_at: datetime
    degraded: bool = False
    # per-boot dedup so a blocked tag logs once, not every cycle
    logged: set[str] = field(default_factory=set)
    blocked_count: int = 0
    warned_count: int = 0


_STATE: _ResolverState | None = None


def _drifted_deployment_ids(session) -> set[int]:
    """Deployments with an UNRESOLVED config-drift integrity event."""
    rows = session.scalars(
        select(ExperimentIntegrityEvent.deployment_id).where(
            ExperimentIntegrityEvent.kind == "EXPERIMENT_CONFIG_DRIFT",
            ExperimentIntegrityEvent.resolved_at.is_(None),
            ExperimentIntegrityEvent.deployment_id.is_not(None),
        )
    )
    return set(rows)


def _impact_blocked_experiment_ids(session) -> set[int]:
    """Experiments with an unresolved MATERIAL platform-impact disposition:
    proposed (classification open), or accepted with an action that changes the
    world/numbers but has not been applied. Mirrors
    platform_impact._BLOCKING_WHEN_ACCEPTED — duplicated as a plain query because
    this module deliberately imports only models/lifecycle."""
    rows = session.execute(
        select(
            PlatformImpactAction.experiment_id,
            PlatformImpactAction.status,
            PlatformImpactAction.action,
        ).where(PlatformImpactAction.status.in_(("proposed", "accepted")))
    ).all()
    return {
        exp_id
        for exp_id, status, action in rows
        if status == "proposed" or action != "NO_ACTION"
    }


def refresh(session) -> None:
    """Reload the enforcement mode + active tag map. Called once per cycle by the
    worker; failures keep the previous snapshot (stale-but-safe) and mark the
    state degraded rather than raising into the trading loop."""
    global _STATE
    try:
        record = current_enforcement(session)
        mode = EnforcementMode(record.mode) if record else EnforcementMode.OFF
        effective = record.effective_at if record else None
        rows = session.execute(
            select(
                ExperimentDeploymentArm.strategy_tag,
                ExperimentDeploymentArm.id,
                ExperimentDeployment.id,
                ExperimentDeployment.grandfathered,
                Experiment.id,
            )
            .join(
                ExperimentDeployment,
                ExperimentDeployment.id == ExperimentDeploymentArm.deployment_id,
            )
            .join(ExperimentEpoch, ExperimentEpoch.id == ExperimentDeployment.epoch_id)
            .join(ExperimentVersion, ExperimentVersion.id == ExperimentEpoch.version_id)
            .join(Experiment, Experiment.id == ExperimentVersion.experiment_id)
            .where(
                ExperimentDeploymentArm.strategy_tag.is_not(None),
                ExperimentDeployment.ended_at.is_(None),
                ExperimentEpoch.ended_at.is_(None),
                Experiment.state != LifecycleState.RETIRED.value,
            )
        ).all()
        drifted = _drifted_deployment_ids(session)
        impact_blocked = _impact_blocked_experiment_ids(session)
        tag_map: dict[str, list[tuple[int, int, bool, bool, bool]]] = {}
        for tag, arm_link_id, dep_id, grand, exp_id in rows:
            tag_map.setdefault(tag, []).append(
                (arm_link_id, dep_id, bool(grand), dep_id in drifted,
                 exp_id in impact_blocked)
            )
        prev_logged = _STATE.logged if _STATE is not None else set()
        _STATE = _ResolverState(
            mode=mode, effective_at=effective, tag_map=tag_map,
            loaded_at=_now(), logged=prev_logged,
        )
    except Exception as exc:  # noqa: BLE001 — an outage must not kill the loop
        session.rollback()
        if _STATE is not None:
            entering = not _STATE.degraded
            _STATE.degraded = True
        else:
            entering = True
            _STATE = _ResolverState(
                mode=EnforcementMode.OFF, effective_at=None, tag_map={},
                loaded_at=_now(), degraded=True,
            )
        logger.error(
            "experiment OS enforcement refresh failed; keeping previous snapshot",
            extra={"extra_fields": {"error": str(exc)}},
        )
        if entering:  # loud, durable, once per outage (reset by a good refresh)
            try:
                session.add(
                    SystemEvent(
                        created_at=_now(), level="error",
                        component="experiment_os_enforcement",
                        message=(
                            "enforcement DEGRADED: resolver refresh failed — admission "
                            "continues on the LAST GOOD snapshot (known lineage keeps "
                            "trading; unknown tags stay fail-closed under "
                            f"NEW_ONLY/STRICT); error: {exc}"
                        ),
                        raw_json={"error": str(exc),
                                  "cached_tags": len(_STATE.tag_map),
                                  "cached_mode": _STATE.mode.value},
                    )
                )
                session.flush()
            except Exception:  # noqa: BLE001 — the alarm must not break the loop either
                session.rollback()


def _log_once(session, tag: str, level: str, message: str) -> None:
    if _STATE is None or tag in _STATE.logged:
        return
    _STATE.logged.add(tag)
    try:
        session.add(
            SystemEvent(
                created_at=_now(), level=level,
                component="experiment_os_enforcement", message=message,
                raw_json={"tag": tag},
            )
        )
        session.flush()
    except Exception:  # noqa: BLE001 — observability must never break the write path
        session.rollback()


def stamp_or_block(session, tag: str | None, *, channel: str) -> int | None:
    """The admission decision for one new paper entry / live order.

    Returns the deployment-arm id to stamp (or None when unresolvable but the
    mode allows the write). Raises LineageBlocked only when the CURRENT mode says
    this tag may not create new exposure. Called from the repository write
    helpers; must stay cheap (dict lookups on the per-cycle snapshot)."""
    state = _STATE
    if not tag or state is None:
        return None  # pre-enforcement caller (tests, scripts) — OFF semantics

    candidates = state.tag_map.get(tag, [])
    mode = state.mode
    # A resolver outage does NOT relax admission: known lineage continues from the
    # cached snapshot below; unknown/ambiguous tags keep the mode's semantics.
    # Degradation preserves continuity for known lineage — never permission for
    # unknown lineage.

    if len(candidates) == 1:
        arm_link_id, dep_id, grandfathered, drifted, impact_blocked = candidates[0]
        if drifted and mode is EnforcementMode.STRICT:
            state.blocked_count += 1
            _log_once(
                session, tag, "error",
                f"BLOCKED ({channel}): tag {tag!r} belongs to a config-drifted "
                "deployment and mode is STRICT — resolve the drift integrity event",
            )
            raise LineageBlocked(
                tag, "deployment config has drifted from its registered fingerprint "
                "(unresolved EXPERIMENT_CONFIG_DRIFT) and mode is STRICT"
            )
        if impact_blocked and mode is EnforcementMode.STRICT:
            state.blocked_count += 1
            _log_once(
                session, tag, "error",
                f"BLOCKED ({channel}): tag {tag!r} belongs to an experiment with an "
                "unresolved platform-impact disposition and mode is STRICT — apply "
                "(or exempt) the impact action before it may accumulate evidence",
            )
            raise LineageBlocked(
                tag, "the experiment has an unresolved material platform-impact "
                "disposition (proposed, or accepted-but-unapplied) and mode is STRICT"
            )
        return arm_link_id

    if mode is EnforcementMode.OFF:
        return None
    problem = (
        f"resolves to {len(candidates)} active deployment arms — ambiguous"
        if candidates
        else "is not registered to any active Experiment OS deployment arm"
    )
    if state.degraded:
        problem += (
            " [resolver DEGRADED: deciding from the last good snapshot of "
            f"{state.loaded_at:%Y-%m-%dT%H:%M:%SZ}; a tag registered during the "
            "outage stays blocked until refresh succeeds]"
        )
    if mode is EnforcementMode.WARN:
        state.warned_count += 1
        _log_once(
            session, tag, "warning",
            f"WARN ({channel}): tag {tag!r} {problem}; allowed under WARN — this "
            "becomes a hard block under NEW_ONLY",
        )
        return None
    # NEW_ONLY / STRICT: fail closed — no new exposure without lineage.
    state.blocked_count += 1
    _log_once(
        session, tag, "error",
        f"BLOCKED ({channel}): tag {tag!r} {problem}; register the "
        "experiment/deployment (or classify the tag) before it may trade",
    )
    raise LineageBlocked(tag, problem)


def tag_admissible(session, tag: str) -> bool:
    """Cheap pre-check for trackers that want to skip a blocked book without
    attempting an entry."""
    try:
        stamp_or_block(session, tag, channel="precheck")
        return True
    except LineageBlocked:
        return False


# ---------------------------------------------------------------------------
# Config drift (v1: the live books' material configuration)
# ---------------------------------------------------------------------------


def _canonical_material(value):
    """Order-insensitive canonical form of a material-config fact.

    Membership facts are SETS in meaning — `live_strategies_contains`
    ["Lmmsell8", "Lmmsell10"] and ["Lmmsell10", "Lmmsell8"] describe the same
    world. The observed side is rebuilt by iterating live config, so its order
    is incidental; comparing it raw against the manifest's hand-written order
    reports drift that does not exist, and a false EXPERIMENT_CONFIG_DRIFT is
    expensive: it blocks gate evaluation and, under STRICT, the book's tags."""
    if isinstance(value, list):
        return sorted(_canonical_material(v) for v in value)
    if isinstance(value, dict):
        return {k: _canonical_material(value[k]) for k in sorted(value)}
    return value


def _material_runtime_config(settings, material: dict) -> dict:
    """Recompute the registered 'material' config shape from live Settings so the
    two fingerprint-comparable dicts describe the same facts."""
    out: dict = {}
    if "live_strategies_contains" in material:
        live = {t.strip() for t in (settings.live_strategies or "").split(",") if t.strip()}
        out["live_strategies_contains"] = sorted(
            t for t in material["live_strategies_contains"] if t in live
        )
    if "twin_pairs" in material:
        pairs = {lt: tt for lt, tt in settings.live_paper_twin_pairs}
        out["twin_pairs"] = {
            lt: pairs.get(lt) for lt in material["twin_pairs"]
        }
    if "book_params" in material:
        params: dict[str, str | None] = {}
        configured = {}
        # mmsell_variants: semicolon-separated "tag:params" specs (the L replicas live here).
        for spec in (settings.mmsell_variants or "").split(";"):
            spec = spec.strip()
            if not spec or ":" not in spec:
                continue
            tag, param_str = spec.split(":", 1)
            configured[tag.strip()] = param_str.strip()
        for tag in material["book_params"]:
            params[tag] = configured.get(tag)
        out["book_params"] = params
    return out


#: Integrity kinds that record a STATE rather than a contamination. They stay open
#: for as long as the state holds and deliberately do not block gate evaluation —
#: see `_record_stand_down` for why the distinction is load-bearing.
NON_BLOCKING_INTEGRITY_KINDS: frozenset[str] = frozenset({
    "EXPERIMENT_EXECUTION_STOOD_DOWN",
})


def _record_stand_down(session, dep) -> None:
    """Mark one live deployment as intentionally stood down, and clear its drift.

    The canonical classification for "the operator emptied the live allowlist".
    Without it a deliberate pause masquerades as an unexplained integrity failure:
    the drift detector fires per book, the gates go BLOCKED_INTEGRITY, and the
    event text asks the reader to classify the cause as a deployment revision, a
    new epoch, a new version or platform impact — none of which is "someone turned
    it off on purpose"."""
    epoch = session.get(ExperimentEpoch, dep.epoch_id)
    version = session.get(ExperimentVersion, epoch.version_id)
    for drift in session.scalars(
        select(ExperimentIntegrityEvent).where(
            ExperimentIntegrityEvent.deployment_id == dep.id,
            ExperimentIntegrityEvent.kind == "EXPERIMENT_CONFIG_DRIFT",
            ExperimentIntegrityEvent.resolved_at.is_(None),
        )
    ):
        drift.resolved_at = _now()
        drift.resolution = (
            "reclassified as an intentional operator stand-down: the runtime live "
            "allowlist is empty, so no book is configured to trade. This is a "
            "recorded state, not a contamination — the evidence gathered before "
            "the stand-down is unaffected."
        )
    already = session.scalar(
        select(func.count()).select_from(ExperimentIntegrityEvent).where(
            ExperimentIntegrityEvent.deployment_id == dep.id,
            ExperimentIntegrityEvent.kind == "EXPERIMENT_EXECUTION_STOOD_DOWN",
            ExperimentIntegrityEvent.resolved_at.is_(None),
        )
    )
    if not already:
        session.add(
            ExperimentIntegrityEvent(
                experiment_id=version.experiment_id,
                version_id=version.id,
                epoch_id=epoch.id,
                deployment_id=dep.id,
                kind="EXPERIMENT_EXECUTION_STOOD_DOWN",
                severity="info",
                detected_at=_now(),
                description=(
                    f"{dep.deployment_key} is intentionally stood down — the "
                    "runtime live allowlist is empty, so this book places no new "
                    "entries. Open positions still exit and settle. Evidence "
                    "accrual is paused; existing evidence is unaffected and gate "
                    "evaluation is NOT blocked."
                ),
                details_json={"live_strategies": "", "stood_down": True},
                created_at=_now(),
            )
        )
    session.flush()


def runtime_config_check(session, settings) -> list[dict]:
    """Compare each ACTIVE live deployment's registered material config against the
    running Settings. A mismatch records an unresolved EXPERIMENT_CONFIG_DRIFT
    integrity event (which already blocks gate evaluation via the PR 3 evaluator,
    and blocks the tags under STRICT). Never raises; returns the drift findings.

    ONLY THE LIVE TRADING WORKER MAY JUDGE LIVE CONFIG. Several workers share this
    database — the evo worker runs the same boot path with `BOT_MODE=evo` and no
    live configuration at all. To such a process every live book looks like it
    vanished, and it would record drift on all of them: exactly what happened in
    production on 2026-08-16T16:01:40Z, blocking gate evaluation on both live
    canaries (and, under STRICT, it would have blocked real trading). "I cannot
    see the live config" is not evidence that the live config changed. A process
    that does not run the live books is not a witness to them."""
    findings: list[dict] = []
    if (getattr(settings, "bot_mode", None) or "") != "live":
        logger.debug(
            "skipping live-config drift check: not the live trading worker",
            extra={"extra_fields": {"bot_mode": getattr(settings, "bot_mode", None)}},
        )
        return findings
    try:
        # An EMPTY live allowlist is an operator stand-down, not per-book drift.
        # `config.live_strategy_list` is explicit that empty means nothing trades.
        stood_down = not [
            t for t in (settings.live_strategies or "").split(",") if t.strip()
        ]
        deployments = session.scalars(
            select(ExperimentDeployment).where(
                ExperimentDeployment.kind == "live",
                ExperimentDeployment.ended_at.is_(None),
            )
        ).all()
        for dep in deployments:
            material = (dep.config_json or {}).get("material")
            if not material:
                continue
            observed = _canonical_material(
                _material_runtime_config(settings, material)
            )
            if stood_down:
                # An OPERATOR STAND-DOWN, not drift. Every registered live book
                # "differs" from its registered config the moment the allowlist is
                # emptied, so the drift detector would report one unexplained
                # integrity failure per book — telling the operator N times that
                # they did the thing they did, in the vocabulary of a malfunction.
                #
                # Recorded as its own kind instead, and NON-BLOCKING: a stand-down
                # means no NEW evidence accrues, not that existing evidence is
                # contaminated. Blocking gate evaluation would conflate "nothing is
                # running" with "what ran cannot be trusted", which are different
                # claims about different things.
                _record_stand_down(session, dep)
                continue
            expected = _canonical_material({k: material[k] for k in observed})
            if observed == expected:
                # The authority on this book's config says it matches. Any open
                # drift event about it is therefore stale — clear it, with the
                # observation recorded. Without this, a false drift written by a
                # non-live worker (see the docstring) would block this
                # experiment's gates forever, since nothing else can resolve it.
                for stale in session.scalars(
                    select(ExperimentIntegrityEvent).where(
                        ExperimentIntegrityEvent.deployment_id == dep.id,
                        ExperimentIntegrityEvent.kind == "EXPERIMENT_CONFIG_DRIFT",
                        ExperimentIntegrityEvent.resolved_at.is_(None),
                    )
                ):
                    stale.resolved_at = _now()
                    stale.resolution = (
                        "resolved automatically: the LIVE trading worker observed "
                        f"{dep.deployment_key}'s runtime config matching its "
                        "registered material config"
                    )
                    session.flush()
                    logger.info(
                        "cleared stale config-drift event",
                        extra={"extra_fields": {"deployment": dep.deployment_key,
                                                "event_id": stale.id}},
                    )
                continue
            finding = {
                "deployment": dep.deployment_key,
                "expected": expected,
                "observed": observed,
            }
            findings.append(finding)
            already = session.scalar(
                select(func.count()).select_from(ExperimentIntegrityEvent).where(
                    ExperimentIntegrityEvent.deployment_id == dep.id,
                    ExperimentIntegrityEvent.kind == "EXPERIMENT_CONFIG_DRIFT",
                    ExperimentIntegrityEvent.resolved_at.is_(None),
                )
            )
            if not already:
                epoch = session.get(ExperimentEpoch, dep.epoch_id)
                version = session.get(ExperimentVersion, epoch.version_id)
                session.add(
                    ExperimentIntegrityEvent(
                        experiment_id=version.experiment_id,
                        version_id=version.id,
                        epoch_id=epoch.id,
                        deployment_id=dep.id,
                        kind="EXPERIMENT_CONFIG_DRIFT",
                        severity="error",
                        detected_at=_now(),
                        description=(
                            f"runtime config for {dep.deployment_key} differs from "
                            "the registered deployment — classify: harmless "
                            "deployment revision, new epoch, new version, or "
                            "platform impact. Gate evaluation is blocked while "
                            "unresolved."
                        ),
                        details_json=finding,
                        created_at=_now(),
                    )
                )
                session.flush()
    except Exception as exc:  # noqa: BLE001 — a boot check must not stop the worker
        session.rollback()
        logger.error(
            "runtime config check failed",
            extra={"extra_fields": {"error": str(exc)}},
        )
    return findings


# ---------------------------------------------------------------------------
# Production readiness (the mechanical pre-cutover checklist)
# ---------------------------------------------------------------------------


def production_readiness(session, settings=None) -> dict:
    """The 8-point checklist that must be green before NEW_ONLY can be recorded.

    Mechanical, read-only, and stored as the readiness evidence on the cutover
    record. `ok` is the conjunction of every check."""
    from . import importer  # lazy: importer pulls the manifest

    checks: dict[str, dict] = {}

    def check(name: str, ok: bool, detail) -> None:
        checks[name] = {"ok": bool(ok), "detail": detail}

    n_exp = session.scalar(select(func.count()).select_from(Experiment)) or 0
    check("import_ran", n_exp > 0, f"{n_exp} experiments recorded")

    report = importer.migration_report(session) if n_exp else {"unmapped_tags": None}
    unmapped = report.get("unmapped_tags")
    check(
        "coverage_complete",
        n_exp > 0 and unmapped == [],
        {"unmapped_tags": unmapped},
    )

    live_rows = session.execute(
        select(ExperimentDeployment, ExperimentDeploymentArm.strategy_tag)
        .join(
            ExperimentDeploymentArm,
            ExperimentDeploymentArm.deployment_id == ExperimentDeployment.id,
        )
        .where(
            ExperimentDeployment.kind == "live",
            ExperimentDeployment.ended_at.is_(None),
        )
    ).all()
    live_tags = sorted({tag for _, tag in live_rows if tag})
    if settings is not None:
        expected = sorted(
            t.strip() for t in (settings.live_strategies or "").split(",") if t.strip()
        )
        missing = [t for t in expected if t not in live_tags]
        check(
            "live_books_represented",
            not missing,
            {"configured_live": expected, "registered_live_tags": live_tags,
             "missing": missing},
        )
    else:
        check(
            "live_books_represented",
            bool(live_tags),
            {"registered_live_tags": live_tags,
             "note": "no Settings supplied — presence-only check"},
        )

    try:
        from .service import get_active_platform_snapshot, snapshot_missing_components

        snap = get_active_platform_snapshot(session)
        missing_components = (
            snapshot_missing_components(session, snap) if snap is not None else None
        )
        check(
            "platform_snapshot_complete",
            snap is not None and missing_components == [],
            {"snapshot": snap.fingerprint[:16] if snap else None,
             "missing_components": missing_components},
        )
    except Exception as exc:  # noqa: BLE001
        check("platform_snapshot_complete", False, f"error: {exc}")

    n_deps = session.scalar(
        select(func.count()).select_from(ExperimentDeployment)
    ) or 0
    n_grand = session.scalar(
        select(func.count()).select_from(ExperimentDeployment).where(
            ExperimentDeployment.grandfathered.is_(True)
        )
    ) or 0
    check(
        "grandfathered_identity",
        n_exp == 0 or (n_deps > 0 and n_grand > 0),
        {"deployments": n_deps, "grandfathered": n_grand,
         "native": n_deps - n_grand},
    )

    twin_problems: list[str] = []
    twin_notes: list[str] = []
    live_deps = {dep.id: dep for dep, _ in live_rows}
    for dep in live_deps.values():
        twin = session.scalar(
            select(ExperimentDeployment).where(
                ExperimentDeployment.twin_of_deployment_id == dep.id
            )
        )
        if twin is None:
            twin_problems.append(f"{dep.deployment_key}: no twin registered")
        elif twin.epoch_id != dep.epoch_id:
            twin_problems.append(f"{dep.deployment_key}: twin in a different epoch")
        elif twin.started_at != dep.started_at:
            msg = (
                f"{dep.deployment_key}: twin start {twin.started_at} != live "
                f"start {dep.started_at}"
            )
            if dep.grandfathered:
                # Imported truth, not a defect: e.g. theta4 armed live 2026-07-30
                # but its _pt3 twin epoch was cut at the 2026-08-11 fee re-baseline.
                # Recording the asymmetry honestly beats inventing an equal
                # boundary; the strict same-instant rule binds NATIVE canaries,
                # whose boundary arm_live_canary controls.
                twin_notes.append(msg + " (grandfathered — recorded, not invented)")
            else:
                twin_problems.append(msg)
    check(
        "live_twin_links",
        not twin_problems,
        {"problems": twin_problems, "grandfathered_notes": twin_notes}
        if (twin_problems or twin_notes)
        else "all linked",
    )

    open_integrity = session.scalar(
        select(func.count()).select_from(ExperimentIntegrityEvent).where(
            ExperimentIntegrityEvent.resolved_at.is_(None)
        )
    ) or 0
    check("no_unresolved_integrity", open_integrity == 0,
          f"{open_integrity} unresolved integrity events")

    open_impact = session.scalar(
        select(func.count()).select_from(PlatformImpactAction).where(
            PlatformImpactAction.status.in_(("proposed", "accepted"))
        )
    ) or 0
    check(
        "no_unresolved_platform_impact",
        open_impact == 0,
        f"{open_impact} platform-impact dispositions awaiting acceptance/application",
    )

    # Both runtime write paths must be able to persist lineage — NEW_ONLY on a DB
    # where live_orders (or paper_trades) cannot stamp would enforce blind.
    try:
        from sqlalchemy import inspect as sa_inspect

        # Inspect over the session's OWN connection: an engine-level inspector
        # checks a pooled connection back in with a rollback, which on sqlite's
        # shared-connection pool silently wipes the session's uncommitted work.
        inspector = sa_inspect(session.connection())
        detail: dict[str, str] = {}
        both = True
        for table in ("paper_trades", "live_orders"):
            try:
                cols = {c["name"] for c in inspector.get_columns(table)}
                present = "experiment_deployment_arm_id" in cols
            except Exception as exc:  # noqa: BLE001 — a missing table is a failure, not a crash
                present = False
                detail[table] = f"error inspecting table: {exc}"
            else:
                detail[table] = (
                    "experiment_deployment_arm_id present"
                    if present
                    else "experiment_deployment_arm_id MISSING — migration not applied"
                )
            both = both and present
        check("lineage_columns_present", both, detail)
    except Exception as exc:  # noqa: BLE001
        check("lineage_columns_present", False, f"error: {exc}")

    recent_degraded = session.scalar(
        select(func.count()).select_from(SystemEvent).where(
            SystemEvent.component == "experiment_os_enforcement",
            SystemEvent.message.like("enforcement DEGRADED%"),
            SystemEvent.created_at >= _now() - timedelta(hours=6),
        )
    ) or 0
    check(
        "resolver_health",
        recent_degraded == 0,
        f"{recent_degraded} resolver-degraded alarms in the last 6h"
        + ("" if recent_degraded == 0 else " — do not cut over during/right after "
           "a metadata outage"),
    )

    return {
        "ok": all(c["ok"] for c in checks.values()),
        "checked_at": str(_now()),
        "system_version": ENFORCEMENT_SYSTEM_VERSION,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


def enforcement_report(session) -> dict:
    """The Control Tower answer to "is anything trading or accumulating evidence
    outside Experiment OS?" — mechanically, from the DB."""
    record = current_enforcement(session)
    mode = record.mode if record else EnforcementMode.OFF.value
    cutover_at = record.effective_at if record else None

    n_deps = session.scalar(select(func.count()).select_from(ExperimentDeployment)) or 0
    n_grand = session.scalar(
        select(func.count()).select_from(ExperimentDeployment).where(
            ExperimentDeployment.grandfathered.is_(True)
        )
    ) or 0

    def _post_cutover_unstamped(model) -> list:
        if cutover_at is None:
            return []
        rows = session.execute(
            select(model.strategy, func.count())
            .where(
                model.created_at >= cutover_at,
                model.experiment_deployment_arm_id.is_(None),
                model.strategy.is_not(None),
            )
            .group_by(model.strategy)
        ).all()
        return [{"tag": t, "rows": int(n)} for t, n in rows]

    live_canaries = []
    for dep in session.scalars(
        select(ExperimentDeployment).where(
            ExperimentDeployment.kind == "live",
            ExperimentDeployment.ended_at.is_(None),
        )
    ):
        twin = session.scalar(
            select(ExperimentDeployment).where(
                ExperimentDeployment.twin_of_deployment_id == dep.id
            )
        )
        live_canaries.append(
            {
                "deployment": dep.deployment_key,
                "grandfathered": dep.grandfathered,
                "twin": twin.deployment_key if twin else None,
                "boundary_match": bool(twin and twin.started_at == dep.started_at
                                       and twin.epoch_id == dep.epoch_id),
            }
        )

    open_drift = session.scalar(
        select(func.count()).select_from(ExperimentIntegrityEvent).where(
            ExperimentIntegrityEvent.kind == "EXPERIMENT_CONFIG_DRIFT",
            ExperimentIntegrityEvent.resolved_at.is_(None),
        )
    ) or 0
    open_integrity = session.scalar(
        select(func.count()).select_from(ExperimentIntegrityEvent).where(
            ExperimentIntegrityEvent.resolved_at.is_(None)
        )
    ) or 0

    degraded_alarms_24h = session.scalar(
        select(func.count()).select_from(SystemEvent).where(
            SystemEvent.component == "experiment_os_enforcement",
            SystemEvent.message.like("enforcement DEGRADED%"),
            SystemEvent.created_at >= _now() - timedelta(hours=24),
        )
    ) or 0

    state = _STATE
    return {
        "mode": mode,
        "cutover_id": record.cutover_id if record else None,
        "effective_at": str(cutover_at) if cutover_at else None,
        "system_version": record.system_version if record else None,
        "resolver_degraded_alarms_24h": degraded_alarms_24h,
        "deployments": {"total": n_deps, "grandfathered": n_grand,
                        "native": n_deps - n_grand},
        "post_cutover_unstamped": {
            "paper_trades": _post_cutover_unstamped(PaperTrade),
            "live_orders": _post_cutover_unstamped(LiveOrder),
        },
        "live_canaries": live_canaries,
        "unresolved_integrity_events": open_integrity,
        "unresolved_config_drift": open_drift,
        "session_counters": {
            "blocked": state.blocked_count if state else 0,
            "warned": state.warned_count if state else 0,
            "resolver_degraded": state.degraded if state else False,
            "resolver_loaded_at": str(state.loaded_at) if state else None,
        },
    }


# ---------------------------------------------------------------------------
# The operator-declared cutover hook (worker-side, because ops is read-only)
# ---------------------------------------------------------------------------


def run_boot_cutover(session, settings) -> dict | None:
    """Record the operator's declared enforcement mode, gated on a readiness
    report computed at that instant. Idempotent, and NEVER raises.

    Why this lives in the worker: the ops channel is deliberately read-only
    against production Postgres (it holds DATABASE_URL_RO and a SELECT-only
    role), so the worker is the only process that can write the enforcement
    record. This hook is NOT an inference of the boundary — the operator
    declares the target mode, cutover id, identity and reason explicitly, and
    the canonical append-only record written here (whose `effective_at` is the
    real instant) remains the one boundary anything else reads.

    Safety: moving to NEW_ONLY/STRICT requires `production_readiness(...)` to be
    ok RIGHT NOW; a red checklist refuses loudly and changes nothing. There is
    deliberately NO env-driven force — overriding the checklist stays a human
    decision made through a different door (`record_enforcement_change(
    force=True)`), so a config typo can never bypass the gate."""
    declared = (getattr(settings, "experiment_os_enforcement_mode", "") or "").strip()
    if not declared:
        return None
    try:
        try:
            target = EnforcementMode(declared.upper())
        except ValueError:
            logger.error(
                "EXPERIMENT_OS_ENFORCEMENT_MODE is not a valid mode; ignoring",
                extra={"extra_fields": {"declared": declared,
                                        "valid": [m.value for m in EnforcementMode]}},
            )
            return {"ok": False, "reason": "invalid mode", "declared": declared}

        current = current_mode(session)
        if target is current:
            return None  # already there — idempotent, silent, safe to leave set

        cutover_id = (getattr(settings, "experiment_os_cutover_id", "") or "").strip()
        actor = (getattr(settings, "experiment_os_cutover_actor", "") or "").strip()
        reason = (getattr(settings, "experiment_os_cutover_reason", "") or "").strip()
        missing = [
            name
            for name, value in (
                ("EXPERIMENT_OS_CUTOVER_ID", cutover_id),
                ("EXPERIMENT_OS_CUTOVER_ACTOR", actor),
                ("EXPERIMENT_OS_CUTOVER_REASON", reason),
            )
            if not value
        ]
        if missing:
            logger.error(
                "enforcement cutover declared but not attributable; refusing",
                extra={"extra_fields": {"target": target.value, "missing": missing}},
            )
            return {"ok": False, "reason": "missing attribution", "missing": missing}

        readiness = production_readiness(session, settings)
        gated = target in (EnforcementMode.NEW_ONLY, EnforcementMode.STRICT)
        if gated and not readiness.get("ok"):
            failing = [k for k, v in (readiness.get("checks") or {}).items()
                       if not v.get("ok")]
            logger.error(
                "enforcement cutover REFUSED — readiness is not ok",
                extra={"extra_fields": {"target": target.value, "failing": failing,
                                        "cutover_id": cutover_id}},
            )
            session.add(
                SystemEvent(
                    created_at=_now(), level="error",
                    component="experiment_os_enforcement",
                    message=(
                        f"enforcement cutover to {target.value} REFUSED: readiness "
                        f"checks failing {failing} (cutover {cutover_id}). Mode "
                        f"remains {current.value}."
                    ),
                    raw_json={"target": target.value, "failing": failing,
                              "cutover_id": cutover_id},
                )
            )
            session.flush()
            return {"ok": False, "reason": "readiness not ok", "failing": failing,
                    "readiness": readiness}

        record = record_enforcement_change(
            session,
            mode=target,
            actor=actor,
            reason=reason,
            cutover_id=cutover_id,
            readiness=readiness,
        )
        logger.info(
            "enforcement cutover RECORDED",
            extra={"extra_fields": {
                "mode": target.value, "preceding": current.value,
                "cutover_id": cutover_id, "actor": actor,
                "effective_at": str(record.effective_at),
            }},
        )
        return {"ok": True, "mode": target.value, "cutover_id": cutover_id,
                "effective_at": str(record.effective_at), "readiness": readiness}
    except Exception as exc:  # noqa: BLE001 — a cutover attempt must not stop the worker
        logger.error(
            "enforcement cutover hook failed (trading unaffected)",
            extra={"extra_fields": {"error": str(exc)}},
        )
        try:
            session.rollback()
            session.add(
                SystemEvent(
                    created_at=_now(), level="error",
                    component="experiment_os_enforcement",
                    message=f"enforcement cutover hook failed: {exc}",
                    raw_json={"declared": declared},
                )
            )
            session.flush()
        except Exception:  # noqa: BLE001
            logger.exception("could not record the cutover failure either")
        return None


def reset_for_tests() -> None:
    """Test hook: clear the module resolver state."""
    global _STATE
    _STATE = None
