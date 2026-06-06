"""Deterministic signal scoring.

No LLM, no forecasting in the MVP. A market is scored 0..100 from a handful of
sub-scores and bucketed into one of four labels:

    ignore | watch | candidate | paper_trade

`candidate` requires the basic liquidity/time gates to pass *and* a high enough
composite score. `paper_trade` is reserved for the Phase 3 engine and is not
emitted here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import MarketMetrics

LABELS = ("ignore", "watch", "candidate", "paper_trade")

# Title keywords used to infer category relevance when the market object carries
# no usable `category` field and no series prefixes are configured.
KEYWORDS = {
    "fed", "rate", "cpi", "inflation", "jobs", "payroll", "unemployment",
    "gdp", "economy", "recession", "treasury", "s&p", "nasdaq", "dow",
}


@dataclass
class SignalResult:
    ticker: str
    label: str
    score: float
    implied_probability: float | None
    confidence: float
    reason: str


def category_priority(market: dict, settings) -> float:
    cat = (market.get("category") or "").strip().lower()
    if cat and cat in settings.target_category_list:
        return 1.0
    ticker = (market.get("ticker") or "").upper()
    if any(ticker.startswith(prefix) for prefix in settings.target_series_prefix_list):
        return 1.0
    title = (market.get("title") or "").lower()
    if any(k in title for k in KEYWORDS):
        return 0.7
    return 0.2


def score_market(metrics: MarketMetrics, market: dict, *, settings) -> SignalResult:
    if not metrics.two_sided or metrics.spread is None:
        return SignalResult(metrics.ticker, "ignore", 0.0, None, 0.0, "no two-sided market")

    spread_ok = metrics.spread <= settings.max_spread_cents
    spread_score = max(0.0, 1.0 - (metrics.spread / max(1, settings.max_spread_cents * 3)))
    depth_score = min(1.0, metrics.top_depth / 500.0)
    volume_score = min(1.0, metrics.volume / max(1, settings.min_volume * 5))
    ttc_hours = (metrics.time_to_close_seconds or 0) / 3600.0
    ttc_score = 1.0 if ttc_hours >= settings.min_hours_to_close else 0.0
    cat_score = category_priority(market, settings)
    has_rules = bool(
        market.get("rules_primary") or market.get("rules_secondary") or market.get("subtitle")
    )
    rules_score = 1.0 if has_rules else 0.5

    score = round(
        100.0
        * (
            0.25 * spread_score
            + 0.20 * depth_score
            + 0.15 * volume_score
            + 0.15 * ttc_score
            + 0.15 * cat_score
            + 0.10 * rules_score
        ),
        2,
    )

    meets_basic = (
        spread_ok
        and metrics.volume >= settings.min_volume
        and metrics.open_interest >= settings.min_open_interest
        and ttc_hours >= settings.min_hours_to_close
    )

    note = ""
    if not meets_basic:
        label = "watch" if score >= 40 else "ignore"
        note = "; below basic candidate thresholds"
    elif score >= 70:
        label = "candidate"
    elif score >= 50:
        label = "watch"
    else:
        label = "ignore"

    implied = metrics.midpoint / 100.0 if metrics.midpoint is not None else None
    confidence = round(min(1.0, score / 100.0), 3)
    reason = (
        f"score={score}; spread={metrics.spread}; depth={metrics.top_depth}; "
        f"vol={metrics.volume}; oi={metrics.open_interest}; ttc_h={ttc_hours:.1f}{note}"
    )
    return SignalResult(metrics.ticker, label, score, implied, confidence, reason)
