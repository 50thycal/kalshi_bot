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

import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

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
#: 2 -> 10 (§9.56, operator 2026-10-15: "go full send … the 100 dollar limit"), with the
#: universe narrowed to event-tied mention markets (§9.55) and one ~$10 bid per market.
MAX_OPEN_ORDERS = 10
#: Total dollars committed at once (resting bids + held legs; exit legs excluded, §9.53).
#: 50 -> 100 (§9.56). One-sided since §9.48, so `MAX_OPEN_ORDERS * MAX_ORDER_DOLLARS` fits.
MAX_STRATEGY_EXPOSURE_USD = 100.00
#: Markets of ONE event this book may rest in at once. Was 1 (§9.15: several markets of one event
#: resolve together). 1 -> 3 (§9.56): a mention event is ~16-40 separate word markets, each its
#: own ~$100 reward pool, and one-per-event left the book unable to use more than 2-4 slots.
#: The shared risk — every word of one speech is said in the same hour — is what the §9.55
#: pre-event cancel removes; a held POSITION on an event still blocks further entries there.
MAX_MARKETS_PER_EVENT = 3
#: Refuse a leg priced above this. Was the one-sided book's 25c cheap-side cap; a pair always
#: has a dear side, so the cap bounds each leg. 90 -> 5 (§9.41, operator decision 2026-09-29):
#: only cheap-both-sides pairs. The lopsided pairs the 90c cap admitted (gas at 10c/28c NO legs)
#: were ~$12.6 of the book's ~$16 realized loss while earning cents of reward; a 5c leg bounds a
#: lone-leg loss to 5c a contract. 5 -> 10 (§9.46, operator decision 2026-10-03): ~87% of
#: refusals were too_expensive and the book sat idle; dollars per leg ($10) and the $50 budget are
#: unchanged, so a lone-leg loss is still at most $10.
MAX_PRICE_CENTS = 10
#: A pair must lock at least this much if both legs fill: yes_bid + no_bid <= 100 - edge.
MIN_PAIR_EDGE_CENTS = 1
#: A program must still have at least this long to run, so the order can rest and be scored.
#: 2 -> 24 (§9.42): Kalshi pays nothing on a program whose reward is under $1.00, so a quote
#: needs a day of resting to be worth placing at all.
MIN_PROGRAM_HOURS_REMAINING = 24.0

#: The close-time window, in hours from now. Outside it, no entry.
#:   * 3–72h (§9.37) -> 7–60 days (§9.42, operator decision 2026-09-29). Near resolution is when
#:     news lands and a cheap leg gets filled just before it loses (ANTH, GOOG, both FX pairs);
#:     a market weeks from resolving barely moves, so a lone fill is rarer and less one-sided.
#:   * the ceiling still keeps capital out of markets that cannot resolve within two months.
MAX_HOURS_TO_CLOSE = 1440.0
MIN_HOURS_TO_CLOSE = 168.0
#: Flatten anything still held this close to the market's close.
FLATTEN_HOURS_BEFORE_CLOSE = 1.0

#: Replace-only-when-better (§9.47, operator decision 2026-10-04: "remove the timeout, but only
#: for this experiment"). This book's resting pairs are exempt from the executor's 4h timeout —
#: every other book keeps it. A pair that is still resting is still earning, and on 2026-10-04
#: the timeout cancelled both pairs with nothing to replace them. Instead, once a resting pair is
#: older than STALE_PAIR_SECONDS and every slot is full, it is cancelled only when a new
#: candidate's estimated reward per hour beats the held pair's by REPLACE_MIN_GAIN_MULTIPLE (a
#: held pair whose programme has ended is worth 0/h).
#:
#: Stay to the end (§9.49, operator decision 2026-10-07). Kalshi pays a programme only after it
#: ends and only if our reward in it is at least $1.00, so leaving a programme early forfeits
#: everything accrued there. The 4h / 1.25x rule churned ~15 times in 30h (stints of 4-5h) and
#: left programmes like KXGEMINI-NEXTPRO before they could pay; both credits the book has ever
#: received ($2.21 Sep 29, $2.75 Oct 6) came from programmes it stayed in until they ended.
#: Now a held quote is replaced only when it is DEAD: its estimated reward per hour is below
#: DEAD_QUOTE_FRACTION of the best new candidate's (book moved away, programme ended). A quote
#: that has merely fallen behind its own market's reference price is re-priced IN PLACE
#: (same market, same programme) rather than abandoned. STALE_PAIR_SECONDS is now only how long
#: a quote must rest before either check may touch it, so a momentary book does not move it.
STALE_PAIR_SECONDS = 1 * 3600.0
DEAD_QUOTE_FRACTION = 0.10
#: Rank new entries by projected payout over what is left of the programme (est reward per
#: hour x hours remaining), and skip any whose projection is under this — a programme that cannot
#: plausibly clear Kalshi's $1.00 minimum pays nothing however long we rest there (§9.49).
MIN_PROJECTED_PROGRAM_USD = 1.00

#: §9.55 (operator decision 2026-10-15): the universe is MENTION markets tied to ONE scheduled
#: event (a rally, a broadcast, an earnings call) — "will X be said during <event>". Over the
#: book's life they paid $5.40 of $10.91 confirmed and filled once in 48 orders, while the
#: markets that track a moving number (gas, FX, approval, Billboard) filled ~7 times and paid
#: nothing. "Will Trump say X before <date>" windows are OUT: the word can come at any moment, so
#: there is no "right before" to step aside. Supply is ample (~66 event-tied mention events, ~3,000
#: markets in the 35 days to 2026-10-15) for a two-slot book.
EVENT_MENTION_ONLY = True
#: A resting bid is cancelled this long before its event starts, so the event cannot fill it.
PRE_EVENT_CUTOFF_MINUTES = 15
#: When the programme does not end at the event (it runs to market close), the event's hour is
#: unknown; step aside at 07:00 ET (11:00Z) on the event's date — before a pre-market earnings call
#: and well before an evening rally or broadcast.
EVENT_DATE_FALLBACK_HOUR_UTC = 11
#: A programme ending at least this long before the market closes is taken to end AT the event
#: (Kalshi stops paying when the event starts; the market stays open for resolution).
PROGRAM_ENDS_AT_EVENT_MIN_GAP_HOURS = 1.0
#: No new entry with less than this left before its pre-event cutoff: too little time to earn.
MIN_HOURS_BEFORE_EVENT = 6.0
#: A held quote is never swapped for a better market before it has rested this long (§9.55; the
#: book swapped gas -> IASAND -> gas inside two hours on 2026-10-14, resetting time-in-book).
MIN_HOLD_SECONDS = 24 * 3600.0
#: §9.57 (operator, 2026-10-16): a resting bid whose side's reference price has moved ABOVE
#: MAX_PRICE_CENTS scores ~nothing (DiscountFactor per tick below the reference) and cannot be
#: re-priced inside the cap. Once it has been out of the band continuously for this long — checked
#: every cycle (~2 min), a return inside the band resets the clock — it is cancelled to free the
#: slot, without waiting for MIN_HOLD_SECONDS.
OUT_OF_BAND_EXIT_SECONDS = 3600.0
#: The clock only runs on evidence: if the last out-of-band read is older than this (books
#: unreadable, or the worker down across a restart), the hour starts again. Wider than one cycle
#: (~2 min) plus a typical redeploy (~5 min), so a routine restart resumes the clock (§9.58).
OUT_OF_BAND_MAX_GAP_SECONDS = 600.0

_EVENT_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})$")
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def event_date(event_ticker: str | None):
    """The event's date from a Kalshi event ticker ending in YYMMMDD (KXTRUMPMENTION-26OCT16 ->
    2026-10-16), else None."""
    m = _EVENT_DATE_RE.search((event_ticker or "").upper())
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return datetime(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)),
                        tzinfo=timezone.utc).date()
    except ValueError:
        return None


def is_event_mention(series: str | None, event_ticker: str | None, title: str | None) -> bool:
    """A mention market tied to one dated event — not a "say X before <date>" window."""
    if "MENTION" not in (series or "").upper():
        return False
    if " before " in f" {(title or '').lower()} ":
        return False
    return event_date(event_ticker) is not None


def pre_event_cutoff(*, series: str | None, event_ticker: str | None, title: str | None,
                     program_end: datetime | None, close_time: datetime | None):
    """When a resting bid in this market must be gone, or None when it is not an event-tied
    mention market (and so is outside the §9.55 universe).

    A programme that ends well before the market closes ends AT the event: cut off
    PRE_EVENT_CUTOFF_MINUTES before it. Otherwise the event's hour is unknown and the cutoff is
    EVENT_DATE_FALLBACK_HOUR_UTC on the date in the event ticker."""
    if not is_event_mention(series, event_ticker, title):
        return None
    if program_end is not None and close_time is not None and (
            close_time - program_end) >= timedelta(hours=PROGRAM_ENDS_AT_EVENT_MIN_GAP_HOURS):
        return program_end - timedelta(minutes=PRE_EVENT_CUTOFF_MINUTES)
    d = event_date(event_ticker)
    return datetime(d.year, d.month, d.day, EVENT_DATE_FALLBACK_HOUR_UTC, tzinfo=timezone.utc)

#: §9.63 (operator, 2026-10-19): entries step aside this long before the pre-event cutoff, not
#: just at it. 5 of the 6 mention-market fills since 2026-10-13 came within ~2 days of the event
#: (−$19.6 of −$26.5). Unfilled bids are cancelled and no new bid is placed inside the window; a
#: held leg's resting sells still run to `pre_event_cutoff`.
ENTRY_STEP_ASIDE_HOURS = 48.0
#: §9.63: after one of our bids fills, our other resting bids in the same event and on the same
#: word in other events are cancelled, and no new bid goes there, for this long. A fill is the
#: sign of a seller who knows something (NQE then FENT, one event, 4h apart; CEAS Oct 19 then
#: CEAS Oct 25, one word, a day apart).
FILL_CONTAGION_HOURS = 12.0


def entry_cutoff(**kw):
    """§9.63: the last moment a bid may rest or be placed — `pre_event_cutoff` less
    ENTRY_STEP_ASIDE_HOURS. None outside the event-mention universe."""
    cutoff = pre_event_cutoff(**kw)
    return None if cutoff is None else cutoff - timedelta(hours=ENTRY_STEP_ASIDE_HOURS)


def market_word(ticker: str | None, event_ticker: str | None) -> str | None:
    """The word a mention market is about — the ticker's suffix after its event
    (`KXTRUMPMENTION-26OCT19-CEAS` -> `CEAS`). None when the ticker is not `<event>-<word>`."""
    t, ev = (ticker or ""), (event_ticker or "")
    if ev and t.startswith(ev + "-") and len(t) > len(ev) + 1:
        return t[len(ev) + 1:].upper()
    return None


#: How an entry is quoted (§9.48, operator decision 2026-10-04, probe LIMM-PLACEMENT P1):
#:   "cheap_side" — ONE bid, on the side whose reference price is <= MAX_PRICE_CENTS (the
#:                  cheaper if both), AT that reference price, so it scores full credit;
#:   "pair"       — the §9.37–§9.47 two-sided pair, one tick behind each touch.
#: Read at call time, so a test can switch it.
QUOTE_MODE = "cheap_side"

#: Exit distances. Stop when the held side's bid is down STOP_LOSS_FRACTION of the entry price
#: (rounded up, at least STOP_MIN_DISTANCE_CENTS); take profit when it is up TAKE_PROFIT_CENTS.
#: 0.40 / floor 3 -> 0.50 / floor 1 (§9.48, protection "B", operator decision 2026-10-04): the
#: old 3c floor never stopped a 1–3c leg at all. §9.53 (operator decision 2026-10-13, "2C"): the
#: §9.48 scratch at entry is retired and the take-profit is a fixed +5c, so a fill now ends at
#: +5c or the stop instead of at about zero or the stop.
STOP_LOSS_FRACTION = 0.50
STOP_MIN_DISTANCE_CENTS = 1
TAKE_PROFIT_CENTS = 5
#: The opposite-side exit bid nets a held leg at entry + at least this many cents: the same +5c as
#: TAKE_PROFIT_CENTS, so the resting exit and the marketable take-profit lock the same edge
#: (§9.48: 1 -> 0, a scratch; §9.53: 0 -> 5).
EXIT_LEG_EDGE_CENTS = TAKE_PROFIT_CENTS
#: Most cash a resting exit leg may tie up (price x size). Kalshi reserves the full cost of a
#: resting buy even when it would only net a held position flat, so an 51c x499 exit on a 1c leg
#: held ~$254 and every entry after it was refused for insufficient balance (§9.54). Above this
#: no exit leg rests; the leg is closed by the take-profit sell, the stop or the pre-close flatten.
MAX_EXIT_LEG_DOLLARS = MAX_ORDER_DOLLARS
#: A marketable exit crosses this many cents past the touch so it fills rather than expires.
EXIT_SLIPPAGE_CENTS = 2
#: §9.60 (operator, 2026-10-17): a held leg rests a post-only SELL of exactly the held contracts
#: at the current best ask — joining the sellers — instead of the opposite-side exit leg. Re-priced
#: to the ask, never at or below the stop level; pulled at the pre-event cutoff; cancelled before
#: any marketable stop / take-profit / pre-close exit. Off by setting this False.
RESTING_SELL_ENABLED = True
#: A resting sell is not moved to a new ask until it has rested this long (no churn on a flicker).
#: A sell larger than the position left is cancelled at once, whatever its age.
RESTING_SELL_REPRICE_MIN_SECONDS = 300.0
#: Client-order-id prefix of a §9.60 resting sell (`LiveExecutor.place_incentive_resting_sell`).
RESTING_SELL_PREFIX = "limmsell:"
#: §9.61 (operator, 2026-10-17): the held contracts rest as TWO sells — half (rounded up) at the
#: other sellers' best ask ("a"), the rest at break-even, max(entry, that ask) ("b") — tagged by
#: role after the prefix (`limmsell:a:` / `limmsell:b:`).
RESTING_SELL_ROLE_ASK = "a"
RESTING_SELL_ROLE_ENTRY = "b"
#: §9.61: in the last this-many hours before the pre-event cutoff both sells step down in a
#: straight line — "a" from the ask to the floor, "b" down to "a" — so a slow market still has a
#: chance to fill before the position must ride into the event.
RESTING_SELL_DECAY_HOURS = 6.0
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
#:
#: Raised 3.0 -> 10.0 by the operator (§9.45). Since §9.42 we rest one tick BEHIND the touch, so
#: a deep book now puts other orders between us and a sweep instead of adding risk, and 3x left
#: nearly every weeks-out market refused. 10x still guarantees a share of at least ~9%.
MAX_COMPETING_DEPTH_TARGET_MULTIPLE = 10.0

#: Rest this many ticks BEHIND the touch rather than at it (§9.42). The orders at the touch are
#: the ones a sweep hits first, so sitting one tick back leaves them as a buffer. Applied only
#: when the depth already at the touch is under Target Size — Kalshi scores only the orders that
#: fill Target Size from the best price down, so behind a touch that alone meets it we would
#: score nothing and join the touch instead. The cost is at most one tick of discount factor.
QUOTE_TICKS_BEHIND_TOUCH = 1

#: Freshness buckets for ranking (§9.42): programs pay most in their first hours and decay
#: steeply after, so a newer program outranks a thinner book within the same bucket width.
PROGRAM_AGE_BUCKET_HOURS = 12.0

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
#: The executor's outcome for a market on a non-default matching-engine shard (§9.45). Such an
#: order is answered 409 and never rests, so the runner cools the market down instead of retrying.
GATE_NON_DEFAULT_SHARD = "gate:shard"
#: Kalshi refused an entry for lack of free cash. The runner stops placing for
#: BALANCE_BACKOFF_SECONDS rather than trying every remaining candidate into the same refusal.
REJECT_INSUFFICIENT_BALANCE = "rejected:insufficient_balance"
BALANCE_BACKOFF_SECONDS = 30 * 60
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
#: Retired by §9.53 (no rule returns it); kept so rows recorded under §9.48 still read.
EXIT_SCRATCH = "scratch"


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
class SideQuote:
    """ONE bid on one side of one market (§9.48, QUOTE_MODE "cheap_side")."""

    market_ticker: str
    leg: LiveQuote
    quantity: int
    collateral_usd: float
    max_loss_usd: float
    hours_to_close: float | None

    @property
    def legs(self) -> tuple[LiveQuote]:
        return (self.leg,)

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
        reason=f"rested the {side} bid at {price}c as one leg of a pair",
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
    yes_touch_depth: float | None = None,
    no_touch_depth: float | None = None,
) -> PairQuote | Refusal:
    """The whole entry decision, as one pure function. Returns the pair to place, or why not.

    There is deliberately no path that returns a quote by relaxing a cap."""
    refused = _entry_refusal(
        market_ticker=market_ticker, best_yes_bid=best_yes_bid, best_no_bid=best_no_bid,
        yes_resting_total=yes_resting_total, no_resting_total=no_resting_total,
        target_size=target_size, hours_to_close=hours_to_close,
        program_hours_remaining=program_hours_remaining, excluded_series=excluded_series,
        event_ticker=event_ticker, blocked_event_tickers=blocked_event_tickers,
        open_orders_now=open_orders_now)
    if refused is not None:
        return refused
    y, n = int(best_yes_bid), int(best_no_bid)
    y = behind_touch_price(y, yes_touch_depth, target_size)
    n = behind_touch_price(n, no_touch_depth, target_size)
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


def build_side_quote(
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
) -> SideQuote | Refusal:
    """§9.48: one bid on the cheap side, AT that side's reference price (full scoring credit).

    Same gates as the pair (window, two-sided book meeting Target Size, depth rule, programme
    time, concentration). The side is the one whose reference price is <= `max_price_cents`,
    the cheaper if both; quantity is the most the $ budget buys at that price, capped."""
    refused = _entry_refusal(
        market_ticker=market_ticker, best_yes_bid=best_yes_bid, best_no_bid=best_no_bid,
        yes_resting_total=yes_resting_total, no_resting_total=no_resting_total,
        target_size=target_size, hours_to_close=hours_to_close,
        program_hours_remaining=program_hours_remaining, excluded_series=excluded_series,
        event_ticker=event_ticker, blocked_event_tickers=blocked_event_tickers,
        open_orders_now=open_orders_now)
    if refused is not None:
        return refused
    refs = reference_price_by_side or {}
    cands = []
    for side in (SIDE_YES, SIDE_NO):
        ref = refs.get(side)
        if ref is not None and 1 <= int(ref) <= int(max_price_cents):
            cands.append((int(ref), side))
    if not cands:
        known = {s: refs.get(s) for s in (SIDE_YES, SIDE_NO)}
        return Refusal(REFUSE_TOO_EXPENSIVE,
                       f"no side's reference price is within {max_price_cents}c ({known})")
    price, side = min(cands)
    qty = min(MAX_CONTRACTS_PER_ORDER, int(MAX_ORDER_DOLLARS * 100 // price))
    if qty < 1:
        return Refusal(REFUSE_TOO_EXPENSIVE,
                       f"{price}c exceeds the ${MAX_ORDER_DOLLARS:.2f} per-leg budget")
    collateral = round(price * qty / 100.0, 4)
    if strategy_exposure_now_usd + collateral > MAX_STRATEGY_EXPOSURE_USD:
        return Refusal(
            REFUSE_EXPOSURE_CAP,
            f"${strategy_exposure_now_usd:.2f} committed + ${collateral:.2f} exceeds "
            f"${MAX_STRATEGY_EXPOSURE_USD:.2f}")
    leg = LiveQuote(
        market_ticker=market_ticker, side=side, price_cents=price, quantity=qty,
        collateral_usd=collateral, max_loss_usd=collateral, reference_price_cents=price,
        at_or_above_reference=True,
        reason=f"rested the cheap {side} side at its {price}c reference price",
    )
    return SideQuote(market_ticker=market_ticker, leg=leg, quantity=qty,
                     collateral_usd=collateral, max_loss_usd=collateral,
                     hours_to_close=hours_to_close)


def _entry_refusal(
    *, market_ticker: str, best_yes_bid: int | None, best_no_bid: int | None,
    yes_resting_total: float, no_resting_total: float, target_size: float | None,
    hours_to_close: float | None, program_hours_remaining: float | None,
    excluded_series: frozenset[str], event_ticker: str | None,
    blocked_event_tickers: frozenset[str], open_orders_now: int,
) -> Refusal | None:
    """The gates every entry shares, pair or single side, in order. None when all pass."""
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
    return None


def behind_touch_price(touch: int, touch_depth: float | None, target_size: float | None) -> int:
    """Where to rest one side: `QUOTE_TICKS_BEHIND_TOUCH` behind the touch when that still
    scores, else at the touch. Unknown depth joins the touch — never guess a scoring position."""
    behind = int(touch) - QUOTE_TICKS_BEHIND_TOUCH
    if behind < 1 or touch_depth is None or target_size is None:
        return int(touch)
    if float(touch_depth) >= float(target_size):
        return int(touch)
    return behind


# ------------------------------------------------------------------ exits


def stop_distance_cents(entry_cents: int) -> int:
    return max(STOP_MIN_DISTANCE_CENTS, math.ceil(int(entry_cents) * STOP_LOSS_FRACTION))


def take_profit_distance_cents(entry_cents: int) -> int:
    return TAKE_PROFIT_CENTS


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
    return None   # §9.53: no scratch at entry — hold for the take-profit or the stop


def exit_leg_affordable(price_cents: int, qty: int) -> bool:
    """Whether an exit leg at this price and size stays within MAX_EXIT_LEG_DOLLARS (§9.54)."""
    return int(price_cents) * int(qty) / 100.0 <= MAX_EXIT_LEG_DOLLARS + 1e-9


def exit_leg_price(*, entry_cents: int, best_opposite_bid: int | None) -> int | None:
    """The resting bid on the OPPOSITE side that closes a held leg at a profit when it fills.

    Buying the opposite side nets the held one flat, so a bid at p realises
    (100 - entry - p) per contract. Join the opposite touch, but never pay more than leaves
    EXIT_LEG_EDGE_CENTS (+5c since §9.53). None when no such price exists."""
    cap = 100 - int(entry_cents) - EXIT_LEG_EDGE_CENTS
    price = cap if best_opposite_bid is None else min(int(best_opposite_bid), cap)
    return price if price >= 1 else None


def resting_sell_price(*, entry_cents: int, held_bid_cents: int | None,
                       best_other_ask_cents: int | None) -> int | None:
    """§9.60: where a resting sell of a held leg goes, in the held side's own cents.

    Join the best ask of the OTHER sellers (`best_other_ask_cents`, our own sell taken out of the
    book). With no other seller, ask entry + TAKE_PROFIT_CENTS. Never at or below the stop level
    (the stop sells the leg there anyway) and never at or below the held side's best bid, which a
    post-only order would cross. None when no such price exists (e.g. a 99c bid)."""
    entry = int(entry_cents)
    price = (entry + TAKE_PROFIT_CENTS if best_other_ask_cents is None
             else int(best_other_ask_cents))
    floor = entry - stop_distance_cents(entry) + 1
    if held_bid_cents is not None:
        floor = max(floor, int(held_bid_cents) + 1)
    price = max(price, floor)
    return price if 1 <= price <= 99 else None


def _half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def resting_sell_prices(*, entry_cents: int, held_bid_cents: int | None,
                        best_other_ask_cents: int | None,
                        hours_to_cutoff: float | None) -> tuple[int, int] | None:
    """§9.61: the (ask-role, break-even-role) prices of a held leg's two resting sells.

    Outside the last RESTING_SELL_DECAY_HOURS before the cutoff: "a" at `resting_sell_price`,
    "b" at max(entry, a). Inside it both step down linearly with time — "a" towards the floor
    (above the stop and the held bid), "b" towards "a" — reaching them at the cutoff. None when
    no valid price exists."""
    a = resting_sell_price(entry_cents=entry_cents, held_bid_cents=held_bid_cents,
                           best_other_ask_cents=best_other_ask_cents)
    if a is None:
        return None
    entry = int(entry_cents)
    floor = entry - stop_distance_cents(entry) + 1
    if held_bid_cents is not None:
        floor = max(floor, int(held_bid_cents) + 1)
    b = max(entry, a)
    w = 0.0
    if hours_to_cutoff is not None and hours_to_cutoff < RESTING_SELL_DECAY_HOURS:
        w = min(1.0, max(0.0, 1.0 - hours_to_cutoff / RESTING_SELL_DECAY_HOURS))
    a = max(floor, a - _half_up((a - floor) * w))
    b = max(a, b - _half_up((b - a) * w))
    return min(a, 99), min(b, 99)


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
                    max_price_cents: int = MAX_PRICE_CENTS) -> PairQuote | SideQuote | Refusal:
    """The entry decision over one runner candidate dict, in the current QUOTE_MODE."""
    if QUOTE_MODE == "cheap_side":
        return build_side_quote(
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
        yes_touch_depth=c.get("yes_touch_depth"), no_touch_depth=c.get("no_touch_depth"),
    )


def _age_bucket(candidate: dict) -> float:
    """Program age in `PROGRAM_AGE_BUCKET_HOURS` buckets; unknown age sorts last."""
    age = candidate.get("program_age_hours")
    if age is None:
        return float("inf")
    return float(max(0.0, float(age)) // PROGRAM_AGE_BUCKET_HOURS)


def rank_candidates(candidates: list[dict], *, excluded_series: frozenset[str] = frozenset(),
                    blocked_event_tickers: frozenset[str] = frozenset(),
                    max_price_cents: int = MAX_PRICE_CENTS) -> list[tuple[dict, PairQuote]]:
    """Every candidate that yields a placeable pair, best first.

    Order (§9.42): FRESHEST program (reward yield decays steeply with program age), then
    THINNEST competing book (reward share per contract, §9.27), then WIDEST edge. Soonest close
    is no longer a key: near resolution is where lone fills lose.
    Price is no longer a ranking key: a pair always holds both sides, so "the cheap side" is not
    a choice this book makes, and the dollar downside of a single-leg fill is capped per leg."""
    out: list[tuple[dict, PairQuote | SideQuote]] = []
    for c in candidates:
        q = quote_candidate(c, excluded_series=excluded_series,
                            blocked_event_tickers=blocked_event_tickers,
                            max_price_cents=max_price_cents)
        if isinstance(q, (PairQuote, SideQuote)):
            out.append((c, q))

    def last_key(q) -> int:
        # Pair: widest edge first. Single side: cheapest first (smallest loss per contract).
        return -q.edge_cents if isinstance(q, PairQuote) else q.leg.price_cents

    out.sort(key=lambda cq: (_age_bucket(cq[0]), _depth_ratio(cq[0]), last_key(cq[1])))
    return out
