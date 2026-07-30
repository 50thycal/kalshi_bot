"""Live entry price + size arithmetic — ONE definition, shared by the real executor and the
paper twin that shadows it.

The live/paper parallel-run harness (docs/LIVE_PAPER_TWIN.md) is only meaningful if the twin
prices and sizes its paper entries exactly the way the live book does; the moment the two
formulas drift the parity report starts measuring our own bookkeeping instead of the market. So
both callers import from here rather than re-deriving the arithmetic.

Both functions take the book's price-offset/dollar-cap/contract-cap as REQUIRED explicit
arguments — deliberately not a `settings` object read internally. With two live books now
sharing this module (mmsell, theta), an implicit `settings.mmsell_live_*` read would let a
caller that forgot to pass its own book's knobs silently inherit another book's live sizing
instead of failing loudly at the call site. Each book's `mirror_*_entry` reads its own
`<book>_live_*` config and passes the values in.
"""

from __future__ import annotations

import math


def maker_no_price(
    metrics, no_price: int | None, price_offset_cents: int
) -> int | None:
    """The NO price a maker entry rests at: the no-bid (join the queue), improved by
    `price_offset_cents`, never paying through the no-ask. Returns None when the book gives us
    nothing to price off."""
    base = no_price if no_price is not None else metrics.best_no_bid if metrics else None
    if base is None:
        return None
    price = int(base) + max(0, int(price_offset_cents))
    ask = getattr(metrics, "best_no_ask", None) if metrics is not None else None
    if ask is not None:
        price = min(price, int(ask))
    return max(1, min(99, price))


def order_quantity(price_cents: int, max_order_dollars: float, max_contracts: int) -> int:
    """Contracts for one live entry: the per-order dollar cap divided by the price, floored to
    whole contracts and capped by the book's hard contract-count guard. 0 means "too expensive
    to size" and the caller must place nothing."""
    if price_cents <= 0:
        return 0
    qty = math.floor(float(max_order_dollars) / (price_cents / 100.0))
    return max(0, min(qty, int(max_contracts)))
