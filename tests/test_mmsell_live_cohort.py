"""The LIVE-COHORT books (`Lmmsell8`, `Lmmsell10`) — replicas armed instead of their parents.

Why they exist (docs/LIVE_PAPER_TWIN.md "Arming"): the live mirror only fires when the PAPER
book opens. When paper already holds the ticker the scan takes `skip_already_open`, and
`_maybe_retry_live` returns early at `attempts == 0` — correctly, since with no prior live order
there is no price anchor. So arming a tag whose paper book has already been running hands live a
book full of tickers it can never trade. Measured 2026-08-15: 87 of `mmsell10`'s open positions
predated arming and got zero live orders ever, against 3 for `mmsell8` — an asymmetry that
throttled the CONTROL harder than the treatment.

RETIRED 2026-09-06. The experiment they carried (`mmsell-scheduled-settle-live`) was stood
down by operator decision — last live order 2026-08-19, neither tag armed in LIVE_STRATEGIES
since, keep gate unable to rule (BLOCKED_DATA, XOS-000025) — and the two entries were removed
from the default 2026-09-12 (XOS-000034). Under NEW_ONLY a configured book with no active
deployment arm is constructed every scan cycle and refused at the write path, so the entries
were not harmless residue. The PARENTS (`mmsell8`, `mmsell10`) keep running as paper.

What these tests pin now:
  * the replicas stay retired — asserted as absence, so a re-add has to argue with a test — and
    the parents are still configured.
  * the naming convention that made the replicas safe stays load-bearing for the next live
    cohort: `L*` tags are PREFIX-SAFE against their parents. LIVE_STRATEGIES matches with
    `startswith`, so a tag like `mmsell10L` would be silently captured by an allowlist entry
    naming `mmsell10` — arming a book nobody asked for.
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
def test_live_cohort_book_is_retired_and_its_parent_still_runs(settings, replica, parent):
    """The replica existed for one reason — fresh position state at arming — and that arming is
    over. Its parent is the registry's long-run control series and must still be configured."""
    assert _by_tag(settings, replica) is None, (
        f"{replica} was retired 2026-09-06 (XOS-000034) and is configured again")
    assert _by_tag(settings, parent) is not None, f"{parent} did not parse"


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
