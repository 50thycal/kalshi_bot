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


# --- the ops runner's copy ---------------------------------------------------------------------

def _pnl_script():
    """The ops-channel report duplicates the split set because the runner has no package to
    import from — the same constraint that duplicates `SERIES_TYPES` in the universe review."""
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mmsell_series_pnl.py"
    spec = importlib.util.spec_from_file_location("mmsell_series_pnl", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_ops_script_split_set_matches_the_workers():
    """A drifted copy would report a series' own-sample weight under one key while the book
    trades it under another — and the whole point of the column is to say how much of a score
    is that series' own evidence."""
    assert _pnl_script().SUBJECT_SPLIT_SERIES == SUBJECT_SPLIT_SERIES


def test_the_ops_script_splits_the_same_tickers_the_worker_does():
    """Not a set comparison: the two implementations differ (the worker groups sports across
    series, the script never claims to), so this asserts they agree on the DECISION that a
    ticker's last token is a subject — which is the only part the report reads."""
    mod = _pnl_script()
    for ticker in ("KXTRUMPSAY-26AUG03-AMER", "KXRAIN-26SEP06-TTN",
                   "KXWCMENTION-26JUL03ARGCPV-BICY"):
        assert mod.contest_of(ticker, split_subjects=True) == ticker.upper()
        assert contest_key_of(ticker, split_subjects=True) == ticker.upper()
    for ticker in ("KXNFLSPREAD-25AUG14ATLDET-DET3", "KXWTI-26SEP0414-T93.99"):
        assert mod.contest_of(ticker, split_subjects=True) != ticker.upper()
        assert contest_key_of(ticker, split_subjects=True) != ticker.upper()


def test_the_default_key_is_byte_identical_in_the_report_too():
    """The report's shipped meaning must not move under a reader who did not pass the flag."""
    mod = _pnl_script()
    for ticker in ("KXTRUMPSAY-26AUG03-AMER", "KXRAIN-26SEP06-TTN",
                   "KXNFLSPREAD-25AUG14ATLDET-DET3", "KXPAYROLLS-26SEP"):
        assert mod.contest_of(ticker) == mod.contest_of(ticker, split_subjects=False)
    # And the default really is the OLD key, not the new one wearing the old name.
    assert mod.contest_of("KXTRUMPSAY-26AUG03-AMER") == "KXTRUMPSAY:26AUG03"
    assert mod.contest_of("KXPAYROLLS-26SEP") == "KXPAYROLLS:26SEP"


def test_own_weight_is_the_fitted_constant_and_rises_with_evidence():
    """`own%` is the scorecard component, not a display nicety: at the family MEDIAN of 3
    contests it must read as almost entirely prior, or a thin series' edge gets read as its
    own record — the exact error the pooling exists to prevent."""
    mod = _pnl_script()
    assert mod.POOLING_K_CONTESTS == 38

    def weight(n_contests):
        rows = [{"pnl_c": -1.0, "ticker": f"KXX-{i}", "contest": f"c{i}", "book": "b",
                 "entry_c": 6.0, "live": False} for i in range(n_contests)]
        return mod.summarize(rows)["own_weight"]

    assert round(100 * weight(3)) == 7
    assert round(100 * weight(38)) == 50
    assert round(100 * weight(100)) == 72
    assert weight(3) < weight(38) < weight(100) < 1.0


def _concentration_script():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "series_concentration.py"
    spec = importlib.util.spec_from_file_location("series_concentration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_concentration_script_split_set_matches_the_workers():
    """Third copy of the set, same drift risk as the P&L report's — check 3 keyed on a stale
    list would report a series' independence unit under a rule the book no longer uses."""
    assert _concentration_script().SUBJECT_SPLIT_SERIES == SUBJECT_SPLIT_SERIES


def test_the_concentration_script_defaults_to_the_CORRECTED_key():
    """Opposite default from the P&L report, deliberately: there the shipped meaning must not
    move under an unsuspecting reader, here an honest independence unit IS the measurement."""
    mod = _concentration_script()
    assert mod.contest_of("KXRAIN-26SEP06-TTN") == "KXRAIN-26SEP06-TTN"
    assert mod.contest_of("KXRAIN-26SEP06-TTN", split_subjects=False) == "KXRAIN:26SEP06"
    assert mod.contest_of("KXNFLSPREAD-26AUG13ARILV-ARI10") == "KXNFLSPREAD:26AUG13ARILV"


def test_a_bare_date_token_is_recognised_so_the_cross_series_column_can_disclaim_itself():
    """KXRAIN read 5.93 series per event and KXTRUMPSAY 7.50 in the batch-1 ad-hoc query. Both
    are artifacts: their event token is a bare date, so it collides with every date-keyed
    series. The column must say `date?` rather than report a number that means nothing."""
    mod = _concentration_script()
    for token in ("26SEP06", "26AUG03", "26SEP0414", "26AUG1717",
                  # A month ALONE is still only a date. KXFEDMENTION-26JUL-ADP keys on `26JUL`
                  # and read 15.00 cross-series on the first production run, because requiring
                  # digits after the month made an empty remainder fail.
                  "26JUL", "26AUG", "26SEP"):
        assert mod.looks_like_a_bare_date(token), token
    for token in ("26AUG13ARILV", "26JUL03ARGCPV", "26SEP022138NYYLAA", "", "T93.99"):
        assert not mod.looks_like_a_bare_date(token), token


def test_concentration_separates_a_ladder_from_independent_positions():
    """The whole point of check 3: one oil print carrying 18 strikes must not read the same as
    18 markets that each ride their own outcome."""
    mod = _concentration_script()

    def rows(tickers):
        return [{"ticker": t, "series": mod.series_of(t), "ev": mod.event_token(t),
                 "contest": mod.contest_of(t), "c": -1.0} for t in tickers]

    ladder = rows([f"KXWTI-26SEP0414-T{90 + i}.99" for i in range(18)])
    s = mod.summarize(ladder, {})
    assert s["contests"] == 1 and s["max_mkts"] == 18 and s["multi_share"] == 1.0
    assert s["date_keyed"] is True          # one oil print, and the token is a bare date

    spread = rows([f"KXRAIN-26SEP0{d}-TTN" for d in range(1, 9)])
    s = mod.summarize(spread, {})
    assert s["contests"] == 8 and s["max_mkts"] == 1 and s["avg_mkts"] == 1.0
    assert s["multi_share"] == 0.0


def test_every_allowlisted_ops_script_reads_the_READ_ONLY_database_url():
    """The ops channel holds only `DATABASE_URL_RO`. A script reading `DATABASE_URL` alone dies
    on "not set" the first time it runs in production — which is exactly what happened to
    `series_concentration` on its first real run. Asserted across the whole allowlist, because
    the next script added will make the same assumption for the same reason."""
    import importlib.util
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("ops_runner", root / "scripts" / "ops_runner.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    offenders = []
    for name in runner.ALLOWED_SCRIPTS:
        path = root / "scripts" / f"{name}.py"
        if not path.exists():
            continue
        src = path.read_text()
        if not re.search(r"DATABASE_URL\b", src):
            continue                      # reads no database at all
        if "DATABASE_URL_RO" not in src:
            offenders.append(name)
    assert not offenders, f"allowlisted scripts that never read DATABASE_URL_RO: {offenders}"
