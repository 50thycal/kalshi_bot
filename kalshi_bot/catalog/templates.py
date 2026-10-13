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

# v2: whitespace-insensitive, absent text equals empty text, and a date in a display title no
# longer makes its day number count as the market's own strike.
# v3: numbers in the mechanics compare by value, so 60 and 60.0 are the same timer.
# v4: a name may contain digits and "of"/parentheses ("49ers", "W75", "(Lima) Round of 32"), a
# number inside a word is never a strike, and a noun's plural after a strike ("2 goals") is
# its singular.
METHOD = "rules-template-v4"
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
_NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?(?![\w])")
_WORD = r"(?!(?:Yes|No)\b)(?:[A-Z][\w'’.&-]*|\d+[A-Za-z][\w'’.&-]*)"
_PIECE = r"\(?" + _WORD + r"\)?"
_NAME = re.compile(
    r"(?<![\w<])" + _PIECE + r"(?:\s+(?:of\s+)?(?:" + _PIECE + r"|\d+(?![\w.])|<n>))*"
)
_PLURAL = re.compile(r"(<n>\+?\s+[a-z]*[a-rt-z])s\b")


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
        display = _TIME.sub(" ", _DATE.sub(" ", raw.get(key) or ""))
        found.update(_value(token) for token in _NUMBER.findall(display))
    found.discard(None)
    return found


def _canonical(value):
    """Same value, same encoding: a number is its float, however the payload typed it."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    return value


def mask(text, own_numbers=frozenset()):
    text = " ".join((text or "").split())
    text = _DATE.sub("<date>", text)
    text = _TIME.sub("<time>", text)
    text = _NUMBER.sub(
        lambda m: "<n>" if _value(m.group()) in own_numbers else m.group(), text
    )
    return _PLURAL.sub(r"\1", _NAME.sub("<name>", text))


def template_document(raw):
    """The wording and mechanics two sibling markets must share to share one approval."""
    raw = raw or {}
    own = _own_numbers(raw)
    return {
        "method": METHOD,
        "text": {key: mask(raw.get(key), own) for key in TEXT_FIELDS},
        "exact": {key: _canonical(raw.get(key)) for key in EXACT_FIELDS},
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


def differences(document, approved, limit=400):
    """The template parts where a wording version differs from the closest approved one."""
    def parts(doc):
        return {f"{section}.{key}": value for section in ("text", "exact", "strikes_present",
                "keyed") for key, value in doc[section].items()}

    if not approved:
        return None
    def encoded(value):
        return json.dumps(value, sort_keys=True, default=str)

    mine = {key: encoded(value) for key, value in parts(document).items()}
    closest = min(
        ({key: encoded(value) for key, value in parts(doc).items()} for doc in approved),
        key=lambda other: sum(mine[k] != other.get(k) for k in mine),
    )
    differing = [
        {"field": key, "unapproved": mine[key][:limit], "approved": str(closest.get(key))[:limit]}
        for key in mine
        if mine[key] != closest.get(key)
    ]
    # Identical parts with a different hash mean the method itself differs; say so.
    return differing or [{"field": "method", "unapproved": document.get("method"),
                          "approved": "see bound approvals"}]
