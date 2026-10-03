"""A book may run a randomized SIZE split: `sizes=1+3` hashes each ticker to one contract count.

WHY THIS EXISTS. The Hmmsell10 canary (docs/MMSELL_SIZE_SPLIT_CANARY.md) measures what a bigger
clip actually does on real money: how often the extra contracts fill, what they earn, and what a
3-lot loss does to the envelope. Two books (`part=0/2,size=1` / `part=1/2,size=3`) would answer
it with two contest caps (two positions per game), two twins and the account-wide budgets
deciding which book scans first. One book with a per-ticker count keeps ONE of each, and the
split is still a coin flip per market.

What must never break, in the order in which breaking it would matter:

  * **a book without `sizes` is untouched** — same contract count, same twin snapshot. Every
    book running today declares no `sizes`.
  * **the count never raises a dollar figure.** The per-order dollar cap still binds on top, so
    `sizes=` cannot be used to walk around LIVE_MAX_ORDER_DOLLARS.
  * **the split is stable per ticker and independent of `part`.** A retry can never flip a
    market between 1 and 3 contracts, and a book declaring both keys still populates both arms.
  * **live, the retry path, the twin and the parity tape read ONE definition.**
  * **a malformed or contradictory spec is REFUSED, not defaulted.**
"""

from __future__ import annotations

import pytest

from kalshi_bot.live.sizing import order_quantity, ticker_partition, ticker_size

SALT = "mmsell-partition-v1"
TICKERS = [f"KXMLBTOTAL-26SEP{d:02d}NYYLAA-{k}" for d in range(1, 31) for k in range(1, 9)]


# --- the primitive -------------------------------------------------------------

def test_every_ticker_gets_one_of_the_declared_sizes_and_keeps_it():
    for t in TICKERS:
        idx, qty = ticker_size(t, (1, 3), salt=SALT)
        assert (idx, qty) in ((0, 1), (1, 3))
        assert {ticker_size(t, (1, 3), salt=SALT) for _ in range(5)} == {(idx, qty)}


def test_the_size_split_is_roughly_balanced():
    big = sum(1 for t in TICKERS if ticker_size(t, (1, 3), salt=SALT)[1] == 3)
    assert 0.35 < big / len(TICKERS) < 0.65


def test_the_size_split_is_independent_of_the_partition_on_the_same_salt():
    """Same base salt, different assignment: within EITHER half of a `part=i/2` split both size
    arms are populated. Hashing the size on the bare partition salt would make a book declaring
    both keys trade only one size."""
    for half in (0, 1):
        mine = [t for t in TICKERS if ticker_partition(t, n=2, salt=SALT) == half]
        assert {ticker_size(t, (1, 3), salt=SALT)[1] for t in mine} == {1, 3}


def test_a_one_size_split_is_refused():
    with pytest.raises(ValueError):
        ticker_size(TICKERS[0], (3,), salt=SALT)


def test_the_dollar_cap_still_binds_on_the_large_arm():
    """The count is a REQUEST. At the predecessor's $1.00 cap a 3-lot at 93c is floored to one
    contract; only a ceiling that clears 3 x price lets the third contract through."""
    assert order_quantity(93, 1.0, 3) == 1
    assert order_quantity(93, 3.0, 3) == 3
    assert order_quantity(97, 3.0, 3) == 3
    assert order_quantity(93, 3.0, 1) == 1


# --- the config spec -----------------------------------------------------------

def _books(settings, variants):
    settings.mmsell_variants = variants
    return {b["tag"]: b for b in settings.mmsell_variant_list}


def test_sizes_parses_and_leaves_every_other_knob_alone(settings):
    books = _books(settings, "Hmmsell10:lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,"
                             "sizes=1+3")
    b = books["Hmmsell10"]
    assert b["sizes"] == (1, 3)
    assert b["size"] is None and b["part"] is None and b["abarm"] is None
    assert (b["lo"], b["hi"], b["maxyes"]) == (5.0, 10.0, 7.0)
    assert (b["contestcap"], b["contestkey"]) == (1, "split")


def test_a_book_without_sizes_has_sizes_none(settings):
    assert _books(settings, "mmsellX:lo=5,hi=10,size=1")["mmsellX"]["sizes"] is None


def test_a_malformed_or_contradictory_size_split_is_refused(settings):
    for bad in ("sizes=3", "sizes=1+0", "sizes=1+11", "sizes=a+3", "sizes=1+-3",
                "sizes=1+3,size=1"):
        assert _books(settings, f"mmsellBad:lo=5,hi=10,{bad}") == {}


# --- what the tracker sends ------------------------------------------------------

def _tracker(settings):
    from kalshi_bot.mmsell.tracker import MmSellTracker

    return type("T", (), {
        "settings": settings,
        "_book_contracts": MmSellTracker._book_contracts,
    })()


def test_a_size_split_book_asks_for_the_hashed_count(settings):
    settings.mmsell_live_partition_salt = SALT
    t = _tracker(settings)
    for ticker in TICKERS:
        assert t._book_contracts({"sizes": (1, 3)}, ticker) == ticker_size(ticker, (1, 3),
                                                                           salt=SALT)


def test_a_book_without_sizes_keeps_its_size_and_has_no_arm(settings):
    """The regression guard that matters at merge: Fmmsell10 (`size=1`) and every paper book."""
    t = _tracker(settings)
    assert t._book_contracts({"size": 1}, TICKERS[0]) == (None, 1)
    assert t._book_contracts({}, TICKERS[0]) == (None, None)
    assert t._book_contracts(None, TICKERS[0]) == (None, None)


def test_every_sizing_call_site_reads_the_one_definition():
    """Live mirror, retry path and the twin/parity helper must all size through
    `_book_contracts`; a call site still reading `book.get("size")` would let the twin or a
    retry trade a different clip from the live entry."""
    import inspect

    from kalshi_bot.mmsell import tracker

    src = inspect.getsource(tracker)
    assert 'max_contracts=(book or {}).get("size")' not in src
    assert 'max_contracts=book.get("size")' not in src
    assert src.count("max_contracts=self._book_contracts(book, ticker)[1]") == 2
    live_size = inspect.getsource(tracker.MmSellTracker._live_price_and_size)
    assert "_book_contracts" in live_size


# --- the twin epoch snapshot ---------------------------------------------------

def _twin_params(settings, book):
    from kalshi_bot.mmsell.tracker import MmSellTracker

    stub = type("T", (), {
        "settings": settings,
        "twin_harness": type("H", (), {"max_open_positions": staticmethod(lambda cap: cap)})(),
        "_twin_params": MmSellTracker._twin_params,
    })()
    return stub._twin_params(book)


def _book(**over):
    b = {"lo": 5.0, "hi": 10.0, "htcmin": 1, "htcmax": 48, "twin_of": "Fmmsell10",
         "size": 1, "contestcap": 1}
    b.update(over)
    return b


def test_a_running_twin_snapshot_gains_no_new_keys(settings):
    """Fmmsell10_pt4's book: no `sizes`, no `contestkey`. Its snapshot must stay byte-identical
    or the running twin reports param drift on the deploy that ships this."""
    params = _twin_params(settings, _book())
    for k in ("sizes", "live_size_salt", "contestkey"):
        assert k not in params


def test_a_size_split_twin_records_its_counts_salt_and_key(settings):
    settings.mmsell_live_partition_salt = SALT
    params = _twin_params(settings, _book(size=None, sizes=(1, 3), contestkey="split"))
    assert params["sizes"] == [1, 3]
    assert params["live_size_salt"] == SALT
    assert params["contestkey"] == "split"
