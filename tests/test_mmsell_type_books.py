"""The market-type book families (Wmmsell*/Tmmsell*, docs/MMSELL_TYPE_BOOKS.md).

These books select candidates by CONTRACT STRUCTURE instead of by series substring, which is a
new filter path through config -> tracker. The tests concentrate on the ways it could silently
trade the WRONG SET rather than fail loudly:

  * the worker's taxonomy is a deliberate duplicate of the ops script's and must not drift —
    if it does, the census scores a book on a different definition than the book traded;
  * a misspelled type/mode must reject the spec, not produce a book that admits nothing and
    reads as selectivity at n=0 forever;
  * an unclassified series must be admitted by NO type/mode filter, so a newly listed contract
    is never swept into a book that did not ask for it;
  * the type filter must compose correctly with the pre-existing only=/skip= series filters;
  * books with no type keys (the controls, every legacy variant) must be completely unaffected;
  * the tags must survive every downstream "is this an mmsell book" check — the prefix-based
    ones would have excluded Wmmsell*/Tmmsell* from hold-to-settlement, which would silently
    make them a different experiment from the controls they are read against.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from kalshi_bot.config import Settings
from kalshi_bot.mmsell.market_types import DISCRETE, IN_PLAY, SCHEDULED, classify
from kalshi_bot.mmsell.tracker import MmSellTracker


def _settings(**over):
    base = dict(_env_file=None, kalshi_api_key_id="k", kalshi_private_key="p",
                database_url="sqlite://", bot_mode="weather")
    base.update(over)
    return Settings(**base)


def _books(**over):
    return {b["tag"]: b for b in _settings(**over).mmsell_variant_list}


# ------------------------------------------------------------------ the duplicated taxonomy


def test_worker_taxonomy_matches_the_ops_script():
    """kalshi_bot/mmsell/market_types.py duplicates scripts/mmsell_market_types.py because ops
    scripts run on a runner that never installs this package. If the two drift, a book trades
    one definition of 'in_play' while the census scores it under another."""
    scripts = pathlib.Path(__file__).resolve().parent.parent / "scripts"
    spec = importlib.util.spec_from_file_location(
        "mmsell_market_types", scripts / "mmsell_market_types.py")
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)

    from kalshi_bot.mmsell import market_types as worker

    assert worker.SERIES_TYPES == ops.SERIES_TYPES
    assert (worker.IN_PLAY, worker.SCHEDULED, worker.DISCRETE) == \
           (ops.IN_PLAY, ops.SCHEDULED, ops.DISCRETE)
    assert worker.UNCLASSIFIED == ops.UNCLASSIFIED
    # The classifiers must agree market-by-market, not just share a table.
    for series, _t, _m in worker.SERIES_TYPES:
        assert worker.classify(series) == ops.classify(series)


# ------------------------------------------------------------------ spec parsing / validation


def test_the_fourteen_type_books_are_configured():
    books = _books()
    for i in range(1, 9):
        assert f"Wmmsell{i}" in books, "wide-band type book missing"
    for i in range(1, 7):
        assert f"Tmmsell{i}" in books, "tight-band type book missing"


def test_wide_books_share_the_control_band_and_tight_books_the_mmsell10_band():
    """Each family differs from its control ONLY by the type filter — that is what makes the
    difference attributable to the type rather than to the band."""
    books = _books()
    for i in range(1, 9):
        b = books[f"Wmmsell{i}"]
        assert (b["lo"], b["hi"], b["maxyes"]) == (5.0, 40.0, None)
    for i in range(1, 7):
        b = books[f"Tmmsell{i}"]
        assert (b["lo"], b["hi"], b["maxyes"]) == (5.0, 10.0, 7.0)


def test_type_books_carry_no_anchor_mechanic():
    """A stop / vol gate / strangle would confound the type experiment with the anchor set's."""
    for tag, b in _books().items():
        if tag[0] in "WT":
            assert b["stopl"] is None and b["volw"] is None and b["strangle"] is False


def test_unknown_type_or_mode_rejects_the_whole_spec():
    # A typo'd allowlist admits NO market, so the book would sit at n=0 forever and read as
    # selectivity. It must never be constructed.
    assert "Xmmsell9" not in _books(mmsell_variants="Xmmsell9:mtype=playerprop")
    assert "Xmmsell9" not in _books(mmsell_variants="Xmmsell9:mode=inplay")
    assert "Xmmsell9" not in _books(mmsell_variants="Xmmsell9:xmtype=nonsense")
    # ...while a correctly spelled one is accepted.
    assert "Xmmsell9" in _books(mmsell_variants="Xmmsell9:mtype=player_prop")


def test_tag_may_carry_a_family_prefix_but_must_still_name_mmsell():
    assert "Wmmsell1" in _books(mmsell_variants="Wmmsell1:mode=in_play")
    assert "notabook" not in _books(mmsell_variants="notabook:mode=in_play")
    # The bare control tag can never be reparameterized by a variant spec.
    assert "mmsell" not in _books(mmsell_variants="mmsell:mode=in_play")


# ------------------------------------------------------------------ the admission filter

admits = MmSellTracker._book_admits_series


def test_books_without_type_keys_are_unaffected():
    """Every legacy variant and both controls must admit exactly what they did before."""
    plain = {"skip": [], "only": [], "mtype": [], "xmtype": [], "mode": []}
    for series in ("KXMLBGAME", "KXBTCD", "KXTRUMPSAY", "KXTOTALLYNEWSERIES"):
        assert admits(plain, series) is True


def test_mode_filter_selects_per_series_not_per_type():
    """`outright` is a MIXED-mode type: golf tournaments play out in-play, a Golden-Boot award
    resolves discretely. A mode filter resolves that per series, which is the whole point of
    carrying mode separately from type."""
    inplay = {"mode": [IN_PLAY]}
    assert admits(inplay, "KXPGATOUR") is True      # outright, but in_play
    assert admits(inplay, "KXWCAWARD") is False     # outright, but discrete
    assert admits(inplay, "KXMLBGAME") is True
    assert admits(inplay, "KXBTCD") is False        # scheduled


def test_mtype_allowlist_and_xmtype_blocklist():
    only_props = {"mtype": ["player_prop"]}
    assert admits(only_props, "KXWCGOAL") is True
    assert admits(only_props, "KXMLBGAME") is False

    no_h2h = {"xmtype": ["h2h", "total"]}
    assert admits(no_h2h, "KXMLBGAME") is False
    assert admits(no_h2h, "KXMLBTOTAL") is False
    assert admits(no_h2h, "KXWCGOAL") is True


def test_xmtype_wins_over_mode():
    """Wmmsell7/Tmmsell5 are 'no-clock, minus the known bleeders' — the blocklist is applied
    last precisely so it can carve losers out of an otherwise-admitted mode."""
    book = {"mode": [SCHEDULED, DISCRETE], "xmtype": ["event_stat", "politics", "announcement"]}
    assert admits(book, "KXBTCD") is True            # scheduled, kept
    assert admits(book, "KXTRUMPSAY") is True        # discrete, kept
    assert admits(book, "KXWCATTEND") is False       # scheduled but event_stat -> dropped
    assert admits(book, "KXPLATNERDROPOUT") is False  # discrete but politics -> dropped
    assert admits(book, "KXMLBGAME") is False        # in_play -> not in the mode allowlist


def test_unclassified_series_is_excluded_by_allowlists_but_not_by_blocklists():
    """The asymmetry is deliberate. An ALLOWLIST book asked for named structures, so a contract
    nobody has classified is not one of them. A BLOCKLIST book is defined as 'my control minus
    these named types' — dropping unknowns there would make it differ from its control by more
    than the thing under test, which is exactly what the comparison is trying to isolate."""
    assert classify("KXBRANDNEWTHING") == ("unclassified", "unknown")
    for book in ({"mtype": ["player_prop"]}, {"mode": [IN_PLAY]},
                 {"mode": [SCHEDULED, DISCRETE]}):
        assert admits(book, "KXBRANDNEWTHING") is False
    assert admits({"xmtype": ["h2h"]}, "KXBRANDNEWTHING") is True
    # ...and an unfiltered book (both controls) always takes it.
    assert admits({}, "KXBRANDNEWTHING") is True


def test_type_filter_composes_with_the_legacy_series_filters():
    book = {"only": ["MLB"], "mtype": ["total", "h2h"]}
    assert admits(book, "KXMLBTOTAL") is True
    assert admits(book, "KXMLBGAME") is True
    assert admits(book, "KXMLBHR") is False      # in `only`, wrong type
    assert admits(book, "KXWNBATOTAL") is False  # right type, not in `only`

    book = {"skip": ["WC"], "mtype": ["total"]}
    assert admits(book, "KXMLBTOTAL") is True
    assert admits(book, "KXWCTOTAL") is False    # right type, series skipped


@pytest.mark.parametrize("tag,series,expected", [
    ("Wmmsell1", "KXMLBGAME", True),     # in_play
    ("Wmmsell1", "KXBTCD", False),       # scheduled
    ("Wmmsell4", "KXBTCD", True),        # price_strike only
    ("Wmmsell4", "KXWCGOAL", False),
    ("Wmmsell5", "KXTRUMPSAY", True),    # mention only
    ("Wmmsell6", "KXMLBTOTAL", False),   # blocklist drops total
    ("Wmmsell6", "KXWCGOAL", True),
    ("Wmmsell7", "KXBTCD", True),        # no-clock
    ("Wmmsell7", "KXMLBGAME", False),
    ("Wmmsell8", "KXPGATOUR", True),     # outright is in the high-conviction set
    ("Tmmsell3", "KXMLBTOTAL", True),    # total is +EV in the TIGHT band only
    ("Tmmsell4", "KXMLBGAME", False),    # h2h blocked in the tight band
    ("Tmmsell6", "KXMLBTOTAL", False),   # total not a both-band survivor
    ("Tmmsell6", "KXWCGOAL", True),
])
def test_configured_books_admit_what_their_thesis_says(tag, series, expected):
    assert admits(_books()[tag], series) is expected


# ------------------------------------------------------------------ downstream tag handling


def test_type_book_tags_hold_to_settlement():
    """The engine's hold-to-settlement set was PREFIX-matched on 'mmsell'. Under that check a
    Wmmsell*/Tmmsell* position would have been force-closed by the global max-hold timeout —
    a silently different experiment from the control it is compared against."""
    import re
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "kalshi_bot" / "paper" / "engine.py").read_text()
    line = next(ln for ln in src.splitlines() if "no_timeout =" in ln)
    assert '"mmsell" in strat' in line
    assert not re.search(r'startswith\(\("mmsell', line)


def test_type_book_tags_are_resolvable_as_books():
    s = _settings()
    for tag in ("Wmmsell1", "Tmmsell6"):
        assert s.mmsell_book_by_tag(tag) is not None
    assert s.mmsell_book_by_tag("nosuchbook") is None


# ------------------------------------------------------------------ the start-up abandon sweep


def test_type_books_survive_the_foreign_position_sweep():
    """`abandon_open_paper_trades` clears books that are not part of the running experiment.
    It matched on a PREFIX, under which Wmmsell*/Tmmsell* looked foreign — so every worker
    start wiped their open positions, and because the entry dedup guard keys off an OPEN
    position each book re-entered the same market on the next cycle. Observed in production
    2026-08-04: Wmmsell6 held 9 markets behind 47 `abandoned` rows in under four hours.

    A book that cannot hold a position across a deploy can never reach its settled-n gate."""
    from kalshi_bot.repository import strategy_is_kept

    keep = ("weather", "mmsell", "theta")
    for tag in ("mmsell", "mmsell10", "mmsellA1", "Wmmsell1", "Wmmsell8",
                "Tmmsell1", "Tmmsell6", "mmsell10_pt"):
        assert strategy_is_kept(tag, keep) is True, f"{tag} would be abandoned on restart"
    for tag in ("pin15", "wcprop", "xgame", "tfav", None, ""):
        assert strategy_is_kept(tag, keep) is False
    # The substring rule is scoped to mmsell: it must not resurrect other families.
    assert strategy_is_kept("Wmmsell1", ("weather",)) is False
    assert strategy_is_kept("xtheta9", ("theta",)) is False
