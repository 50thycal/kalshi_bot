"""A book may size by MARKET TYPE: `bigtypes=...,bigsize=3,size=1`.

WHY THIS EXISTS. The random 1-vs-3 split (`sizes=1+3`, docs/MMSELL_SIZE_SPLIT_CANARY.md) showed
the clip is executable (every 3-lot filled completely) and that live edge is not spread evenly:
the slow-information cell earned +7.05c/contract on 25 settled markets with no loss while the
rest ran -2.80c. The `Kmmsell10` canary (docs/MMSELL_CELL_SIZE_CANARY.md) therefore sizes up
only that cell. What must never break:

  * **a book without `bigtypes` is untouched** — same count, same twin snapshot.
  * **the count is decided by the ticker's series type alone**, so live, the retry path, the
    twin and the parity tape agree, and an unclassified series is never sized up.
  * **a malformed or contradictory spec is REFUSED, not defaulted.**
"""

from __future__ import annotations

from kalshi_bot.mmsell.market_types import classify

SLOW = ("event_stat", "mention", "exact_score", "outright", "game_prop")
SPEC = ("Kmmsell10:lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,size=1,"
        "bigtypes=event_stat+mention+exact_score+outright+game_prop,bigsize=3")


def _books(settings, variants):
    settings.mmsell_variants = variants
    return {b["tag"]: b for b in settings.mmsell_variant_list}


def _tracker(settings):
    from kalshi_bot.mmsell.tracker import MmSellTracker

    return type("T", (), {
        "settings": settings,
        "_book_contracts": MmSellTracker._book_contracts,
    })()


def _series_of_type(mtype: str) -> str:
    from kalshi_bot.mmsell.market_types import SERIES_TYPES

    return next(p for p, t, _ in SERIES_TYPES if t == mtype)


# --- the config spec -----------------------------------------------------------

def test_the_canary_spec_parses(settings):
    b = _books(settings, SPEC)["Kmmsell10"]
    assert sorted(b["bigtypes"]) == sorted(SLOW)
    assert b["bigsize"] == 3 and b["size"] == 1
    assert b["sizes"] is None
    assert (b["contestcap"], b["contestkey"]) == (1, "split")


def test_a_book_without_bigtypes_is_unchanged(settings):
    b = _books(settings, "mmsellX:lo=5,hi=10,size=1")["mmsellX"]
    assert b["bigtypes"] == [] and b["bigsize"] is None


def test_a_malformed_or_contradictory_type_size_is_refused(settings):
    for bad in ("bigsize=3", "bigtypes=mention", "bigtypes=notatype,bigsize=3",
                "bigtypes=mention,bigsize=0", "bigtypes=mention,bigsize=11",
                "bigtypes=mention,bigsize=3,sizes=1+3"):
        assert _books(settings, f"mmsellBad:lo=5,hi=10,{bad}") == {}, bad


# --- what the tracker sends ------------------------------------------------------

def test_slow_cell_tickers_get_the_big_clip_and_the_rest_the_book_size(settings):
    book = _books(settings, SPEC)["Kmmsell10"]
    t = _tracker(settings)
    for mtype in SLOW:
        ticker = f"{_series_of_type(mtype)}-26OCT11-X"
        assert classify(_series_of_type(mtype))[0] == mtype
        assert t._book_contracts(book, ticker) == (1, 3), mtype
    for mtype in ("h2h", "total", "spread", "player_prop", "price_strike"):
        ticker = f"{_series_of_type(mtype)}-26OCT11-X"
        assert t._book_contracts(book, ticker) == (0, 1), mtype


def test_an_unclassified_series_is_never_sized_up(settings):
    book = _books(settings, SPEC)["Kmmsell10"]
    assert _tracker(settings)._book_contracts(book, "KXNOSUCHSERIESEVER-26OCT11-X") == (0, 1)


def test_a_book_without_bigtypes_keeps_its_size_and_has_no_arm(settings):
    t = _tracker(settings)
    assert t._book_contracts({"size": 1, "bigtypes": [], "bigsize": None},
                             "KXMLBTOTAL-26OCT11-X") == (None, 1)


# --- the twin epoch snapshot ---------------------------------------------------

def _twin_params(settings, book):
    from kalshi_bot.mmsell.tracker import MmSellTracker

    stub = type("T", (), {
        "settings": settings,
        "twin_harness": type("H", (), {"max_open_positions": staticmethod(lambda cap: cap)})(),
        "_twin_params": MmSellTracker._twin_params,
    })()
    return stub._twin_params(book)


def test_a_type_sized_twin_records_its_rule(settings):
    book = _books(settings, SPEC)["Kmmsell10"]
    book["twin_of"] = "Kmmsell10"
    params = _twin_params(settings, book)
    assert params["bigtypes"] == sorted(SLOW)
    assert params["bigsize"] == 3 and params["size"] == 1


def test_a_running_size_split_twin_snapshot_gains_no_new_keys(settings):
    """Jmmsell10_pt4's book (`sizes=1+3`) must not gain keys on the deploy that ships this, or
    the running twin reports param drift."""
    book = _books(settings, "Jmmsell10:lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,"
                            "sizes=1+3")["Jmmsell10"]
    book["twin_of"] = "Jmmsell10"
    params = _twin_params(settings, book)
    for k in ("bigtypes", "bigsize"):
        assert k not in params
