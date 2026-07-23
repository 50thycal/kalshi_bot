"""Agent-facing on-demand LIVE Kalshi market discovery (spec: agents may explore the
market on request, not just what the orchestrator's per-cycle scan surfaces). Backs
the `explore_markets` action: an agent names a series (or omits it for a broad sample)
and gets a bounded, current snapshot of open/settled markets so it can find NEW
domains to research beyond weather (crypto KXBTC*, sports, etc.).

Two-stage, read-only:
1. DISCOVER — LiveMarketData.list_markets (get_markets/iter_markets) yields the current
   tickers for the series. The list endpoint is a ticker index; it does NOT carry live
   prices (Kalshi returns those on the per-market quote), which is why a raw scan shows
   bare tickers.
2. ENRICH — each discovered ticker is run through LiveMarketData.get_quote, the SAME
   live-quote path the worker trades on (market detail + orderbook), to fill live
   yes_bid/yes_ask/mid/spread, volume, open interest, last price, category and title.
   Markets are then ordered most-liquid-first so the agent sees the tradeable ones on top.

Never places an order. Bounded + budget-charged by the caller. Degrades safely: a backend
without list_markets/get_quote, or any per-ticker fetch failure, yields fewer/bare rows
rather than raising. Results persist (sandbox-run record, kind='market_scan') so they
resurface in the agent's next-heartbeat prompt, since action outcomes are not re-fed
within the same turn."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

# NOTE: sqlalchemy + EvoSandboxRun are imported lazily inside record_scan/recent_scans
# (not at module top) so the pure explore() path stays importable with only the stdlib.
# That lets the read-only ops probe (scripts/evo_explore_probe.py) drive the REAL
# explore() against live Kalshi without pulling in the ORM.
if TYPE_CHECKING:
    from .models import EvoSandboxRun

# A fully-enriched scan does up to `limit` per-ticker quote fetches, so the default is
# kept modest for a responsive heartbeat; an agent can ask for up to MAX_LIMIT.
DEFAULT_LIMIT = 12
MAX_LIMIT = 30
STATUSES = ("open", "settled", "closed")
_CELL_CAP = 120

# The snapshot each market is reduced to (stable shape; everything else is dropped).
# ticker/event_ticker/close_time/status come from discovery; the rest are filled from
# the live quote so the agent can judge price, spread and liquidity at a glance.
_FIELDS = (
    "ticker", "event_ticker", "category", "title", "status",
    "yes_bid", "yes_ask", "yes_mid", "spread",
    "volume", "open_interest", "last_price",
    "close_time", "hours_to_close",
)


def _cap(v: object) -> object:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        return v if len(v) <= _CELL_CAP else v[: _CELL_CAP - 1] + "…"
    return v


def _enriched_row(base: dict, q) -> dict:
    """One market's snapshot: discovery-level fields from the list dict, with the live
    quote `q` (a marketdata.Quote or None) overlaid for price/liquidity."""
    row: dict = dict.fromkeys(_FIELDS)
    row["ticker"] = base.get("ticker")
    row["event_ticker"] = base.get("event_ticker")
    row["category"] = base.get("category")
    row["status"] = base.get("status")
    row["close_time"] = base.get("close_time")
    # the list endpoint sometimes carries these; keep them as a fallback under the quote
    row["yes_bid"] = base.get("yes_bid")
    row["yes_ask"] = base.get("yes_ask")
    row["volume"] = base.get("volume")
    if q is not None:
        row["event_ticker"] = q.event_ticker or row["event_ticker"]
        row["category"] = q.category if q.category is not None else row["category"]
        row["title"] = q.title
        row["status"] = q.status or row["status"]
        if q.yes_bid is not None:
            row["yes_bid"] = q.yes_bid
        if q.yes_ask is not None:
            row["yes_ask"] = q.yes_ask
        row["yes_mid"] = round(q.mid, 1) if q.mid is not None else None
        row["spread"] = q.spread
        if q.volume is not None:
            row["volume"] = q.volume
        row["open_interest"] = q.open_interest
        row["last_price"] = q.last_price
        if q.close_time is not None:
            row["close_time"] = q.close_time
        h = q.hours_to_close()
        row["hours_to_close"] = round(h, 1) if h is not None else None
    return {k: _cap(v) for k, v in row.items()}


def explore(
    md, *, series: str | None = None, status: str = "open",
    limit: int = DEFAULT_LIMIT, enrich: bool = True,
) -> dict:
    """Snapshot of current Kalshi markets: discover tickers via the read-only list
    endpoint, then enrich each with its live quote (price/spread/liquidity) so the agent
    sees a tradeable picture, most-liquid-first. Tolerant: a backend without
    list_markets/get_quote — or any per-ticker fetch failure — yields fewer/bare rows
    rather than raising."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    lister = getattr(md, "list_markets", None)
    raw: list = []
    if callable(lister):
        params: dict = {"status": status, "max_markets": limit}
        if series:
            params["series_ticker"] = series
        try:
            raw = list(lister(**params))
        except Exception:  # noqa: BLE001 — a data failure must degrade, not crash the heartbeat
            raw = []

    discovered = [m for m in raw[:limit] if isinstance(m, dict)]
    getq = getattr(md, "get_quote", None) if enrich else None
    rows: list[dict] = []
    for m in discovered:
        q = None
        ticker = str(m.get("ticker") or "")
        if callable(getq) and ticker:
            try:
                q = getq(ticker)
            except Exception:  # noqa: BLE001 — one bad ticker must not sink the whole scan
                q = None
        rows.append(_enriched_row(m, q))

    # Lead with the most liquid markets (highest volume first; unknown volume last) so the
    # tradeable ones are on top.
    rows.sort(key=lambda r: (r.get("volume") is None, -(r.get("volume") or 0)))
    priced = sum(1 for r in rows if r.get("yes_bid") is not None or r.get("yes_ask") is not None)
    return {
        "series": series, "status": status, "count": len(rows),
        "priced": priced, "markets": rows,
    }


def record_scan(
    session, *, agent_uuid: str, heartbeat_id: int | None, result: dict
) -> EvoSandboxRun:
    """Persist an explore_markets scan (kind='market_scan') so the discovered markets
    resurface in the agent's next-heartbeat prompt."""
    from .models import EvoSandboxRun

    run = EvoSandboxRun(
        agent_uuid=agent_uuid,
        heartbeat_id=heartbeat_id,
        kind="market_scan",
        dataset=str(result.get("series") or "any")[:64],
        params_json={"series": result.get("series"), "status": result.get("status")},
        result_json=result,
        rows_processed=int(result.get("count", 0) or 0),
    )
    session.add(run)
    session.flush()
    return run


def recent_scans(session, agent_uuid: str, *, limit: int = 4) -> list[dict]:
    """The agent's most recent explore_markets scans, for its next-heartbeat prompt."""
    from sqlalchemy import select

    from .models import EvoSandboxRun

    rows = list(
        session.scalars(
            select(EvoSandboxRun)
            .where(
                EvoSandboxRun.agent_uuid == agent_uuid,
                EvoSandboxRun.kind == "market_scan",
            )
            .order_by(EvoSandboxRun.created_at.desc())
            .limit(limit)
        )
    )
    out: list[dict] = []
    for r in rows:
        res = r.result_json or {}
        out.append(
            {
                "series": res.get("series"),
                "status": res.get("status"),
                "count": res.get("count"),
                "markets": (res.get("markets") or [])[:12],
            }
        )
    return out
