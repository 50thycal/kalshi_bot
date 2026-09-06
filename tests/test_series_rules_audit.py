"""The series rules audit — verifying a recorded settlement mode against Kalshi's rulebook.

WHAT MUST NEVER BREAK, in the order in which breaking it would matter:

  * **INSUFFICIENT must stay reachable.** The audit is only safe to automate because it can
    fail to conclude. If uncertainty could be rendered as CONFIRMS, a machine's guess would be
    laundered into a human's signature on `rules_reviewed_at` — the exact failure the two-part
    graduation bar exists to prevent.
  * **the patch carries CONFIRMS only.** A contradiction needs the taxonomy fixed first; an
    insufficient row has nothing to sign.
  * **documents that disagree refuse.** Either the series has no single settlement semantics or
    the sample spans a rule change, and neither can be signed off.
  * **a Kalshi failure is not a verdict.** One unreachable series must degrade to INSUFFICIENT,
    not end the audit and not silently confirm.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import series_rules_audit as audit  # noqa: E402

from kalshi_bot import registry  # noqa: E402

IN_PLAY, SCHEDULED, DISCRETE = "in_play", "scheduled", "discrete"

# Rule-document shapes drawn from the corpus the patterns were tuned against.
DOC_INPLAY = {"source": "ESPN official box score", "title": "Final score",
              "rules": "the team that wins the game"}
DOC_SCHEDULED = {"source": "CME reference rate", "title": "Closing level",
                 "rules": "settles to the official closing price"}


def _fetch(docs, markets=8):
    def fake(prefix, want=8, timeout=12.0):
        return {"unique_markets": markets, "docs": list(docs), "schema": [],
                "statuses": ("settled", "open")}
    return fake


def test_confirms_when_the_evidence_names_the_recorded_mode(monkeypatch):
    monkeypatch.setattr(audit, "fetch_series_text", _fetch([DOC_INPLAY]))
    r = audit.audit_series("KXMLBGAME", IN_PLAY, timeout=1.0)
    assert r["verdict"] == audit.CONFIRMS
    assert r["implied"] == IN_PLAY


def test_contradicts_when_the_evidence_names_a_different_mode(monkeypatch):
    """The finding the audit exists to produce: a series traded under the wrong settlement
    model. Every book selecting on `mode=` has been picking it up, or missing it, wrongly."""
    monkeypatch.setattr(audit, "fetch_series_text", _fetch([DOC_SCHEDULED]))
    r = audit.audit_series("KXMLBGAME", IN_PLAY, timeout=1.0)
    assert r["verdict"] == audit.CONTRADICTS
    assert r["implied"] == SCHEDULED
    assert "recorded as in_play" in r["why"]


def test_insufficient_when_no_document_names_a_mode(monkeypatch):
    monkeypatch.setattr(audit, "fetch_series_text",
                        _fetch([{"source": "", "title": "A market", "rules": "see terms"}]))
    r = audit.audit_series("KXWHATEVER", IN_PLAY, timeout=1.0)
    assert r["verdict"] == audit.INSUFFICIENT
    assert r["implied"] is None


def test_insufficient_when_the_two_strong_signals_disagree(monkeypatch):
    """Kalshi's settlement-source field and its rules text pointing different ways is not a
    tiebreak situation — it means we do not know."""
    monkeypatch.setattr(audit, "fetch_series_text", _fetch([
        {"source": "CME reference rate", "title": "Final score",
         "rules": "the team that wins the game"}]))
    r = audit.audit_series("KXODD", IN_PLAY, timeout=1.0)
    assert r["verdict"] == audit.INSUFFICIENT
    assert "settlement source says" in r["why"]


def test_insufficient_when_distinct_documents_disagree_with_each_other(monkeypatch):
    monkeypatch.setattr(audit, "fetch_series_text", _fetch([DOC_INPLAY, DOC_SCHEDULED]))
    r = audit.audit_series("KXMIDSEASONCHANGE", IN_PLAY, timeout=1.0)
    assert r["verdict"] == audit.INSUFFICIENT
    assert "disagree" in r["why"]


def test_insufficient_when_no_rules_text_is_retrieved(monkeypatch):
    monkeypatch.setattr(audit, "fetch_series_text", _fetch([], markets=0))
    r = audit.audit_series("KXGONE", IN_PLAY, timeout=1.0)
    assert r["verdict"] == audit.INSUFFICIENT
    assert "no rules text" in r["why"]


def test_a_kalshi_failure_degrades_to_insufficient(monkeypatch):
    def boom(prefix, want=8, timeout=12.0):
        raise TimeoutError("kalshi is down")

    monkeypatch.setattr(audit, "fetch_series_text", boom)
    r = audit.audit_series("KXMLBGAME", IN_PLAY, timeout=1.0)
    assert r["verdict"] == audit.INSUFFICIENT
    assert "TimeoutError" in r["why"]


def test_evidence_is_counted_over_documents_not_markets(monkeypatch):
    """One rule document answers for every market under the prefix, but it answers ONCE.
    Counting it per market is how run `tax-6` reported "100% of 46 texts" from one document."""
    monkeypatch.setattr(audit, "fetch_series_text",
                        _fetch([dict(DOC_INPLAY, n_markets=46)], markets=46))
    r = audit.audit_series("KXMLBGAME", IN_PLAY, timeout=1.0)
    assert r["docs"] == 1
    assert r["markets"] == 46
    assert r["source_vote"][2] == 1


def test_the_patch_carries_confirms_only(capsys):
    # `markets` is set on every row a real run produces; without it these would trip the
    # total-fetch-failure guard and the test would pass for the wrong reason.
    results = [
        {"series": "KXGOOD", "verdict": audit.CONFIRMS, "markets": 8},
        {"series": "KXWRONG", "verdict": audit.CONTRADICTS, "markets": 8},
        {"series": "KXUNKNOWN", "verdict": audit.INSUFFICIENT, "markets": 8},
    ]
    audit.emit_patch(results, "someone")
    out = capsys.readouterr().out
    assert "KXGOOD" in out
    assert "KXWRONG" not in out
    assert "KXUNKNOWN" not in out


def test_report_leads_with_contradictions(capsys):
    """138 rows is too many to read top to bottom, so the section that can be costing money
    has to come first."""
    audit.report([
        {"series": "KXA", "verdict": audit.CONFIRMS, "recorded": IN_PLAY, "implied": IN_PLAY,
         "docs": 1, "markets": 8, "why": "ok"},
        {"series": "KXB", "verdict": audit.CONTRADICTS, "recorded": IN_PLAY,
         "implied": SCHEDULED, "docs": 1, "markets": 8, "why": "wrong"},
    ])
    out = capsys.readouterr().out
    assert out.index("## CONTRADICTS") < out.index("## CONFIRMS")


def test_it_audits_the_manifests_unreviewed_graduated_rows():
    """The audit's worklist and the registry's backlog have to be the same set, or the audit
    discharges rows nobody was asking about and leaves the debt standing."""
    manifest = audit.load_manifest()
    worklist = {s for s, r in manifest.items()
                if r.get("state") == "graduated" and not r.get("rules_reviewed_at")}
    assert worklist == set(registry.unreviewed_graduated())


def test_it_reads_the_same_manifest_the_worker_does():
    assert audit.MANIFEST_PATH == registry.MANIFEST_PATH


@pytest.mark.parametrize("verdict", [audit.CONTRADICTS, audit.INSUFFICIENT])
def test_a_non_confirming_verdict_never_reaches_the_patch(verdict, capsys):
    audit.emit_patch(
        [{"series": "KXFINE", "verdict": audit.CONFIRMS, "markets": 8},
         {"series": "KXNOPE", "verdict": verdict, "markets": 8}], "someone")
    assert "KXNOPE" not in capsys.readouterr().out


def test_a_total_fetch_failure_is_flagged_as_not_a_result(capsys):
    """`fetch_series_text` swallows its own HTTP errors, so a runner with no route to Kalshi
    produces 138 rows of INSUFFICIENT / "no rules text retrieved" — each individually plausible,
    collectively an infrastructure failure. Observed for real: this sandbox's network policy
    blocks api.elections.kalshi.com, and the first run read exactly that way.

    The report must refuse to be read as findings."""
    results = [{"series": s, "verdict": audit.INSUFFICIENT, "recorded": IN_PLAY,
                "implied": None, "docs": 0, "markets": 0, "why": "no rules text retrieved"}
               for s in ("KXA", "KXB", "KXC")]
    assert audit.looks_like_a_network_failure(results)
    audit.report(results)
    assert "NOT A RESULT" in capsys.readouterr().out


def test_the_patch_is_refused_after_a_total_fetch_failure(capsys):
    """An empty patch reads as 'nothing confirmed', which is a finding. It is not one."""
    results = [{"series": "KXA", "verdict": audit.INSUFFICIENT, "markets": 0}]
    audit.emit_patch(results, "someone")
    out = capsys.readouterr().out
    assert "refused" in out
    assert "rules_reviewed_by" not in out


def test_one_unreachable_series_among_many_is_not_a_network_failure():
    """The guard must not fire on a genuine per-series gap, or it would mask real findings."""
    results = [{"series": "KXA", "verdict": audit.CONFIRMS, "markets": 8},
               {"series": "KXB", "verdict": audit.INSUFFICIENT, "markets": 0}]
    assert not audit.looks_like_a_network_failure(results)


# --- the pattern fix -------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Will this video record 50000000+ views? Resolves Yes if it records 50,000,000+ views.",
    "Resolves Yes if the video records 50,000,000+ views.",
    "Will the channel record 10000+ subscribers?",
])
def test_a_bare_count_is_not_evidence_of_a_live_contest(text):
    """`records? \\d+\\+` was written for player props ("records 3+ goals") and matched
    "record 50000000+ views" on the YouTube view-count series, proposing in_play for a market
    with no contest in it. It produced two of the four CONTRADICTS in the first full audit
    (2026-09-06), which is how it was found.

    Same failure class as the bare clock time the pattern table already documents: a number is
    not evidence of a live contest — the thing being counted is."""
    from mmsell_taxonomy_audit import _RULES_PATTERNS, _match_mode
    assert _match_mode(text, _RULES_PATTERNS) != "in_play"


@pytest.mark.parametrize("text", [
    "Will the player record 3+ goals in the match?",
    "Resolves Yes if the player records 20+ points.",
    "Will the pitcher record 8+ strikeouts?",
    "Will he record 100+ yards?",
    "Will the player record 1,000+ yards this season?",
    "Will the keeper record 5+ saves?",
])
def test_the_player_prop_forms_the_pattern_exists_for_still_match(text):
    """Tightening must not cost the cases the pattern was written for — including the comma
    grouping Kalshi uses on large numbers."""
    from mmsell_taxonomy_audit import _RULES_PATTERNS, _match_mode
    assert _match_mode(text, _RULES_PATTERNS) == "in_play"
