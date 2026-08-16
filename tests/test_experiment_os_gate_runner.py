"""Persisted gate evaluation (gate_runner) — tests target the ways an automatic
writer could quietly become an automatic promoter, or bury the trail in noise.

What must never happen, pinned here:

  * the same evidence written twice as if it were two decisions;
  * a real change (verdict, binding, blocking state, sample progress) skipped as
    "unchanged";
  * a recorded PASS turning into a promotion by itself, or an old PASS surviving
    a later HOLD/FAIL as a permission slip;
  * a hand-written result masquerading as an evaluator result;
  * a BLOCKED_* verdict laundered into something promotable;
  * one broken gate stopping the others, or evaluation stopping trading;
  * an undesignated process (evo worker, gates-off worker) writing results.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import evaluator, gate_runner, read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.models import ExperimentGateResult
from kalshi_bot.models import PaperTrade

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


class FakeSettings:
    """The two fields the authority model reads, and nothing else."""

    def __init__(self, bot_mode="live", evaluate=True, interval=60):
        self.bot_mode = bot_mode
        self.experiment_os_evaluate_gates = evaluate
        self.experiment_os_evaluate_interval_minutes = interval


LIVE = FakeSettings()


@pytest.fixture(autouse=True)
def _reset_cadence():
    gate_runner.reset_for_tests()
    yield
    gate_runner.reset_for_tests()


def _trade(s, tag, *, pnl=0.05, status="settled", at=None, qty=1):
    s.add(PaperTrade(
        market_ticker=f"T-{tag}-{id(object())}", strategy=tag, status=status,
        pnl=pnl, quantity=qty, created_at=at or (T0 + timedelta(hours=1)),
    ))


SPEC = {
    "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 20}},
    "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                  "op": ">", "value": 0}],
    "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                  "op": "<=", "value": -5}],
}


def _experiment(s, key="exp-r", *, state="PAPER", tag=None, spec=None,
                gate_key="paper_to_live_canary"):
    """Frozen, single-arm PAPER (or further) experiment with one started gate."""
    tag = tag or f"{key}-t"
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
        control_exemption_reason="test shape: absolute-threshold gate, no control arm",
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=tag)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper-1", stage="PAPER", kind="paper",
        arms={"treatment": tag}, started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    if state != "PROBE":
        svc.transition_experiment(s, exp, "PAPER", actor="operator")
    gate = svc.register_gate(
        s, ver, gate_key=gate_key, kind="promotion", spec=spec or SPEC,
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
    )
    svc.mark_gate_evidence_started(s, gate, at=T0)
    s.commit()
    return exp, ver, epoch, gate, tag


def _redeploy(s, epoch, key, tag, *, at=None):
    """A new epoch is a new operating interval: the arms have to be deployed into
    it, exactly as in production. Without this the new epoch has no tags at all."""
    return svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper-e{epoch.epoch_number}", stage="PAPER",
        kind="paper", arms={"treatment": tag}, started_at=at or epoch.started_at,
    )


def _results(s, gate):
    return read.gate_results_for(s, gate)


def _run(s, **kw):
    return gate_runner.run_evaluation_cycle(s, settings=LIVE, **kw)


# ---------------------------------------------------------------------------
# Authority: exactly one designated writer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("settings,needle", [
    (FakeSettings(bot_mode="evo"), "not the designated evaluator"),
    (FakeSettings(bot_mode="scanner"), "not the designated evaluator"),
    (FakeSettings(bot_mode="live", evaluate=False), "EXPERIMENT_OS_EVALUATE_GATES is off"),
])
def test_undesignated_process_may_not_write(xos_session, xos_platform, settings, needle):
    s = xos_session
    _experiment(s)
    with pytest.raises(gate_runner.NotAuthorized, match=needle):
        gate_runner.run_evaluation_cycle(s, settings=settings)
    assert s.query(ExperimentGateResult).count() == 0


def test_undesignated_process_may_still_dry_run(xos_session, xos_platform):
    """A read is always allowed — the Control Tower and the ops channel depend on it."""
    s = xos_session
    _experiment(s)
    out = gate_runner.run_evaluation_cycle(
        s, settings=FakeSettings(bot_mode="evo"), dry_run=True
    )
    assert out["evaluated"] == 1 and out["written"] == 0
    assert s.query(ExperimentGateResult).count() == 0


def test_scheduled_hook_is_silent_for_undesignated_and_writes_for_designated(
    xos_session, xos_platform
):
    s = xos_session
    _experiment(s)
    assert gate_runner.run_scheduled_evaluation(s, FakeSettings(bot_mode="evo")) is None
    assert gate_runner.run_scheduled_evaluation(
        s, FakeSettings(bot_mode="live", evaluate=False)) is None
    assert s.query(ExperimentGateResult).count() == 0

    summary = gate_runner.run_scheduled_evaluation(s, LIVE)
    assert summary is not None and summary["written"] == 1
    # Cadence: the immediate next cycle does nothing at all.
    assert gate_runner.run_scheduled_evaluation(s, LIVE) is None
    assert s.query(ExperimentGateResult).count() == 1


def test_scheduled_hook_never_raises_into_the_trading_loop(xos_session, xos_platform,
                                                           monkeypatch):
    s = xos_session
    _experiment(s)

    def boom(*a, **k):
        raise RuntimeError("catastrophic evaluation failure")

    monkeypatch.setattr(gate_runner, "run_evaluation_cycle", boom)
    assert gate_runner.run_scheduled_evaluation(s, LIVE) is None  # swallowed


# ---------------------------------------------------------------------------
# Dedupe: decision points, not a heartbeat
# ---------------------------------------------------------------------------


def test_same_evidence_twice_writes_one_row(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s)
    for _ in range(30):
        _trade(s, tag, pnl=0.05)
    s.commit()

    first = _run(s)
    assert first["written"] == 1 and first["skipped_unchanged"] == 0
    second = _run(s)
    assert second["evaluated"] == 1
    assert second["written"] == 0 and second["skipped_unchanged"] == 1
    assert "unchanged" in second["skipped"][0]["reason"]
    assert len(_results(s, gate)) == 1


def test_metric_drift_alone_is_not_a_new_decision(xos_session, xos_platform):
    """A HOLD whose ¢/trade moved a hair is the same decision. Values are
    deliberately outside the fingerprint."""
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s)
    for _ in range(5):
        _trade(s, tag, pnl=0.05)
    s.commit()
    assert _run(s)["written"] == 1
    assert _results(s, gate)[-1].verdict == "HOLD"  # floor 5/20

    _trade(s, tag, pnl=-0.04)  # moves the mean, not the decision, not the step
    s.commit()
    again = _run(s)
    assert again["written"] == 0 and again["skipped_unchanged"] == 1
    assert len(_results(s, gate)) == 1


def test_sample_progress_records_a_hold_trail(xos_session, xos_platform):
    """A long HOLD must still show it is accumulating — but in steps, not per cycle."""
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s)
    for _ in range(4):
        _trade(s, tag, pnl=0.05)
    s.commit()
    _run(s)
    assert len(_results(s, gate)) == 1

    for _ in range(10):  # 4 → 14: below the 25 absolute step
        _trade(s, tag, pnl=0.05)
    s.commit()
    assert _run(s)["written"] == 0

    for _ in range(4):  # 14 → 18, still HOLD, still below the step
        _trade(s, tag, pnl=0.05)
    s.commit()
    assert _run(s)["written"] == 0
    assert len(_results(s, gate)) == 1


def test_verdict_change_always_records_even_at_the_same_sample_step(
    xos_session, xos_platform
):
    """HOLD → PASS is the whole point: two immutable results, both kept."""
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s)
    for _ in range(19):
        _trade(s, tag, pnl=0.05)
    s.commit()
    _run(s)
    assert _results(s, gate)[-1].verdict == "HOLD"

    _trade(s, tag, pnl=0.05)  # 19 → 20 crosses the floor; +1 is under the step
    s.commit()
    assert _run(s)["written"] == 1
    rows = _results(s, gate)
    assert [r.verdict for r in rows] == ["HOLD", "PASS"]
    # Both are immutable history — the HOLD is not rewritten into the PASS.
    rows[0].verdict = "PASS"
    with pytest.raises(svc.ImmutableRecord):
        s.flush()
    s.rollback()


def test_pass_then_fail_supersedes_without_erasing(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s)
    for _ in range(25):
        _trade(s, tag, pnl=0.05)
    s.commit()
    _run(s)
    assert _results(s, gate)[-1].verdict == "PASS"

    for _ in range(60):
        _trade(s, tag, pnl=-0.30)  # the edge dies
    s.commit()
    assert _run(s)["written"] == 1
    rows = _results(s, gate)
    assert [r.verdict for r in rows] == ["PASS", "FAIL"]
    assert evaluator.latest_result(s, gate).verdict == "FAIL"


def test_new_epoch_rerecords_the_same_verdict(xos_session, xos_platform):
    """Same verdict under a different world is a different claim — the binding is
    inside the fingerprint, so the new epoch gets its own first result."""
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s)
    for _ in range(30):
        _trade(s, tag, pnl=0.05)
    s.commit()
    _run(s)
    first = _results(s, gate)[-1]

    svc.close_epoch(s, epoch, ended_at=T0 + timedelta(days=2))
    e2 = svc.open_epoch(s, ver, reason="platform boundary", started_at=T0 + timedelta(days=2))
    _redeploy(s, e2, "exp-r", tag)
    for _ in range(30):
        _trade(s, tag, pnl=0.05, at=T0 + timedelta(days=3))
    s.commit()

    assert _run(s)["written"] == 1
    rows = _results(s, gate)
    assert len(rows) == 2
    assert rows[0].epoch_id == epoch.id and rows[1].epoch_id == e2.id
    assert rows[0].verdict == rows[1].verdict == "PASS"
    assert first.id != rows[1].id


def test_unfingerprinted_prior_is_not_rewritten_on_adoption(xos_session, xos_platform):
    """Adopting this module must not re-record every pre-existing result. It
    re-records only on a real verdict change."""
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s)
    for _ in range(30):
        _trade(s, tag, pnl=0.05)
    s.commit()
    evaluator.evaluate_gate(s, gate, epoch=epoch)  # legacy path: no fingerprint
    s.commit()
    assert (_results(s, gate)[-1].metrics_json or {}).get("fingerprint") is None

    assert _run(s)["written"] == 0
    for _ in range(60):
        _trade(s, tag, pnl=-0.30)
    s.commit()
    assert _run(s)["written"] == 1
    assert [r.verdict for r in _results(s, gate)] == ["PASS", "FAIL"]


# ---------------------------------------------------------------------------
# Eligibility: what the writer refuses to touch
# ---------------------------------------------------------------------------


def test_retired_and_paused_and_idea_experiments_are_skipped(xos_session, xos_platform):
    s = xos_session
    _experiment(s, key="exp-live-one")
    _, _, _, retired_gate, _ = _experiment(s, key="exp-retired")
    _, _, _, paused_gate, _ = _experiment(s, key="exp-paused")
    svc.create_experiment(s, key="exp-idea", origin="operator")  # IDEA, no version
    svc.transition_experiment(s, read.get_experiment(s, "exp-retired"), "RETIRED",
                              actor="operator", reason="killed")
    svc.transition_experiment(s, read.get_experiment(s, "exp-paused"), "PAUSED",
                              actor="operator", reason="halted")
    s.commit()

    _experiment(s, key="exp-probe", state="PROBE")  # PROBE evidence still counts
    s.commit()

    keys = {i["experiment"].key for i in gate_runner.eligible_gates(s)}
    assert keys == {"exp-live-one", "exp-probe"}
    out = _run(s)
    assert out["considered"] == 2
    assert _results(s, retired_gate) == [] and _results(s, paused_gate) == []


def test_unstarted_gate_and_closed_epoch_are_not_evaluated(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s, key="exp-unstarted")
    later = svc.register_gate(
        s, ver, gate_key="live_canary_keep", kind="promotion", spec=SPEC,
        from_state="LIVE_CANARY", to_state="PRODUCTION", registered_at=T0,
    )  # registered, evidence not started
    s.commit()
    assert {i["gate"].id for i in gate_runner.eligible_gates(s)} == {gate.id}
    assert _results(s, later) == []

    svc.close_epoch(s, epoch, ended_at=T0 + timedelta(days=1))
    s.commit()
    assert gate_runner.eligible_gates(s) == []  # no operating interval
    assert _run(s)["considered"] == 0


def test_filters_narrow_the_cycle_without_widening_authority(xos_session, xos_platform):
    s = xos_session
    _experiment(s, key="exp-a")
    _experiment(s, key="exp-b")
    s.commit()
    out = _run(s, only_experiment="exp-a")
    assert out["considered"] == 1 and out["written"] == 1
    assert s.query(ExperimentGateResult).count() == 1
    out = _run(s, only_gate="nonexistent")
    assert out["considered"] == 0


# ---------------------------------------------------------------------------
# Blocked verdicts are recorded as blocked, never laundered
# ---------------------------------------------------------------------------


def test_blocked_data_is_recorded_and_cannot_promote(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(
        s, key="exp-blocked-data",
        spec={"pass_all": [{"metric": "realizable_cents_per_trade",
                            "arm": "treatment", "op": ">", "value": 0}]},
    )
    for _ in range(30):
        _trade(s, tag, pnl=0.05)
    s.commit()
    assert _run(s)["written"] == 1
    row = _results(s, gate)[-1]
    assert row.verdict == "BLOCKED_DATA"
    assert row.computed_by == "system"
    with pytest.raises(svc.ExperimentOsError):
        svc.transition_experiment(s, exp, "LIVE_CANARY", actor="operator",
                                  approved_by="operator", gate_result=row)
    s.rollback()
    assert read.get_experiment(s, "exp-blocked-data").state == "PAPER"


def test_blocked_integrity_is_recorded_and_cannot_promote(xos_session, xos_platform):
    """A clause the addressing contract refuses to resolve stays refused in the
    persisted trail — it does not become a HOLD that later "clears"."""
    s = xos_session
    exp = svc.create_experiment(s, key="exp-blocked-int", origin="operator")
    ver = svc.create_experiment_version(s, exp, hypothesis="h",
                                        independent_variable="lever", now=T0)
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag="bi_t")
    svc.add_arm(s, ver, arm_key="control", role="control", strategy_tag="bi_c")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(s, epoch, deployment_key="bi-paper-1", stage="PAPER",
                            kind="paper", arms={"treatment": "bi_t", "control": "bi_c"},
                            started_at=T0)
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    from kalshi_bot.experiment_os.models import ExperimentGate

    gate = ExperimentGate(  # legacy shape: ambiguous clause, no arm, two arms
        version_id=ver.id, gate_key="paper_to_live_canary", kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY",
        spec_json={"pass_all": [{"metric": "pnl_cents_per_trade", "op": ">", "value": 0}]},
        spec_hash=svc.canonical_hash({"x": 1}),
        registered_at=T0, evidence_started_at=T0, created_at=T0,
    )
    s.add(gate)
    s.commit()

    assert _run(s)["written"] == 1
    row = _results(s, gate)[-1]
    assert row.verdict == "BLOCKED_INTEGRITY"
    with pytest.raises(svc.ExperimentOsError):
        svc.transition_experiment(s, exp, "LIVE_CANARY", actor="operator",
                                  approved_by="operator", gate_result=row)
    s.rollback()


def test_blocked_platform_is_recorded_and_the_unblock_is_a_new_result(
    xos_session, xos_platform
):
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s, key="exp-blocked-plat")
    for _ in range(30):
        _trade(s, tag, pnl=0.05)
    s.commit()
    svc.register_platform_revision(
        s, "FEE_MODEL", version="v2", activate=True,
        activated_at=T0 + timedelta(hours=6),
        force=True, force_reason="test: unclassified platform change",
    )
    s.commit()

    assert _run(s)["written"] == 1
    row = _results(s, gate)[-1]
    assert row.verdict == "BLOCKED_PLATFORM"
    with pytest.raises(svc.ExperimentOsError):
        svc.transition_experiment(s, exp, "LIVE_CANARY", actor="operator",
                                  approved_by="operator", gate_result=row)
    s.rollback()
    assert _run(s)["written"] == 0  # still blocked, still one row

    # Resolve the boundary the real way: classify the change, close the epoch
    # across it, re-open and re-deploy. The unblock is then a NEW result, not a
    # rewrite of the blocked one.
    from kalshi_bot.experiment_os.models import ExperimentIntegrityEvent

    for ev in s.query(ExperimentIntegrityEvent).filter(
        ExperimentIntegrityEvent.resolved_at.is_(None)
    ):
        ev.resolved_at = T0 + timedelta(hours=6)
        ev.resolution = "classified I2; fresh epoch opened"
    svc.close_epoch(s, epoch, ended_at=T0 + timedelta(hours=6))
    e2 = svc.open_epoch(s, ver, reason="fee model v2", impact_class="I2",
                        started_at=T0 + timedelta(hours=6))
    _redeploy(s, e2, "exp-blocked-plat", tag)
    for _ in range(30):
        _trade(s, tag, pnl=0.05, at=T0 + timedelta(hours=8))
    s.commit()
    assert _run(s)["written"] == 1
    assert [r.verdict for r in _results(s, gate)] == ["BLOCKED_PLATFORM", "PASS"]


# ---------------------------------------------------------------------------
# The line: evaluation is automatic, promotion is not
# ---------------------------------------------------------------------------


def test_a_recorded_pass_does_not_move_the_experiment(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s, key="exp-nopromote")
    for _ in range(30):
        _trade(s, tag, pnl=0.05)
    s.commit()
    before = len(read.transitions_for(s, exp))
    assert _run(s)["written"] == 1
    assert _results(s, gate)[-1].verdict == "PASS"
    assert read.get_experiment(s, "exp-nopromote").state == "PAPER"
    assert len(read.transitions_for(s, exp)) == before
    # The operator's act is what moves it, and the recorded PASS authorizes that.
    svc.transition_experiment(s, exp, "LIVE_CANARY", actor="operator",
                              approved_by="operator",
                              gate_result=_results(s, gate)[-1])
    assert exp.state == "LIVE_CANARY"


def test_the_writer_never_calls_a_lifecycle_write(xos_session, xos_platform, monkeypatch):
    """Structural, not incidental: every promotion/transition entry point raises if
    the evaluation cycle so much as touches it."""
    s = xos_session
    _experiment(s, key="exp-nolifecycle")
    for _ in range(30):
        _trade(s, "exp-nolifecycle-t", pnl=0.05)
    s.commit()

    def forbidden(name):
        def _f(*a, **k):
            raise AssertionError(f"the gate writer called {name}")
        return _f

    for name in ("transition_experiment", "arm_live_canary", "pause_experiment",
                 "retire_experiment", "open_epoch", "close_epoch",
                 "register_deployment", "create_experiment_version"):
        if hasattr(svc, name):
            monkeypatch.setattr(svc, name, forbidden(name))
    out = _run(s)
    assert out["written"] == 1 and out["errors"] == 0


def test_a_hand_written_result_cannot_masquerade_as_the_evaluator(
    xos_session, xos_platform
):
    """`computed_by` is a label, not authority. What actually authorizes is the
    binding the evaluator writes — epoch, snapshot, metric revision, window — and
    a row typed in by hand has none of it, whatever it calls itself."""
    s = xos_session
    forge_exp, _, _, forge_gate, forge_tag = _experiment(s, key="exp-forge")
    real_exp, _, real_epoch, real_gate, real_tag = _experiment(s, key="exp-real")
    for _ in range(30):
        _trade(s, forge_tag, pnl=0.05)
        _trade(s, real_tag, pnl=0.05)
    s.commit()

    forged = svc.record_gate_result(s, forge_gate, verdict="PASS", computed_by="system")
    s.commit()
    assert forged.epoch_id is None and forged.metric_revision is None
    with pytest.raises(svc.ExperimentOsError, match="epoch-less PASS"):
        svc.transition_experiment(s, forge_exp, "LIVE_CANARY", actor="operator",
                                  approved_by="operator", gate_result=forged)
    s.rollback()

    _run(s)
    real = _results(s, real_gate)[-1]
    assert real.computed_by == "system" and real.epoch_id == real_epoch.id
    assert real.metric_revision is not None and real.platform_snapshot_id is not None
    svc.transition_experiment(s, real_exp, "LIVE_CANARY", actor="operator",
                              approved_by="operator", gate_result=real)
    assert real_exp.state == "LIVE_CANARY"
    # And the forged row did not become the writer's baseline: the gate it sits on
    # is still evaluated, it just has nothing new to say yet.
    assert forge_exp.state == "PAPER"


def test_a_stale_recorded_pass_still_cannot_promote(xos_session, xos_platform):
    """The writer records history; currency is the promotion path's job. A PASS
    recorded under a closed epoch is refused exactly as a manual one would be."""
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s, key="exp-stale")
    for _ in range(30):
        _trade(s, tag, pnl=0.05)
    s.commit()
    _run(s)
    old_pass = _results(s, gate)[-1]
    assert old_pass.verdict == "PASS"

    svc.close_epoch(s, epoch, ended_at=T0 + timedelta(days=5))
    svc.open_epoch(s, ver, reason="new world", started_at=T0 + timedelta(days=5))
    s.commit()
    with pytest.raises(svc.ExperimentOsError, match="stale-epoch evidence never promotes"):
        svc.transition_experiment(s, exp, "LIVE_CANARY", actor="operator",
                                  approved_by="operator", gate_result=old_pass)
    s.rollback()


def test_a_superseded_pass_cannot_promote_after_the_writer_records_a_hold(
    xos_session, xos_platform
):
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s, key="exp-superseded")
    for _ in range(30):
        _trade(s, tag, pnl=0.05)
    s.commit()
    _run(s)
    old_pass = _results(s, gate)[-1]

    # A new epoch resets the sample: the same gate is now underpowered → HOLD.
    svc.close_epoch(s, epoch, ended_at=T0 + timedelta(days=5))
    e2 = svc.open_epoch(s, ver, reason="new world", started_at=T0 + timedelta(days=5))
    _redeploy(s, e2, "exp-superseded", tag)
    _trade(s, tag, pnl=0.05, at=T0 + timedelta(days=6))
    s.commit()
    assert _run(s)["written"] == 1
    assert _results(s, gate)[-1].verdict == "HOLD"
    with pytest.raises(svc.ExperimentOsError):
        svc.transition_experiment(s, exp, "LIVE_CANARY", actor="operator",
                                  approved_by="operator", gate_result=old_pass)
    s.rollback()


# ---------------------------------------------------------------------------
# Isolation: one bad gate must not stop the rest
# ---------------------------------------------------------------------------


def test_one_crashing_gate_does_not_stop_the_others(xos_session, xos_platform,
                                                    monkeypatch):
    s = xos_session
    _experiment(s, key="exp-good-1")
    _, _, _, bad_gate, _ = _experiment(s, key="exp-bad")
    _experiment(s, key="exp-good-2")
    for key in ("exp-good-1", "exp-bad", "exp-good-2"):
        for _ in range(30):
            _trade(s, f"{key}-t", pnl=0.05)
    s.commit()

    real = evaluator.evaluate_gate

    def flaky(session, gate, **kw):
        if gate.id == bad_gate.id:
            raise RuntimeError("metric provider exploded")
        return real(session, gate, **kw)

    monkeypatch.setattr(gate_runner, "evaluate_gate", flaky)
    out = _run(s)
    assert out["considered"] == 3
    assert out["evaluated"] == 2 and out["written"] == 2 and out["errors"] == 1
    assert out["failures"][0]["gate"].startswith("exp-bad/")
    assert "metric provider exploded" in out["failures"][0]["error"]
    assert len(_results(s, bad_gate)) == 0
    assert s.query(ExperimentGateResult).count() == 2


def test_a_failed_write_rolls_back_only_that_gate(xos_session, xos_platform,
                                                  monkeypatch):
    """The narrow per-gate transaction: a persist that blows up must not take a
    sibling's already-committed result with it."""
    s = xos_session
    _experiment(s, key="exp-w-1")
    _, _, _, bad_gate, _ = _experiment(s, key="exp-w-2")
    _experiment(s, key="exp-w-3")
    for key in ("exp-w-1", "exp-w-2", "exp-w-3"):
        for _ in range(30):
            _trade(s, f"{key}-t", pnl=0.05)
    s.commit()

    real = gate_runner.persist_outcome

    def flaky(session, gate, outcome, **kw):
        if gate.id == bad_gate.id:
            raise RuntimeError("write failed")
        return real(session, gate, outcome, **kw)

    monkeypatch.setattr(gate_runner, "persist_outcome", flaky)
    out = _run(s)
    assert out["written"] == 2 and out["errors"] == 1
    assert s.query(ExperimentGateResult).count() == 2
    assert len(_results(s, bad_gate)) == 0


# ---------------------------------------------------------------------------
# Dry run parity: what it says it would write is what it writes
# ---------------------------------------------------------------------------


def test_dry_run_predicts_the_write_exactly(xos_session, xos_platform):
    s = xos_session
    _experiment(s, key="exp-parity-a")
    _experiment(s, key="exp-parity-b")
    for key in ("exp-parity-a", "exp-parity-b"):
        for _ in range(30):
            _trade(s, f"{key}-t", pnl=0.05)
    s.commit()

    dry = gate_runner.run_evaluation_cycle(s, settings=LIVE, dry_run=True)
    assert dry["written"] == 0 and s.query(ExperimentGateResult).count() == 0
    predicted = {(r["gate"], r["verdict"]) for r in dry["written_rows"]}

    wet = _run(s)
    actual = {(r["gate"], r["verdict"]) for r in wet["written_rows"]}
    assert predicted == actual
    assert wet["written"] == len(predicted) == 2

    # And the dry run after the write predicts nothing further.
    again = gate_runner.run_evaluation_cycle(s, settings=LIVE, dry_run=True)
    assert again["written_rows"] == [] and again["skipped_unchanged"] == 2


def test_fingerprint_is_stable_across_identical_evaluations(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch, gate, tag = _experiment(s, key="exp-fp")
    for _ in range(30):
        _trade(s, tag, pnl=0.05)
    s.commit()
    a = evaluator.evaluate_gate(s, gate, epoch=epoch, persist=False,
                                window_end=T0 + timedelta(days=1))
    b = evaluator.evaluate_gate(s, gate, epoch=epoch, persist=False,
                                window_end=T0 + timedelta(days=2))
    # Different windows, same decision → same fingerprint.
    assert (gate_runner.evaluation_fingerprint(a, gate, epoch)
            == gate_runner.evaluation_fingerprint(b, gate, epoch))
    _run(s)
    stored = (_results(s, gate)[-1].metrics_json or {}).get("fingerprint")
    assert stored == gate_runner.evaluation_fingerprint(a, gate, epoch)


# ---------------------------------------------------------------------------
# The operator path: writes only where writes are possible
# ---------------------------------------------------------------------------


def test_cli_refuses_to_persist_over_the_read_only_connection(xos_session, xos_platform,
                                                              monkeypatch, capsys):
    """The ops channel always runs with DATABASE_URL_RO set. `evaluate-gates` must
    fail loudly there rather than half-way through, in a Postgres permission error."""
    from argparse import Namespace

    from kalshi_bot.experiment_os import cli

    s = xos_session
    _experiment(s, key="exp-cli")
    for _ in range(30):
        _trade(s, "exp-cli-t", pnl=0.05)
    s.commit()

    monkeypatch.setenv("DATABASE_URL_RO", "postgresql://ro@example/db")
    args = Namespace(dry_run=False, experiment=None, gate=None)
    assert cli.cmd_evaluate_gates(s, args) == 2
    assert "refusing to persist over DATABASE_URL_RO" in capsys.readouterr().err
    assert s.query(ExperimentGateResult).count() == 0

    # Dry run over the same read-only connection is fine, and still writes nothing.
    assert cli.cmd_evaluate_gates(s, Namespace(dry_run=True, experiment=None,
                                               gate=None)) == 0
    assert s.query(ExperimentGateResult).count() == 0

    # Writable connection: the same command records.
    monkeypatch.delenv("DATABASE_URL_RO")
    assert cli.cmd_evaluate_gates(s, args) == 0
    assert s.query(ExperimentGateResult).count() == 1


# ---------------------------------------------------------------------------
# Runtime wiring: the hook must run in the mode production actually uses
# ---------------------------------------------------------------------------


def test_experiment_os_hook_runs_in_every_mode_not_just_the_scanner():
    """The 2026-08-16 production miss, made structural.

    `main._run_cycle` is only the SCANNER fallback: with BOT_MODE=live the loop
    calls `_run_live_cycle`, with weather `_run_weather_cycle`, and so on. The
    first deploy of this module wired the evaluator into `_run_cycle`, so on the
    live worker it never executed once — no error, no log, no rows.

    The maintenance hook therefore belongs in the LOOP BODY, which every mode
    passes through. This pins that: it is called from `run()`, before the mode
    dispatch, and not from any individual cycle function (which would double-run
    it in one mode and skip it in the rest)."""
    import ast
    import inspect

    from kalshi_bot import main as main_mod

    HOOK = "_experiment_os_cycle"
    assert callable(getattr(main_mod, HOOK)), "the maintenance hook must exist"

    tree = ast.parse(inspect.getsource(main_mod))
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def calls_hook(node) -> bool:
        return any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == HOOK
            for c in ast.walk(node)
        )

    loops = [n for n in ast.walk(fns["run"]) if isinstance(n, ast.While)]
    assert loops, "run() must still drive a cycle loop"
    assert any(calls_hook(loop) for loop in loops), (
        f"{HOOK}() must be called from run()'s cycle loop — a hook inside a single "
        "cycle function does not run in the modes that use a different one"
    )

    for name in ("_run_cycle", "_run_live_cycle", "_run_weather_cycle",
                 "_run_mmsell_cycle"):
        if name in fns:
            assert not calls_hook(fns[name]), (
                f"{name} must not call {HOOK}() — the loop body owns it, so calling "
                "it here would double-run in one mode and still skip the others"
            )

    # And the hook itself must do both jobs, each independently guarded.
    src = inspect.getsource(getattr(main_mod, HOOK))
    assert "enforcement" in src and "gate_runner" in src
    assert src.count("except Exception") >= 2


def test_an_inert_evaluator_announces_itself_once(xos_session, xos_platform, caplog):
    """Silence is indistinguishable from 'ran and found nothing' — which is what
    made the production miss invisible. Say it once, then stay quiet."""
    import logging

    s = xos_session
    _experiment(s, key="exp-inert")
    evo = FakeSettings(bot_mode="evo")
    with caplog.at_level(logging.INFO, logger="kalshi_bot.experiment_os.gate_runner"):
        assert gate_runner.run_scheduled_evaluation(s, evo) is None
        assert gate_runner.run_scheduled_evaluation(s, evo) is None
    inert = [r for r in caplog.records if "inert" in r.getMessage()]
    assert len(inert) == 1  # once per process, not once per cycle


def test_a_completed_cycle_always_logs_its_counts(xos_session, xos_platform, caplog):
    """Including a cycle that writes nothing: `considered=0` is the diagnosis."""
    import logging

    s = xos_session
    with caplog.at_level(logging.INFO, logger="kalshi_bot.experiment_os.gate_runner"):
        summary = gate_runner.run_scheduled_evaluation(s, LIVE)  # no experiments at all
    assert summary is not None and summary["considered"] == 0
    assert any("gate evaluation cycle" == r.getMessage() for r in caplog.records)


def test_progress_compares_like_with_like_across_clause_widths(xos_session,
                                                               xos_platform):
    """The 2026-08-16 production duplicate, made a regression test.

    mmsell-anchor-vol-entry's sample FLOOR clause counted 29 trades while its
    widest pass_all clause counted 77. The old check measured the prior from
    `sample_json` (floors only) and the present from every clause, so the fixed
    48-wide gap read as "+48 of progress" on every cycle and re-recorded the same
    HOLD forever. Same gate, same evidence, one row."""
    s = xos_session
    exp = svc.create_experiment(s, key="exp-widths", origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag="w_t")
    svc.add_arm(s, ver, arm_key="control", role="control", strategy_tag="w_c")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key="w-paper-1", stage="PAPER", kind="paper",
        arms={"treatment": "w_t", "control": "w_c"}, started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    # Floor on the THIN treatment arm; the pass clause is scored on the WIDE
    # control — exactly the production shape.
    gate = svc.register_gate(
        s, ver, gate_key="paper_keep", kind="promotion",
        spec={
            "sample": {"treatment": {"metric": "settled_trades", "op": ">=",
                                     "value": 100}},
            "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "control",
                          "op": ">", "value": 0}],
        },
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
    )
    svc.mark_gate_evidence_started(s, gate, at=T0)
    for _ in range(29):
        _trade(s, "w_t", pnl=0.05)
    for _ in range(77):
        _trade(s, "w_c", pnl=0.05)
    s.commit()

    assert _run(s)["written"] == 1
    row = _results(s, gate)[-1]
    assert row.verdict == "HOLD"  # floor 29/100
    # The asymmetry is real in the stored row: floors say 29, clauses say 77.
    assert max(v["n"] for v in row.sample_json.values()) == 29
    assert max(c["n"] for c in row.metrics_json["clauses"]) == 77

    for _ in range(3):  # every subsequent cycle, unchanged evidence
        out = _run(s)
        assert out["written"] == 0, out["written_rows"]
        assert out["skipped_unchanged"] == 1
    assert len(_results(s, gate)) == 1

    # Real progress on the widest clause still records.
    for _ in range(40):
        _trade(s, "w_c", pnl=0.05)
    s.commit()
    assert _run(s)["written"] == 1
    assert len(_results(s, gate)) == 2
