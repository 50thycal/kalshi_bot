"""Read-only open market discovery for the desks (DEC-024).

Ported from the legacy desk board (`scripts/kalshi_desk_board.py`) without the ops-branch
transport. Kalshi's public market-data API has no server-side sort, category or text
search, so the browser keeps a short-lived in-memory index of the whole open board (every
event, nested markets) and answers sorted/filtered/paginated queries from it. Single
market, order book, trade, event and series reads are always live.

Browsing is discovery, not evidence: it never consumes the job's capture allowance and is
never accepted as decision evidence. To rely on a market read, capture its `capture_url`
through the job's `source` operation (which also records `rules_sha256`). The settlement
source is read from the contract's rules text, never inferred from the title (legacy R13).
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode

import httpx

from .contracts import DeskError
from .exchange import rules_hash
from .web import retry_delay

PUBLIC_BASE = "https://api.elections.kalshi.com/trade-api/v2"
INDEX_TTL_SECONDS = 300
MAX_INDEX_PAGES = 250            # x200 events per page
MAX_INDEX_SECONDS = 150          # a slow exchange yields a partial, labelled index
MAX_RESPONSE_BYTES = 12_000_000
MAX_PAGE_LIMIT = 500
SORTS = ("volume", "volume_total", "open_interest", "newest", "closing_soon", "liquidity")
_IDENT = re.compile(r"^[A-Za-z0-9._-]{1,200}$")


def _num(value) -> Decimal:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else Decimal(0)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def count(obj: dict, field: str) -> Decimal:
    """Contract counts moved to `<field>_fp` strings; the bare legacy field may be absent."""
    fp = obj.get(f"{field}_fp")
    return _num(fp if fp not in (None, "") else obj.get(field))


def price(obj: dict, field: str) -> Decimal | None:
    """Decimal dollars from `<field>_dollars`, falling back to legacy integer cents."""
    dollars = obj.get(f"{field}_dollars")
    if dollars not in (None, ""):
        value = _num(dollars)
        return value
    raw = obj.get(field)
    if raw in (None, ""):
        return None
    return _num(raw) / 100


def taker_fee(ask: Decimal | None, qty: int = 1) -> Decimal | None:
    """Kalshi taker fee for one order: ceil(0.07 x qty x P x (1-P)) to the cent."""
    if ask is None or not 0 < ask < 1:
        return None
    cents = Decimal("0.07") * qty * ask * (1 - ask) * 100
    return Decimal(math.ceil(cents - Decimal("0.000000001"))) / 100


def _time(value) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def series_of(event_ticker: str) -> str:
    return (event_ticker or "").split("-", 1)[0]


def capture_url(ticker: str) -> str:
    return f"{PUBLIC_BASE}/markets/{quote(ticker, safe='')}"


def _s(value) -> str | None:
    return None if value is None else str(value)


def market_row(market: dict, event: dict | None = None) -> dict:
    """Flatten one market (+ event) into the structured columns the desk browses."""
    event = event or {}
    yes_bid, yes_ask = price(market, "yes_bid"), price(market, "yes_ask")
    no_bid, no_ask = price(market, "no_bid"), price(market, "no_ask")
    if yes_ask is None and no_bid is not None:
        yes_ask = 1 - no_bid
    if no_ask is None and yes_bid is not None:
        no_ask = 1 - yes_bid
    event_ticker = market.get("event_ticker") or event.get("event_ticker") or ""
    fee_yes, fee_no = taker_fee(yes_ask), taker_fee(no_ask)
    return {
        "ticker": market.get("ticker") or "",
        "event_ticker": event_ticker,
        "series_ticker": event.get("series_ticker") or series_of(event_ticker),
        "category": event.get("category") or market.get("category") or "",
        "event_title": (event.get("title") or "").strip(),
        "title": (market.get("title") or "").strip(),
        "subtitle": (market.get("yes_sub_title") or market.get("subtitle") or "").strip(),
        "status": market.get("status") or "",
        "open_time": market.get("open_time") or market.get("created_time"),
        "close_time": market.get("close_time"),
        "expiration_time": market.get("expiration_time") or market.get("latest_expiration_time"),
        "yes_bid": _s(yes_bid), "yes_ask": _s(yes_ask), "no_bid": _s(no_bid), "no_ask": _s(no_ask),
        "spread": _s(yes_ask - yes_bid) if yes_ask is not None and yes_bid is not None else None,
        "last_price": _s(price(market, "last_price")),
        "volume_24h": _s(count(market, "volume_24h")), "volume": _s(count(market, "volume")),
        "open_interest": _s(count(market, "open_interest")),
        "liquidity": _s(price(market, "liquidity")),
        "taker_fee_yes": _s(fee_yes), "taker_fee_no": _s(fee_no),
        "breakeven_yes": _s(yes_ask + fee_yes) if fee_yes is not None else None,
        "breakeven_no": _s(no_ask + fee_no) if fee_no is not None else None,
        "strike_type": market.get("strike_type"), "floor_strike": market.get("floor_strike"),
        "cap_strike": market.get("cap_strike"),
        "rules_sha256": rules_hash(market) if market.get("rules_primary") is not None else None,
        "capture_url": capture_url(market.get("ticker") or ""),
    }


def _combo(market: dict, event: dict) -> bool:
    return bool(market.get("mve_selected_legs") or market.get("mve_collection_ticker")
                or market.get("market_type", "binary") != "binary"
                or series_of(event.get("event_ticker") or "").startswith("KXMVE"))


def _cursor(snapshot: str, offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([snapshot, offset]).encode()).decode()


def _offset(cursor: str | None, snapshot: str) -> int:
    if not cursor:
        return 0
    try:
        stamp, offset = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        offset = int(offset)
    except (ValueError, TypeError):
        raise DeskError("market_cursor_invalid") from None
    if stamp != snapshot:
        raise DeskError("market_cursor_stale")  # the board moved; restart the listing
    if offset < 0:
        raise DeskError("market_cursor_invalid")
    return offset


def _ident(value, code="market_identifier_invalid") -> str:
    if not isinstance(value, str) or not _IDENT.match(value):
        raise DeskError(code)
    return value


def _int(params: dict, key: str, default: int, low: int, high: int) -> int:
    raw = params.get(key)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise DeskError(f"invalid_{key}") from None
    if not low <= value <= high:
        raise DeskError(f"invalid_{key}")
    return value


def _float(params: dict, key: str) -> float | None:
    raw = params.get(key)
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise DeskError(f"invalid_{key}") from None
    if not math.isfinite(value) or value < 0:
        raise DeskError(f"invalid_{key}")
    return value


class PublicKalshiReader:
    """Fixed-host public GET; no credentials, redirects, cookies or environment proxies."""

    def __init__(self, base=PUBLIC_BASE, *, transport=None):
        self.base = base
        self.client = httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False,
                                   transport=transport, trust_env=False,
                                   headers={"User-Agent": "KalshiDeskBrowse/2.0",
                                            "Accept": "application/json"})

    def close(self):
        self.client.close()

    def __call__(self, path: str, params: dict | None = None) -> dict:
        query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
        url = self.base + path + ("?" + query if query else "")
        for attempt in range(3):
            try:
                with self.client.stream("GET", url) as response:
                    status, retry_after = response.status_code, response.headers.get("retry-after")
                    if status == 200:
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_RESPONSE_BYTES:
                                raise DeskError("market_data_too_large")
            except httpx.TimeoutException:
                raise DeskError("market_data_timeout") from None
            except httpx.HTTPError:
                raise DeskError("market_data_unavailable") from None
            if status == 429:
                # Same bounded policy as evidence capture (#506): <=3 attempts, short waits only.
                delay = retry_delay(retry_after, attempt)
                if attempt == 2 or delay is None:
                    raise DeskError("market_data_rate_limited")
                time.sleep(delay)
                continue
            if status == 404:
                raise DeskError("market_not_found")
            if status != 200:
                raise DeskError("market_data_unavailable")
            try:
                value = json.loads(body)
            except ValueError:
                raise DeskError("market_data_unavailable") from None
            if not isinstance(value, dict):
                raise DeskError("market_data_unavailable")
            return value
        raise DeskError("market_data_rate_limited")


class MarketBrowser:
    def __init__(self, get_json=None, *, ttl_seconds=INDEX_TTL_SECONDS, max_pages=MAX_INDEX_PAGES,
                 clock=None):
        self.get = get_json or PublicKalshiReader()
        self.ttl, self.max_pages = ttl_seconds, max_pages
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._index = None

    # -- whole-board index -------------------------------------------------
    def _build(self, now: datetime) -> dict:
        rows, events, cursor, pages, complete = [], 0, "", 0, False
        started = time.monotonic()
        while pages < self.max_pages and time.monotonic() - started < MAX_INDEX_SECONDS:
            page = self.get("/events", {"status": "open", "with_nested_markets": "true",
                                        "limit": 200, "cursor": cursor})
            pages += 1
            batch = page.get("events") or []
            for event in batch:
                events += 1
                for market in event.get("markets") or []:
                    if (market.get("status") or "open") not in ("open", "active") or _combo(market, event):
                        continue
                    row = market_row(market, event)
                    closes = _time(row["close_time"])
                    if closes is not None and closes <= now:
                        continue
                    rows.append(row)
            cursor = page.get("cursor") or ""
            if not cursor or not batch:
                complete = True
                break
        snapshot = hashlib.sha256(f"{now.isoformat()}:{len(rows)}".encode()).hexdigest()[:16]
        return {"rows": rows, "as_of": now, "snapshot": snapshot,
                "coverage": {"events_scanned": events, "pages": pages, "markets": len(rows),
                             "complete": complete, "combos_excluded": True,
                             "as_of": now.isoformat(), "ttl_seconds": self.ttl}}

    def index(self, refresh=False) -> dict:
        with self._lock:  # one board scan at a time; concurrent readers share its result
            now = self.clock()
            current = self._index
            age = None if current is None else (now - current["as_of"]).total_seconds()
            # An explicit refresh is honoured once a minute so it cannot hammer the exchange.
            if age is None or age > self.ttl or (refresh and age > 60):
                self._index = self._build(now)
            return self._index

    # -- queries over the index ---------------------------------------------
    @staticmethod
    def _filter(rows, params, now):
        category = (params.get("category") or "").lower()
        series = (params.get("series") or "").upper()
        event = (params.get("event") or "").upper()
        search = (params.get("search") or "").lower()
        min_volume = _float(params, "min_volume")
        min_oi = _float(params, "min_open_interest")
        close_within = _float(params, "close_within_hours")
        opened_within = _float(params, "opened_within_hours")
        max_spread = _float(params, "max_spread")
        for row in rows:
            if category and category not in row["category"].lower():
                continue
            if series and not row["series_ticker"].upper().startswith(series):
                continue
            if event and row["event_ticker"].upper() != event:
                continue
            if search:
                hay = " ".join((row["title"], row["subtitle"], row["event_title"], row["ticker"],
                                row["event_ticker"])).lower()
                if not all(term in hay for term in search.split()):
                    continue
            if min_volume is not None and _num(row["volume_24h"]) < Decimal(str(min_volume)):
                continue
            if min_oi is not None and _num(row["open_interest"]) < Decimal(str(min_oi)):
                continue
            if close_within is not None:
                closes = _time(row["close_time"])
                if closes is None or closes > now + timedelta(hours=close_within):
                    continue
            if opened_within is not None:
                opened = _time(row["open_time"])
                if opened is None or opened < now - timedelta(hours=opened_within):
                    continue
            if max_spread is not None and (row["spread"] is None
                                           or _num(row["spread"]) > Decimal(str(max_spread))):
                continue
            yield row

    @staticmethod
    def _sort_key(sort):
        far = datetime.max.replace(tzinfo=timezone.utc)
        old = datetime.min.replace(tzinfo=timezone.utc)
        return {
            "volume": (lambda r: (_num(r["volume_24h"]), _num(r["volume"])), True),
            "volume_total": (lambda r: _num(r["volume"]), True),
            "open_interest": (lambda r: _num(r["open_interest"]), True),
            "liquidity": (lambda r: _num(r["liquidity"]), True),
            "newest": (lambda r: _time(r["open_time"]) or old, True),
            "closing_soon": (lambda r: _time(r["close_time"]) or far, False),
        }[sort]

    def markets(self, params: dict) -> dict:
        sort = params.get("sort") or "volume"
        if sort not in SORTS:
            raise DeskError("invalid_sort")
        limit = _int(params, "limit", 50, 1, MAX_PAGE_LIMIT)
        board = self.index(refresh=str(params.get("refresh", "")).lower() in {"1", "true"})
        rows = list(self._filter(board["rows"], params, board["as_of"]))
        key, reverse = self._sort_key(sort)
        rows.sort(key=lambda r: r["ticker"])
        rows.sort(key=key, reverse=reverse)
        offset = _offset(params.get("cursor"), board["snapshot"])
        page = rows[offset:offset + limit]
        following = offset + len(page)
        return {"markets": page, "total": len(rows), "offset": offset,
                "cursor": _cursor(board["snapshot"], following) if following < len(rows) else None,
                "sort": sort, "coverage": board["coverage"],
                "note": "Index quotes are as of coverage.as_of. Read `market` for live rules and "
                        "quote, and capture capture_url via `source` before relying on it."}

    def events(self, params: dict) -> dict:
        sort = params.get("sort") or "volume"
        if sort not in ("volume", "open_interest", "newest", "closing_soon", "markets"):
            raise DeskError("invalid_sort")
        limit = _int(params, "limit", 50, 1, MAX_PAGE_LIMIT)
        board = self.index()
        grouped: dict[str, dict] = {}
        for row in self._filter(board["rows"], params, board["as_of"]):
            item = grouped.setdefault(row["event_ticker"], {
                "event_ticker": row["event_ticker"], "series_ticker": row["series_ticker"],
                "title": row["event_title"], "category": row["category"], "markets": 0,
                "volume_24h": Decimal(0), "open_interest": Decimal(0),
                "first_close": None, "newest_open": None})
            item["markets"] += 1
            item["volume_24h"] += _num(row["volume_24h"])
            item["open_interest"] += _num(row["open_interest"])
            closes, opened = _time(row["close_time"]), _time(row["open_time"])
            if closes and (item["first_close"] is None or closes < item["first_close"]):
                item["first_close"] = closes
            if opened and (item["newest_open"] is None or opened > item["newest_open"]):
                item["newest_open"] = opened
        far = datetime.max.replace(tzinfo=timezone.utc)
        old = datetime.min.replace(tzinfo=timezone.utc)
        key, reverse = {
            "volume": (lambda e: e["volume_24h"], True),
            "open_interest": (lambda e: e["open_interest"], True),
            "markets": (lambda e: e["markets"], True),
            "newest": (lambda e: e["newest_open"] or old, True),
            "closing_soon": (lambda e: e["first_close"] or far, False),
        }[sort]
        rows = sorted(grouped.values(), key=lambda e: e["event_ticker"])
        rows.sort(key=key, reverse=reverse)
        offset = _offset(params.get("cursor"), board["snapshot"])
        page = [{**e, "volume_24h": str(e["volume_24h"]), "open_interest": str(e["open_interest"]),
                 "first_close": e["first_close"].isoformat() if e["first_close"] else None,
                 "newest_open": e["newest_open"].isoformat() if e["newest_open"] else None}
                for e in rows[offset:offset + limit]]
        following = offset + len(page)
        return {"events": page, "total": len(rows), "offset": offset, "sort": sort,
                "cursor": _cursor(board["snapshot"], following) if following < len(rows) else None,
                "coverage": board["coverage"]}

    def series(self, params: dict) -> dict:
        limit = _int(params, "limit", 100, 1, MAX_PAGE_LIMIT)
        board = self.index()
        grouped: dict[str, dict] = {}
        for row in self._filter(board["rows"], params, board["as_of"]):
            item = grouped.setdefault(row["series_ticker"], {
                "series_ticker": row["series_ticker"], "categories": set(), "events": set(),
                "markets": 0, "volume_24h": Decimal(0)})
            item["categories"].add(row["category"])
            item["events"].add(row["event_ticker"])
            item["markets"] += 1
            item["volume_24h"] += _num(row["volume_24h"])
        rows = sorted(grouped.values(), key=lambda s: s["series_ticker"])
        rows.sort(key=lambda s: s["volume_24h"], reverse=True)
        offset = _offset(params.get("cursor"), board["snapshot"])
        page = [{"series_ticker": s["series_ticker"], "categories": sorted(s["categories"]),
                 "events": len(s["events"]), "markets": s["markets"],
                 "volume_24h": str(s["volume_24h"])} for s in rows[offset:offset + limit]]
        following = offset + len(page)
        return {"series": page, "total": len(rows), "offset": offset,
                "cursor": _cursor(board["snapshot"], following) if following < len(rows) else None,
                "coverage": board["coverage"]}

    def categories(self) -> dict:
        board = self.index()
        totals: dict[str, list] = {}
        for row in board["rows"]:
            item = totals.setdefault(row["category"] or "?", [0, Decimal(0)])
            item[0] += 1
            item[1] += _num(row["volume_24h"])
        return {"categories": [{"category": c, "markets": n, "volume_24h": str(v)}
                               for c, (n, v) in sorted(totals.items(), key=lambda kv: kv[1][1], reverse=True)],
                "coverage": board["coverage"]}

    # -- live reads ----------------------------------------------------------
    def market(self, ticker: str) -> dict:
        ticker = _ident(ticker)
        fetched = self.clock()
        data = self.get(f"/markets/{quote(ticker, safe='')}")
        market = data.get("market")
        if not isinstance(market, dict):
            raise DeskError("market_not_found")
        event, series = {}, {}
        if market.get("event_ticker") and _IDENT.match(market["event_ticker"]):
            try:
                event = self.get(f"/events/{quote(market['event_ticker'], safe='')}").get("event") or {}
            except DeskError:
                event = {}
        series_ticker = event.get("series_ticker") or series_of(market.get("event_ticker") or "")
        if series_ticker and _IDENT.match(series_ticker):
            try:
                series = self.get(f"/series/{quote(series_ticker, safe='')}").get("series") or {}
            except DeskError:
                series = {}
        return {"market": market, "summary": market_row(market, event),
                "rules_primary": market.get("rules_primary"),
                "rules_secondary": market.get("rules_secondary"),
                "rules_sha256": rules_hash(market),
                "event": {k: event.get(k) for k in ("event_ticker", "series_ticker", "title",
                                                     "sub_title", "category", "mutually_exclusive")},
                "series": {k: series.get(k) for k in ("ticker", "title", "category", "frequency",
                                                       "settlement_sources", "contract_url",
                                                       "contract_terms_url")},
                "quote_at": fetched.isoformat(), "capture_url": capture_url(ticker),
                "note": "Settlement source comes from the rules text above (R13), not the title. "
                        "Capture capture_url via `source` to use this read as evidence."}

    def orderbook(self, ticker: str, depth: int = 10) -> dict:
        ticker = _ident(ticker)
        depth = _int({"depth": depth}, "depth", 10, 1, 100)
        fetched = self.clock()
        data = self.get(f"/markets/{quote(ticker, safe='')}/orderbook", {"depth": depth})
        book = data.get("orderbook_fp") or data.get("orderbook") or {}
        sides = {}
        for side in ("yes", "no"):
            levels = book.get(f"{side}_dollars") or book.get(side) or []
            parsed = []
            for level in levels:
                try:
                    raw_price, quantity = level[0], level[1]
                    value = (_num(raw_price) if isinstance(raw_price, str) and "." in str(raw_price)
                             else _num(raw_price) / 100)
                    parsed.append({"price": str(value), "quantity": str(_num(quantity))})
                except (TypeError, IndexError):
                    continue
            parsed.sort(key=lambda lv: _num(lv["price"]), reverse=True)
            sides[f"{side}_bids"] = parsed[:depth]
        best_yes = _num(sides["yes_bids"][0]["price"]) if sides["yes_bids"] else None
        best_no = _num(sides["no_bids"][0]["price"]) if sides["no_bids"] else None
        return {"ticker": ticker, **sides, "fetched_at": fetched.isoformat(),
                "implied_yes_ask": str(1 - best_no) if best_no is not None else None,
                "implied_no_ask": str(1 - best_yes) if best_yes is not None else None,
                "note": "Resting bids. A NO bid at p is a YES offer at 1-p."}

    def trades(self, ticker: str, limit: int = 50, cursor: str | None = None) -> dict:
        ticker = _ident(ticker)
        limit = _int({"limit": limit}, "limit", 50, 1, 1000)
        data = self.get("/markets/trades", {"ticker": ticker, "limit": limit, "cursor": cursor or ""})
        trades = []
        for trade in data.get("trades") or []:
            trades.append({"created_time": trade.get("created_time"),
                           "yes_price": _s(price(trade, "yes_price")),
                           "no_price": _s(price(trade, "no_price")),
                           "count": _s(count(trade, "count")), "taker_side": trade.get("taker_side"),
                           "trade_id": trade.get("trade_id")})
        return {"ticker": ticker, "trades": trades, "cursor": data.get("cursor") or None,
                "fetched_at": self.clock().isoformat()}

    def event(self, event_ticker: str) -> dict:
        event_ticker = _ident(event_ticker)
        fetched = self.clock()
        data = self.get(f"/events/{quote(event_ticker, safe='')}", {"with_nested_markets": "true"})
        event = data.get("event") or {}
        markets = event.get("markets") or data.get("markets") or []
        if not event and not markets:
            raise DeskError("market_not_found")
        rows = [market_row(m, event) for m in markets]
        rows.sort(key=lambda r: (_num(r["floor_strike"]) if r["floor_strike"] is not None else Decimal(0),
                                 r["ticker"]))
        asks = [_num(r["yes_ask"]) for r in rows if r["yes_ask"] is not None]
        return {"event": {k: v for k, v in event.items() if k != "markets"}, "markets": rows,
                "market_count": len(rows), "sum_yes_asks": str(sum(asks, Decimal(0))),
                "fetched_at": fetched.isoformat(),
                "capture_url": f"{PUBLIC_BASE}/events/{quote(event_ticker, safe='')}?with_nested_markets=true"}

    def series_detail(self, series_ticker: str) -> dict:
        series_ticker = _ident(series_ticker)
        data = self.get(f"/series/{quote(series_ticker, safe='')}")
        if not data.get("series"):
            raise DeskError("market_not_found")
        return {"series": data["series"], "fetched_at": self.clock().isoformat()}
