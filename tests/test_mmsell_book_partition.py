"""Two concurrent experiments whose universes overlap must not contest tickers.

WHY THIS EXISTS. Live order dedup is strategy-AGNOSTIC (`repository.live_open_order_exists`
filters on market_ticker alone) and `mirror_mmsell_entry` COMMITS the order row before the Kalshi
POST. So when two books admit the same market, the one evaluated first in the cycle claims it and
every later book gets `gate:dedup`. Evaluation order is `MMSELL_VARIANTS` order, so the loser is
always the SAME book — it trades the residue of the winner's gates rather than its own strategy,
and a resting mmsell order is GTC held to settlement, so the lockout lasts the life of the
position rather than a cycle.

The hash split that fixes this already existed as `abarm`, but `arm_book_offset` returns an
OFFSET as its verdict: enrolling two books in it also forces their resting prices to differ. For
two books asking unrelated questions that is a confound nobody asked for. `part=i/n` is the same
primitive with the price removed.

What must never break, in the order in which breaking it would matter:

  * **an unpartitioned book admits everything.** Every book running today declares no `part`,
    so merging this must change nothing for any of them.
  * **the offset A/B's split is byte-identical.** `offset_arm` was refactored onto the shared
    hash; if the assignment moved, every number collected under the old salt would silently stop
    being comparable with every number after.
  * **the partition is exhaustive and disjoint.** Every ticker goes to exactly one book — no
    market is dropped by the split and none is contested.
  * **`part` decides the claim and NOTHING else.** It must not touch price, size or band.
  * **a malformed or contradictory spec is REFUSED, not defaulted.** A book that reads as
    partitioned but is not would contest every ticker with its sibling.
"""

from __future__ import annotations

from kalshi_bot.live.sizing import offset_arm, ticker_partition

SALT = "mmsell-partition-v1"

# A realistic spread of tickers, not a handful: the disjointness and balance claims are only
# meaningful across enough of them to catch an off-by-one in the modulus.
TICKERS = [f"KXMLBTOTAL-26SEP{d:02d}NYYLAA-{k}" for d in range(1, 31) for k in range(1, 9)]


# --- the primitive -------------------------------------------------------------

def test_every_ticker_lands_in_exactly_one_partition():
    """Exhaustive AND disjoint. If a ticker belonged to neither book the flow would silently
    shrink; if it belonged to both we would be back to the scan-order race this replaces."""
    for t in TICKERS:
        owners = [i for i in range(2) if ticker_partition(t, n=2, salt=SALT) == i]
        assert owners == [ticker_partition(t, n=2, salt=SALT)]
        assert len(owners) == 1


def test_the_split_is_stable_for_a_ticker():
    """A ticker keeps ONE owner for its whole life. The live entry-retry path re-posts the same
    ticker across cycles; an owner that could flip would let both books hold the same market."""
    t = TICKERS[0]
    assert len({ticker_partition(t, n=2, salt=SALT) for _ in range(50)}) == 1


def test_the_split_is_roughly_balanced():
    """Not a statistical claim — just that neither side is starved, which a broken modulus
    (e.g. hashing a constant) would show up as immediately."""
    zero = sum(1 for t in TICKERS if ticker_partition(t, n=2, salt=SALT) == 0)
    assert 0.35 < zero / len(TICKERS) < 0.65


def test_a_different_salt_reassigns():
    """The salt is the knob that starts a NEW split, and the reason changing it makes evidence
    either side of the change non-poolable."""
    moved = sum(1 for t in TICKERS
                if ticker_partition(t, n=2, salt=SALT)
                != ticker_partition(t, n=2, salt="mmsell-partition-v2"))
    assert moved > 0


def test_three_way_works_too():
    counts = [0, 0, 0]
    for t in TICKERS:
        idx = ticker_partition(t, n=3, salt=SALT)
        assert 0 <= idx < 3
        counts[idx] += 1
    assert all(c > 0 for c in counts)


def test_a_one_book_partition_is_refused():
    """n=1 is not a split. Returning 0 would make a `part=0/1` book read as partitioned while
    admitting everything — exactly the invisible no-op this whole mechanism guards against."""
    try:
        ticker_partition("KXMLBTOTAL-X", n=1, salt=SALT)
    except ValueError:
        return
    raise AssertionError("n=1 must raise")


# --- the load-bearing regression: the offset A/B did not move ------------------

def test_the_offset_ab_assignment_is_unchanged_by_the_refactor():
    """`offset_arm` now delegates to the shared hash. Its assignment MUST be what it always was,
    or every queue-position number collected under `mmsell-offset-ab-v1` silently stops being
    comparable with anything collected after this merge."""
    import hashlib
    ab_salt = "mmsell-offset-ab-v1"
    arms = (0, 1)
    for t in TICKERS[:100]:
        # Recomputed inline from the ORIGINAL formula, not from the function under test.
        digest = hashlib.sha256(f"{ab_salt}:{t}".encode()).digest()
        expected_idx = int.from_bytes(digest[:8], "big") % len(arms)
        idx, offset = offset_arm(t, arms=arms, salt=ab_salt)
        assert idx == expected_idx
        assert offset == arms[expected_idx]


def test_offset_arm_still_tolerates_a_single_arm():
    """ticker_partition refuses n=1; offset_arm must not, because a single configured offset is
    a legitimate (experiment-off) caller shape rather than a broken split."""
    assert offset_arm("KXMLBTOTAL-X", arms=(3,), salt="s") == (0, 3)


# --- the config spec -----------------------------------------------------------

def _books(settings, variants):
    settings.mmsell_variants = variants
    return {b["tag"]: b for b in settings.mmsell_variant_list}


def test_part_parses_and_leaves_every_other_knob_alone(settings):
    """`part` decides the CLAIM and nothing else — the two books stay identical in band, ceiling
    and size, which is the whole point of splitting it out of `abarm`."""
    books = _books(settings, "mmsellP0:lo=5,hi=10,maxyes=7,part=0/2;"
                             "mmsellP1:lo=5,hi=10,maxyes=7,part=1/2")
    assert books["mmsellP0"]["part"] == (0, 2)
    assert books["mmsellP1"]["part"] == (1, 2)
    for tag in ("mmsellP0", "mmsellP1"):
        assert books[tag]["abarm"] is None          # untouched by the partition
        assert (books[tag]["lo"], books[tag]["hi"], books[tag]["maxyes"]) == (5.0, 10.0, 7.0)


def test_an_unpartitioned_book_has_part_none(settings):
    """The whole existing cohort. If this ever stopped being None the running books would start
    trading a fraction of their flow without any spec change."""
    assert _books(settings, "mmsellX:lo=5,hi=10")["mmsellX"]["part"] is None


def test_a_malformed_or_out_of_range_partition_is_refused(settings):
    """Refused, never defaulted: a book that reads as partitioned but admits everything would
    contest every ticker with its sibling, which is the failure this mechanism exists to stop."""
    for bad in ("part=0", "part=2/2", "part=3/2", "part=-1/2", "part=0/1", "part=a/b", "part=/2"):
        assert _books(settings, f"mmsellBad:lo=5,hi=10,{bad}") == {}


def test_declaring_both_part_and_abarm_is_refused(settings):
    """Two independent hash splits on one book leaves it trading a QUARTER of the flow while its
    spec reads as half."""
    assert _books(settings, "mmsellBoth:lo=5,hi=10,part=0/2,abarm=0") == {}


# --- what the tracker actually admits -----------------------------------------

def _admits(settings, book, ticker):
    """`_book_admits_ticker` on a bare stub carrying only `settings`. Deliberately not a real
    tracker: this is a pure selection decision and must stay one — if admitting a ticker ever
    needed the client, the DB or a scan cycle, that would itself be the regression."""
    from kalshi_bot.mmsell.tracker import MmSellTracker

    stub = type("T", (), {
        "settings": settings,
        "_book_arm_offset": MmSellTracker._book_arm_offset,
        "_book_admits_ticker": MmSellTracker._book_admits_ticker,
    })()
    return stub._book_admits_ticker(book, ticker)


def test_two_partitioned_books_never_admit_the_same_ticker(settings):
    """The property the whole change is for: no ticker is ever contested, so neither book is
    trading the residue of the other's gates."""
    settings.mmsell_live_partition_salt = SALT
    a, b = {"part": (0, 2)}, {"part": (1, 2)}
    for t in TICKERS:
        assert _admits(settings, a, t) != _admits(settings, b, t)
    # ...and between them they cover the whole flow.
    assert all(_admits(settings, a, t) or _admits(settings, b, t) for t in TICKERS)


def test_an_unpartitioned_book_admits_everything(settings):
    """Inert for the running cohort — the regression guard that matters most at merge."""
    settings.mmsell_live_partition_salt = SALT
    assert all(_admits(settings, {}, t) for t in TICKERS)


def test_a_partitioned_book_is_unaffected_by_the_offset_experiment(settings):
    """`part` keeps trading its half whatever the offset A/B is doing. An `abarm` book with no
    configured arms admits NOTHING (it has no defined price); a `part` book has no such failure
    mode, because it decides only the claim."""
    settings.mmsell_live_partition_salt = SALT
    settings.mmsell_live_offset_ab_arms = ""        # experiment off
    part_book, arm_book = {"part": (0, 2)}, {"abarm": 0}
    assert any(_admits(settings, part_book, t) for t in TICKERS)
    assert not any(_admits(settings, arm_book, t) for t in TICKERS)


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
    b = {"lo": 5.0, "hi": 10.0, "htcmin": 1, "htcmax": 48, "twin_of": "mmsell10"}
    b.update(over)
    return b


def test_an_unpartitioned_twin_snapshot_gains_no_new_keys(settings):
    """`sync_twin_epoch` compares the whole params dict with `!=` against what the epoch was
    OPENED with, so a key added unconditionally would make every twin ALREADY RUNNING report
    param drift on the next deploy — a false alarm on a live comparison. The keys must be
    absent, not None: a None value is still a dict difference."""
    params = _twin_params(settings, _book())
    assert "part" not in params
    assert "live_partition_salt" not in params


def test_a_partitioned_twin_records_its_partition_and_salt(settings):
    """When it DOES apply, it must be in the snapshot — changing a book's half of the flow
    mid-epoch is precisely the drift this snapshot exists to catch."""
    settings.mmsell_live_partition_salt = SALT
    params = _twin_params(settings, _book(part=(1, 2)))
    assert params["part"] == [1, 2]
    assert params["live_partition_salt"] == SALT
