"""The mmsell10 capacity successor: what it must refuse, and what it must carry.

`arm_live_canary` refuses unless the experiment is in PAPER, and LIVE_CANARY →
PAPER is an illegal rollback. So an experiment already in LIVE_CANARY has no
sanctioned way to re-arm at a new risk envelope, and the lifecycle's own remedy
is a successor referencing the retired predecessor. Both rules are load-bearing —
the PAPER guard is what stopped the 2026-08-15 inherited-state failure — so what
these tests pin is that the successor path does NOT become a way around either.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kalshi_bot.experiment_os import canary_mmsell10 as v2
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import successor_mmsell10_capacity as cap
from kalshi_bot.experiment_os.enforcement import EnforcementMode
from kalshi_bot.experiment_os.lifecycle import ArmRole, LifecycleState

UTC = timezone.utc
T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
#: A later instant, so "the successor's epoch starts at registration" is a
#: distinguishable claim rather than one that happens to coincide with T0.
T1 = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)


# --- the contract, checkable without a database ----------------------------


def test_the_keep_gate_is_the_predecessors_object_not_a_retyped_copy():
    """A successor that quietly relaxed its own stops would defeat the entire
    point of pre-registration. Importing the spec instead of retyping it makes
    "carried verbatim" a property of the code rather than a claim in a docstring:
    a loosened threshold is not a one-character edit away, it is impossible.
    """
    assert cap.KEEP_GATE_SPEC is v2.KEEP_GATE_SPEC


def test_the_coverage_clause_is_carried_over_unchanged():
    """D7's weaker option was to lower this until the book could step over it. A
    15% mirror is not an execution control whatever the threshold says, so the
    clause stays at <50 and the CONSTRAINT moves instead (twin cap 20 → 250)."""
    holds = cap.KEEP_GATE_SPEC["hold_if"]
    coverage = [c for c in holds if c["metric"] == "twin_mirror_coverage_pct"]

    assert coverage, "the coverage clause must survive into the successor"
    assert coverage[0]["value"] == 50.0
    assert coverage[0]["op"] == "<"


def test_capacity_doubles_and_book_exposure_follows_it():
    """The cap is the independent variable; book exposure is cap x one clip, so
    it must move with it or the envelope silently under-declares real risk."""
    env = cap.RISK_ENVELOPE

    assert env["max_open_positions"] == 40
    assert env["settings"]["MMSELL_LIVE_MAX_OPEN_POSITIONS"] == "40"
    assert env["max_book_exposure_usd"] == pytest.approx(39.60)


def test_loss_budgets_are_NOT_doubled():
    """Doubling capacity is the hypothesis. Doubling how much we are willing to
    lose testing it is a separate decision nobody made — and holding these flat
    makes the stop stricter per contract, which is the safe way to be wrong."""
    env = cap.RISK_ENVELOPE

    assert env["daily_realized_loss_stop_usd"] == v2.RISK_ENVELOPE[
        "daily_realized_loss_stop_usd"]
    assert env["total_canary_loss_budget_usd"] == v2.RISK_ENVELOPE[
        "total_canary_loss_budget_usd"]


def test_per_order_and_per_market_limits_are_untouched():
    """This experiment buys MORE OF THE SAME distribution. Anything that changed
    the shape of an individual trade would confound the capacity question."""
    for key in ("contracts_per_order", "max_order_dollars",
                "max_market_exposure_usd", "max_event_rungs",
                "max_event_exposure_usd", "entry_price_offset_cents",
                "order_timeout_seconds"):
        assert cap.RISK_ENVELOPE[key] == v2.RISK_ENVELOPE[key], key


def test_the_twin_cap_is_sized_from_turnover_not_from_a_ratio_to_live():
    """The measurement that changed my mind: live entered 188.6 markets/day to
    the twin's 21.3 (11.3%), matching the 14.7% coverage actually read. Live's
    unfilled orders recycle a slot every 4h; the twin assumes fill and holds to
    settlement. A twin at 2x live's 40 would still read ~23% and re-break the
    gate — which is exactly the D7 mistake repeating one Version later.
    """
    twin = int(cap.RISK_ENVELOPE["settings"]["LIVE_PAPER_TWIN_MAX_OPEN_POSITIONS"])
    live = cap.RISK_ENVELOPE["max_open_positions"]

    assert twin >= 5 * live, (
        "a twin sized as a small multiple of live is bound by settlement "
        "turnover, not by slots, and cannot reach 50% coverage"
    )


def test_live_and_twin_tags_are_fresh_and_distinct_from_the_predecessors():
    """`arm_live_canary`'s no-inherited-state rule exists because of 2026-08-15,
    where mmsell10 armed onto a tag with 87 pre-existing paper positions."""
    assert cap.LIVE_TAG != v2.LIVE_TAG
    assert cap.TWIN_TAG != v2.TWIN_TAG
    assert cap.LIVE_TAG != cap.TWIN_TAG
    assert cap.PAPER_TAG not in (cap.LIVE_TAG, cap.TWIN_TAG)


def test_the_paper_control_tag_is_carried_over_deliberately():
    """The paper book is the CONTROL, not the canary, so history on it is wanted.
    Handing it to the successor at the same instant the predecessor's deployment
    ends is what stops the tag losing its arm — the XOS-000011 blackout shape."""
    assert cap.PAPER_TAG == v2.PAPER_TAG == "mmsell10"


def test_the_activation_settings_all_clear_the_ops_allowlist():
    """A package whose activation the env channel refuses halfway through leaves
    an operator with a half-applied write. Fail here instead."""
    from scripts import railway_env

    for name in cap.RISK_ENVELOPE["settings"]:
        assert name in railway_env.ALLOWED_VARS, name


# --- the precondition that protects the predecessor's evidence -------------


def test_a_draining_live_book_does_NOT_block_the_successor(monkeypatch):
    """The old book winds down BESIDE the new one, on separate tags, deployments
    and epochs. An earlier draft of this package ended the predecessor's live
    deployment too and therefore had to refuse until it fully drained — idling
    the new book for days for no safety gain. Only the PAPER handover is
    blocking, and a paper deployment has nothing to drain.
    """
    seen = {}

    class _Pred:
        id = 7

    class _Live:
        id, kind, ended_at = 1, "live", None
        deployment_key = "mmsell-ceiling-live-1"

    class _Session:
        def scalars(self, *a, **k):
            return type("R", (), {"all": staticmethod(lambda: [_Live()])})()

    monkeypatch.setattr(cap, "get_experiment",
                        lambda s, key: _Pred() if key == cap.PREDECESSOR_KEY else None)
    monkeypatch.setattr(cap, "_epoch_experiment_id", lambda s, d: _Pred.id)
    monkeypatch.setattr(cap, "_tags_of", lambda s, d: ["Cmmsell10"])

    import kalshi_bot.repository as repo
    monkeypatch.setattr(repo, "count_live_book_open", lambda s, tag: 20)
    monkeypatch.setattr(svc, "end_deployment",
                        lambda *a, **k: seen.setdefault("ended", []).append(a))
    # Stop after the guard: everything past it needs a real database.
    monkeypatch.setattr(svc, "create_experiment",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached")))

    with pytest.raises(RuntimeError, match="reached"):
        cap.register(_Session(), actor="cal", now=T0)

    assert "ended" not in seen, (
        "a live deployment holding positions must be LEFT OPEN so its "
        "settlements keep recording, not ended"
    )


def test_ending_a_paper_deployment_whose_tag_holds_live_positions_is_refused(
    monkeypatch,
):
    """The guard scopes to what is actually being ended. Ending any deployment
    leaves its tag without an arm, so settlements could not be RECORDED (the
    XOS-000011 shape) and the evidence would be wrong."""
    class _Pred:
        id = 7

    class _Paper:
        id, kind, ended_at = 2, "paper", None
        deployment_key = "mmsell-ceiling-paper-2-e2"

    class _Session:
        def scalars(self, *a, **k):
            return type("R", (), {"all": staticmethod(lambda: [_Paper()])})()

    monkeypatch.setattr(cap, "get_experiment",
                        lambda s, key: _Pred() if key == cap.PREDECESSOR_KEY else None)
    monkeypatch.setattr(cap, "_epoch_experiment_id", lambda s, d: _Pred.id)
    monkeypatch.setattr(cap, "_tags_of", lambda s, d: ["mmsell10"])

    import kalshi_bot.repository as repo
    monkeypatch.setattr(repo, "count_live_book_open", lambda s, tag: 3)

    ended = []
    monkeypatch.setattr(svc, "end_deployment", lambda *a, **k: ended.append(a))

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.register(_Session(), actor="cal", now=T0)

    assert "3 open live position" in str(exc.value)
    assert not ended, "must refuse BEFORE ending anything"


def test_register_refuses_to_run_twice(monkeypatch):
    """Re-running must not fork the lineage into two successors."""
    monkeypatch.setattr(cap, "get_experiment", lambda s, key: object())

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.register(object(), actor="cal", now=T0)

    assert "already exists" in str(exc.value)


# --- the package must be REACHABLE, not just correct ------------------------
#
# The contract, the envelope and the gates were all right and the package was
# still unrunnable: it had no `arm` and was never registered with the command
# transport, so neither REGISTER_PACKAGE nor ARM_CANARY could reach it. A
# package nothing can invoke is indistinguishable from one that does not exist,
# and "correct but unreachable" is not a state a review catches by reading the
# module.


def test_the_package_is_registered_with_the_command_transport():
    from kalshi_bot.experiment_os.experiment_commands import _packages

    pkg = _packages().get("mmsell10-capacity-successor")

    assert pkg is not None, "REGISTER_PACKAGE cannot reach an unregistered package"
    assert pkg.experiment_key == cap.SUCCESSOR_KEY


def test_the_package_exposes_both_register_and_arm():
    """Registering the contract and putting money behind it are two acts. Both
    have to be callable, and ARM_CANARY aimed at a package with no `arm` has
    nothing to call."""
    from kalshi_bot.experiment_os.experiment_commands import _packages

    pkg = _packages()["mmsell10-capacity-successor"]

    assert pkg.register is cap.register
    assert pkg.arm is cap.arm


def test_every_declared_activation_var_clears_the_ops_allowlist():
    """A package whose activation the env channel refuses halfway through leaves
    an operator with a write already submitted — the #266 defect class."""
    from kalshi_bot.experiment_os.experiment_commands import _packages
    from scripts import railway_env

    pkg = _packages()["mmsell10-capacity-successor"]

    assert pkg.activation_vars, "the activation step must declare what it sets"
    for name in pkg.activation_vars:
        assert name in railway_env.ALLOWED_VARS, name


def test_arm_refuses_before_the_successor_is_registered(monkeypatch):
    """ARM_CANARY on an unregistered contract must fail loudly, not create one."""
    monkeypatch.setattr(cap, "get_experiment", lambda s, key: None)

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.arm(object(), approved_by="cal")

    assert "REGISTER_PACKAGE first" in str(exc.value)


# --- register() against an actual database ----------------------------------
#
# Every test above this line checks `register` by reading its inputs or by
# monkeypatching its collaborators away, and all sixteen of them passed while
# `register` could not run AT ALL: it called `create_experiment_version` with
# `risk_json=` (the parameter is `risk`) and `actor=` (there is no such
# parameter), and it froze a single-arm version without declaring a control
# exemption. Three hard failures, none reachable from a test that never opens a
# session. A package whose write path is only ever exercised through mocks has
# not been tested; it has been described.


def _predecessor_in_paper(session):
    """The bare shape `register` needs to find: mmsell-price-ceiling with an
    open paper deployment carrying the mmsell10 control tag."""
    exp = svc.create_experiment(
        session, key=cap.PREDECESSOR_KEY, origin="operator",
        title="predecessor", family="maker",
        hypothesis="h", mechanism="m", falsification="f", actor="t", now=T0,
    )
    version = svc.create_experiment_version(
        session, exp, independent_variable="entry-price ceiling",
        risk={"max_open_positions": 20}, control_required=False,
        control_exemption_reason="the execution control is the paper twin",
        now=T0,
    )
    svc.add_arm(
        session, version, arm_key=cap.ARM_KEY, role=ArmRole.TREATMENT,
        description="d", params={"lo": 5, "hi": 10, "maxyes": 7},
        strategy_tag=cap.PAPER_TAG,
    )
    svc.freeze_version(session, version, now=T0)
    for state in (LifecycleState.PROBE, LifecycleState.PAPER):
        svc.transition_experiment(session, exp, state, actor="t",
                                  reason="r", occurred_at=T0)
    epoch = svc.open_epoch(session, version, reason="e", started_at=T0)
    svc.register_deployment(
        session, epoch, deployment_key="pred-paper-1",
        stage=LifecycleState.PAPER, kind="paper",
        arms={cap.ARM_KEY: cap.PAPER_TAG}, started_at=T0,
    )
    return exp


def test_register_runs_end_to_end_against_a_database(xos_session, xos_platform):
    """The test the other sixteen could not be: open a session, call `register`,
    and read back what it actually wrote."""
    _predecessor_in_paper(xos_session)

    out = cap.register(xos_session, actor="claude-code", now=T1)

    successor = out["successor"]
    version = out["version"]
    assert successor.key == cap.SUCCESSOR_KEY
    assert successor.state == LifecycleState.PAPER.value
    assert version.frozen_at is not None
    # `arm_live_canary` REFUSES a version without a risk envelope, so this
    # single assertion is the difference between an armable contract and a dead
    # one — and it is what `risk_json=` silently failed to produce.
    assert version.risk_json == cap.RISK_ENVELOPE
    assert out["paper_deployment"].deployment_key == cap.PAPER_DEPLOYMENT_KEY
    assert out["ended_deployments"] == ["pred-paper-1"]


def test_arming_straight_after_registration_is_refused_for_want_of_EVIDENCE(
    xos_session, xos_platform, monkeypatch
):
    """Registering and arming are not one act, and not only by convention.

    The successor's epoch opens at registration, and the evaluator floors every
    evidence window at `max(epoch.started_at, gate.evidence_started_at)`. So a
    freshly registered successor has a zero-width window, its promotion metric is
    undefined, and `arm_live_canary` — which re-evaluates the gate SYNCHRONOUSLY
    under enforcement — refuses. The successor must earn its own paper evidence.
    That is the pre-registration working, not an obstacle to route around: the
    floor is what stops a successor from inheriting a predecessor's numbers
    across an epoch boundary the platform declared non-poolable.
    """
    monkeypatch.setattr(
        "kalshi_bot.experiment_os.enforcement.current_mode",
        lambda s: EnforcementMode.NEW_ONLY,
    )
    _predecessor_in_paper(xos_session)
    cap.register(xos_session, actor="claude-code", now=T1)

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.arm(xos_session, approved_by="50thycal", actor="claude-code")

    message = str(exc.value)
    assert "paper_to_live_canary" in message
    assert "not PASS" in message
    assert "no settled trades in window" in message


def test_the_evidence_window_starts_at_registration_not_earlier(
    xos_session, xos_platform
):
    """Pinned because the docstring once claimed the opposite. `register` passes
    `evidence_started_at or at`, so the DEFAULT is registration time — and even
    an explicit earlier value could not take effect, because the evaluator floors
    the window at the successor's own epoch start. Anyone reading "it defaults to
    the predecessor's boundary" would plan an arming that cannot happen."""
    _predecessor_in_paper(xos_session)

    out = cap.register(xos_session, actor="claude-code", now=T1)

    assert out["evidence_started_at"] == T1
    assert out["promotion_gate"].evidence_started_at == T1
    assert out["epoch"].started_at == T1


# --- every gate metric must exist in the canonical registry -----------------
#
# Registering v1 with `promotion_sample_floor=150` produced a gate the evaluator
# could never pass: the floor clause named `paper_settled_contracts`, which is
# not a registered metric (the paper-scope name is `settled_contracts`), so the
# promotion gate came back BLOCKED_INTEGRITY rather than HOLD. Gates are frozen
# and immutable, so that mistake cost a whole Version to correct.
#
# `promotion_gate_spec(None)` was exercised by every other test and is fine. The
# floor is a BRANCH nothing had ever taken, and a metric name is exactly the kind
# of typo that reads correctly. So assert against the registry, not against a
# remembered spelling — and assert it for every clause of both gates.


def _metric_names(spec: dict):
    """Every metric a gate spec names, wherever it appears in the structure."""
    found = []
    for key, clauses in spec.items():
        if key in ("description", "notes"):
            continue
        if isinstance(clauses, dict):
            clauses = list(clauses.values())
        if not isinstance(clauses, list):
            continue
        for clause in clauses:
            if not isinstance(clause, dict):
                continue
            metric = clause.get("metric")
            if metric:
                # `delta.x` addresses metric `x` against a control.
                found.append(metric.split(".")[-1])
            floor = clause.get("min_evidence") or {}
            if floor.get("metric"):
                found.append(floor["metric"].split(".")[-1])
    return found


@pytest.mark.parametrize("floor", [None, 150])
def test_every_metric_the_promotion_gate_names_is_registered(floor):
    from kalshi_bot.experiment_os.metrics import REGISTRY

    names = _metric_names(cap.promotion_gate_spec(floor))

    assert names, "a gate that names no metric decides nothing"
    unknown = sorted({n for n in names if n not in REGISTRY})
    assert not unknown, (
        f"gate names metric(s) absent from the canonical registry: {unknown}. "
        "The evaluator returns BLOCKED_INTEGRITY, which never passes, and the "
        "gate is frozen the moment it is registered."
    )


def test_every_metric_the_keep_gate_names_is_registered():
    from kalshi_bot.experiment_os.metrics import REGISTRY

    names = _metric_names(cap.KEEP_GATE_SPEC)

    assert names
    unknown = sorted({n for n in names if n not in REGISTRY})
    assert not unknown, f"keep gate names unregistered metric(s): {unknown}"


# --- the version revision ---------------------------------------------------
#
# v1 was registered in production with a promotion gate that can never PASS, and
# a gate is frozen with its version. The remedy the lifecycle allows is a new
# Version, in PAPER, where a contract may still be revised. What these tests pin
# is that the revision cannot become anything else: not a way to relax a floor,
# not a way to restate a healthy gate, and not a rollback.


def _registered_successor(session, floor=150):
    _predecessor_in_paper(session)
    return cap.register(
        session, actor="claude-code", now=T1, promotion_sample_floor=floor
    )


def _registered_with_the_shipped_defect(session, monkeypatch, floor=150):
    """Register v1 with its promotion gate EXACTLY as it went to production on
    2026-09-02: the floor clause naming `paper_settled_contracts`, which is not
    a registered metric. Reproduced rather than described, so the revision is
    tested against the real defect and not a tidied-up memory of it."""
    unfixed = cap.promotion_gate_spec  # bound before the patch, or this recurses

    def shipped(sample_floor):
        spec = unfixed(None)
        spec["sample"] = {
            cap.ARM_KEY: {"metric": "paper_settled_contracts", "op": ">=",
                          "value": int(sample_floor), "deployment_kind": "paper"},
        }
        return spec

    monkeypatch.setattr(cap, "promotion_gate_spec", shipped)
    out = _registered_successor(session, floor=floor)
    monkeypatch.undo()
    return out


def test_v1s_floored_gate_reproduces_the_production_defect(xos_session, xos_platform):
    """The bug as it actually shipped, so the fix is anchored to it."""
    from kalshi_bot.experiment_os.metrics import REGISTRY

    out = _registered_successor(xos_session)
    spec = out["promotion_gate"].spec_json

    # After the fix the floor names a REGISTERED metric. Before it, this clause
    # said `paper_settled_contracts` and the evaluator returned BLOCKED_INTEGRITY.
    clause = spec["sample"][cap.ARM_KEY]
    assert clause["metric"] == "settled_contracts"
    assert clause["metric"] in REGISTRY
    assert clause["value"] == 150
    assert clause["deployment_kind"] == "paper"


def test_the_revision_opens_v2_and_carries_the_paper_book(
    xos_session, xos_platform, monkeypatch
):
    _registered_with_the_shipped_defect(xos_session, monkeypatch)

    out = cap.revise_promotion_gate(xos_session, actor="claude-code", now=T1)

    assert out["superseded_version"] == 1
    assert out["version"].version == 2
    assert out["version"].frozen_at is not None
    # The science is untouched. Only the gate's metric name moves.
    assert out["version"].risk_json == cap.RISK_ENVELOPE
    assert out["keep_gate"].spec_json == cap.KEEP_GATE_SPEC
    # The control book keeps running: an epoch cut that stranded it would be the
    # XOS-000011 blackout shape.
    assert out["carried_deployments"], "the paper control must survive the cut"


def test_the_revision_refuses_a_contract_with_nothing_wrong_with_it(
    xos_session, xos_platform, monkeypatch
):
    """Otherwise it is a general-purpose way to discard an evidence window."""
    _registered_with_the_shipped_defect(xos_session, monkeypatch)
    cap.revise_promotion_gate(xos_session, actor="claude-code", now=T1)

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.revise_promotion_gate(xos_session, actor="claude-code", now=T1)

    assert "nothing here to repair" in str(exc.value)


def test_the_revision_cannot_quietly_drop_the_operators_floor(
    xos_session, xos_platform, monkeypatch
):
    """The transport passes `promotion_sample_floor=None` whenever the envelope
    omits it. If that meant "no floor", a revision billed as a metric-name typo
    fix would silently halve the evidence real money waits on."""
    _registered_with_the_shipped_defect(xos_session, monkeypatch, floor=150)

    out = cap.revise_promotion_gate(
        xos_session, actor="claude-code", promotion_sample_floor=None, now=T1
    )

    assert out["promotion_gate"].spec_json["sample"][cap.ARM_KEY]["value"] == 150


def test_the_revision_refuses_outside_PAPER(xos_session, xos_platform, monkeypatch):
    """In LIVE_CANARY a changed decision rule is a successor experiment, not a
    new version — the same rule that made this whole successor necessary."""
    _registered_with_the_shipped_defect(xos_session, monkeypatch)
    successor = cap.get_experiment(xos_session, cap.SUCCESSOR_KEY)
    successor.state = LifecycleState.LIVE_CANARY.value

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.revise_promotion_gate(xos_session, actor="claude-code", now=T1)

    assert "only be revised in PAPER" in str(exc.value)


def test_the_gatefix_package_is_reachable_through_the_transport():
    from kalshi_bot.experiment_os.experiment_commands import _packages

    pkg = _packages().get("mmsell10-capacity-gatefix")

    assert pkg is not None
    assert pkg.register is cap.revise_promotion_gate
    # It registers a contract; it must never be able to arm one.
    assert pkg.arm is None


# --- reverting the operator sample floor ------------------------------------
#
# The floor was not inherited. mmsell-price-ceiling v2 — the version Cmmsell10
# actually armed under — registered this promotion bar with NO sample clause. The
# floor was added to this successor on 2026-09-02 on my recommendation, and the
# recommendation was wrong: the independent variable is the LIVE open-position
# cap, and the paper book has no cap and assumes fill, so paper is identical
# across the change. A floor on it buys sample size in a measurement that cannot
# move. Removing it restores the inherited bar rather than inventing a softer one.
#
# The danger is obvious and these tests are aimed squarely at it: a function that
# removes a pre-registered threshold is one bad call away from being a way to
# move goalposts. What makes it safe is that it can produce exactly ONE spec, and
# only while the deciding metric is still unobserved.


def test_dropping_the_floor_restores_the_predecessors_exact_object(
    xos_session, xos_platform
):
    """Not "a lower floor" — the inherited spec itself. If this ever produces
    something other than the predecessor's object, it has become a dial."""
    _registered_successor(xos_session, floor=150)

    out = cap.drop_operator_sample_floor(xos_session, actor="claude-code", now=T1)

    assert out["dropped_floor"] == 150
    assert out["promotion_gate"].spec_json == v2.PROMOTION_GATE_SPEC
    assert "sample" not in out["promotion_gate"].spec_json
    # Everything that is not the floor survives untouched.
    assert out["keep_gate"].spec_json == cap.KEEP_GATE_SPEC
    assert out["version"].risk_json == cap.RISK_ENVELOPE
    assert out["carried_deployments"], "the paper control must survive the cut"


def test_dropping_the_floor_is_REFUSED_once_the_metric_has_been_observed(
    xos_session, xos_platform
):
    """The guard that matters. Removing a floor BEFORE seeing the number reverts
    a design mistake; removing it AFTER is choosing a threshold that fits a
    result. Nothing about intent distinguishes those from the outside, so the
    code refuses rather than relying on whoever is holding it being honest."""
    out = _registered_successor(xos_session, floor=150)
    svc.record_gate_result(
        xos_session, out["promotion_gate"], verdict="HOLD",
        metrics={"realizable_cents_per_trade": {"value": 1.42}},
        computed_by="test", epoch=out["epoch"],
    )

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.drop_operator_sample_floor(xos_session, actor="claude-code", now=T1)

    assert "already been OBSERVED" in str(exc.value)
    assert "The floor stands." in str(exc.value)


def test_an_undefined_metric_reading_does_NOT_count_as_observed(
    xos_session, xos_platform
):
    """A recorded HOLD on an empty window carries the metric key with a null
    value. Treating that as "observed" would make the revert impossible from the
    moment the gate first evaluated, which is roughly instantly."""
    out = _registered_successor(xos_session, floor=150)
    svc.record_gate_result(
        xos_session, out["promotion_gate"], verdict="HOLD",
        metrics={"realizable_cents_per_trade": {"value": None}},
        computed_by="test", epoch=out["epoch"],
    )

    revised = cap.drop_operator_sample_floor(
        xos_session, actor="claude-code", now=T1
    )

    assert revised["dropped_floor"] == 150


def test_dropping_the_floor_is_refused_when_there_is_no_floor(
    xos_session, xos_platform, monkeypatch
):
    """Otherwise it is a general-purpose way to discard an evidence window."""
    _predecessor_in_paper(xos_session)
    cap.register(xos_session, actor="claude-code", now=T1, promotion_sample_floor=None)

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.drop_operator_sample_floor(xos_session, actor="claude-code", now=T1)

    assert "carries no sample floor" in str(exc.value)


def test_the_unfloor_package_refuses_to_SET_a_floor(xos_session, xos_platform):
    """REGISTER_PACKAGE always passes `promotion_sample_floor`. A package named
    "unfloor" that accepted a value would be able to set one."""
    _registered_successor(xos_session, floor=150)

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.drop_operator_sample_floor(
            xos_session, actor="claude-code", promotion_sample_floor=25, now=T1
        )

    assert "removes the operator sample floor and can set none" in str(exc.value)


def test_dropping_the_floor_is_refused_outside_PAPER(xos_session, xos_platform):
    _registered_successor(xos_session, floor=150)
    cap.get_experiment(xos_session, cap.SUCCESSOR_KEY).state = (
        LifecycleState.LIVE_CANARY.value
    )

    with pytest.raises(svc.ExperimentOsError) as exc:
        cap.drop_operator_sample_floor(xos_session, actor="claude-code", now=T1)

    assert "only be revised in PAPER" in str(exc.value)


def test_the_unfloor_package_is_reachable_and_callable_as_the_transport_calls_it():
    """The transport invokes `register(session, actor=..., promotion_sample_floor
    =...)`. A package whose callable does not accept that signature raises
    TypeError at the boot hook — twice now the defect in this package has been
    exactly that, so assert the call shape, not just the wiring."""
    import inspect

    from kalshi_bot.experiment_os.experiment_commands import _packages

    pkg = _packages().get("mmsell10-capacity-unfloor")

    assert pkg is not None
    assert pkg.register is cap.drop_operator_sample_floor
    assert pkg.arm is None, "a contract revision must never be able to arm"
    params = inspect.signature(pkg.register).parameters
    for name in ("session", "actor", "promotion_sample_floor"):
        assert name in params, f"the transport passes {name}"
