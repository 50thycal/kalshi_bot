"""The Stage-4 A/B replay's rules and its power arithmetic.

The replay costs an ops run against production, so its rules are verified here first. Every
failure below is one that would otherwise have produced a plausible-looking floor derived from
the wrong selected set — which is exactly the defect §4.2.3 was corrected for.
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))

import theta_ab_replay as ab  # noqa: E402


def q(ticker="KXBTC-1", event="E1", mid=10.0, p=0.02, volume=500.0, mtc=20.0,
      bid=9.0, ask=11.0, yes=False) -> dict:
    return {"ticker": ticker, "event": event, "mid": mid, "p_new": p, "p_old": p,
            "volume": volume, "mtc": mtc, "yes_bid": bid, "yes_ask": ask,
            "yes_resolved": yes}


class TestEligibilityIsIdenticalInBothArms:
    def test_a_normal_candidate_is_eligible(self):
        assert ab.eligible(q())

    @pytest.mark.parametrize("kw", [
        {"mid": 2.0}, {"mid": 21.0},           # outside the band
        {"volume": 99.0},                       # under the volume floor
        {"mtc": 9.0}, {"mtc": 36.0},           # outside the entry window
        {"bid": None}, {"ask": None},          # one-sided book
    ])
    def test_each_eligibility_clause_can_reject(self, kw):
        assert not ab.eligible(q(**kw))

    def test_eligibility_does_not_look_at_the_model(self):
        """If eligibility depended on P, the arms would not share a stream and the whole
        comparison would be between different opportunity sets."""
        assert ab.eligible(q(p=0.001)) and ab.eligible(q(p=0.9))


class TestTheTwoArmsSelectDifferently:
    def _stream(self):
        # Cheap tail the model likes; expensive tail the model hates. Control ranks on excess
        # (favours the expensive one), treatment ranks on price (favours the cheap one).
        return [q("cheap", mid=4.0, p=0.02), q("dear", mid=19.0, p=0.05)]

    def test_control_ranks_by_excess_descending(self):
        got = ab.take(self._stream(), lambda r: -(r["mid"] - 100.0 * r["p_new"]),
                      lambda r: (r["mid"] - 100.0 * r["p_new"]) >= ab.CONTROL_EDGE, cap=1)
        assert [r["ticker"] for r in got] == ["dear"]     # 19 - 5 = 14c beats 4 - 2 = 2c

    def test_treatment_ranks_by_price_ascending(self):
        got = ab.take(self._stream(), lambda r: r["mid"],
                      lambda r: r["p_new"] <= ab.TREATMENT_VETO_P, cap=1)
        assert [r["ticker"] for r in got] == ["cheap"]

    def test_the_control_threshold_still_applies(self):
        """'Today's rule, unchanged' includes its 6c edge, not only its ranking."""
        thin = [q("thin", mid=4.0, p=0.03)]                # excess 1c
        got = ab.take(thin, lambda r: -(r["mid"] - 100.0 * r["p_new"]),
                      lambda r: (r["mid"] - 100.0 * r["p_new"]) >= ab.CONTROL_EDGE)
        assert got == []

    def test_the_veto_removes_and_never_promotes(self):
        """A veto can only shrink the treatment's pool. If it could reorder, it would be
        capable of manufacturing winner's curse — the thing the arm exists to avoid."""
        rows = [q("a", mid=5.0, p=0.50), q("b", mid=6.0, p=0.01)]
        got = ab.take(rows, lambda r: r["mid"], lambda r: r["p_new"] <= ab.TREATMENT_VETO_P)
        assert [r["ticker"] for r in got] == ["b"]        # 'a' vetoed despite the cheaper price

    def test_the_per_event_cap_binds_within_an_event_not_across(self):
        rows = [q(f"t{i}", event="E1", mid=3.0 + i) for i in range(5)]
        rows += [q(f"u{i}", event="E2", mid=3.0 + i) for i in range(5)]
        got = ab.take(rows, lambda r: r["mid"], lambda r: True)
        assert len(got) == 2 * ab.PER_EVENT_CAP
        assert sum(1 for r in got if r["event"] == "E1") == ab.PER_EVENT_CAP


class TestTiesBreakDeterministically:
    def test_equal_scores_resolve_the_same_way_every_time(self):
        rows = [q("aaa", mid=5.0), q("bbb", mid=5.0), q("ccc", mid=5.0)]
        runs = {tuple(r["ticker"] for r in ab.take(list(reversed(rows)), lambda r: r["mid"],
                                                   lambda r: True))
                for _ in range(5)}
        assert len(runs) == 1, "a tie broke differently between replays"

    def test_the_tie_key_does_not_depend_on_process_hash_seed(self):
        assert ab.tie_key("KXBTC-1") == ab.tie_key("KXBTC-1")
        assert ab.tie_key("KXBTC-1") != ab.tie_key("KXBTC-2")


class TestTheRequirementUsesEachArmsOwnRate:
    """The §4.2.3 defect in miniature: sizing both arms off ONE historical selected set."""

    def _arm(self, lam, r, deff=2.0, per_day=5.0):
        return {"lam_market": lam, "r": r, "deff": deff, "per_day": per_day}

    def test_a_thinner_treatment_pool_costs_more_sample(self):
        rich = ab.requirement(self._arm(0.03, 1.0), self._arm(0.03, 1.0))
        thin = ab.requirement(self._arm(0.03, 1.0), self._arm(0.01, 1.0))
        assert thin["floor"] > rich["floor"]

    def test_the_design_effect_multiplies_the_iid_requirement(self):
        one = ab.requirement(self._arm(0.03, 1.0, deff=1.0), self._arm(0.03, 1.0, deff=1.0))
        four = ab.requirement(self._arm(0.03, 1.0, deff=4.0), self._arm(0.03, 1.0, deff=1.0))
        assert four["floor"] == pytest.approx(one["floor"] * 4.0)
        assert one["floor"] == pytest.approx(one["iid"])

    def test_it_takes_the_LARGER_design_effect_of_the_two_arms(self):
        a = ab.requirement(self._arm(0.03, 1.0, deff=4.0), self._arm(0.03, 1.0, deff=1.0))
        b = ab.requirement(self._arm(0.03, 1.0, deff=1.0), self._arm(0.03, 1.0, deff=4.0))
        assert a["deff"] == b["deff"] == 4.0

    def test_the_horizon_sits_above_the_floor(self):
        req = ab.requirement(self._arm(0.03, 1.0), self._arm(0.03, 1.0))
        assert req["horizon"] > req["floor"]
        assert req["horizon"] == pytest.approx(req["floor"] * ab.HORIZON_MULTIPLE)

    def test_calendar_time_is_floor_over_cadence_per_arm(self):
        req = ab.requirement(self._arm(0.03, 1.0, per_day=10.0),
                             self._arm(0.03, 1.0, per_day=2.0))
        assert req["days_c"] == pytest.approx(req["floor"] / 10.0)
        assert req["days_t"] == pytest.approx(req["floor"] / 2.0)
        assert req["days_t"] > req["days_c"], "the slower arm must be the binding one"

    def test_an_arm_with_no_expected_loss_yields_no_requirement(self):
        """Refusing to produce a number is the correct output here; a floor computed from a
        zero rate would be infinite or, worse, silently huge."""
        assert ab.requirement(self._arm(0.0, 1.0), self._arm(0.03, 1.0))["ok"] is False
        assert ab.requirement(self._arm(0.03, float("nan")),
                              self._arm(0.03, 1.0))["ok"] is False

    def test_the_arithmetic_matches_the_closed_form(self):
        """Two equal arms at rate lam, R=1: obs per arm = n*lam, and under a halving the
        treatment observes half. Var = 1/obs_T + 1/obs_C = 3/(n*lam)."""
        lam = 0.02
        req = ab.requirement(self._arm(lam, 1.0, deff=1.0), self._arm(lam, 1.0, deff=1.0))
        se = ab.MIN_USEFUL_EFFECT / (ab.Z_ALPHA + ab.Z_POWER)
        assert req["iid"] == pytest.approx(3.0 / (lam * se * se))

    def test_a_halving_is_the_preregistered_effect(self):
        assert ab.MIN_USEFUL_EFFECT == pytest.approx(math.log(2.0))
