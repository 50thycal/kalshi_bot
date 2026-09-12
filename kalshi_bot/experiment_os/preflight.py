"""`package-preflight` — the mechanical "does this meet the criteria" read that
lets a paper-tape request run end to end without a conversation.

WHY THIS EXISTS
---------------
Registering a paper book used to be a chain of operator confirmations: *is the
package there? is the experiment already registered? is the tag free? is the
platform snapshot complete? is the enforcement mode what we think?* Each answer
was a separate reply. `docs/STANDING_AUTHORIZATIONS.md` makes a paper-scope
request self-authorizing **when it meets the criteria** — so the criteria have
to be something a program can answer, or "meets the criteria" is still a
judgment call made in chat.

This module answers them. It is a READ: it opens no epoch, writes no row, and is
allowlisted on the ops channel against `DATABASE_URL_RO`. Its verdict authorizes
nothing on its own — `REGISTER_PACKAGE` still runs through the worker with a
named approver — it only says whether the envelope is worth sending.

It also prints the envelope. The Gmmsell2 handoff spent a page on the shape of
one JSON string and the constraints that bite (`command_id` charset, a stripped
`approved_by`, the single-slot transport). Printing it from the package's own
declaration removes the retyping.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import read
from .enforcement import current_mode
from .experiment_commands import _packages, receipts
from .lifecycle import EnforcementMode
from .service import get_active_platform_snapshot

#: `command_id` charset per the transport (8–64 of [A-Za-z0-9._-]).
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def _check(key: str, ok: bool | None, detail: str, **extra) -> dict:
    """One row of the report. `ok=None` is informational — it never fails."""
    return {"check": key, "ok": ok, "detail": detail, **extra}


def suggested_command_id(package: str, now: datetime | None = None) -> str:
    """A fresh, charset-safe id: `<package>-register-<UTCstamp>`. Reuse is a
    claim on someone else's receipt, so every printed id carries the minute."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M")
    base = re.sub(r"[^A-Za-z0-9._-]", "-", package)[:40]
    cid = f"{base}-register-{stamp}"
    assert _ID_RE.match(cid), cid
    return cid


def list_packages() -> list[dict]:
    """Every reviewed package the transport can name, with its verbs."""
    out = []
    for name, pkg in sorted(_packages().items()):
        out.append({
            "package": name,
            "experiment_key": pkg.experiment_key,
            "verbs": [v for v, fn in (("register", pkg.register), ("arm", pkg.arm),
                                       ("repair", pkg.repair), ("close_out", pkg.close_out))
                      if fn is not None],
            "strategy_tags": list(pkg.strategy_tags),
            "activation_vars": sorted(pkg.activation_vars),
        })
    return out


def package_preflight(session, package: str, *, now: datetime | None = None) -> dict:
    """Run every mechanical criterion for `REGISTER_PACKAGE` on `package`.

    Returns a report: `verdict` is `GO` when every non-informational check is ok,
    else `NO-GO`. A GO report carries the exact envelope to send (with
    `approved_by` left for the operator's name) and the steps that follow it."""
    now = now or datetime.now(timezone.utc)
    checks: list[dict] = []
    pkgs = _packages()

    if package not in pkgs:
        checks.append(_check(
            "package_known", False,
            f"{package!r} is not a reviewed package; known: {sorted(pkgs)}"))
        return _finish(package, checks, None, now)

    pkg = pkgs[package]
    checks.append(_check("package_known", True, pkg.description[:160]))

    if pkg.register is None:
        checks.append(_check(
            "package_registers", False,
            "this package has no `register` verb — it is a repair or close-out "
            "package, not a contract that lets a book start"))
    else:
        checks.append(_check("package_registers", True, "has a `register` verb"))

    # --- platform -----------------------------------------------------------
    mode = current_mode(session)
    checks.append(_check(
        "enforcement_mode", None, f"{mode.value}",
        note=("a tag trades only under an active deployment arm" if mode in
              (EnforcementMode.NEW_ONLY, EnforcementMode.STRICT) else
              "NOT NEW_ONLY — an unregistered tag could trade; register anyway, "
              "and tell Live Ops")))

    snap = get_active_platform_snapshot(session)
    checks.append(_check(
        "platform_snapshot_complete", snap is not None,
        f"active snapshot {snap.fingerprint[:12]}" if snap is not None else
        "no complete active platform snapshot — epoch creation would refuse; "
        "Platform Change Review owns this"))

    # --- experiment ---------------------------------------------------------
    exp = read.get_experiment(session, pkg.experiment_key)
    if exp is None:
        checks.append(_check(
            "experiment", True,
            f"{pkg.experiment_key!r} not registered — a fresh registration"))
    else:
        ver = read.latest_version(session, exp)
        epoch = read.open_epoch_for(session, ver) if ver is not None else None
        detail = (f"{exp.key} state={exp.state} latest v{ver.version if ver else '-'} "
                  f"frozen={'yes' if ver is not None and ver.frozen_at else 'no'} "
                  f"open_epoch={epoch.epoch_number if epoch else '-'}")
        if exp.state == "RETIRED":
            checks.append(_check(
                "experiment", False, detail + " — RETIRED is terminal; a new "
                "question is a NEW experiment, never a re-registration"))
        else:
            checks.append(_check(
                "experiment", None, detail + " — already registered: the package "
                "must register a SUCCESSOR version (a second REGISTER_PACKAGE at "
                "the same version raises, it is not a quiet no-op)"))

    # --- tags ---------------------------------------------------------------
    if not pkg.strategy_tags:
        checks.append(_check(
            "tag_collisions", None,
            "package declares no strategy_tags; collision check skipped — "
            "the resolver still refuses two active arms on one tag at run time"))
    else:
        collisions = []
        for tag in pkg.strategy_tags:
            for row in read.strategy_tag_lineage(session, tag):
                if row["experiment_key"] != pkg.experiment_key:
                    collisions.append(
                        f"{tag} already carried by {row['experiment_key']}/"
                        f"v{row['version']}/{row['deployment_key']}")
        checks.append(_check(
            "tag_collisions", not collisions,
            "no declared tag is carried by another experiment"
            if not collisions else "; ".join(collisions),
            tags=list(pkg.strategy_tags)))

    # --- transport ----------------------------------------------------------
    running = [r for r in receipts(session, limit=20) if r["status"] == "RUNNING"]
    checks.append(_check(
        "transport_idle", not running,
        "no experiment command is mid-execution" if not running else
        f"receipt(s) still RUNNING: {[r['command_id'] for r in running]} — "
        "wait for a terminal receipt; retrying means a new command_id"))

    return _finish(package, checks, pkg, now)


def _finish(package: str, checks: list[dict], pkg, now: datetime) -> dict:
    failed = [c["check"] for c in checks if c["ok"] is False]
    report = {
        "package": package,
        "as_of": now.isoformat(),
        "checks": checks,
        "failed": failed,
        "verdict": "GO" if not failed else "NO-GO",
    }
    if pkg is not None and not failed:
        cid = suggested_command_id(package, now)
        envelope = {
            "command_id": cid,
            "action": "REGISTER_PACKAGE",
            "actor": "claude-code",
            "actor_role": "RESEARCH_LAB",
            "payload": {"package": package, "approved_by": "<operator>",
                        "reason": "<one line: the request that authorized this>"},
            "schema_version": 1,
        }
        report["envelope"] = envelope
        report["env_request"] = {
            "type": "env",
            "set": {"EXPERIMENT_OS_EXPERIMENT_COMMAND": json.dumps(envelope)},
            "id": f"{cid}-send",
        }
        steps = [
            "send env_request (approved_by = the operator's name, verbatim)",
            f"read the receipt: xos experiment-command-show {cid}",
            f"xos show {pkg.experiment_key} — every declared tag on an ACTIVE arm",
        ]
        if pkg.activation_vars:
            steps.append(
                "runtime step (separate `env` request, read the live value FIRST "
                f"and append, never retype): {sorted(pkg.activation_vars)}")
        steps.append("xos control-tower after SILENT_ARM_HOURS — no silent arm")
        report["next_steps"] = steps
    return report


def render(report: dict) -> str:
    lines = [f"PACKAGE PREFLIGHT — {report['package']}  ({report['as_of']})", ""]
    for c in report["checks"]:
        mark = {True: "ok  ", False: "FAIL", None: "info"}[c["ok"]]
        lines.append(f"  [{mark}] {c['check']}: {c['detail']}")
        if c.get("note"):
            lines.append(f"         {c['note']}")
    lines.append("")
    lines.append(f"VERDICT: {report['verdict']}"
                 + (f"  (failed: {', '.join(report['failed'])})" if report["failed"] else ""))
    if "env_request" in report:
        lines += ["", "envelope (ops request, one line):",
                  json.dumps(report["env_request"]), "", "then:"]
        lines += [f"  {i + 1}. {s}" for i, s in enumerate(report["next_steps"])]
    return "\n".join(lines)
