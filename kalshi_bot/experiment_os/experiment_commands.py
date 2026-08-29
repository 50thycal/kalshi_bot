"""`EXPERIMENT_OS_EXPERIMENT_COMMAND` — the sanctioned write path for experiment
LIFECYCLE work, executed once at worker boot.

WHY A THIRD TRANSPORT
---------------------
Two already exist and neither can do this. `EXPERIMENT_OS_ISSUE_COMMAND` moves
investigations; `EXPERIMENT_OS_PLATFORM_COMMAND` moves Platform Revisions and
their impact dispositions. Both vocabularies are deliberately disjoint, so a
ticket can never mutate a revision. Registering a Version, freezing it, opening
an epoch and arming a canary belong to neither, and until now had no transport at
all — the ops channel is read-only against Postgres by design (a SELECT-only
role, enforced server-side), so that work could only be done by an operator on
their own writable connection.

This module keeps that rule rather than working around it: **the worker remains
the only writer.** The variable is a request; the worker executes it, once, and
records a receipt.

WHAT IT CAN AND CANNOT SAY
--------------------------
The vocabulary is deliberately NARROW, and the reason is worth stating because
the obvious design is worse. A generic transport — "create a version with these
arms and this gate spec" — would let a scientific contract be authored in an
environment variable, unreviewed, on a public branch. Pre-registration would mean
nothing: the thing a gate is supposed to have committed to *before* seeing
results could be written the same afternoon the results arrived.

So an envelope cannot author anything. It names a **package** — a contract that
already exists in reviewed code, in the repository, with its arms, its risk
envelope, its gate specs and its tags as literals someone read in a pull request.
The envelope's whole content is *which* reviewed package to run and who approved
it. Adding a package is a code change; running one is this transport.

    REGISTER_PACKAGE   register the package's successor contract: version, arms,
                       gates, epoch, deployments. Arms nothing, places nothing.
    REPAIR_LINEAGE     run a reviewed one-shot repair of deployment ROWS an engine
                       defect left inconsistent. Authors no contract, moves no
                       lifecycle state, touches no gate, creates no live lineage.
    ARM_CANARY         arm the package's live canary and its twin through
                       `service.arm_live_canary`. **This expands real-money
                       capability** and requires `approved_by` naming a person.

Neither action can place an order. Even a successful ARM_CANARY leaves the
runtime allowlist exactly as it was: `LIVE_STRATEGIES` is a separate switch, and
a transport that could both arm the canary and open the allowlist would be one
environment variable away from unreviewed exposure.

WHAT THIS TRANSPORT STILL CANNOT DO
-----------------------------------
Everything `arm_live_canary` refuses, it still refuses — fresh tags, a twin at
the same instant, a pre-registered risk envelope, a complete arm mapping, and a
FRESH synchronous re-evaluation of the promotion gate. This module carries the
call; it does not soften it, and it passes no flag that could.

⚠ THE ENVELOPE IS PUBLIC. It is committed in plaintext to `ops/request.json` on a
public branch and stays in Git history. Package names, actor names and approver
names are the only things it carries, and all of them are meant to be disclosed.
Never put a secret, a credential or private evidence in one.
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

from sqlalchemy import select

from . import service
from .models import ExperimentOsExperimentCommand

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

ENVELOPE_KEYS = frozenset(
    {"command_id", "action", "actor", "actor_role", "payload", "schema_version"}
)

#: Which session role may author which action, mirroring `.claude/sessions/`.
#: Attribution, not authorization — the real authority is who can set a Railway
#: variable — but a receipt naming the wrong role would misdescribe who decided,
#: and arming real money is Live Ops' call rather than a build session's.
ACTION_ROLES: dict[str, frozenset[str]] = {
    "REGISTER_PACKAGE": frozenset({"RESEARCH_LAB", "TASK_SPECIFIC", "LIVE_OPS"}),
    "REPAIR_LINEAGE": frozenset({"LIVE_OPS", "TASK_SPECIFIC"}),
    "ARM_CANARY": frozenset({"LIVE_OPS"}),
}

MAX_ENVELOPE_BYTES = 4096
MAX_STRING_CHARS = 256
MAX_ERROR_CHARS = 400
MAX_PAYLOAD_KEYS = 8

_COMMAND_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_ACTOR_RE = re.compile(r"^[A-Za-z0-9._@ -]{1,64}$")
_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,47}$")


class CommandStatus:
    """Receipt states. All three terminal states are final for a `command_id`."""

    SUCCEEDED = "SUCCEEDED"
    #: Well formed but refused by the workflow — an unknown package, a gate that
    #: did not pass, a tag with inherited history. The system working.
    REJECTED = "REJECTED"
    #: An unexpected exception. The executor no longer knows what it did.
    FAILED = "FAILED"
    RUNNING = "RUNNING"


class ExperimentCommandRejected(ValueError):
    def __init__(self, message: str, code: str = "ENVELOPE_REJECTED"):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# The package registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExperimentPackage:
    """One reviewed lifecycle package.

    `register` and `arm` are the package's own functions. They are called with a
    session and keyword arguments this transport controls entirely — a package
    never sees the envelope, so a payload cannot reach into one and change what
    it does."""

    name: str
    experiment_key: str
    description: str
    register: Callable[..., dict]
    #: None for a package that registers a contract but arms nothing.
    arm: Callable[..., dict] | None = None
    #: Every Railway variable the package's runtime-allowlist step sets. NOT
    #: something this transport can apply — that step is a separate Live Ops act
    #: through the `env` channel, deliberately unreachable from here. It is
    #: declared so CI can assert each name clears `railway_env.ALLOWED_VARS`: a
    #: package whose activation step the channel refuses halfway through is the
    #: #266 defect class, and it should fail in CI rather than in front of an
    #: operator with a write already submitted.
    activation_vars: frozenset[str] = frozenset()
    #: A one-shot lineage REPAIR: reviewed code that fixes deployment rows an
    #: engine defect left inconsistent. Deliberately its own slot rather than a
    #: mode of `register` — a repair authors no contract, moves no lifecycle
    #: state and touches no gate, and a package that could do both would blur
    #: the one boundary this transport exists to keep.
    repair: Callable[..., dict] | None = None


def _no_contract(session, **kw):
    """A repair package has no contract to register, and says so rather than
    quietly doing nothing if REGISTER_PACKAGE is aimed at it."""
    raise ExperimentCommandRejected(
        "this is a lineage repair, not a contract — use REPAIR_LINEAGE",
        "NOT_A_CONTRACT",
    )


def _packages() -> dict[str, ExperimentPackage]:
    """Imported lazily so this module stays importable when a package's own
    dependencies are not (and so the boot hook's error handler can still run)."""
    from . import canary_mmsell10, marktangle, repair_tmmsell_epoch

    return {
        "marktangle-reversion": ExperimentPackage(
            name="marktangle-reversion",
            experiment_key=marktangle.EXPERIMENT_KEY,
            description=(
                "MARKTANGLE conditional-reversion contract: five arms (three "
                "treatments, the continuation mirror as control, the un-gated "
                "fallacy benchmark), both paper gates pre-registered, and a "
                "TAGLESS probe deployment. Arms nothing, trades nothing."
            ),
            register=marktangle.register,
        ),
        "tmmsell-epoch-repair": ExperimentPackage(
            name="tmmsell-epoch-repair",
            experiment_key=repair_tmmsell_epoch.EXPERIMENT_KEY,
            description=(
                "XOS-000011: end the deployment the 2026-08-24 taxonomy boundary "
                "stranded on mmsell-type-tight v1/e1 and re-register its four "
                "Tmmsell books on the open epoch"
            ),
            register=_no_contract,
            repair=repair_tmmsell_epoch.repair,
        ),
        "mmsell10-canary": ExperimentPackage(
            name="mmsell10-canary",
            experiment_key=canary_mmsell10.EXPERIMENT_KEY,
            description=(
                "mmsell-price-ceiling successor contract (single arm mmsell10, "
                "Stage-1 risk envelope, pre-registered keep/stop gate) and its "
                "live canary with an exact paper twin"
            ),
            register=canary_mmsell10.register_successor_version,
            arm=canary_mmsell10.arm,
            activation_vars=canary_mmsell10.ACTIVATION_VARS,
        ),
    }


def package_names() -> list[str]:
    return sorted(_packages())


def _package_or_refuse(name: Any) -> ExperimentPackage:
    if not isinstance(name, str) or not _PACKAGE_RE.match(name):
        raise ExperimentCommandRejected(
            "package must be 3-48 chars of [a-z0-9-] naming a REVIEWED package; "
            f"registered: {package_names()}",
            "BAD_PACKAGE",
        )
    package = _packages().get(name)
    if package is None:
        raise ExperimentCommandRejected(
            f"no registered package {name!r} — a package is code in the "
            f"repository, not something an envelope can define. Registered: "
            f"{package_names()}",
            "UNKNOWN_PACKAGE",
        )
    return package


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
    """Structural validation of the addressable part: identity, action, role, size.

    Deliberately does NOT validate the per-action payload. Everything checked here
    is what makes a receipt trustworthy — if the `command_id` cannot be believed,
    recording a receipt under it would be worse than refusing. Payload validation
    happens after the claim, so a bad payload leaves a readable REJECTED receipt.
    """
    if not isinstance(envelope, dict):
        raise ExperimentCommandRejected(
            f"envelope must be a JSON object, got {type(envelope).__name__}",
            "NOT_AN_OBJECT",
        )
    unknown = sorted(set(envelope) - ENVELOPE_KEYS)
    if unknown:
        raise ExperimentCommandRejected(
            f"unknown top-level field(s) {unknown}; legal: {sorted(ENVELOPE_KEYS)}",
            "UNKNOWN_TOP_LEVEL_FIELD",
        )
    missing = sorted(ENVELOPE_KEYS - set(envelope))
    if missing:
        raise ExperimentCommandRejected(
            f"envelope is missing required field(s) {missing}",
            "MISSING_TOP_LEVEL_FIELD",
        )
    if envelope["schema_version"] != SCHEMA_VERSION:
        raise ExperimentCommandRejected(
            f"schema_version must be {SCHEMA_VERSION}, got "
            f"{envelope['schema_version']!r}",
            "BAD_SCHEMA_VERSION",
        )
    command_id = envelope["command_id"]
    if not isinstance(command_id, str) or not _COMMAND_ID_RE.match(command_id):
        raise ExperimentCommandRejected(
            "command_id must be 8-64 chars of [A-Za-z0-9._-]; it is the sole basis "
            "of exactly-once, so it must be stable, unique and legible",
            "BAD_COMMAND_ID",
        )
    action = envelope["action"]
    if not isinstance(action, str) or action not in ACTIONS:
        raise ExperimentCommandRejected(
            f"action {action!r} is not in the allowed vocabulary: {sorted(ACTIONS)}",
            "UNKNOWN_ACTION",
        )
    actor = envelope["actor"]
    if not isinstance(actor, str) or not _ACTOR_RE.match(actor):
        raise ExperimentCommandRejected(
            "actor must be 1-64 chars of [A-Za-z0-9._@ -] naming who is acting",
            "BAD_ACTOR",
        )
    actor_role = envelope["actor_role"]
    allowed = ACTION_ROLES[action]
    if actor_role not in allowed:
        raise ExperimentCommandRejected(
            f"actor_role for {action} must be one of {sorted(allowed)}; a receipt "
            "naming another role would misdescribe who decided this",
            "BAD_ACTOR_ROLE",
        )
    payload = envelope["payload"]
    if not isinstance(payload, dict):
        raise ExperimentCommandRejected(
            f"payload must be a JSON object, got {type(payload).__name__}",
            "PAYLOAD_NOT_AN_OBJECT",
        )
    if len(payload) > MAX_PAYLOAD_KEYS:
        raise ExperimentCommandRejected(
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
        raise ExperimentCommandRejected(
            f"{action}: {len(unknown)} unrecognised payload field(s) — names not "
            f"echoed because they are author-supplied. Legal fields: {sorted(known)}",
            "UNKNOWN_PAYLOAD_FIELD",
        )
    missing = sorted(spec.required - set(payload))
    if missing:
        raise ExperimentCommandRejected(
            f"{action} is missing required payload field(s) {missing}",
            "MISSING_PAYLOAD_FIELD",
        )
    for value in payload.values():
        if isinstance(value, str) and len(value) > MAX_STRING_CHARS:
            raise ExperimentCommandRejected(
                f"{action}: a payload string is {len(value)} chars; the limit is "
                f"{MAX_STRING_CHARS}",
                "PAYLOAD_STRING_TOO_LONG",
            )


# ---------------------------------------------------------------------------
# Actions — two, each a thin call into a REVIEWED package
# ---------------------------------------------------------------------------


def _register_package(session, env: _Envelope, now: datetime):
    """Register the package's successor contract. Arms nothing.

    `promotion_sample_floor` is the one knob an envelope may turn, and only
    upward: it ADDS an evidence floor to the promotion gate, which can make the
    gate stricter and can never make it pass on less. Everything else — arms,
    parameters, risk envelope, gate thresholds, tags — is a literal in reviewed
    code and is not reachable from here.
    """
    package = _package_or_refuse(env.payload.get("package"))
    floor = env.payload.get("promotion_sample_floor")
    if floor is not None:
        if not isinstance(floor, int) or isinstance(floor, bool) or floor < 0:
            raise ExperimentCommandRejected(
                "promotion_sample_floor must be a non-negative integer",
                "BAD_SAMPLE_FLOOR",
            )
    produced = package.register(
        session, actor=env.actor, promotion_sample_floor=floor
    )
    return {"kind": "register", "package": package.name, "produced": produced}


def _repair_lineage(session, env: _Envelope, now: datetime):
    """Run a reviewed package's one-shot lineage repair.

    A repair fixes deployment ROWS an engine defect left inconsistent — a
    deployment stranded on a closed epoch, a successor epoch left empty. It is
    bounded by construction: the package checks every precondition it was reviewed
    against and refuses if production does not match, it is idempotent, and it can
    reach no gate, verdict, transition, Version or epoch. What it CAN do is make a
    book admissible again, which is why it leaves a receipt naming who asked.

    It creates no real-money capability: `arm_live_canary` remains the only path
    that may register live lineage, and `carry_deployments_forward` refuses a live
    or twin deployment by name.
    """
    del now
    package = _package_or_refuse(env.payload.get("package"))
    if package.repair is None:
        raise ExperimentCommandRejected(
            f"package {package.name!r} declares no repair", "NO_REPAIR"
        )
    produced = package.repair(session, actor=env.actor)
    return {"kind": "repair", "package": package.name, "produced": produced}


def _arm_canary(session, env: _Envelope, now: datetime):
    """Arm the package's live canary and its twin. EXPANDS REAL-MONEY CAPABILITY.

    `approved_by` is required and is recorded on the lifecycle transition, so the
    audit trail names a person rather than a process. The transport adds nothing
    to `arm_live_canary` and softens nothing: the promotion gate is re-evaluated
    synchronously, and a verdict other than PASS refuses the whole command.

    It still places no order. `LIVE_STRATEGIES` is a separate switch and is not
    reachable from this transport.
    """
    package = _package_or_refuse(env.payload.get("package"))
    if package.arm is None:
        raise ExperimentCommandRejected(
            f"package {package.name!r} registers a contract but arms nothing",
            "PACKAGE_NOT_ARMABLE",
        )
    approved_by = env.payload.get("approved_by")
    if not isinstance(approved_by, str) or not _ACTOR_RE.match(approved_by or ""):
        raise ExperimentCommandRejected(
            "approved_by must name the person approving real-money capability",
            "BAD_APPROVED_BY",
        )
    produced = package.arm(session, approved_by=approved_by, actor=env.actor)
    return {"kind": "arm", "package": package.name, "produced": produced}


@dataclass(frozen=True)
class _Action:
    required: frozenset[str]
    optional: frozenset[str]
    run: Callable[[Any, _Envelope, datetime], Any]
    doc: str


ACTIONS: dict[str, _Action] = {
    "REGISTER_PACKAGE": _Action(
        required=frozenset({"package"}),
        optional=frozenset({"promotion_sample_floor"}),
        run=_register_package,
        doc="Register a reviewed package's successor contract. Arms nothing.",
    ),
    "REPAIR_LINEAGE": _Action(
        required=frozenset({"package", "reason"}),
        optional=frozenset(),
        run=_repair_lineage,
        doc="Run a reviewed package's one-shot lineage repair. Registers no "
            "contract, moves no lifecycle state, touches no gate.",
    ),
    "ARM_CANARY": _Action(
        required=frozenset({"package", "approved_by"}),
        optional=frozenset(),
        run=_arm_canary,
        doc="Arm a reviewed package's live canary and twin. Expands real-money "
            "capability; still places no order.",
    ),
}


# ---------------------------------------------------------------------------
# Redaction — an error message must not echo what was submitted
# ---------------------------------------------------------------------------

MIN_REDACTED_CHARS = 3
REDACTION = "<redacted>"


def _redactions(env: _Envelope | None) -> list[str]:
    if env is None:
        return []
    out: set[str] = {env.actor}
    for value in env.payload.values():
        if isinstance(value, str):
            out.add(value)
    return sorted(
        (s for s in out if len(s) >= MIN_REDACTED_CHARS), key=len, reverse=True
    )


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
_REFUSALS = (service.ExperimentOsError, ExperimentCommandRejected, ValueError)


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
        raise ExperimentCommandRejected(
            f"experiment commands need ON CONFLICT support; dialect {dialect!r} "
            "has none",
            "UNSUPPORTED_DIALECT",
        )
    stmt = (
        insert(ExperimentOsExperimentCommand)
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
        .returning(ExperimentOsExperimentCommand.id)
    )
    return session.execute(stmt).scalar_one_or_none()


def _result_of(produced: Any) -> dict | None:
    """Bounded, machine-generated metadata about what the command produced.

    Identifiers and states only — never the prose that was submitted, and never
    the package's own literals, which are already in the repository.
    """
    if not isinstance(produced, dict):
        return None
    inner = produced.get("produced") or {}
    out: dict = {"kind": produced.get("kind"), "package": produced.get("package")}
    version = inner.get("version")
    if version is not None:
        out["version"] = version.version
        out["version_frozen_at"] = str(version.frozen_at)
    epoch = inner.get("epoch")
    if epoch is not None:
        out["epoch_number"] = epoch.epoch_number
        out["epoch_started_at"] = str(epoch.started_at)
        out["impact_class"] = epoch.impact_class
    for key in ("live", "twin", "paper_deployment"):
        dep = inner.get(key)
        if dep is not None:
            out[f"{key}_deployment"] = dep.deployment_key
            out[f"{key}_started_at"] = str(dep.started_at)
    for key in ("promotion_gate", "keep_gate"):
        gate = inner.get(key)
        if gate is not None:
            out[key] = gate.gate_key
            out[f"{key}_spec_hash"] = gate.spec_hash[:16] if gate.spec_hash else None
    return out


def _receipt_view(row: ExperimentOsExperimentCommand, **extra) -> dict:
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
    """Validate, claim, execute, and commit a durable receipt.

    The only outcome that leaves no receipt is an envelope so malformed that no
    trustworthy `command_id` can be recorded against it.
    """
    now = now or _now()
    env = _validate_envelope(envelope)

    receipt_id = _claim(session, env, now)
    if receipt_id is None:
        existing = session.scalar(
            select(ExperimentOsExperimentCommand).where(
                ExperimentOsExperimentCommand.command_id == env.command_id
            )
        )
        replayed = existing.payload_hash == env.payload_hash
        if not replayed:
            # Same name, different command. Execute nothing and change nothing:
            # the stored receipt belongs to whatever really ran under that id.
            logger.error(
                "experiment command id collision; refusing to execute",
                extra={"extra_fields": {
                    "command_id": env.command_id,
                    "stored_hash": existing.payload_hash,
                    "submitted_hash": env.payload_hash,
                }},
            )
        return _receipt_view(
            existing, replayed=replayed, collision=not replayed, executed=False
        )

    row = session.get(ExperimentOsExperimentCommand, receipt_id)
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
        # unsanitized message and every frame's locals — the envelope among them —
        # through the handler's formatter. The receipt already holds a bounded,
        # redacted account.
        logger.error(
            "experiment command executor failed",
            extra={"extra_fields": {
                "command_id": env.command_id, "action": env.action,
            }},
        )
    row.completed_at = _now()
    session.flush()
    return _receipt_view(row, replayed=False, collision=False, executed=True)


def run_boot_command(session, raw: str, *, now: datetime | None = None) -> dict | None:
    """Boot entry point: parse the transport variable and execute it.

    Returns a log-safe receipt view, or None when nothing was set. The raw value
    is never returned, logged or embedded in an error.
    """
    text = (raw or "").strip()
    if not text:
        return None
    size = len(text.encode("utf-8"))
    if size > MAX_ENVELOPE_BYTES:
        raise ExperimentCommandRejected(
            f"experiment command is {size} bytes; the limit is {MAX_ENVELOPE_BYTES}",
            "ENVELOPE_TOO_LARGE",
        )
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExperimentCommandRejected(
            f"experiment command is not valid JSON (line {exc.lineno}, "
            f"col {exc.colno})",
            "NOT_JSON",
        ) from None
    return execute_envelope(session, envelope, now=now)


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


# ---------------------------------------------------------------------------
# Receipt reads (the ops channel's only view of this transport)
# ---------------------------------------------------------------------------


def receipts(session, *, limit: int = 20) -> list[dict]:
    rows = session.scalars(
        select(ExperimentOsExperimentCommand)
        .order_by(ExperimentOsExperimentCommand.id.desc())
        .limit(max(1, min(int(limit), 100)))
    ).all()
    return [_receipt_view(r, replayed=False, collision=False, executed=True)
            for r in rows]


def receipt(session, command_id: str) -> dict | None:
    row = session.scalar(
        select(ExperimentOsExperimentCommand).where(
            ExperimentOsExperimentCommand.command_id == command_id
        )
    )
    if row is None:
        return None
    view = _receipt_view(row, replayed=False, collision=False, executed=True)
    package = (row.result_json or {}).get("package")
    if package:
        view["experiment"] = getattr(
            _packages().get(package), "experiment_key", None
        )
    return view
