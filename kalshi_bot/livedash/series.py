"""Historical P&L and price series for a paired run.

**Nothing here is fabricated.** Every point is computed from records that existed at
that instant:

* positions and their real entry prices, sizes and entry times (live fills /
  paper trades),
* settlement times and realized P&L as each system booked them,
* and `mmsell_position_ticks` — the per-cycle orderbook capture the tracker
  already writes — read strictly **as of** each point in time.

The current price is never applied retroactively: `MarkIndex.mark_as_of` only ever
returns a tick at or before the point being computed. Where a ticker has no tick at
or before a point, that position is *excluded from that point* and the point is
flagged `partial`, so a data gap reads as a gap rather than as a P&L move.

Both legs are valued off the same tick at the same instant, which is what makes the
difference series attributable to execution rather than to marking.

Coverage begins when the tick capture began; it is not backfilled.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from .. import models as m

MAX_POINTS = 300


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _sample(stamps: list[datetime], max_points: int) -> list[datetime]:
    """Thin an arbitrarily long tick timeline to a bounded number of points, always
    keeping the first and last so the window's endpoints are exact."""
    if len(stamps) <= max_points:
        return stamps
    step = len(stamps) / float(max_points)
    picked = [stamps[min(len(stamps) - 1, int(i * step))] for i in range(max_points)]
    if picked[-1] != stamps[-1]:
        picked[-1] = stamps[-1]
    out: list[datetime] = []
    for s in picked:
        if not out or s != out[-1]:
            out.append(s)
    return out


def _leg_point(positions, marks, when: datetime) -> tuple[float, float, float, bool]:
    """(realized, unrealized, open_entry_fees, complete) for one leg at `when`."""
    realized = 0.0
    unrealized = 0.0
    open_fees = 0.0
    complete = True
    for pos in positions:
        entry = _aware(pos.entry_at)
        if entry is None or entry > when:
            continue  # the position did not exist yet
        exit_at = _aware(pos.exit_at)
        if exit_at is not None and exit_at <= when:
            realized += pos.realized_pnl_usd or 0.0
            continue
        if pos.status == "unfilled":
            continue  # an order that never filled carries no P&L
        if pos.entry_price_cents is None or not pos.quantity:
            continue
        mark = marks.mark_as_of(pos.ticker, when)
        if mark is None:
            complete = False  # a gap is reported, never valued at the entry price
            continue
        unrealized += pos.quantity * (mark.price_cents - pos.entry_price_cents) / 100.0
        open_fees += pos.entry_fees_usd or 0.0
    return realized, unrealized, open_fees, complete


def build_pnl_series(
    live, paper, marks, *, since: datetime, until: datetime | None = None,
    max_points: int = MAX_POINTS,
) -> dict:
    """Overlaid live and paper P&L over the epoch, plus their difference.

    `gross` = realized + unrealized. `net` additionally subtracts the entry fees
    still sitting in open positions; realized already nets each side's own fees, so
    subtracting them again would double-count.
    """
    until = until or datetime.now(timezone.utc)
    stamps = [s for s in marks.timeline() if since <= s <= until]
    if not stamps:
        return {
            "points": [], "markers": [], "coverage": marks.coverage(),
            "since": since.isoformat(), "until": until.isoformat(),
            "note": "no market ticks in this window yet — the series begins when the "
                    "orderbook capture has data for a held market",
        }
    stamps = _sample(stamps, max_points)

    points = []
    for when in stamps:
        l_real, l_unreal, l_fees, l_ok = _leg_point(live.positions, marks, when)
        p_real, p_unreal, p_fees, p_ok = _leg_point(paper.positions, marks, when)
        live_gross = l_real + l_unreal
        paper_gross = p_real + p_unreal
        points.append({
            "at": when.isoformat(),
            "live_realized": round(l_real, 4),
            "live_unrealized": round(l_unreal, 4),
            "live_gross": round(live_gross, 4),
            "live_net": round(live_gross - l_fees, 4),
            "paper_realized": round(p_real, 4),
            "paper_unrealized": round(p_unreal, 4),
            "paper_gross": round(paper_gross, 4),
            "paper_net": round(paper_gross - p_fees, 4),
            "difference": round(paper_gross - live_gross, 4),
            "partial": not (l_ok and p_ok),
        })
    return {
        "points": points,
        "markers": _markers(live, paper, since),
        "coverage": marks.coverage(),
        "since": since.isoformat(),
        "until": until.isoformat(),
        "note": "difference = paper gross minus live gross; both legs are marked off the "
                "same tick, so it isolates execution rather than valuation",
    }


def _markers(live, paper, since: datetime) -> list[dict]:
    """Annotations for the chart: run start, entries and exits on each side."""
    out = [{"at": since.isoformat(), "kind": "run_start", "label": "Paired run started",
            "environment": "both"}]
    for leg, env in ((live, "live"), (paper, "paper")):
        for pos in leg.positions:
            if pos.status == "unfilled":
                if pos.entry_at:
                    out.append({"at": _aware(pos.entry_at).isoformat(), "kind": "unfilled_order",
                                "label": f"{pos.ticker} order never filled", "environment": env,
                                "ticker": pos.ticker})
                continue
            if pos.entry_at:
                out.append({"at": _aware(pos.entry_at).isoformat(), "kind": "entry",
                            "label": f"{pos.ticker} entry", "environment": env,
                            "ticker": pos.ticker})
            if pos.exit_at:
                out.append({"at": _aware(pos.exit_at).isoformat(), "kind": "exit",
                            "label": f"{pos.ticker} settled", "environment": env,
                            "ticker": pos.ticker,
                            "pnl_usd": round(pos.realized_pnl_usd or 0.0, 4)})
    out.sort(key=lambda mk: mk["at"])
    return out


# ---------------------------------------------------------------------------
# Price / execution chart
# ---------------------------------------------------------------------------


def pick_focus_ticker(live, paper) -> str | None:
    """The most instructive market to chart: prefer one where the two sides
    disagreed (paper filled, live did not), then any market both held, then any."""
    live_by = {p.ticker: p for p in live.positions}
    paper_by = {p.ticker: p for p in paper.positions}
    missed = [t for t, p in paper_by.items()
              if t not in live_by or live_by[t].status == "unfilled"]
    if missed:
        return sorted(missed)[0]
    shared = sorted(set(live_by) & set(paper_by))
    if shared:
        return shared[0]
    return next(iter(sorted(paper_by)), None) or next(iter(sorted(live_by)), None)


def build_price_series(
    session, ticker: str, live, paper, *, since: datetime, until: datetime | None = None,
    max_points: int = MAX_POINTS,
) -> dict:
    """One market's order book over the run, with both sides' execution marked.

    This is the chart that answers "would that fill have happened?" — the paper
    assumed price sits on the same axis as the real book and the real fill.
    """
    until = until or datetime.now(timezone.utc)
    rows = list(session.scalars(
        select(m.MmSellPositionTick).where(
            m.MmSellPositionTick.market_ticker == ticker,
            m.MmSellPositionTick.captured_at >= since,
            m.MmSellPositionTick.captured_at <= until,
        ).order_by(m.MmSellPositionTick.captured_at)
    ))
    stamps = [_aware(r.captured_at) for r in rows]
    keep = set(_sample(stamps, max_points)) if stamps else set()
    points = [
        {
            "at": _aware(r.captured_at).isoformat(),
            "no_bid": r.no_bid, "no_ask": r.no_ask,
            "yes_bid": r.yes_bid, "yes_ask": r.yes_ask,
            "yes_mid": r.mid,
        }
        for r in rows if _aware(r.captured_at) in keep
    ]

    events: list[dict] = []
    for order in session.scalars(
        select(m.LiveOrder).where(
            m.LiveOrder.market_ticker == ticker, m.LiveOrder.created_at >= since
        ).order_by(m.LiveOrder.created_at)
    ):
        events.append({
            "at": _aware(order.created_at).isoformat(), "kind": "live_order",
            "label": f"live order {order.status}", "price_cents": order.limit_price,
            "status": order.status, "environment": "live",
        })
    live_pos = next((p for p in live.positions if p.ticker == ticker), None)
    if live_pos is not None and live_pos.fill_count and live_pos.entry_at:
        events.append({
            "at": _aware(live_pos.entry_at).isoformat(), "kind": "live_fill",
            "label": "live fill", "price_cents": live_pos.entry_price_cents,
            "status": "filled", "environment": "live",
        })
    paper_pos = next((p for p in paper.positions if p.ticker == ticker), None)
    if paper_pos is not None and paper_pos.entry_at:
        events.append({
            "at": _aware(paper_pos.entry_at).isoformat(), "kind": "paper_fill",
            "label": "paper assumed fill", "price_cents": paper_pos.entry_price_cents,
            "status": "assumed", "environment": "paper",
        })
    for pos, env in ((live_pos, "live"), (paper_pos, "paper")):
        if pos is not None and pos.exit_at:
            events.append({
                "at": _aware(pos.exit_at).isoformat(), "kind": "settle", "label": "settled",
                "price_cents": pos.resolved_value_cents, "status": pos.status,
                "environment": env,
            })
    events.sort(key=lambda e: e["at"])
    return {
        "ticker": ticker,
        "points": points,
        "events": events,
        "point_count": len(rows),
        "note": "NO-side book from mmsell_position_ticks; the paper marker is the price the "
                "twin ASSUMED it rested and filled at, not an observed trade",
    }


# ---------------------------------------------------------------------------
# Excursion metrics (only from the same honest series)
# ---------------------------------------------------------------------------


def excursions(points: list[dict], prefix: str) -> dict:
    """Max favourable / adverse excursion, drawdown and time above water for one
    leg, computed over the reconstructed series. Returns nulls (not zeros) when the
    series is too short to mean anything."""
    values = [p[f"{prefix}_gross"] for p in points if not p["partial"]]
    if len(values) < 2:
        return {"max_favorable_usd": None, "max_adverse_usd": None,
                "max_drawdown_usd": None, "time_profitable_pct": None, "samples": len(values)}
    peak = values[0]
    drawdown = 0.0
    for v in values:
        peak = max(peak, v)
        drawdown = min(drawdown, v - peak)
    return {
        "max_favorable_usd": round(max(values), 4),
        "max_adverse_usd": round(min(values), 4),
        "max_drawdown_usd": round(drawdown, 4),
        "time_profitable_pct": round(sum(1 for v in values if v > 0) / len(values) * 100, 1),
        "samples": len(values),
    }
