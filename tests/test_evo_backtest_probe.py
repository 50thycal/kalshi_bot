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
