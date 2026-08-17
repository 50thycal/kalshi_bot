"""`realizable_cents_per_trade` as a canonical metric provider.

This is the metric the whole mmsell cohort's promotion decisions turn on. Paper's
headline averages in winners a resting maker order can never capture; the fill
model corrects for that, and `docs/MMSELL_FILL_MODEL.md` records the mistake it
prevents — mmsell6 and mmsell11 looked promotable on blended paper and are
mirages under the correction.

What must never happen, pinned here:

  * a book whose price mix the live calibration never reached getting a confident
    number instead of MISSING (that is the mirage, restored);
  * the metrics engine and the evo sandbox disagreeing about the same book;
  * an uncovered cell being filled in from a neighbour;
  * coverage being invisible, so a 20%-covered estimate reads like a 100% one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot import fill_calibration as fc
from kalshi_bot.experiment_os.metrics import MetricScope, compute_metric
from kalshi_bot.models import PaperTrade

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _scope(tags, arm="treatment"):
    return MetricScope(
        experiment_key="exp-fm", version=1, epoch_number=1, arm_key=arm,
        deployment_kind="paper", strategy_tags=tuple(tags), deployment_keys=(),
        window_start=T0, window_end=T0 + timedelta(days=30),
        platform_snapshot_fingerprint="f" * 64,
    )


def _trade(s, tag, *, side="no", price=92, pnl=0.05, status="settled"):
    """A resting NO bid at 92c is the 8c YES cell — the calibration's own keying."""
    s.add(PaperTrade(
        market_ticker=f"T-{tag}-{id(object())}", strategy=tag, status=status,
        side=side, assumed_price=price, pnl=pnl, quantity=1,
        created_at=T0 + timedelta(hours=1),
    ))


# ---------------------------------------------------------------------------
# The correction itself
# ---------------------------------------------------------------------------


def test_realizable_uses_the_calibrated_cell_not_the_paper_average(xos_session):
    """A book priced entirely in the 8c cell realizes that cell's measured number,
    whatever paper says. 8c: fill 68.8%, realizable -0.81c — paper's +5c is the
    fill-everything fantasy."""
    s = xos_session
    for _ in range(40):
        _trade(s, "fm_t", side="no", price=92, pnl=0.05)  # yes-equivalent 8c
    s.commit()

    mv = compute_metric(s, "realizable_cents_per_trade", _scope(("fm_t",)))
    assert mv.missing is False
    assert mv.value == fc.MAKER_FILL_CALIBRATION[8].realizable_cents
    assert mv.value < 0 < mv.provenance["optimistic_cents_per_trade"]  # the mirage
    assert mv.n == 40
    assert mv.provenance["fill_calibration_version"] == fc.CALIBRATION_VERSION
    assert mv.provenance["platform_component"] == "FILL_MODEL"

    cov = compute_metric(s, "fill_model_coverage_pct", _scope(("fm_t",)))
    assert cov.value == 100.0


def test_a_yes_side_entry_lands_in_the_same_cell_as_its_no_mirror(xos_session):
    """Fillability is a property of the market's price cell, not of which leg you
    took: a resting NO bid at 92c and a resting YES bid at 8c are one book event."""
    s = xos_session
    for _ in range(10):
        _trade(s, "fm_no", side="no", price=92, pnl=0.05)
        _trade(s, "fm_yes", side="yes", price=8, pnl=0.05)
    s.commit()
    a = compute_metric(s, "realizable_cents_per_trade", _scope(("fm_no",)))
    b = compute_metric(s, "realizable_cents_per_trade", _scope(("fm_yes",)))
    assert a.value == b.value


def test_a_mixed_price_book_is_trade_weighted_across_cells(xos_session):
    s = xos_session
    for _ in range(30):
        _trade(s, "fm_mix", side="yes", price=7)   # 7c: +0.92
    for _ in range(10):
        _trade(s, "fm_mix", side="yes", price=9)   # 9c: -5.79
    s.commit()
    mv = compute_metric(s, "realizable_cents_per_trade", _scope(("fm_mix",)))
    expected = (30 * fc.MAKER_FILL_CALIBRATION[7].realizable_cents
                + 10 * fc.MAKER_FILL_CALIBRATION[9].realizable_cents) / 40
    assert mv.value == round(round(expected, 3), 4)
    assert mv.n == 40


# ---------------------------------------------------------------------------
# Coverage: uncovered is MISSING, never guessed and never zero
# ---------------------------------------------------------------------------


def test_a_book_outside_every_trusted_cell_is_missing_not_zero(xos_session):
    """The whole point. 40c YES is nowhere near the calibrated 6-12c band, so the
    model cannot say what a maker would realize — and says so, rather than
    answering 0 or handing back paper's number."""
    s = xos_session
    for _ in range(50):
        _trade(s, "fm_rich", side="yes", price=40, pnl=0.20)
    s.commit()
    mv = compute_metric(s, "realizable_cents_per_trade", _scope(("fm_rich",)))
    assert mv.missing is True
    assert mv.value is None
    assert "unmeasured here, not zero" in mv.reason
    assert str(fc.CALIBRATION_VERSION) in mv.reason
    assert compute_metric(s, "fill_model_coverage_pct", _scope(("fm_rich",))).value == 0.0


def test_an_untrusted_cell_is_excluded_rather_than_borrowed(xos_session):
    """13c has 4 live fills, below the 8-fill threshold. It must not borrow 12c's
    rate — an estimate from a neighbouring cell is a guess wearing a number."""
    assert fc.MAKER_FILL_CALIBRATION[13].n_fills < fc.MIN_CELL_FILLS
    s = xos_session
    for _ in range(20):
        _trade(s, "fm_thin", side="yes", price=13, pnl=0.10)
    for _ in range(20):
        _trade(s, "fm_thin", side="yes", price=7, pnl=0.10)
    s.commit()
    mv = compute_metric(s, "realizable_cents_per_trade", _scope(("fm_thin",)))
    # Only the 20 covered trades back the estimate, and coverage says so.
    assert mv.value == fc.MAKER_FILL_CALIBRATION[7].realizable_cents
    assert mv.n == 20
    assert compute_metric(s, "fill_model_coverage_pct", _scope(("fm_thin",))).value == 50.0


def test_a_trade_with_no_recorded_entry_price_reduces_coverage(xos_session):
    """It cannot be placed in a cell, so it is uncovered — not dropped from the
    denominator, which would inflate coverage and hide the gap."""
    s = xos_session
    for _ in range(10):
        _trade(s, "fm_np", side="yes", price=7)
    for _ in range(10):
        _trade(s, "fm_np", side="yes", price=None)
    s.commit()
    assert compute_metric(s, "fill_model_coverage_pct", _scope(("fm_np",))).value == 50.0
    assert compute_metric(
        s, "realizable_cents_per_trade", _scope(("fm_np",))
    ).provenance["total_n"] == 20


def test_an_empty_scope_is_undefined_not_missing(xos_session):
    """No settled trades is a known-empty sample, the same as every other mean —
    the sample floor handles it. It is not a data-provider failure."""
    s = xos_session
    mv = compute_metric(s, "realizable_cents_per_trade", _scope(("fm_none",)))
    assert mv.missing is False and mv.value is None
    assert "no settled trades" in mv.reason


def test_only_settled_trades_count_including_stop_closed(xos_session):
    s = xos_session
    for _ in range(10):
        _trade(s, "fm_st", side="yes", price=7, status="settled")
    _trade(s, "fm_st", side="yes", price=7, status="closed_sl")
    _trade(s, "fm_st", side="yes", price=7, status="open", pnl=None)
    s.commit()
    mv = compute_metric(s, "realizable_cents_per_trade", _scope(("fm_st",)))
    assert mv.provenance["total_n"] == 11  # the stop-closed one counts, the open one not


# ---------------------------------------------------------------------------
# One calibration, one answer, everywhere
# ---------------------------------------------------------------------------


def test_the_sandbox_and_the_metrics_engine_cannot_drift(xos_session):
    """The evo sandbox re-exports the shared calibration rather than holding its
    own copy: agents backtesting a book and the gate deciding its promotion must
    read the same numbers or they are two answers to one question."""
    from kalshi_bot.evo import fill_model as evo_fm

    assert evo_fm.MAKER_FILL_CALIBRATION is fc.MAKER_FILL_CALIBRATION
    assert evo_fm.project_realizable is fc.project_realizable
    assert evo_fm.CALIBRATION_VERSION == fc.CALIBRATION_VERSION

    s = xos_session
    for _ in range(25):
        _trade(s, "fm_par", side="no", price=93, pnl=0.03)  # 7c cell
    s.commit()
    mv = compute_metric(s, "realizable_cents_per_trade", _scope(("fm_par",)))
    proj = evo_fm.project_realizable({7: (25, 25 * 3.0)})
    assert mv.value == round(proj["est_realizable_cents"], 4)


def test_parity_with_the_ops_script_projection():
    """`scripts/mmsell_fill_model.py` restates the projection to stay stdlib-only
    for the ops runner. Restated is fine; divergent is not."""
    import sys

    sys.path.insert(0, "scripts")
    import mmsell_fill_model as ops

    hist = {6: (40, 120.0), 9: (25, -30.0), 40: (10, 500.0)}
    calib = {c: (cell.n_fills, cell.fill_rate, cell.realizable_cents)
             for c, cell in fc.MAKER_FILL_CALIBRATION.items()}
    theirs = ops.project_realizable(hist, calib)
    ours = fc.project_realizable(hist)
    assert ours["total_n"] == theirs["total_n"]
    assert ours["covered_n"] == theirs["covered_n"]
    assert ours["est_realizable_cents"] == round(theirs["est_realizable_cents"], 3)
    assert ours["est_fill_rate"] == round(theirs["est_fill_rate"], 4)


def test_the_metric_is_registered_as_provided_with_its_platform_component():
    from kalshi_bot.experiment_os.metrics import REGISTRY

    for key in ("realizable_cents_per_trade", "fill_model_coverage_pct"):
        d = REGISTRY[key]
        assert d.provided is True
        assert "FILL_MODEL" in d.source
