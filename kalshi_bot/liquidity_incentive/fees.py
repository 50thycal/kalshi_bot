"""Fee rules for the incentive shadow — one rule per market, persisted beside the program.

The arithmetic is Kalshi's published one, `ceil_to_cent(rate × contracts × P × (1−P))`, with a
`round(x, 10)` step before the ceiling so float noise never rounds a 1.75 into 1.76 (the
gallantfox `kalshi-incentives` test anchors are reproduced in the tests). The coefficients
are the repository's own (`paper/engine.py`: taker 0.07, maker 0.0175), and — the part that
matters for a maker strategy — `docs/MMSELL_FEE_RECON.md` measured Kalshi billing ~0.01c per
maker contract on the series MMSELL trades, i.e. effectively **zero maker fees** there.

So the rule carried per market is DATA, resolved from the market object where Kalshi exposes
it and defaulting to "maker 0 / taker 7%" otherwise, with `source` saying which. The economics
layer never assumes zero maker fees silently: the rule is on the row, and the pair edge is
computed with whatever it says.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

TAKER_RATE_DEFAULT = 0.07
MAKER_RATE_DEFAULT = 0.0          # observed: 0.010c/contract on MMSELL series (MMSELL_FEE_RECON)
MAKER_RATE_PUBLISHED = 0.0175     # the published "maker fee" coefficient where a series charges one

SOURCE_DEFAULT = "default_schedule"
SOURCE_MARKET_FIELD = "market_field"
SOURCE_SERIES_OVERRIDE = "series_override"


@dataclass(frozen=True)
class FeeRule:
    maker_rate: float
    taker_rate: float
    source: str
    fee_type: str | None = None          # Kalshi's own label when the market object carries one
    detail: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def roundup_to_cent(amount: float) -> float:
    """Kalshi rounds each fill's fee UP to the next cent; absorb float error first."""
    return math.ceil(round(amount, 10) * 100) / 100


def trade_fee_usd(count: float, price_cents: int, *, rate: float) -> float:
    """Fee in dollars for `count` contracts at `price_cents`, at `rate`, per-fill ceiling."""
    if count <= 0 or price_cents <= 0 or price_cents >= 100:
        return 0.0
    if rate <= 0:
        return 0.0
    p = price_cents / 100.0
    return roundup_to_cent(rate * count * p * (1 - p))


def maker_fee_usd(rule: FeeRule, count: float, price_cents: int) -> float:
    return trade_fee_usd(count, price_cents, rate=rule.maker_rate)


def taker_fee_usd(rule: FeeRule, count: float, price_cents: int) -> float:
    return trade_fee_usd(count, price_cents, rate=rule.taker_rate)


def pair_maker_fee_cents(rule: FeeRule, yes_bid: int, no_bid: int, qty: float = 1.0) -> float:
    """Maker fee in CENTS PER PAIR for a YES bid + NO bid both filling at `qty` per side.
    Computed at the batch size, because the per-fill ceiling amortizes across a clip."""
    if qty <= 0:
        return 0.0
    total = maker_fee_usd(rule, qty, yes_bid) + maker_fee_usd(rule, qty, no_bid)
    return round(total * 100 / qty, 4)


def rule_from_market(market: dict[str, Any] | None, *, series_overrides: dict[str, FeeRule] | None = None,
                     series_ticker: str | None = None) -> FeeRule:
    """Resolve the fee rule for one market.

    Precedence: an explicit series override (operator-maintained, from a reconciled statement)
    > the market object's own fee fields (Kalshi exposes `fee_type` / maker-fee flags on some
    markets; the shapes vary, so every field read is tolerant) > the default schedule."""
    if series_overrides and series_ticker and series_ticker in series_overrides:
        return series_overrides[series_ticker]
    if isinstance(market, dict):
        fee_type = market.get("fee_type")
        maker_flag = market.get("maker_fees") if "maker_fees" in market else market.get("maker_fee")
        maker_rate = None
        if isinstance(maker_flag, bool):
            maker_rate = MAKER_RATE_PUBLISHED if maker_flag else 0.0
        elif isinstance(maker_flag, (int, float)) and not isinstance(maker_flag, bool):
            maker_rate = float(maker_flag)
        if isinstance(fee_type, str) or maker_rate is not None:
            taker_rate = TAKER_RATE_DEFAULT
            if isinstance(fee_type, str) and fee_type.lower() in ("quadratic_with_maker_fees", "maker"):
                maker_rate = MAKER_RATE_PUBLISHED if maker_rate is None else maker_rate
            return FeeRule(
                maker_rate=MAKER_RATE_DEFAULT if maker_rate is None else maker_rate,
                taker_rate=taker_rate, source=SOURCE_MARKET_FIELD,
                fee_type=fee_type if isinstance(fee_type, str) else None,
                detail=f"maker_flag={maker_flag!r}",
            )
    return FeeRule(maker_rate=MAKER_RATE_DEFAULT, taker_rate=TAKER_RATE_DEFAULT, source=SOURCE_DEFAULT,
                   detail="no fee fields on the market object; MMSELL_FEE_RECON measured ~0 maker")
