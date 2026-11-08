"""The shadow incentive market maker: state machine + daemon thread. NO ORDERS.

Same two-layer construction as the execution telemetry collector (`execution/collector.py`,
WS-019), because it earned its shape in production:

* `ShadowState` — decides and writes. Fed WebSocket frames and clock ticks, returns the
  outbound subscribe/unsubscribe commands. No socket, no sleep, no thread: fully testable
  with SQLite and a fake read-only client.
* `ShadowThread` — connect with the signed upgrade headers, pump frames, tick the clock,
  back off on failure. Every loop body is wrapped; nothing here can raise into the worker.

What it does, per market with an active liquidity program (Phase 0B–0E of the handoff):

  1. keeps a local order book (`execution.book.LocalBook`, both sides on the YES scale) from
     `orderbook_delta`, and the public trade tape from `trade`; persists both raw;
  2. every `requote_seconds` writes a market snapshot with the scoring model's field read
     (reference price, qualifying depth, does each side meet Target Size);
  3. maintains one hypothetical quote PAIR per (policy, capital tier). A pair is placed at
     prices we would genuinely accept a fill at, and ends ONLY for a legitimate reason:
     the market moved (`move_ticks`), the program ended or vanished, the market closed or
     was deactivated, the book went invalid, the collector stopped, both legs filled under
     every model, or the pair reached `max_rest_seconds` (a refresh, recorded as such);
  4. replays every trade against every resting leg under the three fill models, records
     fills, schedules mark-to-market at 1 s / 5 s / 30 s / 60 s / 5 min after the first
     fill, accrues the scoring model's reward estimate while the pair rests;
  5. writes one outcome row per (pair, fill model) when the pair ends, and stamps
     settlement on single-leg outcomes when the market resolves.

Rules the code enforces rather than documents:
  - the state holds an `IncentiveReadOnlyKalshi` (GET only) — it cannot place anything;
  - it writes ONLY the `incentive_*` tables; it never reads or writes a trading table;
  - a sequence gap invalidates the local book and requests a fresh snapshot;
  - raw persistence is capped per minute and the drop count is itself recorded.
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

from .. import models as m
from .. import repository as repo
from ..execution.book import PRICE_CONVENTION, LocalBook
from ..execution.parse import dollars_to_cents, envelope, fp_to_float, int_or_none
from ..kalshi.errors import AuthError
from . import economics as econ
from . import fills as fm
from . import live as live_tags
from . import paper_arms as pa
from . import programs as pg
from . import quotes as qp
from . import scoring as sc
from . import store
from .fees import FeeRule, pair_maker_fee_cents

logger = logging.getLogger(__name__)

MARKET_CHANNELS = ("orderbook_delta", "trade")
GLOBAL_CHANNELS = ("market_lifecycle_v2",)

MARK_HORIZONS_SECONDS = (1, 5, 30, 60, 300)
#: The paper arms add a 1 h and a 24 h markout (handoff §5 step 2); settlement is stamped too.
ARM_MARK_HORIZONS_SECONDS = MARK_HORIZONS_SECONDS + (3600, 86400)

# collector-event kinds (incentive_collector_events.kind)
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
EV_THROTTLED = "throttled"
EV_LOOP_ERROR = "loop_error"
EV_UNPARSED = "unparsed"
EV_DISCOVERY = "discovery"
EV_MARKET_CAP = "market_cap_reached"
EV_SETTLED = "settled"

# quote end reasons (incentive_shadow_quotes.end_reason)
END_MARKET_MOVED = "cancel_market_moved"
END_PROGRAM_ENDED = "cancel_program_ended"
END_PROGRAM_GONE = "cancel_program_disappeared"
END_MARKET_CLOSED = "market_closed"
END_MARKET_DEACTIVATED = "cancel_market_deactivated"
END_BOOK_INVALID = "cancel_book_invalid"
END_COLLECTOR_STOP = "collector_stop"
END_FILLED_ALL = "filled_all_models"
END_REFRESH = "refresh_max_rest"

#: shadow-event kind written once per paper-arm quote when it ends (always, not gated by
#: `persist_shadow_events`): the hour's share, qualification and thin-rule diagnostics.
EV_ARM_HOUR = "arm_hour"
EV_ARM_SELECT = "arm_select"          # incentive_collector_events: the hourly arm selection
#: How often the arms re-select markets, and how stale a market's candidate may be.
ARM_SELECT_SECONDS = 3600.0
ARM_FIRST_SELECT_AFTER_SECONDS = 300.0
ARM_CANDIDATE_MAX_AGE_SECONDS = 900.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class ProgramTerms:
    """The current terms row, copied out of the ORM so the state never holds a session."""

    row_id: int
    market_ticker: str
    target_size: float | None
    discount_factor_bps: int | None
    period_reward_usd: float | None
    period_seconds: float | None
    start_date: datetime | None
    end_date: datetime | None
    close_time: datetime | None
    fee_rule: FeeRule
    event_ticker: str | None = None
    series_ticker: str | None = None

    @classmethod
    def from_row(cls, row) -> ProgramTerms:
        start, end = _aware(row.start_date), _aware(row.end_date)
        period = (end - start).total_seconds() if start and end and end > start else None
        fr = row.fee_rule_json or {}
        rule = FeeRule(maker_rate=float(fr.get("maker_rate", 0.0)),
                       taker_rate=float(fr.get("taker_rate", 0.07)),
                       source=str(fr.get("source", "default_schedule")),
                       fee_type=fr.get("fee_type"), detail=fr.get("detail"))
        return cls(row_id=int(row.id), market_ticker=row.market_ticker,
                   target_size=float(row.target_size) if row.target_size is not None else None,
                   discount_factor_bps=row.discount_factor_bps,
                   period_reward_usd=float(row.period_reward_usd) if row.period_reward_usd is not None else None,
                   period_seconds=period, start_date=start, end_date=end,
                   close_time=_aware(row.close_time), fee_rule=rule,
                   event_ticker=getattr(row, "event_ticker", None),
                   series_ticker=getattr(row, "series_ticker", None))


def daily_pay_usd(terms: ProgramTerms) -> float | None:
    """The programme's pool per day: period reward ÷ programme length in days (A2: the
    reward accrues uniformly over [start, end]). None when the length is unknown."""
    if terms.period_reward_usd is None or not terms.period_seconds or terms.period_seconds <= 0:
        return None
    return float(terms.period_reward_usd) / (terms.period_seconds / 86_400.0)


def daily_pay_sort_key(terms: ProgramTerms) -> tuple[int, float]:
    """Markets with a known daily pay rank first, by that pay; a programme with no usable
    dates ranks after every dated one, by its total reward (the old key)."""
    rate = daily_pay_usd(terms)
    if rate is None:
        return (0, float(terms.period_reward_usd or 0.0))
    return (1, rate)


@dataclass
class LivePair:
    quote_id: int
    policy: str
    tier: int
    yes_bid: int
    no_bid: int
    qty: float
    placed_at: datetime
    capital_required: float
    yes_leg: fm.ShadowLeg
    no_leg: fm.ShadowLeg
    reward_yes_usd: float = 0.0        # accrued while the yes leg rests (optimistic: unfilled)
    reward_no_usd: float = 0.0
    last_accrual_at: datetime | None = None
    # (model, side) -> list of (horizon, bid_cents) marks taken so far
    marks: dict[tuple[str, str], list[tuple[int, int | None]]] = field(default_factory=dict)
    marks_scheduled: set[tuple[str, str]] = field(default_factory=set)
    # --- one-sided paper arms only (None for the two-sided policy pairs) ---
    arm: str | None = None
    side: str | None = None              # the side the arm's bid rests on
    programme_start: datetime | None = None
    seconds_scored: float = 0.0
    seconds_qualifying: float = 0.0
    seconds_thin: float = 0.0
    share_seconds: float = 0.0           # share x seconds, for the hour's mean share
    max_share: float = 0.0
    reward_uncredited_usd: float = 0.0   # thin hours: estimated but not credited (study 3)


@dataclass
class PendingMark:
    quote_id: int
    ticker: str
    model: str
    side: str
    horizon: int
    due_at: datetime
    fill_price: int
    filled_qty: float


@dataclass
class PendingSettlement:
    quote_id: int
    ticker: str
    side: str
    qty_by_model: dict[str, float]
    price_cents: int


@dataclass
class TrackedMarket:
    ticker: str
    terms: ProgramTerms
    book: LocalBook
    trades: deque = field(default_factory=lambda: deque(maxlen=5000))  # (at, yes_px, count, taker_side)
    seen_trade_ids: deque = field(default_factory=lambda: deque(maxlen=10000))
    pairs: dict[tuple[str, int], LivePair] = field(default_factory=dict)
    subscribed: bool = False
    last_requote_at: datetime | None = None
    invalid_since: datetime | None = None
    status: str | None = None


class ShadowState:
    """Decides and writes. Blocks on the network only inside `refresh_programs` and
    `settle_pending` (REST GETs)."""

    def __init__(self, client, settings, session_factory: Callable[[], Any],
                 *, clock: Callable[[], datetime] = _utcnow) -> None:
        self.client = client
        self.settings = settings
        self.session_factory = session_factory
        self.clock = clock
        self.markets: dict[str, TrackedMarket] = {}
        self.sid_to_channel: dict[int, str] = {}
        self.channel_sid: dict[str, int] = {}
        self.pending_cmds: dict[int, tuple[str, list[str]]] = {}
        self.connection_id = 0
        self.connected = False
        self._next_cmd_id = 1
        self._seq: dict[int, int] = {}
        self._persist_times: deque = deque()
        self._throttled_dropped = 0
        self._throttled_reported_at: datetime | None = None
        self.pending_marks: list[PendingMark] = []
        self.pending_settlements: dict[int, PendingSettlement] = {}
        self._last_discovery_at: datetime | None = None
        self._last_settlement_at: datetime | None = None
        self.tiers: tuple[int, ...] = self._tiers()
        self.arms = pa.ArmSelector()
        self.arm_candidates: dict[str, pa.Candidate] = {}
        self._started_at: datetime | None = None
        self._last_arm_select_at: datetime | None = None

    # -- config --------------------------------------------------------------------------
    def _tiers(self) -> tuple[int, ...]:
        raw = str(getattr(self.settings, "liquidity_incentive_capital_tiers", "") or "")
        out: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit() and int(part) > 0:
                out.append(int(part))
        return tuple(out) or qp.CAPITAL_TIERS_USD

    def _cfg(self, name: str, default):
        return getattr(self.settings, f"liquidity_incentive_{name}", default)

    def _arms_on(self) -> bool:
        return bool(self._cfg("paper_arms_enabled", False))

    # -- helpers -------------------------------------------------------------------------
    def _cmd_id(self) -> int:
        cid = self._next_cmd_id
        self._next_cmd_id += 1
        return cid

    def _record(self, kind: str, *, ticker: str | None = None, detail: str | None = None,
                detail_json: Any | None = None) -> None:
        try:
            with self.session_factory() as session:
                store.record_event(session, kind=kind, at=self.clock(), ticker=ticker,
                                   connection_id=self.connection_id, detail=detail,
                                   detail_json=detail_json)
        except Exception:  # noqa: BLE001 — recording must never break the collector
            logger.exception("incentive shadow: could not record %s", kind)

    def _persist_allowed(self, now: datetime) -> bool:
        cap = int(self._cfg("book_events_max_per_minute", 0))
        if cap <= 0:
            return False            # tape persistence off: not a throttle, so nothing to report
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

    # -- connection lifecycle -------------------------------------------------------------
    def on_connected(self) -> list[dict]:
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
        for channel in GLOBAL_CHANNELS:
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
                params["use_yes_price"] = True
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
            cmds.append({"id": self._cmd_id(), "cmd": "update_subscription",
                         "params": {"sid": sid, "market_tickers": list(tickers),
                                    "action": "delete_markets"}})
        return cmds

    # -- program discovery -> tracked set -------------------------------------------------
    def refresh_programs(self, now: datetime | None = None, *, force: bool = False) -> list[dict]:
        """Run discovery when due, then reconcile the tracked set. Returns WS commands."""
        now = now or self.clock()
        every = float(self._cfg("discovery_seconds", 300.0))
        if not force and self._last_discovery_at is not None and (now - self._last_discovery_at).total_seconds() < every:
            return []
        self._last_discovery_at = now
        try:
            with self.session_factory() as session:
                result = pg.run_discovery(self.client, session, now=now)
                current = [pg.current_programs(session, now=now)]
                terms = {r.market_ticker: ProgramTerms.from_row(r) for r in current[0]}
                # The markets the live book is actually trading. The shadow ranks by daily
                # pay and the live runner by soonest programme end, so the two sets were
                # disjoint: `incentive_shadow_outcomes` held ZERO rows for every ticker the live
                # book had ever quoted, leaving no `est_reward` to compare a payout against
                # (thesis §9.13). Pinning them costs at most a handful of extra subscriptions.
                pinned = repo.live_book_open_tickers(session, live_tags.LIVE_TAG)
                summary = {"listed": len(result.current), "new": result.new_terms,
                           "changed": result.changed_terms, "gone": result.disappeared,
                           "errors": result.errors, "liquidity_current": len(terms),
                           "pinned_live": len(pinned)}
        except Exception as exc:  # noqa: BLE001
            self._record(EV_LOOP_ERROR, detail=f"refresh_programs: {type(exc).__name__}: {exc}")
            return []
        self._record(EV_DISCOVERY, detail_json=summary)
        return self._reconcile(terms, now, pinned=pinned)

    def _reconcile(self, terms: dict[str, ProgramTerms], now: datetime,
                   *, pinned: set[str] | None = None) -> list[dict]:
        min_reward = float(self._cfg("min_reward_usd", 0.0))
        cap = int(self._cfg("max_markets", 150))
        wanted = [t for t in terms.values()
                  if (t.period_reward_usd or 0.0) >= min_reward and t.target_size]
        # Rank by DAILY pay, not total programme reward (DEC-043.2). Total reward favours
        # long-dated ladders and left ~$56k/day of short weekly pools unwatched (ranker
        # cycle 7, docs/LIMM_MARKET_RANKER.md); pool per day is what a resting bid earns from.
        wanted.sort(key=daily_pay_sort_key, reverse=True)
        if len(wanted) > cap:
            self._record(EV_MARKET_CAP, detail=f"{len(wanted)} eligible, tracking {cap}")
            wanted = wanted[:cap]
        wanted_by_ticker = {t.market_ticker: t for t in wanted}
        # Pinned markets survive the cap. The cap exists to bound WS volume, not to decide what
        # is worth measuring, and the one thing always worth measuring is what we are trading.
        for ticker in sorted(pinned or ()):
            if ticker not in wanted_by_ticker and ticker in terms:
                wanted_by_ticker[ticker] = terms[ticker]
        cmds: list[dict] = []
        new_tickers: list[str] = []
        for ticker, t in wanted_by_ticker.items():
            mk = self.markets.get(ticker)
            if mk is None:
                self.markets[ticker] = TrackedMarket(ticker=ticker, terms=t, book=LocalBook(ticker))
                new_tickers.append(ticker)
            elif mk.terms.row_id != t.row_id:
                mk.terms = t          # terms changed: quotes re-evaluate on the next tick
        retire: list[str] = []
        for ticker, mk in list(self.markets.items()):
            if ticker not in wanted_by_ticker:
                reason = END_PROGRAM_ENDED if (mk.terms.end_date and mk.terms.end_date <= now) else END_PROGRAM_GONE
                self._end_all_pairs(mk, reason, now)
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

    # -- inbound frames --------------------------------------------------------------------
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
            cmds: list[dict] = []
            if sid is not None and seq is not None:
                if kind == "orderbook_snapshot":
                    self._seq[sid] = seq
                else:
                    cmds = self._check_seq(self.sid_to_channel.get(sid, kind or ""), sid, seq,
                                           msg.get("market_ticker"))
            if kind == "orderbook_snapshot":
                return cmds + self._on_snapshot(msg, sid, seq, received_at, message)
            if kind == "orderbook_delta":
                return cmds + self._on_delta(msg, sid, seq, received_at, message)
            if kind == "trade":
                return cmds + self._on_trade(msg, sid, seq, received_at, message)
            if kind in ("market_lifecycle_v2", "market_lifecycle"):
                return cmds + self._on_lifecycle(msg, received_at, message)
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

    def _check_seq(self, channel: str, sid: int | None, seq: int | None, ticker: str | None) -> list[dict]:
        if sid is None or seq is None:
            return []
        last = self._seq.get(sid)
        self._seq[sid] = seq
        if last is not None and seq != last + 1:
            self._record(EV_SEQ_GAP, ticker=ticker,
                         detail=f"{channel} sid={sid} expected {last + 1} got {seq}",
                         detail_json={"channel": channel, "sid": sid, "expected": last + 1, "got": seq})
            if channel == "orderbook_delta":
                for mk in self.markets.values():
                    mk.book.valid = False
                tickers = sorted(self.markets)
                if tickers:
                    self._record(EV_SNAPSHOT_REQUESTED, detail=f"{len(tickers)} markets")
                    return [{"id": self._cmd_id(), "cmd": "update_subscription",
                             "params": {"sid": sid, "market_tickers": tickers, "action": "get_snapshot"}}]
        return []

    def _on_snapshot(self, msg, sid, seq, received_at, raw) -> list[dict]:
        ticker = msg.get("market_ticker")
        mk = self.markets.get(ticker)
        if mk is None:
            return []
        if sid is not None and seq is not None:
            self._seq[sid] = seq
        mk.book.apply_snapshot(msg, sid=sid, seq=seq, received_at=received_at)
        mk.invalid_since = None
        if self._persist_allowed(received_at):
            with self.session_factory() as session:
                store.insert_book_event(
                    session, market_ticker=ticker, kind="snapshot", sid=sid, seq=seq, ts_ms=None,
                    received_at=received_at, side=None, price_cents=None,
                    price_convention=PRICE_CONVENTION, delta_fp=None, level_qty_after=None,
                    connection_id=self.connection_id, raw_json=raw)
        return []

    def _on_delta(self, msg, sid, seq, received_at, raw) -> list[dict]:
        ticker = msg.get("market_ticker")
        mk = self.markets.get(ticker)
        if mk is None:
            return []
        side = msg.get("side")
        price = dollars_to_cents(msg.get("price_dollars"))
        delta = fp_to_float(msg.get("delta_fp"))
        after: float | None = None
        if side in ("yes", "no") and price is not None and delta is not None:
            was_valid = mk.book.valid
            after = mk.book.apply_delta(side, price, delta, received_at)
            if after is None and was_valid:
                mk.invalid_since = received_at
                self._record(EV_BOOK_INVALID, ticker=ticker,
                             detail=f"level {side}@{price} would go negative by {delta}")
        else:
            self._record(EV_UNPARSED, ticker=ticker, detail_json={"frame": str(raw)[:400]})
        if self._persist_allowed(received_at):
            with self.session_factory() as session:
                store.insert_book_event(
                    session, market_ticker=ticker, kind="delta", sid=sid, seq=seq,
                    ts_ms=int_or_none(msg.get("ts_ms")), received_at=received_at, side=side,
                    price_cents=price, price_convention=PRICE_CONVENTION, delta_fp=delta,
                    level_qty_after=after, connection_id=self.connection_id, raw_json=raw)
        # Queue-aware model: the resting quantity at one of our levels changed.
        if after is not None and side in ("yes", "no"):
            for pair in mk.pairs.values():
                leg = pair.yes_leg if side == "yes" else pair.no_leg
                if leg.price_yes_scale == price:
                    leg.on_level_quantity(after)
        return []

    def _on_trade(self, msg, sid, seq, received_at, raw) -> list[dict]:
        ticker = msg.get("market_ticker")
        mk = self.markets.get(ticker)
        if mk is None:
            return []
        trade_id = msg.get("trade_id")
        if trade_id is not None and trade_id in mk.seen_trade_ids:
            return []
        if trade_id is not None:
            mk.seen_trade_ids.append(trade_id)
        yes_px = dollars_to_cents(msg.get("yes_price_dollars"))
        no_px = dollars_to_cents(msg.get("no_price_dollars"))
        count = fp_to_float(msg.get("count_fp"))
        taker_out = msg.get("taker_outcome_side") or msg.get("taker_side")
        mk.trades.append((received_at, yes_px, count, taker_out))
        if trade_id:
            with self.session_factory() as session:
                store.insert_trade_event(
                    session, trade_id=str(trade_id), market_ticker=ticker,
                    ts_ms=int_or_none(msg.get("ts_ms")), received_at=received_at,
                    yes_price_cents=yes_px, no_price_cents=no_px, count_fp=count,
                    taker_outcome_side=taker_out, taker_book_side=msg.get("taker_book_side"),
                    is_block_trade=msg.get("is_block_trade"), sid=sid, seq=seq, raw_json=raw)
        self._replay_trade(mk, yes_px=yes_px, count=count, taker_out=taker_out,
                           trade_id=str(trade_id) if trade_id else None, at=received_at)
        return []

    def _on_lifecycle(self, msg, received_at, raw) -> list[dict]:
        ticker = msg.get("market_ticker")
        mk = self.markets.get(ticker)
        event_type = msg.get("event_type")
        result = msg.get("result")
        if mk is not None:
            mk.status = event_type
            if event_type == "deactivated" or msg.get("is_deactivated") is True:
                self._end_all_pairs(mk, END_MARKET_DEACTIVATED, received_at)
            elif event_type in ("determined", "settled"):
                self._end_all_pairs(mk, END_MARKET_CLOSED, received_at)
        if event_type in ("determined", "settled") and result in ("yes", "no") and ticker:
            self._settle_ticker(ticker, result, received_at)
        return []

    # -- the shadow book -------------------------------------------------------------------
    def _replay_trade(self, mk: TrackedMarket, *, yes_px: int | None, count: float | None,
                      taker_out: str | None, trade_id: str | None, at: datetime) -> None:
        if yes_px is None or not count:
            return
        for pair in list(mk.pairs.values()):
            for leg in (pair.yes_leg, pair.no_leg):
                if not leg.trade_hits_us(yes_px, taker_out):
                    continue
                before = {model: leg.queue_ahead(model) for model in leg.models}
                newly = leg.on_trade(yes_price_cents=yes_px, count=count,
                                     taker_outcome_side=taker_out, at=at)
                native_price = pair.yes_bid if leg.side == "yes" else pair.no_bid
                if not newly:
                    # Reached our level but filled nothing under any model (queue ahead). The
                    # raw trade is already on the tape; a per-pair row for every such print
                    # would be the largest table in the schema for no extra information.
                    continue
                with self.session_factory() as session:
                    if self._cfg("persist_shadow_events", False):
                        store.insert_shadow_event(
                            session, quote_id=pair.quote_id, market_ticker=mk.ticker, at=at,
                            kind="trade_hit", side=leg.side, yes_price_cents=yes_px, count=count,
                            taker_outcome_side=taker_out, trade_id=trade_id,
                            detail_json={"newly_filled": newly, "queue_ahead_before": before})
                    for model, got in newly.items():
                        store.insert_fill(
                            session, quote_id=pair.quote_id, market_ticker=mk.ticker,
                            fill_model=model, side=leg.side, at=at, price_cents=native_price,
                            qty=got, cumulative_qty=leg.filled[model], is_full=leg.is_full(model),
                            queue_ahead_before=before[model],
                            seconds_since_placed=(at - pair.placed_at).total_seconds(),
                            trade_id=trade_id, mid_at_fill=self._mid(mk.book))
                horizons = ARM_MARK_HORIZONS_SECONDS if pair.arm else MARK_HORIZONS_SECONDS
                for model in newly:
                    key = (model, leg.side)
                    if key not in pair.marks_scheduled:
                        pair.marks_scheduled.add(key)
                        for h in horizons:
                            self.pending_marks.append(PendingMark(
                                quote_id=pair.quote_id, ticker=mk.ticker, model=model,
                                side=leg.side, horizon=h, due_at=at + timedelta(seconds=h),
                                fill_price=native_price, filled_qty=leg.filled[model]))
            if all(pair.yes_leg.is_full(mo) and pair.no_leg.is_full(mo) for mo in pair.yes_leg.models):
                self._end_pair(mk, pair, END_FILLED_ALL, at)

    @staticmethod
    def _mid(book: LocalBook) -> float | None:
        bid, ask = book.best_yes_bid(), book.best_yes_ask()
        return ((bid + ask) / 2.0) if bid is not None and ask is not None else None

    @staticmethod
    def _side_bid(book: LocalBook, side: str) -> int | None:
        """Best bid on `side` in that side's NATIVE cents (the liquidation price of a held
        contract on that side)."""
        if side == "yes":
            return book.best_yes_bid()
        ask = book.best_yes_ask()
        return (100 - ask) if ask is not None else None

    def _view(self, mk: TrackedMarket, now: datetime) -> qp.BookView:
        cutoff = now - timedelta(minutes=5)
        recent = [(px, c) for (at, px, c, _s) in mk.trades if at >= cutoff and px is not None]
        rng = (max(px for px, _ in recent) - min(px for px, _ in recent)) if recent else 0
        return qp.book_view_from_local(mk.book, trades_last_5m=len(recent), price_range_5m_cents=rng)

    def _native_levels(self, book: LocalBook) -> tuple[dict[int, float], dict[int, float]]:
        """(yes levels in yes cents, no levels in NO cents) for the scoring model."""
        return dict(book.yes), {100 - p: q for p, q in book.no.items()}

    def tick(self, now: datetime | None = None) -> None:
        """Clock-driven work: requotes, reward accrual, due marks, settlement pass."""
        now = now or self.clock()
        if self._started_at is None:
            self._started_at = now
        every = float(self._cfg("requote_seconds", 60.0))
        if self._arms_on():
            try:
                self._maybe_select_arms(now)
            except Exception as exc:  # noqa: BLE001
                self._record(EV_LOOP_ERROR, detail=f"arm select: {type(exc).__name__}: {exc}")
        for mk in list(self.markets.values()):
            try:
                if mk.last_requote_at is None or (now - mk.last_requote_at).total_seconds() >= every:
                    mk.last_requote_at = now
                    self._requote(mk, now)
            except Exception as exc:  # noqa: BLE001
                self._record(EV_LOOP_ERROR, ticker=mk.ticker,
                             detail=f"requote: {type(exc).__name__}: {exc}")
        self._process_marks(now)
        settle_every = float(self._cfg("settlement_seconds", 600.0))
        if self._last_settlement_at is None or (now - self._last_settlement_at).total_seconds() >= settle_every:
            self._last_settlement_at = now
            try:
                self.settle_pending(now)
            except Exception as exc:  # noqa: BLE001
                self._record(EV_LOOP_ERROR, detail=f"settle: {type(exc).__name__}: {exc}")

    def _requote(self, mk: TrackedMarket, now: datetime) -> None:
        t = mk.terms
        if t.end_date is not None and t.end_date <= now:
            self._end_all_pairs(mk, END_PROGRAM_ENDED, now)
            return
        if t.close_time is not None and t.close_time <= now:
            self._end_all_pairs(mk, END_MARKET_CLOSED, now)
            return
        if not mk.book.valid:
            if mk.invalid_since is None:
                mk.invalid_since = now
            elif (now - mk.invalid_since).total_seconds() >= 60 and mk.pairs:
                self._end_all_pairs(mk, END_BOOK_INVALID, now)
            return
        mk.invalid_since = None
        view = self._view(mk, now)
        yes_lv, no_lv = self._native_levels(mk.book)
        disc = sc.discount_factor(t.discount_factor_bps)
        ys = sc.side_score(yes_lv, t.target_size, disc)
        ns = sc.side_score(no_lv, t.target_size, disc)
        with self.session_factory() as session:
            store.insert_market_snapshot(
                session, program_row_id=t.row_id, market_ticker=mk.ticker, at=now,
                book_valid=True, best_yes_bid=view.best_yes_bid, best_no_bid=view.best_no_bid,
                spread_cents=view.spread_cents, yes_depth_at_best=view.yes_depth_at_best,
                no_depth_at_best=view.no_depth_at_best, yes_depth_total=ys.resting_total,
                no_depth_total=ns.resting_total,
                yes_levels_json=sorted(yes_lv.items(), reverse=True)[:10],
                no_levels_json=sorted(no_lv.items(), reverse=True)[:10],
                trades_last_5m=view.trades_last_5m,
                volume_last_5m=sum(c for (at, _p, c, _s) in mk.trades
                                   if at >= now - timedelta(minutes=5) and c) or 0.0,
                price_range_5m_cents=view.price_range_5m_cents,
                last_trade_yes_price=mk.trades[-1][1] if mk.trades else None,
                est_reference_price=ys.reference_price,
                est_yes_qualifying_depth=ys.resting_total, est_no_qualifying_depth=ns.resting_total,
                est_yes_meets_target=ys.meets_target, est_no_meets_target=ns.meets_target,
                est_yes_score_total=ys.field_score, est_no_score_total=ns.field_score,
                scoring_version=sc.SCORING_VERSION, seq=mk.book.last_seq, market_status=mk.status)
        # Accrue reward on every resting pair, then maintain / place.
        for pair in list(mk.pairs.values()):
            if pair.arm:
                self._accrue_arm(mk, pair, yes_lv, no_lv, now)
            else:
                self._accrue(mk, pair, yes_lv, no_lv, now)
        if self._arms_on():
            try:
                self._maintain_arms(mk, view, yes_lv, no_lv, now)
            except Exception as exc:  # noqa: BLE001
                self._record(EV_LOOP_ERROR, ticker=mk.ticker,
                             detail=f"arms: {type(exc).__name__}: {exc}")
        max_rest = float(self._cfg("max_rest_seconds", 3600))
        move_ticks = int(self._cfg("move_ticks", 2))
        max_loss = float(self._cfg("max_pair_loss_cents", 1.0))
        for policy in qp.POLICIES:
            for tier in self.tiers:
                key = (policy, tier)
                pair = mk.pairs.get(key)
                # First pass at zero fee to size the clip, then price with the fee at that size.
                fresh0 = qp.build_quote(policy, view, maker_fee_cents_per_pair=0.0, max_pair_loss_cents=max_loss)
                if fresh0 is None:
                    if pair is not None:
                        self._end_pair(mk, pair, END_BOOK_INVALID, now)
                    continue
                qty = qp.quantity_for_capital(tier, fresh0.pair_cost_cents, target_size=t.target_size)
                fee = pair_maker_fee_cents(t.fee_rule, fresh0.yes_bid, fresh0.no_bid, qty) if qty else 0.0
                fresh = qp.build_quote(policy, view, maker_fee_cents_per_pair=fee, max_pair_loss_cents=max_loss) or fresh0
                qty = qp.quantity_for_capital(tier, fresh.pair_cost_cents, target_size=t.target_size)
                if pair is not None:
                    if (now - pair.placed_at).total_seconds() >= max_rest:
                        self._end_pair(mk, pair, END_REFRESH, now)
                        pair = None
                    elif (abs(fresh.yes_bid - pair.yes_bid) >= move_ticks
                          or abs(fresh.no_bid - pair.no_bid) >= move_ticks):
                        self._end_pair(mk, pair, END_MARKET_MOVED, now)
                        pair = None
                if pair is None and qty > 0:
                    self._place(mk, policy, tier, fresh, qty, fee, yes_lv, no_lv, now)

    def _place(self, mk: TrackedMarket, policy: str, tier: int, quote: qp.QuotePair, qty: int,
               fee_cents: float, yes_lv: dict, no_lv: dict, now: datetime) -> None:
        t = mk.terms
        est = sc.estimate(yes_levels=yes_lv, no_levels=no_lv, target_size=t.target_size,
                          discount_factor_bps=t.discount_factor_bps,
                          our_yes_price=quote.yes_bid, our_yes_size=qty,
                          our_no_price=quote.no_bid, our_no_size=qty,
                          period_reward_usd_value=t.period_reward_usd, period_seconds=t.period_seconds)
        y_ahead = float(mk.book.yes.get(quote.yes_bid_yes_scale, 0.0))
        n_ahead = float(mk.book.no.get(quote.no_bid_yes_scale, 0.0))
        capital = qp.capital_required_usd(quote.yes_bid, quote.no_bid, qty)
        with self.session_factory() as session:
            row = store.insert_quote(
                session, program_row_id=t.row_id, market_ticker=mk.ticker, policy=policy,
                capital_tier_usd=tier, placed_at=now, yes_bid=quote.yes_bid, no_bid=quote.no_bid,
                yes_bid_yes_scale=quote.yes_bid_yes_scale, no_bid_yes_scale=quote.no_bid_yes_scale,
                qty_per_side=qty, pair_cost_cents=quote.pair_cost_cents,
                maker_fee_cents_per_pair=fee_cents, pair_edge_cents=quote.pair_edge_cents,
                capital_required_usd=capital, capital_unused_usd=round(tier - capital, 2),
                reason=quote.reason, yes_joins_best=quote.yes_joins_best,
                no_joins_best=quote.no_joins_best, yes_queue_ahead=y_ahead, no_queue_ahead=n_ahead,
                book_json={"yes": sorted(yes_lv.items(), reverse=True)[:10],
                           "no": sorted(no_lv.items(), reverse=True)[:10]},
                est_reference_price=est.yes.reference_price, est_yes_score=est.our_yes_raw,
                est_no_score=est.our_no_raw, est_yes_share=est.our_yes_share,
                est_no_share=est.our_no_share, est_reward_per_hour_usd=est.reward_per_hour_usd,
                scoring_version=sc.SCORING_VERSION)
            quote_id = int(row.id)
        pair = LivePair(
            quote_id=quote_id, policy=policy, tier=tier, yes_bid=quote.yes_bid, no_bid=quote.no_bid,
            qty=float(qty), placed_at=now, capital_required=capital,
            yes_leg=fm.ShadowLeg("yes", quote.yes_bid_yes_scale, float(qty), now, y_ahead),
            no_leg=fm.ShadowLeg("no", quote.no_bid_yes_scale, float(qty), now, n_ahead),
            last_accrual_at=now)
        mk.pairs[(policy, tier)] = pair

    def _accrue(self, mk: TrackedMarket, pair: LivePair, yes_lv: dict, no_lv: dict, now: datetime) -> None:
        """Add the scoring model's reward for the seconds since the last accrual, per side,
        assuming the leg still rests (per-model proration happens at outcome time)."""
        t = mk.terms
        last = pair.last_accrual_at or pair.placed_at
        seconds = max(0.0, (now - last).total_seconds())
        pair.last_accrual_at = now
        if seconds <= 0:
            return
        est = sc.estimate(yes_levels=yes_lv, no_levels=no_lv, target_size=t.target_size,
                          discount_factor_bps=t.discount_factor_bps,
                          our_yes_price=pair.yes_bid, our_yes_size=pair.qty,
                          our_no_price=pair.no_bid, our_no_size=pair.qty,
                          period_reward_usd_value=t.period_reward_usd, period_seconds=t.period_seconds)
        if not est.snapshot_qualifies or est.reward_per_second_usd is None:
            return
        total = est.reward_per_second_usd * seconds
        denom = est.our_yes_share + est.our_no_share
        if denom <= 0:
            return
        pair.reward_yes_usd += total * (est.our_yes_share / denom)
        pair.reward_no_usd += total * (est.our_no_share / denom)

    # -- one-sided paper arms (S1-S4, DEC-043/044) ------------------------------------------
    def _arm_candidate(self, mk: TrackedMarket, view: qp.BookView, yes_lv: dict, no_lv: dict,
                       now: datetime) -> pa.Candidate:
        """The market as the arms see it: each side's best bid and what a $10 bid there earns,
        scored by the same `scoring.estimate` the pairs use (the ranker's measure)."""
        t = mk.terms

        def side(name: str) -> pa.SideView:
            price = view.best_yes_bid if name == "yes" else view.best_no_bid
            if price is None or not (1 <= price <= 99):
                return pa.SideView(price=price)
            qty = pa.contracts(self.arms.rules.base_size_usd, price, 1000)
            est = self._arm_estimate(t, yes_lv, no_lv, name, price, float(qty))
            share = est.our_yes_share if name == "yes" else est.our_no_share
            return pa.SideView(price=price, rate10_per_hour_usd=float(est.reward_per_hour_usd or 0.0),
                               qualifies10=bool(est.snapshot_qualifies), share10=float(share))

        return pa.Candidate(
            ticker=mk.ticker, event=pa.event_of(mk.ticker, t.event_ticker),
            series=pa.series_of(mk.ticker, t.series_ticker), at=now, start=t.start_date,
            end=t.end_date, close_time=t.close_time, pool_day_usd=daily_pay_usd(t),
            yes=side("yes"), no=side("no"))

    @staticmethod
    def _arm_estimate(t: ProgramTerms, yes_lv: dict, no_lv: dict, side: str, price: int,
                      qty: float) -> sc.ScoreEstimate:
        yes = side == "yes"
        return sc.estimate(yes_levels=yes_lv, no_levels=no_lv, target_size=t.target_size,
                           discount_factor_bps=t.discount_factor_bps,
                           our_yes_price=price if yes else None, our_yes_size=qty if yes else 0.0,
                           our_no_price=None if yes else price, our_no_size=0.0 if yes else qty,
                           period_reward_usd_value=t.period_reward_usd,
                           period_seconds=t.period_seconds)

    def _maybe_select_arms(self, now: datetime) -> None:
        """Hourly, sticky re-selection over the latest fresh candidate of every tracked market.
        The first pass waits a few minutes so every market has requoted at least once."""
        started = self._started_at or now
        if self._last_arm_select_at is None:
            if (now - started).total_seconds() < ARM_FIRST_SELECT_AFTER_SECONDS:
                return
        elif (now - self._last_arm_select_at).total_seconds() < ARM_SELECT_SECONDS:
            return
        self._last_arm_select_at = now
        fresh = {t: cnd for t, cnd in self.arm_candidates.items()
                 if t in self.markets and (now - cnd.at).total_seconds() <= ARM_CANDIDATE_MAX_AGE_SECONDS}
        before = set(self.arms.selected[pa.S4])
        dropped = self.arms.select(fresh, now)
        for ticker, sel in self.arms.selected[pa.S4].items():
            if ticker not in before:
                self._restore_burst(sel)
        self._record(EV_ARM_SELECT, detail_json={
            "candidates": len(fresh),
            "selected": {s: sorted(v) for s, v in self.arms.selected.items()},
            "dropped": {s: v for s, v in dropped.items() if v}})

    def _restore_burst(self, sel: pa.Selection) -> None:
        """S4-burst's accrued reward lives in memory; a restart must not reset a programme's
        progress toward the $1 threshold. Rebuild it from the arm's own outcome rows."""
        key = self.arms.burst_key(sel.ticker, sel.programme_start)
        if key in self.arms.bursts:
            return
        try:
            with self.session_factory() as session:
                prior = store.arm_reward_sum(session, policy=pa.ARM_S4_BURST, ticker=sel.ticker,
                                             since=sel.programme_start,
                                             fill_model=fm.MODEL_CONSERVATIVE)
        except Exception:  # noqa: BLE001 — a failed read starts the burst from zero, logged
            logger.exception("incentive shadow: could not restore S4 burst for %s", sel.ticker)
            prior = 0.0
        self.arms.burst(sel.ticker, sel.programme_start)
        if prior > 0:
            self.arms.add_burst_reward(sel.ticker, sel.programme_start, prior)

    @staticmethod
    def _arm_pair(mk: TrackedMarket, arm: str) -> LivePair | None:
        for (policy, _tier), pair in mk.pairs.items():
            if policy == arm:
                return pair
        return None

    def _maintain_arms(self, mk: TrackedMarket, view: qp.BookView, yes_lv: dict, no_lv: dict,
                       now: datetime) -> None:
        cand = self._arm_candidate(mk, view, yes_lv, no_lv, now)
        self.arm_candidates[mk.ticker] = cand
        sels = self.arms.selections_for(mk.ticker)
        max_rest = float(self._cfg("max_rest_seconds", 3600))
        for strategy, arms in pa.ARMS_BY_STRATEGY.items():
            sel = sels.get(strategy)
            if sel is not None:
                self.arms.observe(sel, cand)
            for arm in arms:
                cur = self._arm_pair(mk, arm)
                want = (self.arms.desired(arm, sel, cand, now) if sel is not None
                        else pa.Pull(pa.END_DESELECTED))
                if isinstance(want, pa.Pull):
                    if cur is not None:
                        self._end_pair(mk, cur, want.reason, now)
                    continue
                if cur is not None:
                    cur_price = cur.yes_bid if cur.side == "yes" else cur.no_bid
                    if cur.side != want.side or cur.tier != want.size_usd or int(cur.qty) != want.qty:
                        self._end_pair(mk, cur, pa.END_RESIZED, now)
                    elif cur_price != want.price:
                        self._end_pair(mk, cur, END_MARKET_MOVED, now)
                    elif (now - cur.placed_at).total_seconds() >= max_rest:
                        self._end_pair(mk, cur, END_REFRESH, now)
                    else:
                        continue
                self._place_arm(mk, want, sel, cand, yes_lv, no_lv, now)

    def _place_arm(self, mk: TrackedMarket, q: pa.ArmQuote, sel: pa.Selection, cand: pa.Candidate,
                   yes_lv: dict, no_lv: dict, now: datetime) -> None:
        t = mk.terms
        yes = q.side == "yes"
        est = self._arm_estimate(t, yes_lv, no_lv, q.side, q.price, float(q.qty))
        share = est.our_yes_share if yes else est.our_no_share
        price_ys = q.price if yes else 100 - q.price
        ahead = float((mk.book.yes if yes else mk.book.no).get(price_ys, 0.0))
        capital = round(q.price * q.qty / 100.0, 2)
        joins = q.price == cand.side(q.side).price
        ref = est.yes.reference_price if yes else est.no.reference_price
        with self.session_factory() as session:
            row = store.insert_quote(
                session, program_row_id=t.row_id, market_ticker=mk.ticker, policy=q.arm,
                capital_tier_usd=q.size_usd, placed_at=now,
                # one-sided: the side we do not quote carries price 0 (no bid)
                yes_bid=q.price if yes else 0, no_bid=0 if yes else q.price,
                yes_bid_yes_scale=q.price if yes else 0, no_bid_yes_scale=100 if yes else price_ys,
                qty_per_side=q.qty, pair_cost_cents=q.price, maker_fee_cents_per_pair=None,
                pair_edge_cents=None, capital_required_usd=capital,
                capital_unused_usd=round(q.size_usd - capital, 2),
                reason=f"{q.arm}:{q.side}@{q.price}x{q.qty}"[:64],
                yes_joins_best=joins if yes else None, no_joins_best=None if yes else joins,
                yes_queue_ahead=ahead if yes else None, no_queue_ahead=None if yes else ahead,
                book_json={"yes": sorted(yes_lv.items(), reverse=True)[:10],
                           "no": sorted(no_lv.items(), reverse=True)[:10],
                           "arm": {"arm": q.arm, "strategy": pa.STRATEGY_OF[q.arm],
                                   "side": q.side, "size_usd": q.size_usd,
                                   "best_bid": cand.side(q.side).price,
                                   "event": cand.event, "series": cand.series,
                                   "pool_day_usd": cand.pool_day_usd,
                                   "qualifies": bool(est.snapshot_qualifies), "share": share,
                                   "thin": share > self.arms.rules.thin_share,
                                   "programme_start": (sel.programme_start.isoformat()
                                                       if sel.programme_start else None)}},
                est_reference_price=ref, est_yes_score=est.our_yes_raw, est_no_score=est.our_no_raw,
                est_yes_share=est.our_yes_share, est_no_share=est.our_no_share,
                est_reward_per_hour_usd=est.reward_per_hour_usd, scoring_version=sc.SCORING_VERSION)
            quote_id = int(row.id)
        other = "no" if yes else "yes"
        leg = fm.ShadowLeg(q.side, price_ys, float(q.qty), now, ahead, models=fm.ARM_FILL_MODELS)
        # The unquoted side: zero contracts at a level no print can reach (YES scale 0 / 100).
        dummy = fm.ShadowLeg(other, 0 if other == "yes" else 100, 0.0, now, 0.0,
                             models=fm.ARM_FILL_MODELS)
        pair = LivePair(
            quote_id=quote_id, policy=q.arm, tier=q.size_usd,
            yes_bid=q.price if yes else 0, no_bid=0 if yes else q.price, qty=float(q.qty),
            placed_at=now, capital_required=capital,
            yes_leg=leg if yes else dummy, no_leg=dummy if yes else leg, last_accrual_at=now,
            arm=q.arm, side=q.side, programme_start=sel.programme_start)
        mk.pairs[(q.arm, q.size_usd)] = pair

    def _accrue_arm(self, mk: TrackedMarket, pair: LivePair, yes_lv: dict, no_lv: dict,
                    now: datetime) -> None:
        """One-sided accrual with the hour's diagnostics. Study 3's thin rule: while we would
        be more than `thin_share` of our side, the estimate is logged but NOT credited."""
        last = pair.last_accrual_at or pair.placed_at
        seconds = max(0.0, (now - last).total_seconds())
        pair.last_accrual_at = now
        if seconds <= 0:
            return
        price = pair.yes_bid if pair.side == "yes" else pair.no_bid
        est = self._arm_estimate(mk.terms, yes_lv, no_lv, pair.side, price, pair.qty)
        share = est.our_yes_share if pair.side == "yes" else est.our_no_share
        pair.seconds_scored += seconds
        pair.share_seconds += share * seconds
        pair.max_share = max(pair.max_share, share)
        if not est.snapshot_qualifies or est.reward_per_second_usd is None:
            return
        pair.seconds_qualifying += seconds
        amount = est.reward_per_second_usd * seconds
        if share > self.arms.rules.thin_share:
            pair.seconds_thin += seconds
            pair.reward_uncredited_usd += amount
            return
        if pair.side == "yes":
            pair.reward_yes_usd += amount
        else:
            pair.reward_no_usd += amount
        if pair.arm == pa.ARM_S4_BURST:
            self.arms.add_burst_reward(mk.ticker, pair.programme_start, amount)

    def _process_marks(self, now: datetime) -> None:
        due = [pm for pm in self.pending_marks if pm.due_at <= now]
        if not due:
            return
        self.pending_marks = [pm for pm in self.pending_marks if pm.due_at > now]
        with self.session_factory() as session:
            for pm in due:
                mk = self.markets.get(pm.ticker)
                book = mk.book if mk is not None else None
                valid = bool(book and book.valid)
                bid = self._side_bid(book, pm.side) if valid else None
                mid_yes = self._mid(book) if valid else None
                mid = None if mid_yes is None else (mid_yes if pm.side == "yes" else 100 - mid_yes)
                pnl_bid = round((bid - pm.fill_price) * pm.filled_qty / 100.0, 4) if bid is not None else None
                pnl_mid = round((mid - pm.fill_price) * pm.filled_qty / 100.0, 4) if mid is not None else None
                store.insert_mark(
                    session, quote_id=pm.quote_id, market_ticker=pm.ticker, fill_model=pm.model,
                    side=pm.side, horizon_seconds=pm.horizon, at=now, fill_price_cents=pm.fill_price,
                    filled_qty=pm.filled_qty, mark_bid_cents=bid, mark_mid_cents=mid,
                    pnl_at_bid_usd=pnl_bid, pnl_at_mid_usd=pnl_mid, book_valid=valid)
                if mk is not None:
                    for pair in mk.pairs.values():
                        if pair.quote_id == pm.quote_id:
                            pair.marks.setdefault((pm.model, pm.side), []).append((pm.horizon, bid))

    # -- ending pairs -> outcomes ----------------------------------------------------------
    def _end_all_pairs(self, mk: TrackedMarket, reason: str, now: datetime) -> None:
        for pair in list(mk.pairs.values()):
            self._end_pair(mk, pair, reason, now)

    def _end_pair(self, mk: TrackedMarket, pair: LivePair, reason: str, now: datetime) -> None:
        mk.pairs.pop((pair.policy, pair.tier), None)
        rest = max(0.0, (now - pair.placed_at).total_seconds())
        t = mk.terms
        with self.session_factory() as session:
            store.end_quote(session, pair.quote_id, ended_at=now, reason=reason, rest_seconds=rest)
            if self._cfg("persist_shadow_events", False):
                store.insert_shadow_event(session, quote_id=pair.quote_id, market_ticker=mk.ticker,
                                          at=now, kind="end", detail_json={"reason": reason})
            if pair.arm:
                store.insert_shadow_event(
                    session, quote_id=pair.quote_id, market_ticker=mk.ticker, at=now,
                    kind=EV_ARM_HOUR, side=pair.side,
                    detail_json={"arm": pair.arm, "reason": reason, "rest_seconds": rest,
                                 "seconds_scored": pair.seconds_scored,
                                 "seconds_qualifying": pair.seconds_qualifying,
                                 "seconds_thin": pair.seconds_thin,
                                 "mean_share": (pair.share_seconds / pair.seconds_scored
                                                if pair.seconds_scored else None),
                                 "max_share": pair.max_share,
                                 "reward_credited_usd": pair.reward_yes_usd + pair.reward_no_usd,
                                 "reward_uncredited_thin_usd": pair.reward_uncredited_usd})
            for model in pair.yes_leg.models:
                # A filled leg stops earning: prorate that side's accrual by its resting time.
                ry = self._prorated(pair.reward_yes_usd, pair.yes_leg, model, pair.placed_at, now)
                rn = self._prorated(pair.reward_no_usd, pair.no_leg, model, pair.placed_at, now)
                y, n = pair.yes_leg.filled[model], pair.no_leg.filled[model]
                single_side = "yes" if y > n + 1e-9 else ("no" if n > y + 1e-9 else None)
                marks = pair.marks.get((model, single_side), []) if single_side else []
                bids = [b for (_h, b) in marks if b is not None]
                five = [b for (h, b) in marks if h == 300 and b is not None]
                mark_bid = five[0] if five else (bids[-1] if bids else None)
                worst = min(bids) if bids else None
                pe = econ.pair_economics(
                    fill_model=model, yes_leg=pair.yes_leg, no_leg=pair.no_leg,
                    yes_bid=pair.yes_bid, no_bid=pair.no_bid, rest_seconds=rest,
                    capital_required_usd=pair.capital_required, fee_rule=t.fee_rule,
                    reward_yes_usd=ry, reward_no_usd=rn,
                    single_leg_mark_bid_cents=mark_bid, single_leg_worst_bid_cents=worst)
                yf, nf = pair.yes_leg.first_fill_at.get(model), pair.no_leg.first_fill_at.get(model)
                store.insert_outcome(
                    session, quote_id=pair.quote_id, program_row_id=t.row_id, market_ticker=mk.ticker,
                    policy=pair.policy, capital_tier_usd=pair.tier, fill_model=model,
                    placed_at=pair.placed_at, ended_at=now, end_reason=reason, rest_seconds=rest,
                    outcome=pe.outcome, yes_filled_qty=y, no_filled_qty=n, matched_pairs=pe.matched_pairs,
                    yes_first_fill_at=yf, no_first_fill_at=nf,
                    seconds_between_legs=(abs((yf - nf).total_seconds()) if yf and nf else None),
                    capital_required_usd=pair.capital_required, capital_hours=pe.capital_hours,
                    est_reward_usd=pe.est_reward_usd, est_reward_yes_usd=pe.est_reward_yes_usd,
                    est_reward_no_usd=pe.est_reward_no_usd, paired_pnl_usd=pe.paired_pnl_usd,
                    fees_usd=pe.fees_usd, single_leg_side=pe.single_leg_side,
                    single_leg_qty=pe.single_leg_qty if pe.single_leg_side else None,
                    single_leg_mtm_5m_usd=pe.single_leg_mtm_usd,
                    single_leg_max_adverse_usd=pe.single_leg_max_adverse_usd,
                    net_before_settlement_usd=pe.net_before_settlement_usd,
                    scoring_version=sc.SCORING_VERSION)
                if pe.single_leg_side:
                    ps = self.pending_settlements.setdefault(pair.quote_id, PendingSettlement(
                        quote_id=pair.quote_id, ticker=mk.ticker, side=pe.single_leg_side,
                        qty_by_model={}, price_cents=(pair.yes_bid if pe.single_leg_side == "yes" else pair.no_bid)))
                    ps.qty_by_model[model] = pe.single_leg_qty

    @staticmethod
    def _prorated(accrued: float, leg: fm.ShadowLeg, model: str, placed: datetime, ended: datetime) -> float:
        full_at = leg.full_fill_at.get(model)
        if full_at is None:
            return accrued
        total = max(1e-9, (ended - placed).total_seconds())
        return accrued * max(0.0, min(1.0, (full_at - placed).total_seconds() / total))

    # -- settlement ------------------------------------------------------------------------
    def load_pending_settlements(self) -> int:
        """On start: single-leg outcomes not yet settled (survives a restart)."""
        with self.session_factory() as session:
            rows = store.unsettled_single_legs(session)
            for r in rows:
                ps = self.pending_settlements.setdefault(int(r.quote_id), PendingSettlement(
                    quote_id=int(r.quote_id), ticker=r.market_ticker, side=r.single_leg_side,
                    qty_by_model={}, price_cents=0))
                ps.qty_by_model[r.fill_model] = float(r.single_leg_qty or 0.0)
            for ps in self.pending_settlements.values():
                if ps.price_cents == 0:
                    q = session.get(m.IncentiveShadowQuote, ps.quote_id)
                    if q is not None:
                        ps.price_cents = q.yes_bid if ps.side == "yes" else q.no_bid
        return len(self.pending_settlements)

    def settle_pending(self, now: datetime) -> int:
        """REST pass over unsettled single-leg outcomes whose market may have resolved."""
        settled = 0
        by_ticker: dict[str, list[PendingSettlement]] = {}
        for ps in self.pending_settlements.values():
            by_ticker.setdefault(ps.ticker, []).append(ps)
        for ticker in by_ticker:
            mk = self.markets.get(ticker)
            if mk is not None and mk.terms.close_time is not None and mk.terms.close_time > now:
                continue          # cannot have resolved yet
            try:
                market = (self.client.get_market(ticker) or {}).get("market") or {}
            except Exception as exc:  # noqa: BLE001
                self._record(EV_LOOP_ERROR, ticker=ticker, detail=f"settle lookup: {type(exc).__name__}")
                continue
            result = market.get("result")
            status = market.get("status")
            if status in ("determined", "finalized", "settled") and result in ("yes", "no"):
                settled += self._settle_ticker(ticker, result, now)
            elif status in ("finalized", "settled"):
                # Resolved without a yes/no result (scalar or voided): the single-leg P&L is
                # not defined by this model. Drop the pending entry and say so, rather than
                # polling the market every ten minutes forever.
                dropped = [qid for qid, ps in self.pending_settlements.items() if ps.ticker == ticker]
                for qid in dropped:
                    del self.pending_settlements[qid]
                self._record(EV_SETTLED, ticker=ticker,
                             detail=f"resolved without yes/no result ({result!r}); {len(dropped)} outcomes left unsettled")
        return settled

    def _settle_ticker(self, ticker: str, result: str, now: datetime) -> int:
        n = 0
        for qid, ps in list(self.pending_settlements.items()):
            if ps.ticker != ticker:
                continue
            pnl = {model: econ.settlement_pnl_usd(single_leg_side=ps.side, single_leg_qty=q,
                                                   single_price_cents=ps.price_cents, result=result)
                   for model, q in ps.qty_by_model.items()}
            with self.session_factory() as session:
                n += store.stamp_settlement(session, quote_id=qid, settled_at=now, result=result,
                                            pnl_by_model=pnl)
            del self.pending_settlements[qid]
        if n:
            self._record(EV_SETTLED, ticker=ticker, detail=f"{result}: {n} outcome rows")
        return n

    def stop(self, now: datetime | None = None) -> None:
        now = now or self.clock()
        for mk in list(self.markets.values()):
            self._end_all_pairs(mk, END_COLLECTOR_STOP, now)


class ShadowThread:
    """Thin glue around `ShadowState`: a daemon thread holding one WebSocket connection."""

    def __init__(self, state: ShadowState, *, connect=None, recv_timeout: float = 1.0,
                 max_backoff: float = 60.0) -> None:
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
            except Exception as exc:  # noqa: BLE001
                self.state._record(EV_DISABLED, detail=f"websockets unavailable: {exc}")
                logger.warning("incentive shadow disabled: websockets unavailable: %s", exc)
                return False
            self._connect = ws_connect
        self._thread = threading.Thread(target=self._run, name="incentive-shadow", daemon=True)
        self._thread.start()
        self.state._record(EV_THREAD_STARTED)
        return True

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        try:
            self.state.stop()
        except Exception:  # noqa: BLE001
            logger.exception("incentive shadow: stop failed")
        self.state._record(EV_THREAD_STOPPED)

    def _run(self) -> None:
        backoff = 1.0
        try:
            self.state.load_pending_settlements()
        except Exception:  # noqa: BLE001
            logger.exception("incentive shadow: could not load pending settlements")
        while not self._stop.is_set():
            try:
                self._session()
                backoff = 1.0
            except AuthError as exc:
                self.state._record(EV_LOOP_ERROR, detail=f"auth: {exc}")
                return
            except Exception as exc:  # noqa: BLE001
                self.state.on_disconnected(f"{type(exc).__name__}: {str(exc)[:300]}")
                logger.warning("incentive shadow session ended: %s: %s", type(exc).__name__, str(exc)[:300])
            if self._stop.wait(backoff):
                break
            backoff = min(self.max_backoff, backoff * 2)

    def _session(self) -> None:
        client = self.state.client
        # Discovery BEFORE connecting: the first pass resolves every market/series and can
        # take a while; doing it inside the socket loop would stall the frame pump.
        self.state.refresh_programs(force=True)
        with self._connect(client.ws_url, additional_headers=client.ws_headers(), open_timeout=15) as conn:
            self._send_all(conn, self.state.on_connected())
            last_tick = time.monotonic()
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
                if time.monotonic() - last_tick >= 1.0:
                    last_tick = time.monotonic()
                    self.state.tick()
                    self._send_all(conn, self.state.refresh_programs())
        self.state.on_disconnected("closed")

    @staticmethod
    def _send_all(conn, cmds: list[dict]) -> None:
        for cmd in cmds:
            conn.send(json.dumps(cmd))


def start_shadow(client, settings) -> ShadowThread | None:
    """Build and start the shadow collector for any worker mode. Returns None when disabled."""
    if not getattr(settings, "liquidity_incentive_shadow_enabled", False):
        return None
    from .. import db
    from .readonly import IncentiveReadOnlyKalshi

    state = ShadowState(IncentiveReadOnlyKalshi(client, settings), settings, db.session_scope)
    thread = ShadowThread(state)
    return thread if thread.start() else None
