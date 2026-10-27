"""The 2026-10-12 football coverage revision, as falsifiable claims.

`docs/mmsell_taxonomy_repair/PLATFORM_IMPACT_20261012.md` is the review. This file pins the parts
a reviewer should not have to take on trust: every one of the 24 series classifies exactly as its
live subtitle says, the two deliberately deferred series stay unclassified, the revision is
additive (no prior prefix moves, no new type or mode name), the three copies of the table agree
byte for byte, and no added type reaches the live cell-size book's `bigtypes` sizing.
"""

from __future__ import annotations

import importlib.util
import pathlib

from kalshi_bot.mmsell.market_types import (
    DISCRETE,
    IN_PLAY,
    KNOWN_MODES,
    KNOWN_TYPES,
    SCHEDULED,
    SERIES_TYPES,
    UNCLASSIFIED,
    classify,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
WORKER_PATH = REPO / "kalshi_bot" / "mmsell" / "market_types.py"
OPS_PATH = REPO / "scripts" / "mmsell_market_types.py"
REVIEW_PATH = REPO / "scripts" / "mmsell_universe_review.py"

BLOCK_MARK = "# ===== 2026-10-12 FOOTBALL COVERAGE (MARKET_TAXONOMY football_coverage_2026_10_12) ====="

#: Every series the revision classifies, with the classification its live subtitle supports.
FOOTBALL = {
    "KXNCAAFGAME": "h2h",
    "KXNCAAFSPREAD": "spread",
    "KXNCAAFTOTAL": "total",
    "KXNCAAFTEAMTOTAL": "total",
    "KXNCAAF1H": "h2h_period",
    "KXNCAAF1HSPREAD": "spread",
    "KXNCAAF1HTOTAL": "total",
    "KXNCAAF2HSPREAD": "spread",
    "KXNCAAF1QSPREAD": "spread",
    "KXNFL1HSPREAD": "spread",
    "KXNFL2HSPREAD": "spread",
    "KXNFL1QSPREAD": "spread",
    "KXNFL3QSPREAD": "spread",
    "KXNFL4QSPREAD": "spread",
    "KXNFLWINMARGIN": "spread",
    "KXNFLTEAMTOTAL": "total",
    "KXNFL1HTEAMTOTAL": "total",
    "KXNFL1QTOTAL": "total",
    "KXNFL2HTOTAL": "total",
    "KXNFLFIRSTTD": "player_prop",
    "KXNFLPASSTDS": "player_prop",
    "KXNFLREC": "player_prop",
    "KXNFLRECYDS": "player_prop",
    "KXNFLRRYDS": "player_prop",
}

#: Kmmsell10 (LIVE canary, `mmsell-price-ceiling-cell-size`) rests a 3-lot on these types.
CELL_SIZE_BIGTYPES = {"event_stat", "mention", "exact_score", "outright", "game_prop"}


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"_m_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _block(path: pathlib.Path) -> str:
    src = path.read_text()
    i = src.index(BLOCK_MARK)
    return src[i:src.index("\n)\n", i)]


def test_every_football_series_classifies_as_its_live_subtitle_says():
    assert len(FOOTBALL) == 24
    for series, mtype in FOOTBALL.items():
        assert classify(f"{series}-26OCT11BALATL-X") == (mtype, IN_PLAY), series


def test_the_two_deferred_series_stay_unclassified():
    """KXNFL1HFT is a half-time/full-time double — a `game_prop`, which the live cell-size book
    sizes up. KXNFL1H would swallow it as an `h2h_period` by longest-prefix match. Both wait for
    their own decision; an unclassified series is admitted by no type or mode filter."""
    assert classify("KXNFL1HFT-26OCT11BALATL-ATLTIE") == UNCLASSIFIED
    assert classify("KXNFL1H-26OCT11BALATL-TIE") == UNCLASSIFIED


def test_no_added_type_reaches_the_live_cell_size_sizing():
    for series, mtype in FOOTBALL.items():
        assert mtype not in CELL_SIZE_BIGTYPES, series


def test_the_revision_is_additive():
    """No prefix that classified before moves, and no new type or mode NAME appears — so every
    existing book spec validates bit-for-bit as before."""
    assert KNOWN_MODES == {IN_PLAY, SCHEDULED, DISCRETE}
    assert {t for t in FOOTBALL.values()} <= KNOWN_TYPES
    assert len(KNOWN_TYPES) == 15
    pre_existing = {
        "KXNFLSPREAD": ("spread", IN_PLAY), "KXNFLTOTAL": ("total", IN_PLAY),
        "KXNFLGAME": ("h2h", IN_PLAY), "KXNFLTD": ("player_prop", IN_PLAY),
        "KXNFLPASSYDS": ("player_prop", IN_PLAY), "KXNFLRSHYDS": ("player_prop", IN_PLAY),
        "KXNFL1HTOTAL": ("total", IN_PLAY), "KXUECLTOTAL": ("total", IN_PLAY),
        "KXUE": ("econ_release", SCHEDULED),
    }
    for prefix, expected in pre_existing.items():
        assert classify(f"{prefix}-26OCT11-X") == expected, prefix
    prefixes = [p for p, _, _ in SERIES_TYPES]
    # 388 at the football revision; +1 KXTRUMPMENTION (MARKET_TAXONOMY trumpmention_2026_10_27)
    assert len(prefixes) == len(set(prefixes)) == 389


def test_specific_entries_still_win_under_the_new_broad_prefixes():
    """KXNFLREC sits over KXNFLRECYDS and KXNCAAF1H over its spread/total siblings: the longer
    entry must keep its own tickers."""
    assert classify("KXNFLRECYDS-26OCT12BUFLAR-X-40") == ("player_prop", IN_PLAY)
    assert classify("KXNCAAF1HSPREAD-26OCT10BSUFRES-BSU14") == ("spread", IN_PLAY)
    assert classify("KXNCAAF1HTOTAL-26OCT09ISUBYU-25") == ("total", IN_PLAY)


def test_the_three_copies_are_one_table():
    ops = _load(OPS_PATH)
    review = _load(REVIEW_PATH)
    assert ops.SERIES_TYPES == SERIES_TYPES
    assert review.SERIES_TYPES == SERIES_TYPES
    assert _block(WORKER_PATH) == _block(OPS_PATH)
