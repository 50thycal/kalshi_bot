"""Live entry price + size arithmetic — ONE definition, shared by the real executor and the
paper twin that shadows it.

The live/paper parallel-run harness (docs/LIVE_PAPER_TWIN.md) is only meaningful if the twin
prices and sizes its paper entries exactly the way the live book does; the moment the two
formulas drift the parity report starts measuring our own bookkeeping instead of the market. So
both callers import from here rather than re-deriving the arithmetic.

Every function here takes the book's knobs — price offset, dollar/contract caps, hot-market
thresholds, and the tape reader — as REQUIRED explicit arguments, deliberately not a `settings`
object read internally and with no defaults. With two live books sharing this module (mmsell,
theta), an implicit `settings.mmsell_live_*` read would let a caller that forgot to pass its own
book's knobs silently inherit another book's live sizing instead of failing loudly at the call
site. `is_hot_entry`'s `lookup` is required for exactly the same reason: each book keeps its own
quote tape (mmsell_candidate_ticks vs crypto_ladder_snapshots), and defaulting it would let theta
silently measure volatility against mmsell's markets. Each book's `mirror_*_entry` reads its own
`<book>_live_*` config and passes the values in.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone

# Excludes the current cycle's own tape row (written moments earlier for the same ticker) from
# being compared against itself in is_hot_entry.
_SAME_CYCLE_BUFFER_SECONDS = 30


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_hot_entry(
    session, ticker: str, current_no_bid: int, *,
    move_cents: int, lookback_minutes: int, lookup, now: datetime | None = None,
) -> bool:
    """Has `ticker` moved at least `move_cents` recently enough that this entry should price
    defensively?

    `move_cents`/`lookback_minutes`/`lookup` are all the caller's own book-scoped choices (see the
    file docstring). `lookup` is `(session, ticker, *, before) -> (no_bid_cents, captured_at)`,
    returning `(None, None)` when the market has no prior quote — `repository`'s
    `latest_mmsell_no_bid_before` / `latest_theta_no_bid_before`. Keeping the tape behind that
    callable is what lets this stay a pure decision function shared by both books.

    Three cases, judged against the most recent quote older than a few seconds (see
    _SAME_CYCLE_BUFFER_SECONDS, which keeps this cycle's own just-written row from being compared
    against itself):

    1. No prior quote at all -> NOT hot. A brand-new candidate has no tape to judge by, so it
       behaves like a calm market — the same "don't fire on thin history" convention the anchor
       set's volatility entry gate already uses (mmsell tracker's _vol_gate_blocks), so a market's
       FIRST entry is never penalized for lacking a history it couldn't have had.
    2. A quote exists but predates `lookback_minutes` -> hot. The ticker was seen before but has
       gone quiet inside the window we'd want a comparison from; that absence is itself the
       signal, not evidence of calm (confirmed live 2026-07-30: KXFEDMENTION-26JUL-PROJ was
       missing from the candidate tape for the entire 32-minute gap while its no-bid moved
       73c -> 94c, then got cross-canceled on re-entry).
    3. A quote exists inside the window -> hot iff the no-bid has moved at least `move_cents`
       since then.

    Read-only and fail-open: a lookup error must never block an entry, so this returns False
    (behave like a calm market) rather than raise."""
    now = now or datetime.now(timezone.utc)
    try:
        prior_no_bid, prior_at = lookup(
            session, ticker, before=now - timedelta(seconds=_SAME_CYCLE_BUFFER_SECONDS))
    except Exception:  # noqa: BLE001 — a defensive-pricing lookup must never block an entry
        return False
    if prior_at is None:
        return False
    captured_at = _aware(prior_at)
    if captured_at < now - timedelta(minutes=lookback_minutes):
        return True
    if prior_no_bid is None:
        return False
    return abs(int(current_no_bid) - int(prior_no_bid)) >= int(move_cents)


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


def offset_arm(ticker: str, *, arms, salt: str) -> tuple[int, int]:
    """(arm_index, offset_cents) for one ticker in the randomized queue-position A/B
    (docs/MMSELL_OFFSET_AB.md).

    Assignment is a deterministic hash of (salt, ticker), NOT a random draw, for three reasons:

    1. A ticker keeps ONE arm for its entire life, so the entry-retry path (mmsell tracker's
       _maybe_retry_live re-posts up to mmsell_live_max_attempts_per_ticker times) cannot flip
       arms mid-market and blend the two prices into an uninterpretable average.
    2. The analysis recomputes the assignment from the ticker alone, so attributing a fill to an
       arm needs no new column and no join — see scripts/mmsell_offset_ab.py.
    3. Re-running the study on the same salt reproduces exactly the same split.

    Hashing the TICKER rather than the event is deliberate: mmsell rests one order per ticker and
    several tickers of one event are often candidates at once, so per-ticker assignment keeps the
    arms balanced inside an event instead of correlating a whole ladder into a single arm.
    """
    arms = tuple(arms)
    if not arms:
        raise ValueError("offset_arm requires at least one arm")
    digest = hashlib.sha256(f"{salt}:{ticker}".encode()).digest()
    idx = int.from_bytes(digest[:8], "big") % len(arms)
    return idx, int(arms[idx])


def arm_book_offset(ticker: str, abarm, *, arms, salt: str) -> int | None:
    """The offset a per-arm BOOK should use for this ticker, or None when the ticker belongs to a
    different arm and this book must skip it entirely.

    This is the two-book form of the queue-position experiment (docs/MMSELL_OFFSET_AB.md): rather
    than one book splitting its own orders, two separate live books each claim the tickers of one
    arm. That gives each arm its own strategy tag, its own paper twin and its own P&L line, while
    the hash — not book evaluation order — decides who gets which ticker.

    Why the hash has to do the deciding: `repository.live_open_order_exists` is strategy-AGNOSTIC,
    so whichever book is evaluated first claims a ticker and blocks the rest. Without per-arm
    assignment the two books would be split by scan order, not at random, and the comparison would
    be worthless. With it, no ticker is ever contested and the two books are disjoint by
    construction — which also means they never queue against each other for the same fill.

    Returns None (skip) when the experiment is off, when this book declares no arm, or when the
    ticker hashes to another arm."""
    if abarm is None or not tuple(arms):
        return None
    idx, offset = offset_arm(ticker, arms=arms, salt=salt)
    return offset if idx == int(abarm) else None


def maker_offset(
    ticker: str, *, hot: bool, calm_offset: int, hot_offset: int, ab_arms=(), ab_salt: str = "",
) -> tuple[int, int | None]:
    """The price offset this maker entry rests at, plus the A/B arm it belongs to (None = not in
    the experiment). One definition, called by BOTH the live executor and the paper twin, for the
    same reason maker_no_price is shared: the moment the two derive the price differently the
    parity report starts measuring our own bookkeeping.

    A HOT entry is always priced defensively AND excluded from the experiment (arm None): its
    offset is chosen by the momentum guard, not by the arm, so counting it in either arm would
    measure the guard rather than queue position. With `ab_arms` empty the A/B is off entirely and
    this returns exactly today's behaviour."""
    if hot:
        return int(hot_offset), None
    if not tuple(ab_arms):
        return int(calm_offset), None
    idx, off = offset_arm(ticker, arms=ab_arms, salt=ab_salt)
    return off, idx


def order_quantity(price_cents: int, max_order_dollars: float, max_contracts: int) -> int:
    """Contracts for one live entry: the per-order dollar cap divided by the price, floored to
    whole contracts and capped by the book's hard contract-count guard. 0 means "too expensive
    to size" and the caller must place nothing."""
    if price_cents <= 0:
        return 0
    qty = math.floor(float(max_order_dollars) / (price_cents / 100.0))
    return max(0, min(qty, int(max_contracts)))
