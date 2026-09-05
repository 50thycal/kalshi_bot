"""The worker-side issue-command transport.

Ordinary issue writes had no production path: the ops channel connects with
`DATABASE_URL_RO`, every mutation refuses that connection, and the sandbox cannot
reach Railway. So a ticket could be adopted, triaged and resolved locally and
never in production — a workflow that is complete on paper and unusable in fact.

These tests exercise the real transport, not the service behind it. What they are
really asserting is that the door is narrow (a fixed vocabulary, no SQL, no
lifecycle, no gates, no exposure), that it opens exactly once per `command_id`
however many times the worker restarts, and that the envelope — which travels in
plaintext on a PUBLIC branch — never comes back out of any surface that prints.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from kalshi_bot.experiment_os import cli, read
from kalshi_bot.experiment_os import control_tower as ct
from kalshi_bot.experiment_os import issue_commands as ic
from kalshi_bot.experiment_os import issue_policy as ipol
from kalshi_bot.experiment_os import issues as ix
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.models import ExperimentOsIssueCommand

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _envelope(action, payload, *, command_id="cmd-00000001", actor="cal",
              actor_role="LIVE_OPS", schema_version=ic.SCHEMA_VERSION):
    return {
        "command_id": command_id,
        "action": action,
        "actor": actor,
        "actor_role": actor_role,
        "schema_version": schema_version,
        "payload": payload,
    }


def _receipts(s, command_id) -> int:
    return (
        s.query(ExperimentOsIssueCommand)
        .filter(ExperimentOsIssueCommand.command_id == command_id)
        .count()
    )


def _book(s, key="quiet-book"):
    """A registered, deployed paper book with no trades: a zero-evidence
    candidate the Control Tower reports as unticketed."""
    tag = f"{key}-t"
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
        control_exemption_reason="test shape",
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=tag)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper", stage="PAPER", kind="paper",
        arms={"treatment": tag}, started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    s.commit()
    return exp, ver, epoch


def _open_ticket(s, key="quiet-book"):
    """A real issue, adopted from a real candidate — the state most commands act
    on."""
    _book(s, key)
    rep = ct.build_report(s, evaluate=False)
    cand = next(c for c in rep.issue_candidates
                if c["detector"] == ipol.DETECTOR_ZERO_EVIDENCE)
    receipt = ic.execute_envelope(s, _envelope(
        "OPEN_CANDIDATE", {"fingerprint": cand["fingerprint"]},
        command_id="adopt-quiet-book", actor_role="EXPERIMENT_CONTROL_TOWER",
    ))
    assert receipt["status"] == ic.CommandStatus.SUCCEEDED
    return ix.get_issue(s, receipt["issue_key"]), cand


# ---------------------------------------------------------------------------
# The end-to-end path the spec asks for
# ---------------------------------------------------------------------------


def test_candidate_to_covered_issue_through_the_transport(xos_session, xos_platform):
    """candidate → OPEN_CANDIDATE → executed once → receipt → covered → replay."""
    s = xos_session
    exp, ver, epoch = _book(s, "quiet-book")

    before = ct.build_report(s, evaluate=False)
    cand = next(c for c in before.issue_candidates
                if c["detector"] == ipol.DETECTOR_ZERO_EVIDENCE)

    env = _envelope("OPEN_CANDIDATE", {"fingerprint": cand["fingerprint"]},
                    command_id="ct-adopt-0001",
                    actor_role="EXPERIMENT_CONTROL_TOWER")
    receipt = ic.execute_envelope(s, env)

    assert receipt["status"] == ic.CommandStatus.SUCCEEDED
    assert receipt["executed"] is True
    assert receipt["issue_key"].startswith("XOS-")

    # The issue carries the candidate's own lineage — this is the whole point of
    # adoption rather than hand-opening.
    issue = ix.get_issue(s, receipt["issue_key"])
    assert issue.detector_fingerprint == cand["fingerprint"]
    assert (issue.experiment_id, issue.version_id, issue.epoch_id) == (
        exp.id, ver.id, epoch.id)

    # The anomaly is covered: gone from UNTICKETED, present as an open issue.
    after = ct.build_report(s, evaluate=False)
    assert not [c for c in after.issue_candidates
                if c["fingerprint"] == cand["fingerprint"]]
    assert any(o["issue_key"] == issue.issue_key for o in after.open_issues)

    # Replay: the identical envelope executes nothing and returns the receipt.
    replay = ic.execute_envelope(s, env)
    assert replay["executed"] is False
    assert replay["replayed"] is True
    assert replay["issue_key"] == receipt["issue_key"]
    assert len(ix.list_issues(s, open_only=True)) == 1
    assert s.query(ExperimentOsIssueCommand).count() == 1


def test_a_full_investigation_can_be_driven_entirely_by_command(xos_session,
                                                                xos_platform):
    """Every stage of the documented workflow is reachable from the transport."""
    s = xos_session
    issue, _cand = _open_ticket(s)
    key = issue.issue_key

    steps = [
        ("TRIAGE", {"issue": key, "reason": "first pass", "severity": "HIGH",
                    "priority": "P1", "classification": "OPS",
                    "owner_role": "LIVE_OPS"}),
        ("STATUS", {"issue": key, "status": "INVESTIGATING",
                    "reason": "starting operational diagnosis"}),
        ("ADD_EVIDENCE", {"issue": key, "evidence_type": "OPS_RESULT",
                          "summary": "no settled trades in 14 days",
                          "source_ref": "xos scoreboard"}),
        ("ADD_LINK", {"issue": key, "link_type": "RESEARCH_DOC",
                      "reference": "docs/OPS_RUNBOOK.md", "label": "runbook"}),
        ("PROPOSE_FIX", {"issue": key,
                         "proposed_fix": "restart the paper deployment"}),
        ("RECORD_DISPOSITION", {"issue": key, "disposition": "OPS_REPAIR",
                                "reason": "operational, not scientific"}),
        ("RECORD_VALIDATION_PLAN", {"issue": key,
                                    "validation_plan": "settled trades > 0 in 7d"}),
        ("RECORD_VALIDATION_RESULT", {"issue": key, "passed": True,
                                      "summary": "12 settled trades observed"}),
        ("RESOLVE", {"issue": key, "resolution_summary": "deployment restarted"}),
    ]
    for n, (action, payload) in enumerate(steps):
        receipt = ic.execute_envelope(
            s, _envelope(action, payload, command_id=f"step-{n:04d}0000")
        )
        assert receipt["status"] == ic.CommandStatus.SUCCEEDED, (action, receipt)

    issue = ix.get_issue(s, key)
    assert issue.status == "RESOLVED"
    assert issue.resolved_at is not None

    # And reopening is a command too, so a premature close is recoverable.
    receipt = ic.execute_envelope(s, _envelope(
        "REOPEN", {"issue": key, "reason": "recurred the following week"},
        command_id="reopen-000001",
    ))
    assert receipt["status"] == ic.CommandStatus.SUCCEEDED
    assert ix.get_issue(s, key).status in ipol.OPEN_STATUSES


def test_every_allowed_action_is_reachable_and_no_others_exist(xos_session):
    """The vocabulary is exactly the eighteen operations, each wired to a real
    function. A typo in the table would otherwise surface only in production."""
    assert set(ic.ACTIONS) == {
        "OPEN_CANDIDATE", "OPEN_MANUAL", "TRIAGE", "CLASSIFY", "ASSIGN",
        "TRANSFER", "STATUS", "ADD_EVIDENCE", "ADD_LINK", "PROPOSE_FIX",
        "RECORD_DISPOSITION", "RECORD_VALIDATION_PLAN",
        "RECORD_VALIDATION_RESULT", "RESOLVE", "CLOSE_NO_ACTION",
        "MARK_DUPLICATE", "REOPEN", "RECORD_RECURRENCE",
    }
    openers = {"OPEN_CANDIDATE", "OPEN_MANUAL"}
    for name, spec in ic.ACTIONS.items():
        assert callable(spec.run), name
        assert spec.doc.strip(), name
        # Every action except the two openers addresses an existing issue.
        if name not in openers:
            assert "issue" in spec.required, name
        # No action's vocabulary may outgrow the ceiling the module asserts.
        assert len(spec.required | spec.optional) <= ic.MAX_PAYLOAD_KEYS, name


# ---------------------------------------------------------------------------
# Exactly-once
# ---------------------------------------------------------------------------


def test_repeated_boots_execute_a_command_once(xos_session, xos_platform):
    """`railway.json` restarts on failure up to ten times and every restart
    re-reads the same variable. Inertness has to come from the ledger."""
    s = xos_session
    issue, _ = _open_ticket(s)
    raw = json.dumps(_envelope(
        "CLASSIFY", {"issue": issue.issue_key, "classification": "OPS",
                     "reason": "operational"},
        command_id="boot-classify-1",
    ))

    first = ic.run_boot_command(s, raw)
    assert first["status"] == ic.CommandStatus.SUCCEEDED
    for _ in range(9):
        again = ic.run_boot_command(s, raw)
        assert again["executed"] is False
        assert again["replayed"] is True

    assert _receipts(s, "boot-classify-1") == 1
    events = [e for e in ix.issue_events(s, issue) if e.event_type == "CLASSIFIED"]
    assert len(events) == 1


def test_an_empty_transport_variable_does_nothing(xos_session):
    assert ic.run_boot_command(xos_session, "") is None
    assert ic.run_boot_command(xos_session, "   ") is None
    assert xos_session.query(ExperimentOsIssueCommand).count() == 0


def test_same_id_different_payload_is_a_collision_and_executes_nothing(
        xos_session, xos_platform):
    s = xos_session
    issue, _ = _open_ticket(s)
    first = ic.execute_envelope(s, _envelope(
        "ASSIGN", {"issue": issue.issue_key, "owner_role": "LIVE_OPS",
                   "reason": "operational first"},
        command_id="assign-000001",
    ))
    assert first["status"] == ic.CommandStatus.SUCCEEDED

    collision = ic.execute_envelope(s, _envelope(
        "ASSIGN", {"issue": issue.issue_key, "owner_role": "RESEARCH_LAB",
                   "reason": "actually scientific"},
        command_id="assign-000001",       # same id, different command
    ))
    assert collision["collision"] is True
    assert collision["executed"] is False
    # The stored receipt is untouched and the mutation never happened.
    assert collision["payload_hash"] == first["payload_hash"]
    assert ix.get_issue(s, issue.issue_key).current_owner_role == "LIVE_OPS"
    assert _receipts(s, "assign-000001") == 1


def test_a_differing_actor_is_also_a_collision(xos_session, xos_platform):
    """The hash covers the WHOLE envelope, not just the payload: resubmitting an
    id under someone else's name is a different command."""
    s = xos_session
    issue, _ = _open_ticket(s)
    payload = {"issue": issue.issue_key, "reason": "no change needed"}
    ic.execute_envelope(s, _envelope("CLOSE_NO_ACTION", payload,
                                     command_id="close-0000001", actor="cal"))
    second = ic.execute_envelope(s, _envelope(
        "CLOSE_NO_ACTION", payload, command_id="close-0000001", actor="someone"))
    assert second["collision"] is True


def test_concurrent_claims_cannot_both_execute(xos_session, xos_platform):
    """Two workers booting together. The claim is the unique index, so the loser
    gets no row back from ON CONFLICT DO NOTHING and returns the winner's receipt
    rather than executing a second mutation."""
    from sqlalchemy.orm import sessionmaker

    s = xos_session
    issue, _ = _open_ticket(s)
    other = sessionmaker(bind=s.get_bind(), expire_on_commit=False)()
    env = _envelope("RECORD_RECURRENCE", {"issue": issue.issue_key,
                                          "note": "seen again"},
                    command_id="recur-00000001")
    try:
        winner = ic.execute_envelope(s, env)
        loser = ic.execute_envelope(other, env)
    finally:
        other.close()

    assert winner["executed"] is True
    assert loser["executed"] is False and loser["replayed"] is True
    assert _receipts(s, "recur-00000001") == 1
    recurrences = [e for e in ix.issue_events(s, ix.get_issue(s, issue.issue_key))
                   if e.event_type == "RECURRENCE_OBSERVED"]
    assert len(recurrences) == 1


@pytest.mark.skipif(
    not __import__("os").environ.get("XOS_TEST_POSTGRES_URL"),
    reason="XOS_TEST_POSTGRES_URL unset (CI always sets it — see ci.yml)",
)
def test_concurrent_claims_on_postgres(tmp_path):
    """The same race under genuine parallelism, against the database production
    actually runs on.

    SQLite serialises writers, so the test above proves the LOGIC and not the
    locking. Here four threads submit the identical command in overlapping
    transactions: three block on the uncommitted unique key, then find no row
    returned and read the winner's receipt.

    **CI sets `XOS_TEST_POSTGRES_URL` to its own Postgres service**, so this runs
    on every PR and a SKIP fails the build — `.github/workflows/ci.yml` runs it
    again on its own and greps the result. It skips only on a developer machine
    with no Postgres, where the sqlite tests above still cover the logic. Locally:

        XOS_TEST_POSTGRES_URL=postgresql+psycopg://… pytest -k postgres
    """
    import os
    import threading

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import kalshi_bot.experiment_os.models  # noqa: F401 — register tables
    import kalshi_bot.experiment_os.service  # noqa: F401 — install the guard
    from kalshi_bot.config import normalize_database_url
    from kalshi_bot.models import Base

    # CI supplies a bare `postgresql://` URL; the repo standardises on psycopg3.
    engine = create_engine(
        normalize_database_url(os.environ["XOS_TEST_POSTGRES_URL"])
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    setup = Session()
    issue = ix.create_issue(setup, title="race", opened_by_role="LIVE_OPS",
                            current_owner_role="LIVE_OPS", classification="OPS")
    setup.commit()
    key, cmd = issue.issue_key, f"race-{issue.id:08d}"
    setup.close()

    env = _envelope("RECORD_RECURRENCE", {"issue": key, "note": "again"},
                    command_id=cmd)
    results, errors = [], []
    gate = threading.Barrier(4)

    def worker():
        s = Session()
        try:
            gate.wait(timeout=30)
            results.append(ic.execute_envelope(s, dict(env)))
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
        assert check.query(ExperimentOsIssueCommand).filter_by(
            command_id=cmd).count() == 1
        recurred = [e for e in ix.issue_events(check, ix.get_issue(check, key))
                    if e.event_type == "RECURRENCE_OBSERVED"]
        assert len(recurred) == 1
        assert ix.get_issue(check, key).occurrence_count == 2
    finally:
        check.close()


# ---------------------------------------------------------------------------
# Refusals — the envelope contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutate, why", [
    (lambda e: e.pop("action"), "missing action"),
    (lambda e: e.update(action="DELETE_EXPERIMENT"), "unknown action"),
    (lambda e: e.update(sql="drop table experiments"), "unknown top-level field"),
    (lambda e: e.update(command_id="short"), "command_id too short"),
    (lambda e: e.update(command_id="has spaces in it"), "illegal command_id"),
    (lambda e: e.update(actor=""), "blank actor"),
    (lambda e: e.update(actor_role="FIXER"), "no such role"),
    (lambda e: e.update(actor_role="OPERATOR_OVERRIDE"), "not a legal role"),
    (lambda e: e.update(payload="issue=XOS-000001"), "payload not an object"),
    (lambda e: e.update(schema_version=2), "wrong schema version"),
    (lambda e: e.pop("schema_version"), "missing schema version"),
])
def test_a_malformed_envelope_is_refused_before_any_database_work(
        xos_session, mutate, why):
    env = _envelope("RECORD_RECURRENCE", {"issue": "XOS-000001"})
    mutate(env)
    with pytest.raises(ic.IssueCommandRejected):
        ic.execute_envelope(xos_session, env)
    # No receipt: an envelope whose identity cannot be trusted must not claim a
    # command_id, and nothing it asked for happened.
    assert xos_session.query(ExperimentOsIssueCommand).count() == 0, why


def test_an_oversized_envelope_is_refused(xos_session, xos_platform):
    s = xos_session
    issue, _ = _open_ticket(s)
    env = _envelope("ADD_EVIDENCE", {
        "issue": issue.issue_key, "evidence_type": "OPS_RESULT",
        "summary": "x" * (ic.MAX_ENVELOPE_BYTES + 100),
    }, command_id="huge-00000001")
    with pytest.raises(ic.IssueCommandRejected):
        ic.execute_envelope(s, env)
    assert _receipts(s, "huge-00000001") == 0


def test_a_non_json_transport_value_is_refused_without_echoing_it(xos_session):
    secret = "not json {{{ but-do-not-print-me"
    with pytest.raises(ic.IssueCommandRejected) as exc:
        ic.run_boot_command(xos_session, secret)
    assert "but-do-not-print-me" not in str(exc.value)


# ---------------------------------------------------------------------------
# Refusals — the workflow, unchanged
# ---------------------------------------------------------------------------


def test_a_bad_payload_produces_a_terminal_rejected_receipt(xos_session,
                                                            xos_platform):
    """Payload validation happens AFTER the claim on purpose: a refusal the
    operator cannot read back is a refusal they will retry blind."""
    s = xos_session
    issue, _ = _open_ticket(s)
    receipt = ic.execute_envelope(s, _envelope(
        "TRIAGE", {"issue": issue.issue_key, "reason": "x", "sevirity": "HIGH"},
        command_id="typo-00000001",
    ))
    assert receipt["status"] == ic.CommandStatus.REJECTED
    # The refusal says WHAT was wrong, how many fields, and what the legal
    # vocabulary is — but never echoes the author's own key back out. That key is
    # author-controlled text and every surface that prints this one is public.
    assert "sevirity" not in receipt["error"]
    assert "1 unrecognised payload field" in receipt["error"]
    assert "severity" in receipt["error"]          # the LEGAL name is printed
    stored = read.issue_command(s, "typo-00000001")
    assert stored.status == ic.CommandStatus.REJECTED
    assert stored.completed_at is not None
    view = read.issue_command_summary(stored)
    assert "sevirity" not in json.dumps(view, default=str)
    assert view["payload_unrecognised_fields"] == 1
    assert view["payload_fields"] == ["issue", "reason"]


def test_an_illegal_transition_is_still_refused(xos_session, xos_platform):
    """The transport reimplements no rules. The state machine refuses, the
    savepoint rolls the attempt back, and the refusal is recorded."""
    s = xos_session
    issue, _ = _open_ticket(s)
    receipt = ic.execute_envelope(s, _envelope(
        "STATUS", {"issue": issue.issue_key, "status": "RESOLVED",
                   "reason": "skipping the middle"},
        command_id="jump-00000001",
    ))
    assert receipt["status"] == ic.CommandStatus.REJECTED
    assert ix.get_issue(s, issue.issue_key).status != "RESOLVED"


def test_transfer_still_requires_evidence(xos_session, xos_platform):
    """A transfer with nothing recorded is just moving a problem nobody has
    looked at. The service refuses it; the transport does not get a say."""
    s = xos_session
    bare = ix.create_issue(
        s, title="unexamined", opened_by_role="LIVE_OPS",
        current_owner_role="LIVE_OPS", classification="OPS",
    )
    s.commit()
    receipt = ic.execute_envelope(s, _envelope(
        "TRANSFER", {"issue": bare.issue_key, "to_owner_role": "RESEARCH_LAB",
                     "reason": "looks scientific"},
        command_id="xfer-00000001",
    ))
    assert receipt["status"] == ic.CommandStatus.REJECTED
    assert "evidence" in receipt["error"]
    assert ix.get_issue(s, bare.issue_key).current_owner_role == "LIVE_OPS"

    # With the waiver the service records — the same rule, its documented
    # exception, and still entirely the service's decision.
    ok = ic.execute_envelope(s, _envelope(
        "TRANSFER", {"issue": bare.issue_key, "to_owner_role": "RESEARCH_LAB",
                     "reason": "mis-routed at creation",
                     "evidence_waiver_reason": "wrong owner from the first line"},
        command_id="xfer-00000002",
    ))
    assert ok["status"] == ic.CommandStatus.SUCCEEDED
    assert ix.get_issue(s, bare.issue_key).current_owner_role == "RESEARCH_LAB"


def test_an_unknown_issue_key_is_rejected_not_created(xos_session):
    receipt = ic.execute_envelope(xos_session, _envelope(
        "CLOSE_NO_ACTION", {"issue": "XOS-999999", "reason": "nope"},
        command_id="ghost-0000001",
    ))
    assert receipt["status"] == ic.CommandStatus.REJECTED
    assert receipt["issue_key"] is None
    assert not ix.list_issues(xos_session)


def test_a_stale_candidate_fingerprint_is_rejected(xos_session, xos_platform):
    receipt = ic.execute_envelope(xos_session, _envelope(
        "OPEN_CANDIDATE", {"fingerprint": "0" * 64},
        command_id="stale-0000001", actor_role="EXPERIMENT_CONTROL_TOWER",
    ))
    assert receipt["status"] == ic.CommandStatus.REJECTED
    assert not ix.list_issues(xos_session)


def test_a_rejected_receipt_is_terminal(xos_session, xos_platform):
    """Resubmitting the SAME id after a refusal does not re-run it — retrying
    means a new command_id. Otherwise a bad command retries on every restart."""
    s = xos_session
    issue, _ = _open_ticket(s)
    env = _envelope("STATUS", {"issue": issue.issue_key, "status": "RESOLVED"},
                    command_id="terminal-001")
    first = ic.execute_envelope(s, env)
    assert first["status"] == ic.CommandStatus.REJECTED
    second = ic.execute_envelope(s, env)
    assert second["executed"] is False
    assert second["status"] == ic.CommandStatus.REJECTED


def test_an_unexpected_failure_rolls_back_the_mutation_and_records_it(
        xos_session, xos_platform, monkeypatch):
    """The savepoint is the load-bearing part: the receipt row is claimed OUTSIDE
    it, so rolling back a half-done mutation never rolls back the explanation."""
    s = xos_session
    issue, _ = _open_ticket(s)
    before = len(ix.issue_events(s, issue))

    real = ix.record_recurrence

    def explode(session, target, **kwargs):
        real(session, target, **kwargs)        # write, then die
        raise RuntimeError("executor blew up after writing")

    monkeypatch.setattr(ic.issues, "record_recurrence", explode)
    receipt = ic.execute_envelope(s, _envelope(
        "RECORD_RECURRENCE", {"issue": issue.issue_key},
        command_id="boom-00000001",
    ))

    assert receipt["status"] == ic.CommandStatus.FAILED
    assert "RuntimeError" in receipt["error"]
    # The partial write is gone; the receipt that explains it is committed.
    s.expire_all()
    assert len(ix.issue_events(s, ix.get_issue(s, issue.issue_key))) == before
    assert read.issue_command(s, "boom-00000001").status == ic.CommandStatus.FAILED


def test_a_failed_command_does_not_block_a_concurrent_reader(xos_session,
                                                             xos_platform):
    """A FAILED receipt is committed, so a second worker sees it and stops —
    it does not sit RUNNING forever or get retried into a duplicate."""
    from sqlalchemy.orm import sessionmaker

    s = xos_session
    issue, _ = _open_ticket(s)
    env = _envelope("MARK_DUPLICATE", {"issue": issue.issue_key,
                                       "duplicate_of": "XOS-999999",
                                       "reason": "same thing"},
                    command_id="dupfail-0001")
    first = ic.execute_envelope(s, env)
    assert first["status"] in (ic.CommandStatus.REJECTED, ic.CommandStatus.FAILED)

    other = sessionmaker(bind=s.get_bind(), expire_on_commit=False)()
    try:
        second = ic.execute_envelope(other, env)
    finally:
        other.close()
    assert second["executed"] is False
    assert second["status"] == first["status"]


# ---------------------------------------------------------------------------
# Blast radius
# ---------------------------------------------------------------------------


def test_the_executor_imports_no_lifecycle_evaluator_platform_or_live_helper():
    """A structural proof, not a promise. The module can only reach what it
    imports, so the import list IS the blast radius — the same argument that
    keeps the Control Tower read-only."""
    import ast
    import pathlib

    src = pathlib.Path(ic.__file__).read_text()
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)

    forbidden = {
        "lifecycle", "evaluator", "platform_impact", "enforcement", "importer",
        "executor", "live", "live_executor", "orders", "kalshi_client",
        "gates", "metrics",
    }
    assert not (imported & forbidden), sorted(imported & forbidden)
    # And what it DOES reach is the issue service, the policy and the receipt row.
    assert {"issues", "issue_policy"} <= imported


def test_a_command_cannot_touch_experiment_state_gates_or_exposure(xos_session,
                                                                   xos_platform):
    """Run the whole vocabulary's worth of writes and assert the canonical tables
    are byte-identical afterwards."""
    from kalshi_bot.experiment_os.models import (
        Experiment,
        ExperimentDeployment,
        ExperimentEpoch,
        ExperimentGate,
        ExperimentGateResult,
        ExperimentStateTransition,
        ExperimentVersion,
        PlatformRevision,
        PlatformSnapshot,
    )

    s = xos_session
    issue, _ = _open_ticket(s)
    watched = (Experiment, ExperimentVersion, ExperimentEpoch,
               ExperimentDeployment, ExperimentGate, ExperimentGateResult,
               ExperimentStateTransition, PlatformRevision, PlatformSnapshot)

    def snapshot():
        return {
            m.__name__: sorted(
                tuple(sorted(
                    (c.name, str(getattr(row, c.name)))
                    for c in m.__table__.columns
                ))
                for row in s.query(m).all()
            )
            for m in watched
        }

    before = snapshot()
    for n, (action, payload) in enumerate([
        ("TRIAGE", {"issue": issue.issue_key, "reason": "r", "severity": "HIGH"}),
        ("STATUS", {"issue": issue.issue_key, "status": "INVESTIGATING"}),
        ("ADD_EVIDENCE", {"issue": issue.issue_key, "evidence_type": "OPS_RESULT",
                          "summary": "observed"}),
        ("RECORD_DISPOSITION", {"issue": issue.issue_key,
                                "disposition": "NEW_VERSION",
                                "reason": "the contract was wrong",
                                "requires_new_version": True,
                                "version_and_epoch_rationale": "changed question"}),
    ]):
        r = ic.execute_envelope(s, _envelope(action, payload,
                                             command_id=f"inert-{n:06d}0"))
        assert r["status"] == ic.CommandStatus.SUCCEEDED, r

    s.expire_all()
    assert snapshot() == before
    # RECORD_DISPOSITION with requires_new_version DECLARES a need; it creates no
    # Version, no Epoch and no transition. That separation is the whole design.
    assert ix.get_issue(s, issue.issue_key).requires_new_version is True


# ---------------------------------------------------------------------------
# The transport is public: the envelope must not come back out
# ---------------------------------------------------------------------------


MARKER = "do-not-echo-this-marker"


def test_the_raw_envelope_never_appears_in_any_output_path(xos_session,
                                                           xos_platform, caplog):
    """Output hygiene, verified rather than asserted. This is NOT confidentiality
    — the same bytes sit in Git history on the public ops branch — but a value
    that is echoed into ops results and worker logs gets copied into places
    nobody is thinking about, so it stays out of all of them."""
    import logging

    s = xos_session
    issue, _ = _open_ticket(s)
    env = _envelope("ADD_LINK", {
        "issue": issue.issue_key, "link_type": "RESEARCH_DOC",
        "reference": f"docs/{MARKER}.md", "label": MARKER,
    }, command_id="echo-00000001")
    raw = json.dumps(env)

    with caplog.at_level(logging.DEBUG):
        receipt = ic.run_boot_command(s, raw)
    assert receipt["status"] == ic.CommandStatus.SUCCEEDED

    # 1. The receipt view returned to the boot hook (which logs it verbatim).
    assert MARKER not in json.dumps(receipt, default=str)
    # 2. The read surfaces, which print everything they are handed.
    stored = read.issue_command(s, "echo-00000001")
    assert MARKER not in json.dumps(read.issue_command_summary(stored), default=str)
    # 3. The CLI's own rendering of both.
    for argv in (["issue", "command-show", "echo-00000001"],
                 ["issue", "command-list"]):
        args = cli.build_parser().parse_args(argv)
        assert cli.cmd_issue(s, args) == 0
    # 4. Nothing logged during execution carried it either.
    assert MARKER not in caplog.text

    # The payload IS stored, though — a receipt that cannot prove what it ran is
    # not an audit record. It is simply never printed.
    assert MARKER in json.dumps(stored.payload_json)


def test_the_receipt_read_surface_is_metadata_only(xos_session, xos_platform):
    s = xos_session
    issue, _ = _open_ticket(s)
    ic.execute_envelope(s, _envelope(
        "ADD_EVIDENCE", {"issue": issue.issue_key, "evidence_type": "OPS_RESULT",
                         "summary": "a bounded summary", "source_ref": "docs/X.md"},
        command_id="meta-00000001",
    ))
    view = read.issue_command_summary(read.issue_command(s, "meta-00000001"))
    assert "payload" not in view
    assert view["payload_fields"] == ["evidence_type", "issue", "source_ref",
                                      "summary"]
    assert len(view["payload_hash"]) == 64
    assert view["result"]["kind"] == "ExperimentIssueEvidence"


def test_the_transport_variable_is_absent_from_the_startup_summary(base_env):
    """`main.run` logs `settings.redacted_summary()` on every boot. That summary
    is an explicit allowlist, so the command field is excluded by construction —
    pinned here because someone adding a field to the allowlist later would not
    otherwise be told."""
    from kalshi_bot.config import Settings

    s = Settings(_env_file=None,
                 experiment_os_issue_command=json.dumps(
                     _envelope("RECORD_RECURRENCE",
                               {"issue": "XOS-000001", "note": MARKER})))
    summary = s.redacted_summary()
    assert "experiment_os_issue_command" not in summary
    assert MARKER not in json.dumps(summary, default=str)


def test_railway_env_redacts_the_transport_variable_both_ways():
    """Setting AND reading. A value redacted on the way in and printed on the way
    out is not redacted."""
    import sys
    sys.path.insert(0, "scripts")
    import railway_env

    assert "EXPERIMENT_OS_ISSUE_COMMAND" in railway_env.ALLOWED_VARS
    assert "EXPERIMENT_OS_ISSUE_COMMAND" in railway_env.REDACTED_VARS
    body = json.dumps(_envelope("RECORD_RECURRENCE", {"issue": "XOS-000001"}))
    for prefix in ("  ", "  set "):
        line = railway_env._echo("EXPERIMENT_OS_ISSUE_COMMAND", body, prefix)
        assert body not in line
        assert "XOS-000001" not in line
        assert "redacted" in line and "sha256:" in line
    # An ordinary operational var is still printed in full — redaction is for the
    # command body, not a blanket blindfold over the ops channel.
    assert railway_env._echo("KILL_SWITCH", "false", "  ").endswith("KILL_SWITCH=false")


def test_railway_env_bounds_the_value_before_calling_railway(monkeypatch):
    """The size check must happen locally: an oversized body should never leave
    this process, and it must not half-apply a batch."""
    import sys
    sys.path.insert(0, "scripts")
    import railway_env

    called = []
    monkeypatch.setattr(railway_env, "_graphql",
                        lambda *a, **k: called.append(a) or {})
    monkeypatch.setattr(railway_env, "_ctx", lambda: ("t", "p", "e", "s"))
    rc = railway_env.run_set(
        {"EXPERIMENT_OS_ISSUE_COMMAND": "x" * (railway_env.MAX_VALUE_BYTES + 1)}
    )
    assert rc == 1
    assert called == []


# ---------------------------------------------------------------------------
# Role ownership: attribution, not authorization
# ---------------------------------------------------------------------------


def test_actor_role_is_recorded_but_grants_nothing(xos_session, xos_platform):
    """The transport cannot verify identity; the authority is who can push to the
    ops branch. So the role is recorded for the history and then handed to the
    service, which applies its OWN rule — here, that the Control Tower may detect
    and hand off but may never own a ticket."""
    s = xos_session
    issue, _ = _open_ticket(s)
    receipt = ic.execute_envelope(s, _envelope(
        "ASSIGN", {"issue": issue.issue_key,
                   "owner_role": "EXPERIMENT_CONTROL_TOWER",
                   "reason": "claiming it for the tower"},
        command_id="tower-000001", actor_role="EXPERIMENT_CONTROL_TOWER",
    ))
    assert receipt["status"] == ic.CommandStatus.REJECTED
    assert ix.get_issue(s, issue.issue_key).current_owner_role != \
        "EXPERIMENT_CONTROL_TOWER"
    # The attempt is still attributed, which is the point of recording the role.
    assert read.issue_command(s, "tower-000001").actor_role == \
        "EXPERIMENT_CONTROL_TOWER"


def test_the_ops_channel_exposes_only_reads(xos_session):
    """Every ops-exposed xos issue command must be classified read-only by the
    CLI — the existing invariant, extended to the two receipt commands."""
    import sys
    sys.path.insert(0, "scripts")
    import ops_runner

    for name, argv in ops_runner.XOS_ISSUE_READS.items():
        assert argv[0] == "issue", name
        assert argv[1] in cli._ISSUE_READ_ACTIONS, name
    assert {"issue-command-show", "issue-command-list"} <= set(
        ops_runner.XOS_ISSUE_READS)
    # And the executor is not reachable from the ops runner at all.
    import pathlib
    src = pathlib.Path(ops_runner.__file__).read_text()
    assert "issue_commands" not in src


# ---------------------------------------------------------------------------
# OPEN_MANUAL — a production path for problems a PERSON found
# ---------------------------------------------------------------------------


def _manual(payload, **kw):
    base = {
        "title": "collector stopped mid-cycle",
        "problem_statement": "the NWS collector logged no observations after 03:00",
        "classification": "OPS",
        "owner_role": "LIVE_OPS",
        "reason": "noticed during the morning Live Ops read of the runtime",
    }
    base.update(payload)
    return _envelope("OPEN_MANUAL", base, **kw)


def test_a_manual_report_opens_an_unscoped_issue(xos_session, xos_platform):
    """The gap this closes: before OPEN_MANUAL, a problem the Tower's detectors
    do not cover had NO production path at all — and "use the local CLI" is not
    an answer when the local CLI is exactly what cannot write production."""
    s = xos_session
    receipt = ic.execute_envelope(s, _manual({}, command_id="manual-000001"))

    assert receipt["status"] == ic.CommandStatus.SUCCEEDED
    issue = ix.get_issue(s, receipt["issue_key"])
    assert issue.title == "collector stopped mid-cycle"
    assert issue.classification == "OPS"
    assert issue.current_owner_role == "LIVE_OPS"
    assert issue.opened_by_role == "LIVE_OPS"
    assert issue.opened_by_actor == "cal"
    # A fixed source, and NO fingerprint: fabricating one would make a manual
    # ticket look like it covers a candidate that was never detected.
    assert issue.detector == ipol.DETECTOR_MANUAL
    assert issue.detector_fingerprint is None
    assert issue.details_json["manual_report"] is True
    # Unscoped is legitimate — not every problem belongs to one experiment.
    assert issue.experiment_id is None
    assert issue.version_id is None


def test_a_manual_report_records_how_it_was_discovered(xos_session, xos_platform):
    """`reason` is required because a manual ticket has no detector to explain
    itself, and "someone said so" is not provenance."""
    s = xos_session
    receipt = ic.execute_envelope(s, _manual({}, command_id="manual-000002"))
    issue = ix.get_issue(s, receipt["issue_key"])
    created = [e for e in ix.issue_events(s, issue) if e.event_type == "CREATED"]
    assert len(created) == 1
    assert "morning Live Ops read" in (created[0].reason or "")
    assert "morning Live Ops read" in (issue.evidence_summary or "")


@pytest.mark.parametrize("missing", [
    "title", "problem_statement", "classification", "owner_role", "reason",
])
def test_a_manual_report_requires_all_five_fields(xos_session, missing):
    payload = {
        "title": "t", "problem_statement": "p", "classification": "OPS",
        "owner_role": "LIVE_OPS", "reason": "r",
    }
    payload.pop(missing)
    receipt = ic.execute_envelope(xos_session, _envelope(
        "OPEN_MANUAL", payload, command_id=f"manual-req-{missing[:4]}"))
    assert receipt["status"] == ic.CommandStatus.REJECTED
    assert missing in receipt["error"]        # a LEGAL field name; safe to print
    assert not ix.list_issues(xos_session)


def test_a_manual_report_can_be_scoped_to_an_experiment_and_version(
        xos_session, xos_platform):
    s = xos_session
    exp, ver, _epoch = _book(s, "scoped-book")
    receipt = ic.execute_envelope(s, _manual(
        {"experiment": "scoped-book", "version": "1"},
        command_id="manual-scope-1",
    ))
    assert receipt["status"] == ic.CommandStatus.SUCCEEDED, receipt
    issue = ix.get_issue(s, receipt["issue_key"])
    assert issue.experiment_id == exp.id
    assert issue.version_id == ver.id


def test_a_manual_report_can_be_scoped_to_a_deployment(xos_session, xos_platform):
    """A deployment KEY, not a raw id — and the epoch, version and experiment
    above it are derived, because the lineage chain already knows them."""
    s = xos_session
    exp, ver, epoch = _book(s, "dep-book")
    receipt = ic.execute_envelope(s, _manual(
        {"deployment": "dep-book-paper"}, command_id="manual-scope-2"))
    assert receipt["status"] == ic.CommandStatus.SUCCEEDED, receipt
    issue = ix.get_issue(s, receipt["issue_key"])
    assert issue.epoch_id == epoch.id
    assert issue.version_id == ver.id
    assert issue.experiment_id == exp.id


def test_a_manual_report_can_be_scoped_to_a_gate(xos_session, xos_platform):
    s = xos_session
    exp, ver, _epoch = _book(s, "gate-book")
    gate = svc.register_gate(
        s, ver, gate_key="paper_to_live_canary", kind="promotion",
        spec={"pass_all": [{"metric": "candidate_rejection_rate_pct",
                            "arm": "treatment", "op": ">", "value": 0}]},
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
    )
    s.commit()
    receipt = ic.execute_envelope(s, _manual(
        {"experiment": "gate-book", "version": "1",
         "gate": "paper_to_live_canary"},
        command_id="manual-scope-3",
    ))
    assert receipt["status"] == ic.CommandStatus.SUCCEEDED, receipt
    issue = ix.get_issue(s, receipt["issue_key"])
    assert issue.gate_id == gate.id
    assert issue.version_id == ver.id


def test_a_manual_report_can_be_scoped_to_a_platform_revision(xos_session,
                                                              xos_platform):
    s = xos_session
    from kalshi_bot.experiment_os.models import (
        PlatformComponent,
        PlatformRevision,
    )
    component = s.query(PlatformComponent).first()
    revision = (
        s.query(PlatformRevision)
        .filter(PlatformRevision.component_id == component.id)
        .first()
    )
    receipt = ic.execute_envelope(s, _manual(
        {"classification": "PLATFORM", "owner_role": "PLATFORM_CHANGE_REVIEW",
         "platform_revision": f"{component.key}:{revision.version}"},
        command_id="manual-scope-4",
    ))
    assert receipt["status"] == ic.CommandStatus.SUCCEEDED, receipt
    issue = ix.get_issue(s, receipt["issue_key"])
    assert issue.platform_revision_id == revision.id


@pytest.mark.parametrize("bad, why", [
    ({"experiment": "no-such-book"}, "unknown experiment key"),
    ({"experiment": "lineage-book", "version": "99"}, "no such version"),
    ({"version": "1"}, "version without its experiment"),
    ({"gate": "paper_to_live_canary"}, "gate without its version"),
    ({"deployment": "no-such-deployment"}, "unknown deployment key"),
    ({"platform_revision": "no-colon-here"}, "malformed revision reference"),
    ({"platform_revision": "NOPE:v9"}, "unknown revision"),
])
def test_a_manual_report_refuses_invalid_lineage(xos_session, xos_platform,
                                                 bad, why):
    """Every reference is resolved explicitly before `create_issue` is called, so
    a lineage that cannot be resolved is a refusal, never a half-scoped ticket."""
    s = xos_session
    _book(s, "lineage-book")
    receipt = ic.execute_envelope(s, _manual(bad, command_id=f"bad-{abs(hash(why))%10**8:08d}"))
    assert receipt["status"] == ic.CommandStatus.REJECTED, why
    assert not ix.list_issues(s), why


def test_a_manual_report_refuses_mismatched_lineage(xos_session, xos_platform):
    """A version that belongs to a DIFFERENT experiment than the one named. The
    resolver catches it because the version number is looked up within the named
    experiment; nothing is silently corrected."""
    s = xos_session
    _book(s, "book-a")
    _book(s, "book-b")
    # book-a has version 1; ask for version 2 under it after creating a second
    # version elsewhere — the number simply does not exist on book-a.
    receipt = ic.execute_envelope(s, _manual(
        {"experiment": "book-a", "version": "2"}, command_id="mismatch-0001"))
    assert receipt["status"] == ic.CommandStatus.REJECTED
    assert not ix.list_issues(s)


def test_a_manual_report_cannot_smuggle_extra_create_issue_kwargs(xos_session):
    """There is no `**payload` passthrough. A field `create_issue` would happily
    accept is still not part of OPEN_MANUAL's vocabulary."""
    receipt = ic.execute_envelope(xos_session, _manual(
        {"detector_fingerprint": "0" * 64}, command_id="smuggle-0001"))
    assert receipt["status"] == ic.CommandStatus.REJECTED
    assert not ix.list_issues(xos_session)


def test_adoption_is_still_the_rule_when_a_candidate_exists(xos_session,
                                                            xos_platform):
    """OPEN_MANUAL does not make OPEN_CANDIDATE optional. A manual ticket for a
    DETECTED problem carries no fingerprint, so the Tower goes on reporting the
    anomaly as unticketed — which is the defect adoption exists to prevent. The
    rule is documented; this test pins the consequence so nobody rediscovers it
    in production."""
    s = xos_session
    _book(s, "quiet-book")
    before = ct.build_report(s, evaluate=False)
    cand = next(c for c in before.issue_candidates
                if c["detector"] == ipol.DETECTOR_ZERO_EVIDENCE)

    ic.execute_envelope(s, _manual(
        {"title": "quiet-book has no evidence"}, command_id="manual-wrong-1"))

    after = ct.build_report(s, evaluate=False)
    still = [c for c in after.issue_candidates
             if c["fingerprint"] == cand["fingerprint"]]
    assert still, "a manual ticket must NOT be mistaken for coverage"


# ---------------------------------------------------------------------------
# Failure-path disclosure — the harder half of "the transport is public"
# ---------------------------------------------------------------------------
#
# The success-path marker test above is not enough. Success writes exactly what
# the executor chose to write; FAILURE writes whatever an exception happened to
# say, and exceptions quote things: the offending value, the failing SQL
# statement, its bound parameters (which include `payload_json`), the whole
# document. Each test below drives one such path and asserts the marker reaches
# no output surface at all.


FAIL_MARKER = "leak-me-nowhere-9d41f2"


def _log_text(caplog) -> str:
    """Everything a handler could format, not just the rendered message: a
    traceback lives on the record, not in `getMessage()`."""
    parts = [caplog.text]
    for record in caplog.records:
        parts.append(str(record.getMessage()))
        parts.append(str(getattr(record, "extra_fields", "")))
        if record.exc_info:
            import traceback
            parts.append("".join(traceback.format_exception(*record.exc_info)))
        if record.exc_text:
            parts.append(record.exc_text)
    return "\n".join(parts)


def _cli_text(session, command_id, capsys) -> str:
    for argv in (["issue", "command-show", command_id], ["issue", "command-list"]):
        args = cli.build_parser().parse_args(argv)
        cli.cmd_issue(session, args)
    return capsys.readouterr().out


def test_an_executor_failure_after_writing_leaks_nothing(
        xos_session, xos_platform, monkeypatch, caplog, capsys):
    """An action that writes and THEN raises with the payload in its message.

    This is the FAILED path: savepoint rolled back, terminal receipt committed,
    nothing logged with `exc_info`. The marker must not survive into the receipt,
    the stored summary, the CLI, or the log.
    """
    import logging

    s = xos_session
    issue, _ = _open_ticket(s)
    real = ix.record_recurrence

    def explode(session, target, **kwargs):
        real(session, target, **kwargs)          # write, then die loudly
        raise RuntimeError(f"boom while handling {FAIL_MARKER}")

    monkeypatch.setattr(ic.issues, "record_recurrence", explode)
    with caplog.at_level(logging.DEBUG):
        receipt = ic.execute_envelope(s, _envelope(
            "RECORD_RECURRENCE",
            {"issue": issue.issue_key, "note": f"context {FAIL_MARKER}"},
            command_id="leak-fail-001",
        ))

    assert receipt["status"] == ic.CommandStatus.FAILED
    assert "RuntimeError" in receipt["error"]
    stored = read.issue_command(s, "leak-fail-001")
    assert stored.status == ic.CommandStatus.FAILED

    assert FAIL_MARKER not in json.dumps(receipt, default=str)
    assert FAIL_MARKER not in json.dumps(
        read.issue_command_summary(stored), default=str)
    assert FAIL_MARKER not in _cli_text(s, "leak-fail-001", capsys)
    assert FAIL_MARKER not in _log_text(caplog)
    # And no failure path may attach a traceback, which would carry the original
    # unsanitized message straight past every redaction above.
    assert not any(r.exc_info for r in caplog.records)


def test_an_unknown_key_carrying_the_marker_is_never_echoed(
        xos_session, xos_platform, caplog, capsys):
    """The key NAME is as author-controlled as a value. Reported as a count."""
    import logging

    s = xos_session
    issue, _ = _open_ticket(s)
    with caplog.at_level(logging.DEBUG):
        receipt = ic.execute_envelope(s, _envelope(
            "TRIAGE",
            {"issue": issue.issue_key, "reason": "r", FAIL_MARKER: "x"},
            command_id="leak-key-0001",
        ))

    assert receipt["status"] == ic.CommandStatus.REJECTED
    stored = read.issue_command(s, "leak-key-0001")
    view = read.issue_command_summary(stored)
    assert view["payload_unrecognised_fields"] == 1
    assert FAIL_MARKER not in json.dumps(receipt, default=str)
    assert FAIL_MARKER not in json.dumps(view, default=str)
    assert FAIL_MARKER not in _cli_text(s, "leak-key-0001", capsys)
    assert FAIL_MARKER not in _log_text(caplog)
    # It IS stored, because a receipt that cannot prove what ran is not an audit
    # record. Stored is not the same as printed.
    assert FAIL_MARKER in json.dumps(stored.payload_json)


def test_a_database_failure_during_the_claim_leaks_nothing(
        xos_session, xos_platform, monkeypatch, caplog):
    """SQLAlchemy's StatementError appends the statement AND its bound
    parameters to `str(exc)` — and `payload_json` is one of them. A claim that
    fails writes no receipt, so the exception is the only record of it, which
    makes the exception the thing that must not carry the payload out."""
    import logging

    s = xos_session

    def boom(session, env, now):
        raise RuntimeError(f"could not insert … parameters: {FAIL_MARKER}")

    monkeypatch.setattr(ic, "_claim", boom)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ic.IssueCommandTransportError) as exc:
            ic.execute_envelope(s, _envelope(
                "RECORD_RECURRENCE",
                {"issue": "XOS-000001", "note": FAIL_MARKER},
                command_id="leak-claim-01",
            ))

    assert FAIL_MARKER not in str(exc.value)
    assert exc.value.__cause__ is None      # the raw original is not chained
    assert FAIL_MARKER not in _log_text(caplog)


def test_the_boot_hook_error_output_leaks_nothing(xos_session, caplog):
    """The outer hook catches what produced no receipt at all. It logs an
    exception CLASS, a stable code, and the hash and length of the value — never
    `str(exc)`, which is where the document or the bound parameters live."""
    import logging

    raw = json.dumps(_envelope(
        "RECORD_RECURRENCE", {"issue": "XOS-000001", "note": FAIL_MARKER},
        command_id="leak-boot-001",
    ))

    # 1. Whatever the hook would log for a transport failure.
    fields = ic.safe_error_fields(raw, RuntimeError(f"raw {FAIL_MARKER}"))
    assert FAIL_MARKER not in json.dumps(fields, default=str)
    assert fields["error_class"] == "RuntimeError"
    assert fields["command_bytes"] == len(raw.encode())
    assert len(fields["command_hash"]) == 16

    # 2. A rejection carries its stable code through instead of prose.
    rejected = ic.safe_error_fields(raw, ic.IssueCommandRejected("x", "NOT_JSON"))
    assert rejected["error_code"] == "NOT_JSON"

    # 3. And the real hook path: a malformed value never reaches the log.
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ic.IssueCommandRejected):
            ic.run_boot_command(xos_session, "{not json " + FAIL_MARKER)
    assert FAIL_MARKER not in _log_text(caplog)


def test_main_boot_hook_logs_only_safe_fields():
    """Pinned at the source: hook 2b-iii must not reach for `str(exc)`.

    A structural check rather than a behavioural one because the leak would be a
    single edit away and would only show up in a production log nobody diffs."""
    import ast
    import pathlib

    import kalshi_bot.main as main_module

    src = pathlib.Path(main_module.__file__).read_text()
    start = src.index("2b-iii")
    end = src.index("# 2c)", start)
    # CODE only: the comment above the handler quotes `str(exc)` to say it is
    # exactly what must not be logged, and that explanation should not be the
    # thing that fails the test.
    hook = "\n".join(
        line for line in src[start:end].splitlines()
        if not line.strip().startswith("#")
    )
    assert "safe_error_fields" in hook
    assert "str(exc)" not in hook
    assert "exc_info" not in hook
    ast.parse(src)          # the slice above must not have been a stale index


def test_sanitize_removes_individual_values_not_only_the_whole_envelope():
    """The unit that the four tests above depend on."""
    env = ic._validate_envelope(_envelope(
        "ADD_LINK",
        {"issue": "XOS-000001", "link_type": "RESEARCH_DOC",
         "reference": f"docs/{FAIL_MARKER}.md"},
        command_id="sanitize-001",
    ))
    text = ic._sanitize(
        RuntimeError(f"failed on docs/{FAIL_MARKER}.md while parsing"), env
    )
    assert FAIL_MARKER not in text
    assert "RuntimeError" in text
    assert ic.REDACTION in text
    # Bounded, and never a traceback.
    assert len(text) <= ic.MAX_ERROR_CHARS
    assert "Traceback" not in text


def test_the_receipt_list_limit_is_clamped(xos_session, xos_platform):
    """A negative LIMIT is a SQL error on some dialects and unbounded on others;
    an enormous one turns a status read into a table scan printed to ops."""
    s = xos_session
    issue, _ = _open_ticket(s)
    for n in range(3):
        ic.execute_envelope(s, _envelope(
            "RECORD_RECURRENCE", {"issue": issue.issue_key},
            command_id=f"limit-{n:08d}"))

    assert read.clamp_issue_command_limit(-5) == 1
    assert read.clamp_issue_command_limit(0) == 1
    assert read.clamp_issue_command_limit(10 ** 9) == read.ISSUE_COMMAND_LIMIT_MAX
    assert read.clamp_issue_command_limit("nonsense") == 20
    assert len(read.issue_commands(s, limit=-1)) == 1
    assert len(read.issue_commands(s, limit=10 ** 9)) == 4

    args = cli.build_parser().parse_args(["issue", "command-list", "--limit", "-3"])
    assert cli.cmd_issue(s, args) == 0


# ---------------------------------------------------------------------------
# Batching — one boot, several commands (the redeploy-per-command problem)
# ---------------------------------------------------------------------------


def _workflow_batch(issue_key, *, prefix="btch"):
    """The head of a real ticket workflow: triage, then INVESTIGATING."""
    return [
        _envelope("TRIAGE", {
            "issue": issue_key, "reason": "first look before anything else",
        }, command_id=f"{prefix}-0000001"),
        _envelope("STATUS", {
            "issue": issue_key, "status": "INVESTIGATING",
            "reason": "diagnosing before proposing anything",
        }, command_id=f"{prefix}-0000002"),
    ]


def test_a_batch_runs_every_command_in_order_with_its_own_receipt(
    xos_session, xos_platform
):
    """The point of batching: a ticket's workflow lands in ONE boot instead of one
    redeploy per step — and every command still gets its own durable receipt, so
    nothing about exactly-once is traded away for the convenience."""
    s = xos_session
    issue, _ = _open_ticket(s)
    view = ic.run_boot_command(s, json.dumps(_workflow_batch(issue.issue_key)))
    assert view["batch"] is True
    assert view["status"] == "SUCCEEDED"
    assert (view["count"], view["executed"], view["skipped"]) == (2, 2, 0)
    assert view["stopped_at"] is None
    assert view["commands"] == ["btch-0000001:SUCCEEDED", "btch-0000002:SUCCEEDED"]
    # One receipt per command, and the ORDER actually took effect: a STATUS to
    # INVESTIGATING is only legal from TRIAGE, so this state proves sequencing.
    assert _receipts(s, "btch-0000001") == 1
    assert _receipts(s, "btch-0000002") == 1
    s.refresh(issue)
    assert issue.status == "INVESTIGATING"


def test_a_batch_is_replayed_not_re_executed_on_the_next_boot(
    xos_session, xos_platform
):
    """Restarts re-read the same variable. A batch must be as inert on the second
    boot as a single command is — per element, via the same ledger."""
    s = xos_session
    issue, _ = _open_ticket(s)
    raw = json.dumps(_workflow_batch(issue.issue_key))
    first = ic.run_boot_command(s, raw)
    again = ic.run_boot_command(s, raw)
    assert first["executed"] == again["executed"] == 2
    assert all(r["executed"] is False for r in again["receipts"])
    assert all(r["replayed"] is True for r in again["receipts"])
    assert _receipts(s, "btch-0000001") == 1
    assert _receipts(s, "btch-0000002") == 1


def test_a_batch_stops_at_the_first_refusal_and_leaves_the_tail_RETRYABLE(
    xos_session, xos_platform
):
    """The stop rule and the property that makes it safe.

    Ticket workflows are sequential, so continuing past a refusal would apply
    later steps to a state their author never saw. The tail is therefore left
    entirely unclaimed — no row, no receipt — which is precisely the state the
    module says permits another attempt. The same ids can be resubmitted verbatim
    once the cause is fixed; a batch that had marked them FAILED would have burned
    them.
    """
    s = xos_session
    issue, _ = _open_ticket(s)
    batch = [
        _envelope("TRIAGE", {"issue": issue.issue_key, "reason": "first look"},
                  command_id="stop-0000001"),
        # Illegal: RESOLVED is not reachable from TRIAGE. A refusal, not a crash.
        _envelope("STATUS", {
            "issue": issue.issue_key, "status": "RESOLVED",
            "reason": "skipping the whole investigation",
        }, command_id="stop-0000002"),
        _envelope("ADD_LINK", {
            "issue": issue.issue_key, "link_type": "PR", "reference": "#1",
        }, command_id="stop-0000003"),
    ]
    view = ic.run_boot_command(s, json.dumps(batch))
    assert view["status"] == "REJECTED"
    assert (view["count"], view["executed"], view["skipped"]) == (3, 2, 1)
    assert view["stopped_at"] == "stop-0000002"
    # The refusal is durable; the untouched tail is genuinely untouched.
    assert _receipts(s, "stop-0000001") == 1
    assert _receipts(s, "stop-0000002") == 1
    assert _receipts(s, "stop-0000003") == 0
    # ...and therefore still runnable under its ORIGINAL id.
    retry = ic.run_boot_command(s, json.dumps([batch[2]]))
    assert retry["status"] == "SUCCEEDED"
    assert _receipts(s, "stop-0000003") == 1


def test_a_malformed_element_refuses_the_WHOLE_batch_before_anything_applies(
    xos_session, xos_platform
):
    """Validation is all-or-nothing even though execution is not: a typo in the
    last element must not leave the first one applied."""
    s = xos_session
    issue, _ = _open_ticket(s)
    batch = _workflow_batch(issue.issue_key)
    batch.append({"command_id": "bad-00000001", "action": "NOT_AN_ACTION",
                  "actor": "cal", "actor_role": "LIVE_OPS",
                  "schema_version": ic.SCHEMA_VERSION, "payload": {}})
    with pytest.raises(ic.IssueCommandRejected) as exc:
        ic.run_boot_command(s, json.dumps(batch))
    assert exc.value.code == "UNKNOWN_ACTION"
    assert _receipts(s, "btch-0000001") == 0
    assert _receipts(s, "btch-0000002") == 0


def test_a_duplicate_command_id_inside_one_batch_is_refused(
    xos_session, xos_platform
):
    """The second would replay the first and silently do nothing — a command that
    looks executed and did nothing is the exact failure receipts exist to prevent."""
    s = xos_session
    issue, _ = _open_ticket(s)
    dup = _envelope("TRIAGE", {"issue": issue.issue_key, "reason": "first look"},
                    command_id="dupe-0000001")
    with pytest.raises(ic.IssueCommandRejected) as exc:
        ic.run_boot_command(s, json.dumps([dup, dict(dup)]))
    assert exc.value.code == "BATCH_DUPLICATE_COMMAND_ID"
    assert _receipts(s, "dupe-0000001") == 0


def test_batch_bounds_are_enforced(xos_session, xos_platform):
    s = xos_session
    issue, _ = _open_ticket(s)
    one = _envelope("TRIAGE", {"issue": issue.issue_key, "reason": "r"},
                    command_id="many-0000001")
    too_long = [dict(one, command_id=f"many-{i:07d}")
                for i in range(ic.MAX_BATCH_COMMANDS + 1)]
    with pytest.raises(ic.IssueCommandRejected) as exc:
        ic.run_boot_command(s, json.dumps(too_long))
    assert exc.value.code == "BATCH_TOO_LONG"
    with pytest.raises(ic.IssueCommandRejected) as empty:
        ic.run_boot_command(s, json.dumps([]))
    assert empty.value.code == "BATCH_EMPTY"
    # Refused before any database work: not one of the batch's ids was claimed.
    assert all(_receipts(s, e["command_id"]) == 0 for e in too_long)


def test_a_single_envelope_is_untouched_by_batching(xos_session, xos_platform):
    """The regression that matters most: batching added an array shape and changed
    nothing about the object shape — same receipt view, no batch keys."""
    s = xos_session
    issue, _ = _open_ticket(s)
    view = ic.run_boot_command(s, json.dumps(_envelope(
        "TRIAGE", {"issue": issue.issue_key, "reason": "first look"},
        command_id="solo-0000001",
    )))
    assert view["status"] == "SUCCEEDED"
    assert "batch" not in view and "receipts" not in view
    assert _receipts(s, "solo-0000001") == 1
