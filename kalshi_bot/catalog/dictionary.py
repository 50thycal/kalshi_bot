"""Preset zlib dictionary for catalog documents (storage encoding v2).

One catalog document is ~2.5-3.5 KB of canonical JSON, but most of it is key names and
boilerplate that zlib cannot share across rows: each row is compressed alone. A preset
dictionary carries that shared text once, in code, so each row stores only what differs.
Measured on real Kalshi market payloads (2026-10-10, WS-024): 1,064 -> 571 and
1,499 -> 1,045 bytes against the v1 per-row encoding.

The bytes are part of the storage format. Rows written with them can only be read with
exactly these bytes, so they are FROZEN: never edit this module's content. A better
dictionary is a new version with its own prefix, and every old version stays readable.
`tests/test_market_catalog.py` pins the digest.
"""

import json

PREFIX = b"catalog:zdict:1\x00"


def _canonical(value):
    # Same encoding as store.encode; repeated here so the frozen bytes cannot drift with it.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _document(kind, ticker, series, raw, parents):
    return {
        "exchange_created_at": "2026-10-01T00:00:00.000000Z",
        "first_seen_at": "2026-10-01T00:00:00.000000+00:00",
        "kind": kind,
        "last_seen_at": "2026-10-01T00:00:00.000000+00:00",
        "legacy_classification": None,
        "legacy_registry": None,
        "parent_rules_hashes": parents,
        "raw": raw,
        "rules_hash": "",
        "schema_version": 1,
        "series_ticker": series,
        "source": "kalshi_rest",
        "ticker": ticker,
    }


_SERIES = {
    "additional_prohibitions": [],
    "category": "",
    "contract_terms_url": "https://kalshi-public-docs.s3.amazonaws.com/contract_terms/.pdf",
    "contract_url": "https://kalshi-public-docs.s3.amazonaws.com/regulatory/product-certifications/.pdf",
    "fee_multiplier": 1,
    "fee_type": "quadratic",
    "frequency": "daily",
    "product_metadata": None,
    "settlement_sources": [{"name": "", "url": "https://"}],
    "tags": [],
    "ticker": "KX",
    "title": "",
}
_EVENT = {
    "category": "",
    "collateral_return_type": "",
    "event_ticker": "KX-26OCT01",
    "exchange_index": 0,
    "last_updated_ts": "2026-10-01T00:00:00.000000Z",
    "mutually_exclusive": False,
    "product_metadata": None,
    "series_ticker": "KX",
    "settlement_sources": [{"name": "", "url": "https://"}],
    "strike_date": "2026-10-01T00:00:00Z",
    "strike_period": "",
    "sub_title": "On Oct 1, 2026",
    "title": "",
}
_MARKET = {
    "can_close_early": True,
    "cap_strike": None,
    "close_time": "2026-10-01T00:00:00Z",
    "created_time": "2026-10-01T00:00:00.000000Z",
    "custom_strike": None,
    "early_close_condition": "This market will close and expire early if the event occurs.",
    "event_ticker": "KX-26OCT01",
    "exchange_index": 0,
    "expected_expiration_time": "2026-10-01T00:00:00Z",
    "expiration_time": "2026-10-01T00:00:00Z",
    "expiration_value": "",
    "floor_strike": None,
    "last_price_dollars": "0.0000",
    "latest_expiration_time": "2026-10-01T00:00:00Z",
    "market_type": "binary",
    "no_ask_dollars": "1.0000",
    "no_bid_dollars": "0.0000",
    "no_sub_title": "",
    "notional_value_dollars": "1.0000",
    "occurrence_datetime": "2026-10-01T00:00:00Z",
    "open_interest_fp": "0.00",
    "open_time": "2026-10-01T00:00:00Z",
    "previous_price_dollars": "0.0000",
    "previous_yes_ask_dollars": "1.0000",
    "previous_yes_bid_dollars": "0.0000",
    "price_level_structure": "linear_cent",
    "price_ranges": [{"end": "1.0000", "start": "0.0000", "step": "0.0100"}],
    "result": "",
    "rules_primary": "If the  is above  on Oct 1, 2026, then the market resolves to Yes.",
    "rules_secondary": "",
    "settlement_bounds_type": "default",
    "settlement_timer_seconds": 300,
    "settlement_ts": "2026-10-01T00:00:00.000000Z",
    "settlement_value_dollars": "0.0000",
    "status": "finalized",
    "strike_type": "greater",
    "subtitle": "",
    "ticker": "KX-26OCT01-T0",
    "title": "",
    "updated_time": "2026-10-01T00:00:00.000000Z",
    "volume_24h_fp": "0.00",
    "volume_fp": "0.00",
    "yes_ask_dollars": "1.0000",
    "yes_ask_size_fp": "0.00",
    "yes_bid_dollars": "0.0000",
    "yes_bid_size_fp": "0.00",
    "yes_sub_title": "",
}
_PARENTS = {"event:KX-26OCT01": "", "series:KX": ""}

# zlib favours the end of a dictionary, so the most common document (a market) goes last.
DICTIONARY = "".join(
    _canonical(doc)
    for doc in (
        _document("series", "KX", "KX", _SERIES, {}),
        _document("event", "KX-26OCT01", "KX", _EVENT, {"series:KX": ""}),
        _document("market", "KX-26OCT01-T0", "KX", _MARKET, _PARENTS),
    )
).encode()
