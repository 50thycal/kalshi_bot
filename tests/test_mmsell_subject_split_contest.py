"""The corrected contest key: a subject-split series is one contest per MARKET.

WHY. `contest_key_of` groups markets that resolve on ONE underlying outcome, so a concentration
cap counts a nested ladder as the single bet it is. That is right for a strike ladder or a
spread ladder — one oil print, one football game. It is WRONG where the last ticker token names
a distinct SUBJECT: `KXTRUMPSAY-26AUG03-AMER` and `-ZOHR` are different words, and whether
Trump says one tells you nothing about the other. The shipped key collapsed 37 distinct words
into 10 weekly contests, and collapsed `KXWCMENTION`'s 70 words into the SOCCER GAME they were
spoken during — so a cap of 1 refused entries sharing no outcome at all.

WHAT MUST NEVER BREAK:

  * **the default is byte-identical.** The corrected key is opt-in. Gmmsell1 is running against
    the shipped key and its numbers must stay comparable; a fix applied in place would have
    changed the arm under the experiment rather than beside it.
  * **threshold ladders stay grouped.** They are the case the cap exists for.
  * **the type does not decide it.** `KXTRUTHSOCIAL` is typed `mention` and is a THRESHOLD
    series; a rule keyed on the market type would split a ladder that must stay grouped.
"""

from __future__ import annotations

import pytest

from kalshi_bot.mmsell.regimes import SUBJECT_SPLIT_SERIES, contest_key_of

# Verified against real traded tickers, 2026-09-07.
SUBJECT_PAIRS = [
    ("KXTRUMPSAY-26AUG03-AMER", "KXTRUMPSAY-26AUG03-ZOHR"),      # different words
    ("KXRAIN-26SEP06-TTN", "KXRAIN-26SEP06-SEA"),                # different cities
    ("KXFEDMENTION-26JUL-ADP", "KXFEDMENTION-26JUL-TRUM"),       # different words
    ("KXTRUMPSAYCOMPANY-26AUG01-ANTH", "KXTRUMPSAYCOMPANY-26AUG01-VERI"),
    ("KXWCFIRSTSONG-26JUL20-DAI", "KXWCFIRSTSONG-26JUL20-VOG"),  # different songs
    ("KXWCATTEND-26JUL20-BRA", "KXWCATTEND-26JUL20-ZEN"),        # different teams
]

THRESHOLD_PAIRS = [
    ("KXWTI-26SEP0414-T93.99", "KXWTI-26SEP0414-T72.49"),        # one oil print
    ("KXBTCD-26AUG0117-T63249.99", "KXBTCD-26AUG0117-T61000.00"),
    ("KXTRUTHSOCIAL-26AUG08-B230", "KXTRUTHSOCIAL-26AUG08-T240"),  # typed `mention`!
    ("KXNFLSPREAD-26AUG13ARILV-ARI10", "KXNFLSPREAD-26AUG13ARILV-ARI3"),  # one game
    ("KXALBUMEQUIV-CHU26AUG13-40K", "KXALBUMEQUIV-CHU26AUG13-60K"),  # one album
]


@pytest.mark.parametrize("a,b", SUBJECT_PAIRS)
def test_split_separates_distinct_subjects(a, b):
    ka = contest_key_of(a, split_subjects=True)
    kb = contest_key_of(b, split_subjects=True)
    assert ka != kb, f"{a} and {b} resolve on unrelated outcomes and must not share a contest"


@pytest.mark.parametrize("a,b", SUBJECT_PAIRS)
def test_the_default_still_groups_them(a, b):
    """Byte-identical default: the running arm must not move underneath the experiment."""
    assert contest_key_of(a) == contest_key_of(b)


@pytest.mark.parametrize("a,b", THRESHOLD_PAIRS)
def test_threshold_ladders_stay_grouped_under_both_keys(a, b):
    """One print, one game, one album — the correlation the cap exists to catch."""
    assert contest_key_of(a) == contest_key_of(b)
    assert contest_key_of(a, split_subjects=True) == contest_key_of(b, split_subjects=True)


def test_the_market_type_does_not_decide_it():
    """`KXTRUTHSOCIAL` is typed `mention` but is a threshold series. A rule keyed on the type
    would have split a ladder that must stay grouped — which is why the set is hand-audited."""
    from kalshi_bot.mmsell.market_types import classify

    assert classify("KXTRUTHSOCIAL")[0] == "mention"
    assert "KXTRUTHSOCIAL" not in SUBJECT_SPLIT_SERIES
    assert (contest_key_of("KXTRUTHSOCIAL-26AUG08-B230", split_subjects=True)
            == contest_key_of("KXTRUTHSOCIAL-26AUG08-T240", split_subjects=True))


def test_a_subject_split_series_escapes_its_sports_regime():
    """`KXWCMENTION` sits in the Soccer regime, so the shipped key filed every mention word
    under the GAME it was spoken during — colliding mention markets with the actual match
    markets. Under the corrected key it is its own contest."""
    k_default = contest_key_of("KXWCMENTION-26JUL03ARGCPV-BICY")
    k_split = contest_key_of("KXWCMENTION-26JUL03ARGCPV-BICY", split_subjects=True)
    assert k_default.startswith("Soccer:")
    assert k_split == "KXWCMENTION-26JUL03ARGCPV-BICY"
    # and it no longer collides with a real soccer game market on that contest
    assert k_split != contest_key_of("KXWCGAME-26JUL03ARGCPV-ARG", split_subjects=True)


def test_sports_grouping_is_untouched_by_the_flag():
    """The measured XOS-000020 finding — one game resolving TOTAL/SPREAD/HR together — must
    survive. Nothing about this change touches it."""
    for t in ("KXNFLSPREAD-26AUG13ARILV-ARI10", "KXMLBTOTAL-26SEP061310ATLPHI-13",
              "KXMLBHR-26SEP061310ATLPHI-ATLRACUNA13-1"):
        assert contest_key_of(t) == contest_key_of(t, split_subjects=True)


def test_every_listed_series_is_upper_and_prefix_shaped():
    for s in SUBJECT_SPLIT_SERIES:
        assert s == s.upper() and s.startswith("KX") and "-" not in s


def test_none_and_empty_survive_both_keys():
    for v in (None, ""):
        assert contest_key_of(v) is None
        assert contest_key_of(v, split_subjects=True) is None


# --- the arm ---------------------------------------------------------------------------------

def test_gmmsell2_is_gmmsell1_plus_the_corrected_key(settings):
    """The pair isolates the KEY, not the cap: same band, same cap, different grouping."""
    books = {b["tag"]: b for b in settings.mmsell_variant_list}
    g1, g2 = books["Gmmsell1"], books["Gmmsell2"]
    assert g2["contestcap"] == g1["contestcap"] == 1
    assert g1["contestkey"] is None and g2["contestkey"] == "split"
    for k in ("lo", "hi", "maxyes"):
        assert g1[k] == g2[k], f"{k} differs — the arms would not isolate the key"


def test_an_unknown_contestkey_is_rejected_not_ignored(settings):
    """Falling back silently would give a book that reads as testing the new key while running
    the old one — the invisible no-op the spec validation exists to prevent."""
    from kalshi_bot.config import Settings

    bad = Settings(_env_file=None, mmsell_variants="Xmmsell:lo=5,hi=10,contestkey=bogus")
    assert [b["tag"] for b in bad.mmsell_variant_list] == []
