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


def test_fingerprint_ignores_runtime_only_elapsed_time(probe):
    fingerprint = probe["_fingerprint"]
    a = {"n_trades": 30, "truncated": False, "elapsed_ms": 100}
    b = {"n_trades": 30, "truncated": False, "elapsed_ms": 999}
    assert fingerprint(a) == fingerprint(b)


def test_fingerprint_changes_when_evidence_changes(probe):
    fingerprint = probe["_fingerprint"]
    a = {"n_trades": 30, "truncated": False, "elapsed_ms": 100}
    b = {"n_trades": 31, "truncated": False, "elapsed_ms": 100}
    assert fingerprint(a) != fingerprint(b)


def _result(**over):
    base = {
        "markets_considered": 12,
        "rows_processed": 900,
        "truncated": False,
        "n_trades": 30,
        "total_pnl_usd": 4.5,
        "max_drawdown_usd": 2.0,
        "elapsed_ms": 100,
    }
    base.update(over)
    return base


def test_corpus_fingerprint_ignores_the_order_dependent_aggregate(probe):
    """Two replays over the same trade set that differ only in market ORDER agree on the
    corpus fingerprint and disagree on the strict one."""
    a = _result()
    b = _result(max_drawdown_usd=3.25, elapsed_ms=880)
    assert probe["_corpus_fingerprint"](a) == probe["_corpus_fingerprint"](b)
    assert probe["_fingerprint"](a) != probe["_fingerprint"](b)


def test_differing_fields_names_exactly_what_changed(probe):
    a = _result()
    b = _result(max_drawdown_usd=3.25, elapsed_ms=880)
    assert probe["_differing_fields"]([a, b]) == ["max_drawdown_usd"]
    assert probe["_differing_fields"]([a, _result(elapsed_ms=7)]) == []
    assert probe["_differing_fields"]([a]) == []


def test_assess_requires_every_requested_repetition_to_have_run(probe):
    """A spec that errored on one repetition is not reproducible — it is unproven."""
    a = probe["_assess"]([_result()], repeat=2)
    assert a["ran"] is False
    assert a["non_empty"] is False
    assert a["reproducible"] is False
    assert a["untruncated"] is False


def test_assess_passes_a_clean_repeated_run(probe):
    a = probe["_assess"]([_result(), _result(elapsed_ms=999)], repeat=2)
    assert a["ran"] and a["non_empty"] and a["reproducible"]
    assert a["corpus_reproducible"] and a["untruncated"]
    assert a["empty_window"] is False
    assert probe["_diagnose"](a) is None


def test_ordering_only_divergence_is_diagnosed_as_such(probe):
    a = probe["_assess"]([_result(), _result(max_drawdown_usd=3.25)], repeat=2)
    assert a["reproducible"] is False
    assert a["corpus_reproducible"] is True
    why = probe["_diagnose"](a)
    assert "ordering-only divergence" in why
    assert "D7" in why


def test_a_genuinely_different_corpus_is_not_excused_as_ordering(probe):
    a = probe["_assess"]([_result(), _result(n_trades=31)], repeat=2)
    assert a["corpus_reproducible"] is False
    why = probe["_diagnose"](a)
    assert "corpus itself did not reproduce" in why
    assert "n_trades" in why


def test_truncation_is_diagnosed_before_reproducibility(probe):
    a = probe["_assess"]([_result(truncated=True), _result(truncated=True)], repeat=2)
    assert a["reproducible"] is True  # identical, and still not a proving artifact
    assert a["untruncated"] is False
    assert "truncated" in probe["_diagnose"](a)


def test_an_empty_window_is_diagnosed_as_coverage_not_strategy(probe):
    empty = _result(markets_considered=0, rows_processed=0, n_trades=0)
    a = probe["_assess"]([empty, empty], repeat=2)
    assert a["empty_window"] is True
    why = probe["_diagnose"](a)
    assert "empty window" in why
    assert "not evidence about the strategy" in why
