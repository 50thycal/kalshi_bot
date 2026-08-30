"""The PERP-V1 tape collector: poll Kalshi's perp surface, store what it says.

WHAT THIS IS FOR
----------------
Arms A, B and C are scored later over ONE shared tape. Arm A needs the premium
(mark vs index) and its history; arm B needs funding and enough price history to
estimate a BTC beta; arm C needs book depth alongside the crypto ladders it
already collects. This writes all of it, per cycle, and writes a telemetry row
whether or not the cycle went well.

WHY EVERY TABLE KEEPS THE RAW PAYLOAD
-------------------------------------
The field names here were measured, not assumed (`scripts/perp_surface_survey.py`,
recorded in `docs/RESEARCH_JOURNAL.md` 2026-08-29 and 2026-08-30) — but three of
them arrive as nested OBJECTS whose inner shape has never been read
(`reference_price`, `settlement_mark_price`, `liquidation_mark_price`), and the
funding payload's shape is still unknown after a live 200 — production returns
one carrying no records at all. So the extractors below are deliberately defensive: they look for a
number, and record NULL when they cannot find one. A NULL that keeps its raw
payload is recoverable. A guessed number is not, and it would land in the exact
quantity arm A trades on.

WHY IT NEVER RAISES INTO THE CYCLE
----------------------------------
The collector runs in the worker's every-mode hook, beside books holding real
money. An instrument that can kill a trading cycle is a liability, not an
instrument, so every stage is individually guarded and failures are COUNTED into
the telemetry row rather than propagated. The one thing it must never do is
report a gap as an absence of data: `perp_collector_cycles` is what makes a
missed poll visible, because `perp_data_coverage_pct` is a pre-registered gate
clause on every arm and rows that were never written look exactly like a market
that did not exist.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Settings
from ..kalshi.client import KalshiClient
from ..kalshi.errors import AuthError
from ..models import (
    PerpCollectorCycle,
    PerpFundingObservation,
    PerpMarketSnapshot,
    PerpOrderbookSnapshot,
)

logger = logging.getLogger(__name__)

#: Keys a nested price object might carry its number under. Ordered by how
#: strongly each implies "this is the price", so a payload with several is read
#: the same way every time rather than by dict iteration order.
_PRICE_KEYS = ("price", "value", "amount", "mark", "mid", "last", "close")

#: Same idea for funding. `rate` first because that is what the quantity is.
_FUNDING_RATE_KEYS = ("funding_rate", "rate", "funding", "value", "amount")
_FUNDING_TIME_KEYS = ("settled_at", "settlement_time", "funding_time", "timestamp",
                      "ts", "time", "date", "period_end", "end_ts")
_FUNDING_TICKER_KEYS = ("ticker", "market_ticker", "symbol", "market")


def _num(value: Any) -> float | None:
    """A float, or None. Kalshi returns most perp numerics as decimal STRINGS.

    Booleans are rejected explicitly: `float(True)` is 1.0, and a bool sliding
    into a price column would be a silent corruption rather than a visible gap.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _nested_price(obj: Any) -> float | None:
    """Pull a number out of a nested price object, or give up honestly.

    The survey recorded `reference_price` and friends as `dict` and did NOT read
    their contents, so this cannot be written against a known schema. It accepts
    a bare number, and otherwise tries the ordered key list one level deep. It
    does NOT recurse arbitrarily or take "the first number it finds": on an
    object holding several numbers that would pick one by luck, and being wrong
    about the index price is worse than not having it.
    """
    direct = _num(obj)
    if direct is not None:
        return direct
    if isinstance(obj, dict):
        for key in _PRICE_KEYS:
            found = _num(obj.get(key))
            if found is not None:
                return found
    return None


def _first_key(row: dict, keys: Iterable[str]) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _as_datetime(value: Any) -> datetime | None:
    """A timezone-aware UTC datetime from an epoch or an ISO string, or None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        # Kalshi mixes seconds and milliseconds across endpoints; anything past
        # the year ~2286 in seconds is milliseconds.
        seconds = float(value)
        if seconds > 1e11:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _premium_bps(mark: float | None, index: float | None) -> float | None:
    """(mark - index) / index in basis points, or None.

    Guarded on a zero or absent index because a divide-by-zero here would not
    just crash the cycle — a silently-infinite premium is arm A's entry signal.
    """
    if mark is None or index is None or index == 0:
        return None
    return (mark - index) / index * 10_000.0


def _depth(levels: Any) -> float | None:
    """Total size across one side of a book.

    Accepts the two shapes an order book realistically arrives in — a list of
    [price, size] pairs, or a list of objects — and returns None for anything
    else rather than a misleading zero. The book's exact shape has not been read
    (the survey confirmed the endpoint is READABLE but did not dump its body),
    so `raw_json` remains authoritative.
    """
    if not isinstance(levels, list) or not levels:
        return None
    total = 0.0
    seen = False
    for level in levels:
        size = None
        if isinstance(level, (list, tuple)) and len(level) >= 2:
            size = _num(level[1])
        elif isinstance(level, dict):
            size = _num(_first_key(level, ("size", "quantity", "qty", "amount")))
        if size is not None:
            total += size
            seen = True
    return total if seen else None


def _best(levels: Any, *, side: str) -> float | None:
    """Best price on one side: the highest bid, the lowest ask."""
    if not isinstance(levels, list) or not levels:
        return None
    prices = []
    for level in levels:
        if isinstance(level, (list, tuple)) and level:
            price = _num(level[0])
        elif isinstance(level, dict):
            price = _num(_first_key(level, ("price", "value")))
        else:
            price = _num(level)
        if price is not None:
            prices.append(price)
    if not prices:
        return None
    return max(prices) if side == "bid" else min(prices)


class PerpsCollector:
    """Polls the perp surface and writes the tape. Reads only."""

    def __init__(self, client: KalshiClient, settings: Settings):
        self._client = client
        self._settings = settings
        #: Funding is fetched on its own, slower schedule — it settles every 8
        #: hours, so polling it per cycle would be thousands of redundant calls
        #: for the same rows.
        self._funding_due_at: datetime | None = None

    # -- universe ----------------------------------------------------------
    def _wanted(self) -> set[str] | None:
        """The configured asset filter, or None for "everything readable".

        Matching is by SUBSTRING of the ticker, which the survey showed looks
        like `KXAAVEPERP`. `docs/EXPERIMENT_OS_ISSUES.md` XOS-000009 records what
        substring matching costs when it is used as a blocklist; here it is an
        allowlist over a small, uniform namespace, and the resolved universe is
        recorded per cycle so an over-match is visible rather than assumed away.
        """
        raw = (self._settings.perps_assets or "").strip()
        if not raw:
            return None
        return {a.strip().upper() for a in raw.split(",") if a.strip()}

    def _markets(self) -> list[dict]:
        payload = self._client.get_perp_markets(limit=self._settings.perps_market_limit)
        markets = payload.get("markets") if isinstance(payload, dict) else None
        if not isinstance(markets, list):
            return []
        wanted = self._wanted()
        if wanted is None:
            return [m for m in markets if isinstance(m, dict)]
        out = []
        for market in markets:
            if not isinstance(market, dict):
                continue
            ticker = str(market.get("ticker") or "").upper()
            if any(asset in ticker for asset in wanted):
                out.append(market)
        return out

    # -- one cycle ---------------------------------------------------------
    def run_once(self, session, *, now: datetime | None = None) -> PerpCollectorCycle:
        """Poll once and write the tape. Never raises except on AuthError.

        AuthError is deliberately NOT swallowed: the worker treats it as
        fail-closed and shuts down, and a collector that quietly ate it would
        hide a credential problem affecting every book in the process.
        """
        at = now or datetime.now(timezone.utc)
        # The counters are initialised HERE, not left to the column defaults: a
        # SQLAlchemy `default=` is applied at INSERT, so the attributes are None
        # on the in-memory object and `cycle.errors += 1` would raise inside the
        # very handler that exists to stop this collector raising.
        cycle = PerpCollectorCycle(
            started_at=at, markets_seen=0, market_snapshots=0,
            orderbook_snapshots=0, funding_rows=0, errors=0,
        )
        notes: dict[str, Any] = {}
        session.add(cycle)

        try:
            markets = self._markets()
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001 — an instrument never kills a cycle
            cycle.errors += 1
            notes["markets"] = f"{type(exc).__name__}: {exc}"[:400]
            cycle.finished_at = datetime.now(timezone.utc)
            cycle.notes_json = notes
            logger.warning("perps: market listing failed", exc_info=True)
            return cycle

        cycle.markets_seen = len(markets)
        tickers: list[str] = []

        for market in markets:
            ticker = str(market.get("ticker") or "").strip()
            if not ticker:
                continue
            tickers.append(ticker)
            try:
                session.add(self._snapshot(ticker, market, at))
                cycle.market_snapshots += 1
            except Exception as exc:  # noqa: BLE001
                cycle.errors += 1
                notes.setdefault("snapshots", []).append(f"{ticker}: {exc}"[:200])

        if self._settings.perps_orderbook_enabled:
            for ticker in tickers[: self._settings.perps_orderbook_max_markets]:
                try:
                    book = self._client.get_perp_orderbook(ticker)
                    session.add(self._orderbook(ticker, book, at))
                    cycle.orderbook_snapshots += 1
                except AuthError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    cycle.errors += 1
                    notes.setdefault("orderbooks", []).append(f"{ticker}: {exc}"[:200])

        if self._funding_is_due(at):
            try:
                cycle.funding_rows += self._collect_funding(
                    session, at, notes, probe_ticker=_busiest(markets),
                )
                self._funding_due_at = at + timedelta(
                    minutes=self._settings.perps_funding_interval_minutes
                )
            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001
                cycle.errors += 1
                notes["funding"] = f"{type(exc).__name__}: {exc}"[:400]
                # Back off on failure too, so a permanently-400ing endpoint is
                # not retried every single cycle forever.
                self._funding_due_at = at + timedelta(
                    minutes=self._settings.perps_funding_interval_minutes
                )
                logger.warning("perps: funding fetch failed", exc_info=True)

        cycle.finished_at = datetime.now(timezone.utc)
        cycle.notes_json = notes or None
        logger.info(
            "perps tape cycle",
            extra={"extra_fields": {
                "markets": cycle.markets_seen,
                "snapshots": cycle.market_snapshots,
                "books": cycle.orderbook_snapshots,
                "funding": cycle.funding_rows,
                "errors": cycle.errors,
            }},
        )
        return cycle

    # -- row builders ------------------------------------------------------
    def _snapshot(self, ticker: str, market: dict, at: datetime) -> PerpMarketSnapshot:
        reference = market.get("reference_price")
        settlement = market.get("settlement_mark_price")
        index = _nested_price(reference)
        # The mark: the settlement mark price if it resolves, otherwise the last
        # traded price. Both are recorded separately; this is only which one the
        # premium is computed against, and the fallback is stated rather than
        # hidden because it changes what `premium_bps` MEANS on those rows.
        mark = _nested_price(settlement)
        if mark is None:
            mark = _num(market.get("price"))
        return PerpMarketSnapshot(
            ticker=ticker,
            captured_at=at,
            status=str(market.get("status"))[:32] if market.get("status") else None,
            bid=_num(market.get("bid")),
            ask=_num(market.get("ask")),
            price=_num(market.get("price")),
            open_interest=_num(market.get("open_interest")),
            open_interest_notional_usd=_num(
                market.get("open_interest_notional_value_dollars")
            ),
            volume=_num(market.get("volume")),
            volume_24h=_num(market.get("volume_24h")),
            reference_price=index,
            settlement_mark_price=_nested_price(settlement),
            premium_bps=_premium_bps(mark, index),
            reference_price_json=reference if isinstance(reference, dict) else None,
            settlement_mark_price_json=settlement if isinstance(settlement, dict) else None,
            raw_json=market,
        )

    def _orderbook(self, ticker: str, payload: dict, at: datetime) -> PerpOrderbookSnapshot:
        book = payload.get("orderbook") if isinstance(payload, dict) else None
        book = book if isinstance(book, dict) else (payload if isinstance(payload, dict) else {})
        bids = _first_key(book, ("bids", "bid", "buy", "yes"))
        asks = _first_key(book, ("asks", "ask", "sell", "no"))
        bid_depth = _depth(bids)
        ask_depth = _depth(asks)
        total = (bid_depth or 0.0) + (ask_depth or 0.0)
        return PerpOrderbookSnapshot(
            ticker=ticker,
            captured_at=at,
            best_bid=_best(bids, side="bid"),
            best_ask=_best(asks, side="ask"),
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            # None, not 0.5, when the book is empty on both sides: an empty book
            # is not a balanced one, and arm C would read the difference.
            depth_imbalance=((bid_depth or 0.0) / total) if total > 0 else None,
            raw_json=payload,
        )

    # -- funding -----------------------------------------------------------
    def _funding_is_due(self, at: datetime) -> bool:
        if not self._settings.perps_funding_enabled:
            return False
        return self._funding_due_at is None or at >= self._funding_due_at

    def _collect_funding(
        self, session, at: datetime, notes: dict[str, Any], *,
        probe_ticker: str | None = None,
    ) -> int:
        """Fetch the recent funding window and store whatever comes back.

        The response shape is UNKNOWN — the worker's 200s have carried no records
        — so this walks the payload for a list of objects rather than indexing a
        key it cannot know exists, and every row keeps its raw payload. When the
        first non-empty payload lands, this is the function to correct against it.

        WHY A ZERO-ROW PARSE IS RECORDED
        --------------------------------
        Production returns 200 here and this function finds nothing in it, which
        has two very different causes: an empty MARKET-WIDE rates feed (arm B is
        blocked on Kalshi, and we should stop) or an empty ACCOUNT funding-payment
        ledger (we hold no perp positions, so empty is exactly right, and arm B is
        blocked on us having no other source). Discarding the payload — the one
        case where its unknown shape matters most — leaves that undecidable, so
        the envelope's SHAPE is recorded against the cycle.

        Keys only, never values: this endpoint is authenticated and the leading
        hypothesis is that it returns our own account history, so its contents are
        private evidence and `perp_collector_cycles.notes_json` is read through
        the public ops channel.

        THE TICKER-SCOPED RETRY
        -----------------------
        The first shape read came back `{"funding_history": []}` — one key, an
        empty list, on a call that passed NO ticker. Empty is what an account
        ledger returns for an account with no perp positions; it is also what a
        market-wide feed would return if the filter it actually wants was never
        supplied. Those are still the two readings, so before arm B is called
        BLOCKED_DATA the endpoint is asked ONE more way: scoped to a real ticker
        from the universe we just listed. If that carries rows it is a rates feed
        and we were asking wrong; if it is empty too, we asked both ways.

        One extra call per funding interval, only when the unscoped call found
        nothing, and it stops entirely the moment either call returns rows.
        """
        lookback = timedelta(days=self._settings.perps_funding_lookback_days)
        payload = self._client.get_perp_funding_history(
            start_date=(at - lookback).strftime("%Y-%m-%d"),
            end_date=at.strftime("%Y-%m-%d"),
        )
        rows = _funding_rows(payload)
        if not rows:
            notes["funding_shape"] = _payload_shape(payload)
            if probe_ticker:
                # Guarded separately: this retry is a diagnostic, and letting it
                # fail the whole funding stage would discard the unscoped shape
                # above — the finding it exists to refine.
                try:
                    scoped = self._client.get_perp_funding_history(
                        start_date=(at - lookback).strftime("%Y-%m-%d"),
                        end_date=at.strftime("%Y-%m-%d"),
                        ticker=probe_ticker,
                    )
                except AuthError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    notes["funding_shape_by_ticker"] = {
                        "ticker": probe_ticker,
                        "error": f"{type(exc).__name__}: {exc}"[:200],
                    }
                else:
                    rows = _funding_rows(scoped)
                    notes["funding_shape_by_ticker"] = {
                        "ticker": probe_ticker, **_payload_shape(scoped),
                    }
        written = 0
        seen: set[str] = set()
        for row in rows:
            key = _funding_key(row)
            if key in seen:
                continue
            seen.add(key)
            observed = _as_datetime(_first_key(row, _FUNDING_TIME_KEYS))
            ticker = _first_key(row, _FUNDING_TICKER_KEYS)
            session.add(PerpFundingObservation(
                ticker=str(ticker)[:64] if ticker else None,
                observed_at=observed,
                captured_at=at,
                funding_rate=_num(_first_key(row, _FUNDING_RATE_KEYS)),
                source_key=key,
                raw_json=row,
            ))
            written += 1
        return written


def _funding_rows(payload: Any) -> list[dict]:
    """The list of funding records inside an unknown payload.

    Takes the first list-of-objects it finds one level down, and accepts a bare
    list at the top. Anything else yields nothing — which is recorded as zero
    rows against a cycle that ran, not as a silent success.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and any(isinstance(r, dict) for r in value):
                return [r for r in value if isinstance(r, dict)]
    return []


def _busiest(markets: list[dict]) -> str | None:
    """The ticker with the most open interest, for the funding diagnostic.

    Which market the ticker-scoped funding probe asks about decides how much its
    answer is worth. The first ticker in the listing is alphabetical — the first
    run drew `KXAAVEPERP` — and "we found no funding on an illiquid market" is a
    much weaker statement than "we found none on the busiest one". Falls back to
    the first ticker when no market carries a readable open interest.
    """
    best: str | None = None
    best_oi = float("-inf")
    for market in markets:
        ticker = str(market.get("ticker") or "").strip()
        if not ticker:
            continue
        if best is None:
            best = ticker
        oi = _num(market.get("open_interest_notional_value_dollars"))
        if oi is None:
            oi = _num(market.get("open_interest"))
        if oi is not None and oi > best_oi:
            best_oi, best = oi, ticker
    return best


def _payload_shape(payload: Any, *, max_keys: int = 40) -> dict[str, Any]:
    """A keys-only description of an unknown payload.

    Deliberately carries NO values. Sizes are recorded because "the key exists
    and its list is empty" and "the key is absent" are the two answers that
    separate an empty rates feed from an empty payments ledger.
    """
    if isinstance(payload, dict):
        keys = sorted(str(k) for k in payload)[:max_keys]
        lists = {
            str(k): len(v)
            for k, v in payload.items()
            if isinstance(v, list)
        }
        shape: dict[str, Any] = {"type": "object", "keys": keys}
        if lists:
            shape["list_lengths"] = dict(sorted(lists.items())[:max_keys])
        return shape
    if isinstance(payload, list):
        return {"type": "array", "length": len(payload)}
    return {"type": type(payload).__name__}


def _funding_key(row: dict) -> str:
    """A stable dedupe key from the row itself.

    Overlapping fetches are expected (the window is re-requested on a schedule),
    and a funding period counted twice would land directly in arm B's carry. An
    id-like field is used when present; otherwise the canonical JSON of the whole
    row is hashed, which is stable for an identical record and different for a
    corrected one.
    """
    explicit = _first_key(row, ("id", "funding_id", "uuid"))
    if explicit is not None:
        return str(explicit)[:128]
    blob = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:64]
