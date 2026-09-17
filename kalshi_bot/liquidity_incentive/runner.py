"""Phase 1a — the live smoke-test RUNNER: the one cycle that turns a decision into an order.

`live.py` decides WHICH resting bid the smoke test would place and whether it may be placed;
`LiveExecutor.mirror_incentive_entry` places it. This module is the thing that runs between
them once per live cycle: it picks the candidate markets, reads their books fresh, ranks the
quotes, and places up to the strategy's own open-order cap — writing the paper TWIN's mirror
entry at the same instant, because a live canary without its twin is an unmeasurable one.

WHY IT READS BOOKS OVER REST RATHER THAN FROM THE SHADOW COLLECTOR
------------------------------------------------------------------
The shadow collector (`collector.py`) holds live WebSocket books, but it runs wherever
`LIQUIDITY_INCENTIVE_SHADOW_ENABLED` is set — today the `evo` service, deliberately, so a
research instrument never shares a process with real money. Reading its books would make the
live path depend on another service's memory. So this runner does a bounded number of plain
GETs on its own candidates: fewer markets, fresher data, no cross-service coupling.

The bound is the point. `LIQUIDITY_INCENTIVE_LIVE_MAX_BOOK_FETCHES` caps the GETs per cycle,
and candidates are ordered by soonest program end before any book is fetched — rewards are
credited only after a program ends, so the soonest-ending program is the one whose payout leg
of the test is confirmable first.

WHAT THIS RUNNER WILL NOT DO
----------------------------
It never cancels a resting order to game a metric. Orders leave the book by the shared paths
only: the executor's per-order timeout, `drain_stood_down_books` when the allowlist drops the
tag, or a fill. There is no "pull the quote" branch here, because a quote we pull the moment
it might trade is not liquidity, and this strategy's whole premise is that it is.

It is INERT by default: `LIQUIDITY_INCENTIVE_LIVE_ENABLED` is off, and even on, the executor
refuses every order unless the tag is in `LIVE_STRATEGIES` with both master switches set.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .. import repository as repo
from ..scanner.metrics import parse_orderbook
from . import live as limm
from . import programs as progs
from .scoring import discount_factor, side_score

logger = logging.getLogger(__name__)

#: Outcome codes recorded per candidate, so a cycle that placed nothing still says why.
PLACED = "placed"
SKIP_BOOK_ERROR = "book_error"
SKIP_NO_SLOTS = "no_slots"


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
    }


class IncentiveLiveRunner:
    """One cycle of the one-sided live smoke test. Holds no session and no state worth keeping."""

    def __init__(self, client, settings, twin_harness=None):
        self.client = client
        self.settings = settings
        self.twin_harness = twin_harness

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
        """Place up to the remaining open-order slots. Returns a per-cycle summary; never raises
        for a bad book or a refused order — both are recorded outcomes, not exceptions."""
        now = now or datetime.now(timezone.utc)
        summary: dict = {"armed": False, "considered": 0, "fetched": 0, "placed": 0,
                         "twin_opened": 0, "outcomes": {}}
        if not self.armed(executor):
            return summary
        summary["armed"] = True

        open_now = repo.count_live_book_open(session, limm.LIVE_TAG)
        exposure_now = repo.live_strategy_exposure(session, limm.LIVE_TAG)
        slots = limm.MAX_OPEN_ORDERS - int(open_now)
        if slots <= 0:
            summary["outcomes"][SKIP_NO_SLOTS] = 1
            return summary

        excluded = self.excluded_series()
        candidates = self._candidate_programs(session, now=now)
        summary["considered"] = len(candidates)
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

        ranked = limm.rank_candidates(built, excluded_series=excluded)
        # Every candidate that produced no quote is still reported, by its refusal code, so a
        # cycle that placed nothing says which cap or which book stopped it.
        placeable = {c["market_ticker"] for c, _ in ranked}
        for c in built:
            if c["market_ticker"] in placeable:
                continue
            refusal = limm.build_live_quote(
                market_ticker=c["market_ticker"], best_yes_bid=c["best_yes_bid"],
                best_no_bid=c["best_no_bid"],
                yes_resting_total=c["yes_resting_total"], no_resting_total=c["no_resting_total"],
                target_size=c["target_size"],
                reference_price_by_side=c["reference_price_by_side"],
                program_hours_remaining=c["program_hours_remaining"],
                excluded_series=excluded,
            )
            code = getattr(refusal, "code", "unknown")
            summary["outcomes"][code] = summary["outcomes"].get(code, 0) + 1

        for candidate, quote in ranked:
            if slots <= 0:
                summary["outcomes"][SKIP_NO_SLOTS] = \
                    summary["outcomes"].get(SKIP_NO_SLOTS, 0) + 1
                break
            if exposure_now + quote.collateral_usd > limm.MAX_STRATEGY_EXPOSURE_USD:
                summary["outcomes"][limm.REFUSE_EXPOSURE_CAP] = \
                    summary["outcomes"].get(limm.REFUSE_EXPOSURE_CAP, 0) + 1
                break
            outcome = executor.mirror_incentive_entry(
                session, strategy=limm.LIVE_TAG,
                event_ticker=candidate.get("event_ticker") or quote.market_ticker,
                ticker=quote.market_ticker, quote=quote, account_state=account_state,
            )
            summary["outcomes"][outcome] = summary["outcomes"].get(outcome, 0) + 1
            if outcome != PLACED:
                continue
            summary["placed"] += 1
            slots -= 1
            exposure_now += quote.collateral_usd
            if self._open_twin(session, quote):
                summary["twin_opened"] += 1
        return summary

    # --- pieces ---------------------------------------------------------------

    def _candidate_programs(self, session, *, now: datetime) -> list:
        """Current liquidity programs worth fetching a book for, soonest-ending first.

        The ordering is the test's own priority, not an economic ranking: Kalshi credits a
        liquidity reward only after the program ends, so the program ending soonest is the one
        that can confirm the payout leg first. Economics are decided per book by
        `live.build_live_quote`, which is the only place a cap is applied."""
        rows = progs.current_programs(session, now=now, liquidity_only=True)
        out = []
        for row in rows:
            if row.market_status and row.market_status not in ("active", "open"):
                continue
            end = _aware(row.end_date)
            if end is None:
                continue
            hours = (end - now).total_seconds() / 3600.0
            if hours < limm.MIN_PROGRAM_HOURS_REMAINING:
                continue
            out.append((hours, row))
        out.sort(key=lambda hr: hr[0])
        return [row for _h, row in out]

    def _open_twin(self, session, quote) -> bool:
        """The twin's mirror of the order just placed: same ticker, same side, same price, same
        size, assumed filled. Returns False when no twin is configured or armed — which is a
        state the arming path refuses to create, but this must not crash if it ever happens."""
        harness = self.twin_harness
        if harness is None or not harness.enabled:
            return False
        twin_tag = harness.twin_of(limm.LIVE_TAG)
        if not twin_tag or not harness.active_for(limm.LIVE_TAG):
            return False
        harness.open_twin_entry(
            session, twin_tag=twin_tag, ticker=quote.market_ticker, side=quote.side,
            price=int(quote.price_cents), quantity=int(quote.quantity),
            note=f"twin of incentive bid {quote.side}@{quote.price_cents}c x{quote.quantity}",
        )
        return True


__all__ = ["IncentiveLiveRunner", "PLACED", "SKIP_BOOK_ERROR", "SKIP_NO_SLOTS",
           "candidate_from_book"]
