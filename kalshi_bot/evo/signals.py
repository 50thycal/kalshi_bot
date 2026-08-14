"""External-signal metrics: the DSL vocabulary that is not the order book.

Every metric the strategy language started with was a property of Kalshi's own book — bid,
ask, spread, mid, volume, hours_to_close. So the only hypotheses an agent could state were
price patterns ("buy when cheap", "buy when the spread is tight"), and on a roughly efficient
book those earn the spread minus two fees. That is the shape of the search space, not a tuning
problem: the fleet ran 900+ backtests inside a hypothesis space with almost nothing in it.

  spot_vs_strike  Percent distance from the underlying's spot price to a crypto market's
                  decision boundary, signed so POSITIVE always means "YES is currently
                  winning" regardless of strike_type. One sign convention, or an agent needs
                  three rules to say one thing.

RETIRED — `pm_divergence` (Polymarket's implied probability minus our mid). It shipped here on
an assumption-free premise: "do two venues disagree about the same event?" needs no forecasting
skill of ours. The premise was then TESTED (`docs/PMDIV_THESIS.md`, 39,740 cycles / 198 settled
events) and refuted. pm_better% by divergence band ran 27% -> 9% -> 1% as disagreement grew: the
further Polymarket strays from the Kalshi price, the more reliably POLYMARKET is wrong. A large
`pm_divergence` was an ANTI-signal, so gating on it meant trading toward the mistaken venue. Our
own NWS/ensemble forecast produced the identical shape the same day (0%/4% in its outer bands).
On Kalshi weather the market is the best forecaster we have access to, and "X disagrees with the
market" is evidence against X. Polymarket's raw prices remain readable via inspect_data — what
was withdrawn is the claim that differencing them against our mid is an edge.

WHERE THE NUMBERS COME FROM. The evo worker does not call any external API. The main worker
already collects Coinbase spot and the crypto ladders into provenance-labeled Postgres tables;
this module reads those. That is deliberate, not a shortcut: an API call made inside a heartbeat
could never be replayed in a backtest, so no strategy using it could be validated — the same trap
as assuming a maker order always fills. Reading a collected table means the live path and the
replay path see the same number by construction.

FAIL CLOSED. Feeds die quietly. Past `signal_max_age_minutes` a value is dropped to None rather
than served. A None metric fails its condition (strategy_spec._metric_value), so a dead feed
blocks entries instead of authorizing trades on a number nobody refreshed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..models import CryptoLadderSnapshot, CryptoSpotCandle
from .config import EvoSettings

logger = logging.getLogger(__name__)

# Every external-signal metric name. Kept here so the DSL, the dataset capability
# map and the prompt all agree on one list.
SIGNAL_METRICS = ("spot_vs_strike",)



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
    for metric, fn in (("spot_vs_strike", _spot_vs_strike),):
        try:
            for ticker, value in fn(session, tickers, cutoff).items():
                out.setdefault(ticker, {})[metric] = value
        except Exception:  # noqa: BLE001 — a signal failure must not stop the cycle
            logger.exception("evo signal computation failed", extra={
                "extra_fields": {"metric": metric}})
    return out
