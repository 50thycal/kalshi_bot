"""The Experiment OS package for the one-sided live smoke test (WS-020 Phase 1a).

The load-bearing property of this file is the FIRST test: the risk envelope registered on the
frozen version is the envelope the running code applies. Everything else in Experiment OS
assumes that, and nothing else checks it — a package is reviewed as literals in a pull request,
and literals drift from the constants they were copied out of.
"""

from __future__ import annotations

import pytest

from kalshi_bot.experiment_os import liquidity_incentive_mm as pkg
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.experiment_commands import _packages
from kalshi_bot.experiment_os.lifecycle import LifecycleState
from kalshi_bot.experiment_os.metrics import REGISTRY, resolve_definition
from kalshi_bot.liquidity_incentive import live as limm

GATE_SPECS = (pkg.PROBE_GATE_SPEC, pkg.PROMOTION_GATE_SPEC, pkg.KEEP_GATE_SPEC)


# ------------------------------------------------------- the envelope IS the running code


def test_the_registered_envelope_equals_the_running_constants():
    e = pkg.RISK_ENVELOPE
    assert e["contracts_per_order"] == limm.MAX_CONTRACTS_PER_ORDER == 1
    assert e["max_order_dollars"] == limm.MAX_ORDER_DOLLARS
    assert e["max_price_cents"] == limm.MAX_PRICE_CENTS
    assert e["max_open_orders"] == limm.MAX_OPEN_ORDERS
    assert e["max_book_exposure_usd"] == limm.MAX_STRATEGY_EXPOSURE_USD
    # The stated per-clip downside must be what the price cap actually allows, not a rounder
    # number someone liked better.
    assert e["max_loss_per_clip_usd"] == pytest.approx(limm.MAX_PRICE_CENTS / 100.0)


def test_the_operator_guardrails_are_not_exceeded():
    """The operator authorized: under $10 total, at most 3 at a time, $1 per trade."""
    assert limm.MAX_STRATEGY_EXPOSURE_USD <= 10.00
    assert limm.MAX_OPEN_ORDERS <= 3
    assert limm.MAX_ORDER_DOLLARS <= 1.00
    # And the caps are mutually consistent: three orders at the per-order cap stay inside the
    # book budget, so no combination of allowed orders can breach it.
    assert limm.MAX_OPEN_ORDERS * limm.MAX_ORDER_DOLLARS <= limm.MAX_STRATEGY_EXPOSURE_USD


# ------------------------------------------------------- tags


def test_tags_come_from_the_runtime_module_and_are_prefix_safe():
    assert (pkg.PAPER_TAG, pkg.LIVE_TAG, pkg.TWIN_TAG) == (
        limm.PAPER_TAG, limm.LIVE_TAG, limm.TWIN_TAG)
    # LIVE_STRATEGIES matches by PREFIX: an allowlist entry naming one tag must never arm
    # another. The twin is the one deliberate exception — it is DERIVED from the live tag, and
    # `LiveExecutor._allowed` refuses every configured twin tag outright.
    assert not pkg.LIVE_TAG.startswith(pkg.PAPER_TAG)
    assert not pkg.PAPER_TAG.startswith(pkg.LIVE_TAG)
    assert pkg.TWIN_TAG == pkg.LIVE_TAG + limm.TWIN_SUFFIX
    # paper_trades.strategy is String(24).
    assert max(len(t) for t in (pkg.PAPER_TAG, pkg.LIVE_TAG, pkg.TWIN_TAG)) <= 24


def test_owns_tag_names_the_live_and_twin_tags_and_nothing_else():
    assert limm.owns_tag(pkg.LIVE_TAG) and limm.owns_tag(pkg.TWIN_TAG)
    # Exact match, not a prefix test — a future book must not inherit an exemption nobody read.
    for other in (pkg.PAPER_TAG, "Alimm1x", "Alimm", "Fmmsell10", None, ""):
        assert not limm.owns_tag(other)


# ------------------------------------------------------- gates


@pytest.mark.parametrize("spec", GATE_SPECS)
def test_every_gate_spec_is_structurally_valid(spec):
    svc.validate_gate_spec(spec)


@pytest.mark.parametrize("spec", GATE_SPECS)
def test_every_gate_metric_is_in_the_canonical_registry(spec):
    for name in ("pass_all", "fail_any", "hold_if"):
        for clause in spec.get(name) or []:
            assert resolve_definition(clause["metric"]) is not None, clause
    for floor in (spec.get("sample") or {}).values():
        assert resolve_definition(floor["metric"]) is not None, floor


def test_the_instrument_gates_address_the_probe_and_never_an_arm():
    """The incentive metrics observe a market universe, not a book. A clause that addressed
    one as paper or live evidence would read as though a book had earned it — the provider
    refuses that, and these specs must not rely on the refusal."""
    for spec in (pkg.PROBE_GATE_SPEC, pkg.PROMOTION_GATE_SPEC):
        for name in ("pass_all", "fail_any", "hold_if"):
            for clause in spec.get(name) or []:
                assert clause["metric"].startswith("incentive_"), clause
                assert clause["deployment_kind"] == "probe", clause
                assert clause["scope"] == "experiment", clause
                assert "arm" not in clause, clause


def test_the_keep_gate_addresses_live_explicitly_everywhere():
    """An unaddressed clause defaults to paper, which on a live-only epoch resolves to an empty
    scope and takes the whole gate to BLOCKED_DATA — the defect that left a sibling canary
    unjudgeable."""
    spec = pkg.KEEP_GATE_SPEC
    for name in ("pass_all", "fail_any"):
        for clause in spec[name]:
            assert clause["deployment_kind"] == "live", clause
            assert clause["arm"] == pkg.ARM_KEY, clause
    assert spec["sample"][pkg.ARM_KEY]["deployment_kind"] == "live"
    assert spec["max_evidence_horizon"]["deployment_kind"] == "live"
    assert spec["max_evidence_horizon"]["arms"] == [pkg.ARM_KEY]


def test_no_gate_claims_an_economic_result():
    """At a $10 ceiling this book cannot earn a measurable reward, so a profitability bar would
    certify nothing. If one is ever added, it must be argued for — not slipped in."""
    economic = {"live_cents_per_contract", "realizable_cents_per_trade", "cents_per_trade",
                "sharpe_like", "win_rate_pct"}
    for spec in (pkg.PROBE_GATE_SPEC, pkg.PROMOTION_GATE_SPEC):
        for name in ("pass_all", "fail_any", "hold_if"):
            for clause in spec.get(name) or []:
                assert clause["metric"] not in economic, clause


def test_the_keep_gate_stop_is_inside_the_declared_budget():
    stop = next(c for c in pkg.KEEP_GATE_SPEC["fail_any"]
                if c["metric"] == "live_realized_pnl_usd")
    assert abs(stop["value"]) <= limm.MAX_STRATEGY_EXPOSURE_USD
    clip = next(c for c in pkg.KEEP_GATE_SPEC["fail_any"]
                if c["metric"] == "live_max_realized_loss_usd")
    # One clip cannot lose more than the per-order dollar cap; a settled market that does means
    # the envelope is not being applied.
    assert clip["value"] <= limm.MAX_ORDER_DOLLARS


# ------------------------------------------------------- registration + transport


def test_the_package_is_registered_and_names_its_own_constants():
    p = _packages()["liquidity-incentive-mm"]
    assert p.experiment_key == pkg.EXPERIMENT_KEY
    assert p.register is pkg.register and p.arm is pkg.arm
    assert set(p.strategy_tags) == {pkg.PAPER_TAG, pkg.LIVE_TAG, pkg.TWIN_TAG}
    assert p.activation_vars == pkg.ACTIVATION_VARS


def test_every_activation_var_clears_the_env_channel():
    """A package whose activation step the ops channel refuses halfway through is the #266
    defect class: an approved procedure that fails in front of an operator with a write already
    submitted. It should fail here instead."""
    import sys
    sys.path.insert(0, "scripts")
    import railway_env

    assert not (pkg.ACTIVATION_VARS - set(railway_env.ALLOWED_VARS))


def test_activation_sets_the_allowlist_last():
    env = pkg.activation_env()
    assert list(env)[-1] == "LIVE_STRATEGIES"
    assert env["LIVE_STRATEGIES"] == pkg.LIVE_TAG
    assert env["LIVE_PAPER_TWINS"] == f"{pkg.LIVE_TAG}:{pkg.TWIN_TAG}"
    assert env["LIVE_PAPER_TWIN_SUFFIX"] == limm.TWIN_SUFFIX


def test_the_material_baseline_names_both_tags_with_no_book_params():
    """This book's parameters are CODE constants, not an `mmsell_variants` spec, so the runtime
    recomputes book_params as None. Registering a spec it cannot produce would put the book
    permanently in EXPERIMENT_CONFIG_DRIFT."""
    material = pkg.material_config()["material"]
    assert material["live_strategies_contains"] == [pkg.LIVE_TAG]
    assert material["twin_pairs"] == {pkg.LIVE_TAG: pkg.TWIN_TAG}
    assert material["book_params"] == {pkg.LIVE_TAG: None, pkg.TWIN_TAG: None}


# ------------------------------------------------------- register() behaviour


def _register(xos_session, xos_platform):
    return pkg.register(xos_session, actor="tester")


def test_register_stops_at_probe_and_arms_nothing(xos_session, xos_platform):
    out = _register(xos_session, xos_platform)
    assert out["experiment"].state == LifecycleState.PROBE.value
    assert out["version"].frozen_at is not None
    assert out["version"].risk_json["max_price_cents"] == limm.MAX_PRICE_CENTS
    # The probe deployment carries NO tag: nothing becomes admissible to the write path here.
    from kalshi_bot.experiment_os.read import deployment_arms
    assert all(tag is None for _arm, tag in deployment_arms(xos_session, out["probe_deployment"]))


def test_register_refuses_a_second_run(xos_session, xos_platform):
    _register(xos_session, xos_platform)
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        _register(xos_session, xos_platform)


def test_arm_refuses_while_the_probe_gate_has_not_passed(xos_session, xos_platform):
    """The whole point of stopping at PROBE: a fresh registration has no post-registration
    evidence, so the probe gate cannot PASS, so `arm` cannot reach `arm_live_canary`."""
    _register(xos_session, xos_platform)
    with pytest.raises(svc.ExperimentOsError, match="probe gate"):
        pkg.arm(xos_session, approved_by="tester")


def test_all_three_gates_start_their_evidence_clock_at_registration(xos_session, xos_platform):
    out = _register(xos_session, xos_platform)
    at = out["registered_at"]
    for key in ("probe_gate", "promotion_gate", "keep_gate"):
        gate = out[key]
        assert gate.evidence_started_at is not None
        assert abs((gate.evidence_started_at.replace(tzinfo=at.tzinfo) - at).total_seconds()) < 5


def test_the_incentive_metrics_are_all_provided():
    """A declared-but-unprovided metric evaluates BLOCKED_DATA, which would make every gate
    here unjudgeable — silently, and only once an operator was waiting on it."""
    for key, d in REGISTRY.items():
        if key.startswith("incentive_"):
            assert d.provided, key
