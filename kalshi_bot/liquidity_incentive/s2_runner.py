"""The S2 live test's runner (`As2best1`, docs/LIMM_S2_LIVE_TEST.md). One cycle per live loop.

Every rule comes from `s2_live` (frozen by the pre-registration); this module only does the
reads and the order calls:

  1. stop check — a recorded stop (survives restarts), the $30 fill-loss stop, day 14;
  2. selection — the paper arm `S2-best`'s OPEN shadow rows -> `s2_live.select_live`;
  3. cancel what left the selection or whose side/price the paper arm moved;
  4. place what is missing, through `LiveExecutor.place_s2_bid` (this book's own caps), and
     mirror each placement to the paper twin;
  5. log, at most hourly per resting bid, the uncalibrated `scoring.estimate` of OUR order on
     the live book — the denominator of the read's paid / estimated ratio.

Fills are held to settlement (no exit logic here; the executor keeps the process-wide TP/SL
and the 4 h timeout off this tag). A market that filled is never re-entered.

INERT unless `LIQUIDITY_INCENTIVE_S2_LIVE_ENABLED` is on (main.py does not even construct it
otherwise), and even then the executor refuses every order until `LIVE_STRATEGIES` names
`As2best1` with the master switches set.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import models as m
from .. import repository as repo
from ..scanner.metrics import parse_orderbook
from . import live as limm
from . import s2_live as s2

logger = logging.getLogger(__name__)

STOP_COMPONENT = "limm_s2_stop"
ESTIMATE_COMPONENT = "limm_s2_estimate"
ESTIMATE_EVERY_SECONDS = 3600.0
CANCEL_DESELECTED = "s2_deselected"
CANCEL_REPRICE = "s2_reprice"
CANCEL_FILLED = "s2_filled_no_reentry"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def hours_to_step_aside(program, now: datetime, rules: s2.S2LiveRules = s2.DEFAULT_RULES) -> float:
    """The paper arm's step-aside: the programme's end, or `step_aside_hours` before close."""
    cuts = [t for t in (_aware(getattr(program, "end_date", None)),
                        (_aware(program.close_time) - timedelta(hours=rules.step_aside_hours))
                        if getattr(program, "close_time", None) else None) if t is not None]
    if not cuts:
        return float("inf")
    return (min(cuts) - now).total_seconds() / 3600.0


#: The collector ends a paper row and writes its replacement in two commits when it refreshes
#: an hour-old bid at the same price. A row ended for that reason this recently still counts,
#: so a cycle that reads between the two commits does not cancel and re-place a live bid (and
#: lose its queue place) for nothing. Every other end reason takes effect at once.
REFRESH_GRACE_SECONDS = 600.0
REFRESH_END_REASON = "refresh_max_rest"     # collector.END_REFRESH


def paper_rows(session, now: datetime) -> tuple[list[s2.PaperS2Row], dict[str, object]]:
    """The paper arm's open quote rows, as `PaperS2Row`s, plus each ticker's programme row."""
    Q, P = m.IncentiveShadowQuote, m.IncentiveProgram
    rows = session.execute(
        select(Q, P).join(P, P.id == Q.program_row_id, isouter=True)
        .where(Q.policy == s2.SOURCE_ARM, Q.ended_at.is_(None))
    ).all()
    open_tickers = {q.market_ticker for q, _p in rows}
    grace = session.execute(
        select(Q, P).join(P, P.id == Q.program_row_id, isouter=True)
        .where(Q.policy == s2.SOURCE_ARM, Q.end_reason == REFRESH_END_REASON,
               Q.ended_at >= now - timedelta(seconds=REFRESH_GRACE_SECONDS))
        .order_by(Q.ended_at.desc())
    ).all()
    seen: set[str] = set()
    for q, p in grace:
        if q.market_ticker in open_tickers or q.market_ticker in seen:
            continue
        seen.add(q.market_ticker)
        rows.append((q, p))
    out: list[s2.PaperS2Row] = []
    programs: dict[str, object] = {}
    for q, p in rows:
        parsed = s2.parse_arm_reason(q.reason)
        if parsed is None or parsed[0] != s2.SOURCE_ARM:
            continue
        _arm, side, price, _qty = parsed
        meta = ((q.book_json or {}).get("arm") or {}) if isinstance(q.book_json, dict) else {}
        ticker = q.market_ticker
        event = meta.get("event") or (p.event_ticker if p is not None else None) \
            or ticker.rsplit("-", 1)[0]
        series = meta.get("series") or (p.series_ticker if p is not None else None) \
            or ticker.split("-", 1)[0]
        out.append(s2.PaperS2Row(
            ticker=ticker, event=event, series=series, side=side, price=price,
            rate_per_hour_usd=float(q.est_reward_per_hour_usd or 0.0),
            hours_to_step_aside=(hours_to_step_aside(p, now) if p is not None else float("inf"))))
        if p is not None:
            programs[ticker] = p
    return out, programs


def _held(session, ticker: str) -> bool:
    snap = repo.latest_position_snapshot(session, ticker)
    if snap is None:
        return False
    qty = snap.quantity_fp if snap.quantity_fp is not None else snap.quantity
    return qty is not None and abs(float(qty)) >= limm.EVENT_BLOCK_MIN_CONTRACTS


class S2LiveRunner:
    """One cycle of the S2 live test. See the module docstring."""

    def __init__(self, client, settings, twin_harness=None, rules: s2.S2LiveRules = s2.DEFAULT_RULES):
        self.client = client
        self.settings = settings
        self.twin_harness = twin_harness
        self.rules = rules
        self._marks: dict[str, int] = {}               # ticker -> last best bid of our side
        self._estimated_at: dict[str, datetime] = {}   # ticker -> last estimate log

    # -- gating --------------------------------------------------------------------------
    def armed(self, executor) -> bool:
        return bool(self.settings.liquidity_incentive_s2_live_enabled
                    and executor._switches_on() and executor._allowed(s2.LIVE_TAG))

    # -- reads ---------------------------------------------------------------------------
    @staticmethod
    def own_orders(session) -> list:
        return list(session.scalars(select(m.LiveOrder).where(
            m.LiveOrder.strategy == s2.LIVE_TAG)).all())

    @staticmethod
    def stopped(session) -> m.SystemEvent | None:
        return session.scalars(select(m.SystemEvent).where(
            m.SystemEvent.component == STOP_COMPONENT).limit(1)).first()

    @staticmethod
    def fills_by_ticker(session, orders) -> dict[str, dict]:
        """Contracts bought and their cost (with fees), per ticker, from recorded fills."""
        out: dict[str, dict] = {}
        for o in orders:
            if o.action != "buy" or not o.kalshi_order_id:
                continue
            for f in repo.fills_for_order(session, o.kalshi_order_id):
                q = float(f.quantity or 0)
                if q <= 0:
                    continue
                d = out.setdefault(o.market_ticker, {"side": o.side, "qty": 0.0, "cost": 0.0})
                d["qty"] += q
                d["cost"] += q * float(f.price or o.limit_price or 0) / 100.0 + float(f.fee or 0)
        return out

    @staticmethod
    def settled(session, ticker: str) -> bool:
        """The newest position snapshot shows the market flat (settled or sold)."""
        snap = repo.latest_position_snapshot(session, ticker)
        if snap is None:
            return False
        qty = snap.quantity_fp if snap.quantity_fp is not None else snap.quantity
        return qty is not None and abs(float(qty)) <= 0.01

    def _mark_cents(self, ticker: str, side: str) -> int | None:
        """Our side's best bid now (what the held contracts could be sold into)."""
        try:
            yes, no = parse_orderbook(self.client.get_orderbook(ticker) or {})
            levels = [int(p) for p, q in (yes if side == limm.SIDE_YES else no) if float(q) > 0]
            # No bid on our side: the held contracts are worth nothing to sell into now.
            self._marks[ticker] = max(levels) if levels else 0
        except Exception as exc:  # noqa: BLE001 — fall back to the last mark
            logger.warning(f"s2 live: mark read failed for {ticker}: {type(exc).__name__}: {exc}")
        return self._marks.get(ticker)

    def fill_loss_usd(self, session, fills: dict[str, dict]) -> float:
        """Realized losses on settled markets plus held fills marked at our side's best bid.
        Per market, losses only: a gain on one market does not offset a loss on another."""
        loss = 0.0
        for ticker, d in fills.items():
            snap = repo.latest_position_snapshot(session, ticker)
            qty = None if snap is None else (
                snap.quantity_fp if snap.quantity_fp is not None else snap.quantity)
            if snap is not None and qty is not None and abs(float(qty)) <= 0.01 \
                    and snap.realized_pnl is not None:
                loss += max(0.0, -float(snap.realized_pnl))
                continue
            mark = self._mark_cents(ticker, d["side"])
            if mark is None:
                # No read ever succeeded: count it at cost (no loss) and say so; the daily
                # check and the keep gate are the backstop.
                logger.warning(f"s2 live: no mark for held {ticker}; counted at cost")
                continue
            loss += max(0.0, d["cost"] - d["qty"] * mark / 100.0)
        return round(loss, 2)

    # -- writes --------------------------------------------------------------------------
    def _cancel_all(self, session, executor, working, reason: str) -> int:
        n = 0
        for ticker in sorted({o.market_ticker for o in working}):
            if executor.cancel_incentive_orders(session, strategy=s2.LIVE_TAG, ticker=ticker,
                                                reason=reason):
                n += 1
        return n

    def _record_stop(self, session, reason: str, fill_loss: float, now: datetime) -> None:
        repo.log_system_event(
            session, level="WARNING", component=STOP_COMPONENT,
            message=f"S2 live test stopped: {reason} (fill loss ${fill_loss:.2f})",
            raw={"reason": reason, "fill_loss_usd": fill_loss, "at": now.isoformat(),
                 "tag": s2.LIVE_TAG})

    def _open_twin(self, session, bid: s2.LiveBid) -> bool:
        h = self.twin_harness
        if h is None or not getattr(h, "enabled", False):
            return False
        twin = h.twin_of(s2.LIVE_TAG)
        if not twin or not h.active_for(s2.LIVE_TAG):
            return False
        h.open_twin_entry(session, twin_tag=twin, ticker=bid.ticker, side=bid.side,
                          price=int(bid.price), quantity=int(bid.contracts),
                          note=f"twin of S2 live bid {bid.side}@{bid.price}c x{bid.contracts}")
        return True

    def _log_estimates(self, session, working, programs: dict, now: datetime) -> int:
        """At most hourly per resting bid: our order's uncalibrated reward rate on the live
        book (our own size taken out of the level first, then added back by the estimator)."""
        from .runner import candidate_from_book, est_reward_per_hour

        n = 0
        for o in working:
            if o.status not in ("resting", "partial"):
                continue
            last = self._estimated_at.get(o.market_ticker)
            if last is not None and (now - last).total_seconds() < ESTIMATE_EVERY_SECONDS:
                continue
            program = programs.get(o.market_ticker)
            if program is None:
                continue
            try:
                c = candidate_from_book(program, self.client.get_orderbook(o.market_ticker) or {},
                                        now=now)
                own = {int(o.limit_price): float(o.quantity or 0)}
                yes = o.side == limm.SIDE_YES
                rate = est_reward_per_hour(
                    c, yes_price=int(o.limit_price) if yes else None,
                    no_price=None if yes else int(o.limit_price), qty=float(o.quantity or 0),
                    own_yes=own if yes else None, own_no=None if yes else own)
            except Exception as exc:  # noqa: BLE001 — a data log never stops the book
                logger.warning(f"s2 live: estimate failed for {o.market_ticker}: {exc}")
                continue
            repo.log_system_event(
                session, level="INFO", component=ESTIMATE_COMPONENT,
                message=f"{o.market_ticker} {o.side}@{o.limit_price}c x{o.quantity}: "
                        f"${rate:.4f}/h (uncalibrated)",
                raw={"ticker": o.market_ticker, "event": o.event_ticker, "side": o.side,
                     "price": o.limit_price, "qty": o.quantity,
                     "program_row_id": getattr(program, "id", None),
                     "program_id": getattr(program, "program_id", None),
                     "est_reward_per_hour_usd": rate, "at": now.isoformat()})
            self._estimated_at[o.market_ticker] = now
            n += 1
        return n

    # -- the cycle -----------------------------------------------------------------------
    def cycle(self, session, executor, account_state=None, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        summary: dict = {"armed": False}
        if not self.armed(executor):
            return summary
        summary["armed"] = True
        orders = self.own_orders(session)
        working = [o for o in orders if o.status in repo.LIVE_NONTERMINAL_STATUSES]

        if self.stopped(session) is not None:
            summary["stopped"] = True
            summary["canceled"] = self._cancel_all(session, executor, working, "s2_stopped")
            return summary

        fills = self.fills_by_ticker(session, orders)
        fill_loss = self.fill_loss_usd(session, fills)
        started = min((_aware(o.created_at) for o in orders), default=now)
        summary["fill_loss_usd"] = fill_loss
        reason = s2.stop_reason(fill_loss_usd=fill_loss, started_at=started, now=now,
                                rules=self.rules)
        if reason is not None:
            self._record_stop(session, reason, fill_loss, now)
            summary["stopped"] = reason
            summary["canceled"] = self._cancel_all(session, executor, working, reason)
            return summary

        rows, programs = paper_rows(session, now)
        filled = set(fills)
        held = {r.ticker for r in rows if r.ticker not in filled and _held(session, r.ticker)}
        current = {o.market_ticker for o in working}
        events = {o.market_ticker: (o.event_ticker or o.market_ticker.rsplit("-", 1)[0])
                  for o in orders}
        held_fills = {t: (events.get(t, t), d["cost"]) for t, d in fills.items()
                      if not self.settled(session, t)}
        sel = s2.select_live(rows, current=current, account_held=held, filled_before=filled,
                             held_fills=held_fills, rules=self.rules)
        want = {b.ticker: b for b in sel.bids}

        canceled = 0
        for o in working:
            b = want.get(o.market_ticker)
            if o.market_ticker in filled:
                why = CANCEL_FILLED
            elif b is None:
                why = CANCEL_DESELECTED
            elif (o.side, int(o.limit_price or 0)) != (b.side, b.price):
                why = CANCEL_REPRICE
            else:
                continue
            if executor.cancel_incentive_orders(session, strategy=s2.LIVE_TAG,
                                                ticker=o.market_ticker, reason=why):
                canceled += 1
        working = [o for o in self.own_orders(session)
                   if o.status in repo.LIVE_NONTERMINAL_STATUSES]
        resting = {o.market_ticker for o in working}

        outcomes: dict[str, int] = {}
        placed = twin = 0
        for b in sel.bids:
            if b.ticker in resting:
                continue
            out = executor.place_s2_bid(session, ticker=b.ticker, event_ticker=b.event,
                                        side=b.side, price=b.price, qty=b.contracts,
                                        account_state=account_state)
            outcomes[out] = outcomes.get(out, 0) + 1
            if out == "placed":
                placed += 1
                twin += int(self._open_twin(session, b))
            elif out == limm.REJECT_INSUFFICIENT_BALANCE:
                break
        working = [o for o in self.own_orders(session)
                   if o.status in repo.LIVE_NONTERMINAL_STATUSES]
        summary.update({
            "selected": sorted(want), "placed": placed, "canceled": canceled,
            "twin_opened": twin, "outcomes": outcomes,
            "skipped": dict(sorted(sel.skipped.items())[:20]),
            "estimates": self._log_estimates(session, working, programs, now),
        })
        return summary


__all__ = ["S2LiveRunner", "paper_rows", "hours_to_step_aside"]
