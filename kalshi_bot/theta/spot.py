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
from datetime import datetime, timedelta, timezone

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


def refresh_spot_model(session, spot_client, product: str, *, trail_days: float,
                       retention_days: float | None = None) -> SpotModel | None:
    """Gap-fetch + persist 1-min closes for `product` and build a SpotModel over the trailing
    window, or None if the persisted window is too thin. Shared by the theta and tfav books so
    both price off ONE persisted spot window (crypto_spot_candles) — whichever book runs first
    this cycle pays the fetch; the other reads the just-stored closes for free."""
    from .. import repository as repo

    now = datetime.now(timezone.utc)
    trail_start = now - timedelta(days=trail_days)
    latest = repo.latest_spot_minute(session, product)
    fetch_from = trail_start if latest is None else latest
    if fetch_from.tzinfo is None:
        fetch_from = fetch_from.replace(tzinfo=timezone.utc)
    start_unix = int(fetch_from.timestamp())
    end_unix = int(now.timestamp())
    if end_unix - start_unix >= 60:
        closes = spot_client.candles(product, start_unix, end_unix)
        if closes:
            repo.insert_spot_candles(session, product, closes)
    # Prune on RETENTION, not on the model window. The two were the same number until it
    # emerged that 6 days of closes cannot support a tail fit at all
    # (Settings.theta_spot_retention_days). Keeping more history changes nothing the model
    # reads — `load_spot_closes` below is still bounded by `trail_start`.
    keep = trail_days + 1.0 if retention_days is None else max(retention_days, trail_days + 1.0)
    repo.prune_spot_candles(session, product, now - timedelta(days=keep))
    stored = repo.load_spot_closes(session, product, trail_start)
    if len(stored) < SpotModel.MIN_SAMPLES:
        return None
    return SpotModel(stored, trail_days=trail_days)


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

    def returns(self, ts_unix: int, h_min: int) -> list[float]:
        """Public accessor so a caller can compute several variant probabilities from one
        pass over the trailing window (the per-cycle hot path)."""
        return self._returns(ts_unix, h_min)

    @staticmethod
    def prob_from_returns(
        rets: list[float],
        spot: float,
        strike_type: str,
        floor: float | None,
        cap: float | None,
        vol_mult: float = 1.0,
    ) -> float | None:
        """P(YES) from a precomputed return sample. `vol_mult` scales the distribution's
        width: evaluating r*k against threshold x is identical to evaluating r against
        x/k, so widening (k>1) is a cheap threshold rescale, not a re-simulation."""
        if not rets or len(rets) < SpotModel.MIN_SAMPLES or spot is None or spot <= 0:
            return None
        k = vol_mult if vol_mult and vol_mult > 0 else 1.0
        n = len(rets)
        try:
            if strike_type == "greater" and floor:
                x = math.log(float(floor) / spot) / k
                return sum(1 for r in rets if r > x) / n
            if strike_type == "less" and cap:
                x = math.log(float(cap) / spot) / k
                return sum(1 for r in rets if r < x) / n
            if strike_type == "between" and floor and cap:
                lo_x = math.log(float(floor) / spot) / k
                hi_x = math.log(float(cap) / spot) / k
                return sum(1 for r in rets if lo_x <= r <= hi_x) / n
        except (TypeError, ValueError):
            return None
        return None

    def realized_vol_bps(self, ts_unix: int, lookback_min: int) -> float | None:
        """Sample sd of 1-minute log returns over the trailing lookback, in basis points/minute.

        Per-minute rather than per-window so the number means the same thing whatever horizon
        it is read against, and it is the natural unit for comparing a 15-minute regime to a
        4-hour one. Needs at least 10 returns; returns None rather than a number built from
        three points."""
        rets: list[float] = []
        for k in range(lookback_min):
            a = self.closes.get((ts_unix - (k + 1) * 60) // 60 * 60)
            b = self.closes.get((ts_unix - k * 60) // 60 * 60)
            if a and b and a > 0 and b > 0:
                rets.append(math.log(b / a))
        n = len(rets)
        if n < 10:
            return None
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        return math.sqrt(var) * 1e4

    def trailing_move_bps(self, ts_unix: int, lookback_min: int) -> float | None:
        """SIGNED log move over the trailing lookback, in basis points.

        Signed on purpose: a tail sold into a rally and one sold into a selloff are different
        trades, and the tail diagnosis could only bucket on |move| because nothing recorded the
        direction (`docs/RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md` §2.5)."""
        now = self.spot_at(ts_unix)
        then = self.spot_at(ts_unix - lookback_min * 60, max_stale_min=lookback_min)
        if not now or not then or now <= 0 or then <= 0:
            return None
        return math.log(now / then) * 1e4

    def p_yes(
        self,
        ts_unix: int,
        h_min: int,
        strike_type: str,
        floor: float | None,
        cap: float | None,
        vol_mult: float = 1.0,
    ) -> float | None:
        """P(YES) for the strike over the next h_min minutes; None if unpriceable."""
        if h_min <= 0:
            return None
        s = self.spot_at(ts_unix)
        if s is None or s <= 0:
            return None
        rets = self._returns(ts_unix, h_min)
        return self.prob_from_returns(rets, s, strike_type, floor, cap, vol_mult)
