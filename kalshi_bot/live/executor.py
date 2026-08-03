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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .. import repository as repo
from ..kalshi.errors import AuthError, KalshiAPIError, TransientError
from ..paper.engine import kalshi_fee
from ..risk.manager import RiskDecision
from ..scanner.metrics import _to_count, ask_depth_within, parse_dt, price_to_cents
from ..weather.cities import CITIES
from . import exit_rules
from .sizing import is_hot_entry, maker_no_price, maker_offset, order_quantity

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


def _strategy_book(strategy: str | None) -> str:
    """The book prefix of a strategy (the `_h<window>` suffix removed): 'weather_low_fav_h20'
    -> 'weather_low_fav'. Used to match the precise per-cell live allowlist."""
    return _WINDOW_RE.sub("", strategy or "")


_STRIKE_RE = re.compile(r"-[BT][0-9.]+$")


def _event_of(ticker: str | None) -> str:
    """A market ticker's parent event (strike token stripped): 'KXLOWTPHIL-26JUN16-B59.5'
    -> 'KXLOWTPHIL-26JUN16'. Used to dedup per-cell coverage notes per event-day."""
    return _STRIKE_RE.sub("", ticker or "")


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
        self._market_ids: dict[str, str] = {}  # ticker -> v1 market UUID (cached)
        self._cell_skips_noted: set = set()  # (book, event, reason, day) already logged this run

    def reset_summary(self) -> None:
        self.summary = LiveCycleSummary()

    # --- gating -----------------------------------------------------------------

    def _switches_on(self) -> bool:
        s = self.settings
        return s.bot_mode == "live" and not s.kill_switch and s.live_enabled

    def _allowed(self, strategy: str) -> bool:
        # Paper TWIN books (docs/LIVE_PAPER_TWIN.md) must never place a real order. They can't
        # reach here — the twin entry path never calls the executor — but the allowlist is
        # PREFIX-matched, so "mmsell10_pt" would satisfy an "mmsell10" allowlist if a future
        # refactor ever routed a twin through. Defense in depth: reject twin tags outright.
        if strategy in {tt for _lt, tt in self.settings.live_paper_twin_pairs}:
            logger.error("refused live order for a PAPER TWIN tag", extra={"extra_fields": {
                "strategy": strategy}})
            return False
        prefixes = self.settings.live_strategy_list
        return any(strategy.startswith(p) for p in prefixes)

    def _cell_allowed(self, strategy: str, ticker: str) -> bool:
        """Narrow the strategy allowlist to specific cells. If a precise `live_cells` allowlist is
        set, ONLY exact (book, city, window) cells trade — this supersedes the coarse city/window
        cross-product so a mix like high-fav DEN/LAX + low-fav NYC/PHIL can't leak unwanted cells.
        Otherwise fall back to the optional city/window filters."""
        cells = self.settings.live_cell_list
        if cells:
            return (_strategy_book(strategy), _city_of(ticker), _window_of(strategy)) in cells
        cities = self.settings.live_city_list
        if cities and _city_of(ticker) not in cities:
            return False
        windows = self.settings.live_window_list
        if windows and _window_of(strategy) not in windows:
            return False
        return True

    def live_cell_enabled(self, book: str, city: str | None, window: int | None) -> bool:
        """True if (book, city, window) is an enabled live cell (switches on + precise allowlist).
        Used to scope availability/coverage logging to cells we actually trade live."""
        if not self._switches_on():
            return False
        cells = self.settings.live_cell_list
        return bool(cells) and (book, city, window) in cells

    def is_live_cell(self, strategy: str, ticker: str) -> bool:
        return self.live_cell_enabled(_strategy_book(strategy), _city_of(ticker), _window_of(strategy))

    def note_cell_skip(self, session, strategy: str, ticker: str, reason: str) -> None:
        """Record (once per process per cell+event-day) that a LIVE cell was due at its window but
        could NOT trade — no tradeable market / illiquid book. Surfaced by the daily digest so a
        structurally-thin cell is visible vs a one-off miss. Best-effort: never breaks the cycle."""
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = (_strategy_book(strategy), _event_of(ticker), reason, day)
        if key in self._cell_skips_noted:
            return
        self._cell_skips_noted.add(key)
        self.summary.notes.append(f"cell could not trade: {strategy} {ticker} ({reason})")
        logger.warning("live cell could not trade at its window", extra={"extra_fields": {
            "strategy": strategy, "ticker": ticker, "reason": reason}})
        try:
            repo.log_system_event(
                session, level="WARNING", component="live_cell",
                message=f"{strategy} {_event_of(ticker)}: {reason}",
                raw={"strategy": strategy, "ticker": ticker, "reason": reason})
        except Exception:  # noqa: BLE001 — observability must never disturb trading
            logger.exception("note_cell_skip failed")

    def _daily_loss_hit(self, session) -> bool:
        s = self.settings
        if not s.live_kill_on_daily_loss:
            return False
        realized = repo.live_realized_pnl_today(session)
        return realized <= -abs(s.max_daily_loss)

    # --- entry ------------------------------------------------------------------

    def mirror_entry(
        self, session, *, strategy: str, event_ticker: str, ticker: str, side: str,
        action: str, metrics, model_probability=None, account_state=None, hours_to_close=None,
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
        # On-time only: skip a window whose moment already passed (hours-to-close more than the
        # grace below the window). Prevents a LATE catch-up entry when a cell is freshly enabled
        # mid-day or the worker was down across the window — "if we missed it, we missed it".
        win = _window_of(strategy)
        if (win is not None and hours_to_close is not None
                and float(hours_to_close) < win - s.live_entry_grace_hours):
            self.summary.skipped_gate += 1
            logger.info("live entry skipped — window already passed", extra={"extra_fields": {
                "ticker": ticker, "strategy": strategy, "window": win,
                "hours_to_close": round(float(hours_to_close), 2)}})
            return
        if self._daily_loss_tripped or self._daily_loss_hit(session):
            self._daily_loss_tripped = True
            self.summary.skipped_gate += 1
            return
        if repo.live_order_exists(session, event_ticker, strategy):
            self.summary.skipped_dedup += 1
            return
        # One position per event-day: skip a later-window entry while an earlier-window position
        # on the same event is still open (caps real exposure to one bet per city/day).
        if s.live_one_position_per_event and repo.event_has_open_live_position(
                session, event_ticker, exclude_strategy=strategy):
            self.summary.skipped_dedup += 1
            logger.info("live entry skipped — event already holds an open position", extra={
                "extra_fields": {"event_ticker": event_ticker, "strategy": strategy}})
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
        client_order_id = f"{strategy}:{event_ticker}"
        is_v1 = bool(s.live_fractional)
        user_id = (s.live_user_id or "").strip()
        if is_v1:
            # Fractional entries MUST use Kalshi's v1 user-scoped endpoint — the v2
            # /portfolio/orders API rejects every count_fp buy with 'invalid_parameters'
            # (verified live across marketable prices). Spend the dollar cap precisely via a
            # fractional contract count and place a v1 fractional MARKET buy of YES (same shape as
            # the proven v1 close), priced through the ask with a cost cap. Caps still apply as
            # upper bounds; the position snapshot from reconcile is the source of truth (the
            # Integer `quantity` column stores a rounded value for the local record).
            # Bounded slippage: cross up to best_ask + live_entry_slippage_cents and cap the
            # count at the depth available WITHIN that band (not just the single best-ask level),
            # so a thin top-of-book can't shrink the dollar-cap size to a token fill.
            buy_price = max(1, min(99, int(metrics.best_yes_ask) + s.live_entry_slippage_cents))
            fill_depth = ask_depth_within(metrics.raw_orderbook, buy_price)
            qty_fp = round(s.live_max_order_dollars / (price / 100.0), 2)
            qty_fp = min(qty_fp, float(fill_depth),
                         float(decision.max_allowed_quantity), float(s.max_order_size))
            market_id = self._market_id(ticker)
            if qty_fp <= 0 or not user_id or not market_id:
                self.summary.skipped_gate += 1
                if qty_fp > 0 and (not user_id or not market_id):
                    logger.error("live fractional entry missing user_id/market_id; skipped", extra={
                        "extra_fields": {"ticker": ticker, "have_user_id": bool(user_id),
                                         "market_id": market_id}})
                return
            order = self._v1_buy_body(market_id, qty_fp, buy_price)
            qty = max(1, round(qty_fp))
            record_price = buy_price
        else:
            # Bounded-slippage marketable entry: cross the spread up to a price CEILING
            # (best_ask + live_entry_slippage_cents) and size to the dollar cap, but cap the
            # count at the liquidity available WITHIN that band so a thin best-ask level can't
            # shrink the position to a token fill (the bug that bought 1 Denver contract) AND
            # the marketable limit still fully fills with no resting remainder. A passive entry
            # rests one bid below the ask at the best level (unchanged).
            if s.live_entry_style == "passive":
                order_price = price
                fill_depth = metrics.depth_at_best_ask
            else:
                order_price = max(1, min(99, int(metrics.best_yes_ask) + s.live_entry_slippage_cents))
                fill_depth = ask_depth_within(metrics.raw_orderbook, order_price)
            qty_cap = math.floor(s.live_max_order_dollars / (order_price / 100.0))
            qty = min(qty_cap, fill_depth, decision.max_allowed_quantity, s.max_order_size)
            if qty <= 0:
                self.summary.skipped_gate += 1
                return
            order = {
                "ticker": ticker, "action": action, "side": side, "count": qty,
                "type": "limit", "yes_price": order_price, "client_order_id": client_order_id,
            }
            record_price = order_price
        # Persist intent BEFORE the POST, and COMMIT it durably so it survives any later
        # rollback of the cycle-wide transaction. Otherwise a downstream cycle error would
        # erase the intent AFTER the order already hit Kalshi, and the dedup guard (which reads
        # this row) would re-fire a DUPLICATE real order on the next cycle.
        row = repo.create_live_order(
            session, signal_id=None, ticker=ticker, event_ticker=event_ticker,
            strategy=strategy, side=side, action=action, limit_price=record_price, quantity=qty,
            status="pending", client_order_id=client_order_id, raw_order_json=order,
        )
        session.commit()
        try:
            resp = (self.client.create_v1_order(user_id, order) if is_v1
                    else self.client.place_order(**order))
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

        koid = None
        if isinstance(resp, dict):
            o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
            koid = o.get("order_id") or o.get("id")
        # v1 fractional is a market IOC that fills async -> 'submitted'; a v2 passive limit rests.
        status = "resting" if (s.live_entry_style == "passive" and not is_v1) else "submitted"
        repo.update_live_order_status(session, row, status=status, kalshi_order_id=koid, raw=resp)
        self.summary.placed += 1
        logger.info("live order placed", extra={"extra_fields": {
            "ticker": ticker, "strategy": strategy, "price": record_price, "count": qty,
            "fractional": is_v1, "style": s.live_entry_style, "coid": client_order_id,
            "kalshi_order_id": koid}})

    def mirror_mmsell_entry(
        self, session, *, strategy: str, event_ticker: str | None, ticker: str, metrics,
        no_price: int | None = None, account_state=None,
    ) -> str:
        """Mirror one allowlisted mmsell paper entry into a real resting MAKER order: BUY NO at
        the no-bid (== sell yes at the ask), held to settlement. Self-guarded + fail-closed — any
        gate failure places nothing, and any exception is swallowed so the paper record stands.

        Distinct from mirror_entry (the weather YES-taker path): mmsell is a maker NO-buy with no
        window/city/one-per-event semantics, per-TICKER dedup (markets share an event), and its own
        gates — the shared risk.evaluate's spread<=max_spread_cents gate is antithetical to the
        cheap-longshot maker edge (a wide spread is what the maker collects), so it's not used here.

        Returns a short outcome code ("placed", "gate:cap", "rejected", ...). The live/paper twin
        harness records it in the parity tape so a live book that trades fewer candidates than its
        paper twin is explained by the specific gate that stopped it, rather than guessed at.
        """
        s = self.settings
        if not self._allowed(strategy) or not self._switches_on():
            self.summary.skipped_gate += 1
            return "gate:switches"
        if self._daily_loss_tripped or self._daily_loss_hit(session):
            self._daily_loss_tripped = True
            self.summary.skipped_gate += 1
            return "gate:daily_loss"
        # Per-ticker dedup + any in-flight order on this market.
        if repo.live_buy_exists_for_ticker(session, ticker, strategy) \
                or repo.live_open_order_exists(session, ticker):
            self.summary.skipped_dedup += 1
            return "gate:dedup"
        # Cap the book's concurrent live footprint.
        if repo.count_live_book_open(session, strategy) >= s.mmsell_live_max_open_positions:
            self.summary.skipped_gate += 1
            return "gate:open_cap"
        # Data-quality + balance + exposure gates (mmsell-scoped; NOT the weather spread gate).
        if metrics is None or metrics.best_no_bid is None or not metrics.two_sided:
            self.summary.skipped_gate += 1
            return "gate:illiquid"
        if metrics.spread is not None and metrics.spread > s.mmsell_live_max_spread_cents:
            self.summary.skipped_gate += 1
            return "gate:spread"
        balance = None if account_state is None else account_state.get("cash_balance")
        if balance is None:  # fail-closed: refuse to place without a real balance
            self.summary.skipped_gate += 1
            return "gate:no_balance"
        if self._market_exposure(session, ticker) >= s.max_market_exposure:
            self.summary.risk_blocked += 1
            return "gate:exposure"

        # Rest a BUY-NO limit at the no-bid (+offset to improve fill), never paying through the ask
        # — UNLESS this ticker just repriced hard (is_hot_entry), in which case it prices
        # defensively instead (see maker_no_price). Still enters every candidate; only the price
        # changes. Price + size come from kalshi_bot/live/sizing.py — the SAME arithmetic the
        # paper twin uses, so the two can't drift apart and turn the parity read into a
        # bookkeeping artifact.
        hot = is_hot_entry(
            session, ticker, metrics.best_no_bid,
            move_cents=s.mmsell_live_hot_market_move_cents,
            lookback_minutes=s.mmsell_live_hot_market_lookback_minutes,
            lookup=repo.latest_mmsell_no_bid_before,
        )
        offset, ab_arm = maker_offset(
            ticker, hot=hot,
            calm_offset=s.mmsell_live_price_offset_cents,
            hot_offset=s.mmsell_live_hot_market_defensive_offset_cents,
            ab_arms=s.mmsell_live_offset_ab_arm_list,
            ab_salt=s.mmsell_live_offset_ab_salt,
        )
        price = maker_no_price(metrics, no_price, offset, hot=hot)
        if price is None:
            self.summary.skipped_gate += 1
            return "gate:illiquid"
        qty = order_quantity(price, s.live_max_order_dollars, s.max_order_size)
        if qty <= 0:
            self.summary.skipped_gate += 1
            return "gate:size"

        # Audit trail: record an (approved) risk event so the live decision is inspectable.
        # "hot_entry" flags a defensively-priced entry for later analysis — informational only,
        # never blocks placement. "ab_arm:<i>/<offset>c" pins which queue-position arm this entry
        # was priced under, so the experiment stays attributable even if the salt is later changed
        # (the analysis recomputes from the ticker, but only the salt in force at the time is
        # correct — this row is the durable record of what actually happened).
        codes = ["hot_entry"] if hot else []
        if ab_arm is not None:
            codes.append(f"ab_arm:{ab_arm}/{offset}c")
        repo.insert_risk_event(session, None, ticker, RiskDecision(
            approved=True, reason_codes=codes,
            max_allowed_quantity=qty, max_allowed_price=price))

        # Kalshi V2 order (POST /portfolio/events/orders): mmsell SELLS yes (== buys NO), so
        # side="ask" and price is the YES-side price in DOLLARS = (100 - no_price)/100. count and
        # price MUST be decimal strings (Kalshi rejects numeric types with 400). post_only keeps it
        # a pure maker — a PostOnlyCrossCancel just means no fill this cycle, which is safer than
        # accidentally taking and paying the spread. A fresh UUID client_order_id lets the book
        # re-enter a ticker after a timeout-cancel (a stable id would 409); the DB-side dedup +
        # commit-before-POST below prevent double-orders without relying on Kalshi idempotency.
        yes_price = max(1, min(99, 100 - price))
        client_order_id = str(uuid.uuid4())
        order = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": "ask",
            "count": f"{qty:.2f}",
            "price": f"{yes_price / 100.0:.4f}",
            "time_in_force": "good_till_canceled",
            "post_only": True,
            "self_trade_prevention_type": "taker_at_cross",
        }
        # Persist intent BEFORE the POST and COMMIT it durably (survives a later cycle rollback),
        # so the dedup guard can't re-fire a DUPLICATE real order after one already hit Kalshi.
        # limit_price keeps the NO price (our maker cost basis) for the internal record; the actual
        # sent YES-side body is preserved in raw_order_json.
        row = repo.create_live_order(
            session, signal_id=None, ticker=ticker, event_ticker=event_ticker,
            strategy=strategy, side="no", action="buy", limit_price=price, quantity=qty,
            status="pending", client_order_id=client_order_id, raw_order_json=order,
        )
        session.commit()
        try:
            resp = self.client.create_events_order(order)
        except AuthError:
            repo.update_live_order_status(session, row, status="error", cancel_reason="auth")
            raise
        except TransientError as exc:
            # Indeterminate: never assume placed/failed; reconcile resolves via client_order_id.
            repo.update_live_order_status(session, row, status="unknown", cancel_reason=str(exc))
            logger.warning("mmsell live place_order transient; status=unknown",
                           extra={"extra_fields": {"ticker": ticker, "coid": client_order_id}})
            return "unknown"
        except KalshiAPIError as exc:
            if getattr(exc, "status_code", None) == 409:  # a prior identical send landed
                repo.update_live_order_status(session, row, status="submitted",
                                              cancel_reason="409_already_exists")
                self.summary.placed += 1
                return "placed"
            repo.update_live_order_status(session, row, status="rejected", cancel_reason=str(exc))
            self.summary.rejected += 1
            return "rejected"
        except Exception as exc:  # noqa: BLE001 — never let live break the paper record
            repo.update_live_order_status(session, row, status="error", cancel_reason=str(exc))
            logger.exception("mmsell live place_order failed")
            return "error"

        koid = None
        if isinstance(resp, dict):
            o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
            koid = o.get("order_id") or o.get("id")
        # A v2 limit maker order rests until filled/canceled (or auto-settles if it fills).
        repo.update_live_order_status(session, row, status="resting", kalshi_order_id=koid, raw=resp)
        self.summary.placed += 1
        logger.info("mmsell live order placed (resting maker no-buy)", extra={"extra_fields": {
            "ticker": ticker, "strategy": strategy, "no_price": price, "sell_yes_price": yes_price,
            "count": qty, "coid": client_order_id, "kalshi_order_id": koid}})
        return "placed"

    def mirror_theta_entry(
        self, session, *, strategy: str, event_ticker: str | None, ticker: str, metrics,
        no_price: int | None = None, account_state=None,
    ) -> str:
        """Mirror one allowlisted theta paper entry into a real resting MAKER order: BUY NO at
        the no-bid (== sell yes at the ask), held to settlement. Mechanically identical to
        `mirror_mmsell_entry` (same maker convention, same V2 order shape) — this exists as a
        SEPARATE method, not a shared call, because it reads theta's OWN live knobs
        (`theta_live_*`) rather than mmsell's: two live books can run at once now, and sharing a
        dollar/contract cap or an open-position/spread gate would mean resizing one silently
        resizes or gates the other. See docs/THETA_LIVE_PLAN.md and docs/THETA_FILL_MODEL.md
        (this live path is what replaces that doc's borrowed mmsell3 calibration with theta's
        own ground truth).

        Returns a short outcome code ("placed", "gate:cap", "rejected", ...), same vocabulary as
        `mirror_mmsell_entry`, so the live/paper twin harness's parity tape works unmodified.
        """
        s = self.settings
        if not self._allowed(strategy) or not self._switches_on():
            self.summary.skipped_gate += 1
            return "gate:switches"
        if self._daily_loss_tripped or self._daily_loss_hit(session):
            self._daily_loss_tripped = True
            self.summary.skipped_gate += 1
            return "gate:daily_loss"
        # Per-ticker dedup + any in-flight order on this market.
        if repo.live_buy_exists_for_ticker(session, ticker, strategy) \
                or repo.live_open_order_exists(session, ticker):
            self.summary.skipped_dedup += 1
            return "gate:dedup"
        # Cap the book's concurrent live footprint (theta's own cap, not mmsell's).
        if repo.count_live_book_open(session, strategy) >= s.theta_live_max_open_positions:
            self.summary.skipped_gate += 1
            return "gate:open_cap"
        # Data-quality + balance + exposure gates (theta-scoped; NOT the weather spread gate).
        if metrics is None or metrics.best_no_bid is None or not metrics.two_sided:
            self.summary.skipped_gate += 1
            return "gate:illiquid"
        if metrics.spread is not None and metrics.spread > s.theta_live_max_spread_cents:
            self.summary.skipped_gate += 1
            return "gate:spread"
        balance = None if account_state is None else account_state.get("cash_balance")
        if balance is None:  # fail-closed: refuse to place without a real balance
            self.summary.skipped_gate += 1
            return "gate:no_balance"
        if self._market_exposure(session, ticker) >= s.max_market_exposure:
            self.summary.risk_blocked += 1
            return "gate:exposure"

        # Rest a BUY-NO limit at the no-bid (+offset to improve fill), never paying through the ask
        # — UNLESS this ticker just repriced hard (is_hot_entry), in which case it prices
        # defensively instead. Reads theta's OWN ladder tape and its own knobs; a live order was
        # rejected outright with "post only cross" on 2026-08-02 for want of exactly this.
        hot = is_hot_entry(
            session, ticker, metrics.best_no_bid,
            move_cents=s.theta_live_hot_market_move_cents,
            lookback_minutes=s.theta_live_hot_market_lookback_minutes,
            lookup=repo.latest_theta_no_bid_before,
        )
        offset = (s.theta_live_hot_market_defensive_offset_cents if hot
                  else s.theta_live_price_offset_cents)
        price = maker_no_price(metrics, no_price, offset, hot=hot)
        if price is None:
            self.summary.skipped_gate += 1
            return "gate:illiquid"
        qty = order_quantity(price, s.theta_live_max_order_dollars, s.theta_live_max_contracts)
        if qty <= 0:
            self.summary.skipped_gate += 1
            return "gate:size"

        # Audit trail: record an (approved) risk event so the live decision is inspectable.
        # "hot_entry" flags a defensively-priced entry for later analysis — informational only.
        repo.insert_risk_event(session, None, ticker, RiskDecision(
            approved=True, reason_codes=["hot_entry"] if hot else [],
            max_allowed_quantity=qty, max_allowed_price=price))

        # Kalshi V2 order (POST /portfolio/events/orders): theta SELLS yes (== buys NO), so
        # side="ask" and price is the YES-side price in DOLLARS = (100 - no_price)/100 — identical
        # shape to mmsell's live order (see mirror_mmsell_entry's comment for the full rationale).
        yes_price = max(1, min(99, 100 - price))
        client_order_id = str(uuid.uuid4())
        order = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": "ask",
            "count": f"{qty:.2f}",
            "price": f"{yes_price / 100.0:.4f}",
            "time_in_force": "good_till_canceled",
            "post_only": True,
            "self_trade_prevention_type": "taker_at_cross",
        }
        row = repo.create_live_order(
            session, signal_id=None, ticker=ticker, event_ticker=event_ticker,
            strategy=strategy, side="no", action="buy", limit_price=price, quantity=qty,
            status="pending", client_order_id=client_order_id, raw_order_json=order,
        )
        session.commit()
        try:
            resp = self.client.create_events_order(order)
        except AuthError:
            repo.update_live_order_status(session, row, status="error", cancel_reason="auth")
            raise
        except TransientError as exc:
            repo.update_live_order_status(session, row, status="unknown", cancel_reason=str(exc))
            logger.warning("theta live place_order transient; status=unknown",
                           extra={"extra_fields": {"ticker": ticker, "coid": client_order_id}})
            return "unknown"
        except KalshiAPIError as exc:
            if getattr(exc, "status_code", None) == 409:  # a prior identical send landed
                repo.update_live_order_status(session, row, status="submitted",
                                              cancel_reason="409_already_exists")
                self.summary.placed += 1
                return "placed"
            repo.update_live_order_status(session, row, status="rejected", cancel_reason=str(exc))
            self.summary.rejected += 1
            return "rejected"
        except Exception as exc:  # noqa: BLE001 — never let live break the paper record
            repo.update_live_order_status(session, row, status="error", cancel_reason=str(exc))
            logger.exception("theta live place_order failed")
            return "error"

        koid = None
        if isinstance(resp, dict):
            o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
            koid = o.get("order_id") or o.get("id")
        repo.update_live_order_status(session, row, status="resting", kalshi_order_id=koid, raw=resp)
        self.summary.placed += 1
        logger.info("theta live order placed (resting maker no-buy)", extra={"extra_fields": {
            "ticker": ticker, "strategy": strategy, "no_price": price, "sell_yes_price": yes_price,
            "count": qty, "coid": client_order_id, "kalshi_order_id": koid}})
        return "placed"

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
                # A v1 order (fractional entry / bucket close) is NEVER visible in the v2 orders
                # feed, so 'not found here' is not proof of anything. Resolve it from Kalshi's
                # fresh FILLS first — the accurate terminal status. The fill carries the exchange
                # order_id, so we also backfill it onto the row for a clean audit trail.
                fill = self._fill_for_order(row, fills)
                if fill is not None:
                    repo.update_live_order_status(
                        session, row, status="filled",
                        kalshi_order_id=row.kalshi_order_id or fill.get("order_id"))
                elif row.status in ("pending", "unknown"):
                    # No fill yet. If a position exists the order likely executed (fill not in
                    # this batch) -> 'submitted' (committed) so dedup can't re-fire a DUPLICATE;
                    # otherwise it genuinely never landed -> 'not_landed' (re-entry allowed).
                    if self._executed_on_exchange(row, fills, positions):
                        repo.update_live_order_status(session, row, status="submitted",
                                                      cancel_reason="resolved_by_exchange_state")
                    else:
                        repo.update_live_order_status(session, row, status="not_landed",
                                                      cancel_reason="not_found_on_exchange")
                # else: submitted/resting with no fill yet -> leave; a later cycle resolves it.
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

        # v1 IOC closes aren't in the v2 orders feed; resolve them by their fill (or age-out).
        self._resolve_v1_exits(session)

        # Snapshot positions.
        # Kalshi market_positions shape (confirmed via real positions): position_fp
        # (signed fixed-point), market_exposure_dollars, realized_pnl_dollars.
        for p in positions:
            pos_fp = _to_float(_first(p, "position_fp", "position")) or 0.0  # fractional, signed
            pos = _to_count(_first(p, "position_fp", "position"))            # rounded (legacy col)
            exposure = _to_float(_first(p, "market_exposure_dollars", "market_exposure"))
            realized = _to_float(_first(p, "realized_pnl_dollars", "realized_pnl"))
            qty = abs(pos_fp)
            avg_px = round(exposure / qty * 100, 2) if (exposure is not None and qty) else None
            repo.insert_position_snapshot(
                session, ticker=p.get("ticker"),
                side="yes" if pos_fp >= 0 else "no", quantity=pos, quantity_fp=pos_fp,
                avg_price=avg_px, market_exposure=exposure, realized_pnl=realized, raw_json=p)
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
                    # V2 events endpoint (mmsell is the only book that rests live orders).
                    self.client.cancel_events_order(row.kalshi_order_id)
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

    def run_probe(self, session) -> None:
        """One-shot isolated PROBE for fractional buy/sell verification (gated on `live_probe`,
        empty = off). NEVER touches the strategy pipeline — a `close` only matches the given
        ticker prefix, so the strategy's positions are untouched. Directives:
          buy:<ticker>:<dollars>  -> v1 fractional market buy (count_fp = dollars/ask)
          close:<ticker_prefix>   -> targeted v1 close of matching open positions only."""
        directive = (self.settings.live_probe or "").strip()
        if not directive or not self._switches_on():
            return
        parts = directive.split(":")
        kind = parts[0].lower()
        try:
            if kind == "buy" and len(parts) >= 3:
                self._probe_buy(session, parts[1], float(parts[2]))
            elif kind == "close" and len(parts) >= 2:
                self._probe_close(session, parts[1])
            else:
                logger.error("live_probe not understood",
                             extra={"extra_fields": {"directive": directive}})
        except AuthError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("live probe failed", extra={"extra_fields": {"directive": directive}})

    @staticmethod
    def _fill_for_order(row, fills):
        """The Kalshi fill (from the fresh fills batch) belonging to a live_order, used to resolve
        v1 orders the v2 feed never shows. Match by exchange order_id when we have one; otherwise
        (an indeterminate/transient POST left no order_id) fall back to a same-ticker, same-ACTION
        fill (buy<->entry, sell<->close). Returns the fill dict or None."""
        koid = row.kalshi_order_id
        if koid:
            for f in fills:
                if f.get("order_id") == koid:
                    return f
            return None  # we have an id but no matching fill -> not filled (yet)
        tkr = row.market_ticker
        for f in fills:
            if (f.get("market_ticker") or f.get("ticker")) == tkr and f.get("action") == row.action:
                return f
        return None

    @staticmethod
    def _executed_on_exchange(row, fills, positions) -> bool:
        """Evidence (from Kalshi's freshly-fetched fills/positions) that an indeterminate order
        actually executed — an open position on its ticker, or a same-action fill for it. Used so
        a v1 order whose POST was transient/indeterminate isn't wrongly declared 'not_landed'
        (which would let entry-dedup re-fire a duplicate). Conservative: any evidence -> executed."""
        tkr = row.market_ticker
        for p in positions:
            if p.get("ticker") == tkr:
                pf = p.get("position_fp")
                if pf is None:
                    pf = p.get("position")
                try:
                    if pf is not None and abs(float(pf)) >= 0.01:
                        return True
                except (TypeError, ValueError):
                    pass
        for f in fills:
            if (f.get("market_ticker") or f.get("ticker")) == tkr and f.get("action") == row.action:
                return True
        return False

    @staticmethod
    def _v1_buy_body(market_id: str, count_fp: float, buy_price: int) -> dict:
        """A v1 fractional MARKET buy of YES (the only order shape Kalshi accepts for fractional;
        the v2 endpoint rejects count_fp). Mirrors the proven v1 close vocabulary, flipped to buy:
        priced through the ask (buy_price) with a generous cost cap. Shared by the strategy's
        fractional entry and the isolated probe so they stay byte-for-byte identical."""
        return {
            "market_id": market_id,
            "user_side": "yes",
            "side": "yes",
            "order_action": "buy",
            "order_type": "market",
            "time_in_force": "immediate_or_cancel",
            "count_fp": f"{count_fp:.2f}",
            "price_dollars": f"{buy_price / 100:.4f}",
            "sell_position_capped": False,
            "post_only": False,
            "expiration_unix_ts": 0,
            "max_cost_cents": int(math.ceil(count_fp * buy_price)) + 5,
        }

    def _probe_buy(self, session, ticker: str, dollars: float) -> None:
        coid = f"probe:buy:{ticker}"
        prior = repo.get_live_order_by_client_id(session, coid)
        if prior and prior.status not in ("rejected", "not_landed", "canceled", "error"):
            return  # one-shot per ticker (already submitted/filled); retry only after a failure
        from ..scanner.metrics import compute_metrics
        ob = self.client.get_orderbook(ticker, depth=self.settings.orderbook_depth)
        mx = compute_metrics({"ticker": ticker}, ob, top_n=self.settings.orderbook_depth)
        ask = mx.best_yes_ask
        if not ask or not (1 <= ask <= 99):
            logger.error("probe buy: no ask", extra={"extra_fields": {"ticker": ticker}})
            return
        # FRACTIONAL is a v1-only capability: the v2 /portfolio/orders endpoint rejects ANY
        # count_fp buy with 'invalid_parameters' (verified live across yes_price 91..99 — all
        # marketable). The web app does every fractional trade through the v1 user-scoped
        # endpoint, which accepts count_fp (our v1 close proves it). So place a v1 fractional
        # MARKET buy of YES, mirroring the EXACT field vocabulary Kalshi accepted for the v1
        # close (flipped buy/yes), priced a couple cents through the ask with a cost cap.
        user_id = (self.settings.live_user_id or "").strip()
        market_id = self._market_id(ticker)
        if not user_id or not market_id:
            logger.error("probe buy cannot build v1 order (missing user_id/market_id)", extra={
                "extra_fields": {"ticker": ticker, "have_user_id": bool(user_id),
                                 "market_id": market_id}})
            return
        buy_price = min(99, int(ask) + 2)
        count_fp = round(dollars / (ask / 100.0), 2)
        if count_fp < 0.01:
            return
        order = self._v1_buy_body(market_id, count_fp, buy_price)
        row = repo.create_live_order(
            session, signal_id=None, ticker=ticker, event_ticker=None, strategy="probe",
            side="yes", action="buy", limit_price=buy_price, quantity=max(1, round(count_fp)),
            status="pending", client_order_id=coid, raw_order_json=order)
        session.commit()  # durable intent before POST — survives later cycle rollback (no dup order)
        logger.info("live PROBE buy (v1 fractional)", extra={"extra_fields": {
            "ticker": ticker, "dollars": dollars, "ask": ask, "buy_price": buy_price,
            "count_fp": f"{count_fp:.2f}", "market_id": market_id, "payload": order}})
        try:
            resp = self.client.create_v1_order(user_id, order)
        except AuthError as exc:
            repo.update_live_order_status(session, row, status="error", cancel_reason=f"v1_auth:{exc}")
            logger.error("probe buy v1 AUTH FAILED", extra={"extra_fields": {
                "ticker": ticker, "error": str(exc)}})
            return
        except KalshiAPIError as exc:
            if exc.status_code == 409:
                repo.update_live_order_status(session, row, status="submitted", cancel_reason="409")
                return
            repo.update_live_order_status(session, row, status="rejected",
                                          cancel_reason=f"{exc.status_code}:{exc.message}", raw=order)
            logger.error("probe buy REJECTED", extra={"extra_fields": {
                "ticker": ticker, "status_code": exc.status_code, "body": exc.message, "payload": order}})
            return
        koid = None
        if isinstance(resp, dict):
            o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
            koid = o.get("order_id") or o.get("id")
        repo.update_live_order_status(session, row, status="submitted", kalshi_order_id=koid, raw=resp)
        logger.info("live PROBE buy submitted (v1)", extra={"extra_fields": {
            "ticker": ticker, "order_id": koid}})

    def _probe_close(self, session, prefix: str) -> None:
        for ticker, strategy, _entry_price, _entry_at, entry_qty in repo.open_live_positions(session):
            if not ticker.startswith(prefix):
                continue  # ONLY the probe's ticker(s) — never the strategy's positions
            remaining = self._remaining_open_qty(session, ticker, entry_qty)
            if remaining < 0.01:
                continue
            if repo.live_exit_in_flight(session, ticker, strategy):
                continue
            attempts = repo.count_exit_attempts(session, ticker, strategy)
            if attempts >= self.settings.live_exit_max_attempts:
                self._log_exit_abandoned(ticker, strategy, remaining, attempts)
                continue
            bid = self._current_bid(ticker)
            if bid is None:
                continue
            self._place_exit(session, ticker, strategy, bid, remaining, "probe_close",
                             attempt=attempts + 1, level=0)

    def _window_exit_params(self, strategy: str | None) -> tuple:
        """(take_profit, stop_loss, break_even) for a position's entry window. A window listed in
        `live_take_profit_by_window` (e.g. h20 scalps +5, h14 runs to +20) is TP-ONLY — no stop,
        since stops whipsaw these high-win favorites out of trades that settle YES. Windows not
        listed fall back to the global TP/SL/BE settings."""
        s = self.settings
        wmap = s.live_tp_by_window_map
        win = _window_of(strategy)
        if wmap and win in wmap:
            return (wmap[win], None, None)
        return (s.live_take_profit_cents, s.live_stop_loss_cents, s.live_break_even_arm_cents)

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
            if remaining < 0.01:  # flat (or sub-0.01 dust that can't be traded)
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
                tp, sl, be = self._window_exit_params(strategy)
                kind = exit_rules.should_exit(entry_price, history, live_bid, tp=tp, sl=sl, be=be)
                if kind is None:
                    continue
            else:
                # Already committed to closing — keep trying to get fully out (escalating price)
                # rather than leaving a partial/stuck position because the trigger no longer holds.
                kind = "reattempt"
                self.summary.exits_reattempted += 1
            self._place_exit(session, ticker, strategy, live_bid, remaining, kind,
                             attempt=attempts + 1, level=self._escalation_level(attempts))

    def _resolve_v1_exits(self, session) -> None:
        """Resolve submitted v1 exit (close) orders, which the v2 orders feed can't see. The v1
        IOC returns 'pending' + an order_id and fills async; match the fill by that order_id ->
        'filled'. If no fill lands within a bounded window, the IOC expired -> 'canceled' (which
        also unblocks a re-attempt). Keeps order status honest without affecting the
        position-as-truth re-attempt logic."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=180)
        for row in repo.get_nonterminal_live_orders(session):
            if not (row.client_order_id or "").startswith("exit:"):
                continue
            if row.kalshi_order_id and repo.fills_for_order(session, row.kalshi_order_id):
                repo.update_live_order_status(session, row, status="filled")
            elif _aware(row.created_at) < cutoff:
                repo.update_live_order_status(session, row, status="canceled",
                                              cancel_reason="ioc_no_fill")

    def _escalation_level(self, attempts: int) -> int:
        """0 = base marketable limit; 1 = aggressive (slippage-buffered) limit;
        2 = optional best-effort market order on the final attempt."""
        if attempts == 0:
            return 0
        if (attempts + 1) >= self.settings.live_exit_max_attempts and \
                self.settings.live_exit_use_market_fallback:
            return 2
        return 1

    def _market_id(self, ticker: str) -> str | None:
        """Resolve a ticker to its market UUID (cached). The v1 order endpoint requires the
        UUID, which only the v1 event's nested markets expose. ticker = SERIES-DATE-STRIKE."""
        mid = self._market_ids.get(ticker)
        if mid:
            return mid
        parts = ticker.split("-")
        if len(parts) < 3:
            logger.error("cannot derive event from ticker", extra={"extra_fields": {"ticker": ticker}})
            return None
        series, event = parts[0], f"{parts[0]}-{parts[1]}"
        try:
            data = self.client.get_v1_event(series, event)
        except AuthError as exc:
            logger.error("v1 event lookup AUTH FAILED — API key not accepted on /v1 endpoint",
                         extra={"extra_fields": {"ticker": ticker, "error": str(exc)}})
            return None
        except KalshiAPIError as exc:
            logger.error("v1 event lookup API error", extra={"extra_fields": {
                "ticker": ticker, "status_code": exc.status_code, "path": exc.path,
                "body": exc.message}})
            return None
        except Exception:  # noqa: BLE001
            logger.exception("v1 event lookup failed", extra={"extra_fields": {"ticker": ticker}})
            return None
        ev = data.get("event") if isinstance(data.get("event"), dict) else data
        markets = (ev or {}).get("markets") or data.get("markets") or []
        for mk in markets:
            if (mk.get("ticker_name") or mk.get("ticker")) == ticker:
                mid = mk.get("id") or mk.get("market_id")
                break
        if mid:
            self._market_ids[ticker] = mid
        else:
            logger.error("v1 event has no matching market id", extra={"extra_fields": {
                "ticker": ticker, "n_markets": len(markets)}})
        return mid

    def _remaining_open_qty(self, session, ticker: str, entry_qty: int) -> float:
        """Remaining open YES contracts from Kalshi truth, FRACTIONAL. The latest position
        snapshot (refreshed by reconcile each cycle) is authoritative — prefer its fractional
        quantity_fp so the close sizes to the exact remainder (a fractional position rounded to a
        whole count under-closes, leaving a residual). 0 means flat. Falls back to the entry qty
        minus already-filled exit contracts if no fresh snapshot exists yet."""
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        snap = repo.latest_position_snapshot(session, ticker)
        if snap is not None and _aware(snap.captured_at) >= midnight:
            if snap.quantity_fp is not None:
                return max(0.0, abs(float(snap.quantity_fp)))
            return float(max(0, abs(int(snap.quantity or 0))))
        closed = sum(int(f.quantity or 0) for f in repo.fills_for_ticker(session, ticker)
                     if f.side == "yes" and f.action == "sell")
        return float(max(0, int(entry_qty) - closed))

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
        # Close via Kalshi's v1 user-scoped order endpoint (POST /v1/users/{user_id}/orders) —
        # the path the web app uses, which ACCEPTS closes on range-bucket markets that the v2
        # /portfolio/orders endpoint rejects (captured from the app's Slide-to-Sell request).
        # Body = a position-capped market sell of the held (YES) side. coid is local-only for
        # attempt tracking (v1 has no client_order_id; sell_position_capped guards a double-send).
        coid = f"exit:{strategy}:{ticker}:{attempt}"
        user_id = (self.settings.live_user_id or "").strip()
        market_id = self._market_id(ticker)
        if not user_id or not market_id:
            logger.error("live exit cannot build v1 close (missing user_id/market_id)", extra={
                "extra_fields": {"ticker": ticker, "have_user_id": bool(user_id),
                                 "market_id": market_id}})
            self._log_exit_abandoned(ticker, strategy, qty, attempt)
            return
        # price_dollars is the NO-side marketable price = 100 - yes_bid (the app sent 0.01 only
        # because its position was at yes~99c). An IOC that doesn't cross is canceled unfilled,
        # so derive it from the live bid (+ optional slippage to cross deeper and guarantee fill).
        no_price = max(1, min(99, 100 - int(bid) + self.settings.live_exit_slippage_cents))
        order = {
            "market_id": market_id,
            "user_side": "yes",          # the bot only holds YES (buy-favorite entries)
            "side": "no",
            "order_action": "sell",
            "order_type": "market",
            "time_in_force": "immediate_or_cancel",
            "count_fp": f"{float(qty):.2f}",  # close the EXACT (fractional) remainder
            "price_dollars": f"{no_price / 100:.4f}",
            "sell_position_capped": True,
            "post_only": False,
            "expiration_unix_ts": 0,
            "max_cost_cents": 0,
        }
        if level >= 1:
            self.summary.exits_escalated += 1
        row = repo.create_live_order(
            session, signal_id=None, ticker=ticker, event_ticker=None, strategy=strategy,
            side="yes", action="sell", limit_price=int(bid), quantity=max(1, round(float(qty))),
            status="pending", client_order_id=coid, raw_order_json=order)
        session.commit()  # durable intent before POST — survives later cycle rollback (no dup order)
        logger.info("live exit attempt (v1 close)", extra={"extra_fields": {
            "ticker": ticker, "coid": coid, "market_id": market_id, "payload": order}})
        try:
            resp = self.client.create_v1_order(user_id, order)
        except AuthError as exc:
            # v1 endpoint auth is experimental (the app used a browser session; we use the API
            # key). If the key isn't accepted on /v1, do NOT crash the worker — log clearly,
            # abandon the close (hold to settlement). The v2 entry path keeps strict auth.
            repo.update_live_order_status(session, row, status="error", cancel_reason=f"v1_auth:{exc}")
            logger.error("live v1 close AUTH FAILED — API key not accepted on /v1 endpoint",
                         extra={"extra_fields": {"ticker": ticker, "coid": coid, "error": str(exc)}})
            self._log_exit_abandoned(ticker, strategy, qty, attempt)
            return
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
        # The v1 close returns status='pending' + an order_id and FILLS asynchronously (the fill
        # carries that order_id). Mark it 'submitted' with the order_id; reconcile resolves it to
        # 'filled' when the matching fill lands, or 'canceled' if the IOC ages out unfilled
        # (_resolve_v1_exits). The position snapshot stays the source of truth for re-attempts.
        koid = None
        ostatus = ""
        if isinstance(resp, dict):
            o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
            koid = o.get("order_id") or o.get("id")
            ostatus = str(o.get("status") or "").lower()
        repo.update_live_order_status(session, row, status="submitted", kalshi_order_id=koid, raw=resp)
        self.summary.exits_placed += 1
        logger.info("live exit order placed (v1 close)", extra={"extra_fields": {
            "ticker": ticker, "strategy": strategy, "rule": kind, "resp_status": ostatus,
            "no_price": no_price, "market_id": market_id, "count": qty, "attempt": attempt}})

    def _current_bid(self, ticker: str) -> int | None:
        try:
            from ..scanner.metrics import compute_metrics
            ob = self.client.get_orderbook(ticker, depth=self.settings.orderbook_depth)
            mx = compute_metrics({"ticker": ticker}, ob, top_n=self.settings.orderbook_depth)
            return mx.best_yes_bid
        except Exception:  # noqa: BLE001
            return None

    def _current_yes_ask(self, ticker: str) -> int | None:
        try:
            from ..scanner.metrics import compute_metrics
            ob = self.client.get_orderbook(ticker, depth=self.settings.orderbook_depth)
            mx = compute_metrics({"ticker": ticker}, ob, top_n=self.settings.orderbook_depth)
            return mx.best_yes_ask
        except Exception:  # noqa: BLE001
            return None

    # --- mmsell one-shot closeout (end of strategy — no other path closes a NO position) -----

    def close_mmsell_positions(self, session) -> int:
        """ONE-SHOT: close every open NO position for `mmsell_closeout_strategies` by BUYING YES
        at the current ask (marketable, crosses the spread deliberately — a close must guarantee
        execution, not rest as a maker like the entry does). Uses the same V2 events endpoint as
        mmsell entries. Tagged distinctly (client_order_id 'closeout:...', DB strategy suffixed
        '_closeout') so it reads unambiguously in live_orders as an intentional end-of-strategy
        exit. Gated on its OWN flag + explicit allowlist — independent of LIVE_STRATEGIES (so
        clearing the entry allowlist stops new entries while this still closes what's open) —
        but order placement is still subject to the client's own bot_mode+KILL_SWITCH guard, so
        KILL_SWITCH must be false for closes to actually reach Kalshi. Self-guarded per-position:
        one failure never blocks the rest. Returns the count of close orders placed."""
        s = self.settings
        if not s.mmsell_closeout_enabled or s.bot_mode != "live":
            return 0
        placed = 0
        for prefix in s.mmsell_closeout_strategy_list:
            for ticker, strategy, entry_price, _entry_at, qty in repo.open_live_no_positions(
                session, prefix
            ):
                if qty <= 0:
                    continue
                # SAFETY: `qty` above is the FULL Kalshi account-level position on this ticker,
                # not scoped to `prefix` — Kalshi has no notion of our internal book tags. If
                # another live strategy (e.g. theta4) also holds this exact ticker, closing it
                # out here would sell THEIR contracts too, mislabeled as an mmsell exit. Skip
                # rather than guess; a shared ticker needs a human, not an automated closeout.
                other = repo.other_live_no_strategies_on_ticker(session, ticker, prefix)
                if other:
                    logger.error(
                        "mmsell closeout SKIPPED shared ticker (also held by another live "
                        "strategy) -- resolve manually", extra={"extra_fields": {
                            "ticker": ticker, "strategy": prefix, "also_held_by": other}})
                    continue
                if repo.live_open_order_exists(session, ticker):
                    continue  # an order is already in flight -- let it resolve before retrying
                ask = self._current_yes_ask(ticker)
                if ask is None:
                    logger.warning("mmsell closeout: no ask available, skipping this cycle",
                                   extra={"extra_fields": {"ticker": ticker, "strategy": strategy}})
                    continue
                price = max(1, min(99, int(ask) + s.mmsell_closeout_slippage_cents))
                client_order_id = f"closeout:{strategy}:{ticker}:{uuid.uuid4()}"
                # EXACT field set of a recorded status-201 taker-IOC request (ground truth) -- do
                # not add fields chasing "extra safety": doing so cost a live round. reduce_only,
                # though documented on CreateOrderV2Request, is a perps-oriented field and produced
                # 400 invalid_parameters on these event-contract orders (0/48 accepted with it vs
                # 2 accepted without it) -- so it is deliberately NOT sent. post_only is also
                # omitted: it contradicts immediate_or_cancel (400 invalid_parameters when paired).
                order = {
                    "ticker": ticker,
                    "client_order_id": client_order_id,
                    "side": "bid",              # buy YES -> flattens a held NO position
                    "count": f"{qty:.2f}",
                    "price": f"{price / 100.0:.4f}",
                    "time_in_force": "immediate_or_cancel",  # guarantee execution, don't rest
                    "self_trade_prevention_type": "taker_at_cross",  # REQUIRED by the schema
                    #    (confirmed live: omitting it -> 400 missing_parameters).
                }
                row = repo.create_live_order(
                    session, signal_id=None, ticker=ticker, event_ticker=None,
                    strategy=f"{strategy}_closeout", side="yes", action="buy",
                    limit_price=price, quantity=qty, status="pending",
                    client_order_id=client_order_id, raw_order_json=order,
                )
                session.commit()  # durable intent before POST — survives a later cycle rollback
                logger.warning("mmsell live position CLOSEOUT (end of strategy)", extra={
                    "extra_fields": {"ticker": ticker, "strategy": strategy,
                                     "entry_no_price": entry_price, "close_yes_price": price,
                                     "qty": qty, "coid": client_order_id}})
                try:
                    resp = self.client.create_events_order(order)
                except AuthError:
                    repo.update_live_order_status(session, row, status="error", cancel_reason="auth")
                    raise
                except TransientError as exc:
                    repo.update_live_order_status(session, row, status="unknown",
                                                  cancel_reason=str(exc))
                    continue
                except KalshiAPIError as exc:
                    if getattr(exc, "status_code", None) == 409:
                        repo.update_live_order_status(session, row, status="submitted",
                                                      cancel_reason="409_already_exists")
                        placed += 1
                        continue
                    repo.update_live_order_status(session, row, status="rejected",
                                                  cancel_reason=str(exc))
                    logger.error("mmsell closeout REJECTED", extra={"extra_fields": {
                        "ticker": ticker, "strategy": strategy, "coid": client_order_id,
                        "error": str(exc)}})
                    continue
                except Exception as exc:  # noqa: BLE001 — one failure must not block the rest
                    repo.update_live_order_status(session, row, status="error",
                                                  cancel_reason=str(exc))
                    logger.exception("mmsell closeout order failed")
                    continue
                koid = None
                if isinstance(resp, dict):
                    o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
                    koid = o.get("order_id") or o.get("id")
                repo.update_live_order_status(session, row, status="submitted",
                                              kalshi_order_id=koid, raw=resp)
                placed += 1
        return placed

    def close_theta_positions(self, session) -> int:
        """ONE-SHOT: close every open NO position for `theta_closeout_strategies`. Identical
        mechanism to `close_mmsell_positions` (see its docstring for the full rationale) — a
        separate method only because it reads `theta_closeout_*` instead of `mmsell_closeout_*`;
        `repo.open_live_no_positions` is already generic over any strategy prefix. Gated on its
        OWN flag + explicit allowlist, independent of LIVE_STRATEGIES. Self-guarded per-position.
        Returns the count of close orders placed."""
        s = self.settings
        if not s.theta_closeout_enabled or s.bot_mode != "live":
            return 0
        placed = 0
        for prefix in s.theta_closeout_strategy_list:
            for ticker, strategy, entry_price, _entry_at, qty in repo.open_live_no_positions(
                session, prefix
            ):
                if qty <= 0:
                    continue
                # SAFETY: see close_mmsell_positions's identical guard -- `qty` above is the FULL
                # Kalshi account-level position on this ticker, not scoped to `prefix`. If another
                # live strategy (e.g. mmsell10) also holds this exact ticker, closing it out here
                # would sell THEIR contracts too. Skip rather than guess.
                other = repo.other_live_no_strategies_on_ticker(session, ticker, prefix)
                if other:
                    logger.error(
                        "theta closeout SKIPPED shared ticker (also held by another live "
                        "strategy) -- resolve manually", extra={"extra_fields": {
                            "ticker": ticker, "strategy": prefix, "also_held_by": other}})
                    continue
                if repo.live_open_order_exists(session, ticker):
                    continue  # an order is already in flight -- let it resolve before retrying
                ask = self._current_yes_ask(ticker)
                if ask is None:
                    logger.warning("theta closeout: no ask available, skipping this cycle",
                                   extra={"extra_fields": {"ticker": ticker, "strategy": strategy}})
                    continue
                price = max(1, min(99, int(ask) + s.theta_closeout_slippage_cents))
                client_order_id = f"closeout:{strategy}:{ticker}:{uuid.uuid4()}"
                order = {
                    "ticker": ticker,
                    "client_order_id": client_order_id,
                    "side": "bid",              # buy YES -> flattens a held NO position
                    "count": f"{qty:.2f}",
                    "price": f"{price / 100.0:.4f}",
                    "time_in_force": "immediate_or_cancel",  # guarantee execution, don't rest
                    "self_trade_prevention_type": "taker_at_cross",  # REQUIRED by the schema
                }
                row = repo.create_live_order(
                    session, signal_id=None, ticker=ticker, event_ticker=None,
                    strategy=f"{strategy}_closeout", side="yes", action="buy",
                    limit_price=price, quantity=qty, status="pending",
                    client_order_id=client_order_id, raw_order_json=order,
                )
                session.commit()  # durable intent before POST — survives a later cycle rollback
                logger.warning("theta live position CLOSEOUT (end of strategy)", extra={
                    "extra_fields": {"ticker": ticker, "strategy": strategy,
                                     "entry_no_price": entry_price, "close_yes_price": price,
                                     "qty": qty, "coid": client_order_id}})
                try:
                    resp = self.client.create_events_order(order)
                except AuthError:
                    repo.update_live_order_status(session, row, status="error", cancel_reason="auth")
                    raise
                except TransientError as exc:
                    repo.update_live_order_status(session, row, status="unknown",
                                                  cancel_reason=str(exc))
                    continue
                except KalshiAPIError as exc:
                    if getattr(exc, "status_code", None) == 409:
                        repo.update_live_order_status(session, row, status="submitted",
                                                      cancel_reason="409_already_exists")
                        placed += 1
                        continue
                    repo.update_live_order_status(session, row, status="rejected",
                                                  cancel_reason=str(exc))
                    logger.error("theta closeout REJECTED", extra={"extra_fields": {
                        "ticker": ticker, "strategy": strategy, "coid": client_order_id,
                        "error": str(exc)}})
                    continue
                except Exception as exc:  # noqa: BLE001 — one failure must not block the rest
                    repo.update_live_order_status(session, row, status="error",
                                                  cancel_reason=str(exc))
                    logger.exception("theta closeout order failed")
                    continue
                koid = None
                if isinstance(resp, dict):
                    o = resp.get("order") if isinstance(resp.get("order"), dict) else resp
                    koid = o.get("order_id") or o.get("id")
                repo.update_live_order_status(session, row, status="submitted",
                                              kalshi_order_id=koid, raw=resp)
                placed += 1
        return placed

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
