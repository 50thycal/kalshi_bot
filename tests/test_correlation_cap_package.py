"""Acceptance evidence for `kalshi_bot/experiment_os/correlation_cap.py`.

Every claim the contract makes about itself is proved here against the real service under
NEW_ONLY enforcement, rather than described in a docstring. The three that matter most:

  * **it arms nothing.** The probe deployment is tagless and the only tagged deployment is
    PAPER, so nothing registered here can reach real money.
  * **the control is INSIDE the experiment.** `mmsell10` is already the control arm of
    `mmsell-price-ceiling-capacity`, and naming it as an external control is what has
    `mmsell-anchor-vol-entry` in BLOCKED_PLATFORM. All three arms share one epoch and one
    platform snapshot by construction.
  * **the gate reads the DAILY series, not cents per trade.** At this book's measured per-trade
    sd ($0.2343) a c/trade test of the effect size needs ~95,700 trades per arm, so a c/trade
    promotion criterion would be satisfiable only by noise. It appears as a floor and nowhere
    else.
"""

from __future__ import annotations

import pytest

from kalshi_bot.experiment_os import correlation_cap as pkg
from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import read
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
    out = pkg.register(xos_session, actor="research-lab")
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


def test_three_arms_with_exactly_one_control(registered, xos_session):
    _exp, ver = _version(xos_session)
    arms = {a.arm_key: a for a in read.arms_for(xos_session, ver)}
    assert set(arms) == {pkg.CONTROL_ARM, pkg.GAME_ARM, pkg.ALL_ARM}
    controls = [k for k, a in arms.items() if a.role == ArmRole.CONTROL.value]
    assert controls == [pkg.CONTROL_ARM]


def test_every_arm_shares_the_same_entry_parameters(registered, xos_session):
    """The independent variable is the cap and nothing else. If an arm's band or ceiling ever
    differs, the experiment stops isolating concentration and starts measuring entry price —
    which the type-book family already established is what does the work."""
    _exp, ver = _version(xos_session)
    books = {a.arm_key: (a.params_json or {}).get("book")
             for a in read.arms_for(xos_session, ver)}
    assert set(books.values()) == {pkg.BASE_BOOK_PARAMS}


def test_the_control_carries_no_cap_and_the_treatments_do(registered, xos_session):
    _exp, ver = _version(xos_session)
    params = {a.arm_key: (a.params_json or {}) for a in read.arms_for(xos_session, ver)}
    assert params[pkg.CONTROL_ARM]["corrcap"] is None
    assert params[pkg.GAME_ARM]["corrcap"] == 1
    assert params[pkg.GAME_ARM]["corrscope"] == "game"
    assert params[pkg.ALL_ARM]["corrcap"] == 1
    assert params[pkg.ALL_ARM]["corrscope"] == "all"


# ---------------------------------------------------------------- it arms nothing


def test_nothing_registered_here_can_reach_real_money(registered, xos_session):
    _exp, ver = _version(xos_session)
    for epoch in read.epochs_for(xos_session, ver):
        for dep in read.deployments_for(xos_session, epoch):
            assert dep.kind != DeploymentKind.LIVE.value
            assert dep.stage != LifecycleState.LIVE_CANARY.value


def test_the_probe_deployment_is_tagless(registered, xos_session):
    """A replay of settled history places no order. Under NEW_ONLY a tag no active deployment
    arm carries cannot trade, and this deployment carries none."""
    probe = registered["probe"]
    tags = [tag for _arm, tag in read.deployment_arms(xos_session, probe)]
    assert tags and all(t is None for t in tags)


def test_the_paper_deployment_names_three_fresh_tags(registered, xos_session):
    paper = registered["paper"]
    tags = {arm.arm_key: tag for arm, tag in read.deployment_arms(xos_session, paper)}
    assert tags == {pkg.CONTROL_ARM: "Gmmsell0", pkg.GAME_ARM: "Gmmsell1",
                    pkg.ALL_ARM: "Gmmsell2"}
    # `mmsell10` is deliberately NOT reused: it already carries another experiment's arm.
    assert "mmsell10" not in set(tags.values())


def test_all_three_arms_live_in_one_epoch(registered, xos_session):
    """What makes the control poolable with the treatments without an external reference — the
    failure mode that has mmsell-anchor-vol-entry blocked."""
    _exp, ver = _version(xos_session)
    epochs = read.epochs_for(xos_session, ver)
    assert len(epochs) == 1
    assert registered["probe"].epoch_id == registered["paper"].epoch_id == epochs[0].id


# ---------------------------------------------------------------- the gate


def test_the_gate_is_registered_and_reads_the_daily_series(registered, xos_session):
    spec = registered["keep_gate"].spec_json
    metrics = {c["metric"] for c in spec["pass_all"]} | {c["metric"] for c in spec["fail_any"]}
    assert "delta.daily_pnl_stability" in metrics
    assert {c["metric"] for c in spec["sample"].values()} == {"settled_days"}


def test_cents_per_trade_appears_only_as_a_floor(registered, xos_session):
    """A c/trade PROMOTION criterion on this book would need ~95,700 settled trades per arm to
    have power at the effect size in question. It may only ever kill."""
    spec = registered["keep_gate"].spec_json
    for clause in spec["pass_all"]:
        if clause["metric"] == "delta.pnl_cents_per_trade":
            assert clause["op"] == ">=" and clause["value"] == pkg.EDGE_FLOOR_CENTS
            assert clause["value"] < 0, "an edge FLOOR is negative; a bar would be positive"


def test_every_metric_the_gate_names_has_a_provider(registered, xos_session):
    """A gate on an unprovided metric evaluates BLOCKED_DATA forever and is pre-registration in
    name only. Both daily-series metrics were written for this contract."""
    spec = registered["keep_gate"].spec_json
    named = ({c["metric"] for c in spec["pass_all"]}
             | {c["metric"] for c in spec["fail_any"]}
             | {c["metric"] for c in spec["sample"].values()})
    for key in named:
        base = key.split(".", 1)[1] if key.startswith("delta.") else key
        assert base in REGISTRY, f"{base} is not a registered metric"
        assert REGISTRY[base].provided, f"{base} has no provider — the gate would block"


def test_there_is_no_promotion_gate(registered, xos_session):
    """Dmmsell10 is stood down and nothing here is a live candidate. Registering a
    PAPER -> LIVE_CANARY gate now would pre-authorize a transition no evidence supports."""
    _exp, ver = _version(xos_session)
    gates = read.gates_for(xos_session, ver)
    assert [g.gate_key for g in gates] == [pkg.KEEP_GATE_KEY]
    assert all(g.to_state != LifecycleState.LIVE_CANARY.value for g in gates)


def test_the_sample_floor_is_in_days_and_can_only_be_raised(xos_session, xos_platform):
    enf.record_enforcement_change(
        xos_session, mode="NEW_ONLY", actor="operator", reason="test",
        cutover_id="test-new-only", readiness={"ok": True, "checks": {}},
    )
    with pytest.raises(svc.ExperimentOsError, match="stricter, never weaker"):
        pkg.register(xos_session, actor="research-lab", promotion_sample_floor=30)


def test_registering_twice_is_refused_not_a_silent_no_op(registered, xos_session):
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        pkg.register(xos_session, actor="research-lab")


# ---------------------------------------------------------------- the falsification is recorded


def test_the_contract_records_that_the_tickets_headline_axis_was_FALSIFIED(registered,
                                                                          xos_session):
    """The single most important thing in this contract. The next reader of XOS-000020 will
    otherwise build the contest cap, measure nothing, and conclude the correlation thesis is
    dead — when what actually failed was the axis the ticket named, not the thesis."""
    _exp, ver = _version(xos_session)
    probe = ver.sample_json["probe"]
    assert "falsified" in probe
    assert probe["recorded_result"]["corr_cap_game"]["worst_day_usd"] \
        <= probe["recorded_result"]["control"]["worst_day_usd"]
    assert probe["authority"].startswith("NONE")
