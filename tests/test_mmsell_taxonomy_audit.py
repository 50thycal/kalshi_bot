"""`scripts/mmsell_taxonomy_audit.py` — the settlement-taxonomy repair package.

Two failures this guards against, both of which happened.

**Guessing.** An unknown series ASSUMED to be scheduled would enter the MMSELL 2x2's treatment
arm and make the primary comparison measure the very confound the design exists to control for.
The proposal rule must be willing to return no answer.

**Miscounting.** Settlement semantics are a property of the SERIES, so one rule document answers
for every market under a prefix — but it answers ONCE. Run `tax-6` fetched one market per prefix,
copied its blob onto all forty-six markets under it, and reported "100% of 46 texts".
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mmsell_taxonomy_audit as ta  # noqa: E402


def _rows(n: int, prefix: str = "KXFOO"):
    return [{"ticker": f"{prefix}-25AUG{i:02d}-T1", "series": prefix,
             "hours_to_close": None, "hours_to_expiration": None} for i in range(n)]


def _db(rows, *, gap_h=None):
    """The `markets`-table side. For this population it is empty for every ticker; the gap
    signal falls back to the candidate stream's own two clocks."""
    if gap_h is None:
        return {}
    base = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)
    return {r["ticker"]: {"close_time": base,
                          "expiration_time": base + dt.timedelta(hours=gap_h)} for r in rows}


def _kalshi(*docs, unique_markets: int | None = None):
    """A fetched bundle: DISTINCT rule documents, plus how many markets were inspected.

    `docs` are already deduplicated, exactly as `fetch_series_text` returns them.
    """
    out = [{"title": d.get("title", ""), "rules": d.get("rules", ""),
            "source": d.get("source", ""), "category": "",
            "can_close_early": d.get("can_close_early"),
            "n_markets": d.get("n_markets", 1)} for d in docs]
    return {"docs": out, "schema": [],
            "unique_markets": unique_markets if unique_markets is not None
            else sum(d["n_markets"] for d in out)}


class TestCryptoDetection:
    @pytest.mark.parametrize("series,crypto", [
        ("KXBTCD", True), ("KXETH", True), ("KXSOLD", True),
        ("KXMLBGAME", False), ("KXFED", False), ("", False), (None, False),
    ])
    def test_crypto_series_are_excluded_from_the_non_crypto_population(self, series, crypto):
        assert ta.is_crypto(series) is crypto


class TestEvidenceIsCountedOverDocuments:
    """THE accounting fix. A prefix with forty markets and one rule document has ONE
    observation of what Kalshi says about it."""

    def test_one_document_across_forty_markets_is_one_observation(self):
        rows = _rows(40)
        k = _kalshi({"rules": "settles to the closing price", "n_markets": 8},
                    unique_markets=8)
        ev = ta.prefix_evidence(rows, {}, k, {})
        assert ev["unique_docs"] == 1
        assert ev["unique_markets"] == 8
        assert ev["rules"][2] == 1, "documents voted, not markets"
        mode, why = ta.propose(ev)
        assert mode == ta.SCHEDULED
        assert "ONE rules text document" in why
        assert "one observation, not" in why

    def test_several_agreeing_documents_are_reported_as_several(self):
        rows = _rows(40)
        k = _kalshi({"rules": "settles to the closing price at 4pm"},
                    {"rules": "the end-of-day index value decides this market"},
                    {"rules": "the close price of the contract"}, unique_markets=8)
        ev = ta.prefix_evidence(rows, {}, k, {})
        assert ev["unique_docs"] == 3
        mode, why = ta.propose(ev)
        assert mode == ta.SCHEDULED
        assert "3 distinct rules text document(s)" in why
        assert "unanimous" in why

    def test_documents_that_disagree_refuse_to_propose(self):
        # The series does not have one settlement semantics, or the sample spans a rule change.
        rows = _rows(40)
        k = _kalshi({"rules": "settles to the closing price"},
                    {"rules": "wins the game after 90 minutes plus stoppage time"},
                    unique_markets=6)
        mode, why = ta.propose(ta.prefix_evidence(rows, {}, k, {}))
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert "documents disagree" in why

    def test_no_documents_retrieved_refuses(self):
        mode, why = ta.propose(ta.prefix_evidence(_rows(40), {}, {}, {}))
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert "no Kalshi rules text" in why


class TestProposalRule:
    def test_a_settlement_source_naming_an_index_proposes_scheduled(self):
        ev = ta.prefix_evidence(_rows(30), {},
                                _kalshi({"source": "CME CF Bitcoin Reference Rate"}), {})
        mode, why = ta.propose(ev)
        assert mode == ta.SCHEDULED
        assert "settlement source" in why

    def test_rules_text_about_a_final_score_proposes_in_play(self):
        ev = ta.prefix_evidence(_rows(30), {},
                                _kalshi({"rules": "Settles to the final score of the game"}), {})
        assert ta.propose(ev)[0] == ta.IN_PLAY

    def test_conflicting_strong_signals_refuse_to_propose(self):
        k = _kalshi({"source": "ESPN box score", "rules": "the closing price at 4:00 pm ET"})
        mode, why = ta.propose(ta.prefix_evidence(_rows(30), {}, k, {}))
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert "settlement source says" in why

    def test_shape_evidence_alone_is_never_enough(self):
        # An expiration gap and a jumpy price path look the same for a scheduled print and a
        # discrete announcement. Corroboration is not identification.
        rows = _rows(30)
        ev = ta.prefix_evidence(rows, _db(rows, gap_h=0.0), _kalshi({"rules": "no signal here"}),
                                {r["ticker"]: 3.0 for r in rows})
        mode, why = ta.propose(ev)
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert "only shape evidence" in why

    def test_can_close_early_does_not_vote(self):
        """Tried as a strong signal; run `tax-2` refuted it. Kalshi sets the flag on index-close
        markets too, so it proposed in_play for KXINX and KXNASDAQ100 while blocking four
        prefixes whose rules text said scheduled. Reported, decides nothing."""
        k = _kalshi({"rules": "settles to the closing price at 4:00 pm ET",
                     "can_close_early": True})
        ev = ta.prefix_evidence(_rows(30), {}, k, {})
        assert ev["early_share"] == 1.0            # measured and reported...
        assert ta.propose(ev)[0] == ta.SCHEDULED   # ...and the rules text still decides

    def test_can_close_early_alone_proposes_nothing(self):
        k = _kalshi({"rules": "nothing decisive", "can_close_early": True})
        assert ta.propose(ta.prefix_evidence(_rows(30), {}, k, {}))[0] == "INSUFFICIENT_EVIDENCE"

    def test_no_evidence_at_all_refuses_rather_than_defaulting(self):
        # THE regression this file exists for: silence must not become `scheduled`.
        mode, why = ta.propose(ta.prefix_evidence(_rows(30), {}, _kalshi({"rules": ""}), {}))
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert mode != ta.SCHEDULED
        assert "no settlement source" in why

    def test_a_handful_of_markets_is_anecdote_not_evidence(self):
        rows = _rows(ta.MIN_MARKETS_TO_PROPOSE - 1)
        mode, why = ta.propose(ta.prefix_evidence(
            rows, {}, _kalshi({"source": "CME CF Reference Rate"}), {}))
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert "only" in why and "markets" in why

    def test_text_contradicted_by_the_price_path_refuses(self):
        # Rules text says scheduled; a quarter of the markets are still mid-book at the last
        # tick, which a scheduled settle does not do.
        rows = _rows(30)
        mode, why = ta.propose(ta.prefix_evidence(
            rows, {}, _kalshi({"rules": "the closing price at 4:00 pm ET"}),
            {r["ticker"]: 50.0 for r in rows}))
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert "price path" in why

    def test_corroborated_text_is_reported_as_corroborated(self):
        rows = _rows(30)
        mode, why = ta.propose(ta.prefix_evidence(
            rows, _db(rows, gap_h=0.0), _kalshi({"source": "CME CF Reference Rate"}),
            {r["ticker"]: 2.0 for r in rows}))
        assert mode == ta.SCHEDULED
        assert "expiration gap" in why and "price path" in why


class TestEvidenceSignals:
    def test_a_far_future_close_time_reads_as_in_play(self):
        # Kalshi sets a far-future fallback close_time on in-play sports; a large gap is
        # evidence, not a data error.
        rows = _rows(20)
        ev = ta.prefix_evidence(rows, _db(rows, gap_h=48.0), _kalshi(), {})
        assert ev["gap_mode"] == ta.IN_PLAY

    def test_expiration_at_the_close_reads_as_scheduled(self):
        rows = _rows(20)
        ev = ta.prefix_evidence(rows, _db(rows, gap_h=0.0), _kalshi(), {})
        assert ev["gap_mode"] == ta.SCHEDULED

    def test_an_ambiguous_gap_votes_for_nothing(self):
        rows = _rows(20)
        assert ta.prefix_evidence(rows, _db(rows, gap_h=3.0), _kalshi(), {})["gap_mode"] is None

    def test_the_candidate_clocks_are_used_when_markets_has_no_row(self):
        rows = [{"ticker": f"KXFOO-{i}", "series": "KXFOO",
                 "hours_to_close": 50.0, "hours_to_expiration": 2.0} for i in range(10)]
        assert ta.prefix_evidence(rows, {}, _kalshi(), {})["gap_mode"] == ta.IN_PLAY

    def test_the_bar_and_band_are_the_designs_not_this_scripts(self):
        assert ta.UNCLASSIFIED_BAR == 0.05
        assert ta.BAND == (5.0, 7.0)


class TestAgainstKalshisRealProse:
    """Verbatim rules text from run `tax-3`, one per settlement mode it must separate.

    The regexes were written against guesses first and got two of these backwards: a bare
    "at 8:10 PM EDT" was read as a scheduled settlement, but Kalshi writes
    "the game originally scheduled for Aug 22, 2026 at 8:10 PM EDT" on IN-PLAY markets, so MLB
    player props and KBO baseball came back `scheduled`. A clock time does not discriminate;
    genuinely scheduled markets say which CLOSE they settle to.
    """

    CORPUS = [
        (ta.IN_PLAY, "KXMLSSCORE",
         "If Atlanta United FC wins 5-2 in the Atlanta United FC vs Sporting Kansas City "
         "professional MLS soccer game originally scheduled for Aug 23, 2026 after 90 minutes "
         "plus stoppage time (does not include extra time or penalties), then the market "
         "resolves to Yes."),
        (ta.IN_PLAY, "KXMLBTB",
         "If Steven Kwan records 5+ total bases in the Cleveland vs Colorado professional "
         "baseball game originally scheduled for Aug 22, 2026 at 8:10 PM EDT, then the market "
         "resolves to Yes."),
        (ta.IN_PLAY, "KXKBOGAME",
         "If Lotte Giants wins the Lotte Giants vs Kia Tigers Korea KBO game originally "
         "scheduled for Aug 25, 2026 at 6:00 AM EDT, then the market resolves to Yes. If this "
         "game is postponed or delayed, the market will remain open and close after the "
         "rescheduled game has finished"),
        (ta.IN_PLAY, "KXKFTOUR",
         "If Jamie Wilson wins the AdventHealth Championship, then the market resolves to Yes."),
        (ta.IN_PLAY, "KXYTVIEWSW",
         "If Bad Bunny has above 34M Global daily views on YouTube at any point during "
         "August 17, 2026 - August 23, 2026, then the market resolves to Yes."),
        (ta.SCHEDULED, "KXINX",
         "If the end-of-day S&P 500 index value on August 28, 2026 is above 7974.9999, then the "
         "market resolves to Yes. The market will expire at the sooner of the first release of "
         "the data, or one week after August 28, 2026."),
        (ta.SCHEDULED, "KXCOPPERD",
         "If the close price of the 1-minute candlestick for copper using the CCU6 contract on "
         "August 24, 2026 at 5:00 PM EDT is above 6.40 USD/Lbs, then the market resolves to Yes."),
    ]

    @pytest.mark.parametrize("expected,prefix,rules", CORPUS, ids=[c[1] for c in CORPUS])
    def test_kalshis_own_words_classify_correctly(self, expected, prefix, rules):
        assert ta._match_mode(rules, ta._RULES_PATTERNS) == expected

    def test_a_game_start_time_is_not_a_scheduled_settlement(self):
        # The exact defect, isolated.
        assert ta._match_mode("originally scheduled for Aug 22, 2026 at 8:10 PM EDT",
                              ta._RULES_PATTERNS) == ta.IN_PLAY


class TestWeakSignalsCancel:
    def test_two_disagreeing_corroborators_do_not_veto_the_text(self):
        # An earlier version let EITHER weak signal veto, so a prefix whose expiration gap
        # agreed with the rules text and whose price path did not came back INSUFFICIENT.
        rows = _rows(30)
        mode, why = ta.propose(ta.prefix_evidence(
            rows, _db(rows, gap_h=0.0), _kalshi({"rules": "settles to the closing price"}),
            {r["ticker"]: 50.0 for r in rows}))
        assert mode == ta.SCHEDULED
        assert "disagrees" in why

    def test_a_lone_disagreeing_corroborator_still_blocks(self):
        rows = _rows(30)
        mode, _why = ta.propose(ta.prefix_evidence(
            rows, {}, _kalshi({"rules": "settles to the closing price"}),
            {r["ticker"]: 50.0 for r in rows}))
        assert mode == "INSUFFICIENT_EVIDENCE"


class TestDocumentDeduplication:
    def test_identical_documents_rendered_differently_collapse(self):
        a = ta._norm("If the  close price\nis above 6.40, then Yes.")
        b = ta._norm("if the close price is above 6.40, then yes.")
        assert a == b
