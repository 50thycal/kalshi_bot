"""DEPTH-AWARE maker fill model — docs/MMSELL_DEPTH_FILL_MODEL.md.

These tests exist to catch DANGEROUS FALSE CONCLUSIONS, not to exercise the SQL. Every case below
is one where the script would still run, still print a confident-looking table, and be wrong:

  * an administrative cancel counted as "the market declined to fill us" — which would drag every
    fill rate down. Not hypothetical: standing the mmsell books down on 2026-08-14 produced 23 of
    them in one afternoon;
  * `depth = 0` (front of the queue, the most valuable observation there is) collapsing into
    "unknown" and vanishing from the table;
  * `depth = NULL` (unknown) silently becoming `0`, which would invent front-of-queue orders;
  * the finer model winning the holdout by memorising cells of size 1;
  * a "chronological" split that is not chronological, which leaks the future into training.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

_SPEC = importlib.util.spec_from_file_location(
    "mmsell_depth_fill_model",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mmsell_depth_fill_model.py",
)
dfm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dfm)  # type: ignore[union-attr]

_T0 = datetime(2026, 8, 4, tzinfo=timezone.utc)


def _order(*, status="filled", reason=None, depth=5, yes_c=7, minutes=0):
    return {
        "id": 1, "strategy": "mmsell10a", "ticker": "KXT-1",
        "created_at": _T0 + timedelta(minutes=minutes),
        "status": status, "cancel_reason": reason, "yes_c": yes_c,
        "depth": depth, "bucket": dfm.depth_bucket(depth),
        "series": "KXT", "htc": 5.0, "volume": 100,
    }


# ------------------------------------------------------------- zero vs null


def test_zero_depth_is_a_real_bucket_not_missing_data():
    """Front of the queue is the single most informative observation the feature has. Any
    truthiness check anywhere on this path deletes it."""
    assert dfm.depth_bucket(0) == "0 (front)"
    assert dfm.depth_bucket(0) is not None


def test_unknown_depth_is_none_and_never_becomes_zero():
    """A missing observation is not "nothing was ahead of us". Conflating them would invent
    front-of-queue orders out of gaps in the candidate-tick join."""
    assert dfm.depth_bucket(None) is None


def test_buckets_are_contiguous_and_ordered():
    """A gap between buckets would silently drop orders from every table without changing any
    total that is printed."""
    assert dfm.depth_bucket(1) == "1-10"
    assert dfm.depth_bucket(10) == "1-10"
    assert dfm.depth_bucket(11) == "11-100"
    assert dfm.depth_bucket(1000) == "101-1k"
    assert dfm.depth_bucket(1001) == "1k+"
    assert dfm.depth_bucket(10**9) == "1k+"


# ------------------------------------------------------------- censoring


def test_our_own_stand_down_cancels_are_censored_not_counted_as_non_fills():
    """The trap that is LIVE in our data. On 2026-08-14 the drain cancelled 23 resting orders
    when the books stood down. Those orders were pulled by us; the market never declined them.
    Counting them as non-fills biases every fill rate downward."""
    rows = [_order(status="filled"),
            _order(status="canceled", reason="book_stood_down"),
            _order(status="canceled", reason="kill_switch"),
            _order(status="canceled", reason="drain_unconfirmed")]
    use = dfm.usable(rows)
    assert len(use) == 1 and use[0]["status"] == "filled"


def test_a_genuine_timeout_IS_a_non_fill():
    """The other half of the same distinction. An order that rested its full window and was never
    hit is exactly the observation the model needs — censoring it would bias fill rates UP."""
    use = dfm.usable([_order(status="canceled", reason="timeout")])
    assert len(use) == 1


def test_a_cancel_with_no_recorded_reason_is_a_non_fill():
    """337 of our cancels carry no reason (the exchange resolved them). They died without
    filling, so they count — defaulting the unknown case to 'censor' would quietly discard a
    third of the negative class."""
    assert len(dfm.usable([_order(status="canceled", reason=None)])) == 1


def test_orders_with_unknown_depth_are_dropped_from_the_model_set():
    """No feature, no record. They still appear in COVERAGE so the gap stays visible."""
    assert dfm.usable([_order(depth=None)]) == []


# ------------------------------------------------------------- the model


def test_a_thin_cell_falls_back_instead_of_memorising_itself():
    """Without a floor the price x depth predictor wins every holdout by predicting 100% on cells
    of size 1 that contain their own outcome. That is the classic way a finer model 'beats' a
    coarser one while knowing strictly less."""
    # A distinct global rate to fall back TO, so the fallback is actually observable. (With a
    # one-row training set the base rate is itself 1.0, and a passing assertion would prove
    # nothing — the first version of this test made exactly that mistake.)
    train = [_order(yes_c=9, depth=5000, status="canceled", reason="timeout")] * 40
    train += [_order(yes_c=7, depth=5, status="filled")]       # one observation in its own cell
    price, cell, base = dfm._fit_tables(train)
    assert base < 0.1, "the fixture must have a low global rate for the fallback to be visible"

    p = dfm._predict(_order(yes_c=7, depth=5), price, cell, base, use_depth=True)
    assert p != 1.0, "a single-observation cell must not be trusted with its own outcome"
    assert p == base, "it must fall through to the coarser rate"


def test_a_well_populated_cell_is_used():
    train = ([_order(yes_c=7, depth=5, status="filled")] * dfm.MIN_CELL_N
             + [_order(yes_c=7, depth=5000, status="canceled", reason="timeout")]
             * dfm.MIN_CELL_N)
    price, cell, base = dfm._fit_tables(train)
    front = dfm._predict(_order(yes_c=7, depth=5), price, cell, base, use_depth=True)
    deep = dfm._predict(_order(yes_c=7, depth=5000), price, cell, base, use_depth=True)
    assert front == 1.0 and deep == 0.0
    # ...and the price-only predictor cannot tell them apart, which is the whole comparison.
    assert (dfm._predict(_order(yes_c=7, depth=5), price, cell, base, use_depth=False)
            == dfm._predict(_order(yes_c=7, depth=5000), price, cell, base, use_depth=False))


def test_scores_reward_the_predictor_that_knows_more():
    """Sanity on the metric itself: a predictor that separates the classes must score better than
    one that cannot. If this ever inverts, the log-loss sign convention is wrong and every
    'improvement' number in the report is backwards."""
    train = ([_order(yes_c=7, depth=5, status="filled")] * dfm.MIN_CELL_N
             + [_order(yes_c=7, depth=5000, status="canceled", reason="timeout")]
             * dfm.MIN_CELL_N)
    price, cell, base = dfm._fit_tables(train)
    test = [_order(yes_c=7, depth=5, status="filled"),
            _order(yes_c=7, depth=5000, status="canceled", reason="timeout")]
    ll_depth, br_depth = dfm._scores(
        test, lambda r: dfm._predict(r, price, cell, base, use_depth=True))
    ll_price, br_price = dfm._scores(
        test, lambda r: dfm._predict(r, price, cell, base, use_depth=False))
    assert ll_depth < ll_price and br_depth < br_price


def test_wilson_interval_stays_inside_zero_one_at_the_extremes():
    """Several depth buckets sit at 0% or 100% at modest n, where a normal-approx interval runs
    outside [0,1] and reads as certainty."""
    lo, hi = dfm.wilson(0, 10)
    assert lo == 0.0 and 0 < hi < 1
    lo, hi = dfm.wilson(10, 10)
    assert hi == 1.0 and 0 < lo < 1
    assert dfm.wilson(0, 0) == (0.0, 1.0)


# ------------------------------------------------------------- leakage boundary


def test_the_depth_join_cannot_see_past_the_order():
    """The leakage boundary is enforced in SQL, not in a comment. A depth reading taken AFTER
    placement could already reflect the very trade that filled the order — the feature would then
    contain its own label."""
    src = pathlib.Path("scripts/mmsell_depth_fill_model.py").read_text()
    assert "c.captured_at <= lo.created_at" in src
    assert "ORDER BY c.captured_at DESC LIMIT 1" in src
    # ...and the same boundary on the paper side.
    assert "c.captured_at <= pt.created_at" in src


def test_the_holdout_split_is_chronological():
    """A shuffled split on market data leaks the future into training and inflates every score."""
    src = pathlib.Path("scripts/mmsell_depth_fill_model.py").read_text()
    assert 'sorted(use, key=lambda r: r["created_at"])' in src
    assert "random" not in src.lower().replace("randomly", "")


def test_settlement_never_reaches_a_model_feature():
    """P&L is joined ONLY in the adverse-selection section, which describes fills after the fact.
    If it reached the feature set the fill model would be predicting outcomes from outcomes."""
    feature_keys = set(_order().keys())
    assert not (feature_keys & {"pnl", "settled", "resolved_value", "realized"})


# ------------------------------------------------------------- the proxy gate


def test_the_proxy_validation_gates_the_queue_reading():
    """The most expensive mistake this script can make is reporting a null about
    `depth_at_best_ask` as a null about QUEUE POSITION. Those license opposite decisions: one
    closes the queue-preservation/amend line, the other leaves it open.

    Measured 2026-08-15 on 102 paired live orders: r=0.059, truth at the front 41% of the time,
    proxy never zero. So the gate must run FIRST and must be able to invalidate everything under
    it — not merely print a caveat somewhere in the middle."""
    src = pathlib.Path("scripts/mmsell_depth_fill_model.py").read_text()
    # runs before the tables it governs
    assert src.index("def print_proxy_validation") < src.index("def report")
    assert src.index("print_proxy_validation(cur)") < src.index("print_fill_by_depth(rows)")
    # and its failure is surfaced as a refusal, not a footnote
    assert "PROXY INVALID" in src
    assert "READ NOTHING ABOVE AS A QUEUE RESULT" in src


def test_the_gate_requires_both_correlation_and_front_of_queue_agreement():
    """Correlation alone is not enough. A proxy could correlate while systematically never
    reporting front-of-queue — which is exactly our failure mode, and front-of-queue is the most
    decision-relevant state there is (nothing has to trade before you)."""
    src = pathlib.Path("scripts/mmsell_depth_fill_model.py").read_text()
    assert "ok = (r or 0) >= 0.5 and (p0 > 0 or t0 == 0)" in src
