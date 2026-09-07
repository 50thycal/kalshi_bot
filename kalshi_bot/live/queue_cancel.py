"""QUEUE-AWARE CANCELLATION — the decision engine, and nothing else.

WHAT THIS IS
------------
`docs/MMSELL_QUEUE_AWARE_CANCEL.md` asks one execution question about the mmsell10 maker book:
an unfilled BUY-NO order rests for four hours; some of those orders are so deep in Kalshi's
queue that they are unlikely to fill before the timeout, and while they rest they hold one of
the book's limited open slots. Would cancelling THOSE orders earlier — and only those — release
capacity for later candidates without buying a worse fill?

This module holds the frozen rule and the pure decision function. It touches no I/O: it never
calls Kalshi, never reads the database, never cancels. `LiveExecutor.evaluate_queue_cancellations`
feeds it one observation per resting order and acts (or, in shadow mode, only records) on the
answer. Keeping the arithmetic here, apart from the executor, is what makes every invariant
below a unit test rather than a promise:

  * **Missing, stale, malformed or errored telemetry NEVER cancels.** Every one of those is a
    distinct decision code that resolves to KEEP, so the existing four-hour timeout — which the
    executor applies on its own, before this runs — stays the only fallback. An API failure
    cannot cause a cancellation because an API failure never reaches `QUEUE_CANCEL`.
  * **The rule reads only what was known at decision time.** Its fill-probability estimate is a
    table frozen from the baseline epoch (`BASELINE_SURVIVAL_2026_09_07`), indexed by the
    order's CURRENT age and CURRENT contracts-ahead. Nothing about the order's eventual outcome
    is an input.
  * **A thin cell is not evidence.** A table cell backed by fewer than `min_cell_n` orders
    answers "insufficient evidence", which resolves to KEEP — the rule may only cancel where the
    baseline actually measured the probability it is acting on.
  * **The rule never touches price or size.** It returns a decision, not an order; the
    executor's only write on a cancel is the cancel itself plus the audit row.

THE FROZEN RULE, IN WORDS
-------------------------
Cancel a resting order when ALL of:
  1. it is at least `min_age_seconds` old (the baseline shows most fills land in the first
     30 minutes at every depth — a young order is where the fills are);
  2. queue telemetry for it is present, readable, and fresher than `max_observation_age_seconds`;
  3. the frozen table's P(fill before the timeout | this age bucket, this depth bucket) is at
     most `max_fill_probability_pct`, with the cell backed by at least `min_cell_n` orders;
  4. some timeout remains (an order past the timeout belongs to the ordinary timeout path).

The numbers in `FROZEN_RULE` come from the baseline read of 2026-09-07 over the four live
canary tags (408 orders, 18,143 queue samples, 100% telemetry coverage). They are written into
the Experiment OS pre-registration (`kalshi_bot/experiment_os/queue_aware_cancel.py`) and a test
asserts this module and that package agree byte-for-byte. Changing them is a new Version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulary — every decision and every telemetry state is a named string that
# lands in `live_order_queue_decisions`. Nothing here is a boolean.
# ---------------------------------------------------------------------------

#: Telemetry states. `observed` is the only one that can ever lead to a cancel.
TELEMETRY_OBSERVED = "observed"
TELEMETRY_MISSING = "missing"          # no sample for this order this cycle
TELEMETRY_STALE = "stale"              # a sample exists but is too old to act on
TELEMETRY_MALFORMED = "malformed"      # the API answered, the payload was unreadable
TELEMETRY_ERROR = "error"              # the API call itself failed
TELEMETRY_UNAVAILABLE_PAPER = "unavailable_paper"  # paper books have no queue at all
TELEMETRY_SIMULATED = "simulated"      # reserved: a modelled value, never a Kalshi one

#: Decision codes.
KEEP = "keep"                              # rule evaluated, order stays
QUEUE_CANCEL = "queue_cancel"              # rule says cancel (acted on only in live mode)
NORMAL_TIMEOUT = "normal_timeout"          # past the 4h timeout — the ordinary path owns it
TOO_YOUNG = "too_young"                    # under min_age; not evaluated further
INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # table cell too thin to act on
TELEMETRY_KEEP = "telemetry_keep"          # telemetry missing/stale/malformed/error -> keep
FILLED = "filled"                          # order filled before a decision could apply
EXCHANGE_ERROR = "exchange_error"          # a live cancel was attempted and Kalshi refused
REFUSED_UNREGISTERED = "refused_unregistered"  # live cancel refused: no registered lineage
DEFERRED_CYCLE_CAP = "deferred_cycle_cap"  # live cancel deferred: per-cycle bound reached

#: Codes that mean "the order is left resting". Everything a cancel could be gated on
#: resolves to one of these when the gate does not open.
KEEP_CODES = frozenset({KEEP, TOO_YOUNG, INSUFFICIENT_EVIDENCE, TELEMETRY_KEEP,
                        REFUSED_UNREGISTERED, DEFERRED_CYCLE_CAP})


# ---------------------------------------------------------------------------
# The frozen baseline table
# ---------------------------------------------------------------------------

#: Depth buckets over CONTRACTS AHEAD (Kalshi's `queue_position_fp` is a contract quantity,
#: not an ordinal rank — see live/queue_position.py). Upper bounds are inclusive.
DEPTH_BUCKETS: tuple[tuple[str, int | None], ...] = (
    ("b0", 0),
    ("b1_10", 10),
    ("b11_100", 100),
    ("b101_1k", 1000),
    ("b1k_5k", 5000),
    ("b5k+", None),
)

#: Age checkpoints, in minutes. An order is read at the LARGEST checkpoint it has reached,
#: so a 100-minute-old order uses the 90-minute row — the most recent measurement of orders
#: at least that old, never a younger (more optimistic) one.
AGE_CHECKPOINTS_MIN: tuple[int, ...] = (15, 30, 45, 60, 90, 120, 150, 180, 210)

#: P(fill before the 4h timeout | still resting at `age`, current depth bucket), as
#: (still_resting_n, filled_later_n). Measured 2026-09-07 over Cmmsell10 / Dmmsell10 /
#: Emmsell10 / Fmmsell10 orders created in the prior 336h with a terminal status, reading each
#: order's nearest queue sample (±3 min) at each checkpoint. Source query: ops request
#: `qac-b-3`, reproduced in docs/MMSELL_QUEUE_AWARE_CANCEL.md. Counts, not percentages, so the
#: thin-cell rule can be applied to the same object.
BASELINE_SURVIVAL_2026_09_07: dict[tuple[int, str], tuple[int, int]] = {
    (15, "b0"): (148, 48), (15, "b1_10"): (11, 3), (15, "b11_100"): (29, 17),
    (15, "b101_1k"): (45, 14), (15, "b1k_5k"): (35, 12), (15, "b5k+"): (64, 20),
    (30, "b0"): (127, 31), (30, "b1_10"): (12, 3), (30, "b11_100"): (20, 11),
    (30, "b101_1k"): (38, 10), (30, "b1k_5k"): (31, 10), (30, "b5k+"): (57, 15),
    (45, "b0"): (117, 28), (45, "b1_10"): (12, 3), (45, "b11_100"): (16, 9),
    (45, "b101_1k"): (36, 8), (45, "b1k_5k"): (29, 11), (45, "b5k+"): (52, 12),
    (60, "b0"): (103, 20), (60, "b1_10"): (12, 3), (60, "b11_100"): (17, 9),
    (60, "b101_1k"): (33, 7), (60, "b1k_5k"): (33, 15), (60, "b5k+"): (46, 7),
    (90, "b0"): (78, 16), (90, "b1_10"): (9, 1), (90, "b11_100"): (13, 5),
    (90, "b101_1k"): (31, 6), (90, "b1k_5k"): (25, 7), (90, "b5k+"): (40, 4),
    (120, "b0"): (62, 10), (120, "b1_10"): (7, 0), (120, "b11_100"): (12, 4),
    (120, "b101_1k"): (29, 6), (120, "b1k_5k"): (27, 7), (120, "b5k+"): (34, 2),
    (150, "b0"): (47, 7), (150, "b1_10"): (7, 0), (150, "b11_100"): (12, 4),
    (150, "b101_1k"): (26, 6), (150, "b1k_5k"): (19, 2), (150, "b5k+"): (33, 2),
    (180, "b0"): (41, 2), (180, "b1_10"): (6, 0), (180, "b11_100"): (10, 2),
    (180, "b101_1k"): (24, 4), (180, "b1k_5k"): (18, 1), (180, "b5k+"): (26, 1),
    (210, "b0"): (42, 0), (210, "b1_10"): (5, 0), (210, "b11_100"): (10, 1),
    (210, "b101_1k"): (19, 2), (210, "b1k_5k"): (17, 1), (210, "b5k+"): (25, 0),
}


def depth_bucket(contracts_ahead: int | None) -> str | None:
    """The depth bucket for a contracts-ahead figure; None when the figure is unusable."""
    if contracts_ahead is None or isinstance(contracts_ahead, bool):
        return None
    try:
        ahead = int(contracts_ahead)
    except (TypeError, ValueError):
        return None
    if ahead < 0:
        return None
    for name, upper in DEPTH_BUCKETS:
        if upper is None or ahead <= upper:
            return name
    return None  # unreachable: the last bucket is unbounded


def age_checkpoint(age_seconds: float) -> int | None:
    """The largest checkpoint the order has reached, or None if younger than the first."""
    minutes = age_seconds / 60.0
    reached = [cp for cp in AGE_CHECKPOINTS_MIN if minutes >= cp]
    return reached[-1] if reached else None


@dataclass(frozen=True)
class QueueCancelRule:
    """The frozen treatment rule. Immutable; a changed rule is a new `rule_version`."""

    rule_version: str
    min_age_seconds: int
    max_observation_age_seconds: int
    max_fill_probability_pct: float
    min_cell_n: int
    #: (age_checkpoint_min, depth_bucket) -> (still_resting_n, filled_later_n)
    survival: dict[tuple[int, str], tuple[int, int]] = field(
        default_factory=lambda: dict(BASELINE_SURVIVAL_2026_09_07), compare=True
    )

    def fill_probability(self, age_seconds: float, contracts_ahead: int | None
                         ) -> tuple[float | None, int, str | None, int | None]:
        """(p_fill_pct, cell_n, depth_bucket, age_checkpoint) from the frozen table.

        `p_fill_pct` is None when no cell applies (too young, unusable depth). A thin cell
        still returns its point estimate — the caller decides admissibility from `cell_n`,
        so the audit row can show the number the rule declined to act on."""
        bucket = depth_bucket(contracts_ahead)
        cp = age_checkpoint(age_seconds)
        if bucket is None or cp is None:
            return None, 0, bucket, cp
        cell = self.survival.get((cp, bucket))
        if not cell or cell[0] <= 0:
            return None, 0, bucket, cp
        n, filled = cell
        return round(100.0 * filled / n, 2), n, bucket, cp

    def inputs(self) -> dict[str, Any]:
        """The threshold inputs, in the shape the audit row and the pre-registration carry."""
        return {
            "rule_version": self.rule_version,
            "min_age_seconds": self.min_age_seconds,
            "max_observation_age_seconds": self.max_observation_age_seconds,
            "max_fill_probability_pct": self.max_fill_probability_pct,
            "min_cell_n": self.min_cell_n,
            "survival_table": "BASELINE_SURVIVAL_2026_09_07",
        }


#: THE PRE-REGISTERED RULE. Derived from the baseline read (docs/MMSELL_QUEUE_AWARE_CANCEL.md
#: §Baseline). With these inputs the cells that actually open the gate are:
#:   depth b5k+ from 90 min  (P(fill later) 10.0% n=40; 5.9% n=34 at 120; 6.1% n=33 at 150)
#:   depth b0   from 180 min (4.9% n=41; 0% n=42 at 210)
#:   depth b5k+ from 180 min (3.8% n=26; 0% n=25 at 210)
#: Every other cell is either above 10% or too thin (b1_10 at every age, b1k_5k at 150+).
#: Mirrored in `experiment_os/queue_aware_cancel.FROZEN_RULE_INPUTS`; a test pins the two.
FROZEN_RULE = QueueCancelRule(
    rule_version="qac-v1-2026-09-07",
    min_age_seconds=90 * 60,
    max_observation_age_seconds=10 * 60,
    max_fill_probability_pct=10.0,
    min_cell_n=20,
)


@dataclass(frozen=True)
class QueueObservation:
    """One order's queue telemetry as the executor saw it this cycle."""

    status: str                      # one of the TELEMETRY_* constants
    contracts_ahead: int | None = None
    queue_position: int | None = None
    observed_at: datetime | None = None
    detail: str | None = None        # error text / parse note, bounded by the caller


@dataclass(frozen=True)
class QueueCancelDecision:
    code: str
    telemetry_status: str
    order_age_seconds: int
    remaining_timeout_seconds: int
    estimated_fill_probability_pct: float | None
    cell_n: int
    depth_bucket: str | None
    age_checkpoint_min: int | None
    reason: str

    @property
    def cancels(self) -> bool:
        return self.code == QUEUE_CANCEL


def decide(
    rule: QueueCancelRule,
    *,
    order_age_seconds: float,
    timeout_seconds: int,
    observation: QueueObservation | None,
    now: datetime,
) -> QueueCancelDecision:
    """Apply the frozen rule to one resting order. Pure; never raises on bad telemetry."""
    age = int(max(0.0, order_age_seconds))
    remaining = int(timeout_seconds) - age
    tel = observation.status if observation is not None else TELEMETRY_MISSING

    def keep(code: str, reason: str, *, p=None, n=0, bucket=None, cp=None) -> QueueCancelDecision:
        return QueueCancelDecision(
            code=code, telemetry_status=tel, order_age_seconds=age,
            remaining_timeout_seconds=remaining, estimated_fill_probability_pct=p,
            cell_n=n, depth_bucket=bucket, age_checkpoint_min=cp, reason=reason,
        )

    if remaining <= 0:
        return keep(NORMAL_TIMEOUT, "past the order timeout — the ordinary timeout path owns it")
    if age < rule.min_age_seconds:
        return keep(TOO_YOUNG, f"age {age}s < min_age {rule.min_age_seconds}s")

    # Telemetry gates. Each is its own reason so the audit trail says WHICH one held.
    if observation is None or tel == TELEMETRY_MISSING:
        return keep(TELEMETRY_KEEP, "no queue observation this cycle")
    if tel != TELEMETRY_OBSERVED:
        return keep(TELEMETRY_KEEP, f"telemetry {tel}: {observation.detail or 'not actionable'}")
    if observation.observed_at is None:
        return keep(TELEMETRY_KEEP, "observation carries no timestamp")
    obs_age = (now - observation.observed_at).total_seconds()
    if obs_age < 0 or obs_age > rule.max_observation_age_seconds:
        # `tel` is rewritten so the row says stale rather than observed.
        tel = TELEMETRY_STALE
        return keep(TELEMETRY_KEEP,
                    f"observation {int(obs_age)}s old > {rule.max_observation_age_seconds}s")
    if observation.contracts_ahead is None:
        tel = TELEMETRY_MALFORMED
        return keep(TELEMETRY_KEEP, "observation has no contracts-ahead figure")

    p, n, bucket, cp = rule.fill_probability(age, observation.contracts_ahead)
    if p is None:
        return keep(INSUFFICIENT_EVIDENCE, "no frozen-table cell for this age/depth",
                    bucket=bucket, cp=cp)
    if n < rule.min_cell_n:
        return keep(INSUFFICIENT_EVIDENCE,
                    f"cell ({cp}m,{bucket}) n={n} < min_cell_n {rule.min_cell_n}",
                    p=p, n=n, bucket=bucket, cp=cp)
    if p > rule.max_fill_probability_pct:
        return keep(KEEP, f"P(fill later)={p}% > {rule.max_fill_probability_pct}%",
                    p=p, n=n, bucket=bucket, cp=cp)
    return QueueCancelDecision(
        code=QUEUE_CANCEL, telemetry_status=tel, order_age_seconds=age,
        remaining_timeout_seconds=remaining, estimated_fill_probability_pct=p,
        cell_n=n, depth_bucket=bucket, age_checkpoint_min=cp,
        reason=(f"P(fill later)={p}% (cell ({cp}m,{bucket}) n={n}) <= "
                f"{rule.max_fill_probability_pct}% at age {age}s, ahead={observation.contracts_ahead}"),
    )


def rule_from_settings(settings) -> QueueCancelRule:
    """The rule the worker runs, read from Settings so the drift check can compare it against
    the registered material config. Defaults equal `FROZEN_RULE` (a test pins this)."""
    return QueueCancelRule(
        rule_version=str(settings.live_queue_cancel_rule_version),
        min_age_seconds=int(settings.live_queue_cancel_min_age_seconds),
        max_observation_age_seconds=int(settings.live_queue_cancel_max_observation_age_seconds),
        max_fill_probability_pct=float(settings.live_queue_cancel_max_fill_probability_pct),
        min_cell_n=int(settings.live_queue_cancel_min_cell_n),
    )
