"""Dedicated, subaccount-scoped Kalshi adapter. No bot settings or live executor.

API contracts checked 2026-09-20 against docs.kalshi.com. Writes are sent once:
network errors never mean an order is safe to repeat. All money uses Decimal.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import quote as urlquote
from uuid import UUID

import httpx

from kalshi_bot.kalshi.auth import KalshiSigner

from .contracts import DeskError, OrderReport, Quote, Settlement, utcnow

D = Decimal
API_PREFIX = "/trade-api/v2"


def rules_hash(market: dict) -> str:
    """Versioned canonical hash of the exact settlement terms reviewed by a desk."""
    fields = ("ticker", "event_ticker", "rules_primary", "rules_secondary", "close_time",
              "expiration_time", "early_close_condition", "strike_type", "floor_strike",
              "cap_strike", "custom_strike")
    raw = json.dumps({key: market.get(key) for key in fields}, sort_keys=True,
                     separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _decimal(value) -> Decimal:
    try:
        result = D(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DeskError("invalid_exchange_number") from exc


def _date(value) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except (ValueError, TypeError) as exc:
        raise DeskError("invalid_exchange_timestamp") from exc


class KalshiDeskExchange:
    """A Direct-account key locked to one numbered subaccount is required.

    Neither keys nor account creation/funding are discovered or mutated here.
    Injecting a client supports offline MockTransport tests.
    """

    def __init__(self, base_url: str, key_id: str, private_key: str, subaccount: int,
                 *, client: httpx.Client | None = None):
        if isinstance(subaccount, bool) or not 1 <= subaccount <= 63:
            raise DeskError("dedicated_subaccount_required")
        if not base_url.startswith("https://") or not base_url.rstrip("/").endswith(API_PREFIX):
            raise DeskError("invalid_exchange_url")
        self.base_url = base_url.rstrip("/")
        self.subaccount = subaccount
        self.signer = KalshiSigner(key_id, private_key)
        self.client = client or httpx.Client(timeout=15, follow_redirects=False)
        self._owns_client = client is None
        self._sides: dict[str, str] = {}

    def close(self):
        if self._owns_client:
            self.client.close()

    def _request(self, method: str, path: str, *, params=None, body=None):
        headers = self.signer.auth_headers(method, API_PREFIX + path)
        # Deliberately no retry, including HTTP 429, 5xx, and transport errors.
        response = self.client.request(method, self.base_url + path, params=params,
                                       json=body, headers=headers)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise DeskError("invalid_exchange_payload")
        return result

    def _pages(self, path: str, key: str, params: dict):
        params = dict(params, limit=1000)
        seen: set[str] = set()
        for _ in range(100):
            page = self._request("GET", path, params=params)
            rows = page.get(key)
            if not isinstance(rows, list):
                raise DeskError("invalid_exchange_payload")
            yield from rows
            cursor = page.get("cursor")
            if not cursor:
                return
            if cursor in seen:
                raise DeskError("exchange_pagination_loop")
            seen.add(cursor)
            params["cursor"] = cursor
        raise DeskError("exchange_pagination_limit")

    def check_isolation(self) -> dict:
        """Read-only proof that own balances work and cross-subaccount access fails.

        This cannot prove that existing bot credentials are similarly restricted;
        that is a separate operator deployment attestation.
        """
        own = self._request("GET", "/portfolio/balance", params={"subaccount": self.subaccount})
        balance = _decimal(own.get("balance")) / 100
        if balance < 0:
            raise DeskError("invalid_exchange_balance")
        for other in (0, 1 if self.subaccount != 1 else 2):
            try:
                self._request("GET", "/portfolio/balance", params={"subaccount": other})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 403:
                    raise DeskError("isolation_unproven") from exc
            else:
                raise DeskError("unrestricted_exchange_key")
        return {"verified": True, "subaccount": self.subaccount, "balance": str(balance),
                "checked_at": utcnow().isoformat()}

    def check_clean_book(self) -> dict:
        """Launch-only read check; never call on restart of an already active book."""
        positions = list(self._pages("/portfolio/positions", "market_positions",
                                    {"subaccount": self.subaccount}))
        if any(_decimal(row.get("position_fp")) != 0 for row in positions):
            raise DeskError("subaccount_has_existing_positions")
        orders = list(self._pages("/portfolio/orders", "orders",
                                 {"subaccount": self.subaccount, "status": "resting"}))
        if any(_decimal(row.get("remaining_count_fp")) > 0 for row in orders):
            raise DeskError("subaccount_has_resting_orders")
        return {"clean": True, "subaccount": self.subaccount, "checked_at": utcnow().isoformat()}

    def quote(self, ticker: str, side: str) -> Quote:
        if side not in ("yes", "no"):
            raise DeskError("invalid_side")
        fetched_at = utcnow()
        market = self._request("GET", f"/markets/{urlquote(ticker, safe='')}")["market"]
        if (market.get("ticker") != ticker or market.get("market_type") != "binary"
                or _decimal(market.get("notional_value_dollars")) != 1
                or not market.get("rules_primary") or market.get("is_provisional", False)):
            raise DeskError("unsupported_market")
        event_id = market["event_ticker"]
        event = self._request("GET", f"/events/{urlquote(event_id, safe='')}")["event"]
        series = self._request("GET", f"/series/{urlquote(event['series_ticker'], safe='')}")["series"]
        fee_type, multiplier = series.get("fee_type"), series.get("fee_multiplier")
        changes = list(self._pages("/events/fee_changes", "event_fee_changes", {"event_ticker": event_id}))
        now = utcnow()
        changes = [row for row in changes if row.get("event_ticker") == event_id and _date(row["scheduled_ts"]) <= now]
        if changes:
            latest = max(changes, key=lambda row: _date(row["scheduled_ts"]))
            fee_type = latest.get("fee_type_override") or fee_type
            if latest.get("fee_multiplier_override") is not None:
                multiplier = latest["fee_multiplier_override"]
        if fee_type not in ("quadratic", "quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"):
            raise DeskError("unsupported_fee_schedule")
        rate = D("0.07") * _decimal(multiplier)
        if not 0 <= rate <= 1:
            raise DeskError("unsupported_fee_schedule")
        # Whole-cent limit prices are on every currently documented grid. Refuse
        # unfamiliar grids instead of guessing; fractional fills remain supported.
        ranges = market.get("price_ranges")
        if not ranges or any(D("0.01") % _decimal(r["step"]) != 0 for r in ranges):
            raise DeskError("unsupported_price_grid")
        if any(_decimal(r["start"]) % _decimal(r["step"]) != 0 for r in ranges):
            raise DeskError("unsupported_price_grid")
        available = market.get("yes_ask_size_fp") if side == "yes" else market.get("yes_bid_size_fp")
        return Quote(ticker=ticker, event_id=event_id, side=side,
                     ask=_decimal(market.get(f"{side}_ask_dollars")),
                     available_quantity=max(0, int(_decimal(available))), fetched_at=fetched_at,
                     closes_at=_date(market["close_time"]), rules_sha256=rules_hash(market),
                     fee_rate=rate, status=market.get("status", "unknown"))

    def submit_ioc(self, client_order_id: str, ticker: str, side: str,
                   quantity: int, limit_price: Decimal) -> OrderReport:
        UUID(client_order_id)
        if side not in ("yes", "no") or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise DeskError("invalid_order")
        price = _decimal(limit_price)
        if not 0 < price < 1 or price % D("0.01"):
            raise DeskError("invalid_limit_price")
        self._sides[client_order_id] = side
        result = self._request("POST", "/portfolio/events/orders", body={
            "ticker": ticker, "client_order_id": client_order_id,
            "side": "bid" if side == "yes" else "ask", "count": f"{quantity:.2f}",
            "price": f"{price if side == 'yes' else 1-price:.4f}",
            "time_in_force": "immediate_or_cancel", "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True, "subaccount": self.subaccount, "exchange_index": -1,
        })
        if result.get("client_order_id", client_order_id) != client_order_id or not result.get("order_id"):
            raise DeskError("order_identity_mismatch")
        # POST average fields do not replace authoritative cumulative accounting.
        # Reconcile GET immediately; if delayed, retain the full reservation.
        report = self.reconcile(client_order_id, ticker)
        if report.order_id is None:
            report = report.model_copy(update={"order_id": str(result["order_id"])})
        return report

    def reconcile(self, client_order_id: str, ticker: str) -> OrderReport:
        now = utcnow()
        matches = [row for row in self._pages("/portfolio/orders", "orders", {
            "ticker": ticker, "subaccount": self.subaccount,
        }) if row.get("client_order_id") == client_order_id]
        if not matches:
            return OrderReport(client_order_id=client_order_id, status="unknown", observed_at=now)
        if len(matches) != 1:
            raise DeskError("duplicate_exchange_order")
        row = matches[0]
        if row.get("subaccount_number") != self.subaccount or row.get("ticker") != ticker:
            raise DeskError("order_identity_mismatch")
        qty = _decimal(row.get("fill_count_fp"))
        remaining = _decimal(row.get("remaining_count_fp"))
        terminal = row.get("status") in ("canceled", "executed") and remaining == 0
        cost, fees, fill_qty = D(0), D(0), D(0)
        if qty > 0:
            outcome = row.get("outcome_side")
            if outcome not in ("yes", "no"):
                raise DeskError("unknown_order_direction")
            if client_order_id in self._sides and self._sides[client_order_id] != outcome:
                raise DeskError("order_direction_mismatch")
            fills = list(self._pages("/portfolio/fills", "fills", {
                "order_id": row["order_id"], "ticker": ticker, "subaccount": self.subaccount,
            }))
            seen_fills = set()
            for fill in fills:
                identity = fill.get("fill_id") or fill.get("trade_id")
                if not identity or identity in seen_fills:
                    raise DeskError("invalid_fill_identity")
                seen_fills.add(identity)
                if (fill.get("order_id") != row["order_id"]
                        or fill.get("subaccount_number") != self.subaccount
                        or fill.get("outcome_side") != outcome
                        or (fill.get("ticker") or fill.get("market_ticker")) != ticker):
                    raise DeskError("fill_identity_mismatch")
                count = _decimal(fill.get("count_fp"))
                price = _decimal(fill.get(f"{outcome}_price_dollars"))
                fee = _decimal(fill.get("fee_cost"))
                if count <= 0 or count % D(".01") or not 0 < price < 1 or fee < 0:
                    raise DeskError("invalid_fill_values")
                fill_qty += count
                cost += count * price
                fees += fee
            if fill_qty != qty:
                return OrderReport(client_order_id=client_order_id, order_id=row["order_id"],
                                   status="unknown", observed_at=now)
        return OrderReport(client_order_id=client_order_id, order_id=row["order_id"],
                           status="terminal" if terminal else "pending", filled_quantity=qty,
                           fill_cost=cost, fees=fees, observed_at=now)

    def settlement(self, ticker: str) -> Settlement | None:
        market = self._request("GET", f"/markets/{urlquote(ticker, safe='')}")["market"]
        if market.get("ticker") != ticker:
            raise DeskError("market_identity_mismatch")
        if market.get("status") not in ("settled", "finalized"):
            return None
        # Never infer a settlement from a provisional result or last traded price.
        if market.get("is_provisional", False) or not market.get("settlement_ts"):
            return None
        return Settlement(ticker=ticker, yes_payout=_decimal(market["settlement_value_dollars"]),
                          settled_at=_date(market["settlement_ts"]),
                          source=self.base_url + "/markets/" + urlquote(ticker, safe=""))
