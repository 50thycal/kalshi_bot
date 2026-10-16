"""Fill-level live attribution: each strategy tag's economics from ITS OWN fills.

WS-027 phase 1 (Platform Change Review). `docs/LIVE_FILL_ATTRIBUTION.md` is the review
record; this module is the implementation it reviews.

WHY. `positions` is keyed by MARKET, not by tag, so the shipped live provider
(`metrics._live_market_rows`, `live_exec_v1`) cannot split a market two tags traded and
drops it from every live metric as CONTESTED. That was a sound refusal while no two tags
were meant to share a market. The WIN-set canary is designed to share them with
`Kmmsell10`, so the refusal would make every shared market vanish from both books' gates.

WHAT. Every tag's economics are rebuilt from the fills on its OWN orders
(`fills` x `live_orders` on `kalshi_order_id`):

    contracts(tag, market) = sum of entry-BUY fill quantity on that tag's orders
    pnl(tag, market)       = sum over the tag's fills of  s * q * (settle - px) / 100
                             minus the tag's own fees

where `s` is +1 for a buy and -1 for a sell, and `q`, `px` and `settle` are all in the
ORDER's side frame. Three facts measured in production on 2026-10-16 (ops
`ws027-fills-1`, `ws027-recon-3`) shape that formula, and each is load-bearing:

  * **Direction comes from the ORDER, never the fill.** Kalshi reports an mmsell maker
    NO-buy as a fill with `side=no, action=sell` at the NO price. The fill's `action`
    would invert every mmsell trade.
  * **The fill price is mapped into the order's side frame.** Where the fill's side
    differs from the order's (seen on the incentive book), the price is `100 - p`.
  * **Quantity is `count_fp`, not `fills.quantity`.** The integer column is rounded from
    a fractional fill: 1.50 + 0.75 + 0.75 contracts was stored as 2 + 1 + 1 = 4 on
    `KXRAIN-26OCT13-MIN`, which settled as 3.00. The shipped provider sums the integer
    column too; this one does not.

The settlement value is a property of the MARKET, read from the settlement snapshot
`reconcile` writes (`raw_json.value`, the YES settlement in cents, which also prices a
`scalar` result), never a pro-rata split of `positions.realized_pnl`.

Measured against production before any of this ran: 2,490 of 2,491 settled single-tag
mmsell markets reconcile to `positions.realized_pnl` within 1c, and all 16 settled
two-tag markets reconcile exactly (ops `ws027-recon-4`). The one residual is the kind of
thing `record_mismatches` exists to surface.

ACTIVATION. Nothing here changes a verdict until the owner activates the
`METRICS_ENGINE:live_fill_attribution_2026_10` Platform Revision. `metrics.py` asks
`fill_attribution_active()` and runs the shipped provider, byte-identical, until then.
Merging is not activation; a `CUTOVER` carrying `expect_metrics_fingerprint` is, and it
defers on any worker whose copy of this file is not the reviewed one.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select

#: The Platform Revision whose activation turns this provider on.
COMPONENT = "METRICS_ENGINE"
VERSION = "live_fill_attribution_2026_10"
REVISION_REF = f"{COMPONENT}:{VERSION}"
#: The provider revision every live metric records once fill attribution is active.
PROVIDER_REVISION = "live_fill_v1"
#: Sum of per-tag P&L must equal the account's realized P&L on a market within this.
#: Measured: 2,490 of 2,491 settled markets within 1c. Two cents absorbs per-fill fee
#: rounding against the settlement's single fee figure without hiding a missing contract.
RECONCILE_TOLERANCE_USD = 0.02
#: Integrity kind recorded for a market whose per-tag P&L does not reconcile.
MISMATCH_KIND = "LIVE_ATTRIBUTION_MISMATCH"
#: Suffix the closeout path appends to a book's tag (`LiveExecutor.close_mmsell_positions`).
CLOSEOUT_SUFFIX = "_closeout"
#: Positions below this are dust that cannot be traded out (same threshold as
#: `metrics.live_open_exposure`).
DUST = 0.01


def base_tag(strategy: str | None) -> str:
    """A closeout order belongs to the book it closes."""
    s = strategy or ""
    return s[: -len(CLOSEOUT_SUFFIX)] if s.endswith(CLOSEOUT_SUFFIX) else s


def fill_qty(fill) -> float:
    """Contracts on one fill: Kalshi's fractional `count_fp` when recorded, else the
    (rounded) integer column. See the module docstring for why the order matters."""
    raw = fill.raw_fill_json if isinstance(fill.raw_fill_json, dict) else None
    if raw is not None:
        for key in ("count_fp", "count"):
            val = raw.get(key)
            if val is None:
                continue
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return float(fill.quantity or 0)


def price_in_order_frame(fill_side: str | None, fill_price, order_side: str | None) -> float:
    """The fill price expressed on the ORDER's side (YES and NO prices sum to 100)."""
    px = float(fill_price or 0)
    fs, os_ = (fill_side or "").lower(), (order_side or "").lower()
    if fs and os_ and fs != os_:
        return 100.0 - px
    return px


def _sign(action: str | None) -> int:
    return -1 if (action or "").lower() == "sell" else 1


def no_frame(order_side: str | None, signed_qty: float) -> float:
    """Signed quantity expressed as a NO position: Kalshi nets a YES contract against a
    NO contract, so holding YES is holding negative NO."""
    return signed_qty if (order_side or "").lower() == "no" else -signed_qty


@dataclass
class MarketSettlement:
    """What the newest position snapshot says about one market."""

    state: str                     # open | settled | flat_unpriced | unknown
    yes_value: float | None = None  # YES settlement value in cents, when settled
    realized_usd: float | None = None
    account_qty: float = 0.0       # signed, Kalshi convention (negative = NO held)


def market_settlement(session, ticker: str) -> MarketSettlement:
    """The newest snapshot decides (`positions` is append-only)."""
    from ..models import Position

    row = session.scalar(
        select(Position).where(Position.market_ticker == ticker)
        .order_by(Position.captured_at.desc(), Position.id.desc()).limit(1)
    )
    if row is None:
        return MarketSettlement("unknown")
    qty = float(row.quantity_fp) if row.quantity_fp is not None else float(row.quantity or 0)
    realized = float(row.realized_pnl) if row.realized_pnl is not None else None
    if abs(qty) > DUST:
        return MarketSettlement("open", None, realized, qty)
    raw = row.raw_json if isinstance(row.raw_json, dict) else {}
    yes_value = None
    if raw.get("value") is not None:
        try:
            yes_value = float(raw["value"])
        except (TypeError, ValueError):
            yes_value = None
    if yes_value is None:
        result = str(raw.get("market_result") or "").lower()
        if result in ("yes", "no"):
            yes_value = 100.0 if result == "yes" else 0.0
    if yes_value is None:
        return MarketSettlement("flat_unpriced", None, realized, qty)
    return MarketSettlement("settled", yes_value, realized, qty)


@dataclass
class TagLedger:
    """One tag's fills on one market, reduced to what the providers need."""

    entry_contracts: float = 0.0     # entry-BUY fill quantity
    net_no: float = 0.0              # signed NO-frame position (YES held is negative)
    cash_cents: float = 0.0          # sum of -s * q * px, in the order's frame
    settle_legs: list = field(default_factory=list)  # (order_side, signed qty)
    fees_usd: float = 0.0
    fills: int = 0
    sides: set = field(default_factory=set)

    def pnl_usd(self, yes_value: float | None) -> float | None:
        """Realized P&L. Needs no settlement value once the tag is flat: the
        settlement value multiplies the NET position, so it drops out — a closed-out
        tag is priced on a market that has not settled. Flat is not "no settlement
        term", though: a NO position closed by BUYING YES (the closeout path) still
        redeems each YES+NO pair at 100c, which the per-leg sum below keeps."""
        if yes_value is None:
            if abs(self.net_no) > DUST:
                return None
            yes_value = 0.0   # any value: it multiplies a zero net position
        settle = sum(q * (yes_value if side == "yes" else 100.0 - yes_value)
                     for side, q in self.settle_legs)
        return (self.cash_cents + settle) / 100.0 - self.fees_usd


def ledgers_for_market(session, ticker: str, *, tags=None, window=None) -> dict[str, TagLedger]:
    """`{base_tag: TagLedger}` for every tag (or the given tags) with fills on `ticker`.

    `window` (start, end) restricts ENTRY orders to those created inside it; a book's
    closeout orders count whenever they happened, because they close a position the
    window opened. Fills with no matching order are reported under the empty tag, so a
    reconciliation can see contracts nobody's order explains."""
    from ..models import Fill, LiveOrder

    rows = session.execute(
        select(LiveOrder.strategy, LiveOrder.side, LiveOrder.action, LiveOrder.created_at,
               Fill)
        .join(Fill, Fill.kalshi_order_id == LiveOrder.kalshi_order_id)
        .where(LiveOrder.market_ticker == ticker)
    ).all()
    out: dict[str, TagLedger] = defaultdict(TagLedger)
    seen_fill_ids: set = set()
    for strategy, o_side, o_action, created_at, fill in rows:
        if fill.id in seen_fill_ids:
            continue
        seen_fill_ids.add(fill.id)
        tag = base_tag(strategy)
        is_closeout = (strategy or "").endswith(CLOSEOUT_SUFFIX)
        if tags is not None and tag not in tags:
            continue
        if window is not None and not is_closeout:
            start, end = window
            at = _aware(created_at)
            if at is None or at < _aware(start) or at > _aware(end):
                continue
        led = out[tag]
        q = fill_qty(fill)
        s = _sign(o_action)
        px = price_in_order_frame(fill.side, fill.price, o_side)
        side = (o_side or "").lower()
        led.cash_cents += -s * q * px
        led.settle_legs.append((side, s * q))
        led.net_no += no_frame(side, s * q)
        led.fees_usd += float(fill.fee or 0)
        led.fills += 1
        led.sides.add(side)
        if s > 0 and not is_closeout:
            led.entry_contracts += q
    if tags is None:
        orphan = session.scalars(
            select(Fill).where(
                Fill.market_ticker == ticker,
                or_(Fill.kalshi_order_id.is_(None),
                    Fill.kalshi_order_id.notin_(
                        select(LiveOrder.kalshi_order_id).where(
                            LiveOrder.kalshi_order_id.is_not(None)))),
            )
        ).all()
        for fill in orphan:
            led = out[""]
            q = fill_qty(fill)
            led.fills += 1
            led.net_no += no_frame(fill.side, q)
    return dict(out)


def holdings_by_tag(session, ticker: str) -> dict[str, float]:
    """`{base_tag: contracts held}` on one market, from each tag's own fills — the
    per-tag split `positions` cannot give. Positive = NO held. Tags that are flat are
    omitted; fills no order explains are reported under the empty tag."""
    return {t: led.net_no for t, led in ledgers_for_market(session, ticker).items()
            if abs(led.net_no) > DUST}


def committed_cost_usd(session, ticker: str, tags) -> float:
    """Dollars these tags have committed on one market: the fill cost of what they hold,
    plus the unfilled remainder of their working orders at its limit price."""
    from ..models import LiveOrder
    from ..repository import LIVE_NONTERMINAL_STATUSES

    total = 0.0
    for led in ledgers_for_market(session, ticker, tags=set(tags)).values():
        if led.net_no > DUST and led.entry_contracts > 0:
            # Average entry cost per contract, applied to what is still held.
            paid = -led.cash_cents / 100.0 if led.cash_cents < 0 else 0.0
            total += paid * (led.net_no / led.entry_contracts)
    for qty, limit, filled in _working_orders(session, ticker, tags, LiveOrder,
                                              LIVE_NONTERMINAL_STATUSES):
        total += max(0.0, qty - filled) * float(limit or 0) / 100.0
    return total


def committed_contracts(session, ticker: str, tags) -> float:
    """Contracts these tags hold on one market plus the unfilled remainder of their
    working entry orders — what a combined per-market bound is measured against."""
    from ..models import LiveOrder
    from ..repository import LIVE_NONTERMINAL_STATUSES

    held = sum(max(0.0, led.net_no) for led in
               ledgers_for_market(session, ticker, tags=set(tags)).values())
    working = sum(max(0.0, qty - filled) for qty, _limit, filled in
                  _working_orders(session, ticker, tags, LiveOrder,
                                  LIVE_NONTERMINAL_STATUSES))
    return held + working


def _working_orders(session, ticker, tags, LiveOrder, statuses):
    from ..models import Fill

    orders = session.execute(
        select(LiveOrder.kalshi_order_id, LiveOrder.quantity, LiveOrder.limit_price)
        .where(LiveOrder.market_ticker == ticker,
               LiveOrder.strategy.in_(list(tags)),
               LiveOrder.action == "buy",
               LiveOrder.status.in_(statuses))
    ).all()
    out = []
    for koid, qty, limit in orders:
        filled = 0.0
        if koid:
            filled = sum(fill_qty(f) for f in session.scalars(
                select(Fill).where(Fill.kalshi_order_id == koid)).all())
        out.append((float(qty or 0), limit, filled))
    return out


def attributed_market_rows(session, tags: tuple[str, ...], scope) -> dict:
    """`metrics._live_market_rows`, per FILL. Same return shape, so every live provider
    reads it unchanged; the differences are deliberate and listed in the review doc:

      * a market another tag also traded is INCLUDED, priced from this arm's own fills
        (`shared_markets_included` counts them; `contested_markets` is 0 — nothing is
        excluded for being shared);
      * contracts are `count_fp`, not the rounded integer column;
      * a tag that closed out flat is priced without waiting for settlement.
    """
    from ..models import LiveOrder

    tag_set = set(tags)
    mine_rows = session.execute(
        select(LiveOrder.market_ticker, LiveOrder.side)
        .where(
            LiveOrder.strategy.in_(tags),
            LiveOrder.action == "buy",
            LiveOrder.created_at >= scope.window_start,
            LiveOrder.created_at <= scope.window_end,
        )
        .distinct()
    ).all()
    sides: dict[str, set[str]] = {}
    for ticker, side in mine_rows:
        sides.setdefault(ticker, set()).add((side or "").lower())
    mine = set(sides)
    empty = {"markets": 0, "settled_markets": 0, "contracts": 0, "pnl_usd": 0.0,
             "wins": 0, "contested_markets": 0, "open_markets": 0,
             "unpriced_markets": 0, "never_held_markets": 0, "per_market": [],
             "settled_tickers": [], "entered_tickers": [], "sides": {},
             "per_market_by_ticker": [], "shared_markets_included": 0,
             "attribution": "fill"}
    if not mine:
        return empty

    shared = set(session.execute(
        select(LiveOrder.market_ticker)
        .where(LiveOrder.market_ticker.in_(mine),
               LiveOrder.strategy.notin_(list(tag_set | {t + CLOSEOUT_SUFFIX
                                                         for t in tag_set})))
        .distinct()
    ).scalars().all())

    settled: set[str] = set()
    open_markets = unpriced = never_held = wins = 0
    pnl_usd = 0.0
    cents: dict[str, float] = {}
    qty_by: dict[str, float] = {}
    window = (scope.window_start, scope.window_end)
    for ticker in sorted(mine):
        ledgers = ledgers_for_market(session, ticker, tags=tag_set, window=window)
        if not ledgers or all(led.fills == 0 for led in ledgers.values()):
            never_held += 1
            continue
        net = sum(led.net_no for led in ledgers.values())
        st = market_settlement(session, ticker)
        if abs(net) > DUST and st.state == "open":
            open_markets += 1
            continue
        yes_value = st.yes_value if st.state == "settled" else None
        pnls = [led.pnl_usd(yes_value) for led in ledgers.values()]
        if any(p is None for p in pnls):
            if st.state in ("open", "unknown"):
                open_markets += 1
            else:
                unpriced += 1
            continue
        pnl = sum(pnls)
        settled.add(ticker)
        pnl_usd += pnl
        wins += int(pnl > 0)
        cents[ticker] = pnl * 100.0
        qty_by[ticker] = sum(led.entry_contracts for led in ledgers.values())

    contracts = sum(qty_by.values())
    ordered = sorted(settled)
    return empty | {
        "markets": len(mine),
        "settled_markets": len(settled),
        # A count of contracts can be fractional now; integral values stay ints so a
        # 1-lot book's provenance reads exactly as it always has.
        "contracts": int(contracts) if float(contracts).is_integer() else round(contracts, 4),
        "pnl_usd": pnl_usd,
        "wins": wins,
        "open_markets": open_markets,
        "unpriced_markets": unpriced,
        "never_held_markets": never_held,
        "per_market": [(cents[t], qty_by[t]) for t in ordered],
        "settled_tickers": ordered,
        "sides": {t: (next(iter(v)) if len(v) == 1 else None) for t, v in sides.items()},
        "entered_tickers": sorted(mine),
        "per_market_by_ticker": [(t, cents[t], qty_by[t]) for t in ordered],
        "shared_markets_included": len(shared & settled),
    }


@dataclass(frozen=True)
class Reconciliation:
    ticker: str
    per_tag_usd: dict
    attributed_usd: float | None
    position_realized_usd: float | None
    diff_usd: float | None
    ok: bool | None   # None = cannot be checked (not settled / unpriced)
    reason: str = ""


def reconcile_market(session, ticker: str) -> Reconciliation:
    """Sum of per-tag P&L against the account's realized P&L on one settled market."""
    st = market_settlement(session, ticker)
    if st.state != "settled" or st.realized_usd is None:
        return Reconciliation(ticker, {}, None, st.realized_usd, None, None,
                              f"market is {st.state}, not a priced settlement")
    ledgers = ledgers_for_market(session, ticker)
    if "" in ledgers and ledgers[""].fills:
        per = {t: led.pnl_usd(st.yes_value) for t, led in ledgers.items() if t}
        return Reconciliation(
            ticker, per, None, st.realized_usd, None, False,
            f"{ledgers[''].fills} fill(s) on this market match no live order, so its "
            "realized P&L cannot be attributed to tags")
    per = {t: led.pnl_usd(st.yes_value) for t, led in ledgers.items()}
    if any(v is None for v in per.values()):
        return Reconciliation(ticker, per, None, st.realized_usd, None, None,
                              "a tag's P&L could not be priced")
    total = sum(per.values())
    diff = total - st.realized_usd
    return Reconciliation(ticker, {t: round(v, 4) for t, v in per.items()},
                          round(total, 4), round(st.realized_usd, 4), round(diff, 4),
                          abs(diff) <= RECONCILE_TOLERANCE_USD)


def fill_attribution_active(session) -> bool:
    """True once `METRICS_ENGINE:live_fill_attribution_2026_10` is the ACTIVE revision.
    Fails closed to the shipped provider on any read error, so a database hiccup can
    never switch semantics underneath a gate."""
    try:
        from .models import PlatformComponent, PlatformRevision

        found = session.scalar(
            select(PlatformRevision.id)
            .join(PlatformComponent, PlatformComponent.id == PlatformRevision.component_id)
            .where(PlatformComponent.key == COMPONENT,
                   PlatformRevision.version == VERSION,
                   PlatformRevision.status == "active")
            .limit(1)
        )
    except Exception:  # noqa: BLE001
        return False
    return found is not None


def metrics_fingerprint() -> str:
    """Hash of THIS file — the whole provider implementation. A CUTOVER carrying
    `expect_metrics_fingerprint` defers on a worker whose copy differs."""
    from . import service

    path = Path(__file__).resolve()
    return service.canonical_hash({path.name: hashlib.sha256(path.read_bytes()).hexdigest()})


def record_mismatches(session, *, now: datetime | None = None,
                      lookback_days: int = 7) -> list[dict]:
    """Record one non-blocking `LIVE_ATTRIBUTION_MISMATCH` integrity event per
    (experiment, market) whose per-tag P&L does not reconcile. Markets settled in the
    lookback; tags mapped to an OPEN live deployment arm. Never silently dropped,
    never recorded twice while unresolved. Returns what it recorded."""
    from ..models import Position
    from .models import (
        Experiment,
        ExperimentDeployment,
        ExperimentDeploymentArm,
        ExperimentEpoch,
        ExperimentIntegrityEvent,
        ExperimentVersion,
    )
    from .service import record_integrity_event

    at = now or datetime.now(timezone.utc)
    since = at - timedelta(days=lookback_days)
    tickers = sorted(set(session.scalars(
        select(Position.market_ticker).where(Position.captured_at >= since,
                                             Position.quantity == 0)
    ).all()))
    recorded: list[dict] = []
    for ticker in tickers:
        rec = reconcile_market(session, ticker)
        if rec.ok is not False:
            continue
        tags = sorted(t for t in rec.per_tag_usd if t)
        if not tags:
            continue
        deps = session.execute(
            select(ExperimentDeployment, ExperimentDeploymentArm.strategy_tag)
            .join(ExperimentDeploymentArm,
                  ExperimentDeploymentArm.deployment_id == ExperimentDeployment.id)
            .where(ExperimentDeploymentArm.strategy_tag.in_(tags),
                   ExperimentDeployment.kind == "live",
                   ExperimentDeployment.ended_at.is_(None))
        ).all()
        for dep, tag in deps:
            epoch = session.get(ExperimentEpoch, dep.epoch_id)
            version = session.get(ExperimentVersion, epoch.version_id)
            already = [
                e for e in session.scalars(
                    select(ExperimentIntegrityEvent).where(
                        ExperimentIntegrityEvent.experiment_id == version.experiment_id,
                        ExperimentIntegrityEvent.kind == MISMATCH_KIND,
                        ExperimentIntegrityEvent.resolved_at.is_(None),
                    )
                ).all()
                if (e.details_json or {}).get("ticker") == ticker
            ]
            if already:
                continue
            experiment = session.get(Experiment, version.experiment_id)
            details = {
                "ticker": ticker, "tag": tag, "per_tag_usd": rec.per_tag_usd,
                "attributed_usd": rec.attributed_usd,
                "position_realized_usd": rec.position_realized_usd,
                "diff_usd": rec.diff_usd, "tolerance_usd": RECONCILE_TOLERANCE_USD,
                "reason": rec.reason, "revision": REVISION_REF,
            }
            record_integrity_event(
                session, experiment, kind=MISMATCH_KIND, severity="warning",
                detected_at=at, version=version, epoch=epoch, deployment=dep,
                description=(
                    f"{ticker}: per-tag live P&L does not reconcile to the account's "
                    f"realized P&L ({rec.attributed_usd} vs {rec.position_realized_usd}"
                    f"{'; ' + rec.reason if rec.reason else ''}). The metric keeps "
                    f"{tag}'s fill-attributed figure; gate evaluation is NOT blocked."
                ),
                details=details,
            )
            recorded.append(details)
    return recorded


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


__all__ = [
    "COMPONENT", "MISMATCH_KIND", "PROVIDER_REVISION", "RECONCILE_TOLERANCE_USD",
    "REVISION_REF", "VERSION", "Reconciliation", "attributed_market_rows", "base_tag",
    "committed_contracts", "committed_cost_usd", "fill_attribution_active", "fill_qty",
    "holdings_by_tag", "ledgers_for_market", "market_settlement", "metrics_fingerprint",
    "price_in_order_frame", "reconcile_market", "record_mismatches",
]
