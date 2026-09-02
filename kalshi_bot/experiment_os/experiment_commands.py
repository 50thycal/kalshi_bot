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
from .lifecycle import GateVerdict, LifecycleState
from .models import ExperimentGateResult, ExperimentOsExperimentCommand
from .read import get_experiment

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
    # A retrospective record is bookkeeping an operator attests to, not research and
    # not a real-money act. Research Lab is deliberately absent: the session that RAN
    # the experiment should not also be the one that writes down its own verdict.
    "CLOSE_OUT_RETROSPECTIVE": frozenset({"LIVE_OPS", "TASK_SPECIFIC"}),
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
    #: A one-shot RETROSPECTIVE CLOSE-OUT: record an experiment that ran to a
    #: conclusion outside this system, and retire it, in one act. Its own slot for
    #: the same reason `repair` is — it authors a contract AND records verdicts AND
    #: moves lifecycle state, which is more than `register` may ever do, so it must
    #: be a verb an operator names deliberately rather than a mode hiding inside
    #: one that is documented as arming nothing.
    #:
    #: It is bounded where it counts: `service.close_out_retrospective` refuses a
    #: PASS verdict, refuses any target but RETIRED, and refuses an experiment
    #: holding deployments. `_close_out_retrospective` below re-checks the outcome
    #: rather than trusting the package to have used it.
    close_out: Callable[..., dict] | None = None


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
    from . import (
        canary_mmsell10,
        marktangle,
        marktangle2,
        perp_v1,
        repair_tmmsell_epoch,
        successor_mmsell10_capacity,
    )

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
        "marktangle-2": ExperimentPackage(
            name="marktangle-2",
            experiment_key=marktangle2.EXPERIMENT_KEY,
            description=(
                "MARKTANGLE-2 conditional-dependence contract: two independent tracks "
                "(cross-family conditional reversion; crypto threshold persistence), "
                "each with an independence baseline, three treatments and a mirror "
                "control, four paper gates pre-registered, and a TAGLESS probe "
                "deployment. MARKTANGLE-1 is its recorded predecessor and is "
                "untouched. Arms nothing, trades nothing."
            ),
            register=marktangle2.register,
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
        "mmsell10-capacity-successor": ExperimentPackage(
            name="mmsell10-capacity-successor",
            experiment_key=successor_mmsell10_capacity.SUCCESSOR_KEY,
            description=(
                "The mmsell10 price-ceiling book re-armed at 2x open capacity "
                "(cap 20->40, book ceiling $19.80->$39.60, twin cap 20->250). A "
                "SUCCESSOR experiment rather than a new version because "
                "arm_live_canary requires PAPER and LIVE_CANARY->PAPER is an "
                "illegal rollback, so an already-live experiment has no "
                "sanctioned way to re-arm. Registering ends only the predecessor's "
                "PAPER deployment, to hand the mmsell10 control tag over; its "
                "live book is left running so it drains with every settlement "
                "still recording. The arm parameters, entry pricing, exits, fee "
                "model and every keep/stop threshold are carried over unchanged, "
                "and the keep gate is the predecessor's own object."
            ),
            register=successor_mmsell10_capacity.register,
            arm=successor_mmsell10_capacity.arm,
            activation_vars=frozenset(
                successor_mmsell10_capacity.RISK_ENVELOPE["settings"]
            ) | {"LIVE_STRATEGIES"},
        ),
        "mmsell10-capacity-gatefix": ExperimentPackage(
            name="mmsell10-capacity-gatefix",
            experiment_key=successor_mmsell10_capacity.SUCCESSOR_KEY,
            description=(
                "Opens v2 of the capacity successor because v1's promotion gate "
                "can never PASS: its sample floor named `paper_settled_contracts`, "
                "which is not in the canonical metric registry, so the evaluator "
                "returns BLOCKED_INTEGRITY and arm_live_canary — which re-runs "
                "the gate synchronously — refuses forever. A gate is immutable "
                "with its version, so the only remedy the lifecycle allows is a "
                "new Version, and PAPER is where a contract may still be revised. "
                "Nothing about the science changes: same hypothesis, arm, risk "
                "envelope, keep gate and promotion bar; only the floor clause's "
                "metric name is corrected. It refuses unless it finds exactly "
                "that defect, so it cannot re-run or touch a healthy contract."
            ),
            register=successor_mmsell10_capacity.revise_promotion_gate,
        ),
        "mmsell10-capacity-unfloor": ExperimentPackage(
            name="mmsell10-capacity-unfloor",
            experiment_key=successor_mmsell10_capacity.SUCCESSOR_KEY,
            description=(
                "Opens the next Version with the OPERATOR SAMPLE FLOOR removed, "
                "restoring the predecessor's own unfloored promotion bar. A "
                "revert, not a relaxation: mmsell-price-ceiling v2 — the version "
                "Cmmsell10 armed under — carried no sample clause, and the floor "
                "was added to this successor by mistake. The successor's "
                "independent variable is the LIVE cap; the paper book has no cap "
                "and assumes fill, so paper cannot move across the change and a "
                "floor on it buys sample in a measurement that cannot inform. It "
                "produces exactly one spec, the inherited object, verified equal "
                "before writing; it refuses outside PAPER, refuses when there is "
                "no floor to drop, and REFUSES once the deciding metric has been "
                "observed — removing a floor before seeing the number reverts a "
                "design mistake, removing it after fits a threshold to a result."
            ),
            register=successor_mmsell10_capacity.drop_operator_sample_floor,
        ),
        "perp-v1": ExperimentPackage(
            name="perp-v1",
            experiment_key=perp_v1.EXPERIMENT_KEY,
            description=(
                "PERP-V1: Kalshi crypto perpetual futures — one experiment, three "
                "treatment arms (premium reversion, funding dispersion, perp->"
                "prediction lead/lag) and a matched random-direction control, "
                "frozen at PROBE with a per-arm pre-registered bar. Registers no "
                "strategy tag and no deployment, so nothing becomes admissible to "
                "the trading write path; it has no `arm` function, so ARM_CANARY "
                "aimed at it has nothing to call. CLOSED 2026-09-02 — its "
                "`close_out` records the experiment as it actually ended (arm A "
                "FAIL on execution economics, arm B BLOCKED_DATA, arm C HOLD on an "
                "operator NO-GO with the mechanism untested) and retires it, having "
                "never been registered while it ran."
            ),
            register=perp_v1.register,
            close_out=perp_v1.close_out_retrospective,
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


def _close_out_retrospective(session, env: _Envelope, now: datetime):
    """Record an experiment that ran and finished OUTSIDE Experiment OS, and retire
    it in the same act. Creates no deployment, no tag and no real-money capability.

    This exists because the system could not previously say the one thing that was
    true about PERP-V1: it happened, and it is over. It ran a full probe lifecycle
    unregistered — correct at the time, since registering redeploys the worker and a
    probe that cannot trade had no reason to force that — and the documents ended up
    as the only durable record. That is the fragmentation Experiment OS exists to
    prevent, and it is general: any experiment that runs outside and finishes hits it.

    Why it is not REGISTER_PACKAGE. That action registers a contract and stops. Used
    alone here it would leave a closed, failed experiment sitting in production as an
    ACTIVE PROBE with open, never-evaluated gates — the Control Tower would show a
    dead experiment as live research, which is worse than the documents-only state it
    was meant to fix. The close-out is therefore ATOMIC: either the whole retired
    record exists or nothing does. There is no intermediate state to get stuck in.

    Why it is not a legacy import. `import_legacy_experiment` is for PRE-cutover
    history and would mark post-cutover work grandfathered — precisely what the
    Legacy Migration role must never do, by its own rules.

    `approved_by` is required and recorded on the transition: writing down someone
    else's conclusion, by hand, after the fact, is an operator act and the audit row
    should name a person rather than a process.
    """
    del now
    package = _package_or_refuse(env.payload.get("package"))
    if package.close_out is None:
        raise ExperimentCommandRejected(
            f"package {package.name!r} declares no retrospective close-out",
            "NO_CLOSE_OUT",
        )
    approved_by = env.payload.get("approved_by")
    if not isinstance(approved_by, str) or not _ACTOR_RE.match(approved_by or ""):
        raise ExperimentCommandRejected(
            "approved_by must name the person attesting this retrospective record",
            "BAD_APPROVED_BY",
        )
    reason = env.payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ExperimentCommandRejected(
            "reason is required: it becomes the retirement audit row",
            "BAD_REASON",
        )

    produced = package.close_out(
        session, actor=env.actor, approved_by=approved_by, reason=reason
    )

    # The transport verifies the OUTCOME rather than trusting the package to have
    # gone through the guarded service helper. A package is reviewed code, but a
    # reviewed function can still be edited later, and the two properties that make
    # this verb safe to exist are cheap to re-check here: nothing it wrote may
    # authorize anything, and it must have actually closed the experiment.
    experiment = get_experiment(session, package.experiment_key)
    if experiment is None or experiment.state != LifecycleState.RETIRED.value:
        raise ExperimentCommandRejected(
            f"close-out left {package.experiment_key!r} in state "
            f"{getattr(experiment, 'state', None)!r} rather than RETIRED",
            "CLOSE_OUT_NOT_RETIRED",
        )
    passes = session.scalars(
        select(ExperimentGateResult).where(
            ExperimentGateResult.experiment_id == experiment.id,
            ExperimentGateResult.verdict == GateVerdict.PASS.value,
        )
    ).all()
    if passes:
        raise ExperimentCommandRejected(
            f"close-out recorded {len(passes)} PASS verdict(s) — a retrospective "
            "record is history and may never authorize a promotion",
            "CLOSE_OUT_RECORDED_PASS",
        )
    return {"kind": "close_out", "package": package.name, "produced": produced}


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
    "CLOSE_OUT_RETROSPECTIVE": _Action(
        required=frozenset({"package", "approved_by", "reason"}),
        optional=frozenset(),
        run=_close_out_retrospective,
        doc="Record an experiment that ran and finished OUTSIDE this system, and "
            "retire it, atomically. Records only non-PASS verdicts, creates no "
            "deployment or tag, and authorizes nothing.",
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
