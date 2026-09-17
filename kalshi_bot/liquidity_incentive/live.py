"""Phase 1a — the ONE-SIDED live smoke test: which order to place, and whether it may be placed.

This module is the decision half of the smoke test. It places nothing: it returns a
`LiveQuote` (or a refusal reason) and the executor decides what to do with it. Every number it
produces is bounded by the strategy's OWN caps, declared here as module constants that the
Experiment OS risk envelope names and a test asserts equal — so "the registered envelope is the
running envelope" is a property of the code, not a promise.

WHY ONE SIDE, AND WHY THIS SIDE
-------------------------------
Two-sided quoting is the strategy; it is NOT what this test exercises, because the live path
refuses a second resting order on a ticker that already has one (`LiveExecutor` gate 4:
`live_buy_exists_for_ticker(...) or live_open_order_exists(...)`, which is strategy-agnostic).
Making that gate understand a two-sided quote changes shared risk semantics that also guard the
running MMSELL canary — a Platform Change Review, not a thing to slip into a smoke test.

Kalshi scores the YES and NO sides SEPARATELY (`scoring.py` R5), so a single resting bid still
earns liquidity score on its own side. That is enough to prove the whole pipe: order accepted,
rests as genuine liquidity, appears in our own collector's book, earns score, gets credited,
cancels and settles. It is NOT enough to say anything about the strategy's economics, and this
module's output must never be read that way.

**Side choice is the safety lever.** A resting bid's entire downside is the price paid: a NO bid
at 3c risks 3c per contract, a NO bid at 90c risks 90c. So we pick the side whose touch price is
CHEAPEST, and rest AT the touch. Resting at the touch also means sitting at or above the
Reference Price (which is walked down from the best bid), so the distance multiplier is 1.0 and
the order earns full weight for its size — the cheap side is also the efficient side. The one
thing we never do is rest deep for safety: a deep order is discounted to nothing
(`DiscountFactor ** ticks`) and would be liquidity nobody is paying for.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# --- the strategy's own caps. The XOS risk envelope names these; a test asserts they match. ---
#: Contracts per resting order. One. The whole downside of the test is price x this.
MAX_CONTRACTS_PER_ORDER = 1
#: Dollars of collateral per resting order.
MAX_ORDER_DOLLARS = 1.00
#: Resting orders this strategy may hold at once, across all markets.
MAX_OPEN_ORDERS = 3
#: Total dollars this strategy may have committed at once (its own budget, not the shared one).
MAX_STRATEGY_EXPOSURE_USD = 10.00
#: Refuse any order priced above this. Caps the per-contract downside directly, and is the
#: reason the test prefers a market whose cheap side is at the touch.
MAX_PRICE_CENTS = 25
#: A program must still have at least this long to run, so the order can rest and be scored.
MIN_PROGRAM_HOURS_REMAINING = 2.0
#: Prefer a program ending within this many hours: Kalshi credits rewards only AFTER a program
#: ends, so a short program is what makes the payout leg of the test confirmable in days.
PREFER_PROGRAM_ENDS_WITHIN_HOURS = 72.0

#: The live canary's tag, and the paper tag the PAPER stage registers.
#:
#: Three constraints shaped them, all load-bearing:
#:   * FRESH — `arm_live_canary` refuses a tag carrying `paper_trades` rows or an active
#:     deployment arm (the 2026-08-15 Lmmsell lesson);
#:   * PREFIX-SAFE — `LIVE_STRATEGIES` matches by PREFIX, so the live tag must not begin with
#:     the paper tag or an allowlist entry naming the paper book would arm the live one. `A`
#:     is this book's generation marker, as `C` was mmsell10's;
#:   * SHORT ENOUGH for `paper_trades.strategy` (24 chars) once the twin suffix is appended.
PAPER_TAG = "limm1"
LIVE_TAG = "Alimm1"
#: The twin tag is DERIVED at runtime as `<live_tag><LIVE_PAPER_TWIN_SUFFIX>`; production
#: carries `_pt3`. Registering anything else would put a tag in Experiment OS that the twin
#: book never trades under, and under NEW_ONLY its paper rows would be refused.
TWIN_SUFFIX = "_pt3"
TWIN_TAG = LIVE_TAG + TWIN_SUFFIX


def owns_tag(strategy: str | None) -> bool:
    """True for this strategy's own live or twin tag.

    Used by shared live paths that must treat this book differently — today only
    `LiveExecutor.manage_exits`, which must NOT apply the process-wide TP/SL exit rules to a
    book whose registered contract is hold-to-settlement. Deliberately an exact-match over the
    two registered tags rather than a prefix test: a prefix test would silently capture a
    future book whose contract nobody has read."""
    return strategy in (LIVE_TAG, TWIN_TAG)


SIDE_YES = "yes"
SIDE_NO = "no"

# Refusal codes. Each is a reason NOT to place; the caller records them verbatim.
REFUSE_NO_BOOK = "no_book"
REFUSE_NOT_TWO_SIDED = "not_two_sided"
REFUSE_TARGET_NOT_MET = "target_not_met"
REFUSE_TOO_EXPENSIVE = "too_expensive"
REFUSE_PROGRAM_ENDING = "program_ending"
REFUSE_NO_TARGET_SIZE = "no_target_size"
REFUSE_POST_ONLY_CROSS = "post_only_would_cross"
REFUSE_EXCLUDED_SERIES = "excluded_series"
REFUSE_OPEN_ORDER_CAP = "open_order_cap"
REFUSE_EXPOSURE_CAP = "exposure_cap"


@dataclass(frozen=True)
class LiveQuote:
    """One resting post-only bid the smoke test would place. Prices are that side's own cents."""

    market_ticker: str
    side: str                 # "yes" | "no" — the side the bid rests on
    price_cents: int          # what we pay per contract on that side
    quantity: int
    collateral_usd: float     # price x qty: the entire downside if it fills and loses
    max_loss_usd: float       # identical to collateral for a resting bid; named for the record
    reference_price_cents: int | None
    at_or_above_reference: bool
    reason: str               # how the price was chosen (audit)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Refusal:
    code: str
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


def _cheapest_side(best_yes_bid: int | None, best_no_bid: int | None) -> tuple[str, int] | None:
    """The side whose touch is cheaper, and that price. Ties go to NO, arbitrarily but fixedly."""
    if best_yes_bid is None and best_no_bid is None:
        return None
    if best_no_bid is None:
        return SIDE_YES, int(best_yes_bid)
    if best_yes_bid is None:
        return SIDE_NO, int(best_no_bid)
    return (SIDE_YES, int(best_yes_bid)) if int(best_yes_bid) < int(best_no_bid) else (SIDE_NO, int(best_no_bid))


def build_live_quote(
    *,
    market_ticker: str,
    best_yes_bid: int | None,
    best_no_bid: int | None,
    yes_resting_total: float,
    no_resting_total: float,
    target_size: float | None,
    reference_price_by_side: dict[str, int | None] | None = None,
    program_hours_remaining: float | None = None,
    excluded_series: frozenset[str] = frozenset(),
    open_orders_now: int = 0,
    strategy_exposure_now_usd: float = 0.0,
    max_price_cents: int = MAX_PRICE_CENTS,
) -> LiveQuote | Refusal:
    """The whole decision, as one pure function. Returns the order to place, or why not.

    Every refusal is a reason the smoke test declines to spend money; there is deliberately no
    path that returns a quote by relaxing a cap."""
    series = market_ticker.split("-", 1)[0] if market_ticker else ""
    if series and series in excluded_series:
        return Refusal(REFUSE_EXCLUDED_SERIES,
                       f"{series} is reserved for another live book; no ticker collisions")
    if open_orders_now >= MAX_OPEN_ORDERS:
        return Refusal(REFUSE_OPEN_ORDER_CAP,
                       f"{open_orders_now} resting already, cap {MAX_OPEN_ORDERS}")
    if best_yes_bid is None or best_no_bid is None:
        return Refusal(REFUSE_NOT_TWO_SIDED, "a one-sided book cannot produce a qualifying snapshot")
    # Structural validity of the book comes BEFORE any pricing decision: a crossed book means the
    # feed is wrong or stale, and "the cheap side is too expensive" would be the wrong diagnosis.
    if best_yes_bid + best_no_bid > 100:
        return Refusal(REFUSE_POST_ONLY_CROSS,
                       f"crossed book: yes {best_yes_bid} + no {best_no_bid} > 100")
    if target_size is None or target_size <= 0:
        return Refusal(REFUSE_NO_TARGET_SIZE, "program carries no Target Size")
    # Both sides must already meet Target Size or NO snapshot pays anyone (scoring R2) — and we
    # are far too small to carry a side over the line ourselves.
    if yes_resting_total < target_size or no_resting_total < target_size:
        return Refusal(
            REFUSE_TARGET_NOT_MET,
            f"yes {yes_resting_total:.0f} / no {no_resting_total:.0f} vs target {target_size:.0f}")
    if program_hours_remaining is not None and program_hours_remaining < MIN_PROGRAM_HOURS_REMAINING:
        return Refusal(REFUSE_PROGRAM_ENDING,
                       f"{program_hours_remaining:.1f}h left, need {MIN_PROGRAM_HOURS_REMAINING}")

    picked = _cheapest_side(best_yes_bid, best_no_bid)
    if picked is None:
        return Refusal(REFUSE_NO_BOOK, "no best bid on either side")
    side, price = picked
    if price > max_price_cents:
        return Refusal(
            REFUSE_TOO_EXPENSIVE,
            f"cheapest touch is {side} at {price}c, above the {max_price_cents}c downside cap")
    if price < 1:
        return Refusal(REFUSE_NO_BOOK, f"{side} touch at {price}c is not a placeable price")
    # Joining the touch on our own side cannot cross: the opposing side's price lives on the
    # other book, and the yes+no <= 100 identity checked above is what guarantees it.
    qty = min(MAX_CONTRACTS_PER_ORDER, int(MAX_ORDER_DOLLARS * 100 // price))
    if qty < 1:
        return Refusal(REFUSE_TOO_EXPENSIVE,
                       f"{price}c exceeds the ${MAX_ORDER_DOLLARS:.2f} per-order budget")
    collateral = round(price * qty / 100.0, 4)
    if strategy_exposure_now_usd + collateral > MAX_STRATEGY_EXPOSURE_USD:
        return Refusal(
            REFUSE_EXPOSURE_CAP,
            f"${strategy_exposure_now_usd:.2f} committed + ${collateral:.2f} exceeds "
            f"${MAX_STRATEGY_EXPOSURE_USD:.2f}")

    ref = (reference_price_by_side or {}).get(side)
    at_or_above = ref is None or price >= ref
    return LiveQuote(
        market_ticker=market_ticker, side=side, price_cents=price, quantity=qty,
        collateral_usd=collateral, max_loss_usd=collateral,
        reference_price_cents=ref, at_or_above_reference=at_or_above,
        reason=f"joined the cheaper touch ({side} at {price}c); downside is the collateral",
    )


def rank_candidates(candidates: list[dict], *, excluded_series: frozenset[str] = frozenset(),
                    max_price_cents: int = MAX_PRICE_CENTS) -> list[tuple[dict, LiveQuote]]:
    """Every candidate that yields a placeable quote, cheapest downside first, then soonest
    program end (a program that ends sooner pays sooner, which is what the test needs)."""
    out: list[tuple[dict, LiveQuote]] = []
    for c in candidates:
        q = build_live_quote(
            market_ticker=c.get("market_ticker", ""),
            best_yes_bid=c.get("best_yes_bid"), best_no_bid=c.get("best_no_bid"),
            yes_resting_total=float(c.get("yes_resting_total") or 0.0),
            no_resting_total=float(c.get("no_resting_total") or 0.0),
            target_size=c.get("target_size"),
            reference_price_by_side=c.get("reference_price_by_side"),
            program_hours_remaining=c.get("program_hours_remaining"),
            excluded_series=excluded_series, max_price_cents=max_price_cents,
        )
        if isinstance(q, LiveQuote):
            out.append((c, q))
    out.sort(key=lambda cq: (cq[1].collateral_usd,
                             cq[0].get("program_hours_remaining") or 1e9))
    return out
