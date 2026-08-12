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
    # External signals (evo/signals.py) — NOT from Kalshi's book. None means "no
    # fresh signal for this market", which fails any condition referencing it; it
    # must never be read as zero. Stamped per cycle by MarketData.set_signals live,
    # and by the backtest adapters from historical values, so both paths agree.
    pm_divergence: float | None = None
    spot_vs_strike: float | None = None

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


def _level_price_cents(v: object) -> int | None:
    """A level price as integer cents, from either the legacy integer-cents form
    (93) or the current dollar-string form ('0.9300'). Valid prices are 1..99c
    i.e. $0.01..$0.99, so a value below 1 is unambiguously dollars."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(f * 100) if 0 < f < 1 else round(f)


def _parse_levels(raw: list | None) -> list[tuple[int, int]]:
    """Kalshi orderbook levels -> [(price_cents, qty)], best (highest bid) first.

    Tolerates both shapes the API has used: legacy `[[93, 52], ...]` integer
    cents/counts, and the current `[["0.9300", "52.00"], ...]` dollar-string
    price with a fixed-point (often fractional) size. Sizes are rounded down to
    whole contracts — a partial contract is not fillable.

    Live incident this guards: when this returned [] for every market, both
    level lists were empty, so Quote.taker_levels() was always empty and
    evaluate_order could not fill ANY order at ANY price — while quotes still
    looked healthy, because top-of-book prices come from the market object.
    Nothing surfaced the failure; orders just sat open forever."""
    out: list[tuple[int, int]] = []
    for lvl in raw or []:
        try:
            price = _level_price_cents(lvl[0])
            qty_raw = float(lvl[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        qty = int(qty_raw)  # floor: partial contracts are not fillable
        if price is not None and 0 < price < 100 and qty > 0:
            out.append((price, qty))
    out.sort(key=lambda pq: -pq[0])
    return out


# Orderbook envelope + side keys the API has used. The live elections API returns
# {"orderbook_fp": {"yes_dollars": [...], "no_dollars": [...]}}; older/other
# responses use {"orderbook": {"yes": [...], "no": [...]}}. Accept both (and a
# bare, unwrapped body) so a shape change on either axis cannot silently zero out
# every fill again.
_OB_ENVELOPES = ("orderbook", "orderbook_fp")
_OB_SIDE_KEYS = {"yes": ("yes", "yes_dollars"), "no": ("no", "no_dollars")}


def _orderbook_sides(orderbook: dict | None) -> tuple[list, list]:
    """(yes_levels_raw, no_levels_raw) from any accepted orderbook envelope."""
    body: dict = orderbook if isinstance(orderbook, dict) else {}
    for env in _OB_ENVELOPES:
        inner = body.get(env)
        if isinstance(inner, dict):
            body = inner
            break

    def side(name: str) -> list:
        for key in _OB_SIDE_KEYS[name]:
            v = body.get(key)
            if isinstance(v, list):
                return v
        return []

    return side("yes"), side("no")


def _to_cents(dollars: object) -> int | None:
    """Kalshi's '<field>_dollars' price strings ('0.0200') -> integer cents."""
    try:
        return round(float(dollars) * 100)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_count(fp: object) -> int | None:
    """Kalshi's '<field>_fp' fixed-point count strings ('941.68') -> int."""
    try:
        return round(float(fp))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _mkt_cents(market: dict, name: str) -> int | None:
    """Price (cents) from a market dict, tolerating BOTH the legacy integer-cents field
    (`yes_bid`) and the current elections-API dollar-string field (`yes_bid_dollars`).
    The live API only returns the latter, so without this prices/volume come back None."""
    v = market.get(name)
    return v if v is not None else _to_cents(market.get(f"{name}_dollars"))


def _mkt_count(market: dict, name: str) -> int | None:
    """Count (volume / open_interest) tolerating `volume` and `volume_fp` alike."""
    v = market.get(name)
    return v if v is not None else _to_count(market.get(f"{name}_fp"))


def quote_from_kalshi(market: dict, orderbook: dict | None, *, source: str = "live") -> Quote:
    """Build a Quote from a Kalshi market dict + orderbook response. Reads both the legacy
    cents/int fields and the elections API's `_dollars`/`_fp` string fields (the live API
    only sends the latter), so price, volume, open interest and last price populate."""
    yes_raw, no_raw = _orderbook_sides(orderbook)
    yes_levels = _parse_levels(yes_raw)
    no_levels = _parse_levels(no_raw)
    yes_bid = yes_levels[0][0] if yes_levels else _mkt_cents(market, "yes_bid")
    no_bid = no_levels[0][0] if no_levels else _mkt_cents(market, "no_bid")
    yes_ask = (100 - no_bid) if no_bid is not None else _mkt_cents(market, "yes_ask")
    no_ask = (100 - yes_bid) if yes_bid is not None else _mkt_cents(market, "no_ask")
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
        last_price=_mkt_cents(market, "last_price"),
        volume=_mkt_count(market, "volume"),
        open_interest=_mkt_count(market, "open_interest"),
        close_time=close_time,
        event_ticker=market.get("event_ticker"),
        category=market.get("category"),
        title=market.get("title"),
    )


class MarketData:
    """Interface: get_quote / list_tickers. Implementations below."""

    _signals: dict[str, dict[str, float | None]] = {}

    def set_signals(self, mapping: dict[str, dict[str, float | None]]) -> None:
        """Install this cycle's external signals (evo/signals.py).

        REPLACES the previous cycle's map rather than merging it. A feed that stops
        reporting must make its metric disappear — carrying forward the last value
        that happened to authorize a trade is exactly the silent-staleness failure
        the freshness gate exists to prevent."""
        self._signals = mapping or {}

    def _stamp(self, quote: Quote | None) -> Quote | None:
        if quote is None:
            return None
        sig = self._signals.get(quote.ticker) or {}
        quote.pm_divergence = sig.get("pm_divergence")
        quote.spot_vs_strike = sig.get("spot_vs_strike")
        return quote

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
        return self._stamp(self.quotes.get(ticker))

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
            return self._stamp(self._cache[ticker])
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
        return self._stamp(quote)

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
