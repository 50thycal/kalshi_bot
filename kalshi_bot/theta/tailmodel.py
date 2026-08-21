"""A probability model for theta that has a tail beyond the data it was fitted on.

WHY THIS EXISTS. `SpotModel.prob_from_returns` returns the raw empirical frequency of trailing
overlapping h-minute log-returns that satisfy the strike:

    sum(1 for r in rets if r > x) / n

An empirical distribution has **no mass beyond its own sample maximum**. Any strike further out
than the largest move seen in the trailing window therefore prices at *exactly* 0.0, and any
strike inside the smallest at exactly 1.0. Measured over 111,242 ladder quotes in theta's entry
window: **53.7% price at exactly 0 and 39.4% at exactly 1** — 93.1% of the model's output is not
a probability at all (`docs/RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md` §2.3).

It also explains why `mult=2.0` did not repair anything: `vol_mult` rescales the THRESHOLD
(`x / k`), so it can pull a strike back inside the empirical support, but where `x / k` still
exceeds `max(rets)` the answer is still exactly zero. Widening a distribution that ends at a hard
edge moves the edge; it does not remove it.

WHAT THIS MODULE DOES **NOT** CLAIM. An earlier draft of this docstring said the truncation
"explains the strategy's failure shape". It does not, and the historical validation says so:
replacing the degenerate output improved the deepest probabilities substantially (the 0–0.2%
bucket's miss fell from ~21x to ~7x) while leaving the overall miss unchanged (R 1.79 against the
incumbent's 1.71), still understating 1–5% events by 3.5–9.8x, and leaving residual selection
bias large. Degeneracy was a real defect and removing it was worth doing. It was not the cause of
theta4's failure, and this module is not a fix for that failure — see
`docs/RESEARCH_THETA_REMEDIATION.md`.

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

NON-OVERLAPPING BLOCKS, NOT A METADATA DISCLAIMER. `SpotModel._returns` emits an h-minute return
from EVERY minute, so consecutive samples share h-1 of their h minutes and one underlying shock
appears as ~h neighbouring extreme observations. Fitting a GPD to that is pseudo-replication: the
exceedance count, the scale AND the shape are estimated as though one move were dozens of
independent ones. An earlier version of this module reported an `n_eff` disclaimer alongside an
overlapping fit and called that honest. It was not — a denominator in the metadata does not
correct a fitted shape.

The fit therefore consumes **non-overlapping** h-minute blocks (`SpotModel.block_returns`,
stepping by h rather than by 1).

WHAT THE PROBABILITY IS, AND WHY THE FIT MATCHES IT. The quantity a strike needs is MARGINAL:
"what is P(the next h-minute return exceeds x)". So both halves of the survival function are
estimated on the marginal distribution — `zeta` is the per-block exceedance frequency, and the
GPD severity is fitted to **all** marginal excesses over the threshold.

An earlier version fitted the severity to declustered CLUSTER MAXIMA while keeping `zeta` as the
raw per-block frequency. That was incoherent: it multiplied a marginal exceedance rate by a
cluster-max conditional severity, which is the survival function of nothing. Either both halves
are marginal (this) or both are cluster-based, in which case `zeta` becomes a cluster arrival
rate and an extremal-index adjustment is needed to return to a per-block probability. The
marginal form is chosen because a single upcoming block is exactly what a strike asks about, and
because it needs no extremal-index estimate stacked on an already thin sample.

Declustering did not go away; it changed job. Runs-declustering collapses exceedances separated
by fewer than `RUN_SEPARATION` blocks into one cluster, and the resulting count measures how much
INDEPENDENT evidence stands behind the fit — reported per tail, and what gates "powered". It no
longer touches the estimate. Estimation and uncertainty are different questions, and they were
conflated.

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

# Refuse to fit at all below this many non-overlapping blocks. Lower than SpotModel's 300
# because blocks are ~h times scarcer than overlapping returns by construction; the per-tail
# power test above is what actually decides whether a fit may be read as an estimate.
MIN_SAMPLES = 60

# A GPD needs this many DECLUSTERED exceedances on a side before that side is an estimate.
#
# The bar is on exceedances, not on blocks, because the two are not interchangeable: 400 blocks
# give ~40 exceedances at tail_q=0.90 and ~4 at tail_q=0.99. An earlier version gated on a fixed
# block count and would have called the second one powered.
MIN_TAIL_EXCEEDANCES_FOR_POWER = 20

# Runs-declustering separation, in BLOCKS. Exceedances closer together than this belong to one
# storm and count once. 2 is the conventional minimum; it is reported with every fit so a reader
# sees what was assumed instead of inferring it.
RUN_SEPARATION = 2


@dataclass(frozen=True)
class TailFit:
    """One side's fitted tail. `xi` > 0 is heavy, 0 is exponential, < 0 is bounded."""

    threshold: float      # u, in log-return units, on this side's own sign convention
    scale: float          # sigma
    xi: float             # shape
    exceedance: float     # zeta_u = P(block beyond u), empirical over non-overlapping blocks
    n_excess: int         # marginal exceedances over u — what the severity is FITTED to
    n_clusters: int       # after runs-declustering — the INDEPENDENT evidence behind the fit
    fitted: bool          # False = exponential fallback (too few excesses)

    @property
    def powered(self) -> bool:
        """Whether THIS side carries enough INDEPENDENT evidence to be read as an estimate.

        The fit uses every marginal excess; the power test uses the declustered count, because
        dependent excesses inflate a sample without adding information. A fit can therefore be
        arithmetically well-determined and still unpowered, which is the honest description of
        one storm's worth of data."""
        return self.fitted and self.n_clusters >= MIN_TAIL_EXCEEDANCES_FOR_POWER

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


def decluster(vals: list[float], u: float, run_separation: int = RUN_SEPARATION) -> list[float]:
    """Runs-declustering: exceedances of `u` separated by fewer than `run_separation`
    non-exceeding blocks form ONE cluster, represented by its maximum.

    `vals` must be in TIME ORDER — the construction is entirely about adjacency, so handing it a
    sorted list would collapse everything into one cluster and silently report 1. Volatility
    clusters, so even non-overlapping blocks put several exceedances inside a single storm;
    counting those separately is the same pseudo-replication de-overlapping removed, one level
    up."""
    clusters: list[float] = []
    current: float | None = None
    gap = 0
    for v in vals:
        if v > u:
            current = v if current is None else max(current, v)
            gap = 0
        elif current is not None:
            gap += 1
            if gap >= run_separation:
                clusters.append(current)
                current = None
    if current is not None:
        clusters.append(current)
    return clusters


def _fit_side(vals: list[float], q: float) -> TailFit:
    """Fit the UPPER tail of `vals`, which must be in TIME ORDER (see `decluster`).

    The lower tail is fitted by passing negated values."""
    n = len(vals)
    u = _quantile(sorted(vals), q)
    raw = [v - u for v in vals if v > u]
    clusters = [c - u for c in decluster(vals, u)]
    # BOTH halves marginal: `zeta` is the per-block exceedance frequency and the severity is
    # fitted to every marginal excess. Fitting cluster maxima here while keeping a marginal
    # `zeta` multiplies two different distributions together — see the module docstring.
    zeta = len(raw) / n if n else 0.0
    sigma, xi, fitted = fit_gpd(raw)
    if sigma <= 0:
        sigma = 1e-9
    return TailFit(threshold=u, scale=sigma, xi=xi, exceedance=zeta,
                   n_excess=len(raw), n_clusters=len(clusters), fitted=fitted)


@dataclass(frozen=True)
class SplicedReturnModel:
    """Empirical body + fitted GPD tails over one horizon's return sample.

    `p_greater(x)` = P(R > x); `p_less(x)` = P(R < x). Both are strictly inside (0, 1).
    """

    n: int                  # non-overlapping blocks the fit consumed
    horizon_min: int
    fit_days: float         # ACTUAL span of history the fit saw
    requested_fit_days: float   # what was ASKED for — the two differ on a cold start
    tail_q: float
    upper: TailFit
    lower: TailFit          # fitted on NEGATED returns; its axis is -r
    _sorted: tuple[float, ...]

    @property
    def n_eff(self) -> int:
        """Blocks ARE the independent unit now, so this is `n`. Kept as a name because stored
        telemetry and reports ask for it."""
        return self.n

    @property
    def underpowered(self) -> bool:
        """True when EITHER tail has too little declustered evidence to be read as an estimate.

        Either-side rather than both, deliberately: a `less` strike prices off the lower tail, so
        a model whose upper tail is powered and lower tail is not would still hand out an
        unsupported number half the time. Callers who care about one direction should ask
        `powered_for(strike_type)`.

        An underpowered model still returns probabilities — they are just not evidence.
        Segregate on this flag; never average across it."""
        return not (self.upper.powered and self.lower.powered)

    def powered_for(self, strike_type: str) -> bool:
        """Whether the tail THIS strike is priced off has enough declustered evidence."""
        st = (strike_type or "").lower()
        if st == "greater":
            return self.upper.powered
        if st == "less":
            return self.lower.powered
        return self.upper.powered and self.lower.powered

    def active_xi(self, strike_type: str) -> float | None:
        """The shape parameter of the tail this strike actually uses.

        Storing `upper_xi` beside a `less` strike's probability describes the wrong tail — the
        defect this replaces. A `between` strike uses both, so it has no single active xi."""
        st = (strike_type or "").lower()
        if st == "greater":
            return self.upper.xi
        if st == "less":
            return self.lower.xi
        return None

    def active_clusters(self, strike_type: str) -> int | None:
        """Declustered exceedances backing the tail this strike uses."""
        st = (strike_type or "").lower()
        if st == "greater":
            return self.upper.n_clusters
        if st == "less":
            return self.lower.n_clusters
        return min(self.upper.n_clusters, self.lower.n_clusters)

    @property
    def p_floor(self) -> float:
        """Half of what `n` independent blocks can resolve. Never zero."""
        return 1.0 / (2.0 * max(1, self.n))

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
            "blocks": self.n, "n_eff": self.n_eff, "p_floor": self.p_floor,
            "horizon_min": self.horizon_min, "fit_days": self.fit_days,
            "requested_fit_days": self.requested_fit_days,
            "tail_q": self.tail_q, "run_separation": RUN_SEPARATION,
            "min_tail_exceedances": MIN_TAIL_EXCEEDANCES_FOR_POWER,
            "upper_xi": self.upper.xi, "upper_sigma": self.upper.scale,
            "upper_threshold": self.upper.threshold, "upper_n_excess": self.upper.n_excess,
            "upper_n_clusters": self.upper.n_clusters, "upper_fitted": self.upper.fitted,
            "upper_powered": self.upper.powered,
            "lower_xi": self.lower.xi, "lower_sigma": self.lower.scale,
            "lower_threshold": self.lower.threshold, "lower_n_excess": self.lower.n_excess,
            "lower_n_clusters": self.lower.n_clusters, "lower_fitted": self.lower.fitted,
            "lower_powered": self.lower.powered,
            "underpowered": self.underpowered,
        }


def build(blocks: list[float], h_min: int, *, tail_q: float = DEFAULT_TAIL_Q,
          fit_days: float = 0.0, requested_fit_days: float | None = None
          ) -> SplicedReturnModel | None:
    """Fit both tails over one horizon's NON-OVERLAPPING blocks, in TIME ORDER.

    `blocks` must come from `SpotModel.block_returns` (step h), never `SpotModel.returns`
    (step 1) — passing overlapping returns restores the pseudo-replication this module exists to
    remove, and the time ordering is load-bearing for declustering.
    """
    if not blocks or len(blocks) < MIN_SAMPLES:
        return None
    return SplicedReturnModel(
        n=len(blocks), horizon_min=int(h_min), fit_days=float(fit_days),
        requested_fit_days=float(fit_days if requested_fit_days is None else requested_fit_days),
        tail_q=float(tail_q),
        upper=_fit_side(blocks, tail_q),
        lower=_fit_side([-r for r in blocks], tail_q),
        _sorted=tuple(sorted(blocks)),
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
