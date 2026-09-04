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

import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import compare, legs, market_meta, pairs, series
from . import events as events_mod
from . import marks as marks_mod

# The default chart window when the caller does not ask for one. The epoch start
# always wins if the run is younger than this.
DEFAULT_WINDOW_HOURS = 72

# How much of the tick tape a route needs. Reading more than it needs is what made this
# page take half a minute to open, so each route now says.
#
#   FULL    every tick in the epoch. Only the P&L series needs it, because only it asks
#           what a position was worth at a past instant.
#   LATEST  one mark per ticker — enough to value what is open right now, which is all
#           the run page does with marks.
#   NONE    no marks at all. Realized P&L, position counts and the closed/open split are
#           read from settlements and snapshots and never touch a tick, so the run LIST,
#           which shows nothing else, loads none.
MARKS_FULL = "full"
MARKS_LATEST = "latest"
MARKS_NONE = "none"

logger = logging.getLogger("kalshi_bot.livedash")


class Stages:
    """Wall clock per stage of one payload build, logged as a single line.

    The server can say a route took thirty seconds; it cannot say which of the
    eight things that route does spent them. Reading the code does not settle it
    either — the run page and the P&L series share almost all of their loading,
    and the expensive half is not the half either one looks like it should be.

    Cost is `perf_counter` around work that is already doing database round trips,
    so it is free at this resolution, and it is emitted at INFO on every build
    rather than behind a flag: a slow page that has to be reproduced under a debug
    setting before it can be measured is a slow page nobody measures."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.timings: dict[str, float] = {}
        self._started = time.perf_counter()
        _in_flight.stages = self

    @contextmanager
    def stage(self, name: str):
        began = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = self.timings.get(name, 0.0) + (
                time.perf_counter() - began) * 1000

    def total_ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000

    def log(self) -> None:
        _in_flight.stages = None
        parts = " ".join(f"{name}={ms:.0f}ms" for name, ms in
                         sorted(self.timings.items(), key=lambda kv: -kv[1]))
        logger.info("livedash stages %s total=%.0fms %s",
                    self.label, self.total_ms(), parts)


#: The build running on this thread, until it logs itself. One handler thread runs
#: one build, so a plain thread-local is the whole of the bookkeeping needed.
_in_flight = threading.local()


def log_pending_stages() -> None:
    """Emit the stage line for a build that raised before reaching its own `log()`.

    Without this the one route that FAILS is the one route whose timings are lost,
    which is exactly backwards: a build that got most of the way and then threw has
    already said where it spent its time, and that is what says how far it got. The
    server calls this from its error path.
    """
    stages = getattr(_in_flight, "stages", None)
    if stages is not None:
        stages.log()


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


def load_run(session, pair, *, now: datetime | None = None, marks: str = MARKS_LATEST,
             stages: Stages | None = None):
    """Both legs plus the shared mark index — the common prelude to every route.

    `marks` is how much of the tape to read (see MARKS_*). It never changes what a
    number means, only whether the route paid to load evidence it does not use: an
    unrealized figure is still derived for both legs off the SAME mark, and a leg with
    an open position and no usable mark still reports its unrealized P&L as
    unmeasurable rather than as zero.
    """
    now = _now(now)
    since, until = pair.window(now)
    stages = stages or Stages("load_run")
    with stages.stage(f"marks[{marks}]"):
        index = _marks(session, pair, since, until, marks)
    with stages.stage("live_leg"):
        live = legs.live_leg(session, pair.live_tag, since, index)
    with stages.stage("paper_leg"):
        paper = legs.paper_leg(session, pair.twin_tag, since, index)
    return live, paper, index


def _marks(session, pair, since, until, scope: str):
    if scope == MARKS_NONE:
        # Not "no marks were found" — nothing on this route is marked at all. The empty
        # index reports 0 requested, so coverage stays honest about what was asked for.
        return marks_mod.MarkIndex(side="no", scope=marks_mod.SCOPE_NONE)
    live_tickers, paper_tickers = _pair_tickers(session, pair, since)
    if scope == MARKS_FULL:
        return _load_marks(session, live_tickers, paper_tickers, since, until)
    return marks_mod.MarkIndex.load_latest(
        session, set(live_tickers) | set(paper_tickers), since, until, side="no"
    )


# ---------------------------------------------------------------------------
# /api/runs
# ---------------------------------------------------------------------------


def build_runs(
    session, *, now: datetime | None = None, limit: int = 50, summaries: bool = True,
) -> dict:
    """Every paired run, plus live strategies that have no pair.

    This answers two questions with very different costs, so it takes a parameter
    rather than always paying the higher one. `summaries=False` returns the pairs
    alone — which tags, when they started, whether they ended — and that is the whole
    of what the run PICKER needs. `summaries=True` adds the per-run P&L columns the
    history table shows, and reconstructing both legs of every recorded run to get
    them is the most expensive read on this dashboard.

    Splitting them is what lets the page put a working selector in front of an
    operator immediately and fill the table in behind it, instead of showing nothing
    at all until the last retired run from months ago has been rebuilt.
    """
    now = _now(now)
    stages = Stages(f"build_runs summaries={summaries}")
    with stages.stage("list_pairs"):
        all_pairs = pairs.list_pairs(session, limit=limit)
    with stages.stage(f"rows[{len(all_pairs)}]"):
        rows = [_run_row(session, pair, now) if summaries else pair.to_dict()
                for pair in all_pairs]
    default = pairs.default_from(all_pairs)
    payload = {
        "generated_at": now.isoformat(),
        "runs": rows,
        "summaries": summaries,
        "default_run": default.twin_tag if default else None,
    }
    if summaries:
        # An unpaired live strategy is a real-money book running with nothing to compare
        # it against, so it is never hidden — but finding them scans every live order,
        # which belongs with the rest of the heavy read rather than in front of the
        # selector.
        with stages.stage("unpaired"):
            payload["unpaired_live_strategies"] = pairs.unpaired_live_strategies(session)
    stages.log()
    return payload


def _run_row(session, pair, now: datetime) -> dict:
    """One history-table row. Realized P&L, the open/closed split and the market count
    all come from settlements and snapshots, so this loads no marks at all — the run
    list never displayed an unrealized figure, and reading the tape to compute one it
    would throw away was pure cost."""
    live, paper, _marks = load_run(session, pair, now=now, marks=MARKS_NONE)
    live_s, paper_s = live.summary(), paper.summary()
    gap = None
    if live_s["realized_pnl_usd"] is not None and paper_s["realized_pnl_usd"] is not None:
        gap = round(paper_s["realized_pnl_usd"] - live_s["realized_pnl_usd"], 4)
    return {
        **pair.to_dict(),
        "markets": max(live_s["positions_total"], paper_s["positions_total"]),
        "live_realized_usd": live_s["realized_pnl_usd"],
        "paper_realized_usd": paper_s["realized_pnl_usd"],
        "difference_usd": gap,
        "live_closed": live_s["positions_closed"],
        "paper_closed": paper_s["positions_closed"],
        "status": pairs.pair_status(session, pair, now=now),
    }


# ---------------------------------------------------------------------------
# /api/runs/<twin_tag>
# ---------------------------------------------------------------------------


def build_run(
    session, twin_tag: str, *, now: datetime | None = None, incumbent: bool = False,
) -> dict | None:
    now = _now(now)
    stages = Stages(f"build_run {twin_tag}")
    with stages.stage("get_pair"):
        pair = pairs.get_pair(session, twin_tag)
    if pair is None:
        return None
    live, paper, marks = load_run(session, pair, now=now, stages=stages)
    thresholds = compare.Thresholds.from_env()
    since, until = pair.window(now)
    with stages.stage("decompose"):
        divergence = compare.decompose(live, paper, thresholds)
    with stages.stage("discrepancies"):
        discrepancies = compare.discrepancies(live, paper, thresholds)
    with stages.stage("status"):
        status = pairs.pair_status(session, pair, now=now)
    with stages.stage("incumbent"):
        incumbent_paper = (
            legs.paper_leg(session, pair.live_tag, None, marks).summary()
            if incumbent else None
        )
    with stages.stage("compare_legs"):
        comparison = compare.compare_legs(live, paper, thresholds, now=now)
    with stages.stage("gates"):
        gates = events_mod.gate_breakdown(session, pair, now=now)
    with stages.stage("market_tags"):
        market_tags = _market_tags(
            session,
            (p.ticker for p in live.positions), (p.ticker for p in paper.positions),
            (r["ticker"] for r in divergence["per_ticker"]),
            (d["ticker"] for d in discrepancies),
        )
    stages.log()

    return {
        "generated_at": now.isoformat(),
        "pair": pair.to_dict(),
        "status": status,
        "window": {"since": since.isoformat(), "until": until.isoformat(),
                   "hours": round((until - since).total_seconds() / 3600, 2)},
        "live": {**live.summary(), "positions": [p.to_dict() for p in live.positions]},
        "paper": {**paper.summary(), "positions": [p.to_dict() for p in paper.positions]},
        # What a naive comparison against the long-running incumbent book would have
        # claimed — carried precisely so it is never mistaken for the twin comparison.
        # It is the one figure here with no window: the incumbent's WHOLE history, which
        # on a book that has run for months is more paper trades than everything else on
        # this page put together. So it is computed when asked for, and the page asks
        # when the provenance section is opened rather than on every load.
        "incumbent_paper": incumbent_paper,
        "comparison": comparison,
        "divergence": divergence,
        "discrepancies": discrepancies,
        "gates": gates,
        "marks": marks.coverage(),
        "diagnostics": _diagnostics(pair, live, paper, marks, now),
        "market_tags": market_tags,
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
    stages = Stages(f"build_series {twin_tag}")
    live, paper, marks = load_run(session, pair, now=now, marks=MARKS_FULL, stages=stages)
    epoch_start, epoch_end = pair.window(now)
    window_start = max(epoch_start, since) if since else max(
        epoch_start, epoch_end - timedelta(hours=DEFAULT_WINDOW_HOURS)
    )
    window_end = min(epoch_end, until) if until else epoch_end

    with stages.stage("pnl_series"):
        pnl = series.build_pnl_series(
            live, paper, marks, since=window_start, until=window_end, max_points=max_points
        )
    focus = ticker or series.pick_focus_ticker(live, paper)
    with stages.stage("price_series"):
        price = (series.build_price_series(
            session, focus, live, paper, since=window_start, until=window_end,
            max_points=max_points) if focus else None)
    available = sorted({p.ticker for p in live.positions} | {p.ticker for p in paper.positions})
    stages.log()
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
