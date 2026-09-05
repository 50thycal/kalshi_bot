"""Refuse to overwrite an Experiment OS command envelope that has not run yet.

## The hazard

The three Experiment OS write transports — `EXPERIMENT_OS_ISSUE_COMMAND`,
`EXPERIMENT_OS_PLATFORM_COMMAND`, `EXPERIMENT_OS_EXPERIMENT_COMMAND` — are each a
SINGLE Railway environment variable, consumed once at the worker's next boot.
That is deliberate: the worker is the only writer, and the ops channel is
read-only against Postgres by design, so a variable the worker reads at boot is
the whole authorized path.

It has one failure mode, and it only appears with more than one session driving
the channel. Session A sets the variable and waits for the boot that will consume
it. Session B sets the same variable first. A's envelope is gone — never claimed,
never executed, no receipt — and because the ledger's exactly-once guarantee is
per `command_id`, nothing is corrupted and nothing complains. A finds out by
noticing a receipt that never appears, which is to say: usually not for a while,
and never at the moment it mattered.

This is not the ledger's job to solve. The ledger answers "did this command_id
run", correctly, and it is asked *after* the fact. The question here is asked
before: "is the slot I am about to overwrite still holding someone's unconsumed
work?"

## The check

Parse the CURRENT value of the variable, take the `command_id` of every envelope
in it (one object, or an array of them since batching), and ask that transport's
own receipt ledger whether each has reached a terminal state. A command is
UNCONSUMED when it has no ledger row at all, or when its row is still `RUNNING`.

`SUCCEEDED`, `REJECTED` and `FAILED` are all terminal for a `command_id`, so a
value holding only those is spent and may be freely replaced — which is the
common case, since a session that finishes its work leaves its last command
behind in the variable.

Note what a missing row does NOT mean: the platform transport's `DEFERRED`
outcome deliberately writes no row, precisely so the same envelope runs again on
a later boot. Treating "no row" as unconsumed is therefore correct for it too —
a deferred cutover is exactly the thing that must not be silently discarded.

## Fail-open, on purpose

Every uncertainty here resolves to ALLOW: no database URL, an unreachable
database, a missing table, an unparseable current value, a value whose envelopes
carry no usable id. This guard exists to catch an honest collision between two
cooperating sessions, and it sits in front of the only authorized production
write path. A guard that fails closed would turn a transient database blip into
"nobody can record anything", which is a worse outage than the race it prevents.
What it must never do is stay silent: an inconclusive check says so in the
result, so the operator knows the check did not actually run.

Overriding is explicit and recorded — `"force_replace": true` on the ops request.
There are real reasons to need it (an abandoned session's envelope will never be
consumed because nobody is going to redeploy for it), and none of them are served
by making the operator guess which flag to pass.
"""

from __future__ import annotations

import json
import os

#: transport variable -> the ledger table its receipts land in. All three share
#: the same two columns (`command_id`, `status`) and the same terminal set, so the
#: check is table-driven rather than three near-copies.
TRANSPORT_LEDGERS: dict[str, str] = {
    "EXPERIMENT_OS_ISSUE_COMMAND": "experiment_os_issue_commands",
    "EXPERIMENT_OS_PLATFORM_COMMAND": "experiment_os_platform_commands",
    "EXPERIMENT_OS_EXPERIMENT_COMMAND": "experiment_os_experiment_commands",
}

#: A receipt in any of these states is final for its `command_id` — the envelope
#: that produced it can never run again, so the slot is free.
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "REJECTED", "FAILED"})

#: Bound the parse: a value larger than any legitimate batch is not worth
#: inspecting, and the guard fails open rather than chewing on it.
MAX_INSPECT_BYTES = 262144


class GuardResult:
    """What the check concluded, in a shape the receipt can carry verbatim.

    `blocked` is the only field that changes behaviour. `conclusive` is what stops
    a fail-open from looking like a pass: an operator reading a receipt must be
    able to tell "checked, and the slot was free" from "could not check".
    """

    def __init__(self, *, blocked: bool, conclusive: bool, reason: str,
                 unconsumed: list[str] | None = None):
        self.blocked = blocked
        self.conclusive = conclusive
        self.reason = reason
        self.unconsumed = unconsumed or []

    def as_receipt(self) -> dict:
        return {
            "blocked": self.blocked,
            "conclusive": self.conclusive,
            "reason": self.reason,
            # Command IDs only. They are author-chosen labels that already appear
            # in public receipts; the envelope itself is never echoed here.
            "unconsumed_command_ids": self.unconsumed,
        }


def command_ids(raw: str) -> list[str] | None:
    """Every `command_id` in a transport value, in order.

    Returns None when the value cannot be understood as envelopes at all — which
    the caller treats as "cannot check", never as "nothing there". An empty list
    means the value was understood and holds no ids (an empty array).
    """
    text = (raw or "").strip()
    if not text:
        return []
    if len(text.encode("utf-8")) > MAX_INSPECT_BYTES:
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    envelopes = parsed if isinstance(parsed, list) else [parsed]
    out: list[str] = []
    for env in envelopes:
        if not isinstance(env, dict):
            return None
        cid = env.get("command_id")
        if not isinstance(cid, str) or not cid.strip():
            return None
        out.append(cid.strip())
    return out


def _terminal_ids(table: str, ids: list[str]) -> set[str] | None:
    """Which of `ids` already hold a terminal receipt. None = could not ask."""
    dsn = os.environ.get("DATABASE_URL_RO", "").strip()
    if not dsn or not ids:
        return None if not dsn else set()
    try:
        import psycopg
    except ImportError:  # pragma: no cover - psycopg is installed by the workflow
        return None
    # `table` is never caller-supplied: it comes from TRANSPORT_LEDGERS, keyed by
    # a variable name that railway_env has already checked against its allowlist.
    # The ids ARE caller-supplied and are bound as parameters.
    sql = (
        f"select command_id from {table} "  # noqa: S608 - constant, see above
        "where command_id = any(%s) and status = any(%s)"
    )
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(sql, (list(ids), sorted(TERMINAL_STATUSES)))
                return {row[0] for row in cur.fetchall()}
    except Exception:  # noqa: BLE001 — every database problem fails OPEN; see module docstring
        return None


def check(var: str, current_value: str | None) -> GuardResult:
    """Is it safe to overwrite `var`, whose current value is `current_value`?"""
    table = TRANSPORT_LEDGERS.get(var)
    if table is None:
        return GuardResult(blocked=False, conclusive=True,
                           reason=f"{var} is not a command transport")
    if current_value is None:
        return GuardResult(
            blocked=False, conclusive=False,
            reason=f"{var}: the current value could not be read, so whether it "
                   "holds unconsumed work is unknown",
        )
    ids = command_ids(current_value)
    if ids is None:
        return GuardResult(
            blocked=False, conclusive=False,
            reason=f"{var}: the current value is not a readable envelope, so it "
                   "cannot be checked for unconsumed commands",
        )
    if not ids:
        return GuardResult(blocked=False, conclusive=True,
                           reason=f"{var} is empty — nothing to overwrite")
    terminal = _terminal_ids(table, ids)
    if terminal is None:
        return GuardResult(
            blocked=False, conclusive=False,
            reason=f"{var}: the receipt ledger could not be read, so whether the "
                   f"{len(ids)} command(s) already there have run is unknown",
        )
    unconsumed = [cid for cid in ids if cid not in terminal]
    if not unconsumed:
        return GuardResult(
            blocked=False, conclusive=True,
            reason=f"{var}: all {len(ids)} command(s) in the current value have "
                   "terminal receipts — the slot is spent and safe to replace",
        )
    return GuardResult(
        blocked=True, conclusive=True, unconsumed=unconsumed,
        reason=(
            f"{var} still holds {len(unconsumed)} command(s) with no terminal "
            f"receipt: {', '.join(unconsumed)}. Another session is very likely "
            "waiting on the boot that would consume them, and overwriting the "
            "variable would discard that work silently. Wait for the receipt "
            "(xos issue-command-show <command_id>), or pass "
            '"force_replace": true if you know it will never be consumed'
        ),
    )
