"""Measure liquidity rewards by arithmetic, because the API will not tell us directly.

WHY THIS EXISTS
---------------
The whole liquidity-incentive thesis turns on one number — what Kalshi actually credits us —
and that number has no endpoint. `GET /incentive_programs` returns a programme's *terms* (its
pool, `target_size_fp`, `discount_factor_bps`, whether it has paid out); it never returns our
share of them. The portfolio surface is positions, orders, fills, settlements and queue
positions. A full census of every key the programme object returns, across 5,332 current rows,
found eleven fields and none of them is a credit to us (thesis §9.21). The only external reading
that exists is the "Lifetime rewards" figure on Kalshi's own web page, which a human has to go
and look at, and which has read **$0** every time it has been checked (§9.17, §9.21).

So we recover it as a residual. A liquidity credit is cash that appears in the account balance
and is neither a trade nor a market settlement:

    Δbalance = settlements + sell proceeds − buy cost − fees + REWARDS + external transfers

Rearranged, that is what this module computes:

    residual = Δbalance − settlements − sell proceeds + buy cost + fees

Everything on the right is observable through endpoints we already call. What is left over is a
candidate reward.

WHAT A RESIDUAL IS NOT
----------------------
**`residual` is a candidate, not a reward, and the difference matters.** Anything that moves cash
without being a fill or a settlement lands here:

  * a deposit or withdrawal — a transfer in reads as a large positive residual and would be the
    most embarrassing possible false positive, so `EXTERNAL_TRANSFER_CENTS` marks the magnitude
    above which a residual is presumed to be a transfer rather than a reward;
  * a fee Kalshi charged that its fills payload did not report to us — this pushes the residual
    NEGATIVE, so a persistent small negative drift is an unattributed-fee problem, not a reward;
  * our own ingestion lag — a fill that happened inside the window but was not yet returned by
    the API when we read it lands in the NEXT window, with the wrong sign in both.

The window boundaries are therefore recorded on every row, so a residual can always be
re-derived from the raw data rather than trusted. Rows are append-only observations, never
conclusions: this module computes a number and stores it. Deciding whether a residual IS a
reward is a judgement made against the payout dates of programmes we actually quoted, and no
code here makes it.

ACCOUNTING UNITS
----------------
Everything is INTEGER CENTS, end to end. The reward being measured is expected to be on the
order of fractions of a cent per period at the canary's size (§9.13 predicted, and §9.17
confirmed, a lifetime total that rounds to zero), so a float rounding error is the same size as
the signal. Integers make an exactly-zero residual mean exactly zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

#: A residual at least this large is presumed to be a deposit or withdrawal, not a reward.
#: The canary risks at most $10 in total (`MAX_STRATEGY_EXPOSURE_USD`) and the modelled reward
#: at its size is fractions of a cent, so a dollar-scale jump is a transfer with near-certainty.
#: Presumed, not discarded: the row is still written, with `presumed_transfer` set.
EXTERNAL_TRANSFER_CENTS = 100

#: Residuals smaller than this in magnitude are treated as accounting noise rather than signal
#: when summarising. Kept at one cent because a cent is the smallest unit the balance moves in:
#: the first real reward we could ever SEE in a balance is 1c, whatever Kalshi accrued.
MATERIAL_RESIDUAL_CENTS = 1


def _to_cents(value, *, default: int = 0) -> int:
    """A MONEY field as integer cents, accepting both shapes Kalshi ships.

    `'0.94'` is ninety-four cents; `94` is already ninety-four cents. Reading the dollar string
    as an integer would value a 94c fill at 0, which is precisely the defect §9.29 records.
    Never raises: a payload we cannot parse must not take down the loop that calls this."""
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return default


def _to_count(value, *, default: int = 0) -> int:
    """A CONTRACT COUNT, as an int. `'2.00'` (fixed-point string) and `2` both mean two."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _first(fill: dict, *keys: str):
    """First non-None value among `keys`. Kalshi ships several names for the same field."""
    for k in keys:
        v = fill.get(k)
        if v is not None:
            return v
    return None


def _fill_cost_cents(fill: dict) -> tuple[int, int, int]:
    """`(buy_cost, sell_proceeds, fee)` in cents for one fill, from its own side's price.

    A Kalshi fill names a side (`yes`/`no`) and an action (`buy`/`sell`), and carries the price
    for BOTH sides. The cash that moved is the price of the side actually traded — using
    `yes_price` for a NO fill would be wrong by `100 − price` per contract, which at the
    canary's 3c and 10c NO bids is an order-of-magnitude error in the thing we are measuring.

    THE KEY NAMES ARE THE WHOLE PROBLEM, and getting them wrong is silent. The live fills feed
    ships dollar STRINGS under `*_price_dollars`, a fixed-point string count under `count_fp`,
    and the fee in DOLLARS under `fee_cost` — the shapes `LiveExecutor.reconcile` has read since
    its shape probe. This function originally read only `yes_price` / `no_price` / `count`, so
    against the real payload every price and every count parsed to ZERO: the fills were counted
    and then valued at nothing, and the cash they moved fell through into the residual.

    That is not a harmless miss. On 2026-09-19 the very first window containing live fills — two
    Fmmsell10 NO buys at 90c and 94c — produced a residual of exactly -184c, which is exactly
    those two fills, and the ledger labelled it `presumed deposit/withdrawal` because it crossed
    `EXTERNAL_TRANSFER_CENTS`. A plausible-sounding label on a number that was really our own
    mis-parse is the worst failure this module can have, so the preferred names now come first
    and the legacy cents shapes stay as fallbacks (thesis §9.29)."""
    count = _to_count(_first(fill, "count_fp", "count", "quantity"))
    side = str(fill.get("side") or "").strip().lower()
    if side == "no":
        price = _to_cents(_first(fill, "no_price_dollars", "no_price", "price"))
    else:
        price = _to_cents(_first(fill, "yes_price_dollars", "yes_price", "price"))
    # Fee is optional in the payload. When Kalshi does not report it the charge still happened,
    # so it lands in the residual with a NEGATIVE sign — see the module docstring.
    fee = _to_cents(_first(fill, "fee_cost", "fee", "fee_cents"))
    gross = price * count
    action = str(fill.get("action") or "").strip().lower()
    if action == "sell":
        return 0, gross, fee
    return gross, 0, fee


@dataclass(frozen=True)
class Reconciliation:
    """One window's worth of cash movement, split into what we can explain and what we cannot."""

    delta_cents: int
    buy_cost_cents: int
    sell_proceeds_cents: int
    fees_cents: int
    settlement_cents: int
    residual_cents: int
    fills_counted: int
    settlements_counted: int

    @property
    def presumed_transfer(self) -> bool:
        """Whether this residual is large enough to be a deposit or withdrawal."""
        return abs(self.residual_cents) >= EXTERNAL_TRANSFER_CENTS

    @property
    def is_material(self) -> bool:
        """A residual worth looking at: non-trivial, and not presumed to be a transfer."""
        return (abs(self.residual_cents) >= MATERIAL_RESIDUAL_CENTS
                and not self.presumed_transfer)

    def as_dict(self) -> dict:
        d = {
            "delta_cents": self.delta_cents,
            "buy_cost_cents": self.buy_cost_cents,
            "sell_proceeds_cents": self.sell_proceeds_cents,
            "fees_cents": self.fees_cents,
            "settlement_cents": self.settlement_cents,
            "residual_cents": self.residual_cents,
            "fills_counted": self.fills_counted,
            "settlements_counted": self.settlements_counted,
        }
        d["presumed_transfer"] = self.presumed_transfer
        d["is_material"] = self.is_material
        return d


def reconcile(*, prev_balance_cents: int, balance_cents: int,
              fills: list[dict] | None = None,
              settlements: list[dict] | None = None) -> Reconciliation:
    """Split a balance change into explained cash and an unexplained residual.

    Pure: it takes the two balances and the raw payloads for the window between them, and
    returns arithmetic. It reads no clock, opens no session and makes no request, so the
    accounting identity can be tested directly instead of through a live account.

    The identity, restated from the module docstring:

        residual = Δbalance − settlements − sell proceeds + buy cost + fees

    A positive residual is cash that arrived from somewhere that is not a trade and not a
    settlement. That is the shape a liquidity credit has. It is also the shape a deposit has,
    which is why the result carries `presumed_transfer` rather than calling it a reward.
    """
    buy_cost = 0
    sell_proceeds = 0
    fees = 0
    fill_rows = list(fills or ())
    for fill in fill_rows:
        cost, proceeds, fee = _fill_cost_cents(fill)
        buy_cost += cost
        sell_proceeds += proceeds
        fees += fee
    settle_rows = list(settlements or ())
    # `revenue` has read as integer cents in production (a 2026-09-19 settlement differenced the
    # balance exactly), so this preserves that. The dollar-string variant is accepted too, for
    # the same reason the fill parser accepts both: a shape change here would not raise, it
    # would quietly turn settled cash into a residual and invite it to be read as a reward.
    settlement = sum(_to_cents(_first(s, "revenue", "revenue_dollars")) for s in settle_rows)
    delta = int(balance_cents) - int(prev_balance_cents)
    residual = delta - settlement - sell_proceeds + buy_cost + fees
    return Reconciliation(
        delta_cents=delta, buy_cost_cents=buy_cost, sell_proceeds_cents=sell_proceeds,
        fees_cents=fees, settlement_cents=settlement, residual_cents=residual,
        fills_counted=len(fill_rows), settlements_counted=len(settle_rows),
    )


@dataclass
class _Paged:
    """What a paged portfolio read returned, plus whether it ran out of pages cleanly."""

    rows: list[dict] = field(default_factory=list)
    truncated: bool = False


def _collect(fetch, key: str, *, params: dict, max_pages: int = 20,
             page_size: int = 200) -> _Paged:
    """Follow Kalshi's cursor until it stops, bounded.

    The bound is the point. An unbounded cursor loop inside the collector's tick is a way to
    hang the worker on a bad day, and a truncated window is recoverable — it is recorded as
    `truncated` and the residual from that window is not trustworthy. Better a marked-bad
    observation than a stalled collector."""
    out = _Paged()
    cursor = None
    for _ in range(max_pages):
        page = dict(params)
        page["limit"] = page_size
        if cursor:
            page["cursor"] = cursor
        got = fetch(**page) or {}
        rows = got.get(key) or []
        out.rows.extend(r for r in rows if isinstance(r, dict))
        cursor = got.get("cursor") or None
        if not cursor or not rows:
            return out
    out.truncated = True
    return out


def observe(client, *, prev_balance_cents: int | None,
            since: datetime | None) -> tuple[int, Reconciliation | None, dict]:
    """Read the balance now, and reconcile it against the last reading.

    Returns `(balance_cents, reconciliation_or_None, notes)`. The reconciliation is `None` on
    the very first observation, when there is no previous balance to difference against — that
    first row exists only to anchor the next one, and reporting a residual against a balance of
    zero would invent a credit the size of the whole account.

    Every failure is a note rather than an exception. This runs inside the collector's tick, and
    a measurement that cannot be taken must not stop the shadow that is being measured."""
    notes: dict = {}
    balance_raw = client.get_balance() or {}
    # Integer cents in production (the observed balances difference exactly), but read through
    # the tolerant parser anyway: a dollar-string `balance` taken as an int would understate the
    # account hundredfold and manufacture an enormous residual on the very next window.
    balance_cents = _to_cents(_first(balance_raw, "balance", "balance_dollars"))
    if prev_balance_cents is None:
        notes["anchor"] = "first observation; no previous balance to difference against"
        return balance_cents, None, notes

    params: dict = {}
    if since is not None:
        # Kalshi filters these by unix seconds. The window is inclusive of its start, so a
        # boundary fill can be counted twice across two windows; one second of overlap is the
        # safe direction, since double-counting a KNOWN cost understates the residual rather
        # than inventing a reward.
        params["min_ts"] = int(since.timestamp())

    fills = _Paged()
    settlements = _Paged()
    try:
        fills = _collect(client.get_fills, "fills", params=params)
    except Exception as exc:  # noqa: BLE001 — a missed read is a note, never a crash
        notes["fills_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    try:
        settlements = _collect(client.get_settlements, "settlements", params=params)
    except Exception as exc:  # noqa: BLE001
        notes["settlements_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    if fills.truncated:
        notes["fills_truncated"] = True
    if settlements.truncated:
        notes["settlements_truncated"] = True
    if notes:
        # Any of the above means the explained side of the identity is incomplete, so whatever
        # is left over is not attributable. Say so on the row rather than letting a reader treat
        # an under-explained window as a reward.
        notes["residual_untrustworthy"] = True

    rec = reconcile(prev_balance_cents=prev_balance_cents, balance_cents=balance_cents,
                    fills=fills.rows, settlements=settlements.rows)
    return balance_cents, rec, notes


__all__ = [
    "EXTERNAL_TRANSFER_CENTS",
    "MATERIAL_RESIDUAL_CENTS",
    "Reconciliation",
    "observe",
    "reconcile",
]
