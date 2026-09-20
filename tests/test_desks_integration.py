"""Offline integration of two research desks from evidence to execution to learning.

Only network-facing exchange, source fetch, and alert delivery are substituted.
Persistence, provenance, scheduling, execution, accounting, and scoring are real.
"""
from __future__ import annotations

import hashlib
import socket
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from kalshi_bot.desks.config import DeskSettings
from kalshi_bot.desks.contracts import Decision, DeskError, OrderReport, Quote, Settlement
from kalshi_bot.desks.execution import DeskExecutor
from kalshi_bot.desks.service import DeskService
from kalshi_bot.desks.store import DeskStore
from kalshi_bot.desks.supervisor import Supervisor

D = Decimal
NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
SOURCE = 'https://api.weather.gov/observations/integration-fixture'
RULES = 'Official observation determines the yes outcome at settlement.'
RULE_HASH = hashlib.sha256(RULES.encode()).hexdigest()


class OfflineExchange:
    def __init__(self, subaccount):
        self.subaccount = subaccount
        self.now = NOW
        self.balance = D(30)
        self.orders = []
        self.outcome = None
        self.clean_checks = 0

    def check_isolation(self):
        return {'verified': True, 'balance': str(self.balance)}

    def check_clean_book(self):
        self.clean_checks += 1
        assert not self.orders

    def quote(self, ticker, side):
        return Quote(ticker=ticker, event_id='OBSERVATION-EVENT', side=side, ask='.40',
                     available_quantity=5, fetched_at=self.now,
                     closes_at=self.now + timedelta(hours=2), rules_sha256=RULE_HASH,
                     fee_rate='.07')

    def submit_ioc(self, client_id, ticker, side, quantity, max_price):
        assert quantity == 2 and max_price == D('.40') and side == 'yes'
        self.orders.append({'client_id': client_id, 'ticker': ticker, 'quantity': quantity})
        self.balance -= D('.84')
        return OrderReport(client_order_id=client_id, order_id=f'offline-order-{len(self.orders)}',
                           status='terminal', filled_quantity=quantity,
                           fill_cost='.80', fees='.04', observed_at=self.now)

    def reconcile(self, client_id, ticker):
        raise AssertionError('No order in this scenario should remain pending')

    def settlement(self, ticker):
        if self.outcome is None:
            return None
        return Settlement(ticker=ticker, yes_payout=self.outcome,
                          settled_at=self.now, source=SOURCE)


class OfflineNotifier:
    def verified(self):
        return True

    def status(self):
        return {'configured': True, 'verified': True}

    def observe(self, status, now):
        pass

    def deliver(self, now):
        pass


def verified_source(url, now):
    assert url == SOURCE
    excerpt = 'Official observation: station value exceeds the event threshold.'
    return {'source_id': str(uuid4()), 'url': url, 'retrieved_at': now.isoformat(),
            'excerpt': excerpt, 'sha256': hashlib.sha256(excerpt.encode()).hexdigest()}


def complete(supervisor, desk, job, output, now):
    return supervisor.complete_external(job['job_id'], job['claim_token'], output,
                                        f'offline-{desk}-model', now, desk_id=desk)


def fetch(supervisor, desk, job, now):
    return supervisor.fetch_external_source(job['job_id'], job['claim_token'], desk, SOURCE, now)


def make_decision(source, now):
    return Decision(
        decision_id='integration-chatgpt-trade', desk_id='chatgpt', round_id='integration-round',
        ticker='OBSERVATION-MARKET', event_id='OBSERVATION-EVENT', side='yes',
        observed_price='.40', quote_at=now, max_price='.40', max_spend='1',
        probability='.8', probability_low='.7', probability_high='.9', expected_net_profit='.76',
        settlement_source=SOURCE, settlement_rule=RULES, rules_sha256=RULE_HASH,
        thesis='Verified official observations imply an information advantage.',
        counterargument='A later correction could reverse the threshold result.',
        invalidation='The official source corrects its recorded observation.', edge_class='information',
        evidence=[{key: source[key] for key in ('url', 'retrieved_at', 'excerpt', 'sha256')}],
        created_at=now, expires_at=now + timedelta(minutes=5), author_model='offline-chatgpt-model',
        origin='session')


@pytest.mark.parametrize('yes_payout,expected_pnl,expected_brier', [('0', '-.84', '.64'), ('1', '1.16', '.04')])
def test_two_desks_evidence_fill_settlement_and_learning(tmp_path, monkeypatch, yes_payout, expected_pnl, expected_brier):
    def network_forbidden(*args, **kwargs):
        raise AssertionError('This integration scenario must remain offline')
    monkeypatch.setattr(socket, 'create_connection', network_forbidden)
    monkeypatch.setattr(socket.socket, 'connect', network_forbidden)
    store = DeskStore(f"sqlite:///{tmp_path / 'integration.db'}")
    store.initialize('integration-round', NOW)
    settings = DeskSettings(
        database_url=f"sqlite:///{tmp_path / 'integration.db'}", round_id='integration-round',
        operator_token='operator-' + 'x'*40, chatgpt_token='chatgpt-' + 'x'*40,
        claude_token='claude-' + 'x'*40, live_enabled=True, existing_workers_isolated=True,
        external_runners_verified=True, chatgpt_subaccount=1, claude_subaccount=2,
        chatgpt_kalshi_key_id='offline-chatgpt-key', claude_kalshi_key_id='offline-claude-key',
        chatgpt_kalshi_private_key='offline-chatgpt-private',
        claude_kalshi_private_key='offline-claude-private',
        alert_webhook_url='https://example.invalid/offline-alerts')
    exchanges = {desk: OfflineExchange(index) for index, desk in enumerate(('chatgpt', 'claude'), 1)}
    executors = {desk: DeskExecutor(store, exchange, desk, live_enabled=True,
                                    existing_workers_isolated=True)
                 for desk, exchange in exchanges.items()}
    supervisor = Supervisor(store, source_fetcher=verified_source,
                            market_reader=lambda now: {'markets': [], 'sources': []},
                            external_runners_verified=True)
    service = DeskService(settings, store, supervisor, executors, notifier=OfflineNotifier())
    supervisor.submit_decision = service.submit
    assert not service.check_launch(NOW, refresh=True)['ready']

    # Both runners establish actual healthy research activity before a common start.
    for desk in ('chatgpt', 'claude'):
        job = supervisor.claim_external(desk, f'offline-{desk}-runner', NOW)
        source = fetch(supervisor, desk, job, NOW)
        baseline = {'summary': 'No sufficiently strong edge at the present price.',
                    'next_action': 'Check the next observation update.',
                    'rejected': [{'ticker': 'BASELINE-MARKET', 'hypothesis': 'Possible source edge.',
                                  'probability': '.51', 'reason': 'Costs overwhelm the small estimate.',
                                  'source_ids': [source['source_id']]}]}
        complete(supervisor, desk, job, baseline, NOW + timedelta(seconds=1))
        store.ready(desk, NOW + timedelta(seconds=1))
    assert not store.snapshot(NOW)['decisions']
    assert all(row['status'] == 'healthy' for row in supervisor.status(NOW + timedelta(seconds=2))['health'].values())
    started = service.start(NOW + timedelta(seconds=2))
    assert started['started_at'] == (NOW + timedelta(seconds=2)).isoformat()
    assert all(exchange.clean_checks > 0 for exchange in exchanges.values())
    assert {row['status'] for row in store.snapshot(NOW)['desks']} == {'running'}

    # The next ChatGPT cycle obtains provenance through its own claimed job.
    trade_at = NOW + timedelta(hours=1)
    exchanges['chatgpt'].now = trade_at
    service.tick(trade_at)  # Real monitor renews isolation and schedules both desk jobs.
    assert service.last_tick == trade_at
    assert service._isolation['chatgpt']['at'] == trade_at
    job = supervisor.claim_external('chatgpt', 'offline-chatgpt-runner', trade_at)
    assert any(p['desk_id'] == 'claude' for p in job['context']['peer_and_own_publications'])
    source = fetch(supervisor, 'chatgpt', job, trade_at)
    decision = make_decision(source, trade_at)
    output = {'summary': 'A source-backed candidate clears the conservative entry gate.',
              'next_action': 'Review the official result and execution after settlement.',
              'decisions': [{'decision': decision.model_dump(mode='json'),
                             'source_ids': [source['source_id']]}]}
    with pytest.raises(DeskError, match='research_claim_mismatch'):
        complete(supervisor, 'claude', job, output, trade_at)
    wrong_owner = {**output, 'decisions': [{'decision': decision.model_dump(mode='json') | {'desk_id': 'claude'},
                                          'source_ids': [source['source_id']]}]}
    with pytest.raises(DeskError, match='research_decision_owner_mismatch'):
        complete(supervisor, 'chatgpt', job, wrong_owner, trade_at)
    forged = decision.model_copy(update={'evidence': [decision.evidence[0].model_copy(update={'sha256': 'f'*64})]})
    with pytest.raises(DeskError, match='unverified_decision_evidence'):
        service.submit(forged, trade_at)
    assert not exchanges['chatgpt'].orders and not store.snapshot(trade_at)['decisions']

    complete(supervisor, 'chatgpt', job, output, trade_at)
    filled = store.get_decision(decision.decision_id)
    assert filled['status'] == 'terminal' and D(filled['filled_quantity']) == 2
    assert D(filled['fill_cost']) + D(filled['fees']) == D('.84')
    assert len(exchanges['chatgpt'].orders) == 1 and not exchanges['claude'].orders
    service.submit(decision, trade_at)  # Same immutable decision cannot submit twice.
    assert len(exchanges['chatgpt'].orders) == 1
    books = {row['desk_id']: row for row in store.snapshot(trade_at)['desks']}
    assert books['chatgpt']['filled_today'] == 1 and D(books['chatgpt']['cash']) == D('29.16')
    assert books['claude']['filled_today'] == 0 and D(books['claude']['cash']) == D(30)

    # Exchange settlement flows through the real executor into ledger and scoring.
    settle_at = trade_at + timedelta(minutes=10)
    exchanges['chatgpt'].now = settle_at
    exchanges['chatgpt'].outcome = D(yes_payout)
    service.reconcile(settle_at)
    before_review = service.status(settle_at)['comparison']['chatgpt']
    assert D(before_review['realized_pnl']) == D(expected_pnl)
    assert D(before_review['brier_score']) == D(expected_brier)
    assert before_review['unreviewed_settlements'] == 1
    assert before_review['unreviewed_losses'] == (1 if yes_payout == '0' else 0)
    assert before_review['independent_settled_events'] == 1

    # The next cycle receives settled evidence and publishes an attributable lesson.
    review_at = NOW + timedelta(hours=2)
    review_job = supervisor.claim_external('chatgpt', 'offline-chatgpt-runner', review_at)
    assert any(row.get('decision_id', row.get('record_id')) == decision.decision_id and row['settled']
               for row in review_job['context']['recent_decisions'])
    lesson = 'Require corroborating observations before treating one source as decisive.'
    review = {'summary': 'Reviewed entry price, realized result, and original evidence.',
              'next_action': 'Test source corroboration on subsequent independent events.',
              'lessons': [lesson], 'postmortems': [{
                  'decision_id': decision.decision_id, 'thesis_correct': yes_payout == '1',
                  'price_wrong': False, 'failure_category': 'outcome_variance',
                  'analysis': 'The price stayed within the cap; one outcome cannot prove calibration.',
                  'next_change': 'Measure source corroboration without increasing trade size.'}]}
    complete(supervisor, 'chatgpt', review_job, review, review_at)
    final = service.status(review_at)
    assert final['comparison']['chatgpt']['unreviewed_settlements'] == 0
    assert final['comparison']['chatgpt']['unreviewed_losses'] == 0
    assert final['comparison']['chatgpt']['filled_picks'] == 1
    assert final['comparison']['claude']['filled_picks'] == 0
    assert any(p['kind'] == 'lesson' and p['desk_id'] == 'chatgpt' and p['payload']['lesson'] == lesson
               for p in final['publications'])
    assert any(p['kind'] == 'postmortem' and p['payload']['decision_id'] == decision.decision_id
               for p in final['publications'])
    final_books = {row['desk_id']: row for row in final['desks']}
    assert D(final_books['chatgpt']['cash']) == D(30) + D(expected_pnl)
    assert D(final_books['chatgpt']['committed']) == 0
    assert D(final_books['claude']['cash']) == 30
    assert final['started_at'] == started['started_at']
