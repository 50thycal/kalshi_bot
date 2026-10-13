"""Series rule templates: one approval for every market whose contract wording matches.

A market's rules hash covers every listing field, so it differs for each strike, date and
player. A template keeps the contract wording and the settlement mechanics exactly, and masks
only what varies between sibling markets: calendar dates, clock times, the market's own strike
numbers, and capitalised names (players, teams, cities, stations, tournaments). Display titles
and listing clocks are left out. Yes/No are never masked.

Masking can only merge markets whose remaining wording is identical, so a reworded rule (a
new participation test, an added disclaimer, a changed strike type) is a different template
and needs its own approval. A name or source change inside the wording is masked here; the
series payload, which carries settlement sources and terms links, is checked separately by
the parent rules hash. Stdlib only: no catalog imports.
"""

from __future__ import annotations

import hashlib
import json
import re

METHOD = "rules-template-v1"
# Contract wording: kept, with dates, times, own strike numbers and names masked.
TEXT_FIELDS = ("rules_primary", "rules_secondary", "early_close_condition")
# Settlement mechanics: kept exactly.
EXACT_FIELDS = (
    "can_close_early", "settlement_timer_seconds", "strike_type", "settlement_sources",
    "contract_url", "contract_terms_url", "fee_type", "fee_multiplier", "mutually_exclusive",
)
# Strike values vary per market; only whether each is set is kept.
STRIKE_FIELDS = ("floor_strike", "cap_strike", "functional_strike")
# Per-market identities (player/team ids); only which keys exist is kept.
KEYED_FIELDS = ("custom_strike",)
# Display text: never part of the template, but its numbers are the market's own strikes.
DISPLAY_FIELDS = ("title", "yes_sub_title", "no_sub_title")
# Listing clocks: part of each market, not of the contract wording.
CLOCK_FIELDS = ("open_time", "close_time", "expected_expiration_time")

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE = re.compile(_MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*\d{4})?")
_TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)\b(?:\s+[A-Z]{2,4}T)?")
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")
_WORD = r"(?!(?:Yes|No)\b)[A-Z][\w'’.&-]*"
_NAME = re.compile(r"\b" + _WORD + r"(?:\s+" + _WORD + r")*")


def _value(token):
    try:
        return repr(float(token.replace("$", "").replace(",", "")))
    except ValueError:
        return None


def _own_numbers(raw):
    found = set()
    for key in STRIKE_FIELDS:
        if isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool):
            found.add(repr(float(raw[key])))
    for key in DISPLAY_FIELDS:
        found.update(_value(token) for token in _NUMBER.findall(raw.get(key) or ""))
    found.discard(None)
    return found


def mask(text, own_numbers=frozenset()):
    if text is None:
        return None
    text = _DATE.sub("<date>", text)
    text = _TIME.sub("<time>", text)
    text = _NUMBER.sub(
        lambda m: "<n>" if _value(m.group()) in own_numbers else m.group(), text
    )
    return _NAME.sub("<name>", text)


def template_document(raw):
    """The wording and mechanics two sibling markets must share to share one approval."""
    raw = raw or {}
    own = _own_numbers(raw)
    return {
        "method": METHOD,
        "text": {key: mask(raw.get(key), own) for key in TEXT_FIELDS},
        "exact": {key: raw.get(key) for key in EXACT_FIELDS},
        "strikes_present": {key: raw.get(key) is not None for key in STRIKE_FIELDS},
        "keyed": {key: sorted((raw.get(key) or {}).keys()) for key in KEYED_FIELDS},
    }


def template_hash(raw):
    encoded = json.dumps(
        template_document(raw), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def bind(semantics, raw):
    """Fill a template's per-market placeholders from the market's own listing."""
    event = (raw or {}).get("event_ticker") or ""
    values = {"event_ticker": event, "event_suffix": event.split("-", 1)[-1]}
    bound = dict(semantics)
    key = bound.get("shared_exposure_key")
    if isinstance(key, str):
        bound["shared_exposure_key"] = key.format(**values) if event else None
    return bound
