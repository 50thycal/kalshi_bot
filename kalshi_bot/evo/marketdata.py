"""Market-data access for the evo system.

One narrow interface consumed by listeners, the paper fill simulator, and the
sandbox, with three sources behind it:
- LiveMarketData: the real Kalshi client (read-only endpoints) — shadow mode.
- StaticMarketData: an in-memory book set by the caller — simulation and tests.
Replay for backtests is provided by sandbox.py over the backfill tables.

Quotes carry captured_at + a source tag so staleness can fail closed and fills
record their data provenance (spec §17, §19)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class Quote:
    ticker: str
    captured_at: datetime
    source: str = "live"
    status: str = ""  # active | closed | settled | finalized | determined | ...
    result: str = ""  # '' | 'yes' | 'no' | 'void'
    yes_bid: int | None = None
    yes_ask: int | None = None
    no_bid: int | None = None
    no_ask: int | None = None
    # Resting bid ladders, best-first: [(price_cents, qty), ...]
    yes_levels: list[tuple[int, int]] = field(default_factory=list)
    no_levels: list[tuple[int, int]] = field(default_factory=list)
    last_price: int | None = None
    volume: int | None = None
    open_interest: int | None = None
    close_time: datetime | None = None
    event_ticker: str | None = None
    category: str | None = None
    title: str | None = None

    @property
    def spread(self) -> int | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    @property
    def mid(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2.0

    def hours_to_close(self, now: datetime | None = None) -> float | None:
        if self.close_time is None:
            return None
        now = now or datetime.now(timezone.utc)
        ct = self.close_time if self.close_time.tzinfo else self.close_time.replace(
            tzinfo=timezone.utc
        )
        return (ct - now).total_seconds() / 3600.0

    def age_seconds(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        cap = self.captured_at if self.captured_at.tzinfo else self.captured_at.replace(
            tzinfo=timezone.utc
        )
        return (now - cap).total_seconds()

    # --- ladder helpers (Kalshi convention: order books are resting bids only; the
    # YES ask is derived from NO bids: yes_ask = 100 - best_no_bid) ---

    def taker_levels(self, side: str) -> list[tuple[int, int]]:
        """Price levels a taker BUY of `side` fills against, best-first, as the
        taker's own cost per contract. Buying YES consumes resting NO bids at
        cost 100-no_bid; buying NO consumes resting YES bids at cost 100-yes_bid."""
        opposite = self.no_levels if side == "yes" else self.yes_levels
        return [(100 - price, qty) for price, qty in opposite if 0 < 100 - price < 100]

    def exit_levels(self, side: str) -> list[tuple[int, int]]:
        """Levels a taker SELL of held `side` fills against (that side's own bids)."""
        return list(self.yes_levels if side == "yes" else self.no_levels)

    def best_taker_price(self, side: str) -> int | None:
        levels = self.taker_levels(side)
        return levels[0][0] if levels else None

    def best_exit_bid(self, side: str) -> int | None:
        return self.yes_bid if side == "yes" else self.no_bid

    def is_terminal(self) -> bool:
        return self.result in ("yes", "no", "void") or self.status in (
            "settled", "finalized", "determined",
        )


def _parse_levels(raw: list | None) -> list[tuple[int, int]]:
    """Kalshi orderbook levels: [[price, qty], ...] — normalize + sort best (highest
    bid) first."""
    out: list[tuple[int, int]] = []
    for lvl in raw or []:
        try:
            price, qty = int(lvl[0]), int(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 < price < 100 and qty > 0:
            out.append((price, qty))
    out.sort(key=lambda pq: -pq[0])
    return out


def quote_from_kalshi(market: dict, orderbook: dict | None, *, source: str = "live") -> Quote:
    """Build a Quote from a Kalshi market dict + orderbook response."""
    ob = orderbook.get("orderbook") if isinstance(orderbook, dict) and "orderbook" in (
        orderbook or {}
    ) else orderbook
    yes_levels = _parse_levels((ob or {}).get("yes"))
    no_levels = _parse_levels((ob or {}).get("no"))
    yes_bid = yes_levels[0][0] if yes_levels else market.get("yes_bid")
    no_bid = no_levels[0][0] if no_levels else market.get("no_bid")
    yes_ask = (100 - no_bid) if no_bid is not None else market.get("yes_ask")
    no_ask = (100 - yes_bid) if yes_bid is not None else market.get("no_ask")
    close_time = market.get("close_time")
    if isinstance(close_time, str):
        try:
            close_time = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        except ValueError:
            close_time = None
    return Quote(
        ticker=market.get("ticker", ""),
        captured_at=datetime.now(timezone.utc),
        source=source,
        status=(market.get("status") or "").lower(),
        result=(market.get("result") or "").lower(),
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        yes_levels=yes_levels,
        no_levels=no_levels,
        last_price=market.get("last_price"),
        volume=market.get("volume"),
        open_interest=market.get("open_interest"),
        close_time=close_time,
        event_ticker=market.get("event_ticker"),
        category=market.get("category"),
        title=market.get("title"),
    )


class MarketData:
    """Interface: get_quote / list_tickers. Implementations below."""

    def get_quote(self, ticker: str) -> Quote | None:  # pragma: no cover - interface
        raise NotImplementedError

    def known_tickers(self) -> list[str]:  # pragma: no cover - interface
        raise NotImplementedError


class StaticMarketData(MarketData):
    """In-memory book for tests and the deterministic simulation. The sim advances
    time by replacing quotes; captured_at is controlled by the caller."""

    def __init__(self) -> None:
        self.quotes: dict[str, Quote] = {}

    def set_quote(self, quote: Quote) -> None:
        self.quotes[quote.ticker] = quote

    def get_quote(self, ticker: str) -> Quote | None:
        return self.quotes.get(ticker)

    def known_tickers(self) -> list[str]:
        return sorted(self.quotes)


class LiveMarketData(MarketData):
    """Read-only Kalshi wrapper with a per-cycle memo cache. Never places orders —
    it only calls get_market / get_orderbook / get_markets."""

    def __init__(self, client, orderbook_depth: int = 10) -> None:
        self._client = client
        self._depth = orderbook_depth
        self._cache: dict[str, Quote] = {}

    def begin_cycle(self) -> None:
        self._cache.clear()

    def get_quote(self, ticker: str) -> Quote | None:
        if ticker in self._cache:
            return self._cache[ticker]
        try:
            resp = self._client.get_market(ticker)
            market = resp.get("market") if isinstance(resp, dict) and "market" in resp else resp
            ob = None
            if not (market.get("result") or "").lower() and (
                market.get("status") or ""
            ).lower() not in ("settled", "finalized", "determined"):
                ob = self._client.get_orderbook(ticker, depth=self._depth)
        except Exception as exc:  # noqa: BLE001 — data failures fail closed upstream
            logger.warning(
                "evo marketdata fetch failed",
                extra={"extra_fields": {"ticker": ticker, "error": str(exc)}},
            )
            return None
        quote = quote_from_kalshi(market, ob)
        self._cache[ticker] = quote
        return quote

    def known_tickers(self) -> list[str]:
        return sorted(self._cache)

    def list_markets(self, **params) -> list[dict]:
        """Paged open-market listing for universe scans (read-only)."""
        try:
            return list(self._client.iter_markets(**params))
        except AttributeError:
            resp = self._client.get_markets(**params)
            return resp.get("markets", []) if isinstance(resp, dict) else []
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "evo market listing failed", extra={"extra_fields": {"error": str(exc)}}
            )
            return []
