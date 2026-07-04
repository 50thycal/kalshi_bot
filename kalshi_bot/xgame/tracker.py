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
from ..scanner.metrics import parse_dt
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

    def _in_window(self, match, now: datetime) -> bool:
        """Poll from a few hours before the (approximate) game end; matches with no
        close_time are always polled. (Past-grace matches are ended before this check.)"""
        close = self._aware_close(match)
        return close is None or now >= close - timedelta(hours=6)

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
        for match in matches:
            close = self._aware_close(match)
            if close is not None and now > close + timedelta(
                minutes=s.xgame_ended_grace_minutes
            ):
                # the tail was already polled during the grace window each cycle
                match.status = "ended"
                summ.ended += 1
                continue
            if not self._in_window(match, now):
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
            match.last_polled_at = now
        session.flush()
        return summ
