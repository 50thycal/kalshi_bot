"""Paper trading engine.

Simulates trades from the scanner's candidate signals so we can measure fill
quality, spread/fee cost, and resolution outcomes — without placing real orders.

- Entry (baseline, swappable): `buy_favorite` buys the side implied >= 50% at that
  side's ask; `buy_yes`/`buy_no` force a side. Quantity is the risk-approved size
  capped by the order-book depth at the entry price (depth 0 -> recorded no_fill).
- Risk: every entry passes `RiskManager.evaluate(for_paper=True)` — the live-only
  gates are skipped but all quality/liquidity/exposure gates apply, measured against
  a simulated bankroll and open paper positions.
- Exit: settlement (payoff 0/100), or — before settlement — a max-hold timeout or
  optional take-profit/stop-loss, closed conservatively at the current bid.
- Fees: Kalshi's `ceil(0.07 * C * P * (1-P))` per trade, on entry and early-exit
  sells (never on settlement).

The MVP signal is non-directional, so this is a measurement harness; `choose_entry`
is the single seam where a future forecasting model plugs in.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .. import repository as repo
from ..config import Settings
from ..kalshi.client import KalshiClient
from ..kalshi.errors import AuthError
from ..risk.manager import RiskDecision, RiskManager
from ..scanner.metrics import MarketMetrics, compute_metrics

logger = logging.getLogger(__name__)


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

    @property
    def fillability_rate(self) -> float | None:
        denom = self.opened + self.no_fill
        return round(self.opened / denom, 3) if denom else None


def kalshi_fee(price_cents: int | None, qty: int, enabled: bool = True) -> float:
    """Kalshi trading fee in dollars: ceil(0.07 * C * P * (1-P)) rounded up to a cent."""
    if not enabled or not qty or price_cents is None:
        return 0.0
    p = price_cents / 100.0
    return math.ceil(0.07 * qty * p * (1 - p) * 100) / 100.0


def choose_entry(metrics: MarketMetrics, settings: Settings) -> tuple[str, str, int] | None:
    """Return (side, action, entry_price_cents) for a new paper entry, or None."""
    if not metrics.two_sided:
        return None
    strategy = settings.paper_strategy
    if strategy == "buy_yes":
        side = "yes"
    elif strategy == "buy_no":
        side = "no"
    else:  # buy_favorite
        side = "yes" if (metrics.midpoint is not None and metrics.midpoint >= 50) else "no"
    price = metrics.best_yes_ask if side == "yes" else metrics.best_no_ask
    if price is None:
        return None
    return side, "buy", price


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

    # -- entries ----------------------------------------------------------
    def consider(
        self,
        session,
        *,
        signal,
        signal_id: int | None,
        metrics: MarketMetrics,
        market: dict,
        account_state: dict | None = None,
    ) -> RiskDecision | None:
        s = self.settings
        self.summary.considered += 1
        ticker = signal.ticker

        entry = choose_entry(metrics, s)
        if entry is None:
            self.summary.risk_blocked += 1
            return None
        side, action, price = entry

        # One open position per market in the MVP (no averaging).
        if repo.get_open_paper_position(session, ticker) is not None:
            self.summary.already_open += 1
            return None
        if repo.count_open_paper_positions(session) >= s.paper_max_open_positions:
            self.summary.risk_blocked += 1
            return None

        existing_exposure = repo.open_paper_exposure(session, ticker)
        decision = self.risk.evaluate(
            signal=signal,
            metrics=metrics,
            account_state=account_state,
            existing_exposure=existing_exposure,
            for_paper=True,
        )
        repo.insert_risk_event(session, signal_id, ticker, decision)
        if not decision.approved:
            self.summary.risk_blocked += 1
            return decision

        depth = _entry_depth(metrics, side)
        qty = min(s.paper_order_size, depth)  # paper size, capped by fillable depth
        if qty <= 0:
            repo.record_no_fill(
                session,
                signal_id=signal_id,
                ticker=ticker,
                side=side,
                action=action,
                assumed_price=price,
                fill_assumption=f"buy {side} @ {price}c but depth {depth} -> no fill",
            )
            self.summary.no_fill += 1
            return decision

        entry_fee = kalshi_fee(price, qty, s.paper_fees_enabled)
        repo.create_paper_trade(
            session,
            signal_id=signal_id,
            ticker=ticker,
            side=side,
            action=action,
            assumed_price=price,
            quantity=qty,
            fill_assumption=f"buy {side} @ {side} ask {price}c, depth {depth}, qty {qty}",
            entry_fee=entry_fee,
        )
        repo.open_paper_position_for_trade(
            session, ticker=ticker, side=side, quantity=qty, avg_price=price
        )
        self.summary.opened += 1
        return decision

    # -- open-position management -----------------------------------------
    def manage_open_positions(self, session) -> PaperCycleSummary:
        s = self.settings
        for trade in repo.get_open_paper_trades(session):
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

    def _mark_or_exit(self, session, trade, metrics: MarketMetrics) -> None:
        s = self.settings
        bid = _exit_bid(metrics, trade.side)
        if bid is None:
            return  # can't mark a one-sided book; leave the position open
        gain_cents = bid - trade.assumed_price
        held_hours = (
            datetime.now(timezone.utc) - _aware(trade.created_at)
        ).total_seconds() / 3600.0

        exit_status: str | None = None
        if s.paper_take_profit_cents is not None and gain_cents >= s.paper_take_profit_cents:
            exit_status, counter = "closed_tp", "closed_tp"
        elif s.paper_stop_loss_cents is not None and gain_cents <= -s.paper_stop_loss_cents:
            exit_status, counter = "closed_sl", "closed_sl"
        elif held_hours >= s.paper_max_hold_hours:
            exit_status, counter = "closed_timeout", "closed_timeout"

        if exit_status is None:
            unrealized = trade.quantity * gain_cents / 100.0
            repo.mark_paper_position(session, trade.market_ticker, unrealized)
            self.summary.marked += 1
            return

        exit_fee = kalshi_fee(bid, trade.quantity, s.paper_fees_enabled)
        pnl = trade.quantity * gain_cents / 100.0 - float(trade.fees or 0.0) - exit_fee
        repo.close_paper_trade(
            session, trade, status=exit_status, pnl=pnl, exit_price=bid, exit_fee=exit_fee
        )
        setattr(self.summary, counter, getattr(self.summary, counter) + 1)
        self.summary.realized_pnl += pnl
