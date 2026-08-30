"""PERP-V1 Probe 0's classification contract.

The whole probe exists to keep three answers apart — the path is not there, the
path is there and needs credentials, the path is there and needs different
arguments — because they are three different decisions. The 2026-08-29 run
proved the distinction is not academic: `/margin/markets/{t}/candlesticks`
answered `400 "Query argument start_ts is required, but not found"` and the
first version of `_classify` reported it ABSENT, which would have written the
candle feed off as missing when it was answering.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import perp_surface_survey as survey  # noqa: E402


def test_a_400_is_the_endpoint_talking_not_a_missing_path():
    """A 400 means the route parsed, reached its own argument validation, and
    told us what it wants. Folding it in with 404 loses the finding."""
    assert survey._classify(400) == "EXISTS/ARGS"
    assert survey._classify(404) == "ABSENT"
    assert survey._classify(400) != survey._classify(404)


def test_auth_failure_is_a_successful_discovery():
    """The ops runner holds no Kalshi credentials by design, so 401/403 is the
    strongest signal available that a private path is real."""
    assert survey._classify(401) == "EXISTS/AUTH"
    assert survey._classify(403) == "EXISTS/AUTH"


def test_no_answer_is_not_absent():
    """DNS, TLS and timeout failures say nothing about whether a path exists.
    Reporting them as ABSENT would manufacture a discovery from a network hiccup."""
    assert survey._classify(0) == "NO-ANSWER"


def test_candlestick_probes_supply_the_arguments_the_endpoint_asked_for():
    """The 400 named `start_ts`. A follow-up run that does not supply it learns
    nothing new — but one unparameterised probe is kept deliberately so the
    400-vs-404 distinction stays visible in every run."""
    paths = [tmpl for tmpl, _ in survey.PER_MARKET_PATHS if "candlesticks" in tmpl]
    assert any("start_ts=" in p and "end_ts=" in p for p in paths)
    assert any(p.endswith("/candlesticks") for p in paths)


def test_the_confirmed_funding_endpoint_is_probed_with_its_date_range():
    """The 2026-08-30 run answered 400 "Query argument start_date is required"
    on `/margin/funding_history` — the endpoint is real, and the classifier fix
    is the only reason we know it. Supplying the range is what turns "the route
    parses" into "here is the response shape", which is what arm B's ranking and
    arm A's entry confirmation actually need. The bare path stays as the
    regression canary for the 400-vs-404 distinction."""
    paths = [p for p, _ in survey.CANDIDATE_PATHS if "funding_history" in p]
    assert any("start_date=" in p and "end_date=" in p for p in paths)
    assert "/margin/funding_history" in paths


def test_funding_is_hunted_rather_than_assumed():
    """Funding is arm B's entire ranking, arm A's entry confirmation and a cost
    in every arm's headline metric. The two names the brief used both 404'd, so
    the probe carries alternatives instead of concluding from two guesses."""
    funding = [p for p, _ in survey.CANDIDATE_PATHS if "funding" in p]
    per_market = [p for p, _ in survey.PER_MARKET_PATHS if "funding" in p]
    assert len(funding) >= 4
    assert per_market


def test_the_script_is_allowlisted_on_the_ops_channel():
    """It is unrunnable otherwise, and a probe nobody can run is not a probe."""
    from ops_runner import ALLOWED_SCRIPTS

    assert "perp_surface_survey" in ALLOWED_SCRIPTS
