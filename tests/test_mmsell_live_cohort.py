"""The LIVE-COHORT books (`Lmmsell8`, `Lmmsell10`) — replicas armed instead of their parents.

Why they exist (docs/LIVE_PAPER_TWIN.md "Arming"): the live mirror only fires when the PAPER
book opens. When paper already holds the ticker the scan takes `skip_already_open`, and
`_maybe_retry_live` returns early at `attempts == 0` — correctly, since with no prior live order
there is no price anchor. So arming a tag whose paper book has already been running hands live a
book full of tickers it can never trade. Measured 2026-08-15: 87 of `mmsell10`'s open positions
predated arming and got zero live orders ever, against 3 for `mmsell8` — an asymmetry that
throttled the CONTROL harder than the treatment.

What these tests pin, in order of what would silently break the arrangement:
  * each live-cohort book is a byte-identical replica of its parent apart from the tag. If
    someone retunes a parent and not its replica, the live book stops being the book the paper
    history describes and every read across the pair is confounded.
  * the replica tags are PREFIX-SAFE against the parents. LIVE_STRATEGIES matches with
    `startswith`, so a tag like `mmsell10L` would be silently captured by an allowlist entry
    naming `mmsell10` — arming a book nobody asked for.
  * both parse at all (a tag without the substring `mmsell` is dropped on the floor by
    `mmsell_variant_list`, which would leave the live allowlist naming a book that cannot exist).
"""

from __future__ import annotations

import pytest

PAIRS = (("Lmmsell8", "mmsell8"), ("Lmmsell10", "mmsell10"))


def _by_tag(settings, tag):
    for v in settings.mmsell_variant_list:
        if v["tag"] == tag:
            return v
    return None


@pytest.mark.parametrize(("replica", "parent"), PAIRS)
def test_live_cohort_book_parses(settings, replica, parent):
    """A tag lacking the substring 'mmsell' is silently skipped by the parser, so a typo here
    would arm a book that does not exist — the allowlist would name it and nothing would trade."""
    assert _by_tag(settings, replica) is not None, f"{replica} did not parse"
    assert _by_tag(settings, parent) is not None, f"{parent} did not parse"


@pytest.mark.parametrize(("replica", "parent"), PAIRS)
def test_live_cohort_book_is_an_exact_replica_of_its_parent(settings, replica, parent):
    """Same book, different tag. The ONLY reason the replica exists is fresh position state; any
    other difference makes the parent's history stop describing what live is actually running."""
    r, p = _by_tag(settings, replica), _by_tag(settings, parent)
    assert r.keys() == p.keys()
    differing = {k for k in r if r[k] != p[k]}
    assert differing == {"tag"}, f"{replica} diverges from {parent} on {differing - {'tag'}}"


@pytest.mark.parametrize(("replica", "parent"), PAIRS)
def test_live_cohort_tag_is_prefix_safe_against_its_parent(settings, replica, parent):
    """LIVE_STRATEGIES is matched with `startswith` (see LiveExecutor._strategy_allowed), so a
    replica named `mmsell10L` would be armed by an allowlist entry that only names `mmsell10`.
    Neither tag may be a prefix of the other, in either direction."""
    assert not replica.startswith(parent), (
        f"{replica} starts with {parent}: arming {parent} would silently arm it too")
    assert not parent.startswith(replica)


def test_no_configured_mmsell_tag_is_a_prefix_of_another(settings):
    """The general form of the trap above, across every configured book.

    A known live exception is grandfathered: `mmsell10a`/`mmsell10b` DO start with `mmsell10`.
    That pair is inert unless MMSELL_LIVE_OFFSET_AB_ARMS is set, but it is exactly why the
    live-cohort tags were named `L*` rather than `*L` — and why arming `mmsell10` directly should
    be avoided while those two exist."""
    known = {("mmsell10", "mmsell10a"), ("mmsell10", "mmsell10b")}
    tags = [v["tag"] for v in settings.mmsell_variant_list]
    offenders = {(a, b) for a in tags for b in tags
                 if a != b and b.startswith(a) and (a, b) not in known}
    assert not offenders, f"prefix-shadowed tags (arming the first silently arms the second): {offenders}"
