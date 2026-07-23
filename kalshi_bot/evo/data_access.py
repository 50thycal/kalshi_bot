"""Agent-facing READ access to everything the bot has collected (spec: agents are
not limited to weather). This backs the `inspect_data` action: an agent names an
allowlisted SOURCE and gets a bounded, most-recent-first sample of that table so it
can study any market/domain we have data for — including what our OTHER strategies
(mmsell, theta, weather, ...) actually did, via paper_trades/signals — and copy them.

Safety is by CONSTRUCTION, not by parsing (the evo worker runs on the read-write DB
URL, so a lexical SQL guard would not be enough): agents never supply SQL. Each
source is a typed select() over ONE model with a whitelisted column set; filters are
only accepted on a per-source whitelist of (indexed) columns and their values are
bound parameters; output is row- and cell-capped. Read-only — nothing here writes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from ..models import (
    CryptoLadderSnapshot,
    CryptoSpotCandle,
    GameMarketMatch,
    GameTapeSnapshot,
    MarketSnapshot,
    MmSellPositionTick,
    OrderbookSnapshot,
    PaperPosition,
    PaperTrade,
    PolymarketSnapshot,
    Signal,
)
from .models import EvoSandboxRun

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
_CELL_CAP = 200


# source -> (model, output columns, filterable columns, order-by column [desc]).
# Only these tables/columns are ever exposed; anything else is rejected.
SOURCES: dict[str, dict] = {
    # what our OTHER strategies actually did (so agents can study + copy them)
    "paper_trades": dict(
        model=PaperTrade,
        columns=("created_at", "strategy", "market_ticker", "side", "action",
                 "assumed_price", "quantity", "edge", "status", "resolved_value",
                 "pnl", "closed_at"),
        filters=("strategy", "market_ticker", "status"),
        order="created_at",
    ),
    "paper_positions": dict(
        model=PaperPosition,
        columns=("opened_at", "strategy", "market_ticker", "side", "quantity",
                 "avg_price", "status", "pnl", "unrealized_pnl", "closed_at"),
        filters=("strategy", "market_ticker", "status"),
        order="opened_at",
    ),
    "signals": dict(
        model=Signal,
        columns=("created_at", "market_ticker", "signal_type", "bot_mode",
                 "implied_probability", "model_probability", "edge", "confidence",
                 "label"),
        filters=("market_ticker", "bot_mode", "signal_type", "label"),
        order="created_at",
    ),
    # crypto (Kalshi contract ladders + Coinbase underlying)
    "crypto_ladders": dict(
        model=CryptoLadderSnapshot,
        columns=("captured_at", "market_ticker", "event_ticker", "strike_type",
                 "floor_strike", "cap_strike", "yes_bid_cents", "yes_ask_cents",
                 "mid_cents", "volume", "minutes_to_close", "spot", "model_p",
                 "model_excess_cents"),
        filters=("market_ticker", "event_ticker", "series", "strike_type"),
        order="captured_at",
    ),
    "crypto_spot": dict(
        model=CryptoSpotCandle,
        columns=("minute_ts", "product", "close"),
        filters=("product",),
        order="minute_ts",
    ),
    # mmsell intraday tape (join to paper_trades for outcomes)
    "mmsell_ticks": dict(
        model=MmSellPositionTick,
        columns=("captured_at", "market_ticker", "yes_bid", "yes_ask", "no_bid",
                 "no_ask", "mid", "volume"),
        filters=("market_ticker",),
        order="captured_at",
    ),
    # sports/game
    "game_matches": dict(
        model=GameMarketMatch,
        columns=("created_at", "sport", "day", "team", "kalshi_ticker",
                 "kalshi_title", "status", "close_time"),
        filters=("sport", "kalshi_ticker", "kalshi_series", "status"),
        order="created_at",
    ),
    "game_tape": dict(
        model=GameTapeSnapshot,
        columns=("captured_at", "match_id", "venue", "market_id", "traded_at",
                 "price_cents", "team_prob_cents", "size", "taker_side"),
        filters=("match_id", "market_id", "venue"),
        order="captured_at",
    ),
    # general Kalshi scan history
    "market_snapshots": dict(
        model=MarketSnapshot,
        columns=("captured_at", "market_ticker", "yes_bid", "yes_ask", "no_bid",
                 "no_ask", "last_price", "volume", "open_interest", "spread"),
        filters=("market_ticker",),
        order="captured_at",
    ),
    "orderbook": dict(
        model=OrderbookSnapshot,
        columns=("captured_at", "market_ticker", "best_yes_bid", "best_yes_ask",
                 "best_no_bid", "best_no_ask", "top_depth"),
        filters=("market_ticker",),
        order="captured_at",
    ),
    "polymarket": dict(
        model=PolymarketSnapshot,
        columns=("captured_at", "city", "kind", "target_date", "yes_prob"),
        filters=("city", "kind", "target_date"),
        order="captured_at",
    ),
}


def source_catalog() -> list[dict]:
    """Compact description of every readable source, for the prompt."""
    return [
        {"source": name, "filters": list(cfg["filters"])}
        for name, cfg in SOURCES.items()
    ]


def _cap_cell(v: object) -> object:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        return v if len(v) <= _CELL_CAP else v[: _CELL_CAP - 1] + "…"
    return v


def inspect(
    session, source: str, *, filters: dict | None = None, limit: int = DEFAULT_LIMIT
) -> tuple[dict | None, str | None]:
    """Return a bounded, most-recent-first sample of an allowlisted source. Returns
    (result, None) or (None, error). Read-only; injection-safe by construction."""
    cfg = SOURCES.get(source)
    if cfg is None:
        return None, (
            f"unknown source {source!r}; valid sources: {sorted(SOURCES)}"
        )
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    model = cfg["model"]
    columns = cfg["columns"]
    q = select(*[getattr(model, c) for c in columns])

    applied: dict = {}
    for key, value in (filters or {}).items():
        if key not in cfg["filters"]:
            return None, (
                f"cannot filter {source!r} by {key!r}; filterable columns: "
                f"{list(cfg['filters'])}"
            )
        q = q.where(getattr(model, key) == value)  # bound param — injection-safe
        applied[key] = value

    q = q.order_by(getattr(model, cfg["order"]).desc()).limit(limit)
    rows = [
        {c: _cap_cell(v) for c, v in zip(columns, row, strict=True)}
        for row in session.execute(q).all()
    ]
    return {"source": source, "filters": applied, "count": len(rows), "rows": rows}, None


def record_read(
    session, *, agent_uuid: str, heartbeat_id: int | None, result: dict
) -> EvoSandboxRun:
    """Persist an inspect_data pull (reusing the sandbox-run record, kind='data_read')
    so the fetched rows resurface in the agent's next-heartbeat prompt."""
    run = EvoSandboxRun(
        agent_uuid=agent_uuid,
        heartbeat_id=heartbeat_id,
        kind="data_read",
        dataset=str(result.get("source", ""))[:64],
        params_json={"source": result.get("source"), "filters": result.get("filters")},
        result_json=result,
        rows_processed=int(result.get("count", 0) or 0),
    )
    session.add(run)
    session.flush()
    return run


def recent_reads(session, agent_uuid: str, *, limit: int = 4) -> list[dict]:
    """The agent's most recent inspect_data pulls, so the rows it fetched are visible
    in its NEXT heartbeat's prompt (action outcomes are not re-fed the same turn)."""
    rows = list(
        session.scalars(
            select(EvoSandboxRun)
            .where(
                EvoSandboxRun.agent_uuid == agent_uuid,
                EvoSandboxRun.kind == "data_read",
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
                "source": res.get("source") or r.dataset,
                "filters": res.get("filters") or {},
                "count": res.get("count"),
                "rows": (res.get("rows") or [])[:12],
            }
        )
    return out
