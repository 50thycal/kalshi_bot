"""Turn the Open-Meteo ensemble (the forecast *distribution*) into per-bucket
probabilities, and pick the single Kalshi bucket the model thinks is most underpriced.

This is the live counterpart of the offline grader in scripts/weather_model_check.py:
the probability math is duplicated there deliberately (that script must stay
self-contained — stdlib + psycopg only — so it can run on the ops runner), the same way
the bucket parsing in buckets.py is mirrored into the scripts.

Why an ensemble distribution beats a point forecast: temperature buckets are priced off a
*distribution*, not a single number. Each ensemble member is one plausible outcome; the
spread across members (plus a small kernel for error beyond that spread) is exactly
P(temperature lands in bucket). The backfill study (#6) found the market's own implied
distribution is OVERDISPERSED — too wide — so a tighter, ensemble-grounded distribution is
where the edge is. Keep the kernel sigma modest so the model stays tighter than the ladder.
"""

from __future__ import annotations

import math
from statistics import mean

PROB_FLOOR = 1e-4  # keep a zeroed bucket from dominating normalization


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def member_bucket_prob(
    member_f: float, low: float | None, high: float | None, sigma: float
) -> float:
    """P(this member's true integer temperature lands in [low, high]).

    Buckets settle on integer-degree climate reports, so bucket [74, 75] covers the
    continuous range [73.5, 75.5). sigma is the kernel width around the member value
    (forecast error beyond the ensemble spread); sigma<=0 degrades to the raw indicator,
    matching kalshi_bot/weather/buckets.py::forecast_in_bucket's rounding.
    """
    if sigma <= 0:
        r = round(member_f)
        if low is not None and r < low:
            return 0.0
        if high is not None and r > high:
            return 0.0
        return 1.0
    upper = _phi((high + 0.5 - member_f) / sigma) if high is not None else 1.0
    lower = _phi((low - 0.5 - member_f) / sigma) if low is not None else 0.0
    return max(0.0, upper - lower)


def model_bucket_probs(
    members_by_model: dict[str, list[float]],
    buckets: list[tuple[float | None, float | None]],
    sigma: float,
    floor: float = PROB_FLOOR,
) -> list[float] | None:
    """Blend per-model bucket probabilities (equal model weight), floored + normalized."""
    per_model: list[list[float]] = []
    for members in members_by_model.values():
        vals = [float(v) for v in members if v is not None]
        if not vals:
            continue
        per_model.append(
            [mean(member_bucket_prob(v, lo, hi, sigma) for v in vals) for lo, hi in buckets]
        )
    if not per_model:
        return None
    blend = [max(mean(col), floor) for col in zip(*per_model, strict=True)]
    total = sum(blend)
    return [p / total for p in blend] if total > 0 else None


def best_bucket_by_edge(
    ladder: list[tuple[tuple[float | None, float | None], float | None]],
    members_by_model: dict[str, list[float]],
    *,
    sigma: float,
    min_edge_cents: float,
    fee_cents,
):
    """Pick the bucket whose model probability most beats its YES ask, net of the fee,
    if that edge clears min_edge_cents.

    `ladder` is [( (low, high), yes_ask_cents ), ...] in bucket order; `fee_cents(ask)`
    returns the Kalshi entry fee. Returns (index, model_prob, edge_cents) or None.
    """
    buckets = [rng for rng, _ask in ladder]
    probs = model_bucket_probs(members_by_model, buckets, sigma)
    if probs is None:
        return None
    best = None  # (edge, index, prob)
    for i, ((_rng, ask), p) in enumerate(zip(ladder, probs, strict=True)):
        if ask is None or not (1 <= ask <= 99):
            continue
        edge = p * 100.0 - ask - fee_cents(ask)
        if edge >= min_edge_cents and (best is None or edge > best[0]):
            best = (edge, i, p)
    if best is None:
        return None
    return (best[1], best[2], best[0])
