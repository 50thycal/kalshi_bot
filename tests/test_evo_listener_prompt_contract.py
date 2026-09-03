"""The agent-facing prompt for create_listener must actually teach the shape
`listeners.validate_condition` accepts.

Root cause found while investigating WS-014's D1 ("are listeners broken or just
never used?"): production shows 175 create_listener attempts across the fleet's
life and zero rows in evo_listeners — a 100% rejection rate, every single time
with "condition object required" or "condition needs a non-empty 'all' or 'any'
list". The dispatch code (cognition.py:_execute_one) and the validator
(listeners.validate_condition) were both correct; the prompt in
cognition.ACTION_PROTOCOL documented only the top-level field list
({name, condition, purpose, effect, ...}) and never told the agent what shape
`condition` itself must be, nor what metric names exist. Agents were guessing
blind and guessing wrong, every time.

This is a documentation-completeness contract, not a code-behavior test — the
two checks below can't catch a future prompt edit that silently drifts from the
validator by prose alone, but they pin the concrete failure this investigation
found: the worked example must actually validate, and the vocabulary named in
the prompt must not silently diverge from what the validator will accept.
"""

from __future__ import annotations

from kalshi_bot.evo.cognition import ACTION_PROTOCOL
from kalshi_bot.evo.listeners import MARKET_METRICS, MAX_CLAUSES, SCALAR_METRICS, validate_condition


def test_prompt_documents_every_recognized_metric():
    for metric in MARKET_METRICS:
        assert metric in ACTION_PROTOCOL, f"{metric!r} is a valid listener metric but undocumented"
    for metric in SCALAR_METRICS:
        if metric in MARKET_METRICS:
            continue
        assert metric in ACTION_PROTOCOL, f"{metric!r} is a valid listener metric but undocumented"
    for special in ("delta:", "status_is:", "result_is:", "new_market:"):
        assert special in ACTION_PROTOCOL, f"{special!r} clause form is undocumented"
    assert str(MAX_CLAUSES) in ACTION_PROTOCOL, "clause cap must be stated so agents don't guess"


def test_prompt_documents_the_all_any_wrapper():
    assert '"all"' in ACTION_PROTOCOL, "the required condition wrapper is undocumented"


def test_prompt_worked_example_actually_validates():
    # The exact clause shape shown in ACTION_PROTOCOL's create_listener entry —
    # kept in sync by hand since it's prose, not a literal the prompt exec's.
    example = {"all": [{"metric": "yes_ask", "op": "<=", "value": 30,
                        "ticker": "KXHIGHCHI-26SEP03-T85"}]}
    assert validate_condition(example) is None


def test_status_is_clause_needs_no_op_or_value():
    # The three string-suffix metrics (status_is / result_is / new_market) carry
    # their comparison in the metric string itself — validate_condition must not
    # require op/value for them, matching what the prompt now tells agents.
    assert validate_condition({"all": [{"metric": "status_is:active", "ticker": "T1"}]}) is None
    assert validate_condition({"all": [{"metric": "new_market:KXHIGHCHI"}]}) is None
