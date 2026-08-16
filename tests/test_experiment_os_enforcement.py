"""NEW_ONLY enforcement + runtime lineage (PR 4) — adversarial, not CRUD.

False-success scenarios pinned here:

  * a new tag begins trading after the cutover without registration → blocked;
  * a grandfathered legacy tag continues, stamped;
  * an ambiguous tag (two active deployments) → blocked;
  * a metadata outage degrades NEW_ONLY to WARN loudly instead of killing
    grandfathered books; STRICT does not degrade;
  * config drift raises an integrity event that blocks gates (and tags, under
    STRICT);
  * the cutover is an explicit recorded event — NEW_ONLY refuses an un-ok
    readiness report unless forced;
  * a manual PASS, a stale evaluator PASS, or a PASS superseded by HOLD cannot
    promote once enforcement is on (synchronous re-evaluation is the authority);
  * a live canary cannot arm on a tag with inherited paper history, without a
    twin, with mismatched boundaries, or without a pre-registered risk envelope;
  * post-cutover rows without lineage are mechanically detectable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from kalshi_bot import repository as repo
from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import evaluator, importer, read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import EnforcementMode
from kalshi_bot.experiment_os.models import (
    ExperimentGateResult,
    ExperimentIntegrityEvent,
    ExperimentOsEnforcement,
)
from kalshi_bot.models import LivePaperTwin, PaperTrade, SystemEvent

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


def _dt(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _paper_entry(s, tag):
    return repo.create_paper_trade(
        s, signal_id=None, ticker=f"T-{tag}", strategy=tag, side="no", action="buy",
        assumed_price=93, quantity=1, fill_assumption="test", entry_fee=0.0,
    )


def _experiment_with_deployment(s, key="native-1", tag="native_tag", grandfathered=False):
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(s, exp, hypothesis="h", now=T0)
    svc.add_arm(s, ver, arm_key="a", role="treatment", strategy_tag=tag)
    svc.add_arm(s, ver, arm_key="c", role="control", strategy_tag=tag + "_c")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    dep = svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper-1", stage="PAPER", kind="paper",
        arms={"a": tag, "c": tag + "_c"}, started_at=T0, grandfathered=grandfathered,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    return exp, ver, epoch, dep


def _set_mode(s, mode, *, readiness=None, force=False, cutover_id=None):
    return enf.record_enforcement_change(
        s, mode=mode, actor="operator", reason="test",
        cutover_id=cutover_id or f"test-{mode}-{s.scalar(select(ExperimentOsEnforcement.id).order_by(ExperimentOsEnforcement.id.desc()).limit(1)) or 0}",
        readiness=readiness, force=force,
    )


def _ok_readiness():
    return {"ok": True, "checks": {}}


# ---------------------------------------------------------------------------
# Recorded enforcement state
# ---------------------------------------------------------------------------


def test_mode_defaults_off_and_cutover_is_explicit(xos_session):
    s = xos_session
    assert enf.current_mode(s) is EnforcementMode.OFF
    assert enf.current_enforcement(s) is None  # never inferred from anything
    row = _set_mode(s, "WARN", cutover_id="cutover-warn-1")
    assert enf.current_mode(s) is EnforcementMode.WARN
    assert row.preceding_mode is None
    assert row.system_version == enf.ENFORCEMENT_SYSTEM_VERSION
    # One clear answer to "when did enforcement begin": the recorded effective_at.
    assert enf.current_enforcement(s).effective_at is not None
    # Append-only: history cannot be rewritten.
    row.mode = "STRICT"
    with pytest.raises(svc.ImmutableRecord):
        s.flush()
    s.rollback()


def test_new_only_requires_ok_readiness_or_explicit_force(xos_session):
    s = xos_session
    with pytest.raises(ValueError, match="requires the production-readiness report"):
        _set_mode(s, "NEW_ONLY")
    bad = {"ok": False, "checks": {"coverage_complete": {"ok": False, "detail": "x"}}}
    with pytest.raises(ValueError, match="readiness is NOT ok"):
        _set_mode(s, "NEW_ONLY", readiness=bad)
    forced = _set_mode(s, "NEW_ONLY", readiness=bad, force=True, cutover_id="forced-1")
    assert forced.mode == "NEW_ONLY"  # audited override, not a silent bypass
    assert forced.readiness_json["ok"] is False


def _cutover_settings(**kw):
    from kalshi_bot.config import Settings

    base = dict(
        _env_file=None,
        experiment_os_enforcement_mode="NEW_ONLY",
        experiment_os_cutover_id="cutover-test-1",
        experiment_os_cutover_actor="operator",
        experiment_os_cutover_reason="production cutover",
        live_strategies="",
    )
    base.update(kw)
    return Settings(**base)


def test_boot_cutover_hook_refuses_red_readiness_and_cannot_force(xos_session, base_env):
    """The worker-side cutover hook is the only writable path to the enforcement
    record (ops is read-only), so its gate must be unbypassable from config."""
    s = xos_session
    # Empty DB ⇒ readiness is red (import never ran).
    out = enf.run_boot_cutover(s, _cutover_settings())
    assert out["ok"] is False and out["reason"] == "readiness not ok"
    assert "import_ran" in out["failing"]
    assert enf.current_mode(s) is EnforcementMode.OFF        # nothing changed
    assert enf.current_enforcement(s) is None                # nothing recorded
    refusal = s.scalar(select(SystemEvent).where(
        SystemEvent.message.like("%cutover to NEW_ONLY REFUSED%")))
    assert refusal is not None                               # loud, durable
    # There is no env-driven force: no combination of settings records it.
    assert not hasattr(_cutover_settings(), "experiment_os_cutover_force")


def test_boot_cutover_hook_requires_attribution(xos_session, base_env):
    s = xos_session
    out = enf.run_boot_cutover(s, _cutover_settings(experiment_os_cutover_id=""))
    assert out["ok"] is False and out["missing"] == ["EXPERIMENT_OS_CUTOVER_ID"]
    assert enf.current_mode(s) is EnforcementMode.OFF
    bad = enf.run_boot_cutover(s, _cutover_settings(
        experiment_os_enforcement_mode="TURBO"))
    assert bad["ok"] is False and bad["reason"] == "invalid mode"


def test_boot_cutover_hook_records_once_then_no_ops(xos_session, xos_platform, base_env):
    s = xos_session
    for twin_tag, live_tag in [("Lmmsell8_pt3", "Lmmsell8"),
                               ("Lmmsell10_pt3", "Lmmsell10"),
                               ("theta4_pt3", "theta4")]:
        s.add(LivePaperTwin(twin_tag=twin_tag, live_tag=live_tag, started_at=T0))
    s.commit()
    importer.import_legacy(s, now=_dt(2026, 8, 15, 16, 0))
    s.commit()
    assert enf.production_readiness(s)["ok"] is True          # green baseline

    out = enf.run_boot_cutover(s, _cutover_settings())
    assert out["ok"] is True and out["mode"] == "NEW_ONLY"
    record = enf.current_enforcement(s)
    assert record.cutover_id == "cutover-test-1"
    assert record.actor == "operator"
    assert record.preceding_mode is None      # OFF was never itself recorded
    assert record.readiness_json["ok"] is True                # evidence attached
    # The reported boundary IS the recorded one (sqlite drops the tz suffix).
    assert out["effective_at"][:19] == str(record.effective_at)[:19]
    # Idempotent: the same settings on every later boot record nothing new.
    assert enf.run_boot_cutover(s, _cutover_settings()) is None
    assert s.scalar(select(func.count()).select_from(ExperimentOsEnforcement)) == 1


# ---------------------------------------------------------------------------
# Admission: block new, continue grandfathered
# ---------------------------------------------------------------------------


def test_unregistered_tag_blocked_only_after_cutover(xos_session, xos_platform):
    s = xos_session
    _experiment_with_deployment(s, grandfathered=True)
    # OFF: anything writes (unstamped when unknown).
    enf.refresh(s)
    t = _paper_entry(s, "rogue_book")
    assert t.experiment_deployment_arm_id is None
    # WARN: still writes, but the tag is recorded.
    _set_mode(s, "WARN")
    enf.refresh(s)
    _paper_entry(s, "rogue_book2")
    warn_row = s.scalar(
        select(SystemEvent).where(SystemEvent.component == "experiment_os_enforcement",
                                  SystemEvent.level == "warning")
    )
    assert warn_row is not None and "rogue_book2" in warn_row.message
    # NEW_ONLY: fail closed.
    _set_mode(s, "NEW_ONLY", readiness=_ok_readiness())
    enf.refresh(s)
    with pytest.raises(enf.LineageBlocked, match="not registered"):
        _paper_entry(s, "rogue_book3")
    # ...and no row was written for the blocked entry.
    assert s.scalar(
        select(PaperTrade).where(PaperTrade.strategy == "rogue_book3")
    ) is None


def test_grandfathered_and_native_tags_continue_stamped(xos_session, xos_platform):
    s = xos_session
    _, _, _, dep = _experiment_with_deployment(
        s, key="legacy-book", tag="legacy_tag", grandfathered=True
    )
    _set_mode(s, "NEW_ONLY", readiness=_ok_readiness())
    enf.refresh(s)
    t = _paper_entry(s, "legacy_tag")
    assert t.experiment_deployment_arm_id is not None  # stamped lineage
    lineage = read.strategy_tag_lineage(s, "legacy_tag")
    assert lineage[0]["deployment_key"] == dep.deployment_key


def test_ambiguous_tag_blocked(xos_session, xos_platform):
    s = xos_session
    _experiment_with_deployment(s, key="amb-1", tag="shared_tag")
    _experiment_with_deployment(s, key="amb-2", tag="shared_tag")
    _set_mode(s, "NEW_ONLY", readiness=_ok_readiness())
    enf.refresh(s)
    with pytest.raises(enf.LineageBlocked, match="ambiguous"):
        _paper_entry(s, "shared_tag")


def _degrade_resolver(s, monkeypatch):
    """Fail the NEXT refresh so the resolver keeps its last snapshot, degraded."""
    real_execute = type(s).execute

    def boom(self, *a, **k):
        raise RuntimeError("metadata outage")

    monkeypatch.setattr(type(s), "execute", boom)
    enf.refresh(s)
    monkeypatch.setattr(type(s), "execute", real_execute)
    assert enf._STATE.degraded is True


def test_degraded_resolver_preserves_known_lineage_continuity(
    xos_session, xos_platform, monkeypatch
):
    """The invariant, half one: degradation preserves continuity for PREVIOUSLY
    KNOWN lineage — grandfathered and native books continue, stamped from the
    cached snapshot, through a metadata outage under NEW_ONLY."""
    s = xos_session
    _, _, _, gf_dep = _experiment_with_deployment(
        s, key="gf", tag="gf_tag", grandfathered=True
    )
    _, _, _, nat_dep = _experiment_with_deployment(
        s, key="nat", tag="native_tag", grandfathered=False
    )
    _set_mode(s, "NEW_ONLY", readiness=_ok_readiness())
    enf.refresh(s)
    _degrade_resolver(s, monkeypatch)
    gf_row = _paper_entry(s, "gf_tag")
    nat_row = _paper_entry(s, "native_tag")
    assert gf_row.experiment_deployment_arm_id is not None      # stamped from cache
    assert nat_row.experiment_deployment_arm_id is not None
    # Entering the degraded state was alarmed durably, exactly once.
    alarms = s.scalars(
        select(SystemEvent).where(SystemEvent.message.like("enforcement DEGRADED%"))
    ).all()
    assert len(alarms) == 1
    enf.refresh(s)  # second failed refresh would re-alarm only after a recovery
    assert len(s.scalars(
        select(SystemEvent).where(SystemEvent.message.like("enforcement DEGRADED%"))
    ).all()) == 1
    # The outage is visible to the pre-cutover checklist and the report.
    assert enf.production_readiness(s)["checks"]["resolver_health"]["ok"] is False
    assert enf.enforcement_report(s)["resolver_degraded_alarms_24h"] == 1


def test_degraded_resolver_never_creates_permission_for_unknown_lineage(
    xos_session, xos_platform, monkeypatch
):
    """The invariant, half two: an outage is NOT a window in which new
    experimental tags can start accumulating exposure — unknown and ambiguous
    tags stay fail-closed under NEW_ONLY while degraded."""
    s = xos_session
    _experiment_with_deployment(s, key="gf", tag="gf_tag", grandfathered=True)
    _experiment_with_deployment(s, key="amb-1", tag="shared_tag")
    _experiment_with_deployment(s, key="amb-2", tag="shared_tag")
    _set_mode(s, "NEW_ONLY", readiness=_ok_readiness())
    enf.refresh(s)
    _degrade_resolver(s, monkeypatch)
    with pytest.raises(enf.LineageBlocked, match="not registered"):
        _paper_entry(s, "sneaky_new_tag")
    assert s.scalar(
        select(PaperTrade).where(PaperTrade.strategy == "sneaky_new_tag")
    ) is None  # no row — fail closed even mid-outage
    with pytest.raises(enf.LineageBlocked, match="ambiguous"):
        _paper_entry(s, "shared_tag")
    # The block message says the decision came from a stale snapshot.
    with pytest.raises(enf.LineageBlocked, match="resolver DEGRADED"):
        _paper_entry(s, "sneaky_new_tag")


def test_strict_stays_fail_closed_while_degraded(xos_session, xos_platform, monkeypatch):
    s = xos_session
    _experiment_with_deployment(s, key="gf", tag="gf_tag", grandfathered=True)
    _set_mode(s, "STRICT", readiness=_ok_readiness())
    enf.refresh(s)
    _degrade_resolver(s, monkeypatch)
    assert _paper_entry(s, "gf_tag").experiment_deployment_arm_id is not None
    with pytest.raises(enf.LineageBlocked):
        _paper_entry(s, "sneaky_new_tag2")


def test_live_orders_fail_closed_too(xos_session, xos_platform):
    s = xos_session
    _set_mode(s, "NEW_ONLY", readiness=_ok_readiness())
    enf.refresh(s)
    with pytest.raises(enf.LineageBlocked):
        repo.create_live_order(
            s, signal_id=None, ticker="T-X", event_ticker=None,
            strategy="rogue_live", side="no", action="buy", limit_price=93,
            quantity=1, status="pending", client_order_id="c1",
        )


# ---------------------------------------------------------------------------
# Config drift
# ---------------------------------------------------------------------------


def test_matching_config_is_never_reported_as_drift(xos_session, base_env):
    """The production configuration must read as CLEAN.

    Regression: the observed side is rebuilt by iterating live config, so its
    list order is incidental; comparing it raw against the manifest's
    hand-written order reported drift on a book whose config had not changed.
    A false EXPERIMENT_CONFIG_DRIFT is expensive — it blocks gate evaluation,
    turns readiness red, and under STRICT blocks the book's tags."""
    from kalshi_bot.config import Settings

    s = xos_session
    for twin_tag, live_tag in [
        ("Lmmsell8_pt3", "Lmmsell8"), ("Lmmsell10_pt3", "Lmmsell10"),
        ("theta4_pt3", "theta4"),
    ]:
        s.add(LivePaperTwin(twin_tag=twin_tag, live_tag=live_tag, started_at=T0))
    s.commit()
    importer.import_legacy(s, now=_dt(2026, 8, 15, 16, 0))
    s.commit()
    # Exactly production's values, in production's declaration order.
    settings = Settings(
        _env_file=None,
        live_strategies="theta4,Lmmsell8,Lmmsell10",
        live_paper_twins="",
        live_paper_twin_suffix="_pt3",
        mmsell_variants=(
            "Lmmsell8:lo=5,hi=12,only=BTCD+ETH+ASG+HRDERBY;Lmmsell10:lo=5,hi=10,maxyes=7"
        ),
    )
    assert enf.runtime_config_check(s, settings) == []
    s.commit()
    assert s.scalar(
        select(func.count()).select_from(ExperimentIntegrityEvent).where(
            ExperimentIntegrityEvent.kind == "EXPERIMENT_CONFIG_DRIFT"
        )
    ) == 0
    # ...and the checklist stays green, so the cutover is not blocked by a phantom.
    assert enf.production_readiness(s, settings)["ok"] is True


def test_config_drift_detected_and_blocks_under_strict(xos_session, base_env, monkeypatch):
    from kalshi_bot.config import Settings

    s = xos_session
    for twin_tag, live_tag in [
        ("Lmmsell8_pt3", "Lmmsell8"), ("Lmmsell10_pt3", "Lmmsell10"),
        ("theta4_pt3", "theta4"),
    ]:
        s.add(LivePaperTwin(twin_tag=twin_tag, live_tag=live_tag, started_at=T0))
    s.commit()
    importer.import_legacy(s, now=_dt(2026, 8, 15, 16, 0))
    s.commit()

    settings = Settings(
        _env_file=None,
        live_strategies="theta4,Lmmsell8,Lmmsell10",
        # Lmmsell10's registered band quietly widened — material drift.
        mmsell_variants="Lmmsell8:lo=5,hi=12,only=BTCD+ETH+ASG+HRDERBY;Lmmsell10:lo=5,hi=14",
        live_paper_twins="theta4:theta4_pt3,Lmmsell8:Lmmsell8_pt3,Lmmsell10:Lmmsell10_pt3",
    )
    findings = enf.runtime_config_check(s, settings)
    s.commit()
    assert findings and findings[0]["deployment"] == "lmmsell-live-1"
    drift = s.scalar(
        select(ExperimentIntegrityEvent).where(
            ExperimentIntegrityEvent.kind == "EXPERIMENT_CONFIG_DRIFT"
        )
    )
    assert drift is not None and drift.resolved_at is None
    # The unresolved drift blocks gate evaluation (existing PR 3 machinery).
    lm = read.get_experiment(s, "mmsell-scheduled-settle-live")
    gate = read.gates_for(s, read.latest_version(s, lm))[0]
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "BLOCKED_INTEGRITY"
    assert any("EXPERIMENT_CONFIG_DRIFT" in r for r in out.blocking_reasons)
    # Under STRICT the drifted deployment's tags are blocked from new entries.
    _set_mode(s, "STRICT", readiness=_ok_readiness(), cutover_id="strict-drift")
    enf.refresh(s)
    with pytest.raises(enf.LineageBlocked, match="drifted"):
        _paper_entry(s, "Lmmsell10")
    # A re-run of the check does not duplicate the open event.
    enf.runtime_config_check(s, settings)
    n = s.scalar(
        select(ExperimentIntegrityEvent.id).where(
            ExperimentIntegrityEvent.kind == "EXPERIMENT_CONFIG_DRIFT"
        ).order_by(ExperimentIntegrityEvent.id.desc())
    )
    assert n == drift.id


# ---------------------------------------------------------------------------
# Promotion hardening under enforcement
# ---------------------------------------------------------------------------


def _promotable(s):
    exp, ver, epoch, dep = _experiment_with_deployment(s, key="promo", tag="p_tag")
    gate = svc.register_gate(
        s, ver, gate_key="paper_to_live_canary", kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY",
        spec={
            "sample": {"a": {"metric": "settled_trades", "op": ">=", "value": 3}},
            "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "a",
                          "op": ">", "value": 0}],
            "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "a",
                          "op": "<=", "value": 0}],
        },
    )
    svc.mark_gate_evidence_started(s, gate, at=T0)
    for i in range(5):
        s.add(PaperTrade(market_ticker=f"P-{i}", strategy="p_tag", status="settled",
                         pnl=0.05, quantity=1, created_at=T0 + timedelta(hours=2)))
    s.commit()
    return exp, ver, epoch, gate


def test_manual_pass_cannot_promote_under_enforcement(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate = _promotable(s)
    manual = svc.record_gate_result(s, gate, verdict="PASS", epoch=epoch,
                                    computed_by="manual")
    _set_mode(s, "WARN")
    # The manual PASS only names the gate; authority is the synchronous re-run —
    # which passes here, so promotion succeeds THROUGH the fresh system result.
    row = svc.transition_experiment(
        s, exp, "LIVE_CANARY", actor="operator", approved_by="operator",
        gate_result=manual,
    )
    authorizing = s.get(ExperimentGateResult, row.gate_result_id)
    assert authorizing.computed_by == "system"
    assert authorizing.id != manual.id


def test_stale_pass_refused_when_evidence_moved(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate = _promotable(s)
    good = evaluator.evaluate_gate(s, gate)
    s.commit()
    assert good.verdict == "PASS"
    result = s.get(ExperimentGateResult, good.result_id)
    # The edge dies AFTER the PASS was recorded.
    for i in range(50):
        s.add(PaperTrade(market_ticker=f"L-{i}", strategy="p_tag", status="settled",
                         pnl=-0.50, quantity=1, created_at=T0 + timedelta(hours=3)))
    s.commit()
    _set_mode(s, "WARN")
    with pytest.raises(svc.ExperimentOsError, match="re-evaluation .* returned FAIL"):
        svc.transition_experiment(
            s, exp, "LIVE_CANARY", actor="operator", approved_by="operator",
            gate_result=result,
        )
    assert exp.state == "PAPER"  # nothing moved


def test_context_only_import_never_promotes(xos_session, xos_platform):
    s = xos_session
    exp = svc.import_legacy_experiment(
        s, key="ctx-only", state="PAPER", legacy_class="ACTIVE_PAPER",
        migration_integrity="C",
    )
    with pytest.raises(svc.ExperimentOsError, match="context-only"):
        svc.transition_experiment(
            s, exp, "LIVE_CANARY", actor="operator", approved_by="operator",
        )


# ---------------------------------------------------------------------------
# Live canary arming — the sanctioned path
# ---------------------------------------------------------------------------


def _canary_ready(s, *, risk=True):
    exp = svc.create_experiment(s, key="canary", origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", risk={"max_exposure_usd": 20} if risk else None, now=T0
    )
    svc.add_arm(s, ver, arm_key="a", role="treatment", strategy_tag="cx")
    svc.add_arm(s, ver, arm_key="c", role="control", strategy_tag="cx_c")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="paper", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key="canary-paper-1", stage="PAPER", kind="paper",
        arms={"a": "cx", "c": "cx_c"}, started_at=T0,
    )
    gate = svc.register_gate(
        s, ver, gate_key="paper_to_live_canary", kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "a",
                            "op": ">", "value": 0}]},
    )
    svc.mark_gate_evidence_started(s, gate, at=T0)
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    for i in range(4):
        s.add(PaperTrade(market_ticker=f"C-{i}", strategy="cx", status="settled",
                         pnl=0.05, quantity=1, created_at=T0 + timedelta(hours=1)))
    s.commit()
    return exp, ver, epoch, gate


def _arm(s, exp, gate, **overrides):
    kw = dict(
        gate=gate, approved_by="operator",
        live_key="canary-live-1", twin_key="canary-twin-1",
        live_tags={"a": "Lcx", "c": "Lcx_c"},
        twin_tags={"a": "Lcx_pt", "c": "Lcx_c_pt"},
        started_at=_dt(2026, 8, 10),
    )
    kw.update(overrides)
    return svc.arm_live_canary(s, exp, **kw)


def test_canary_arms_fresh_with_linked_twin(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate = _canary_ready(s)
    live, twin, live_epoch = _arm(s, exp, gate)
    assert exp.state == "LIVE_CANARY"
    assert twin.twin_of_deployment_id == live.id
    assert twin.started_at == live.started_at  # identical effective boundary
    assert live_epoch.impact_class == "I2"
    assert live.grandfathered is False
    # The authorizing result is a fresh system evaluation even under OFF.
    canary_tr = next(
        t for t in read.transitions_for(s, exp) if t.to_state == "LIVE_CANARY"
    )
    assert s.get(ExperimentGateResult, canary_tr.gate_result_id).computed_by == "system"


def test_canary_refuses_inherited_paper_state(xos_session, xos_platform):
    """The 2026-08-15 failure, made mechanically impossible: arming a live tag
    that already carries paper history is refused by name."""
    s = xos_session
    exp, ver, epoch, gate = _canary_ready(s)
    s.add(PaperTrade(market_ticker="OLD", strategy="Lcx", status="open",
                     created_at=T0 + timedelta(days=1)))
    s.commit()
    with pytest.raises(svc.ExperimentOsError, match="inherited paper state"):
        _arm(s, exp, gate)
    assert exp.state == "PAPER"  # arming is atomic — nothing half-armed


def test_canary_refuses_active_tag_reuse(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate = _canary_ready(s)
    with pytest.raises(svc.ExperimentOsError, match="already carried by an active"):
        _arm(s, exp, gate, live_tags={"a": "cx", "c": "Lcx_c"})  # reuses the paper tag


def test_canary_requires_full_arm_mapping_and_risk(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate = _canary_ready(s)
    with pytest.raises(svc.ExperimentOsError, match="must equal the declared arm set"):
        _arm(s, exp, gate, twin_tags={"a": "Lcx_pt"})  # twin misses the control arm
    # No pre-registered risk envelope → no live canary.
    s2 = s
    exp2 = svc.create_experiment(s2, key="canary-norisk", origin="operator")
    ver2 = svc.create_experiment_version(s2, exp2, hypothesis="h", now=T0)
    svc.add_arm(s2, ver2, arm_key="a", role="control", strategy_tag="nr")
    svc.freeze_version(s2, ver2, now=T0)
    svc.transition_experiment(s2, exp2, "PROBE", actor="operator")
    svc.open_epoch(s2, ver2, reason="paper", started_at=T0)
    svc.transition_experiment(s2, exp2, "PAPER", actor="operator")
    gate2 = svc.register_gate(
        s2, ver2, gate_key="paper_to_live_canary", kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "a",
                            "op": ">", "value": 0}]},
    )
    svc.mark_gate_evidence_started(s2, gate2, at=T0)
    with pytest.raises(svc.ExperimentOsError, match="risk envelope"):
        svc.arm_live_canary(
            s2, exp2, gate=gate2, approved_by="op", live_key="x-l", twin_key="x-t",
            live_tags={"a": "xnr"}, twin_tags={"a": "xnr_pt"},
        )


def test_direct_live_registration_blocked_under_new_only(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate = _canary_ready(s)
    _set_mode(s, "NEW_ONLY", readiness=_ok_readiness())
    with pytest.raises(svc.ExperimentOsError, match="arm_live_canary"):
        svc.register_deployment(
            s, epoch, deployment_key="rogue-live", stage="LIVE_CANARY", kind="live",
            arms={"a": "rogue"}, started_at=_dt(2026, 8, 10),
        )
    # WARN allows it but leaves a visible integrity event.
    _set_mode(s, "WARN", cutover_id="warn-after")
    svc.register_deployment(
        s, epoch, deployment_key="warned-live", stage="LIVE_CANARY", kind="live",
        arms={"a": "warned"}, started_at=_dt(2026, 8, 10),
    )
    ev = s.scalar(
        select(ExperimentIntegrityEvent).where(
            ExperimentIntegrityEvent.kind == "LIVE_REGISTERED_OUTSIDE_CANARY_PATH"
        )
    )
    assert ev is not None


# ---------------------------------------------------------------------------
# Readiness + observability
# ---------------------------------------------------------------------------


def test_readiness_requires_lineage_columns_on_both_runtime_tables(
    xos_session, monkeypatch
):
    """NEW_ONLY must be able to persist lineage on BOTH write paths — a missing
    live_orders column fails readiness and the detail names the exact table."""
    s = xos_session
    ready = enf.production_readiness(s)
    detail = ready["checks"]["lineage_columns_present"]["detail"]
    assert detail["paper_trades"] == "experiment_deployment_arm_id present"
    assert detail["live_orders"] == "experiment_deployment_arm_id present"
    assert ready["checks"]["lineage_columns_present"]["ok"]

    from sqlalchemy.engine.reflection import Inspector

    real = Inspector.get_columns

    def live_column_missing(self, table, *a, **k):
        cols = real(self, table, *a, **k)
        if table == "live_orders":
            return [c for c in cols if c["name"] != "experiment_deployment_arm_id"]
        return cols

    monkeypatch.setattr(Inspector, "get_columns", live_column_missing)
    again = enf.production_readiness(s)
    chk = again["checks"]["lineage_columns_present"]
    assert chk["ok"] is False
    assert "MISSING" in chk["detail"]["live_orders"]
    assert chk["detail"]["paper_trades"] == "experiment_deployment_arm_id present"
    assert again["ok"] is False


def test_readiness_fails_on_empty_db_and_passes_after_clean_import(xos_session):
    s = xos_session
    not_ready = enf.production_readiness(s)
    assert not_ready["ok"] is False
    assert not_ready["checks"]["import_ran"]["ok"] is False
    for twin_tag, live_tag in [
        ("Lmmsell8_pt3", "Lmmsell8"), ("Lmmsell10_pt3", "Lmmsell10"),
        ("theta4_pt3", "theta4"),
    ]:
        s.add(LivePaperTwin(twin_tag=twin_tag, live_tag=live_tag, started_at=T0))
    s.commit()
    importer.import_legacy(s, now=_dt(2026, 8, 15, 16, 0))
    s.commit()
    ready = enf.production_readiness(s)
    assert ready["checks"]["import_ran"]["ok"]
    assert ready["checks"]["coverage_complete"]["ok"]  # no traded tags in this DB
    assert ready["checks"]["platform_snapshot_complete"]["ok"]
    assert ready["checks"]["grandfathered_identity"]["ok"]
    # theta4's twin epoch starts at the fee re-baseline, 12 days after the live
    # arm — imported truth. It must be RECORDED (a grandfathered note), never a
    # blocker (that would force inventing an equal boundary) and never silent.
    twin_check = ready["checks"]["live_twin_links"]
    assert twin_check["ok"]
    assert any(
        "theta4-live-1" in note for note in twin_check["detail"]["grandfathered_notes"]
    )
    assert ready["checks"]["lineage_columns_present"]["ok"]
    assert ready["ok"] is True
    # An unmapped traded tag flips coverage off — the report names it.
    s.add(PaperTrade(market_ticker="X", strategy="mystery", created_at=T0))
    s.commit()
    again = enf.production_readiness(s)
    assert again["ok"] is False
    assert again["checks"]["coverage_complete"]["detail"]["unmapped_tags"] == ["mystery"]


def test_report_finds_post_cutover_unstamped_rows(xos_session, xos_platform):
    s = xos_session
    _experiment_with_deployment(s, key="gf2", tag="known_tag", grandfathered=True)
    _set_mode(s, "WARN", cutover_id="cutover-obs")
    enf.refresh(s)
    _paper_entry(s, "known_tag")     # stamped
    _paper_entry(s, "outside_tag")   # allowed under WARN, unstamped
    s.commit()
    report = enf.enforcement_report(s)
    assert report["mode"] == "WARN"
    assert report["cutover_id"] == "cutover-obs"
    unstamped = {r["tag"] for r in report["post_cutover_unstamped"]["paper_trades"]}
    assert unstamped == {"outside_tag"}  # the mechanical Control Tower answer
    assert report["deployments"]["grandfathered"] == 1
    assert report["session_counters"]["warned"] >= 1
