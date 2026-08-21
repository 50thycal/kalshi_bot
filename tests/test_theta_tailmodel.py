"""The spliced EVT probability model (kalshi_bot/theta/tailmodel.py).

The incumbent model's defect is mechanical, not statistical: an empirical distribution has no
mass past its own sample maximum, so 93.1% of theta's ladder output was exactly 0 or exactly 1.
The tests below pin the three properties that make the replacement a fix rather than a
different set of numbers:

  * it NEVER returns exactly 0 or 1, at any distance;
  * past the sample maximum it still DISCRIMINATES — a strike twice as far out prices lower,
    which an earlier draft's blanket floor destroyed;
  * it knows when it cannot know: below the independent-observation bar it says so, because a
    floor-dominated probability pooled with an estimated one reads as evidence and is not.
"""

from __future__ import annotations

import math
import random

import pytest

from kalshi_bot.theta import tailmodel as tm
from kalshi_bot.theta.spot import SpotModel


def gbm(n: int, sigma: float = 0.0006, seed: int = 3) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(0.0, sigma) for _ in range(n)]


def pareto_excesses(alpha: float, n: int, seed: int = 5) -> list[float]:
    rng = random.Random(seed)
    return [(rng.random() ** (-1.0 / alpha)) - 1.0 for _ in range(n)]


class TestGpdFit:
    def test_an_exponential_sample_recovers_xi_zero_and_the_mean_scale(self):
        rng = random.Random(1)
        ys = [rng.expovariate(1.0) for _ in range(20000)]
        sigma, xi, fitted = tm.fit_gpd(ys)
        assert fitted
        assert xi == pytest.approx(0.0, abs=0.05)
        assert sigma == pytest.approx(1.0, abs=0.05)

    def test_a_pareto_sample_recovers_its_tail_index(self):
        # GPD shape xi = 1/alpha for a Pareto tail.
        sigma, xi, fitted = tm.fit_gpd(pareto_excesses(3.0, 20000))
        assert fitted
        assert xi == pytest.approx(1.0 / 3.0, abs=0.06)
        assert sigma > 0

    def test_too_few_excesses_falls_back_to_exponential_and_says_so(self):
        sigma, xi, fitted = tm.fit_gpd([0.1, 0.2, 0.3])
        assert fitted is False
        assert xi == 0.0
        assert sigma == pytest.approx(0.2)

    def test_an_empty_sample_does_not_raise(self):
        assert tm.fit_gpd([]) == (0.0, 0.0, False)

    def test_pwm_saturates_on_a_tail_heavier_than_it_can_represent(self):
        # PWM's moments exist only for xi < 0.5. Against Pareto(alpha=0.5), whose true xi is 2,
        # it returns ~0.95 rather than diverging. Pinned because the bias runs DOWNWARD — the
        # dangerous direction for a seller — so anyone reading a large xi must read it as "at
        # least this heavy" and not as a point estimate.
        _sigma, xi, fitted = tm.fit_gpd(pareto_excesses(0.5, 500, seed=9))
        assert fitted is True
        assert 0.5 < xi < 1.0
        assert xi < 2.0


class TestNoDegenerateProbabilities:
    def test_never_exactly_zero_or_one_however_far_out(self):
        rets = gbm(5000)
        m = tm.build(rets, h_min=35)
        mx, mn = max(rets), min(rets)
        for mult in (1.0, 2.0, 10.0, 100.0):
            assert 0.0 < m.p_greater(mx * mult) < 1.0
            assert 0.0 < m.p_less(mn * mult) < 1.0

    def test_the_incumbent_model_IS_exactly_zero_there(self):
        # The defect this module exists to remove, pinned so the comparison stays honest.
        rets = gbm(5000)
        far = max(rets) * 1.5
        spot = 65000.0
        assert SpotModel.prob_from_returns(
            rets, spot, "greater", spot * math.exp(far), None, 1.0) == 0.0

    def test_vol_mult_cannot_escape_the_truncation(self):
        # Widening rescales the THRESHOLD, so it pulls some strikes back inside the support but
        # leaves anything beyond `max(rets) * k` at exactly zero. This is why mult=2.0 reduced
        # theta4's miss without removing it.
        rets = gbm(5000)
        spot = 65000.0
        strike = spot * math.exp(max(rets) * 3.0)
        assert SpotModel.prob_from_returns(rets, spot, "greater", strike, None, 2.0) == 0.0


class TestTailStillDiscriminates:
    def test_probability_decreases_monotonically_past_the_sample_maximum(self):
        # Two regressions in one. An early draft floored every probability at 1/(2*n_eff),
        # returning the SAME number for a strike 1x, 1.5x and 2x beyond the data. A later one
        # extrapolated a fitted xi < 0 to its endpoint and returned exactly zero past it —
        # reinstating "impossible", just relocated. A Gaussian sample fits xi < 0, so this is
        # exactly the case that caught it.
        rets = gbm(20000)
        m = tm.build(rets, h_min=5)          # n_eff = 4000, so the floor is tiny
        mx = max(rets)
        ps = [m.p_greater(mx * k) for k in (1.0, 1.5, 2.0, 3.0)]
        assert all(a > b for a, b in zip(ps, ps[1:], strict=False)), ps
        assert all(p > 0.0 for p in ps), ps

    def test_a_bounded_fit_still_extrapolates_past_its_own_endpoint(self):
        rets = gbm(20000)
        m = tm.build(rets, h_min=5)
        assert m.upper.xi < 0, "a Gaussian sample should fit a bounded tail"
        endpoint = m.upper.threshold - m.upper.scale / m.upper.xi
        assert m.upper.sf(endpoint * 2.0) > 0.0

    def test_a_heavier_tail_prices_a_far_strike_higher(self):
        light = tm.build(gbm(20000, sigma=0.0006, seed=11), h_min=5)
        heavy_rets = gbm(20000, sigma=0.0006, seed=11)
        heavy_rets[:200] = [r * 12 for r in heavy_rets[:200]]     # inject fat tail events
        heavy = tm.build(heavy_rets, h_min=5)
        x = max(gbm(20000, sigma=0.0006, seed=11)) * 2.0
        assert heavy.p_greater(x) > light.p_greater(x)


class TestKnowsWhenItCannotKnow:
    def test_thetas_actual_window_is_underpowered_and_declares_it(self):
        # 5 days of 1-minute closes at a 35-minute horizon: 7200/35 ~= 205 independent blocks,
        # against the ~400 a GPD at the 95th percentile needs. This is the finding that makes
        # spot RETENTION, not the estimator, the binding constraint.
        m = tm.build(gbm(7200), h_min=35)
        assert m.n_eff == 7200 // 35
        assert m.underpowered is True
        assert m.describe()["underpowered"] is True

    def test_a_long_enough_window_is_not_underpowered(self):
        m = tm.build(gbm(60000), h_min=35)      # ~42 days
        assert m.n_eff >= tm.MIN_N_EFF_FOR_TAIL
        assert m.underpowered is False

    def test_a_thin_window_yields_no_model_at_all(self):
        assert tm.build(gbm(100), h_min=35) is None
        assert tm.build([], h_min=35) is None

    def test_the_floor_is_half_a_resolution_step(self):
        m = tm.build(gbm(7000), h_min=35)
        assert m.p_floor == pytest.approx(1.0 / (2 * m.n_eff))


class TestStrikeSemantics:
    @pytest.fixture()
    def model(self):
        return tm.build(gbm(20000), h_min=5)

    def test_greater_and_less_at_the_same_strike_sum_to_one(self, model):
        spot = 65000.0
        for k in (0.99, 1.0, 1.01):
            g = tm.p_yes(model, spot, "greater", spot * k, None)
            lt = tm.p_yes(model, spot, "less", None, spot * k)
            assert g + lt == pytest.approx(1.0, abs=0.02)

    def test_a_wider_between_band_is_more_probable(self, model):
        spot = 65000.0
        narrow = tm.p_yes(model, spot, "between", spot * 0.999, spot * 1.001)
        wide = tm.p_yes(model, spot, "between", spot * 0.99, spot * 1.01)
        assert wide > narrow

    def test_an_inverted_between_band_is_not_negative(self, model):
        assert tm.p_yes(model, 65000.0, "between", 66000.0, 64000.0) > 0.0

    @pytest.mark.parametrize("args", [
        (None, "greater", 100.0, None),          # no model
        ("model", "greater", None, None),        # no floor strike
        ("model", "less", None, None),           # no cap
        ("model", "weird", 100.0, 200.0),        # unknown strike type
    ])
    def test_unpriceable_inputs_return_None_rather_than_a_guess(self, model, args):
        m = model if args[0] == "model" else None
        assert tm.p_yes(m, 65000.0, args[1], args[2], args[3]) is None

    def test_a_nonpositive_spot_is_unpriceable(self, model):
        assert tm.p_yes(model, 0.0, "greater", 100.0, None) is None
        assert tm.p_yes(model, None, "greater", 100.0, None) is None


class TestTrailingTelemetry:
    def _closes(self, n=500, sigma=0.0005, seed=5):
        rng = random.Random(seed)
        base = 1_700_000_000 // 60 * 60
        px, out = 65000.0, {}
        for i in range(n):
            px *= math.exp(rng.gauss(0.0, sigma))
            out[base + i * 60] = px
        return base, out

    def test_realized_vol_recovers_the_generating_sigma(self):
        base, closes = self._closes(sigma=0.0005)
        m = SpotModel(closes)
        ts = base + 499 * 60
        # 0.0005 in log terms == 5 bps/min.
        assert m.realized_vol_bps(ts, 240) == pytest.approx(5.0, rel=0.25)

    def test_too_few_returns_gives_None_rather_than_a_number(self):
        base, closes = self._closes()
        assert SpotModel(closes).realized_vol_bps(base + 499 * 60, 5) is None

    def test_trailing_move_is_signed(self):
        base = 1_700_000_000 // 60 * 60
        up = {base + i * 60: 100.0 * (1.0 + i * 0.001) for i in range(120)}
        down = {base + i * 60: 100.0 * (1.0 - i * 0.001) for i in range(120)}
        ts = base + 119 * 60
        assert SpotModel(up).trailing_move_bps(ts, 60) > 0
        assert SpotModel(down).trailing_move_bps(ts, 60) < 0

    def test_a_missing_lookback_point_gives_None(self):
        base = 1_700_000_000 // 60 * 60
        m = SpotModel({base: 100.0})
        assert m.trailing_move_bps(base, 60) is None


class TestRefitHarness:
    """`scripts/theta_tail_refit.py` — the out-of-sample scorer. Its own helpers, pinned away
    from the DB, because a wrong horizon bucket or product map would silently score the model
    against the wrong return sample."""

    @staticmethod
    def _mod():
        import importlib.util
        import pathlib
        import sys as _sys

        path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "theta_tail_refit.py"
        spec = importlib.util.spec_from_file_location("theta_tail_refit", path)
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_it_loads_the_ONE_tail_model_rather_than_a_copy(self):
        # The ops runner installs psycopg only, so the script loads tailmodel.py by path. If
        # that ever silently fell back to a duplicated definition, the validation would be
        # scoring a model the worker does not run.
        mod = self._mod()
        assert mod.tm.MIN_N_EFF_FOR_TAIL == tm.MIN_N_EFF_FOR_TAIL
        assert mod.tm.DEFAULT_TAIL_Q == tm.DEFAULT_TAIL_Q

    @pytest.mark.parametrize("minutes,expected", [
        (10.0, 10), (12.4, 10), (13.0, 15), (22.0, 20), (33.0, 35), (35.0, 35),
    ])
    def test_horizon_rounds_to_the_nearest_cached_grid_point(self, minutes, expected):
        assert self._mod().h_bucket(minutes) == expected

    @pytest.mark.parametrize("series,product", [
        ("KXETHD", "ETH"), ("KXETH", "ETH"), ("KXBTCD", "BTC"), ("KXBTC", "BTC"),
        ("KXSOLD", "BTC"), (None, "BTC"),
    ])
    def test_product_map_sends_eth_series_to_the_eth_feed(self, series, product):
        # KXSOLD falling to BTC is a real limitation, not an oversight: there is no SOL candle
        # feed, so a SOL strike is priced off BTC returns. It is called out in the study.
        assert self._mod().product_of(series) == product

    def test_deep_buckets_resolve_below_one_percent(self):
        mod = self._mod()
        # The whole point: 0.001 and 0.015 must not land in one bucket, because a short-tail
        # book lives between them.
        assert mod.deep_bucket(0.001) != mod.deep_bucket(0.015)
        assert mod.deep_bucket(0.001) == "0.000-0.002"
        assert mod.deep_bucket(0.9) == "0.500-1.010"

    def test_the_returns_cache_is_shared_across_tail_q_instances(self):
        # Rebuilding the return samples per tail_q would triple the dominant cost of a sweep
        # for identical numbers.
        mod = self._mod()
        a = mod.FitCache({"BTC": {}, "ETH": {}}, 0.90)
        b = mod.FitCache({"BTC": {}, "ETH": {}}, 0.99)
        assert a._rets is b._rets
