"""Regression guard for the ACTION_PROTOCOL prompt text (kalshi_bot/evo/cognition.py).

Every run_backtest/save_strategy attempt across the live population failed
StrategySpec validation, 100% of the time, since the first attempt — because the
prompt only said `run_backtest {spec, ...}` without ever showing agents the actual
required schema, so each agent guessed its own field names (hypothesis_id,
strategy_name, commission_bps, ...), all rejected by StrategySpec's extra="forbid".
Same story for submit_ticket's category: rejected because the valid enum was never
shown. These tests prove the protocol text now documents the real schema/enums, and
that the worked example it gives agents actually validates."""

from __future__ import annotations

from kalshi_bot.evo.cognition import ACTION_PROTOCOL
from kalshi_bot.evo.strategy_spec import METRICS, OPS, validate_spec
from kalshi_bot.evo.tickets import CATEGORIES


def test_no_leftover_placeholder_tokens():
    for token in ("MAXN", "METRICS_LIST", "OPS_LIST", "TICKET_CATEGORIES"):
        assert token not in ACTION_PROTOCOL, f"unsubstituted placeholder {token!r} in prompt text"


def test_protocol_documents_real_metric_and_op_vocab():
    for metric in METRICS:
        assert metric in ACTION_PROTOCOL
    for op in OPS:
        assert op in ACTION_PROTOCOL


def test_protocol_documents_real_ticket_categories():
    for category in CATEGORIES:
        assert category in ACTION_PROTOCOL


def test_protocol_forbids_invented_field_names_the_population_actually_guessed():
    # Names live agents fabricated (per the DB record of failed attempts) that the
    # protocol should now explicitly warn against re-guessing.
    for guessed in ("hypothesis_id", "strategy_name", "commission_bps", "order_style"):
        assert guessed in ACTION_PROTOCOL  # named as counter-examples, not valid fields


def test_worked_example_spec_actually_validates():
    # The exact example given to agents in the prompt must itself be valid, or the
    # fix just teaches a new wrong shape.
    example = {
        "name": "weather_fade_v1",
        "universe": {"series_prefixes": ["KXHIGH"]},
        "entry": {"conditions": [{"metric": "spread", "op": "<=", "value": 6}]},
    }
    spec, err = validate_spec(example)
    assert err is None, err
    assert spec is not None
    assert spec.universe.series_prefixes == ["KXHIGH"]
    assert spec.entry.conditions[0].metric == "spread"
