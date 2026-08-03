"""Queue-position A/B: the arm-assignment hash (kalshi_bot/live/sizing) and the report
arithmetic (scripts/mmsell_offset_ab), pinned without a DB.

Two properties matter more than the exact hash values: assignment must be STABLE for a ticker
(so the entry-retry path can't flip a market between arms mid-life and blend two prices), and a
HOT entry must never be counted into an arm (it is priced by the momentum guard, so counting it
would measure the guard instead of queue position).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from kalshi_bot.live.sizing import maker_offset, offset_arm  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "mmsell_offset_ab", _ROOT / "scripts" / "mmsell_offset_ab.py")
ab = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ab)  # type: ignore[union-attr]


# --------------------------------------------------------------- arm assignment

def test_assignment_is_stable_for_a_ticker():
    a = offset_arm("KXMLBGAME-26AUG03NYYBOS-NYY", arms=(0, 1), salt="s1")
    b = offset_arm("KXMLBGAME-26AUG03NYYBOS-NYY", arms=(0, 1), salt="s1")
    assert a == b


def test_arm_index_maps_to_the_declared_offset():
    for tk in ("A", "B", "C", "D", "E"):
        idx, off = offset_arm(tk, arms=(0, 3), salt="s1")
        assert (0, 3)[idx] == off


def test_salt_change_rerandomizes():
    tickers = [f"KX-{i}" for i in range(200)]
    s1 = [offset_arm(t, arms=(0, 1), salt="s1")[0] for t in tickers]
    s2 = [offset_arm(t, arms=(0, 1), salt="s2")[0] for t in tickers]
    assert s1 != s2  # a new salt is a new experiment, not a continuation


def test_arms_are_roughly_balanced():
    tickers = [f"KXMLBTOTAL-26AUG{i:03d}-T8.5" for i in range(2000)]
    zero = sum(1 for t in tickers if offset_arm(t, arms=(0, 1), salt="s1")[0] == 0)
    assert 900 < zero < 1100  # ~50/50; a badly skewed hash would wreck the comparison


def test_offset_arm_rejects_empty_arms():
    try:
        offset_arm("KX-1", arms=(), salt="s")
    except ValueError:
        return
    raise AssertionError("empty arms must raise, not silently pick an offset")


# --------------------------------------------------------------- maker_offset

def test_hot_entry_is_excluded_from_the_experiment():
    off, arm = maker_offset("KX-1", hot=True, calm_offset=0, hot_offset=-3,
                            ab_arms=(0, 1), ab_salt="s1")
    assert off == -3 and arm is None


def test_experiment_off_reproduces_todays_behaviour():
    off, arm = maker_offset("KX-1", hot=False, calm_offset=0, hot_offset=-3)
    assert off == 0 and arm is None
    off, arm = maker_offset("KX-1", hot=False, calm_offset=2, hot_offset=-3, ab_arms=())
    assert off == 2 and arm is None


def test_experiment_on_uses_the_arm_offset():
    off, arm = maker_offset("KX-1", hot=False, calm_offset=0, hot_offset=-3,
                            ab_arms=(0, 1), ab_salt="s1")
    assert arm in (0, 1)
    assert off == (0, 1)[arm]


# --------------------------------------------------------------- report maths

def test_summarize_arm_counts_fills_and_cents_per_contract():
    # (filled, fill_px, qty, realized_dollars)
    rows = [
        (True, 92.0, 2, 0.10),     # settled winner: +10c over 2 contracts
        (True, 93.0, 1, -0.94),    # settled loser
        (True, 92.0, 1, None),     # filled but not settled yet -> excluded from P&L
        (False, None, None, None),  # never filled
    ]
    st = ab.summarize_arm(rows)
    assert st["orders"] == 4
    assert st["filled"] == 3
    assert st["fill_rate"] == 0.75
    assert st["settled_positions"] == 2
    assert st["settled_contracts"] == 3
    # (+10c + -94c) / 3 contracts
    assert round(st["cents_per_contract"], 4) == round((10.0 - 94.0) / 3, 4)


def test_summarize_arm_weights_fill_price_by_size():
    rows = [(True, 90.0, 1, None), (True, 94.0, 3, None)]
    st = ab.summarize_arm(rows)
    assert st["avg_fill_px"] == (90.0 * 1 + 94.0 * 3) / 4


def test_summarize_arm_handles_an_empty_arm():
    st = ab.summarize_arm([])
    assert st["orders"] == 0 and st["filled"] == 0
    assert st["cents_per_contract"] != st["cents_per_contract"]  # NaN, not a crash


def test_wilson_bounds_are_sane():
    lo, hi = ab.wilson(0, 68)
    assert lo == 0.0 and 0.0 < hi < 0.10
    lo, hi = ab.wilson(50, 100)
    assert lo < 0.5 < hi
