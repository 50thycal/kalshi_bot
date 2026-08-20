"""One-sided bound clauses, and the promotion/failure evidence-floor split.

Two defects motivate this file, and each is pinned rather than described.

**A point estimate is not a decision standard.** `mean > 0` passes half the time
when the true effect is exactly zero, at every sample size — so a sample floor
controls variance without ever bounding false promotion. A bound clause fixes the
error rate by construction.

**One shared floor silences safety clauses.** The evaluator checked sample floors
before `fail_any`, so a catastrophic failure below the promotion floor sat at HOLD
while real money kept trading. Observed on `mmsell-anchor-strangle`. A `fail_any`
clause may now carry its own `min_evidence` and fire on that floor alone; one
without `min_evidence` still waits for the promotion floor, so nothing frozen
before this change behaves differently.
"""

from __future__ import annotations

import pytest

from kalshi_bot.experiment_os.bounds import (
    Z_ONE_SIDED,
    compute_bound,
    poisson_rate_lower,
    poisson_rate_upper,
    validate_bound_spec,
)


def _spec(direction="lower", confidence=0.95, method="normal"):
    return {"direction": direction, "confidence": confidence, "method": method}


# ---------------------------------------------------------------------------
# Normal bounds
# ---------------------------------------------------------------------------


def test_normal_bound_moves_away_from_the_estimate_in_the_named_direction():
    lo = compute_bound(estimate=10.0, stderr=2.0, provenance={}, spec=_spec("lower"))
    hi = compute_bound(estimate=10.0, stderr=2.0, provenance={}, spec=_spec("upper"))
    z = Z_ONE_SIDED[0.95]
    assert lo.value == pytest.approx(10.0 - z * 2.0, abs=1e-6)
    assert hi.value == pytest.approx(10.0 + z * 2.0, abs=1e-6)
    assert lo.value < 10.0 < hi.value


def test_a_higher_confidence_is_a_more_demanding_bound():
    a = compute_bound(estimate=5.0, stderr=1.0, provenance={},
                      spec=_spec(confidence=0.95)).value
    b = compute_bound(estimate=5.0, stderr=1.0, provenance={},
                      spec=_spec(confidence=0.99)).value
    assert b < a, "99% must be harder to clear than 95% for a lower bound"


def test_a_missing_standard_error_is_MISSING_not_the_point_estimate(recwarn):
    """The whole reason the clause exists. Falling back to the estimate would
    restore the 50%-at-zero behavior a bound is bought to remove."""
    r = compute_bound(estimate=1.0, stderr=None, provenance={}, spec=_spec())
    assert r.missing is True
    assert r.value is None
    assert "not silently downgraded" in r.reason


def test_a_bad_bound_spec_is_refused_rather_than_defaulted():
    assert validate_bound_spec({"direction": "sideways", "confidence": 0.95,
                                "method": "normal"})
    assert validate_bound_spec({"direction": "lower", "confidence": 0.9123,
                                "method": "normal"})
    assert validate_bound_spec({"direction": "lower", "confidence": 0.95,
                                "method": "bootstrap"})
    assert validate_bound_spec("nope")
    assert validate_bound_spec(_spec()) == []


def test_direction_is_never_inferred_from_the_operator():
    """`UCB > t` and `LCB > t` are different tests. A metric's own higher/lower-
    is-better direction does not decide which one a contract wants."""
    assert "direction" in " ".join(
        validate_bound_spec({"confidence": 0.95, "method": "normal"})
    )


# ---------------------------------------------------------------------------
# Exact Poisson bounds — for rare-event ratios where a normal bound is wrong
# ---------------------------------------------------------------------------


def test_poisson_lower_bound_is_zero_at_zero_observations():
    """No observations is no lower bound. A small positive number would be a
    claim the data does not make."""
    assert poisson_rate_lower(0, 0.95) == 0.0


def test_poisson_bounds_bracket_the_observed_count():
    for k in (1, 5, 20, 100):
        lo = poisson_rate_lower(k, 0.95)
        hi = poisson_rate_upper(k, 0.95)
        assert lo < k < hi, k


def test_poisson_lower_bound_matches_its_defining_tail_probability():
    """lambda_L(k) is the rate at which observing k or more has probability 5%."""
    from kalshi_bot.experiment_os.bounds import _poisson_sf

    for k in (3, 12, 40):
        lam = poisson_rate_lower(k, 0.95)
        assert _poisson_sf(lam, k) == pytest.approx(0.05, abs=1e-4), k


def test_poisson_bound_on_theta4s_actual_evidence():
    """theta4's measured paper evidence: 20 hits against 17.1808 expected.

    R-hat is 1.164, which reads like miscalibration until it is bounded — the 95%
    lower bound is 0.77, nowhere near the 1.0 threshold. This is the number the
    tail clause would have seen, and it does not fire."""
    r = compute_bound(estimate=20 / 17.1808, stderr=None,
                      provenance={"observed": 20, "expected": 17.1808},
                      spec=_spec(method="poisson_exact", confidence=0.95))
    assert r.value == pytest.approx(0.771, abs=0.002)
    assert r.value < 1.0, "the clause must not fire on this evidence"
    assert r.provenance["uncertainty_inputs"]["observed"] == 20


def test_poisson_bound_needs_observed_and_expected():
    r = compute_bound(estimate=1.2, stderr=3.0, provenance={},
                      spec=_spec(method="poisson_exact"))
    assert r.missing is True
    assert "observed" in r.reason


def test_a_zero_expected_count_is_undefined_not_infinite():
    r = compute_bound(estimate=None, stderr=None,
                      provenance={"observed": 3, "expected": 0.0},
                      spec=_spec(method="poisson_exact"))
    assert r.missing is True


def test_the_exact_bound_is_usable_where_a_normal_one_is_not():
    """At the theta tail failure floor the expected count is ~4. A normal bound on
    a count that small is simply wrong; the exact one is valid there."""
    E = 0.0792 * 50
    assert E == pytest.approx(3.96, abs=0.01)
    # 10 hits against 3.96 expected is a real signal at 99%…
    hot = compute_bound(estimate=10 / E, stderr=None,
                        provenance={"observed": 10, "expected": E},
                        spec=_spec(method="poisson_exact", confidence=0.99))
    assert hot.value > 1.0
    # …while 4 hits against 3.96 expected is not.
    calm = compute_bound(estimate=4 / E, stderr=None,
                         provenance={"observed": 4, "expected": E},
                         spec=_spec(method="poisson_exact", confidence=0.99))
    assert calm.value < 1.0


def test_false_fire_rate_is_controlled_at_the_stated_confidence():
    """Under a perfectly calibrated model the clause fires at most (1 - conf) of
    the time — at every n, which is what makes the bound usable at small samples."""
    from kalshi_bot.experiment_os.bounds import _poisson_sf

    for n in (25, 50, 100, 300):
        E = 0.0792 * n
        k = 1
        while compute_bound(estimate=k / E, stderr=None,
                            provenance={"observed": k, "expected": E},
                            spec=_spec(method="poisson_exact",
                                       confidence=0.99)).value <= 1.0:
            k += 1
        assert _poisson_sf(E, k) < 0.01, f"n={n}: false-fire above the 1% budget"


# ---------------------------------------------------------------------------
# Evaluator integration — the promotion / failure floor split
# ---------------------------------------------------------------------------

from kalshi_bot.experiment_os import evaluator  # noqa: E402
from tests.test_experiment_os_evaluator import _experiment, _gate, _trade  # noqa: E402

# A promotion floor far above what the evidence will reach, so the split is
# visible: without an early-failure floor every verdict below would be HOLD.
HIGH_FLOOR = {"treatment": {"metric": "settled_trades", "op": ">=", "value": 500}}


def test_a_safety_clause_with_its_own_floor_fails_below_the_promotion_floor(
        xos_session, xos_platform):
    """The defect this exists to fix. Ten bad trades against a 500-trade promotion
    floor used to sit at HOLD while the money kept trading."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="early-fail")
    gate = _gate(s, ver, {
        "sample": HIGH_FLOOR,
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
        "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": "<", "value": -1.0,
                      "min_evidence": {"metric": "settled_trades",
                                       "op": ">=", "value": 10}}],
    })
    for _ in range(10):
        _trade(s, "t_tag", pnl=-0.50)
    s.commit()

    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "FAIL"
    assert "early safety failure" in out.explanation


def test_the_same_clause_is_inert_before_its_own_floor(xos_session, xos_platform):
    """Ineligible is not false and it is not true — it has not been tested."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="early-inert")
    gate = _gate(s, ver, {
        "sample": HIGH_FLOOR,
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
        "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": "<", "value": -1.0,
                      "min_evidence": {"metric": "settled_trades",
                                       "op": ">=", "value": 10}}],
    })
    for _ in range(3):          # bad, but only 3 of the 10 required
        _trade(s, "t_tag", pnl=-0.50)
    s.commit()

    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "HOLD"
    clause = next(c for c in out.clauses if c.list_name == "fail_any")
    assert clause.eligible is False
    assert clause.provenance["min_evidence"]["met"] is False


def test_a_clause_without_min_evidence_still_waits_for_the_promotion_floor(
        xos_session, xos_platform):
    """Backwards compatibility, pinned. Every gate frozen before this change has
    no `min_evidence`, so none of them may start behaving differently — this is
    the `mmsell-anchor-strangle` shape that reported HOLD."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="legacy-shape")
    gate = _gate(s, ver, {
        "sample": HIGH_FLOOR,
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
        "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": "<", "value": -1.0}],
    })
    for _ in range(10):
        _trade(s, "t_tag", pnl=-0.50)
    s.commit()

    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "HOLD", "an unfloored fail_any must not fire early"
    assert "sample floor not met" in out.explanation


def test_min_evidence_is_refused_outside_fail_any(xos_session, xos_platform):
    """A promotion clause's floor is the gate's `sample`. Two floors on the pass
    side would be two answers to one question."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="me-misplaced")
    with pytest.raises(Exception) as ei:
        _gate(s, ver, {
            "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                          "op": ">", "value": 0,
                          "min_evidence": {"metric": "settled_trades",
                                           "op": ">=", "value": 10}}],
        })
    assert "min_evidence" in str(ei.value)


def test_a_malformed_bound_is_refused_at_registration(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="bad-bound")
    with pytest.raises(Exception) as ei:
        _gate(s, ver, {
            "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                          "op": ">", "value": 0,
                          "bound": {"direction": "sideways", "confidence": 0.95,
                                    "method": "normal"}}],
        })
    assert "direction" in str(ei.value)


def test_a_bound_clause_compares_the_bound_not_the_estimate(xos_session,
                                                            xos_platform):
    """Ten trades averaging +5c/trade is a positive point estimate and nowhere
    near a positive 95% lower bound. The point estimate would PASS; the bound
    must not."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="bound-gate")
    gate = _gate(s, ver, {
        "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 5}},
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
    })
    for pnl in (0.60, -0.40, 0.55, -0.35, 0.50, -0.30, 0.45, 0.10, -0.20, 0.30):
        _trade(s, "t_tag", pnl=pnl)
    s.commit()
    plain = evaluator.evaluate_gate(s, gate, persist=False)
    assert plain.verdict == "PASS", "the point-estimate form passes on this data"

    # …and the metric layer has no standard error for pnl_cents_per_trade, so a
    # bound clause on it is honestly MISSING rather than quietly re-using the
    # estimate.
    exp2, ver2, epoch2 = _experiment(s, key="bound-gate-2",
                                     arms=(("treatment", "treatment", "t2_tag"),
                                           ("control", "control", "c2_tag")))
    gate2 = _gate(s, ver2, {
        "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 5}},
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0,
                      "bound": {"direction": "lower", "confidence": 0.95,
                                "method": "normal"}}],
    })
    for pnl in (0.60, -0.40, 0.55, -0.35, 0.50, -0.30, 0.45, 0.10, -0.20, 0.30):
        _trade(s, "t2_tag", pnl=pnl)
    s.commit()
    bounded = evaluator.evaluate_gate(s, gate2, persist=False)
    assert bounded.verdict == "BLOCKED_DATA"
    assert any("standard error" in r for r in bounded.blocking_reasons)


def test_the_failure_explanation_is_readable_without_recomputing_anything(
        xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="readable")
    gate = _gate(s, ver, {
        "sample": HIGH_FLOOR,
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
        "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": "<", "value": -1.0,
                      "min_evidence": {"metric": "settled_trades",
                                       "op": ">=", "value": 10}}],
    })
    for _ in range(12):
        _trade(s, "t_tag", pnl=-0.50)
    s.commit()

    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "FAIL"
    clause = next(c for c in out.clauses if c.list_name == "fail_any")
    me = clause.provenance["min_evidence"]
    assert me["met"] is True and me["observed"] == 12 and me["value"] == 10


# ---------------------------------------------------------------------------
# The evidence horizon
#
# A continuously-evaluated bound is a sequential test: its per-look confidence is
# not its lifetime rate. Measured on the two live-canary promotion gates, a 99%
# bound holds ~5% lifetime false promotion over a 3x horizon and creeps up past
# it. The horizon is the half of that calibration that lives in the contract —
# without it the "~5%" claim is aspirational rather than exact.
# ---------------------------------------------------------------------------


HORIZON_SPEC = {
    "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 5}},
    "max_evidence_horizon": {"metric": "settled_trades", "value": 20},
    "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                  "op": ">", "value": 0}],
    "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                  "op": "<", "value": -100}],
}


def test_a_gate_decides_normally_before_its_horizon(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="pre-horizon")
    gate = _gate(s, ver, HORIZON_SPEC)
    for _ in range(10):
        _trade(s, "t_tag", pnl=0.20)
    s.commit()
    assert evaluator.evaluate_gate(s, gate, persist=False).verdict == "PASS"


def test_at_the_horizon_no_further_authorization_look_accrues(xos_session,
                                                              xos_platform):
    """The same evidence that PASSed at n=10 must not PASS at the horizon."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="at-horizon")
    gate = _gate(s, ver, HORIZON_SPEC)
    for _ in range(25):
        _trade(s, "t_tag", pnl=0.20)
    s.commit()

    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "HORIZON_EXHAUSTED"
    assert out.verdict != "PASS"
    assert "OPERATOR DECISION REQUIRED" in out.explanation


def test_the_horizon_does_not_auto_fail_either(xos_session, xos_platform):
    s = xos_session
    exp, ver, epoch = _experiment(s, key="horizon-losing")
    gate = _gate(s, ver, HORIZON_SPEC)
    for _ in range(25):
        _trade(s, "t_tag", pnl=-9.99)     # trips the fail_any clause
    s.commit()

    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert out.verdict == "HORIZON_EXHAUSTED"
    assert out.verdict != "FAIL"


def test_the_horizon_verdict_reports_where_every_clause_stood(xos_session,
                                                              xos_platform):
    """A decision point, not a wait — so the operator decides on the evidence
    rather than on silence."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="horizon-informative")
    gate = _gate(s, ver, HORIZON_SPEC)
    for _ in range(25):
        _trade(s, "t_tag", pnl=0.20)
    s.commit()

    out = evaluator.evaluate_gate(s, gate, persist=False)
    assert "Clause standing at the horizon" in out.explanation
    assert "pass_all:pnl_cents_per_trade" in out.explanation
    assert "met" in out.explanation


def test_a_horizon_below_its_own_promotion_floor_is_refused(xos_session,
                                                            xos_platform):
    """Such a gate could never render a verdict at all."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="horizon-inverted")
    with pytest.raises(Exception) as ei:
        _gate(s, ver, {**HORIZON_SPEC,
                       "max_evidence_horizon": {"metric": "settled_trades",
                                                "value": 5}})
    assert "not above the promotion floor" in str(ei.value)


def test_a_horizon_in_a_different_unit_from_its_floor_is_refused(xos_session,
                                                                 xos_platform):
    """A horizon in contracts bounding a floor in markets cannot be reasoned
    about — the two count different things."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="horizon-unit")
    with pytest.raises(Exception) as ei:
        _gate(s, ver, {**HORIZON_SPEC,
                       "max_evidence_horizon": {"metric": "settled_contracts",
                                                "value": 900}})
    assert "different unit" in str(ei.value)


def test_a_gate_with_no_horizon_is_unaffected(xos_session, xos_platform):
    """Every contract frozen so far has no horizon and must behave as before."""
    s = xos_session
    exp, ver, epoch = _experiment(s, key="no-horizon")
    gate = _gate(s, ver, {
        "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 5}},
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
    })
    for _ in range(500):
        _trade(s, "t_tag", pnl=0.20)
    s.commit()
    assert evaluator.evaluate_gate(s, gate, persist=False).verdict == "PASS"


def test_horizon_exhausted_can_never_authorize_a_transition():
    """Fail-safe by construction: every authorization path tests `== PASS`."""
    from kalshi_bot.experiment_os.lifecycle import GateVerdict

    assert GateVerdict.HORIZON_EXHAUSTED.value != GateVerdict.PASS.value
    assert GateVerdict.HORIZON_EXHAUSTED.value not in {
        GateVerdict.PASS.value, GateVerdict.FAIL.value, GateVerdict.HOLD.value}
