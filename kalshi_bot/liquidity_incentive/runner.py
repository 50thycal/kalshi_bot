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

from sqlalchemy import select

from .. import models as m
from .. import repository as repo
from ..scanner.metrics import parse_orderbook
from . import live as limm
from . import programs as progs
from . import reward_ledger as rl
from . import store
from .scoring import discount_factor, estimate, side_score

logger = logging.getLogger(__name__)

#: Outcome codes recorded per candidate, so a cycle that placed nothing still says why.
PLACED = "placed"
SKIP_BOOK_ERROR = "book_error"
SKIP_NO_SLOTS = "no_slots"

#: A market whose BOOK refused a pair is skipped for this long before its book is fetched again.
#: Without it the bounded fetch budget is spent on the same first-ranked books every cycle: on
#: 2026-09-30 the eight newest programmes were all `book_too_deep`, so each cycle fetched those
#: eight, refused all eight, and never reached the other ~1,150 (§9.43; §9.39 was the same shape).
BOOK_REFUSAL_COOLDOWN_SECONDS = 3600.0
#: The cooldown map is also written to `system_events` (at most this often) and read back on the
#: first cycle after a restart, so a redeploy does not send the fetch budget back to the top of
#: the ranking (§9.44). Production redeploys several times a day as other work merges.
COOLDOWN_COMPONENT = "limm_book_cooldown"
COOLDOWN_PERSIST_EVERY_SECONDS = 300.0
BOOK_REFUSAL_CODES = frozenset({
    limm.REFUSE_NO_BOOK, limm.REFUSE_NOT_TWO_SIDED, limm.REFUSE_TARGET_NOT_MET,
    limm.REFUSE_TOO_EXPENSIVE, limm.REFUSE_BOOK_TOO_DEEP, limm.REFUSE_POST_ONLY_CROSS,
    limm.REFUSE_NO_EDGE,
})

#: A balance reading whose unexplained remainder is worth a human look (see `reward_ledger`).
EV_REWARD_RESIDUAL = "reward_residual"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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


def est_reward_per_hour(candidate: dict, *, yes_price: int, no_price: int, qty: float,
                        own_yes: dict[int, float] | None = None,
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
        our_yes_price=int(yes_price), our_yes_size=float(qty),
        our_no_price=int(no_price), our_no_size=float(qty),
        period_reward_usd_value=candidate.get("period_reward_usd"),
        period_seconds=candidate.get("period_seconds"))
    return float(est.reward_per_hour_usd or 0.0)


class IncentiveLiveRunner:
    """One cycle of the two-sided live book: manage what is held, then quote new pairs."""

    def __init__(self, client, settings, twin_harness=None):
        self.client = client
        self.settings = settings
        self.twin_harness = twin_harness
        #: When the reward ledger last took a reading. None until the first cycle, which is what
        #: makes the very first cycle after a deploy write the anchor row immediately.
        self._last_balance_at: datetime | None = None
        #: Tickers already reported as past their exit-attempt cap, so the log says it once.
        self._exit_exhausted: set[str] = set()
        #: Ticker -> when its book may be fetched again, after the book refused a pair.
        self._book_refused_until: dict[str, datetime] = {}
        self._cooldown_loaded = False
        self._cooldown_saved_at: datetime | None = None

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

        # Exits before entries: a cycle's first job is the capital already out, and an exit
        # frees the slot and the budget an entry below might use.
        summary["managed"] = self._manage_positions(session, executor, now)
        retired = self._retire_unpaid_pairs(session, executor, now)
        if retired:
            summary["outcomes"]["program_ended"] = retired

        open_now = repo.count_live_book_open_tradeable(session, limm.LIVE_TAG, now)
        exposure_now = repo.live_strategy_exposure(session, limm.LIVE_TAG)
        slots = limm.MAX_OPEN_ORDERS - int(open_now)
        # Full book: carry on only if a held pair is old enough to be swapped for a better one.
        stale = self._stale_held_pairs(session, now) if slots <= 0 else {}
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
        max_fetch = max(1, int(getattr(self.settings,
                                       "liquidity_incentive_live_max_book_fetches", 8)))
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
        blocked_events = repo.live_book_open_events(session, limm.LIVE_TAG)
        for c in built:
            ev = c.get("event_ticker")
            if ev and ev not in blocked_events and repo.event_has_open_live_position(session, ev):
                blocked_events.add(ev)
        ranked = limm.rank_candidates(built, excluded_series=excluded,
                                      blocked_event_tickers=frozenset(blocked_events))
        # Every candidate that produced no pair is still reported, by its refusal code, so a
        # cycle that placed nothing says which cap or which book stopped it.
        placeable = {c["market_ticker"] for c, _ in ranked}
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
                blocked_events.add(placed_event)
            for leg in legs:
                exposure_now += leg.collateral_usd
                if self._open_twin(session, leg):
                    summary["twin_opened"] += 1
        return summary

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
        running = {p.market_ticker for p in progs.current_programs(
            session, now=now, liquidity_only=True)
            if p.end_date is not None and _aware(p.end_date) > now}
        n = 0
        for ticker in resting:
            if ticker in running:
                continue
            if executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker,
                                                reason="program_ended"):
                n += 1
        return n

    def _held_reward_per_hour(self, ticker: str, working: list, programs: dict,
                              now: datetime) -> float | None:
        """A held pair's estimated reward per hour on its CURRENT book. 0.0 when its programme
        is gone or only one leg rests; None when the book cannot be read (then it is kept)."""
        program = programs.get(ticker)
        if program is None:
            return 0.0
        prices = {r.side: int(r.limit_price) for r in working}
        if limm.SIDE_YES not in prices or limm.SIDE_NO not in prices:
            return 0.0                  # a lone leg is not a qualifying two-sided quote
        try:
            ob = self.client.get_orderbook(ticker)
        except Exception:  # noqa: BLE001 — unknown value: keep the pair
            return None
        c = candidate_from_book(program, ob, now=now)
        qty = max(float(r.quantity) for r in working)
        return est_reward_per_hour(c, yes_price=prices[limm.SIDE_YES],
                                   no_price=prices[limm.SIDE_NO], qty=qty,
                                   own_yes={prices[limm.SIDE_YES]: qty},
                                   own_no={prices[limm.SIDE_NO]: qty})

    def _replace_stale_pair(self, session, executor, stale: dict, ranked: list,
                            now: datetime, summary: dict):
        """Cancel the weakest stale held pair when a new candidate is estimated to earn at least
        `REPLACE_MIN_GAIN_MULTIPLE` times its reward per hour, and return that candidate.
        None leaves every held pair resting."""
        def rate(cq) -> float:
            c, q = cq
            return est_reward_per_hour(c, yes_price=q.yes.price_cents,
                                       no_price=q.no.price_cents, qty=q.quantity)

        fresh = [cq for cq in ranked if cq[0]["market_ticker"] not in stale]
        if not fresh:
            return None
        best = max(fresh, key=rate)
        best_rate = rate(best)
        if best_rate <= 0:
            return None
        programs = {p.market_ticker: p for p in progs.current_programs(
            session, now=now, liquidity_only=True)}
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
        if best_rate <= held_rate or best_rate < held_rate * limm.REPLACE_MIN_GAIN_MULTIPLE:
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
            if working:
                return "holding"
            price = limm.exit_leg_price(entry_cents=entry,
                                        best_opposite_bid=best[limm.other_side(held_side)])
            if price is None:
                return "holding"
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
        if not executor.cancel_incentive_orders(session, strategy=limm.LIVE_TAG, ticker=ticker):
            return f"{rule}:cancel_pending"
        res = executor.close_incentive_position(
            session, strategy=limm.LIVE_TAG, ticker=ticker, held_side=held_side, qty=qty,
            mark_bid_cents=mark, rule=rule)
        return f"{rule}:{res}"

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
        rows = progs.current_programs(session, now=now, liquidity_only=True)
        out = []
        for row in rows:
            if row.market_status and row.market_status not in ("active", "open"):
                continue
            if (row.market_ticker or "").split("-", 1)[0].upper() in excluded_series:
                continue
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
