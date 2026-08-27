"""Unit checks for the read-only real-dataset proving harness."""

from __future__ import annotations

import runpy

import pytest


@pytest.fixture(scope="module")
def probe():
    return runpy.run_path(
        "scripts/evo_backtest_probe.py",
        run_name="evo_backtest_probe_test",
    )


def test_proving_arguments_require_a_closed_window(probe):
    parse = probe["_parse_args"]
    with pytest.raises(SystemExit):
        parse(["--date-from", "2026-08-01"])
    with pytest.raises(SystemExit):
        parse(["--require-complete"])
    with pytest.raises(SystemExit):
        parse(
            [
                "--date-from",
                "2026-08-03",
                "--date-to",
                "2026-08-01",
            ]
        )


def test_pre_registered_weather_arguments_are_accepted(probe):
    args = probe["_parse_args"](
        [
            "--dataset",
            "backfill_weather",
            "--date-from",
            "2026-08-01",
            "--date-to",
            "2026-08-03",
            "--repeat",
            "2",
            "--require-complete",
        ]
    )
    assert args.dataset == "backfill_weather"
    assert args.repeat == 2
    assert args.require_complete is True


def _trade(ticker="KXHIGHNY-1", pnl=0.5, **over):
    t = {
        "ticker": ticker, "side": "no", "style": "maker", "quantity": 5,
        "entry_price_cents": 40.0, "exit_price_cents": 100.0,
        "entered_at": "2026-08-01T12:00:00Z", "exited_at": "2026-08-01T20:00:00Z",
        "exit": "settlement", "settled": True, "win": True, "pnl": pnl, "fees": 0.05,
        "month": "2026-08", "cents_per_contract": 10.0, "maker_yes_c": 60,
    }
    t.update(over)
    return t


def _result(trades=None, **over):
    tape = [_trade(), _trade("KXHIGHLA-2", pnl=-0.25, win=False)] if trades is None else trades
    base = {
        "markets_considered": 12,
        "rows_processed": 900,
        "truncated": False,
        "n_trades": len(tape),
        "total_pnl_usd": 4.5,
        "max_drawdown_usd": 2.0,
        "elapsed_ms": 100,
        "trades": tape,
    }
    base.update(over)
    return base


M = ["mfp", "mfp"]  # a matching two-repetition market manifest


def test_fingerprint_ignores_runtime_only_elapsed_time(probe):
    f = probe["_fingerprint"]
    assert f(_result()) == f(_result(elapsed_ms=999))


def test_fingerprint_changes_when_evidence_changes(probe):
    f = probe["_fingerprint"]
    assert f(_result()) != f(_result(total_pnl_usd=9.9))


def test_aggregate_fingerprint_excludes_the_trade_tape(probe):
    """The tape is proven separately; folding it into the aggregate hash would conflate
    'the same summary' with 'the same trades'."""
    f = probe["_fingerprint"]
    other = [_trade("KXOTHER-9"), _trade("KXOTHER-8", pnl=-0.25, win=False)]
    assert f(_result()) == f(_result(trades=other))


def test_trade_fingerprint_is_order_independent_but_identity_sensitive(probe):
    tf = probe["_trade_fingerprint"]
    a = _result()
    reversed_tape = _result(trades=list(reversed(a["trades"])))
    assert tf(a) == tf(reversed_tape)
    # Same count, same total P&L, DIFFERENT markets — the case aggregates cannot catch.
    swapped = _result(trades=[_trade("KXELSE-1"), _trade("KXELSE-2", pnl=-0.25, win=False)])
    assert tf(a) != tf(swapped)


def test_missing_trade_tape_is_never_read_as_agreement(probe):
    a = _result()
    del a["trades"]
    assert probe["_trade_fingerprint"](a) is None
    assert probe["_assess"]([a, a], repeat=2, manifests=M)["trades_reproducible"] is False


def test_assess_requires_every_requested_repetition_to_have_run(probe):
    a = probe["_assess"]([_result()], repeat=2, manifests=M)
    assert a["ran"] is False
    assert a["non_empty"] is False
    assert a["reproducible"] is False
    assert a["untruncated"] is False


def test_assess_passes_a_clean_repeated_run(probe):
    a = probe["_assess"]([_result(), _result(elapsed_ms=999)], repeat=2, manifests=M)
    assert a["ran"] and a["non_empty"] and a["reproducible"]
    assert a["trades_reproducible"] and a["manifest_reproducible"] and a["manifest_covered"]
    assert a["untruncated"] and a["empty_window"] is False
    assert probe["_diagnose"](a) is None


def test_a_differing_market_manifest_fails_before_anything_downstream(probe):
    a = probe["_assess"]([_result(), _result()], repeat=2, manifests=["mfp", "OTHER"])
    assert a["manifest_reproducible"] is False
    assert "market manifest itself differed" in probe["_diagnose"](a)


def test_an_uncovered_manifest_is_reported_as_consistent_with_not_proven(probe):
    a = probe["_assess"]([_result(), _result()], repeat=2, manifests=[None, None])
    assert a["manifest_covered"] is False
    assert a["manifest_reproducible"] is False
    why = probe["_diagnose"](a)
    assert "UNCOVERED" in why and "consistent-with, not proven" in why
    assert "manifest itself differed" not in why


def test_a_differing_trade_tape_is_a_replay_defect_not_an_ordering_artifact(probe):
    b = _result(trades=[_trade("KXELSE-1"), _trade("KXELSE-2", pnl=-0.25, win=False)])
    a = probe["_assess"]([_result(), b], repeat=2, manifests=M)
    assert a["trades_reproducible"] is False
    assert "genuine replay defect" in probe["_diagnose"](a)


def test_ordering_only_divergence_is_only_claimed_once_identity_is_proven(probe):
    """Same manifest, same canonicalized tape, differing only in the order-dependent
    aggregate: now a demonstrated claim rather than an inference — and, post-D7, a defect."""
    a = probe["_assess"](
        [_result(), _result(trades=list(reversed(_result()["trades"])), max_drawdown_usd=3.25)],
        repeat=2,
        manifests=M,
    )
    assert a["manifest_reproducible"] and a["trades_reproducible"]
    assert a["reproducible"] is False
    why = probe["_diagnose"](a)
    assert "ordering-only divergence" in why and "D7" in why


def test_truncation_is_diagnosed_before_reproducibility(probe):
    a = probe["_assess"](
        [_result(truncated=True), _result(truncated=True)], repeat=2, manifests=M
    )
    assert a["reproducible"] is True  # identical, and still not a proving artifact
    assert a["untruncated"] is False
    assert "truncated" in probe["_diagnose"](a)


def test_an_empty_window_is_diagnosed_as_coverage_not_strategy(probe):
    empty = _result(markets_considered=0, rows_processed=0, n_trades=0, trades=[])
    a = probe["_assess"]([empty, empty], repeat=2, manifests=M)
    assert a["empty_window"] is True
    why = probe["_diagnose"](a)
    assert "empty window" in why and "not evidence about the strategy" in why
