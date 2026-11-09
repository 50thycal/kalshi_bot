"""Tests for the mention-listener research prototype (docs/research/mention_listener_20261109).

The rules text below is Kalshi's own market text, fetched 2026-11-09 through the ops channel
(results rl-mt-kxburnhammention-26nov04-budg, rl-mt-rningsmentionhims-26nov02-test). Inflection
cases follow Kalshi's payout criteria; the compound/hyphen cases are the published comparison
table of the MIT `mention-rules` project (README), used here as data, not code.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

_P = Path(__file__).resolve().parents[1] / "docs/research/mention_listener_20261109/mention_listener.py"
_spec = importlib.util.spec_from_file_location("mention_listener", _P)
ml = importlib.util.module_from_spec(_spec)
sys.modules["mention_listener"] = ml
_spec.loader.exec_module(ml)

BURNHAM_PRIMARY = ("If \n Andy Burnham says Budget as part of next Prime Minister's Questions "
                   "(UK House of Commons)\n, then the market resolves to Yes.")
BURNHAM_SECONDARY = """How this is resolved
Video of the next Prime Minister's Questions (UK House of Commons) using the live broadcast or stream.
If a consensus by Kalshi employees cannot be reached using video, internal & external (official) transcripts of the next Prime Minister's Questions (UK House of Commons) will be used.

Payout Criteria
The exact phrase/word, or a plural or possessive form of the phrase/word.
Plural nouns occurring within proper nouns and noun phrases (e.g. names, titles, etc).
Andy Burnham speaking in an official capacity.
For phrases with slashes like "Doge/Dogecoin," either word satisfies the criterion.

Doesn't count for payout
Grammatical/tense inflections are otherwise not included.
Additional content not included in the original broadcast or stream.
Presence alone during a livestream is not sufficient for the market to qualify (e.g., inaudible private conversations on camera, unofficial statements picked up on a hot mic).
Except as otherwise specified, previously aired content, archival footage, and recordings captured at times, places, or events wholly separate from the featured address."""

HIMS_SECONDARY = """How this is resolved
Video of the Hims & Hers Health, Inc. earnings call.
If a consensus by Kalshi employees cannot be reached using video, transcripts of the Hims & Hers Health, Inc. earnings call will be used according to the news publications listed in the contract.

Payout criteria
The exact phrase/word, or a plural or possessive form of the phrase/word.

Doesn't count for payout
Grammatical/tense inflections."""


def spec(term, prim="If X says it, then Yes.", sec=BURNHAM_SECONDARY):
    return ml.parse_rules(term, prim, sec)


# --- rules -> spec --------------------------------------------------------------------------

def test_parse_real_burnham_rules():
    s = ml.parse_rules("Budget", BURNHAM_PRIMARY, BURNHAM_SECONDARY)
    assert s.speaker == "Andy Burnham"
    assert s.source == "live_video"
    assert s.plural_possessive and s.threshold == 1
    assert s.excludes_archival and s.excludes_hot_mic
    assert s.warnings == ()


def test_parse_real_hims_rules_source_is_video_of_call():
    s = ml.parse_rules("Testosterone", "If Hims says Testosterone during the call, then Yes.",
                       HIMS_SECONDARY)
    assert s.source == "live_video" and s.plural_possessive
    # the Hims template has no slash clause and no archival clause — and we say so
    assert not s.excludes_archival


@pytest.mark.parametrize("term,alts,k", [
    ("Oil / Gas / Gasoline", (("oil",), ("gas",), ("gasoline",)), 1),
    ("Trump (5+ times)", (("trump",),), 5),
    ("Iran (3+ times)", (("iran",),), 3),
    ("Tax 5+ times", (("tax",),), 5),
    ("Border 5+", (("border",),), 5),
    ("Freedom 250", (("freedom", "250"),), 1),            # a name, not a count
    ("AI / Artificial Intelligence", (("ai",), ("artificial", "intelligence")), 1),
    ("Justice Department / DOJ", (("justice", "department"), ("doj",)), 1),
])
def test_parse_term(term, alts, k):
    assert ml.parse_term(term) == (alts, k)


def test_missing_clauses_become_warnings_not_guesses():
    s = ml.parse_rules("Budget", "Budget gets said.", "Settles somehow.")
    assert s.speaker is None and s.source == "unknown"
    assert len(s.warnings) >= 3
    assert not s.plural_possessive        # defaults to the strict reading


# --- matcher: Kalshi criteria -----------------------------------------------------------------

@pytest.mark.parametrize("term,text,expected", [
    ("Russia", "Russia's economy is weak.", True),          # possessive
    ("Russia", "The Russian delegation left.", False),       # other inflection
    ("Tariff", "New tariffs were announced.", True),         # plural
    ("Deport", "They were deported yesterday.", False),      # tense inflection
    ("fire", "The wildfire spread fast.", False),            # compound: Kalshi no
    ("joy", "Don't be a killjoy.", False),
    ("joy", "A joyful day.", False),
    ("fat", "Order the non-fat latte.", True),               # hyphen: observed yes
    ("Fake News", "That is non-fake news, folks.", True),    # observed settlement
    ("Democrat", "A democrat-inclined voter.", True),        # observed settlement
    ("Oil", "He toiled all day.", False),                    # substring never counts
    ("Oil", "Big Oil's lobby.", True),
    ("Policy", "Our policies changed.", True),               # y -> ies
    ("Crisis", "Two crises at once.", True),                 # irregular plural
    ("Child", "Childcare costs.", False),
    ("Data Center", "new data centers in Iowa", True),       # phrase, plural on the head
    ("Data Center", "data about the center", False),
    ("AI", "AI's impact on jobs", True),
])
def test_matcher_kalshi_semantics(term, text, expected):
    assert ml.Matcher(spec(term)).resolves_yes(text) is expected


def test_slash_alternatives_and_case():
    m = ml.Matcher(spec("Oil / Gas / Gasoline"))
    assert m.resolves_yes("GASOLINE prices fell")
    assert not m.resolves_yes("gaseous clouds")


def test_count_threshold():
    m = ml.Matcher(spec("Trump (5+ times)"))
    four = "Trump said. Trump did. Trump's plan. Trumps everywhere."
    assert m.count(four) == 4 and not m.resolves_yes(four)
    assert m.resolves_yes(four + " And Trump again.")


def test_hyphen_hit_is_labelled_as_inference():
    hits = ml.Matcher(spec("fat")).find(ml.tokenize("non-fat milk"))
    assert [h.via for h in hits] == ["hyphen-part"]


# --- streaming detector ---------------------------------------------------------------------

def test_detector_provisional_then_confirmed_with_latency():
    d = ml.StreamDetector([spec("Measles")], stable_updates=2)
    d.on_partial(["THE", "MEASLES"], [10.0, 10.4], audio_pos=10.9)
    assert d.events == []                                  # one sighting is not stable
    d.on_partial(["THE", "MEASLES", "OUTBREAK"], [10.0, 10.4, 10.9], audio_pos=11.2)
    assert [e.kind for e in d.events] == ["PROVISIONAL"]
    assert math.isclose(d.events[0].latency, 0.8)
    d.on_final(["THE", "MEASLES", "OUTBREAK"], [10.0, 10.4, 10.9], audio_pos=12.0)
    assert [e.kind for e in d.events] == ["PROVISIONAL", "CONFIRMED"]
    assert d.counts()["Measles"] == 1


def test_detector_revised_partial_never_confirms():
    d = ml.StreamDetector([spec("Dow")], stable_updates=1)
    d.on_partial(["THE", "DOW"], None, 5.0)                # ASR first hears "Dow"...
    d.on_final(["THE", "DOWN", "SIDE"], None, 6.0)         # ...then revises to "downside"
    assert [e.kind for e in d.events] == ["PROVISIONAL"]
    assert d.counts()["Dow"] == 0                          # a false trigger, visible as such


def test_detector_counts_across_segments():
    d = ml.StreamDetector([spec("Trump (5+ times)")])
    for i in range(5):
        d.on_final(["TRUMP", "SAID"], None, float(i))
    assert d.counts()["Trump (5+ times)"] == 5


# --- hazard model ---------------------------------------------------------------------------

def test_hazard_reproduces_prior_at_start_and_decays():
    h = ml.HazardModel(0.60)
    assert math.isclose(h.p_remaining(0.0, 0), 0.60, rel_tol=1e-9)
    assert h.p_remaining(0.5, 0) < 0.60 and h.p_remaining(1.0, 0) == 0.0
    assert h.p_remaining(0.3, 1) == 1.0


def test_hazard_count_market():
    h = ml.HazardModel(0.85, threshold=5)
    assert math.isclose(h.p_remaining(0.0, 0), 0.85, rel_tol=1e-6)
    assert h.p_remaining(0.5, 4) > h.p_remaining(0.5, 2)


def test_context_multiplier_is_the_llm_hook():
    h = ml.HazardModel(0.30, context=ml.keyword_context(["vaccine", "outbreak"], boost=3.0))
    base = ml.HazardModel(0.30).p_remaining(0.5, 0)
    assert h.p_remaining(0.5, 0, "a new outbreak in Texas") > base
    assert h.p_remaining(0.5, 0, "the stock market rallied") == pytest.approx(base)


# --- cycle-1 regressions (adversarial probe, 2026-11-09) ------------------------------------

@pytest.mark.parametrize("term,text,surface,via", [
    ("AI", "Our A.I. strategy", "ai", "exact"),                    # F1 dotted acronym
    ("AI", "our a i strategy", "a i", "acronym-spelled"),          # F1 ASR spells letters
    ("Email", "send an e-mail", "email", "exact"),                 # F5 hyphenated spelling
    ("5G", "five g rollout", "five g", "spoken-number"),           # F4 spoken digits
    ("COVID-19", "covid nineteen", "covid nineteen", "spoken-number"),
    ("Free Bus / Fast & Free Buses", "fast and free buses",        # F3 ampersand
     "fast and free buses", "exact"),
])
def test_cycle1_speech_forms(term, text, surface, via):
    hits = ml.Matcher(spec(term)).find(ml.tokenize(text))
    assert [(h.surface, h.via) for h in hits] == [(surface, via)]


def test_cycle1_overlapping_alternatives_count_once():                # F2
    m = ml.Matcher(spec("Trump / President Trump (5+ times)"))
    assert m.count("president trump said trump") == 2


def test_cycle1_letters_of_real_words_are_not_acronyms():
    assert ml.Matcher(spec("Oil")).count("o i l") == 0


def test_cycle1_everyday_word_terms_warn():
    s = spec("US")
    assert any("common word" in w for w in s.warnings)


# --- cycle-5 regressions (third untouched earnings call, 2026-11-09) -------------------------

@pytest.mark.parametrize("term,text,expected", [
    ("E-commerce", "our global e commerce business", 1),      # ASR splits the hyphen
    ("E-commerce", "ecommerce sales", 1),                     # ...or joins it
    ("Catalog", "our catalogues and catalogue", 2),           # UK spelling from ASR
    ("Data Center", "a new data centre", 1),
    ("Four", "thanks for joining", 0),                         # no short-word suffix swaps
])
def test_cycle5_spelling_and_hyphen_variants(term, text, expected):
    assert ml.Matcher(spec(term)).count(text) == expected


def test_cycle5_fuzzy_tier_is_separate_and_catches_excluded_inflections():
    """The fuzzy tier exists to flag ASR near-misses — and cycle 5 showed it also flags words
    Kalshi EXCLUDES ("promotional" for Promotion). It must never count as a hit."""
    m = ml.Matcher(spec("Promotion"))
    toks = ml.tokenize("a promotional event")
    assert m.find(toks) == []
    assert [h.via for h in m.find_fuzzy(toks)] == ["fuzzy"]
