"""Source-exchange trading calendar for the FREEZE book — when is the underlying dark?

The whole thesis rests on this file being CONSERVATIVE. A market is only worth trading as a
"pinned" contract if its settlement source genuinely cannot print again before the contract's
window ends. Every hour we wrongly call dark is a market we buy as decided when it is still live,
which is the single failure mode `docs/FREEZE_THESIS.md` P5 makes promotion-blocking.

So `is_open()` errs WIDE: when in doubt, the source is open (=> no trade). Being wrong in that
direction costs a missed entry; being wrong the other way costs a wrong pin.

Only **grains and softs** can freeze. Kalshi's commodity hub settles on Pyth, which prices metals
and energy continuously (~24/5 OTC) — for those the exchange "closure" is not a real information
freeze, so they are never dark here. That distinction is load-bearing: treating a Pyth-continuous
market as frozen is exactly the v1 probe bug that manufactured a fake +15.82¢ edge
(`docs/RESEARCH_JOURNAL.md`, 2026-07-11).

`scripts/kalshi_freeze_study.py` carries its own copy of this calendar because ops-runner scripts
are deliberately self-contained (stdlib only, no `kalshi_bot` import). `tests/test_freeze_calendar.py`
pins the two implementations to the same answers so the backtest and the live book can never drift
apart silently.

All times are US/Eastern wall clock, which is how the exchanges publish their sessions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Groups whose source genuinely goes dark. Anything else is Pyth-continuous -> never pinned.
FREEZE_GROUPS = frozenset({"grain", "soft"})

# How far back `dark_window_start` will walk looking for the last open instant. Sized for the
# longest real dark stretch (Fri 14:20 ET -> Sun 20:00 ET is ~54h) with headroom.
_MAX_BACK_HOURS = 80
_STEP_MINUTES = 15


def _et(ts: float) -> datetime:
    """UTC epoch seconds -> US/Eastern wall clock.

    Uses a fixed -4h (EDT) offset rather than a tz database so this matches the ops-runner
    script's stdlib-only implementation exactly. The cost is one hour of drift across the
    Nov/Mar DST boundaries; `is_open` carries far more than an hour of conservatism at every
    session edge, so a DST-boundary hour never flips a market from open to dark.
    """
    return datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=4)


def is_open(group: str, dt: datetime) -> bool:
    """Is the settlement source for `group` printing at Eastern wall-clock `dt`?

    Sessions (deliberately generous — see the module docstring):
      grain (CBOT): day 09:30-14:20, overnight 20:00-08:45, Sun 20:00 reopen, Sat fully closed.
      soft  (ICE):  a single generous union day session 03:00-14:30, weekends closed.
    """
    wd = dt.weekday()  # Mon=0 .. Sun=6
    hm = dt.hour * 60 + dt.minute
    if group == "grain":
        if wd == 5:            # Saturday: fully closed
            return False
        if wd == 6:            # Sunday: overnight session reopens 20:00 ET
            return hm >= 20 * 60
        if wd == 4:            # Friday: overnight until 08:45, day session, then dark all weekend
            return hm < 8 * 60 + 45 or (9 * 60 + 30 <= hm < 14 * 60 + 20)
        return (hm < 8 * 60 + 45 or hm >= 20 * 60
                or (9 * 60 + 30 <= hm < 14 * 60 + 20))
    if group == "soft":
        if wd >= 5:            # weekend dark
            return False
        return 3 * 60 <= hm < 14 * 60 + 30
    # metals / energy / anything unclassified: Pyth-continuous -> never dark.
    return True


def dark_window_start(group: str, ts: float) -> float | None:
    """If `ts` is inside a dark window for `group`, return the epoch seconds of the window's
    start (i.e. the last instant the source could still print). Returns None when the source is
    open at `ts`, or when the walk-back exhausts without finding an open instant.

    Returning None on exhaustion is the conservative choice: an unexplained multi-day dark
    stretch is more likely a calendar bug than a real freeze, and None means "do not trade".
    """
    if group not in FREEZE_GROUPS:
        return None
    if is_open(group, _et(ts)):
        return None
    step = _STEP_MINUTES * 60
    for i in range(1, int(_MAX_BACK_HOURS * 60 / _STEP_MINUTES) + 1):
        probe = ts - i * step
        if is_open(group, _et(probe)):
            return probe
    return None


def is_pinned(group: str, close_ts: float, now_ts: float) -> bool:
    """True when a contract closing at `close_ts` is mechanically decided as of `now_ts`.

    Both instants must sit inside the SAME dark window: the source's last print happened before
    now, and it cannot print again before the contract's window ends. If either is open, or they
    fall in different dark windows (i.e. the source reopens in between and can still move the
    outcome), this is False.
    """
    if group not in FREEZE_GROUPS:
        return False
    if close_ts < now_ts:
        return False
    start_now = dark_window_start(group, now_ts)
    if start_now is None:
        return False
    start_close = dark_window_start(group, close_ts)
    if start_close is None:
        return False
    # Same window <=> same last-print instant (both walked back to the same open edge).
    return abs(start_now - start_close) < _STEP_MINUTES * 60
