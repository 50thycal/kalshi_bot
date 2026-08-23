"""The evidence funnel, rendered as a bounded summary that is safe to publish.

## Why this is an allowlist and not a log-attribute dump

The ops channel's results are committed in plaintext to a PUBLIC branch, so
anything this module can emit is effectively published. The workers currently
emit **260 distinct structured field names** across `log_event` keyword
arguments and `extra_fields` dictionaries, and an audit of them (recorded on
XOS-000004) found the set includes raw payloads (`payload`, `sample_payload`,
`body`), account and order identifiers (`kalshi_order_id`, `order_id`, `coid`,
`command_id`, `event_id`, `market_id`), private market and account data
(`price`, `ask`, `no_bid`, `buy_price`, `cash_balance`, `exposure_usd`,
`realized_pnl`), and unbounded author-controlled text (`error`, `reason`,
`title`, `url`). Widening the log READ path to return attributes generically
would publish all of it the moment any book logged it.

So the funnel is the other direction: a small, closed vocabulary of
**non-negative integer counters**, rendered by this module into a bounded
one-line string that goes into the log MESSAGE — the one field the ops logs
channel already returns. Nothing author-controlled reaches the output except
Kalshi series tickers, which are sanitized to `[A-Z0-9_-]`, length-capped and
count-capped.

## The states an operator has to be able to tell apart

    fetched == 0, fetch complete  -> NO_MARKETS      the venue answered, with nothing
    fetched == 0, fetch failed    -> FETCH_FAILED    we never successfully asked
    fetched == 0, fetch partial   -> NO_MARKETS_INCOMPLETE   we asked, but not completely
    fetched > 0,  eligible == 0   -> NO_ELIGIBLE     markets returned, all rejected by eligibility
    eligible > 0, candidates == 0 -> NO_CANDIDATES   eligible, none survived to be a candidate
    candidates > 0, actions == 0  -> NO_ACTIONS      candidates produced, all rejected downstream
    actions > 0                   -> ACTIONS         the book acted

The first three are the correction that matters. "The venue has nothing for this
book" and "we could not reach the venue" produce the identical counter — zero —
and have opposite remedies, so the state must never assert the first when only
the second was observed. `NO_MARKETS` is a claim about the VENUE and is reserved
for a cycle in which every configured series was successfully asked.

## A stage that did not run is not a stage that found nothing

Not every tracker runs every stage on every cycle. xgame throttles discovery, so
on most cycles it never asks the venue at all; wcprop only scans for signals
while a match-settled trigger is open. Reporting those as `0` would invent a
finding out of a code path that was skipped — a zero the operator would then try
to explain.

So a stage may be `NOT_RUN`, and the rules are:

* a `NOT_RUN` stage is NEVER reported as a zero, so a cycle that skipped
  discovery cannot come out as `NO_MARKETS`;
* if some stage that DID run is zero, that zero is the diagnosis — a skipped
  earlier stage does not mask a real downstream failure;
* otherwise the first `NOT_RUN` stage is the diagnosis (`<STAGE>_NOT_RUN`),
  because nothing was actioned and the operator needs to know why not;
* `ACTIONS` requires that the action stage actually ran and produced something.

`first_zero_stage` names the earliest stage that RAN and is zero, which is the
single number a diagnosis starts from. It is deliberately NOT a judgement about
whether zero is correct: a book with no qualifying opportunities is a scientific
finding and belongs to Research Lab, not here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The funnel's stages, in order. A stage is only meaningful downstream of the
#: one before it, which is what makes "first zero" a localisation and not a tally.
FUNNEL_STAGES: tuple[str, ...] = ("fetched", "eligible", "candidates", "actions")

#: The ONLY field names this module will ever render. Every one is a
#: non-negative integer counter with no author-controlled content. Adding a name
#: here is a deliberate act: it publishes that number on a public channel.
FUNNEL_COUNTERS: frozenset[str] = frozenset(FUNNEL_STAGES)

#: How the cycle's fetch itself went. This is a separate axis from the counters:
#: a zero can mean the venue answered with nothing, or that we never got an
#: answer, and only the fetch knows which.
FETCH_OK = "OK"
FETCH_EMPTY_UNIVERSE = "EMPTY_UNIVERSE"          # every series succeeded, all returned zero
FETCH_PARTIAL_FAILURE = "PARTIAL_FETCH_FAILURE"  # some series failed: the cycle is INCOMPLETE
FETCH_FAILED = "FETCH_FAILED"                    # every series failed and nothing came back
FETCH_NO_SERIES = "NO_SERIES_CONFIGURED"         # nothing was configured to fetch

FETCH_DIAGNOSES: frozenset[str] = frozenset(
    {FETCH_OK, FETCH_EMPTY_UNIVERSE, FETCH_PARTIAL_FAILURE, FETCH_FAILED, FETCH_NO_SERIES}
)

#: Schema-versioned, greppable marker. It opens every rendered summary so an
#: operator can filter the ops logs channel for exactly these lines, and so a
#: later format change is visible rather than silently reinterpreted.
SUMMARY_MARKER = "funnel/v1"

#: Hard output bounds. These are the disclosure guarantee, not cosmetics: a
#: bounded renderer cannot be made to emit an unbounded string by feeding it a
#: large configuration.
MAX_SUMMARY_CHARS = 600
MAX_SERIES_LISTED = 5
MAX_SERIES_NAME_CHARS = 24
MAX_COUNTER_VALUE = 1_000_000_000

#: Truncation marker. Its presence in the output is the signal that something was
#: withheld — a reader must never mistake a bounded list for a complete one.
TRUNCATION_MARKER = "+"

class _NotRun:
    """Sentinel: this stage did not execute on this cycle.

    A distinct type rather than None, so that "did not run" can never be
    confused with "not supplied" and coerced into a zero by `_counter`.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "NOT_RUN"


#: Pass this as a stage value when the cycle skipped that stage entirely.
NOT_RUN = _NotRun()
NOT_RUN_TOKEN = "NOT_RUN"

_SERIES_SAFE = re.compile(r"[^A-Z0-9_-]")


def sanitize_series(name: object) -> str:
    """A Kalshi series ticker reduced to a publishable token.

    Series tickers come from configuration rather than from a market payload, but
    they are still the only non-numeric thing that reaches this output, so they
    are treated as untrusted: upper-cased, stripped to `[A-Z0-9_-]`, and capped.
    A name that sanitizes to nothing becomes `?` so a malformed entry is still
    counted and visible rather than silently vanishing from the list.
    """
    token = _SERIES_SAFE.sub("", str(name).upper())[:MAX_SERIES_NAME_CHARS]
    return token or "?"


def _counter(value: object) -> int:
    """A counter coerced to a non-negative, bounded int. Never raises."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, MAX_COUNTER_VALUE))


@dataclass(frozen=True)
class FunnelState:
    """One cycle's funnel for one book. Counters only — no tickers, no prices.

    A field is either a non-negative int or `NOT_RUN`.
    """

    fetched: int | _NotRun = 0
    eligible: int | _NotRun = 0
    candidates: int | _NotRun = 0
    actions: int | _NotRun = 0

    @classmethod
    def of(cls, **counts: object) -> FunnelState:
        """Build from whatever a book's cycle summary calls its stages.

        Unknown keyword names are REFUSED rather than ignored: a typo that
        silently produced a zero stage would fabricate exactly the diagnosis this
        module exists to make trustworthy.
        """
        unknown = sorted(set(counts) - FUNNEL_COUNTERS)
        if unknown:
            raise ValueError(f"not funnel counters: {unknown}")
        return cls(**{
            k: (v if isinstance(v, _NotRun) else _counter(v)) for k, v in counts.items()
        })

    def as_dict(self) -> dict[str, int | _NotRun]:
        return {stage: getattr(self, stage) for stage in FUNNEL_STAGES}

    def ran(self, stage: str) -> bool:
        return not isinstance(getattr(self, stage), _NotRun)


def first_zero_stage(state: FunnelState) -> str | None:
    """The earliest stage that RAN and is zero, or None.

    Stages that did not run are skipped, never reported as a zero — that is what
    stops a skipped-discovery cycle from being read as an empty venue.
    """
    for stage in FUNNEL_STAGES:
        if state.ran(stage) and getattr(state, stage) == 0:
            return stage
    return None


def not_run_stages(state: FunnelState) -> list[str]:
    """The stages this cycle skipped, in funnel order."""
    return [stage for stage in FUNNEL_STAGES if not state.ran(stage)]


def diagnose(state: FunnelState, *, fetch: str | None = None) -> str:
    """The operator-facing name of what happened this cycle.

    Order of precedence, and each clause is load-bearing:

    1. a stage that RAN and came back zero is the diagnosis — a skipped earlier
       stage must never mask a real downstream failure;
    2. otherwise, if the action stage ran and produced something, `ACTIONS`;
    3. otherwise the first skipped stage, because nothing was actioned and the
       reason is that the stage never executed.

    `fetch` is the cycle's fetch diagnosis. It only ever overrides the `fetched`
    stage, because that is the only stage a fetch problem can explain: once
    markets came back, a later zero is the book's own filtering. It is ignored
    entirely when `fetched` did not run — there was no fetch to diagnose.
    """
    stage = first_zero_stage(state)
    if stage == "fetched":
        if fetch == FETCH_FAILED:
            # We never successfully asked. Calling this NO_MARKETS would assert a
            # venue answer we did not receive.
            return "FETCH_FAILED"
        if fetch == FETCH_PARTIAL_FAILURE:
            # We asked, but not completely: the universe is unknown, not empty.
            return "NO_MARKETS_INCOMPLETE"
    if stage is not None:
        return {
            "fetched": "NO_MARKETS",
            "eligible": "NO_ELIGIBLE",
            "candidates": "NO_CANDIDATES",
            "actions": "NO_ACTIONS",
        }[stage]
    if state.ran("actions") and state.actions > 0:
        return "ACTIONS"
    skipped = not_run_stages(state)
    if skipped:
        return f"{skipped[0].upper()}_{NOT_RUN_TOKEN}"
    return "ACTIONS"


def _series_field(label: str, names: list[str], total: int) -> list[str]:
    """`<label>_series=n/total` plus a bounded, sorted, marked list."""
    if not names and not total:
        return []
    parts = [f"{label}_series={len(names)}/{total}"]
    if names:
        shown = sorted(names)[:MAX_SERIES_LISTED]
        hidden = len(names) - len(shown)
        listed = " ".join(shown)
        if hidden > 0:
            listed = f"{listed} {TRUNCATION_MARKER}{hidden}"
        parts.append(f"{label}=[{listed}]")
    return parts


def funnel_summary(
    state: FunnelState,
    *,
    fetch: str | None = None,
    empty_series: object = (),
    failed_series: object = (),
    configured_series: object = 0,
) -> str:
    """Render one bounded, publishable line.

    Shape::

        funnel/v1 state=NO_MARKETS_INCOMPLETE first_zero=fetched fetched=0
        eligible=0 candidates=0 actions=0 fetch=PARTIAL_FETCH_FAILURE
        empty_series=1/3 empty=[KXCORN] failed_series=1/3 failed=[KXWHEAT]

    Empty and failed are rendered as SEPARATE fields on purpose: collapsing them
    is the defect this exists to prevent. Only the fetch diagnosis NAME is
    rendered — never an exception message, which is unbounded and
    author-controlled.

    The whole string is capped at `MAX_SUMMARY_CHARS`; if the cap is hit the line
    is cut and `TRUNCATION_MARKER` appended, so a truncated line can never be
    mistaken for a complete one.
    """
    stage = first_zero_stage(state)
    parts = [
        SUMMARY_MARKER,
        f"state={diagnose(state, fetch=fetch)}",
        f"first_zero={stage or '-'}",
    ]
    parts += [
        f"{name}={NOT_RUN_TOKEN if isinstance(value, _NotRun) else value}"
        for name, value in state.as_dict().items()
    ]
    skipped = not_run_stages(state)
    if skipped:
        parts.append(f"not_run={','.join(skipped)}")

    # An unrecognised diagnosis is dropped rather than echoed: this field is a
    # closed vocabulary, and echoing an unknown value would make it an open one.
    if fetch in FETCH_DIAGNOSES and state.ran("fetched"):
        parts.append(f"fetch={fetch}")

    total = _counter(configured_series)
    parts += _series_field("empty", [sanitize_series(s) for s in (empty_series or ())], total)
    parts += _series_field("failed", [sanitize_series(s) for s in (failed_series or ())], total)

    line = " ".join(parts)
    if len(line) > MAX_SUMMARY_CHARS:
        line = line[: MAX_SUMMARY_CHARS - 1].rstrip() + TRUNCATION_MARKER
    return line
