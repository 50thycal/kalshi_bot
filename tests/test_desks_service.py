"""Local-only service boundary tests: authorization, safe launch, and status integrity."""
from __future__ import annotations

import http.client
import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from kalshi_bot.desks.config import DeskSettings
from kalshi_bot.desks.contracts import Decision, DeskError, OrderReport, Settlement
from kalshi_bot.desks.scoreboard import comparison
from kalshi_bot.desks.server import MAX_BODY, make_server
from kalshi_bot.desks.service import DeskService
from kalshi_bot.desks.store import DeskStore

NOW = datetime(2026, 9, 20, 15, tzinfo=timezone.utc)
TOKENS = {role: role + '-secret-' + 'x' * 40 for role in ('operator', 'chatgpt', 'claude')}


class FakeSupervisor:
    def __init__(self):
        self.ticks = []
        self.fail = False
        self.healthy = False
        self.omit_health = False

    def status(self, now):
        return {'health': {} if self.omit_health else {
            desk: {'status': 'healthy' if self.healthy else 'needs_operator',
                   'reasons': [] if self.healthy else ['unattended_runner_missing']}
            for desk in ('chatgpt', 'claude')}, 'jobs': [], 'costs': {}}

    def request_cycle(self, desk_id, now):
        return self.tick(now)

    def tick(self, now):
        if self.fail:
            raise RuntimeError('sensitive backend exception')
        self.ticks.append(now)
        return {'scheduled': True}


class FakeNotifier:
    def __init__(self):
        self.is_verified = True

    def verified(self):
        return self.is_verified

    def status(self):
        return {'configured': True, 'verified': self.is_verified}

    def observe(self, status, now):
        pass

    def deliver(self, now):
        pass


class FakeExchange:
    def __init__(self, balance='30', verified=True):
        self.balance = balance
        self.verified = verified
        self.fail = False

    def check_clean_book(self):
        return True

    def check_isolation(self):
        if self.fail:
            raise RuntimeError('exchange credential secret')
        return {'verified': self.verified, 'balance': self.balance}


def settings(**overrides):
    data = {
        'database_url': 'sqlite://', 'round_id': 'test-round',
        **{f'{role}_token': value for role, value in TOKENS.items()},
        'live_enabled': True, 'existing_workers_isolated': True,
        'external_runners_verified': True,
        'chatgpt_subaccount': 1, 'claude_subaccount': 2,
        'chatgpt_kalshi_key_id': 'chatgpt-exchange-secret',
        'claude_kalshi_key_id': 'claude-exchange-secret',
        'chatgpt_kalshi_private_key': 'chatgpt-signing-secret',
        'claude_kalshi_private_key': 'claude-signing-secret',
        'alert_webhook_url': 'https://example.invalid/operator-webhook-secret',
    }
    data.update(overrides)
    return DeskSettings(**data)


@pytest.fixture
def service(tmp_path):
    store = DeskStore(f"sqlite:///{tmp_path / 'service.db'}")
    store.initialize('test-round', NOW)
    supervisor = FakeSupervisor()
    executors = {desk: SimpleNamespace(exchange=FakeExchange(), isolation_verified=False)
                 for desk in ('chatgpt', 'claude')}
    return DeskService(settings(), store, supervisor, executors, notifier=FakeNotifier())


@pytest.fixture
def api(service):
    server = make_server(service, ('127.0.0.1', 0))
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()

    def request(path, *, role='operator', method='GET', body=None, raw=None, headers=None, token=None):
        request_headers = {}
        if role is not None or token is not None:
            request_headers['Authorization'] = 'Bearer ' + (token if token is not None else TOKENS[role])
        if body is not None:
            raw = json.dumps(body)
            request_headers['Content-Type'] = 'application/json'
        if headers:
            request_headers.update(headers)
        conn = http.client.HTTPConnection(*server.server_address, timeout=3)
        try:
            conn.request(method, path, body=raw, headers=request_headers)
            response = conn.getresponse()
            result = response.read()
            value = json.loads(result) if response.getheader('Content-Type', '').startswith('application/json') else result.decode()
            return response.status, value, dict(response.getheaders())
        finally:
            conn.close()
    yield request
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def ready_both(service):
    service.supervisor.healthy = True
    for desk in ('chatgpt', 'claude'):
        service.store.ready(desk, NOW)


def decision_body(desk='chatgpt'):
    return {
        'decision_id': 'decision-api-test', 'desk_id': desk, 'round_id': 'test-round',
        'ticker': 'MARKET', 'event_id': 'EVENT', 'side': 'yes',
        'observed_price': '.40', 'quote_at': NOW.isoformat(), 'max_price': '.40',
        'max_spend': '1', 'probability': '.80', 'probability_low': '.70', 'probability_high': '.90',
        'expected_net_profit': '.30', 'settlement_source': 'https://example.invalid/settlement',
        'settlement_rule': 'Official outcome settles the contract.', 'rules_sha256': 'a'*64,
        'thesis': 'Contemporaneous evidence supports the thesis.',
        'counterargument': 'The source may revise its preliminary observation.',
        'invalidation': 'A source revision changes the outcome.', 'edge_class': 'information',
        'evidence': [{'url': 'https://example.invalid/evidence', 'retrieved_at': NOW.isoformat(),
                      'excerpt': 'Contemporaneous data.', 'sha256': 'b'*64}],
        'created_at': NOW.isoformat(), 'expires_at': (NOW + timedelta(minutes=5)).isoformat(),
        'author_model': 'model-under-test',
    }


def test_session_alerts_need_no_webhook_but_keep_other_gates(service):
    service.settings = service.settings.model_copy(update={"alert_mode": "session", "research_mode": "session"})
    service.notifier = None
    blocked = service.check_launch(NOW, refresh=True)
    assert "operator_alert_channel_not_configured" not in blocked["blockers"]
    assert "operator_alert_delivery_not_verified" not in blocked["blockers"]
    assert "claude_session_not_ready" in blocked["blockers"]
    ready_both(service)
    assert service.check_launch(NOW, refresh=True)["ready"]
    service.store.pause("chatgpt", "shared_account_check_failed")
    alerts = service.status(NOW)["alerts"]
    assert alerts["push_delivery"] is False
    assert any(n["pause_reason"] == "shared_account_check_failed" for n in alerts["notices"])


def test_session_alerts_never_send_to_old_webhook(service):
    service.settings = service.settings.model_copy(update={"alert_mode": "session", "research_mode": "session"})
    def forbidden(*args):
        raise AssertionError("session alerts must not send webhook messages")
    service.notifier.observe = service.notifier.deliver = forbidden
    service.tick(NOW)


def test_session_alerts_refuse_scheduled_mode():
    with pytest.raises(ValueError, match="app-session research"):
        settings(alert_mode="session", research_mode="scheduled")


def test_public_shell_and_health_expose_no_book_or_credentials(api):
    status, shell, headers = api('/', role=None)
    assert status == 200 and 'Operator access token' in shell
    assert headers['Cache-Control'] == 'no-store'
    assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
    status, health, _ = api('/healthz', role=None)
    assert status == 200 and health == {'service': 'desks', 'status': 'up'}
    assert not any(secret in shell + json.dumps(health) for secret in TOKENS.values())


@pytest.mark.parametrize('path,method,body', [
    ('/api/status', 'GET', None), ('/api/context', 'GET', None),
    ('/api/round/start', 'POST', {}), ('/api/desks/chatgpt/publications', 'POST', {}),
])
def test_private_endpoints_require_authentication(api, path, method, body):
    assert api(path, role=None, method=method, body=body)[0] == 401
    assert api(path, token='incorrect', method=method, body=body)[0] == 401


def test_non_ascii_token_is_rejected_without_crashing_handler(api):
    assert api('/api/status', token='caf\xe9')[0] == 401


@pytest.mark.parametrize('desk', ['chatgpt', 'claude'])
def test_desk_can_read_peer_but_cannot_mutate_peer_or_operate_round(api, service, desk):
    peer = 'claude' if desk == 'chatgpt' else 'chatgpt'
    assert api('/api/status', role=desk)[0] == 200
    for action in ('ready', 'continue', 'pause', 'resume', 'publications', 'claim', 'complete'):
        assert api(f'/api/desks/{peer}/{action}', role=desk, method='POST', body={})[0] == 403
    for action in ('pause', 'resume'):
        assert api(f'/api/desks/{desk}/{action}', role=desk, method='POST', body={})[0] == 403
    assert api('/api/round/start', role=desk, method='POST', body={})[0] == 403
    assert service.store.snapshot(NOW)['started_at'] is None


def test_operator_launch_requires_both_ready_and_starts_same_round(api, service):
    service.supervisor.healthy = True
    assert api('/api/round/start', method='POST', body={})[:2] == (409, {'error': 'launch_not_ready'})
    assert api('/api/desks/chatgpt/ready', role='chatgpt', method='POST', body={})[0] == 200
    assert api('/api/round/start', method='POST', body={})[0] == 409
    assert api('/api/desks/claude/ready', role='claude', method='POST', body={})[0] == 200
    status, started, _ = api('/api/round/start', method='POST', body={})
    assert status == 200 and started['started_at']
    assert {row['status'] for row in service.store.snapshot(NOW)['desks']} == {'running'}
    assert api('/api/round/start', method='POST', body={})[1]['started_at'] == started['started_at']


@pytest.mark.parametrize('fault', ['balance', 'unverified', 'exception', 'alerts', 'runner', 'missing_notifier',
                                    'unverified_delivery', 'unhealthy_research', 'missing_health'])
def test_launch_fails_closed_with_no_partial_start(service, fault):
    ready_both(service)
    if fault == 'balance':
        service.executors['claude'].exchange.balance = '29.99'
    elif fault == 'unverified':
        service.executors['claude'].exchange.verified = False
    elif fault == 'exception':
        service.executors['claude'].exchange.fail = True
    elif fault == 'alerts':
        service.settings = settings(alert_webhook_url='')
    elif fault == 'runner':
        service.settings = settings(external_runners_verified=False)
    elif fault == 'missing_notifier':
        service.notifier = None
    elif fault == 'unverified_delivery':
        service.notifier.is_verified = False
    elif fault == 'unhealthy_research':
        service.supervisor.healthy = False
    elif fault == 'missing_health':
        service.supervisor.omit_health = True
    readiness = service.check_launch(NOW, refresh=True)
    if fault in {'missing_notifier', 'unverified_delivery'}:
        assert 'operator_alert_delivery_not_verified' in readiness['blockers']
    if fault in {'unhealthy_research', 'missing_health'}:
        assert {'chatgpt_research_not_ready', 'claude_research_not_ready'} <= set(readiness['blockers'])
    with pytest.raises(DeskError, match='claude|alert|runner'):
        service.start(NOW)
    assert service.store.snapshot(NOW)['started_at'] is None


def test_failed_recheck_revokes_cached_isolation(service):
    ready_both(service)
    assert service.check_launch(NOW, refresh=True)['ready']
    assert not service.check_launch(NOW + timedelta(minutes=6))['ready']
    service.executors['claude'].exchange.fail = True
    assert not service.check_launch(NOW + timedelta(seconds=1), refresh=True)['ready']
    assert service.executors['claude'].isolation_verified is False
    assert 'claude' not in service._isolation


def test_continue_never_starts_round_or_resumes_paused_desk(api, service):
    assert api('/api/desks/chatgpt/pause', method='POST', body={'reason':'operator test'})[0] == 200
    assert api('/api/desks/chatgpt/continue', role='chatgpt', method='POST', body={})[0] == 200
    snap = service.store.snapshot(NOW)
    assert snap['started_at'] is None
    assert next(row for row in snap['desks'] if row['desk_id'] == 'chatgpt')['paused']
    assert service.supervisor.ticks
    assert api('/api/desks/chatgpt/resume', method='POST', body={})[0] == 200
    assert not next(row for row in service.store.snapshot(NOW)['desks'] if row['desk_id'] == 'chatgpt')['paused']


def test_publication_ownership_and_decision_payload_identity(api, service):
    body = {'kind':'lesson', 'payload':{'summary':'Documented finding.'}, 'record_id':'shared-id'}
    assert api('/api/desks/chatgpt/publications', role='chatgpt', method='POST', body=body)[0] == 200
    assert api('/api/desks/claude/publications', role='claude', method='POST', body=body)[0] == 409
    publications = service.store.snapshot(NOW)['publications']
    assert len(publications) == 1 and publications[0]['desk_id'] == 'chatgpt'
    assert api('/api/desks/chatgpt/decisions', role='chatgpt', method='POST', body=decision_body('claude'))[0] == 403


@pytest.mark.parametrize('body,status', [([],409), ({'kind':'unknown','payload':{}},409),
    ({'kind':'lesson','payload':'not-object'},409)])
def test_invalid_publications_rejected_without_write(api, service, body, status):
    assert api('/api/desks/chatgpt/publications', role='chatgpt', method='POST', body=body)[0] == status
    assert not service.store.snapshot(NOW)['publications']


def test_invalid_json_content_type_body_limit_and_invalid_decision(api):
    path = '/api/desks/chatgpt/decisions'
    assert api(path, method='POST', raw='{', headers={'Content-Type':'application/json'})[0] == 400
    assert api(path, method='POST', raw='{}', headers={'Content-Type':'text/plain'})[0] == 415
    assert api(path, method='POST', raw='x'*(MAX_BODY+1), headers={'Content-Type':'application/json'})[0] == 413
    assert api(path, method='POST', body={'desk_id':'chatgpt'})[:2] == (400, {'error':'invalid_request'})


def test_status_exposes_research_blockers_without_secrets(api, service):
    status, body, _ = api('/api/status')
    assert status == 200
    assert body['health']['chatgpt']['status'] == 'needs_operator'
    assert 'unattended_runner_missing' in body['health']['chatgpt']['reasons']
    encoded = json.dumps(body)
    secrets = [*TOKENS.values(), 'chatgpt-exchange-secret', 'claude-exchange-secret',
               'chatgpt-signing-secret', 'claude-signing-secret', 'operator-webhook-secret']
    assert not any(secret in encoded for secret in secrets)


def test_internal_failure_does_not_expose_exception_detail(api, service):
    service.supervisor.fail = True
    status, response, _ = api('/api/desks/chatgpt/continue', method='POST', body={})
    assert status == 503 and response == {'error':'operation_unavailable'}
    service.tick(NOW)
    assert service.last_error == 'research_cycle_failed'
    assert 'sensitive' not in json.dumps(service.status(NOW))


@pytest.mark.parametrize('override', [
    {'operator_token':'short'}, {'claude_token':TOKENS['chatgpt']},
    {'chatgpt_subaccount':0}, {'claude_subaccount':1},
    {'existing_workers_isolated':False}, {'claude_kalshi_key_id':'chatgpt-exchange-secret'},
    {'kalshi_base_url':'https://attacker.invalid'},
])
def test_live_settings_reject_weak_auth_or_shared_credentials(override):
    with pytest.raises(ValidationError):
        settings(**override)


@pytest.mark.parametrize('change', [
    {'max_spend':'1.01'}, {'probability':'NaN'}, {'probability_low':'.9'},
    {'created_at':'2026-09-20T15:00:00'}, {'expires_at':NOW.isoformat()},
    {'unexpected_field':'not allowed'},
])
def test_decision_contract_rejects_unsafe_values(change):
    with pytest.raises(ValidationError):
        Decision.model_validate(decision_body() | change)


def test_scoreboard_grades_unfilled_forecasts_but_never_counts_paper_profit():
    rows = [
        {'desk_id':'chatgpt','status':'settled','filled_quantity':'0','yes_payout':'1',
         'payload':{'probability':'.8','side':'yes'},'pnl':'0'},
        {'desk_id':'chatgpt','status':'settled','filled_quantity':'1','yes_payout':'0',
         'payload':{'probability':'.8','side':'no'},'pnl':'.58','fill_cost':'.4','fees':'.02'},
        {'desk_id':'chatgpt','status':'settled','filled_quantity':'0','yes_payout':'.5',
         'payload':{'probability':'.1','side':'yes'},'pnl':'0'},
    ]
    for index, row in enumerate(rows):
        row['decision_id'] = f'forecast-{index}'
        row['payload']['event_id'] = f'event-{index}'
    result = comparison({'decisions':rows})['chatgpt']
    assert result['settled_forecasts'] == 2
    assert result['filled_picks'] == 1
    assert Decimal(result['brier_score']) == Decimal('.04')
    assert Decimal(result['realized_pnl']) == Decimal('.58')
    assert Decimal(result['actual_dollars_deployed']) == Decimal('.42')
    assert comparison({})['claude']['brier_score'] is None


@pytest.mark.parametrize('fault,error', [
    ('monitor', 'execution_monitor_stale'),
    ('alerts', 'operator_alert_delivery_not_verified'),
    ('isolation', 'fresh_isolation_check_required'),
    ('runtime', 'runtime_configuration_not_ready'),
])
def test_submit_requires_fresh_monitor_isolation_and_working_alerts(service, fault, error):
    ready_both(service)
    service.start(NOW)
    assert service.supervisor.status(NOW)['health']['chatgpt']['status'] == 'healthy'
    called = []
    service.executors['chatgpt'].submit = lambda *args, **kwargs: called.append('order')
    service.supervisor.verify_decision_sources = lambda decision: called.append('provenance')
    now = NOW
    if fault == 'monitor':
        now = NOW + timedelta(seconds=181)  # Isolation remains fresh for five minutes.
    elif fault == 'alerts':
        service.notifier.is_verified = False
    elif fault == 'isolation':
        service._isolation['chatgpt']['at'] = NOW - timedelta(seconds=301)
    elif fault == 'runtime':
        service.settings = settings(live_enabled=False)
    with pytest.raises(DeskError, match=error):
        service.submit(Decision.model_validate(decision_body()), now)
    assert called == []
    assert not service.store.snapshot(now)['decisions']


def test_direct_postmortem_requires_valid_own_settled_decision(api, service):
    path = '/api/desks/chatgpt/publications'
    invalid = {'kind': 'postmortem', 'payload': {'decision_id': 'invented-decision'}}
    assert api(path, role='chatgpt', method='POST', body=invalid)[:2] == (400, {'error': 'invalid_request'})
    review = {'decision_id': 'decision-api-test', 'thesis_correct': False, 'price_wrong': False,
              'failure_category': 'outcome_variance', 'analysis': 'The evidence did not predict the settled result.',
              'next_change': 'Test corroboration on subsequent observations.'}
    body = {'kind': 'postmortem', 'payload': review}
    assert api(path, role='chatgpt', method='POST', body=body)[:2] == (
        409, {'error': 'postmortem_requires_own_settlement'})
    ready_both(service)
    service.start(NOW)
    decision = Decision.model_validate(decision_body())
    row = service.store.reserve(decision, 2, Decimal('.84'), NOW)
    # An existing decision alone is insufficient; it must actually be settled.
    assert api(path, role='chatgpt', method='POST', body=body)[0] == 409
    service.store.record_order(decision.decision_id, OrderReport(
        client_order_id=row['client_order_id'], order_id='review-order', status='terminal',
        filled_quantity='2', fill_cost='.80', fees='.04', observed_at=NOW), NOW)
    service.store.settlement(decision.decision_id, Settlement(
        ticker=decision.ticker, yes_payout='0', settled_at=NOW, source='https://example.invalid/result'))
    assert api('/api/desks/claude/publications', role='claude', method='POST', body=body)[:2] == (
        409, {'error': 'postmortem_requires_own_settlement'})
    assert not service.store.snapshot(NOW)['publications']
    assert api(path, role='chatgpt', method='POST', body=body)[0] == 200
    records = service.store.snapshot(NOW)['publications']
    assert len(records) == 1 and records[0]['desk_id'] == 'chatgpt'
    assert records[0]['payload']['decision_id'] == decision.decision_id


def test_preflight_refreshes_checks_without_starting_or_granting_desk_control(api, service):
    ready_both(service)
    assert api('/api/round/preflight', role='chatgpt', method='POST', body={})[0] == 403
    status, result, _ = api('/api/round/preflight', method='POST', body={})
    assert status == 200 and result['ready'] is True
    assert service.store.snapshot(NOW)['started_at'] is None
    assert service._isolation.keys() == {'chatgpt', 'claude'}


def test_research_schema_is_available_only_to_authenticated_sessions(api):
    assert api('/api/research/schema', role=None)[0] == 401
    status, schema, _ = api('/api/research/schema', role='chatgpt')
    assert status == 200 and schema['title'] == 'ResearchOutput'
    assert 'decisions' in schema['properties']
