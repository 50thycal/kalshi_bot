"""`scripts/mmsell_taxonomy_audit.py` — the settlement-taxonomy repair package.

The failure this guards against is specific and it already happened once in scoping: an unknown
series being ASSUMED scheduled. An in-play series recorded as scheduled would enter the MMSELL
2x2's treatment arm and make the primary comparison measure the very confound the design exists
to control for, so the proposal rule must be willing to return no answer.
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


def _text(rows, *, source="", rules="", title="", gap_h=None):
    base = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.timezone.utc)
    out = {}
    for r in rows:
        entry = {"title": title, "rules": rules, "source": source, "category": ""}
        if gap_h is not None:
            entry["close_time"] = base
            entry["expiration_time"] = base + dt.timedelta(hours=gap_h)
        out[r["ticker"]] = entry
    return out


class TestCryptoDetection:
    @pytest.mark.parametrize("series,crypto", [
        ("KXBTCD", True), ("KXETH", True), ("KXSOLD", True),
        ("KXMLBGAME", False), ("KXFED", False), ("", False), (None, False),
    ])
    def test_crypto_series_are_excluded_from_the_non_crypto_population(self, series, crypto):
        assert ta.is_crypto(series) is crypto


class TestProposalRule:
    def test_a_settlement_source_naming_an_index_proposes_scheduled(self):
        rows = _rows(30)
        ev = ta.prefix_evidence(rows, _text(rows, source="CME CF Bitcoin Reference Rate"), {})
        mode, why = ta.propose(ev)
        assert mode == ta.SCHEDULED
        assert "settlement source" in why

    def test_rules_text_about_a_final_score_proposes_in_play(self):
        rows = _rows(30)
        ev = ta.prefix_evidence(rows, _text(rows, rules="Settles to the final score of the game"),
                                {})
        assert ta.propose(ev)[0] == ta.IN_PLAY

    def test_conflicting_strong_signals_refuse_to_propose(self):
        rows = _rows(30)
        text = _text(rows, source="ESPN box score", rules="the closing price at 4:00 pm ET")
        mode, why = ta.propose(ta.prefix_evidence(rows, text, {}))
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert "settlement source says" in why

    def test_shape_evidence_alone_is_never_enough(self):
        # An expiration gap and a jumpy price path look the same for a scheduled print and a
        # discrete announcement. Corroboration is not identification.
        rows = _rows(30)
        ev = ta.prefix_evidence(rows, _text(rows, gap_h=0.0),
                                {r["ticker"]: 3.0 for r in rows})
        mode, why = ta.propose(ev)
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert "only shape evidence" in why

    def test_no_evidence_at_all_refuses_rather_than_defaulting(self):
        # THE regression this file exists for: silence must not become `scheduled`.
        rows = _rows(30)
        mode, why = ta.propose(ta.prefix_evidence(rows, {}, {}))
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert mode != ta.SCHEDULED
        assert "no settlement source" in why

    def test_a_handful_of_markets_is_anecdote_not_evidence(self):
        rows = _rows(ta.MIN_MARKETS_TO_PROPOSE - 1)
        mode, why = ta.propose(ta.prefix_evidence(
            rows, _text(rows, source="CME CF Reference Rate"), {}))
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert "only" in why and "markets" in why

    def test_text_contradicted_by_the_price_path_refuses(self):
        # Rules text says scheduled; a quarter of the markets are still mid-book at the last
        # tick, which a scheduled settle does not do.
        rows = _rows(30)
        text = _text(rows, rules="the closing price at 4:00 pm ET")
        late = {r["ticker"]: 50.0 for r in rows}
        mode, why = ta.propose(ta.prefix_evidence(rows, text, late))
        assert mode == "INSUFFICIENT_EVIDENCE"
        assert "price path" in why

    def test_corroborated_text_is_reported_as_corroborated(self):
        rows = _rows(30)
        text = _text(rows, source="CME CF Reference Rate", gap_h=0.0)
        late = {r["ticker"]: 2.0 for r in rows}
        mode, why = ta.propose(ta.prefix_evidence(rows, text, late))
        assert mode == ta.SCHEDULED
        assert "expiration gap" in why and "price path" in why


class TestEvidenceSignals:
    def test_a_far_future_close_time_reads_as_in_play(self):
        # Kalshi sets a far-future fallback close_time on in-play sports; a large gap is
        # evidence, not a data error.
        rows = _rows(20)
        ev = ta.prefix_evidence(rows, _text(rows, gap_h=48.0), {})
        assert ev["gap_mode"] == ta.IN_PLAY

    def test_expiration_at_the_close_reads_as_scheduled(self):
        rows = _rows(20)
        assert ta.prefix_evidence(rows, _text(rows, gap_h=0.0), {})["gap_mode"] == ta.SCHEDULED

    def test_an_ambiguous_gap_votes_for_nothing(self):
        rows = _rows(20)
        assert ta.prefix_evidence(rows, _text(rows, gap_h=3.0), {})["gap_mode"] is None

    def test_the_candidate_clocks_are_used_when_markets_has_no_row(self):
        rows = [{"ticker": f"KXFOO-{i}", "series": "KXFOO",
                 "hours_to_close": 50.0, "hours_to_expiration": 2.0} for i in range(10)]
        assert ta.prefix_evidence(rows, {}, {})["gap_mode"] == ta.IN_PLAY

    def test_the_bar_and_band_are_the_designs_not_this_scripts(self):
        assert ta.UNCLASSIFIED_BAR == 0.05
        assert ta.BAND == (5.0, 7.0)
