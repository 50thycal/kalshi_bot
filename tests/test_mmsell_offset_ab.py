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


# ------------------------------------------- two-book form (mmsell10a / mmsell10b)

from kalshi_bot.live.sizing import arm_book_offset  # noqa: E402


def _settings(**kw):
    from kalshi_bot.config import Settings
    return Settings(kalshi_api_key_id="x", kalshi_private_key="y",
                    database_url="sqlite://", **kw)


#: The two arm books, as the experiment configured them. They were removed from the
#: `mmsell_variants` DEFAULT on 2026-08-28 (retired: the 1c offset was KILLed on
#: 2026-08-14, and they lingered as paper books registered to no Experiment OS
#: deployment arm). The `abarm` MECHANISM they exercised is still live code — any
#: future arm book inherits it — so these tests keep proving it, on books they
#: declare themselves rather than on a default that no longer carries them.
_AB_BOOKS = ("mmsell10a:lo=5,hi=10,maxyes=7,abarm=0,size=1;"
             "mmsell10b:lo=5,hi=10,maxyes=7,abarm=1,size=1")


def _ab_settings(**kw):
    """`_settings`, with the retired arm books declared explicitly."""
    s = _settings(**kw)
    return _settings(mmsell_variants=f"{s.mmsell_variants};{_AB_BOOKS}", **kw)


def test_arm_books_partition_every_ticker_exactly_once():
    """The two live books must never contest a ticker. `live_open_order_exists` is strategy-
    AGNOSTIC, so a contested ticker would be decided by book evaluation order, not at random —
    which would make the A/B a race instead of an experiment."""
    for i in range(500):
        tk = f"KXMLBTOTAL-26AUG{i:04d}-T8.5"
        a = arm_book_offset(tk, 0, arms=(0, 1), salt="s1")
        b = arm_book_offset(tk, 1, arms=(0, 1), salt="s1")
        assert (a is None) != (b is None), f"{tk} was contested or orphaned"


def test_arm_books_split_roughly_evenly():
    tks = [f"KX-{i}" for i in range(2000)]
    a = sum(1 for t in tks if arm_book_offset(t, 0, arms=(0, 1), salt="s1") is not None)
    assert 900 < a < 1100


def test_arm_book_offset_is_its_declared_arms_offset():
    # whichever book claims the ticker must price at ITS arm's offset, never the other's
    for i in range(200):
        tk = f"KX-{i}"
        a = arm_book_offset(tk, 0, arms=(0, 1), salt="s1")
        b = arm_book_offset(tk, 1, arms=(0, 1), salt="s1")
        assert a in (0, None) and b in (1, None)


def test_arm_book_claims_nothing_when_experiment_is_off():
    """Fail CLOSED: an arm book has no defined price unless the experiment is running, so with no
    arms configured it must trade nothing rather than silently fall back to the default offset."""
    assert arm_book_offset("KX-1", 0, arms=(), salt="s1") is None
    assert arm_book_offset("KX-1", None, arms=(0, 1), salt="s1") is None


def test_configured_arm_books_are_mmsell10_plus_a_treatment():
    s = _ab_settings(mmsell_live_offset_ab_arms="0,1")
    books = {b["tag"]: b for b in s.mmsell_variant_list}
    base = books["mmsell10"]
    for tag, arm in (("mmsell10a", 0), ("mmsell10b", 1)):
        b = books[tag]
        # identical ENTRY to mmsell10 -- the offset must be the only difference
        assert (b["lo"], b["hi"], b["maxyes"]) == (base["lo"], base["hi"], base["maxyes"])
        assert b["abarm"] == arm
        assert b["size"] == 1


def test_incumbent_mmsell10_is_not_an_arm_book():
    """mmsell10 must keep trading exactly as before: no arm, no per-book size override."""
    s = _settings(mmsell_live_offset_ab_arms="0,1")
    base = next(b for b in s.mmsell_variant_list if b["tag"] == "mmsell10")
    assert base["abarm"] is None and base["size"] is None


def test_each_arm_book_gets_its_own_paper_twin():
    s = _settings(live_strategies="mmsell10,mmsell10a,mmsell10b")
    pairs = dict(s.live_paper_twin_pairs)
    assert pairs["mmsell10a"] == "mmsell10a_pt"
    assert pairs["mmsell10b"] == "mmsell10b_pt"
    assert pairs["mmsell10"] == "mmsell10_pt"


def test_tracker_admits_only_its_own_arm():
    from kalshi_bot.mmsell.tracker import MmSellTracker
    s = _ab_settings(mmsell_live_offset_ab_arms="0,1")
    t = MmSellTracker(client=None, settings=s)
    books = {b["tag"]: b for b in s.mmsell_variant_list}
    claimed = {"mmsell10a": 0, "mmsell10b": 0}
    for i in range(200):
        tk = f"KX-{i}"
        hits = [tag for tag in claimed if t._book_admits_ticker(books[tag], tk)]
        assert len(hits) == 1
        claimed[hits[0]] += 1
    assert all(v > 60 for v in claimed.values())
    # the incumbent and every non-arm book still admit everything: only a book that
    # DECLARES an `abarm` is partitioned, so the arms can never starve the rest of the
    # cohort of candidates. (mmsell3 stood here until it was retired 2026-08-12;
    # mmsell9 is the surviving non-arm variant that makes the same point.)
    assert t._book_admits_ticker(books["mmsell10"], "KX-1") is True
    assert t._book_admits_ticker(books["mmsell9"], "KX-1") is True
