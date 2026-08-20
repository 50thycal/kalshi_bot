"""One-sided confidence bounds for gate clauses.

A gate clause normally compares a **point estimate** to a threshold. That is the
right shape for a count or a coverage percentage, and the wrong shape for a
promotion decision: `mean > 0` passes half the time when the true effect is
exactly zero, at every sample size, so a sample floor controls variance without
ever defining a false-promotion standard.

A bound clause compares the **bound** instead. `LCB99(x) > 0` passes only when the
evidence puts the true value above zero at 99% confidence, which pins the error
rate by construction rather than by hoping the floor was large enough.

Two things about this are easy to get wrong and are handled explicitly here.

**Direction is never inferred from the operator.** `UCB > t` and `LCB > t` are
different tests, and a metric's own higher/lower-is-better direction does not
determine which bound a contract wants — a lower bound is the right tool for
"demonstrate this is big" and an upper bound for "demonstrate this is small".
The contract states `direction`; nothing guesses it.

**The method must match the statistic.** A normal bound on a count with an
expected value near 4 is simply wrong. `poisson_exact` exists for rare-event
ratios (the theta tail statistic has E≈4 at its failure floor), and it is exact
at every n rather than asymptotically valid at large n.

A caveat that belongs with the machinery rather than in a design document: a
bound re-evaluated on every cadence is a **sequential test**, and its nominal
per-look error rate is not its lifetime rate. Measured on the two proposed
promotion gates, a 95% bound evaluated continuously over a 3x horizon carries
~18% lifetime false promotion, and a 99% bound ~5%. `evaluations_since_eligible`
is recorded in provenance so a reader can see how many chances a clause had
instead of reading a per-look confidence as a lifetime claim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: One-sided normal quantiles. Restricted deliberately — a contract that wants an
#: unusual confidence level should say so in a review, not slip through as a float.
Z_ONE_SIDED: dict[float, float] = {
    0.90: 1.281552,
    0.95: 1.644854,
    0.975: 1.959964,
    0.99: 2.326348,
    0.995: 2.575829,
}

BOUND_DIRECTIONS = frozenset({"lower", "upper"})
BOUND_METHODS = frozenset({"normal", "poisson_exact"})


@dataclass(frozen=True)
class BoundResult:
    value: float | None
    missing: bool = False
    reason: str | None = None
    provenance: dict = field(default_factory=dict)


def validate_bound_spec(spec: object) -> list[str]:
    """Structural problems with a clause's `bound` block, for freeze-time checks."""
    problems: list[str] = []
    if not isinstance(spec, dict):
        return ["bound must be an object with direction, confidence and method"]
    direction = spec.get("direction")
    if direction not in BOUND_DIRECTIONS:
        problems.append(
            f"bound.direction must be one of {sorted(BOUND_DIRECTIONS)}, got "
            f"{direction!r} — it is never inferred from the comparison operator"
        )
    conf = spec.get("confidence")
    if conf not in Z_ONE_SIDED:
        problems.append(
            f"bound.confidence must be one of {sorted(Z_ONE_SIDED)}, got {conf!r}"
        )
    method = spec.get("method")
    if method not in BOUND_METHODS:
        problems.append(
            f"bound.method must be one of {sorted(BOUND_METHODS)}, got {method!r}"
        )
    return problems


# ---------------------------------------------------------------------------
# Exact Poisson bounds
# ---------------------------------------------------------------------------


def _poisson_sf(lam: float, k: int) -> float:
    """P(X >= k) for X ~ Poisson(lam), summed from the lower tail.

    Written out rather than pulled from scipy: the evaluator runs on the trading
    worker, and a verdict must never depend on an optional scientific stack being
    installed."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    total, term = 0.0, math.exp(-lam)
    for i in range(k):
        if i:
            term *= lam / i
        total += term
    return min(1.0, max(0.0, 1.0 - total))


def poisson_rate_lower(k: int, confidence: float) -> float:
    """Exact one-sided lower confidence bound on a Poisson rate given k observed.

    Solves P(X >= k | lam) = 1 - confidence. At k = 0 there is no lower bound and
    the honest answer is 0 — never a small positive number."""
    if k <= 0:
        return 0.0
    alpha = 1.0 - confidence
    lo, hi = 0.0, float(k) + 12.0 * math.sqrt(k) + 12.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _poisson_sf(mid, k) > alpha:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def poisson_rate_upper(k: int, confidence: float) -> float:
    """Exact one-sided upper confidence bound on a Poisson rate given k observed.

    Solves P(X <= k | lam) = 1 - confidence, i.e. P(X >= k+1 | lam) = confidence."""
    alpha = 1.0 - confidence
    lo, hi = 0.0, float(k) + 12.0 * math.sqrt(k + 1) + 12.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        # P(X <= k) = 1 - P(X >= k+1)
        if 1.0 - _poisson_sf(mid, k + 1) < alpha:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Applying a bound to a computed metric
# ---------------------------------------------------------------------------


def compute_bound(
    *,
    estimate: float | None,
    stderr: float | None,
    provenance: dict,
    spec: dict,
) -> BoundResult:
    """The clause's comparison value: a one-sided bound, not the point estimate.

    Returns `missing=True` when the bound cannot be computed. That is deliberately
    MISSING rather than a fallback to the point estimate: silently comparing an
    estimate where a contract asked for a bound would restore exactly the 50%
    false-promotion behavior the bound exists to remove."""
    direction = spec.get("direction")
    confidence = spec.get("confidence")
    method = spec.get("method")
    base = {
        "bound_direction": direction,
        "confidence": confidence,
        "method": method,
    }
    problems = validate_bound_spec(spec)
    if problems:
        return BoundResult(None, missing=True, reason="; ".join(problems),
                           provenance=base)
    if estimate is None:
        return BoundResult(None, missing=True,
                           reason="no point estimate to bound", provenance=base)

    if method == "normal":
        if stderr is None:
            return BoundResult(
                None, missing=True,
                reason=(
                    "the provider did not supply a standard error, so a normal "
                    "bound cannot be computed — the clause is not silently "
                    "downgraded to its point estimate"
                ),
                provenance=base,
            )
        if stderr < 0 or math.isnan(stderr) or math.isinf(stderr):
            return BoundResult(None, missing=True,
                               reason=f"invalid standard error {stderr!r}",
                               provenance=base)
        z = Z_ONE_SIDED[confidence]
        bound = estimate - z * stderr if direction == "lower" else estimate + z * stderr
        return BoundResult(
            round(bound, 6),
            provenance=base | {
                "estimate": estimate,
                "standard_error": round(stderr, 6),
                "z": z,
                "uncertainty_inputs": {"standard_error": round(stderr, 6)},
            },
        )

    # poisson_exact — for rare-event ratios, where a normal bound is simply wrong
    # at the sample sizes involved (the theta tail statistic has E ~ 4 at its
    # failure floor).
    observed = provenance.get("observed")
    expected = provenance.get("expected")
    if observed is None or expected is None:
        return BoundResult(
            None, missing=True,
            reason=(
                "poisson_exact needs 'observed' and 'expected' in the metric's "
                "provenance; this provider supplied neither"
            ),
            provenance=base,
        )
    try:
        k = int(observed)
        e = float(expected)
    except (TypeError, ValueError):
        return BoundResult(None, missing=True,
                           reason=f"non-numeric observed/expected "
                                  f"({observed!r}/{expected!r})", provenance=base)
    if e <= 0:
        return BoundResult(
            None, missing=True,
            reason="expected count is zero — the ratio is undefined, not infinite",
            provenance=base,
        )
    rate = (poisson_rate_lower(k, confidence) if direction == "lower"
            else poisson_rate_upper(k, confidence))
    return BoundResult(
        round(rate / e, 6),
        provenance=base | {
            "estimate": estimate,
            "uncertainty_inputs": {
                "observed": k,
                "expected": round(e, 6),
                "bounded_rate": round(rate, 6),
            },
        },
    )
