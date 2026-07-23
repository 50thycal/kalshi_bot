"""Agent-facing on-demand LIVE Kalshi market discovery (spec: agents may explore the
market on request, not just what the orchestrator's per-cycle scan surfaces). Backs
the `explore_markets` action: an agent names a series (or omits it for a broad sample)
and gets a bounded, current snapshot of open/settled markets so it can find NEW
domains to research beyond weather (crypto KXBTC*, sports, etc.).

Read-only: goes through LiveMarketData.list_markets, which only calls the Kalshi read
endpoints (get_markets/iter_markets) — never places an order. Bounded + budget-charged
by the caller. Results persist (reusing the sandbox-run record, kind='market_scan')
so they resurface in the agent's next-heartbeat prompt, since action outcomes are not
re-fed within the same turn."""

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

DEFAULT_LIMIT = 20
MAX_LIMIT = 30
STATUSES = ("open", "settled", "closed")
_CELL_CAP = 120

# Fields lifted from each Kalshi market dict (everything else is dropped).
_FIELDS = (
    "ticker", "event_ticker", "category", "yes_bid", "yes_ask", "volume",
    "close_time", "status",
)


def _cap(v: object) -> object:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        return v if len(v) <= _CELL_CAP else v[: _CELL_CAP - 1] + "…"
    return v


def explore(
    md, *, series: str | None = None, status: str = "open", limit: int = DEFAULT_LIMIT
) -> dict:
    """Snapshot of current Kalshi markets via the read-only client. Returns a bounded,
    field-whitelisted list. Tolerant: a market-data backend without list_markets (or an
    API error) yields an empty list rather than raising."""
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

    markets = [
        {f: _cap(m.get(f)) for f in _FIELDS}
        for m in raw[:limit]
        if isinstance(m, dict)
    ]
    return {"series": series, "status": status, "count": len(markets), "markets": markets}


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
