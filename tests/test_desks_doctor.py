"""The diagnostic is read-only, fail-closed on missing evidence, and redacted."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_bot.desks.doctor import (
    MAX_RESPONSE_BYTES,
    DoctorError,
    assess,
    fetch_status,
    main,
    render,
    validate_connection,
)

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
TOKEN = 'private-test-token-' + 'x'*32
ENV = {'DESK_SERVICE_URL': 'https://desk.example.invalid', 'DESK_SESSION_TOKEN': TOKEN}


def healthy():
    return {'generated_at': NOW.isoformat(), 'started_at': None,
            'readiness': {'ready': True, 'blockers': []},
            'worker': {'last_tick': NOW.isoformat(), 'error': None},
            'alerts': {'configured': True, 'verified': True},
            'health': {desk: {'status': 'healthy', 'reasons': []} for desk in ('chatgpt', 'claude')},
            'desks': [{'desk_id': desk, 'ready': True, 'paused': False, 'status': 'waiting'}
                      for desk in ('chatgpt', 'claude')]}


def test_session_alerts_are_not_misrepresented_as_push_delivery():
    status = healthy()
    status['research_mode'] = 'session'
    status['alerts'] = {'mode': 'session', 'configured': True, 'push_delivery': False}
    report = assess(status, now=NOW)
    assert 'session_alerts_configured' in json.dumps(report)
    assert 'alerts_unverified' not in json.dumps(report)
    status['research_mode'] = 'scheduled'
    assert 'alerts_unverified' in json.dumps(assess(status, now=NOW))


@pytest.mark.parametrize('url', [
    'https://desk.example.invalid', 'https://desk.example.invalid/service/',
    'http://localhost:8090', 'http://127.0.0.1:8090', 'http://[::1]:8090',
])
def test_safe_connection_urls(url):
    endpoint, token = validate_connection(url, TOKEN)
    assert endpoint == url.rstrip('/') + '/api/status'
    assert token == TOKEN


@pytest.mark.parametrize('url', [
    '', 'http://remote.example.invalid', 'http://localhost.attacker.invalid',
    'https://user:password@desk.example.invalid', 'https://user@desk.example.invalid',
    'https://desk.example.invalid?token=secret', 'https://desk.example.invalid?',
    'https://desk.example.invalid#secret', 'https://desk.example.invalid#',
    'https://desk.example.invalid:70000', 'https://desk.example.invalid:0',
    'ftp://localhost', 'https:///missing-host', 'https://☃.invalid', 'https://desk.example.invalid/\nsecret',
])
def test_unsafe_connection_never_creates_transport(url):
    with pytest.raises(DoctorError, match='Set DESK_SERVICE_URL'):
        fetch_status(url, TOKEN, transport=httpx.MockTransport(lambda req: pytest.fail('No network expected')))


@pytest.mark.parametrize('token', ['', 'bad\nheader', 'token with spaces', 'non-ascii-\xe9'])
def test_invalid_token_is_rejected_without_echo(token, capsys):
    result = main(['--json'], environ=ENV | {'DESK_SESSION_TOKEN': token}, now=NOW)
    output = capsys.readouterr().out
    assert result == 1 and json.loads(output)['mode'] == 'unavailable'
    assert token not in output if token else True


def test_only_authenticated_bounded_get_no_writes_or_redirect_following(capsys):
    calls = []
    def response(request):
        calls.append(request)
        assert request.method == 'GET'
        assert str(request.url) == ENV['DESK_SERVICE_URL'] + '/api/status'
        assert request.headers['Authorization'] == 'Bearer ' + TOKEN
        assert request.headers['Accept-Encoding'] == 'identity'
        assert not request.content
        return httpx.Response(200, json=healthy())
    assert main([], environ=ENV, transport=httpx.MockTransport(response), now=NOW) == 0
    text = capsys.readouterr().out
    assert 'operator-controlled common start' in text
    assert TOKEN not in text and len(calls) == 1
    calls.clear()
    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={'location': 'https://attacker.invalid/' + TOKEN})
    assert main(['--json'], environ=ENV, transport=httpx.MockTransport(redirect), now=NOW) == 1
    report = capsys.readouterr().out
    assert TOKEN not in report and 'attacker' not in report
    assert len(calls) == 1 and 'redirect_refused' in report


@pytest.mark.parametrize('code', [401, 403, 500])
def test_auth_and_service_failure_bodies_are_not_echoed(code, capsys):
    transport = httpx.MockTransport(lambda request: httpx.Response(code, json={'error': TOKEN}))
    assert main(['--json'], environ=ENV, transport=transport, now=NOW) == 1
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert ('authentication_failed' if code in (401,403) else 'service_unavailable') in output


def test_transport_failure_is_redacted(capsys):
    def unavailable(request):
        raise httpx.ConnectError('private connection details ' + TOKEN)
    assert main(['--json'], environ=ENV, transport=httpx.MockTransport(unavailable), now=NOW) == 1
    output = capsys.readouterr().out
    assert 'connection_failed' in output and TOKEN not in output


@pytest.mark.parametrize('response', [
    lambda: httpx.Response(200, text='not json ' + TOKEN),
    lambda: httpx.Response(200, json=[]),
    lambda: httpx.Response(200, content=b'{"value": NaN}', headers={'content-type':'application/json'}),
    lambda: httpx.Response(200, content=b'x'*(MAX_RESPONSE_BYTES+1), headers={'content-type':'application/json'}),
])
def test_invalid_and_oversize_responses_fail_safely(response, capsys):
    result = main(['--json'], environ=ENV, transport=httpx.MockTransport(lambda request: response()), now=NOW)
    output = capsys.readouterr().out
    assert result == 1 and TOKEN not in output
    assert json.loads(output)['exit_code'] == 1


def test_streamed_body_is_limited_without_content_length():
    class LargeBody(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(40):
                yield b'x'*65536
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=LargeBody(),
                                      headers={'content-type': 'application/json'}))
    with pytest.raises(DoctorError) as caught:
        fetch_status(ENV['DESK_SERVICE_URL'], TOKEN, transport=transport)
    assert caught.value.code == 'response_too_large'


@pytest.mark.parametrize('missing', ['generated_at', 'worker', 'readiness', 'alerts', 'health', 'desks'])
def test_missing_evidence_never_claims_launch_ready(missing):
    status = healthy()
    del status[missing]
    report = assess(status, now=NOW)
    assert report['exit_code'] == 2 and not report['launch_ready']


def test_empty_status_is_reachable_but_unknown():
    report = assess({}, now=NOW)
    assert report['reachable'] and report['exit_code'] == 2
    assert not report['launch_ready'] and not report['research_healthy']


@pytest.mark.parametrize('change', ['stale_status', 'stale_worker', 'future_worker', 'worker_error', 'mixed_health', 'paused', 'missing_reason_list'])
def test_operational_issues_override_ready_boolean(change):
    status = healthy()
    if change == 'stale_status':
        status['generated_at'] = (NOW-timedelta(minutes=4)).isoformat()
    elif change == 'stale_worker':
        status['worker']['last_tick'] = (NOW-timedelta(seconds=181)).isoformat()
    elif change == 'future_worker':
        status['worker']['last_tick'] = (NOW+timedelta(minutes=1)).isoformat()
    elif change == 'worker_error':
        status['worker']['error'] = TOKEN
    elif change == 'mixed_health':
        status['health']['claude'] = {'status':'needs_operator', 'reasons':['unattended_runner_missing']}
    elif change == 'paused':
        status['desks'][0]['paused'] = True
    elif change == 'missing_reason_list':
        del status['health']['chatgpt']['reasons']
    report = assess(status, now=NOW)
    assert report['exit_code'] == 2 and not report['launch_ready']
    assert TOKEN not in json.dumps(report)


def test_research_only_is_distinct_from_live_ready():
    status = healthy()
    status['readiness'] = {'ready':False, 'blockers':['live_execution_disabled', 'chatgpt_fresh_isolation_check_required']}
    report = assess(status, now=NOW)
    assert report['mode'] == 'research_only' and report['research_healthy']
    assert report['exit_code'] == 2 and not report['launch_ready']
    assert 'Research is healthy; live launch remains blocked.' in render(report)
    assert not any(item['code'] == 'service_launch_ready' for item in report['groups']['trading_gates'])


def test_no_account_payload_arbitrary_error_or_secret_is_echoed(capsys):
    status = healthy()
    status.update({'round_id':TOKEN, 'publications':[{'payload':{'password':TOKEN}}],
                   'decisions':[{'payload':{'evidence':TOKEN}}], 'settings':{'api_key':TOKEN}})
    status['desks'][0].update({'cash':'123456.78', 'subaccount':53, 'pause_reason':TOKEN})
    status['readiness'] = {'ready':False, 'blockers':[TOKEN, {'password':TOKEN}, 'chatgpt_' + TOKEN]}
    status['health']['claude'] = {'status':TOKEN, 'reasons':[TOKEN]}
    for args in ([], ['--json']):
        assert main(args, environ=ENV, transport=httpx.MockTransport(lambda request: httpx.Response(200,json=status)), now=NOW) == 2
        output = capsys.readouterr().out
        assert TOKEN not in output and '123456.78' not in output and 'subaccount": 53' not in output
        assert 'password' not in output and 'api_key' not in output


def test_success_machine_report_uses_only_safe_fields(capsys):
    status = deepcopy(healthy())
    status['started_at'] = (NOW-timedelta(hours=1)).isoformat()
    for row in status['desks']:
        row['status'] = 'running'
    assert main(['--json'], environ=ENV, transport=httpx.MockTransport(lambda request: httpx.Response(200,json=status)), now=NOW) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['mode'] == 'live_healthy'
    assert set(report['groups']) == {'deployment','runtime','desk_readiness','trading_gates'}
    assert all(item['status'] == 'ok' for items in report['groups'].values() for item in items)
