"""XGAME in-play tape collector (COLLECT ONLY — no trading, no paper book yet).

The XGAME thesis (docs/IDEA_MODEL_20260704.md) needs sub-minute cross-venue tapes of
live game markets, which no public history endpoint provides finer than 1-min bars —
so this tracker rides the weather/live cycle like theta and, for every matched
(Kalshi per-team moneyline <-> Polymarket same-team/day market) pair:

  1. DISCOVERY (throttled): pages the configured Kalshi game series and Polymarket tag
     slugs, extracts (day, normalized team) keys on both venues, and upserts exact-key
     matches into game_market_matches. Precision over recall — an ambiguous key (the
     same (day, team) appearing twice on a venue) is dropped, and the FULL PM
     clobTokenId is stored.
  2. TAPE POLLING: for each active match inside its game window, pulls both venues'
     trade tapes since the stored high-water mark (minus an overlap, deduped on the
     venue trade id) into game_tape_snapshots. Trades carry the venues' own
     timestamps, so the analysis (scripts/xgame_tape_study.py) builds ~10s bars no
     matter the poll cadence; the cadence only bounds how much tape could be lost if
     a market out-trades the page caps between polls.

Normalization: both venues land as team_prob_cents = P(matched team wins) in cents —
Kalshi trades are already the team's YES price; PM trades on the complementary token
are flipped (100 - price). The Kalshi 'Reg Time' 3-way vs PM 2-way semantic gap is
fine for lead-lag (a goal moves both the same direction; see scripts/xvenue_game.py).

Fail-soft everywhere: a venue/page/match failure logs and moves on — only AuthError
(Kalshi credentials) propagates, matching the other ride-alongs.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .. import repository as repo
from ..config import Settings
from ..kalshi.errors import AuthError
from ..paper.engine import kalshi_fee
from ..scanner.metrics import compute_metrics, parse_dt
from .pm import PmGamesClient, norm_team

logger = logging.getLogger(__name__)

_TICKER_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def ticker_day(ticker: str) -> str:
    """KXWCGAME-25JUL04PORCRO-POR -> '2025-07-04' (same parse as scripts/xvenue_game.py)."""
    m = _TICKER_DATE.search(ticker or "")
    if not m:
        return ""
    mon = _MON.get(m.group(2))
    return f"20{m.group(1)}-{mon:02d}-{int(m.group(3)):02d}" if mon else ""


def _trade_price_c(trade: dict) -> float | None:
    """Kalshi trade YES price as cents, tolerating dollar-string vs int-cent fields."""
    v = trade.get("yes_price_dollars")
    if v not in (None, ""):
        try:
            return float(v) * 100.0
        except (TypeError, ValueError):
            return None
    v = trade.get("yes_price")
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _size(trade: dict) -> float:
    for k in ("count_fp", "count", "size"):
        v = trade.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


@dataclass
class XGameCycleSummary:
    kalshi_games: int = 0        # (day, team) keys found on Kalshi at discovery
    pm_games: int = 0            # (day, team) keys found on Polymarket at discovery
    ambiguous_dropped: int = 0
    matched_new: int = 0
    matches_active: int = 0
    polled: int = 0
    skipped_window: int = 0      # active matches outside their game window this cycle
    kalshi_rows: int = 0
    pm_rows: int = 0
    ended: int = 0
    errors: int = 0
    # paper BOOK counters (rides on top of the collector; see _maybe_enter / manage_open_positions)
    signals: int = 0             # matches with a live PM-shock + Kalshi-lag gap this cycle
    opened: int = 0
    already_open: int = 0
    capped: int = 0
    exits_converged: int = 0
    exits_timeout: int = 0
    book_pnl: float = 0.0
    per_match: dict[str, int] = field(default_factory=dict)  # ticker -> rows this cycle


class XGameTracker:
    def __init__(self, client, settings: Settings, pm_client: PmGamesClient | None = None):
        self.client = client
        self.settings = settings
        self.pm = pm_client or PmGamesClient()
        self._last_discovery = 0.0  # monotonic; 0 -> discover on first cycle

    # -- discovery ----------------------------------------------------------
    def _kalshi_games(self) -> dict[tuple[str, str], dict]:
        """(day, team) -> market info for open per-team game markets; ambiguous keys
        dropped (value None sentinels are removed at the end)."""
        s = self.settings
        out: dict[tuple[str, str], dict | None] = {}
        for series in s.xgame_series_list:
            cursor: str | None = None
            for _ in range(8):
                try:
                    page = self.client.get_markets(
                        status="open", series_ticker=series, limit=200, cursor=cursor
                    )
                except AuthError:
                    raise
                except Exception as exc:  # noqa: BLE001 — one series must not kill discovery
                    logger.warning(
                        "xgame: kalshi discovery fetch failed",
                        extra={"extra_fields": {"series": series, "error": str(exc)[:200]}},
                    )
                    break
                mkts = (page or {}).get("markets") or []
                for mkt in mkts:
                    ticker = mkt.get("ticker") or ""
                    sub = (mkt.get("yes_sub_title") or "").split(":")[-1]
                    team = norm_team(sub)
                    day = ticker_day(ticker)
                    if not ticker or not team or team == "tie" or not day:
                        continue
                    key = (day, team)
                    if key in out:
                        out[key] = None  # ambiguous on Kalshi -> drop
                        continue
                    out[key] = {
                        "series": series,
                        "ticker": ticker,
                        "event_ticker": ticker.rsplit("-", 1)[0],
                        "title": mkt.get("title") or "",
                        "close_time": parse_dt(mkt.get("close_time")),
                    }
                cursor = (page or {}).get("cursor") or None
                if not cursor or not mkts:
                    break
        return {k: v for k, v in out.items() if v is not None}

    def _pm_games(self) -> dict[tuple[str, str], dict]:
        s = self.settings
        try:
            markets = self.pm.game_markets(s.xgame_pm_tag_list, s.xgame_pm_pages)
        except Exception as exc:  # noqa: BLE001 — PM discovery is fail-soft
            logger.warning(
                "xgame: polymarket discovery failed",
                extra={"extra_fields": {"error": str(exc)[:200]}},
            )
            return {}
        out: dict[tuple[str, str], dict | None] = {}
        for mk in markets:
            key = (mk["day"], mk["team"])
            if key in out:
                out[key] = None  # ambiguous on PM -> drop
                continue
            out[key] = mk
        return {k: v for k, v in out.items() if v is not None}

    def _discover(self, session, summ: XGameCycleSummary) -> None:
        s = self.settings
        kal = self._kalshi_games()
        pm = self._pm_games()
        summ.kalshi_games = len(kal)
        summ.pm_games = len(pm)
        active = repo.count_active_game_matches(session)
        for key in sorted(set(kal) & set(pm)):
            if active >= s.xgame_max_matches:
                logger.info(
                    "xgame: active-match cap reached; not adding more",
                    extra={"extra_fields": {"cap": s.xgame_max_matches}},
                )
                break
            k, p = kal[key], pm[key]
            if not p["token_id"] or not p["condition_id"]:
                continue
            _, created = repo.upsert_game_match(
                session,
                sport=s.xgame_pm_tag_list[0] if s.xgame_pm_tag_list else None,
                day=key[0],
                team=key[1],
                kalshi_series=k["series"],
                kalshi_ticker=k["ticker"],
                kalshi_event_ticker=k["event_ticker"],
                kalshi_title=k["title"][:500],
                pm_condition_id=p["condition_id"],
                pm_token_id=p["token_id"],
                pm_question=p["question"][:500],
                close_time=k["close_time"],
            )
            if created:
                summ.matched_new += 1
                active += 1
                logger.info(
                    "xgame: matched game market pair",
                    extra={"extra_fields": {"day": key[0], "team": key[1],
                                            "kalshi": k["ticker"],
                                            "pm": p["question"][:80]}},
                )

    # -- tape polling ---------------------------------------------------------
    @staticmethod
    def _aware_close(match) -> datetime | None:
        close = match.close_time
        if close is not None and close.tzinfo is None:
            close = close.replace(tzinfo=timezone.utc)
        return close

    def _poll_window(self, match) -> tuple[datetime, datetime] | None:
        """(lo, hi) of the polling window. Keyed on the ticker-derived GAME DAY —
        the 2026-07-04 format probe showed KXWCGAME close_time is a far-future
        settlement deadline (game Jul 6, close Jul 21), so close_time would start
        polling weeks late. Day windows cover any kickoff that UTC day plus the
        post-game tail; close_time (plus grace) is only the day-less fallback."""
        s = self.settings
        grace = timedelta(minutes=s.xgame_ended_grace_minutes)
        if match.day:
            try:
                start = datetime.fromisoformat(match.day).replace(tzinfo=timezone.utc)
            except ValueError:
                start = None
            if start is not None:
                return start - timedelta(hours=2), start + timedelta(hours=30) + grace
        close = self._aware_close(match)
        if close is None:
            return None  # no window information: always poll, never auto-end
        return close - timedelta(hours=6), close + grace

    def _poll_kalshi(self, session, match) -> int:
        s = self.settings
        since = match.kalshi_since_ts
        min_ts = None
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            min_ts = int(since.timestamp()) - s.xgame_overlap_seconds
        rows: list[dict] = []
        cursor: str | None = None
        for _ in range(s.xgame_kalshi_trade_pages):
            page = self.client.get_market_trades(
                ticker=match.kalshi_ticker, limit=1000, cursor=cursor, min_ts=min_ts
            )
            trades = (page or {}).get("trades") or []
            for t in trades:
                traded_at = parse_dt(t.get("created_time"))
                price = _trade_price_c(t)
                if traded_at is None or price is None or not (0 < price < 100):
                    continue
                trade_id = str(t.get("trade_id") or "") or (
                    f"k:{match.kalshi_ticker}:{int(traded_at.timestamp())}"
                    f":{price:.1f}:{_size(t):.1f}"
                )
                rows.append({
                    "market_id": match.kalshi_ticker,
                    "trade_id": trade_id,
                    "traded_at": traded_at,
                    "price_cents": price,
                    "team_prob_cents": price,  # per-team market: YES price IS P(team)
                    "size": _size(t),
                    "taker_side": (t.get("taker_side") or "")[:8] or None,
                })
            cursor = (page or {}).get("cursor") or None
            if not cursor or not trades:
                break
        inserted = repo.insert_game_tape_trades(session, match.id, "kalshi", rows)
        if rows:
            newest = max(r["traded_at"] for r in rows)
            prev = match.kalshi_since_ts
            if prev is not None and prev.tzinfo is None:
                prev = prev.replace(tzinfo=timezone.utc)
            if prev is None or newest > prev:
                match.kalshi_since_ts = newest
            match.kalshi_trades = (match.kalshi_trades or 0) + inserted
        return inserted

    def _poll_pm(self, session, match) -> int:
        s = self.settings
        since_unix = None
        since = match.pm_since_ts
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            since_unix = since.timestamp() - s.xgame_overlap_seconds
        rows: list[dict] = []
        done = False
        for page in range(s.xgame_pm_trade_pages):
            trades = self.pm.trades(match.pm_condition_id, offset=page * 500)
            if not trades:
                break
            for t in trades:
                try:
                    ts = float(t.get("timestamp"))
                except (TypeError, ValueError):
                    continue
                if since_unix is not None and ts < since_unix:
                    done = True  # newest-first: everything further back is stored
                    break
                try:
                    price = float(t.get("price"))
                except (TypeError, ValueError):
                    continue
                if not (0.0 < price < 1.0):
                    continue
                asset = str(t.get("asset") or "")
                team_prob = price * 100.0 if asset == match.pm_token_id else 100.0 - price * 100.0
                tx = str(t.get("transactionHash") or "")
                trade_id = f"{tx[:36]}:{asset[-12:]}:{int(ts)}:{price:.4f}:{_size(t):.2f}"
                rows.append({
                    "market_id": asset or None,
                    "trade_id": trade_id,
                    "traded_at": datetime.fromtimestamp(ts, tz=timezone.utc),
                    "price_cents": price * 100.0,
                    "team_prob_cents": team_prob,
                    "size": _size(t),
                    "taker_side": (t.get("side") or "").lower()[:8] or None,
                })
            if done or len(trades) < 500:
                break
        inserted = repo.insert_game_tape_trades(session, match.id, "polymarket", rows)
        if rows:
            newest = max(r["traded_at"] for r in rows)
            prev = match.pm_since_ts
            if prev is not None and prev.tzinfo is None:
                prev = prev.replace(tzinfo=timezone.utc)
            if prev is None or newest > prev:
                match.pm_since_ts = newest
            match.pm_trades = (match.pm_trades or 0) + inserted
        return inserted

    # -- one cycle ------------------------------------------------------------
    def run_once(self, session) -> XGameCycleSummary:
        s = self.settings
        summ = XGameCycleSummary()
        now_mono = time.monotonic()
        if (
            self._last_discovery == 0.0
            or now_mono - self._last_discovery >= s.xgame_discovery_minutes * 60.0
        ):
            self._last_discovery = now_mono
            try:
                self._discover(session, summ)
            except AuthError:
                raise
            except Exception:  # noqa: BLE001 — discovery must not stop polling
                summ.errors += 1
                logger.exception("xgame: discovery failed")

        now = datetime.now(timezone.utc)
        matches = repo.active_game_matches(session, limit=s.xgame_max_matches)
        summ.matches_active = len(matches)

        # paper-book open state (recomputed once per cycle; updated as we open)
        book_open_count = repo.count_open_paper_positions(session, "xgame") if s.xgame_book_enabled else 0
        book_open_tickers = (
            repo.open_paper_position_tickers(session, "xgame") if s.xgame_book_enabled else set()
        )

        for match in matches:
            window = self._poll_window(match)
            if window is not None and now > window[1]:
                # the tail was already polled while inside the window each cycle
                match.status = "ended"
                summ.ended += 1
                continue
            if window is not None and now < window[0]:
                summ.skipped_window += 1
                continue
            summ.polled += 1
            got = 0
            try:
                k = self._poll_kalshi(session, match)
                summ.kalshi_rows += k
                got += k
            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001 — one venue must not stop the rest
                summ.errors += 1
                logger.warning(
                    "xgame: kalshi tape poll failed",
                    extra={"extra_fields": {"ticker": match.kalshi_ticker,
                                            "error": str(exc)[:200]}},
                )
            try:
                p = self._poll_pm(session, match)
                summ.pm_rows += p
                got += p
            except Exception as exc:  # noqa: BLE001
                summ.errors += 1
                logger.warning(
                    "xgame: polymarket tape poll failed",
                    extra={"extra_fields": {"condition": (match.pm_condition_id or "")[:20],
                                            "error": str(exc)[:200]}},
                )
            if got:
                summ.per_match[match.kalshi_ticker] = got

            # paper book: act on the freshly-polled tape (PM lead / Kalshi lag)
            if s.xgame_book_enabled and match.kalshi_ticker not in book_open_tickers:
                try:
                    opened = self._maybe_enter(session, match, now, book_open_count, summ)
                except AuthError:
                    raise
                except Exception as exc:  # noqa: BLE001 — the book must never stop the collector
                    summ.errors += 1
                    logger.warning(
                        "xgame: entry check failed",
                        extra={"extra_fields": {"ticker": match.kalshi_ticker,
                                                "error": str(exc)[:200]}},
                    )
                    opened = False
                if opened:
                    book_open_count += 1
                    book_open_tickers.add(match.kalshi_ticker)

            match.last_polled_at = now
        session.flush()
        return summ

    # -- paper book: entry ----------------------------------------------------
    def _tape_levels(self, session, match, now: datetime) -> tuple[float, float, float] | None:
        """(pm_now, pm_prev, kal_now) team-prob levels over the recent shock window, or None
        if either venue is too thin to read a level. pm_prev is the oldest PM print in the
        window (the pre-shock level); *_now are the latest prints."""
        s = self.settings
        since = now - timedelta(seconds=s.xgame_shock_window_seconds)
        pm = repo.recent_game_tape(session, match.id, "polymarket", since)
        kal = repo.recent_game_tape(session, match.id, "kalshi", since)
        if not pm or not kal:
            return None
        return pm[-1][1], pm[0][1], kal[-1][1]

    def _maybe_enter(self, session, match, now: datetime, open_count: int,
                     summ: XGameCycleSummary) -> bool:
        """Open one taker paper position on the lagging Kalshi market when PM has shocked and
        Kalshi has not yet followed. Returns True if a position was opened."""
        s = self.settings
        levels = self._tape_levels(session, match, now)
        if levels is None:
            return False
        pm_now, pm_prev, kal_now = levels
        pm_shock = pm_now - pm_prev          # PM's recent jump (signed)
        gap = pm_now - kal_now               # live lead the Kalshi tape hasn't closed (signed)
        if abs(pm_shock) < s.xgame_shock_cents or abs(gap) < s.xgame_min_gap_cents:
            return False
        # the lag must be in the SAME direction as the PM shock (Kalshi behind the move)
        if (pm_shock > 0) != (gap > 0):
            return False
        summ.signals += 1
        if open_count >= s.xgame_book_max_open_positions:
            summ.capped += 1
            return False

        try:
            ob = self.client.get_orderbook(match.kalshi_ticker, depth=s.orderbook_depth)
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "xgame: book orderbook fetch failed",
                extra={"extra_fields": {"ticker": match.kalshi_ticker, "error": str(exc)[:200]}},
            )
            return False
        metrics = compute_metrics({"ticker": match.kalshi_ticker}, ob, top_n=s.orderbook_depth)
        if not metrics.two_sided:
            return False

        # buy the side the gap points to, TAKER at that side's ask; qty capped by ask depth.
        if gap > 0:  # expect Kalshi P(team) to RISE toward PM -> buy YES
            side, price, depth = "yes", metrics.best_yes_ask, metrics.depth_at_best_ask
        else:        # expect Kalshi P(team) to FALL -> buy NO
            side, price, depth = "no", metrics.best_no_ask, metrics.depth_at_best_bid
        if price is None or not (0 < price < 100):
            return False
        qty = min(s.xgame_order_size, depth or 0)
        if qty <= 0:
            return False

        fee = kalshi_fee(price, qty, s.paper_fees_enabled)
        repo.create_paper_trade(
            session,
            signal_id=None,
            ticker=match.kalshi_ticker,
            strategy="xgame",
            side=side,
            action="buy",
            assumed_price=price,
            fill_assumption=(
                f"[xgame] buy {side} @ {price:.0f}c pm {pm_now:.0f} kal {kal_now:.0f} gap {gap:+.0f}"
            ),
            quantity=qty,
            entry_fee=fee,
            model_probability=(pm_now / 100.0),   # convergence target (PM's P(team))
            edge=gap / 100.0,                     # signed entry gap
        )
        repo.open_paper_position_for_trade(
            session, ticker=match.kalshi_ticker, strategy="xgame", side=side,
            quantity=qty, avg_price=price,
        )
        summ.opened += 1
        return True

    # -- paper book: exit (converge or timeout; the shared engine skips xgame) -------
    def manage_open_positions(self, session) -> XGameCycleSummary:
        """Close open xgame paper positions on the fast converge/timeout rule — the shared
        paper engine deliberately skips 'xgame' so this seconds-scale exit owns them. Never
        raises into the cycle; a mark failure just leaves the position for the next cycle."""
        s = self.settings
        summ = XGameCycleSummary()
        now = datetime.now(timezone.utc)
        for trade in repo.open_paper_trades_with_prefix(session, "xgame"):
            created = trade.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_s = (now - created).total_seconds() if created is not None else 0.0

            try:
                ob = self.client.get_orderbook(trade.market_ticker, depth=s.orderbook_depth)
            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "xgame: exit orderbook fetch failed",
                    extra={"extra_fields": {"ticker": trade.market_ticker,
                                            "error": str(exc)[:200]}},
                )
                continue
            metrics = compute_metrics({"ticker": trade.market_ticker}, ob, top_n=s.orderbook_depth)

            target = float(trade.model_probability or 0.0) * 100.0   # PM target P(team)
            gap0 = float(trade.edge or 0.0) * 100.0                  # signed entry gap
            cur_mid = metrics.midpoint
            converged = False
            if cur_mid is not None and gap0 != 0.0:
                # fraction of the entry gap Kalshi has now closed toward the PM target
                closed_frac = 1.0 - (target - cur_mid) / gap0
                converged = closed_frac >= s.xgame_converge_frac
            timed_out = age_s >= s.xgame_hold_seconds
            if not converged and not timed_out:
                continue

            # exit taker at the held side's bid (sell what we bought); fall back to the mid
            # (then the entry price) so an aged, one-sided book still closes rather than hangs.
            exit_bid = metrics.best_yes_bid if trade.side == "yes" else metrics.best_no_bid
            if exit_bid is None:
                exit_bid = int(round(cur_mid)) if cur_mid is not None else trade.assumed_price
            exit_fee = kalshi_fee(exit_bid, trade.quantity, s.paper_fees_enabled)
            pnl = trade.quantity * (exit_bid - trade.assumed_price) / 100.0 \
                - float(trade.fees or 0.0) - exit_fee
            status = "closed_tp" if converged else "closed_timeout"
            repo.close_paper_trade(
                session, trade, status=status, pnl=pnl, exit_price=exit_bid, exit_fee=exit_fee
            )
            if converged:
                summ.exits_converged += 1
            else:
                summ.exits_timeout += 1
            summ.book_pnl += pnl
        return summ
