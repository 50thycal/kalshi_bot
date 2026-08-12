"""The startup API-tier probe (main._probe_api_limits) and the client read behind it.

The mmsell scan bursts at roughly 6-25 requests/sec against a token bucket whose size is set by
our Kalshi tier — Basic 200 tokens/sec at 10 per request is 20 reads/sec, Advanced is 30. Every
capacity argument in docs/MMSELL_QUOTE_PARITY.md turns on which side of that we are on, and
until this probe nothing in the system knew. `GET /account/limits` is authoritative but needs
auth, so only the worker can ask.

What these tests protect is the probe's FAIL-SOFT contract. It runs during startup, before the
trading loop exists; if it could raise, a diagnostic would be able to stop the bot from
trading — a strictly worse outcome than not knowing the tier. The one exception is AuthError,
which must still propagate: bad credentials are a refuse-to-start condition everywhere else in
startup, and swallowing them here would turn a hard failure into a silent one.
"""

from __future__ import annotations

import pytest

from kalshi_bot import main as m
from kalshi_bot.kalshi.errors import AuthError


class _Client:
    def __init__(self, result=None, exc=None):
        self._result, self._exc = result, exc
        self.calls = 0

    def get_account_limits(self):
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._result


def test_probe_records_the_tier(monkeypatch):
    written: list[dict] = []
    monkeypatch.setattr(m, "session_scope", _fake_session_scope(written))

    client = _Client({"tier": "basic", "reads_per_second": 20})
    m._probe_api_limits(client)

    assert client.calls == 1
    assert written == [{"tier": "basic", "reads_per_second": 20}]


def test_a_failing_probe_never_stops_startup(monkeypatch):
    """The endpoint may not exist on every plan, and the network may be down at boot. Either
    way the bot must go on to trade — an unknown tier is a worse report, not a worse bot."""
    written: list[dict] = []
    monkeypatch.setattr(m, "session_scope", _fake_session_scope(written))

    m._probe_api_limits(_Client(exc=RuntimeError("404 not found")))
    assert written == []


def test_a_failing_PERSIST_never_stops_startup(monkeypatch):
    """The DB write is the second thing that can fail, and it fails independently of the fetch.
    A probe that logged the tier and then died on the insert would be the worst of both."""
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(m, "session_scope", _boom)
    m._probe_api_limits(_Client({"tier": "advanced"}))


def test_auth_errors_still_propagate(monkeypatch):
    """Bad credentials are a refuse-to-start condition. Swallowing an AuthError here would let
    the worker boot with keys that cannot trade, and the first sign would be a silent book."""
    monkeypatch.setattr(m, "session_scope", _fake_session_scope([]))
    with pytest.raises(AuthError):
        m._probe_api_limits(_Client(exc=AuthError("bad key")))


def test_a_non_dict_response_is_persisted_as_empty_not_crashed(monkeypatch):
    """Kalshi has changed this payload's shape before. An unexpected type must degrade to an
    empty row, never to an exception in startup."""
    written: list[dict] = []
    monkeypatch.setattr(m, "session_scope", _fake_session_scope(written))

    m._probe_api_limits(_Client(["unexpected"]))
    assert written == [{}]


def test_client_limits_endpoint_is_read_only():
    """The tier read must never become a tier CHANGE. Upgrading is a POST to a different path
    that this client deliberately does not implement, so an accidental verb change here would
    silently mutate the account."""
    import inspect

    from kalshi_bot.kalshi.client import KalshiClient

    # Body only — the docstring names the POST it is explaining that we do NOT make.
    src = inspect.getsource(KalshiClient.get_account_limits)
    body = src.rsplit('"""', 1)[-1]
    assert '"GET"' in body and "/account/limits" in body
    assert "POST" not in body
    assert not hasattr(KalshiClient, "upgrade_api_usage_level")


# ------------------------------------------------------------------ helpers


def _fake_session_scope(sink: list[dict]):
    """Stand-in for the real contextmanager: captures whatever raw payload gets logged."""
    import contextlib

    class _Session:
        def add(self, row):
            sink.append(getattr(row, "raw_json", None) or {})

        def flush(self):
            pass

    @contextlib.contextmanager
    def _scope():
        yield _Session()

    return _scope
