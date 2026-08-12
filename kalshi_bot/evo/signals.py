"""External-signal metrics: the first DSL vocabulary that is not the order book.

Every metric the strategy language had was a property of Kalshi's own book — bid,
ask, spread, mid, volume, hours_to_close. So the only hypotheses an agent could
state were price patterns ("buy when cheap", "buy when the spread is tight"), and
on a roughly efficient book those earn the spread minus two fees. That is not a
tuning problem, it is the shape of the search space: the fleet ran 900+ backtests
inside a hypothesis space with almost nothing in it.

These two metrics change what an agent is able to SAY. Neither requires our own
forecast model to be any good, which is why they come first:

  pm_divergence   Polymarket's implied probability minus Kalshi's mid, in cents,
                  for the same weather bucket. Two venues price one event and
                  disagree; that disagreement is information Kalshi's book does not
                  contain. Positive => Kalshi's YES is cheap vs the other venue.
                  (A signal, not an arbitrage — we only trade one of the venues.)

  spot_vs_strike  Percent distance from the underlying's spot price to a crypto
                  market's decision boundary, signed so POSITIVE always means "YES
                  is currently winning" regardless of strike_type. One sign
                  convention, or an agent needs three rules to say one thing.

WHERE THE NUMBERS COME FROM. The evo worker does not call any external API. The
main worker already collects NWS, Open-Meteo, Coinbase and Polymarket into
provenance-labeled Postgres tables; this module reads those. That is deliberate,
not a shortcut: an API call made inside a heartbeat could never be replayed in a
backtest, so no strategy using it could be validated — the same trap as assuming a
maker order always fills. Reading a collected table means the live path and the
replay path see the same number by construction.

Nor is it stale. Measured lag at build time: Polymarket and the Kalshi bucket tape
2 min, Coinbase spot 6 min, ensembles 10 min — against sources that themselves
republish hourly (NWS) or six-hourly (ensemble runs). The DB copy refreshes faster
than the upstream data changes.

FAIL CLOSED. Feeds die quietly. Every signal here carries the age of its oldest
input, and past `signal_max_age_minutes` the value is dropped to None rather than
served. A None metric fails its condition (strategy_spec._metric_value), so a dead
feed blocks entries instead of authorizing trades on a number nobody refreshed.
Both legs of a difference must be fresh: a current Polymarket price against an
hour-old Kalshi mid is a fabricated divergence, not a signal.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..models import (
    CryptoLadderSnapshot,
    CryptoSpotCandle,
    PolymarketSnapshot,
    WeatherBucketSnapshot,
    WeatherForecast,
)
from .config import EvoSettings

logger = logging.getLogger(__name__)

# Every external-signal metric name. Kept here so the DSL, the dataset capability
# map and the prompt all agree on one list.
SIGNAL_METRICS = ("pm_divergence", "spot_vs_strike")

# Bucket bounds are whole or half degrees; this is well below the smallest real gap.
_EPS = 1e-6


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _fresh(ts: datetime | None, cutoff: datetime) -> bool:
    ts = _aware(ts)
    return ts is not None and ts >= cutoff


def spot_vs_strike(
    strike_type: str | None, floor: float | None, cap: float | None, spot: float | None
) -> float | None:
    """Percent distance from spot to the decision boundary, signed so positive means
    YES is currently winning. Pure function — the crypto backtest adapter calls this
    with historical spot so replay and live compute it identically.

      greater : (spot - floor) / floor           YES wins above the floor
      less    : (cap - spot)   / cap             YES wins below the cap
      between : distance to the NEARER edge, positive only when inside the band
    """
    if spot is None or not spot:
        return None
    if strike_type == "greater":
        if not floor:
            return None
        return 100.0 * (spot - floor) / floor
    if strike_type == "less":
        if not cap:
            return None
        return 100.0 * (cap - spot) / cap
    if strike_type == "between":
        if floor is None or cap is None or not spot:
            return None
        if floor <= spot <= cap:
            return 100.0 * min(spot - floor, cap - spot) / spot
        edge = floor if spot < floor else cap
        return -100.0 * abs(spot - edge) / spot
    return None


def _interval(low: float | None, high: float | None) -> tuple[float, float]:
    """A bucket labelled `low-high` catches temperatures that round into it, so
    `99-100` is the continuous span [98.5, 100.5). An open side is unbounded."""
    return (
        float("-inf") if low is None else float(low) - 0.5,
        float("inf") if high is None else float(high) + 0.5,
    )


def _same_bounds(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < _EPS


def pm_probability_for_bucket(
    low_f: float | None,
    high_f: float | None,
    ladder: list[tuple[float | None, float | None, float]],
) -> tuple[float, bool] | None:
    """Polymarket's implied probability for a KALSHI bucket, as (probability, exact).

    The two venues do not share a bucket grid. Measured in production: Austin's
    Kalshi ladder starts on odd degrees (99-100, 101-102, ...) while Polymarket's
    starts on even ones (98-99, 100-101, ...), so their bucket boundaries are
    disjoint sets and an equality join matches nothing there however fresh both
    feeds are. Only cities where the ladders happen to coincide ever produced a
    signal — 4 of 60 markets on the day this was measured.

    So re-bin instead of matching. Polymarket's ladder is a distribution over the
    same temperatures; a Kalshi bucket is a range of them. Overlap allocation:

        P(Kalshi 99-100) = 0.5 * P(Poly 98-99) + 0.5 * P(Poly 100-101)

    The estimate assumes mass is spread UNIFORMLY inside each Polymarket bucket.
    That is wrong in the third decimal — a peaked distribution puts more mass on
    the side nearer the mode — so an exact match is still used verbatim when one
    exists, and the caller is told which it got.

    Returns None rather than a partial sum in the two cases where re-binning would
    invent information:
      - the ladder does not fully cover the Kalshi bucket (a half-covered bucket
        would report about half its true probability, i.e. a large fake NEGATIVE
        divergence — the direction that makes an agent sell);
      - the bucket would have to split an open-ended tail, whose mass has no
        defined shape.
    """
    # An exact match is not an estimate; prefer it, tails included.
    for lo, hi, prob in ladder:
        if _same_bounds(lo, low_f) and _same_bounds(hi, high_f):
            return (float(prob), True)

    # Re-binning needs a bounded target: an open Kalshi tail has no finite span.
    if low_f is None or high_f is None:
        return None
    lo_t, hi_t = _interval(low_f, high_f)
    span = hi_t - lo_t
    if span <= 0:
        return None

    total = 0.0
    covered = 0.0
    for lo, hi, prob in ladder:
        lo_b, hi_b = _interval(lo, hi)
        overlap = min(hi_t, hi_b) - max(lo_t, lo_b)
        if overlap <= _EPS:
            continue
        if lo is None or hi is None:
            return None  # would have to split a tail
        width = hi_b - lo_b
        if width <= 0:
            continue
        total += float(prob) * (overlap / width)
        covered += overlap
    if abs(covered - span) > _EPS:
        return None  # ladder does not cover the bucket
    return (total, False)


def _pm_divergence(session, tickers: list[str], cutoff: datetime) -> dict[str, float]:
    """Difference each Kalshi weather bucket against Polymarket's ladder for the
    same (city, kind, target_date).

    `weather_bucket_snapshots` carries the Kalshi side including the market_ticker;
    target_date comes from `weather_forecasts` via the shared event_ticker (bucket
    snapshots do not store it). The bucket-level match is `pm_probability_for_bucket`,
    which re-bins across the two venues' misaligned grids rather than requiring
    identical bounds the way the weather tracker's `_pm_best_bucket` does.
    """
    latest_bucket = (
        select(
            WeatherBucketSnapshot.market_ticker,
            func.max(WeatherBucketSnapshot.captured_at).label("captured_at"),
        )
        .where(
            WeatherBucketSnapshot.market_ticker.in_(tickers),
            WeatherBucketSnapshot.captured_at >= cutoff,
            WeatherBucketSnapshot.mid_cents.isnot(None),
        )
        .group_by(WeatherBucketSnapshot.market_ticker)
        .subquery()
    )
    rows = list(
        session.execute(
            select(
                WeatherBucketSnapshot.market_ticker,
                WeatherBucketSnapshot.event_ticker,
                WeatherBucketSnapshot.city,
                WeatherBucketSnapshot.kind,
                WeatherBucketSnapshot.low_f,
                WeatherBucketSnapshot.high_f,
                WeatherBucketSnapshot.mid_cents,
            ).join(
                latest_bucket,
                (WeatherBucketSnapshot.market_ticker == latest_bucket.c.market_ticker)
                & (WeatherBucketSnapshot.captured_at == latest_bucket.c.captured_at),
            )
        )
    )
    if not rows:
        return {}

    events = {r[1] for r in rows if r[1]}
    target_by_event = {
        event: target
        for event, target in session.execute(
            select(WeatherForecast.event_ticker, func.max(WeatherForecast.target_date))
            .where(WeatherForecast.event_ticker.in_(events))
            .group_by(WeatherForecast.event_ticker)
        )
    }

    # One current Polymarket price per (city, kind, target_date, low_f, high_f).
    pm: dict[tuple, float] = {}
    pm_rows = session.execute(
        select(
            PolymarketSnapshot.city,
            PolymarketSnapshot.kind,
            PolymarketSnapshot.target_date,
            PolymarketSnapshot.low_f,
            PolymarketSnapshot.high_f,
            PolymarketSnapshot.yes_prob,
            PolymarketSnapshot.captured_at,
        ).where(
            PolymarketSnapshot.captured_at >= cutoff,
            PolymarketSnapshot.yes_prob.isnot(None),
        )
    )
    seen_at: dict[tuple, datetime] = {}
    for city, kind, target, low, high, prob, cap_at in pm_rows:
        key = (city, kind, target, low, high)
        cap_at = _aware(cap_at)
        if key not in seen_at or (cap_at and cap_at > seen_at[key]):
            seen_at[key] = cap_at
            pm[key] = float(prob)

    # Group into one ladder per event so a bucket can be re-binned against the whole
    # distribution rather than looked up by its exact bounds.
    ladders: dict[tuple, list[tuple[float | None, float | None, float]]] = {}
    for (city, kind, target, low, high), prob in pm.items():
        ladders.setdefault((city, kind, target), []).append((low, high, prob))

    out: dict[str, float] = {}
    exact_n = est_n = 0
    for ticker, event, city, kind, low, high, mid in rows:
        ladder = ladders.get((city, kind, target_by_event.get(event)))
        if not ladder or mid is None:
            continue
        got = pm_probability_for_bucket(low, high, ladder)
        if got is None:
            continue
        prob, exact = got
        exact_n, est_n = (exact_n + 1, est_n) if exact else (exact_n, est_n + 1)
        out[ticker] = round(prob * 100.0 - float(mid), 4)
    if rows:
        logger.info("evo pm_divergence coverage", extra={"extra_fields": {
            "kalshi_buckets": len(rows), "pm_buckets": len(pm),
            "matched_exact": exact_n, "matched_rebinned": est_n}})
    return out


def _spot_vs_strike(session, tickers: list[str], cutoff: datetime) -> dict[str, float]:
    latest = (
        select(
            CryptoLadderSnapshot.market_ticker,
            func.max(CryptoLadderSnapshot.captured_at).label("captured_at"),
        )
        .where(
            CryptoLadderSnapshot.market_ticker.in_(tickers),
            CryptoLadderSnapshot.captured_at >= cutoff,
        )
        .group_by(CryptoLadderSnapshot.market_ticker)
        .subquery()
    )
    rows = list(
        session.execute(
            select(
                CryptoLadderSnapshot.market_ticker,
                CryptoLadderSnapshot.series,
                CryptoLadderSnapshot.strike_type,
                CryptoLadderSnapshot.floor_strike,
                CryptoLadderSnapshot.cap_strike,
            ).join(
                latest,
                (CryptoLadderSnapshot.market_ticker == latest.c.market_ticker)
                & (CryptoLadderSnapshot.captured_at == latest.c.captured_at),
            )
        )
    )
    if not rows:
        return {}

    # Spot must be fresh in its own right — it is the one genuinely continuous feed
    # here, so a stale close is the likeliest way this metric goes quietly wrong.
    spot: dict[str, float] = {
        product: float(close)
        for product, close in session.execute(
            select(CryptoSpotCandle.product, CryptoSpotCandle.close)
            .where(CryptoSpotCandle.minute_ts >= cutoff)
            .order_by(CryptoSpotCandle.minute_ts)
        )
    }

    out: dict[str, float] = {}
    for ticker, series, strike_type, floor, cap in rows:
        product = _product_for(series, ticker)
        value = spot_vs_strike(strike_type, floor, cap, spot.get(product))
        if value is not None:
            out[ticker] = round(value, 4)
    return out


def _product_for(series: str | None, ticker: str | None) -> str | None:
    """Kalshi crypto series -> Coinbase product. Only BTC/ETH have a spot feed."""
    s = (series or ticker or "").upper()
    if s.startswith("KXBTC"):
        return "BTC-USD"
    if s.startswith("KXETH"):
        return "ETH-USD"
    return None


def compute_signals(
    session,
    tickers: list[str],
    *,
    now: datetime | None = None,
    settings: EvoSettings,
) -> dict[str, dict[str, float | None]]:
    """{ticker: {metric: value}} for the tickers that have a fresh, matched signal.

    Bulk — three or four indexed queries for the whole cycle's universe, not one per
    ticker. Absent entries mean "no signal", which the interpreter treats as a failed
    condition rather than a zero."""
    if not tickers:
        return {}
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=settings.signal_max_age_minutes)
    out: dict[str, dict[str, float | None]] = {}
    for metric, fn in (("pm_divergence", _pm_divergence),
                       ("spot_vs_strike", _spot_vs_strike)):
        try:
            for ticker, value in fn(session, tickers, cutoff).items():
                out.setdefault(ticker, {})[metric] = value
        except Exception:  # noqa: BLE001 — a signal failure must not stop the cycle
            logger.exception("evo signal computation failed", extra={
                "extra_fields": {"metric": metric}})
    return out
