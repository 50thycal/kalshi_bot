"""Event timeline scoped to ONE paired run.

Every source is filtered by this pair's tags and epoch window, so no other
strategy's activity can appear. The high-volume source here is the per-candidate
decision tape (`live_paper_parity_events`, hundreds of rows per run), so it lives
in its own `decisions` category and is never part of the default view — the same
principle as keeping heartbeats out of a primary feed.

Paging is a composite `(<timestamp>|<stream>|<pk>)` cursor, so "load more" walks
back through the whole run rather than stopping at a recent-window limit.

Sanitisation: `live_orders.cancel_reason` is a short internal label written by our
own executor ("timeout"), and system-event messages are our own log lines — both
are length-capped here. Raw exchange payloads (`raw_order_json`, `raw_fill_json`,
`raw_json`) are never read by this module.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select

from .. import models as m

CATEGORIES = ("all", "orders", "fills", "positions", "pnl", "decisions", "errors")
DEFAULT_CATEGORY = "all"
DEFAULT_LIMIT = 50
MAX_LIMIT = 250

ENVIRONMENTS = ("live", "paper")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ev(stream: str, pk: int, at, *, category: str, environment: str, kind: str,
        message: str, severity: str = "info", ticker: str | None = None) -> dict | None:
    when = _aware(at)
    if when is None:
        return None
    return {
        "id": f"{stream}:{pk}", "stream": stream, "pk": pk, "_at": when,
        "at": when.isoformat(), "category": category, "environment": environment,
        "kind": kind, "message": message, "severity": severity, "ticker": ticker,
    }


def _sort_key(e: dict) -> tuple:
    return (e["_at"], e["stream"], e["pk"])


def encode_cursor(event: dict) -> str:
    return f"{event['_at'].isoformat()}|{event['stream']}|{event['pk']}"


def decode_cursor(cursor: str | None):
    if not cursor:
        return None
    try:
        raw_at, stream, pk = cursor.split("|", 2)
        return (_aware(datetime.fromisoformat(raw_at)), stream, int(pk))
    except (ValueError, TypeError):
        return None


def _window(query, column, since, before, limit):
    query = query.where(column >= since)
    if before is not None:
        query = query.where(column <= before)
    return query.order_by(column.desc()).limit(limit)


def fetch_events(
    session, pair, *, category: str = DEFAULT_CATEGORY, environment: str | None = None,
    limit: int = DEFAULT_LIMIT, cursor: str | None = None, now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    category = category if category in CATEGORIES else DEFAULT_CATEGORY
    environment = environment if environment in ENVIRONMENTS else None
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    decoded = decode_cursor(cursor)
    before = decoded[0] if decoded else None
    since, until = pair.window(now)
    # +2: one slot for the cursor row every stream re-reads and drops, one more so
    # "is there another page" is still detectable.
    per_stream = limit + 2

    wants = lambda c: category in ("all", c)  # noqa: E731
    live_ok = environment in (None, "live")
    paper_ok = environment in (None, "paper")
    # The decision tape is hundreds of rows per run, so it is opt-in only: "all"
    # means all the *narrative* events, not a firehose that buries them. It also
    # describes both sides at once, so an environment filter excludes it rather
    # than mislabelling it as one environment's event.
    decisions_ok = category == "decisions" and environment is None
    merged: list[dict] = []

    if live_ok and (wants("orders") or wants("errors")):
        merged.extend(_live_order_events(session, pair, since, before, per_stream, category))
    if live_ok and wants("fills"):
        merged.extend(_live_fill_events(session, pair, since, before, per_stream))
    if live_ok and (wants("positions") or wants("pnl")):
        merged.extend(_live_position_events(session, pair, since, before, per_stream))
    if paper_ok and (wants("orders") or wants("fills")):
        merged.extend(_paper_trade_events(session, pair, since, before, per_stream))
    if paper_ok and (wants("positions") or wants("pnl")):
        merged.extend(_paper_settle_events(session, pair, since, before, per_stream))
    if decisions_ok:
        merged.extend(_decision_events(session, pair, since, before, per_stream))
    if wants("errors"):
        merged.extend(_system_events(session, pair, since, before, per_stream))

    merged = [e for e in merged if e]
    if category == "errors":
        merged = [e for e in merged if e["severity"] in ("warn", "error")]
    if decoded is not None:
        merged = [e for e in merged if _sort_key(e) < decoded]
    merged.sort(key=_sort_key, reverse=True)

    page = merged[:limit]
    next_cursor = encode_cursor(page[-1]) if len(merged) > limit and page else None
    for e in page:
        e.pop("_at", None)
    return {
        "events": page, "next_cursor": next_cursor, "category": category,
        "environment": environment, "limit": limit,
        "window": {"since": since.isoformat(), "until": until.isoformat()},
    }


def _live_order_events(session, pair, since, before, limit, category) -> list[dict]:
    query = select(m.LiveOrder).where(m.LiveOrder.strategy == pair.live_tag)
    if category == "errors":
        query = query.where(m.LiveOrder.status.in_(("rejected", "error", "not_landed")))
    out = []
    for o in session.scalars(_window(query, m.LiveOrder.created_at, since, before, limit)):
        status = o.status or "submitted"
        bad = status in ("rejected", "error", "not_landed")
        reason = (o.cancel_reason or "")[:120]
        out.append(_ev(
            "live_order", o.id, o.created_at, category="orders", environment="live",
            kind=f"live_order_{status}", ticker=o.market_ticker,
            severity="error" if bad else ("warn" if status == "canceled" else "info"),
            message=(f"live {o.action} {o.quantity} {o.side} @ {o.limit_price}¢ "
                     f"in {o.market_ticker} — {status}"
                     + (f" ({reason})" if reason else "")),
        ))
    return out


def _live_fill_events(session, pair, since, before, limit) -> list[dict]:
    order_ids = list(session.scalars(
        select(m.LiveOrder.kalshi_order_id).where(
            m.LiveOrder.strategy == pair.live_tag,
            m.LiveOrder.created_at >= since,
            m.LiveOrder.kalshi_order_id.is_not(None),
        )
    ))
    if not order_ids:
        return []
    query = select(m.Fill).where(m.Fill.kalshi_order_id.in_(order_ids))
    # fills.filled_at is nullable in this schema, so order by id and filter in Python.
    rows = list(session.scalars(query.order_by(m.Fill.id.desc()).limit(limit * 3)))
    out = []
    for f in rows:
        at = _aware(f.filled_at)
        if at is None:
            continue
        if at < since or (before is not None and at > before):
            continue
        out.append(_ev(
            "fill", f.id, at, category="fills", environment="live", kind="live_fill",
            ticker=f.market_ticker,
            message=(f"filled {f.quantity} {f.side} @ {f.price}¢ in {f.market_ticker}"
                     f" (fee ${float(f.fee or 0):.2f})"),
        ))
        if len(out) >= limit:
            break
    return out


def _pair_tickers(session, pair, since) -> list[str]:
    return list(session.scalars(
        select(m.LiveOrder.market_ticker).where(
            m.LiveOrder.strategy == pair.live_tag, m.LiveOrder.created_at >= since
        ).distinct()
    ))


def _live_position_events(session, pair, since, before, limit) -> list[dict]:
    """Settlement snapshots only — the reconcile loop writes a position row every
    cycle, so publishing all of them would be exactly the repetitive-noise problem."""
    tickers = _pair_tickers(session, pair, since)
    if not tickers:
        return []
    query = select(m.Position).where(
        m.Position.market_ticker.in_(tickers),
        m.Position.quantity == 0,
        m.Position.realized_pnl.is_not(None),
    )
    out = []
    for p in session.scalars(_window(query, m.Position.captured_at, since, before, limit)):
        pnl = float(p.realized_pnl or 0.0)
        out.append(_ev(
            "live_settle", p.id, p.captured_at, category="pnl", environment="live",
            kind="live_settled", ticker=p.market_ticker,
            severity="info",
            message=f"{p.market_ticker} settled — live realized {pnl:+.2f} USD",
        ))
    return out


def _paper_trade_events(session, pair, since, before, limit) -> list[dict]:
    query = select(m.PaperTrade).where(
        m.PaperTrade.strategy == pair.twin_tag, m.PaperTrade.legacy.is_(False)
    )
    out = []
    for t in session.scalars(_window(query, m.PaperTrade.created_at, since, before, limit)):
        out.append(_ev(
            "paper_trade", t.id, t.created_at, category="orders", environment="paper",
            kind="paper_order_simulated", ticker=t.market_ticker,
            message=(f"paper assumed a {t.action} of {t.quantity} {t.side} @ "
                     f"{t.assumed_price}¢ in {t.market_ticker}"),
        ))
    return out


def _paper_settle_events(session, pair, since, before, limit) -> list[dict]:
    query = select(m.PaperTrade).where(
        m.PaperTrade.strategy == pair.twin_tag,
        m.PaperTrade.legacy.is_(False),
        m.PaperTrade.closed_at.is_not(None),
    )
    out = []
    for t in session.scalars(_window(query, m.PaperTrade.closed_at, since, before, limit)):
        pnl = float(t.pnl or 0.0)
        out.append(_ev(
            "paper_settle", t.id, t.closed_at, category="pnl", environment="paper",
            kind=f"paper_{t.status or 'closed'}", ticker=t.market_ticker,
            message=f"{t.market_ticker} {t.status or 'closed'} — paper P&L {pnl:+.2f} USD",
        ))
    return out


# Live outcome codes from the parity tape that mean the live book was BLOCKED.
_GATE_PREFIX = "gate:"


def _decision_events(session, pair, since, before, limit) -> list[dict]:
    """The per-candidate decision tape: what each actor did with the same market."""
    query = select(m.LivePaperParityEvent).where(
        m.LivePaperParityEvent.twin_tag == pair.twin_tag
    )
    out = []
    for row in session.scalars(
        _window(query, m.LivePaperParityEvent.recorded_at, since, before, limit)
    ):
        live_out = row.live_outcome or "not_attempted"
        blocked = live_out.startswith(_GATE_PREFIX) or live_out in ("rejected", "unknown")
        out.append(_ev(
            "parity", row.id, row.recorded_at, category="decisions", environment="paper",
            kind=f"decision_{live_out.replace(':', '_')}", ticker=row.market_ticker,
            severity="warn" if blocked else "info",
            message=(f"{row.market_ticker}: twin={row.twin_outcome or '—'}"
                     f"@{row.twin_price if row.twin_price is not None else '—'}¢, "
                     f"live={live_out}"
                     f"@{row.live_price if row.live_price is not None else '—'}¢"),
        ))
    return out


def _system_events(session, pair, since, before, limit) -> list[dict]:
    """Our own log lines about this pair — twin param drift, live execution errors.
    Message text is our own and length-capped; no exchange payload is read."""
    query = select(m.SystemEvent).where(
        m.SystemEvent.level.in_(("WARNING", "ERROR", "CRITICAL")),
        or_(
            m.SystemEvent.message.contains(pair.twin_tag),
            m.SystemEvent.message.contains(pair.live_tag),
        ),
    )
    out = []
    for row in session.scalars(_window(query, m.SystemEvent.created_at, since, before, limit)):
        out.append(_ev(
            "system", row.id, row.created_at, category="errors",
            environment="live" if row.component != "twin" else "paper",
            kind=f"system_{row.component}"[:48],
            severity="error" if row.level in ("ERROR", "CRITICAL") else "warn",
            message=str(row.message)[:280],
        ))
    return out


def gate_breakdown(session, pair, *, now: datetime | None = None) -> dict:
    """Why live placed nothing on candidates its twin took — the capacity-versus-edge
    distinction. A gap dominated by gates says nothing about whether the edge is real."""
    now = now or datetime.now(timezone.utc)
    since, _ = pair.window(now)
    counts: dict[str, int] = {}
    total = 0
    for row in session.scalars(
        select(m.LivePaperParityEvent).where(
            m.LivePaperParityEvent.twin_tag == pair.twin_tag,
            m.LivePaperParityEvent.recorded_at >= since,
        )
    ):
        total += 1
        key = row.live_outcome or "not_attempted"
        counts[key] = counts.get(key, 0) + 1
    placed = counts.get("placed", 0)
    return {
        "total_candidates": total,
        "live_placed": placed,
        "outcomes": sorted(
            ({"outcome": k, "count": v,
              "blocked": k.startswith(_GATE_PREFIX) or k in ("rejected", "unknown")}
             for k, v in counts.items()),
            key=lambda r: -r["count"],
        ),
        "note": "a gap dominated by gates is a capacity difference, not evidence about edge",
    }
