"""Phase 1b — the live RUNNER: the one cycle that turns decisions into orders.

`live.py` decides which pair to quote and when a held leg must come off; `LiveExecutor` places
and cancels. This module runs between them once per live cycle, in two halves:

  1. MANAGE what is held — for each market this book has filled on, apply the exit rules
     (stop-loss, take-profit, pre-close flatten) or, when none fires and nothing is resting,
     rest an opposite-side bid that closes the position at a profit when it fills;
  2. QUOTE new pairs — pick candidate markets inside the close-time window, read their books
     fresh, rank the pairs, and place up to the market cap, mirroring each leg to the paper TWIN.

WHY IT READS BOOKS OVER REST RATHER THAN FROM THE SHADOW COLLECTOR
------------------------------------------------------------------
The shadow collector (`collector.py`) holds live WebSocket books, but it runs wherever
`LIQUIDITY_INCENTIVE_SHADOW_ENABLED` is set — today the `evo` service, deliberately, so a
research instrument never shares a process with real money. So this runner does a bounded
number of plain GETs (`LIQUIDITY_INCENTIVE_LIVE_MAX_BOOK_FETCHES`) on its own candidates, plus
one per held market.

WHEN THIS RUNNER CANCELS
------------------------
Only on the way OUT of a position: before a marketable exit (a resting leg that filled after the
exit would open a fresh position) and after a completed round trip (whatever still rests could
only reopen one). It never pulls a quote because it might trade — a quote pulled the moment it
might trade is not liquidity, and this strategy's premise is that it is.

It is INERT by default: `LIQUIDITY_INCENTIVE_LIVE_ENABLED` is off, and even on, the executor
refuses every order unless the tag is in `LIVE_STRATEGIES` with both master switches set.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .. import models as m
from .. import repository as repo
from ..scanner.metrics import parse_orderbook
from . import live as limm
from . import programs as progs
from . import reward_ledger as rl
from . import store
from .readonly import IncentiveReadOnlyKalshi
from .scoring import SCORING_VERSION, discount_factor, estimate, side_score

logger = logging.getLogger(__name__)

#: Outcome codes recorded per candidate, so a cycle that placed nothing still says why.
PLACED = "placed"
SKIP_BOOK_ERROR = "book_error"
SKIP_NO_SLOTS = "no_slots"
#: A placeable candidate whose projected programme payout is under limm.MIN_PROJECTED_PROGRAM_USD.
SKIP_LOW_PROJECTED = "low_projected_payout"

#: A market whose BOOK refused a pair is skipped for this long before its book is fetched again.
#: Without it the bounded fetch budget is spent on the same first-ranked books every cycle: on
#: 2026-09-30 the eight newest programmes were all `book_too_deep`, so each cycle fetched those
#: eight, refused all eight, and never reached the other ~1,150 (§9.43; §9.39 was the same shape).
BOOK_REFUSAL_COOLDOWN_SECONDS = 3600.0
#: §9.69: how long a market's estimated reward rate orders the fetches before it is re-explored.
REWARD_MEMORY_SECONDS = 6 * 3600.0
#: The cooldown map is also written to `system_events` (at most this often) and read back on the
#: first cycle after a restart, so a redeploy does not send the fetch budget back to the top of
#: the ranking (§9.44). Production redeploys several times a day as other work merges.
COOLDOWN_COMPONENT = "limm_book_cooldown"
COOLDOWN_PERSIST_EVERY_SECONDS = 300.0
#: §9.58: the out-of-band clock is persisted the same way, so a restart resumes it.
OUT_OF_BAND_COMPONENT = "limm_out_of_band"
OUT_OF_BAND_PERSIST_EVERY_SECONDS = 120.0
BOOK_REFUSAL_CODES = frozenset({
    limm.REFUSE_NO_BOOK, limm.REFUSE_NOT_TWO_SIDED, limm.REFUSE_TARGET_NOT_MET,
    limm.REFUSE_TOO_EXPENSIVE, limm.REFUSE_BOOK_TOO_DEEP, limm.REFUSE_POST_ONLY_CROSS,
    limm.REFUSE_NO_EDGE,
})

#: A balance reading whose unexplained remainder is worth a human look (see `reward_ledger`).
EV_REWARD_RESIDUAL = "reward_residual"


#: Client-order-id prefix of the resting opposite-side exit leg (`place_incentive_exit_leg`).
EXIT_LEG_PREFIX = "limmexitleg:"
#: §9.70: orders that only CLOSE a held position — never an entry bid.
_CLOSING_PREFIXES = ("limmexit", "limmsell:")

#: How often the book snapshots the markets it rests or holds in, for the dashboard (§9.53).
OWN_BOOK_SNAPSHOT_SECONDS = 600.0


#: §9.60: the resting sell's off switch, persisted (see `_switch_off_resting_sells`).
RESTING_SELL_OFF_COMPONENT = "limm_resting_sell_off"
#: §9.60 sweep log: an own market's bid side losing at least this many contracts between two
#: reads, from at or above the old best bid, with the best bid falling — recorded, never acted on.
SWEEP_MIN_CONTRACTS = 150.0
#: Follow-up reads of a recorded sweep, minutes after it (the best bid then, for recovery).
SWEEP_FOLLOWUP_MINUTES = (10, 30, 60)
EV_BOOK_SWEEP = "book_sweep"
EV_BOOK_SWEEP_FOLLOWUP = "book_sweep_followup"


def _is_resting_sell(row) -> bool:
    return (row.client_order_id or "").startswith(limm.RESTING_SELL_PREFIX)


def _sell_role(row) -> str | None:
    """§9.61: "a" (at the ask) or "b" (break-even) from a resting sell's client order id; None
    for a §9.60 sell placed before the split."""
    rest = (row.client_order_id or "")[len(limm.RESTING_SELL_PREFIX):]
    role = rest.split(":", 1)[0] if ":" in rest else None
    return role if role in (limm.RESTING_SELL_ROLE_ASK, limm.RESTING_SELL_ROLE_ENTRY) else None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _entry_cutoff(row) -> datetime | None:
    """§9.63: `_pre_event_cutoff` less ENTRY_STEP_ASIDE_HOURS — when a resting BID steps aside."""
    cutoff = _pre_event_cutoff(row)
    return None if cutoff is None else cutoff - timedelta(hours=limm.ENTRY_STEP_ASIDE_HOURS)


def _pre_event_cutoff(row) -> datetime | None:
    """`live.pre_event_cutoff` for a programme row (§9.55)."""
    return limm.pre_event_cutoff(
        series=row.series_ticker or (row.market_ticker or "").split("-", 1)[0],
        event_ticker=row.event_ticker, title=row.market_title,
        program_end=_aware(row.end_date), close_time=_aware(row.close_time))


def candidate_from_book(program, orderbook: dict, *, now: datetime) -> dict:
    """One `rank_candidates` input built from a program row and its fresh book.

    Prices stay NATIVE per side — yes cents on the yes book, no cents on the no book — which is
    both what Kalshi's REST orderbook returns and what the scoring rules are written in. The
    reference price per side comes from `scoring.side_score`, so the runner and the shadow
    instrument compute it with the same function rather than two readings of the same rule."""
    yes_levels, no_levels = parse_orderbook(orderbook or {})
    yes_map = {int(p): float(q) for p, q in yes_levels}
    no_map = {int(p): float(q) for p, q in no_levels}
    target = float(program.target_size) if program.target_size is not None else None
    disc = discount_factor(program.discount_factor_bps)
    ys = side_score(yes_map, target, disc)
    ns = side_score(no_map, target, disc)
    end = _aware(program.end_date)
    hours_left = None if end is None else (end - now).total_seconds() / 3600.0
    close = _aware(program.close_time)
    hours_to_close = None if close is None else (close - now).total_seconds() / 3600.0
    start = _aware(program.start_date)
    age = None if start is None else (now - start).total_seconds() / 3600.0
    return {
        "market_ticker": program.market_ticker,
        "program_row_id": program.id,
        "event_ticker": program.event_ticker,
        "best_yes_bid": max(yes_map) if yes_map else None,
        "best_no_bid": max(no_map) if no_map else None,
        "yes_resting_total": ys.resting_total,
        "no_resting_total": ns.resting_total,
        "target_size": target,
        "reference_price_by_side": {
            limm.SIDE_YES: ys.reference_price,
            limm.SIDE_NO: ns.reference_price,
        },
        "program_hours_remaining": hours_left,
        "hours_to_close": hours_to_close,
        # Depth AT the touch per side decides whether resting one tick behind still scores.
        "yes_touch_depth": yes_map[max(yes_map)] if yes_map else None,
        "no_touch_depth": no_map[max(no_map)] if no_map else None,
        "program_age_hours": age,
        # What `est_reward_per_hour` needs: the full book and the programme's pool (§9.47).
        "yes_levels": yes_map,
        "no_levels": no_map,
        "discount_factor_bps": program.discount_factor_bps,
        "period_reward_usd": (float(program.period_reward_usd)
                              if program.period_reward_usd is not None else None),
        "period_seconds": (None if start is None or end is None
                           else (end - start).total_seconds()),
    }


def est_reward_per_hour(candidate: dict, *, yes_price: int | None, no_price: int | None,
                        qty: float, own_yes: dict[int, float] | None = None,
                        own_no: dict[int, float] | None = None) -> float:
    """Estimated reward dollars per hour for resting `qty` on both sides at these prices — the
    programme's pool times our share of the scored book (`scoring.estimate`). `own_*` are this
    book's OWN resting orders already in the levels, removed first so a held pair is not
    counted twice. 0.0 when the programme pays nothing we can estimate."""
    if candidate.get("period_reward_usd") is None or not candidate.get("period_seconds"):
        return 0.0

    def _without(levels: dict | None, own: dict | None) -> dict:
        out = dict(levels or {})
        for price, q in (own or {}).items():
            left = out.get(price, 0.0) - q
            if left > 1e-9:
                out[price] = left
            else:
                out.pop(price, None)
        return out

    est = estimate(
        yes_levels=_without(candidate.get("yes_levels"), own_yes),
        no_levels=_without(candidate.get("no_levels"), own_no),
        target_size=candidate.get("target_size"),
        discount_factor_bps=candidate.get("discount_factor_bps"),
        our_yes_price=None if yes_price is None else int(yes_price),
        our_yes_size=0.0 if yes_price is None else float(qty),
        our_no_price=None if no_price is None else int(no_price),
        our_no_size=0.0 if no_price is None else float(qty),
        period_reward_usd_value=candidate.get("period_reward_usd"),
        period_seconds=candidate.get("period_seconds"))
    return float(est.reward_per_hour_usd or 0.0)


def quote_rate(cq) -> float:
    """Estimated reward per hour for a ranked (candidate, quote) as it would rest."""
    c, q = cq
    prices = {leg.side: leg.price_cents for leg in q.legs}
    return est_reward_per_hour(c, yes_price=prices.get(limm.SIDE_YES),
                               no_price=prices.get(limm.SIDE_NO), qty=q.quantity)


def projected_payout(cq) -> float:
    """Estimated reward over what is left of the programme if the quote rests to the end."""
    hours_left = cq[0].get("program_hours_remaining")
    if hours_left is None or hours_left <= 0:
        return 0.0
    return quote_rate(cq) * float(hours_left)


def rank_by_projected_payout(ranked: list) -> tuple[list, list]:
    """§9.49: order entries by projected payout to programme end, best first, and drop those
    projected under `MIN_PROJECTED_PROGRAM_USD` (Kalshi pays nothing under $1.00 per programme).
    Returns (kept, dropped); the sort is stable, so ties keep `rank_candidates`' order."""
    scored = [(projected_payout(cq), cq) for cq in ranked]
    kept = [cq for p, cq in sorted(scored, key=lambda t: -t[0])
            if p >= limm.MIN_PROJECTED_PROGRAM_USD]
    dropped = [cq for p, cq in scored if p < limm.MIN_PROJECTED_PROGRAM_USD]
    return kept, dropped


class IncentiveLiveRunner:
    """One cycle of the two-sided live book: manage what is held, then quote new pairs."""

    def __init__(self, client, settings, twin_harness=None):
        self.client = client
        self.settings = settings
        self.twin_harness = twin_harness
        #: When the reward ledger last took a reading. None until the first cycle, which is what
        #: makes the very first cycle after a deploy write the anchor row immediately.
        self._last_balance_at: datetime | None = None
        #: When this book last snapshotted the books it rests or holds in (`_snapshot_own_books`).
        self._last_book_snapshot_at: datetime | None = None
        #: Tickers already reported as past their exit-attempt cap, so the log says it once.
        self._exit_exhausted: set[str] = set()
        #: Ticker -> when its book may be fetched again, after the book refused a pair.
        self._book_refused_until: dict[str, datetime] = {}
        self._cooldown_loaded = False
        self._cooldown_saved_at: datetime | None = None
        #: No entries before this: Kalshi said the account has no free cash (see live.py).
        self._balance_backoff_until: datetime | None = None
        #: §9.57/§9.58: ticker -> (first seen out of the price band, last seen out of it).
        #: Persisted as a system event and restored once per process, so a restart resumes it.
        self._out_of_band: dict[str, tuple[datetime, datetime]] = {}
        self._out_of_band_loaded = False
        self._out_of_band_saved_at: datetime | None = None
        #: §9.60: ticker -> when its resting sell was last cancelled. No order is sized from a
        #: position read older than that (the sell may have part-filled until the cancel).
        self._sell_cancelled_at: dict[str, datetime] = {}
        #: §9.63: (events, words) with a fill of one of our bids in the last FILL_CONTAGION_HOURS,
        #: refreshed every cycle from the fills table.
        self._contagion: tuple[set[str], set[str]] = (set(), set())
        #: §9.62: ticker -> {role: contracts} of the resting sells a re-price just cancelled, so
        #: they are re-placed at the same sizes. In memory: a restart re-splits what is left.
        self._sell_sizes: dict[str, dict[str, int]] = {}
        #: §9.60: True once Kalshi refused cash while a resting sell was up; restored per process.
        self._resting_sell_off = False
        self._resting_sell_off_loaded = False
        #: §9.60 sweep log: ticker -> {"yes": levels, "no": levels, "at": when} from the last read,
        #: and the recorded sweeps still owed a follow-up read.
        self._sweep_prev: dict[str, dict] = {}
        self._sweep_pending: list[dict] = []
        #: ticker -> parsed book, for this cycle's data readers only (`_data_book`).
        self._data_books: dict[str, tuple] = {}
        #: WS-024 D1 programme discovery: the GET-only client it runs through (built on first
        #: use), and when this process last attempted a pass — success or not, so a failing
        #: pass is retried on the discovery cadence, never every cycle.
        self._discovery_client = None
        self._discovery_attempted_at: datetime | None = None
        #: (session id, now, rows): the running liquidity programmes, read once per cycle. Up to
        #: four steps of a cycle read the same list, and each read was a full scan of
        #: `incentive_programs` (WS-024). Cleared at the start of a cycle and after discovery,
        #: the only writer of the table inside a cycle.
        self._programs_memo: tuple[int, datetime, list] | None = None
        #: §9.69: ticker -> (estimated reward $/h of our quote there, when estimated), from every
        #: book this process fetched. Orders the next cycles' fetches by expected reward. In
        #: memory: after a restart it is rebuilt by exploration within about an hour.
        self._reward_seen: dict[str, tuple[float, datetime]] = {}

    # --- arming ---------------------------------------------------------------

    def armed(self, executor) -> bool:
        """True only when every switch that could stop this is on. Checked before any GET so a
        disabled book costs nothing, and re-checked inside the executor so this is convenience,
        never the guard."""
        if not getattr(self.settings, "liquidity_incentive_live_enabled", False):
            return False
        if executor is None:
            return False
        return executor._switches_on() and executor._allowed(limm.LIVE_TAG)

    def excluded_series(self) -> frozenset[str]:
        raw = getattr(self.settings, "liquidity_incentive_excluded_series", "") or ""
        return frozenset(s.strip().upper() for s in raw.split(",") if s.strip())

    # --- the cycle ------------------------------------------------------------

    def cycle(self, session, executor, account_state=None, now: datetime | None = None) -> dict:
        """Manage held positions, then place pairs up to the remaining market slots. Returns a
        per-cycle summary; never raises for a bad book or a refused order — both are recorded
        outcomes, not exceptions."""
        now = now or datetime.now(timezone.utc)
        self._programs_memo = None
        self._data_books = {}
        summary: dict = {"armed": False, "considered": 0, "fetched": 0, "placed": 0,
                         "twin_opened": 0, "outcomes": {}, "managed": {}}

        # The reward ledger runs BEFORE the armed gate, deliberately. Kalshi credits a liquidity
        # reward only AFTER a programme ends, so a credit for quoting we already did can land
        # days later — including after this book has been stood down. A ledger that stopped
        # measuring when the book stopped quoting would miss exactly the payment it exists to
        # catch.
        self._observe_rewards(session, now)

        if not self.armed(executor):
            return summary
        summary["armed"] = True

        if not self._resting_sell_off_loaded:
            self._load_resting_sell_off(session)
        # Exits before entries: a cycle's first job is the capital already out, and an exit
        # frees the slot and the budget an entry below might use.
        summary["managed"] = self._manage_positions(session, executor, now)
        # Then refresh the programme list, before anything below reads it (retire, reprice,
        # candidates). Runs before every early return, so a full book still keeps it fresh.
        discovery = self._refresh_programs(session, now)
        self._programs_memo = None
        if discovery is not None:
            summary["discovery"] = discovery
        retired = self._retire_unpaid_pairs(session, executor, now)
        if retired:
            summary["outcomes"]["program_ended"] = retired
        stepped = self._retire_before_event(session, executor, now)
        if stepped:
            summary["outcomes"].update(stepped)
        self._load_contagion(session, now)
        contagion = self._retire_contagion(session, executor, now)
        if contagion:
            summary["outcomes"]["fill_contagion"] = contagion
        partial = self._retire_partially_filled(session, executor)
        if partial:
            summary["outcomes"]["partial_fill"] = partial
        out_of_band = self._retire_out_of_band(session, executor, now)
        if out_of_band:
            summary["outcomes"]["out_of_band"] = out_of_band
        snapped = self._snapshot_own_books(session, now)
        if snapped:
            summary["book_snapshots"] = snapped
        try:
            sweeps = self._record_sweeps(session, now)
        except Exception:  # noqa: BLE001 — a data log must never stop the book
            logger.warning("incentive book: sweep log failed", exc_info=True)
            sweeps = 0
        if sweeps:
            summary["sweeps"] = sweeps

        if self._balance_backoff_until is not None and now < self._balance_backoff_until:
            summary["outcomes"]["balance_backoff"] = 1
            return summary

        repriced = self._reprice_held(session, executor, now, account_state)
        if repriced:
            summary["outcomes"]["repriced"] = repriced

        open_now =repo.count_live_book_open_tradeable(session, limm.LIVE_TAG, now)
        exposure_now = repo.live_strategy_exposure(session, limm.LIVE_TAG)
        slots = limm.MAX_OPEN_ORDERS - int(open_now)
        # Full book: carry on only if a held pair is old enough to be swapped for a better one.
        stale = (self._stale_held_pairs(session, now, min_age_seconds=limm.MIN_HOLD_SECONDS)
                 if slots <= 0 else {})
        if slots <= 0 and not stale:
            summary["outcomes"][SKIP_NO_SLOTS] = 1
            return summary

        excluded = self.excluded_series()
        candidates = self._candidate_programs(session, now=now, excluded_series=excluded)
        summary["considered"] = len(candidates)
        if not self._cooldown_loaded:
            self._load_cooldown(session, now)
        self._book_refused_until = {t: u for t, u in self._book_refused_until.items() if u > now}
        cooling = [p for p in candidates if p.market_ticker in self._book_refused_until]
        if cooling:
            summary["cooling_down"] = len(cooling)
            candidates = [p for p in candidates if p.market_ticker not in self._book_refused_until]
        # §9.59: drop markets of events this book already holds MAX_MARKETS_PER_EVENT of BEFORE
        # spending the bounded book fetches. On 2026-10-16 the 30 newest programmes were all
        # words of three capped Trump events, so every fetch was refused event_cap and a free
        # slot stayed empty (the §9.39 failure, with the per-event cap as the filter).
        own_per_event = repo.live_book_open_event_counts(session, limm.LIVE_TAG)
        capped = {ev for ev, n in own_per_event.items() if n >= limm.MAX_MARKETS_PER_EVENT}
        if capped:
            n_before = len(candidates)
            candidates = [p for p in candidates if (p.event_ticker or "") not in capped]
            if len(candidates) < n_before:
                summary["outcomes"][limm.REFUSE_EVENT_CAP] = n_before - len(candidates)
        # §9.65: an event any live book holds a position on is refused below (event_cap) and that
        # refusal never cools down, so its markets took the whole fetch budget every cycle and
        # starved every other event (2026-10-21 → 10-23: 35 KXTRUMPMENTIONB-26OCT27 markets ahead
        # of all KXMAMDANIMENTION). Drop those events BEFORE the fetches, as §9.59 does for caps.
        position_blocked: dict[str, bool] = {}
        for p in candidates:
            ev = p.event_ticker or ""
            if ev and ev not in capped and ev not in position_blocked:
                position_blocked[ev] = repo.event_has_open_live_position(
                    session, ev, min_quantity=limm.EVENT_BLOCK_MIN_CONTRACTS)
        if any(position_blocked.values()):
            n_before = len(candidates)
            candidates = [p for p in candidates if not position_blocked.get(p.event_ticker or "")]
            if len(candidates) < n_before:
                summary["outcomes"][limm.REFUSE_EVENT_CAP] = \
                    summary["outcomes"].get(limm.REFUSE_EVENT_CAP, 0) + n_before - len(candidates)
        max_fetch = max(1, int(getattr(self.settings,
                                       "liquidity_incentive_live_max_book_fetches", 8)))
        candidates = self._order_by_expected_reward(candidates, now, max_fetch)
        built: list[dict] = []
        for program in candidates[:max_fetch]:
            try:
                ob = self.client.get_orderbook(program.market_ticker)
            except Exception:  # noqa: BLE001 — one unreadable book must not end the cycle
                summary["outcomes"][SKIP_BOOK_ERROR] = \
                    summary["outcomes"].get(SKIP_BOOK_ERROR, 0) + 1
                logger.warning("incentive live: orderbook fetch failed",
                               extra={"extra_fields": {"ticker": program.market_ticker}})
                continue
            summary["fetched"] += 1
            built.append(candidate_from_book(program, ob, now=now))

        # One event, one commitment. Two sources of a block, and both are needed: events this
        # book already holds (its own stacking, which is what happened on 2026-09-18), and events
        # any live book holds a position on (the fleet's concentration).
        # §9.56: this book may rest in up to MAX_MARKETS_PER_EVENT markets of one event; an event
        # is blocked once it reaches that, or once any live book HOLDS a position on it.
        blocked_events = {ev for ev, n in own_per_event.items()
                          if n >= limm.MAX_MARKETS_PER_EVENT}
        for c in built:
            ev = c.get("event_ticker")
            if ev and ev not in blocked_events and repo.event_has_open_live_position(
                    session, ev, min_quantity=limm.EVENT_BLOCK_MIN_CONTRACTS):
                blocked_events.add(ev)
        ranked = limm.rank_candidates(built, excluded_series=excluded,
                                      blocked_event_tickers=frozenset(blocked_events))
        ranked, dropped = rank_by_projected_payout(ranked)
        if dropped:
            summary["outcomes"][SKIP_LOW_PROJECTED] = len(dropped)
        self._remember_rewards(built, ranked + dropped, now)
        # Every candidate that produced no pair is still reported, by its refusal code, so a
        # cycle that placed nothing says which cap or which book stopped it.
        placeable = {c["market_ticker"] for c, _ in ranked + dropped}
        for c in built:
            if c["market_ticker"] in placeable:
                continue
            refusal = limm.quote_candidate(c, excluded_series=excluded,
                                           blocked_event_tickers=frozenset(blocked_events))
            code = getattr(refusal, "code", "unknown")
            summary["outcomes"][code] = summary["outcomes"].get(code, 0) + 1
            if code in BOOK_REFUSAL_CODES:
                self._book_refused_until[c["market_ticker"]] = now + timedelta(
                    seconds=BOOK_REFUSAL_COOLDOWN_SECONDS)
        self._save_cooldown(session, now)

        if slots <= 0:
            chosen = self._replace_stale_pair(session, executor, stale, ranked, now, summary)
            if chosen is None:
                summary["outcomes"][SKIP_NO_SLOTS] = \
                    summary["outcomes"].get(SKIP_NO_SLOTS, 0) + 1
                return summary
            ranked = [chosen] + [r for r in ranked if r is not chosen]
            slots = 1
            exposure_now = repo.live_strategy_exposure(session, limm.LIVE_TAG)

        for candidate, pair in ranked:
            if slots <= 0:
                summary["outcomes"][SKIP_NO_SLOTS] = \
                    summary["outcomes"].get(SKIP_NO_SLOTS, 0) + 1
                break
            cand_event = candidate.get("event_ticker")
            if cand_event and cand_event in blocked_events:
                summary["outcomes"][limm.REFUSE_EVENT_CAP] = \
                    summary["outcomes"].get(limm.REFUSE_EVENT_CAP, 0) + 1
                continue
            if exposure_now + pair.collateral_usd > limm.MAX_STRATEGY_EXPOSURE_USD:
                summary["outcomes"][limm.REFUSE_EXPOSURE_CAP] = \
                    summary["outcomes"].get(limm.REFUSE_EXPOSURE_CAP, 0) + 1
                break
            outcome, legs = executor.mirror_incentive_pair(
                session, strategy=limm.LIVE_TAG,
                event_ticker=candidate.get("event_ticker") or pair.market_ticker,
                ticker=pair.market_ticker, pair=pair, account_state=account_state,
            )
            summary["outcomes"][outcome] = summary["outcomes"].get(outcome, 0) + 1
            if outcome == limm.REJECT_INSUFFICIENT_BALANCE:
                # Every other candidate would be refused the same way: stop, and wait.
                self._balance_backoff_until = now + timedelta(seconds=limm.BALANCE_BACKOFF_SECONDS)
                logger.warning("incentive book: Kalshi reports insufficient balance; no entries "
                               f"for {limm.BALANCE_BACKOFF_SECONDS // 60} min")
                if self._any_resting_sell(session):
                    self._switch_off_resting_sells(
                        session, executor, now,
                        why=f"entry on {pair.market_ticker} refused for cash while a sell rested")
                break
            if outcome == limm.GATE_NON_DEFAULT_SHARD:
                # The shard is a property of the market, not of this minute's book: rest it so
                # the fetch budget goes to markets this book can actually quote (§9.45).
                self._book_refused_until[pair.market_ticker] = now + timedelta(
                    seconds=BOOK_REFUSAL_COOLDOWN_SECONDS)
                self._cooldown_saved_at = None
                self._save_cooldown(session, now)
            if not legs:
                continue
            summary["placed"] += 1
            slots -= 1
            placed_event = candidate.get("event_ticker")
            if placed_event:
                own_per_event[placed_event] = own_per_event.get(placed_event, 0) + 1
                if own_per_event[placed_event] >= limm.MAX_MARKETS_PER_EVENT:
                    blocked_events.add(placed_event)
            for leg in legs:
                exposure_now += leg.collateral_usd
                if self._open_twin(session, leg):
                    summary["twin_opened"] += 1
        return summary

    def _snapshot_own_books(self, session, now: datetime) -> int:
        """Record the current book of every market this book rests or holds in, every
        OWN_BOOK_SNAPSHOT_SECONDS, as an `IncentiveMarketSnapshot`.

        The shadow collector wrote these and has been off since 2026-10-07 (§9.50), so the
        dashboard had no current price for a held position and no reward estimate for a resting
        bid. This is the same row, scored with the same functions, for at most the book's own
        two markets: a GET each, never an order. A failure is logged, never raised."""
        last = self._last_book_snapshot_at
        if last is not None and (now - last).total_seconds() < OWN_BOOK_SNAPSHOT_SECONDS:
            return 0
        self._last_book_snapshot_at = now
        by_ticker = {p.market_ticker: p for p in self._running_programs(session, now)}
        n = 0
        for ticker in sorted(repo.live_book_open_tickers(session, limm.LIVE_TAG)):
            close = repo.market_close_time(session, ticker)
            if close is not None and close <= now:
                continue   # closed, awaiting settlement: nothing to price (and leave it alone)
            try:
                yes_levels, no_levels = self._data_book(ticker)
                yes_map = {int(p): float(q) for p, q in yes_levels}
                no_map = {int(p): float(q) for p, q in no_levels}
                prog = by_ticker.get(ticker)
                target = float(prog.target_size) if prog and prog.target_size is not None else None
                disc = discount_factor(prog.discount_factor_bps if prog else None)
                ys = side_score(yes_map, target, disc)
                ns = side_score(no_map, target, disc)
                store.insert_market_snapshot(
                    session, program_row_id=prog.id if prog else None, market_ticker=ticker,
                    at=now, book_valid=bool(yes_map or no_map),
                    best_yes_bid=max(yes_map) if yes_map else None,
                    best_no_bid=max(no_map) if no_map else None,
                    yes_depth_total=ys.resting_total, no_depth_total=ns.resting_total,
                    yes_levels_json=sorted(yes_map.items(), reverse=True)[:10],
                    no_levels_json=sorted(no_map.items(), reverse=True)[:10],
                    est_reference_price=ys.reference_price,
                    est_yes_qualifying_depth=ys.resting_total,
                    est_no_qualifying_depth=ns.resting_total,
                    est_yes_meets_target=ys.meets_target, est_no_meets_target=ns.meets_target,
                    est_yes_score_total=ys.field_score, est_no_score_total=ns.field_score,
                    scoring_version=SCORING_VERSION)
                n += 1
            except Exception as exc:  # noqa: BLE001 — a snapshot must never stop the book
                logger.warning(f"incentive book snapshot {ticker}: "
                               f"{type(exc).__name__}: {str(exc)[:200]}")
        return n

    def _running_programs(self, session, now: datetime) -> list:
        """`progs.current_programs(now=now, liquidity_only=True)`, read once per session and
        `now`. Callers only read the list; nothing in a cycle but discovery writes the table."""
        memo = self._programs_memo
        if memo is None or memo[0] != id(session) or memo[1] != now:
            memo = (id(session), now, progs.current_programs(session, now=now,
                                                             liquidity_only=True))
            self._programs_memo = memo
        return memo[2]

    # --- programme discovery (WS-024 D1) ---------------------------------------

    def _refresh_programs(self, session, now: datetime) -> dict | None:
        """Run one programme-discovery pass when nothing else has kept the table fresh.

        WHY THIS LIVES HERE. `incentive_programs` was written only by the shadow collector,
        which ran on the `evo` service; when that service was deleted on 2026-10-07 the list
        this book reads froze (WS-024). This restores the writer inside the process that needs
        it, with the collector's own boundaries: REST GETs only, through the same GET-only
        `IncentiveReadOnlyKalshi` wrapper, never the order path.

        - A no-op while any other writer keeps the table fresh: it runs only when no
          `incentive_discovery_cycles` row is newer than `liquidity_incentive_discovery_seconds`.
        - Inside a SAVEPOINT: a failure rolls back the discovery pass alone, and the cycle goes
          on to place and manage as before. Failure is logged and returned, never raised.
        - Bounded: at most `liquidity_incentive_runner_discovery_max_new_terms` new or changed
          terms per pass; the rest wait for the next pass.
        - Off with `LIQUIDITY_INCENTIVE_RUNNER_DISCOVERY=false`."""
        if not getattr(self.settings, "liquidity_incentive_runner_discovery", True):
            return None
        every = float(getattr(self.settings, "liquidity_incentive_discovery_seconds", 300.0))
        if (self._discovery_attempted_at is not None
                and (now - self._discovery_attempted_at).total_seconds() < every):
            return None
        latest = session.scalar(select(func.max(m.IncentiveDiscoveryCycle.started_at)))
        if latest is not None and (now - _aware(latest)).total_seconds() < every:
            return None
        self._discovery_attempted_at = now
        cap = int(getattr(self.settings, "liquidity_incentive_runner_discovery_max_new_terms",
                          100) or 0)
        try:
            if self._discovery_client is None:
                self._discovery_client = IncentiveReadOnlyKalshi(self.client, self.settings)
            with session.begin_nested():
                result = progs.run_discovery(self._discovery_client, session, now=now,
                                             max_new_terms=cap if cap > 0 else None,
                                             load_current=False)
        except Exception as exc:  # noqa: BLE001 — a failed pass must never stop the book
            logger.exception("incentive runner discovery failed (book unaffected)")
            return {"failed": f"{type(exc).__name__}: {str(exc)[:200]}"}
        return {"cycle_id": result.cycle_id, "new": result.new_terms,
                "changed": result.changed_terms, "gone": result.disappeared,
                "deferred": result.deferred, "errors": result.errors}

    # --- replace only when better (§9.47) ---------------------------------------

    def _stale_held_pairs(self, session, now: datetime,
                          min_age_seconds: float = limm.STALE_PAIR_SECONDS) -> dict[str, list]:
        """Held tickers whose only working orders are this book's resting, unfilled BUY legs,
        the oldest older than `min_age_seconds`. Anything filled, in flight or shared is left
        to the position manager — only a pair that is purely resting can be swapped."""
        out: dict[str, list] = {}
        for ticker in sorted(repo.live_book_open_tickers(session, limm.LIVE_TAG)):
            rows = repo.live_orders_on_ticker(session, ticker)
            if any(r.strategy != limm.LIVE_TAG and r.status in repo.LIVE_NONTERMINAL_STATUSES
                   for r in rows):
                continue
            working = [r for r in rows if r.strategy == limm.LIVE_TAG
                       and r.status in repo.LIVE_NONTERMINAL_STATUSES]
            if not working or any(r.status != "resting" or r.action != "buy"
                                  or not r.kalshi_order_id for r in working):
                continue
            snap = repo.latest_position_snapshot(session, ticker)
            if snap is not None:
                net = float(snap.quantity_fp if snap.quantity_fp is not None
                            else snap.quantity or 0)
                if abs(net) > 0.01:
                    continue            # something filled: a position, not a quote
            oldest = min(_aware(r.created_at) for r in working)
            if (now - oldest).total_seconds() > min_age_seconds:
                out[ticker] = working
        return out

    def _retire_unpaid_pairs(self, session, executor, now: datetime) -> int:
        """Cancel a purely resting pair whose programme is no longer running. With no timeout
        (§9.47) nothing else would take it down, and a quote that earns nothing is only risk."""
        resting = self._stale_held_pairs(session, now, min_age_seconds=0.0)
        if not resting:
            return 0
        running = {p.market_ticker for p in self._running_programs(session, now)
            if p.end_date is not None and _aware(p.end_date) > now}
        n = 0
        for ticker in resting:
            if ticker in running:
                continue
            if executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker,
                                                reason="program_ended"):
                n += 1
        return n

    def _retire_before_event(self, session, executor, now: datetime) -> dict:
        """§9.55: cancel a purely resting quote whose event is about to start (its pre-event
        cutoff has passed), or that is outside the event-mention universe altogether. A fill
        comes when the event happens — the word is said — so the bid steps aside before it.
        Held POSITIONS are left to the exit rules; only resting bids go."""
        if not limm.EVENT_MENTION_ONLY:
            return {}
        resting = self._stale_held_pairs(session, now, min_age_seconds=0.0)
        if not resting:
            return {}
        programs = {p.market_ticker: p for p in self._running_programs(session, now)}
        out: dict = {}
        for ticker in resting:
            row = programs.get(ticker)
            if row is None:
                continue            # programme gone: `_retire_unpaid_pairs` owns that case
            cutoff = _entry_cutoff(row)     # §9.63: ENTRY_STEP_ASIDE_HOURS before the event
            if cutoff is not None and now < cutoff:
                continue
            reason = "pre_event" if cutoff is not None else "out_of_universe"
            if executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker,
                                                reason=reason):
                out[reason] = out.get(reason, 0) + 1
                logger.info(f"incentive book: {reason}: cancelled resting bid on {ticker}"
                            + (f" (cutoff {cutoff.isoformat()})" if cutoff else ""))
        return out

    def _load_contagion(self, session, now: datetime) -> None:
        """§9.63: the events and words where one of our BIDS filled in the last
        FILL_CONTAGION_HOURS, read from the fills themselves (so a restart keeps them)."""
        self._contagion = (set(), set())
        if limm.FILL_CONTAGION_HOURS <= 0:
            return
        since = now - timedelta(hours=limm.FILL_CONTAGION_HOURS)
        rows = session.execute(
            select(m.LiveOrder.market_ticker, m.LiveOrder.event_ticker)
            .join(m.Fill, m.Fill.kalshi_order_id == m.LiveOrder.kalshi_order_id)
            .where(m.LiveOrder.strategy == limm.LIVE_TAG, m.LiveOrder.action == "buy",
                   m.Fill.filled_at >= since)).all()
        events, words = self._contagion
        for ticker, event in rows:
            if event:
                events.add(event)
            word = limm.market_word(ticker, event)
            if word:
                words.add(word)

    def _contagion_blocks(self, ticker: str | None, event_ticker: str | None) -> bool:
        events, words = self._contagion
        if event_ticker and event_ticker in events:
            return True
        word = limm.market_word(ticker, event_ticker)
        return bool(word and word in words)

    def _retire_contagion(self, session, executor, now: datetime) -> int:
        """§9.63: cancel a purely resting bid in an event, or on a word, where one of our bids
        filled in the last FILL_CONTAGION_HOURS. Held positions are left to the exit rules."""
        if not self._contagion[0] and not self._contagion[1]:
            return 0
        n = 0
        for ticker, working in self._stale_held_pairs(session, now, min_age_seconds=0.0).items():
            event = next((r.event_ticker for r in working if r.event_ticker), None)
            if not self._contagion_blocks(ticker, event):
                continue
            if executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker,
                                                reason="fill_contagion"):
                n += 1
                logger.info(f"incentive book: fill_contagion: cancelled resting bid on {ticker} "
                            f"(a bid in its event or on its word filled in the last "
                            f"{limm.FILL_CONTAGION_HOURS:.0f}h)")
        return n

    def _retire_partially_filled(self, session, executor) -> int:
        """§9.70: cancel the rest of any entry bid of ours that has started to fill. A seller
        working into our bid a little at a time is the sign the price is about to move against
        us; left resting, the bid grew to full size before the stop-loss sold it. Only entry
        bids — resting sells and exits are never touched here. Returns the number cancelled."""
        n = 0
        for ticker in sorted(repo.live_book_open_tickers(session, limm.LIVE_TAG)):
            bids = [r for r in repo.live_orders_on_ticker(session, ticker)
                    if r.strategy == limm.LIVE_TAG and r.action == "buy"
                    and r.status in ("resting", "partial") and r.kalshi_order_id
                    and not (r.client_order_id or "").startswith(_CLOSING_PREFIXES)]
            if len({r.side for r in bids}) > 1:
                continue            # a two-sided pair (pre-§9.48 mode) is hedged, not one bet
            started = [r for r in bids if sum(repo.filled_quantity_by_fill_id(
                session, r.kalshi_order_id).values()) >= limm.PARTIAL_FILL_CANCEL_MIN_CONTRACTS]
            if not started:
                continue
            if executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker,
                                                reason="partial_fill"):
                n += 1
                logger.info(f"incentive book: partial_fill: cancelled the rest of the bid on "
                            f"{ticker} after {len(started)} bid(s) started to fill")
        return n

    def _retire_out_of_band(self, session, executor, now: datetime) -> int:
        """§9.57: cancel a purely resting single bid whose side's reference price has been above
        MAX_PRICE_CENTS for OUT_OF_BAND_EXIT_SECONDS without a break. Such a bid scores ~nothing
        and cannot be re-priced inside the cap, so it only holds a slot. Checked every cycle; a
        read inside the band resets the clock, an unreadable book adds no evidence, and a gap of
        more than OUT_OF_BAND_MAX_GAP_SECONDS since the last out-of-band read restarts it. The
        clock is persisted (§9.58), so a worker restart resumes it. Returns the number
        cancelled."""
        if not self._out_of_band_loaded:
            self._load_out_of_band(session, now)
        before = dict(self._out_of_band)
        held = self._stale_held_pairs(session, now, min_age_seconds=0.0)
        programs = {p.market_ticker: p for p in self._running_programs(session, now)}
        seen: set[str] = set()
        n = 0
        for ticker, working in held.items():
            if len(working) != 1 or ticker not in programs:
                continue
            seen.add(ticker)
            row = working[0]
            try:
                ob = self.client.get_orderbook(ticker)
            except Exception:  # noqa: BLE001 — unknown book: no evidence either way
                continue
            c = candidate_from_book(programs[ticker], ob, now=now)
            ref = (c.get("reference_price_by_side") or {}).get(row.side)
            # Our own bid sits at or below the cap, so it can never lift the reference above it:
            # a reference above the cap is the rest of the book's doing.
            if ref is None or int(ref) <= limm.MAX_PRICE_CENTS:
                self._out_of_band.pop(ticker, None)
                continue
            prev = self._out_of_band.get(ticker)
            if prev is not None and \
                    (now - prev[1]).total_seconds() <= limm.OUT_OF_BAND_MAX_GAP_SECONDS:
                since = prev[0]
            else:
                since = now
            self._out_of_band[ticker] = (since, now)
            if (now - since).total_seconds() < limm.OUT_OF_BAND_EXIT_SECONDS:
                continue
            if executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker,
                                                reason="out_of_band"):
                self._out_of_band.pop(ticker, None)
                n += 1
                logger.info(f"incentive book: out_of_band: cancelled resting {row.side} bid on "
                            f"{ticker} at {row.limit_price}c (reference {ref}c above "
                            f"{limm.MAX_PRICE_CENTS}c since {since.isoformat()})")
        for t in [t for t in self._out_of_band if t not in seen]:
            del self._out_of_band[t]
        changed = {t: v[0] for t, v in before.items()} != \
            {t: v[0] for t, v in self._out_of_band.items()}
        self._save_out_of_band(session, now, force=changed)
        return n

    def _load_out_of_band(self, session, now: datetime) -> None:
        """Restore the out-of-band clocks from the newest saved copy, once per process. A clock
        whose last out-of-band read is older than the gap allowance is dropped (it restarts).
        Any failure leaves the map empty — clocks restart, which only delays an exit."""
        self._out_of_band_loaded = True
        try:
            row = session.scalar(
                select(m.SystemEvent)
                .where(m.SystemEvent.component == OUT_OF_BAND_COMPONENT)
                .order_by(m.SystemEvent.id.desc()).limit(1))
            saved = (row.raw_json or {}).get("tickers", {}) if row is not None else {}
            for ticker, v in saved.items():
                since = _aware(datetime.fromisoformat(v["since"]))
                last = _aware(datetime.fromisoformat(v["last"]))
                if since is None or last is None:
                    continue
                if 0 <= (now - last).total_seconds() <= limm.OUT_OF_BAND_MAX_GAP_SECONDS:
                    self._out_of_band[ticker] = (since, last)
        except Exception:  # noqa: BLE001 — a bad saved copy must not stop the book
            logger.warning("incentive live: out-of-band clock restore failed", exc_info=True)

    def _save_out_of_band(self, session, now: datetime, *, force: bool) -> None:
        """Persist the clocks when the set of clocks changed, else at most every
        OUT_OF_BAND_PERSIST_EVERY_SECONDS while any is running (so a restored `last` is never
        older than that plus the restart). Nothing is written while no clock runs."""
        last = self._out_of_band_saved_at
        if not force:
            if not self._out_of_band:
                return
            if last is not None and \
                    (now - last).total_seconds() < OUT_OF_BAND_PERSIST_EVERY_SECONDS:
                return
        self._out_of_band_saved_at = now
        try:
            repo.log_system_event(
                session, level="info", component=OUT_OF_BAND_COMPONENT,
                message=f"{len(self._out_of_band)} resting bids out of the price band",
                raw={"tickers": {t: {"since": a.isoformat(), "last": b.isoformat()}
                                 for t, (a, b) in self._out_of_band.items()}})
        except Exception:  # noqa: BLE001 — persistence is a convenience, never the guard
            logger.warning("incentive live: out-of-band clock save failed", exc_info=True)

    def _reprice_held(self, session, executor, now: datetime, account_state=None) -> int:
        """§9.49: a held single-side quote that has fallen BELOW its own market's reference
        price (so it scores at a discount) is moved to the reference in the SAME market — the
        programme, and what it has accrued, is kept. Only quotes resting at least
        `STALE_PAIR_SECONDS`, only when the market still yields a quote on the same side, and
        only to a different price. A book that cannot be read or quoted leaves the quote alone
        (the dead check decides whether to leave the market). Returns the number re-priced."""
        held = self._stale_held_pairs(session, now)
        if not held:
            return 0
        programs = {p.market_ticker: p for p in self._running_programs(session, now)}
        n = 0
        for ticker, working in held.items():
            if len(working) != 1 or ticker not in programs:
                continue
            row = working[0]
            try:
                ob = self.client.get_orderbook(ticker)
            except Exception:  # noqa: BLE001 — unknown book: keep the quote
                continue
            c = candidate_from_book(programs[ticker], ob, now=now)
            ref = (c.get("reference_price_by_side") or {}).get(row.side)
            if ref is None or int(row.limit_price) >= int(ref):
                continue            # at or above the reference: full credit already
            # Our own resting order is in this book; take it out before quoting.
            for key in ("yes_levels", "no_levels"):
                if (key == "yes_levels") == (row.side == limm.SIDE_YES):
                    lv = dict(c.get(key) or {})
                    left = lv.get(int(row.limit_price), 0.0) - float(row.quantity)
                    if left > 1e-9:
                        lv[int(row.limit_price)] = left
                    else:
                        lv.pop(int(row.limit_price), None)
                    c[key] = lv
            q = limm.quote_candidate(c, excluded_series=self.excluded_series())
            if not isinstance(q, limm.SideQuote) or q.leg.side != row.side \
                    or int(q.leg.price_cents) == int(row.limit_price):
                continue
            # §9.69: a re-price across SIZE_UP_MIN_PRICE_CENTS doubles the bid's dollars, so it
            # must still fit the book's total.
            held_usd = int(row.limit_price) * float(row.quantity or 0) / 100.0
            if repo.live_strategy_exposure(session, limm.LIVE_TAG) - held_usd \
                    + q.collateral_usd > limm.MAX_STRATEGY_EXPOSURE_USD + 1e-9:
                continue
            if not executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG,
                                                    ticker=ticker, reason="repriced"):
                continue            # part-cancelled: next cycle re-checks
            outcome, legs = executor.mirror_incentive_pair(
                session, strategy=limm.LIVE_TAG, event_ticker=c.get("event_ticker") or ticker,
                ticker=ticker, pair=q, account_state=account_state)
            logger.info(f"incentive book: re-priced {ticker} {row.side} "
                        f"{row.limit_price}c -> {q.leg.price_cents}c ({outcome})")
            if legs:
                n += 1
                for leg in legs:
                    self._open_twin(session, leg)
        return n

    def _held_reward_per_hour(self, ticker: str, working: list, programs: dict,
                              now: datetime) -> float | None:
        """A held pair's estimated reward per hour on its CURRENT book. 0.0 when its programme
        is gone; None when the book cannot be read (then it is kept)."""
        program = programs.get(ticker)
        if program is None:
            return 0.0
        prices = {r.side: int(r.limit_price) for r in working}
        try:
            ob = self.client.get_orderbook(ticker)
        except Exception:  # noqa: BLE001 — unknown value: keep the pair
            return None
        c = candidate_from_book(program, ob, now=now)
        qty = max(float(r.quantity) for r in working)
        y, n = prices.get(limm.SIDE_YES), prices.get(limm.SIDE_NO)
        return est_reward_per_hour(c, yes_price=y, no_price=n, qty=qty,
                                   own_yes=None if y is None else {y: qty},
                                   own_no=None if n is None else {n: qty})

    def _replace_stale_pair(self, session, executor, stale: dict, ranked: list,
                            now: datetime, summary: dict):
        """Cancel the weakest stale held quote only when it is DEAD — its estimated reward per
        hour below `DEAD_QUOTE_FRACTION` of the best new candidate's (§9.49) — and return that
        candidate. None leaves every held quote resting."""
        rate = quote_rate
        fresh = [cq for cq in ranked if cq[0]["market_ticker"] not in stale]
        if not fresh:
            return None
        best = max(fresh, key=rate)
        best_rate = rate(best)
        if best_rate <= 0:
            return None
        programs = {p.market_ticker: p for p in self._running_programs(session, now)}
        held: list[tuple[float, str]] = []
        for ticker, working in stale.items():
            r = self._held_reward_per_hour(ticker, working, programs, now)
            if r is not None:
                held.append((r, ticker))
        if not held:
            return None
        held_rate, ticker = min(held)
        summary["replace_check"] = {"held": ticker, "held_per_hour": round(held_rate, 4),
                                    "new": best[0]["market_ticker"],
                                    "new_per_hour": round(best_rate, 4)}
        # §9.49: only a DEAD quote is abandoned. Leaving a live programme early forfeits what it
        # has accrued (Kalshi pays at programme end, $1.00 minimum), so "the new one is a bit
        # better per hour" is not a reason to move.
        if held_rate >= best_rate * limm.DEAD_QUOTE_FRACTION:
            return None
        if not executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker,
                                                reason="replaced"):
            return None                 # part-cancelled: the next cycle re-checks
        summary["outcomes"]["replaced"] = summary["outcomes"].get("replaced", 0) + 1
        logger.info(f"incentive book: replaced {ticker} (~${held_rate:.4f}/h) with "
                    f"{best[0]['market_ticker']} (~${best_rate:.4f}/h)")
        return best

    # --- held positions -------------------------------------------------------

    def _manage_positions(self, session, executor, now: datetime) -> dict:
        """Apply the exit rules to every market this book holds a position on. Returns outcome
        counts. Every branch that cannot establish the position with certainty does NOTHING:
        Kalshi has no reduce-only for these orders, so an exit sent against a position that is
        already flat opens a new one."""
        out: dict = {}

        def note(code: str) -> None:
            out[code] = out.get(code, 0) + 1

        for ticker in sorted(repo.live_book_open_tickers(session, limm.LIVE_TAG)):
            try:
                note(self._manage_one(session, executor, ticker, now))
            except Exception as exc:  # noqa: BLE001 — one market must not stop the others
                logger.warning(f"incentive position manager: {ticker}: "
                               f"{type(exc).__name__}: {str(exc)[:200]}")
                note("error")
        return out

    def _manage_one(self, session, executor, ticker: str, now: datetime) -> str:
        rows = repo.live_orders_on_ticker(session, ticker)
        if any(r.strategy != limm.LIVE_TAG and r.action == "buy"
               and r.status in repo.LIVE_NONTERMINAL_STATUSES + ("filled",) for r in rows):
            return "shared_ticker"   # another book is in this market; its position is not ours
        hours_to_close = self._hours_to_close(session, ticker, now)
        if hours_to_close is not None and hours_to_close <= 0:
            # Closed and not yet settled: nothing can trade it. The KXBIGGESTQUAKE positions sit
            # here, which is also what keeps the operator's "leave them alone" true.
            return "closed_awaiting_settlement"
        ours = [r for r in rows if r.strategy == limm.LIVE_TAG]
        working = [r for r in ours if r.status in repo.LIVE_NONTERMINAL_STATUSES]

        own_net, entry_by_side, any_filled = self._own_position(session, ticker, ours)
        snap = repo.latest_position_snapshot(session, ticker)
        if snap is None or (now - _aware(snap.captured_at)).total_seconds() \
                > limm.POSITION_FRESH_SECONDS:
            return "no_fresh_position"
        snap_net = float(snap.quantity_fp if snap.quantity_fp is not None else snap.quantity or 0)

        if abs(snap_net) <= 0.01:
            # Flat by Kalshi AND by our own fills, after something filled: the round trip is done,
            # and a LONE leg still resting could only open a fresh naked position. Both sides still
            # resting is the balanced remainder of a pair that part-filled evenly — a hedged quote,
            # left alone.
            working_sides = {r.side for r in working if r.action == "buy"}
            if any_filled and abs(own_net) < 0.01 and working and len(working_sides) < 2:
                executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker)
                return "round_trip_cleanup"
            return "flat"
        # Kalshi and our own fills must agree on the side, or the state is mid-update: wait.
        if own_net == 0 or (own_net > 0) != (snap_net > 0):
            return "unsettled_state"
        held_side = limm.SIDE_YES if snap_net > 0 else limm.SIDE_NO
        qty = int(min(abs(snap_net), abs(own_net)))
        entry = entry_by_side.get(held_side)
        if qty < 1 or entry is None:
            return "unsettled_state"

        try:
            yes_levels, no_levels = parse_orderbook(self.client.get_orderbook(ticker) or {})
        except Exception:  # noqa: BLE001 — no book, no decision
            return "book_error"
        best = {limm.SIDE_YES: max((int(p) for p, _q in yes_levels), default=None),
                limm.SIDE_NO: max((int(p) for p, _q in no_levels), default=None)}
        mark = best[held_side]

        rule = limm.decide_exit(entry_cents=entry, mark_bid_cents=mark,
                                hours_to_close=hours_to_close)
        if rule is None:
            # §9.54: an exit leg dearer than MAX_EXIT_LEG_DOLLARS ties up the account's cash
            # (Kalshi reserves its full cost), so it comes off; the leg is then closed by the
            # take-profit, the stop or the pre-close flatten.
            dear = [r for r in working
                    if (r.client_order_id or "").startswith(EXIT_LEG_PREFIX)
                    and r.limit_price is not None and r.quantity is not None
                    and not limm.exit_leg_affordable(r.limit_price, r.quantity)]
            if dear:
                if executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG,
                                                    ticker=ticker, reason="exit_leg_over_cap"):
                    return "exit_leg_over_cap:cancelled"
                return "exit_leg_over_cap:cancel_pending"
            if limm.RESTING_SELL_ENABLED and not self._resting_sell_off:
                res = self._manage_resting_sell(
                    session, executor, ticker, now, working=working, snap=snap,
                    held_side=held_side, qty=qty, entry=entry, mark=mark,
                    opposite_levels=no_levels if held_side == limm.SIDE_YES else yes_levels)
                if res is not None:
                    return res
            if working:
                return "holding"
            price = limm.exit_leg_price(entry_cents=entry,
                                        best_opposite_bid=best[limm.other_side(held_side)])
            if price is None:
                return "holding"
            if not limm.exit_leg_affordable(price, qty):
                return "holding:exit_leg_over_cap"
            res = executor.place_incentive_exit_leg(
                session, strategy=limm.LIVE_TAG, ticker=ticker, held_side=held_side,
                qty=qty, price=price)
            return f"exit_leg:{res}"
        if mark is None:
            return f"{rule}:no_bid"
        if any((r.client_order_id or "").startswith("limmexit:") for r in working):
            return f"{rule}:in_flight"
        if executor.incentive_exit_attempts(session, strategy=limm.LIVE_TAG,
                                            ticker=ticker) >= limm.EXIT_MAX_ATTEMPTS:
            if ticker not in self._exit_exhausted:
                self._exit_exhausted.add(ticker)
                logger.error(f"incentive exit GIVING UP on {ticker} after "
                             f"{limm.EXIT_MAX_ATTEMPTS} attempts ({rule}); holding to settlement "
                             "-- close it by hand if it matters")
            return f"{rule}:exhausted"
        if any(_is_resting_sell(r) for r in working):
            # §9.60: a resting sell may part-fill up to the moment it is cancelled, so the
            # marketable exit waits for a position read taken AFTER the cancel — sizing it from
            # an older one could sell more than is held and open the other side.
            if executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker,
                                                reason=f"resting_sell:{rule}"):
                self._sell_cancelled_at[ticker] = now
                return f"{rule}:resting_sell_cancelled"
            return f"{rule}:cancel_pending"
        cancelled_at = self._sell_cancelled_at.get(ticker)
        if cancelled_at is not None and _aware(snap.captured_at) <= cancelled_at:
            return f"{rule}:await_position"
        if not executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker):
            return f"{rule}:cancel_pending"
        res = executor.close_incentive_position(
            session, strategy=limm.LIVE_TAG, ticker=ticker, held_side=held_side, qty=qty,
            mark_bid_cents=mark, rule=rule)
        return f"{rule}:{res}"

    def _manage_resting_sell(self, session, executor, ticker: str, now: datetime, *, working,
                             snap, held_side: str, qty: int, entry: int, mark: int | None,
                             opposite_levels) -> str | None:
        """§9.60: keep one post-only sell of exactly the held contracts resting at the current
        best ask while no exit rule fires. Only on event-tied mention markets with a running
        programme, and only before the pre-event cutoff — at the event the sell comes off and the
        position rides, as before. Returns an outcome code, or None to fall back to the old
        opposite-side exit leg (a market outside that universe, e.g. TSWIF, is left as it was)."""
        sells = [r for r in working if _is_resting_sell(r)]
        others = [r for r in working if not _is_resting_sell(r)]

        def cancel(reason: str) -> bool:
            ok = executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG,
                                                  ticker=ticker, reason=reason)
            if ok:
                self._sell_cancelled_at[ticker] = now
                self._sell_sizes.pop(ticker, None)
            return ok

        prog = next((p for p in self._running_programs(session, now)
                     if p.market_ticker == ticker), None)
        eligible = prog is not None and limm.in_universe(
            prog.series_ticker or ticker.split("-", 1)[0], prog.event_ticker, prog.market_title)
        cutoff = _pre_event_cutoff(prog) if eligible else None
        if not eligible or cutoff is None or now >= cutoff:
            if sells:
                if cancel("resting_sell:pre_event"):
                    logger.info(f"incentive book: resting sell pulled on {ticker} "
                                "(event about to start, or programme gone)")
                    return "resting_sell:pulled"
                return "resting_sell:cancel_pending"
            return None
        if any((r.client_order_id or "").startswith(EXIT_LEG_PREFIX) for r in others):
            # The opposite-side exit leg is what the resting sell replaces.
            if cancel("replaced_by_resting_sell"):
                return "resting_sell:exit_leg_cancelled"
            return "resting_sell:cancel_pending"
        if others:
            return "holding"   # an entry bid still works here: the position may still grow

        def remaining(r) -> float:
            if not r.kalshi_order_id:
                return float(r.quantity or 0)
            done = sum(float(f.quantity or 0) for f in repo.fills_for_order(session,
                                                                          r.kalshi_order_id))
            return float(r.quantity or 0) - done

        # The best ask of the OTHER sellers: a sell of our side at p is a bid at 100 - p on the
        # opposite book, so take our own sell out of that level before reading it.
        opp = {int(p): float(q) for p, q in opposite_levels}
        for s in sells:
            if s.limit_price is None:
                continue
            lvl = 100 - int(s.limit_price)
            left = opp.get(lvl, 0.0) - remaining(s)
            if left > 1e-9:
                opp[lvl] = left
            else:
                opp.pop(lvl, None)
        other_ask = 100 - max(opp) if opp else None
        hours_left = (cutoff - now).total_seconds() / 3600.0
        prices = limm.resting_sell_prices(entry_cents=entry, held_bid_cents=mark,
                                          best_other_ask_cents=other_ask,
                                          hours_to_cutoff=hours_left)
        targets = None if prices is None else {limm.RESTING_SELL_ROLE_ASK: prices[0],
                                               limm.RESTING_SELL_ROLE_ENTRY: prices[1]}

        roles = [_sell_role(r) for r in sells]
        if sells and (None in roles or len(set(roles)) < len(roles)):
            # A §9.60 single sell (no role), or two of one role: re-place as the §9.61 split.
            return "resting_sell:resplit" if cancel("resting_sell:resplit") \
                else "resting_sell:cancel_pending"
        held_by_sells = sum(remaining(r) for r in sells)
        if held_by_sells > qty + 1e-9:
            # Larger than the position left: if it all filled it would open the other side.
            return "resting_sell:resized" if cancel("resting_sell:resize") \
                else "resting_sell:cancel_pending"
        stale = [r for r in sells if targets is not None and r.limit_price is not None
                 and int(r.limit_price) != targets[_sell_role(r)]]
        if stale:
            if any(r.status != "resting" or not r.kalshi_order_id for r in sells):
                return "resting_sell:in_flight"
            youngest = max(_aware(r.created_at) for r in sells)
            if (now - youngest).total_seconds() < limm.RESTING_SELL_REPRICE_MIN_SECONDS:
                return "resting_sell:resting"
            sizes = {_sell_role(r): int(round(remaining(r))) for r in sells}
            if cancel("resting_sell:repriced"):
                # Re-placed at the SAME sizes per role, so a re-price never re-splits what is
                # left and moves contracts from the break-even half into the ask half.
                self._sell_sizes[ticker] = sizes
                logger.info(f"incentive book: resting sells on {ticker} "
                            + ", ".join(f"{_sell_role(r)} {r.limit_price}c->"
                                        f"{targets[_sell_role(r)]}c" for r in stale)
                            + " (re-placed next cycle)")
                return "resting_sell:repricing"
            return "resting_sell:cancel_pending"
        if targets is None:
            return "resting_sell:resting" if sells else "resting_sell:no_price"
        # Place whichever role is missing, from the contracts no sell covers yet: the ask role
        # takes half (rounded up), the break-even role the rest. After one role has filled, what
        # is left is already covered and nothing more is placed.
        free = int(round(qty - held_by_sells))
        have = set(roles)
        if free >= 1 and {limm.RESTING_SELL_ROLE_ASK, limm.RESTING_SELL_ROLE_ENTRY} <= have:
            # §9.67: both halves rest but the position grew past them (an orphaned bid filled
            # 494 more PEAC under a 5-contract sell, 2026-10-23). Re-place the split over the
            # whole position, so no held contract is left without a sell.
            if any(r.status != "resting" or not r.kalshi_order_id for r in sells):
                return "resting_sell:in_flight"
            return "resting_sell:grown" if cancel("resting_sell:grow") \
                else "resting_sell:cancel_pending"
        plan = []
        kept = self._sell_sizes.get(ticker) if not sells else None
        if kept:
            for role in (limm.RESTING_SELL_ROLE_ASK, limm.RESTING_SELL_ROLE_ENTRY):
                n = min(int(kept.get(role) or 0), free)
                if n > 0:
                    plan.append((role, n))
                    free -= n
        elif limm.RESTING_SELL_ROLE_ASK not in have and free > 0:
            n = min(free, (qty + 1) // 2) if limm.RESTING_SELL_ROLE_ENTRY not in have else free
            plan.append((limm.RESTING_SELL_ROLE_ASK, n))
            free -= n
        if not kept and limm.RESTING_SELL_ROLE_ENTRY not in have and free > 0:
            plan.append((limm.RESTING_SELL_ROLE_ENTRY, free))
        if not plan:
            return "resting_sell:resting"
        cancelled_at = self._sell_cancelled_at.get(ticker)
        if cancelled_at is not None and _aware(snap.captured_at) <= cancelled_at:
            return "resting_sell:await_position"
        res = "none"
        for role, n in plan:
            res = executor.place_incentive_resting_sell(
                session, strategy=limm.LIVE_TAG, ticker=ticker, held_side=held_side, qty=n,
                price=targets[role], role=role)
            if res == limm.REJECT_INSUFFICIENT_BALANCE:
                self._switch_off_resting_sells(session, executor, now,
                                               why=f"resting sell on {ticker} refused for cash")
                break
            if res != "placed":
                break
        else:
            self._sell_sizes.pop(ticker, None)
        return f"resting_sell:{res}"

    def _load_resting_sell_off(self, session) -> None:
        self._resting_sell_off_loaded = True
        try:
            row = session.scalar(
                select(m.SystemEvent)
                .where(m.SystemEvent.component == RESTING_SELL_OFF_COMPONENT)
                .order_by(m.SystemEvent.id.desc()).limit(1))
            if row is not None and (row.raw_json or {}).get("off"):
                self._resting_sell_off = True
        except Exception:  # noqa: BLE001 — unreadable: stay as configured
            logger.warning("incentive live: resting-sell switch restore failed", exc_info=True)

    def _switch_off_resting_sells(self, session, executor, now: datetime, *, why: str) -> None:
        """§9.60 self-test: whether Kalshi holds back cash for a resting sell of a held leg is
        not documented to us (§9.54 suggests it may). The first time Kalshi refuses an order for
        cash while one of ours is up, every resting sell comes off and the mechanic stays off —
        persisted, so a restart does not re-arm it. Re-enabling is a deliberate change."""
        if self._resting_sell_off:
            return
        self._resting_sell_off = True
        n = 0
        for ticker in sorted(repo.live_book_open_tickers(session, limm.LIVE_TAG)):
            rows = repo.live_orders_on_ticker(session, ticker)
            if any(r.strategy == limm.LIVE_TAG and r.status in repo.LIVE_NONTERMINAL_STATUSES
                   and _is_resting_sell(r) for r in rows):
                if executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG,
                                                    ticker=ticker, reason="resting_sell:off"):
                    self._sell_cancelled_at[ticker] = now
                    n += 1
        logger.error(f"incentive book: resting sells SWITCHED OFF ({why}); {n} cancelled")
        try:
            repo.log_system_event(session, level="error", component=RESTING_SELL_OFF_COMPONENT,
                                  message=f"resting sells switched off: {why}",
                                  raw={"off": True, "why": why, "at": now.isoformat()})
        except Exception:  # noqa: BLE001 — the in-process switch still holds
            logger.warning("incentive live: resting-sell switch save failed", exc_info=True)

    def _any_resting_sell(self, session) -> bool:
        for ticker in repo.live_book_open_tickers(session, limm.LIVE_TAG):
            if any(r.strategy == limm.LIVE_TAG and r.status in repo.LIVE_NONTERMINAL_STATUSES
                   and _is_resting_sell(r) for r in repo.live_orders_on_ticker(session, ticker)):
                return True
        return False

    # --- sweep log (§9.60, data only) -------------------------------------------

    def _data_book(self, ticker: str):
        """One parsed book per ticker per cycle for the two pure-data readers (the dashboard
        snapshot and the sweep log), so they cost one GET between them, not two."""
        if ticker not in self._data_books:
            self._data_books[ticker] = parse_orderbook(self.client.get_orderbook(ticker) or {})
        return self._data_books[ticker]

    def _record_sweeps(self, session, now: datetime) -> int:
        """Read the book of every market this book rests or holds in, and record a SWEEP: a bid
        side that lost at least SWEEP_MIN_CONTRACTS from at or above its old best bid while the
        best bid fell, between two reads at most OUT_OF_BAND_MAX_GAP_SECONDS apart. Each sweep
        gets follow-up reads at SWEEP_FOLLOWUP_MINUTES (did the bid come back?). Data only:
        nothing here places or cancels. Without a trade tape a pulled bid and a sale look the
        same; the record says how many of the contracts were ours. Returns sweeps recorded."""
        n = 0
        own: dict[str, dict[str, dict[int, float]]] = {}
        for ticker in sorted(repo.live_book_open_tickers(session, limm.LIVE_TAG)):
            close = repo.market_close_time(session, ticker)
            if close is not None and close <= now:
                continue
            try:
                yes_levels, no_levels = self._data_book(ticker)
            except Exception:  # noqa: BLE001 — no read, no evidence
                continue
            cur = {limm.SIDE_YES: {int(p): float(q) for p, q in yes_levels},
                   limm.SIDE_NO: {int(p): float(q) for p, q in no_levels}}
            mine: dict[str, dict[int, float]] = {limm.SIDE_YES: {}, limm.SIDE_NO: {}}
            for r in repo.live_orders_on_ticker(session, ticker):
                if r.strategy == limm.LIVE_TAG and r.action == "buy" \
                        and r.status in repo.LIVE_NONTERMINAL_STATUSES and r.limit_price:
                    lv = mine[r.side]
                    lv[int(r.limit_price)] = lv.get(int(r.limit_price), 0.0) + float(r.quantity or 0)
            own[ticker] = mine
            prev = self._sweep_prev.get(ticker)
            self._sweep_prev[ticker] = {"at": now, "levels": cur, "own": mine}
            for p in [p for p in self._sweep_pending if p["ticker"] == ticker]:
                due = [mm for mm in p["left"] if now >= p["at"] + timedelta(minutes=mm)]
                if not due:
                    continue
                side_now = cur[p["side"]]
                best_now = max(side_now) if side_now else None
                for mm in due:
                    p["left"].remove(mm)
                    store.record_event(session, kind=EV_BOOK_SWEEP_FOLLOWUP, at=now,
                                       ticker=ticker, detail_json={
                                           "sweep_at": p["at"].isoformat(), "side": p["side"],
                                           "minutes": mm, "best_bid_before": p["before"],
                                           "best_bid_now": best_now,
                                           "recovered": best_now is not None
                                           and best_now >= p["before"]})
            if prev is None or (now - prev["at"]).total_seconds() > limm.OUT_OF_BAND_MAX_GAP_SECONDS:
                continue
            for side in (limm.SIDE_YES, limm.SIDE_NO):
                before, after = prev["levels"][side], cur[side]
                if not before:
                    continue
                b0 = max(before)
                b1 = max(after) if after else 0
                if b1 >= b0:
                    continue
                gone = [(p, q, after.get(p, 0.0)) for p, q in sorted(before.items(), reverse=True)
                        if p >= b1 and q > after.get(p, 0.0)]
                removed = sum(q - a for _p, q, a in gone)
                if removed < SWEEP_MIN_CONTRACTS:
                    continue
                ours = sum(q for p, q in prev["own"][side].items() if p >= b1)
                store.record_event(session, kind=EV_BOOK_SWEEP, at=now, ticker=ticker,
                                   detail_json={
                                       "side": side, "prev_at": prev["at"].isoformat(),
                                       "gap_seconds": round((now - prev["at"]).total_seconds()),
                                       "best_bid_before": b0, "best_bid_after": b1 or None,
                                       "contracts_removed": round(removed, 2),
                                       "levels": [[p, q, a] for p, q, a in gone],
                                       "own_contracts_in_range": round(ours, 2),
                                       "top_before": sorted(before.items(), reverse=True)[:5],
                                       "top_after": sorted(after.items(), reverse=True)[:5]})
                logger.info(f"incentive book: sweep on {ticker} {side} bids {b0}c -> "
                            f"{b1 or 'none'}c, {removed:.0f} contracts gone "
                            f"({ours:.0f} ours) — recorded, no action")
                self._sweep_pending.append({"ticker": ticker, "side": side, "at": now,
                                            "before": b0, "left": list(SWEEP_FOLLOWUP_MINUTES)})
                n += 1
        live = set(own)
        self._sweep_prev = {t: v for t, v in self._sweep_prev.items() if t in live}
        self._sweep_pending = [p for p in self._sweep_pending if p["left"]
                               and p["ticker"] in live]
        return n

    @staticmethod
    def _own_position(session, ticker: str, ours: list) -> tuple[float, dict, bool]:
        """This book's own net position on `ticker` from ITS fills (+YES / -NO), the
        fill-weighted entry price per side, and whether anything of ours ever filled.

        Signed by our order row, not the fill's own side/action: those are YES-denominated on
        Kalshi and have inverted this repository's accounting before (§9.29/§9.31)."""
        by_koid = {r.kalshi_order_id: r for r in ours if r.kalshi_order_id}
        net = 0.0
        cost: dict[str, float] = {}
        size: dict[str, float] = {}
        any_filled = any(r.status in ("filled", "partial") for r in ours)
        for f in repo.fills_for_ticker(session, ticker):
            row = by_koid.get(f.kalshi_order_id)
            if row is None or not f.quantity:
                continue
            q = float(f.quantity)
            any_filled = True
            if row.action == "buy":
                net += q if row.side == limm.SIDE_YES else -q
                if row.limit_price is not None:
                    cost[row.side] = cost.get(row.side, 0.0) + q * float(row.limit_price)
                    size[row.side] = size.get(row.side, 0.0) + q
            else:  # an exit: selling the held side
                net += -q if row.side == limm.SIDE_YES else q
        entry = {side: int(round(cost[side] / size[side])) for side in cost if size.get(side)}
        return net, entry, any_filled

    @staticmethod
    def _hours_to_close(session, ticker: str, now: datetime) -> float | None:
        close = repo.market_close_time(session, ticker)
        return None if close is None else (close - now).total_seconds() / 3600.0

    # --- pieces ---------------------------------------------------------------

    def _observe_rewards(self, session, now: datetime) -> dict | None:
        """Take one balance reading and record what of its change we can explain.

        WHY THIS LIVES HERE AND NOT IN THE SHADOW COLLECTOR. The first version put it there,
        and it could not work: the collector is handed an `IncentiveReadOnlyKalshi`, a wrapper
        that exposes exactly the market-data GETs the research tape needs and nothing else. It
        has no `get_balance`, and production said so — `AttributeError: 'IncentiveReadOnlyKalshi'
        object has no attribute 'get_balance'`, twice, in the first fifteen minutes after deploy.

        That wrapper is a boundary, not an oversight: the shadow is research and has no business
        reading our portfolio. Widening it to reach the balance would have been the easy fix and
        the wrong one. The ledger measures REAL MONEY, so it belongs with the book that spends
        it — this runner, which already holds the authenticated client for exactly that reason.

        Failure is a recorded event, never an exception: a missed reading is recoverable, and
        this must never be able to stop the book from placing or cancelling."""
        every = float(getattr(self.settings, "liquidity_incentive_balance_seconds", 900.0))
        if (self._last_balance_at is not None
                and (now - self._last_balance_at).total_seconds() < every):
            return None
        self._last_balance_at = now
        try:
            prev = store.latest_balance_observation(session)
            prev_balance = None if prev is None else int(prev.balance_cents)
            prev_at = None if prev is None else prev.at
            prev_notes = (prev.notes_json if prev is not None
                          and isinstance(prev.notes_json, dict) else {})
            prev_positions = prev_notes.get("positions")
            balance_cents, rec, notes = rl.observe(
                self.client, prev_balance_cents=prev_balance, since=prev_at,
                prev_positions=prev_positions if isinstance(prev_positions, dict) else None)
            store.record_balance_observation(
                session, at=now, balance_cents=balance_cents,
                prev_at=prev_at, prev_balance_cents=prev_balance,
                reconciliation=rec, notes=notes or None)
            if rec is not None and rec.is_material and not (notes or {}).get(
                    "residual_untrustworthy"):
                # Cash moved that no trade and no settlement accounts for, the window was fully
                # explained, and it is too small to be a transfer. Announced in the collector's
                # event stream so it is visible without querying the new table.
                store.record_event(session, kind=EV_REWARD_RESIDUAL, at=now,
                                   detail_json=rec.as_dict())
            return None if rec is None else rec.as_dict()
        except Exception as exc:  # noqa: BLE001 — a missed reading must not stop the book
            logger.warning("incentive reward ledger: %s: %s", type(exc).__name__, exc)
            try:
                store.record_event(session, kind="loop_error", at=now,
                                   detail=f"reward_ledger: {type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 — recording a failure must not raise either
                logger.exception("incentive reward ledger: could not record its own failure")
            return None

    def _load_cooldown(self, session, now: datetime) -> None:
        """Restore the cooldown map from the newest persisted copy, once per process. Any failure
        leaves it empty — the pre-§9.44 behaviour — rather than stopping the cycle."""
        self._cooldown_loaded = True
        try:
            row = session.scalar(
                select(m.SystemEvent)
                .where(m.SystemEvent.component == COOLDOWN_COMPONENT)
                .order_by(m.SystemEvent.id.desc()).limit(1))
            until = (row.raw_json or {}).get("until", {}) if row is not None else {}
            for ticker, iso in until.items():
                t = _aware(datetime.fromisoformat(iso))
                if t is not None and t > now:
                    self._book_refused_until[ticker] = t
        except Exception:  # noqa: BLE001 — a bad saved copy must not stop the book
            logger.warning("incentive live: cooldown restore failed", exc_info=True)

    def _save_cooldown(self, session, now: datetime) -> None:
        if not self._book_refused_until:
            return
        last = self._cooldown_saved_at
        if last is not None and (now - last).total_seconds() < COOLDOWN_PERSIST_EVERY_SECONDS:
            return
        self._cooldown_saved_at = now
        try:
            repo.log_system_event(
                session, level="info", component=COOLDOWN_COMPONENT,
                message=f"{len(self._book_refused_until)} markets cooling down",
                raw={"until": {t: u.isoformat() for t, u in self._book_refused_until.items()}})
        except Exception:  # noqa: BLE001 — persistence is a convenience, never the guard
            logger.warning("incentive live: cooldown save failed", exc_info=True)

    def _order_by_expected_reward(self, candidates: list, now: datetime, max_fetch: int) -> list:
        """§9.69 (operator, 2026-10-24: "choose markets by expected rewards, not newest first"):
        the fetch order. Half the budget re-reads the markets with the highest remembered
        projected payout (reward $/h x hours the programme has left), so the best books are
        fresh when the ranking picks; the rest explores markets not yet estimated, newest
        first. Markets estimated at nothing go last. Then the post-fetch ranking
        (`rank_by_projected_payout`) chooses among fresh books."""
        horizon = now - timedelta(seconds=REWARD_MEMORY_SECONDS)
        self._reward_seen = {t: v for t, v in self._reward_seen.items() if v[1] >= horizon}
        known, unknown, zero = [], [], []
        for p in candidates:
            seen = self._reward_seen.get(p.market_ticker)
            if seen is None:
                unknown.append(p)
                continue
            end = _aware(p.end_date)
            hours_left = 0.0 if end is None else max(0.0, (end - now).total_seconds() / 3600.0)
            payout = seen[0] * hours_left
            (known if payout > 0 else zero).append((payout, p))
        known.sort(key=lambda t: -t[0])
        best = [p for _v, p in known]
        n_best = min(len(best), max(1, max_fetch // 2))
        return best[:n_best] + unknown + best[n_best:] + [p for _v, p in zero]

    def _remember_rewards(self, built: list[dict], quotable: list, now: datetime) -> None:
        """§9.69: record the estimated reward rate of every book fetched this cycle; a book that
        produced no quote is remembered at zero."""
        rates = {c["market_ticker"]: quote_rate((c, q)) for c, q in quotable}
        for c in built:
            self._reward_seen[c["market_ticker"]] = (rates.get(c["market_ticker"], 0.0), now)

    def _candidate_programs(self, session, *, now: datetime,
                            excluded_series: frozenset[str] = frozenset()) -> list:
        """Current liquidity programs worth fetching a book for, NEWEST program first.

        The close-time window is applied here as well as in `live.build_pair_quote`, so the
        bounded book fetches are never spent on a market the decision would refuse anyway.
        Newest first because a program pays most in its first hours (§9.42); it was
        soonest-closing first under §9.37.

        Excluded series are dropped HERE, before any book is fetched. Filtering them only in
        the decision layer spent the whole bounded fetch budget on markets that could never be
        quoted: on 2026-09-25 the eight soonest-closing programmes were all in excluded series,
        so every cycle fetched the same eight, refused all eight, and placed nothing (§9.39)."""
        rows = self._running_programs(session, now)
        out = []
        for row in rows:
            if row.market_status and row.market_status not in ("active", "open"):
                continue
            if (row.market_ticker or "").split("-", 1)[0].upper() in excluded_series:
                continue
            if limm.EVENT_MENTION_ONLY:
                # §9.55: event-tied mention markets (and §9.69's month-window series) only, and
                # only with time left to earn before the bot must step aside for the event.
                cutoff = _entry_cutoff(row)
                if cutoff is None or (cutoff - now).total_seconds() / 3600.0 \
                        < limm.MIN_HOURS_BEFORE_EVENT:
                    continue
            if self._contagion_blocks(row.market_ticker, row.event_ticker):
                continue            # §9.63: a recent fill in this event or on this word
            end = _aware(row.end_date)
            if end is None:
                continue
            if (end - now).total_seconds() / 3600.0 < limm.MIN_PROGRAM_HOURS_REMAINING:
                continue
            close = _aware(row.close_time)
            if close is None:
                continue
            to_close = (close - now).total_seconds() / 3600.0
            if not (limm.MIN_HOURS_TO_CLOSE <= to_close <= limm.MAX_HOURS_TO_CLOSE):
                continue
            start = _aware(row.start_date)
            age = (now - start).total_seconds() / 3600.0 if start is not None else float("inf")
            out.append((age, to_close, row))
        out.sort(key=lambda r: (r[0], r[1]))
        return [row for _a, _c, row in out]

    def _open_twin(self, session, leg) -> bool:
        """The twin's mirror of one leg just placed: same ticker, side, price and size, assumed
        filled. A pair is mirrored as its two legs, so the twin records the both-filled case —
        which settles to exactly the pair's locked edge. The twin never exits early: a live exit
        happens only after a SINGLE-leg fill, which is precisely the divergence the twin exists
        to expose. Returns False when no twin is configured or armed."""
        harness = self.twin_harness
        if harness is None or not harness.enabled:
            return False
        twin_tag = harness.twin_of(limm.LIVE_TAG)
        if not twin_tag or not harness.active_for(limm.LIVE_TAG):
            return False
        harness.open_twin_entry(
            session, twin_tag=twin_tag, ticker=leg.market_ticker, side=leg.side,
            price=int(leg.price_cents), quantity=int(leg.quantity),
            note=f"twin of incentive leg {leg.side}@{leg.price_cents}c x{leg.quantity}",
        )
        return True


__all__ = ["IncentiveLiveRunner", "PLACED", "SKIP_BOOK_ERROR", "SKIP_NO_SLOTS",
           "candidate_from_book"]
