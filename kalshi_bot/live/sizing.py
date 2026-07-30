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
from datetime import datetime, timedelta, timezone

from .. import repository as repo

# Excludes the current cycle's own candidate-tick insert (recorded moments earlier for the same
# ticker) from being compared against itself in is_hot_entry.
_SAME_CYCLE_BUFFER_SECONDS = 30


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_hot_entry(
    session, ticker: str, current_no_bid: int, *,
    move_cents: int, lookback_minutes: int, now: datetime | None = None,
) -> bool:
    """Has `ticker` moved at least `move_cents` recently enough that this entry should price
    defensively? `move_cents`/`lookback_minutes` are the caller's own book-scoped knobs (e.g.
    mmsell's mmsell_live_hot_market_move_cents/_lookback_minutes) — this module still takes no
    settings object, per the file docstring above.

    Three cases, using the most recent mmsell_candidate_ticks row for this ticker older than a
    few seconds (see _SAME_CYCLE_BUFFER_SECONDS, which keeps this cycle's own just-inserted tick
    from being compared against itself). Currently mmsell-specific (it reads
    mmsell_candidate_ticks); a second book would need its own candidate tape to reuse this as-is.

    1. No tick at all, ever -> NOT hot. A brand-new candidate has no tape to judge by, so it
       behaves like a calm market — the same "don't fire on thin history" convention the anchor
       set's volatility entry gate already uses (tracker.py's _vol_gate_blocks), so a market's
       FIRST entry is never penalized for lacking a history it couldn't have had.
    2. A tick exists but predates `lookback_minutes` -> hot. The ticker was seen before but has
       gone quiet inside the window we'd want a comparison from; that absence is itself the
       signal, not evidence of calm (confirmed live 2026-07-30: KXFEDMENTION-26JUL-PROJ was
       missing from the candidate tape for the entire 32-minute gap while its no-bid moved
       73c -> 94c, then got cross-canceled on re-entry).
    3. A tick exists inside the window -> hot iff the no-bid has moved at least `move_cents`
       since then.

    Read-only and fail-open: a lookup error must never block an entry, so this returns False
    (behave like a calm market) rather than raise."""
    now = now or datetime.now(timezone.utc)
    try:
        tick = repo.latest_candidate_tick_before(
            session, ticker, before=now - timedelta(seconds=_SAME_CYCLE_BUFFER_SECONDS))
    except Exception:  # noqa: BLE001 — a defensive-pricing lookup must never block an entry
        return False
    if tick is None:
        return False
    captured_at = _aware(tick.captured_at)
    lookback_floor = now - timedelta(minutes=lookback_minutes)
    if captured_at is None or captured_at < lookback_floor:
        return True
    if tick.no_bid is None:
        return False
    return abs(int(current_no_bid) - int(tick.no_bid)) >= int(move_cents)


def maker_no_price(
    metrics, no_price: int | None, price_offset_cents: int, *, hot: bool = False,
) -> int | None:
    """The NO price a maker entry rests at: the no-bid (join the queue), improved by
    `price_offset_cents`, never paying through the no-ask.

    Calm entry (the default): `price_offset_cents` is floored at 0 — never worse than joining the
    queue at the no-bid.

    Hot entry (`hot=True`, see is_hot_entry): the caller is expected to pass a defensive offset
    for `price_offset_cents` instead (e.g. mmsell's mmsell_live_hot_market_defensive_offset_cents)
    — typically negative, so this is used AS GIVEN rather than floored at 0, resting BELOW the
    no-bid for headroom against continued momentum, instead of improving into the spread. Still
    enters every candidate exactly as a calm entry would; only the price changes.

    Returns None when the book gives us nothing to price off."""
    base = no_price if no_price is not None else metrics.best_no_bid if metrics else None
    if base is None:
        return None
    price = int(base) + (int(price_offset_cents) if hot else max(0, int(price_offset_cents)))
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
