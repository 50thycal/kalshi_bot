"""`_details` decides what an operator actually sees under a log line.

The incentive canary went an hour without quoting on 2026-09-18 while printing the same
`incentive smoke cycle` line as a healthy cycle: the runner computes a per-refusal-code
breakdown precisely so a cycle that placed nothing says what stopped it, and the log reader
threw all of it away. These tests pin the two reasons that happened."""

from __future__ import annotations

import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "railway_logs", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "railway_logs.py")
railway_logs = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(railway_logs)


def _entry(**fields):
    return {"attributes": [{"key": k, "value": v} for k, v in fields.items()]}


def test_starved_cycle_reports_its_refusal_codes():
    details = dict(railway_logs._details(_entry(
        considered=3921, fetched=8, placed=0, outcomes="{'program_ending': 8}")))
    assert details["outcomes"] == "{'program_ending': 8}"
    assert details["considered"] == 3921
    assert details["fetched"] == 8


def test_placed_zero_is_printed():
    """The whole point: a zero is the value worth reading, not the one to hide."""
    assert ("placed", 0) in railway_logs._details(_entry(placed=0))


def test_empty_string_is_still_dropped():
    assert railway_logs._details(_entry(outcomes="")) == []


def test_tracebacks_still_print_first():
    details = railway_logs._details(_entry(placed=0, exc="Traceback..."))
    assert details[0][0] == "exc"


def test_unlisted_fields_are_not_printed():
    assert railway_logs._details(_entry(kalshi_private_key="x")) == []


def test_missing_or_malformed_attributes_are_tolerated():
    assert railway_logs._details({}) == []
    assert railway_logs._details({"attributes": "nope"}) == []
