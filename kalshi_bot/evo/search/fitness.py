"""Search scoring: component metrics first, a derived score second.

**This is not agent fitness and must never become it.** It scores one strategy document
over one replay window, so that variants inside a single search can be ordered. An Evo
agent's authoritative fitness lives in `evo/fitness.py`, scores an *organism* over a
cohort, and includes dimensions no replay can see — adaptive intelligence, opportunity
capture, decayed historical reliability. Nothing here writes `evo_fitness`, and a high
score here entitles a strategy to nothing except the agent's attention.

One consequence worth stating, because it is the difference that matters. Here,
`insufficient` evidence leaves a variant *unranked*: a three-trade sample genuinely
cannot order two strategies, and pretending otherwise would rank noise. In the organism
that rule would be wrong and is deliberately absent — `evo/fitness.py` scores a
low-evidence agent DOWN (component 4: "no incubation") rather than exempting it, because
an exemption is an immunity from selection, and letting an agent avoid the cohort-end
gate by producing too little evidence inverts the north star.

The rule this module exists to enforce is that **raw P&L never decides anything on its
own**. Three failure modes make that necessary, and each has a component that catches it:

* the *lucky* variant — a big number off a handful of trades. Caught by the lower
  confidence bound on per-contract edge, which shrinks hard at small `n`, and by the
  evidence class, which holds a thin sample out of the ranking entirely rather than
  letting it win or lose on noise.
* the *reckless* variant — a big number bought with a drawdown that would have ended
  the account. Caught by the drawdown and tail components.
* the *broken* variant — a big number produced by a replay that did not finish or by
  data that is not what it claims. Caught by the evidence class and the integrity
  component; it is classified `invalid`, not ranked badly.

Every component is persisted with both its raw measurement and its normalized score, so
a rank can be explained to the agent rather than asserted at it. The weights are a
parameter of the call, not a constant in this file — a single opaque number is exactly
what we were asked not to build.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Bumped when a component definition or a normalization changes. Persisted on every
# scored candidate, so a score produced under one evaluator is never silently compared
# with a score produced under another.
EVALUATOR_REVISION = "fit-1"

#: Default weights. Sum to 1.0; a caller may override any subset.
DEFAULT_WEIGHTS: dict[str, float] = {
    "edge_lcb": 0.30,
    "return_on_capital": 0.10,
    "drawdown_control": 0.20,
    "tail_control": 0.15,
    "stability": 0.10,
    "exposure_efficiency": 0.05,
    "concentration": 0.04,
    "breadth": 0.03,
    "integrity": 0.03,
}

#: Scales that turn a raw measurement into a 0..1 score. Caller-overridable.
DEFAULT_SCALES: dict[str, float] = {
    # cents/contract of edge that counts as a strong result
    "edge_scale_cents": 2.0,
    # return on virtual capital that counts as a strong result
    "roc_scale": 0.10,
    # drawdown, as a fraction of starting capital, that scores zero
    "drawdown_tolerance_frac": 0.20,
    # mean cents/contract of the worst tail that scores zero. Binary contracts lose
    # roughly the entry price when they settle against you, so a tolerance near a
    # typical entry would score every strategy zero and discriminate nothing.
    "tail_tolerance_cents": 80.0,
    # net return per turnover dollar that counts as a strong result
    "exposure_scale": 0.02,
    # distinct market families that counts as fully diversified
    "breadth_target": 8.0,
    # one-sided z for the edge lower confidence bound (1.645 ≈ 95%)
    "lcb_z": 1.645,
}

EVIDENCE_ADEQUATE = "adequate"
EVIDENCE_INSUFFICIENT = "insufficient"
EVIDENCE_INVALID = "invalid"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _squash(x: float, scale: float) -> float:
    """Sign-preserving squash of a raw quantity into (0, 1), centred at 0.5.

    A candidate with zero edge scores 0.5, not 0 — the component is a comparison, and
    treating "no edge" as the floor would leave nothing to distinguish it from
    catastrophic. Losing money scores below 0.5."""
    if scale <= 0:
        return 0.5
    z = x / scale
    return _clamp(0.5 * (1.0 + z / (1.0 + abs(z))))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))


def _cvar(values: list[float], pct: float = 0.10) -> float:
    """Mean of the worst `pct` of observations — the tail, not the single worst trade.

    A single worst trade is one draw; the conditional mean is what a candidate can
    expect when things go against it, which is the quantity a drawdown-averse ranking
    should actually price."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(1, int(round(len(ordered) * pct)))
    return _mean(ordered[:k])


@dataclass
class Component:
    """One dimension of fitness: what was measured, and what it scored."""

    key: str
    raw: float | None
    score: float
    weight: float
    detail: str

    def as_dict(self) -> dict:
        return {
            "raw": self.raw,
            "score": round(self.score, 6),
            "weight": round(self.weight, 6),
            "contribution": round(self.score * self.weight, 6),
            "detail": self.detail,
        }


def resolve_weights(overrides: dict | None) -> dict[str, float]:
    """Caller overrides over the defaults, renormalized to sum to 1.

    Renormalizing matters: overriding three weights without touching the rest would
    otherwise silently change the scale of the whole score, and the resulting ranks
    would not be comparable with any produced under the defaults."""
    weights = dict(DEFAULT_WEIGHTS)
    for key, value in (overrides or {}).items():
        if key in weights:
            try:
                weights[key] = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    total = sum(weights.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: v / total for k, v in weights.items()}


def resolve_scales(overrides: dict | None) -> dict[str, float]:
    scales = dict(DEFAULT_SCALES)
    for key, value in (overrides or {}).items():
        if key in scales:
            try:
                scales[key] = float(value)
            except (TypeError, ValueError):
                continue
    return scales


def classify_evidence(
    *,
    run_status: str,
    integrity: dict | None,
    n_trades: int,
    min_trades: int,
) -> tuple[str, str]:
    """Adequate / insufficient / invalid, with the reason.

    The order matters. Invalid is checked first because a broken replay's trade count is
    not evidence of anything, and calling it "insufficient" would invite someone to fix
    it by widening the window."""
    integ = integrity or {}
    if run_status != "completed":
        return EVIDENCE_INVALID, f"run status {run_status}"
    if integ.get("truncated"):
        return EVIDENCE_INVALID, "replay truncated before the window completed"
    if integ.get("data_broken"):
        return EVIDENCE_INVALID, str(integ.get("data_broken_reason") or "data integrity failure")
    if n_trades < min_trades:
        return (
            EVIDENCE_INSUFFICIENT,
            f"n={n_trades} below the search minimum of {min_trades}",
        )
    return EVIDENCE_ADEQUATE, f"n={n_trades}"


def edge_lower_bound(
    per_contract_cents: list[float], *, z: float
) -> tuple[float, float, float]:
    """(lcb, mean, stderr) of per-contract edge in cents.

    This is the single most important number in the module: it is what stops a lucky
    run from outranking a proven one. With few trades the standard error is large and
    the bound sits far below the mean, so a candidate has to earn its rank with sample
    size as well as with size of edge."""
    n = len(per_contract_cents)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean = _mean(per_contract_cents)
    if n == 1:
        # One observation carries no dispersion estimate. Crediting it with its own
        # mean would rank a single lucky trade at the top.
        return min(mean, 0.0), mean, 0.0
    stderr = _stdev(per_contract_cents) / math.sqrt(n)
    return mean - z * stderr, mean, stderr


def compute(
    *,
    outcome: dict,
    ledger: dict,
    integrity: dict | None,
    trade_cents: list[float],
    starting_capital_usd: float,
    weights: dict[str, float],
    scales: dict[str, float],
) -> tuple[dict[str, Component], float]:
    """Build every component, then the weighted score.

    `trade_cents` is the per-contract result of each trade — the dispersion the edge
    bound and the tail component are computed from. Passing it explicitly rather than
    re-deriving it keeps this function pure and unit-testable."""
    integ = integrity or {}
    n = int(outcome.get("n_trades") or 0)
    comps: dict[str, Component] = {}

    # --- edge, lower-bounded -------------------------------------------------
    z = scales["lcb_z"]
    lcb, mean_c, stderr = edge_lower_bound(trade_cents, z=z)
    # Prefer the fill-adjusted number when the calibration actually covers these trades:
    # an edge that only exists in fills we would never receive is not an edge.
    realizable = outcome.get("realizable_cents_per_contract")
    coverage = outcome.get("fill_coverage") or 0.0
    if realizable is not None and coverage and float(coverage) >= 0.5:
        adjustment = float(realizable) - mean_c
        lcb += adjustment
        edge_detail = (
            f"lcb {lcb:.2f}c/ct (mean {mean_c:.2f}, se {stderr:.2f}, n={n}; "
            f"fill-adjusted by {adjustment:+.2f}c at {float(coverage):.0%} coverage)"
        )
    else:
        edge_detail = f"lcb {lcb:.2f}c/ct (mean {mean_c:.2f}, se {stderr:.2f}, n={n})"
    comps["edge_lcb"] = Component(
        "edge_lcb", round(lcb, 4), _squash(lcb, scales["edge_scale_cents"]),
        weights["edge_lcb"], edge_detail,
    )

    # --- return on virtual capital -------------------------------------------
    roc = float(ledger.get("return_on_capital") or 0.0)
    comps["return_on_capital"] = Component(
        "return_on_capital", round(roc, 6), _squash(roc, scales["roc_scale"]),
        weights["return_on_capital"],
        f"{roc:+.2%} on ${starting_capital_usd:,.0f} virtual capital",
    )

    # --- drawdown ------------------------------------------------------------
    max_dd = float(ledger.get("max_drawdown_usd") or 0.0)
    tol = max(1e-9, scales["drawdown_tolerance_frac"] * max(1.0, starting_capital_usd))
    dd_score = _clamp(1.0 - max_dd / tol)
    comps["drawdown_control"] = Component(
        "drawdown_control", round(max_dd, 4), dd_score, weights["drawdown_control"],
        f"max drawdown ${max_dd:,.2f} against a ${tol:,.2f} tolerance",
    )

    # --- tail ----------------------------------------------------------------
    cvar = _cvar(trade_cents, 0.10)
    tail_loss = max(0.0, -cvar)
    tail_score = _clamp(1.0 - tail_loss / max(1e-9, scales["tail_tolerance_cents"]))
    comps["tail_control"] = Component(
        "tail_control", round(cvar, 4), tail_score, weights["tail_control"],
        f"worst-decile mean {cvar:.2f}c/ct",
    )

    # --- stability across subwindows -----------------------------------------
    by_month = outcome.get("by_month") or {}
    months = [m for m in by_month.values() if isinstance(m, dict)]
    positive = sum(1 for m in months if float(m.get("pnl") or 0.0) > 0)
    # Laplace-smoothed: one good month out of one is not a stable strategy.
    stability = (positive + 1) / (len(months) + 2) if months else 0.5
    comps["stability"] = Component(
        "stability", round(stability, 4), _clamp(stability), weights["stability"],
        f"{positive}/{len(months)} subwindows positive",
    )

    # --- exposure efficiency --------------------------------------------------
    turnover = float(ledger.get("turnover_usd") or 0.0)
    net = float(ledger.get("realized_pnl_usd") or 0.0)
    eff = net / turnover if turnover > 0 else 0.0
    comps["exposure_efficiency"] = Component(
        "exposure_efficiency", round(eff, 6), _squash(eff, scales["exposure_scale"]),
        weights["exposure_efficiency"],
        f"{eff:+.2%} net per turnover dollar (${turnover:,.0f} deployed)",
    )

    # --- concentration --------------------------------------------------------
    hhi = ledger.get("concentration_hhi")
    conc_score = _clamp(1.0 - float(hhi)) if hhi is not None else 0.5
    top = ledger.get("concentration_top_family")
    comps["concentration"] = Component(
        "concentration", round(float(hhi), 4) if hhi is not None else None, conc_score,
        weights["concentration"],
        f"HHI {float(hhi):.2f}, top family {float(top):.0%}"
        if hhi is not None and top is not None else "no trades",
    )

    # --- breadth --------------------------------------------------------------
    families = len(outcome.get("by_family") or {})
    target = max(1.0, scales["breadth_target"])
    breadth = math.log1p(families) / math.log1p(target) if families else 0.0
    comps["breadth"] = Component(
        "breadth", families, _clamp(breadth), weights["breadth"],
        f"{families} distinct market families",
    )

    # --- integrity ------------------------------------------------------------
    penalties: list[str] = []
    integ_score = 1.0
    if integ.get("capital_breached"):
        integ_score -= 0.5
        penalties.append(
            f"peak exposure ${float(integ.get('peak_exposure_usd') or 0):,.0f} exceeded "
            f"${starting_capital_usd:,.0f} starting capital"
        )
    if integ.get("concurrency_over_cap"):
        integ_score -= 0.3
        penalties.append(
            f"{integ.get('max_concurrent_positions')} concurrent positions exceeded the "
            "genome's own risk cap"
        )
    if integ.get("fill_model_applied") is False:
        integ_score -= 0.1
        penalties.append("maker-fill calibration did not apply to these entries")
    comps["integrity"] = Component(
        "integrity", None, _clamp(integ_score), weights["integrity"],
        "; ".join(penalties) if penalties else "clean",
    )

    score = sum(c.score * c.weight for c in comps.values())
    return comps, round(score, 6)


def components_payload(comps: dict[str, Component]) -> dict:
    return {key: comp.as_dict() for key, comp in comps.items()}


def explain(components: dict | None, *, limit: int = 4) -> str:
    """The components that moved a rank most, best and worst, as one line.

    Contribution, not score: a high score on a 3%-weight component is not why anything
    ranked where it did, and showing it would be a misleading explanation."""
    if not components:
        return "no components recorded"
    rows = [
        (key, float(val.get("contribution") or 0.0), float(val.get("weight") or 0.0),
         str(val.get("detail") or ""))
        for key, val in components.items()
        if isinstance(val, dict)
    ]
    if not rows:
        return "no components recorded"
    # Distance from a neutral 0.5 contribution is what actually separated this candidate
    # from the field.
    rows.sort(key=lambda r: abs(r[1] - 0.5 * r[2]), reverse=True)
    return " · ".join(f"{key}: {detail}" for key, _, _, detail in rows[:limit])


__all__ = [
    "DEFAULT_SCALES",
    "DEFAULT_WEIGHTS",
    "EVALUATOR_REVISION",
    "EVIDENCE_ADEQUATE",
    "EVIDENCE_INSUFFICIENT",
    "EVIDENCE_INVALID",
    "Component",
    "classify_evidence",
    "components_payload",
    "compute",
    "edge_lower_bound",
    "explain",
    "resolve_scales",
    "resolve_weights",
]
