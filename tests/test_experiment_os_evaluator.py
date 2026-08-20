"""The gate evaluator + metric layer (PR 3) — tests target false conclusions.

What must never happen, pinned here:

  * a scope guessed for an ambiguous clause (→ BLOCKED_INTEGRITY, exact problem named);
  * pre-epoch rows counted after an I2 boundary (hard floor);
  * a sample crossing an unresolved platform boundary interpreted (→ BLOCKED_PLATFORM,
    never a convenient timestamp);
  * missing data read as zero (→ BLOCKED_DATA), or an empty mean read as anything;
  * an underpowered sample FAILed (→ HOLD);
  * stop-closed trades dropped from "settled" (the recorded mmsellA1–A3 error);
  * a stale PASS — old epoch, old version, old snapshot, superseded, wrong gate,
    wrong transition, epoch-less — authorizing a promotion.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import evaluator, importer, read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.metrics import (
    METRICS_ENGINE_REVISION,
    MetricScope,
    compute_metric,
    compute_paired_metric,
)
from kalshi_bot.experiment_os.models import ExperimentGate, ExperimentGateResult
from kalshi_bot.models import PaperTrade

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _dt(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _trade(s, tag, *, pnl=0.05, status="settled", at=None, qty=1, ticker=None):
    s.add(
        PaperTrade(
            market_ticker=ticker or f"T-{tag}-{id(object())}",
            strategy=tag, status=status, pnl=pnl, quantity=qty,
            created_at=at or (T0 + timedelta(hours=1)),
        )
    )


def _experiment(s, key="exp-e", arms=(("treatment", "treatment", "t_tag"),
                                      ("control", "control", "c_tag"))):
    """Experiment in PAPER with a frozen version, arms with tags, an open epoch and
    a paper deployment. Returns (exp, ver, epoch)."""
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0
    )
    for arm_key, role, tag in arms:
        svc.add_arm(s, ver, arm_key=arm_key, role=role, strategy_tag=tag)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper-1", stage="PAPER", kind="paper",
        arms={arm_key: tag for arm_key, _, tag in arms},
        started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    return exp, ver, epoch


def _gate(s, ver, spec, *, key="g", kind="promotion", frm="PAPER", to="LIVE_CANARY",
          started=T0):
    gate = svc.register_gate(
        s, ver, gate_key=key, kind=kind, spec=spec, from_state=frm, to_state=to,
        registered_at=started,
    )
    svc.mark_gate_evidence_started(s, gate, at=started)
    return gate


def _raw_gate(s, ver, spec, *, key="legacy", started=T0):
    """A grandfathered gate written straight to the table, bypassing the modern
    registration validation — how an underspecified legacy DB row looks."""
    gate = ExperimentGate(
        version_id=ver.id, gate_key=key, kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY",
        spec_json=spec, spec_hash=svc.canonical_hash(spec),
        registered_at=started, evidence_started_at=started,
        created_at=started,
    )
    s.add(gate)
    s.flush()
    return gate


# ---------------------------------------------------------------------------
# Metric layer: zero vs empty vs missing; the settled-status lesson
# ---------------------------------------------------------------------------


def _scope(tags, start=T0, end=None, arm="treatment"):
    return MetricScope(
        experiment_key="exp-e", version=1, epoch_number=1, arm_key=arm,
        deployment_kind="paper", strategy_tags=tuple(tags), deployment_keys=(),
        window_start=start, window_end=end or (T0 + timedelta(days=30)),
        platform_snapshot_fingerprint="f" * 64,
    )


def test_counts_are_meaningful_zero_and_means_are_undefined(xos_session):
    scope = _scope(("t_tag",))
    n = compute_metric(xos_session, "settled_trades", scope)
    assert (n.value, n.n, n.missing) == (0.0, 0, False)  # zero, not missing
    mean = compute_metric(xos_session, "pnl_cents_per_trade", scope)
    assert mean.value is None and mean.missing is False  # undefined, not zero, not missing
    assert "no settled trades" in mean.reason


def test_unprovided_metric_is_missing_with_reference(xos_session):
    # The example moves as providers land: realizable_cents_per_trade and the
    # live-execution metrics now HAVE canonical providers. The theta tail ratio
    # does not, and carries the same contract.
    mv = compute_metric(
        xos_session, "candidate_rejection_rate_pct", _scope(("t_tag",))
    )
    assert mv.missing is True
    # The example moves as providers land — this one counts a quantity the scan
    # cycle never persists, so it stays unprovided by construction.
    assert "no canonical provider yet" in mv.reason
    assert "never persisted" in mv.reason


def test_settled_includes_stop_closed_trades(xos_session):
    """The recorded mmsellA1–A3 reading error: filtering 'settled' alone drops every
    position the stop actually closed — exactly the trades that measure the stop.
    Parity with the corrected read: status IN (settled, closed_sl, ...)."""
    s = xos_session
    _trade(s, "t_tag", pnl=0.10, status="settled")
    _trade(s, "t_tag", pnl=-0.30, status="closed_sl")  # the stop-closed loser
    _trade(s, "t_tag", pnl=0.02, status="closed_tp")
    _trade(s, "t_tag", pnl=99.0, status="closed_void")  # annulled — censored
    _trade(s, "t_tag", pnl=None, status="open")
    s.commit()
    scope = _scope(("t_tag",))
    assert compute_metric(s, "settled_trades", scope).value == 3.0
    # Hand-computed parity: (0.10 - 0.30 + 0.02) * 100 / 3 = -6.0 cents/trade.
    assert compute_metric(s, "pnl_cents_per_trade", scope).value == -6.0
    assert compute_metric(s, "win_rate_pct", scope).value == pytest.approx(66.67, abs=0.01)
    assert compute_metric(s, "voided_trades", scope).value == 1.0  # visible, not pooled


def test_delta_and_entry_deficit_hand_checked(xos_session):
    s = xos_session
    for _ in range(4):
        _trade(s, "t_tag", pnl=0.05)  # +5c/trade
    for _ in range(5):
        _trade(s, "c_tag", pnl=0.02)  # +2c/trade
    _trade(s, "c_tag", status="open", pnl=None)  # control: 6 entries, 5 settled
    s.commit()
    t, c = _scope(("t_tag",)), _scope(("c_tag",), arm="control")
    delta = compute_paired_metric(s, "delta.pnl_cents_per_trade", t, c)
    assert delta.value == 3.0  # 5 - 2
    assert delta.n == 4  # powered by the thinner side
    deficit = compute_paired_metric(s, "relative_entry_deficit_pct", t, c)
    assert deficit.value == pytest.approx(100 * (1 - 4 / 6), abs=0.01)


# ---------------------------------------------------------------------------
# Addressing: ambiguity is refused, never inferred
# ---------------------------------------------------------------------------


def test_ambiguous_legacy_clause_blocks_with_exact_problem(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s)
    gate = _raw_gate(s, ver, {"pass_all": [
        {"metric": "pnl_cents_per_trade", "op": ">", "value": 0}  # no arm, 2 arms
    ]})
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "BLOCKED_INTEGRITY"
    assert "no arm named" in out.blocking_reasons[0]
    assert "2 arms" in out.blocking_reasons[0]
    assert "register a corrected gate on a new version" in out.explanation


@pytest.mark.parametrize("spec,needle", [
    ({"pass_all": [{"metric": "vibes", "arm": "treatment", "op": ">", "value": 0}]},
     "not in the canonical registry"),
    ({"pass_all": [{"metric": "delta.pnl_cents_per_trade", "arm": "treatment",
                    "op": ">", "value": 0}]},
     "needs an explicit control"),
    ({"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "ghost_arm",
                    "op": ">", "value": 0}]},
     "not declared"),
    ({"pass_all": [{"metric": "delta.pnl_cents_per_trade", "arm": "treatment",
                    "external_control": {"experiment_key": "nope", "arm_key": "x"},
                    "op": ">", "value": 0}]},
     "does not exist"),
    ({"sample": {"ghost": {"metric": "settled_trades", "op": ">=", "value": 1}},
      "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                    "op": ">", "value": 0}]},
     "sample floor names arm"),
])
def test_structural_problems_are_named(xos_session, xos_platform, spec, needle):
    s = xos_session
    exp, ver, epoch = _experiment(s, key=f"exp-{abs(hash(needle)) % 10_000}")
    gate = _raw_gate(s, ver, spec)
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "BLOCKED_INTEGRITY"
    assert any(needle in p for p in out.blocking_reasons), out.blocking_reasons


def test_modern_registration_refuses_what_evaluation_would_block(xos_session, xos_platform):
    """The same ambiguity that BLOCKS a grandfathered gate FAILS validation for a
    new one — at freeze, and at registration on a frozen version."""
    s = xos_session
    exp, ver, epoch = _experiment(s)
    with pytest.raises(svc.GateSpecError, match="scope must be explicit"):
        svc.register_gate(
            s, ver, gate_key="bad", kind="kill",
            spec={"pass_all": [{"metric": "pnl_cents_per_trade", "op": ">", "value": 0}]},
        )
    # And at freeze time for gates registered while drafting:
    exp2 = svc.create_experiment(s, key="exp-freeze-check", origin="operator")
    ver2 = svc.create_experiment_version(s, exp2, hypothesis="h")
    svc.add_arm(s, ver2, arm_key="a", role="treatment")
    svc.add_arm(s, ver2, arm_key="b", role="control")
    svc.register_gate(
        s, ver2, gate_key="bad", kind="kill",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "op": ">", "value": 0}]},
    )
    with pytest.raises(svc.GateSpecError, match="does not resolve"):
        svc.freeze_version(s, ver2)


def test_single_arm_auto_resolution_is_not_inference(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(
        s, key="exp-single", arms=(("only", "control", "only_tag"),)
    )
    gate = _raw_gate(s, ver, {"pass_all": [
        {"metric": "pnl_cents_per_trade", "op": ">", "value": 0}  # no arm: unique referent
    ]})
    _trade(s, "only_tag", pnl=0.05)
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "PASS"
    assert out.clauses[0].scope.endswith("/only")


def test_arm_star_expands_per_arm(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-star")
    gate = _gate(s, ver, {
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "*", "op": ">", "value": 0}],
    })
    for _ in range(3):
        _trade(s, "t_tag", pnl=0.05)
        _trade(s, "c_tag", pnl=-0.05)  # one arm negative
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "HOLD"  # each arm must pass individually
    scopes = {c.scope for c in out.clauses}
    assert len(scopes) == 2  # expanded per arm


# ---------------------------------------------------------------------------
# Verdict semantics
# ---------------------------------------------------------------------------


def test_underpowered_is_hold_even_when_kill_would_trigger(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-hold")
    gate = _gate(s, ver, {
        "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 100}},
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
        "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": "<=", "value": 0}],
    })
    for _ in range(5):
        _trade(s, "t_tag", pnl=-0.50)  # would trip the kill clause
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "HOLD"
    assert "sample floor not met" in out.explanation


def test_fail_beats_pass_once_powered(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-fail")
    gate = _gate(s, ver, {
        "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 3}},
        "pass_all": [{"metric": "win_rate_pct", "arm": "treatment", "op": ">", "value": 0}],
        "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": "<=", "value": 0}],
    })
    for _ in range(4):
        _trade(s, "t_tag", pnl=-0.10)
    _trade(s, "t_tag", pnl=0.30)
    s.commit()
    out = evaluator.evaluate_gate(s, gate)
    assert out.verdict == "FAIL"
    assert "fail condition met" in out.explanation
    s.commit()


def test_missing_metric_blocks_data_not_zero(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-miss")
    gate = _gate(s, ver, {
        "pass_all": [{"metric": "candidate_rejection_rate_pct",
                      "arm": "treatment", "op": ">", "value": 0}],
    })
    for _ in range(200):
        _trade(s, "t_tag", pnl=0.05)
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "BLOCKED_DATA"
    assert "missing is not zero" in out.explanation
    assert "candidate_rejection_rate_pct" in out.blocking_reasons[0]


def test_hold_if_clause_holds(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-holdif")
    gate = _gate(s, ver, {
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
        "hold_if": [{"metric": "voided_trades", "arm": "treatment", "op": ">", "value": 0}],
    })
    _trade(s, "t_tag", pnl=0.10)
    _trade(s, "t_tag", status="closed_void", pnl=0.0)
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "HOLD"
    assert "hold condition met" in out.explanation


# ---------------------------------------------------------------------------
# Evidence windows: hard epoch floors, recorded gate floors, closed epochs
# ---------------------------------------------------------------------------


def test_pre_epoch_rows_never_count(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-floor")
    gate = _gate(s, ver, {
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
    })
    for _ in range(500):
        _trade(s, "t_tag", pnl=-1.0, at=T0 - timedelta(days=3))  # pre-epoch poison
    for _ in range(3):
        _trade(s, "t_tag", pnl=0.05, at=T0 + timedelta(hours=2))
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "PASS"
    assert out.clauses[0].n == 3  # the 500 pre-epoch rows are invisible


def test_gate_floor_tighter_than_epoch_wins(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-tight")
    floor = T0 + timedelta(days=2)
    gate = _gate(s, ver, {
        "pass_all": [{"metric": "settled_trades", "arm": "treatment",
                      "op": ">=", "value": 2}],
    }, started=floor)
    _trade(s, "t_tag", pnl=0.05, at=T0 + timedelta(hours=1))  # post-epoch, pre-floor
    _trade(s, "t_tag", pnl=0.05, at=floor + timedelta(hours=1))
    _trade(s, "t_tag", pnl=0.05, at=floor + timedelta(hours=2))
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.evidence_start == floor  # max(epoch.start, gate floor)
    assert out.clauses[0].value == 2.0  # the pre-floor trade is excluded
    assert out.verdict == "PASS"


def test_closed_epoch_clips_the_window_end(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-clip")
    gate = _gate(s, ver, {
        "pass_all": [{"metric": "settled_trades", "arm": "treatment",
                      "op": ">=", "value": 1}],
    })
    end = T0 + timedelta(days=1)
    _trade(s, "t_tag", pnl=0.05, at=T0 + timedelta(hours=1))
    _trade(s, "t_tag", pnl=-9.0, at=end + timedelta(hours=1))  # after the epoch closed
    svc.close_epoch(s, epoch, ended_at=end)
    s.commit()
    out = evaluator.evaluate_gate(s, gate, epoch=epoch, persist=False)
    assert out.evidence_end == end
    assert out.clauses[0].value == 1.0


# ---------------------------------------------------------------------------
# Platform blocking (spec §17.4): no convenient timestamps, ever
# ---------------------------------------------------------------------------


def test_unestablished_boundary_blocks(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-plat")
    gate = _gate(s, ver, {
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
    })
    for _ in range(3):
        _trade(s, "t_tag", pnl=0.05)
    svc.register_platform_revision(
        s, "FEE_MODEL", version="v2", activate=True, boundary_unknown=True,
        force=True, force_reason="test: simulate an unclassified platform change",
    )
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "BLOCKED_PLATFORM"
    assert "UNESTABLISHED" in out.blocking_reasons[0]
    assert "FEE_MODEL" in out.blocking_reasons[0]


def test_known_mid_window_boundary_blocks_until_new_epoch(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-plat2")
    gate = _gate(s, ver, {
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
    })
    for _ in range(3):
        _trade(s, "t_tag", pnl=0.05)
    boundary = T0 + timedelta(days=1)
    svc.register_platform_revision(
        s, "MARKET_TAXONOMY", version="v2", activate=True, activated_at=boundary,
        force=True, force_reason="test: simulate an unclassified platform change",
    )
    # This test isolates the WINDOW-vs-boundary mechanics — resolve the forced-
    # activation integrity event so it does not shadow the platform verdicts.
    from sqlalchemy import select

    from kalshi_bot.experiment_os.models import ExperimentIntegrityEvent

    for ev in s.scalars(select(ExperimentIntegrityEvent).where(
        ExperimentIntegrityEvent.kind == "PLATFORM_ACTIVATION_FORCED"
    )):
        ev.resolved_at = _dt(2026, 8, 2)
        ev.resolution = "test fixture: classified out-of-band"
    s.commit()
    # Window spans the boundary → blocked.
    out = evaluator.evaluate_gate(
        s, gate, window_end=boundary + timedelta(days=1), persist=False
    )
    assert out.verdict == "BLOCKED_PLATFORM"
    assert "spans a platform boundary" in out.blocking_reasons[0]
    # An explicit historical window that PRE-dates the change is comparable.
    out2 = evaluator.evaluate_gate(
        s, gate, window_end=boundary - timedelta(hours=1), persist=False
    )
    assert out2.verdict == "PASS"
    # The remedy: close the stale epoch, open a fresh one under the new snapshot,
    # and re-register the deployment in it (deployments are per-epoch).
    svc.close_epoch(s, epoch, ended_at=boundary)
    epoch2 = svc.open_epoch(
        s, ver, reason="taxonomy change", impact_class="I2",
        started_at=boundary,
    )
    svc.register_deployment(
        s, epoch2, deployment_key="exp-plat2-paper-2", stage="PAPER", kind="paper",
        arms={"treatment": "t_tag", "control": "c_tag"}, started_at=boundary,
    )
    for _ in range(2):
        _trade(s, "t_tag", pnl=0.07, at=boundary + timedelta(hours=2))
    s.commit()
    out3 = evaluator.evaluate_gate(s, gate, epoch=epoch2, persist=False)
    assert out3.verdict == "PASS"
    assert out3.clauses[0].n == 2  # pre-boundary trades stay in the old epoch


def test_cross_snapshot_external_control_blocks(xos_session, xos_platform):
    """A delta against an external control pinned to a DIFFERENT snapshot would
    silently pool incomparable evidence — blocked instead."""
    s = xos_session
    _experiment(s, key="ext-control", arms=(("armx", "control", "x_tag"),))
    exp, ver, epoch = _experiment(s, key="exp-xsnap")
    gate = _gate(s, ver, {
        "pass_all": [{
            "metric": "delta.pnl_cents_per_trade", "arm": "treatment",
            "external_control": {"experiment_key": "ext-control", "arm_key": "armx"},
            "op": ">", "value": 0,
        }],
    })
    # Platform moves; the MAIN experiment gets a fresh epoch under the new
    # snapshot, the external control's epoch stays pinned to the old one.
    boundary = T0 + timedelta(days=1)
    svc.register_platform_revision(
        s, "FILL_MODEL", version="v2", activate=True, activated_at=boundary,
        force=True, force_reason="test: simulate an unclassified platform change",
    )
    svc.close_epoch(s, epoch, ended_at=boundary)
    epoch2 = svc.open_epoch(s, ver, reason="fill model", impact_class="I2",
                            started_at=boundary)
    _trade(s, "t_tag", pnl=0.05, at=boundary + timedelta(hours=1))
    _trade(s, "x_tag", pnl=0.01, at=boundary + timedelta(hours=1))
    s.commit()
    out = evaluator.evaluate_gate(s, gate, epoch=epoch2, persist=False)
    assert out.verdict == "BLOCKED_PLATFORM"
    assert any("different platform snapshot" in r for r in out.blocking_reasons)


def test_unresolved_integrity_event_blocks(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-integ")
    gate = _gate(s, ver, {
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
    })
    _trade(s, "t_tag", pnl=0.05)
    ev = svc.record_integrity_event(
        s, exp, kind="HELD_CONSTANT_CHANGED",
        description="scanner depth changed mid-experiment",
    )
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "BLOCKED_INTEGRITY"
    assert "HELD_CONSTANT_CHANGED" in out.blocking_reasons[0]
    ev.resolved_at = _dt(2026, 8, 2)
    ev.resolution = "classified I2; fresh epoch opened"
    s.commit()
    assert evaluator.evaluate_gate(s, gate, persist=False).verdict == "PASS"


# ---------------------------------------------------------------------------
# Persistence: immutable system results with full lineage
# ---------------------------------------------------------------------------


def test_evaluation_persists_full_lineage_and_dry_run_writes_nothing(
    xos_session, xos_platform
):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-persist")
    gate = _gate(s, ver, {
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
    })
    _trade(s, "t_tag", pnl=0.05)
    s.commit()
    before = len(read.gate_results_for(s, gate))
    dry = evaluator.evaluate_gate(s, gate, persist=False)
    assert dry.result_id is None
    assert len(read.gate_results_for(s, gate)) == before

    out = evaluator.evaluate_gate(s, gate)
    s.commit()
    row = s.get(ExperimentGateResult, out.result_id)
    assert row.computed_by == "system"
    assert row.verdict == "PASS"
    assert row.epoch_id == epoch.id
    assert row.platform_snapshot_id == epoch.platform_snapshot_id
    assert row.metric_revision == METRICS_ENGINE_REVISION
    assert row.metrics_json["clauses"][0]["value"] == 5.0
    assert row.evidence_start is not None and row.evidence_end is not None
    # Immutable like every gate result.
    row.verdict = "FAIL"
    with pytest.raises(svc.ImmutableRecord):
        s.flush()
    s.rollback()


def test_unstarted_gate_and_missing_epoch_refuse_to_evaluate(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-refuse")
    gate = svc.register_gate(
        s, ver, gate_key="unstarted", kind="kill",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                            "op": ">", "value": 0}]},
    )
    with pytest.raises(svc.ExperimentOsError, match="evidence has not begun"):
        evaluator.evaluate_gate(s, gate, persist=False)


# ---------------------------------------------------------------------------
# Adversarial: a PASS is not a reusable permission slip
# ---------------------------------------------------------------------------


@pytest.fixture
def promotable(xos_session, xos_platform):
    """PAPER experiment with a powered, passing promotion gate and a fresh PASS."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-promote")
    gate = _gate(s, ver, {
        "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 3}},
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
        "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": "<=", "value": 0}],
    }, key="paper_to_live_canary")
    for _ in range(5):
        _trade(s, "t_tag", pnl=0.05)
    s.commit()
    out = evaluator.evaluate_gate(s, gate)
    s.commit()
    assert out.verdict == "PASS"
    return exp, ver, epoch, gate, s.get(ExperimentGateResult, out.result_id)


def test_fresh_pass_promotes(xos_session, promotable):
    exp, ver, epoch, gate, result = promotable
    svc.transition_experiment(
        xos_session, exp, "LIVE_CANARY",
        actor="operator", approved_by="operator", gate_result=result,
    )
    assert exp.state == "LIVE_CANARY"


def test_stale_epoch_pass_never_promotes(xos_session, promotable):
    s = xos_session
    exp, ver, epoch, gate, result = promotable
    svc.close_epoch(s, epoch, ended_at=T0 + timedelta(days=10))
    svc.open_epoch(s, ver, reason="fresh boundary", started_at=T0 + timedelta(days=10))
    with pytest.raises(svc.ExperimentOsError, match="stale-epoch evidence never promotes"):
        svc.transition_experiment(
            s, exp, "LIVE_CANARY",
            actor="operator", approved_by="operator", gate_result=result,
        )


def test_previous_version_pass_never_promotes(xos_session, promotable):
    s = xos_session
    exp, ver, epoch, gate, result = promotable
    v2 = svc.create_experiment_version(
        s, exp, hypothesis="h2", change_reason="treatment redefined"
    )
    svc.add_arm(s, v2, arm_key="treatment", role="treatment", strategy_tag="t_tag")
    svc.add_arm(s, v2, arm_key="control", role="control", strategy_tag="c_tag")
    svc.freeze_version(s, v2)
    with pytest.raises(svc.ExperimentOsError, match="CURRENT version"):
        svc.transition_experiment(
            s, exp, "LIVE_CANARY",
            actor="operator", approved_by="operator", gate_result=result,
        )


def test_wrong_gate_shape_never_promotes(xos_session, xos_platform):
    """A kill gate's PASS, or a PAPER→LIVE_CANARY PASS reused for
    LIVE_CANARY→PRODUCTION, is refused by gate shape."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="exp-shape")
    kill = _gate(s, ver, {
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
    }, key="kill_gate", kind="kill", frm=None, to=None)
    _trade(s, "t_tag", pnl=0.05)
    s.commit()
    kill_pass = evaluator.evaluate_gate(s, kill)
    s.commit()
    with pytest.raises(svc.ExperimentOsError, match="not the promotion gate"):
        svc.transition_experiment(
            s, exp, "LIVE_CANARY", actor="operator", approved_by="operator",
            gate_result=s.get(ExperimentGateResult, kill_pass.result_id),
        )
    # The right-shaped gate promotes; its PASS then cannot ALSO buy PRODUCTION.
    promo = _gate(s, ver, {
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
    }, key="paper_to_live_canary")
    promo_pass = evaluator.evaluate_gate(s, promo)
    s.commit()
    result = s.get(ExperimentGateResult, promo_pass.result_id)
    svc.transition_experiment(
        s, exp, "LIVE_CANARY", actor="operator", approved_by="operator",
        gate_result=result,
    )
    with pytest.raises(svc.ExperimentOsError, match="not the promotion gate"):
        svc.transition_experiment(
            s, exp, "PRODUCTION", actor="operator", approved_by="operator",
            gate_result=result,
        )


def test_superseded_pass_never_promotes(xos_session, promotable):
    s = xos_session
    exp, ver, epoch, gate, result = promotable
    for _ in range(50):
        _trade(s, "t_tag", pnl=-0.50)  # the edge died
    s.commit()
    newer = evaluator.evaluate_gate(s, gate)
    s.commit()
    assert newer.verdict == "FAIL"
    with pytest.raises(svc.ExperimentOsError, match="superseded"):
        svc.transition_experiment(
            s, exp, "LIVE_CANARY",
            actor="operator", approved_by="operator", gate_result=result,
        )


def test_platform_change_stales_a_pass(xos_session, promotable):
    s = xos_session
    exp, ver, epoch, gate, result = promotable
    svc.register_platform_revision(
        s, "FEE_MODEL", version="v2", activate=True,
        activated_at=T0 + timedelta(days=5),
        force=True, force_reason="test: simulate an unclassified platform change",
    )
    s.commit()
    with pytest.raises(svc.ExperimentOsError, match="platform has changed"):
        svc.transition_experiment(
            s, exp, "LIVE_CANARY",
            actor="operator", approved_by="operator", gate_result=result,
        )


def test_epochless_manual_pass_never_promotes(xos_session, promotable):
    s = xos_session
    exp, ver, epoch, gate, result = promotable
    loose = svc.record_gate_result(s, gate, verdict="PASS", computed_by="manual")
    s.commit()
    with pytest.raises(svc.ExperimentOsError, match="epoch-less PASS"):
        svc.transition_experiment(
            s, exp, "LIVE_CANARY",
            actor="operator", approved_by="operator", gate_result=loose,
        )


# ---------------------------------------------------------------------------
# The migrated shapes, end to end
# ---------------------------------------------------------------------------


@pytest.fixture
def migrated(xos_session):
    importer.import_legacy(xos_session, now=_dt(2026, 8, 15, 16, 0))
    xos_session.commit()
    return _dt(2026, 8, 15, 16, 0)


def _gate_of(s, key, gate_key):
    exp = read.get_experiment(s, key)
    ver = read.latest_version(s, exp)
    return next(g for g in read.gates_for(s, ver) if g.gate_key == gate_key)


def test_freeze_holds_then_passes_on_evidence(xos_session, migrated):
    s = xos_session
    gate = _gate_of(s, "freeze-dark-window-pin", "paper_to_live_canary")
    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "HOLD"  # floors 0/150
    t0 = migrated + timedelta(hours=1)
    for _ in range(160):
        _trade(s, "freeze1", pnl=0.05, at=t0)
        _trade(s, "freeze3", pnl=0.001, at=t0)
    s.commit()
    out = evaluator.evaluate_gate(s, gate, window_end=migrated + timedelta(days=30))
    s.commit()
    assert out.verdict == "PASS"  # +5c abs, +4.9c over the control ≥ 3c


def test_freeze_kill_clause_fires_when_control_wins(xos_session, migrated):
    """freeze1 ≤ freeze3 is the family's pre-registered death: tfav on a new venue."""
    s = xos_session
    gate = _gate_of(s, "freeze-dark-window-pin", "paper_to_live_canary")
    t0 = migrated + timedelta(hours=1)
    for _ in range(160):
        _trade(s, "freeze1", pnl=0.01, at=t0)
        _trade(s, "freeze3", pnl=0.02, at=t0)  # the control wins
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False,
                                  window_end=migrated + timedelta(days=30))
    assert out.verdict == "FAIL"
    assert "delta.pnl_cents_per_trade" in out.explanation


def test_a4_external_control_full_evaluation(xos_session, migrated):
    """mmsellA4 vs the mmsell10 book — the cross-experiment control as an explicit
    reference, both clauses hand-checkable."""
    s = xos_session
    gate = _gate_of(s, "mmsell-anchor-vol-entry", "paper_keep")
    t0 = migrated + timedelta(hours=1)
    for _ in range(120):
        _trade(s, "mmsellA4", pnl=0.05, at=t0)  # +5c/trade, 120 entries
    for _ in range(200):
        _trade(s, "mmsell10", pnl=0.02, at=t0)  # +2c/trade, 200 entries
    s.commit()
    out = evaluator.evaluate_gate(s, gate, window_end=migrated + timedelta(days=30))
    s.commit()
    # delta = +3.0 ≥ 1.0; deficit = 100*(1-120/200) = 40% ≥ 15; floor 120 ≥ 100.
    assert out.verdict == "PASS"
    by_metric = {c.clause["metric"]: c for c in out.clauses if c.list_name == "pass_all"}
    assert by_metric["delta.pnl_cents_per_trade"].value == 3.0
    assert by_metric["relative_entry_deficit_pct"].value == 40.0
    assert "mmsell-price-ceiling" in by_metric["delta.pnl_cents_per_trade"].scope


def test_a4_inert_gate_fails(xos_session, migrated):
    """rejection <5% = the vol gate is inert → the book is a duplicate → KILL."""
    s = xos_session
    gate = _gate_of(s, "mmsell-anchor-vol-entry", "paper_keep")
    t0 = migrated + timedelta(hours=1)
    for _ in range(100):
        _trade(s, "mmsellA4", pnl=0.05, at=t0)
    for _ in range(102):
        _trade(s, "mmsell10", pnl=0.02, at=t0)  # deficit ~2% < 5%
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False,
                                  window_end=migrated + timedelta(days=30))
    assert out.verdict == "FAIL"
    assert "relative_entry_deficit_pct" in out.explanation


def test_tmmsell_blocked_on_realizable_not_guessed(xos_session, migrated):
    """The Tmmsell gate's realizable clause has no canonical provider yet — the
    whole gate is BLOCKED_DATA naming it, never evaluated on the computable
    subset as if the clause were optional."""
    s = xos_session
    gate = _gate_of(s, "mmsell-type-tight", "paper_keep")
    t0 = migrated + timedelta(hours=1)
    for tag in ("Tmmsell1", "Tmmsell2", "Tmmsell5", "Tmmsell6"):
        for _ in range(120):
            _trade(s, tag, pnl=0.05, at=t0)
    for _ in range(200):
        _trade(s, "mmsell10", pnl=0.02, at=t0)
    s.commit()
    out = evaluator.evaluate_gate(s, gate, persist=False,
                                  window_end=migrated + timedelta(days=30))
    assert out.verdict == "BLOCKED_DATA"
    assert any("realizable_cents_per_trade" in r for r in out.blocking_reasons)


def test_live_canaries_block_on_unprovided_live_metrics(xos_session, migrated):
    s = xos_session
    for key, gate_key in (
        ("mmsell-scheduled-settle-live", "live_canary_keep"),
        ("theta4-fat-tail", "live_canary_keep"),
    ):
        out = evaluator.evaluate_gate(s, _gate_of(s, key, gate_key), persist=False)
        assert out.verdict == "BLOCKED_DATA", key
    # A5 no longer blocks: clean_pairs / pair_win_rate_95lb_pct have canonical
    # providers, so the gate renders a real verdict. With no paired evidence yet
    # that verdict is HOLD on the sample floor — underpowered, not un-evaluatable.
    a5 = evaluator.evaluate_gate(
        s, _gate_of(s, "mmsell-anchor-strangle", "paper_keep"), persist=False
    )
    assert a5.verdict == "HOLD"
    assert not a5.blocking_reasons


def test_scoreboard_reads_the_same_state(xos_session, migrated):
    s = xos_session
    t0 = migrated + timedelta(hours=1)
    for _ in range(7):
        _trade(s, "freeze1", pnl=0.05, at=t0)
    s.commit()
    exp = read.get_experiment(s, "freeze-dark-window-pin")
    gate = _gate_of(s, "freeze-dark-window-pin", "paper_to_live_canary")
    evaluator.evaluate_gate(s, gate)
    s.commit()
    board = read.experiment_scoreboard(s, exp)
    f1 = next(a for a in board["arms"] if a["arm"] == "freeze1")
    assert f1["settled_trades"] == 7.0
    assert f1["pnl_cents_per_trade"] == 5.0
    g = next(g for g in board["gates"] if g["gate_key"] == "paper_to_live_canary")
    assert g["latest_result"]["verdict"] == "HOLD"  # floors not met at n=7
    assert g["latest_result"]["computed_by"] == "system"
    # Legacy stubs read cleanly too.
    pin = read.experiment_scoreboard(s, read.get_experiment(s, "pin15"))
    assert pin["note"] == "no version (minimal legacy stub)"
