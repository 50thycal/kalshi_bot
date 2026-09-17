"""The Experiment OS package for the one-sided live smoke test (WS-020 Phase 1a).

The load-bearing property of this file is the FIRST test: the risk envelope registered on the
frozen version is the envelope the running code applies. Everything else in Experiment OS
assumes that, and nothing else checks it — a package is reviewed as literals in a pull request,
and literals drift from the constants they were copied out of.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


# ------------------------------------------------------- the transport's floor keyword


def test_register_accepts_the_transport_floor_keyword():
    """`_register_package` always passes `promotion_sample_floor=`; a package whose register()
    cannot take it fails at the WORKER with a TypeError, which is how MARKTANGLE-2's first
    envelope died."""
    import inspect
    assert "promotion_sample_floor" in inspect.signature(pkg.register).parameters


def test_the_floor_may_tighten_the_thinness_bar_but_never_loosen_it():
    registered = next(c["value"] for c in pkg.PROMOTION_GATE_SPEC["hold_if"]
                      if c["metric"] == "incentive_shadow_quotes")
    assert pkg._promotion_spec(None) is pkg.PROMOTION_GATE_SPEC
    raised = pkg._promotion_spec(registered + 300)
    assert next(c["value"] for c in raised["hold_if"]
                if c["metric"] == "incentive_shadow_quotes") == registered + 300
    # The other clauses are untouched, and the registered spec is not mutated.
    assert raised["pass_all"] == pkg.PROMOTION_GATE_SPEC["pass_all"]
    assert next(c["value"] for c in pkg.PROMOTION_GATE_SPEC["hold_if"]
                if c["metric"] == "incentive_shadow_quotes") == registered
    # Loosening a pre-registered bar from an environment variable is the one thing the
    # envelope's narrow vocabulary exists to prevent.
    with pytest.raises(svc.ExperimentOsError, match="BELOW the registered"):
        pkg._promotion_spec(registered - 1)


def test_the_floor_reaches_the_registered_gate(xos_session, xos_platform):
    out = pkg.register(xos_session, actor="tester", promotion_sample_floor=999)
    spec = out["promotion_gate"].spec_json
    assert next(c["value"] for c in spec["hold_if"]
                if c["metric"] == "incentive_shadow_quotes") == 999


def test_book_params_is_declared_none_rather_than_omitted():
    """`runtime_config_check` recomputes book_params for every NAMED tag. Declaring None keeps
    declared and running in agreement; an invented spec the runtime cannot produce would put
    the book permanently in EXPERIMENT_CONFIG_DRIFT."""
    assert pkg.BOOK_PARAMS is None
    assert "book_spec" not in pkg.material_config()


# ------------------------------------------------------- arming, end to end


def _seed_shadow_evidence(session, *, since, n_cycles=14, n_quotes=300, n_outcomes=60,
                          n_programs=25, errors=0):
    """Real shadow-instrument rows in the gate's evidence window.

    Deliberately NOT a hand-written gate result: the gates compute their own verdict from
    these, which is the only way a test can prove the bars are satisfiable by the data the
    collector actually writes."""
    from kalshi_bot import models as m

    for i in range(n_cycles):
        session.add(m.IncentiveDiscoveryCycle(
            started_at=since + timedelta(minutes=5 * (i + 1)),
            errors=1 if i < errors else 0, programs_listed=n_programs))
    for i in range(n_programs):
        session.add(m.IncentiveProgram(
            program_id=f"p{i}", market_ticker=f"KXA-{i}", incentive_type="liquidity",
            terms_hash=f"h{i}", first_seen_at=since, last_seen_at=since + timedelta(hours=1)))
    for _i in range(n_quotes):
        session.add(m.IncentiveShadowQuote(
            market_ticker="KXA-1", policy="break_even", capital_tier_usd=100,
            placed_at=since + timedelta(minutes=1), yes_bid=20, no_bid=75,
            yes_bid_yes_scale=20, no_bid_yes_scale=25, qty_per_side=1, pair_cost_cents=95))
    for i in range(n_outcomes):
        session.add(m.IncentiveShadowOutcome(
            quote_id=i + 1, market_ticker="KXA-1", policy="break_even", capital_tier_usd=100,
            fill_model="conservative", placed_at=since + timedelta(minutes=1),
            ended_at=since + timedelta(minutes=30), outcome="neither_filled",
            yes_filled_qty=0, no_filled_qty=0, matched_pairs=0))
    session.flush()


def test_arming_ends_the_probe_deployment_and_registers_live_and_twin(
        xos_session, xos_platform):
    """The production defect of 2026-09-17 (`limm-arm-1`, REJECTED), as a test.

    `arm_live_canary` closes the operating epoch and carries the open deployments across the
    boundary; `carry_deployments_forward` refuses any kind but `paper`. A PROBE deployment
    left open therefore refuses the whole arming — which the engine is right to do, since a
    probe is a validation instrument and has no business in a live epoch."""
    registered_at = datetime.now(timezone.utc) - timedelta(hours=2)
    out = pkg.register(xos_session, actor="tester", now=registered_at)
    probe = out["probe_deployment"]
    assert probe.ended_at is None, "precondition: the probe deployment starts open"

    _seed_shadow_evidence(xos_session, since=registered_at)

    armed = pkg.arm(xos_session, approved_by="Calvin 50thycal", actor="tester")

    # The probe deployment is ENDED, not carried: its stage is over.
    xos_session.refresh(probe)
    assert probe.ended_at is not None

    live, twin = armed["live"], armed["twin"]
    assert live.kind == "live" and twin.kind == "paper_twin"
    assert twin.twin_of_deployment_id == live.id
    assert xos_session.get(type(out["experiment"]), out["experiment"].id).state == \
        LifecycleState.LIVE_CANARY.value
    # Live and twin share one boundary — the property the comparison depends on.
    assert live.epoch_id == twin.epoch_id == armed["epoch"].id
    assert live.started_at == twin.started_at


def test_arming_is_refused_when_the_shadow_evidence_is_thin(xos_session, xos_platform):
    """The probe gate HOLDs on a thin window, and this package refuses PROBE→PAPER on
    anything but PASS — stricter than the engine, which does not require one."""
    registered_at = datetime.now(timezone.utc) - timedelta(hours=2)
    pkg.register(xos_session, actor="tester", now=registered_at)
    # One discovery cycle: below the 12-cycle thinness bar.
    _seed_shadow_evidence(xos_session, since=registered_at, n_cycles=1, n_quotes=5,
                          n_outcomes=0)
    with pytest.raises(svc.ExperimentOsError, match="probe gate"):
        pkg.arm(xos_session, approved_by="Calvin 50thycal", actor="tester")


def test_end_probe_deployments_leaves_other_kinds_alone(xos_session, xos_platform):
    out = pkg.register(xos_session, actor="tester")
    ended = pkg._end_probe_deployments(xos_session, out["version"],
                                       at=datetime.now(tz=out["registered_at"].tzinfo))
    assert ended == [pkg.PROBE_DEPLOYMENT_KEY]
    # Idempotent: a second call finds nothing open and says so rather than failing.
    assert pkg._end_probe_deployments(
        xos_session, out["version"],
        at=datetime.now(tz=out["registered_at"].tzinfo)) == []
