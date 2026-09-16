"""The execution telemetry collector: state machine + the daemon thread that drives it.

docs/MMSELL_QUEUE_FILL_TELEMETRY.md §6. Two layers on purpose:

* `CollectorState` — everything that decides and writes. Fed WebSocket frames and clock ticks,
  it returns the outbound commands it wants sent. No socket, no sleep, no thread: fully
  testable with SQLite and a fake read-only client.
* `TelemetryThread` — the thin glue: connect with the signed upgrade headers, pump frames into
  the state, send what it returns, poll the clock, back off on failure. Every loop body is
  wrapped; nothing here can raise into the trading loop.

Rules the code enforces rather than documents:
  - the state holds a `ReadOnlyKalshi` (GET only) — it structurally cannot write an order;
  - it never writes `live_orders`; the tracked set is READ from the database every scan so a
    restart, or an order placed before the thread started, is picked up without shared memory;
  - a failed or unreadable poll writes a NULL tick and a collector event, never a zero;
  - a sequence gap invalidates the local book and asks for a fresh snapshot; features derived
    from an invalid book say so (`book_valid: false`) rather than looking like a quiet book;
  - raw event persistence is capped per minute and the drop count is itself recorded.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import repository as repo
from ..kalshi.errors import AuthError
from ..live.queue_position import order_id_of, parse_batch
from .book import PRICE_CONVENTION, LocalBook
from .parse import dollars_to_cents, envelope, fp_to_float, int_or_none

logger = logging.getLogger(__name__)

# Queue-sample triggers (live_order_queue_ticks.trigger).
TRIGGER_AT_REST = "at_rest"
TRIGGER_INTERVAL = "interval"
TRIGGER_EVENT_TRADE = "event:trade"
TRIGGER_EVENT_DELTA = "event:delta"
TRIGGER_EVENT_FILL = "event:fill"
TRIGGER_TERMINAL = "terminal"

# Collector-event kinds (execution_collector_events.kind).
EV_THREAD_STARTED = "thread_started"
EV_THREAD_STOPPED = "thread_stopped"
EV_DISABLED = "disabled"
EV_CONNECTED = "connected"
EV_DISCONNECTED = "disconnected"
EV_SUBSCRIBED = "subscribed"
EV_UNSUBSCRIBED = "unsubscribed"
EV_SEQ_GAP = "seq_gap"
EV_SNAPSHOT_REQUESTED = "snapshot_requested"
EV_BOOK_INVALID = "book_invalid"
EV_WS_ERROR = "ws_error"
EV_POLL_FAILED = "poll_failed"
EV_POLL_MISSING = "poll_missing"
EV_RATE_LIMITED = "rate_limited"
EV_THROTTLED = "throttled"
EV_LOOP_ERROR = "loop_error"
EV_UNPARSED = "unparsed"

MARKET_CHANNELS = ("orderbook_delta", "trade")
ACCOUNT_CHANNELS = ("fill", "user_orders", "market_lifecycle_v2")

TERMINAL_WS_STATUSES = ("executed", "canceled", "cancelled")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class TrackedOrder:
    live_order_id: int
    kalshi_order_id: str
    client_order_id: str | None
    strategy: str | None
    market_ticker: str
    side: str            # "yes" | "no" — the side our order rests on
    price_cents: int     # our level in the YES price convention
    quantity: float | None
    created_at: datetime
    first_seen_at: datetime
    remaining: float | None = None
    terminal_at: datetime | None = None
    terminal_reason: str | None = None
    sampled_at_rest: bool = False
    sampled_terminal: bool = False


@dataclass
class TrackedMarket:
    ticker: str
    book: LocalBook
    trades: deque = field(default_factory=lambda: deque(maxlen=2000))  # (ts, yes_px, count, taker_outcome_side)
    subscribed: bool = False
    last_terminal_at: datetime | None = None
    seen_trade_ids: deque = field(default_factory=lambda: deque(maxlen=5000))


def our_level(side: str | None, limit_price: int | None) -> tuple[str, int] | None:
    """Where OUR order sits in the yes-price convention.

    `live_orders.limit_price` is the NO price for a `side="no"` maker buy (mmsell), which is a
    NO bid at yes price 100 - limit_price. A `side="yes"` order rests on the yes side at its
    own price. Anything else is unknown, and unknown is not tracked."""
    if limit_price is None:
        return None
    if side == "no":
        return "no", max(1, min(99, 100 - int(limit_price)))
    if side == "yes":
        return "yes", max(1, min(99, int(limit_price)))
    return None


class CollectorState:
    """Decides and writes. Never blocks on the network except inside `poll_queue`."""

    def __init__(self, client, settings, session_factory: Callable[[], Any],
                 *, clock: Callable[[], datetime] = _utcnow) -> None:
        self.client = client
        self.settings = settings
        self.session_factory = session_factory
        self.clock = clock
        self.orders: dict[str, TrackedOrder] = {}        # by kalshi_order_id
        self.markets: dict[str, TrackedMarket] = {}      # by ticker
        self.sid_to_channel: dict[int, str] = {}
        self.channel_sid: dict[str, int] = {}
        self.pending_cmds: dict[int, tuple[str, list[str]]] = {}   # id -> (channel, tickers)
        self.connection_id = 0
        self.connected = False
        self._next_cmd_id = 1
        self._last_interval_poll: datetime | None = None
        self._event_poll_due: datetime | None = None
        self._event_poll_trigger: str | None = None
        self._event_poll_times: deque = deque()
        self._rate_limited_until: datetime | None = None
        self._persist_times: deque = deque()
        self._throttled_dropped = 0
        self._throttled_reported_at: datetime | None = None
        self._seq: dict[int, int] = {}   # last seq seen per sid

    # -- helpers -----------------------------------------------------------------------------
    def _cmd_id(self) -> int:
        cid = self._next_cmd_id
        self._next_cmd_id += 1
        return cid

    def _record(self, kind: str, *, ticker: str | None = None, detail: str | None = None,
                detail_json: Any | None = None) -> None:
        try:
            with self.session_factory() as session:
                repo.insert_execution_collector_event(
                    session, kind=kind, market_ticker=ticker, connection_id=self.connection_id,
                    detail=detail, detail_json=detail_json, at=self.clock())
        except Exception:  # noqa: BLE001 — recording must never be what breaks the collector
            logger.exception("execution telemetry: could not record %s", kind)

    def _persist_allowed(self, now: datetime) -> bool:
        """Per-minute cap on raw event rows. Beyond it the local book still updates, rows are
        dropped, and the drop count is recorded once a minute — never silently."""
        cap = int(self.settings.execution_book_events_max_per_minute)
        cutoff = now - timedelta(seconds=60)
        while self._persist_times and self._persist_times[0] < cutoff:
            self._persist_times.popleft()
        if len(self._persist_times) >= cap:
            self._throttled_dropped += 1
            if (self._throttled_reported_at is None
                    or now - self._throttled_reported_at >= timedelta(seconds=60)):
                self._record(EV_THROTTLED, detail=f"dropped {self._throttled_dropped} raw events",
                             detail_json={"dropped": self._throttled_dropped, "cap_per_min": cap})
                self._throttled_reported_at = now
                self._throttled_dropped = 0
            return False
        self._persist_times.append(now)
        return True

    # -- connection lifecycle ---------------------------------------------------------------
    def on_connected(self) -> list[dict]:
        """Fresh connection: every subscription is gone. Rebuild them all, and mark every local
        book invalid until its snapshot arrives."""
        self.connection_id += 1
        self.connected = True
        self.sid_to_channel.clear()
        self.channel_sid.clear()
        self.pending_cmds.clear()
        self._seq.clear()
        for mk in self.markets.values():
            mk.book.valid = False
            mk.subscribed = False
        self._record(EV_CONNECTED, detail=f"connection {self.connection_id}")
        cmds: list[dict] = []
        for channel in ACCOUNT_CHANNELS:
            cid = self._cmd_id()
            self.pending_cmds[cid] = (channel, [])
            cmds.append({"id": cid, "cmd": "subscribe", "params": {"channels": [channel]}})
        tickers = sorted(self.markets)
        if tickers:
            cmds.extend(self._subscribe_markets(tickers))
        return cmds

    def on_disconnected(self, reason: str) -> None:
        if self.connected:
            self._record(EV_DISCONNECTED, detail=reason[:500])
        self.connected = False
        for mk in self.markets.values():
            mk.book.valid = False
            mk.subscribed = False

    def _subscribe_markets(self, tickers: list[str]) -> list[dict]:
        cmds = []
        for channel in MARKET_CHANNELS:
            cid = self._cmd_id()
            self.pending_cmds[cid] = (channel, list(tickers))
            params: dict[str, Any] = {"channels": [channel], "market_tickers": list(tickers)}
            if channel == "orderbook_delta":
                params["use_yes_price"] = True   # one price scale for both sides (§5)
            cmds.append({"id": cid, "cmd": "subscribe", "params": params})
        for t in tickers:
            if t in self.markets:
                self.markets[t].subscribed = True
        return cmds

    def _unsubscribe_markets(self, tickers: list[str]) -> list[dict]:
        cmds = []
        for channel in MARKET_CHANNELS:
            sid = self.channel_sid.get(channel)
            if sid is None:
                continue
            cid = self._cmd_id()
            cmds.append({"id": cid, "cmd": "update_subscription",
                         "params": {"sid": sid, "market_tickers": list(tickers),
                                    "action": "delete_markets"}})
        return cmds

    # -- tracked set -------------------------------------------------------------------------
    def refresh_tracked(self, now: datetime | None = None) -> list[dict]:
        """Re-read `live_orders`; subscribe new markets, retire post-window ones. Returns the
        commands to send (empty when nothing changed or not connected)."""
        now = now or self.clock()
        cmds: list[dict] = []
        try:
            with self.session_factory() as session:
                rows = repo.get_trackable_live_orders(session)
                snapshot = [(r.id, r.kalshi_order_id, r.client_order_id, r.strategy,
                             r.market_ticker, r.side, r.limit_price, r.quantity,
                             _aware(r.created_at)) for r in rows]
        except Exception as exc:  # noqa: BLE001
            self._record(EV_LOOP_ERROR, detail=f"refresh_tracked: {type(exc).__name__}: {exc}")
            return cmds

        seen: set[str] = set()
        new_tickers: list[str] = []
        for (oid, koid, coid, strategy, ticker, side, limit_price, qty, created) in snapshot:
            koid = str(koid)
            seen.add(koid)
            if koid in self.orders:
                continue
            level = our_level(side, limit_price)
            if level is None or not ticker:
                continue
            if ticker not in self.markets:
                if len(self.markets) >= int(self.settings.execution_telemetry_max_markets):
                    self._record(EV_THROTTLED, ticker=ticker,
                                 detail="market cap reached; order not tracked")
                    continue
                self.markets[ticker] = TrackedMarket(ticker=ticker, book=LocalBook(ticker))
                new_tickers.append(ticker)
            self.orders[koid] = TrackedOrder(
                live_order_id=int(oid), kalshi_order_id=koid, client_order_id=coid,
                strategy=strategy, market_ticker=ticker, side=level[0], price_cents=level[1],
                quantity=float(qty) if qty is not None else None,
                created_at=created or now, first_seen_at=now)
        # Orders the database now calls terminal (reconcile ran) that the stream never told us
        # about: mark terminal here so the post-window clock starts.
        for koid, od in list(self.orders.items()):
            if koid not in seen and od.terminal_at is None:
                self._mark_terminal(od, "db_terminal", now)
        # Retire orders + markets past the post-terminal window.
        window = timedelta(seconds=int(self.settings.execution_telemetry_post_window_seconds))
        for koid, od in list(self.orders.items()):
            if od.terminal_at is not None and now - od.terminal_at > window:
                del self.orders[koid]
        retire: list[str] = []
        for ticker, mk in list(self.markets.items()):
            if any(o.market_ticker == ticker for o in self.orders.values()):
                continue
            if mk.last_terminal_at is None or now - mk.last_terminal_at > window:
                retire.append(ticker)
        if self.connected:
            if new_tickers:
                cmds.extend(self._subscribe_markets(new_tickers))
            if retire:
                cmds.extend(self._unsubscribe_markets(retire))
        for ticker in retire:
            del self.markets[ticker]
            self._record(EV_UNSUBSCRIBED, ticker=ticker)
        return cmds

    def _mark_terminal(self, od: TrackedOrder, reason: str, now: datetime) -> None:
        od.terminal_at = now
        od.terminal_reason = reason
        mk = self.markets.get(od.market_ticker)
        if mk is not None:
            mk.last_terminal_at = now

    def active_orders(self) -> list[TrackedOrder]:
        return [o for o in self.orders.values() if o.terminal_at is None]

    # -- inbound frames ----------------------------------------------------------------------
    def handle_message(self, message: Any, received_at: datetime | None = None) -> list[dict]:
        received_at = received_at or self.clock()
        kind, msg, sid, seq, cid = envelope(message)
        try:
            if kind == "subscribed":
                return self._on_subscribed(msg, cid)
            if kind == "error":
                self._record(EV_WS_ERROR, detail=json.dumps(message)[:500])
                return []
            if kind in ("ok", "unsubscribed"):
                return []
            # Sequence check for EVERY sequenced frame on a known sid, BEFORE dispatch. A sid
            # carries several frame types (the lifecycle channel emits `event_lifecycle`,
            # `event_fee_update` and `market_metadata_updated` on the same counter), so a check
            # inside only the handlers we care about reads every ignored frame as a gap — which
            # is exactly what the first production hour recorded, eight times.
            cmds: list[dict] = []
            if sid is not None and seq is not None:
                if kind == "orderbook_snapshot":
                    self._seq[sid] = seq   # a snapshot is the baseline, never a gap
                else:
                    cmds = self._check_seq(self.sid_to_channel.get(sid, kind or ""), sid, seq,
                                           msg.get("market_ticker"))
            if kind == "orderbook_snapshot":
                return cmds + self._on_snapshot(msg, sid, seq, received_at, message)
            if kind == "orderbook_delta":
                return cmds + self._on_delta(msg, sid, seq, received_at, message)
            if kind == "trade":
                return cmds + self._on_trade(msg, sid, seq, received_at, message)
            if kind == "fill":
                return self._on_fill(msg, received_at, message)
            if kind == "user_order":
                return self._on_user_order(msg, received_at, message)
            if kind in ("market_lifecycle_v2", "market_lifecycle"):
                return cmds + self._on_lifecycle(msg, sid, seq, received_at, message)
            return cmds
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._record(EV_LOOP_ERROR, detail=f"handle {kind}: {type(exc).__name__}: {exc}",
                         detail_json={"frame": str(message)[:600]})
            return []

    def _on_subscribed(self, msg: dict, cid: int | None) -> list[dict]:
        channel = msg.get("channel")
        sid = int_or_none(msg.get("sid"))
        if isinstance(channel, str) and sid is not None:
            self.sid_to_channel[sid] = channel
            self.channel_sid[channel] = sid
        pending = self.pending_cmds.pop(cid, None) if cid is not None else None
        tickers = pending[1] if pending else []
        self._record(EV_SUBSCRIBED, detail=f"{channel} sid={sid} markets={len(tickers)}",
                     detail_json={"channel": channel, "sid": sid, "tickers": tickers[:50]})
        return []

    def _check_seq(self, channel: str, sid: int | None, seq: int | None,
                   ticker: str | None) -> list[dict]:
        """Per-sid sequence check. A gap on the book stream invalidates the local book and
        requests a snapshot for that market; on any stream it is recorded."""
        if sid is None or seq is None:
            return []
        last = self._seq.get(sid)
        self._seq[sid] = seq
        if last is not None and seq != last + 1:
            self._record(EV_SEQ_GAP, ticker=ticker,
                         detail=f"{channel} sid={sid} expected {last + 1} got {seq}",
                         detail_json={"channel": channel, "sid": sid, "expected": last + 1,
                                      "got": seq})
            if channel == "orderbook_delta":
                for m in self.markets.values():
                    m.book.valid = False
                tickers = sorted(self.markets)
                if tickers:
                    self._record(EV_SNAPSHOT_REQUESTED, detail=f"{len(tickers)} markets")
                    return [{"id": self._cmd_id(), "cmd": "update_subscription",
                             "params": {"sid": sid, "market_tickers": tickers,
                                        "action": "get_snapshot"}}]
        return []

    def _on_snapshot(self, msg, sid, seq, received_at, raw) -> list[dict]:
        ticker = msg.get("market_ticker")
        mk = self.markets.get(ticker)
        if mk is None:
            return []
        # A snapshot is a new baseline for this sid (whether or not seq resets — unverified).
        if sid is not None and seq is not None:
            self._seq[sid] = seq
        mk.book.apply_snapshot(msg, sid=sid, seq=seq, received_at=received_at)
        if self._persist_allowed(received_at):
            with self.session_factory() as session:
                repo.insert_execution_book_event(
                    session, market_ticker=ticker, kind="snapshot", sid=sid, seq=seq,
                    ts_ms=None, received_at=received_at, side=None, price_cents=None,
                    price_convention=PRICE_CONVENTION, delta_fp=None, level_qty_after=None,
                    ours=None, connection_id=self.connection_id, raw_json=raw)
        return []

    def _on_delta(self, msg, sid, seq, received_at, raw) -> list[dict]:
        ticker = msg.get("market_ticker")
        mk = self.markets.get(ticker)
        cmds: list[dict] = []
        if mk is None:
            return cmds
        side = msg.get("side")
        price = dollars_to_cents(msg.get("price_dollars"))
        delta = fp_to_float(msg.get("delta_fp"))
        ours = bool(msg.get("client_order_id"))
        after: float | None = None
        if side in ("yes", "no") and price is not None and delta is not None:
            was_valid = mk.book.valid
            after = mk.book.apply_delta(side, price, delta, received_at)
            if after is None and was_valid:
                self._record(EV_BOOK_INVALID, ticker=ticker,
                             detail=f"level {side}@{price} would go negative by {delta}")
        else:
            self._record(EV_UNPARSED, ticker=ticker, detail_json={"frame": str(raw)[:400]})
        if self._persist_allowed(received_at):
            with self.session_factory() as session:
                repo.insert_execution_book_event(
                    session, market_ticker=ticker, kind="delta", sid=sid, seq=seq,
                    ts_ms=int_or_none(msg.get("ts_ms")), received_at=received_at, side=side,
                    price_cents=price, price_convention=PRICE_CONVENTION, delta_fp=delta,
                    level_qty_after=after, ours=ours, connection_id=self.connection_id,
                    raw_json=raw)
        # A change at or better than our level, not caused by us, is worth a queue sample.
        if not ours and price is not None and side in ("yes", "no"):
            for od in self.active_orders():
                if od.market_ticker != ticker or od.side != side:
                    continue
                better = price < od.price_cents if side == "no" else price > od.price_cents
                if price == od.price_cents or better:
                    self.request_event_poll(TRIGGER_EVENT_DELTA, received_at)
                    break
        return cmds

    def _on_trade(self, msg, sid, seq, received_at, raw) -> list[dict]:
        ticker = msg.get("market_ticker")
        mk = self.markets.get(ticker)
        cmds: list[dict] = []
        if mk is None:
            return cmds
        trade_id = msg.get("trade_id")
        yes_px = dollars_to_cents(msg.get("yes_price_dollars"))
        no_px = dollars_to_cents(msg.get("no_price_dollars"))
        count = fp_to_float(msg.get("count_fp"))
        taker_out = msg.get("taker_outcome_side") or msg.get("taker_side")
        if trade_id is not None and trade_id in mk.seen_trade_ids:
            return cmds   # a resubscribe replayed it; the DB is idempotent and so is memory
        if trade_id is not None:
            mk.seen_trade_ids.append(trade_id)
        mk.trades.append((received_at, yes_px, count, taker_out, int_or_none(msg.get("ts_ms"))))
        if trade_id and self._persist_allowed(received_at):
            with self.session_factory() as session:
                repo.insert_execution_trade_event(
                    session, trade_id=str(trade_id), market_ticker=ticker,
                    ts_ms=int_or_none(msg.get("ts_ms")), received_at=received_at,
                    yes_price_cents=yes_px, no_price_cents=no_px, count_fp=count,
                    taker_outcome_side=taker_out, taker_book_side=msg.get("taker_book_side"),
                    is_block_trade=msg.get("is_block_trade"), sid=sid, seq=seq, raw_json=raw)
        if yes_px is not None:
            for od in self.active_orders():
                if od.market_ticker == ticker and yes_px == od.price_cents:
                    self.request_event_poll(TRIGGER_EVENT_TRADE, received_at)
                    break
        return cmds

    def _on_fill(self, msg, received_at, raw) -> list[dict]:
        koid = msg.get("order_id")
        trade_id = msg.get("trade_id")
        ticker = msg.get("market_ticker")
        if not trade_id or not ticker:
            self._record(EV_UNPARSED, detail_json={"frame": str(raw)[:400]})
            return []
        count = fp_to_float(msg.get("count_fp"))
        with self.session_factory() as session:
            repo.insert_execution_fill_event(
                session, trade_id=str(trade_id), kalshi_order_id=koid,
                client_order_id=msg.get("client_order_id"), market_ticker=ticker,
                ts_ms=int_or_none(msg.get("ts_ms")), received_at=received_at,
                yes_price_cents=dollars_to_cents(msg.get("yes_price_dollars")), count_fp=count,
                fee_cost=fp_to_float(msg.get("fee_cost")), is_taker=msg.get("is_taker"),
                outcome_side=msg.get("outcome_side") or msg.get("side"),
                book_side=msg.get("book_side"),
                post_position_fp=fp_to_float(msg.get("post_position_fp")),
                exchange_index=int_or_none(msg.get("exchange_index")), raw_json=raw)
        od = self.orders.get(str(koid)) if koid else None
        if od is not None and od.terminal_at is None:
            if od.remaining is not None and count is not None:
                od.remaining = max(0.0, od.remaining - count)
            elif od.quantity is not None and count is not None and od.remaining is None:
                od.remaining = max(0.0, od.quantity - count)
            # A partial fill keeps the order tracked; only a zero remainder ends resting.
            if od.remaining is not None and od.remaining <= 1e-9:
                self._mark_terminal(od, "ws_filled", received_at)
                self.request_event_poll(TRIGGER_TERMINAL, received_at, force=True)
            else:
                self.request_event_poll(TRIGGER_EVENT_FILL, received_at)
        return []

    def _on_user_order(self, msg, received_at, raw) -> list[dict]:
        koid = msg.get("order_id")
        if not koid:
            return []
        remaining = fp_to_float(msg.get("remaining_count_fp"))
        status = msg.get("status")
        with self.session_factory() as session:
            repo.insert_execution_order_event(
                session, kalshi_order_id=str(koid), client_order_id=msg.get("client_order_id"),
                market_ticker=msg.get("ticker") or msg.get("market_ticker"), status=status,
                fill_count_fp=fp_to_float(msg.get("fill_count_fp")),
                remaining_count_fp=remaining,
                initial_count_fp=fp_to_float(msg.get("initial_count_fp")),
                maker_fill_cost_dollars=fp_to_float(msg.get("maker_fill_cost_dollars")),
                maker_fees_dollars=fp_to_float(msg.get("maker_fees_dollars")),
                last_updated_ts_ms=int_or_none(msg.get("last_updated_ts_ms")),
                received_at=received_at, raw_json=raw)
        od = self.orders.get(str(koid))
        if od is not None:
            if remaining is not None:
                od.remaining = remaining
            if od.terminal_at is None and isinstance(status, str) \
                    and status.lower() in TERMINAL_WS_STATUSES:
                self._mark_terminal(od, f"ws_{status.lower()}", received_at)
                self.request_event_poll(TRIGGER_TERMINAL, received_at, force=True)
        return []

    def _on_lifecycle(self, msg, sid, seq, received_at, raw) -> list[dict]:
        ticker = msg.get("market_ticker")
        cmds: list[dict] = []
        if ticker not in self.markets:
            return cmds
        ts = None
        for key in ("settled_ts", "determination_ts", "close_ts", "open_ts"):
            if msg.get(key) is not None:
                ts = int_or_none(msg.get(key))
                break
        with self.session_factory() as session:
            repo.insert_execution_market_event(
                session, market_ticker=ticker, event_type=msg.get("event_type"),
                is_deactivated=msg.get("is_deactivated"), event_ts=ts,
                received_at=received_at, sid=sid, seq=seq, raw_json=raw)
        return cmds

    # -- queue polling -----------------------------------------------------------------------
    def request_event_poll(self, trigger: str, now: datetime, *, force: bool = False) -> None:
        """Ask for an event-triggered poll. Debounced; a terminal request is never coalesced
        away (force) but still obeys the per-minute cap."""
        due = now + timedelta(seconds=float(self.settings.execution_queue_event_debounce_seconds))
        if self._event_poll_due is None or force:
            self._event_poll_due = due if not force else now
            self._event_poll_trigger = trigger
        # keep the earliest-due request; a later trigger does not push it out

    def _event_budget_ok(self, now: datetime) -> bool:
        cap = int(self.settings.execution_queue_max_polls_per_minute)
        cutoff = now - timedelta(seconds=60)
        while self._event_poll_times and self._event_poll_times[0] < cutoff:
            self._event_poll_times.popleft()
        return len(self._event_poll_times) < cap

    def run_due_polls(self, now: datetime | None = None) -> str | None:
        """Run at most ONE queue poll if one is due. Returns its trigger, or None."""
        now = now or self.clock()
        if self._rate_limited_until is not None and now < self._rate_limited_until:
            return None
        active = self.active_orders()
        # First sighting of a resting order → sample at rest, regardless of the interval.
        if any(not o.sampled_at_rest for o in active):
            self.poll_queue(TRIGGER_AT_REST, now)
            return TRIGGER_AT_REST
        if self._event_poll_due is not None and now >= self._event_poll_due:
            trigger = self._event_poll_trigger or TRIGGER_EVENT_DELTA
            self._event_poll_due, self._event_poll_trigger = None, None
            if self._event_budget_ok(now):
                self._event_poll_times.append(now)
                self.poll_queue(trigger, now)
                return trigger
            self._record(EV_THROTTLED, detail=f"event poll {trigger} skipped: per-minute cap")
            return None
        interval = float(self.settings.execution_queue_poll_seconds)
        if active and (self._last_interval_poll is None
                       or (now - self._last_interval_poll).total_seconds() >= interval):
            self.poll_queue(TRIGGER_INTERVAL, now)
            return TRIGGER_INTERVAL
        return None

    def poll_queue(self, trigger: str, now: datetime | None = None) -> int:
        """One batch GET for every active order; a tick per order, NULL when unreadable.
        Terminal orders not yet sampled at terminal get one last attempt here too."""
        now = now or self.clock()
        targets = [o for o in self.orders.values()
                   if o.terminal_at is None or not o.sampled_terminal]
        if not targets:
            return 0
        tickers = sorted({o.market_ticker for o in targets})
        payload: Any = None
        failure: str | None = None
        try:
            payload = self.client.get_queue_positions(market_tickers=",".join(tickers))
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            failure = f"{type(exc).__name__}: {str(exc)[:200]}"
            if "429" in str(exc):
                self._rate_limited_until = now + timedelta(seconds=10)
                self._record(EV_RATE_LIMITED, detail=failure)
            else:
                self._record(EV_POLL_FAILED, detail=failure, detail_json={"trigger": trigger})
        samples, unreadable = parse_batch(payload) if payload is not None else ({}, [])
        # The VERBATIM row per order, so the stored raw is Kalshi's payload and not our parse.
        raw_by_id: dict[str, Any] = {}
        rows = payload.get("queue_positions") if isinstance(payload, dict) else None
        for row in rows if isinstance(rows, list) else []:
            oid = order_id_of(row)
            if oid is not None:
                raw_by_id[str(oid)] = row
        if unreadable:
            self._record(EV_UNPARSED, detail=f"{len(unreadable)} queue rows unreadable",
                         detail_json={"sample": str(unreadable[:2])[:600]})
        failed_by_id = {oid: r for r in unreadable if (oid := order_id_of(r)) is not None}
        self._last_interval_poll = now   # any sample restarts the interval clock
        written = 0
        missing: list[str] = []
        with self.session_factory() as session:
            for od in targets:
                sample = samples.get(od.kalshi_order_id)
                raw = raw_by_id.get(od.kalshi_order_id) or failed_by_id.get(od.kalshi_order_id)
                if sample is None and failure is None and od.terminal_at is None:
                    missing.append(od.kalshi_order_id)
                tick_trigger = TRIGGER_TERMINAL if od.terminal_at is not None else trigger
                if od.terminal_at is None and not od.sampled_at_rest:
                    tick_trigger = TRIGGER_AT_REST
                repo.insert_queue_tick(
                    session, live_order_id=od.live_order_id, kalshi_order_id=od.kalshi_order_id,
                    strategy=od.strategy, ticker=od.market_ticker,
                    queue_position=(sample or {}).get("queue_position"),
                    contracts_ahead=(sample or {}).get("contracts_ahead"),
                    limit_price=(100 - od.price_cents) if od.side == "no" else od.price_cents,
                    rest_seconds=int((now - od.created_at).total_seconds()),
                    raw_json=raw if raw is not None else ({"error": failure} if failure else None),
                    trigger=tick_trigger, source="rest_batch", remaining_count=od.remaining,
                    features_json=self._features(od, now), captured_at=now)
                written += 1
                if od.terminal_at is None:
                    od.sampled_at_rest = True
                else:
                    od.sampled_terminal = True
        if missing:
            self._record(EV_POLL_MISSING, detail=f"{len(missing)} active orders absent from batch",
                         detail_json={"order_ids": missing[:50], "trigger": trigger})
        return written

    def _features(self, od: TrackedOrder, now: datetime) -> dict:
        mk = self.markets.get(od.market_ticker)
        if mk is None:
            return {"book_valid": False}
        feats = mk.book.features_for(od.side, od.price_cents)
        feats["book_age_s"] = (
            (now - mk.book.last_update_at).total_seconds() if mk.book.last_update_at else None)
        since = od.created_at
        recent = [t for t in mk.trades if t[0] >= since]
        last60 = [t for t in recent if (now - t[0]).total_seconds() <= 60]
        at_price = [t for t in recent if t[1] == od.price_cents]
        # For a NO bid (side "no") the taker who hits us bought YES at our yes price.
        hitting_side = "yes" if od.side == "no" else "no"
        hits = [t for t in at_price if t[3] == hitting_side]
        feats.update({
            "trades_since_placement": len(recent),
            "volume_since_placement": float(sum((t[2] or 0.0) for t in recent)),
            "volume_at_our_price": float(sum((t[2] or 0.0) for t in at_price)),
            "volume_hitting_our_side_at_price": float(sum((t[2] or 0.0) for t in hits)),
            "trades_last_60s": len(last60),
            "volume_last_60s": float(sum((t[2] or 0.0) for t in last60)),
            "seconds_since_last_trade": (
                (now - recent[-1][0]).total_seconds() if recent else None),
            "rest_seconds": (now - od.created_at).total_seconds(),
        })
        return feats


class TelemetryThread:
    """Daemon thread: connect, pump, poll, back off. Never raises into the caller."""

    def __init__(self, state: CollectorState, *, connect: Callable[..., Any] | None = None,
                 recv_timeout: float = 1.0, max_backoff: float = 60.0) -> None:
        self.state = state
        self._connect = connect
        self.recv_timeout = recv_timeout
        self.max_backoff = max_backoff
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._connect is None:
            try:
                from websockets.sync.client import connect as ws_connect
            except Exception as exc:  # noqa: BLE001 — a missing dependency disables, loudly
                self.state._record(EV_DISABLED, detail=f"websockets unavailable: {exc}")
                logger.warning("execution telemetry disabled: websockets unavailable: %s", exc)
                return False
            self._connect = ws_connect
        self._thread = threading.Thread(target=self._run, name="execution-telemetry", daemon=True)
        self._thread.start()
        self.state._record(EV_THREAD_STARTED)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self.state._record(EV_THREAD_STOPPED)

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._session()
                backoff = 1.0
            except AuthError as exc:
                # Credentials are wrong: the worker will fail closed on its own path; here we
                # stop rather than hammer the socket.
                self.state._record(EV_LOOP_ERROR, detail=f"auth: {exc}")
                return
            except Exception as exc:  # noqa: BLE001
                self.state.on_disconnected(f"{type(exc).__name__}: {str(exc)[:300]}")
                logger.warning("execution telemetry session ended: %s: %s",
                               type(exc).__name__, str(exc)[:300])
            if self._stop.wait(backoff):
                break
            backoff = min(self.max_backoff, backoff * 2)

    def _session(self) -> None:
        client = self.state.client
        with self._connect(client.ws_url, additional_headers=client.ws_headers(),
                           open_timeout=15) as conn:
            self._send_all(conn, self.state.on_connected())
            self._send_all(conn, self.state.refresh_tracked())
            self.state.run_due_polls()
            last_scan = time.monotonic()
            scan_every = float(self.state.settings.execution_telemetry_scan_seconds)
            while not self._stop.is_set():
                frame = None
                try:
                    frame = conn.recv(timeout=self.recv_timeout)
                except TimeoutError:
                    pass
                if frame is not None:
                    try:
                        message = json.loads(frame)
                    except (TypeError, ValueError):
                        message = None
                    if message is not None:
                        self._send_all(conn, self.state.handle_message(message))
                if time.monotonic() - last_scan >= scan_every:
                    self._send_all(conn, self.state.refresh_tracked())
                    last_scan = time.monotonic()
                self.state.run_due_polls()
        self.state.on_disconnected("closed")

    @staticmethod
    def _send_all(conn, cmds: list[dict]) -> None:
        for cmd in cmds:
            conn.send(json.dumps(cmd))


def start_collector(client, settings) -> TelemetryThread | None:
    """Build and start the collector for the live worker. Returns None when disabled."""
    if not getattr(settings, "execution_telemetry_enabled", False):
        return None
    from .. import db
    from .readonly import ReadOnlyKalshi

    state = CollectorState(ReadOnlyKalshi(client, settings), settings, db.session_scope)
    thread = TelemetryThread(state)
    return thread if thread.start() else None
