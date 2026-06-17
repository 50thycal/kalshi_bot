"""Consensus ("layered") signal logic for the `con` weather book.

The single-signal books each follow one estimate; this layer makes the independent signal
FAMILIES converge before trading. Offline validation (scripts/weather_consensus_study.py)
showed two distinct, opposite-by-kind edges, which `choose_bucket` encodes:

  - HIGH at the early windows (h20/h14): a skill-WEIGHTED blend of the families
    (obs/pm weighted above the forecasts, since two forecasts share the same error) points
    to a bucket; trade it ONLY when it DEVIATES from the market favorite — those cheaper,
    model-preferred buckets were +EV while confirming an expensive early favorite was -EV.

  - LOW (any window) and HIGH late (h8): trade ONLY a near-unanimous agreement (K of the
    families) that lands ON the favorite — a high-confidence near-lock filter; otherwise skip.

Each signal is reduced to the index of the ladder bucket its temperature falls in; agreement
is counted within +/-tol buckets (1-2 degF buckets make exact equality too brittle). Pure +
stdlib so it is unit-testable in isolation from the tracker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FAMILIES = ("fc", "ens", "obs", "pm")
DEFAULT_WEIGHTS = {"fc": 1.0, "ens": 1.0, "obs": 2.0, "pm": 2.0}
OPEN_BUCKET_HALF_WIDTH = 1.0


@dataclass(frozen=True)
class ConsensusParams:
    tol: int = 1
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    early_min_mass: float = 3.0          # high early: weighted-mass threshold to act
    confirm_k: int = 4                   # low/late: families that must agree on the favorite
    early_windows: frozenset = frozenset({20.0, 14.0})


def params_from_settings(s) -> ConsensusParams:
    weights = dict(DEFAULT_WEIGHTS)
    for part in (s.weather_consensus_weights or "").split(","):
        if "=" in part:
            fam, val = part.split("=", 1)
            if fam.strip() in FAMILIES:
                try:
                    weights[fam.strip()] = float(val)
                except ValueError:
                    pass
    early = frozenset(
        float(x) for x in (s.weather_consensus_early_windows or "").split(",") if x.strip()
    )
    return ConsensusParams(
        tol=s.weather_consensus_tol,
        weights=weights,
        early_min_mass=s.weather_consensus_early_min_mass,
        confirm_k=s.weather_consensus_confirm_k,
        early_windows=early or frozenset({20.0, 14.0}),
    )


def bucket_mid_temp(low: float | None, high: float | None) -> float | None:
    """Representative temperature for a bucket; open-ended below/above buckets use the
    finite edge offset by a half-width (mirrors weather/validation.py)."""
    if low is not None and high is not None:
        return (low + high) / 2.0
    if high is not None:
        return high - OPEN_BUCKET_HALF_WIDTH
    if low is not None:
        return low + OPEN_BUCKET_HALF_WIDTH
    return None


def bucket_index_for_temp(temp: float | None, ranges: list[tuple]) -> int | None:
    """Index of the ladder bucket (ranges sorted ascending by low) containing temp;
    nearest-by-edge when none strictly contains it (open-ended edges carry None)."""
    if temp is None:
        return None
    for i, (lo, hi) in enumerate(ranges):
        if (lo is None or temp >= lo) and (hi is None or temp <= hi):
            return i
    best, best_d = None, None
    for i, (lo, hi) in enumerate(ranges):
        edges = [e for e in (lo, hi) if e is not None]
        d = min(abs(temp - e) for e in edges) if edges else 0.0
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def pm_implied_mean(buckets) -> float | None:
    """Probability-weighted mean temperature of a Polymarket bucket set (objects with
    .low_f/.high_f/.yes_prob). None when degenerate."""
    num = den = 0.0
    for b in buckets or []:
        mt = bucket_mid_temp(getattr(b, "low_f", None), getattr(b, "high_f", None))
        prob = getattr(b, "yes_prob", None)
        if mt is not None and prob is not None:
            num += prob * mt
            den += prob
    return (num / den) if den > 0 else None


def _present(votes: dict) -> dict:
    return {f: idx for f, idx in votes.items() if idx is not None}


def k_pick(votes: dict, *, k: int, tol: int) -> tuple[int, int] | None:
    """Bucket with the most family votes within +/-tol; (bucket, count) if count >= k."""
    present = _present(votes)
    if len(present) < k:
        return None
    centroid = sum(present.values()) / len(present)
    best = None  # (count, -dist, cand)
    for cand in set(present.values()):
        count = sum(1 for idx in present.values() if abs(idx - cand) <= tol)
        key = (count, -abs(cand - centroid))
        if best is None or key > best[:2]:
            best = (count, -abs(cand - centroid), cand)
    if best is None or best[0] < k:
        return None
    return (best[2], best[0])


def weighted_pick(votes: dict, *, weights: dict, tol: int, min_mass: float) -> tuple[int, float] | None:
    """Bucket with the most skill-WEIGHTED vote mass within +/-tol; (bucket, mass) if
    mass >= min_mass."""
    present = _present(votes)
    if not present:
        return None
    centroid = sum(present.values()) / len(present)
    best = None
    for cand in set(present.values()):
        mass = sum(weights.get(f, 1.0) for f, idx in present.items() if abs(idx - cand) <= tol)
        key = (mass, -abs(cand - centroid))
        if best is None or key > best[:2]:
            best = (mass, -abs(cand - centroid), cand)
    if best is None or best[0] < min_mass:
        return None
    return (best[2], best[0])


def choose_bucket(
    kind: str, window: float, votes: dict, fav_idx: int | None, params: ConsensusParams
) -> tuple[int, float] | None:
    """The consensus bucket index + conviction to trade, or None to skip.

    HIGH early window -> weighted blend, trade only when it DEVIATES from the favorite.
    LOW / HIGH-late   -> near-unanimous K agreement that lands ON the favorite, else skip."""
    if kind == "high" and float(window) in params.early_windows:
        pick = weighted_pick(votes, weights=params.weights, tol=params.tol,
                             min_mass=params.early_min_mass)
        if pick is None:
            return None
        idx, mass = pick
        if fav_idx is not None and idx == fav_idx:
            return None  # deviation-only: confirming an early-high favorite was -EV
        return (idx, mass)

    pick = k_pick(votes, k=params.confirm_k, tol=params.tol)
    if pick is None:
        return None
    idx, count = pick
    if fav_idx is None or idx != fav_idx:
        return None  # near-lock: the agreement must be ON the favorite
    return (idx, float(count))
