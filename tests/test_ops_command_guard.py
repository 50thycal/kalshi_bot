"""The unconsumed-command guard on the Experiment OS write transports.

The hazard it exists for only appears with more than one session driving the ops
channel: the three command transports are single-slot environment variables
consumed at the worker's next boot, so a second session that sets one before the
first session's boot lands discards that envelope silently. Nothing is corrupted —
the ledger keeps exactly-once per `command_id` — and nothing complains, because
the ledger is only ever asked AFTER the fact.

What these tests pin is the shape of the answer, not just the happy path:

  * a confirmed collision REFUSES the whole request, before any write;
  * a spent slot (every command terminal) is freely replaceable — the common case,
    since a session leaves its last command sitting in the variable;
  * every uncertainty FAILS OPEN, because this guard sits in front of the only
    authorized production write path and a closed failure would turn a database
    blip into "nobody can record anything";
  * a fail-open is never silent: an inconclusive check says so, so "checked and
    clear" can never be confused with "could not check";
  * the override is explicit and recorded.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load("ops_command_guard")

ISSUE_VAR = "EXPERIMENT_OS_ISSUE_COMMAND"


def _env(command_id):
    return {"command_id": command_id, "action": "TRIAGE", "actor": "cal",
            "actor_role": "LIVE_OPS", "schema_version": 1, "payload": {}}


# ---------------------------------------------------------------------------
# Reading command ids out of a transport value
# ---------------------------------------------------------------------------


def test_command_ids_reads_both_the_single_and_the_batch_shape():
    assert guard.command_ids(json.dumps(_env("a-0000001"))) == ["a-0000001"]
    assert guard.command_ids(
        json.dumps([_env("a-0000001"), _env("b-0000002")])
    ) == ["a-0000001", "b-0000002"]
    assert guard.command_ids("") == []
    assert guard.command_ids("   ") == []


def test_an_unreadable_value_is_UNKNOWN_not_empty():
    """The distinction the whole guard rests on. `[]` means 'understood, nothing
    there' and permits an overwrite; None means 'could not tell' and must not be
    silently treated as the former."""
    for bad in ("not json", "[1, 2]", '{"no_command_id": true}', '"a string"',
                json.dumps([_env("ok-000001"), {"command_id": ""}])):
        assert guard.command_ids(bad) is None, bad


def test_an_absurdly_large_value_is_not_parsed():
    assert guard.command_ids("x" * (guard.MAX_INSPECT_BYTES + 1)) is None


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


def test_a_variable_that_is_not_a_command_transport_is_never_guarded():
    out = guard.check("KILL_SWITCH", "false")
    assert (out.blocked, out.conclusive) == (False, True)


def test_an_empty_slot_is_free():
    out = guard.check(ISSUE_VAR, "")
    assert (out.blocked, out.conclusive) == (False, True)


def test_a_slot_whose_commands_all_have_terminal_receipts_is_replaceable(monkeypatch):
    """The common case: a session that finished its work leaves its last command
    behind in the variable. That is spent, not pending."""
    monkeypatch.setattr(guard, "_terminal_ids",
                        lambda table, ids: {"a-0000001", "b-0000002"})
    out = guard.check(ISSUE_VAR, json.dumps([_env("a-0000001"), _env("b-0000002")]))
    assert (out.blocked, out.conclusive) == (False, True)
    assert out.unconsumed == []


def test_an_unconsumed_command_BLOCKS_and_names_it(monkeypatch):
    """The collision this exists to catch: another session's envelope is still
    waiting for the boot that would run it."""
    monkeypatch.setattr(guard, "_terminal_ids", lambda table, ids: {"a-0000001"})
    out = guard.check(ISSUE_VAR, json.dumps([_env("a-0000001"), _env("b-0000002")]))
    assert out.blocked is True and out.conclusive is True
    assert out.unconsumed == ["b-0000002"]
    assert "b-0000002" in out.reason
    assert "force_replace" in out.reason  # the refusal names its own override


def test_a_command_with_NO_ledger_row_counts_as_unconsumed(monkeypatch):
    """Including the platform transport's DEFERRED outcome, which deliberately
    writes no row so the same envelope runs again on a later boot. A deferred
    cutover is exactly the thing that must not be silently discarded."""
    monkeypatch.setattr(guard, "_terminal_ids", lambda table, ids: set())
    out = guard.check("EXPERIMENT_OS_PLATFORM_COMMAND", json.dumps(_env("c-0000003")))
    assert out.blocked is True
    assert out.unconsumed == ["c-0000003"]


def test_a_RUNNING_receipt_is_not_terminal():
    """RUNNING means a worker claimed it and has not finished. Only SUCCEEDED,
    REJECTED and FAILED free the slot."""
    assert "RUNNING" not in guard.TERMINAL_STATUSES
    assert guard.TERMINAL_STATUSES == {"SUCCEEDED", "REJECTED", "FAILED"}


# ---------------------------------------------------------------------------
# Fail-open, loudly
# ---------------------------------------------------------------------------


def test_an_unreadable_ledger_fails_OPEN_but_INCONCLUSIVE(monkeypatch):
    monkeypatch.setattr(guard, "_terminal_ids", lambda table, ids: None)
    out = guard.check(ISSUE_VAR, json.dumps(_env("a-0000001")))
    assert out.blocked is False, "a database problem must never block the only write path"
    assert out.conclusive is False, "but it must never look like a clean pass"


def test_an_unreadable_current_value_fails_OPEN_but_INCONCLUSIVE():
    for value in (None, "not json at all"):
        out = guard.check(ISSUE_VAR, value)
        assert out.blocked is False
        assert out.conclusive is False


def test_every_transport_has_a_ledger_and_no_others_do():
    assert set(guard.TRANSPORT_LEDGERS) == {
        "EXPERIMENT_OS_ISSUE_COMMAND",
        "EXPERIMENT_OS_PLATFORM_COMMAND",
        "EXPERIMENT_OS_EXPERIMENT_COMMAND",
    }


def test_the_guarded_variables_are_exactly_the_write_transports():
    """A drift check: if a fourth command transport is added to the env allowlist
    without a ledger here, it silently loses the guard."""
    renv = _load("railway_env")
    from_allowlist = {
        v for v in renv.ALLOWED_VARS
        if v.startswith("EXPERIMENT_OS_") and v.endswith("_COMMAND")
    }
    assert from_allowlist == set(guard.TRANSPORT_LEDGERS)


def test_the_receipt_shape_carries_the_verdict_and_never_the_envelope(monkeypatch):
    monkeypatch.setattr(guard, "_terminal_ids", lambda table, ids: set())
    payload = json.dumps(_env("a-0000001"))
    receipt = guard.check(ISSUE_VAR, payload).as_receipt()
    assert set(receipt) == {"blocked", "conclusive", "reason", "unconsumed_command_ids"}
    # Command ids are author-chosen labels that already appear in public receipts;
    # the envelope body is not, and must not ride out through the guard.
    assert "payload" not in json.dumps(receipt)
    assert "LIVE_OPS" not in json.dumps(receipt)
