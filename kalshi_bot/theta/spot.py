"""Spot feed + probability model for the theta book.

CoinbaseSpotClient pulls public 1-minute candles (no auth; the BRRNY settlement index
aggregates the same major exchanges, so basis is cents-scale). SpotModel turns a rolling
window of 1-min closes into P(YES) for a threshold/range strike over the remaining
minutes to settlement: the fraction of trailing overlapping h-minute log-returns that
satisfy the strike condition from the current spot. Empirical, so fat tails come from
the data — no Gaussian assumption. Same math as the validation probe
(scripts/kalshi_theta_study.py, kept self-contained there per ops-script convention).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

COINBASE_BASE = "https://api.exchange.coinbase.com"


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CoinbaseSpotClient:
    """Public Coinbase Exchange candles (300 x 1-min per call). Fail-soft: errors log
    and return what was fetched so far; the tracker self-gates on missing data."""

    def __init__(self, *, timeout: float = 15.0, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=COINBASE_BASE,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "kalshi-bot theta (research)", "Accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def candles(self, product: str, start_unix: int, end_unix: int) -> dict[int, float]:
        """{minute_unix: close} over [start, end], chunked to Coinbase's 300-candle cap."""
        out: dict[int, float] = {}
        s = start_unix
        while s < end_unix:
            e = min(s + 300 * 60, end_unix)
            try:
                resp = self._client.get(
                    f"/products/{product}/candles",
                    params={"granularity": 60, "start": _iso(s), "end": _iso(e)},
                )
                resp.raise_for_status()
                for row in resp.json() or []:
                    # [time, low, high, open, close, volume]
                    out[int(row[0])] = float(row[4])
            except Exception as exc:  # noqa: BLE001 — fail soft, partial data is fine
                logger.warning(
                    "coinbase candles fetch failed",
                    extra={"extra_fields": {"product": product, "error": str(exc)[:200]}},
                )
                break
            s = e
        return out


class SpotModel:
    """Empirical remaining-window return distribution over trailing 1-min closes."""

    MIN_SAMPLES = 300  # refuse to price off a thin distribution

    def __init__(self, closes: dict[int, float], trail_days: float = 5.0):
        self.closes = closes
        self.ts_sorted = sorted(closes)
        self.trail = int(trail_days * 86400)

    def spot_at(self, ts_unix: int, max_stale_min: int = 6) -> float | None:
        m = ts_unix // 60 * 60
        for k in range(0, max_stale_min * 60, 60):
            if (m - k) in self.closes:
                return self.closes[m - k]
        return None

    def _returns(self, ts_unix: int, h_min: int) -> list[float]:
        lo, hi = ts_unix - self.trail, ts_unix
        h = h_min * 60
        out: list[float] = []
        for t in self.ts_sorted:
            if t < lo or t + h >= hi:
                continue
            a, b = self.closes.get(t), self.closes.get(t + h)
            if a and b:
                out.append(math.log(b / a))
        return out

    def p_yes(
        self,
        ts_unix: int,
        h_min: int,
        strike_type: str,
        floor: float | None,
        cap: float | None,
    ) -> float | None:
        """P(YES) for the strike over the next h_min minutes; None if unpriceable."""
        if h_min <= 0:
            return None
        s = self.spot_at(ts_unix)
        if s is None or s <= 0:
            return None
        rets = self._returns(ts_unix, h_min)
        if len(rets) < self.MIN_SAMPLES:
            return None
        n = len(rets)
        try:
            if strike_type == "greater" and floor:
                x = math.log(float(floor) / s)
                return sum(1 for r in rets if r > x) / n
            if strike_type == "less" and cap:
                x = math.log(float(cap) / s)
                return sum(1 for r in rets if r < x) / n
            if strike_type == "between" and floor and cap:
                lo_x, hi_x = math.log(float(floor) / s), math.log(float(cap) / s)
                return sum(1 for r in rets if lo_x <= r <= hi_x) / n
        except (TypeError, ValueError):
            return None
        return None
