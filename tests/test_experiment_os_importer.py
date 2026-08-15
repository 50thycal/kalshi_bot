"""The legacy importer (PR 2) — the migration must reconstruct, never invent.

What these tests pin:

  * idempotency — a re-run imports nothing and re-seeds nothing;
  * the baseline snapshot records MEASURED activation boundaries exactly
    (fee 2026-08-11T15:00Z, taxonomy 2026-08-13T18:09:40Z) and leaves unmeasured
    ones explicitly NULL — never the import timestamp;
  * FILL_MODEL describes deployed semantics and records the depth-proxy REJECTION;
  * retired books import as minimal stubs (no versions/epochs/gates), with real
    historical dates only;
  * active books get the §22.2/22.3 reconstruction: frozen version, arms, gates
    with their historically recorded evidence floors, a migration epoch pinned to
    the baseline snapshot, grandfathered deployments, first-class twin links with
    start instants resolved from live_paper_twins (measured) when present;
  * the migration report finds every unmapped tag and auto-stubs nothing;
  * the boot hook can never take the worker down.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from kalshi_bot.experiment_os import importer, read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.legacy_manifest import (
    A5_PAIRING_BOUNDARY,
    FEE_BOUNDARY,
    LEGACY_EXPERIMENTS,
    TAXONOMY_BOUNDARY,
)
from kalshi_bot.experiment_os.models import (
    Experiment,
    ExperimentGate,
    PlatformComponent,
    PlatformRevision,
)
from kalshi_bot.models import LivePaperTwin, PaperTrade, SystemEvent

UTC = timezone.utc
ARMING_INSTANT = datetime(2026, 8, 15, 13, 42, 17, tzinfo=UTC)
IMPORT_INSTANT = datetime(2026, 8, 15, 16, 0, 0, tzinfo=UTC)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


@pytest.fixture
def imported(xos_session):
    """Prod-shaped seed (twin epochs + a spread of traded tags, one unmapped) and
    one import run."""
    s = xos_session
    for twin_tag, live_tag in [
        ("Lmmsell8_pt3", "Lmmsell8"),
        ("Lmmsell10_pt3", "Lmmsell10"),
        ("theta4_pt3", "theta4"),
    ]:
        s.add(LivePaperTwin(twin_tag=twin_tag, live_tag=live_tag, started_at=ARMING_INSTANT))
    for tag in [
        "freeze1", "mmsell10", "pin15", "weather_dir7", "Lmmsell8_pt3",
        "theta4_pt2", "mystery_book",
    ]:
        s.add(PaperTrade(market_ticker="T-X", strategy=tag))
    s.commit()
    report = importer.import_legacy(s, now=IMPORT_INSTANT)
    s.commit()
    return report


def test_import_counts_and_classification(xos_session, imported):
    s = xos_session
    assert len(imported["imported_this_run"]) == len(LEGACY_EXPERIMENTS)
    assert s.scalar(select(func.count()).select_from(Experiment)) == len(LEGACY_EXPERIMENTS)
    assert imported["by_legacy_class"] == {
        "ACTIVE_LIVE": 2, "ACTIVE_PAPER": 8, "RETIRED_OR_KILLED": 15,
    }
    # Integrity is visible and never silently VERIFIED (no A grades were earned).
    assert imported["by_integrity"] == {"B": 9, "C": 15, "D": 1}
    assert "A" not in imported["by_integrity"]
    # One import transition per experiment, actor=import, from nothing.
    for exp in read.list_experiments(s):
        rows = read.transitions_for(s, exp)
        assert len(rows) == 1 and rows[0].actor == "import" and rows[0].from_state is None


def test_import_is_idempotent(xos_session, imported):
    report2 = importer.import_legacy(xos_session, now=IMPORT_INSTANT)
    assert report2["imported_this_run"] == []
    assert report2["seeded_revisions"] == []
    assert report2["skipped_existing"] == sorted(
        e["key"] for e in LEGACY_EXPERIMENTS
    ) or set(report2["skipped_existing"]) == {e["key"] for e in LEGACY_EXPERIMENTS}
    assert xos_session.scalar(select(func.count()).select_from(Experiment)) == len(
        LEGACY_EXPERIMENTS
    )


def test_baseline_boundaries_measured_or_explicitly_unknown(xos_session, imported):
    s = xos_session

    def rev(key: str) -> PlatformRevision:
        return s.execute(
            select(PlatformRevision)
            .join(PlatformComponent, PlatformComponent.id == PlatformRevision.component_id)
            .where(PlatformComponent.key == key, PlatformRevision.status == "active")
        ).scalar_one()

    # Measured boundaries stored exactly.
    assert _naive(rev("FEE_MODEL").activated_at) == _naive(FEE_BOUNDARY)
    assert _naive(rev("MARKET_TAXONOMY").activated_at) == _naive(TAXONOMY_BOUNDARY)
    # Unmeasured boundaries are NULL — not the import instant.
    for key in (
        "FILL_MODEL", "EXECUTION_ENGINE", "SETTLEMENT_ENGINE", "RISK_ENGINE",
        "DATA_PROVENANCE", "KALSHI_API_SCHEMA", "METRICS_ENGINE", "EXPERIMENT_ENGINE",
    ):
        assert rev(key).activated_at is None, key
    # FILL_MODEL is the deployed composite and records the depth-proxy rejection.
    fill = rev("FILL_MODEL")
    assert "REJECTED" in fill.description and "PR #218" in fill.description
    assert "mmsell3" in fill.description  # the live calibration window
    # The taxonomy revision knows history is NOT normalizable across it; fee is.
    assert rev("MARKET_TAXONOMY").normalization_available is False
    assert rev("FEE_MODEL").normalization_available is True
    # The snapshot is complete over every registered component.
    snap = svc.get_active_platform_snapshot(s)
    assert snap is not None
    assert svc.snapshot_missing_components(s, snap) == []
    assert imported["baseline_snapshot_fingerprint"] == snap.fingerprint


def test_retired_stubs_invent_nothing(xos_session, imported):
    s = xos_session
    retired = read.list_experiments(s, state="RETIRED")
    assert len(retired) == 15
    for exp in retired:
        assert read.versions_for(s, exp) == [], exp.key  # no reconstruction for dead books
        assert exp.platform_snapshot_id is None, exp.key
    pin15 = read.get_experiment(s, "pin15")
    assert _naive(pin15.retired_at) == datetime(2026, 7, 16)
    # The lump row imports at integrity D with no invented hypothesis or date.
    lump = read.get_experiment(s, "weather-legacy-cells")
    assert lump.migration_integrity == "D"
    assert lump.hypothesis is None
    assert lump.retired_at is None  # the registry records no retirement date for the lump
    # wcprop's registry row records no kill date either — stays NULL.
    assert read.get_experiment(s, "wcprop").retired_at is None
    # Offset A/B evidence preserves the recorded numbers verbatim.
    ab = read.get_experiment(s, "mmsell-maker-offset-ab")
    ev = read.legacy_evidence_for(s, ab)[0]
    assert ev.evidence_class == "context_only"
    assert ev.summary_json["delta_gap_cents"] == 3.51
    assert ev.summary_json["n"] == {"mmsell10a": 420, "mmsell10b": 384}


def test_active_reconstruction_uses_recorded_floors(xos_session, imported):
    s = xos_session

    def gate(exp_key: str, gate_key: str) -> ExperimentGate:
        exp = read.get_experiment(s, exp_key)
        ver = read.latest_version(s, exp)
        return next(g for g in read.gates_for(s, ver) if g.gate_key == gate_key)

    # Historically recorded evidence floors, stored exactly.
    assert _naive(gate("mmsell-type-tight", "paper_keep").evidence_started_at) == _naive(
        TAXONOMY_BOUNDARY
    )
    assert _naive(
        gate("mmsell-anchor-strangle", "paper_keep").evidence_started_at
    ) == _naive(A5_PAIRING_BOUNDARY)
    assert _naive(gate("mmsell-anchor-vol-entry", "paper_keep").evidence_started_at) == _naive(
        FEE_BOUNDARY
    )
    # Every active book: frozen version, migration epoch at the import instant,
    # pinned to the baseline snapshot.
    snap = svc.get_active_platform_snapshot(s)
    for exp in read.list_experiments(s):
        vers = read.versions_for(s, exp)
        if not vers:
            continue
        ver = vers[0]
        assert ver.frozen_at is not None, exp.key
        epoch = read.epochs_for(s, ver)[0]
        assert epoch.platform_snapshot_id == snap.id, exp.key
        assert _naive(epoch.started_at) == _naive(IMPORT_INSTANT), exp.key
    # The freeze reconstruction matches the PR-1 proving shape.
    freeze = read.get_experiment(s, "freeze-dark-window-pin")
    arms = {a.arm_key: a.role for a in read.arms_for(s, read.latest_version(s, freeze))}
    assert arms == {
        "freeze1": "treatment", "freeze2": "secondary",
        "freeze3": "control", "freeze4": "secondary",
    }


def test_live_canaries_have_first_class_twins(xos_session, imported):
    s = xos_session
    for exp_key, live_key, twin_key in [
        ("theta4-fat-tail", "theta4-live-1", "theta4-twin-pt3"),
        ("mmsell-scheduled-settle-live", "lmmsell-live-1", "lmmsell-twin-pt3"),
    ]:
        exp = read.get_experiment(s, exp_key)
        assert exp.state == "LIVE_CANARY"
        epoch = read.epochs_for(s, read.latest_version(s, exp))[0]
        deps = {d.deployment_key: d for d in read.deployments_for(s, epoch)}
        live, twin = deps[live_key], deps[twin_key]
        assert twin.kind == "paper_twin" and twin.twin_of_deployment_id == live.id
        assert read.twin_for(s, live).id == twin.id
    # The Lmmsell arming instant comes MEASURED from live_paper_twins, not the
    # manifest's day-precision date.
    lm = read.get_experiment(s, "mmsell-scheduled-settle-live")
    epoch = read.epochs_for(s, read.latest_version(s, lm))[0]
    deps = {d.deployment_key: d for d in read.deployments_for(s, epoch)}
    assert _naive(deps["lmmsell-live-1"].started_at) == _naive(ARMING_INSTANT)
    assert _naive(deps["lmmsell-twin-pt3"].started_at) == _naive(ARMING_INSTANT)
    # Lineage: the live tag and its twin tag resolve to the same arm identity.
    assert read.strategy_tag_lineage(s, "Lmmsell8")[0]["arm_key"] == "scheduled_settle"
    assert read.strategy_tag_lineage(s, "Lmmsell8_pt3")[0]["arm_key"] == "scheduled_settle"
    # Predecessor lineage: theta4 succeeds the shelved family; the family is PAUSED
    # from PAPER (resumable), not retired.
    theta_family = read.get_experiment(s, "theta-tail-sell")
    theta4 = read.get_experiment(s, "theta4-fat-tail")
    assert theta4.predecessor_experiment_id == theta_family.id
    assert theta_family.state == "PAUSED"
    assert theta_family.paused_from_state == "PAPER"


def test_twin_instant_falls_back_to_manifest_date(xos_session):
    """Without a live_paper_twins row (e.g. a fresh test DB), the deployment keeps
    the manifest's day-precision date rather than failing or fabricating now()."""
    s = xos_session
    importer.import_legacy(s, now=IMPORT_INSTANT)
    s.commit()
    lm = read.get_experiment(s, "mmsell-scheduled-settle-live")
    epoch = read.epochs_for(s, read.latest_version(s, lm))[0]
    deps = {d.deployment_key: d for d in read.deployments_for(s, epoch)}
    assert _naive(deps["lmmsell-live-1"].started_at) == datetime(2026, 8, 15)


def test_migration_report_finds_unmapped_tags(xos_session, imported):
    # mystery_book traded but no experiment covers it → loud, not absorbed.
    assert imported["unmapped_tags"] == ["mystery_book"]
    # Prefix coverage works: weather_dir7 (weather_ lump), theta4_pt2 (twin epochs),
    # Lmmsell8_pt3 (live twin) are all covered.
    assert "weather_dir7" not in imported["unmapped_tags"]
    assert "theta4_pt2" not in imported["unmapped_tags"]
    assert imported["db_strategy_tags"] == 7
    # The report is persisted for the ops channel.
    row = xos_session.scalar(
        select(SystemEvent).where(SystemEvent.component == "experiment_os")
    )
    assert row is not None and row.raw_json["unmapped_tags"] == ["mystery_book"]
    # A re-run does not spam another report row.
    importer.import_legacy(xos_session, now=IMPORT_INSTANT)
    n_rows = xos_session.scalar(
        select(func.count()).select_from(SystemEvent).where(
            SystemEvent.component == "experiment_os"
        )
    )
    assert n_rows == 1


def test_gate_floors_are_immutable_after_import(xos_session, imported):
    """Imported gates carry historical evidence floors — so they are already
    locked: the registry's pre-registrations cannot be quietly reshaped."""
    s = xos_session
    freeze = read.get_experiment(s, "freeze-dark-window-pin")
    gate = read.gates_for(s, read.latest_version(s, freeze))[0]
    gate.spec_json = {**gate.spec_json, "pass_all": []}
    with pytest.raises(svc.ImmutableRecord):
        s.flush()
    s.rollback()


def test_boot_hook_never_raises(xos_session, monkeypatch):
    calls = {}

    def boom(session, now=None):
        raise RuntimeError("manifest exploded")

    monkeypatch.setattr(importer, "import_legacy", boom)
    result = importer.run_boot_import(xos_session)
    assert result is None
    # The failure is recorded, and the session is still usable.
    xos_session.commit()
    row = xos_session.scalar(
        select(SystemEvent).where(SystemEvent.component == "experiment_os")
    )
    assert row is not None and "manifest exploded" in row.message
    calls  # noqa: B018 — silence unused warning


def test_import_flag_defaults_off(base_env):
    from kalshi_bot.config import Settings

    assert Settings(_env_file=None).experiment_os_import_on_boot is False


def test_manifest_is_internally_consistent():
    """Pure manifest lint: unique keys, valid enums, tags declared exactly once,
    predecessors resolvable — a manifest edit that breaks these fails here, not in
    production at boot."""
    from kalshi_bot.experiment_os.lifecycle import (
        LegacyClass,
        LifecycleState,
        MigrationIntegrity,
    )

    keys = [e["key"] for e in LEGACY_EXPERIMENTS]
    assert len(keys) == len(set(keys)), "duplicate experiment keys"
    tag_owner: dict[str, str] = {}
    for e in LEGACY_EXPERIMENTS:
        LifecycleState(e["state"])
        LegacyClass(e["legacy_class"])
        MigrationIntegrity(e["integrity"])
        if e.get("predecessor_key"):
            assert e["predecessor_key"] in keys, e["key"]
        for tag in e.get("covers_tags") or []:
            assert tag not in tag_owner, f"tag {tag} covered by both {tag_owner[tag]} and {e['key']}"
            tag_owner[tag] = e["key"]
        if e.get("state") == "RETIRED":
            assert not e.get("active"), f"{e['key']}: retired books get no reconstruction"
        if e.get("active"):
            arm_keys = [a["arm_key"] for a in e["active"]["arms"]]
            assert len(arm_keys) == len(set(arm_keys)), e["key"]
            declared = {a["arm_key"] for a in e["active"]["arms"]}
            for d in e["active"].get("deployments") or []:
                assert set(d["tags"]) <= declared, f"{e['key']}: deployment uses undeclared arm"
