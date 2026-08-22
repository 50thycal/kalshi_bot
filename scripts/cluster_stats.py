"""Cluster-robust uncertainty for evidence whose independent unit is not the row.

WHY THIS EXISTS. Every crypto ladder event publishes many markets — one per strike — and all
of them resolve against a **single** spot print. If BTC gapped up in the last ten minutes of
one hour, every "above" strike on that ladder hits together and every "below" strike misses
together. Treating those markets as independent Poisson observations counts one move as a
dozen, and the resulting interval is too narrow by roughly the square root of the average
cluster size. A per-event position cap bounds *exposure*; it does nothing to the *statistics*.

WHAT IT PROVIDES. A nonparametric **cluster bootstrap**: resample whole events with
replacement, keeping all of an event's rows together, and recompute the statistic on each
replicate. That reproduces the real dependence without having to model it. Intervals are
percentile intervals over the replicate distribution.

  cluster_profile      how concentrated the evidence is: events, markets/event, percentiles,
                       Kish effective cluster count
  ratio_ci             observed/expected (calibration R) with an event-clustered interval
  mean_ci              a mean per row, event-clustered — the shape a proper score takes
  paired_mean_ci       the mean of a per-row PAIRED difference, event-clustered. This is the
                       comparison to make between two models scored on the same markets.
  design_effect        empirical variance inflation: clustered variance / iid variance, and
                       the effective evidence size it implies

DETERMINISM. Every routine takes an explicit integer seed and uses its own `random.Random`,
so a rerun of a recorded analysis reproduces its intervals exactly. An analysis whose
uncertainty moves between runs cannot be a frozen result.

Stdlib only, so ops `script` requests can import it without the SQLAlchemy package.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Hashable, Sequence

# Enough replicates that the interval endpoints are stable to ~0.01 at 99%; the cost is
# trivial next to loading and fitting.
DEFAULT_B = 2000
DEFAULT_SEED = 20260822

# The design effect's iid arm resamples every ROW, so it is the one routine whose
# cost scales with n rather than with the cluster count. A variance RATIO is stable
# long before an interval ENDPOINT is, so it runs fewer replicates.
DEFAULT_DEFF_B = 400

# Below this many independent clusters a bootstrap interval is not trustworthy — the
# resample can only ever contain the handful of events that exist, so the interval
# describes those events rather than the process. Reported as `n/a` instead of a number.
MIN_CLUSTERS_FOR_CI = 8


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted sequence."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(len(sorted_vals) - 1, lo + 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def group_by_cluster(rows: Sequence[dict], cluster_key: str) -> dict[Hashable, list[dict]]:
    """Bucket rows by their cluster id. Rows with a missing id each become their OWN cluster
    rather than being pooled into one giant `None` group — pooling unrelated rows would
    understate the evidence exactly as badly as splitting related ones overstates it."""
    out: dict[Hashable, list[dict]] = defaultdict(list)
    for i, r in enumerate(rows):
        cid = r.get(cluster_key)
        out[cid if cid is not None else ("__row__", i)].append(r)
    return dict(out)


def cluster_profile(rows: Sequence[dict], cluster_key: str) -> dict:
    """How much independent evidence is really here.

    `kish_effective_clusters` is (Σm)² / Σm² over cluster sizes m. It equals the cluster count
    when every event contributes equally and falls toward 1 as the evidence concentrates into a
    few large events, so it says in one number how far the sample is from its nominal size.
    """
    groups = group_by_cluster(rows, cluster_key)
    sizes = sorted(len(v) for v in groups.values())
    n = sum(sizes)
    if not sizes:
        return {"rows": 0, "clusters": 0}
    ssq = sum(s * s for s in sizes)
    return {
        "rows": n,
        "clusters": len(sizes),
        "mean_size": n / len(sizes),
        "p50_size": _percentile(sizes, 0.50),
        "p90_size": _percentile(sizes, 0.90),
        "max_size": sizes[-1],
        "kish_effective_clusters": (n * n) / ssq if ssq else float("nan"),
        "largest_cluster_share": sizes[-1] / n if n else float("nan"),
    }


def _cluster_pairs(rows: Sequence[dict], cluster_key: str,
                   num: Callable[[dict], float], den: Callable[[dict], float],
                   *, event_weighted: bool = False) -> list[tuple[float, float]]:
    """Collapse rows to one (numerator, denominator) pair per cluster.

    Every statistic here is a ratio of two sums — R is observed over expected, a market-weighted
    mean is Σv over n, an event-weighted mean is Σ(cluster means) over (cluster count) — so the
    bootstrap only ever needs the per-cluster subtotals. Resampling those instead of the rows is
    what makes 2,000 replicates over 30,000 markets finish in seconds rather than minutes, and
    it is exact, not an approximation: a cluster is resampled whole either way.
    """
    out: list[tuple[float, float]] = []
    for v in group_by_cluster(rows, cluster_key).values():
        n = sum(num(r) for r in v)
        d = sum(den(r) for r in v)
        if event_weighted:
            # One cluster, one observation: the ladder's own mean, weighted equally with
            # every other ladder however many strikes it happened to publish.
            out.append(((n / d) if d else 0.0, 1.0))
        else:
            out.append((n, d))
    return out


def _boot(pairs: Sequence[tuple[float, float]], b: int, seed: int) -> list[float]:
    """`b` percentile-bootstrap replicates of Σnum / Σden over clusters resampled with
    replacement. Deterministic in `seed`."""
    rng = random.Random(seed)
    k = len(pairs)
    if k == 0:
        return []
    out: list[float] = []
    rand = rng.randrange
    for _ in range(b):
        sn = sd = 0.0
        for _ in range(k):
            n, d = pairs[rand(k)]
            sn += n
            sd += d
        if sd:
            v = sn / sd
            if math.isfinite(v):
                out.append(v)
    return out


def _ci(reps: list[float], alpha: float) -> tuple[float | None, float | None]:
    if not reps:
        return None, None
    reps = sorted(reps)
    return _percentile(reps, alpha / 2.0), _percentile(reps, 1.0 - alpha / 2.0)


def ratio_ci(rows: Sequence[dict], cluster_key: str, p_key: str, outcome_key: str, *,
             alpha: float = 0.01, b: int = DEFAULT_B, seed: int = DEFAULT_SEED) -> dict:
    """Calibration R = observed hits / expected hits, with an event-clustered interval.

    The point estimate is unchanged by clustering — only the interval is. That is the whole
    correction: R was never wrong, the confidence in it was.
    """
    exp = sum(r[p_key] for r in rows)
    obs = sum(1 for r in rows if r[outcome_key])
    prof = cluster_profile(rows, cluster_key)
    out = {"n": len(rows), "expected": exp, "observed": obs,
           "r": (obs / exp if exp > 0 else float("nan")),
           "clusters": prof.get("clusters", 0), "lo": None, "hi": None}
    if prof.get("clusters", 0) >= MIN_CLUSTERS_FOR_CI:
        pairs = _cluster_pairs(rows, cluster_key,
                              lambda r: 1.0 if r[outcome_key] else 0.0,
                              lambda r: r[p_key])
        out["lo"], out["hi"] = _ci(_boot(pairs, b, seed), alpha)
    return out


def mean_ci(rows: Sequence[dict], cluster_key: str, value: Callable[[dict], float], *,
            alpha: float = 0.01, b: int = DEFAULT_B, seed: int = DEFAULT_SEED,
            event_weighted: bool = False) -> dict:
    """Mean of a per-row quantity with an event-clustered interval.

    `event_weighted` averages within each event first and then across events, so a ladder with
    forty strikes counts once rather than forty times. Market-weighted and event-weighted
    answer different questions — per quote priced, and per independent world — and a
    conclusion that survives only one of them is a conclusion about ladder width.
    """
    pairs = _cluster_pairs(rows, cluster_key, value, lambda _r: 1.0,
                          event_weighted=event_weighted)
    sn = sum(a for a, _ in pairs)
    sd = sum(d for _, d in pairs)
    out = {"n": len(rows), "clusters": len(pairs),
           "mean": (sn / sd) if sd else float("nan"), "lo": None, "hi": None}
    if len(pairs) >= MIN_CLUSTERS_FOR_CI:
        out["lo"], out["hi"] = _ci(_boot(pairs, b, seed), alpha)
    return out


def paired_mean_ci(rows: Sequence[dict], cluster_key: str, diff: Callable[[dict], float], *,
                   alpha: float = 0.01, b: int = DEFAULT_B, seed: int = DEFAULT_SEED,
                   event_weighted: bool = False) -> dict:
    """Mean of a per-row PAIRED difference, event-clustered.

    This is the correct comparison between two models scored on the same markets. Comparing
    two separately-computed intervals — one that excludes some reference value and one that
    does not — is not a test of a difference: overlapping intervals routinely hide a
    significant paired difference, and separated ones can exaggerate it. The pairing also
    removes the market-to-market variation that both models share, which is most of it.

    Sign convention is the caller's: `diff` returns (candidate - reference), so a mean below
    zero favours the candidate for a loss and an interval containing zero establishes nothing.
    """
    res = mean_ci(rows, cluster_key, diff, alpha=alpha, b=b, seed=seed,
                  event_weighted=event_weighted)
    lo, hi = res["lo"], res["hi"]
    res["favors"] = ("candidate" if (hi is not None and hi < 0)
                     else "reference" if (lo is not None and lo > 0)
                     else "neither")
    return res


def design_effect(rows: Sequence[dict], cluster_key: str, value: Callable[[dict], float], *,
                  b: int = DEFAULT_DEFF_B, seed: int = DEFAULT_SEED) -> dict:
    """How much the clustering costs, measured rather than assumed.

    Runs the same mean through a clustered bootstrap and an iid one and takes the variance
    ratio. `deff` near 1 means the rows really were independent; `deff` of 10 means an interval
    computed as if they were is about 3.2x too narrow. `n_eff` = n / deff is the number of
    independent observations this sample is actually worth.

    Fewer replicates than an interval needs, deliberately: the iid arm resamples every ROW, so
    its cost is O(b x n) rather than O(b x clusters), and a variance ratio is stable long
    before an interval endpoint is.
    """
    clustered = _cluster_pairs(rows, cluster_key, value, lambda _r: 1.0)
    iid = [(value(r), 1.0) for r in rows]
    cl = _boot(clustered, b, seed)
    ii = _boot(iid, b, seed)
    if len(cl) < 2 or len(ii) < 2:
        return {"deff": float("nan"), "n_eff": float("nan"), "n": len(rows)}

    def var(xs: list[float]) -> float:
        m = sum(xs) / len(xs)
        return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

    vi = var(ii)
    deff = (var(cl) / vi) if vi > 0 else float("nan")
    n = len(rows)
    return {"deff": deff, "n_eff": (n / deff) if deff and deff > 0 else float("nan"), "n": n}


def fmt_ci(lo: float | None, hi: float | None, digits: int = 2) -> str:
    if lo is None or hi is None or not (math.isfinite(lo) and math.isfinite(hi)):
        return "n/a"
    return f"[{lo:.{digits}f}, {hi:.{digits}f}]"
