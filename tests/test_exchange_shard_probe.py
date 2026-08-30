"""XOS-000014: the read-only probe that must name what Kalshi refuses us on.

31% of the Cmmsell10 canary's live entry orders come back 404 `user_not_found` —
"Exchange user not found. For Predictions: reference documentation Exchange
Sharding documentation" — and the refusals are ~100% per series (every MLB, ITF
and BTCD attempt; 0% across the other seventeen series). Kalshi has sharded its
exchange and this codebase has no concept of a shard: one `kalshi_base_url`, every
order posted to it.

The fix is unknown until we can see what differs between a market Kalshi accepts
our orders on and one it does not, and GUESSING Kalshi's field name is exactly how
this repo has been burned before (`_tier_of` carries three fallback names for the
same reason). So the probe measures instead. What this file pins is that the probe
stays a probe: read-only, both sides sampled, and loud when it cannot sample.
"""

from __future__ import annotations

import logging

from kalshi_bot.main import _probe_exchange_shard


class _Client:
    """Records every call. Any method beyond `get_markets` is an order path and
    must never be reached — the whole point is that this probe risks nothing."""

    def __init__(self, payloads: dict, fail: set[str] | None = None):
        self._payloads = payloads
        self._fail = fail or set()
        self.series_asked: list[str] = []

    def get_markets(self, *, series_ticker=None, limit=1, status="open",
                    cursor=None, min_close_ts=None):
        """Mirrors what `obs.series_fetch.fetch_markets_by_series` really calls —
        including `cursor`, which the probe reaches Kalshi through. A double that
        accepted less would pass while the real call path raised."""
        self.series_asked.append(series_ticker)
        if series_ticker in self._fail:
            raise RuntimeError("boom")
        return self._payloads.get(series_ticker, {"markets": []})

    def __getattr__(self, name):  # pragma: no cover - the assertion IS the point
        raise AssertionError(f"the shard probe must not call {name!r}")


def _market(**extra):
    base = {"ticker": "T-1", "status": "open", "yes_bid": 5, "yes_ask": 7}
    return {"markets": [base | extra]}


def test_probe_samples_both_refused_and_accepted_series(caplog):
    """A field present on the refused markets proves nothing unless the accepted
    ones lack it. Sampling only the broken side would find a difference that was
    never a difference."""
    payloads = {
        "KXMLBHR": _market(exchange_shard="predictions"),
        "KXMLBTOTAL": _market(exchange_shard="predictions"),
        "KXITFMATCH": _market(exchange_shard="predictions"),
        "KXNCAAFSPREAD": _market(exchange_shard="main"),
        "KXLALIGASCORE": _market(exchange_shard="main"),
    }
    client = _Client(payloads)
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(client)

    assert "KXMLBHR" in client.series_asked          # a refused series
    assert "KXNCAAFSPREAD" in client.series_asked    # and an accepted one
    text = caplog.text
    assert "REFUSED" in text and "ACCEPTED" in text


def test_probe_surfaces_a_differing_value_not_only_a_missing_key(caplog):
    """The likely shape: both sides carry the field, with different VALUES.

    A key-set diff alone would report "no difference" and send someone looking in
    the wrong place, so the probe logs the value of any shard-ish field too.
    """
    payloads = {
        "KXMLBHR": _market(exchange_shard="predictions"),
        "KXNCAAFSPREAD": _market(exchange_shard="main"),
    }
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(_Client(payloads))

    text = caplog.text
    assert "predictions" in text and "main" in text
    assert "only-on-refused=(none)" in text     # the key sets are identical...
    assert "exchange_shard" in text             # ...and the value is what differs


def test_probe_is_read_only(caplog):
    """`_Client.__getattr__` raises on anything but `get_markets`. If this passes,
    the probe touched no order path, no balance and no position."""
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(_Client({"KXMLBHR": _market()}))
    assert "shard probe" in caplog.text


def test_a_series_with_no_open_market_is_reported_not_skipped_silently(caplog):
    """Out-of-season MLB returns nothing. That must read as "could not sample",
    not as "no difference found" — the second would close the investigation on
    evidence that was never gathered."""
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(_Client({}))
    assert "no open market to sample" in caplog.text
    assert "DIFF" not in caplog.text            # nothing sampled -> no verdict


def test_one_failing_series_does_not_stop_the_probe(caplog):
    """A probe that dies on the first refused series learns nothing about the rest.

    The per-series failure is reported by the shared fetch helper — which is why
    the probe goes through it — and the probe then says which series it could not
    sample, so a transport error never reads as "no difference found".
    """
    payloads = {"KXMLBTOTAL": _market(), "KXNCAAFSPREAD": _market()}
    client = _Client(payloads, fail={"KXMLBHR"})
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(client)

    assert "markets fetch failed for series KXMLBHR" in caplog.text
    assert "failed=['KXMLBHR']" in caplog.text
    assert "KXNCAAFSPREAD" in client.series_asked      # it kept going
