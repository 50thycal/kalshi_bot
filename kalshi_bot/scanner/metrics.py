"""Order-book parsing and market metric computation.

Kalshi order books contain *resting bids only*, split into `yes` and `no` sides,
each a list of [price_cents, count]. Asks are derived from the opposite side:

    best_yes_ask = 100 - best_no_bid
    best_no_ask  = 100 - best_yes_bid

A one-sided or empty book is treated as not two-sided (illiquid), which downstream
flows reject.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class MarketMetrics:
    ticker: str
    best_yes_bid: int | None
    best_yes_ask: int | None
    best_no_bid: int | None
    best_no_ask: int | None
    midpoint: float | None
    spread: int | None
    depth_at_best_bid: int
    depth_at_best_ask: int
    top_depth: int
    volume: int
    open_interest: int
    last_price: int | None
    time_to_close_seconds: float | None
    liquidity_score: float
    two_sided: bool
    raw_orderbook: dict[str, Any] = field(default_factory=dict)


def parse_dt(value: Any) -> datetime | None:
    """Parse Kalshi timestamps (ISO-8601 string, epoch seconds, or datetime)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _to_count(value: Any) -> int:
    """Parse a contract count: classic int, or new fixed-point string ('13.00' -> 13)."""
    if value is None:
        return 0
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def price_to_cents(value: Any) -> int | None:
    """Parse a price into integer cents.

    Accepts classic integer cents (48) and the new dollar-string form ('0.48' -> 48)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    try:
        return int(round(float(value) * 100))  # dollar string -> cents
    except (TypeError, ValueError):
        return None


def market_volume(market: dict) -> int:
    if market.get("volume") is not None:
        return _to_count(market.get("volume"))
    return _to_count(market.get("volume_fp"))


def market_open_interest(market: dict) -> int:
    if market.get("open_interest") is not None:
        return _to_count(market.get("open_interest"))
    return _to_count(market.get("open_interest_fp"))


def market_last_price(market: dict) -> int | None:
    if market.get("last_price") is not None:
        return price_to_cents(market.get("last_price"))
    return price_to_cents(market.get("last_price_dollars"))


def _levels(side: Any) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for level in side or []:
        try:
            price, count = level[0], level[1]
        except (TypeError, ValueError, IndexError):
            continue
        cents = price_to_cents(price)
        cnt = _to_count(count)
        if cents is not None and cnt > 0:
            out.append((cents, cnt))
    return out


def _best_bid(levels: list[tuple[int, int]]) -> tuple[int, int] | None:
    return max(levels, key=lambda x: x[0]) if levels else None


def orderbook_levels(orderbook: dict) -> tuple[Any, Any]:
    """Return the raw (yes, no) level arrays from either the classic `orderbook`
    ({"yes": ..., "no": ...}) or the new `orderbook_fp` ({"yes_dollars": ...,
    "no_dollars": ...}) wrapper."""
    book = orderbook.get("orderbook") or orderbook.get("orderbook_fp") or orderbook
    yes_raw = book.get("yes")
    if yes_raw is None:
        yes_raw = book.get("yes_dollars")
    no_raw = book.get("no")
    if no_raw is None:
        no_raw = book.get("no_dollars")
    return yes_raw, no_raw


def parse_orderbook(orderbook: dict) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    yes_raw, no_raw = orderbook_levels(orderbook)
    return _levels(yes_raw), _levels(no_raw)


def ask_depth_within(orderbook: dict, max_yes_ask_cents: int) -> int:
    """Cumulative YES contracts buyable at a YES ask <= max_yes_ask_cents.

    Kalshi books hold resting bids only; a YES ask at price p is a resting NO bid at
    100 - p, so YES-ask depth up to a price ceiling is the sum of NO-bid counts at prices
    >= 100 - ceiling. Used to size a bounded-slippage marketable entry to the liquidity
    available within the price band (so the marketable limit fully fills, leaving no
    resting remainder), instead of just the single best-ask level."""
    _yes_levels, no_levels = parse_orderbook(orderbook or {})
    floor_no = 100 - int(max_yes_ask_cents)
    return sum(count for price, count in no_levels if price >= floor_no)



def compute_time_to_close(close_time: Any, now: datetime | None = None) -> float | None:
    dt = parse_dt(close_time)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (dt - now).total_seconds()


def compute_metrics(
    market: dict,
    orderbook: dict,
    *,
    top_n: int = 10,
    now: datetime | None = None,
) -> MarketMetrics:
    yes_levels, no_levels = parse_orderbook(orderbook)
    best_yes = _best_bid(yes_levels)
    best_no = _best_bid(no_levels)

    best_yes_bid = best_yes[0] if best_yes else None
    best_no_bid = best_no[0] if best_no else None
    best_yes_ask = (100 - best_no_bid) if best_no_bid is not None else None
    best_no_ask = (100 - best_yes_bid) if best_yes_bid is not None else None

    two_sided = best_yes_bid is not None and best_yes_ask is not None
    if two_sided:
        midpoint: float | None = (best_yes_bid + best_yes_ask) / 2.0
        spread: int | None = best_yes_ask - best_yes_bid
    else:
        midpoint = None
        spread = None

    depth_at_best_bid = best_yes[1] if best_yes else 0
    depth_at_best_ask = best_no[1] if best_no else 0  # NO-bid depth ≈ YES-ask depth
    top_depth = sum(c for _, c in sorted(yes_levels, key=lambda x: -x[0])[:top_n]) + sum(
        c for _, c in sorted(no_levels, key=lambda x: -x[0])[:top_n]
    )

    volume = market_volume(market)
    open_interest = market_open_interest(market)
    last_price = market_last_price(market)
    ttc = compute_time_to_close(market.get("close_time"), now=now)
    liquidity_score = _liquidity_score(spread, top_depth, volume, open_interest, two_sided)

    return MarketMetrics(
        ticker=market.get("ticker", ""),
        best_yes_bid=best_yes_bid,
        best_yes_ask=best_yes_ask,
        best_no_bid=best_no_bid,
        best_no_ask=best_no_ask,
        midpoint=midpoint,
        spread=spread,
        depth_at_best_bid=depth_at_best_bid,
        depth_at_best_ask=depth_at_best_ask,
        top_depth=top_depth,
        volume=volume,
        open_interest=open_interest,
        last_price=last_price,
        time_to_close_seconds=ttc,
        liquidity_score=liquidity_score,
        two_sided=two_sided,
        raw_orderbook=orderbook,
    )


def _liquidity_score(
    spread: int | None,
    top_depth: int,
    volume: int,
    open_interest: int,
    two_sided: bool,
) -> float:
    """0..100 composite liquidity score. 0 when the book is not two-sided."""
    if not two_sided or spread is None:
        return 0.0
    spread_score = max(0.0, 1.0 - (spread / 20.0))  # 0c spread -> 1.0, 20c -> 0.0
    depth_score = min(1.0, top_depth / 500.0)
    volume_score = min(1.0, volume / 1000.0)
    oi_score = min(1.0, open_interest / 1000.0)
    score = 100.0 * (
        0.35 * spread_score + 0.30 * depth_score + 0.20 * volume_score + 0.15 * oi_score
    )
    return round(score, 2)
