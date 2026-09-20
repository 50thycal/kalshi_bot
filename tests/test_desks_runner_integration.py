"""Real loopback HTTP + subprocess: research, source capture and lost acknowledgement."""
import hashlib
import json
import sys
import threading
from datetime import datetime, timezone

import httpx
import pytest

from kalshi_bot.desks import cli_adapter
from kalshi_bot.desks.config import DeskSettings
from kalshi_bot.desks.runner import RunnerConfig, SessionRunner
from kalshi_bot.desks.server import make_server
from kalshi_bot.desks.service import DeskService
from kalshi_bot.desks.store import DeskStore
from kalshi_bot.desks.supervisor import Supervisor


@pytest.mark.parametrize('adapter', [None, 'codex', 'claude'])
def test_research_worker_round_trip_and_restart_after_lost_acknowledgement(tmp_path, monkeypatch, adapter):
    now = datetime.now(timezone.utc)
    desk = 'claude' if adapter == 'claude' else 'chatgpt'
    monkeypatch.setattr('kalshi_bot.desks.server.utcnow', lambda: now)
    token = desk + '-test-' + 'x' * 40
    settings = DeskSettings(database_url=f'sqlite:///{tmp_path / "desks.sqlite"}',
                            operator_token='operator-test-' + 'x' * 40,
                            chatgpt_token='chatgpt-test-' + 'x' * 40,
                            claude_token='claude-test-' + 'x' * 40)
    store = DeskStore(settings.database_url.get_secret_value())
    store.initialize(settings.round_id, now)
    source_calls = []

    def source(url, timestamp):
        source_calls.append(url)
        excerpt = 'Offline fixture: observations do not establish a pricing edge.'
        return {'source_id': 'fixture-source', 'url': url, 'retrieved_at': timestamp.isoformat(),
                'excerpt': excerpt, 'sha256': hashlib.sha256(excerpt.encode()).hexdigest()}

    supervisor = Supervisor(store, market_reader=lambda timestamp: {'markets': [], 'sources': []},
                            source_fetcher=source, external_runners_verified=True)
    service = DeskService(settings, store, supervisor)
    server = make_server(service, ('127.0.0.1', 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = tmp_path / 'research_command.py'
    program = '''import json, os, sys
data = json.load(sys.stdin)
assert 'DESK_SESSION_TOKEN' not in os.environ
assert 'claim_token' not in data
assert data['desk_id'] == DESK_ID
out = {'summary':'No defensible pricing edge.', 'next_action':'Review the next observation release.'}
if data['phase'] == 'research':
    out['source_requests'] = ['https://api.weather.gov/fixture']
else:
    source = data['context']['requested_sources'][0]
    out['rejected'] = [{'ticker':'FIXTURE', 'hypothesis':'Observations may be delayed.',
                       'reason':'No evidence of an information advantage.',
                       'source_ids':[source['source_id']]}]
print(json.dumps(out))
'''.replace('DESK_ID', repr(desk))
    script.write_text(program)
    command = [sys.executable, str(script)]
    if adapter:
        # Real adapter CLI, fake native model executable: no provider access.
        binary = tmp_path / 'model-bin'
        binary.mkdir()
        client = binary / adapter
        native = program.replace('data = json.load(sys.stdin)',
                                 "data = json.loads(sys.stdin.read().rsplit('\\n\\n', 1)[1])")
        if adapter == 'codex':
            native = native.replace('print(json.dumps(out))',
                                    "from pathlib import Path\nPath(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(out))")
        else:
            native = native.replace('print(json.dumps(out))',
                                    "print(json.dumps({'subtype':'success','structured_output':out}))")
        client.write_text(f'#!{sys.executable}\n' + native)
        client.chmod(0o700)
        monkeypatch.setenv('PATH', str(binary))
        command = [sys.executable, cli_adapter.__file__, adapter]
    monkeypatch.setenv('DESK_SESSION_TOKEN', token)
    config = RunnerConfig(f'http://127.0.0.1:{server.server_port}', token, desk,
                          'integration-worker', 'fixture-command', command,
                          tmp_path / 'worker-state')
    runner = SessionRunner(config, clock=lambda: now)
    post = runner._post

    def drop_acknowledgement(action, body):
        result = post(action, body)
        if action == 'complete':
            raise httpx.ReadTimeout('simulated acknowledgement loss')
        return result

    runner._post = drop_acknowledgement
    try:
        assert runner.once()['status'] == 'recovering'
        assert source_calls == ['https://api.weather.gov/fixture']
        snapshot = store.snapshot(now)
        assert len(snapshot['publications']) == 2
        assert not snapshot['decisions']
        runner.close()

        def never_repeat(*args, **kwargs):
            raise AssertionError('A durable final result must never rerun the model')

        runner = SessionRunner(config, command_runner=never_repeat, clock=lambda: now)
        assert runner.once()['status'] == 'completed'
        assert runner.once()['status'] == 'idle'
        assert store.snapshot(now) == snapshot
        assert supervisor.status(now)['health'][desk]['completed_cycles'] == 1
        pending = json.loads((config.state_dir / 'pending.json').read_text())
        assert pending['phase'] == 'completed'
        assert (config.state_dir.stat().st_mode & 0o777) == 0o700
        assert ((config.state_dir / 'pending.json').stat().st_mode & 0o777) == 0o600
    finally:
        runner.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
