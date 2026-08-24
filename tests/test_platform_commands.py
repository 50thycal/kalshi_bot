"""The Platform Change Review boot-command transport.

This is the only authorized production path that can write a Platform Revision,
so what it CANNOT do matters at least as much as what it can. Pinned here:

  * the vocabulary is a static allowlist of four actions, and the capabilities
    deliberately left out of it stay out — no order path, no exposure setting, no
    lifecycle transition, no gate verdict, no `apply_new_version`, no force;
  * an ISSUE command still cannot touch a Platform Revision, and this transport
    still cannot touch an issue — the two vocabularies and ledgers are disjoint;
  * exactly-once under concurrency: two workers racing the same `command_id`
    execute it once, and the loser reports the winner's receipt;
  * a replay is inert; a same-id/different-payload collision executes nothing;
  * partial failure is atomic — a batch that refuses on its last row leaves no
    half-recorded classification, and the receipt still commits;
  * the CUTOVER precondition DEFERS rather than consuming the command when this
    worker is not the one serving the change;
  * a cutover activates at the measured instant and re-epochs at exactly that
    instant, so no evidence spans an unrecorded gap;
  * errors and receipts disclose no submitted value and no unrecognised key name.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import platform_commands as pc
from kalshi_bot.experiment_os import platform_impact as pi
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.models import (
    ExperimentOsPlatformCommand,
    PlatformImpactAction,
)

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
BOOT = datetime(2026, 8, 24, 12, 34, 56, tzinfo=UTC)
ROLE = "PLATFORM_CHANGE_REVIEW"


def _env(command_id, action, payload, *, actor="claude-code", role=ROLE):
    return {
        "schema_version": 1,
        "command_id": command_id,
        "action": action,
        "actor": actor,
        "actor_role": role,
        "payload": payload,
    }


def _experiment(s, key, tag=None):
    tag = tag or f"{key}_tag"
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=tag)
    svc.add_arm(s, ver, arm_key="control", role="control", strategy_tag=f"{tag}_c")
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper-1", stage="PAPER", kind="paper",
        arms={"treatment": tag, "control": f"{tag}_c"}, started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    return exp, ver, epoch


@pytest.fixture
def seeded(xos_session, xos_platform):
    """One PAPER experiment pinned to the baseline snapshot."""
    exp, ver, epoch = _experiment(xos_session, "exp-one")
    xos_session.flush()
    return xos_session, exp


# ---------------------------------------------------------------------------
# The vocabulary is an allowlist, and what is absent stays absent
# ---------------------------------------------------------------------------


def test_the_action_vocabulary_is_exactly_these_four():
    assert set(pc.ACTIONS) == {
        "REGISTER_REVISION", "RECORD_IMPACTS", "ESTABLISH_BOUNDARY", "CUTOVER",
    }


@pytest.mark.parametrize("forbidden", [
    "APPLY_NEW_VERSION", "EXEMPT_IMPACT", "FORCE_ACTIVATION", "APPLY_RETIRE",
    "APPLY_PAUSE", "PROMOTE", "TRANSITION", "RECORD_GATE_RESULT", "ARM_LIVE_CANARY",
])
def test_capabilities_deliberately_left_out_stay_out(forbidden):
    assert forbidden not in pc.ACTIONS


def test_the_transport_never_reaches_a_forcing_or_version_fabricating_helper():
    """Parse the module and look at what the CODE references, not what the prose
    says. Docstrings here discuss `force` and `apply_new_version` at length —
    explaining why they are absent — so a substring scan would either pass
    vacuously or fail on its own explanation. The AST cannot be talked to.

    A future edit that reintroduces one of these has to delete this test to do it.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(pc.__file__).read_text())
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg:
            referenced.add(node.arg)
    for forbidden in (
        "force", "force_reason", "apply_new_version", "exempt_impact",
        "record_forced_activation", "arm_live_canary", "place_order",
        "transition_experiment", "record_gate_result", "activate",
    ):
        assert forbidden not in referenced, (
            f"{forbidden} is referenced by the transport's code"
        )


def test_an_issue_command_still_cannot_touch_a_platform_revision():
    from kalshi_bot.experiment_os import issue_commands as ic

    for action in ic.ACTIONS:
        assert "PLATFORM" not in action and "REVISION" not in action
    assert set(ic.ACTIONS).isdisjoint(set(pc.ACTIONS))


def test_the_two_transports_use_separate_ledgers_and_variables():
    from kalshi_bot.experiment_os.models import ExperimentOsIssueCommand

    assert (
        ExperimentOsPlatformCommand.__tablename__
        != ExperimentOsIssueCommand.__tablename__
    )
    from kalshi_bot.config import Settings

    fields = Settings.model_fields
    assert "experiment_os_platform_command" in fields
    assert "experiment_os_issue_command" in fields


def test_the_env_var_is_ops_allowlisted_and_redacted():
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "railway_env", pathlib.Path("scripts/railway_env.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "EXPERIMENT_OS_PLATFORM_COMMAND" in mod.ALLOWED_VARS
    assert "EXPERIMENT_OS_PLATFORM_COMMAND" in mod.REDACTED_VARS


# ---------------------------------------------------------------------------
# Envelope discipline
# ---------------------------------------------------------------------------


def test_an_unknown_action_is_refused_before_any_database_work(seeded):
    s, _ = seeded
    with pytest.raises(pc.PlatformCommandRejected) as exc:
        pc.execute_envelope(s, _env("cmd-unknown-1", "DROP_EVERYTHING", {}))
    assert exc.value.code == "UNKNOWN_ACTION"
    assert s.query(ExperimentOsPlatformCommand).count() == 0


def test_only_the_platform_change_review_role_may_use_this_transport(seeded):
    s, _ = seeded
    with pytest.raises(pc.PlatformCommandRejected) as exc:
        pc.execute_envelope(
            s,
            _env("cmd-role-1", "REGISTER_REVISION",
                 {"component": "MARKET_TAXONOMY", "version": "v2"}, role="LIVE_OPS"),
        )
    assert exc.value.code == "BAD_ACTOR_ROLE"
    assert s.query(ExperimentOsPlatformCommand).count() == 0


def test_an_oversized_envelope_is_refused_by_the_boot_entry_point(seeded):
    s, _ = seeded
    raw = json.dumps(_env("cmd-big-1", "REGISTER_REVISION", {
        "component": "MARKET_TAXONOMY", "version": "v2",
        "description": "x" * (pc.MAX_ENVELOPE_BYTES + 10),
    }))
    with pytest.raises(pc.PlatformCommandRejected) as exc:
        pc.run_boot_command(s, raw)
    assert exc.value.code == "ENVELOPE_TOO_LARGE"


def test_an_empty_variable_does_nothing_at_all(seeded):
    s, _ = seeded
    assert pc.run_boot_command(s, "") is None
    assert pc.run_boot_command(s, "   ") is None
    assert s.query(ExperimentOsPlatformCommand).count() == 0


# ---------------------------------------------------------------------------
# REGISTER_REVISION
# ---------------------------------------------------------------------------


def _register(s, cid="cmd-reg-1", version="settlement_repair_2026_08_24", **extra):
    payload = {"component": "MARKET_TAXONOMY", "version": version}
    payload.update(extra)
    return pc.execute_envelope(s, _env(cid, "REGISTER_REVISION", payload), now=BOOT)


def test_registration_produces_a_pending_revision_never_an_active_one(seeded):
    s, _ = seeded
    receipt = _register(s, fingerprint="a" * 64, pr_ref="PR #257")
    assert receipt["status"] == "SUCCEEDED"
    assert receipt["result"]["status"] == "pending"
    assert receipt["result"]["activated_at"] is None
    rev = pi.get_revision(s, "MARKET_TAXONOMY:settlement_repair_2026_08_24")
    assert rev.status == "pending"
    assert rev.fingerprint == "a" * 64
    assert rev.pr_ref == "PR #257"


def test_registering_the_same_version_twice_is_refused_with_a_receipt(seeded):
    s, _ = seeded
    _register(s, "cmd-reg-a")
    receipt = _register(s, "cmd-reg-b")
    assert receipt["status"] == "REJECTED"
    assert "immutable" in receipt["error"] or "already exists" in receipt["error"]


# ---------------------------------------------------------------------------
# Exactly-once: replay, collision, concurrency
# ---------------------------------------------------------------------------


def test_a_replay_of_the_same_envelope_executes_nothing(seeded):
    s, _ = seeded
    first = _register(s, "cmd-replay-1")
    assert first["executed"] is True
    second = _register(s, "cmd-replay-1")
    assert second["executed"] is False
    assert second["replayed"] is True
    assert second["collision"] is False
    assert s.query(ExperimentOsPlatformCommand).count() == 1


def test_the_same_id_with_a_different_payload_is_a_collision_and_runs_nothing(seeded):
    s, _ = seeded
    _register(s, "cmd-collide-1", version="v_one")
    receipt = _register(s, "cmd-collide-1", version="v_two")
    assert receipt["executed"] is False
    assert receipt["collision"] is True
    assert pi.get_revision(s, "MARKET_TAXONOMY:v_two") is None


def test_two_workers_racing_one_command_id_execute_it_exactly_once(xos_session,
                                                                   xos_platform):
    """The claim is `ON CONFLICT DO NOTHING RETURNING`, so the loser gets no row
    back and its transaction stays clean — it reports the winner's receipt rather
    than executing a second time."""
    s = xos_session
    _experiment(s, "exp-race")
    s.flush()
    env = _env("cmd-race-1", "REGISTER_REVISION",
               {"component": "MARKET_TAXONOMY", "version": "raced"})
    first = pc.execute_envelope(s, env, now=BOOT)
    second = pc.execute_envelope(s, env, now=BOOT + timedelta(seconds=1))
    assert [first["executed"], second["executed"]] == [True, False]
    assert s.query(ExperimentOsPlatformCommand).count() == 1
    revisions = s.scalars(
        select(pc.PlatformRevision).where(pc.PlatformRevision.version == "raced")
    ).all()
    assert len(revisions) == 1


# ---------------------------------------------------------------------------
# RECORD_IMPACTS — batching, and atomicity under partial failure
# ---------------------------------------------------------------------------


def _impacts(s, cid, rows, revision="MARKET_TAXONOMY:settlement_repair_2026_08_24"):
    return pc.execute_envelope(
        s, _env(cid, "RECORD_IMPACTS", {"revision": revision, "impacts": rows}),
        now=BOOT,
    )


def test_a_batch_proposes_and_accepts_every_row_in_one_transaction(xos_session,
                                                                   xos_platform):
    s = xos_session
    a, _, _ = _experiment(s, "exp-a")
    b, _, _ = _experiment(s, "exp-b")
    s.flush()
    _register(s)
    receipt = _impacts(s, "cmd-imp-1", [
        {"experiment": "exp-a", "impact_class": "I0", "action": "NO_ACTION",
         "rationale": "no spec field reads the taxonomy"},
        {"experiment": "exp-b", "impact_class": "I0", "action": "NO_ACTION",
         "rationale": "no spec field reads the taxonomy"},
    ])
    assert receipt["status"] == "SUCCEEDED"
    assert receipt["result"]["count"] == 2
    # An accepted NO_ACTION settles as applied inside the engine — I0 rows need no
    # separate application step.
    assert {r["status"] for r in receipt["result"]["records"]} == {"applied"}


def test_an_i2_row_is_accepted_and_left_awaiting_application(xos_session,
                                                            xos_platform):
    s = xos_session
    _experiment(s, "exp-grow")
    s.flush()
    _register(s)
    receipt = _impacts(s, "cmd-imp-2", [
        {"experiment": "exp-grow", "impact_class": "I2", "action": "NEW_EPOCH",
         "rationale": "the eligible universe grows; evidence must not pool"},
    ])
    assert receipt["result"]["records"][0]["status"] == "accepted"


def test_a_batch_that_refuses_on_its_last_row_records_none_of_them(xos_session,
                                                                  xos_platform):
    """Partial failure is the case that matters: a half-recorded batch would leave
    the activation gate reading 'safe' on an incomplete review."""
    s = xos_session
    _experiment(s, "exp-good")
    s.flush()
    _register(s)
    receipt = _impacts(s, "cmd-imp-partial", [
        {"experiment": "exp-good", "impact_class": "I0", "action": "NO_ACTION",
         "rationale": "fine"},
        {"experiment": "exp-does-not-exist", "impact_class": "I0",
         "action": "NO_ACTION", "rationale": "fine"},
    ])
    assert receipt["status"] == "REJECTED"
    # The receipt itself survived the rollback — that is the point of the savepoint.
    # Two receipts exist: the earlier REGISTER_REVISION and this refusal.
    assert s.query(ExperimentOsPlatformCommand).count() == 2
    # The good first row was rolled back with the bad last one. Nothing partial.
    assert s.query(PlatformImpactAction).count() == 0


def test_a_batch_larger_than_the_bound_is_refused(xos_session, xos_platform):
    s = xos_session
    _experiment(s, "exp-bound")
    s.flush()
    _register(s)
    rows = [
        {"experiment": "exp-bound", "impact_class": "I0", "action": "NO_ACTION",
         "rationale": "x"}
    ] * (pc.MAX_IMPACT_ROWS + 1)
    receipt = _impacts(s, "cmd-imp-bound", rows)
    assert receipt["status"] == "REJECTED"
    assert "limit" in receipt["error"]


# ---------------------------------------------------------------------------
# The CUTOVER precondition — defer, never consume
# ---------------------------------------------------------------------------


def _cutover_env(cid, fingerprint, experiments=("exp-grow",)):
    return _env(cid, "CUTOVER", {
        "revision": "MARKET_TAXONOMY:settlement_repair_2026_08_24",
        "expect_taxonomy_fingerprint": fingerprint,
        "new_epoch_experiments": list(experiments),
    })


def test_the_running_taxonomy_fingerprint_is_reproducible_from_the_source_tree():
    import hashlib

    from kalshi_bot.mmsell.market_types import SERIES_TYPES

    rows = sorted(tuple(r) for r in SERIES_TYPES)
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    assert pc.taxonomy_fingerprint() == hashlib.sha256(payload.encode()).hexdigest()


def test_a_cutover_on_the_wrong_worker_defers_and_stays_armed(xos_session,
                                                              xos_platform):
    """The command must survive an unrelated redeploy landing first. A terminal
    receipt here would burn the cutover and leave the revision pending forever."""
    s = xos_session
    _experiment(s, "exp-grow")
    s.flush()
    _register(s)
    raw = json.dumps(_cutover_env("cmd-cut-defer", "b" * 64))
    view = pc.run_boot_command(s, raw, now=BOOT)
    assert view["status"] == "DEFERRED"
    assert view["code"] == "TAXONOMY_NOT_DEPLOYED"
    assert view["executed"] is False
    # Nothing claimed for the CUTOVER: only the earlier registration's receipt
    # exists, so this command_id can still run later.
    assert s.query(ExperimentOsPlatformCommand).count() == 1
    assert s.scalar(
        select(ExperimentOsPlatformCommand.command_id).where(
            ExperimentOsPlatformCommand.command_id == "cmd-cut-defer"
        )
    ) is None
    rev = pi.get_revision(s, "MARKET_TAXONOMY:settlement_repair_2026_08_24")
    assert rev.status == "pending"


def test_a_deferred_cutover_runs_on_the_boot_that_does_serve_the_change(xos_session,
                                                                        xos_platform):
    s = xos_session
    exp, _, _ = _experiment(s, "exp-grow")
    s.flush()
    _register(s)
    _impacts(s, "cmd-imp-grow", [
        {"experiment": "exp-grow", "impact_class": "I2", "action": "NEW_EPOCH",
         "rationale": "universe grows"},
    ])
    raw_wrong = json.dumps(_cutover_env("cmd-cut-same", "c" * 64))
    assert pc.run_boot_command(s, raw_wrong, now=BOOT)["status"] == "DEFERRED"
    raw_right = json.dumps(_cutover_env("cmd-cut-same", pc.taxonomy_fingerprint()))
    view = pc.run_boot_command(s, raw_right, now=BOOT)
    assert view["status"] == "SUCCEEDED"


def test_a_malformed_fingerprint_is_a_refusal_not_a_deferral(xos_session,
                                                             xos_platform):
    s = xos_session
    _experiment(s, "exp-grow")
    s.flush()
    _register(s)
    with pytest.raises(pc.PlatformCommandRejected) as exc:
        pc.execute_envelope(s, _cutover_env("cmd-cut-bad-fp", "not-a-digest"))
    assert exc.value.code == "BAD_FINGERPRINT"


# ---------------------------------------------------------------------------
# CUTOVER semantics — one measured instant, everywhere
# ---------------------------------------------------------------------------


def _ready_for_cutover(s, key="exp-grow", impact_class="I2", action="NEW_EPOCH"):
    exp, ver, epoch = _experiment(s, key)
    s.flush()
    _register(s, fingerprint=pc.taxonomy_fingerprint())
    _impacts(s, f"cmd-imp-{key}", [
        {"experiment": key, "impact_class": impact_class, "action": action,
         "rationale": "the eligible universe grows; evidence must not pool"},
    ])
    return exp, ver, epoch


def test_the_cutover_activates_and_re_epochs_at_exactly_one_measured_instant(
    xos_session, xos_platform
):
    s = xos_session
    _exp, ver, old_epoch = _ready_for_cutover(s)
    receipt = pc.execute_envelope(
        s, _cutover_env("cmd-cut-ok", pc.taxonomy_fingerprint()), now=BOOT
    )
    assert receipt["status"] == "SUCCEEDED"
    rev = pi.get_revision(s, "MARKET_TAXONOMY:settlement_repair_2026_08_24")
    assert rev.status == "active"
    assert rev.activated_at.replace(tzinfo=UTC) == BOOT
    # The predecessor retires at the SAME instant it stopped governing.
    prior = pi.superseded_revision(s, rev)
    assert prior.status == "retired"
    assert prior.retired_at.replace(tzinfo=UTC) == BOOT
    # The old epoch ends and the new one begins at that instant, with no gap.
    s.refresh(old_epoch)
    assert old_epoch.ended_at.replace(tzinfo=UTC) == BOOT
    new_epoch_id = receipt["result"]["new_epochs"][0]["epoch_id"]
    from kalshi_bot.experiment_os.models import ExperimentEpoch

    new_epoch = s.get(ExperimentEpoch, new_epoch_id)
    assert new_epoch.started_at.replace(tzinfo=UTC) == BOOT
    assert new_epoch.id != old_epoch.id


def test_the_new_epoch_pins_a_snapshot_containing_the_new_revision(xos_session,
                                                                   xos_platform):
    s = xos_session
    _ready_for_cutover(s)
    receipt = pc.execute_envelope(
        s, _cutover_env("cmd-cut-pin", pc.taxonomy_fingerprint()), now=BOOT
    )
    from kalshi_bot.experiment_os.models import ExperimentEpoch

    epoch = s.get(ExperimentEpoch, receipt["result"]["new_epochs"][0]["epoch_id"])
    rev = pi.get_revision(s, "MARKET_TAXONOMY:settlement_repair_2026_08_24")
    # apply_new_epoch refuses outright if the resolved snapshot does not pin the
    # new revision, so reaching here already proves it; assert it anyway.
    assert pi._pinned_revision_id(s, epoch, rev.component_id) == rev.id


def test_the_impact_record_is_applied_by_the_cutover(xos_session, xos_platform):
    s = xos_session
    _ready_for_cutover(s)
    pc.execute_envelope(
        s, _cutover_env("cmd-cut-applied", pc.taxonomy_fingerprint()), now=BOOT
    )
    record = s.scalar(select(PlatformImpactAction))
    assert record.status == "applied"
    assert record.resulting_epoch_id is not None


def test_a_cutover_refuses_when_an_affected_experiment_is_unaccounted(xos_session,
                                                                      xos_platform):
    """The transport never forces. An unclassified affected experiment stops the
    activation, and the receipt says so."""
    s = xos_session
    _ready_for_cutover(s)
    _experiment(s, "exp-unclassified")   # affected, pinned, never classified
    s.flush()
    receipt = pc.execute_envelope(
        s, _cutover_env("cmd-cut-unsafe", pc.taxonomy_fingerprint()), now=BOOT
    )
    assert receipt["status"] == "REJECTED"
    assert "gate is not safe" in receipt["error"]
    rev = pi.get_revision(s, "MARKET_TAXONOMY:settlement_repair_2026_08_24")
    assert rev.status == "pending"


def test_a_cutover_of_an_already_active_revision_is_refused(xos_session,
                                                            xos_platform):
    s = xos_session
    _ready_for_cutover(s)
    pc.execute_envelope(
        s, _cutover_env("cmd-cut-once", pc.taxonomy_fingerprint()), now=BOOT
    )
    receipt = pc.execute_envelope(
        s, _cutover_env("cmd-cut-twice", pc.taxonomy_fingerprint()),
        now=BOOT + timedelta(minutes=5),
    )
    assert receipt["status"] == "REJECTED"
    assert receipt["error"].startswith("PlatformCommandRejected")
    assert "not pending" in receipt["error"]


def test_the_boundary_cannot_be_moved_once_established(xos_session, xos_platform):
    s = xos_session
    _ready_for_cutover(s)
    pc.execute_envelope(
        s, _cutover_env("cmd-cut-fixed", pc.taxonomy_fingerprint()), now=BOOT
    )
    receipt = pc.execute_envelope(
        s,
        _env("cmd-boundary-move", "ESTABLISH_BOUNDARY", {
            "revision": "MARKET_TAXONOMY:settlement_repair_2026_08_24",
            "activated_at": "2026-08-24T23:59:59Z",
        }),
        now=BOOT,
    )
    assert receipt["status"] == "REJECTED"
    assert "cannot be moved" in receipt["error"]


# ---------------------------------------------------------------------------
# Disclosure — the receipt and the error say nothing the author wrote
# ---------------------------------------------------------------------------


def test_a_refusal_does_not_echo_a_submitted_value(xos_session, xos_platform):
    s = xos_session
    _experiment(s, "exp-leak")
    s.flush()
    _register(s)
    canary = "CANARYVALUE-QX7-MUSTNOTAPPEAR"
    receipt = _impacts(s, "cmd-leak-1", [
        {"experiment": "exp-does-not-exist", "impact_class": "I0",
         "action": "NO_ACTION", "rationale": canary},
    ])
    assert receipt["status"] == "REJECTED"
    assert canary not in (receipt["error"] or "")
    assert "CANARYVALUE" not in (receipt["error"] or "")


def test_a_receipt_never_carries_the_payload(xos_session, xos_platform):
    s = xos_session
    _experiment(s, "exp-nopay")
    s.flush()
    receipt = _register(s, "cmd-nopay-1", description="a long private note")
    assert "payload" not in receipt
    assert "a long private note" not in json.dumps(receipt)
    # The hash is what proves what was submitted.
    assert len(receipt["payload_hash"]) == 64


def test_safe_error_fields_never_include_the_envelope():
    fields = pc.safe_error_fields('{"secret":"do-not-log"}', ValueError("boom boom"))
    assert "do-not-log" not in json.dumps(fields)
    assert "boom" not in json.dumps(fields)
    assert fields["error_class"] == "ValueError"
    assert fields["command_bytes"] == len('{"secret":"do-not-log"}')


# ---------------------------------------------------------------------------
# The same race under genuine parallelism, on the database production runs on
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("os").environ.get("XOS_TEST_POSTGRES_URL"),
    reason="XOS_TEST_POSTGRES_URL unset (CI always sets it — see ci.yml)",
)
def test_concurrent_platform_claims_on_postgres():
    """SQLite serialises writers, so the in-process race above proves the LOGIC
    and not the locking. Here four threads submit the identical REGISTER_REVISION
    in overlapping transactions: three block on the uncommitted unique key, find
    no row returned, and read the winner's receipt.

    This one matters more than its issue-command sibling. A lost race here would
    not duplicate a note on a ticket — it would register the same platform
    revision twice, and `register_platform_revision` refuses a duplicate version,
    so the loser would write a REJECTED receipt for a revision that in fact
    exists and is correct. Exactly one execution, or the ledger lies.
    """
    import os
    import threading
    import uuid

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kalshi_bot.experiment_os.models  # noqa: F401 — register tables
    import kalshi_bot.experiment_os.service  # noqa: F401 — install the guard
    from kalshi_bot.config import normalize_database_url
    from kalshi_bot.experiment_os.models import STANDARD_PLATFORM_COMPONENTS, PlatformRevision
    from kalshi_bot.models import Base

    engine = create_engine(
        normalize_database_url(os.environ["XOS_TEST_POSTGRES_URL"])
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    setup = Session()
    svc.ensure_standard_components(setup)
    # Idempotent baseline: this database persists between runs, and revisions are
    # immutable, so re-registering a fixed version would fail on the SECOND run
    # rather than testing anything. Only fill in a component that has none.
    for key in STANDARD_PLATFORM_COMPONENTS:
        component = svc.register_platform_component(setup, key)
        active = setup.scalar(
            select(PlatformRevision).where(
                PlatformRevision.component_id == component.id,
                PlatformRevision.status == "active",
            )
        )
        if active is None:
            svc.register_platform_revision(setup, key, version="v1", activate=True)
    setup.commit()
    # Unique per run, for the same reason: the threads must race each other, not
    # collide with a previous run's row.
    marker = f"race_{uuid.uuid4().hex[:12]}"
    cmd = f"plat-race-{marker}"
    setup.close()

    env = _env(cmd, "REGISTER_REVISION",
               {"component": "MARKET_TAXONOMY", "version": marker})
    results, errors = [], []
    gate = threading.Barrier(4)

    def worker():
        s = Session()
        try:
            gate.wait(timeout=30)
            results.append(pc.execute_envelope(s, dict(env)))
            s.commit()
        except Exception as exc:                      # noqa: BLE001
            errors.append(repr(exc))
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    check = Session()
    try:
        assert not errors, errors
        assert len(results) == 4
        assert len([r for r in results if r["executed"]]) == 1
        assert check.query(ExperimentOsPlatformCommand).filter_by(
            command_id=cmd).count() == 1
        # And exactly one revision, not four and not a rejected duplicate.
        assert check.query(PlatformRevision).filter_by(version=marker).count() == 1
    finally:
        check.close()
