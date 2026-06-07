"""Paper-trading entry strategies (the swap-in seam for directional models).

Each strategy turns market state into an `EntryProposal` (side + executable ask price +
model probability + cost-aware edge) or `None` (no trade). Edge is measured against the
price we'd actually pay (the ask), so it already nets out the spread; entries also require
`|edge| >= PAPER_MIN_EDGE_CENTS` to clear the fee hurdle.

Strategies:
- directional (`buy_favorite` / `buy_yes` / `buy_no`): the non-directional control; always
  proposes, edge = 0.
- `momentum`: linear drift of recent midpoints projected forward -> model probability.
- `ladder`: isotonic ("fair curve") relative value across a series' strike ladder.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from ..scanner.metrics import MarketMetrics


@dataclass
class EntryProposal:
    side: str  # "yes" | "no"
    price: int  # executable ask price in cents
    model_probability: float  # model's YES probability, 0..1
    edge: float  # cost-aware edge in probability units (after paying the ask)


def _clamp01(x: float) -> float:
    return min(0.99, max(0.01, x))


def best_side_by_edge(
    model_prob: float, metrics: MarketMetrics, min_edge_cents: int
) -> EntryProposal | None:
    """Pick the side (buy YES or buy NO at its ask) with the largest positive cost-aware edge."""
    options = []
    if metrics.best_yes_ask is not None:
        options.append(("yes", metrics.best_yes_ask, model_prob * 100 - metrics.best_yes_ask))
    if metrics.best_no_ask is not None:
        options.append(("no", metrics.best_no_ask, (1 - model_prob) * 100 - metrics.best_no_ask))
    if not options:
        return None
    side, price, edge_cents = max(options, key=lambda o: o[2])
    if edge_cents < min_edge_cents:
        return None
    return EntryProposal(side=side, price=int(price), model_probability=model_prob, edge=edge_cents / 100.0)


# --- directional baseline ----------------------------------------------------

def directional_proposal(metrics: MarketMetrics, strategy: str) -> EntryProposal | None:
    if not metrics.two_sided or metrics.midpoint is None:
        return None
    if strategy == "buy_yes":
        side = "yes"
    elif strategy == "buy_no":
        side = "no"
    else:  # buy_favorite
        side = "yes" if metrics.midpoint >= 50 else "no"
    price = metrics.best_yes_ask if side == "yes" else metrics.best_no_ask
    if price is None:
        return None
    return EntryProposal(
        side=side, price=int(price), model_probability=metrics.midpoint / 100.0, edge=0.0
    )


# --- momentum ----------------------------------------------------------------

def _ls_slope(points: list[tuple[float, float]]) -> float:
    """Least-squares slope of y vs x (cents per hour)."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    den = sum((x - mx) ** 2 for x, _ in points)
    if den == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in points) / den


def momentum_proposal(
    history: list[tuple[float, float]],
    metrics: MarketMetrics,
    settings,
    direction: str | None = None,
) -> EntryProposal | None:
    """history: (hours_from_start, midpoint_cents) ascending, including the latest point.

    Projects the recent drift `paper_momentum_project_hours` forward to form the model
    probability. `direction` ("momentum"|"reversion") overrides the configured default."""
    if not metrics.two_sided or metrics.midpoint is None or len(history) < 2:
        return None
    slope = _ls_slope(history)  # cents/hour
    effective = direction or settings.paper_momentum_direction
    if effective == "reversion":
        slope = -slope
    projected = metrics.midpoint + slope * settings.paper_momentum_project_hours
    model_prob = _clamp01(projected / 100.0)
    return best_side_by_edge(model_prob, metrics, settings.paper_min_edge_cents)


# --- ladder (isotonic relative value) ----------------------------------------

_STRIKE_RE = re.compile(r"^(?P<series>.+)-(?P<type>[TB])(?P<strike>\d+(?:\.\d+)?)$")


def parse_strike(ticker: str) -> tuple[str, str, float] | None:
    """Parse a strike-ladder ticker -> (series_key, type_token, strike). None if not a ladder."""
    mobj = _STRIKE_RE.match(ticker or "")
    if not mobj:
        return None
    return mobj.group("series"), mobj.group("type"), float(mobj.group("strike"))


def isotonic_pava(y: list[float], increasing: bool = True) -> list[float]:
    """Pool-adjacent-violators isotonic regression (no external deps)."""
    if not increasing:
        return [-v for v in isotonic_pava([-x for x in y], increasing=True)]
    levels: list[list[float]] = []  # each [mean, weight]
    for value in y:
        levels.append([float(value), 1.0])
        while len(levels) > 1 and levels[-2][0] > levels[-1][0]:
            m2, w2 = levels.pop()
            m1, w1 = levels.pop()
            nw = w1 + w2
            levels.append([(m1 * w1 + m2 * w2) / nw, nw])
    out: list[float] = []
    for mean, weight in levels:
        out.extend([mean] * int(weight))
    return out


def ladder_proposals(
    candidates: list[tuple[str, MarketMetrics]], settings
) -> dict[str, EntryProposal]:
    """Compute relative-value proposals across each monotone strike ladder in the batch."""
    groups: dict[tuple[str, str], list[tuple[str, float, MarketMetrics]]] = defaultdict(list)
    for ticker, metrics in candidates:
        if not metrics.two_sided or metrics.midpoint is None:
            continue
        parsed = parse_strike(ticker)
        if parsed is None:
            continue
        series_key, typ, strike = parsed
        groups[(series_key, typ)].append((ticker, strike, metrics))

    out: dict[str, EntryProposal] = {}
    for rungs in groups.values():
        if len(rungs) < 3:
            continue
        rungs.sort(key=lambda r: r[1])  # by strike
        implied = [r[2].midpoint / 100.0 for r in rungs]
        increasing = implied[-1] >= implied[0]
        pairs = list(zip(implied, implied[1:], strict=False))
        if increasing:
            violations = sum(1 for a, b in pairs if b < a)
        else:
            violations = sum(1 for a, b in pairs if b > a)
        if violations > (len(rungs) - 1) / 3:  # too non-monotone to trust as a ladder
            continue
        fair = isotonic_pava(implied, increasing=increasing)
        for (ticker, _strike, metrics), fair_prob in zip(rungs, fair, strict=True):
            proposal = best_side_by_edge(
                _clamp01(fair_prob), metrics, settings.paper_min_edge_cents
            )
            if proposal is not None:
                out[ticker] = proposal
    return out
