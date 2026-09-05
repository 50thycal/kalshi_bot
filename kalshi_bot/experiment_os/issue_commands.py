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
eighteen issue operations, each of which calls the existing function in
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

import contextlib
import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from . import issue_policy as policy
from . import issues, models, read
from .models import ExperimentOsIssueCommand

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIONS",
    "CommandStatus",
    "IssueCommandRejected",
    "IssueCommandTransportError",
    "SCHEMA_VERSION",
    "canonical_envelope",
    "envelope_hash",
    "execute_batch",
    "execute_envelope",
    "safe_error_fields",
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
#: A BATCH is a JSON array of ordinary envelopes (see `execute_batch`). Each
#: element is still bounded by MAX_ENVELOPE_BYTES individually; these two bound
#: the array itself, so one transport value cannot become a storage problem.
MAX_BATCH_COMMANDS = 25
MAX_BATCH_BYTES = 65536
MAX_STRING_CHARS = 4000
MAX_ERROR_CHARS = 400
#: Ceiling on any ONE action's field vocabulary, asserted at import time rather
#: than checked per request: unknown-field rejection already bounds a payload to
#: its action's fixed field set, so the only way a command could carry more
#: fields than this is if someone widened the vocabulary itself.
MAX_PAYLOAD_KEYS = 16

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

    Carries a stable `code` because the message is not always safe to print. The
    boot hook and the ops surfaces report the CODE — a fixed, enumerable string —
    where a raw exception message could echo submitted content back out onto a
    public branch.
    """

    def __init__(self, message: str, code: str = "ENVELOPE_REJECTED"):
        super().__init__(message)
        self.code = code


class IssueCommandTransportError(RuntimeError):
    """The database plumbing around a command failed — the claim, the receipt
    read, or the final commit — so NO receipt was written.

    Raised in place of the driver's own exception, whose message can quote the
    failing statement and its bound parameters (which include `payload_json`).
    The message here is already sanitized and the original is dropped rather than
    chained, because a chained `__cause__` puts the raw text back into any
    traceback that formats it. No receipt means the next boot may legitimately
    retry, which is correct: without one, the mutation cannot have committed
    either.
    """

    def __init__(self, message: str, code: str = "TRANSPORT_DB_ERROR"):
        super().__init__(message)
        self.code = code


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
            f"envelope must be a JSON object, got {type(envelope).__name__}",
            "NOT_AN_OBJECT"
        )

    unknown = sorted(set(envelope) - ENVELOPE_KEYS)
    if unknown:
        raise IssueCommandRejected(
            f"unknown top-level field(s) {unknown}; legal: {sorted(ENVELOPE_KEYS)}",
            "UNKNOWN_TOP_LEVEL_FIELD"
        )
    missing = sorted(ENVELOPE_KEYS - set(envelope))
    if missing:
        raise IssueCommandRejected(f"envelope is missing required field(s) {missing}",
            "MISSING_TOP_LEVEL_FIELD")

    version = envelope["schema_version"]
    if version != SCHEMA_VERSION:
        raise IssueCommandRejected(
            f"schema_version must be {SCHEMA_VERSION}, got {version!r}",
            "BAD_SCHEMA_VERSION"
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
            f"action {action!r} is not in the allowed vocabulary: {sorted(ACTIONS)}",
            "UNKNOWN_ACTION"
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
            f"payload must be a JSON object, got {type(payload).__name__}",
            "PAYLOAD_NOT_AN_OBJECT"
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


def vocabulary(action: str) -> frozenset[str]:
    """Every payload field name this action recognises. Fixed at import time."""
    spec = ACTIONS[action]
    return spec.required | spec.optional


def _check_payload(action: str, payload: dict) -> None:
    """Validate the payload against one action's fixed field vocabulary.

    Every refusal below names only things from that fixed vocabulary — never a
    submitted key name and never a submitted value. An unknown field is reported
    as a COUNT: the name came from the envelope, so printing it would echo
    author-controlled text into an ops result, a worker log and a receipt, all of
    which are read (and the first of which is committed to a public branch).
    """
    spec = ACTIONS[action]
    legal = sorted(vocabulary(action))
    keys = set(payload)

    unknown = keys - spec.required - spec.optional
    if unknown:
        raise IssueCommandRejected(
            f"{action}: {len(unknown)} unrecognised payload field(s) — names not "
            f"echoed because they are author-supplied. Legal fields: {legal}",
            "UNKNOWN_PAYLOAD_FIELD",
        )
    missing = sorted(spec.required - keys)
    if missing:
        raise IssueCommandRejected(
            f"{action}: missing required field(s) {missing}", "MISSING_PAYLOAD_FIELD"
        )

    for key in sorted(keys):
        value = payload[key]
        if key in _BOOL_KEYS:
            if not isinstance(value, bool):
                raise IssueCommandRejected(
                    f"{action}.{key} must be a JSON boolean, got "
                    f"{type(value).__name__}",
                    "BAD_PAYLOAD_TYPE",
                )
            continue
        # `isinstance(True, int)` is True in Python, so booleans are excluded above
        # before the string check rather than after it.
        if not isinstance(value, str):
            raise IssueCommandRejected(
                f"{action}.{key} must be a string, got {type(value).__name__}",
                "BAD_PAYLOAD_TYPE",
            )
        if not value.strip():
            raise IssueCommandRejected(
                f"{action}.{key} may not be blank", "BLANK_PAYLOAD_FIELD"
            )
        if len(value) > MAX_STRING_CHARS:
            raise IssueCommandRejected(
                f"{action}.{key} is {len(value)} chars; the limit is "
                f"{MAX_STRING_CHARS}. Summarise and cite a source instead",
                "PAYLOAD_FIELD_TOO_LONG",
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


# ---------------------------------------------------------------------------
# Manual lineage resolution
# ---------------------------------------------------------------------------

# OPEN_MANUAL is the one action that may name canonical objects it did not get
# from a detector, so every reference is resolved HERE, by an explicit function
# per kind, against a human-legible key. Raw primary keys are deliberately not
# accepted: a bare integer in a public envelope is unreadable, unverifiable in
# review, and one typo away from scoping a ticket to the wrong contract. Nothing
# is passed to `create_issue` as an opaque kwarg — each resolver returns a real
# row or refuses, and `create_issue._resolve_scope` then cross-checks the
# combination, so a version that does not belong to the named experiment is a
# refusal rather than a silently corrected row.


def _need(session, payload: dict, key: str):
    """The value of a lineage field, or None when it was not supplied."""
    return payload.get(key)


def _resolve_experiment(session, value):
    exp = read.get_experiment(session, value)
    if exp is None:
        raise issues.IssueScopeError(f"no experiment with key {value!r}")
    return exp


def _resolve_version(session, experiment, value):
    """`version` is a version NUMBER and is meaningless without its experiment."""
    if experiment is None:
        raise issues.IssueScopeError(
            "version requires experiment: a version number identifies a contract "
            "only within one experiment"
        )
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise issues.IssueScopeError(
            f"version must be a version number, got {value!r}"
        ) from None
    for ver in read.versions_for(session, experiment):
        if ver.version == number:
            return ver
    raise issues.IssueScopeError(
        f"experiment {experiment.key} has no version {number}"
    )


def _resolve_deployment(session, value):
    row = session.execute(
        select(models.ExperimentDeployment).where(
            models.ExperimentDeployment.deployment_key == str(value)
        )
    ).scalars().all()
    if not row:
        raise issues.IssueScopeError(f"no deployment with key {value!r}")
    if len(row) > 1:
        raise issues.IssueScopeError(
            f"deployment key {value!r} matches {len(row)} deployments; refusing "
            "to guess which one this issue is about"
        )
    return row[0]


def _resolve_gate(session, version, value):
    """`gate` is a gate KEY, unique only within its version."""
    if version is None:
        raise issues.IssueScopeError(
            "gate requires experiment and version: a gate key identifies a gate "
            "only within one version"
        )
    for gate in read.gates_for(session, version):
        if gate.gate_key == str(value):
            return gate
    raise issues.IssueScopeError(
        f"version {version.version} has no gate {value!r}"
    )


def _resolve_platform_revision(session, value):
    """`platform_revision` is `COMPONENT:version`, the form the CLI prints."""
    text = str(value)
    if ":" not in text:
        raise issues.IssueScopeError(
            f"platform_revision must be COMPONENT:version, got {text!r}"
        )
    component_key, _, version = text.partition(":")
    row = session.execute(
        select(models.PlatformRevision)
        .join(models.PlatformComponent)
        .where(
            models.PlatformComponent.key == component_key.strip(),
            models.PlatformRevision.version == version.strip(),
        )
    ).scalar_one_or_none()
    if row is None:
        raise issues.IssueScopeError(f"no platform revision {text!r}")
    return row


def _manual_lineage(session, payload: dict) -> dict:
    """Resolve every supplied lineage reference into a real row.

    Order matters: an experiment must exist before a version number means
    anything, and a version before a gate key does. Anything absent stays absent
    — a system-wide problem legitimately has no scope at all.
    """
    experiment = version = deployment = gate = revision = None
    if "experiment" in payload:
        experiment = _resolve_experiment(session, payload["experiment"])
    if "version" in payload:
        version = _resolve_version(session, experiment, payload["version"])
    if "deployment" in payload:
        deployment = _resolve_deployment(session, payload["deployment"])
    if "gate" in payload:
        gate = _resolve_gate(session, version, payload["gate"])
    if "platform_revision" in payload:
        revision = _resolve_platform_revision(session, payload["platform_revision"])
    return {
        "experiment": experiment,
        "version": version,
        "deployment": deployment,
        "gate": gate,
        "platform_revision": revision,
    }


def _open_manual(session, env: _Envelope):
    """Open a ticket for a problem a PERSON found.

    The Tower's detector surface does not cover everything — a Live Ops runtime
    observation, a Research Lab reading of the evidence, an integrity review, an
    operator noticing something odd. Before this existed those problems had no
    production path at all, and "use the local CLI" is not an answer when the
    local CLI is precisely what cannot write production.

    Narrow by construction: five required fields, a fixed optional set, and NO
    `**payload` passthrough to `create_issue`. It records the manual source and
    deliberately no fingerprint — see `policy.DETECTOR_MANUAL`.
    """
    payload = env.payload
    scope = _manual_lineage(session, payload)
    return issues.create_issue(
        session,
        title=payload["title"],
        problem_statement=payload["problem_statement"],
        classification=payload["classification"],
        current_owner_role=payload["owner_role"],
        # How it was found. Required, because a manual ticket has no detector to
        # explain itself and "someone said so" is not provenance.
        owner_rationale=payload["reason"],
        evidence_summary=payload["reason"],
        opened_by_role=env.actor_role,
        opened_by_actor=env.actor,
        detector=policy.DETECTOR_MANUAL,
        detector_fingerprint=None,
        **_opt(payload, "severity", "priority"),
        **scope,
        details_json={
            "manual_report": True,
            "reported_via": "issue_command_transport",
            "discovery": payload["reason"],
        },
    )


#: The complete vocabulary. Eighteen ordinary issue operations, no more.
#:
#: Two ways in, and choosing between them is a rule, not a preference:
#: **`OPEN_CANDIDATE` is MANDATORY whenever the Control Tower has a matching
#: candidate.** Only adoption carries the deterministic fingerprint and exact
#: lineage the detector computed, so only adoption makes the ticket cover the
#: anomaly; a hand-opened ticket for a detected problem leaves the Tower
#: reporting it as UNTICKETED forever, which is the defect that made the
#: documented workflow unusable end to end. `OPEN_MANUAL` is for problems
#: OUTSIDE the detector surface — a Live Ops runtime observation, a Research Lab
#: reading of the evidence, an integrity review, a person noticing something.
#: Run `issue-candidates` first; if the problem is listed there, adopt it.
#:
#: What is still absent is as deliberate as what is present. There is no generic
#: `CREATE`: `OPEN_MANUAL` names five required fields and a fixed optional set,
#: resolves every lineage reference explicitly, and passes no `**payload` through
#: to `create_issue`. There is no `OPEN_CHILD`, whose parent-scoped kwargs are
#: wide enough to be an escape hatch; it stays on the local CLI, where a human
#: reviews the result. Free-form JSON blobs (`set_issue_status(payload=…)`,
#: `add_issue_evidence`'s `payload_json`) are also withheld: this transport is
#: public, so it carries bounded prose and references, not captures.
ACTIONS: dict[str, _Action] = {
    "OPEN_CANDIDATE": _Action(
        required=frozenset({"fingerprint"}), optional=frozenset(),
        run=_open_candidate,
        doc="Adopt a currently detected Control Tower candidate into a real issue.",
    ),
    "OPEN_MANUAL": _Action(
        required=frozenset({
            "title", "problem_statement", "classification", "owner_role", "reason",
        }),
        optional=frozenset({
            "severity", "priority",
            # Lineage, by human-legible reference, never by raw id.
            "experiment",          # experiment key
            "version",             # version number (needs experiment)
            "deployment",          # deployment key
            "gate",                # gate key (needs experiment + version)
            "platform_revision",   # COMPONENT:version
        }),
        run=_open_manual,
        doc=("Open a ticket for a problem found outside the detector surface. "
             "Use OPEN_CANDIDATE instead whenever the Tower has a candidate."),
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


#: Submitted strings shorter than this are not redacted from an error message.
#: A payload value that short comes from a fixed enum (`P1`, `OPS`) and can carry
#: no disclosure, while blanking two-character substrings everywhere would shred
#: the message without protecting anything.
MIN_REDACTED_CHARS = 3
#: Word-level redaction is more aggressive still, because an exception usually
#: quotes a FRAGMENT of a value rather than the whole of it.
MIN_REDACTED_TOKEN_CHARS = 4
REDACTION = "<redacted>"

#: Where a submitted string is cut into the words worth protecting. Hyphens,
#: underscores and dots stay INSIDE a token so an identifier like
#: `mmsell-scheduled-settle` survives as one unit rather than three common words.
_TOKEN_SPLIT = re.compile(r"""[\s/\\,;:()\[\]{}"'=<>|?!*&%$#@+~`^]+""")


def _redactions(env: _Envelope | None) -> list[str]:
    """Every author-supplied string that must not survive into an error message.

    Three sources, all author-controlled: the whole canonical envelope; every
    submitted VALUE **and each word inside it**; and every submitted KEY NAME the
    action does not recognise — an unknown key is as author-controlled as a value
    and can smuggle content just as easily.

    Word-level matters more than it looks. Exceptions rarely quote a value whole:
    they say "boom while handling <one word of it>", or name the single offending
    path segment. Redacting only the complete value leaves every one of those
    fragments intact, which is how the original version of this passed its own
    marker test while still leaking.

    The cost is real and accepted: a common word an author happened to use is
    also removed from our own message, so a refusal can read a little blanked.
    That is the right way round — the message is a debugging aid, and this
    channel is public.

    Longest first, so replacing one never leaves a fragment of another behind.
    """
    if env is None:
        return []
    out = {env.canonical}
    known = vocabulary(env.action) if env.action in ACTIONS else frozenset()
    for key, value in env.payload.items():
        if isinstance(value, str):
            if len(value) >= MIN_REDACTED_CHARS:
                out.add(value)
            out.update(
                tok for tok in _TOKEN_SPLIT.split(value)
                if len(tok) >= MIN_REDACTED_TOKEN_CHARS
            )
        if key not in known and len(str(key)) >= MIN_REDACTED_CHARS:
            out.add(str(key))
    return sorted(out, key=len, reverse=True)


def _sanitize(exc: BaseException, env: _Envelope | None) -> str:
    """A bounded error string with every author-supplied substring removed.

    Never a traceback and never the submitted content. Removing only the COMPLETE
    canonical envelope was not enough: a workflow refusal, a SQLAlchemy statement
    error, or any exception that quotes one offending value re-exposes that value
    through the receipt, the ops read surface and the worker log. So each
    submitted string is stripped individually, and what is left is the shape of
    the failure rather than its contents.
    """
    text = f"{type(exc).__name__}: {exc}"
    for secret in _redactions(env):
        text = text.replace(secret, REDACTION)
    text = " ".join(text.split())
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 1] + "…"
    return text


def safe_error_fields(raw: str | None, exc: BaseException) -> dict:
    """Log-safe metadata for a transport failure that produced NO receipt.

    Used by the boot hook, which sees exceptions raised before a `command_id`
    could be trusted — a malformed envelope, an unreadable value, a database that
    would not take the claim. Those exceptions may quote the offending document
    or, from SQLAlchemy, the bound parameters (which include `payload_json`), so
    the message is never logged at all. What the operator gets instead is the
    exception class, a stable code where one exists, and the hash and length of
    the value that failed — enough to match a log line to the envelope they
    submitted, and nothing that reveals what was in it.
    """
    text = raw or ""
    encoded = text.encode("utf-8")
    return {
        "error_class": type(exc).__name__,
        "error_code": getattr(exc, "code", None) or "TRANSPORT_ERROR",
        "command_bytes": len(encoded),
        "command_hash": (
            hashlib.sha256(encoded).hexdigest()[:16] if encoded else None
        ),
    }


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


@contextlib.contextmanager
def _db_errors(env: _Envelope, stage: str):
    """Translate a driver exception into a sanitized `IssueCommandTransportError`.

    These are the three places a failure produces no receipt at all, so nothing
    downstream can hold a redacted account of it — which makes the exception
    itself the only record, and therefore the thing that must not carry the
    payload out. SQLAlchemy's `StatementError` helpfully appends the statement
    and its bound parameters to `str(exc)`; `payload_json` is one of them.
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 — deliberately broad; see above
        logger.error(
            "issue command transport failure",
            extra={"extra_fields": {
                "stage": stage,
                "command_id": env.command_id,
                "payload_hash": env.payload_hash,
                "error_class": type(exc).__name__,
            }},
        )
        raise IssueCommandTransportError(
            f"{stage} failed: {_sanitize(exc, env)}"
        ) from None


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

    with _db_errors(env, "claim"):
        receipt_id = _claim(session, env, now)
    if receipt_id is None:
        with _db_errors(env, "read_receipt"):
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
        # Deliberately NOT logger.exception: a traceback carries the original,
        # unsanitized exception message and every frame's locals through the
        # handler's formatter, which is exactly the content this transport must
        # not copy into a log. The receipt already holds a bounded, redacted
        # account of the failure, and the receipt is the place to read it.
        logger.error(
            "issue command executor failed",
            extra={"extra_fields": {
                "command_id": env.command_id,
                "action": env.action,
                "payload_hash": env.payload_hash,
                "error": row.error,
            }},
        )
    row.completed_at = _now()
    # One commit, whichever way it went: on success this makes the mutation and
    # its SUCCEEDED receipt atomic; on refusal or failure the mutation is already
    # rolled back to the savepoint and only the terminal receipt lands.
    with _db_errors(env, "commit"):
        session.commit()
    return _receipt_view(row, replayed=False, collision=False, executed=True)


def execute_batch(session, envelopes: list, *, now: datetime | None = None) -> dict:
    """Execute an ORDERED batch of issue commands in one boot, and return a
    batch view over their individual receipts.

    ## Why batching exists

    One command per boot made the transport correct and unusable at the same
    time. Recording a single ticket end to end — triage, status, proposed fix,
    disposition, validation plan, links, validation result, resolve — is a dozen
    commands, and because setting the transport variable redeploys the worker,
    that is a dozen restarts of a live trading process for one ticket's paperwork.
    Worse, the variable is a single slot: while one session's envelope waits for
    its boot, a second session that sets the variable overwrites it, and the
    first command is silently never executed. Nothing is corrupted (the ledger
    still makes each `command_id` exactly-once) but the author only finds out by
    noticing a missing receipt. Batching shrinks both the restart count and that
    collision window by the length of the batch.

    ## What batching deliberately does NOT change

    A batch is a JSON *array of ordinary envelopes* — not a new envelope shape.
    Every element carries its own `command_id`, is validated by the same
    `_validate_envelope`, is claimed and executed by the same `execute_envelope`,
    and gets its own durable receipt row. Exactly-once, canonical hashing,
    collision detection, payload flatness, sanitized errors and the action
    vocabulary are all untouched, because none of them ever knew about the array.
    There is no batch-level receipt and no batch-level transaction: a batch is an
    ordering, not an atom.

    ## Ordering and the stop rule

    Elements run in array order and the batch STOPS at the first element that
    does not SUCCEED. Ticket workflows are sequential — a disposition presumes
    the triage that preceded it — so continuing past a refusal would apply later
    steps to a state their author never saw.

    The elements after the stop are left entirely unclaimed: no row, no receipt.
    That is deliberate and it is what makes a stopped batch recoverable. The
    module's own rule is that "the absence of a receipt is the only state that
    permits another attempt", so the unrun tail may be resubmitted VERBATIM,
    under the same `command_id`s, once the cause of the stop is fixed. A batch
    that had marked them FAILED would have burned those ids for nothing.

    ## Validation is all-or-nothing, execution is not

    Every element is structurally validated BEFORE any of them executes, and a
    duplicate `command_id` within one array is refused here rather than being
    discovered halfway through as a silent replay. A typo in the last element
    therefore costs nothing instead of leaving the first eight applied.
    """
    now = now or _now()
    if not isinstance(envelopes, list):
        raise IssueCommandRejected(
            f"a batch must be a JSON array, got {type(envelopes).__name__}",
            "BATCH_NOT_AN_ARRAY",
        )
    if not envelopes:
        raise IssueCommandRejected("a batch must contain at least one command",
                                   "BATCH_EMPTY")
    if len(envelopes) > MAX_BATCH_COMMANDS:
        raise IssueCommandRejected(
            f"a batch carries {len(envelopes)} commands; the limit is "
            f"{MAX_BATCH_COMMANDS}. Split it across boots",
            "BATCH_TOO_LONG",
        )

    # Validate everything first — see the docstring. This is pure and touches no
    # database, so a malformed tail cannot leave a partially applied head.
    validated = [_validate_envelope(e) for e in envelopes]
    seen: dict[str, int] = {}
    for i, env in enumerate(validated):
        if env.command_id in seen:
            raise IssueCommandRejected(
                f"command_id {env.command_id!r} appears twice in one batch "
                f"(positions {seen[env.command_id]} and {i}) — the second would "
                "replay the first and silently do nothing; give each its own id",
                "BATCH_DUPLICATE_COMMAND_ID",
            )
        seen[env.command_id] = i

    receipts: list[dict] = []
    stopped_at: str | None = None
    for env_dict in envelopes:
        view = execute_envelope(session, env_dict, now=now)
        receipts.append(view)
        if view.get("status") != CommandStatus.SUCCEEDED:
            stopped_at = view.get("command_id")
            break

    executed = len(receipts)
    return {
        # `status` is scalar and first so the boot hook's log-level branch and
        # every receipt reader keep working on a batch without special-casing it.
        "status": (CommandStatus.SUCCEEDED if stopped_at is None
                   else receipts[-1].get("status")),
        "batch": True,
        "count": len(envelopes),
        "executed": executed,
        "skipped": len(envelopes) - executed,
        "stopped_at": stopped_at,
        # Metadata only, exactly like a single receipt view: the compact per
        # command line is what an operator reads in the log, and the full views
        # are there for the ops surfaces.
        "commands": [
            f"{v.get('command_id')}:{v.get('status')}" for v in receipts
        ],
        "receipts": receipts,
    }


def run_boot_command(session, raw: str, *, now: datetime | None = None) -> dict | None:
    """Boot entry point: parse the transport variable and execute it.

    The value is either ONE envelope (a JSON object) or an ordered BATCH of them
    (a JSON array) — see `execute_batch`. The two are distinguished by the parsed
    type alone, so a single command's shape, size bound and receipt are exactly
    what they were before batching existed.

    Returns a log-safe receipt view, or None when nothing was set. The raw value
    is never returned, logged or embedded in an error — see the module docstring
    for why that is hygiene rather than confidentiality.
    """
    text = (raw or "").strip()
    if not text:
        return None
    # The outer bound is the batch ceiling because the shape is not known until
    # the value is parsed. A single envelope is still held to MAX_ENVELOPE_BYTES
    # by `_validate_envelope`, which measures the CANONICAL form — the size that
    # actually gets stored — rather than however much whitespace it arrived in.
    size = len(text.encode("utf-8"))
    if size > MAX_BATCH_BYTES:
        raise IssueCommandRejected(
            f"issue command is {size} bytes; the limit is {MAX_BATCH_BYTES}",
            "ENVELOPE_TOO_LARGE",
        )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        # exc carries the offending document position, not the document.
        raise IssueCommandRejected(
            f"issue command is not valid JSON (line {exc.lineno}, col {exc.colno})"
        ) from None
    if isinstance(parsed, list):
        return execute_batch(session, parsed, now=now)
    return execute_envelope(session, parsed, now=now)
