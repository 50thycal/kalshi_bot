"""XOS-000005 — the receipt reads: reachable, distinguishable, silent, inert.

The ticket was a documentation/runtime mismatch: `issue-command-show` and
`issue-command-list` were documented as ops-allowlisted, and the deployed runner
refused them. The CLI has always had both, and the runner's allowlist on the
default branch has always carried both — what was stale was the copy of the
runner deployed on the `ops` branch.

Which makes the durable repair a test rather than an entry. `test_every_xos_
command_the_docs_advertise_is_allowlisted` closes the class of defect the ticket
is named for: a command can no longer be documented into existence. The rest pin
the properties that make these two commands safe to expose at all, because the
channel that reads them publishes its own results.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest
from sqlalchemy import event

from kalshi_bot.experiment_os import cli, read
from kalshi_bot.experiment_os import issue_commands as ic

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

#: Every `{"type":"xos","command":"…"}` example the operator-facing docs show.
_DOC_XOS = re.compile(r'"type"\s*:\s*"xos"\s*,\s*"command"\s*:\s*"([a-z-]+)"')
_DOCS = ("docs/OPS_RUNBOOK.md", "docs/EXPERIMENT_OS_ISSUES.md")

#: Every worker-side write transport. Each is a variable an operator is told to
#: SET through the ops channel, so each has to clear the `env` allowlist as well
#: as exist in the config — two separate lists that have to agree.
_TRANSPORT_VARS = (
    "EXPERIMENT_OS_ISSUE_COMMAND",
    "EXPERIMENT_OS_PLATFORM_COMMAND",
    "EXPERIMENT_OS_EXPERIMENT_COMMAND",
)


def _runner():
    import ops_runner

    return ops_runner


def _allowlist() -> set[str]:
    """The runner's effective xos allowlist, read out of the source it enforces.

    Taken from the module rather than restated here: a copy in the test would
    pass on the day the runner's own set regressed, which is precisely the
    failure this file exists to prevent.

    Read through the runner's own `xos_allowlist()` rather than by parsing its
    source. The regex version missed an alias map the day a second one was added
    — it saw only the literal inside `run()` — and reported the commands routed
    through that map as unallowlisted. One function, called by both sides, cannot
    drift that way.
    """
    return _runner().xos_allowlist()


# ---------------------------------------------------------------------------
# The defect the ticket is named for
# ---------------------------------------------------------------------------


def test_every_xos_command_the_docs_advertise_is_allowlisted():
    """A command cannot be documented into existence.

    XOS-000005 was exactly this gap in the other direction: two commands the
    runbook told an operator to run, refused by the runner. A runbook step that
    cannot be performed is worse than a missing one — it strands whoever follows
    it, mid-procedure, with a write already submitted.
    """
    allowed = _allowlist()
    documented: dict[str, set[str]] = {}
    for rel in _DOCS:
        text = (REPO / rel).read_text()
        documented[rel] = set(_DOC_XOS.findall(text))

    assert documented, "the docs should show xos examples"
    # Not vacuous: the extractor must actually see the pair this ticket is about,
    # otherwise a regex that stopped matching would turn this into a green no-op.
    runbook = documented["docs/OPS_RUNBOOK.md"]
    assert {"issue-command-show", "issue-command-list"} <= runbook
    for rel, commands in documented.items():
        missing = sorted(commands - allowed)
        assert not missing, f"{rel} advertises xos commands the runner refuses: {missing}"


@pytest.mark.parametrize("var", _TRANSPORT_VARS)
def test_every_write_transport_variable_is_settable_through_the_channel(var):
    """The same defect class as the test above, on the OTHER half of the channel.

    A transport is reachable only if its variable clears `railway_env`'s `env`
    allowlist. That list and the worker's config are separate, so a transport can
    exist, be documented, be tested end to end — and still be unreachable,
    because the one list nobody thought about refuses the variable by name.

    That is exactly what happened to `EXPERIMENT_OS_EXPERIMENT_COMMAND`: the
    module, the boot hook, the config field, the receipt reads, the runbook
    section and 28 tests all shipped, and the first real attempt to use it came
    back `refusing to set non-allowlisted vars`. The runbook advertised a
    variable the runner refused — a documented-into-existence defect, on the env
    side rather than the xos side.
    """
    import railway_env

    assert var in railway_env.ALLOWED_VARS, (
        f"{var} is a write transport the docs tell an operator to set, but the "
        "env allowlist refuses it — the transport is unreachable"
    )


@pytest.mark.parametrize("var", _TRANSPORT_VARS)
def test_every_write_transport_variable_has_its_value_redacted(var):
    """Ops results are committed to a public repository. A variable carrying a
    structured command body is echoed as a hash and a length, never its contents
    — output hygiene, not confidentiality (the same bytes ride the public ops
    branch), but a transport added without it would start printing payloads into
    a channel that publishes them."""
    import railway_env

    assert var in railway_env.REDACTED_VARS


def test_the_runbook_advertises_no_variable_the_channel_refuses():
    """Anything the runbook shows inside an `env` set block has to be settable.

    Derived from the docs rather than from a list, so a future transport
    documented without an allowlist entry fails here instead of at the moment an
    operator tries to use it mid-procedure."""
    import railway_env

    text = (REPO / "docs/OPS_RUNBOOK.md").read_text()
    advertised = set(re.findall(r'"(EXPERIMENT_OS_[A-Z_]+)"\s*:', text))
    # Not vacuous: the extractor must see the transports this test is about.
    assert {"EXPERIMENT_OS_EXPERIMENT_COMMAND"} <= advertised, advertised
    missing = sorted(advertised - set(railway_env.ALLOWED_VARS))
    assert not missing, f"the runbook advertises un-settable variables: {missing}"


def test_the_two_receipt_reads_are_reachable_and_route_to_the_cli():
    """The specific pair the ticket names, and where each one lands."""
    runner = _runner()
    assert runner.XOS_ISSUE_READS["issue-command-show"] == ["issue", "command-show"]
    assert runner.XOS_ISSUE_READS["issue-command-list"] == ["issue", "command-list"]
    assert {"issue-command-show", "issue-command-list"} <= _allowlist()


def test_the_receipt_reads_are_classified_as_reads_by_the_cli_itself():
    """The read-action invariant, for this pair by name.

    The general invariant already asserts every exposed subcommand is one the CLI
    calls a read. Naming these two as well means a future edit that dropped them
    from `_ISSUE_READ_ACTIONS` — and so, silently, from safety — fails here
    rather than passing a set-comparison that no longer covers them.
    """
    assert "command-show" in cli._ISSUE_READ_ACTIONS
    assert "command-list" in cli._ISSUE_READ_ACTIONS
    parser = cli.build_parser()
    assert parser is not None


def test_no_write_action_is_reachable_from_the_ops_channel():
    runner = _runner()
    exposed = {argv[1] for argv in runner.XOS_ISSUE_READS.values()}
    assert exposed <= cli._ISSUE_READ_ACTIONS
    assert not (exposed & set(ic.ACTIONS))


# ---------------------------------------------------------------------------
# SUCCEEDED vs REJECTED, without publishing the envelope
# ---------------------------------------------------------------------------


def _succeeded_and_rejected(s):
    """One receipt of each terminal kind, from the real transport.

    The rejected one carries a long free-text value and an invented key, which is
    what the disclosure assertions below need: a receipt that could leak.
    """
    ok = ic.execute_envelope(s, {
        "command_id": "receipt-ok-0001",
        "action": "OPEN_MANUAL",
        "actor": "cal",
        "actor_role": "LIVE_OPS",
        "schema_version": ic.SCHEMA_VERSION,
        "payload": {
            "title": "a readable ticket",
            "problem_statement": "SECRETVALUE-in-the-problem-statement",
            "classification": "OPS",
            "owner_role": "LIVE_OPS",
            "reason": "because",
        },
    })
    bad = ic.execute_envelope(s, {
        "command_id": "receipt-bad-0001",
        "action": "STATUS",
        "actor": "cal",
        "actor_role": "LIVE_OPS",
        "schema_version": ic.SCHEMA_VERSION,
        "payload": {
            "issue": "XOS-999999",
            "status": "RESOLVED",
            "reason": "SECRETVALUE-in-the-reason",
            "invented_key_name": "SECRETVALUE-in-an-unknown-field",
        },
    })
    s.commit()
    return ok, bad


def test_succeeded_and_rejected_receipts_are_distinguishable(xos_session, xos_platform):
    """The capability the gap cost: from `issue-show` alone, a rejected envelope
    and one that never ran look identical."""
    s = xos_session
    ok, bad = _succeeded_and_rejected(s)
    assert ok["status"] == ic.CommandStatus.SUCCEEDED
    assert bad["status"] == ic.CommandStatus.REJECTED

    shown_ok = read.issue_command_summary(read.issue_command(s, "receipt-ok-0001"))
    shown_bad = read.issue_command_summary(read.issue_command(s, "receipt-bad-0001"))
    assert shown_ok["status"] != shown_bad["status"]
    assert shown_bad["error"], "a rejection must say why"
    assert shown_ok["payload_hash"] != shown_bad["payload_hash"]


def test_a_receipt_never_publishes_a_submitted_payload_value(xos_session, xos_platform):
    """The channel that reads these commits its own results to a public branch."""
    s = xos_session
    _succeeded_and_rejected(s)
    for command_id in ("receipt-ok-0001", "receipt-bad-0001"):
        rendered = json.dumps(
            read.issue_command_summary(read.issue_command(s, command_id)), default=str
        )
        assert "SECRETVALUE" not in rendered


def test_a_receipt_never_publishes_an_unrecognised_key_name(xos_session, xos_platform):
    """An invented key is as author-controlled as an invented value."""
    s = xos_session
    _succeeded_and_rejected(s)
    shown = read.issue_command_summary(read.issue_command(s, "receipt-bad-0001"))
    rendered = json.dumps(shown, default=str)
    assert "invented_key_name" not in rendered
    assert shown["payload_unrecognised_fields"] >= 1      # counted, never named
    assert "issue" in shown["payload_fields"]             # recognised names are fine


def test_the_rejection_reason_is_sanitized_of_the_envelope(xos_session, xos_platform):
    """Exceptions quote things. The receipt's error must not quote the payload."""
    s = xos_session
    _succeeded_and_rejected(s)
    row = read.issue_command(s, "receipt-bad-0001")
    assert "SECRETVALUE" not in (row.error or "")
    assert "invented_key_name" not in (row.error or "")


# ---------------------------------------------------------------------------
# Bounds and inertness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "asked,expected", [(-5, 1), (0, 1), (1, 1), (20, 20), (100, 100), (101, 100), (10**9, 100)]
)
def test_the_list_limit_is_clamped_to_1_100(asked, expected):
    assert read.clamp_issue_command_limit(asked) == expected


def test_the_list_limit_clamp_survives_junk():
    assert read.clamp_issue_command_limit(None) >= 1
    assert read.clamp_issue_command_limit("nonsense") >= 1


class _WriteSpy:
    """Records every DML statement the session emits at the driver boundary.

    Spying on the connection rather than on `Session.add` is deliberate: it
    catches a write however it is issued — ORM flush, bulk operation, or raw
    `session.execute` — which is the only version of "inert" worth asserting.
    """

    def __init__(self, session):
        self.statements: list[str] = []
        self.flushes = 0
        self.session = session
        self._conn_listener = None

    def __enter__(self):
        bind = self.session.get_bind()

        @event.listens_for(bind, "before_cursor_execute")
        def _cursor(conn, cursor, statement, parameters, context, executemany):
            self.statements.append(statement.strip().lower())

        @event.listens_for(self.session, "before_flush")
        def _flush(session, flush_context, instances):
            self.flushes += 1

        self._conn_listener = (bind, _cursor, _flush)
        return self

    def __exit__(self, *exc):
        bind, cursor_fn, flush_fn = self._conn_listener
        event.remove(bind, "before_cursor_execute", cursor_fn)
        event.remove(self.session, "before_flush", flush_fn)
        return False

    @property
    def writes(self) -> list[str]:
        return [
            s for s in self.statements
            if s.startswith(("insert", "update", "delete", "create", "drop", "alter"))
        ]


def test_the_write_spy_actually_detects_a_write(xos_session, xos_platform):
    """A spy that never fires proves nothing.

    Before trusting the inertness assertions below, show the same spy catching a
    real write on the same session — otherwise "no DML observed" could just mean
    the listener was never wired up.
    """
    s = xos_session
    with _WriteSpy(s) as spy:
        _succeeded_and_rejected(s)
    assert spy.writes, "the spy failed to observe the transport's own writes"
    assert any(stmt.startswith("insert") for stmt in spy.writes)
    assert spy.flushes > 0


def test_reading_one_receipt_writes_nothing(xos_session, xos_platform):
    s = xos_session
    _succeeded_and_rejected(s)
    with _WriteSpy(s) as spy:
        row = read.issue_command(s, "receipt-ok-0001")
        read.issue_command_summary(row)
    assert spy.writes == [], f"the receipt read emitted DML: {spy.writes}"
    assert spy.flushes == 0


def test_listing_receipts_writes_nothing(xos_session, xos_platform):
    s = xos_session
    _succeeded_and_rejected(s)
    with _WriteSpy(s) as spy:
        rows = read.issue_commands(s, limit=50)
        [read.issue_command_summary(r) for r in rows]
    assert len(rows) == 2
    assert spy.writes == []
    assert spy.flushes == 0


def test_reading_a_receipt_cannot_re_execute_or_retry_the_command(xos_session, xos_platform):
    """A reader that could act on a receipt turns an audit record into a control
    surface. Nothing about the ticket changes, and no new receipt appears."""
    s = xos_session
    _succeeded_and_rejected(s)
    before = {r.command_id: (r.status, r.completed_at) for r in read.issue_commands(s, limit=100)}

    with _WriteSpy(s) as spy:
        for command_id in before:
            read.issue_command_summary(read.issue_command(s, command_id))
        read.issue_commands(s, limit=100)

    after = {r.command_id: (r.status, r.completed_at) for r in read.issue_commands(s, limit=100)}
    assert after == before
    assert spy.writes == []


def test_the_read_surface_imports_nothing_that_could_execute_a_command():
    """Structural, not behavioural: the ops path reaches `read`, and `read` holds
    no reference to the executor."""
    source = pathlib.Path(read.__file__).read_text()
    for forbidden in ("execute_envelope", "run_boot_command", "session.add(", "session.commit("):
        assert forbidden not in source, f"read.py must not reference {forbidden}"


def test_a_missing_receipt_is_a_clean_miss_not_an_error(xos_session):
    assert read.issue_command(xos_session, "never-submitted-0001") is None
