"""WCPROP book tracker (paper) — ride the tournament-winner ladder's lag after a decisive
World Cup match result.

Thesis (scripts/xmarket_wc.py): when a KXWCGAME/KXWCROUND match settles, the involved teams'
KXMENWORLDCUP winner-ladder contracts should still be repricing — the winner market lags the
decisive match news. The offline probe measured each winner contract's realized [T, T+H] move
and traded its direction; a LIVE book cannot see T+H, so this book uses a causal proxy:

  1. TRIGGER — has any match in wcprop_match_series settled within the recent window
     [now - lookback, now - min_age]? (min_age ~ the probe's +5min entry offset.) Fresh
     decisive news in the tournament is the only thing that opens the trading window.
  2. SIGNAL — while triggered, scan the winner ladder and find rungs that ARE actively
     repricing: |mid_now - mid_last_cycle| >= min_move_cents (the causal, no-lookahead
     "still moving" signal). Enter TAKER in the move direction (buy YES if the rung is
     rising, buy NO if falling) on liquid, tight-spread rungs away from the 0/100 rails.
  3. EXIT — NOT held to settlement (that swaps the ~2h lag residual for a weeks-long
     tournament bet). The shared paper engine times the position out at wcprop_hold_minutes,
     closing at the then-current bid (see paper/engine.py::_mark_or_exit).

Per-cycle mids are remembered on the tracker instance to form the cycle-over-cycle move; a
worker restart just loses one cycle of history (no entries until the next cycle re-seeds it).
Still EXPLORATORY (the probe's P1/P2/P3 gates are unproven); running it as a paper book is how
the settled-trade data that decides it accumulates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import repository as repo
from ..config import Settings
from ..kalshi.errors import AuthError
from ..obs.series_fetch import SeriesFetchResult, fetch_markets_by_series
from ..paper.engine import kalshi_fee
from ..scanner.metrics import compute_metrics, compute_time_to_close

logger = logging.getLogger(__name__)


def _price_c(market: dict, key: str) -> float | None:
    v = market.get(f"{key}_dollars")
    if v not in (None, ""):
        try:
            return float(v) * 100.0
        except (TypeError, ValueError):
            return None
    v = market.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class WcPropCycleSummary:
    recent_settled: int = 0      # match markets settled in the trigger window this cycle
    triggered: bool = False
    winner_rungs: int = 0        # winner-ladder rungs scanned
    moved: int = 0               # rungs whose cycle-over-cycle move cleared min_move
    opened: int = 0
    already_open: int = 0
    capped: int = 0
    skipped_illiquid: int = 0
    skipped_spread: int = 0
    per_side: dict[str, int] = field(default_factory=dict)
    # The series-addressed fetch's own report (XOS-000004).
    fetch: SeriesFetchResult = field(default_factory=SeriesFetchResult)

    def bump_side(self, side: str) -> None:
        self.per_side[side] = self.per_side.get(side, 0) + 1


class WcPropTracker:
    STRATEGY = "wcprop"

    def __init__(self, client, settings: Settings):
        self.client = client
        self.settings = settings
        # cycle-over-cycle winner-ladder mids: {ticker: mid_cents} from the previous cycle.
        self._prev_mid: dict[str, float] = {}

    # -- trigger: did a match settle recently? --------------------------------
    def _recent_settled(self) -> int:
        s = self.settings
        now = datetime.now(timezone.utc)
        min_close_ts = int((now.timestamp()) - s.wcprop_lookback_minutes * 60.0)
        count = 0
        # `warn=False`: an empty settled window is this scan's NORMAL state (no
        # match has finished recently), so a cycle-level empty warning would be
        # pure noise. A fetch FAILURE still warns, from inside the helper.
        fetched = fetch_markets_by_series(
            self.client,
            s.wcprop_match_series_list,
            book="wcprop",
            status="settled",
            max_pages=4,
            min_close_ts=min_close_ts,
            log=logger,
            warn=False,
        )
        for mkt in fetched.markets:
            if (mkt.get("result") or "").lower() not in ("yes", "no"):
                continue
            ttc = compute_time_to_close(mkt.get("close_time"), now=now)
            if ttc is None:
                continue
            age_min = -ttc / 60.0  # settled -> close_time in the past -> ttc negative
            if s.wcprop_min_age_minutes <= age_min <= s.wcprop_lookback_minutes:
                count += 1
        return count

    # -- winner ladder scan ---------------------------------------------------
    def _winner_markets(self) -> SeriesFetchResult:
        """The winner ladder, through the shared series-addressed fetch.

        Same pagination and failure handling as before; what is new is that a
        winner series which returns HTTP 200 with an empty list is COUNTED and
        reported rather than passing for a healthy scan (XOS-000004).
        """
        return fetch_markets_by_series(
            self.client,
            [self.settings.wcprop_winner_series],
            book="wcprop",
            max_pages=6,
            log=logger,
        )

    def run_once(self, session) -> WcPropCycleSummary:
        s = self.settings
        summ = WcPropCycleSummary()

        summ.recent_settled = self._recent_settled()
        summ.triggered = summ.recent_settled > 0

        summ.fetch = self._winner_markets()
        winners = summ.fetch.markets
        summ.winner_rungs = len(winners)
        open_count = repo.count_open_paper_positions(session, self.STRATEGY)
        open_tickers = repo.open_paper_position_tickers(session, self.STRATEGY)

        new_prev: dict[str, float] = {}
        for mkt in winners:
            ticker = mkt.get("ticker") or ""
            if not ticker:
                continue
            yb = _price_c(mkt, "yes_bid")
            ya = _price_c(mkt, "yes_ask")
            if yb is None or ya is None or not (0 <= yb <= 100) or not (0 < ya <= 100):
                continue
            mid = (yb + ya) / 2.0
            new_prev[ticker] = mid

            # No trading unless a match just settled (fresh decisive news opened the window).
            if not summ.triggered:
                continue
            prev = self._prev_mid.get(ticker)
            if prev is None:
                continue  # need a prior mid to measure the cycle-over-cycle move
            move = mid - prev
            if abs(move) < s.wcprop_min_move_cents:
                continue
            summ.moved += 1
            # skip rungs pinned to the rails (a decided team — nothing left to reprice)
            if not (2.0 <= mid <= 98.0):
                continue
            if ticker in open_tickers:
                summ.already_open += 1
                continue
            if open_count >= s.wcprop_max_open_positions:
                summ.capped += 1
                continue

            try:
                ob = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "wcprop: orderbook fetch failed",
                    extra={"extra_fields": {"ticker": ticker, "error": str(exc)[:200]}},
                )
                continue
            metrics = compute_metrics(mkt, ob, top_n=s.orderbook_depth)
            if not metrics.two_sided or metrics.spread is None:
                summ.skipped_illiquid += 1
                continue
            if metrics.spread > s.wcprop_spread_cap_cents:
                summ.skipped_spread += 1
                continue

            # taker entry in the move direction: rising rung -> buy YES at the yes-ask;
            # falling rung -> buy NO at the no-ask (== sell the fading YES).
            if move > 0:
                side, price, depth = "yes", metrics.best_yes_ask, metrics.depth_at_best_ask
            else:
                side, price, depth = "no", metrics.best_no_ask, metrics.depth_at_best_bid
            if price is None or not (0 < price < 100):
                summ.skipped_illiquid += 1
                continue
            qty = min(s.wcprop_order_size, depth or 0)
            if qty <= 0:
                summ.skipped_illiquid += 1
                continue

            fee = kalshi_fee(price, qty, s.paper_fees_enabled)
            repo.create_paper_trade(
                session,
                signal_id=None,
                ticker=ticker,
                strategy=self.STRATEGY,
                side=side,
                action="buy",
                assumed_price=price,
                quantity=qty,
                fill_assumption=(
                    f"[{self.STRATEGY}] buy {side} @ {price:.0f}c move {move:+.0f}c mid {mid:.0f}"
                ),
                entry_fee=fee,
                model_probability=(mid / 100.0),
                edge=move / 100.0,
            )
            repo.open_paper_position_for_trade(
                session, ticker=ticker, strategy=self.STRATEGY, side=side,
                quantity=qty, avg_price=price,
            )
            open_count += 1
            open_tickers.add(ticker)
            summ.opened += 1
            summ.bump_side(side)

        self._prev_mid = new_prev
        return summ
