"""Deterministic listener layer (spec §10): declarative conditions evaluated every
orchestrator cycle against fresh market data + system state, with zero LLM calls.

Condition document (JSON, validated):
  {"all": [ {"metric": "yes_ask", "op": "<=", "value": 30}, ... ],
   "ticker": "KXHIGHCHI-...",         # market-scoped metrics need a ticker
   }
Groups: "all" (AND) / "any" (OR); one level of nesting is enough for the spec's
condition classes and keeps evaluation trivially bounded.

Metrics:
  market:   yes_bid yes_ask no_bid no_ask spread mid last_price volume
            open_interest hours_to_close depth status_is:<s> result_is:<r>
  movement: delta:<metric>:<seconds>   (change vs the listener's stored baseline)
  position: position_pnl_cents position_qty (owner's cohort position on `ticker`)
  portfolio: drawdown_pct cash_usd nav_usd
  system:   data_health_errors (unresolved), new_market:<series_prefix>
Effects (listener.effect): event | trigger_heartbeat | schedule_heartbeat |
  opportunity — all also write an evo_listener_events row (deduped + cooled down)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .config import EvoSettings
from .marketdata import MarketData, Quote
from .models import (
    EvoDataHealthEvent,
    EvoListener,
    EvoListenerEvent,
    EvoOpportunity,
    EvoPortfolio,
    EvoPosition,
)

logger = logging.getLogger(__name__)

OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
       ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}

MARKET_METRICS = (
    "yes_bid", "yes_ask", "no_bid", "no_ask", "spread", "mid", "last_price",
    "volume", "open_interest", "hours_to_close", "depth",
)
SCALAR_METRICS = MARKET_METRICS + (
    "position_pnl_cents", "position_qty", "drawdown_pct", "cash_usd", "nav_usd",
    "data_health_errors",
)
MAX_CLAUSES = 8


def validate_condition(doc: dict) -> str | None:
    """Return an error string or None. Bounds clause count and vocabulary."""
    if not isinstance(doc, dict):
        return "condition must be an object"
    clauses = doc.get("all") or doc.get("any")
    if not isinstance(clauses, list) or not clauses:
        return "condition needs a non-empty 'all' or 'any' list"
    if len(clauses) > MAX_CLAUSES:
        return f"too many clauses (max {MAX_CLAUSES})"
    for c in clauses:
        if not isinstance(c, dict):
            return "clause must be an object"
        metric = str(c.get("metric", ""))
        base = metric.split(":", 1)[0]
        if base in SCALAR_METRICS:
            if c.get("op") not in OPS:
                return f"bad op {c.get('op')!r}"
            if not isinstance(c.get("value"), (int, float)):
                return f"clause value must be numeric for {metric}"
        elif base == "delta":
            parts = metric.split(":")
            if len(parts) != 3 or parts[1] not in MARKET_METRICS:
                return f"delta metric must be delta:<market_metric>:<seconds> ({metric})"
            try:
                int(parts[2])
            except ValueError:
                return f"bad delta window in {metric}"
            if c.get("op") not in OPS or not isinstance(c.get("value"), (int, float)):
                return f"bad delta clause {metric}"
        elif base in ("status_is", "result_is", "new_market"):
            if len(metric.split(":", 1)) != 2:
                return f"{base} needs a ':<value>' suffix"
        else:
            return f"unknown metric {metric!r}"
    return None


class ListenerContext:
    """Everything one evaluation pass needs; quotes are memoized per ticker."""

    def __init__(self, session, settings: EvoSettings, md: MarketData,
                 *, now: datetime | None = None) -> None:
        self.session = session
        self.settings = settings
        self.md = md
        self.now = now or datetime.now(timezone.utc)
        self._quotes: dict[str, Quote | None] = {}
        self._new_tickers: set[str] = set()

    def quote(self, ticker: str) -> Quote | None:
        if ticker not in self._quotes:
            self._quotes[ticker] = self.md.get_quote(ticker)
        return self._quotes[ticker]

    def set_new_tickers(self, tickers: set[str]) -> None:
        """Tickers first seen this cycle (for new_market:<prefix> conditions)."""
        self._new_tickers = tickers

    def new_market_matches(self, prefix: str) -> bool:
        return any(t.startswith(prefix) for t in self._new_tickers)


def _market_value(quote: Quote, metric: str, now: datetime) -> float | None:
    if metric == "spread":
        return None if quote.spread is None else float(quote.spread)
    if metric == "mid":
        return quote.mid
    if metric == "hours_to_close":
        return quote.hours_to_close(now)
    if metric == "depth":
        return float(sum(q for _, q in quote.yes_levels) + sum(q for _, q in quote.no_levels))
    v = getattr(quote, metric, None)
    return None if v is None else float(v)


def _eval_clause(
    ctx: ListenerContext, listener: EvoListener, clause: dict, state: dict
) -> bool | None:
    """None = data unavailable (clause fails closed)."""
    metric = str(clause.get("metric", ""))
    base = metric.split(":", 1)[0]
    ticker = clause.get("ticker") or (listener.condition_json or {}).get("ticker")

    if base in MARKET_METRICS:
        if not ticker:
            return None
        quote = ctx.quote(ticker)
        if quote is None or quote.age_seconds(ctx.now) > ctx.settings.stale_data_seconds:
            return None
        value = _market_value(quote, metric, ctx.now)
    elif base == "delta":
        _, sub, window_s = metric.split(":")
        if not ticker:
            return None
        quote = ctx.quote(ticker)
        if quote is None:
            return None
        current = _market_value(quote, sub, ctx.now)
        if current is None:
            return None
        key = f"baseline:{ticker}:{sub}"
        baseline = state.get(key)
        state[key] = {"value": current, "at": ctx.now.isoformat()}
        if not baseline:
            return None  # first observation establishes the baseline
        try:
            base_at = datetime.fromisoformat(baseline["at"])
        except (KeyError, ValueError):
            return None
        if (ctx.now - base_at).total_seconds() < int(window_s):
            # keep the older baseline until the window elapses
            state[key] = baseline
            return None
        value = current - float(baseline["value"])
    elif base == "status_is":
        if not ticker:
            return None
        quote = ctx.quote(ticker)
        return quote is not None and quote.status == metric.split(":", 1)[1]
    elif base == "result_is":
        if not ticker:
            return None
        quote = ctx.quote(ticker)
        return quote is not None and quote.result == metric.split(":", 1)[1]
    elif base == "new_market":
        return ctx.new_market_matches(metric.split(":", 1)[1])
    elif base == "position_pnl_cents" or base == "position_qty":
        if not ticker:
            return None
        pos = ctx.session.scalar(
            select(EvoPosition).where(
                EvoPosition.agent_uuid == listener.owner_uuid,
                EvoPosition.market_ticker == ticker,
                EvoPosition.ledger.like("cohort:%"),
                EvoPosition.status == "open",
            )
        )
        if pos is None:
            return None
        if base == "position_qty":
            value = float(pos.quantity)
        else:
            mark = pos.last_mark_cents
            if mark is None:
                return None
            value = float(mark) - float(pos.avg_price_cents)
    elif base in ("drawdown_pct", "cash_usd", "nav_usd"):
        pf = ctx.session.scalar(
            select(EvoPortfolio).where(
                EvoPortfolio.agent_uuid == listener.owner_uuid,
                EvoPortfolio.ledger.like("cohort:%"),
            ).order_by(EvoPortfolio.id.desc())
        )
        if pf is None:
            return None
        if base == "cash_usd":
            value = float(pf.cash_usd)
        else:
            from .paper import nav as _nav  # local import to avoid cycle

            current = _nav(ctx.session, listener.owner_uuid, pf.ledger)
            if base == "nav_usd":
                value = current
            else:
                peak = float(pf.peak_nav_usd or pf.starting_capital_usd)
                value = 0.0 if peak <= 0 else max(0.0, (peak - current) / peak * 100.0)
    elif base == "data_health_errors":
        value = float(
            ctx.session.scalar(
                select(EvoDataHealthEvent.id)
                .where(EvoDataHealthEvent.level == "error",
                       EvoDataHealthEvent.resolved_at.is_(None))
                .order_by(EvoDataHealthEvent.id.desc())
                .limit(1)
            )
            is not None
        )
    else:
        return None

    if value is None:
        return None
    op = OPS.get(clause.get("op", ""))
    if op is None:
        return None
    try:
        return bool(op(value, float(clause["value"])))
    except (TypeError, KeyError, ValueError):
        return None


def evaluate_listener(ctx: ListenerContext, listener: EvoListener) -> bool:
    """True when the condition fires. Unavailable data fails closed (False)."""
    doc = listener.condition_json or {}
    clauses = doc.get("all") or doc.get("any") or []
    mode_all = "all" in doc
    state = dict(doc.get("_state") or {})
    results = [
        _eval_clause(ctx, listener, c, state) for c in clauses[:MAX_CLAUSES]
    ]
    # persist rolling baselines for delta metrics
    if state != (doc.get("_state") or {}):
        listener.condition_json = {**doc, "_state": state}
    if mode_all:
        return bool(results) and all(r is True for r in results)
    return any(r is True for r in results)


def _fire(ctx: ListenerContext, listener: EvoListener) -> EvoListenerEvent | None:
    dedup_key = f"{ctx.now.strftime('%Y%m%d%H%M')}"
    existing = ctx.session.scalar(
        select(EvoListenerEvent).where(
            EvoListenerEvent.listener_id == listener.id,
            EvoListenerEvent.dedup_key == dedup_key,
        )
    )
    if existing is not None:
        return None
    event = EvoListenerEvent(
        listener_id=listener.id,
        owner_uuid=listener.owner_uuid,
        dedup_key=dedup_key,
        payload_json={
            "listener": listener.name,
            "effect": listener.effect,
            "purpose": (listener.purpose or "")[:200],
            "ticker": (listener.condition_json or {}).get("ticker"),
        },
    )
    ctx.session.add(event)
    listener.last_triggered_at = ctx.now
    listener.trigger_count += 1
    ctx.session.flush()
    if listener.effect == "opportunity":
        ticker = (listener.condition_json or {}).get("ticker") or ""
        if ticker:
            opp_key = f"listener:{listener.id}:{dedup_key}"
            if ctx.session.scalar(
                select(EvoOpportunity).where(
                    EvoOpportunity.agent_uuid == listener.owner_uuid,
                    EvoOpportunity.dedup_key == opp_key,
                )
            ) is None:
                ctx.session.add(
                    EvoOpportunity(
                        agent_uuid=listener.owner_uuid,
                        dedup_key=opp_key,
                        market_ticker=ticker,
                        source="listener",
                        detail_json={"listener_id": listener.id, "name": listener.name},
                    )
                )
                ctx.session.flush()
    return event


def evaluate_all(ctx: ListenerContext) -> dict[str, int]:
    """One evaluation pass over every active listener. Returns counters. Triggered-
    heartbeat effects are consumed by the heartbeat orchestrator, which reads
    unconsumed events for owners with trigger budget remaining."""
    counts = {"active": 0, "fired": 0, "expired": 0, "cooled": 0}
    listeners = list(
        ctx.session.scalars(select(EvoListener).where(EvoListener.status == "active"))
    )
    for listener in listeners:
        counts["active"] += 1
        if listener.expires_at is not None:
            exp = listener.expires_at if listener.expires_at.tzinfo else (
                listener.expires_at.replace(tzinfo=timezone.utc)
            )
            if ctx.now >= exp:
                listener.status = "expired"
                counts["expired"] += 1
                continue
        if listener.last_triggered_at is not None:
            last = listener.last_triggered_at if listener.last_triggered_at.tzinfo else (
                listener.last_triggered_at.replace(tzinfo=timezone.utc)
            )
            cooldown = max(listener.cooldown_seconds, ctx.settings.listener_min_cooldown_seconds)
            if (ctx.now - last).total_seconds() < cooldown:
                counts["cooled"] += 1
                continue
        try:
            fired = evaluate_listener(ctx, listener)
        except Exception:  # noqa: BLE001 — one bad listener must not stop the pass
            logger.exception("listener %s evaluation failed", listener.id)
            continue
        if fired and _fire(ctx, listener) is not None:
            counts["fired"] += 1
    ctx.session.flush()
    return counts


# ---------------------------------------------------------------------------
# Agent-facing CRUD (via cognition actions)
# ---------------------------------------------------------------------------


def create_listener(
    session,
    settings: EvoSettings,
    *,
    owner_uuid: str,
    name: str,
    condition: dict,
    purpose: str | None = None,
    effect: str = "event",
    cooldown_seconds: int = 300,
    expires_in_hours: float | None = None,
    expected_value_note: str | None = None,
    heartbeat_id: int | None = None,
) -> tuple[EvoListener | None, str | None]:
    err = validate_condition(condition)
    if err:
        return None, err
    if effect not in ("event", "trigger_heartbeat", "schedule_heartbeat", "opportunity"):
        return None, f"unknown effect {effect!r}"
    active = session.scalar(
        select(EvoListener.id)
        .where(EvoListener.owner_uuid == owner_uuid, EvoListener.status == "active")
        .order_by(EvoListener.id.desc())
        .offset(settings.max_listeners_per_agent - 1)
    )
    if active is not None:
        return None, f"listener cap ({settings.max_listeners_per_agent}) reached"
    row = EvoListener(
        owner_uuid=owner_uuid,
        created_heartbeat_id=heartbeat_id,
        name=name[:64],
        purpose=purpose,
        condition_json=condition,
        effect=effect,
        cooldown_seconds=max(settings.listener_min_cooldown_seconds, int(cooldown_seconds)),
        expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
            if expires_in_hours else None
        ),
        expected_value_note=expected_value_note,
    )
    session.add(row)
    session.flush()
    return row, None


def remove_listener(
    session, owner_uuid: str, listener_id: int
) -> tuple[EvoListener | None, str | None]:
    row = session.get(EvoListener, listener_id)
    if row is None:
        return None, f"listener {listener_id} not found"
    if row.owner_uuid != owner_uuid:
        return None, "cannot remove another agent's listener"
    row.status = "removed"
    session.flush()
    return row, None


def unconsumed_events(session, owner_uuid: str, limit: int = 10) -> list[EvoListenerEvent]:
    return list(
        session.scalars(
            select(EvoListenerEvent)
            .where(
                EvoListenerEvent.owner_uuid == owner_uuid,
                EvoListenerEvent.consumed.is_(False),
            )
            .order_by(EvoListenerEvent.id)
            .limit(limit)
        )
    )
