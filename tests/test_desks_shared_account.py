"""Shared-account separation: incumbent protection and immutable book ownership."""
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from pydantic import SecretStr

from kalshi_bot.desks.contracts import DeskError, utcnow
from kalshi_bot.desks.shared_account import SharedAccountExchange
from kalshi_bot.desks.store import DeskStore
from kalshi_bot.kalshi.client import KalshiClient
from kalshi_bot.kalshi.errors import AuthError
from kalshi_bot.kalshi.ownership import MarketOwnership


@pytest.fixture
def ownership(tmp_path):
    return MarketOwnership(f"sqlite:///{tmp_path / 'ownership.db'}", "test-primary")


def test_claim_survives_restart_and_only_one_owner_wins(ownership):
    def claim(owner):
        try:
            ownership.claim('MARKET', owner)
            return owner
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        winners = [r for r in pool.map(claim, ['main', 'chatgpt', 'claude']) if r]
    assert len(winners) == 1
    restarted = MarketOwnership(str(ownership.engine.url), 'test-primary')
    assert restarted.owner('MARKET') == winners[0]
    restarted.claim('MARKET', winners[0])


@pytest.fixture
def shared(tmp_path, settings, ownership):
    store = DeskStore(f"sqlite:///{tmp_path / 'desks.db'}")
    store.initialize('test', utcnow())
    data = {'orders': [], 'market_positions': [], 'balance': 6000}
    requests = []
    def transport(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.url.params['subaccount'] == '0'
        path = request.url.path
        if path.endswith('/orders'):
            return httpx.Response(200, json={'orders': data['orders']})
        if path.endswith('/positions'):
            return httpx.Response(200, json={'market_positions': data['market_positions']})
        if path.endswith('/balance'):
            return httpx.Response(200, json={'balance': data['balance']})
        return httpx.Response(200, json={'market': {'ticker': 'MARKET', 'status': 'open'}})
    exchange = SharedAccountExchange('https://demo-api.kalshi.co/trade-api/v2',
                                    settings.kalshi_api_key_id, settings.private_key_pem,
                                    ownership=ownership, store=store, desk_id='chatgpt',
                                    client=httpx.Client(transport=httpx.MockTransport(transport)))
    return exchange, data, requests


def test_shared_cash_is_not_counted_as_two_separate_deposits(shared):
    exchange, data, requests = shared
    assert exchange.check_isolation()['protection'] == 'software_ownership'
    data['balance'] = 5999
    with pytest.raises(DeskError, match='cash_shortfall'):
        exchange.check_isolation()
    assert all(r.method == 'GET' for r in requests)


@pytest.mark.parametrize('activity', ['orders', 'market_positions'])
def test_existing_incumbent_activity_cannot_be_adopted(shared, ownership, activity):
    exchange, data, _ = shared
    data[activity] = [{'ticker': 'MARKET', 'position_fp': '1', 'client_order_id': 'legacy'}]
    with pytest.raises(DeskError, match='existing_account_activity'):
        exchange.prepare_market('MARKET')
    assert ownership.owner('MARKET') == 'main'


def test_desk_claim_excludes_peer_and_detects_external_activity(shared, ownership):
    exchange, data, _ = shared
    exchange.prepare_market('MARKET')
    assert ownership.owner('MARKET') == 'chatgpt'
    with pytest.raises(ValueError, match='another book'):
        ownership.claim('MARKET', 'claude')
    data['orders'] = [{'ticker': 'MARKET', 'client_order_id': 'manual-unattributed'}]
    with pytest.raises(DeskError, match='unattributed_order'):
        exchange.audit()
    data['orders'] = []
    data['market_positions'] = [{'ticker': 'MARKET', 'position_fp': '2'}]
    with pytest.raises(DeskError, match='position_mismatch'):
        exchange.audit()


def test_worker_v1_v2_and_cancels_cannot_touch_desk_market(settings, ownership):
    settings.shared_account_ownership_url = SecretStr(str(ownership.engine.url))
    settings.shared_account_namespace = 'test-primary'
    settings.bot_mode, settings.kill_switch = 'live', False
    ownership.claim('DESK', 'chatgpt')
    requests = []
    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={'order': {'ticker': 'DESK'}})
    with KalshiClient(settings, transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(ValueError, match='another book'):
            client.create_events_order({'ticker': 'DESK'})
        with pytest.raises(ValueError, match='another book'):
            client.create_v1_order('user', {'market_id': 'uuid'}, ticker='DESK')
        with pytest.raises(ValueError, match='identity'):
            client.create_v1_order('user', {'market_id': 'uuid'})
        settings.kill_switch = True
        with pytest.raises(AuthError, match='outside worker scope'):
            client.cancel_events_order('desk-order', exchange_index=3)
    assert [r.method for r in requests] == ['GET']


def test_worker_filters_desk_positions_and_preserves_cursor(settings, ownership):
    settings.shared_account_ownership_url = SecretStr(str(ownership.engine.url))
    settings.shared_account_namespace = 'test-primary'
    ownership.claim('DESK', 'claude')
    payload = {'market_positions': [{'ticker': 'MAIN'}, {'ticker': 'DESK'}],
               'event_positions': [{'event_ticker': 'MIXED'}], 'cursor': 'next'}
    with KalshiClient(settings, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))) as client:
        result = client.get_positions()
    assert result == {'market_positions': [{'ticker': 'MAIN'}], 'event_positions': [], 'cursor': 'next'}
    assert len(payload['market_positions']) == 2


@pytest.fixture
def desk_case(tmp_path):
    from test_desks_execution import desk_case as factory
    return factory.__wrapped__(tmp_path)


def test_shared_adapter_submits_once_and_attributes_only_own_fill(settings, ownership, desk_case):
    import json
    from decimal import Decimal

    from kalshi_bot.desks.execution import DeskExecutor

    store, decision, quote, _, _, now = desk_case
    state = {'orders': [], 'posts': 0}
    def transport(request):
        assert request.url.params.get('subaccount', '0') == '0'
        if request.method == 'POST':
            body = json.loads(request.content)
            assert body['subaccount'] == 0 and body['time_in_force'] == 'immediate_or_cancel'
            state['posts'] += 1
            state['orders'] = [{'ticker': decision.ticker, 'client_order_id': body['client_order_id'],
                                'order_id': 'owned-order', 'subaccount_number': 0, 'fill_count_fp': '1',
                                'remaining_count_fp': '0', 'status': 'executed', 'outcome_side': 'yes'}]
            return httpx.Response(200, json={'order_id': 'owned-order'})
        path = request.url.path
        if path.endswith('/balance'):
            return httpx.Response(200, json={'balance': 6000 - 42 * state['posts']})
        if path.endswith('/orders'):
            return httpx.Response(200, json={'orders': state['orders']})
        if path.endswith('/positions'):
            return httpx.Response(200, json={'market_positions': [] if not state['posts'] else [
                {'ticker': decision.ticker, 'position_fp': '1'}]})
        if path.endswith('/fills'):
            return httpx.Response(200, json={'fills': [{'fill_id': 'fill-1', 'order_id': 'owned-order',
                'subaccount_number': 0, 'outcome_side': 'yes', 'ticker': decision.ticker,
                'count_fp': '1', 'yes_price_dollars': '.40', 'fee_cost': '.02'}]})
        raise AssertionError(path)
    exchange = SharedAccountExchange('https://demo-api.kalshi.co/trade-api/v2',
                                     settings.kalshi_api_key_id, settings.private_key_pem,
                                     ownership=ownership, store=store, desk_id='chatgpt',
                                     client=httpx.Client(transport=httpx.MockTransport(transport)))
    exchange.quote = lambda *args: quote
    executor = DeskExecutor(store, exchange, 'chatgpt', live_enabled=True,
                            isolation_verified=True, existing_workers_isolated=True)
    result = executor.submit(decision, now)
    assert result['status'] == 'terminal'
    assert Decimal(result['fill_cost']) + Decimal(result['fees']) == Decimal('.42')
    exchange.audit()
    executor.submit(decision, now)
    assert state['posts'] == 1
    books = {b['desk_id']: b for b in store.snapshot(now)['desks']}
    assert Decimal(books['chatgpt']['cash']) == Decimal('29.58')
    assert Decimal(books['claude']['cash']) == 30
    with pytest.raises(ValueError, match='another book'):
        ownership.claim(decision.ticker, 'main')


def test_shared_configuration_allows_existing_key_but_requires_registry():
    from pydantic import ValidationError

    from kalshi_bot.desks.config import DeskSettings
    values = dict(database_url='sqlite://', operator_token='o'*40, chatgpt_token='g'*40,
                  claude_token='c'*40, research_mode='session', account_mode='shared_primary',
                  live_enabled=True, existing_workers_isolated=True,
                  chatgpt_kalshi_key_id='existing-key', claude_kalshi_key_id='existing-key',
                  chatgpt_kalshi_private_key='private', claude_kalshi_private_key='private')
    with pytest.raises(ValidationError, match='ownership database'):
        DeskSettings(**values)
    config = DeskSettings(**values, shared_ownership_url='sqlite://')
    assert config.static_blockers() == []
    with pytest.raises(ValidationError, match='primary account'):
        DeskSettings(**values, shared_ownership_url='sqlite://', chatgpt_subaccount=1)


@pytest.mark.skipif(not __import__('os').environ.get('XOS_TEST_POSTGRES_URL'),
                    reason='CI provides PostgreSQL')
def test_postgres_separate_clients_cannot_claim_same_market():
    import os
    from uuid import uuid4

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    url = make_url(os.environ['XOS_TEST_POSTGRES_URL'])
    if url.drivername in {'postgres', 'postgresql'}:
        url = url.set(drivername='postgresql+psycopg')
    schema = 'ownership_test_' + uuid4().hex
    admin = create_engine(url)
    registries = []
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        scoped = url.update_query_dict({'options': f'-csearch_path={schema}'})
        registries = [MarketOwnership(scoped.render_as_string(hide_password=False), 'test') for _ in range(3)]
        def claim(pair):
            registry, owner = pair
            try:
                registry.claim('MARKET', owner)
                return owner
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=3) as pool:
            winners = [r for r in pool.map(claim, zip(registries, ['main', 'chatgpt', 'claude'], strict=True)) if r]
        assert len(winners) == 1
        assert all(r.owner('MARKET') == winners[0] for r in registries)
    finally:
        for registry in registries:
            registry.engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
