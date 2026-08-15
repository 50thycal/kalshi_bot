"""`_run_live_cycle` must actually invoke every ride-along book whose tracker it accepts.

Regression: `freeze_tracker` was threaded into BOTH `_run_weather_cycle`'s and `_run_live_cycle`'s
signatures in the same commit (580cf44, the FREEZE promotion), but the `_run_freeze_book(...)`
call was only added to `_run_weather_cycle`'s body — `_run_live_cycle` accepted the parameter and
silently dropped it. Production runs `BOT_MODE=live`, i.e. `_run_live_cycle`, so FREEZE placed
ZERO paper positions from the moment it was promoted (2026-08-13 22:37 UTC) until this was caught
2026-08-15 — the promoted book never actually ran on the path that matters.

This is a whole CLASS of bug (a tracker parameter accepted but never called), not just a freeze
bug, so this test parametrizes over every ride-along tracker `_run_live_cycle` accepts and asserts
each one's `run_once` fires exactly once per cycle. A future tracker added the same lopsided way
(wired into the signature, forgotten in the body) fails this test immediately instead of trading
zero positions silently for two days.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from kalshi_bot import db
from kalshi_bot import main as main_mod
from kalshi_bot.live.executor import LiveCycleSummary
from kalshi_bot.weather.tracker import WeatherCycleSummary


class _FakeClient:
    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_balance(self):
        return {"balance": 10_000}


class _FakeEngine:
    def manage_open_positions(self, session):
        pass


class _FakeExecutor:
    def __init__(self):
        self.summary = LiveCycleSummary()

    def reset_summary(self):
        self.summary = LiveCycleSummary()

    def reconcile(self, session, account_state):
        pass

    def manage_exits(self, session):
        pass

    def run_probe(self, session):
        pass

    def close_mmsell_positions(self, session):
        pass

    def close_theta_positions(self, session):
        pass


class _FakeWeatherTracker:
    """Stands in for the required, always-called weather tracker."""

    def run_once(self, session):
        return WeatherCycleSummary()


@dataclass
class _CallRecordingTracker:
    """A minimal ride-along tracker: records that `run_once` fired, and how many times."""

    calls: list = field(default_factory=list)

    def run_once(self, session):
        self.calls.append(session)
        return None


# Every ride-along tracker `_run_live_cycle` accepts, EXCLUDING the always-called weather
# tracker/engine/executor. If this list drifts from the function's actual parameters, the
# `test_every_ride_along_kwarg_is_covered` sweep below catches it.
RIDE_ALONG_KWARGS = (
    "mmsell_tracker", "theta_tracker", "xgame_tracker", "tfav_tracker",
    "wcprop_tracker", "pin15_tracker", "freeze_tracker",
)


def _run_cycle_with(settings, **ride_alongs):
    with db.session_scope():
        pass  # smoke-check the engine is initialized before the real call below
    main_mod._run_live_cycle(
        settings, _FakeClient(), _FakeEngine(), _FakeWeatherTracker(), _FakeExecutor(),
        **ride_alongs,
    )


_THROTTLE_STATE_ATTRS = (
    "_mmsell_last_run", "_theta_last_run", "_tfav_last_run", "_pin15_last_run",
    "_freeze_last_run", "_wcprop_last_run", "_xgame_last_run",
)


@pytest.fixture(autouse=True)
def _db(settings, monkeypatch):
    db.init_engine(settings.database_url)
    db.create_all()
    # Each ride-along runner is throttled against its own module-level "last run" timestamp,
    # compared with time.monotonic() (process-relative, NOT reset between tests) against an
    # interval that defaults as high as 30 minutes (mmsell). A test process is nowhere near that
    # old, so without control here every throttled runner silently no-ops on every call — not a
    # bug in the runners, just the wrong tool for a fast unit test. Pin monotonic time far past
    # any configured interval, and reset every runner's "last fired" state so one parametrized
    # case can never consume another's throttle window.
    monkeypatch.setattr(main_mod.time, "monotonic", lambda: 10**9)
    for attr in _THROTTLE_STATE_ATTRS:
        getattr(main_mod, attr)["ts"] = 0.0


@pytest.mark.parametrize("kwarg", RIDE_ALONG_KWARGS)
def test_ride_along_tracker_run_once_is_called_by_live_cycle(settings, kwarg):
    """The specific regression: a tracker passed into `_run_live_cycle` must be run. Freeze was
    the book that silently fell through this gap for two days in production."""
    tracker = _CallRecordingTracker()
    _run_cycle_with(settings, **{kwarg: tracker})
    assert len(tracker.calls) == 1, f"{kwarg}.run_once was not called by _run_live_cycle"


def test_every_ride_along_kwarg_is_covered(settings):
    """Guards the guard: if `_run_live_cycle` gains a new `*_tracker=None` kwarg that isn't added
    to RIDE_ALONG_KWARGS above, this fails loudly instead of the new tracker silently going
    untested (which is exactly how the freeze gap went unnoticed)."""
    import inspect

    params = inspect.signature(main_mod._run_live_cycle).parameters
    tracker_kwargs = {
        name for name, p in params.items()
        if name.endswith("_tracker") and p.default is None
    }
    assert tracker_kwargs == set(RIDE_ALONG_KWARGS), (
        "_run_live_cycle's tracker kwargs changed — update RIDE_ALONG_KWARGS above "
        f"(missing from test: {tracker_kwargs - set(RIDE_ALONG_KWARGS)}, "
        f"stale in test: {set(RIDE_ALONG_KWARGS) - tracker_kwargs})")


def test_none_tracker_is_a_safe_no_op(settings):
    """None (the default) must not be called — every ride_along runner already guards on
    `if tracker is None: return`; this just pins that the cycle itself doesn't assume otherwise."""
    _run_cycle_with(settings)  # no ride-alongs passed at all -> every kwarg defaults to None
