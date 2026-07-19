"""Recovery of truncated model JSON — the confirmed dominant heartbeat failure:
a verbose reflection runs out of output-token budget and the response ends
mid-string with no closing brace. parse_output must salvage the parseable prefix
(journal + any complete actions) instead of losing the whole paid-for call, while
still refusing genuinely non-JSON output."""

from __future__ import annotations

import json

from kalshi_bot.evo.cognition import _recover_truncated_json, parse_output

# Faithful shape of the real captured sample (evo_heartbeats id 785): opens
# {"journal": {...}} , writes verbose array fields, and is cut off mid-string
# inside alternatives_considered with NO closing brace anywhere.
TRUNCATED_REFLECTION = (
    '{"journal": {"information_reviewed": ["cognitive genome", "trading genome", '
    '"portfolio", "budgets", "peer roster", "strategy graveyard"], '
    '"observations": ["trading genome defaults to order_style=taker, sizing '
    'fixed_cost -- inherited default may itself be a latent weakness given the '
    'graveyard pattern."], "alternatives_considered": ["Re-testing a \'revived\''
)


def test_recovers_truncated_reflection_sample():
    recovered = _recover_truncated_json(TRUNCATED_REFLECTION)
    assert recovered is not None
    doc = json.loads(recovered)  # must be valid JSON now
    assert "journal" in doc
    j = doc["journal"]
    # the completed fields survive intact
    assert "trading genome" in j["information_reviewed"]
    assert j["observations"][0].startswith("trading genome defaults")
    # the partially-written field is salvaged as a (shorter) valid string
    assert isinstance(j["alternatives_considered"], list)


def test_parse_output_salvages_journal_from_truncation():
    journal, actions, err = parse_output(TRUNCATED_REFLECTION)
    assert err is None  # recovered, not degraded
    assert journal["decision"] or journal["observations"]  # journal content survived
    assert isinstance(actions, list)


def test_recovers_no_closing_brace():
    assert json.loads(_recover_truncated_json('{"a": 1, "b": [1, 2, 3'))  # closes ] and }


def test_recovers_trailing_comma():
    assert json.loads(_recover_truncated_json('{"a": 1, "b": 2, '))  # drops comma, closes }


def test_recovers_dangling_key():
    doc = json.loads(_recover_truncated_json('{"a": 1, "b":'))  # drops "b":, closes }
    assert doc == {"a": 1}


def test_recovers_mid_number():
    # cut off inside a number literal
    doc = json.loads(_recover_truncated_json('{"a": 1, "b": 234'))
    assert doc["a"] == 1


def test_complete_json_is_unaffected():
    # a valid object round-trips through parse_output without touching recovery
    good = '{"journal": {"decision": "hold"}, "actions": [{"type": "no_action"}]}'
    journal, actions, err = parse_output(good)
    assert err is None
    assert journal["decision"] == "hold"
    assert actions == [{"type": "no_action"}]


def test_non_json_still_reports_error():
    # plain prose with no JSON object must NOT be "recovered" into something bogus
    journal, actions, err = parse_output("I think we should hold this cycle, no action.")
    assert err == "no JSON object in output"
    assert actions == []


def test_recovery_gives_up_gracefully_on_garbage():
    # an opening brace followed by unparseable garbage returns None (safe degrade)
    assert _recover_truncated_json('{@@@@ not json at all ####') is None
