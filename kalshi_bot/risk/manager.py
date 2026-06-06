"""Risk Manager — fail-closed gatekeeper for any (future) trade.

Every would-be trade passes through `evaluate`, which returns a RiskDecision.
In the Scanner MVP no real orders are placed, but candidate signals are still
evaluated and the decision is persisted as a `risk_event` for the audit trail
and for later backtesting of the risk_block_rate metric.

Fail-closed: anything uncertain adds a reason code, and any reason code blocks
approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings


@dataclass
class RiskDecision:
    approved: bool
    reason_codes: list[str] = field(default_factory=list)
    max_allowed_quantity: int = 0
    max_allowed_price: int | None = None


class RiskManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    def evaluate(
        self,
        *,
        signal,
        metrics,
        account_state: dict | None = None,
        existing_exposure: float | None = 0.0,
        existing_open_order: bool = False,
        for_paper: bool = False,
    ) -> RiskDecision:
        """Evaluate a would-be trade.

        `for_paper=True` evaluates a *simulated* trade: the live-only execution gates
        (kill switch, mode-not-live, real-balance availability) are skipped, but every
        market-quality, liquidity, sizing and exposure gate still applies."""
        s = self.settings
        reasons: list[str] = []

        # --- absolute gates (live execution only) ---
        if not for_paper:
            if s.kill_switch:
                reasons.append("KILL_SWITCH_ON")
            if s.bot_mode != "live":
                reasons.append("MODE_NOT_LIVE")

        # --- market data quality ---
        if not metrics.two_sided:
            reasons.append("MARKET_NOT_TWO_SIDED")
        if metrics.midpoint is None or metrics.spread is None:
            reasons.append("NO_PRICE")
        if metrics.spread is not None and metrics.spread > s.max_spread_cents:
            reasons.append("SPREAD_TOO_WIDE")
        if metrics.liquidity_score <= 0:
            reasons.append("INSUFFICIENT_LIQUIDITY")
        ttc_hours = (metrics.time_to_close_seconds or 0) / 3600.0
        if ttc_hours < s.min_hours_to_close:
            reasons.append("CLOSES_TOO_SOON")

        # --- account / exposure ---
        # Paper trading uses a simulated bankroll, so a missing real balance is fine.
        if not for_paper:
            balance = None if account_state is None else account_state.get("cash_balance")
            if balance is None:
                reasons.append("BALANCE_UNAVAILABLE")
        if existing_open_order:
            reasons.append("EXISTING_OPEN_ORDER")
        if existing_exposure is None:
            reasons.append("EXPOSURE_UNKNOWN")
        elif existing_exposure >= s.max_market_exposure:
            reasons.append("MARKET_EXPOSURE_EXCEEDED")

        # --- sizing (limit orders only) ---
        max_qty = max(0, s.max_order_size)
        # Conservative limit price for a YES buy is the current YES ask.
        max_price = metrics.best_yes_ask

        approved = not reasons and max_qty > 0
        return RiskDecision(
            approved=approved,
            reason_codes=reasons,
            max_allowed_quantity=max_qty if approved else 0,
            max_allowed_price=max_price if approved else None,
        )
