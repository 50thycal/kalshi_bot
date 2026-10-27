"""The liquidity-incentive book's own card section on the landing page.

The headline row a book gets (`overview.build_headline`) is a month of fills and settlements.
For this book that misses most of the story: its point is the REWARD for resting, which never
appears as a fill, and its losses are mostly older than the month. This block adds, read-only:

* **all time** — realized P&L on every market the book ever traded (settled position snapshots,
  after fees), reward credits, and the net of the two;
* **reward credits** — balance residuals from the reward ledger
  (`incentive_balance_observations`) that match a payout on Kalshi's Rewards page
  (`reward_ledger.CONFIRMED_REWARD_PAYOUTS`, §9.51). A residual that only fits the §9.49
  attribution test is reported apart as unconfirmed and never added to the net;
* **positions held** — every market where a bid actually FILLED and is not yet flat: side,
  contracts, entry price, dollars at risk, the stop-loss and take-profit prices the runner will
  act on (`liquidity_incentive.live`), and the resting exit order with what it locks if it fills;
* **bids waiting to fill** — each working ENTRY bid with its market, side, price, size, dollars
  committed, age, its programme's pool and end, and an ESTIMATED reward per day from the
  published scoring rules (`liquidity_incentive.scoring`) on the collector's newest book.

Money at risk counts held positions at cost plus entry bids at limit x size. An exit order is
shown but counts nothing: it can only net a held position flat (thesis §9.53).

Estimates are labelled as such everywhere: the probe found the scoring model reads far above
what the book has actually been paid (docs/LIMM_PLACEMENT_THESIS.md, L0).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import models as m
from .. import repository as repo
from ..liquidity_incentive import live as limm
from ..liquidity_incentive import programs as progs
from ..liquidity_incentive.reward_ledger import attributable_reward, confirmed_reward
from ..liquidity_incentive.scoring import discount_factor, order_multiplier

#: A collector snapshot older than this is too stale to estimate a reward from.
SNAPSHOT_FRESH = timedelta(hours=2)
WORKING = ("pending", "submitted", "resting", "partial")
#: Client-order-id prefixes of this book's closing orders: the resting opposite-side exit bid,
#: and a marketable sell in flight (`LiveExecutor.place_incentive_exit_leg` / `close_incentive_position`).
EXIT_LEG_PREFIX = "limmexitleg:"
EXIT_SELL_PREFIX = "limmexit:"
#: §9.60: the resting post-only sell of a held leg (`LiveExecutor.place_incentive_resting_sell`).
RESTING_SELL_PREFIX = limm.RESTING_SELL_PREFIX


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


def _program_ends(session, tickers: list[str]) -> list[datetime]:
    """End times of the liquidity programmes on markets this book quoted."""
    if not tickers:
        return []
    rows = session.scalars(select(m.IncentiveProgram.end_date).where(
        m.IncentiveProgram.market_ticker.in_(tickers),
        m.IncentiveProgram.incentive_type == "liquidity",
        m.IncentiveProgram.end_date.is_not(None)))
    return sorted({_aware(e) for e in rows})


def _trusted(row) -> bool:
    return row.residual_cents is not None and not (row.notes_json or {}).get(
        "residual_untrustworthy")


def _confirmed_credit(row) -> bool:
    """A balance step that is a payout listed on Kalshi's Rewards page (see `_rewards`)."""
    return _trusted(row) and int(row.residual_cents) > 0 and confirmed_reward(
        int(row.residual_cents), _aware(row.at))


def reward_credits(session, since: datetime) -> list[tuple[datetime, float]]:
    """Confirmed reward payouts that reached the balance at or after `since`, as (when, dollars).

    The landing page counts these toward the month's goal (DEC-039): a reward is realized cash
    the same as a settlement. Dated by when the balance moved, not by Kalshi's local payout date.
    Unconfirmed credits are never included."""
    rows = session.scalars(select(m.IncentiveBalanceObservation)
                           .where(m.IncentiveBalanceObservation.at >= since)
                           .order_by(m.IncentiveBalanceObservation.at))
    return [(_aware(r.at), int(r.residual_cents) / 100.0) for r in rows if _confirmed_credit(r)]


def _rewards(session, tickers: list[str] | None = None
             ) -> tuple[float, float, float, datetime | None]:
    """(rewards, unconfirmed, cash out, last reward time) from the reward ledger.

    A reward is a residual matching a payout on Kalshi's Rewards page
    (`reward_ledger.confirmed_reward`, §9.51). A presumed transfer that only passes the §9.49
    attribution test is UNCONFIRMED and kept apart: on 2026-10-06 that test counted $2.75 that
    Kalshi never paid. Kalshi pays nothing under $1, so cent-scale positive residuals are
    accounting noise, not rewards; negative ones stay the unexplained cash out."""
    cash_in = unconfirmed = cash_out = 0.0
    last_in = None
    ends = _program_ends(session, tickers or [])
    for row in session.scalars(select(m.IncentiveBalanceObservation)):
        if not _trusted(row):
            continue
        cents, at = int(row.residual_cents), _aware(row.at)
        if _confirmed_credit(row):
            cash_in += cents / 100.0
            if last_in is None or (at and at > last_in):
                last_in = at
        elif cents > 0 and row.presumed_transfer and attributable_reward(cents, at, ends):
            unconfirmed += cents / 100.0
        elif cents < 0 and not row.presumed_transfer:
            cash_out += cents / 100.0
    return cash_in, unconfirmed, cash_out, last_in


def _own_position(session, ticker: str, ours: list) -> tuple[float, dict]:
    """This book's net contracts on `ticker` from ITS fills (+YES / -NO) and the fill-weighted
    entry price per side — the same reading the runner's position manager acts on."""
    by_koid = {r.kalshi_order_id: r for r in ours if r.kalshi_order_id}
    net, cost, size = 0.0, {}, {}
    for f in repo.fills_for_ticker(session, ticker):
        row = by_koid.get(f.kalshi_order_id)
        if row is None or not f.quantity:
            continue
        q = float(f.quantity)
        if row.action == "buy":
            net += q if row.side == limm.SIDE_YES else -q
            if row.limit_price is not None:
                cost[row.side] = cost.get(row.side, 0.0) + q * float(row.limit_price)
                size[row.side] = size.get(row.side, 0.0) + q
        else:
            net += -q if row.side == limm.SIDE_YES else q
    entry = {side: int(round(cost[side] / size[side])) for side in cost if size.get(side)}
    return net, entry


def _latest_book(session, ticker: str, now: datetime):
    """The newest market snapshot for `ticker`, or None when it is older than SNAPSHOT_FRESH."""
    snap = session.scalars(
        select(m.IncentiveMarketSnapshot)
        .where(m.IncentiveMarketSnapshot.market_ticker == ticker)
        .order_by(m.IncentiveMarketSnapshot.at.desc()).limit(1)).first()
    if snap is not None and (now - _aware(snap.at)) > SNAPSHOT_FRESH:
        return None
    return snap


def _position_row(session, ticker: str, ours: list, snap, prog, now: datetime) -> dict | None:
    """One held position with its exit plan, or None when this book holds nothing there."""
    # §9.71: a bid cancelled after it part-filled still holds what it bought.
    if not any((o.status or "").lower() in ("filled", "partial")
               or ((o.status or "").lower() == "canceled"
                   and o.cancel_reason in repo.PART_FILLED_CANCEL_REASONS) for o in ours):
        return None
    if snap is not None and abs(float(snap.quantity_fp if snap.quantity_fp is not None
                                     else snap.quantity or 0)) <= 0.01:
        return None   # flat or settled
    net, entry_by_side = _own_position(session, ticker, ours)
    if abs(net) < 1:
        return None
    side = limm.SIDE_YES if net > 0 else limm.SIDE_NO
    entry = entry_by_side.get(side)
    if entry is None:
        return None
    qty = int(round(abs(net)))
    stop = entry - limm.stop_distance_cents(entry)
    tp = entry + limm.take_profit_distance_cents(entry)
    working = [o for o in ours if (o.status or "").lower() in WORKING]
    leg = next((o for o in working if (o.client_order_id or "").startswith(EXIT_LEG_PREFIX)), None)
    closing = any((o.client_order_id or "").startswith(EXIT_SELL_PREFIX) for o in working)
    exit_order = None
    sell = next((o for o in working
                 if (o.client_order_id or "").startswith(RESTING_SELL_PREFIX)), None)
    if sell is not None and sell.limit_price is not None:
        sq = int(sell.quantity or 0)
        exit_order = {"side": sell.side, "price_cents": int(sell.limit_price), "quantity": sq,
                      "kind": "resting_sell",
                      "locks_usd": _r((int(sell.limit_price) - entry) * sq / 100.0)}
    elif leg is not None and leg.limit_price is not None:
        lq = int(leg.quantity or 0)
        exit_order = {"side": leg.side, "price_cents": int(leg.limit_price), "quantity": lq,
                      "locks_usd": _r((100 - entry - int(leg.limit_price)) * lq / 100.0)}
    book = _latest_book(session, ticker, now)
    mark = ask = None
    no_bid = False
    if book is not None:
        mark = book.best_yes_bid if side == limm.SIDE_YES else book.best_no_bid
        other = book.best_no_bid if side == limm.SIDE_YES else book.best_yes_bid
        # The cheapest our side can be bought for: the other side's best bid, mirrored.
        ask = 100 - int(other) if other is not None else None
        if mark is None and book.book_valid:
            # A live book with nobody bidding our side: it cannot be sold right now, so it is
            # worth 0, not "unpriced" (TSWIF, 2026-10-15: no YES bids, NO bid 99).
            mark, no_bid = 0, True
    close = repo.market_close_time(session, ticker)
    return {
        "market": ticker,
        "title": (prog.market_title if prog else None) or ticker,
        "side": side,
        "quantity": qty,
        "entry_cents": entry,
        # What the held side could be sold for now (best bid), from the book's own snapshot.
        "mark_cents": mark,
        "value_usd": _r(mark * qty / 100.0) if mark is not None else None,
        "unrealized_usd": _r((mark - entry) * qty / 100.0) if mark is not None else None,
        "marked_at": _iso(book.at) if book is not None else None,
        "no_bid": no_bid,
        "ask_cents": ask,
        # Closed and waiting for settlement: no book to price it, and none is coming.
        "awaiting_settlement": book is None and close is not None and close <= now,
        "at_risk_usd": _r(entry * qty / 100.0),
        "take_profit_cents": tp,
        "take_profit_usd": _r((tp - entry) * qty / 100.0),
        # A 1c entry cannot be stopped: the bid cannot go below 0, and the most it can lose is
        # what it cost.
        "stop_cents": stop if stop >= 1 else None,
        "stop_usd": _r((stop - entry) * qty / 100.0) if stop >= 1 else _r(-entry * qty / 100.0),
        "exit_order": exit_order,
        "closing": closing,
        "program_ends_at": _iso(_aware(prog.end_date)) if prog else None,
    }


def build_incentive_block(session, book: str, now: datetime, latest_snapshots) -> dict:
    """The incentive section for one book. `latest_snapshots(session, tickers)` is the
    overview's own newest-position-per-ticker read, passed in so both agree."""
    every = list(session.scalars(
        select(m.LiveOrder).where(m.LiveOrder.strategy == book)
        .order_by(m.LiveOrder.created_at)))
    orders = [o for o in every if o.action == "buy"]
    tickers = sorted({o.market_ticker for o in orders})
    snaps = latest_snapshots(session, tickers) if tickers else {}
    realized, settled = 0.0, 0
    for t in tickers:
        s = snaps.get(t)
        if s is not None and (s.quantity or 0) == 0 and s.realized_pnl is not None:
            realized += float(s.realized_pnl)
            settled += 1
    cash_in, unconfirmed, cash_out, last_in = _rewards(session, tickers)

    working = [o for o in orders if (o.status or "").lower() in WORKING
               and not (o.client_order_id or "").startswith(
                   (EXIT_LEG_PREFIX, RESTING_SELL_PREFIX))]
    by_ticker = {}
    for p in progs.current_programs(session, liquidity_only=True):
        if p.market_ticker in set(tickers):
            prev = by_ticker.get(p.market_ticker)
            if prev is None or (_aware(p.last_seen_at) or now) > (_aware(prev.last_seen_at) or now):
                by_ticker[p.market_ticker] = p

    ours_by_ticker: dict[str, list] = {}
    for o in every:
        ours_by_ticker.setdefault(o.market_ticker, []).append(o)
    positions = []
    for t in tickers:
        row = _position_row(session, t, ours_by_ticker.get(t, []), snaps.get(t), by_ticker.get(t),
                            now)
        if row is not None:
            positions.append(row)

    resting = []
    est_total = 0.0
    est_known = 0
    for o in working:
        prog = by_ticker.get(o.market_ticker)
        snap = _latest_book(session, o.market_ticker, now)
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
            "days_left": _r(days_left, 1),
            "est_reward_per_day_usd": _r(est, 3),
            "est_reward_to_end_usd": _r(est * days_left, 2) if est is not None and days_left is not None else None,
            "status": o.status,
        })
    committed = sum(r["committed_usd"] or 0 for r in resting)
    held = sum(p["at_risk_usd"] or 0 for p in positions)
    marked = [p for p in positions if p["unrealized_usd"] is not None]
    return {
        "realized_all_time_usd": _r(realized),
        "settled_markets_all_time": settled,
        "markets_traded_all_time": len(tickers),
        "reward_credits_usd": _r(cash_in),
        "reward_unconfirmed_usd": _r(unconfirmed),
        "unexplained_cash_out_usd": _r(cash_out),
        "last_reward_at": _iso(last_in),
        "net_all_time_usd": _r(realized + cash_in),
        "positions": positions,
        "positions_at_risk_usd": _r(held),
        "positions_unrealized_usd": _r(sum(p["unrealized_usd"] for p in marked)) if marked else None,
        "resting": resting,
        "committed_usd": _r(committed),
        "at_risk_usd": _r(held + committed),
        "est_reward_per_day_usd": _r(est_total, 3) if est_known else None,
        "slots_used": len({r["market"] for r in resting} | {p["market"] for p in positions}),
        "slots_max": limm.MAX_OPEN_ORDERS,
        "quote_mode": limm.QUOTE_MODE,
    }


def is_incentive_book(book: str) -> bool:
    return limm.owns_tag(book)


#: The book reward credits are attributed to. The ledger is account-wide and only this book
#: quotes for rewards, so its live tag owns every confirmed payout.
REWARD_BOOK = limm.LIVE_TAG
