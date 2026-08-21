"""Worker-side transport for ordinary Experiment OS issue writes.

Why this exists
---------------
A Claude session can READ production issue state through the ops channel, but it
cannot write: the ops channel connects with `DATABASE_URL_RO` by design, every
issue mutation refuses that connection, and the sandbox cannot reach Railway or
Postgres directly. So Live Ops and Research Lab could open a ticket locally and
never in production. A CLI that cannot reach production is not an operational
workflow.

This module is the missing door, and it is deliberately a narrow one. It executes
ONE strictly validated envelope per boot, chosen from a fixed vocabulary of
seventeen issue operations, each of which calls the existing function in
`issues.py` and reimplements none of its rules. It cannot run SQL, Python, shell,
a lifecycle transition, a gate evaluation or mutation, a Version or Epoch
creation, a Platform Revision operation, or anything that arms live trading or
moves exposure — not because it declines to, but because no code path exists.

**The transport is public.**
-----------------------------
The envelope travels as an allowlisted Railway environment variable, and it gets
there by being committed in plaintext to `ops/request.json` on the **public**
`ops` branch, where it stays in Git history forever. The redaction in
`scripts/railway_env.py` keeps the value out of ops results, worker logs and the
receipt read surface, but that is **output hygiene, not confidentiality** — it
narrows accidental copies, it does not make the channel private.

Therefore a payload must be safe for public disclosure. Do not put in one:

* secrets or credentials of any kind;
* personal information;
* private logs or raw operational captures;
* account, order, fill or position identifiers;
* sensitive raw evidence.

Prefer a bounded summary and a public source reference — a document path, a PR
number, an `xos` command someone else can run — over pasted content. Evidence is
*cited* by this workflow, not copied into it, which is the same rule the issue
service already applies. If a ticket genuinely requires private content, STOP:
propose an encrypted transport instead. Redaction cannot make this channel
private, and using it anyway publishes the content.

Exactly-once
------------
`railway.json` restarts the worker on failure up to ten times, and every restart
re-reads the same variable, so "one-shot" cannot be a property of the transport.
It is a property of the ledger. `experiment_os_issue_commands.command_id` is
UNIQUE and a command is claimed with `INSERT … ON CONFLICT DO NOTHING RETURNING`
— not by catching a unique-constraint error afterwards. A second worker booting
at the same instant blocks on the uncommitted key, then finds no row returned and
returns the winner's receipt without executing anything.

A committed receipt is **terminal**. `SUCCEEDED`, `REJECTED` and `FAILED` are all
final for that `command_id`; retrying means submitting a NEW `command_id`. The
absence of a receipt is the only state that permits another attempt, and it is
exactly the state in which the mutation cannot have committed either.

The transaction shape, which is the whole correctness argument:

    validate + canonicalize + hash the envelope     (no database work yet)
    claim  INSERT … ON CONFLICT DO NOTHING RETURNING id
      no row  -> read the receipt, compare hashes, return it; execute nothing
      row     -> SAVEPOINT
                   validate the payload for this action
                   call the one issues.py function
                 success  -> release; receipt SUCCEEDED; COMMIT (mutation+receipt)
                 refusal  -> ROLLBACK TO SAVEPOINT; receipt REJECTED; COMMIT
                 failure  -> ROLLBACK TO SAVEPOINT; receipt FAILED;   COMMIT

Because the claim row is inserted OUTSIDE the savepoint, rolling the mutation back
never rolls back the receipt, and the receipt is never written by a second
transaction that could itself fail and leave a mutation unexplained. If the
database is broken badly enough that even the receipt cannot commit, nothing
commits: no receipt, no mutation, and the next boot may legitimately retry.

`actor_role` is **attribution, not authorization**. This transport cannot verify
who anyone is. The real authority is who can push to the `ops` branch and who
holds the Railway token — unchanged by this module. The role is validated against
the policy vocabulary so the recorded history is well formed, and then handed to
the issue service, which applies its own routing rules. Nothing here grants a
permission, and transporting a command implies approval for no canonical
Experiment OS action whatsoever.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import issue_policy as policy
from . import issues
from .models import ExperimentOsIssueCommand

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIONS",
    "CommandStatus",
    "IssueCommandRejected",
    "SCHEMA_VERSION",
    "canonical_envelope",
    "envelope_hash",
    "execute_envelope",
    "run_boot_command",
]

#: The envelope contract. Bumping this is a breaking change to the transport and
#: needs a matching read of every stored receipt, so it is pinned and checked.
SCHEMA_VERSION = 1

#: Exactly these six top-level keys. Unknown keys are refused rather than ignored:
#: a typo'd field that is silently dropped is a command that did something other
#: than what its author wrote.
ENVELOPE_KEYS = frozenset(
    {"command_id", "action", "actor", "actor_role", "payload", "schema_version"}
)

# Bounds. The envelope arrives through an environment variable and a JSON file in
# a Git repository, neither of which has a natural size limit, so this module
# supplies one. They are generous for a ticket update and small enough that a
# malformed or hostile value cannot become a memory or storage problem.
MAX_ENVELOPE_BYTES = 8192
MAX_STRING_CHARS = 4000
MAX_PAYLOAD_KEYS = 12
MAX_ERROR_CHARS = 400

_COMMAND_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9._@ -]{1,64}$")


class CommandStatus:
    """Receipt states. All three terminal states are final for a `command_id`."""

    SUCCEEDED = "SUCCEEDED"
    #: The envelope was well formed but the workflow refused it — a bad enum, an
    #: illegal transition, a missing issue, a transfer without evidence. The
    #: refusal is the system working, so it is recorded, not retried.
    REJECTED = "REJECTED"
    #: Something unexpected broke inside the executor. Auditable, and terminal:
    #: retrying needs a new command_id, because a silent re-run of a command that
    #: failed halfway through an unknown code path is how duplicates are made.
    FAILED = "FAILED"


class IssueCommandRejected(ValueError):
    """The envelope violates the transport contract (shape, size, vocabulary).

    Distinct from the issue service's own refusals: this one means the command
    never reached the workflow at all.
    """


# ---------------------------------------------------------------------------
# Envelope validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Envelope:
    command_id: str
    action: str
    actor: str
    actor_role: str
    payload: dict
    schema_version: int
    canonical: str
    payload_hash: str


def canonical_envelope(envelope: dict) -> str:
    """The exact bytes that get hashed: sorted keys, no incidental whitespace.

    Canonicalisation is what makes "the same command" a decidable question. Two
    submissions that differ only in key order or spacing are the same command and
    must replay; two that differ in any value are a collision and must not.
    """
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def envelope_hash(envelope: dict) -> str:
    """sha256 over the canonical envelope — the WHOLE envelope, not just the
    payload, despite the column being called `payload_hash`. Resubmitting an id
    under a different actor or role is as much a collision as changing a value."""
    return hashlib.sha256(canonical_envelope(envelope).encode("utf-8")).hexdigest()


def _validate_envelope(envelope: Any) -> _Envelope:
    """Structural validation of the addressable part: identity, action, size.

    Deliberately does NOT validate the per-action payload. Everything checked here
    is what makes a receipt trustworthy — if the `command_id` itself cannot be
    believed, recording a receipt under it would be worse than refusing. Payload
    validation happens after the claim, so a bad payload produces a durable
    REJECTED receipt the operator can actually read back.
    """
    if not isinstance(envelope, dict):
        raise IssueCommandRejected(
            f"envelope must be a JSON object, got {type(envelope).__name__}"
        )

    unknown = sorted(set(envelope) - ENVELOPE_KEYS)
    if unknown:
        raise IssueCommandRejected(
            f"unknown top-level field(s) {unknown}; legal: {sorted(ENVELOPE_KEYS)}"
        )
    missing = sorted(ENVELOPE_KEYS - set(envelope))
    if missing:
        raise IssueCommandRejected(f"envelope is missing required field(s) {missing}")

    version = envelope["schema_version"]
    if version != SCHEMA_VERSION:
        raise IssueCommandRejected(
            f"schema_version must be {SCHEMA_VERSION}, got {version!r}"
        )

    command_id = envelope["command_id"]
    if not isinstance(command_id, str) or not _COMMAND_ID_RE.match(command_id):
        raise IssueCommandRejected(
            "command_id must be 8-64 chars of [A-Za-z0-9._-]; it is the sole basis "
            "of exactly-once, so it must be stable, unique and legible"
        )

    action = envelope["action"]
    if not isinstance(action, str) or action not in ACTIONS:
        raise IssueCommandRejected(
            f"action {action!r} is not in the allowed vocabulary: {sorted(ACTIONS)}"
        )

    actor = envelope["actor"]
    if not isinstance(actor, str) or not _ACTOR_RE.match(actor):
        raise IssueCommandRejected(
            "actor must be 1-64 chars of [A-Za-z0-9._@ -] naming who is acting"
        )

    # Attribution, not authorization — see the module docstring. Validated against
    # the OPENING role set (owner roles plus the read-only detecting ones) because
    # the Control Tower may legitimately be the actor that hands a candidate over;
    # each issues.py function then applies its own, stricter rule about who may own.
    try:
        actor_role = policy.validate_opening_role(envelope["actor_role"])
    except policy.IssuePolicyError as exc:
        raise IssueCommandRejected(str(exc)) from None

    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise IssueCommandRejected(
            f"payload must be a JSON object, got {type(payload).__name__}"
        )

    canonical = canonical_envelope(envelope)
    size = len(canonical.encode("utf-8"))
    if size > MAX_ENVELOPE_BYTES:
        raise IssueCommandRejected(
            f"envelope is {size} bytes; the limit is {MAX_ENVELOPE_BYTES}. Cite "
            "evidence by reference instead of pasting it"
        )

    return _Envelope(
        command_id=command_id,
        action=action,
        actor=actor,
        actor_role=actor_role,
        payload=payload,
        schema_version=version,
        canonical=canonical,
        payload_hash=envelope_hash(envelope),
    )


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

#: Payload keys whose value must be a JSON boolean. Everything else must be a
#: string: no nested objects, no arrays, no numbers. That flatness is deliberate —
#: it keeps the envelope legible in a diff and leaves nowhere to smuggle structure
#: past the per-action key check.
_BOOL_KEYS = frozenset({
    "passed",
    "advance",
    "requires_new_version",
    "requires_new_epoch",
    "requires_platform_revision",
    "requires_pause_or_stand_down",
})


def _check_payload(action: str, payload: dict) -> None:
    spec = ACTIONS[action]
    keys = set(payload)
    unknown = sorted(keys - spec.required - spec.optional)
    if unknown:
        legal = sorted(spec.required | spec.optional)
        raise IssueCommandRejected(
            f"{action}: unknown payload field(s) {unknown}; legal: {legal}"
        )
    missing = sorted(spec.required - keys)
    if missing:
        raise IssueCommandRejected(f"{action}: missing required field(s) {missing}")
    if len(keys) > MAX_PAYLOAD_KEYS:
        raise IssueCommandRejected(
            f"{action}: {len(keys)} payload fields exceeds the cap of {MAX_PAYLOAD_KEYS}"
        )

    for key in sorted(keys):
        value = payload[key]
        if key in _BOOL_KEYS:
            if not isinstance(value, bool):
                raise IssueCommandRejected(
                    f"{action}.{key} must be a JSON boolean, got "
                    f"{type(value).__name__}"
                )
            continue
        # `isinstance(True, int)` is True in Python, so booleans are excluded above
        # before the string check rather than after it.
        if not isinstance(value, str):
            raise IssueCommandRejected(
                f"{action}.{key} must be a string, got {type(value).__name__}"
            )
        if not value.strip():
            raise IssueCommandRejected(f"{action}.{key} may not be blank")
        if len(value) > MAX_STRING_CHARS:
            raise IssueCommandRejected(
                f"{action}.{key} is {len(value)} chars; the limit is "
                f"{MAX_STRING_CHARS}. Summarise and cite a source instead"
            )


# ---------------------------------------------------------------------------
# The action vocabulary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Action:
    """One allowed operation: its payload contract and the single function it calls.

    `run` is a closure held in a module-level table. Dispatch is a dictionary
    lookup on a validated key — never `getattr`, never a module or function name
    taken from the envelope, never `eval`. The set of reachable code is fixed at
    import time and visible in one place.
    """

    required: frozenset[str]
    optional: frozenset[str]
    run: Callable[..., Any]
    doc: str


def _issue(session, payload: dict):
    """Resolve the `issue` field to a real row, refusing anything unknown."""
    return issues.get_issue(session, payload["issue"]) or _no_issue(payload["issue"])


def _no_issue(ref):
    raise issues.IssueError(
        f"no issue {ref!r}; check the key with `{{'type':'xos','command':'issue-list'}}`"
    )


def _opt(payload: dict, *names: str) -> dict:
    """Pass only the optional keys that were actually supplied, so an absent field
    means "the service's own default" rather than an explicit None."""
    return {n: payload[n] for n in names if n in payload}


def _open_candidate(session, env: _Envelope):
    return issues.open_issue_from_candidate(
        session,
        env.payload["fingerprint"],
        actor=env.actor,
        opened_by_role=env.actor_role,
    )


def _triage(session, env: _Envelope):
    return issues.triage_issue(
        session, _issue(session, env.payload),
        actor=env.actor, actor_role=env.actor_role, reason=env.payload["reason"],
        **_opt(env.payload, "severity", "priority", "classification", "owner_role"),
    )


def _classify(session, env: _Envelope):
    return issues.classify_issue(
        session, _issue(session, env.payload),
        classification=env.payload["classification"],
        actor=env.actor, actor_role=env.actor_role, reason=env.payload["reason"],
    )


def _assign(session, env: _Envelope):
    return issues.assign_issue(
        session, _issue(session, env.payload),
        owner_role=env.payload["owner_role"],
        actor=env.actor, actor_role=env.actor_role, reason=env.payload["reason"],
    )


def _transfer(session, env: _Envelope):
    return issues.transfer_issue(
        session, _issue(session, env.payload),
        to_owner_role=env.payload["to_owner_role"],
        actor=env.actor, actor_role=env.actor_role, reason=env.payload["reason"],
        **_opt(env.payload, "classification", "evidence_waiver_reason"),
    )


def _status(session, env: _Envelope):
    return issues.set_issue_status(
        session, _issue(session, env.payload),
        status=env.payload["status"],
        actor=env.actor, actor_role=env.actor_role,
        **_opt(env.payload, "reason", "event_type"),
    )


def _add_evidence(session, env: _Envelope):
    return issues.add_issue_evidence(
        session, _issue(session, env.payload),
        evidence_type=env.payload["evidence_type"],
        summary=env.payload["summary"],
        captured_by=env.actor, actor_role=env.actor_role,
        **_opt(env.payload, "source_ref", "content_hash"),
    )


def _add_link(session, env: _Envelope):
    return issues.add_issue_link(
        session, _issue(session, env.payload),
        link_type=env.payload["link_type"],
        reference=env.payload["reference"],
        created_by=env.actor, actor_role=env.actor_role,
        **_opt(env.payload, "label"),
    )


def _propose_fix(session, env: _Envelope):
    return issues.propose_issue_fix(
        session, _issue(session, env.payload),
        proposed_fix=env.payload["proposed_fix"],
        actor=env.actor, actor_role=env.actor_role,
        **_opt(env.payload, "reason", "advance"),
    )


def _record_disposition(session, env: _Envelope):
    return issues.record_disposition(
        session, _issue(session, env.payload),
        disposition=env.payload["disposition"],
        actor=env.actor, actor_role=env.actor_role, reason=env.payload["reason"],
        **_opt(
            env.payload,
            "requires_new_version", "requires_new_epoch",
            "requires_platform_revision", "requires_pause_or_stand_down",
            "version_and_epoch_rationale",
        ),
    )


def _record_validation_plan(session, env: _Envelope):
    return issues.record_validation_plan(
        session, _issue(session, env.payload),
        validation_plan=env.payload["validation_plan"],
        actor=env.actor, actor_role=env.actor_role,
        **_opt(env.payload, "reason", "advance"),
    )


def _record_validation_result(session, env: _Envelope):
    return issues.record_validation_result(
        session, _issue(session, env.payload),
        passed=env.payload["passed"], summary=env.payload["summary"],
        actor=env.actor, actor_role=env.actor_role,
        **_opt(env.payload, "source_ref", "evidence_type"),
    )


def _resolve(session, env: _Envelope):
    return issues.resolve_issue(
        session, _issue(session, env.payload),
        resolution_summary=env.payload["resolution_summary"],
        actor=env.actor, actor_role=env.actor_role,
        **_opt(env.payload, "validation_waiver_reason"),
    )


def _close_no_action(session, env: _Envelope):
    return issues.close_no_action(
        session, _issue(session, env.payload),
        reason=env.payload["reason"], actor=env.actor, actor_role=env.actor_role,
    )


def _mark_duplicate(session, env: _Envelope):
    return issues.mark_duplicate(
        session, _issue(session, env.payload),
        duplicate_of=env.payload["duplicate_of"],
        actor=env.actor, actor_role=env.actor_role, reason=env.payload["reason"],
    )


def _reopen(session, env: _Envelope):
    return issues.reopen_issue(
        session, _issue(session, env.payload),
        reason=env.payload["reason"], actor=env.actor, actor_role=env.actor_role,
        **_opt(env.payload, "owner_role"),
    )


def _record_recurrence(session, env: _Envelope):
    return issues.record_recurrence(
        session, _issue(session, env.payload),
        actor=env.actor, actor_role=env.actor_role,
        **_opt(env.payload, "note"),
    )


#: The complete vocabulary. Seventeen ordinary issue operations, no more.
#:
#: What is absent is as deliberate as what is present. There is no `CREATE` — a
#: ticket that carries no fingerprint cannot cover the candidate it was opened
#: for, which is the defect `OPEN_CANDIDATE` exists to prevent — and no
#: `OPEN_CHILD`, whose parent-scoped kwargs are wide enough to be an escape hatch.
#: Both remain available through the local CLI, where a human reviews the result.
#: Free-form JSON blobs (`set_issue_status(payload=…)`, `add_issue_evidence`'s
#: `payload_json`) are also withheld: this transport is public, so it carries
#: bounded prose and references, not captures.
ACTIONS: dict[str, _Action] = {
    "OPEN_CANDIDATE": _Action(
        required=frozenset({"fingerprint"}), optional=frozenset(),
        run=_open_candidate,
        doc="Adopt a currently detected Control Tower candidate into a real issue.",
    ),
    "TRIAGE": _Action(
        required=frozenset({"issue", "reason"}),
        optional=frozenset({"severity", "priority", "classification", "owner_role"}),
        run=_triage,
        doc="Set severity/priority/classification/owner in one reviewed step.",
    ),
    "CLASSIFY": _Action(
        required=frozenset({"issue", "classification", "reason"}),
        optional=frozenset(), run=_classify,
        doc="Reclassify an investigation.",
    ),
    "ASSIGN": _Action(
        required=frozenset({"issue", "owner_role", "reason"}),
        optional=frozenset(), run=_assign,
        doc="Assign the owning role.",
    ),
    "TRANSFER": _Action(
        required=frozenset({"issue", "to_owner_role", "reason"}),
        optional=frozenset({"classification", "evidence_waiver_reason"}),
        run=_transfer,
        doc="Hand an investigation to another role (evidence rules still apply).",
    ),
    "STATUS": _Action(
        required=frozenset({"issue", "status"}),
        optional=frozenset({"reason", "event_type"}), run=_status,
        doc="Move the issue through its own state machine.",
    ),
    "ADD_EVIDENCE": _Action(
        required=frozenset({"issue", "evidence_type", "summary"}),
        optional=frozenset({"source_ref", "content_hash"}), run=_add_evidence,
        doc="Cite evidence by summary and reference.",
    ),
    "ADD_LINK": _Action(
        required=frozenset({"issue", "link_type", "reference"}),
        optional=frozenset({"label"}), run=_add_link,
        doc="Attach a supporting or canonical reference.",
    ),
    "PROPOSE_FIX": _Action(
        required=frozenset({"issue", "proposed_fix"}),
        optional=frozenset({"reason", "advance"}), run=_propose_fix,
        doc="Record a proposed remedy (diagnosis stays separate from remediation).",
    ),
    "RECORD_DISPOSITION": _Action(
        required=frozenset({"issue", "disposition", "reason"}),
        optional=frozenset({
            "requires_new_version", "requires_new_epoch",
            "requires_platform_revision", "requires_pause_or_stand_down",
            "version_and_epoch_rationale",
        }),
        run=_record_disposition,
        doc=("Record what the investigation concluded. The requires_* flags "
             "DECLARE what a canonical action would need; they perform none of it."),
    ),
    "RECORD_VALIDATION_PLAN": _Action(
        required=frozenset({"issue", "validation_plan"}),
        optional=frozenset({"reason", "advance"}), run=_record_validation_plan,
        doc="Pre-register how the remedy will be shown to have worked.",
    ),
    "RECORD_VALIDATION_RESULT": _Action(
        required=frozenset({"issue", "passed", "summary"}),
        optional=frozenset({"source_ref", "evidence_type"}),
        run=_record_validation_result,
        doc="Record the outcome of the pre-registered validation, pass or fail.",
    ),
    "RESOLVE": _Action(
        required=frozenset({"issue", "resolution_summary"}),
        optional=frozenset({"validation_waiver_reason"}), run=_resolve,
        doc="Close an investigation that was actually fixed and validated.",
    ),
    "CLOSE_NO_ACTION": _Action(
        required=frozenset({"issue", "reason"}), optional=frozenset(),
        run=_close_no_action,
        doc="Close an investigation that needs no change, with the reason why.",
    ),
    "MARK_DUPLICATE": _Action(
        required=frozenset({"issue", "duplicate_of", "reason"}),
        optional=frozenset(), run=_mark_duplicate,
        doc="Fold one investigation into another — explicitly, never by fuzzy match.",
    ),
    "REOPEN": _Action(
        required=frozenset({"issue", "reason"}),
        optional=frozenset({"owner_role"}), run=_reopen,
        doc="Reopen a closed investigation that turned out not to be finished.",
    ),
    "RECORD_RECURRENCE": _Action(
        required=frozenset({"issue"}), optional=frozenset({"note"}),
        run=_record_recurrence,
        doc="Record that a known problem happened again.",
    ),
}


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

#: Refusals the workflow is SUPPOSED to produce: a bad enum, an illegal
#: transition, a missing issue, a transfer without evidence, a stale candidate.
#: These are the system working, so they become a terminal REJECTED receipt.
#: Anything else is a FAILED receipt, because an unexpected exception means the
#: executor no longer knows what it did.
_REFUSALS = (issues.IssueError, policy.IssuePolicyError, IssueCommandRejected)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sanitize(exc: BaseException, env: _Envelope | None) -> str:
    """A bounded, envelope-free error string.

    Never a traceback, never the raw envelope: an exception message is one of the
    output paths the transport value must not reach, so the canonical envelope is
    stripped from it if it somehow appears before the message is truncated.
    """
    text = f"{type(exc).__name__}: {exc}"
    if env is not None:
        text = text.replace(env.canonical, "<envelope redacted>")
    text = " ".join(text.split())
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 1] + "…"
    return text


def _result_of(session, obj: Any) -> dict:
    """Bounded, machine-generated metadata about what the command produced.

    Deliberately identifiers and states — never the prose that was written. This
    is the one piece of the receipt the read surface prints, so it has to be safe
    to print without re-deciding that question per command.
    """
    session.flush()  # ids exist before the receipt claims to know them
    if isinstance(obj, issues.ExperimentIssue):
        return {
            "kind": "issue",
            "issue_key": obj.issue_key,
            "status": obj.status,
            "owner_role": obj.current_owner_role,
            "classification": obj.classification,
            "severity": obj.severity,
            "priority": obj.priority,
            "disposition": obj.disposition,
        }
    issue_key = None
    issue_id = getattr(obj, "issue_id", None)
    if issue_id is not None:
        parent = issues.get_issue(session, issue_id)
        issue_key = getattr(parent, "issue_key", None)
    return {
        "kind": type(obj).__name__,
        "row_id": getattr(obj, "id", None),
        "issue_key": issue_key,
    }


def _issue_key_of(session, obj: Any) -> str | None:
    if isinstance(obj, issues.ExperimentIssue):
        return obj.issue_key
    issue_id = getattr(obj, "issue_id", None)
    if issue_id is None:
        return None
    return getattr(issues.get_issue(session, issue_id), "issue_key", None)


def _claim(session, env: _Envelope, now: datetime) -> int | None:
    """Claim `command_id` for this worker. Returns the new receipt id, or None if
    another worker already owns it.

    `ON CONFLICT DO NOTHING RETURNING` rather than catching an IntegrityError,
    because catching one poisons the transaction on Postgres — the very
    transaction that then has to record the receipt. Here the loser simply gets no
    row back and the transaction stays clean. A concurrent inserter blocks on the
    uncommitted key until the winner commits, so both cannot execute.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - the repo runs on exactly these two
        raise IssueCommandRejected(
            f"issue commands need ON CONFLICT support; dialect {dialect!r} has none"
        )
    stmt = (
        insert(ExperimentOsIssueCommand)
        .values(
            command_id=env.command_id,
            action=env.action,
            actor=env.actor,
            actor_role=env.actor_role,
            payload_hash=env.payload_hash,
            payload_json=env.payload,
            schema_version=env.schema_version,
            status="RUNNING",
            # The transport carries no client clock, so this records when the
            # WORKER received the command, which is the only time it can verify.
            requested_at=now,
            started_at=now,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["command_id"])
        .returning(ExperimentOsIssueCommand.id)
    )
    return session.execute(stmt).scalar_one_or_none()


def _receipt_view(row: ExperimentOsIssueCommand, **extra) -> dict:
    """The log/return shape. Metadata only — no payload, ever."""
    view = {
        "command_id": row.command_id,
        "action": row.action,
        "actor": row.actor,
        "actor_role": row.actor_role,
        "status": row.status,
        "issue_key": row.issue_key,
        "payload_hash": row.payload_hash,
        "schema_version": row.schema_version,
        "result": row.result_json,
        "error": row.error,
    }
    view.update(extra)
    return view


def execute_envelope(session, envelope: Any, *, now: datetime | None = None) -> dict:
    """Execute one issue command exactly once and return its receipt view.

    Owns its own transaction boundaries: it commits the mutation together with the
    receipt, or the receipt alone. Callers should hand it a session and not wrap it
    in a transaction they intend to roll back — a receipt that gets rolled back
    would make a terminal command executable twice.

    Raises `IssueCommandRejected` only for an envelope so malformed that no
    trustworthy `command_id` can be recorded against it. Every other outcome —
    including every refusal and every failure — is a committed, readable receipt.
    """
    now = now or _now()
    env = _validate_envelope(envelope)   # before any database work, per the design

    receipt_id = _claim(session, env, now)
    if receipt_id is None:
        existing = (
            session.query(ExperimentOsIssueCommand)
            .filter(ExperimentOsIssueCommand.command_id == env.command_id)
            .one()
        )
        replayed = existing.payload_hash == env.payload_hash
        if not replayed:
            # Same name, different command. Execute nothing and change nothing:
            # the stored receipt belongs to whatever really ran under that id.
            logger.error(
                "issue command id collision; refusing to execute",
                extra={"extra_fields": {
                    "command_id": env.command_id,
                    "stored_hash": existing.payload_hash,
                    "submitted_hash": env.payload_hash,
                }},
            )
        return _receipt_view(
            existing,
            replayed=replayed,
            collision=not replayed,
            executed=False,
        )

    row = session.get(ExperimentOsIssueCommand, receipt_id)
    savepoint = session.begin_nested()
    try:
        _check_payload(env.action, env.payload)
        produced = ACTIONS[env.action].run(session, env)
        row.issue_key = _issue_key_of(session, produced)
        row.result_json = _result_of(session, produced)
        row.status = CommandStatus.SUCCEEDED
        row.error = None
        savepoint.commit()
    except _REFUSALS as exc:
        savepoint.rollback()
        row.status = CommandStatus.REJECTED
        row.error = _sanitize(exc, env)
    except Exception as exc:  # noqa: BLE001 — see CommandStatus.FAILED
        savepoint.rollback()
        row.status = CommandStatus.FAILED
        row.error = _sanitize(exc, env)
        logger.exception(
            "issue command executor failed",
            extra={"extra_fields": {
                "command_id": env.command_id, "action": env.action,
            }},
        )
    row.completed_at = _now()
    # One commit, whichever way it went: on success this makes the mutation and
    # its SUCCEEDED receipt atomic; on refusal or failure the mutation is already
    # rolled back to the savepoint and only the terminal receipt lands.
    session.commit()
    return _receipt_view(row, replayed=False, collision=False, executed=True)


def run_boot_command(session, raw: str, *, now: datetime | None = None) -> dict | None:
    """Boot entry point: parse the transport variable and execute it.

    Returns a log-safe receipt view, or None when nothing was set. The raw value
    is never returned, logged or embedded in an error — see the module docstring
    for why that is hygiene rather than confidentiality.
    """
    text = (raw or "").strip()
    if not text:
        return None
    size = len(text.encode("utf-8"))
    if size > MAX_ENVELOPE_BYTES:
        raise IssueCommandRejected(
            f"issue command is {size} bytes; the limit is {MAX_ENVELOPE_BYTES}"
        )
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as exc:
        # exc carries the offending document position, not the document.
        raise IssueCommandRejected(
            f"issue command is not valid JSON (line {exc.lineno}, col {exc.colno})"
        ) from None
    return execute_envelope(session, envelope, now=now)
