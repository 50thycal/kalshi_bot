"""The canary's crypto/non-crypto monitoring slice — classification, not economics.

The arithmetic in `scripts/mmsell_canary_slices.py` is the same accounting the
canonical live providers already use and is pinned there. What is pinned HERE is
the part that has actually gone wrong in this repository: deciding which markets
are crypto.

XOS-000009 is open because the production skip list `BTC+ETH+SOL+DOGE+XRP+CRYPTO`
is a SUBSTRING blocklist and therefore drops non-crypto markets. A monitoring
slice built the same way would silently move markets between its own buckets and
then be read as evidence about crypto — a worse failure than the original, since
a skip list at least stops trading while a mis-sliced report invites a decision.
"""

from __future__ import annotations

import pytest

from scripts.mmsell_canary_slices import CRYPTO_SERIES, SLICES, classify_series


@pytest.mark.parametrize("series", ["KXBTC", "KXBTCD", "KXETHD", "KXBNBMINMON"])
def test_known_crypto_series_are_crypto(series):
    assert classify_series(series) == "crypto"


@pytest.mark.parametrize("series", ["KXNFLGAME", "KXWTI", "KXGOLDD", "KXFEDMENTION"])
def test_known_non_crypto_series_are_non_crypto(series):
    """Commodities and FX price-strike markets share crypto's taxonomy shape
    (`price_strike`/scheduled) but are not crypto. The slice is about the
    UNDERLYING, which the taxonomy alone does not answer."""
    assert classify_series(series) == "non_crypto"


def test_the_substring_trap_is_not_reintroduced():
    """`KXHEGSETHANNOUNCEOUT` contains "ETH" and is a Hegseth announcement market.

    This is the concrete case behind XOS-000009. An exact-series match puts it
    where it belongs; any prefix or substring rule would file a politics contract
    under crypto and then let someone read a crypto verdict off it."""
    assert classify_series("KXHEGSETHANNOUNCEOUT") == "non_crypto"
    assert not any(s in "KXHEGSETHANNOUNCEOUT" for s in ("KXETH", "KXBTC")) or \
        classify_series("KXHEGSETHANNOUNCEOUT") == "non_crypto"


def test_an_unknown_series_is_unclassified_not_non_crypto():
    """The residual bucket stays visible. Folding unknowns into `non_crypto`
    would make the larger, better-looking slice absorb every market nobody has
    classified — and would hide the fact that the list needs extending."""
    assert classify_series("KXBRANDNEWTHING") == "unclassified"


def test_a_missing_series_is_unclassified():
    """A ticker absent from `mmsell_settlement_meta` has no series. Absence of
    data is not evidence of a market type."""
    assert classify_series(None) == "unclassified"
    assert classify_series("") == "unclassified"


def test_classification_is_case_insensitive_and_trimmed():
    assert classify_series(" kxbtcd ") == "crypto"


def test_every_classification_lands_in_a_reported_slice():
    """A bucket the report does not print is a bucket that hides markets."""
    for series in ("KXBTC", "KXNFLGAME", "KXBRANDNEWTHING", None):
        assert classify_series(series) in SLICES


def test_the_crypto_list_is_whole_tickers_not_fragments():
    """A short fragment on the list would reintroduce substring behaviour the
    moment anyone changed the matcher. Every entry is a real series ticker."""
    for entry in CRYPTO_SERIES:
        assert entry.startswith("KX") and len(entry) >= 5, entry
        assert entry == entry.upper()
