"""How the PERP-V1 collector is attached to the worker.

The collector's own behaviour is covered in `test_perps_collector.py`. This file
covers the wiring, because that is where a read-only instrument can still do
damage: by running in only some modes (a coverage gap nobody sees), by being on
when nobody asked, or by taking a trading cycle down with it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kalshi_bot import main as worker
from kalshi_bot.config import Settings
from kalshi_bot.kalshi.errors import AuthError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def _settings(**kw):
    return Settings(
        kalshi_api_key_id="k", kalshi_private_key="p", database_url="sqlite://", **kw
    )


class _Collector:
    def __init__(self, exc=None):
        self.runs = 0
        self._exc = exc

    def run_once(self, session, *, now=None):
        self.runs += 1
        if self._exc:
            raise self._exc


def test_the_collector_is_off_by_default():
    """Enabling it redeploys the worker, which also runs the live books. That is
    an operator act, so the default cannot be 'on'."""
    assert _settings().perps_collector_enabled is False


def test_the_hook_is_called_in_the_every_mode_position(monkeypatch):
    """It must sit beside `_experiment_os_cycle` in the loop body, NOT inside a
    BOT_MODE branch. `docs/EXPERIMENT_OS_FOUNDATION.md` records what a hook
    placed in `_run_cycle` cost: it silently never ran under live/weather/mmsell.
    """
    source = Path(worker.__file__).read_text()
    loop = source[source.index("        while True:"):]
    hook = loop.index("_experiment_os_cycle(settings)")
    perps = loop.index("_perps_cycle(")
    branch = loop.index("if live:")
    assert perps > hook
    assert perps < branch, "the perps hook must run before any BOT_MODE branch"


def test_a_disabled_collector_is_simply_absent(monkeypatch):
    monkeypatch.setattr(worker, "_perps_last_poll", None, raising=False)
    worker._perps_cycle(_settings(), object(), None)  # must not raise


def test_the_interval_throttles_polling(monkeypatch):
    """The collector rides the shared worker cycle, which is tuned for books; the
    poll floor is what keeps it from riding it every single time."""
    monkeypatch.setattr(worker, "_perps_last_poll", None, raising=False)
    sessions = []
    monkeypatch.setattr(worker, "session_scope", lambda: _NullSession(sessions))
    collector = _Collector()
    settings = _settings(perps_interval_seconds=3600)
    worker._perps_cycle(settings, object(), collector)
    worker._perps_cycle(settings, object(), collector)
    assert collector.runs == 1


def test_a_collector_failure_never_stops_a_trading_cycle(monkeypatch):
    monkeypatch.setattr(worker, "_perps_last_poll", None, raising=False)
    monkeypatch.setattr(worker, "session_scope", lambda: _NullSession([]))
    worker._perps_cycle(
        _settings(perps_interval_seconds=0), object(), _Collector(RuntimeError("boom"))
    )  # must not raise


def test_auth_failure_still_propagates(monkeypatch):
    """Fail-closed beats data collection: a bad credential affects every book in
    the process, not just this instrument."""
    monkeypatch.setattr(worker, "_perps_last_poll", None, raising=False)
    monkeypatch.setattr(worker, "session_scope", lambda: _NullSession([]))
    with pytest.raises(AuthError):
        worker._perps_cycle(
            _settings(perps_interval_seconds=0), object(), _Collector(AuthError("bad"))
        )


def test_every_perps_setting_is_reachable_through_the_env_channel():
    """A knob the ops `env` channel refuses is a knob that needs a code deploy to
    turn — and the collector's whole point is being adjustable against a measured
    request budget. #266 is the precedent for finding this out mid-operation."""
    from railway_env import ALLOWED_VARS

    fields = {f"PERPS_{n[len('perps_'):].upper()}"
              for n in Settings.model_fields if n.startswith("perps_")}
    assert fields, "expected perps_* settings to exist"
    assert fields <= ALLOWED_VARS, sorted(fields - ALLOWED_VARS)


class _NullSession:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        self._sink.append(self)
        return self

    def __exit__(self, *exc):
        return False
