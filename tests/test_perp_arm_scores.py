"""PERP-V1 Probe 2's honesty contract.

The scorer's job is not only to compute the pre-registered numbers — it is to refuse to
compute the ones the surface cannot support. Three inputs the pre-registration assumed
are missing (funding, the fee schedule, sub-cadence horizons), and the tempting failure
in every one of them is the same: produce something adjacent and give it the registered
name, so it flows into a gate that asked for a different quantity.

These tests pin the refusals as hard as they pin the arithmetic.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import perp_arm_scores as scorer  # noqa: E402

from kalshi_bot.experiment_os.perp_v1 import COVERAGE_FLOOR_PCT, SAMPLE_FLOOR  # noqa: E402

T0 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _snap(i: int, *, premium: float | None, mark: float | None = 100.0,
          bid: float = 99.9, ask: float = 100.1, price: float = 100.0,
          oi: float = 1000.0) -> dict:
    return {
        "ticker": "KXBTCPERP", "captured_at": T0 + timedelta(seconds=145 * i),
        "bid": bid, "ask": ask, "price": price, "premium_bps": premium,
        "reference_price": 100.0, "settlement_mark_price": mark, "open_interest": oi,
    }


# ---------------------------------------------------------------------------
# The refusals
# ---------------------------------------------------------------------------

def test_the_registered_net_edge_metric_is_never_printed_as_a_number(capsys):
    """`perp_net_edge_bps_per_trade` is defined net of funding. Funding is unreadable, so
    the quantity does not exist and the scorer must say so rather than print the
    ex-funding number under the registered key — a gate reading that key would then be
    reading a cost it did not ask to have omitted."""
    cov = scorer.coverage([], 24.0, 60.0)
    a = {"trades": [], "control": [], "rows_with_premium": 0, "rows_dropped_no_mark": 0}
    c = {"ic": {}, "incremental": {}, "matched": 0, "unmatched": 0}
    scorer.report(cov, a, c, _args(), refused=[5, 10, 30, 60])
    out = capsys.readouterr().out
    assert "perp_net_edge_bps_per_trade  NOT PRODUCIBLE" in out
    # The ex-funding number exists, but only under a name that says what it omits.
    assert "net EX FUNDING" in out


def test_arm_b_is_blocked_not_rescoped(capsys):
    cov = scorer.coverage([], 24.0, 60.0)
    a = {"trades": [], "control": [], "rows_with_premium": 0, "rows_dropped_no_mark": 0}
    c = {"ic": {}, "incremental": {}, "matched": 0, "unmatched": 0}
    scorer.report(cov, a, c, _args(), refused=[])
    out = capsys.readouterr().out
    assert "BLOCKED_DATA" in out
    assert "perp_funding_capture_bps  NO INPUT" in out
    # Substituting premium for funding would be a different hypothesis wearing arm B's
    # pre-registered gate. Nothing in the report may offer one.
    assert "proxy" not in out.lower() or "not re-scoped" in out


def test_arm_a_reports_the_unevaluated_entry_condition(capsys):
    """A pre-registered condition that could not be evaluated is not the same experiment
    as one that was evaluated and passed. Arm A ran on a weaker filter than registered
    and the result has to carry that on its face."""
    cov = scorer.coverage([], 24.0, 60.0)
    a = {"trades": [], "control": [], "rows_with_premium": 0, "rows_dropped_no_mark": 0}
    c = {"ic": {}, "incremental": {}, "matched": 0, "unmatched": 0}
    scorer.report(cov, a, c, _args(), refused=[])
    out = capsys.readouterr().out
    assert "NOT EVALUATED" in out
    assert "DEVIATIONS FROM PRE-REGISTRATION" in out


def test_horizons_below_the_sampling_interval_are_refused_not_nulled():
    """A forward move over a horizon shorter than one sample is unobserved, not small.
    Reporting a null there would read as a kill on the mechanism at that horizon."""
    ok, refused = scorer.measurable_horizons(145.0)
    assert ok == [300]
    assert refused == [5, 10, 30, 60]


def test_a_faster_tape_would_unlock_the_shorter_horizons():
    """The refusal is a property of the instrument, not a permanent verdict — so it has
    to lift when the cadence does."""
    ok, refused = scorer.measurable_horizons(8.0)
    assert ok == [10, 30, 60, 300]
    assert refused == [5]


def test_floors_come_from_the_registered_package_not_a_local_copy():
    """A scorer carrying its own floor could drift from the gate and report a number
    against a bar nobody registered."""
    assert scorer.COVERAGE_FLOOR_PCT is COVERAGE_FLOOR_PCT
    assert scorer.SAMPLE_FLOOR is SAMPLE_FLOOR


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def _cycles(n: int, *, interval: float, seen: int, written: int) -> list[dict]:
    return [{"started_at": T0 + timedelta(seconds=interval * i),
             "finished_at": None, "markets_seen": seen, "market_snapshots": written,
             "orderbook_snapshots": 0, "funding_rows": 0, "errors": 0}
            for i in range(n)]


def test_coverage_is_measured_against_the_intended_cadence():
    """Against the achieved cadence coverage is 100% by construction, which would hide
    exactly the shortfall the clause exists to expose."""
    cov = scorer.coverage(_cycles(25, interval=145.0, seen=4, written=4),
                          hours=1.0, interval_sec=60.0)
    assert cov["expected_cycles"] == 60.0
    assert 41 < cov["cadence_pct"] < 42
    assert cov["per_cycle_pct"] == 100.0
    assert cov["perp_data_coverage_pct"] < COVERAGE_FLOOR_PCT


def test_the_two_ways_to_lose_tape_multiply():
    """Half the cycles at half the markets is a quarter of the intended tape, not
    three-quarters of it."""
    cov = scorer.coverage(_cycles(30, interval=120.0, seen=4, written=2),
                          hours=1.0, interval_sec=60.0)
    assert cov["cadence_pct"] == 50.0
    assert cov["per_cycle_pct"] == 50.0
    assert cov["perp_data_coverage_pct"] == 25.0


def test_no_cycles_is_not_full_coverage():
    cov = scorer.coverage([], 24.0, 60.0)
    assert cov["cadence_pct"] == 0.0
    assert cov["perp_data_coverage_pct"] == 0.0


def test_achieved_interval_is_reported_beside_the_intended_one():
    cov = scorer.coverage(_cycles(5, interval=145.0, seen=1, written=1),
                          hours=1.0, interval_sec=60.0)
    assert abs(cov["achieved_interval_sec"] - 145.0) < 1e-6


# ---------------------------------------------------------------------------
# Arm A
# ---------------------------------------------------------------------------

def _args(**kw):
    class A:
        hours = 24.0
        interval_sec = 60.0
        window = 3
        entry_z = 2.5
        exit_z = 0.5
        exit_residual_bps = 5.0
        max_hold_min = 60.0
        theta_entry_cents = 3.0
        max_feature_age_sec = 290.0
        seed = 1
    a = A()
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_arm_a_fades_a_rich_premium_and_earns_the_convergence():
    """Rich mark -> short the perp; the premium collapsing back is the edge, measured on
    the two prices the collector paired inside one poll."""
    rows = [_snap(i, premium=p) for i, p in enumerate([1, 0, 1, 0, 60, 2])]
    res = scorer.score_arm_a({"KXBTCPERP": rows}, window=3, entry_z=2.5, exit_z=0.5,
                             exit_residual_bps=5.0, max_hold_min=60.0, seed=1)
    assert len(res["trades"]) == 1
    t = res["trades"][0]
    assert t["direction"] == -1
    assert t["gross_bps"] == 58.0            # -1 * (2 - 60)
    assert t["net_ex_funding_bps"] < t["gross_bps"]   # the spread is always paid


def test_a_premium_that_never_converges_is_not_a_round_trip():
    """The tape ending mid-trade is not a flat trade. Counting it would import an
    unrealised mark into a per-round-trip mean."""
    rows = [_snap(i, premium=p) for i, p in enumerate([1, 0, 1, 0, 60, 61, 62])]
    res = scorer.score_arm_a({"KXBTCPERP": rows}, window=3, entry_z=2.5, exit_z=0.5,
                             exit_residual_bps=5.0, max_hold_min=0.0, seed=1)
    # max_hold_min=0 forces the very next observation to close it; with a real hold cap
    # and no convergence the trade is dropped instead.
    res2 = scorer.score_arm_a({"KXBTCPERP": rows}, window=3, entry_z=2.5, exit_z=0.5,
                              exit_residual_bps=5.0, max_hold_min=10_000.0, seed=1)
    assert res["trades"] and not res2["trades"]


def test_rows_without_a_mark_are_dropped_and_counted():
    """Where `settlement_mark_price` is absent the collector wrote a
    last-trade-vs-index number into `premium_bps`. That is a different quantity from
    arm A's signal, so it is excluded — and the exclusion is reported, because a silent
    one is indistinguishable from the tape being complete."""
    rows = [_snap(i, premium=1.0) for i in range(4)]
    rows[2]["settlement_mark_price"] = None
    res = scorer.score_arm_a({"KXBTCPERP": rows}, window=3, entry_z=2.5, exit_z=0.5,
                             exit_residual_bps=5.0, max_hold_min=60.0, seed=1)
    assert res["rows_with_premium"] == 4
    assert res["rows_dropped_no_mark"] == 1


def test_a_one_sided_book_does_not_trade_for_free():
    """An absent quote is not a tight one. Defaulting the half-spread to zero would hand
    arm A a free round trip on exactly the illiquid names it is most likely to pick."""
    assert scorer.half_spread_bps({"bid": None, "ask": 100.1}) is None
    assert scorer.half_spread_bps({"bid": 0, "ask": 100.1}) is None
    assert scorer.half_spread_bps({"bid": 100.2, "ask": 100.1}) is None
    hs = scorer.half_spread_bps({"bid": 99.9, "ask": 100.1})
    assert 9.9 < hs < 10.1


def test_the_control_shares_every_entry_and_randomises_only_direction():
    """Without a matched control `delta.perp_net_edge_bps_per_trade` has nothing to
    resolve against and the horse race measures the crypto tape."""
    rows = [_snap(i, premium=p) for i, p in enumerate([1, 0, 1, 0, 60, 2, 1, 0, 1, 55, 3])]
    res = scorer.score_arm_a({"KXBTCPERP": rows}, window=3, entry_z=2.5, exit_z=0.5,
                             exit_residual_bps=5.0, max_hold_min=60.0, seed=7)
    assert len(res["control"]) == len(res["trades"]) >= 1
    for t, c in zip(res["trades"], res["control"], strict=True):
        assert c["entry_at"] == t["entry_at"]
        assert c["ticker"] == t["ticker"]
        assert c["spread_bps"] == t["spread_bps"]
        assert abs(c["gross_bps"]) == abs(t["gross_bps"])   # same move, maybe other sign


def test_the_control_is_seeded_so_a_rerun_of_one_tape_reproduces():
    rows = [_snap(i, premium=p) for i, p in enumerate([1, 0, 1, 0, 60, 2, 1, 0, 1, 55, 3])]
    kw = dict(window=3, entry_z=2.5, exit_z=0.5, exit_residual_bps=5.0,
              max_hold_min=60.0, seed=11)
    one = scorer.score_arm_a({"KXBTCPERP": rows}, **kw)
    two = scorer.score_arm_a({"KXBTCPERP": rows}, **kw)
    assert [c["direction"] for c in one["control"]] == \
           [c["direction"] for c in two["control"]]


def test_a_flat_premium_history_produces_no_infinite_z():
    """A zero-dispersion window has no z-score. Dividing by it would make every
    observation a 2.5-sigma entry."""
    rows = [_snap(i, premium=5.0) for i in range(8)]
    res = scorer.score_arm_a({"KXBTCPERP": rows}, window=3, entry_z=2.5, exit_z=0.5,
                             exit_residual_bps=5.0, max_hold_min=60.0, seed=1)
    assert res["trades"] == []


# ---------------------------------------------------------------------------
# Arm C
# ---------------------------------------------------------------------------

def test_a_feature_is_timestamped_at_the_later_of_its_inputs():
    """The MLBWX probe manufactured a +5.5c edge by taking direction from a price that
    had not happened yet. A feature stamped at its EARLIER input would be available
    before one of the prices that made it."""
    rows = [_snap(0, premium=1.0, price=100.0), _snap(1, premium=3.0, price=101.0)]
    feats = scorer.perp_features(rows)
    assert len(feats) == 1
    ts, f = feats[0]
    assert ts == rows[1]["captured_at"]
    assert abs(f["perp_return_bps"] - 100.0) < 1e-6
    assert f["premium_impulse_bps"] == 2.0


def test_a_stale_perp_feature_is_not_attached_to_a_ladder_quote():
    """Joining a quote to a feature from twenty minutes ago would measure staleness, not
    lead/lag."""
    series = [(T0, {"perp_return_bps": 5.0})]
    assert scorer._latest_before(series, T0 + timedelta(seconds=100), 290.0) is not None
    assert scorer._latest_before(series, T0 + timedelta(seconds=1200), 290.0) is None


def test_no_feature_from_the_future_is_ever_attached():
    series = [(T0 + timedelta(seconds=600), {"perp_return_bps": 5.0})]
    assert scorer._latest_before(series, T0, 290.0) is None


def test_asset_extraction_refuses_an_unfamiliar_ticker():
    """None rather than a guess: an unrecognised ticker must drop out of the arm C join
    instead of silently joining the wrong asset."""
    assert scorer.asset_of("KXBTCPERP") == "BTC"
    assert scorer.asset_of("KXBTCD") == "BTC"
    assert scorer.asset_of("KXETHD") == "ETH"
    assert scorer.asset_of("BTC-USD") is None
    assert scorer.asset_of("") is None


def test_spearman_needs_dispersion_and_a_sample():
    assert scorer.spearman([1.0, 2.0], [1.0, 2.0]) is None          # too few
    assert scorer.spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None  # no dispersion
    ic = scorer.spearman([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])
    assert abs(ic - 1.0) < 1e-9


def test_the_overlay_is_a_subset_of_the_baseline_on_the_same_mark():
    """Arm C's gated question is INCREMENTAL. If the overlay scored a different set of
    trades or a different mark, the difference would not be the overlay."""
    ladder_rows = []
    for i in range(6):
        ladder_rows.append({
            "market_ticker": "KXBTCD-1", "series": "KXBTCD",
            "captured_at": T0 + timedelta(seconds=300 * i),
            "mid_cents": 50.0 + i, "yes_bid_cents": 49.0 + i, "yes_ask_cents": 51.0 + i,
            "model_p": 0.4, "model_excess_cents": 10.0, "minutes_to_close": 30.0,
        })
    snaps = {"KXBTCPERP": [_snap(i, premium=1.0, price=100.0 + i) for i in range(15)]}
    res = scorer.score_arm_c(snaps, {"KXBTCD-1": ladder_rows}, horizons=[300],
                             max_feature_age_sec=290.0, theta_entry_cents=3.0)
    d = res["incremental"][300]
    assert d["overlay_n"] <= d["baseline_n"]


def test_a_much_later_quote_does_not_stand_in_for_the_horizon():
    """A 300 s horizon filled by a 40-minute-old next row is not that horizon."""
    ladder_rows = [
        {"market_ticker": "KXBTCD-1", "series": "KXBTCD", "captured_at": T0,
         "mid_cents": 50.0, "yes_bid_cents": 49.0, "yes_ask_cents": 51.0,
         "model_p": 0.4, "model_excess_cents": 10.0, "minutes_to_close": 30.0},
        {"market_ticker": "KXBTCD-1", "series": "KXBTCD",
         "captured_at": T0 + timedelta(minutes=40),
         "mid_cents": 90.0, "yes_bid_cents": 89.0, "yes_ask_cents": 91.0,
         "model_p": 0.4, "model_excess_cents": 10.0, "minutes_to_close": 30.0},
    ]
    snaps = {"KXBTCPERP": [_snap(i, premium=1.0, price=100.0 + i) for i in range(3)]}
    res = scorer.score_arm_c(snaps, {"KXBTCD-1": ladder_rows}, horizons=[300],
                             max_feature_age_sec=290.0, theta_entry_cents=3.0)
    assert res["incremental"][300]["baseline_n"] == 0


def test_a_ladder_quote_with_no_book_is_not_traded_for_free():
    ladder_rows = [
        {"market_ticker": "KXBTCD-1", "series": "KXBTCD",
         "captured_at": T0 + timedelta(seconds=300 * i), "mid_cents": 50.0 + i,
         "yes_bid_cents": None, "yes_ask_cents": None, "model_p": 0.4,
         "model_excess_cents": 10.0, "minutes_to_close": 30.0}
        for i in range(4)
    ]
    snaps = {"KXBTCPERP": [_snap(i, premium=1.0, price=100.0 + i) for i in range(12)]}
    res = scorer.score_arm_c(snaps, {"KXBTCD-1": ladder_rows}, horizons=[300],
                             max_feature_age_sec=290.0, theta_entry_cents=3.0)
    assert res["incremental"][300]["baseline_n"] == 0


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_the_script_is_reachable_through_the_ops_channel():
    from ops_runner import ALLOWED_SCRIPTS
    assert "perp_arm_scores" in ALLOWED_SCRIPTS


def test_the_report_states_that_no_gate_is_readable_on_this_tape(capsys):
    """The scorer records nothing and authorizes nothing. Its most important output
    today is which pre-registered clauses cannot be read at all."""
    cov = scorer.coverage([], 24.0, 60.0)
    a = {"trades": [], "control": [], "rows_with_premium": 0, "rows_dropped_no_mark": 0}
    c = {"ic": {}, "incremental": {}, "matched": 0, "unmatched": 0}
    scorer.report(cov, a, c, _args(), refused=[5])
    out = capsys.readouterr().out
    assert "GATE READABILITY" in out
    assert "records nothing" in out
    assert "HOLD" in out
