"""`EXPERIMENT_OS_EXPERIMENT_COMMAND` — the lifecycle transport, adversarially.

This transport can reach real-money capability, so the tests are about what it
REFUSES, not what it does when asked nicely.

Four properties carry the design:

  * **An envelope names; it cannot author.** The vocabulary admits a reviewed
    package and an approver. It cannot define a Version, an arm, a gate spec, a
    threshold or a tag — so pre-registration still means what it says, and a
    contract cannot be written in an environment variable the afternoon the
    results arrive.
  * **Exactly-once is the receipt.** `command_id` is UNIQUE and claimed with ON
    CONFLICT DO NOTHING; a committed terminal receipt is final, so a worker
    restarting on the same variable does nothing, and a retry means a new id.
  * **It softens nothing.** Every structural refusal in `arm_live_canary` still
    fires through this path, and a non-PASS promotion gate refuses the command.
  * **It cannot trade.** Arming leaves `LIVE_STRATEGIES` exactly as it was.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import canary_mmsell10 as pkg
from kalshi_bot.experiment_os import experiment_commands as xc
from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.models import (
    ExperimentDeployment,
    ExperimentOsExperimentCommand,
)
from kalshi_bot.models import PaperTrade

UTC = timezone.utc
_NOW = datetime.now(UTC)
V1_FROZE = _NOW - timedelta(days=12)
T = _NOW - timedelta(days=2)


def _envelope(action="REGISTER_PACKAGE", *, command_id="xcmd-0001",
              actor="claude-code", actor_role=None, **payload):
    if actor_role is None:
        actor_role = "TASK_SPECIFIC" if action == "REGISTER_PACKAGE" else "LIVE_OPS"
    body = {"package": "mmsell10-canary"}
    body.update(payload)
    return {
        "command_id": command_id,
        "action": action,
        "actor": actor,
        "actor_role": actor_role,
        "payload": body,
        "schema_version": 1,
    }


@pytest.fixture
def price_ceiling(xos_session, xos_platform):
    """`mmsell-price-ceiling` as production holds it: PAPER, v1 frozen, two arms,
    no risk envelope."""
    s = xos_session
    exp = svc.create_experiment(s, key=pkg.EXPERIMENT_KEY, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", control_required=False,
        control_exemption_reason="absolute realizable bar", now=V1_FROZE)
    svc.add_arm(s, ver, arm_key="mmsell9", role="secondary", strategy_tag="mmsell9")
    svc.add_arm(s, ver, arm_key="mmsell10", role="secondary",
                params={"lo": 5, "hi": 10, "maxyes": 7}, strategy_tag="mmsell10")
    svc.freeze_version(s, ver, now=V1_FROZE)
    epoch = svc.open_epoch(s, ver, reason="paper", started_at=V1_FROZE)
    svc.register_deployment(
        s, epoch, deployment_key=pkg.LEGACY_PAPER_DEPLOYMENT_KEY,
        stage="PAPER", kind="paper",
        arms={"mmsell9": "mmsell9", "mmsell10": "mmsell10"}, started_at=V1_FROZE)
    gate = svc.register_gate(
        s, ver, gate_key=pkg.PROMOTION_GATE_KEY, kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY",
        spec={"pass_all": [{"metric": "realizable_cents_per_trade", "arm": "*",
                            "op": ">", "value": 0}]})
    svc.mark_gate_evidence_started(s, gate, at=V1_FROZE)
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    s.commit()
    return exp, ver, epoch, gate


def _paper(s, tag, n, *, price=94, pnl=0.02, at=None):
    for i in range(n):
        s.add(PaperTrade(market_ticker=f"{tag}-{i}", strategy=tag, status="settled",
                         pnl=pnl, quantity=1, side="no", action="buy",
                         assumed_price=price,
                         created_at=at or (T + timedelta(minutes=1))))


def _paper_in_v2_window(s, n, **kw):
    """Settled rows inside v2/e1's evidence window.

    The transport deliberately gives a package NO control over the boundary: the
    worker stamps the real instant, because an envelope that could set `now`
    could back-date an epoch. So v2/e1 opens at execution time and evidence is
    floored there — rows dated before it are outside the window by construction,
    which is exactly the property v2 exists to have."""
    epoch = read.open_epoch_for(
        s, read.latest_version(s, read.get_experiment(s, pkg.EXPERIMENT_KEY))
    )
    _paper(s, "mmsell10", n, at=epoch.started_at, **kw)


def _run(s, envelope, **kw):
    out = xc.execute_envelope(s, envelope, **kw)
    s.commit()
    return out


# ===========================================================================
# An envelope names; it cannot author
# ===========================================================================


def test_the_vocabulary_cannot_define_a_contract(price_ceiling, xos_session):
    """The load-bearing restriction. Arms, gate specs, thresholds and tags are
    literals in reviewed code; an envelope that could set them would make
    pre-registration meaningless, because the contract could be written after the
    results were known."""
    s = xos_session
    for field in ("arms", "gate_spec", "risk_envelope", "live_tag", "thresholds",
                  "experiment", "version"):
        out = _run(s, _envelope(command_id=f"xcmd-auth-{field}"[:64], **{field: "x"}))
        assert out["status"] == xc.CommandStatus.REJECTED, field
        assert "UNKNOWN_PAYLOAD_FIELD" in out["error"] or \
            "unrecognised payload field" in out["error"], out["error"]


def test_an_unknown_package_is_refused_by_name(price_ceiling, xos_session):
    s = xos_session
    out = _run(s, _envelope(command_id="xcmd-unknown", package="not-a-package"))
    assert out["status"] == xc.CommandStatus.REJECTED
    assert "UNKNOWN_PACKAGE" in out["error"] or "no registered package" in out["error"]
    assert read.latest_version(s, read.get_experiment(s, pkg.EXPERIMENT_KEY)).version == 1


def test_the_payload_never_reaches_the_package(price_ceiling, xos_session):
    """A package is called with keyword arguments the transport controls. The one
    knob an envelope may turn is the promotion floor, and only upward — a floor
    makes the gate stricter and can never make it pass on less."""
    s = xos_session
    out = _run(s, _envelope(command_id="xcmd-floor", promotion_sample_floor=250))
    assert out["status"] == xc.CommandStatus.SUCCEEDED
    ver = read.latest_version(s, read.get_experiment(s, pkg.EXPERIMENT_KEY))
    gate = next(g for g in read.gates_for(s, ver)
                if g.gate_key == pkg.PROMOTION_GATE_KEY)
    assert gate.spec_json["sample"]["mmsell10"]["value"] == 250


@pytest.mark.parametrize("floor", [-1, "300", 1.5, True])
def test_a_bad_sample_floor_is_refused(price_ceiling, xos_session, floor):
    s = xos_session
    out = _run(s, _envelope(command_id=f"xcmd-bf-{abs(hash(str(floor))) % 9999}",
                            promotion_sample_floor=floor))
    assert out["status"] == xc.CommandStatus.REJECTED


# ===========================================================================
# Identity, role and shape
# ===========================================================================


def test_arming_requires_the_live_ops_role(price_ceiling, xos_session):
    """Registering a contract is build work; putting money behind it is Live Ops'
    call. A receipt naming another role would misdescribe who decided."""
    s = xos_session
    with pytest.raises(xc.ExperimentCommandRejected, match="actor_role"):
        xc.execute_envelope(s, _envelope("ARM_CANARY", actor_role="TASK_SPECIFIC",
                                         approved_by="someone"))
    with pytest.raises(xc.ExperimentCommandRejected, match="actor_role"):
        xc.execute_envelope(s, _envelope(actor_role="PLATFORM_CHANGE_REVIEW"))


def test_arming_requires_a_named_approver(price_ceiling, xos_session):
    """`approved_by` is recorded on the lifecycle transition, so the audit trail
    names a person rather than a process."""
    s = xos_session
    _run(s, _envelope(command_id="xcmd-reg-1"))
    _paper_in_v2_window(s, 40)
    s.commit()
    out = _run(s, _envelope("ARM_CANARY", command_id="xcmd-noapp"))
    assert out["status"] == xc.CommandStatus.REJECTED
    assert "approved_by" in out["error"]
    assert read.get_experiment(s, pkg.EXPERIMENT_KEY).state == "PAPER"


@pytest.mark.parametrize("bad", [
    {"schema_version": 2},
    {"command_id": "short"},
    {"actor": "!!"},
    {"payload": "not-an-object"},
])
def test_a_malformed_envelope_leaves_no_receipt(price_ceiling, xos_session, bad):
    """An envelope whose `command_id` cannot be trusted must not have a receipt
    written against it — recording one would attribute whatever ran to a name
    nobody can rely on."""
    s = xos_session
    env = _envelope()
    env.update(bad)
    with pytest.raises(xc.ExperimentCommandRejected):
        xc.execute_envelope(s, env)
    assert s.scalar(select(ExperimentOsExperimentCommand)) is None


def test_an_unknown_action_is_refused(price_ceiling, xos_session):
    s = xos_session
    env = _envelope()
    env["action"] = "RETIRE_EXPERIMENT"
    with pytest.raises(xc.ExperimentCommandRejected, match="vocabulary"):
        xc.execute_envelope(s, env)


def test_an_oversized_envelope_is_refused(price_ceiling, xos_session):
    s = xos_session
    # Padded INSIDE the JSON: `run_boot_command` strips the raw value before
    # measuring it, so trailing whitespace is not a size test.
    with pytest.raises(xc.ExperimentCommandRejected, match="ENVELOPE_TOO_LARGE|bytes"):
        xc.run_boot_command(s, json.dumps(_envelope(package="x" * 5000)))


# ===========================================================================
# Exactly-once
# ===========================================================================


def test_a_replay_executes_nothing_and_returns_the_receipt(price_ceiling, xos_session):
    """The property that makes a worker restart safe: the same variable is read
    again on the next boot and must do nothing."""
    s = xos_session
    first = _run(s, _envelope(command_id="xcmd-once"))
    assert first["status"] == xc.CommandStatus.SUCCEEDED and first["executed"] is True

    second = _run(s, _envelope(command_id="xcmd-once"))
    assert second["executed"] is False and second["replayed"] is True
    assert second["payload_hash"] == first["payload_hash"]
    # ...and no second version was created.
    assert read.latest_version(s, read.get_experiment(s, pkg.EXPERIMENT_KEY)).version == 2


def test_a_reused_id_with_a_different_payload_is_a_collision(price_ceiling,
                                                             xos_session):
    """Same name, different command. Execute nothing and change nothing: the
    stored receipt belongs to whatever really ran under that id."""
    s = xos_session
    _run(s, _envelope(command_id="xcmd-collide"))
    out = _run(s, _envelope(command_id="xcmd-collide", promotion_sample_floor=99))
    assert out["executed"] is False
    assert out["collision"] is True and out["replayed"] is False


def test_a_rejection_is_terminal_for_that_id(price_ceiling, xos_session):
    """A REJECTED receipt is final. Retrying means a NEW command_id, so a failed
    command cannot be silently re-run by leaving the variable set."""
    s = xos_session
    first = _run(s, _envelope(command_id="xcmd-term", package="nope-nope"))
    assert first["status"] == xc.CommandStatus.REJECTED
    again = _run(s, _envelope(command_id="xcmd-term", package="nope-nope"))
    assert again["executed"] is False and again["replayed"] is True
    assert again["status"] == xc.CommandStatus.REJECTED


# ===========================================================================
# It softens nothing
# ===========================================================================


def test_arming_through_the_transport_still_needs_a_passing_gate(price_ceiling,
                                                                 xos_session):
    """A non-PASS promotion gate refuses the whole command, and nothing is left
    half-armed. The transport carries `arm_live_canary`; it does not argue with
    it."""
    s = xos_session
    _run(s, _envelope(command_id="xcmd-reg-2"))
    _paper_in_v2_window(s, 60, price=91)         # a trusted cell measured -5.79c
    s.commit()
    out = _run(s, _envelope("ARM_CANARY", command_id="xcmd-armfail",
                            approved_by="Calvin"))
    assert out["status"] == xc.CommandStatus.REJECTED
    assert read.get_experiment(s, pkg.EXPERIMENT_KEY).state == "PAPER"
    assert s.scalar(select(ExperimentDeployment).where(
        ExperimentDeployment.deployment_key == pkg.LIVE_DEPLOYMENT_KEY)) is None


def test_arming_through_the_transport_still_refuses_inherited_paper_state(
    price_ceiling, xos_session
):
    """The 2026-08-15 lesson reaches through the transport unchanged."""
    s = xos_session
    _run(s, _envelope(command_id="xcmd-reg-3"))
    _paper_in_v2_window(s, 40)
    s.add(PaperTrade(market_ticker="OLD", strategy=pkg.LIVE_TAG, status="open",
                     created_at=T))
    s.commit()
    out = _run(s, _envelope("ARM_CANARY", command_id="xcmd-inherit",
                            approved_by="Calvin"))
    assert out["status"] == xc.CommandStatus.REJECTED
    assert "inherited paper state" in out["error"] or "FRESH" in out["error"]


def test_a_successful_arm_records_the_pair_and_the_approver(price_ceiling,
                                                            xos_session):
    s = xos_session
    _run(s, _envelope(command_id="xcmd-reg-4"))
    _paper_in_v2_window(s, 40)
    s.commit()
    out = _run(s, _envelope("ARM_CANARY", command_id="xcmd-arm-ok",
                            approved_by="Calvin"))
    assert out["status"] == xc.CommandStatus.SUCCEEDED
    assert out["result"]["live_deployment"] == pkg.LIVE_DEPLOYMENT_KEY
    assert out["result"]["twin_deployment"] == pkg.TWIN_DEPLOYMENT_KEY
    # One boundary, recorded on both sides.
    assert out["result"]["live_started_at"] == out["result"]["twin_started_at"]
    assert read.get_experiment(s, pkg.EXPERIMENT_KEY).state == "LIVE_CANARY"
    transition = next(t for t in read.transitions_for(
        s, read.get_experiment(s, pkg.EXPERIMENT_KEY)) if t.to_state == "LIVE_CANARY")
    assert transition.approved_by == "Calvin"


def test_arming_does_not_touch_the_runtime_allowlist(price_ceiling, xos_session,
                                                     settings):
    """The separation that keeps this transport one step away from exposure: a
    successful arm creates the capability and places nothing, because
    LIVE_STRATEGIES is a different switch this transport cannot reach."""
    s = xos_session
    settings.live_strategies = ""
    _run(s, _envelope(command_id="xcmd-reg-5"))
    _paper_in_v2_window(s, 40)
    s.commit()
    assert _run(s, _envelope("ARM_CANARY", command_id="xcmd-arm-allow",
                             approved_by="Calvin"))["status"] == "SUCCEEDED"
    assert settings.live_strategies == ""
    assert settings.live_strategy_list == []


# ===========================================================================
# The receipt itself
# ===========================================================================


def test_a_receipt_never_carries_the_payload(price_ceiling, xos_session):
    """Receipts are read through a public channel. The stored payload exists so a
    replay can be proven identical; the VIEW is metadata only."""
    s = xos_session
    _run(s, _envelope(command_id="xcmd-view", promotion_sample_floor=300))
    view = xc.receipt(s, "xcmd-view")
    assert "payload" not in view and "payload_json" not in view
    assert view["payload_hash"] and len(view["payload_hash"]) == 64
    assert view["actor"] == "claude-code" and view["actor_role"] == "TASK_SPECIFIC"


def test_an_error_does_not_echo_what_was_submitted(price_ceiling, xos_session):
    """An error message rides the same public channel, so author-supplied strings
    are redacted out of it rather than quoted back."""
    s = xos_session
    out = _run(s, _envelope(command_id="xcmd-redact", package="secret-looking-name"))
    assert out["status"] == xc.CommandStatus.REJECTED
    assert "secret-looking-name" not in (out["error"] or "")


def test_the_boot_entry_point_returns_none_when_unset(xos_session):
    """An empty variable is the normal state and must be a no-op, not an error."""
    assert xc.run_boot_command(xos_session, "") is None
    assert xc.run_boot_command(xos_session, "   ") is None


def test_safe_error_fields_never_carry_the_envelope(xos_session):
    fields = xc.safe_error_fields(json.dumps(_envelope()), ValueError("boom"))
    assert set(fields) == {"error_class", "error_code", "command_bytes",
                           "command_hash"}
    assert len(fields["command_hash"]) == 16


def test_the_ops_channel_exposes_only_reads_of_this_transport():
    """The channel that reads these receipts is read-only against Postgres by
    design, and must stay that way. Asserted as an invariant against the CLI's own
    classification rather than a frozen list: pinning the exact set would fail the
    day a read is added and pass the day a WRITE is added under a read-looking
    name."""
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    import ops_runner

    from kalshi_bot.experiment_os import cli

    assert ops_runner.XOS_EXPERIMENT_COMMAND_READS, "the read surface is empty"
    for name, argv in ops_runner.XOS_EXPERIMENT_COMMAND_READS.items():
        assert name.startswith("experiment-command-")
        assert argv[0] == "experiment-command"
        assert argv[1] in cli._EXPERIMENT_COMMAND_READ_ACTIONS, (
            f"ops exposes `{name}` -> `experiment-command {argv[1]}`, a WRITE"
        )
    # The executor is not reachable from this channel under any alias.
    exposed = {argv[1] for argv in ops_runner.XOS_EXPERIMENT_COMMAND_READS.values()}
    assert not (exposed & {"register", "arm", "execute", "run"})
    # ...and the two maps stay disjoint, so an `issue …` name cannot smuggle in a
    # lifecycle action or the reverse.
    assert not (set(ops_runner.XOS_ISSUE_READS)
                & set(ops_runner.XOS_EXPERIMENT_COMMAND_READS))


def test_the_transport_ledger_is_disjoint_from_the_other_two(price_ceiling,
                                                             xos_session):
    """Three tables, three vocabularies. A ticket must not be able to arm a canary
    and a platform revision must not be able to freeze a Version, and keeping the
    ledgers separate is what makes that structural."""
    from kalshi_bot.experiment_os import issue_commands, platform_commands

    assert set(xc.ACTIONS).isdisjoint(platform_commands.ACTIONS)
    assert set(xc.ACTIONS).isdisjoint(getattr(issue_commands, "ACTIONS", {}))
    assert (ExperimentOsExperimentCommand.__tablename__
            == "experiment_os_experiment_commands")
