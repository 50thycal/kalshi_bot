"""Legacy importer — materialize the reviewed manifest into Experiment OS (PR 2).

Turns `legacy_manifest.py` (the hand-authored migration classification) into rows:
the baseline platform snapshot, one experiment per manifest entry, and — for books
that still operate — the §22.2/22.3 reconstruction (version, arms, gates, migration
epoch pinned to the baseline snapshot, grandfathered deployments, twin links).

Ground rules, in code rather than convention:

  * **Idempotent.** Every run skips experiment keys and (component, version)
    revisions that already exist, so the importer can run at every boot; re-running
    with an extended manifest imports only the new entries.
  * **Nothing invented.** All content comes from the manifest (reviewed like the
    registry it condenses). The two runtime lookups are strictly *measured* data:
    a twin deployment's exact start instant from `live_paper_twins`, and the
    import instant itself, which is only ever used as the MIGRATION boundary
    (epoch start / grandfathering start), never back-dated into history.
  * **Clean floor.** Migration epochs open at the import instant. Historical
    windows ride along as legacy evidence (certified only where the manifest says
    comparability is provable); gates keep their historically recorded
    `evidence_started_at` floors (fee boundary, taxonomy cohort restart, pairing
    fix) because those are recorded facts, not reconstructions.
  * **Failure is loud but not fatal.** `run_boot_import` (the flag-gated worker
    hook) logs to system_events and never raises — a broken import must not stop
    trading.

The migration report compares DB reality (every distinct `paper_trades.strategy` /
`live_orders.strategy`) against manifest coverage and is persisted to
`system_events` whenever an import run did work. Evo strategies are out of scope by
design (spec §22.7): evo trades live in evo_* tables and keep their own lineage.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from ..models import LiveOrder, LivePaperTwin, PaperTrade, SystemEvent
from . import service as svc
from .legacy_manifest import BASELINE_REVISIONS, LEGACY_EXPERIMENTS, MANIFEST_VERSION
from .models import (
    Experiment,
    ExperimentDeploymentArm,
    PlatformRevision,
    PlatformSnapshot,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Baseline platform snapshot
# ---------------------------------------------------------------------------


def seed_baseline(session) -> tuple[PlatformSnapshot, list[str]]:
    """Register + activate the manifest's baseline revisions (idempotent) and return
    (the resolved complete snapshot, list of newly registered 'component:version')."""
    svc.ensure_standard_components(session)
    seeded: list[str] = []
    for spec in BASELINE_REVISIONS:
        component = svc.register_platform_component(session, spec["component"])
        existing = session.scalar(
            select(PlatformRevision).where(
                PlatformRevision.component_id == component.id,
                PlatformRevision.version == spec["version"],
            )
        )
        if existing is not None:
            continue
        reason = spec.get("reason") or ""
        if spec.get("boundary_source"):
            reason = f"{reason} | boundary: {spec['boundary_source']}".strip(" |")
        svc.register_platform_revision(
            session,
            component,
            version=spec["version"],
            description=spec.get("description"),
            reason=reason or None,
            normalization_available=spec.get("normalization_available"),
            backward_compatibility=spec.get("backward_compatibility"),
            pr_ref=spec.get("pr_ref"),
            activate=True,
            activated_at=spec.get("activated_at"),
            boundary_unknown=spec.get("boundary_unknown", False),
        )
        seeded.append(f"{spec['component']}:{spec['version']}")
    snapshot = svc.resolve_active_platform_snapshot(
        session,
        label="baseline-2026-08",
        description=f"deployed semantics at legacy migration (manifest {MANIFEST_VERSION})",
    )
    return snapshot, seeded


# ---------------------------------------------------------------------------
# Legacy experiments
# ---------------------------------------------------------------------------


def _twin_started_at(session, twin_tag: str) -> datetime | None:
    """The MEASURED start instant of a live/paper twin epoch, if the DB has it."""
    return session.scalar(
        select(LivePaperTwin.started_at).where(LivePaperTwin.twin_tag == twin_tag)
    )


def _coverage_docs(entry: dict) -> dict:
    """The experiment's docs_json: manifest docs + declared tag coverage, so the
    migration report (and the ops status script) can compute coverage from the DB
    alone."""
    docs = dict(entry.get("docs") or {})
    docs["covered_tags"] = list(entry.get("covers_tags") or [])
    docs["covered_tag_prefixes"] = list(entry.get("covers_tag_prefixes") or [])
    return docs


def _import_one(session, entry: dict, snapshot: PlatformSnapshot, now: datetime) -> Experiment:
    exp = svc.import_legacy_experiment(
        session,
        key=entry["key"],
        state=entry["state"],
        legacy_class=entry["legacy_class"],
        migration_integrity=entry["integrity"],
        origin=entry.get("origin"),
        title=entry.get("title"),
        family=entry.get("family"),
        hypothesis=entry.get("hypothesis"),
        docs=_coverage_docs(entry),
        notes=entry.get("notes"),
        verdict=entry.get("verdict"),
        imported_at=now,
        retired_at=entry.get("retired_at"),
        paused_from=entry.get("paused_from"),
    )
    for ev in entry.get("evidence") or []:
        svc.attach_legacy_evidence(
            session,
            exp,
            label=ev["label"],
            evidence_class=ev["evidence_class"],
            source=ev.get("source"),
            summary=ev.get("summary"),
            window_start=ev.get("window_start"),
            window_end=ev.get("window_end"),
            notes=ev.get("notes"),
        )

    active = entry.get("active")
    if not active:
        return exp

    # --- §22.2/22.3 reconstruction for books that still operate ---
    vspec = active.get("version") or {}
    ver = svc.create_experiment_version(
        session,
        exp,
        hypothesis=entry.get("hypothesis"),
        universe_selector=vspec.get("universe_selector"),
        entry_rule=vspec.get("entry_rule"),
        exit_rule=vspec.get("exit_rule"),
        sizing_rule=vspec.get("sizing_rule"),
        execution_style=vspec.get("execution_style"),
        independent_variable=vspec.get("independent_variable"),
        held_constant=vspec.get("held_constant"),
        control_required=vspec.get("control_required", True),
        control_exemption_reason=vspec.get("control_exemption_reason"),
        sample=vspec.get("sample"),
        docs=vspec.get("docs"),
        now=now,
    )
    for arm in active.get("arms") or []:
        svc.add_arm(
            session,
            ver,
            arm_key=arm["arm_key"],
            role=arm["role"],
            description=arm.get("description"),
            params=arm.get("params"),
            strategy_tag=arm.get("tag"),
        )
    for gspec in active.get("gates") or []:
        gate = svc.register_gate(
            session,
            ver,
            gate_key=gspec["gate_key"],
            kind=gspec["kind"],
            spec=gspec["spec"],
            from_state=gspec.get("from_state"),
            to_state=gspec.get("to_state"),
            registered_at=gspec.get("registered_at"),
            notes=gspec.get("notes"),
        )
        if gspec.get("evidence_started_at"):
            svc.mark_gate_evidence_started(session, gate, at=gspec["evidence_started_at"])
    svc.freeze_version(session, ver, now=now)
    epoch = svc.open_epoch(
        session,
        ver,
        reason=(
            "migration epoch — clean evidence floor at legacy import "
            f"(manifest {MANIFEST_VERSION}); pre-migration windows ride along as "
            "legacy evidence"
        ),
        snapshot=snapshot,
        started_at=now,
    )
    created: dict[str, object] = {}
    for dspec in active.get("deployments") or []:
        twin_of = None
        if dspec.get("twin_of_key"):
            twin_of = created.get(dspec["twin_of_key"])
            if twin_of is None:
                raise svc.ExperimentOsError(
                    f"{entry['key']}: twin_of_key {dspec['twin_of_key']!r} must be "
                    "declared before the twin in the manifest"
                )
        started_at = dspec.get("started_at")
        if dspec.get("twin_started_at_from"):
            measured = _twin_started_at(session, dspec["twin_started_at_from"])
            if measured is not None:
                started_at = measured
        created[dspec["key"]] = svc.register_deployment(
            session,
            epoch,
            deployment_key=dspec["key"],
            stage=dspec["stage"],
            kind=dspec["kind"],
            arms=dspec["tags"],
            twin_of=twin_of,
            config=dspec.get("config"),
            started_at=started_at or now,
            notes=dspec.get("notes"),
            # Every migration-created deployment is grandfathered: its runtime
            # continues under enforcement, its evolution goes through Experiment OS.
            grandfathered=True,
        )
        if dspec.get("ended_at"):
            svc.end_deployment(session, created[dspec["key"]], ended_at=dspec["ended_at"])
    return exp


def import_legacy(session, *, now: datetime | None = None) -> dict:
    """Run the whole migration (baseline + experiments), idempotently.

    Returns the migration report; also persists it to system_events when the run
    did any work. Commit belongs to the caller."""
    at = now or _now()
    snapshot, seeded = seed_baseline(session)
    imported: list[str] = []
    skipped: list[str] = []
    for entry in LEGACY_EXPERIMENTS:
        if session.scalar(select(Experiment).where(Experiment.key == entry["key"])):
            skipped.append(entry["key"])
            continue
        _import_one(session, entry, snapshot, at)
        imported.append(entry["key"])
    # Second pass: predecessor lineage, once every key exists (the manifest is
    # organized by category, not dependency order).
    by_key = {
        e.key: e for e in session.scalars(select(Experiment)) if e.key is not None
    }
    for entry in LEGACY_EXPERIMENTS:
        pred_key = entry.get("predecessor_key")
        if not pred_key:
            continue
        exp = by_key.get(entry["key"])
        pred = by_key.get(pred_key)
        if pred is None:
            raise svc.ExperimentOsError(
                f"{entry['key']}: predecessor {pred_key!r} is not in the manifest"
            )
        if exp is not None and exp.predecessor_experiment_id is None:
            exp.predecessor_experiment_id = pred.id
    session.flush()
    report = migration_report(session)
    report["imported_this_run"] = imported
    report["skipped_existing"] = skipped
    report["seeded_revisions"] = seeded
    report["baseline_snapshot_fingerprint"] = snapshot.fingerprint
    if imported or seeded:
        session.add(
            SystemEvent(
                created_at=at,
                level="info",
                component="experiment_os",
                message=(
                    f"legacy migration: imported {len(imported)} experiments, "
                    f"seeded {len(seeded)} platform revisions "
                    f"(manifest {MANIFEST_VERSION})"
                ),
                raw_json=report,
            )
        )
        session.flush()
    return report


# ---------------------------------------------------------------------------
# Migration report
# ---------------------------------------------------------------------------


def _db_strategy_tags(session) -> set[str]:
    tags = set(
        session.scalars(
            select(PaperTrade.strategy).where(PaperTrade.strategy.is_not(None)).distinct()
        )
    )
    tags |= set(
        session.scalars(
            select(LiveOrder.strategy).where(LiveOrder.strategy.is_not(None)).distinct()
        )
    )
    return tags


def _covered_tags(session) -> tuple[set[str], list[str]]:
    """(exact covered tags, coverage prefixes) declared by imported experiments +
    every concrete deployment-arm tag."""
    exact = set(
        session.scalars(
            select(ExperimentDeploymentArm.strategy_tag).where(
                ExperimentDeploymentArm.strategy_tag.is_not(None)
            )
        )
    )
    prefixes: list[str] = []
    for docs in session.scalars(select(Experiment.docs_json)):
        if not docs:
            continue
        exact.update(docs.get("covered_tags") or [])
        prefixes.extend(docs.get("covered_tag_prefixes") or [])
    return exact, prefixes


def migration_report(session) -> dict:
    """Coverage of DB reality by the imported classification — pure read.

    `unmapped_tags` is the loud list: strategy tags that have traded but resolve to
    no experiment. Nothing is auto-stubbed for them (that would be silent
    classification); they stay unmapped until the manifest classifies them or they
    are explicitly recorded HISTORICAL_UNTRACKED."""
    experiments = session.scalars(select(Experiment).order_by(Experiment.key)).all()
    by_class: dict[str, int] = {}
    by_integrity: dict[str, int] = {}
    for e in experiments:
        by_class[e.legacy_class or "native"] = by_class.get(e.legacy_class or "native", 0) + 1
        if e.migration_integrity:
            by_integrity[e.migration_integrity] = by_integrity.get(e.migration_integrity, 0) + 1
    exact, prefixes = _covered_tags(session)
    db_tags = _db_strategy_tags(session)
    unmapped = sorted(
        t for t in db_tags
        if t not in exact and not any(t.startswith(p) for p in prefixes)
    )
    return {
        "manifest_version": MANIFEST_VERSION,
        "experiments": len(experiments),
        "by_legacy_class": by_class,
        "by_integrity": by_integrity,
        "db_strategy_tags": len(db_tags),
        "unmapped_tags": unmapped,
        "notes": [
            "evo strategies are out of scope by design (spec §22.7): evo trades "
            "live in evo_* tables under evo lineage",
            "unmapped tags stay unmapped until classified — no auto-stubbing",
        ],
    }


# ---------------------------------------------------------------------------
# Flag-gated worker boot hook
# ---------------------------------------------------------------------------


def run_boot_import(session) -> dict | None:
    """The EXPERIMENT_OS_IMPORT_ON_BOOT hook: import, log, never raise.

    A broken import must not stop trading — failures are recorded to
    system_events and the worker carries on exactly as before."""
    try:
        report = import_legacy(session)
        if report.get("imported_this_run") or report.get("seeded_revisions"):
            logger.info(
                "experiment OS legacy import ran",
                extra={"extra_fields": {
                    "imported": report.get("imported_this_run"),
                    "seeded": report.get("seeded_revisions"),
                    "unmapped_tags": report.get("unmapped_tags"),
                }},
            )
        return report
    except Exception as exc:  # noqa: BLE001 — deliberately broad: boot must survive
        logger.error(
            "experiment OS legacy import FAILED (trading unaffected)",
            extra={"extra_fields": {"error": str(exc)}},
        )
        try:
            session.rollback()
            session.add(
                SystemEvent(
                    created_at=_now(),
                    level="error",
                    component="experiment_os",
                    message=f"legacy import failed: {exc}",
                    raw_json={"manifest_version": MANIFEST_VERSION},
                )
            )
            session.flush()
        except Exception:  # noqa: BLE001
            logger.exception("could not record the import failure either")
        return None
