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

UTC = timezone.utc
T0 = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


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
