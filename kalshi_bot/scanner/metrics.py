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


def _levels(side: Any) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for level in side or []:
        try:
            price, count = int(level[0]), int(level[1])
        except (TypeError, ValueError, IndexError):
            continue
        if count > 0:
            out.append((price, count))
    return out


def _best_bid(levels: list[tuple[int, int]]) -> tuple[int, int] | None:
    return max(levels, key=lambda x: x[0]) if levels else None


def parse_orderbook(orderbook: dict) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    book = orderbook.get("orderbook", orderbook) or {}
    return _levels(book.get("yes")), _levels(book.get("no"))


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

    volume = int(market.get("volume") or 0)
    open_interest = int(market.get("open_interest") or 0)
    last_price = market.get("last_price")
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
        last_price=int(last_price) if last_price is not None else None,
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
