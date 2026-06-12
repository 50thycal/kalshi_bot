"""Polymarket (Gamma API) client for the cross-market signal book.

Polymarket runs the SAME daily temperature markets as Kalshi. The alignment probe
confirmed the bucket scheme + dates match, and that the settlement STATION matches
Kalshi for exactly three cities — LAX, MIA, AUS — so only those are safe to trade as
the same bet. This client discovers those open events and exposes Polymarket's implied
per-bucket probability, which the `weather_pm` paper book trades Kalshi toward.

Discovery is via the `weather` tag (a blind scan drowns in crypto 5-minute markets).
We read the public Gamma API only (no auth, no wallet, no Polymarket trading — the
platform is geofenced; we consume its prices purely as a signal for Kalshi).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

logger = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
UA = (  # Gamma is behind Cloudflare; a browser UA avoids the default-client block
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
LIMIT = 100  # Gamma caps page size

# Our city code -> Polymarket title aliases. Only the station-matched cities.
PM_CITY_ALIASES = {
    "LAX": ("los angeles",),
    "MIA": ("miami",),
    "AUS": ("austin",),
}
_MONTHS = {
    m: i + 1 for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"]
    )
}
_NUM = re.compile(r"\d+(?:\.\d+)?")  # unsigned: the range hyphen is not a minus sign
_DATE = re.compile(r"\b([a-z]+)\s+(\d{1,2})\b", re.I)


@dataclass(frozen=True)
class PmBucket:
    low_f: float | None
    high_f: float | None
    subtitle: str
    yes_prob: float  # 0..1 implied probability of this bucket


def match_city(text: str, allowed: set[str]) -> str | None:
    low = (text or "").lower()
    for code, aliases in PM_CITY_ALIASES.items():
        if code in allowed and any(a in low for a in aliases):
            return code
    return None


def parse_kind(text: str) -> str | None:
    low = (text or "").lower()
    if "highest" in low or "high temp" in low:
        return "high"
    if "lowest" in low or "low temp" in low:
        return "low"
    return None


def parse_date(text: str, *, now: datetime) -> str | None:
    m = _DATE.search(text or "")
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    try:
        d = datetime(now.year, month, int(m.group(2)), tzinfo=timezone.utc).date()
    except ValueError:
        return None
    if (now.date() - d).days > 2:  # title month wrapped into next year
        try:
            d = datetime(now.year + 1, month, int(m.group(2)), tzinfo=timezone.utc).date()
        except ValueError:
            return None
    return d.isoformat()


def parse_bucket(label: str) -> tuple[float | None, float | None] | None:
    if not label:
        return None
    low = label.lower()
    nums = [float(x) for x in _NUM.findall(label)]
    if not nums:
        return None
    if any(w in low for w in ("or above", "or higher", "≥", ">=", "above", "or more")):
        return (nums[0], None)
    if any(w in low for w in ("or below", "or lower", "≤", "<=", "below", "or less", "under")):
        return (None, nums[0])
    if len(nums) >= 2:
        return (min(nums[:2]), max(nums[:2]))
    return (nums[0], nums[0])


def _yes_prob(market: dict) -> float | None:
    """Polymarket implied P(Yes): prefer best bid/ask midpoint, fall back to last price."""
    bid, ask = market.get("bestBid"), market.get("bestAsk")
    try:
        if bid is not None and ask is not None:
            return (float(bid) + float(ask)) / 2.0
    except (TypeError, ValueError):
        pass
    raw = market.get("outcomePrices")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if isinstance(raw, list) and raw:
        try:
            return float(raw[0])  # outcomes are [Yes, No]
        except (TypeError, ValueError):
            return None
    last = market.get("lastTradePrice")
    try:
        return float(last) if last is not None else None
    except (TypeError, ValueError):
        return None


class PolymarketClient:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_pages: int = 4,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=GAMMA,
            timeout=timeout,
            headers={"User-Agent": UA, "Accept": "application/json"},
            transport=transport,
        )
        self._max_pages = max_pages
        self._index: dict[tuple[str, str, str], list[PmBucket]] = {}
        self._fetched_at: datetime | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PolymarketClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def refresh(self, allowed_cities: set[str], *, now: datetime | None = None) -> int:
        """Rebuild the (city, kind, date) -> buckets index from the weather tag. Returns
        the number of indexed events. Raises on network failure (caller decides)."""
        now = now or datetime.now(timezone.utc)
        index: dict[tuple[str, str, str], list[PmBucket]] = {}
        for page in range(self._max_pages):
            resp = self._client.get(
                "/events",
                params={"closed": "false", "tag_slug": "weather",
                        "limit": LIMIT, "offset": page * LIMIT},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            for ev in batch:
                parsed = self._parse_event(ev, allowed_cities, now)
                if parsed is not None:
                    key, buckets = parsed
                    if buckets:
                        index[key] = buckets
            if len(batch) < LIMIT:
                break
        self._index = index
        self._fetched_at = now
        return len(index)

    @staticmethod
    def _parse_event(ev: dict, allowed: set[str], now: datetime):
        title = ev.get("title") or ""
        city = match_city(title, allowed)
        kind = parse_kind(title)
        iso = parse_date(title, now=now)
        if city is None or kind is None or iso is None:
            return None
        buckets: list[PmBucket] = []
        for mk in ev.get("markets") or []:
            label = mk.get("groupItemTitle") or mk.get("question") or ""
            rng = parse_bucket(label)
            prob = _yes_prob(mk)
            if rng is not None and prob is not None:
                buckets.append(PmBucket(rng[0], rng[1], label.strip(), prob))
        return (city, kind, iso), buckets

    def lookup(self, city: str, kind: str, target_date: date) -> list[PmBucket]:
        return self._index.get((city, kind, target_date.isoformat()), [])

    @property
    def fetched_at(self) -> datetime | None:
        return self._fetched_at
