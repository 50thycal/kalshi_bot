"""The proving run, in CI.

`kalshi_bot/evo/search/proving_run.py` answers one question — is the evidence this
capability produces worth an agent acting on? — across ten capability checks and three
adversarial cases. It is written to report rather than raise, so that a defect in one
check does not hide the other twelve; this test is what turns that report into a build
failure.

The run is deterministic: a fixed synthetic corpus, a fixed window, a fixed seed. A
change in its verdict is a change in the capability, never noise.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import kalshi_bot.evo.models  # noqa: F401 — register the organism tables on Base
import kalshi_bot.evo.search.models  # noqa: F401 — and the search tables
from kalshi_bot.evo.search import proving_run
from kalshi_bot.models import Base


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def test_the_proving_run_is_clean():
    result = proving_run.run_proving(_session())
    failures = [f"{key}: {detail}" for key, ok, detail in result["checks"] if not ok]
    assert not failures, "\n" + result["report"]
    # Guard against the report passing because it stopped asking questions.
    assert len(result["checks"]) == 13


def test_the_run_still_asks_the_three_adversarial_questions():
    """The capability checks say the machinery works. These three say the SCORING is
    worth believing, and they are the ones most easily lost to a refactor: a search that
    crowns the reckless variant, promotes a six-trade fluke, or ranks a corrupt replay
    at all is worse than no search."""
    result = proving_run.run_proving(_session())
    adversarial = {key.split()[0]: key for key, _, _ in result["checks"]}
    assert "reckless does not outrank steady" in adversarial["A1"]
    assert "thin sample is held, not crowned" in adversarial["A2"]
    assert "corrupt data is invalid" in adversarial["A3"]
    # The report is meant to be read, not just asserted on.
    assert "VERDICT: CLEAN" in result["report"]
    assert "WHAT AN AGENT WOULD SEE" in result["report"]
