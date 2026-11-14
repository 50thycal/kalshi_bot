"""S2 live test (`limm-s2-live`): the frozen rules, as pure functions. NOT WIRED.

Pre-registration: `docs/LIMM_S2_LIVE_TEST.md`. Handoff: `docs/handoffs/HANDOFF-limm-s2-live-test.md`.

WHAT THIS IS
------------
The live test rests $10 bids at the best bid on about five markets taken from the paper arm
`S2-best` (`paper_arms.py`, `docs/LIMM_PAPER_STRATEGIES.md`), under FRESH tags, apart from
`Alimm1`. This module holds every number and decision rule that test uses, so they are frozen
before any live data and the later runner cannot quietly re-scope them:

  * `S2LiveRules`       — the envelope (5 markets, 1 per event, $10 a bid, <= $50 resting,
                          a $30 fill-loss stop, 14 days, hold fills to settlement);
  * `select_live`       — which markets the live book rests on this hour, read from the
                          paper arm's OPEN `S2-best` quote rows (so the paper arm is the twin
                          on exactly the same markets and hours);
  * `bid_for`           — the bid it rests there (the paper arm's own side and price, $10);
  * `stop_reason`       — the stop rules;
  * `read_labels`       — the two pre-registered bars, applied at the read.

WHY IT IS INERT
---------------
Nothing in the worker imports this module. It does no I/O and returns numbers. Running the
test needs a separate runner change (handoff, "Build B") that touches the order path; that
change is merged only on the owner's explicit go, and arming stays a Live Ops hard stop
(`docs/STANDING_AUTHORIZATIONS.md`). Until then this file cannot change live behaviour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import paper_arms as pa

#: Fresh tags (never traded; checked for collisions by `xos package-preflight`). The live tag
#: is NOT a prefix-extension of `Alimm1` or of any other live tag: LIVE_STRATEGIES matches by
#: prefix, and a shared prefix would arm two books with one entry.
PAPER_TAG = "s2best1"
LIVE_TAG = "As2best1"
TWIN_TAG = "As2best1_pt1"

#: The paper arm this book copies its markets, side and price from.
SOURCE_ARM = pa.ARM_S2_BEST

#: Series the owner paused for incentive entries (thesis §9.73: "stop YouTube"). Still binding.
EXCLUDED_SERIES = frozenset({"KXYTVIEWSHIGH"})

# Reasons a paper S2-best market is not taken live (logged by the runner; <= 32 chars).
SKIP_EXCLUDED = "excluded_series"
SKIP_EVENT_TAKEN = "event_taken"
SKIP_HELD = "account_holds"
SKIP_FILLED_BEFORE = "filled_before"
SKIP_UNDER_FLOOR = "under_payout_floor"
SKIP_FULL = "book_full"
SKIP_BAD_ROW = "bad_row"

# Stop reasons.
STOP_LOSS = "stop_fill_loss_cap"
STOP_DURATION = "stop_duration_over"


@dataclass(frozen=True)
class S2LiveRules:
    """Every number of the live test. Frozen by the pre-registration; a change after the test
    starts is a new Version (or epoch), never an edit."""

    max_markets: int = 5
    max_per_event: int = 1
    bid_usd: float = 10.00
    max_resting_usd: float = 50.00
    #: contracts cap per bid ($10 / 1c), the same as the paper arm's
    max_contracts: int = 1000
    #: the cheap side only, as the paper arm
    cheap_max_cents: int = pa.DEFAULT_RULES.cheap_max_cents
    #: every bid leaves this long before close (or at the programme's end), as the paper arm
    step_aside_hours: float = pa.DEFAULT_RULES.step_aside_hours
    #: the calibration used for SELECTION only (the test measures the real ratio)
    calibration: float = pa.DEFAULT_RULES.calibration
    #: a market is entered only if a $10 bid's calibrated reward to its step-aside projects at
    #: least this much: Kalshi pays nothing under $1 per market per programme, plus a margin
    #: (the same $1.50 floor as Alimm1's §9.77 entry rule)
    min_projected_usd: float = 1.50
    #: STOP: realized fill losses plus held fills marked at the best bid reach this
    fill_loss_stop_usd: float = 30.00
    duration_days: float = 14.0
    #: a market that filled (any quantity) is never re-entered during the test; held fills are
    #: kept to settlement (no stop-loss or take-profit), as the paper arm's fill cost assumes
    reenter_after_fill: bool = False
    hold_fills_to_settlement: bool = True

    @property
    def worst_case_loss_usd(self) -> float:
        """The money that can be lost before the stop acts: the stop level plus every resting
        bid filling in the same instant and going to zero."""
        return self.fill_loss_stop_usd + self.max_resting_usd


DEFAULT_RULES = S2LiveRules()


@dataclass(frozen=True)
class PaperS2Row:
    """One OPEN `S2-best` quote row of the shadow collector, as the live book sees it."""

    ticker: str
    event: str
    series: str
    side: str                      # "yes" | "no"
    price: int                     # native cents on that side
    rate_per_hour_usd: float       # est_reward_per_hour_usd (uncalibrated, at the paper's $10)
    hours_to_step_aside: float


@dataclass(frozen=True)
class LiveBid:
    ticker: str
    event: str
    side: str
    price: int
    contracts: int
    cost_usd: float


def parse_arm_reason(reason: str | None) -> tuple[str, str, int, int] | None:
    """`"S2-best:yes@4x250"` -> ("S2-best", "yes", 4, 250). None when it is not an arm row."""
    if not reason or ":" not in reason or "@" not in reason or "x" not in reason:
        return None
    try:
        arm, rest = reason.split(":", 1)
        side, rest = rest.split("@", 1)
        price, qty = rest.split("x", 1)
        side = side.strip().lower()
        if side not in ("yes", "no"):
            return None
        return arm.strip(), side, int(price), int(qty)
    except ValueError:
        return None


def projected_usd(row: PaperS2Row, rules: S2LiveRules = DEFAULT_RULES) -> float:
    """A $10 bid's calibrated reward from now to its step-aside, at the paper row's rate."""
    h = row.hours_to_step_aside
    if not math.isfinite(h) or h <= 0:
        return 0.0
    return row.rate_per_hour_usd * h * rules.calibration


def bid_for(row: PaperS2Row, rules: S2LiveRules = DEFAULT_RULES) -> LiveBid | None:
    """The paper arm's own side and price, $10 of contracts (capped). None if not quotable."""
    if not (1 <= row.price <= rules.cheap_max_cents):
        return None
    qty = min(rules.max_contracts, int(rules.bid_usd * 100 // row.price))
    if qty <= 0:
        return None
    return LiveBid(ticker=row.ticker, event=row.event, side=row.side, price=row.price,
                   contracts=qty, cost_usd=round(qty * row.price / 100.0, 2))


@dataclass
class Selection:
    bids: list[LiveBid] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)   # ticker -> reason
    dropped: list[str] = field(default_factory=list)        # previously selected, now gone


def select_live(
    rows: list[PaperS2Row],
    *,
    current: set[str] | frozenset[str] = frozenset(),
    account_held: set[str] | frozenset[str] = frozenset(),
    filled_before: set[str] | frozenset[str] = frozenset(),
    rules: S2LiveRules = DEFAULT_RULES,
) -> Selection:
    """This hour's live markets, taken from the paper arm's open `S2-best` rows.

    Sticky, like the paper arm: a market already selected stays while the paper arm still
    rests on it (and it is not held or filled), and a better market never evicts it. Free
    slots are filled by the highest paper reward rate, at most one market per event, only
    where a $10 bid projects >= `min_projected_usd` to its step-aside, never on a market the
    account holds under any tag, and never on one this book has filled on. Total resting cost
    stays <= `max_resting_usd`."""
    out = Selection()
    by_ticker: dict[str, PaperS2Row] = {}
    for r in rows:
        if r.ticker not in by_ticker or r.rate_per_hour_usd > by_ticker[r.ticker].rate_per_hour_usd:
            by_ticker[r.ticker] = r
    resting = 0.0

    def _blocked(r: PaperS2Row) -> str | None:
        if r.series.upper() in EXCLUDED_SERIES:
            return SKIP_EXCLUDED
        if r.ticker in account_held:
            return SKIP_HELD
        if r.ticker in filled_before and not rules.reenter_after_fill:
            return SKIP_FILLED_BEFORE
        return None

    def _take(r: PaperS2Row) -> bool:
        nonlocal resting
        bid = bid_for(r, rules)
        if bid is None:
            out.skipped[r.ticker] = SKIP_BAD_ROW
            return False
        if len(out.bids) >= rules.max_markets or resting + bid.cost_usd > rules.max_resting_usd + 1e-9:
            out.skipped[r.ticker] = SKIP_FULL
            return False
        out.bids.append(bid)
        resting += bid.cost_usd
        return True

    # 1. keep what is already selected (sticky), in paper-rate order
    for t in sorted(current):
        r = by_ticker.get(t)
        if r is None:
            out.dropped.append(t)
            continue
        why = _blocked(r)
        if why is not None:
            out.dropped.append(t)
            out.skipped[t] = why
            continue
        if sum(1 for b in out.bids if b.event == r.event) >= rules.max_per_event:
            out.dropped.append(t)
            out.skipped[t] = SKIP_EVENT_TAKEN
            continue
        if not _take(r):
            out.dropped.append(t)

    # 2. fill free slots, best paper rate first (ties: ticker, for determinism)
    pool = sorted((r for t, r in by_ticker.items() if t not in current),
                  key=lambda r: (-r.rate_per_hour_usd, r.ticker))
    for r in pool:
        why = _blocked(r)
        if why is not None:
            out.skipped[r.ticker] = why
            continue
        if sum(1 for b in out.bids if b.event == r.event) >= rules.max_per_event:
            out.skipped[r.ticker] = SKIP_EVENT_TAKEN
            continue
        if projected_usd(r, rules) < rules.min_projected_usd:
            out.skipped[r.ticker] = SKIP_UNDER_FLOOR
            continue
        if len(out.bids) >= rules.max_markets:
            out.skipped[r.ticker] = SKIP_FULL
            continue
        _take(r)
    return out


def stop_reason(*, fill_loss_usd: float, started_at: datetime, now: datetime,
                rules: S2LiveRules = DEFAULT_RULES) -> str | None:
    """The pre-registered stops. `fill_loss_usd` is realized fill losses plus held fills marked
    at the current best bid, as a positive number of dollars lost (gains do not offset it)."""
    if fill_loss_usd >= rules.fill_loss_stop_usd:
        return STOP_LOSS
    if now - started_at >= timedelta(days=rules.duration_days):
        return STOP_DURATION
    return None


# ------------------------------------------------------------------------------- the read

#: Primary bar: net = confirmed payouts - fill losses over the test (USD).
SCALE_NET_USD = 20.00
#: Secondary bar: paid / uncalibrated estimate on this book's own resting time.
RATIO_HOLDS = 1.00          # >= 75% of the 1.34 the paper arms assume
RATIO_FAILS = 0.50          # < 37% of 1.34: the paper reward level is not real on these markets
#: Floors, else EXTEND (one more 2-week run, owner decision).
MIN_BID_DAYS = 40.0
MIN_PROGRAMMES = 4

LABEL_SCALE = "SCALE-CANDIDATE"
LABEL_KEEP = "KEEP"
LABEL_STOP = "STOP"
LABEL_EXTEND = "EXTEND"
RATIO_LABEL_HOLDS = "HOLDS"
RATIO_LABEL_WEAK = "WEAK"
RATIO_LABEL_FAILS = "FAILS"
RATIO_LABEL_NONE = "NO-ESTIMATE"


def paid_ratio(paid_usd: float, estimated_uncalibrated_usd: float) -> float | None:
    if estimated_uncalibrated_usd <= 0:
        return None
    return paid_usd / estimated_uncalibrated_usd


def ratio_label(ratio: float | None) -> str:
    if ratio is None:
        return RATIO_LABEL_NONE
    if ratio >= RATIO_HOLDS:
        return RATIO_LABEL_HOLDS
    if ratio >= RATIO_FAILS:
        return RATIO_LABEL_WEAK
    return RATIO_LABEL_FAILS


def read_labels(*, paid_usd: float, fill_loss_usd: float, estimated_uncalibrated_usd: float,
                bid_days: float, programmes: int, stopped_on_loss: bool = False) -> dict:
    """Apply the pre-registered bars once. `fill_loss_usd` is the net fill result as a loss
    (positive = money lost; a net fill gain is a negative loss). Every label is a
    recommendation to the owner; none moves anything."""
    net = round(paid_usd - fill_loss_usd, 2)
    ratio = paid_ratio(paid_usd, estimated_uncalibrated_usd)
    rl = ratio_label(ratio)
    if stopped_on_loss or net <= 0:
        primary = LABEL_STOP
    elif bid_days < MIN_BID_DAYS or programmes < MIN_PROGRAMMES:
        primary = LABEL_EXTEND
    elif net >= SCALE_NET_USD and rl == RATIO_LABEL_HOLDS:
        primary = LABEL_SCALE
    else:
        primary = LABEL_KEEP
    return {"net_usd": net, "paid_usd": round(paid_usd, 2),
            "fill_loss_usd": round(fill_loss_usd, 2),
            "ratio": None if ratio is None else round(ratio, 3), "ratio_label": rl,
            "bid_days": round(bid_days, 1), "programmes": programmes, "primary": primary}


__all__ = [
    "DEFAULT_RULES", "EXCLUDED_SERIES", "LIVE_TAG", "LiveBid", "PAPER_TAG", "PaperS2Row",
    "S2LiveRules", "SOURCE_ARM", "Selection", "TWIN_TAG", "bid_for", "paid_ratio",
    "parse_arm_reason", "projected_usd", "ratio_label", "read_labels", "select_live",
    "stop_reason",
]
