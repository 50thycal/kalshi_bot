"""LiveExecutor — mirror allowlisted paper entries into real Kalshi orders, reconcile
fills/positions, and manage configurable exits. INERT by default.

Three independent switches must ALL be on before anything is placed:
  BOT_MODE=live  AND  KILL_SWITCH=false  AND  LIVE_ENABLED=true
plus a non-empty LIVE_STRATEGIES allowlist. The client's place_order also self-guards on
mode+kill_switch, so a logic bug cannot place an order outside live mode (defense in depth).

Design: paper trading stays the source of truth for signals/entries; this layer mirrors the
allowlisted entries to real orders. Kalshi is the source of truth for fills/positions, which
we reconcile each cycle. Hold-to-settlement by default (cash settles, no exit order); optional
TP/SL/break-even exits reuse the validated weather_exit_sweep replay semantics.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import repository as repo
from ..kalshi.errors import AuthError, KalshiAPIError, TransientError
from ..paper.engine import kalshi_fee
from ..scanner.metrics import _to_count, parse_dt, price_to_cents
from ..weather.cities import CITIES
from . import exit_rules

logger = logging.getLogger(__name__)

# series-ticker prefix -> city code, for the optional live city filter.
_CITY_BY_SERIES = {}
for _c in CITIES:
    _CITY_BY_SERIES[_c.series_high] = _c.code
    if _c.series_low:
        _CITY_BY_SERIES[_c.series_low] = _c.code

_WINDOW_RE = re.compile(r"_h(\d+)$")


def _city_of(ticker: str | None) -> str | None:
    t = ticker or ""
    for series, code in _CITY_BY_SERIES.items():
        if t.startswith(series):
            return code
    return None


def _window_of(strategy: str | None) -> int | None:
    m = _WINDOW_RE.search(strategy or "")
    return int(m.group(1)) if m else None


@dataclass
class LiveCycleSummary:
    placed: int = 0
    skipped_gate: int = 0
    skipped_dedup: int = 0
    risk_blocked: int = 0
    rejected: int = 0
    new_fills: int = 0
    positions_snapshot: int = 0
    settlements: int = 0
    timed_out_canceled: int = 0
    exits_placed: int = 0
    realized_today: float = 0.0
    notes: list[str] = field(default_factory=list)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class LiveExecutor:
    def __init__(self, client, settings, risk):
        self.client = client
        self.settings = settings
        self.risk = risk
        self.summary = LiveCycleSummary()
        self._daily_loss_tripped = False

    def reset_summary(self) -> None:
        self.summary = LiveCycleSummary()

    # --- gating -----------------------------------------------------------------

    def _switches_on(self) -> bool:
        s = self.settings
        return s.bot_mode == "live" and not s.kill_switch and s.live_enabled

    def _allowed(self, strategy: str) -> bool:
        prefixes = self.settings.live_strategy_list
        return any(strategy.startswith(p) for p in prefixes)

    def _cell_allowed(self, strategy: str, ticker: str) -> bool:
        """Optional city/window narrowing on top of the strategy allowlist, so live trading
        can target the cross-validated cells (e.g. late-window highs in stable cities)."""
        cities = self.settings.live_city_list
        if cities and _city_of(ticker) not in cities:
            return False
        windows = self.settings.live_window_list
        if windows and _window_of(strategy) not in windows:
            return False
        return True

    def _daily_loss_hit(self, session) -> bool:
        s = self.settings
        if not s.live_kill_on_daily_loss:
            return False
        realized = repo.live_realized_pnl_today(session)
        return realized <= -abs(s.max_daily_loss)

    # --- entry ------------------------------------------------------------------

    def mirror_entry(
        self, session, *, strategy: str, event_ticker: str, ticker: str, side: str,
        action: str, metrics, model_probability=None, account_state=None,
    ) -> None:
        """Mirror one allowlisted paper entry into a real order. Self-guarded and
        fail-closed: any gate failure simply places nothing."""
        s = self.settings
        if not self._allowed(strategy) or not self._switches_on():
            self.summary.skipped_gate += 1
            return
        if not self._cell_allowed(strategy, ticker):
            self.summary.skipped_gate += 1
            return
        if self._daily_loss_tripped or self._daily_loss_hit(session):
            self._daily_loss_tripped = True
            self.summary.skipped_gate += 1
            return
        if repo.live_order_exists(session, event_ticker, strategy):
            self.summary.skipped_dedup += 1
            return
        if metrics is None or metrics.best_yes_ask is None:
            self.summary.skipped_gate += 1
            return

        existing_exposure = self._market_exposure(session, ticker)
        decision = self.risk.evaluate(
            signal=None, metrics=metrics, account_state=account_state,
            existing_exposure=existing_exposure,
            existing_open_order=repo.live_open_order_exists(session, ticker),
            for_paper=False,
        )
        repo.insert_risk_event(session, None, ticker, decision)
        if not decision.approved:
            self.summary.risk_blocked += 1
            logger.info("live entry blocked by risk",
                        extra={"extra_fields": {"ticker": ticker, "strategy": strategy,
                                                "reasons": decision.reason_codes}})
            return

        # Dollar-cap sizing, then cap by depth, risk and the hard max order size.
        price = self._entry_price(metrics)
        if price is None:
            self.summary.skipped_gate += 1
            return
        qty_cap = math.floor(s.live_max_order_dollars / (price / 100.0))
        qty = min(qty_cap, metrics.depth_at_best_ask, decision.max_allowed_quantity, s.max_order_size)
        if qty <= 0:
            self.summary.skipped_gate += 1
            return

        client_order_id = f"{strategy}:{event_ticker}"
        order = {
            "ticker": ticker, "action": action, "side": side, "count": qty,
            "type": "limit", "yes_price": price, "client_order_id": client_order_id,
        }
        # Persist intent BEFORE the POST so a crash mid-place is recoverable.
        row = repo.create_live_order(
            session, signal_id=None, ticker=ticker, event_ticker=event_ticker,
            strategy=strategy, side=side, action=action, limit_price=price, quantity=qty,
            status="pending", client_order_id=client_order_id, raw_order_json=order,
        )
        try:
            resp = self.client.place_order(**order)
        except AuthError:
            repo.update_live_order_status(session, row, status="error", cancel_reason="auth")
            raise
        except TransientError as exc:
            # Indeterminate: never assume placed/failed; reconcile resolves via client_order_id.
            repo.update_live_order_status(session, row, status="unknown", cancel_reason=str(exc))
            logger.warning("live place_order transient; status=unknown",
                           extra={"extra_fields": {"ticker": ticker, "coid": client_order_id}})
            return
        except KalshiAPIError as exc:
            repo.update_live_order_status(session, row, status="rejected", cancel_reason=str(exc))
            self.summary.rejected += 1
            return
        except Exception as exc:  # noqa: BLE001 — never let live break the paper record
            repo.update_live_order_status(session, row, status="error", cancel_reason=str(exc))
            logger.exception("live place_order failed")
            return

        koid = (resp or {}).get("order", {}).get("order_id") if isinstance(resp, dict) else None
        status = "resting" if s.live_entry_style == "passive" else "submitted"
        repo.update_live_order_status(session, row, status=status, kalshi_order_id=koid, raw=resp)
        self.summary.placed += 1
        logger.info("live order placed", extra={"extra_fields": {
            "ticker": ticker, "strategy": strategy, "price": price, "count": qty,
            "style": s.live_entry_style, "coid": client_order_id, "kalshi_order_id": koid}})

    def _entry_price(self, metrics) -> int | None:
        ask = metrics.best_yes_ask
        if ask is None:
            return None
        if self.settings.live_entry_style == "passive":
            ask = ask - self.settings.live_passive_offset_cents
        return max(1, min(99, int(ask)))

    def _market_exposure(self, session, ticker: str) -> float:
        pos = repo.latest_position_snapshot(session, ticker)
        if pos is None or not pos.quantity:
            return 0.0
        return abs(pos.quantity) * float(pos.avg_price or 0.0) / 100.0

    # --- reconciliation ---------------------------------------------------------

    def reconcile(self, session, account_state=None) -> None:
        """Pull Kalshi truth (orders/fills/positions), resolve in-flight orders, record
        fills idempotently, snapshot positions, cancel timed-out passive orders, and refresh
        the daily realized P&L. AuthError propagates (worker hard-fails); others are logged."""
        try:
            orders = _items(self.client.get_orders(), "orders")
            fills = _items(self.client.get_fills(), "fills")
            positions = _items(self.client.get_positions(), "market_positions")
        except AuthError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("live reconcile fetch failed")
            return

        by_coid = {o.get("client_order_id"): o for o in orders if o.get("client_order_id")}
        by_koid = {o.get("order_id"): o for o in orders if o.get("order_id")}

        # Resolve in-flight local orders against the exchange.
        for row in repo.get_nonterminal_live_orders(session):
            exch = by_coid.get(row.client_order_id) or (
                by_koid.get(row.kalshi_order_id) if row.kalshi_order_id else None)
            if exch is None:
                if row.status in ("pending", "unknown"):
                    repo.update_live_order_status(session, row, status="not_landed",
                                                  cancel_reason="not_found_on_exchange")
                continue
            repo.update_live_order_status(
                session, row, status=_map_status(exch.get("status")),
                kalshi_order_id=exch.get("order_id"), raw=exch)

        # Record fills idempotently. Kalshi shapes (confirmed via the shape probe):
        # id=trade_id/fill_id; price=yes_price_dollars/no_price_dollars (dollar strings);
        # qty=count_fp (fixed-point string); fee=fee_cost (dollars).
        for f in fills:
            fid = f.get("trade_id") or f.get("fill_id")
            if not fid or repo.fill_exists(session, fid):
                continue
            side = f.get("side")
            if side == "no":
                price = price_to_cents(_first(f, "no_price_dollars", "no_price", "price"))
            else:
                price = price_to_cents(_first(f, "yes_price_dollars", "yes_price", "price"))
            qty = _to_count(_first(f, "count_fp", "count", "quantity"))
            fee_raw = _first(f, "fee_cost", "fee")
            try:
                fee = float(fee_raw) if fee_raw is not None else kalshi_fee(price, qty, True)
            except (TypeError, ValueError):
                fee = kalshi_fee(price, qty, True)
            repo.insert_fill(
                session, kalshi_fill_id=str(fid), kalshi_order_id=f.get("order_id"),
                ticker=f.get("market_ticker") or f.get("ticker"), filled_at=None, side=side,
                action=f.get("action"), price=price, quantity=qty, fee=fee, raw_fill_json=f)
            self.summary.new_fills += 1

        # Snapshot positions.
        # Kalshi market_positions shape (confirmed via real positions): position_fp
        # (signed fixed-point), market_exposure_dollars, realized_pnl_dollars.
        for p in positions:
            pos = _to_count(_first(p, "position_fp", "position"))
            exposure = _to_float(_first(p, "market_exposure_dollars", "market_exposure"))
            realized = _to_float(_first(p, "realized_pnl_dollars", "realized_pnl"))
            qty = abs(pos)
            avg_px = round(exposure / qty * 100, 2) if (exposure is not None and qty) else None
            repo.insert_position_snapshot(
                session, ticker=p.get("ticker"),
                side="yes" if pos >= 0 else "no", quantity=pos, avg_price=avg_px,
                market_exposure=exposure, realized_pnl=realized, raw_json=p)
            self.summary.positions_snapshot += 1

        # Cancel timed-out passive (resting) orders.
        now = datetime.now(timezone.utc)
        for row in repo.get_nonterminal_live_orders(session):
            if row.status != "resting":
                continue
            age = (now - _aware(row.created_at)).total_seconds()
            if age <= self.settings.live_order_timeout_seconds:
                continue
            try:
                if row.kalshi_order_id:
                    self.client.cancel_order(row.kalshi_order_id)
                repo.update_live_order_status(session, row, status="canceled",
                                              cancel_reason="timeout")
                self.summary.timed_out_canceled += 1
            except AuthError:
                raise
            except Exception:  # noqa: BLE001 — likely already filled/gone; next cycle resolves
                logger.warning("live cancel failed", extra={"extra_fields": {
                    "kalshi_order_id": row.kalshi_order_id}})

        # Settled positions vanish from get_positions, so capture realized P&L from
        # /portfolio/settlements (feeds the daily-loss breaker). Record today's settlements
        # once each (qty=0 snapshot with realized_pnl); dedup via the latest snapshot.
        try:
            settlements = _items(self.client.get_settlements(), "settlements")
        except AuthError:
            raise
        except Exception:  # noqa: BLE001
            settlements = []
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        for st in settlements:
            ts = parse_dt(st.get("settled_time"))
            if ts is None or ts < midnight:
                continue
            tkr = st.get("ticker")
            last = repo.latest_position_snapshot(session, tkr)
            if last is not None and last.quantity == 0 and last.realized_pnl is not None:
                continue  # settlement already recorded
            revenue = _to_float(st.get("revenue")) or 0.0  # cents received at settlement
            cost = ((_to_float(st.get("yes_total_cost_dollars")) or 0.0)
                    + (_to_float(st.get("no_total_cost_dollars")) or 0.0))
            fee = _to_float(st.get("fee_cost")) or 0.0
            pnl = revenue / 100.0 - cost - fee
            repo.insert_position_snapshot(
                session, ticker=tkr, side=st.get("market_result"), quantity=0,
                avg_price=None, market_exposure=0.0, realized_pnl=pnl, raw_json=st)
            self.summary.settlements += 1

        self.summary.realized_today = repo.live_realized_pnl_today(session)
        if self.settings.live_kill_on_daily_loss and self._daily_loss_hit(session):
            if not self._daily_loss_tripped:
                logger.critical("live daily-loss circuit breaker tripped", extra={
                    "extra_fields": {"realized_today": self.summary.realized_today,
                                     "max_daily_loss": self.settings.max_daily_loss}})
            self._daily_loss_tripped = True

    # --- dynamic exits ----------------------------------------------------------

    def manage_exits(self, session) -> None:
        """Place closing orders for configured TP/SL/break-even rules. No-op in the default
        'settlement' mode (Kalshi cash-settles automatically). Reuses the replay semantics."""
        s = self.settings
        if s.live_exit_mode != "tp_sl" or not self._switches_on():
            return
        for ticker, strategy, entry_price, entry_at, qty in repo.open_live_positions(session):
            if repo.live_exit_order_exists(session, ticker, strategy):
                continue
            live_bid = self._current_bid(ticker)
            if live_bid is None:
                continue
            history = repo.bucket_bid_path(session, ticker, after=entry_at)
            kind = exit_rules.should_exit(
                entry_price, history, live_bid,
                tp=s.live_take_profit_cents, sl=s.live_stop_loss_cents,
                be=s.live_break_even_arm_cents)
            if kind is not None:
                self._place_exit(session, ticker, strategy, live_bid, qty, kind)

    def _place_exit(self, session, ticker, strategy, bid, qty, kind) -> None:
        # Exit a YES position the Kalshi-native, proven way: BUY the opposite (NO) side at
        # no_price = 100 - yes_bid. This reuses the working buy path (a raw action="sell"
        # yes order returns invalid_parameters), and Kalshi nets the opposing position so
        # the P&L is the same as selling YES at the bid.
        no_price = max(1, min(99, 100 - int(bid)))
        coid = f"exit:{strategy}:{ticker}"
        order = {"ticker": ticker, "action": "buy", "side": "no", "count": qty,
                 "type": "limit", "no_price": no_price, "client_order_id": coid}
        row = repo.create_live_order(
            session, signal_id=None, ticker=ticker, event_ticker=None, strategy=strategy,
            side="no", action="buy", limit_price=no_price, quantity=qty, status="pending",
            client_order_id=coid, raw_order_json=order)
        try:
            resp = self.client.place_order(**order)
        except AuthError:
            repo.update_live_order_status(session, row, status="error", cancel_reason="auth")
            raise
        except Exception as exc:  # noqa: BLE001
            repo.update_live_order_status(session, row, status="rejected", cancel_reason=str(exc))
            return
        koid = (resp or {}).get("order", {}).get("order_id") if isinstance(resp, dict) else None
        repo.update_live_order_status(session, row, status="submitted", kalshi_order_id=koid, raw=resp)
        self.summary.exits_placed += 1
        logger.info("live exit order placed (buy-no to close)", extra={"extra_fields": {
            "ticker": ticker, "strategy": strategy, "rule": kind, "no_price": no_price,
            "count": qty}})

    def _current_bid(self, ticker: str) -> int | None:
        try:
            from ..scanner.metrics import compute_metrics
            ob = self.client.get_orderbook(ticker, depth=self.settings.orderbook_depth)
            mx = compute_metrics({"ticker": ticker}, ob, top_n=self.settings.orderbook_depth)
            return mx.best_yes_bid
        except Exception:  # noqa: BLE001
            return None

    # --- startup recovery -------------------------------------------------------

    def recover(self, session) -> None:
        """On startup, reconcile any in-flight orders from a prior run against Kalshi
        (idempotent). Untracked live positions are logged and never auto-traded."""
        if self.settings.bot_mode != "live":
            return
        logger.info("live recover: reconciling in-flight orders")
        try:
            self.reconcile(session)
        except AuthError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("live recover failed")


def _first(d: dict, *keys: str):
    """First non-None value among keys (tolerates Kalshi field-name variants)."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _items(resp, key: str) -> list:
    if not isinstance(resp, dict):
        return []
    val = resp.get(key)
    return val if isinstance(val, list) else []


def _map_status(kalshi_status: str | None) -> str:
    s = (kalshi_status or "").lower()
    return {
        "resting": "resting", "open": "resting",
        "executed": "filled", "filled": "filled",
        "canceled": "canceled", "cancelled": "canceled",
        "pending": "submitted",
    }.get(s, "submitted")


def _to_float(value) -> float | None:
    """Parse a Kalshi *_dollars string (or number) into a float; None if unparseable."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
