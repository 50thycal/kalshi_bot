"""A probability model for theta that has a tail beyond the data it was fitted on.

WHY THIS EXISTS. `SpotModel.prob_from_returns` returns the raw empirical frequency of trailing
overlapping h-minute log-returns that satisfy the strike:

    sum(1 for r in rets if r > x) / n

An empirical distribution has **no mass beyond its own sample maximum**. Any strike further out
than the largest move seen in the trailing window therefore prices at *exactly* 0.0, and any
strike inside the smallest at exactly 1.0. Measured over 111,242 ladder quotes in theta's entry
window: **53.7% price at exactly 0 and 39.4% at exactly 1** — 93.1% of the model's output is not
a probability at all (`docs/RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md` §2.3).

That single fact explains the strategy's failure shape. The realized-hit ratio R is 1.00 where
the model is near the money and rises monotonically to 4.58 beyond z=2.5 — the exact signature
of a truncated tail. It also explains why `mult=2.0` did not repair it: `vol_mult` rescales the
THRESHOLD (`x / k`), so it can pull a strike back inside the empirical support, but where
`x / k` still exceeds `max(rets)` the answer is still exactly zero. Widening a distribution that
ends at a hard edge moves the edge; it does not remove it.

WHAT THIS DOES INSTEAD. Extreme-value theory's standard construction — Pickands-Balkema-de Haan:
the excesses of ANY reasonable distribution over a high threshold converge to a Generalized
Pareto. So:

  * below the threshold, keep the empirical distribution — it is the observed shape where the
    data is dense, and there is no reason to model what can be counted;
  * above it, splice on a fitted GPD, which has positive mass arbitrarily far out and a shape
    parameter xi that says HOW heavy the tail is rather than assuming it;
  * both directions, since `greater` and `less` strikes need opposite tails.

Fitted by probability-weighted moments (Hosking & Wallis 1987) rather than maximum likelihood:
closed-form, no optimiser, no scipy, and better behaved than MLE at the small excess counts this
window actually supplies. PWM's price is a validity range — its moments exist only for
xi < 0.5, so against a genuinely heavier tail it SATURATES rather than diverging (a Pareto with
true xi = 2 returns ~0.95). That biases the deep tail DOWN, which for a seller is the dangerous
direction, so it is stated here rather than buried: an xi near or above 0.5 should be read as
"at least this heavy", not as a point estimate.

THE SAMPLE IS SMALLER THAN IT LOOKS. `_returns` walks 1-minute closes and emits an h-minute
return from every minute, so consecutive samples share h-1 of their h minutes. ~7,200 raw
samples over a 5-day window at h=35 carry roughly **n/h ~= 206 independent observations**. The
raw count is not evidence. `n_eff` is reported, is what the probability floor is derived from,
and is the honest denominator for anyone reading a fitted tail parameter.

NO PROBABILITY IS EVER EXACTLY ZERO. Where the GPD produces an estimate, that estimate stands,
however small — extrapolating is the point. The floor `1 / (2 * n_eff)` (half of what ~n_eff
independent observations can resolve) catches only the degenerate cases: a fitted xi < 0 implies
a bounded tail with a finite endpoint, which these sample sizes cannot support, and past that
endpoint the fit would otherwise return zero again. `xi` is reported so a bounded fit is visible
rather than silent.

WHAT THIS MODEL IS NOT. A GPD fitted at the 90th percentile of ~200 independent observations is
biased toward the body for genuinely heavy tails, and its deep extrapolation rests on ~20
independent excesses. That is its weakest claim and the reason `tail_q` is a parameter to be
swept and validated out of sample rather than a constant to be trusted.

This module is **additive**. It does not change `SpotModel`, and no book prices off it until an
experiment is registered to do so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Excesses above this quantile are fitted; below it the empirical body is used as-is. 0.90 is
# the conventional starting point for peaks-over-threshold and leaves ~10% of the sample in the
# tail — with n_eff ~= 200 that is ~20 independent excesses, which is thin for a GPD fit and is
# exactly why `n_eff` and `xi` travel with every result instead of being hidden.
DEFAULT_TAIL_Q = 0.90

# A GPD fit needs enough excesses to mean anything. Below this the tail falls back to the
# exponential (xi = 0) special case, which is the memoryless default rather than a fitted claim.
MIN_TAIL_EXCESSES = 8

# Refuse to price off a thin window at all, matching SpotModel.MIN_SAMPLES.
MIN_SAMPLES = 300

# Below this many INDEPENDENT observations the tail is not estimable and the model says so.
# A GPD at the 95th percentile needs ~20 independent excesses to mean anything; 20 / 0.05 = 400.
#
# This bar is not decoration. theta's 5-day trailing window at a 35-minute horizon carries
# 7200 / 35 ~= 205 independent blocks, so the DESIGN POINT is already under it — ~10 excesses.
# Reaching 400 needs roughly a 10-day window of true 1-minute closes; `crypto_spot_candles` is
# pruned at trail_days + 1 = 6. That retention, not the estimator, is the binding constraint on
# ever validating a tail refit, which is why the stage-5 telemetry work is a prerequisite for
# stage 2 rather than a nice-to-have beside it.
MIN_N_EFF_FOR_TAIL = 400


@dataclass(frozen=True)
class TailFit:
    """One side's fitted tail. `xi` > 0 is heavy, 0 is exponential, < 0 is bounded."""

    threshold: float      # u, in log-return units, on this side's own sign convention
    scale: float          # sigma
    xi: float             # shape
    exceedance: float     # zeta_u = P(X beyond u), empirical
    n_excess: int
    fitted: bool          # False = exponential fallback (too few excesses)

    def sf(self, x: float) -> float:
        """P(X beyond x), for x at or past the threshold. Strictly positive, always.

        A fitted `xi` < 0 describes a BOUNDED tail with a finite endpoint at u - sigma/xi, past
        which the GPD's own answer is exactly zero. Extrapolating that would reinstate the very
        claim this module exists to delete — "this move cannot happen" — merely relocated from
        the sample maximum to a fitted endpoint, and ~20 independent excesses cannot establish a
        hard maximum on a crypto return. So extrapolation uses `max(xi, 0)`: the exponential is
        the least-committal unbounded tail, the boundary case between bounded and heavy. `xi` is
        kept unmodified on the dataclass, so a negative fit stays visible in `describe()` and in
        the fit-health reporting even though it is not extrapolated with."""
        y = x - self.threshold
        if y <= 0:
            return self.exceedance
        xi_eff = self.xi if self.xi > 0.0 else 0.0
        if xi_eff < 1e-9:
            return self.exceedance * math.exp(-y / self.scale)
        return self.exceedance * (1.0 + xi_eff * y / self.scale) ** (-1.0 / xi_eff)


def _quantile(sorted_vals: list[float], q: float) -> float:
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def fit_gpd(excesses: list[float]) -> tuple[float, float, bool]:
    """(sigma, xi, fitted) by probability-weighted moments over POSITIVE excesses.

    Hosking & Wallis (1987), in their k = -xi parameterisation:
        a0 = mean(y)
        a1 = (1/n) * sum_i y_(i) * (n - i) / (n - 1)      [y ordered ascending, i = 1..n]
        k  = a0 / (a0 - 2*a1) - 2          sigma = 2*a0*a1 / (a0 - 2*a1)
    and xi = -k. An exponential sample gives a0 = 1, a1 = 1/4, hence xi = 0 and sigma = 1, which
    `tests/test_theta_tailmodel.py` pins.

    Falls back to the exponential fit (xi = 0, sigma = mean) when the sample is too small or the
    moment denominator degenerates — a fallback that is reported, never silent.
    """
    ys = sorted(y for y in excesses if y > 0)
    n = len(ys)
    if n < MIN_TAIL_EXCESSES:
        return ((sum(ys) / n if n else 0.0), 0.0, False)
    a0 = sum(ys) / n
    a1 = sum(y * (n - (i + 1)) / (n - 1) for i, y in enumerate(ys)) / n
    denom = a0 - 2.0 * a1
    if abs(denom) < 1e-12 or a0 <= 0:
        return (a0, 0.0, False)
    xi = 2.0 - a0 / denom
    sigma = 2.0 * a0 * a1 / denom
    if sigma <= 0 or not math.isfinite(sigma) or not math.isfinite(xi):
        return (a0, 0.0, False)
    # A xi at or above 1 implies an infinite mean. PWM cannot normally reach there (see the
    # module docstring on its xi < 0.5 validity range), so this is a defensive guard against a
    # degenerate denominator rather than a routine path — and it reports `fitted=False` instead
    # of returning a number a caller would extrapolate with.
    if xi >= 1.0:
        return (a0, 0.0, False)
    return (sigma, xi, True)


def _fit_side(vals: list[float], q: float) -> TailFit:
    """Fit the UPPER tail of `vals`. The lower tail is fitted by passing negated values."""
    n = len(vals)
    s = sorted(vals)
    u = _quantile(s, q)
    excesses = [v - u for v in s if v > u]
    zeta = len(excesses) / n if n else 0.0
    sigma, xi, fitted = fit_gpd(excesses)
    if sigma <= 0:
        sigma = 1e-9
    return TailFit(threshold=u, scale=sigma, xi=xi, exceedance=zeta,
                   n_excess=len(excesses), fitted=fitted)


@dataclass(frozen=True)
class SplicedReturnModel:
    """Empirical body + fitted GPD tails over one horizon's return sample.

    `p_greater(x)` = P(R > x); `p_less(x)` = P(R < x). Both are strictly inside (0, 1).
    """

    n: int
    n_eff: int
    upper: TailFit
    lower: TailFit          # fitted on NEGATED returns; its axis is -r
    _sorted: tuple[float, ...]

    @property
    def underpowered(self) -> bool:
        """True when the window carries too few independent observations to fit a tail.

        An underpowered model still returns probabilities — they are just floor-dominated, and
        a caller that pools them with well-powered ones will read the floor as though it were an
        estimate. Segregate on this flag; do not average across it."""
        return self.n_eff < MIN_N_EFF_FOR_TAIL

    @property
    def p_floor(self) -> float:
        """Half of what ~n_eff independent observations can resolve. Never zero."""
        return 1.0 / (2.0 * max(1, self.n_eff))

    def _clamp(self, p: float) -> float:
        """Keep the answer strictly inside (0, 1) WITHOUT flattening the fitted tail.

        An earlier version floored every probability at `p_floor`. That reintroduced the very
        defect this module exists to remove, from the other side: past the sample maximum the
        GPD's estimate is far below one resolution step, so a blanket floor returned the SAME
        number for a strike 1x, 1.3x and 2x beyond the data — shape information destroyed, and
        the deep tail overstated by ~40x. The floor now catches only the degenerate cases: a
        xi < 0 fit past its own finite endpoint, and anything non-finite. Where the fit does
        produce a number, that number stands and the out-of-sample calibration in
        `scripts/theta_tail_refit.py` is what judges it."""
        f = self.p_floor
        if not math.isfinite(p) or p <= 0.0:
            return f
        if p >= 1.0:
            return 1.0 - f
        return p

    def _empirical_greater(self, x: float) -> float:
        return sum(1 for r in self._sorted if r > x) / self.n

    def p_greater(self, x: float) -> float:
        if x > self.upper.threshold:
            return self._clamp(self.upper.sf(x))
        return self._clamp(self._empirical_greater(x))

    def p_less(self, x: float) -> float:
        if -x > self.lower.threshold:
            return self._clamp(self.lower.sf(-x))
        return self._clamp(1.0 - self._empirical_greater(x))

    def p_between(self, lo: float, hi: float) -> float:
        if hi <= lo:
            return self.p_floor
        # P(lo <= R <= hi) = 1 - P(R < lo) - P(R > hi), each leg taking its own spliced tail so
        # a narrow band far from spot stays positive instead of collapsing to a difference of
        # two numbers that are both 1.0.
        return self._clamp(1.0 - self.p_less(lo) - self.p_greater(hi))

    def describe(self) -> dict:
        """Everything a calibration report needs to explain a probability it disagrees with."""
        return {
            "n": self.n, "n_eff": self.n_eff, "p_floor": self.p_floor,
            "upper_xi": self.upper.xi, "upper_sigma": self.upper.scale,
            "upper_threshold": self.upper.threshold, "upper_n_excess": self.upper.n_excess,
            "upper_fitted": self.upper.fitted,
            "lower_xi": self.lower.xi, "lower_sigma": self.lower.scale,
            "lower_threshold": self.lower.threshold, "lower_n_excess": self.lower.n_excess,
            "lower_fitted": self.lower.fitted,
            "underpowered": self.underpowered,
        }


def build(rets: list[float], h_min: int, *, tail_q: float = DEFAULT_TAIL_Q
          ) -> SplicedReturnModel | None:
    """Fit both tails over one horizon's returns, or None if the window is too thin.

    `h_min` is needed only to derive `n_eff`: overlapping h-minute returns drawn every minute
    carry about n/h independent observations, and every uncertainty statement here is made
    against that number rather than the raw count.
    """
    if not rets or len(rets) < MIN_SAMPLES:
        return None
    n = len(rets)
    n_eff = max(1, n // max(1, int(h_min)))
    return SplicedReturnModel(
        n=n, n_eff=n_eff,
        upper=_fit_side(rets, tail_q),
        lower=_fit_side([-r for r in rets], tail_q),
        _sorted=tuple(sorted(rets)),
    )


def p_yes(model: SplicedReturnModel | None, spot: float | None, strike_type: str,
          floor: float | None, cap: float | None) -> float | None:
    """P(YES) for a Kalshi ladder strike, or None when it cannot be priced.

    Deliberately mirrors `SpotModel.prob_from_returns`'s signature and strike semantics so the
    two can be scored against each other on identical inputs — minus `vol_mult`, which is the
    knob this model exists to replace rather than inherit.
    """
    if model is None or spot is None or spot <= 0:
        return None
    st = (strike_type or "").lower()
    try:
        if st == "greater" and floor:
            return model.p_greater(math.log(float(floor) / spot))
        if st == "less" and cap:
            return model.p_less(math.log(float(cap) / spot))
        if st == "between" and floor and cap:
            return model.p_between(math.log(float(floor) / spot), math.log(float(cap) / spot))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None
