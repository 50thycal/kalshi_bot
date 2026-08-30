"""What the ops channel can do, said once, by the code that does it.

Everything an operator or a session needs to know about this channel — which
request types exist, which of them can CHANGE production, which Railway services
are targetable, which Experiment OS commands and analysis scripts are
allowlisted, which environment variables are readable or settable — already
exists in code: in `ops_runner`'s allowlists and in `railway_env`'s. It has also
existed, separately, in prose. XOS-000005 is what that costs: two commands the
runbook advertised and the runner refused, indistinguishable from the outside
from commands that never existed.

So this module derives the answer FROM the allowlists rather than restating
them, `{"type":"capabilities"}` prints it, and the docs/runner parity tests read
it. A capability cannot be documented into existence, and a request type cannot
be added without appearing here.

It also owns the two questions that must be answered the SAME way in three
places — the runner, the workflow and the tests:

  * is this request a READ or is it MUTATING (`classify`), and
  * does it need the full dependency set (`needs_full_deps`).

Stdlib only, and it must stay that way: the workflow calls
`needs_full_deps` to decide whether to install `requirements.txt` at all.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

READ = "READ"
MUTATING = "MUTATING"


class OpsRequestError(ValueError):
    """A request is malformed in a way that must fail closed, not be guessed at."""


@dataclass(frozen=True)
class RequestType:
    """One `{"type": ...}` family the runner dispatches."""

    name: str
    summary: str
    #: True when this family has ANY path that changes production. `env` is the
    #: only one: it reads by default and mutates when asked. The per-request
    #: answer is `classify()`; this is the family's ceiling.
    can_mutate: bool = False
    #: Needs the kalshi_bot package + requirements.txt (the canonical Experiment
    #: OS CLI), rather than the fast psycopg-only path.
    full_deps: bool = False
    example: str = ""


#: The dispatch table's public face. `tests/test_ops_channel_vnext.py` asserts
#: this covers exactly the types `ops_runner.main()` actually dispatches, so a
#: new type cannot ship invisible to `capabilities` and the docs parity test.
REQUEST_TYPES: tuple[RequestType, ...] = (
    RequestType(
        "noop", "Do nothing. The resting state of the channel between requests.",
        example='{"type":"noop","id":"idle"}',
    ),
    RequestType(
        "logs", "Bounded Railway deployment logs for one service.",
        example='{"type":"logs","service":"main","limit":200,"id":"logs-1"}',
    ),
    RequestType(
        "db", "One read-only SQL statement against DATABASE_URL_RO.",
        example='{"type":"db","sql":"select count(*) from paper_trades","id":"q-1"}',
    ),
    RequestType(
        "script", "One allowlisted self-contained read-only analysis script.",
        example='{"type":"script","name":"live_paper_parity","args":[],"id":"s-1"}',
    ),
    RequestType(
        "xos", "The canonical Experiment OS read CLI (read-only subcommands).",
        full_deps=True,
        example='{"type":"xos","command":"control-tower","id":"ct-1"}',
    ),
    RequestType(
        "capabilities", "This surface: what the channel can do, generated from the allowlists.",
        example='{"type":"capabilities","id":"cap-1"}',
    ),
    RequestType(
        "doctor", "One-request operating snapshot: runner, DB, Railway, runtime config, Experiment OS.",
        full_deps=True,
        example='{"type":"doctor","id":"doc-1"}',
    ),
    RequestType(
        "incident", "A bounded investigation bundle for one service over a recent window.",
        full_deps=True,
        example='{"type":"incident","service":"main","window_minutes":30,"id":"inc-1"}',
    ),
    RequestType(
        "env",
        "Read allowlisted Railway variables; with an explicit set action, CHANGE them "
        "and redeploy the service.",
        can_mutate=True,
        example='{"type":"env","service":"main","id":"env-1"}',
    ),
)

REQUEST_TYPES_BY_NAME: dict[str, RequestType] = {r.name: r for r in REQUEST_TYPES}

#: Optional, public-safe provenance a producer may attach to any request. None of
#: it is required (every pre-existing request shape stays valid), and none of it
#: is ever interpreted as authority: an actor field is a label for the receipt,
#: not a credential.
PROVENANCE_FIELDS: tuple[str, ...] = ("actor", "purpose", "workstream", "issue")

#: How much of a provenance value is carried into the receipt. These are echoed
#: into a public branch, so they are bounded like everything else here.
MAX_PROVENANCE_CHARS = 200

#: Per-run result files kept on the transport branch (the publish step's prune).
RESULT_RETENTION = 80


def request_type(req: dict) -> str:
    return (req.get("type") or "").strip().lower()


def env_mutation(req: dict) -> dict | None:
    """The variables an `env` request would CHANGE, or None if it is a read.

    Two spellings, one meaning. The legacy shape put a mutation in `set` and
    a read in its absence — a production-changing request and a read differing
    by one key. The explicit shape names the act:

        {"type":"env","action":"get"}
        {"type":"env","action":"set","values":{"KILL_SWITCH":"true"}}

    Both are accepted; the explicit one is what new callers should write. What is
    NOT accepted is an ambiguous request — `action:"get"` carrying values, or
    `action:"set"` carrying none — because the only safe reading of "I cannot
    tell whether you meant to change production" is to refuse.
    """
    action = (req.get("action") or "").strip().lower()
    if action and action not in ("get", "read", "set"):
        raise OpsRequestError(
            f"env action {action!r} is not one of 'get' / 'read' / 'set'"
        )
    raw = req.get("values") if req.get("values") is not None else req.get("set")
    if raw is not None and not isinstance(raw, dict):
        raise OpsRequestError("env values must be an object of NAME -> value")
    if action == "set":
        if not raw:
            raise OpsRequestError(
                "env action 'set' carries no variables — say what to change, or "
                "use action 'get'"
            )
        return dict(raw)
    if raw:
        if action in ("get", "read"):
            raise OpsRequestError(
                "env action 'get' carries variables to set — refusing an "
                "ambiguous request; use action 'set' to change production"
            )
        return dict(raw)          # legacy {"set": {...}} with no action
    return None


def classify(req: dict) -> str:
    """READ or MUTATING for THIS request — the label the result header carries."""
    if request_type(req) != "env":
        return READ
    return MUTATING if env_mutation(req) else READ


def needs_full_deps(req: dict) -> bool:
    """Does serving this request need requirements.txt, not just psycopg?

    The workflow asks this (`python scripts/ops_meta.py needs-full-deps <file>`)
    instead of matching on `type == "xos"` in shell. That test was correct when
    `xos` was the only canonical-CLI caller and became a latent bug the moment
    `doctor` started reading Experiment OS through the same code: the request
    would have failed on an import, in the workflow, where the runner's own
    error handling cannot explain it.

    A MUTATING env request also qualifies: its verification step reads Experiment
    OS readiness back when it touched experiment or live-strategy state.
    """
    spec = REQUEST_TYPES_BY_NAME.get(request_type(req))
    if spec is None:
        return False          # unknown type: the runner refuses it, cheaply
    if spec.full_deps:
        return True
    if spec.name == "env":
        try:
            mutation = env_mutation(req)
        except OpsRequestError:
            return False      # malformed: the runner refuses it, cheaply
        return bool(mutation) and bool(verification_hooks(mutation))
    return False


# ---------------------------------------------------------------------------
# Mutations that are worth remembering
# ---------------------------------------------------------------------------

#: Variables whose change is worth a DURABLE receipt, not just the transport's
#: bounded scratch history. The test is "would we want to reconstruct, months
#: later, who changed this and what happened next" — real-money capability, the
#: risk envelope around it, and the three Experiment OS write transports.
#: Everything else (a scan cadence, a log level, a research collector) is
#: ordinary operation and stays in ops/results.
AUDIT_WORTHY_VARS: frozenset[str] = frozenset({
    "KILL_SWITCH", "LIVE_ENABLED", "LIVE_STRATEGIES", "LIVE_PROBE",
    "LIVE_FRACTIONAL", "LIVE_MAX_ORDER_DOLLARS", "LIVE_KILL_ON_DAILY_LOSS",
    "MAX_ORDER_SIZE", "MAX_MARKET_EXPOSURE", "MAX_TOTAL_EXPOSURE", "MAX_DAILY_LOSS",
    "MMSELL_VARIANTS", "BOT_MODE",
    "EXPERIMENT_OS_EXPERIMENT_COMMAND", "EXPERIMENT_OS_PLATFORM_COMMAND",
    "EXPERIMENT_OS_ISSUE_COMMAND", "EXPERIMENT_OS_ENFORCEMENT_MODE",
    "EXPERIMENT_OS_IMPORT_ON_BOOT", "EXPERIMENT_OS_RECONCILE_FINDINGS_ON_BOOT",
    "EXPERIMENT_OS_CUTOVER_ID",
})

#: Variables whose change should be followed by a canonical Experiment OS
#: readback before the receipt claims anything. Deliberately expressed as the
#: NAMES of canonical readers, never as strategy-specific logic in the runner:
#: this module decides *that* a check is owed, `ops_doctor` runs the canonical
#: one, and neither invents a verdict of its own.
_XOS_SENSITIVE_PREFIXES = ("EXPERIMENT_OS_", "LIVE_", "MMSELL_VARIANTS", "KILL_SWITCH")


def is_audit_worthy(names) -> bool:
    return any(n in AUDIT_WORTHY_VARS for n in names)


def verification_hooks(mapping: dict) -> tuple[str, ...]:
    """Canonical checks owed after mutating these variables, in order."""
    touched = any(
        n.startswith(_XOS_SENSITIVE_PREFIXES) or n in AUDIT_WORTHY_VARS
        for n in mapping
    )
    return ("enforcement", "readiness") if touched else ()


# ---------------------------------------------------------------------------
# The generated capability surface
# ---------------------------------------------------------------------------


def capability_snapshot() -> dict:
    """Everything the channel can currently do, read out of the allowlists.

    Never includes a secret VALUE. A Railway service ID is a secret in this
    repository, so a service reports only whether its ID is configured — which is
    the operationally useful half and the half that is safe to publish.
    """
    import ops_runner
    import railway_env

    services = {}
    for name, secret in sorted(ops_runner._SERVICE_ID_SECRET.items()):
        services[name] = {
            "id_secret": secret,                      # the NAME of the secret
            "configured": bool(os.environ.get(secret, "").strip()),
        }
    env_vars = sorted(railway_env.ALLOWED_VARS)
    return {
        "runner": {
            "code_sha": os.environ.get("OPS_CODE_SHA", "") or "(unknown)",
            "code_source": os.environ.get(ops_runner.CODE_SOURCE_ENV, "") or "(local)",
            "run_url": os.environ.get("OPS_RUN_URL", "") or "(none)",
        },
        "services": services,
        "request_types": [
            {
                "type": r.name,
                "class": MUTATING if r.can_mutate else READ,
                "summary": r.summary,
                "full_deps": r.full_deps,
                "example": r.example,
            }
            for r in REQUEST_TYPES
        ],
        "xos_commands": sorted(ops_runner.xos_allowlist()),
        "scripts": sorted(ops_runner.ALLOWED_SCRIPTS),
        "env": {
            "readable_settable": env_vars,
            "count": len(env_vars),
            "redacted": sorted(railway_env.REDACTED_VARS),
            "audit_worthy": sorted(AUDIT_WORTHY_VARS & set(env_vars)),
        },
        "provenance_fields": list(PROVENANCE_FIELDS),
        "limits": {
            "db_max_rows_default": 200,
            "db_statement_timeout_ms": 30_000,
            "env_max_value_bytes": railway_env.MAX_VALUE_BYTES,
            "result_retention_files": RESULT_RETENTION,
            "provenance_max_chars": MAX_PROVENANCE_CHARS,
        },
    }


def render_capabilities(snap: dict) -> str:
    out: list[str] = []
    add = out.append
    r = snap["runner"]
    add("OPS CHANNEL CAPABILITIES")
    add("=" * 72)
    add(f"code sha      : {r['code_sha']}")
    add(f"code source   : {r['code_source']}")
    add(f"run           : {r['run_url']}")
    add("")
    add("REQUEST TYPES")
    add("-" * 72)
    for t in snap["request_types"]:
        flag = "MUTATING" if t["class"] == MUTATING else "read"
        deps = "  [full deps]" if t["full_deps"] else ""
        add(f"  {t['type']:<13} {flag:<9}{deps}")
        add(f"      {t['summary']}")
        if t["example"]:
            add(f"      e.g. {t['example']}")
    add("")
    add("  Only `env` can change production, and only when it carries an explicit")
    add("  set action. Everything else on this list is a read.")
    add("")
    add("RAILWAY SERVICES (env / logs / incident targets)")
    add("-" * 72)
    for name, svc in snap["services"].items():
        state = "configured" if svc["configured"] else "NOT CONFIGURED"
        add(f"  {name:<10} {state:<15} (id secret: {svc['id_secret']})")
    add("")
    add("EXPERIMENT OS READ COMMANDS (canonical CLI)")
    add("-" * 72)
    add("  " + ", ".join(snap["xos_commands"]))
    add("")
    add(f"ANALYSIS SCRIPTS ({len(snap['scripts'])} allowlisted)")
    add("-" * 72)
    for i in range(0, len(snap["scripts"]), 4):
        add("  " + ", ".join(snap["scripts"][i:i + 4]))
    add("")
    env = snap["env"]
    add(f"ENVIRONMENT VARIABLES ({env['count']} readable and settable)")
    add("-" * 72)
    for i in range(0, len(env["readable_settable"]), 3):
        add("  " + ", ".join(env["readable_settable"][i:i + 3]))
    add("")
    add("  redacted in output (value never printed; the request payload is still public):")
    add("    " + ", ".join(env["redacted"]))
    add("  durably archived when changed:")
    for i in range(0, len(env["audit_worthy"]), 3):
        add("    " + ", ".join(env["audit_worthy"][i:i + 3]))
    add("")
    add("  Secrets are not on this list and cannot be read or set from here:")
    add("  KALSHI_*, DATABASE_URL, RAILWAY_*, and every other credential.")
    add("")
    add("PROVENANCE (optional on any request)")
    add("-" * 72)
    add("  " + ", ".join(snap["provenance_fields"]) + "  — labels for the receipt, never authority")
    add("")
    add("LIMITS")
    add("-" * 72)
    for k, v in snap["limits"].items():
        add(f"  {k:<26} {v}")
    return "\n".join(line.rstrip() for line in out)


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


def provenance(req: dict) -> dict:
    """The public-safe provenance a producer attached, bounded and stringified."""
    out = {}
    for field_name in PROVENANCE_FIELDS:
        value = req.get(field_name)
        if value in (None, ""):
            continue
        out[field_name] = str(value)[:MAX_PROVENANCE_CHARS]
    return out


def header(req: dict, receipt: dict) -> str:
    """The block printed ABOVE a request's output.

    A reader must not have to infer from the presence of a JSON key that the
    thing they are about to read changed production. So the classification is
    the first line of the result, before any output, in the loudest form the
    medium allows.
    """
    lines: list[str] = []
    kind = receipt["class"]
    if kind == MUTATING:
        lines.append("=" * 72)
        lines.append("!! MUTATING OPS REQUEST — this changes the running system !!")
        lines.append("=" * 72)
    else:
        lines.append(f"# ops request [{kind}]")
    bits = [f"type={receipt['type']}"]
    if receipt.get("service"):
        bits.append(f"service={receipt['service']}")
    if receipt.get("targets"):
        bits.append("vars=" + ",".join(receipt["targets"]))
    if receipt.get("id"):
        bits.append(f"id={receipt['id']}")
    lines.append("# " + "  ".join(bits))
    prov = receipt.get("provenance") or {}
    if prov:
        lines.append("# " + "  ".join(f"{k}={v}" for k, v in sorted(prov.items())))
    lines.append(f"# code {receipt.get('code_sha') or '(unknown)'}"
                 f" · started {receipt.get('started_at')}")
    return "\n".join(lines)


def build_receipt(req: dict, *, started_at: str) -> dict:
    """The receipt fields knowable BEFORE the request runs.

    The workflow completes it afterwards (exit status, publication outcome), so
    a receipt exists even for a request that crashes the runner.
    """
    rtype = request_type(req)
    service = None
    targets: list[str] = []
    try:
        kind = classify(req)
        if rtype == "env":
            targets = sorted(env_mutation(req) or {})
    except OpsRequestError:
        # A malformed request is not classifiable. Say so rather than guessing —
        # and never guess READ, which is the answer that would let an ambiguous
        # production change print a quiet header.
        kind = "UNCLASSIFIED"
    if rtype in ("env", "logs", "incident"):
        service = (req.get("service") or "main").strip().lower()
    return {
        "id": str(req.get("id") or "")[:64],
        "type": rtype,
        "class": kind,
        "service": service,
        "targets": targets,
        "provenance": provenance(req),
        "started_at": started_at,
        "code_sha": os.environ.get("OPS_CODE_SHA", ""),
        "code_source": os.environ.get("OPS_RUNNER_CODE_SOURCE", ""),
        "run_url": os.environ.get("OPS_RUN_URL", ""),
        "audit_worthy": bool(targets) and is_audit_worthy(targets),
    }


def finalize_receipt(receipt: dict, request: dict, *, status: int, rid: str) -> dict:
    """Complete a receipt with what only the workflow knows, and rule on archiving.

    The runner writes what it knows before and after dispatch; the workflow owns
    the request's real exit status and where the output landed. If the runner
    crashed before writing anything, a receipt is SYNTHESIZED from the request
    here rather than skipped — "this run produced no receipt of its own" is
    exactly the case an auditor most needs recorded.
    """
    out = dict(receipt or {})
    if not out:
        out = {
            "id": str(request.get("id") or "")[:64],
            "type": request_type(request),
            "class": "UNCLASSIFIED",
            "provenance": provenance(request),
            "runner_receipt": "MISSING — the runner wrote no receipt for this run",
        }
    out["exit_status"] = int(status)
    out["outcome"] = "SUCCEEDED" if int(status) == 0 else "FAILED"
    out["result_file"] = f"ops/results/{rid}.txt"
    out["receipt_file"] = f"ops/results/{rid}.receipt.json"
    # This receipt is only ever written INTO the published tree, so its presence
    # there is the publication record; a failure to publish fails the run loudly
    # in its own step rather than being described here.
    out["publication"] = "COMMITTED"
    # An ATTEMPTED change to real-money capability is archived whether or not it
    # landed: "someone tried to arm this and it was refused" is history too, and
    # a rule that archived only successes would quietly lose the interesting half.
    out["audit_worthy"] = bool(
        out.get("class") == MUTATING and is_audit_worthy(out.get("targets") or [])
    )
    return out


def _cli(argv: list[str]) -> int:
    """The workflow's questions about a request, answered by the same code the
    runner answers them with. Stdlib only, so it runs before any install."""
    if not argv:
        print("usage: ops_meta.py needs-full-deps <request.json> | finalize …",
              file=sys.stderr)
        return 2
    command = argv[0]
    if command == "needs-full-deps":
        try:
            with open(argv[1]) as fh:
                req = json.load(fh)
        except Exception:
            print("no")            # unreadable: the runner will refuse it, cheaply
            return 0
        print("yes" if needs_full_deps(req) else "no")
        return 0
    if command == "finalize":
        opts = _options(argv[1:])
        receipt = _load_json(opts.get("receipt")) or {}
        request = _load_json(opts.get("request")) or {}
        final = finalize_receipt(receipt, request,
                                 status=int(opts.get("status") or 1),
                                 rid=opts.get("rid") or "run")
        out_path = opts.get("out")
        if out_path:
            with open(out_path, "w") as fh:
                json.dump(final, fh, indent=2, sort_keys=True, default=str)
        # The workflow's archive step reads exactly this word.
        print("audit" if final["audit_worthy"] else "no")
        return 0
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


def _options(argv: list[str]) -> dict:
    opts, i = {}, 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--") and i + 1 < len(argv):
            opts[token[2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    return opts


def _load_json(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:                            # a missing receipt is a FACT, not a crash
        return None


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
