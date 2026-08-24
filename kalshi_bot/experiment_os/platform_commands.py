"""One bounded Platform Change Review command, executed once at worker boot.

WHY THIS EXISTS
---------------
Registering a Platform Revision, accepting its impact dispositions and performing
the activation cutover are WRITES to Experiment OS. The ops channel is read-only
against Postgres by design (`DATABASE_URL_RO`) and must stay that way, and the
`EXPERIMENT_OS_ISSUE_COMMAND` vocabulary covers issues only — deliberately, because
**a ticket must never be able to mutate a Platform Revision**. That prohibition is
structural and this module does not weaken it: it is a SEPARATE transport, with a
separate environment variable, a separate receipt ledger and a disjoint action set.
Nothing here is reachable from an issue command, and no issue action was added.

Before this existed there was no authorized production path at all, so a review
that had done its work correctly could not record its result — which is how a
platform change ends up merged with no accounted impact record, the exact failure
the impact engine exists to prevent.

WHAT IT CAN AND CANNOT DO
-------------------------
The action vocabulary is a static allowlist of four operations, each a thin call
into the canonical helpers in `platform_impact.py` / `service.py`. It CANNOT:

  * change exposure, arm a live canary, or alter any risk limit;
  * place, cancel or modify an order;
  * promote an experiment, record a gate verdict, or transition a lifecycle state;
  * fabricate a Version — `apply_new_version` is deliberately NOT in the vocabulary,
    because an I3 scientific-contract change is a researcher's decision, not a
    transport's;
  * force anything. `force=` is never passed and `record_forced_activation` is not
    reachable. A refused activation stays refused and the operator classifies the
    stragglers.

THE ENVELOPE IS PUBLIC
----------------------
Exactly like the issue-command transport, the envelope arrives through a Railway
environment variable that was set by pushing plaintext to the public `ops` branch.
It is NOT secret. Payloads must be safe to disclose: no credentials, no account or
order identifiers, no private market data. What the receipt reports back is
metadata only — never a submitted value, never an unrecognised key name.

EXACTLY ONCE, AND THE ONE DELIBERATE EXCEPTION
----------------------------------------------
`command_id` is the whole basis of exactly-once: it is claimed with
`ON CONFLICT DO NOTHING RETURNING`, so a restart re-reads the same variable and
executes nothing. Every outcome after the claim is a durable terminal receipt.

The exception is a PRECONDITION DEFERRAL, checked BEFORE the claim. `CUTOVER`
asserts that the code this worker is running actually contains the taxonomy the
revision describes, by recomputing its fingerprint in-process. If it does not
match, the command is NOT claimed and NOT consumed: it stays armed for the boot
that really does serve the new table. A terminal receipt there would be worse than
useless — an unrelated redeploy landing first would burn the cutover and leave the
revision pending forever with no way to re-arm the same command_id.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from . import platform_impact, service
from .models import ExperimentOsPlatformCommand, PlatformImpactAction, PlatformRevision

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

ENVELOPE_KEYS = frozenset(
    {"command_id", "action", "actor", "actor_role", "payload", "schema_version"}
)

#: The transport is Platform Change Review's and nobody else's. Attribution, not
#: authorization — the real authority is who can set a Railway variable — but a
#: receipt that named some other role would be a lie about which review produced
#: the change, so the vocabulary refuses one.
REQUIRED_ACTOR_ROLE = "PLATFORM_CHANGE_REVIEW"

# Bounds. The envelope arrives through an environment variable and a JSON file in
# a Git repository, neither of which has a natural size limit.
MAX_ENVELOPE_BYTES = 8192
MAX_STRING_CHARS = 4000
MAX_ERROR_CHARS = 400
MAX_PAYLOAD_KEYS = 12
#: Ceiling on one RECORD_IMPACTS batch. Batching exists because every command
#: costs a worker redeploy, and a cutover is the wrong moment to restart the
#: trading worker a dozen times; it is bounded so a batch can never become an
#: unreviewed bulk write.
MAX_IMPACT_ROWS = 32
#: Ceiling on the experiments one CUTOVER may re-epoch.
MAX_CUTOVER_EXPERIMENTS = 8

_COMMAND_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9._@ -]{1,64}$")
#: COMPONENT:version, the same human-legible reference the CLI takes.
_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}:[A-Za-z0-9._-]{1,48}$")


class CommandStatus:
    """Receipt states. All three terminal states are final for a `command_id`."""

    SUCCEEDED = "SUCCEEDED"
    #: Well formed but refused by the workflow — an unknown revision, an
    #: unaccounted experiment, an activation gate that is not safe. The system
    #: working.
    REJECTED = "REJECTED"
    #: An unexpected exception. The executor no longer knows what it did.
    FAILED = "FAILED"
    RUNNING = "RUNNING"


class PlatformCommandRejected(ValueError):
    def __init__(self, message: str, code: str = "ENVELOPE_REJECTED"):
        super().__init__(message)
        self.code = code


class PlatformCommandDeferred(Exception):
    """A precondition is not met yet. NOT a refusal: nothing is claimed or
    consumed, and the same envelope will be tried again on the next boot."""

    def __init__(self, message: str, code: str = "PRECONDITION_NOT_MET"):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def taxonomy_fingerprint() -> str:
    """Deterministic fingerprint of the settlement taxonomy THIS PROCESS loaded.

    Sorted `(prefix, type, mode)` triples through the same canonical hash the
    Experiment OS uses for every other fingerprint, so the value is reproducible
    from the source tree by anyone, with no database and no import of this module.
    """
    from ..mmsell.market_types import SERIES_TYPES

    return service.canonical_hash(sorted(tuple(row) for row in SERIES_TYPES))


# ---------------------------------------------------------------------------
# Envelope
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
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str)


def _validate_envelope(envelope: Any) -> _Envelope:
    """Structural validation of the addressable part: identity, action, size.

    Deliberately does NOT validate the per-action payload. Everything checked here
    is what makes a receipt trustworthy — if the `command_id` cannot be believed,
    recording a receipt under it would be worse than refusing. Payload validation
    happens after the claim, so a bad payload leaves a readable REJECTED receipt.
    """
    if not isinstance(envelope, dict):
        raise PlatformCommandRejected(
            f"envelope must be a JSON object, got {type(envelope).__name__}",
            "NOT_AN_OBJECT",
        )
    unknown = sorted(set(envelope) - ENVELOPE_KEYS)
    if unknown:
        raise PlatformCommandRejected(
            f"unknown top-level field(s) {unknown}; legal: {sorted(ENVELOPE_KEYS)}",
            "UNKNOWN_TOP_LEVEL_FIELD",
        )
    missing = sorted(ENVELOPE_KEYS - set(envelope))
    if missing:
        raise PlatformCommandRejected(
            f"envelope is missing required field(s) {missing}",
            "MISSING_TOP_LEVEL_FIELD",
        )
    if envelope["schema_version"] != SCHEMA_VERSION:
        raise PlatformCommandRejected(
            f"schema_version must be {SCHEMA_VERSION}, got "
            f"{envelope['schema_version']!r}",
            "BAD_SCHEMA_VERSION",
        )
    command_id = envelope["command_id"]
    if not isinstance(command_id, str) or not _COMMAND_ID_RE.match(command_id):
        raise PlatformCommandRejected(
            "command_id must be 8-64 chars of [A-Za-z0-9._-]; it is the sole basis "
            "of exactly-once, so it must be stable, unique and legible",
            "BAD_COMMAND_ID",
        )
    action = envelope["action"]
    if not isinstance(action, str) or action not in ACTIONS:
        raise PlatformCommandRejected(
            f"action {action!r} is not in the allowed vocabulary: {sorted(ACTIONS)}",
            "UNKNOWN_ACTION",
        )
    actor = envelope["actor"]
    if not isinstance(actor, str) or not _ACTOR_RE.match(actor):
        raise PlatformCommandRejected(
            "actor must be 1-64 chars of [A-Za-z0-9._@ -] naming who is acting",
            "BAD_ACTOR",
        )
    actor_role = envelope["actor_role"]
    if actor_role != REQUIRED_ACTOR_ROLE:
        raise PlatformCommandRejected(
            f"actor_role must be {REQUIRED_ACTOR_ROLE}; this transport belongs to "
            "that review and a receipt naming another role would misattribute the "
            "change",
            "BAD_ACTOR_ROLE",
        )
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise PlatformCommandRejected(
            f"payload must be a JSON object, got {type(payload).__name__}",
            "PAYLOAD_NOT_AN_OBJECT",
        )
    if len(payload) > MAX_PAYLOAD_KEYS:
        raise PlatformCommandRejected(
            f"payload has {len(payload)} fields; the limit is {MAX_PAYLOAD_KEYS}",
            "PAYLOAD_TOO_MANY_FIELDS",
        )
    canonical = canonical_envelope(envelope)
    return _Envelope(
        command_id=command_id,
        action=action,
        actor=actor,
        actor_role=actor_role,
        payload=payload,
        schema_version=SCHEMA_VERSION,
        canonical=canonical,
        payload_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def vocabulary(action: str) -> frozenset[str]:
    spec = ACTIONS[action]
    return spec.required | spec.optional


def _check_payload(action: str, payload: dict) -> None:
    spec = ACTIONS[action]
    known = vocabulary(action)
    unknown = sorted(k for k in payload if k not in known)
    if unknown:
        raise PlatformCommandRejected(
            f"{action}: {len(unknown)} unrecognised payload field(s) — names not "
            f"echoed because they are author-supplied. Legal fields: "
            f"{sorted(known)}",
            "UNKNOWN_PAYLOAD_FIELD",
        )
    missing = sorted(spec.required - set(payload))
    if missing:
        raise PlatformCommandRejected(
            f"{action} is missing required payload field(s) {missing}",
            "MISSING_PAYLOAD_FIELD",
        )
    for value in payload.values():
        if isinstance(value, str) and len(value) > MAX_STRING_CHARS:
            raise PlatformCommandRejected(
                f"{action}: a payload string is {len(value)} chars; the limit is "
                f"{MAX_STRING_CHARS}",
                "PAYLOAD_STRING_TOO_LONG",
            )


# ---------------------------------------------------------------------------
# Actions — a static allowlist of four, each a thin call into the canonical
# helpers. Nothing here reaches an order path, an exposure setting, a gate
# verdict, a lifecycle transition or `apply_new_version`.
# ---------------------------------------------------------------------------


def _revision_or_refuse(session, ref: str) -> PlatformRevision:
    if not isinstance(ref, str) or not _REF_RE.match(ref):
        raise PlatformCommandRejected(
            "revision must be COMPONENT:version", "BAD_REVISION_REF"
        )
    revision = platform_impact.get_revision(session, ref)
    if revision is None:
        raise PlatformCommandRejected(
            f"no platform revision {ref}", "UNKNOWN_REVISION"
        )
    return revision


def _experiment_or_refuse(session, key: str):
    from .models import Experiment

    if not isinstance(key, str) or not key.strip():
        raise PlatformCommandRejected("experiment must be a key", "BAD_EXPERIMENT")
    experiment = session.scalar(select(Experiment).where(Experiment.key == key.strip()))
    if experiment is None:
        raise PlatformCommandRejected(
            f"no experiment {key.strip()!r}", "UNKNOWN_EXPERIMENT"
        )
    return experiment


def _register_revision(session, env: _Envelope, now: datetime):
    """Register an immutable, PENDING revision. Never activates.

    `activate=` is not exposed and is not passed: registration and activation are
    separate commands on purpose, because activation is impact-gated and its
    boundary must be measured on the worker that first serves the change.
    """
    p = env.payload
    component = p["component"]
    if not isinstance(component, str) or not re.match(r"^[A-Z][A-Z0-9_]{1,31}$", component):
        raise PlatformCommandRejected("component must be a KEY", "BAD_COMPONENT")
    revision = service.register_platform_revision(
        session,
        component,
        version=p["version"],
        description=p.get("description"),
        reason=p.get("reason"),
        fingerprint=p.get("fingerprint"),
        backward_compatibility=p.get("backward_compatibility"),
        normalization_available=p.get("normalization_available"),
        safety_class=p.get("safety_class"),
        pr_ref=p.get("pr_ref"),
        actor=env.actor,
    )
    if revision.status != "pending":
        # Defence in depth: the helper cannot activate without activate=True, and
        # this transport never passes it. If that ever changes, fail here rather
        # than silently ship an unreviewed activation.
        raise PlatformCommandRejected(
            f"registration produced status {revision.status!r}, expected 'pending'",
            "UNEXPECTED_STATUS",
        )
    return revision


def _record_impacts(session, env: _Envelope, now: datetime):
    """Propose AND accept a bounded batch of per-experiment dispositions.

    One transaction, so the batch cannot land half-classified and leave the
    activation gate reading 'safe' on an incomplete review. An accepted
    `NO_ACTION` settles as applied inside `accept_impact` — that is the engine's
    behaviour, not something this transport arranges.
    """
    revision = _revision_or_refuse(session, env.payload["revision"])
    rows = env.payload["impacts"]
    if not isinstance(rows, list) or not rows:
        raise PlatformCommandRejected("impacts must be a non-empty list", "BAD_IMPACTS")
    if len(rows) > MAX_IMPACT_ROWS:
        raise PlatformCommandRejected(
            f"{len(rows)} impact rows; the limit is {MAX_IMPACT_ROWS}",
            "TOO_MANY_IMPACTS",
        )
    recorded = []
    for row in rows:
        if not isinstance(row, dict):
            raise PlatformCommandRejected("each impact row must be an object", "BAD_IMPACT_ROW")
        unknown = sorted(set(row) - {"experiment", "impact_class", "action", "rationale"})
        if unknown:
            raise PlatformCommandRejected(
                f"{len(unknown)} unrecognised field(s) in an impact row — names not "
                "echoed because they are author-supplied",
                "UNKNOWN_IMPACT_FIELD",
            )
        missing = sorted({"experiment", "impact_class", "action", "rationale"} - set(row))
        if missing:
            raise PlatformCommandRejected(
                f"an impact row is missing {missing}", "MISSING_IMPACT_FIELD"
            )
        rationale = row["rationale"]
        if not isinstance(rationale, str) or len(rationale) > MAX_STRING_CHARS:
            raise PlatformCommandRejected(
                "an impact rationale is missing or too long", "BAD_IMPACT_RATIONALE"
            )
        experiment = _experiment_or_refuse(session, row["experiment"])
        record = platform_impact.propose_impact(
            session,
            revision,
            experiment,
            impact_class=row["impact_class"],
            action=row["action"],
            rationale=rationale,
            decided_by=env.actor,
            decided_at=now,
        )
        platform_impact.accept_impact(session, record, accepted_by=env.actor, at=now)
        recorded.append(record)
    return recorded


def _establish_boundary(session, env: _Envelope, now: datetime):
    """Set the MEASURED activation boundary on a revision activated without one.

    The recovery path for the case CUTOVER is built to avoid. The instant is the
    caller's measurement; the helper refuses to move one already established.
    """
    revision = _revision_or_refuse(session, env.payload["revision"])
    at = _parse_instant(env.payload["activated_at"])
    return service.establish_activation_boundary(session, revision, activated_at=at)


def _cutover(session, env: _Envelope, now: datetime):
    """The atomic pre-cycle cutover, run on the first boot that serves the change.

    One transaction: activate at the MEASURED instant, then close the old epoch
    and open the new one at exactly that instant for each named experiment. The
    boundary is this worker's own boot time — not the merge commit, not now() at
    some later convenience — and the fingerprint precondition (checked before the
    claim, see the module docstring) is what makes that instant honest: the
    command refuses to run at all on a worker whose loaded taxonomy is not the one
    the revision describes.

    Activation is never forced. If the gate is not safe the whole transaction
    rolls back to the savepoint and the receipt says so.
    """
    revision = _revision_or_refuse(session, env.payload["revision"])
    if revision.status != "pending":
        raise PlatformCommandRejected(
            f"revision is {revision.status}, not pending — a cutover activates a "
            "pending revision exactly once",
            "REVISION_NOT_PENDING",
        )
    keys = env.payload.get("new_epoch_experiments") or []
    if not isinstance(keys, list) or len(keys) > MAX_CUTOVER_EXPERIMENTS:
        raise PlatformCommandRejected(
            f"new_epoch_experiments must be a list of at most "
            f"{MAX_CUTOVER_EXPERIMENTS} keys",
            "BAD_CUTOVER_EXPERIMENTS",
        )
    gate = platform_impact.activation_gate(session, revision)
    if not gate.get("safe"):
        raise PlatformCommandRejected(
            "activation gate is not safe — every affected active experiment needs "
            "an accepted disposition first; this transport never forces",
            "ACTIVATION_GATE_UNSAFE",
        )
    measured = now
    service.activate_platform_revision(
        session, revision, activated_at=measured, actor=env.actor
    )
    epochs = []
    for key in keys:
        experiment = _experiment_or_refuse(session, key)
        record = session.scalar(
            select(PlatformImpactAction).where(
                PlatformImpactAction.revision_id == revision.id,
                PlatformImpactAction.experiment_id == experiment.id,
            )
        )
        if record is None:
            raise PlatformCommandRejected(
                f"experiment {experiment.key} has no impact record for this revision",
                "NO_IMPACT_RECORD",
            )
        epochs.append(
            platform_impact.apply_new_epoch(
                session, record, actor=env.actor, boundary=measured
            )
        )
    return {"revision": revision, "epochs": epochs, "measured": measured}


def _parse_instant(value) -> datetime:
    if not isinstance(value, str):
        raise PlatformCommandRejected("activated_at must be an RFC3339 string", "BAD_INSTANT")
    text = value.strip().replace("Z", "+00:00")
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        raise PlatformCommandRejected(
            "activated_at is not a parseable RFC3339 instant", "BAD_INSTANT"
        ) from None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Action:
    required: frozenset[str]
    optional: frozenset[str]
    run: Any
    doc: str


ACTIONS: dict[str, _Action] = {
    "REGISTER_REVISION": _Action(
        required=frozenset({"component", "version"}),
        optional=frozenset({
            "description", "reason", "fingerprint", "backward_compatibility",
            "normalization_available", "safety_class", "pr_ref",
        }),
        run=_register_revision,
        doc="Register an immutable PENDING revision of one component.",
    ),
    "RECORD_IMPACTS": _Action(
        required=frozenset({"revision", "impacts"}),
        optional=frozenset(),
        run=_record_impacts,
        doc="Propose and accept a bounded batch of per-experiment dispositions.",
    ),
    "ESTABLISH_BOUNDARY": _Action(
        required=frozenset({"revision", "activated_at"}),
        optional=frozenset(),
        run=_establish_boundary,
        doc="Record the measured activation boundary of an already-active revision.",
    ),
    "CUTOVER": _Action(
        required=frozenset({"revision", "expect_taxonomy_fingerprint"}),
        optional=frozenset({"new_epoch_experiments"}),
        run=_cutover,
        doc="Atomic pre-cycle cutover: activate at the measured boot instant and "
            "re-epoch the accepted NEW_EPOCH experiments at exactly that instant.",
    ),
}


# ---------------------------------------------------------------------------
# Preconditions — checked BEFORE the claim, so a deferral consumes nothing
# ---------------------------------------------------------------------------


def check_preconditions(env: _Envelope) -> None:
    """Raise `PlatformCommandDeferred` when this worker must not run this command.

    Only CUTOVER has one, and it is the property that makes the recorded boundary
    an actual measurement: the fingerprint of the taxonomy loaded in THIS process
    must equal the one the envelope names. On any other boot — a redeploy for an
    unrelated merge that happens to land first — the command is left untouched and
    armed rather than burned on a worker that is not serving the change.
    """
    if env.action != "CUTOVER":
        return
    expected = env.payload.get("expect_taxonomy_fingerprint")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise PlatformCommandRejected(
            "expect_taxonomy_fingerprint must be a 64-char sha256 hex digest",
            "BAD_FINGERPRINT",
        )
    actual = taxonomy_fingerprint()
    if actual != expected:
        raise PlatformCommandDeferred(
            "this worker's taxonomy is not the one the revision describes "
            f"(loaded {actual[:16]}…, expected {expected[:16]}…) — deferring, the "
            "command stays armed for the boot that serves the change",
            "TAXONOMY_NOT_DEPLOYED",
        )


# ---------------------------------------------------------------------------
# Redaction — an error message must not echo what was submitted
# ---------------------------------------------------------------------------

MIN_REDACTED_CHARS = 3
MIN_REDACTED_TOKEN_CHARS = 4
REDACTION = "<redacted>"
_TOKEN_SPLIT = re.compile(r"""[\s/\\,;:()\[\]{}"'=<>|?!*&%$#@+~`^]+""")


def _submitted_strings(payload: Any, out: set[str]) -> None:
    """Every author-supplied string anywhere in the payload, including inside the
    impact rows — a batch nests, and a nested value is as author-controlled as a
    top-level one."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if len(str(key)) >= MIN_REDACTED_CHARS:
                out.add(str(key))
            _submitted_strings(value, out)
    elif isinstance(payload, list):
        for item in payload:
            _submitted_strings(item, out)
    elif isinstance(payload, str):
        if len(payload) >= MIN_REDACTED_CHARS:
            out.add(payload)
        out.update(
            tok for tok in _TOKEN_SPLIT.split(payload)
            if len(tok) >= MIN_REDACTED_TOKEN_CHARS
        )


def _redactions(env: _Envelope | None) -> list[str]:
    if env is None:
        return []
    out: set[str] = {env.canonical}
    _submitted_strings(env.payload, out)
    known = vocabulary(env.action) | {
        "experiment", "impact_class", "action", "rationale",
    }
    out -= {k for k in known}
    return sorted(out, key=len, reverse=True)


def _sanitize(exc: BaseException, env: _Envelope | None) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for needle in _redactions(env):
        if needle:
            text = text.replace(needle, REDACTION)
    return text[:MAX_ERROR_CHARS]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

#: Refusals the workflow is SUPPOSED to produce. Anything else is FAILED, because
#: an unexpected exception means the executor no longer knows what it did.
_REFUSALS = (service.ExperimentOsError, PlatformCommandRejected, ValueError)


def _claim(session, env: _Envelope, now: datetime) -> int | None:
    """Claim `command_id` for this worker, or return None if another already has it.

    `ON CONFLICT DO NOTHING RETURNING` rather than catching an IntegrityError,
    which on Postgres poisons the very transaction that then has to record the
    receipt.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - the repo runs on exactly these two
        raise PlatformCommandRejected(
            f"platform commands need ON CONFLICT support; dialect {dialect!r} has none",
            "UNSUPPORTED_DIALECT",
        )
    stmt = (
        insert(ExperimentOsPlatformCommand)
        .values(
            command_id=env.command_id,
            action=env.action,
            actor=env.actor,
            actor_role=env.actor_role,
            payload_hash=env.payload_hash,
            payload_json=env.payload,
            schema_version=env.schema_version,
            status=CommandStatus.RUNNING,
            requested_at=now,
            started_at=now,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["command_id"])
        .returning(ExperimentOsPlatformCommand.id)
    )
    return session.execute(stmt).scalar_one_or_none()


def _result_of(produced: Any) -> dict | None:
    """Bounded, machine-generated metadata about what the command produced.

    Identifiers and states only — never the prose that was submitted.
    """
    if isinstance(produced, PlatformRevision):
        return {
            "kind": "platform_revision",
            "revision_id": produced.id,
            "version": produced.version,
            "status": produced.status,
            "activated_at": str(produced.activated_at) if produced.activated_at else None,
        }
    if isinstance(produced, list) and produced and isinstance(produced[0], PlatformImpactAction):
        return {
            "kind": "impact_records",
            "count": len(produced),
            "records": [
                {
                    "record_id": r.id,
                    "experiment_id": r.experiment_id,
                    "impact_class": r.impact_class,
                    "action": r.action,
                    "status": r.status,
                }
                for r in produced
            ],
        }
    if isinstance(produced, dict) and "epochs" in produced:
        revision = produced["revision"]
        return {
            "kind": "cutover",
            "revision_id": revision.id,
            "status": revision.status,
            "activated_at": str(revision.activated_at),
            "measured_boundary": str(produced["measured"]),
            "new_epochs": [
                {"epoch_id": e.id, "started_at": str(e.started_at)}
                for e in produced["epochs"]
            ],
        }
    return None


def _receipt_view(row: ExperimentOsPlatformCommand, **extra) -> dict:
    """The log/return shape. Metadata only — no payload, ever."""
    view = {
        "command_id": row.command_id,
        "action": row.action,
        "actor": row.actor,
        "actor_role": row.actor_role,
        "status": row.status,
        "payload_hash": row.payload_hash,
        "completed_at": str(row.completed_at) if row.completed_at else None,
        "result": row.result_json,
        "error": row.error,
    }
    view.update(extra)
    return view


def execute_envelope(session, envelope: Any, *, now: datetime | None = None) -> dict:
    """Validate, defer-or-claim, execute, and commit a durable receipt.

    Only two outcomes leave no receipt: an envelope so malformed that no
    trustworthy `command_id` can be recorded against it, and a precondition
    deferral, which is not a refusal and must stay re-runnable.
    """
    now = now or _now()
    env = _validate_envelope(envelope)
    check_preconditions(env)  # before any database work, and before the claim

    receipt_id = _claim(session, env, now)
    if receipt_id is None:
        existing = session.scalar(
            select(ExperimentOsPlatformCommand).where(
                ExperimentOsPlatformCommand.command_id == env.command_id
            )
        )
        replayed = existing.payload_hash == env.payload_hash
        if not replayed:
            # Same name, different command. Execute nothing and change nothing:
            # the stored receipt belongs to whatever really ran under that id.
            logger.error(
                "platform command id collision; refusing to execute",
                extra={"extra_fields": {
                    "command_id": env.command_id,
                    "stored_hash": existing.payload_hash,
                    "submitted_hash": env.payload_hash,
                }},
            )
        return _receipt_view(
            existing, replayed=replayed, collision=not replayed, executed=False
        )

    row = session.get(ExperimentOsPlatformCommand, receipt_id)
    savepoint = session.begin_nested()
    try:
        _check_payload(env.action, env.payload)
        produced = ACTIONS[env.action].run(session, env, now)
        row.result_json = _result_of(produced)
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
        # unsanitized message and every frame's locals — including the envelope —
        # through the handler's formatter. The receipt already holds a bounded,
        # redacted account.
        logger.error(
            "platform command executor failed",
            extra={"extra_fields": {
                "command_id": env.command_id, "action": env.action,
            }},
        )
    row.completed_at = _now()
    session.flush()
    return _receipt_view(row, replayed=False, collision=False, executed=True)


def run_boot_command(session, raw: str, *, now: datetime | None = None) -> dict | None:
    """Boot entry point: parse the transport variable and execute it.

    Returns a log-safe receipt view, None when nothing was set, or a deferral view
    when a precondition says this is not the worker that should run it. The raw
    value is never returned, logged or embedded in an error.
    """
    text = (raw or "").strip()
    if not text:
        return None
    size = len(text.encode("utf-8"))
    if size > MAX_ENVELOPE_BYTES:
        raise PlatformCommandRejected(
            f"platform command is {size} bytes; the limit is {MAX_ENVELOPE_BYTES}",
            "ENVELOPE_TOO_LARGE",
        )
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlatformCommandRejected(
            f"platform command is not valid JSON (line {exc.lineno}, col {exc.colno})",
            "NOT_JSON",
        ) from None
    try:
        return execute_envelope(session, envelope, now=now)
    except PlatformCommandDeferred as exc:
        # Not a receipt and not a failure: nothing was claimed, so the same
        # envelope runs again on the next boot.
        return {
            "status": "DEFERRED",
            "code": exc.code,
            "reason": str(exc),
            "executed": False,
        }


def safe_error_fields(raw: str | None, exc: BaseException) -> dict:
    """Log-safe metadata for a transport failure that produced NO receipt.

    The exception may quote the offending document, or from SQLAlchemy the bound
    parameters (`payload_json` among them), so the message is never logged. What
    the operator gets is the class, a stable code, and the hash and length of the
    value that failed — enough to match the line to the envelope they submitted.
    """
    text = raw or ""
    encoded = text.encode("utf-8")
    return {
        "error_class": type(exc).__name__,
        "error_code": getattr(exc, "code", None) or "TRANSPORT_ERROR",
        "command_bytes": len(encoded),
        "command_hash": hashlib.sha256(encoded).hexdigest()[:16] if encoded else None,
    }
