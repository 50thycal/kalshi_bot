"""Session mode has no background cognition and cannot turn old research into new orders."""
from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_desks_integration import SOURCE, make_decision, verified_source
from test_desks_research import NOW, OUTPUT, FakeProvider, runtime, store_at
from test_desks_service import settings

from kalshi_bot.desks.contracts import DeskError
from kalshi_bot.desks.research import ResearchOutput
from kalshi_bot.desks.research_models import ResearchJob


def test_session_mode_is_explicit_and_rejects_paid_provider_configuration():
    assert settings().research_mode == 'scheduled'
    configured = settings(research_mode='session', external_runners_verified=False)
    assert not configured.static_blockers()
    with pytest.raises(ValidationError, match='session mode requires external'):
        settings(research_mode='session', chatgpt_provider='openai')
    with pytest.raises(ValidationError):
        settings(research_mode='typo')
    blocked = settings(research_mode='session', live_enabled=False, existing_workers_isolated=False,
                       chatgpt_subaccount=0, claude_subaccount=0, chatgpt_kalshi_key_id='')
    assert {'live_execution_disabled', 'existing_worker_subaccount_isolation_unverified',
            'two_distinct_non_primary_subaccounts_required', 'chatgpt_exchange_credentials_missing'} <= set(blocked.static_blockers())


def test_idle_session_never_schedules_or_calls_provider(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(ValueError, match='cannot invoke providers'):
        runtime(store, research_mode='session', providers={'chatgpt': FakeProvider()})
    sup = runtime(store, research_mode='session')
    for day in range(5):
        result = sup.tick(NOW + timedelta(days=day))
        assert result['jobs'] == []
        assert result['health']['chatgpt']['activity'] == 'waiting_for_continue'
        assert result['health']['chatgpt']['reasons'] == ['session_cycle_required']


def test_each_continue_can_do_work_in_same_hour_and_idle_is_not_failure(tmp_path):
    sup = runtime(store_at(tmp_path), research_mode='session')
    first = sup.claim_external('chatgpt', 'app-chatgpt', NOW)
    # Overlapping sessions cannot steal this claim, even with the same identity.
    assert sup.claim_external('chatgpt', 'app-chatgpt', NOW) is None
    sup.complete_external(first['job_id'], first['claim_token'], OUTPUT, 'app-model', NOW, desk_id='chatgpt')
    healthy = sup.tick(NOW + timedelta(days=5))['health']['chatgpt']
    assert healthy['status'] == 'healthy' and not healthy['reasons']
    assert healthy['activity'] == 'waiting_for_continue'
    second = sup.claim_external('chatgpt', 'app-chatgpt', NOW + timedelta(seconds=10))
    assert second['job_id'] != first['job_id']
    with pytest.raises(DeskError, match='research_claim_mismatch'):
        sup.complete_external(second['job_id'], first['claim_token'], OUTPUT, 'app-model', NOW, desk_id='chatgpt')


def test_old_scheduled_cycle_is_not_proof_of_session_setup(tmp_path):
    store = store_at(tmp_path)
    scheduled = runtime(store)
    job = scheduled.claim_external('chatgpt', 'hosted', NOW)
    scheduled.complete_external(job['job_id'], job['claim_token'], OUTPUT, 'model', NOW, desk_id='chatgpt')
    session = runtime(store, research_mode='session')
    assert 'session_cycle_required' in session.status(NOW)['health']['chatgpt']['reasons']


def test_expired_unaccepted_claim_never_publishes_or_executes(tmp_path):
    sup = runtime(store_at(tmp_path), research_mode='session')
    job = sup.claim_external('chatgpt', 'app', NOW)
    with pytest.raises(DeskError, match='research_claim_expired'):
        sup.complete_external(job['job_id'], job['claim_token'], OUTPUT, 'model',
                              NOW + timedelta(minutes=31), desk_id='chatgpt')
    assert not sup.store.snapshot(NOW)['publications']
    result = sup.tick(NOW + timedelta(minutes=31))
    assert len(result['jobs']) == 1 and result['jobs'][0]['state'] == 'failed'


def test_recovery_preserves_notes_but_never_extends_trade_authority(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    sup = runtime(store, research_mode='session')
    job = sup.claim_external('chatgpt', 'app', NOW)
    decision = make_decision(verified_source(SOURCE, NOW), NOW).model_copy(update={'round_id': 'research-test'})
    output = ResearchOutput.model_validate({**OUTPUT, 'decisions': [{'decision': decision, 'source_ids': ['fixture']}]})
    def interrupted(*args):
        raise RuntimeError('process stopped before submission')
    sup.submit_decision = interrupted
    with pytest.raises(RuntimeError):
        sup._publish(job['job_id'], output, 'chatgpt', decision.author_model, NOW)
    # First acceptance only authorizes the exact immutable decision during its original lease.
    sup.verify_session_decision(decision, NOW)
    with pytest.raises(DeskError, match='active_session_completion_required'):
        sup.verify_session_decision(decision.model_copy(update={'max_price': Decimal('.41')}), NOW)
    orders = []
    def guarded(value, now):
        sup.verify_session_decision(value, now)
        orders.append(value)
    sup.submit_decision = guarded
    sup.tick(NOW + timedelta(minutes=31))
    assert not orders
    snapshot = store.snapshot(NOW)
    assert any(p['kind'] == 'research_cycle' for p in snapshot['publications'])
    assert any(p['kind'] == 'decision_refused' and p['payload']['reason'] == 'active_session_completion_required'
               for p in snapshot['publications'])
    with store._tx() as session:
        row = session.scalar(select(ResearchJob))
        assert row.state == 'completed'
        assert row.context['session_submit_until'] == job['lease_until']


def test_session_completion_requires_sources_to_be_captured_first(tmp_path):
    sup = runtime(store_at(tmp_path), research_mode='session')
    job = sup.claim_external('chatgpt', 'app', NOW)
    with pytest.raises(DeskError, match='research_sources_not_captured'):
        sup.complete_external(job['job_id'], job['claim_token'],
                              {**OUTPUT, 'source_requests': [SOURCE]}, 'model', NOW, desk_id='chatgpt')
    assert not sup.store.snapshot(NOW)['publications']
