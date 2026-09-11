"""Acceptance evidence for `kalshi_bot/experiment_os/reviewed_universe.py`.

Every claim the contract makes about itself is proved here against the real service under
NEW_ONLY enforcement, rather than described in a docstring. The five that matter most:

  * **it arms nothing.** The only deployment is PAPER and neither tag is in any live arm set,
    so nothing registered here can reach real money.
  * **the control is INSIDE the experiment.** Both arms share one epoch and one platform
    snapshot by construction. Naming `mmsell10` an external control is what has
    `mmsell-anchor-vol-entry` in BLOCKED_PLATFORM, and this contract does not do it.
  * **the universe question is NOT gated.** It is recorded as an observational read with
    explicit `authority: NONE`, and no gate clause references it. That restraint is the point:
    the question that motivated the whole review is the one this contract refuses to certify.
  * **the universe is FROZEN at registration.** A sign-off landing in the manifest tomorrow
    cannot widen a running book, so evidence stays poolable across the epoch.
  * **the arms differ in the cap and nothing else.** If any other knob diverged, a result would
    be unattributable — which is exactly the confound the two-tape design exists to avoid.
"""

from __future__ import annotations

import pytest

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import reviewed_universe as pkg
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import ArmRole, DeploymentKind, LifecycleState
from kalshi_bot.experiment_os.metrics import REGISTRY


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


@pytest.fixture
def registered(xos_session, xos_platform):
    enf.record_enforcement_change(
        xos_session, mode="NEW_ONLY", actor="operator", reason="test",
        cutover_id="test-new-only", readiness={"ok": True, "checks": {}},
    )
    out = pkg.register(xos_session, actor="task-specific")
    xos_session.commit()
    return out


def _version(session):
    exp = read.get_experiment(session, pkg.EXPERIMENT_KEY)
    return exp, read.latest_version(session, exp)


# ---------------------------------------------------------------- the contract


def test_registration_lands_at_paper_with_a_frozen_v1(registered, xos_session):
    exp, ver = _version(xos_session)
    assert exp.state == LifecycleState.PAPER.value
    assert ver.version == 1
    assert ver.frozen_at is not None, "the contract must be frozen to pre-register"


def test_two_arms_with_exactly_one_control(registered, xos_session):
    _exp, ver = _version(xos_session)
    arms = {a.arm_key: a for a in read.arms_for(xos_session, ver)}
    assert set(arms) == {pkg.CONTROL_ARM, pkg.CAPPED_ARM}
    assert arms[pkg.CONTROL_ARM].role == ArmRole.CONTROL.value
    assert arms[pkg.CAPPED_ARM].role == ArmRole.TREATMENT.value


def test_the_arms_differ_in_the_cap_and_in_nothing_else(registered, xos_session):
    """The whole design. If any other knob diverged, a result would be unattributable."""
    _exp, ver = _version(xos_session)
    arms = {a.arm_key: dict(a.params_json or {}) for a in read.arms_for(xos_session, ver)}
    control, capped = arms[pkg.CONTROL_ARM], arms[pkg.CAPPED_ARM]
    assert control["contestcap"] is None
    assert capped["contestcap"] == 1
    assert capped["contestkey"] == "split", "the shipped key would measure the calendar here"
    assert control["universe"] == capped["universe"]
    # The book bodies differ by exactly the cap fragment appended to the shared base.
    assert control["book"] == pkg.BASE_BOOK_PARAMS
    assert capped["book"] == f"{pkg.BASE_BOOK_PARAMS},{pkg.CAP_PARAMS}"


# ------------------------------------------------------------- it arms nothing


def test_nothing_registered_here_can_reach_real_money(registered, xos_session):
    _exp, ver = _version(xos_session)
    for epoch in read.epochs_for(xos_session, ver):
        for dep in read.deployments_for(xos_session, epoch):
            assert dep.kind != DeploymentKind.LIVE.value
            assert dep.stage != LifecycleState.LIVE_CANARY.value


def test_the_probe_is_tagless_and_only_the_paper_deployment_carries_tags(registered,
                                                                        xos_session):
    """The four-check review reads already-settled history and places no order."""
    probe_tags = [tag for _arm, tag in read.deployment_arms(xos_session, registered["probe"])]
    assert probe_tags and all(t is None for t in probe_tags)
    paper = registered["paper"]
    assert paper.stage == LifecycleState.PAPER.value
    assert paper.kind == DeploymentKind.PAPER.value
    assert paper.deployment_key == pkg.PAPER_DEPLOYMENT_KEY
    tags = {arm.arm_key: tag for arm, tag in read.deployment_arms(xos_session, paper)}
    assert tags == {pkg.CONTROL_ARM: pkg.CONTROL_TAG, pkg.CAPPED_ARM: pkg.CAPPED_TAG}
    # `mmsell10` is deliberately NOT reused: it already carries another experiment's arm.
    assert "mmsell10" not in set(tags.values())


def test_both_arms_live_in_one_epoch(registered, xos_session):
    """What makes the control poolable with the treatment without an external reference."""
    _exp, ver = _version(xos_session)
    epochs = read.epochs_for(xos_session, ver)
    assert len(epochs) == 1
    assert registered["probe"].epoch_id == registered["paper"].epoch_id == epochs[0].id


def test_registration_is_what_makes_exactly_these_two_tags_tradeable(registered, xos_session):
    """Under NEW_ONLY a tag no ACTIVE deployment arm carries is refused at the write path, so
    the lineage link is what registration buys — and it must buy it for these two tags only.

    Asserted on the lineage rather than through `enforcement.tag_admissible`: that helper
    returns None-semantics (admit) whenever no resolver snapshot is loaded, which is the case
    in a unit fixture, so calling it here would pass for any string at all and prove nothing.
    """
    lineage = {t for t in (pkg.CONTROL_TAG, pkg.CAPPED_TAG, "Rmmsell3", "mmsell10")
               if read.strategy_tag_lineage(xos_session, t)}
    assert lineage == {pkg.CONTROL_TAG, pkg.CAPPED_TAG}


def test_the_package_declares_no_activation_vars(registered):
    """`MMSELL_VARIANTS` is set through the `env` channel by an operator, deliberately
    unreachable from the experiment transport. A package that arms nothing declares none."""
    from kalshi_bot.experiment_os import experiment_commands as ec

    assert not ec._packages()["mmsell-reviewed-universe"].activation_vars


# --------------------------------------------------- the gate, and what it refuses to read


def test_the_keep_gate_reads_the_daily_series_and_uses_c_per_trade_only_as_a_floor(
        registered, xos_session):
    _exp, ver = _version(xos_session)
    gates = {g.gate_key: g for g in read.gates_for(xos_session, ver)}
    assert list(gates) == [pkg.KEEP_GATE_KEY], "no promotion is pre-authorized here"
    assert all(g.to_state != LifecycleState.LIVE_CANARY.value for g in gates.values())
    spec = gates[pkg.KEEP_GATE_KEY].spec_json
    primary = [c for c in spec["pass_all"] if c["metric"] == "delta.daily_pnl_stability"]
    assert primary and primary[0]["value"] == pkg.STABILITY_BAR
    # c/trade appears ONLY as a floor that can kill, never as a bar that promotes.
    for clause in spec["pass_all"]:
        if clause["metric"] == "delta.pnl_cents_per_trade":
            assert clause["value"] == pkg.EDGE_FLOOR_CENTS
            assert clause["value"] < 0, "a c/trade PROMOTION bar would be satisfiable by noise"


def test_every_metric_the_gate_names_exists_in_the_engine(registered, xos_session):
    """A gate naming a metric the engine cannot compute is unevaluable — it reads as pending
    forever rather than failing."""
    _exp, ver = _version(xos_session)
    spec = registered["keep_gate"].spec_json
    named = {c["metric"] for c in spec["pass_all"] + spec["fail_any"]}
    named |= {c["metric"] for c in spec["sample"].values()}
    for metric in named:
        base = metric.split("delta.", 1)[-1]
        assert base in REGISTRY, metric
        assert REGISTRY[base].provided, f"{base} has no provider — the gate would block forever"


def test_the_sample_floor_is_settlement_days_not_trades(registered, xos_session):
    """This universe is 24 of 364 known series, so a trade-count floor would make the gate
    unreachable for months. A calendar floor does not move with the trade rate."""
    sample = registered["keep_gate"].spec_json["sample"]
    assert set(sample) == {pkg.CONTROL_ARM, pkg.CAPPED_ARM}
    for arm_key, clause in sample.items():
        assert clause["metric"] == "settled_days", arm_key
        assert clause["value"] == pkg.SAMPLE_FLOOR_DAYS, arm_key


def test_an_envelope_may_raise_the_floor_but_never_lower_it(xos_session, xos_platform):
    enf.record_enforcement_change(
        xos_session, mode="NEW_ONLY", actor="operator", reason="test",
        cutover_id="test-new-only", readiness={"ok": True, "checks": {}},
    )
    with pytest.raises(svc.ExperimentOsError, match="stricter, never weaker"):
        pkg.register(xos_session, actor="task-specific",
                     promotion_sample_floor=pkg.SAMPLE_FLOOR_DAYS - 1)


# ------------------------------------------- the universe question is recorded, NOT certified


def test_the_universe_comparison_is_observational_and_no_gate_reads_it(registered, xos_session):
    """The question that motivated the whole review is the one this contract refuses to
    certify, because its only available control already carries another experiment's arm."""
    _exp, ver = _version(xos_session)
    observational = ver.sample_json["observational"]
    assert observational["authority"].startswith("NONE")
    assert "mmsell10" in observational["instrument"]
    assert observational["how_to_gate_it_properly"]
    spec = registered["keep_gate"].spec_json
    for clause in spec["pass_all"] + spec["fail_any"]:
        assert {clause["treatment"], clause["control"]} == {pkg.CAPPED_ARM, pkg.CONTROL_ARM}


def test_the_in_sample_selection_is_declared_before_either_arm_trades(registered, xos_session):
    """These series were signed partly on their own historical P&L. Saying so afterwards would
    be an excuse; saying so in the frozen contract is a limitation."""
    _exp, ver = _version(xos_session)
    assert "in-sample" in ver.sample_json["observational"]["confound"]
    # ...and the probe that produced the universe declares the same confound about itself.
    assert "fitted to the same tape" in ver.sample_json["probe"]["known_confound"]


# -------------------------------------------------- the universe is frozen at registration


def test_the_universe_is_recorded_on_the_deployment_as_it_stood_at_registration(
        registered, xos_session):
    from kalshi_bot import registry

    _exp, ver = _version(xos_session)
    paper = registered["paper"]
    universe = sorted(registry.reviewed_series())
    assert paper.config_json["universe"] == universe
    assert paper.config_json["universe_size"] == len(universe)
    assert ver.sample_json["universe_at_registration"] == universe
    # Both book specs name the SAME list, exactly — not a substring of it.
    for tag, body in paper.config_json["books"].items():
        allow = body.split("onlyx=", 1)[1]
        assert allow.split("+") == universe, tag


def test_the_allowlist_is_exact_so_the_unreviewed_trumpsay_siblings_cannot_enter(
        registered, xos_session):
    """`only=KXTRUMPSAY` would admit KXTRUMPSAYCOMPANY and KXTRUMPSAYMONTH — identical rules
    over four times the window, measured at -7.5 edge against the signed series' +6.0."""
    from kalshi_bot.mmsell.tracker import MmSellTracker

    for tag, body in registered["paper"].config_json["books"].items():
        allow = body.split("onlyx=", 1)[1].split("+")
        assert "KXTRUMPSAY" in allow, tag
        book = {"onlyx": allow}
        assert MmSellTracker._book_admits_series(book, "KXTRUMPSAY") is True, tag
        assert MmSellTracker._book_admits_series(book, "KXTRUMPSAYCOMPANY") is False, tag
        assert MmSellTracker._book_admits_series(book, "KXTRUMPSAYMONTH") is False, tag


def test_the_recorded_books_are_exactly_what_the_spec_tool_emits(registered, xos_session):
    """Three places could state this universe — the manifest, the doc an operator pastes from,
    and the deployment row. The tool is the one source; this pins the other two to it."""
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "reviewed_tape_spec.py"
    sp = importlib.util.spec_from_file_location("reviewed_tape_spec", path)
    spec_mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(spec_mod)

    emitted = {line.split(":", 1)[0]: line.split(":", 1)[1] for line in spec_mod.specs()}
    assert registered["paper"].config_json["books"] == emitted
    # ...and the doc an operator actually pastes from says the same thing.
    documented = {line.split(":", 1)[0]: line.split(":", 1)[1]
                  for line in spec_mod.documented_spec().split(";")}
    assert documented == emitted


def test_registering_twice_is_refused_not_a_silent_no_op(registered, xos_session):
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        pkg.register(xos_session, actor="task-specific")
