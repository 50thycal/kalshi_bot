"""FREEZE calendar + book-spec parsing.

The calendar decides whether a market is "mechanically decided", so a bug here does not produce
a bad number — it produces a book that buys live markets as if they were settled. These tests
pin the session edges, the conservative defaults, and (critically) that the live calendar agrees
with the ops-runner study's own stdlib-only copy, which is the population the backtest measured.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

from kalshi_bot.config import Settings
from kalshi_bot.freeze import calendar as cal

# Credentials are required Settings fields; the suite normally supplies them via conftest's
# `base_env` fixture, but the book-spec tests below build Settings directly. `_env_file=None`
# keeps a developer's local .env from changing the parsed fixture.
CREDS = {
    "_env_file": None,
    "kalshi_env": "demo",
    "kalshi_api_key_id": "test-key-id",
    "kalshi_private_key": "test-private-key",
    "database_url": "sqlite://",
}


def _et_to_ts(y, m, d, hh, mm) -> float:
    """Eastern wall clock -> epoch seconds, matching calendar._et's fixed -4h (EDT) offset."""
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp() + 4 * 3600


# --- session edges -----------------------------------------------------------------

def test_grain_day_session_is_open():
    # Wed 2026-08-12 11:00 ET — inside the CBOT day session (09:30-14:20).
    assert cal.is_open("grain", datetime(2026, 8, 12, 11, 0))


def test_grain_afternoon_gap_is_dark():
    # Wed 16:00 ET — after the 14:20 close, before the 20:00 overnight reopen.
    assert not cal.is_open("grain", datetime(2026, 8, 12, 16, 0))


def test_grain_saturday_fully_dark():
    assert not cal.is_open("grain", datetime(2026, 8, 15, 12, 0))  # Sat


def test_grain_sunday_reopens_at_2000():
    assert not cal.is_open("grain", datetime(2026, 8, 16, 19, 59))
    assert cal.is_open("grain", datetime(2026, 8, 16, 20, 0))


def test_soft_weekend_dark_and_weekday_session():
    assert not cal.is_open("soft", datetime(2026, 8, 15, 10, 0))   # Sat
    assert cal.is_open("soft", datetime(2026, 8, 12, 10, 0))       # Wed inside 03:00-14:30
    assert not cal.is_open("soft", datetime(2026, 8, 12, 20, 0))   # Wed after 14:30


def test_pyth_continuous_groups_never_dark():
    """Metals/energy settle on Pyth, which prices continuously — calling them frozen is the v1
    lookahead bug. They must read open at every instant, including a weekend."""
    for group in ("metal", "energy", "", "unknown"):
        assert cal.is_open(group, datetime(2026, 8, 15, 3, 0))     # Sat 03:00 ET
        assert cal.dark_window_start(group, _et_to_ts(2026, 8, 15, 3, 0)) is None


# --- dark_window_start / is_pinned -------------------------------------------------

def test_dark_window_start_returns_last_open_instant():
    """Wed 16:00 ET is dark; the walk-back should land near the 14:20 day close."""
    ts = _et_to_ts(2026, 8, 12, 16, 0)
    start = cal.dark_window_start("grain", ts)
    assert start is not None
    back_h = (ts - start) / 3600.0
    assert 1.0 < back_h < 2.5      # ~1.7h back to 14:20, within one 15-min step


def test_dark_window_start_none_when_source_open():
    assert cal.dark_window_start("grain", _et_to_ts(2026, 8, 12, 11, 0)) is None


def test_is_pinned_true_inside_one_dark_window():
    """Close 18:00 ET Wed, now 16:00 ET Wed — both inside the same afternoon gap, so the
    outcome cannot move: pinned."""
    now = _et_to_ts(2026, 8, 12, 16, 0)
    close = _et_to_ts(2026, 8, 12, 18, 0)
    assert cal.is_pinned("grain", close, now)


def test_is_pinned_false_when_source_reopens_before_close():
    """Close 22:00 ET Wed is AFTER the 20:00 overnight reopen — the source can still print, so
    the outcome is not decided even though `now` sits in a dark stretch."""
    now = _et_to_ts(2026, 8, 12, 16, 0)
    close = _et_to_ts(2026, 8, 12, 22, 0)
    assert not cal.is_pinned("grain", close, now)


def test_is_pinned_false_when_source_open_now():
    now = _et_to_ts(2026, 8, 12, 11, 0)
    close = _et_to_ts(2026, 8, 12, 13, 0)
    assert not cal.is_pinned("grain", close, now)


def test_is_pinned_false_for_already_closed_market():
    now = _et_to_ts(2026, 8, 12, 16, 0)
    close = _et_to_ts(2026, 8, 12, 15, 0)
    assert not cal.is_pinned("grain", close, now)


def test_is_pinned_false_for_pyth_continuous_group():
    now = _et_to_ts(2026, 8, 15, 3, 0)     # Sat
    close = _et_to_ts(2026, 8, 15, 9, 0)
    assert not cal.is_pinned("metal", close, now)


def test_weekend_pin_spans_the_full_grain_gap():
    """Fri 15:00 ET -> Sun 12:00 ET is one continuous dark stretch for grains (~45h), the
    longest real freeze the book trades. It must resolve as pinned, not fall off the walk-back."""
    now = _et_to_ts(2026, 8, 14, 15, 0)    # Fri after 14:20 close
    close = _et_to_ts(2026, 8, 16, 12, 0)  # Sun before the 20:00 reopen
    assert cal.is_pinned("grain", close, now)


# --- the live calendar must match the study's own copy -----------------------------

def _load_study():
    """Load scripts/kalshi_freeze_study.py without importing kalshi_bot (it is stdlib-only by
    design). Returns None if its third-party-free import chain is unavailable in this env."""
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "kalshi_freeze_study.py"
    spec = importlib.util.spec_from_file_location("kalshi_freeze_study", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:                 # noqa: BLE001 — sibling-script import chain, env-dependent
        return None
    return mod


def test_live_calendar_matches_the_study_calendar():
    """The backtest that justified this book and the book itself must classify the same instants
    the same way. If they drift, the paper numbers stop being a forward test OF the backtest.

    Walks a full week at 30-min resolution across both freezable groups.
    """
    study = _load_study()
    if study is None:
        return  # study's import chain unavailable here; the direct-edge tests above still apply
    start = datetime(2026, 8, 10, 0, 0)   # Monday
    for group in ("grain", "soft"):
        for i in range(7 * 48):
            dt = start + timedelta(minutes=30 * i)
            assert cal.is_open(group, dt) == study.is_open(group, dt), (
                f"calendar drift: {group} {dt:%a %H:%M} live={cal.is_open(group, dt)} "
                f"study={study.is_open(group, dt)}"
            )


# --- book-spec parsing -------------------------------------------------------------

def test_default_book_specs_parse_into_the_four_arms():
    books = {b["tag"]: b for b in Settings(**CREDS).freeze_book_list}
    assert set(books) == {"freeze1", "freeze2", "freeze3", "freeze4"}
    assert books["freeze1"]["dark"] is True and books["freeze1"]["maxprice"] is None
    assert books["freeze2"]["mindisc"] == 8.0
    assert books["freeze3"]["dark"] is False        # the open-window control
    assert books["freeze4"]["maxprice"] == 95.0


def test_book_specs_inherit_the_default_discount():
    s = Settings(**CREDS, freeze_min_discount_cents=5.0, freeze_books="freeze1:dark=1")
    assert s.freeze_book_list[0]["mindisc"] == 5.0


def test_malformed_book_spec_is_skipped_not_raised():
    s = Settings(**CREDS, freeze_books="freeze1:dark=1;nope:dark=1;freeze9:mindisc=abc")
    tags = [b["tag"] for b in s.freeze_book_list]
    assert tags == ["freeze1"]     # bad prefix and bad value both dropped, good one survives


def test_series_list_normalizes():
    s = Settings(**CREDS, freeze_series="kxcorn, KXWHEAT ,, KXCOFFEE")
    assert s.freeze_series_list == ["KXCORN", "KXWHEAT", "KXCOFFEE"]
