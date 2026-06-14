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
    exits_reattempted: int = 0
    exits_escalated: int = 0
    exits_abandoned: int = 0
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
        self._exit_abandoned: set[tuple[str, str]] = set()

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
            if exc.status_code == 409:  # order_already_exists -> a prior identical send landed;
                # treat as success so reconcile resolves it by client_order_id (NOT rejected,
                # which would corrupt position tracking and block the exit).
                repo.update_live_order_status(session, row, status="submitted",
                                              cancel_reason="409_already_exists")
                self.summary.placed += 1
                logger.info("live entry 409 already_exists -> submitted",
                            extra={"extra_fields": {"ticker": ticker, "coid": client_order_id}})
                return
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
        for ticker, strategy, entry_price, entry_at, entry_qty in repo.open_live_positions(session):
            # The position snapshot (refreshed by reconcile, which runs first) is the source of
            # truth — an exit is "done" only when Kalshi shows the position flat.
            remaining = self._remaining_open_qty(session, ticker, entry_qty)
            if remaining <= 0:
                continue
            if repo.live_exit_in_flight(session, ticker, strategy):
                continue  # an attempt is still working this cycle; let it resolve
            attempts = repo.count_exit_attempts(session, ticker, strategy)
            if attempts >= s.live_exit_max_attempts:
                self._log_exit_abandoned(ticker, strategy, remaining, attempts)
                continue  # give up early-exit; hold to settlement (bounded loss, no catastrophe)
            live_bid = self._current_bid(ticker)
            if live_bid is None:
                continue
            if attempts == 0:
                history = repo.bucket_bid_path(session, ticker, after=entry_at)
                kind = exit_rules.should_exit(
                    entry_price, history, live_bid,
                    tp=s.live_take_profit_cents, sl=s.live_stop_loss_cents,
                    be=s.live_break_even_arm_cents)
                if kind is None:
                    continue
            else:
                # Already committed to closing — keep trying to get fully out (escalating price)
                # rather than leaving a partial/stuck position because the trigger no longer holds.
                kind = "reattempt"
                self.summary.exits_reattempted += 1
            self._place_exit(session, ticker, strategy, live_bid, remaining, kind,
                             attempt=attempts + 1, level=self._escalation_level(attempts))

    def _escalation_level(self, attempts: int) -> int:
        """0 = base marketable limit; 1 = aggressive (slippage-buffered) limit;
        2 = optional best-effort market order on the final attempt."""
        if attempts == 0:
            return 0
        if (attempts + 1) >= self.settings.live_exit_max_attempts and \
                self.settings.live_exit_use_market_fallback:
            return 2
        return 1

    def _exit_sell_price(self, bid: int, level: int) -> int:
        """Limit price (cents) to SELL the held YES at — exactly the app's "Slide to Sell".
        Base = the current bid so it crosses and fills immediately; escalated re-attempts sell
        LOWER (cross deeper) to force the fill."""
        px = int(bid)
        if level >= 1:
            px -= self.settings.live_exit_slippage_cents
        return max(1, min(99, px))

    def _remaining_open_qty(self, session, ticker: str, entry_qty: int) -> int:
        """Remaining open YES contracts from Kalshi truth. The latest position snapshot
        (refreshed by reconcile each cycle) is authoritative; 0 means flat. Falls back to the
        entry qty minus already-filled exit-NO contracts if no fresh snapshot exists yet."""
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        snap = repo.latest_position_snapshot(session, ticker)
        if snap is not None and _aware(snap.captured_at) >= midnight:
            return max(0, abs(int(snap.quantity or 0)))
        closed = sum(int(f.quantity or 0) for f in repo.fills_for_ticker(session, ticker)
                     if f.side == "yes" and f.action == "sell")
        return max(0, int(entry_qty) - closed)

    def _log_exit_abandoned(self, ticker, strategy, remaining, attempts) -> None:
        key = (ticker, strategy)
        if key in self._exit_abandoned:
            return
        self._exit_abandoned.add(key)
        self.summary.exits_abandoned += 1
        logger.critical("live exit exhausted attempts; holding to settlement", extra={
            "extra_fields": {"ticker": ticker, "strategy": strategy,
                             "remaining": remaining, "attempts": attempts}})

    def _place_exit(self, session, ticker, strategy, bid, qty, kind, *, attempt, level) -> None:
        # Close a YES position by SELLING it — exactly the app's "Slide to Sell". These weather
        # markets are fractional_trading_enabled (live probe confirmed), so the accepted order
        # uses the FRACTIONAL fields together: count_fp + yes_price_dollars + type. (Our earlier
        # sell-yes rejections omitted the spec-required `type`; integer count/yes_price mixes the
        # legacy + fractional schemas. The market is structurally identical to threshold markets
        # that DO close, so mutual-exclusivity is not the blocker.) Unique coid per attempt
        # avoids a self-409 on re-tries/escalation.
        coid = f"exit:{strategy}:{ticker}:{attempt}"
        count_fp = f"{int(qty)}.00"
        use_market = level >= 2
        if use_market:
            # Best-effort market sell (immediate cash-out); behind a default-off flag.
            order = {"ticker": ticker, "action": "sell", "side": "yes", "count_fp": count_fp,
                     "type": "market", "client_order_id": coid}
            sell_px = None
        else:
            sell_px = self._exit_sell_price(bid, level)
            order = {"ticker": ticker, "action": "sell", "side": "yes", "count_fp": count_fp,
                     "type": "limit", "yes_price_dollars": f"{sell_px / 100:.2f}",
                     "client_order_id": coid}
            if level >= 1:
                self.summary.exits_escalated += 1
        row = repo.create_live_order(
            session, signal_id=None, ticker=ticker, event_ticker=None, strategy=strategy,
            side="yes", action="sell", limit_price=sell_px, quantity=qty, status="pending",
            client_order_id=coid, raw_order_json=order)
        try:
            resp = self.client.place_order(**order)
        except AuthError:
            repo.update_live_order_status(session, row, status="error", cancel_reason="auth")
            raise
        except TransientError as exc:
            repo.update_live_order_status(session, row, status="unknown", cancel_reason=str(exc))
            logger.warning("live exit transient; status=unknown", extra={"extra_fields": {
                "ticker": ticker, "coid": coid, "attempt": attempt, "level": level}})
            return
        except KalshiAPIError as exc:
            if exc.status_code == 409:  # order_already_exists -> it landed; treat as success
                repo.update_live_order_status(session, row, status="submitted",
                                              cancel_reason="409_already_exists")
                self.summary.exits_placed += 1
                logger.info("live exit 409 already_exists -> submitted", extra={
                    "extra_fields": {"ticker": ticker, "coid": coid}})
                return
            repo.update_live_order_status(
                session, row, status="rejected",
                cancel_reason=f"{exc.status_code}:{exc.message}", raw=order)
            self.summary.rejected += 1
            logger.error("live exit REJECTED", extra={"extra_fields": {
                "ticker": ticker, "strategy": strategy, "coid": coid, "attempt": attempt,
                "level": level, "status_code": exc.status_code, "body": exc.message,
                "payload": order}})  # full body + payload for root-cause analysis
            return
        except Exception as exc:  # noqa: BLE001
            repo.update_live_order_status(session, row, status="error",
                                          cancel_reason=str(exc), raw=order)
            logger.exception("live exit place_order failed")
            return
        koid = (resp or {}).get("order", {}).get("order_id") if isinstance(resp, dict) else None
        repo.update_live_order_status(session, row, status="submitted", kalshi_order_id=koid, raw=resp)
        self.summary.exits_placed += 1
        logger.info("live exit order placed (sell-yes to close)", extra={"extra_fields": {
            "ticker": ticker, "strategy": strategy, "rule": kind,
            "order_type": "market" if level >= 2 else "limit",
            "yes_price_dollars": order.get("yes_price_dollars"), "count": qty,
            "attempt": attempt, "level": level}})

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
