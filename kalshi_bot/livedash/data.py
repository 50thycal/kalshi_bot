"""Payload builders for the live-vs-paper comparison dashboard.

One builder per API route. Every one of them is a pure read: this package contains
no INSERT, UPDATE or DELETE, and the server exposes only GET/HEAD, so nothing here
can place, cancel or alter an order, a position or a strategy setting.

Security posture (the page can be served at a public Railway URL): only structured
operational fields are returned. No credentials, environment values, exchange
request/response bodies, raw order/fill JSON, or stack traces. The one
configuration object that IS published — the twin's parameter snapshot, which is
what makes a param-drift warning readable — is filtered by `pairs.safe_params`.

Calculation provenance is a first-class output: `diagnostics` on the run payload
states, for each headline figure, which table it came from and which formula
produced it, so a displayed number can be traced back to its records.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import compare, legs, market_meta, pairs, series
from . import events as events_mod
from . import marks as marks_mod

# The default chart window when the caller does not ask for one. The epoch start
# always wins if the run is younger than this.
DEFAULT_WINDOW_HOURS = 72


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _load_marks(session, live_leg_tickers, paper_tickers, since, until):
    return marks_mod.MarkIndex.load(
        session, set(live_leg_tickers) | set(paper_tickers), since, until, side="no"
    )


def _pair_tickers(session, pair, since):
    """Every ticker either side touched in the epoch — the mark-loading universe."""
    from sqlalchemy import select

    from .. import models as m

    live = set(session.scalars(
        select(m.LiveOrder.market_ticker).where(
            m.LiveOrder.strategy == pair.live_tag, m.LiveOrder.created_at >= since
        ).distinct()
    ))
    paper = set(session.scalars(
        select(m.PaperTrade.market_ticker).where(
            m.PaperTrade.strategy == pair.twin_tag,
            m.PaperTrade.legacy.is_(False),
            m.PaperTrade.created_at >= since,
        ).distinct()
    ))
    return live, paper


def _market_tags(session, *ticker_groups) -> dict[str, dict]:
    """Human-readable category/subject/type tags for every ticker touched by a
    payload, keyed by ticker so the frontend joins once instead of the response
    repeating the same classification on every row that mentions a market."""
    tickers: set[str] = set()
    for group in ticker_groups:
        tickers.update(t for t in group if t)
    return {t: tag.to_dict() for t, tag in market_meta.classify_many(session, tickers).items()}


def load_run(session, pair, *, now: datetime | None = None):
    """Both legs plus the shared mark index — the common prelude to every route."""
    now = _now(now)
    since, until = pair.window(now)
    live_tickers, paper_tickers = _pair_tickers(session, pair, since)
    marks = _load_marks(session, live_tickers, paper_tickers, since, until)
    live = legs.live_leg(session, pair.live_tag, since, marks)
    paper = legs.paper_leg(session, pair.twin_tag, since, marks)
    return live, paper, marks


# ---------------------------------------------------------------------------
# /api/runs
# ---------------------------------------------------------------------------


def build_runs(session, *, now: datetime | None = None, limit: int = 50) -> dict:
    """The run selector: every paired run, plus live strategies that have no pair."""
    now = _now(now)
    rows = []
    for pair in pairs.list_pairs(session, limit=limit):
        live, paper, _marks = load_run(session, pair, now=now)
        live_s, paper_s = live.summary(), paper.summary()
        gap = None
        if live_s["realized_pnl_usd"] is not None and paper_s["realized_pnl_usd"] is not None:
            gap = round(paper_s["realized_pnl_usd"] - live_s["realized_pnl_usd"], 4)
        rows.append({
            **pair.to_dict(),
            "markets": max(live_s["positions_total"], paper_s["positions_total"]),
            "live_realized_usd": live_s["realized_pnl_usd"],
            "paper_realized_usd": paper_s["realized_pnl_usd"],
            "difference_usd": gap,
            "live_closed": live_s["positions_closed"],
            "paper_closed": paper_s["positions_closed"],
            "status": pairs.pair_status(session, pair, now=now),
        })
    return {
        "generated_at": now.isoformat(),
        "runs": rows,
        "unpaired_live_strategies": pairs.unpaired_live_strategies(session),
        "default_run": (pairs.default_pair(session).twin_tag
                        if pairs.default_pair(session) else None),
    }


# ---------------------------------------------------------------------------
# /api/runs/<twin_tag>
# ---------------------------------------------------------------------------


def build_run(session, twin_tag: str, *, now: datetime | None = None) -> dict | None:
    now = _now(now)
    pair = pairs.get_pair(session, twin_tag)
    if pair is None:
        return None
    live, paper, marks = load_run(session, pair, now=now)
    thresholds = compare.Thresholds.from_env()
    since, until = pair.window(now)
    divergence = compare.decompose(live, paper, thresholds)
    discrepancies = compare.discrepancies(live, paper, thresholds)

    return {
        "generated_at": now.isoformat(),
        "pair": pair.to_dict(),
        "status": pairs.pair_status(session, pair, now=now),
        "window": {"since": since.isoformat(), "until": until.isoformat(),
                   "hours": round((until - since).total_seconds() / 3600, 2)},
        "live": {**live.summary(), "positions": [p.to_dict() for p in live.positions]},
        "paper": {**paper.summary(), "positions": [p.to_dict() for p in paper.positions]},
        # What a naive comparison against the long-running incumbent book would have
        # claimed — shown precisely so it is never mistaken for the twin comparison.
        "incumbent_paper": legs.paper_leg(session, pair.live_tag, None, marks).summary(),
        "comparison": compare.compare_legs(live, paper, thresholds, now=now),
        "divergence": divergence,
        "discrepancies": discrepancies,
        "gates": events_mod.gate_breakdown(session, pair, now=now),
        "marks": marks.coverage(),
        "diagnostics": _diagnostics(pair, live, paper, marks, now),
        "market_tags": _market_tags(
            session,
            (p.ticker for p in live.positions), (p.ticker for p in paper.positions),
            (r["ticker"] for r in divergence["per_ticker"]),
            (d["ticker"] for d in discrepancies),
        ),
    }


def _diagnostics(pair, live, paper, marks, now: datetime) -> dict:
    """Where every headline number came from — table, formula and caveat."""
    return {
        "pairing": {
            "how": "explicit epoch row in live_paper_twins",
            "twin_tag": pair.twin_tag, "live_tag": pair.live_tag,
            "scope": "both legs are filtered to created_at >= live_paper_twins.started_at",
        },
        "live_realized_pnl_usd": {
            "value": live.summary()["realized_pnl_usd"],
            "source": "positions (newest snapshot per ticker, quantity = 0)",
            "formula": "the exchange's own realized_pnl_dollars, written by the reconcile "
                       "loop as revenue/100 - cost - fee_cost from /portfolio/settlements",
            "caveat": "net of the fees Kalshi actually charged — a different arithmetic "
                      "from the paper leg's, which is itself a divergence component",
        },
        "paper_realized_pnl_usd": {
            "value": paper.summary()["realized_pnl_usd"],
            "source": "paper_trades.pnl where status is terminal and NOT legacy",
            "formula": "quantity x (resolved_value - assumed_price) / 100 - entry_fee",
            "caveat": "settlement is free in the simulator; only the entry fee is charged",
        },
        "unrealized_pnl_usd": {
            "live": live.summary()["unrealized_pnl_usd"],
            "paper": paper.summary()["unrealized_pnl_usd"],
            "source": "derived here from mmsell_position_ticks (no-bid)",
            "formula": "quantity x (mark_no_bid - entry_price) / 100",
            "caveat": "positions.unrealized_pnl is never written by the live path, so this "
                      "is derived for BOTH legs off the SAME tick — that removes mark source "
                      "as an explanation for any gap",
        },
        "live_contracts": {
            "source": "fills joined to live_orders on kalshi_order_id",
            "caveat": "there is no strategy column on fills or positions; attribution runs "
                      "through live_orders.strategy",
        },
        "marks": {**marks.coverage(),
                  "as_of": marks.latest_at().isoformat() if marks.latest_at() else None,
                  "caveat": "a ticker is taped only while an mmsell paper book holds it; "
                            "uncovered tickers are excluded from a point, never valued at cost"},
        "generated_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# /api/runs/<twin_tag>/series
# ---------------------------------------------------------------------------


def build_series(
    session, twin_tag: str, *, since: datetime | None = None, until: datetime | None = None,
    ticker: str | None = None, now: datetime | None = None, max_points: int = series.MAX_POINTS,
) -> dict | None:
    now = _now(now)
    pair = pairs.get_pair(session, twin_tag)
    if pair is None:
        return None
    live, paper, marks = load_run(session, pair, now=now)
    epoch_start, epoch_end = pair.window(now)
    window_start = max(epoch_start, since) if since else max(
        epoch_start, epoch_end - timedelta(hours=DEFAULT_WINDOW_HOURS)
    )
    window_end = min(epoch_end, until) if until else epoch_end

    pnl = series.build_pnl_series(
        live, paper, marks, since=window_start, until=window_end, max_points=max_points
    )
    focus = ticker or series.pick_focus_ticker(live, paper)
    price = (series.build_price_series(
        session, focus, live, paper, since=window_start, until=window_end,
        max_points=max_points) if focus else None)
    available = sorted({p.ticker for p in live.positions} | {p.ticker for p in paper.positions})
    return {
        "generated_at": now.isoformat(),
        "twin_tag": pair.twin_tag,
        "pnl": pnl,
        "price": price,
        "available_tickers": available,
        "market_tags": _market_tags(session, available),
        "excursions": {
            "live": series.excursions(pnl["points"], "live"),
            "paper": series.excursions(pnl["points"], "paper"),
        },
    }


# ---------------------------------------------------------------------------
# /api/runs/<twin_tag>/orders
# ---------------------------------------------------------------------------


def build_orders(
    session, twin_tag: str, *, environment: str | None = None, limit: int = 100,
    offset: int = 0, now: datetime | None = None,
) -> dict | None:
    now = _now(now)
    pair = pairs.get_pair(session, twin_tag)
    if pair is None:
        return None
    since, _ = pair.window(now)
    rows: list[dict] = []
    if environment in (None, "live"):
        rows.extend(legs.live_orders(session, pair.live_tag, since))
    if environment in (None, "paper"):
        rows.extend(legs.paper_orders(session, pair.twin_tag, since))
    rows.sort(key=lambda r: (r["submitted_at"] or "", r["order_id"]), reverse=True)

    # Pair each order with its counterpart on the same market, so a row can be read
    # against the other environment without hunting for it.
    by_market: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        by_market.setdefault(row["market"], {}).setdefault(row["environment"], []).append(row)
    for row in rows:
        others = by_market[row["market"]].get(
            "paper" if row["environment"] == "live" else "live", [])
        row["paired_order_id"] = others[0]["order_id"] if others else None
        row["unpaired"] = not others

    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    page = rows[offset:offset + limit]
    return {
        "generated_at": now.isoformat(),
        "twin_tag": pair.twin_tag,
        "orders": page,
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(rows),
        "environment": environment,
        "market_tags": _market_tags(session, (r["market"] for r in page)),
    }


# ---------------------------------------------------------------------------
# /api/runs/<twin_tag>/events
# ---------------------------------------------------------------------------


def build_events(
    session, twin_tag: str, *, category: str = events_mod.DEFAULT_CATEGORY,
    environment: str | None = None, limit: int = events_mod.DEFAULT_LIMIT,
    cursor: str | None = None, now: datetime | None = None,
) -> dict | None:
    now = _now(now)
    pair = pairs.get_pair(session, twin_tag)
    if pair is None:
        return None
    payload = events_mod.fetch_events(
        session, pair, category=category, environment=environment,
        limit=limit, cursor=cursor, now=now,
    )
    payload["twin_tag"] = pair.twin_tag
    payload["generated_at"] = now.isoformat()
    payload["market_tags"] = _market_tags(session, (e.get("ticker") for e in payload["events"]))
    return payload
