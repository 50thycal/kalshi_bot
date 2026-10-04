"""The liquidity-incentive book's own card section on the landing page.

The headline row a book gets (`overview.build_headline`) is a month of fills and settlements.
For this book that misses most of the story: its point is the REWARD for resting, which never
appears as a fill, and its losses are mostly older than the month. This block adds, read-only:

* **all time** — realized P&L on every market the book ever traded (settled position snapshots,
  after fees), reward credits, and the net of the two;
* **reward credits** — positive balance residuals from the reward ledger
  (`incentive_balance_observations`): cash no fill or settlement explains, excluding presumed
  deposits/withdrawals and readings the ledger itself marked untrustworthy. A residual is a
  candidate reward, not a certified one (`reward_ledger.py`), and the page says so;
* **what is resting now** — each working order with its market, side, price, size, dollars
  committed, age, its programme's pool and end, and an ESTIMATED reward per day from the
  published scoring rules (`liquidity_incentive.scoring`) on the collector's newest book.

Estimates are labelled as such everywhere: the probe found the scoring model reads far above
what the book has actually been paid (docs/LIMM_PLACEMENT_THESIS.md, L0).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import models as m
from ..liquidity_incentive import live as limm
from ..liquidity_incentive import programs as progs
from ..liquidity_incentive.scoring import discount_factor, order_multiplier

#: A collector snapshot older than this is too stale to estimate a reward from.
SNAPSHOT_FRESH = timedelta(hours=2)
WORKING = ("pending", "submitted", "resting", "partial")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def _r(v: float | None, places: int = 2) -> float | None:
    return None if v is None else round(float(v), places)


def _levels(raw) -> list[tuple[int, float]]:
    out = []
    for item in raw or []:
        try:
            out.append((int(item[0]), float(item[1])))
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda x: -x[0])
    return out


def _reference(levels: list[tuple[int, float]], target: float | None) -> int | None:
    """Scoring R3: first price where cumulative size from the best bid reaches Target/5."""
    if not levels or not target:
        return None
    need, cum = float(target) / 5.0, 0.0
    for price, qty in levels:
        cum += qty
        if cum >= need - 1e-9:
            return price
    return levels[-1][0]


def estimate_reward_per_day(order, program, snap) -> float | None:
    """Estimated reward dollars per day for ONE resting order, or None when it cannot be read.

    Our share of the side is our weighted size over the side's whole weighted book (the
    snapshot was taken with our order in it), and one side is half a participant's score."""
    if program is None or snap is None or program.period_reward_usd is None:
        return None
    start, end = _aware(program.start_date), _aware(program.end_date)
    if start is None or end is None or end <= start:
        return None
    if not (snap.est_yes_meets_target and snap.est_no_meets_target):
        return 0.0   # no qualifying snapshot pays anyone
    side = (order.side or "").lower()
    levels = _levels(snap.yes_levels_json if side == "yes" else snap.no_levels_json)
    field = float((snap.est_yes_score_total if side == "yes" else snap.est_no_score_total) or 0)
    ref = _reference(levels, float(program.target_size or 0))
    if ref is None or field <= 0:
        return None
    mult = order_multiplier(int(order.limit_price), ref, discount_factor(program.discount_factor_bps))
    ours = float(order.quantity or 0) * mult
    share = min(1.0, ours / field) if field else 0.0
    per_day = float(program.period_reward_usd) / ((end - start).total_seconds() / 86400.0)
    return per_day * share / 2.0


def _rewards(session) -> tuple[float, float, datetime | None]:
    """(cash in, cash out, last credit time) from the reward ledger's trustworthy residuals."""
    cash_in = cash_out = 0.0
    last_in = None
    for row in session.scalars(select(m.IncentiveBalanceObservation)):
        if row.residual_cents is None or row.presumed_transfer:
            continue
        if (row.notes_json or {}).get("residual_untrustworthy"):
            continue
        cents = int(row.residual_cents)
        if cents > 0:
            cash_in += cents / 100.0
            at = _aware(row.at)
            if last_in is None or (at and at > last_in):
                last_in = at
        elif cents < 0:
            cash_out += cents / 100.0
    return cash_in, cash_out, last_in


def build_incentive_block(session, book: str, now: datetime, latest_snapshots) -> dict:
    """The incentive section for one book. `latest_snapshots(session, tickers)` is the
    overview's own newest-position-per-ticker read, passed in so both agree."""
    orders = list(session.scalars(
        select(m.LiveOrder).where(m.LiveOrder.strategy == book, m.LiveOrder.action == "buy")
        .order_by(m.LiveOrder.created_at)))
    tickers = sorted({o.market_ticker for o in orders})
    snaps = latest_snapshots(session, tickers) if tickers else {}
    realized, settled = 0.0, 0
    for t in tickers:
        s = snaps.get(t)
        if s is not None and (s.quantity or 0) == 0 and s.realized_pnl is not None:
            realized += float(s.realized_pnl)
            settled += 1
    cash_in, cash_out, last_in = _rewards(session)

    working = [o for o in orders if (o.status or "").lower() in WORKING]
    by_ticker = {}
    for p in progs.current_programs(session, liquidity_only=True):
        if p.market_ticker in {o.market_ticker for o in working}:
            prev = by_ticker.get(p.market_ticker)
            if prev is None or (_aware(p.last_seen_at) or now) > (_aware(prev.last_seen_at) or now):
                by_ticker[p.market_ticker] = p
    resting = []
    est_total = 0.0
    est_known = 0
    for o in working:
        prog = by_ticker.get(o.market_ticker)
        snap = session.scalars(
            select(m.IncentiveMarketSnapshot)
            .where(m.IncentiveMarketSnapshot.market_ticker == o.market_ticker)
            .order_by(m.IncentiveMarketSnapshot.at.desc()).limit(1)).first()
        if snap is not None and (now - _aware(snap.at)) > SNAPSHOT_FRESH:
            snap = None
        est = estimate_reward_per_day(o, prog, snap)
        end = _aware(prog.end_date) if prog else None
        days_left = max(0.0, (end - now).total_seconds() / 86400.0) if end else None
        if est is not None:
            est_total += est
            est_known += 1
        price, qty = int(o.limit_price or 0), float(o.quantity or 0)
        resting.append({
            "market": o.market_ticker,
            "title": (prog.market_title if prog else None) or o.market_ticker,
            "side": o.side,
            "price_cents": price,
            "quantity": qty,
            "committed_usd": _r(price * qty / 100.0),
            "max_payout_usd": _r(qty),
            "age_minutes": int((now - _aware(o.created_at)).total_seconds() // 60),
            "pool_usd": _r(prog.period_reward_usd) if prog and prog.period_reward_usd is not None else None,
            "program_ends_at": _iso(end),
            "est_reward_per_day_usd": _r(est, 3),
            "est_reward_to_end_usd": _r(est * days_left, 2) if est is not None and days_left is not None else None,
            "status": o.status,
        })
    return {
        "realized_all_time_usd": _r(realized),
        "settled_markets_all_time": settled,
        "markets_traded_all_time": len(tickers),
        "reward_credits_usd": _r(cash_in),
        "unexplained_cash_out_usd": _r(cash_out),
        "last_reward_at": _iso(last_in),
        "net_all_time_usd": _r(realized + cash_in),
        "resting": resting,
        "committed_usd": _r(sum(r["committed_usd"] or 0 for r in resting)),
        "est_reward_per_day_usd": _r(est_total, 3) if est_known else None,
        "slots_used": len({r["market"] for r in resting}),
        "slots_max": limm.MAX_OPEN_ORDERS,
        "quote_mode": limm.QUOTE_MODE,
    }


def is_incentive_book(book: str) -> bool:
    return limm.owns_tag(book)
