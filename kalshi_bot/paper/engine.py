"""Paper trading engine.

Simulates trades from the scanner's candidate signals so we can measure fill
quality, spread/fee cost, and resolution outcomes — without placing real orders.

- Strategies run as **parallel books** (one position per (market, strategy)): a
  directional control plus `momentum` and `ladder` edge models (see strategies.py).
- Risk: every entry passes `RiskManager.evaluate(for_paper=True)` — the live-only
  gates are skipped but all quality/liquidity/exposure gates apply, measured against
  a simulated bankroll and that strategy's open positions.
- Exit: settlement (payoff 0/100), or — before settlement — a max-hold timeout or
  optional take-profit/stop-loss, closed conservatively at the current bid.
- Fees: Kalshi's `ceil(0.07 * C * P * (1-P))` per trade, on entry and early-exit
  sells (never on settlement).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import repository as repo
from ..config import Settings
from ..kalshi.client import KalshiClient
from ..kalshi.errors import AuthError
from ..risk.manager import RiskManager
from ..scanner.metrics import MarketMetrics, compute_metrics
from .strategies import (
    EntryProposal,
    directional_proposal,
    ladder_proposals,
    momentum_proposal,
)

logger = logging.getLogger(__name__)


@dataclass
class CandidateInput:
    signal: object
    signal_id: int | None
    metrics: MarketMetrics
    market: dict


@dataclass
class PaperCycleSummary:
    considered: int = 0
    opened: int = 0
    no_fill: int = 0
    already_open: int = 0
    risk_blocked: int = 0
    marked: int = 0
    closed_settled: int = 0
    closed_timeout: int = 0
    closed_tp: int = 0
    closed_sl: int = 0
    closed_void: int = 0
    realized_pnl: float = 0.0
    open_positions: int = 0
    per_strategy: dict[str, dict[str, int]] = field(default_factory=dict)

    def bucket(self, strategy: str) -> dict[str, int]:
        return self.per_strategy.setdefault(
            strategy,
            {"considered": 0, "proposed": 0, "opened": 0, "already_open": 0,
             "risk_blocked": 0, "no_fill": 0},
        )

    @property
    def fillability_rate(self) -> float | None:
        denom = self.opened + self.no_fill
        return round(self.opened / denom, 3) if denom else None


# Kalshi's published coefficients (July 2026 fee schedule). The maker rate is a quarter of the
# taker rate; scripts/mmsell_fee_recon.py measures both against real `fills.fee` rows.
TAKER_COEFF = 0.07
MAKER_COEFF = 0.0175


def kalshi_fee(price_cents: int | None, qty: int, enabled: bool = True, *,
               maker: bool = False) -> float:
    """Kalshi trading fee in dollars: ceil(coeff * C * P * (1-P)) rounded up to a cent.

    `maker=True` is for an order that RESTS (`post_only: true`) and is filled by someone else
    crossing to it. Passing it is not a refinement — for the mmsell/theta books it is the
    difference between a correct number and one that deletes half the edge:

        1 contract at 93c, taker coeff:  0.07 * 0.93 * 0.07 = 0.455c -> ceil -> 1.00c
        1 contract at 93c, maker coeff: 0.0175 * 0.93 * 0.07 = 0.114c -> ceil -> 1.00c (!)

    Note the ceiling swallows the coefficient entirely at a 1-contract clip, which is why
    `enabled` alone could never fix this and why the measured maker cost is ~0.003c/ct rather
    than either model: on our series Kalshi appears not to charge maker fees at all (see the
    reconciliation in scripts/mmsell_fee_recon.py, n=366). So a maker fill is billed the
    published maker rate WITHOUT the per-order ceiling — rounding a 0.1c fee up to a whole cent
    is what produced the 8x overcharge, not the coefficient.

    Exits keep the taker rate: selling into the bid crosses the spread, so an early exit really
    does pay taker. Only the resting ENTRY is maker.
    """
    if not enabled or not qty or price_cents is None:
        return 0.0
    p = price_cents / 100.0
    if maker:
        # No ceiling: a sub-cent maker fee is a sub-cent maker fee. Still rounded to the cent
        # for storage sanity, but to NEAREST rather than up.
        return round(MAKER_COEFF * qty * p * (1 - p), 4)
    return math.ceil(TAKER_COEFF * qty * p * (1 - p) * 100) / 100.0


def _entry_depth(metrics: MarketMetrics, side: str) -> int:
    # Buying YES is filled by resting NO bids (depth_at_best_ask); buying NO by YES bids.
    return metrics.depth_at_best_ask if side == "yes" else metrics.depth_at_best_bid


def _exit_bid(metrics: MarketMetrics, side: str) -> int | None:
    # Selling the held side is filled at that side's best bid.
    return metrics.best_yes_bid if side == "yes" else metrics.best_no_bid


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PaperTradingEngine:
    def __init__(self, client: KalshiClient, settings: Settings, risk: RiskManager):
        self.client = client
        self.settings = settings
        self.risk = risk
        self.summary = PaperCycleSummary()

    # -- entries (batch, multi-strategy) ----------------------------------
    def consider_candidates(
        self, session, candidates: list[CandidateInput], account_state: dict | None = None
    ) -> PaperCycleSummary:
        s = self.settings
        strategies = s.paper_strategy_list

        # Ladder is relative-value across sibling markets, so precompute it once.
        ladder_map: dict[str, EntryProposal] = {}
        if "ladder" in strategies:
            ladder_map = ladder_proposals(
                [(c.signal.ticker, c.metrics) for c in candidates], s
            )

        for c in candidates:
            self.summary.considered += 1
            for strategy in strategies:
                b = self.summary.bucket(strategy)
                b["considered"] += 1
                proposal = self._proposal(session, strategy, c, ladder_map)
                if proposal is None:
                    continue
                b["proposed"] += 1
                self._open_if_clear(session, strategy, c, proposal, account_state)
        return self.summary

    def _proposal(
        self, session, strategy: str, c: CandidateInput, ladder_map: dict[str, EntryProposal]
    ) -> EntryProposal | None:
        if strategy in ("buy_favorite", "buy_yes", "buy_no"):
            return directional_proposal(c.metrics, strategy)
        if strategy in ("momentum", "reversion"):
            return momentum_proposal(
                self._history(session, c.signal.ticker),
                c.metrics,
                self.settings,
                direction=strategy,
            )
        if strategy == "ladder":
            return ladder_map.get(c.signal.ticker)
        return None

    def _history(self, session, ticker: str) -> list[tuple[float, float]]:
        rows = repo.recent_midpoints(session, ticker, self.settings.paper_momentum_lookback_hours)
        if len(rows) < 2:
            return []
        first = rows[0][0]
        return [((dt - first).total_seconds() / 3600.0, mid) for dt, mid in rows]

    def _open_if_clear(
        self,
        session,
        strategy: str,
        c: CandidateInput,
        proposal: EntryProposal,
        account_state: dict | None,
    ) -> None:
        s = self.settings
        ticker = c.signal.ticker
        b = self.summary.bucket(strategy)

        if repo.get_open_paper_position(session, ticker, strategy) is not None:
            self.summary.already_open += 1
            b["already_open"] += 1
            return
        if repo.count_open_paper_positions(session, strategy) >= s.paper_max_open_positions:
            self.summary.risk_blocked += 1
            b["risk_blocked"] += 1
            return

        existing_exposure = repo.open_paper_exposure(session, strategy=strategy, ticker=ticker)
        decision = self.risk.evaluate(
            signal=c.signal,
            metrics=c.metrics,
            account_state=account_state,
            existing_exposure=existing_exposure,
            for_paper=True,
        )
        repo.insert_risk_event(session, c.signal_id, ticker, decision)
        if not decision.approved:
            self.summary.risk_blocked += 1
            b["risk_blocked"] += 1
            return

        depth = _entry_depth(c.metrics, proposal.side)
        qty = min(s.paper_order_size, depth)
        if qty <= 0:
            self.summary.no_fill += 1
            b["no_fill"] += 1
            repo.record_no_fill(
                session,
                signal_id=c.signal_id,
                ticker=ticker,
                strategy=strategy,
                side=proposal.side,
                action="buy",
                assumed_price=proposal.price,
                fill_assumption=f"buy {proposal.side} @ {proposal.price}c but depth {depth} -> no fill",
                model_probability=proposal.model_probability,
                edge=proposal.edge,
            )
            return

        entry_fee = kalshi_fee(proposal.price, qty, s.paper_fees_enabled)
        repo.create_paper_trade(
            session,
            signal_id=c.signal_id,
            ticker=ticker,
            strategy=strategy,
            side=proposal.side,
            action="buy",
            assumed_price=proposal.price,
            quantity=qty,
            fill_assumption=(
                f"[{strategy}] buy {proposal.side} @ {proposal.price}c, depth {depth}, "
                f"qty {qty}, edge {proposal.edge:+.3f}"
            ),
            entry_fee=entry_fee,
            model_probability=proposal.model_probability,
            edge=proposal.edge,
        )
        repo.open_paper_position_for_trade(
            session,
            ticker=ticker,
            strategy=strategy,
            side=proposal.side,
            quantity=qty,
            avg_price=proposal.price,
        )
        self.summary.opened += 1
        b["opened"] += 1

    # -- open-position management -----------------------------------------
    def manage_open_positions(self, session) -> PaperCycleSummary:
        s = self.settings
        # One price tick per mmsell ticker per cycle (the path is ticker-level; many books can
        # hold the same ticker). Records the intraday tape the mmsell books never captured, for
        # the offline exit-rule study — free, off the orderbook we already fetch below.
        mmsell_ticked: set[str] = set()
        for trade in repo.get_open_paper_trades(session):
            # XGAME positions are settled/exited by their own tracker on a seconds-scale
            # converge/timeout rule (the edge is a 20-90s window); the shared hold-to-
            # settlement / hours-scale management would fight it, so skip them here.
            if (trade.strategy or "").startswith("xgame"):
                continue
            try:
                resp = self.client.get_market(trade.market_ticker)
            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "paper: market fetch failed",
                    extra={"extra_fields": {"ticker": trade.market_ticker, "error": str(exc)}},
                )
                continue
            market = resp.get("market") if isinstance(resp, dict) and "market" in resp else resp
            status = (market.get("status") or "").lower()
            result = (market.get("result") or "").lower()

            if result in ("yes", "no") or status in ("settled", "finalized", "determined"):
                self._settle(session, trade, result)
                continue

            try:
                ob = self.client.get_orderbook(trade.market_ticker, depth=s.orderbook_depth)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "paper: orderbook fetch failed",
                    extra={"extra_fields": {"ticker": trade.market_ticker, "error": str(exc)}},
                )
                continue
            metrics = compute_metrics(market, ob, top_n=s.orderbook_depth)
            # Despite the name, this captures ticks for any maker-sell book holding the
            # position (originally mmsell-only; theta shares the identical maker convention and
            # was added 2026-07 so its live/paper twin also gets a price-history panel on
            # kalshi_bot/livedash — the table is ticker+time keyed, not strategy-keyed, so
            # broadening the write-side gate is the only change either side needs).
            if (s.mmsell_tick_capture_enabled
                    and ("mmsell" in (trade.strategy or "")
                         or (trade.strategy or "").startswith("theta"))
                    and trade.market_ticker not in mmsell_ticked and metrics.two_sided):
                mmsell_ticked.add(trade.market_ticker)
                try:
                    repo.insert_mmsell_tick(session, trade.market_ticker, metrics)
                except Exception:  # noqa: BLE001 — a tick is diagnostic; never break settlement
                    logger.exception("mmsell tick capture failed (position management unaffected)")
            self._mark_or_exit(session, trade, metrics)

        self.summary.open_positions = repo.count_open_paper_positions(session)
        return self.summary

    def _settle(self, session, trade, result: str) -> None:
        if result not in ("yes", "no"):
            # Void / scratch: treat as returned at cost; only the entry fee is lost.
            pnl = -float(trade.fees or 0.0)
            repo.close_paper_trade(session, trade, status="closed_void", pnl=pnl)
            self.summary.closed_void += 1
            self.summary.realized_pnl += pnl
            return
        won = (trade.side == "yes" and result == "yes") or (trade.side == "no" and result == "no")
        resolved_value = 100 if won else 0
        gross = trade.quantity * (resolved_value - trade.assumed_price) / 100.0
        pnl = gross - float(trade.fees or 0.0)  # entry fee only; settlement is free
        repo.close_paper_trade(
            session, trade, status="settled", pnl=pnl, resolved_value=resolved_value
        )
        self.summary.closed_settled += 1
        self.summary.realized_pnl += pnl

    def _anchor_stop_hit(self, session, trade, metrics: MarketMetrics) -> bool:
        """True when an anchor-set book's CONFIRMED catastrophic stop has triggered on this held
        position: the yes-BID has reached the book's level for K consecutive management cycles.

        Only books carrying a `stopl` spec have a stop at all (docs/MMSELL_ANCHOR_SET.md); every
        other book — including the mmsell10 control these are measured against — is untouched and
        still holds to settlement. The trigger is the BID rather than the mid/ask deliberately: at
        these prices thin books quote wide, and the crypto backtest measured a mid-triggered K=1
        stop exiting ~100% of positions on quotes with no real buyer behind them.

        Confirmation reads the taped history (mmsell_position_ticks), which the caller has already
        written this cycle, so the last K ticks ARE the last K cycles. Fail-soft: any error leaves
        the position held, which is the pre-anchor behaviour."""
        strat = trade.strategy or ""
        if "mmsell" not in strat or trade.side != "no":
            return False
        book = self.settings.mmsell_book_by_tag(strat)
        if not book or book.get("stopl") is None:
            return False
        level = float(book["stopl"])
        k = max(1, int(book.get("stopk") or 2))
        if metrics.best_yes_bid is None or metrics.best_yes_bid < level:
            return False          # not at the level right now -> no confirm possible
        if k == 1:
            return True
        try:
            recent = repo.recent_position_yes_bids(session, trade.market_ticker, k)
        except Exception:  # noqa: BLE001 — a diagnostic read must never block position management
            logger.exception("anchor stop: tick history read failed (holding position)")
            return False
        return len(recent) >= k and all(b >= level for b in recent[-k:])

    def _mark_or_exit(self, session, trade, metrics: MarketMetrics) -> None:
        s = self.settings
        bid = _exit_bid(metrics, trade.side)
        if bid is None:
            return  # can't mark a one-sided book; leave the position open
        gain_cents = bid - trade.assumed_price
        held_hours = (
            datetime.now(timezone.utc) - _aware(trade.created_at)
        ).total_seconds() / 3600.0

        # Weather books hold to settlement (no timeout / TP / SL). The mmsell, theta and tfav
        # books also hold to settlement by default (their exit sweeps showed TP/SL only hurt) —
        # but keep TP/SL OPTIONAL so they can be forward-tested; either way they skip the
        # max-hold TIMEOUT (positions settle on their own schedule — a 2h timeout would
        # force-close mmsell days early, and theta/tfav settle within the hour anyway).
        # WCPROP is the exception: it is a DELIBERATELY timed book (capture the winner-ladder
        # lag over a fixed horizon, then get out), so it uses its own hold window instead of
        # the global one and closes at the current bid when it elapses.
        strat = trade.strategy or ""
        weather_hold = strat.startswith("weather")
        # "mmsell" as a SUBSTRING, not a prefix: the market-type books are tagged Wmmsell*/
        # Tmmsell* (docs/MMSELL_TYPE_BOOKS.md). Matching on the prefix would have left them out
        # of the hold-to-settlement set, so they alone would have been force-closed by the
        # global max-hold timeout — silently making them a different experiment from every
        # other mmsell book they are meant to be compared against.
        no_timeout = weather_hold or "mmsell" in strat \
            or strat.startswith(("theta", "tfav", "pin15", "freeze"))
        max_hold_hours = s.paper_max_hold_hours
        if strat.startswith("wcprop"):
            max_hold_hours = s.wcprop_hold_minutes / 60.0

        exit_status: str | None = None
        if weather_hold:
            exit_status = None
        elif self._anchor_stop_hit(session, trade, metrics):
            exit_status, counter = "closed_sl", "closed_sl"
        elif s.paper_take_profit_cents is not None and gain_cents >= s.paper_take_profit_cents:
            exit_status, counter = "closed_tp", "closed_tp"
        elif s.paper_stop_loss_cents is not None and gain_cents <= -s.paper_stop_loss_cents:
            exit_status, counter = "closed_sl", "closed_sl"
        elif not no_timeout and held_hours >= max_hold_hours:
            exit_status, counter = "closed_timeout", "closed_timeout"

        if exit_status is None:
            unrealized = trade.quantity * gain_cents / 100.0
            repo.mark_paper_position(session, trade.market_ticker, trade.strategy, unrealized)
            self.summary.marked += 1
            return

        exit_fee = kalshi_fee(bid, trade.quantity, s.paper_fees_enabled)
        pnl = trade.quantity * gain_cents / 100.0 - float(trade.fees or 0.0) - exit_fee
        repo.close_paper_trade(
            session, trade, status=exit_status, pnl=pnl, exit_price=bid, exit_fee=exit_fee
        )
        setattr(self.summary, counter, getattr(self.summary, counter) + 1)
        self.summary.realized_pnl += pnl
