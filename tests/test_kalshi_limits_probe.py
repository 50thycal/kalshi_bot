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
    """The tier READ must never become a tier CHANGE. Upgrading is a POST to a different path
    and a different method; an accidental verb change here would silently mutate the account on
    every start, with no flag to stop it."""
    import inspect

    from kalshi_bot.kalshi.client import KalshiClient

    # Body only — the docstring names the POST it is explaining that this method does NOT make.
    src = inspect.getsource(KalshiClient.get_account_limits)
    body = src.rsplit('"""', 1)[-1]
    assert '"GET"' in body and "/account/limits" in body
    assert "POST" not in body


# ------------------------------------------------------------------ the ADVANCED upgrade
#
# The one account-MUTATING call the worker makes outside order placement. Every test below is
# about a guard, because the failure modes are asymmetric: not upgrading costs us 10 reads/sec,
# while upgrading when we did not mean to writes to the user's account. When in doubt, skip.


class _UpgradeClient:
    """Reads `basic`, then whatever `after` says once the upgrade is requested."""

    def __init__(self, before="basic", after="advanced", upgrade_exc=None, read_exc=None):
        self._before, self._after = before, after
        self._upgrade_exc, self._read_exc = upgrade_exc, read_exc
        self.upgrades = 0
        self.upgraded = False

    def get_account_limits(self):
        if self._read_exc and self.upgraded:
            raise self._read_exc
        tier = self._after if self.upgraded else self._before
        return {"usage_tier": tier} if tier else {}

    def upgrade_api_usage_level(self):
        self.upgrades += 1
        if self._upgrade_exc:
            raise self._upgrade_exc
        self.upgraded = True
        return {}


class _Settings:
    def __init__(self, on):
        self.kalshi_upgrade_api_tier = on


def test_no_upgrade_without_the_flag():
    """Nothing writes to the account by default. This is the guard that makes every other
    behaviour here opt-in."""
    client = _UpgradeClient()
    assert m._maybe_upgrade_api_tier(client, _Settings(False), {"usage_tier": "basic"}) is None
    assert client.upgrades == 0


def test_upgrade_fires_once_and_is_verified_by_a_re_read():
    """Kalshi answers 201 with an empty body, so the POST alone proves nothing — the grant is
    only real if a fresh read says so."""
    client = _UpgradeClient()
    after = m._maybe_upgrade_api_tier(client, _Settings(True), {"usage_tier": "basic"})

    assert client.upgrades == 1
    assert after == {"usage_tier": "advanced"}


def test_upgrade_is_skipped_once_the_account_is_already_upgraded():
    """This is what makes the switch safe to LEAVE ON. The grant is permanent, so a flag that
    re-POSTed every deploy would write to the account forever for no benefit."""
    client = _UpgradeClient(before="advanced")
    assert m._maybe_upgrade_api_tier(client, _Settings(True), {"usage_tier": "advanced"}) is None
    assert client.upgrades == 0


def test_an_unknown_tier_does_not_qualify_as_basic():
    """The dangerous direction. If the payload is renamed or unreadable we cannot confirm we are
    on basic — and 'cannot confirm' must mean 'do not write', never 'probably fine'."""
    for limits in ({}, None, {"weird": "shape"}, {"usage_tier": ""}, ["nope"]):
        client = _UpgradeClient()
        assert m._maybe_upgrade_api_tier(client, _Settings(True), limits) is None
        assert client.upgrades == 0, f"wrote to the account on an unreadable payload: {limits!r}"


def test_a_refused_upgrade_never_stops_the_bot():
    """403 = Kalshi's eligibility rule (>=1 of the last 100 orders placed via API) was not met.
    An account that cannot upgrade should keep trading on basic, not refuse to start."""
    client = _UpgradeClient(upgrade_exc=RuntimeError("403 forbidden"))
    assert m._maybe_upgrade_api_tier(client, _Settings(True), {"usage_tier": "basic"}) is None
    assert client.upgrades == 1


def test_an_upgrade_that_did_not_take_is_not_reported_as_success():
    """Accepted-but-unchanged is a real outcome: the scan would still be running against the old
    ceiling. Returning the payload keeps the STORED tier honest rather than optimistic."""
    client = _UpgradeClient(after="basic")
    after = m._maybe_upgrade_api_tier(client, _Settings(True), {"usage_tier": "basic"})
    assert after == {"usage_tier": "basic"}


def test_a_failed_re_read_leaves_the_tier_unconfirmed_rather_than_assumed():
    client = _UpgradeClient(read_exc=RuntimeError("network"))
    assert m._maybe_upgrade_api_tier(client, _Settings(True), {"usage_tier": "basic"}) is None


def test_auth_errors_propagate_from_the_upgrade_too():
    client = _UpgradeClient(upgrade_exc=AuthError("bad key"))
    with pytest.raises(AuthError):
        m._maybe_upgrade_api_tier(client, _Settings(True), {"usage_tier": "basic"})


def test_tier_of_reads_kalshis_actual_field_and_tolerates_renames():
    """`usage_tier` is what Kalshi sends (measured 2026-08-12); the rest are carried because the
    payload has been renamed before."""
    assert m._tier_of({"usage_tier": "BASIC"}) == "basic"
    assert m._tier_of({"tier": " Advanced "}) == "advanced"
    assert m._tier_of({"api_usage_level": "premier"}) == "premier"
    assert m._tier_of({"grants": [], "read": {"refill_rate": 200}}) == ""
    assert m._tier_of(None) == ""


def test_the_upgrade_switch_is_settable_from_the_ops_channel():
    """The worker is the only process with Kalshi credentials, so arming this from ops is the
    only way to fire it without a code deploy."""
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "railway_env.py"
    spec = importlib.util.spec_from_file_location("railway_env_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "KALSHI_UPGRADE_API_TIER" in mod.ALLOWED_VARS


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
