"""A dropped completion acknowledgement cannot repeat accepted desk research."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from kalshi_bot.desks.contracts import DeskError
from kalshi_bot.desks.research import parse_output
from kalshi_bot.desks.research_models import ResearchJob
from kalshi_bot.desks.store import DeskStore
from kalshi_bot.desks.supervisor import Supervisor

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
OUTPUT = {"summary": "No supported edge found.", "next_action": "Check the next source release."}


@pytest.fixture
def claimed(tmp_path):
    store = DeskStore(f"sqlite:///{tmp_path / 'desks.db'}")
    store.initialize("runner-round", NOW)
    sup = Supervisor(store, market_reader=lambda now: {"markets": [], "sources": []})
    job = sup.claim_external("chatgpt", "worker", NOW)
    return sup, job


def finish(sup, job, **overrides):
    params = dict(job_id=job["job_id"], claim_token=job["claim_token"], payload=OUTPUT,
                  model_id="configured-model", now=NOW, desk_id="chatgpt")
    params.update(overrides)
    return sup.complete_external(**params)


def test_completed_result_can_be_acknowledged_after_lease_without_republishing(claimed, monkeypatch):
    sup, job = claimed
    assert datetime.fromisoformat(job["lease_until"]) == NOW + timedelta(minutes=30)
    original = finish(sup, job)
    snapshot = sup.store.snapshot(NOW)

    def forbidden(*args, **kwargs):
        pytest.fail("Completion acknowledgement must not publish or execute again")

    monkeypatch.setattr(sup, "_publish", forbidden)
    monkeypatch.setattr(sup, "_verify", forbidden)
    normalized = parse_output(__import__("json").dumps(OUTPUT)).model_dump(mode="json")
    assert finish(sup, job, now=NOW + timedelta(days=1), payload=normalized) == original
    assert sup.store.snapshot(NOW) == snapshot


@pytest.mark.parametrize("overrides,reason", [
    ({"desk_id": "claude"}, "research_claim_mismatch"),
    ({"claim_token": "other-claim"}, "research_claim_mismatch"),
    ({"model_id": "other-model"}, "research_completion_mismatch"),
    ({"payload": {**OUTPUT, "summary": "Changed after observing the result."}}, "research_completion_mismatch"),
])
def test_completed_acknowledgement_requires_original_owner_token_model_and_result(claimed, overrides, reason):
    sup, job = claimed
    finish(sup, job)
    with pytest.raises(DeskError, match=reason):
        finish(sup, job, **overrides)
    assert len(sup.store.snapshot(NOW)["publications"]) == 1


def test_publishing_result_is_pending_until_recovery_completes(claimed, monkeypatch):
    sup, job = claimed
    original = sup._finish
    monkeypatch.setattr(sup, "_finish", lambda *args: None)
    finish(sup, job)
    assert finish(sup, job)["state"] == "publishing"
    assert len(sup.store.snapshot(NOW)["publications"]) == 1
    monkeypatch.setattr(sup, "_finish", original)
    sup.tick(NOW + timedelta(minutes=31))
    assert finish(sup, job, now=NOW + timedelta(minutes=32))["state"] == "completed"
    assert len(sup.store.snapshot(NOW)["publications"]) == 1


def test_unaccepted_expired_claim_cannot_be_completed(claimed):
    sup, job = claimed
    with pytest.raises(DeskError, match="research_claim_expired"):
        finish(sup, job, now=NOW + timedelta(minutes=31))
    with sup.store._tx() as session:
        assert session.scalar(select(ResearchJob)).result is None


@pytest.mark.parametrize("model", ["", None, "x" * 201])
def test_model_identity_required_before_publication(claimed, model):
    sup, job = claimed
    with pytest.raises(DeskError, match="research_model_id_required"):
        finish(sup, job, model_id=model)
    assert not sup.store.snapshot(NOW)["publications"]
