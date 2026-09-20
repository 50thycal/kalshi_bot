"""Read-only desk launch/operations check. Never starts, resumes, or refreshes gates.

Run ``python -m kalshi_bot.desks.doctor [--json]`` with DESK_SERVICE_URL and
DESK_SESSION_TOKEN. Exit 0: fresh evidence reports launch-ready/healthy; 2:
reachable but blocked/incomplete/stale; 1: invalid configuration or failed read.
Only allowlisted diagnostic fields are emitted, never the status payload.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

DESKS = ('chatgpt', 'claude')
GROUPS = ('deployment', 'runtime', 'desk_readiness', 'trading_gates')
MAX_RESPONSE_BYTES = 2_000_000
MAX_EVIDENCE_AGE_SECONDS = 180

# Whitelisted codes and explanatory text prevent error messages or records from
# smuggling credentials/account details into CLI output, including JSON output.
BLOCKERS = {
    'live_execution_disabled': ('trading_gates', 'Live trading is disabled; research can continue.'),
    'existing_worker_subaccount_isolation_unverified': ('trading_gates', 'Verify separation from existing automated books.'),
    'two_distinct_non_primary_subaccounts_required': ('trading_gates', 'Configure a separate non-primary subaccount for each desk.'),
    'exchange_credentials_missing': ('deployment', 'Configure this desk’s restricted trading credentials.'),
    'unattended_runner_unverified': ('runtime', 'Verify the unattended research runner and complete a research cycle.'),
    'unattended_runner_missing': ('runtime', 'Connect an unattended research runner and complete a research cycle.'),
    'paid_research_budget_not_authorized': ('runtime', 'Use an approved runner or authorize a paid research budget.'),
    'model_credentials_missing': ('deployment', 'Configure this desk’s model access credentials.'),
    'session_cycle_required': ('desk_readiness', 'Complete a research cycle in this desk’s app session.'),
    'session_not_ready': ('desk_readiness', 'Complete the desk’s readiness setup.'),
    'exchange_unavailable': ('deployment', 'Restore the desk’s exchange connection.'),
    'funding_or_isolation_not_verified': ('trading_gates', 'Verify the starting funds and restricted-account isolation.'),
    'fresh_isolation_check_required': ('trading_gates', 'A fresh exchange isolation check is needed from the authorized service.'),
    'operator_alert_channel_not_configured': ('deployment', 'Configure an operator alert destination.'),
    'operator_alert_delivery_not_verified': ('trading_gates', 'Verify successful operator alert delivery.'),
    'research_not_ready': ('desk_readiness', 'Resolve the desk’s research health issues before live launch.'),
    'capital_exhausted': ('trading_gates', 'Trading capital is exhausted; do not replenish automatically.'),
    'settlement_learning_backlog': ('desk_readiness', 'Complete the outstanding settlement reviews.'),
    'research_budget_exhausted': ('runtime', 'The research budget is exhausted; spending requires an operator decision.'),
    'repeated_research_failures': ('runtime', 'Investigate repeated research failures.'),
    'provider_bill_requires_reconciliation': ('runtime', 'Reconcile the uncertain model bill before further spending.'),
    'no_completed_cycle_24h': ('runtime', 'Restore the research runner; no cycle has completed in 24 hours.'),
}
FAILURES = {
    'invalid_configuration': 'Set DESK_SERVICE_URL to HTTPS or loopback HTTP and provide a valid DESK_SESSION_TOKEN.',
    'authentication_failed': 'The service rejected the access token. Check its role and current value.',
    'redirect_refused': 'The service redirected the request. Configure the final service URL directly.',
    'service_unavailable': 'The service could not serve status. Check its deployment and application logs.',
    'connection_failed': 'The status request failed or timed out. Check service reachability.',
    'response_too_large': 'The status response exceeded the diagnostic size limit.',
    'invalid_response': 'The service did not return a valid JSON status object.',
}


class DoctorError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(FAILURES[code])


def validate_connection(url: str, token: str) -> tuple[str, str]:
    """Accept a base URL, never an embedded credential or query-token transport."""
    if not isinstance(url, str) or not isinstance(token, str):
        raise DoctorError('invalid_configuration')
    token = token.strip()
    if (not url or not token or len(token) > 4096 or not token.isascii()
            or any(ord(c) <= 32 or ord(c) == 127 for c in url + token)
            or '?' in url or '#' in url):
        raise DoctorError('invalid_configuration')
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port  # Also validates malformed/out-of-range ports.
        if (not host or parsed.username is not None or parsed.password is not None
                or parsed.scheme not in ('https', 'http') or port == 0):
            raise ValueError
        loopback = host.lower() == 'localhost'
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if parsed.scheme == 'http' and not loopback:
            raise ValueError
        httpx.URL(url)  # Validate IDNA/URL syntax before attempting a request.
    except (ValueError, httpx.InvalidURL, UnicodeError):
        raise DoctorError('invalid_configuration') from None
    return url.rstrip('/') + '/api/status', token


def fetch_status(url, token, *, transport=None):
    endpoint, token = validate_connection(url, token)
    try:
        with httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False,
                          trust_env=False, transport=transport) as client:
            with client.stream('GET', endpoint, headers={
                'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                'Accept-Encoding': 'identity',
            }) as response:
                if response.status_code in (401, 403):
                    raise DoctorError('authentication_failed')
                if 300 <= response.status_code < 400:
                    raise DoctorError('redirect_refused')
                if response.status_code != 200:
                    raise DoctorError('service_unavailable')
                if response.headers.get('content-encoding', 'identity') != 'identity':
                    raise DoctorError('invalid_response')
                content_type = response.headers.get('content-type', '').split(';', 1)[0].strip()
                if content_type != 'application/json' and not content_type.endswith('+json'):
                    raise DoctorError('invalid_response')
                length = response.headers.get('content-length')
                if length is not None:
                    try:
                        declared_length = int(length)
                    except ValueError:
                        raise DoctorError('invalid_response') from None
                    if declared_length > MAX_RESPONSE_BYTES:
                        raise DoctorError('response_too_large')
                    if declared_length < 0:
                        raise DoctorError('invalid_response')
                chunks = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(chunks) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise DoctorError('response_too_large')
                    chunks.extend(chunk)
    except httpx.HTTPError:
        raise DoctorError('connection_failed') from None
    try:
        def reject_constant(_value):
            raise ValueError
        payload = json.loads(chunks, parse_constant=reject_constant)
        if not isinstance(payload, dict):
            raise ValueError
        return payload
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise DoctorError('invalid_response') from None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _fresh(raw, now):
    if not isinstance(raw, str):
        return False
    try:
        timestamp = datetime.fromisoformat(raw)
        return timestamp.tzinfo is not None and -5 <= (now - timestamp).total_seconds() <= MAX_EVIDENCE_AGE_SECONDS
    except (ValueError, OverflowError):
        return False


def assess(status, *, now=None):
    """Project untrusted status onto fixed diagnostic text and enumerated labels."""
    now = now or datetime.now(timezone.utc)
    report = {'reachable': True, 'mode': 'setup_needed', 'launch_ready': False,
              'research_healthy': False, 'exit_code': 2, 'groups': {key: [] for key in GROUPS}}
    seen = set()

    def add(group, code, message, *, good=False, desk=None):
        key = (group, code, desk)
        if key in seen:
            return
        seen.add(key)
        item = {'code': code, 'status': 'ok' if good else 'action_needed', 'message': message}
        if desk in DESKS:
            item['desk'] = desk
        report['groups'][group].append(item)

    def blocker(value, desk=None):
        if isinstance(value, str):
            for identity in DESKS:
                if value.startswith(identity + '_'):
                    desk, value = identity, value[len(identity) + 1:]
                    break
            if value.startswith('trading_paused:'):
                add('trading_gates', 'trading_paused', 'Trading is paused; inspect the authenticated dashboard before resuming.', desk=desk)
                return
            if value in BLOCKERS:
                group, message = BLOCKERS[value]
                add(group, value, message, desk=desk)
                return
        add('desk_readiness', 'unrecognized_blocker', 'Inspect the authenticated dashboard for an additional service-reported issue.', desk=desk)

    add('deployment', 'authenticated_status', 'Authenticated status was retrieved without changing the service.', good=True)
    fresh = _fresh(status.get('generated_at'), now)
    add('deployment', 'status_fresh' if fresh else 'status_freshness_unknown',
        'Status evidence is current.' if fresh else 'Status evidence is missing, stale, or has an invalid timestamp.', good=fresh)
    worker = _dict(status.get('worker'))
    monitor = _fresh(worker.get('last_tick'), now) and not worker.get('error')
    add('runtime', 'monitor_healthy' if monitor else 'monitor_not_healthy',
        'The execution monitor has a recent successful tick.' if monitor else 'Restore the execution monitor or inspect its reported failure; a fresh tick is required.', good=monitor)
    readiness = _dict(status.get('readiness'))
    listed = readiness.get('blockers')
    blockers_valid = isinstance(listed, list)
    if blockers_valid:
        for reason in listed[:100]:
            blocker(reason)
        if len(listed) > 100:
            add('desk_readiness', 'more_blockers', 'Further service issues are available in the authenticated dashboard.')
    else:
        add('trading_gates', 'readiness_evidence_missing', 'The service did not report its full launch checks.')
    launch_claim = readiness.get('ready') is True and blockers_valid and not listed
    add('trading_gates', 'service_launch_ready' if launch_claim else 'service_launch_blocked',
        'The service reports its current launch checks passed; this command did not perform or renew them.' if launch_claim else 'The service has not reported all live launch checks passed.', good=launch_claim)
    alerts = _dict(status.get('alerts'))
    alert_ready = alerts.get('configured') is True and alerts.get('verified') is True
    add('trading_gates', 'alerts_verified' if alert_ready else 'alerts_unverified',
        'The service reports verified operator alert delivery.' if alert_ready else 'Configure and verify operator alert delivery before live trading.', good=alert_ready)
    health = _dict(status.get('health'))
    rows = status.get('desks') if isinstance(status.get('desks'), list) else []
    desk_health, desk_readiness = [], []
    for desk in DESKS:
        evidence = _dict(health.get(desk))
        reasons = evidence.get('reasons')
        healthy = evidence.get('status') == 'healthy' and isinstance(reasons, list) and not reasons
        desk_health.append(healthy)
        add('desk_readiness', 'research_healthy' if healthy else 'research_not_healthy',
            'Research is reported healthy; zero trades is acceptable.' if healthy else 'Research health is blocked, incomplete, or not reported.', good=healthy, desk=desk)
        if isinstance(reasons, list):
            for reason in reasons[:100]:
                blocker(reason, desk)
        matches = [row for row in rows if isinstance(row, dict) and row.get('desk_id') == desk]
        row = matches[0] if len(matches) == 1 else {}
        ready = row.get('ready') is True and row.get('paused') is False and row.get('status') in ('waiting', 'running')
        desk_readiness.append(ready)
        add('desk_readiness', 'desk_ready' if ready else 'desk_not_ready',
            'The desk has declared readiness and is not paused.' if ready else 'Desk readiness is missing or trading is paused; inspect the desk setup.', good=ready, desk=desk)
    report['research_healthy'] = fresh and monitor and all(desk_health)
    report['launch_ready'] = report['research_healthy'] and all(desk_readiness) and launch_claim and alert_ready
    if report['launch_ready']:
        report['mode'] = 'live_healthy' if status.get('started_at') else 'launch_ready'
        report['exit_code'] = 0
    elif report['research_healthy']:
        report['mode'] = 'research_only'
    return report


def failed_report(code):
    return {'reachable': False, 'mode': 'unavailable', 'launch_ready': False,
            'research_healthy': False, 'exit_code': 1,
            'groups': {group: ([{'code': code, 'status': 'action_needed', 'message': FAILURES[code]}]
                               if group == 'deployment' else []) for group in GROUPS}}


def render(report):
    summaries = {
        'live_healthy': 'Desk checks passed: live operation is reported healthy.',
        'launch_ready': 'Desk checks passed: ready for an operator-controlled common start.',
        'research_only': 'Research is healthy; live launch remains blocked.',
        'setup_needed': 'Service reachable; setup or operational attention is needed.',
        'unavailable': 'Desk status could not be verified.',
    }
    lines = [summaries[report['mode']], 'Read-only check: no orders, starts, resumes, or gate refreshes were requested.']
    for group, items in report['groups'].items():
        if not items:
            continue
        lines.append('\n' + group.replace('_', ' ').capitalize() + ':')
        for item in items:
            prefix = ('ChatGPT' if item.get('desk') == 'chatgpt' else 'Claude') + ': ' if item.get('desk') else ''
            mark = 'OK' if item['status'] == 'ok' else 'ACTION'
            lines.append(f"  {mark} — {prefix}{item['message']}")
    return '\n'.join(lines)


def main(argv=None, *, environ=None, transport=None, now=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Print only the redacted machine-readable report.')
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        status = fetch_status(environ.get('DESK_SERVICE_URL', ''), environ.get('DESK_SESSION_TOKEN', ''), transport=transport)
        report = assess(status, now=now)
    except DoctorError as error:
        report = failed_report(error.code)
    print(json.dumps(report, sort_keys=True) if args.json else render(report))
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
