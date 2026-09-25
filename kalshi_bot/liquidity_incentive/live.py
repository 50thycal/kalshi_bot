"""Phase 1b — the TWO-SIDED live book: which pair to quote, and when to get out.

This module is the decision half of the live book. It places nothing: it returns a `PairQuote`
(or a refusal), and, for a position it already holds, the name of the exit rule that fired (or
None). The executor places orders; the runner decides which markets to look at. Every number
here is bounded by the strategy's OWN caps, declared as module constants that the Experiment OS
risk envelope names and a test asserts equal.

WHY TWO SIDES (thesis §9.37)
----------------------------
Phase 1a rested ONE bid on the cheaper side and held it to settlement. A fill left a naked
position riding to $1 or $0. Resting a YES bid at y and a NO bid at n (y + n <= 99) changes that:

  * if BOTH fill, Kalshi nets YES against NO in the same market, so the pair closes itself and
    realises (100 - y - n) cents per contract immediately — whatever the market does next. The
    second leg's fill is, mechanically, a maker exit of the first;
  * if ONE fills, the other is still resting as a take-profit at a fixed, profitable price —
    the "resting sell the moment it fills" — and the exit rules below cap the loss if the market
    runs the other way instead;
  * both legs earn liquidity score while they rest (Kalshi scores YES and NO separately, R5).

Both legs carry the SAME quantity. That is what makes the pair a hedge: unequal quantities
leave a naked residual when both fill. The per-leg dollar cap therefore binds on the dearer leg,
and the pair usually commits less than twice the cap.

WHY EXITS (thesis §9.37)
-----------------------
The operator's two priorities, in order: protect the capital, then get it back out fast enough
to redeploy. A held leg is closed by a marketable order when any of three rules fires:

  * stop-loss   — the held side's bid has fallen a fixed fraction of what we paid;
  * take-profit — the held side's bid has risen a fixed fraction of the remaining upside (the
                  resting opposite leg usually takes profit first, at a smaller edge; this is the
                  backstop when it is not resting);
  * pre-close   — the market closes within the hour. Nothing held here may ride through close
                  into settlement: the three KXBIGGESTQUAKE positions closed on 2026-09-17 and
                  had still not settled eight days later, which is capital doing nothing.

Entries are also refused outside a close-time window, so capital is never committed to a market
that cannot resolve within days.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# --- the strategy's own caps. The XOS risk envelope names these; a test asserts they match. ---
#:
#: Every number in this block is AHEAD of the risk envelope frozen into the deployment's
#: `config_json` at arm time (2026-09-17), by deliberate, repeated operator decision — not
#: through `service.arm_live_canary`, because the lifecycle model has no path back from
#: LIVE_CANARY to PAPER on the same experiment (`docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md` §7:
#: "No silent rollback"). See thesis §9.34 (order count), §9.36 (size) and §9.37 (two sides and
#: exits) for the reasoning each time.
#:
#: Contracts per leg. A ceiling that rarely binds — `MAX_ORDER_DOLLARS` is the one that does.
MAX_CONTRACTS_PER_ORDER = 500
#: Dollars of collateral per LEG. 1.00 -> 20.00 (§9.36, one leg per market) -> 10.00 (§9.37):
#: the operator's "$10 a side, still $20 a position". Both legs share one quantity, so the dearer
#: leg is the one this cap binds.
MAX_ORDER_DOLLARS = 10.00
#: Markets this strategy may be working at once. `repository.count_live_book_open` counts open
#: TICKERS, so a two-legged pair is one. 3 -> 5 (§9.34) -> 2 (§9.36); unchanged by §9.37.
MAX_OPEN_ORDERS = 2
#: Total dollars committed at once. `MAX_OPEN_ORDERS * 2 * MAX_ORDER_DOLLARS` must stay within it.
MAX_STRATEGY_EXPOSURE_USD = 50.00
#: Refuse a leg priced above this. Was the one-sided book's 25c cheap-side cap; a pair always
#: has a dear side, so the cap now bounds each leg instead, keeping a single-leg fill off the
#: near-certain favourites where a fill is almost always the losing side of news.
MAX_PRICE_CENTS = 90
#: A pair must lock at least this much if both legs fill: yes_bid + no_bid <= 100 - edge.
MIN_PAIR_EDGE_CENTS = 1
#: A program must still have at least this long to run, so the order can rest and be scored.
MIN_PROGRAM_HOURS_REMAINING = 2.0

#: The close-time window, in hours from now. Outside it, no entry.
#:   * the ceiling keeps capital out of markets that cannot resolve within days (§9.37: a
#:     72-hour cutoff still leaves ~1,000 of ~6,300 live programs, measured 2026-09-25);
#:   * the floor leaves time to rest before the pre-close flatten below takes the position off.
MAX_HOURS_TO_CLOSE = 72.0
MIN_HOURS_TO_CLOSE = 3.0
#: Flatten anything still held this close to the market's close.
FLATTEN_HOURS_BEFORE_CLOSE = 1.0

#: Exit distances, as fractions, with a floor so a cheap leg is not stopped out by one tick.
#: Stop when the held side's bid is down STOP_LOSS_FRACTION of the entry price; take profit
#: when it is up TAKE_PROFIT_FRACTION of the remaining upside (100 - entry). Pre-registered
#: starting values, chosen before any exit has fired — not tuned to a result.
STOP_LOSS_FRACTION = 0.40
TAKE_PROFIT_FRACTION = 0.40
EXIT_MIN_DISTANCE_CENTS = 3
#: A marketable exit crosses this many cents past the touch so it fills rather than expires.
EXIT_SLIPPAGE_CENTS = 2
#: Exit orders fired at one ticker before giving up and leaving it to settle (and to a human).
EXIT_MAX_ATTEMPTS = 3
#: An exit acts only on a position snapshot at most this old. Kalshi has no reduce-only for
#: these orders, so a marketable exit sent against a position that is already flat OPENS one.
POSITION_FRESH_SECONDS = 300.0

#: Refuse a market whose THINNER side already rests more than this multiple of Target Size.
#:
#: This is the universe rule. Reward share is our size over the competing depth, so a deep book
#: pays a rounding error while the adverse selection is the same (§9.27). 3.0 is the
#: `medium`/`deep` line `scripts/liquidity_incentive_report.py` always drew — chosen before the
#: result, deliberately not tuned to it.
MAX_COMPETING_DEPTH_TARGET_MULTIPLE = 3.0

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

    Used by shared live paths that must treat this book differently — today
    `LiveExecutor.manage_exits`, whose process-wide TP/SL rules must never touch this book: it
    manages its own exits (`decide_exit`, run by the incentive runner) with rules registered for
    it, on both YES and NO legs, which the process-wide path cannot close. Exact-match over the
    two registered tags: a prefix test would silently capture a future book nobody has read."""
    return strategy in (LIVE_TAG, TWIN_TAG)


SIDE_YES = "yes"
SIDE_NO = "no"


def other_side(side: str) -> str:
    return SIDE_NO if side == SIDE_YES else SIDE_YES


# Refusal codes. Each is a reason NOT to place; the caller records them verbatim.
REFUSE_NO_BOOK = "no_book"
REFUSE_NOT_TWO_SIDED = "not_two_sided"
REFUSE_TARGET_NOT_MET = "target_not_met"
REFUSE_TOO_EXPENSIVE = "too_expensive"
REFUSE_PROGRAM_ENDING = "program_ending"
REFUSE_NO_TARGET_SIZE = "no_target_size"
REFUSE_BOOK_TOO_DEEP = "book_too_deep"
REFUSE_POST_ONLY_CROSS = "post_only_would_cross"
REFUSE_NO_EDGE = "no_pair_edge"
REFUSE_NO_CLOSE_TIME = "no_close_time"
REFUSE_CLOSES_TOO_SOON = "closes_too_soon"
REFUSE_CLOSES_TOO_LATE = "closes_too_late"
REFUSE_EXCLUDED_SERIES = "excluded_series"
REFUSE_EVENT_CAP = "event_cap"
REFUSE_OPEN_ORDER_CAP = "open_order_cap"
REFUSE_EXPOSURE_CAP = "exposure_cap"

# Exit rules. Each names why a held leg is being closed; the executor records it on the order.
EXIT_STOP_LOSS = "stop_loss"
EXIT_TAKE_PROFIT = "take_profit"
EXIT_PRE_CLOSE = "pre_close"


@dataclass(frozen=True)
class LiveQuote:
    """One resting post-only bid on one side. Prices are that side's own cents."""

    market_ticker: str
    side: str                 # "yes" | "no" — the side the bid rests on
    price_cents: int          # what we pay per contract on that side
    quantity: int
    collateral_usd: float     # price x qty: this leg's entire downside if it fills alone and loses
    max_loss_usd: float       # identical to collateral for a resting bid; named for the record
    reference_price_cents: int | None
    at_or_above_reference: bool
    reason: str               # how the price was chosen (audit)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PairQuote:
    """A YES bid and a NO bid of equal quantity on one market."""

    market_ticker: str
    yes: LiveQuote
    no: LiveQuote
    quantity: int
    edge_cents: int              # 100 - yes - no: locked per contract if both legs fill
    collateral_usd: float        # both legs, as committed while both rest
    max_loss_usd: float          # the dearer leg alone, filled and lost — the single-leg worst case
    hours_to_close: float | None

    @property
    def legs(self) -> tuple[LiveQuote, LiveQuote]:
        return (self.yes, self.no)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Refusal:
    code: str
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)


def pair_quantity(yes_price: int, no_price: int) -> int:
    """The one quantity both legs carry: as many contracts as the DEARER leg's budget buys."""
    dearer = max(int(yes_price), int(no_price))
    if dearer < 1:
        return 0
    return min(MAX_CONTRACTS_PER_ORDER, int(MAX_ORDER_DOLLARS * 100 // dearer))


def _leg(ticker: str, side: str, price: int, qty: int, ref: int | None) -> LiveQuote:
    collateral = round(price * qty / 100.0, 4)
    return LiveQuote(
        market_ticker=ticker, side=side, price_cents=price, quantity=qty,
        collateral_usd=collateral, max_loss_usd=collateral,
        reference_price_cents=ref, at_or_above_reference=(ref is None or price >= ref),
        reason=f"joined the {side} touch at {price}c as one leg of a pair",
    )


def build_pair_quote(
    *,
    market_ticker: str,
    best_yes_bid: int | None,
    best_no_bid: int | None,
    yes_resting_total: float,
    no_resting_total: float,
    target_size: float | None,
    hours_to_close: float | None,
    reference_price_by_side: dict[str, int | None] | None = None,
    program_hours_remaining: float | None = None,
    excluded_series: frozenset[str] = frozenset(),
    event_ticker: str | None = None,
    blocked_event_tickers: frozenset[str] = frozenset(),
    open_orders_now: int = 0,
    strategy_exposure_now_usd: float = 0.0,
    max_price_cents: int = MAX_PRICE_CENTS,
) -> PairQuote | Refusal:
    """The whole entry decision, as one pure function. Returns the pair to place, or why not.

    There is deliberately no path that returns a quote by relaxing a cap."""
    series = market_ticker.split("-", 1)[0] if market_ticker else ""
    if series and series in excluded_series:
        return Refusal(REFUSE_EXCLUDED_SERIES,
                       f"{series} is reserved for another live book; no ticker collisions")
    # Concentration before pricing: several markets of one event resolve together, so stacking
    # them is one bet wearing several tickets (§9.15).
    if event_ticker and event_ticker in blocked_event_tickers:
        return Refusal(REFUSE_EVENT_CAP, f"{event_ticker} already carries an open live commitment")
    if open_orders_now >= MAX_OPEN_ORDERS:
        return Refusal(REFUSE_OPEN_ORDER_CAP,
                       f"{open_orders_now} markets open already, cap {MAX_OPEN_ORDERS}")
    # The close-time window comes before the book: it is the capital-recycling rule, and a market
    # that cannot resolve within days is refused whatever its book looks like.
    if hours_to_close is None:
        return Refusal(REFUSE_NO_CLOSE_TIME, "market carries no close time")
    if hours_to_close < MIN_HOURS_TO_CLOSE:
        return Refusal(REFUSE_CLOSES_TOO_SOON,
                       f"closes in {hours_to_close:.1f}h, need {MIN_HOURS_TO_CLOSE:g}h")
    if hours_to_close > MAX_HOURS_TO_CLOSE:
        return Refusal(REFUSE_CLOSES_TOO_LATE,
                       f"closes in {hours_to_close:.1f}h, cap {MAX_HOURS_TO_CLOSE:g}h")
    if best_yes_bid is None or best_no_bid is None:
        return Refusal(REFUSE_NOT_TWO_SIDED, "a one-sided book cannot produce a qualifying snapshot")
    y, n = int(best_yes_bid), int(best_no_bid)
    # A crossed book means the feed is wrong or stale; say so rather than blame the price.
    if y + n > 100:
        return Refusal(REFUSE_POST_ONLY_CROSS, f"crossed book: yes {y} + no {n} > 100")
    if target_size is None or target_size <= 0:
        return Refusal(REFUSE_NO_TARGET_SIZE, "program carries no Target Size")
    # Both sides must already meet Target Size or no snapshot pays anyone (scoring R2).
    if yes_resting_total < target_size or no_resting_total < target_size:
        return Refusal(
            REFUSE_TARGET_NOT_MET,
            f"yes {yes_resting_total:.0f} / no {no_resting_total:.0f} vs target {target_size:.0f}")
    competing_depth = min(yes_resting_total, no_resting_total)
    depth_cap = MAX_COMPETING_DEPTH_TARGET_MULTIPLE * target_size
    if competing_depth > depth_cap:
        return Refusal(
            REFUSE_BOOK_TOO_DEEP,
            f"thinner side rests {competing_depth:.0f} vs {depth_cap:.0f} "
            f"({MAX_COMPETING_DEPTH_TARGET_MULTIPLE:g}x target {target_size:.0f}); "
            f"our share would round to nothing")
    if program_hours_remaining is not None and program_hours_remaining < MIN_PROGRAM_HOURS_REMAINING:
        return Refusal(REFUSE_PROGRAM_ENDING,
                       f"{program_hours_remaining:.1f}h left, need {MIN_PROGRAM_HOURS_REMAINING}")
    if y < 1 or n < 1:
        return Refusal(REFUSE_NO_BOOK, f"touch yes {y} / no {n} is not a placeable pair")
    edge = 100 - y - n
    if edge < MIN_PAIR_EDGE_CENTS:
        return Refusal(REFUSE_NO_EDGE, f"yes {y} + no {n} leaves {edge}c, need {MIN_PAIR_EDGE_CENTS}c")
    if max(y, n) > max_price_cents:
        return Refusal(REFUSE_TOO_EXPENSIVE,
                       f"dear leg at {max(y, n)}c, above the {max_price_cents}c per-leg cap")
    qty = pair_quantity(y, n)
    if qty < 1:
        return Refusal(REFUSE_TOO_EXPENSIVE,
                       f"{max(y, n)}c exceeds the ${MAX_ORDER_DOLLARS:.2f} per-leg budget")
    refs = reference_price_by_side or {}
    yes_leg = _leg(market_ticker, SIDE_YES, y, qty, refs.get(SIDE_YES))
    no_leg = _leg(market_ticker, SIDE_NO, n, qty, refs.get(SIDE_NO))
    collateral = round(yes_leg.collateral_usd + no_leg.collateral_usd, 4)
    if strategy_exposure_now_usd + collateral > MAX_STRATEGY_EXPOSURE_USD:
        return Refusal(
            REFUSE_EXPOSURE_CAP,
            f"${strategy_exposure_now_usd:.2f} committed + ${collateral:.2f} exceeds "
            f"${MAX_STRATEGY_EXPOSURE_USD:.2f}")
    return PairQuote(
        market_ticker=market_ticker, yes=yes_leg, no=no_leg, quantity=qty, edge_cents=edge,
        collateral_usd=collateral,
        max_loss_usd=max(yes_leg.collateral_usd, no_leg.collateral_usd),
        hours_to_close=hours_to_close,
    )


# ------------------------------------------------------------------ exits


def stop_distance_cents(entry_cents: int) -> int:
    return max(EXIT_MIN_DISTANCE_CENTS, round(int(entry_cents) * STOP_LOSS_FRACTION))


def take_profit_distance_cents(entry_cents: int) -> int:
    return max(EXIT_MIN_DISTANCE_CENTS, round((100 - int(entry_cents)) * TAKE_PROFIT_FRACTION))


def decide_exit(*, entry_cents: int, mark_bid_cents: int | None,
                hours_to_close: float | None) -> str | None:
    """Which exit rule fires for one held leg, or None to keep holding.

    `mark_bid_cents` is the best bid on the HELD side, in that side's own cents — what the leg
    could be sold for now. Pre-close is checked first and needs no mark: a position about to
    close must come off whatever the price, or it rides into settlement."""
    if hours_to_close is not None and hours_to_close <= FLATTEN_HOURS_BEFORE_CLOSE:
        return EXIT_PRE_CLOSE
    if mark_bid_cents is None:
        return None
    entry = int(entry_cents)
    mark = int(mark_bid_cents)
    if mark <= entry - stop_distance_cents(entry):
        return EXIT_STOP_LOSS
    if mark >= entry + take_profit_distance_cents(entry):
        return EXIT_TAKE_PROFIT
    return None


def exit_leg_price(*, entry_cents: int, best_opposite_bid: int | None) -> int | None:
    """The resting bid on the OPPOSITE side that closes a held leg at a profit when it fills.

    Buying the opposite side nets the held one flat, so a bid at p realises
    (100 - entry - p) per contract. Join the opposite touch, but never pay more than leaves
    MIN_PAIR_EDGE_CENTS. None when no profitable price exists."""
    cap = 100 - int(entry_cents) - MIN_PAIR_EDGE_CENTS
    price = cap if best_opposite_bid is None else min(int(best_opposite_bid), cap)
    return price if price >= 1 else None


# ------------------------------------------------------------------ ranking


def _depth_ratio(candidate: dict) -> float:
    """Competing depth on the thinner side, as a multiple of Target Size. Lower is better: reward
    share is our size over the competing depth. Only orders WITHIN the allowed band — it can never
    admit a book the gate refused."""
    target = float(candidate.get("target_size") or 0.0)
    if target <= 0:
        return float("inf")
    depth = min(float(candidate.get("yes_resting_total") or 0.0),
                float(candidate.get("no_resting_total") or 0.0))
    return depth / target


def quote_candidate(c: dict, *, excluded_series: frozenset[str] = frozenset(),
                    blocked_event_tickers: frozenset[str] = frozenset(),
                    max_price_cents: int = MAX_PRICE_CENTS) -> PairQuote | Refusal:
    """`build_pair_quote` over one runner candidate dict."""
    return build_pair_quote(
        market_ticker=c.get("market_ticker", ""),
        best_yes_bid=c.get("best_yes_bid"), best_no_bid=c.get("best_no_bid"),
        yes_resting_total=float(c.get("yes_resting_total") or 0.0),
        no_resting_total=float(c.get("no_resting_total") or 0.0),
        target_size=c.get("target_size"),
        hours_to_close=c.get("hours_to_close"),
        reference_price_by_side=c.get("reference_price_by_side"),
        program_hours_remaining=c.get("program_hours_remaining"),
        excluded_series=excluded_series, max_price_cents=max_price_cents,
        event_ticker=c.get("event_ticker"),
        blocked_event_tickers=blocked_event_tickers,
    )


def rank_candidates(candidates: list[dict], *, excluded_series: frozenset[str] = frozenset(),
                    blocked_event_tickers: frozenset[str] = frozenset(),
                    max_price_cents: int = MAX_PRICE_CENTS) -> list[tuple[dict, PairQuote]]:
    """Every candidate that yields a placeable pair, best first.

    Order: THINNEST competing book (reward share per contract, §9.27), then SOONEST close
    (capital comes back to be redeployed sooner), then WIDEST edge (more locked if both fill).
    Price is no longer a ranking key: a pair always holds both sides, so "the cheap side" is not
    a choice this book makes, and the dollar downside of a single-leg fill is capped per leg."""
    out: list[tuple[dict, PairQuote]] = []
    for c in candidates:
        q = quote_candidate(c, excluded_series=excluded_series,
                            blocked_event_tickers=blocked_event_tickers,
                            max_price_cents=max_price_cents)
        if isinstance(q, PairQuote):
            out.append((c, q))
    out.sort(key=lambda cq: (_depth_ratio(cq[0]),
                             cq[1].hours_to_close if cq[1].hours_to_close is not None else 1e9,
                             -cq[1].edge_cents))
    return out
