"""Desk-only execution orchestration with durable claims and bounded risk.

An accepted decision is recorded and its cash/slot reserved before its single
submission. Unknown submissions remain reserved until exchange reconciliation.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

from .contracts import Decision, DeskError, OrderReport, Quote, utcnow

D = Decimal
CHICAGO = ZoneInfo("America/Chicago")


def conservative_cost(quantity: int, limit_price: Decimal, fee_rate: Decimal) -> Decimal:
    """Upper bound on a Direct-member IOC debit, rounded UP to whole cents.

    At most 100 fills per whole contract (minimum fill .01). For each fill,
    reserve .000001 model rounding + .0001 balance rounding, without counting
    any rebate. Curve maximum is at min(limit, .5), not necessarily the limit.
    See docs.kalshi.com/getting_started/{fee_rounding,fixed_point_migration}.
    """
    if quantity < 1 or not 0 < limit_price < 1 or not 0 <= fee_rate <= 1:
        raise DeskError("invalid_cost_inputs")
    peak = min(limit_price, D("0.5"))
    fee_bound = fee_rate * quantity * peak * (1 - peak) + D(quantity) * D("0.0101")
    return (D(quantity) * limit_price + fee_bound).quantize(D("0.01"), rounding=ROUND_CEILING)


class DeskExecutor:
    def __init__(
        self,
        store,
        exchange,
        desk_id: str,
        *,
        live_enabled: bool = False,
        isolation_verified: bool = False,
        existing_workers_isolated: bool = False,
    ):
        self.store, self.exchange, self.desk_id = store, exchange, desk_id
        self.live_enabled = live_enabled
        self.isolation_verified = isolation_verified
        self.existing_workers_isolated = existing_workers_isolated

    def _guard(self):
        if not self.live_enabled:
            raise DeskError("desk_live_disabled")
        if not self.isolation_verified or not self.existing_workers_isolated:
            raise DeskError("desk_isolation_unverified")
        if not 1 <= getattr(self.exchange, "subaccount", 0) <= 63:
            raise DeskError("dedicated_subaccount_required")

    def _validate(self, decision: Decision, quote: Quote, now: datetime):
        if decision.desk_id != self.desk_id:
            raise DeskError("wrong_desk")
        if now.tzinfo is None:
            raise DeskError("timezone_required")
        if decision.created_at > now or now >= decision.expires_at:
            raise DeskError("expired_or_future_decision")
        if decision.created_at.astimezone(CHICAGO).date() != now.astimezone(CHICAGO).date():
            raise DeskError("decision_from_previous_trading_day")
        for timestamp in (decision.quote_at, quote.fetched_at):
            if timestamp.tzinfo is None or not 0 <= (now - timestamp).total_seconds() <= 60:
                raise DeskError("stale_or_future_quote")
        if (quote.ticker, quote.event_id, quote.side) != (
            decision.ticker,
            decision.event_id,
            decision.side,
        ):
            raise DeskError("quote_identity_mismatch")
        if quote.rules_sha256 != decision.rules_sha256:
            raise DeskError("settlement_rules_changed")
        if (
            quote.closes_at.tzinfo is None
            or quote.closes_at <= now
            or quote.status not in ("open", "active")
        ):
            raise DeskError("market_not_open")
        if quote.ask > decision.max_price:
            raise DeskError("price_cap_exceeded")
        if decision.max_price % D("0.01") or decision.max_price % quote.tick_size:
            raise DeskError("unsupported_limit_grid")

    def submit(self, decision: Decision, now: datetime | None = None):
        now = now or utcnow()
        self._guard()
        existing = self.store.get_decision(decision.decision_id)
        if existing:
            if existing["payload"] != decision.model_dump(mode="json"):
                raise DeskError("decision_id_conflict")
            # A durable claim is never taken twice. Even an old reserved record
            # is reconciled, not submitted again after a restart.
            return existing
        started = time.monotonic()
        quote = self.exchange.quote(decision.ticker, decision.side)
        now += timedelta(seconds=time.monotonic() - started)
        self._validate(decision, quote, now)
        snapshot = self.store.snapshot(now)
        book = next(row for row in snapshot["desks"] if row["desk_id"] == self.desk_id)
        budget = min(
            D(1),
            decision.max_spend,
            D(book["available_cash"]),
            max(D(0), D(10) - D(book["committed"])),
        )
        quantity = min(quote.available_quantity, int(budget / decision.max_price))
        while quantity and conservative_cost(quantity, decision.max_price, quote.fee_rate) > budget:
            quantity -= 1
        if quantity < 1:
            raise DeskError("no_affordable_contract")
        reserved_cost = conservative_cost(quantity, decision.max_price, quote.fee_rate)
        if decision.probability_low * quantity <= reserved_cost:
            raise DeskError("insufficient_conservative_edge")
        if decision.probability * quantity - reserved_cost <= 0:
            raise DeskError("nonpositive_computed_edge")
        row = self.store.reserve(decision, quantity, reserved_cost, now)
        if not self.store.claim_submission(decision.decision_id, now):
            return self.store.get_decision(decision.decision_id)
        try:
            report = self.exchange.submit_ioc(
                row["client_order_id"], decision.ticker, decision.side, quantity, decision.max_price
            )
        except Exception:
            # The exception can occur after the exchange accepted the order.
            # Keep the whole reservation; do not leak provider/credential errors.
            report = OrderReport(
                client_order_id=row["client_order_id"], status="unknown", observed_at=now
            )
        return self.store.record_order(decision.decision_id, report, now)

    def reconcile(self, decision_id: str | None = None, *, now: datetime | None = None):
        """Read-only exchange recovery continues even when live submits are disabled."""
        now = now or utcnow()
        results = []
        for row in self.store.pending_orders():
            if row["desk_id"] != self.desk_id or (
                decision_id and row["decision_id"] != decision_id
            ):
                continue
            if row["status"] == "reserved":
                # A process may stop after reserve but before its atomic claim.
                # No exchange uncertainty exists until that claim is won. The
                # store races cleanup against claim under the same row lock.
                if self.store.release_unsubmitted(row["decision_id"], now):
                    results.append(self.store.get_decision(row["decision_id"]))
                continue
            try:
                report = self.exchange.reconcile(row["client_order_id"], row["ticker"])
                # Missing exchange state cannot erase previously observed fills.
                if report.status == "unknown":
                    report = OrderReport(
                        client_order_id=row["client_order_id"],
                        order_id=row["order_id"],
                        status="unknown",
                        filled_quantity=D(row["filled_quantity"]),
                        fill_cost=D(row["fill_cost"]),
                        fees=D(row["fees"]),
                        observed_at=now,
                    )
                results.append(self.store.record_order(row["decision_id"], report, now))
            except Exception:
                self.store.pause(self.desk_id, "reconciliation_failed")
                # Do not stop recovery of the remaining orders in this desk.
        return results

    def settle(self, now: datetime | None = None):
        now = now or utcnow()
        results = []
        for row in self.store.snapshot(now)["decisions"]:
            if row["desk_id"] != self.desk_id or row["settled"] or row["status"] != "terminal":
                continue
            settlement = self.exchange.settlement(row["ticker"])
            if settlement is not None:
                results.append(self.store.settlement(row["decision_id"], settlement))
        return results
