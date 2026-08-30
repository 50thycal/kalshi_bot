"""XOS-000014: the read-only probe that must name what Kalshi refuses us on.

31% of the Cmmsell10 canary's live entry orders come back 404 `user_not_found` —
"Exchange user not found. For Predictions: reference documentation Exchange
Sharding documentation" — and the refusals are ~100% per series (every MLB, ITF
and BTCD attempt; 0% across the other seventeen series). Kalshi has sharded its
exchange and this codebase has no concept of a shard: one `kalshi_base_url`, every
order posted to it.

Kalshi's Exchange Sharding doc names the mechanism: `exchange_index` rides on GET
/markets and GET /events, and — the part that decides our fix — "programmatic
traders must preallocate collateral on a given exchange shard before order
placement". Those are two different failures with two different remedies. Routing
to a shard we hold no balance on fails just the same, and no routing code fixes
that; it is an operator funding decision.

So the probe answers both halves against our OWN account rather than against the
doc: which index the refused series carry versus the accepted ones, and which
indexes our balance actually reaches. What this file pins is that the probe stays
a probe — read-only, both sides sampled, loud when it cannot sample, and never
logging a balance amount, because these lines are read back through the ops
channel onto a public branch.
"""

from __future__ import annotations

import logging

from kalshi_bot.main import _probe_exchange_shard


class _Client:
    """Records every call. Any method beyond the two read endpoints is an order
    path and must never be reached — the whole point is that this probe risks
    nothing."""

    def __init__(self, payloads: dict, fail: set[str] | None = None,
                 balance: dict | None = None):
        self._payloads = payloads
        self._fail = fail or set()
        self._balance = balance if balance is not None else {"balance": 12345}
        self.series_asked: list[str] = []

    def get_balance(self):
        if self._balance == "boom":
            raise RuntimeError("balance unavailable")
        return self._balance

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


#: The REAL payload shape, copied from what the probe measured in production on
#: 2026-08-30: the key is `balance_breakdown` and the amount is a decimal STRING.
#: The first version of this file invented `exchange_balances` with an int amount,
#: and the probe duly reported "no per-index breakdown" against an account that had
#: one. A fixture that agrees with the code instead of with the venue proves nothing.
_FUNDED_SHARDS = {
    "balance": 4213,
    "balance_breakdown": [
        {"exchange_index": 0, "balance": "42.13"},
        {"exchange_index": 3, "balance": "0"},
    ],
    "balance_dollars": "42.13",
}


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
    """`_Client.__getattr__` raises on anything but the two read endpoints. If this
    passes, the probe touched no order path and no position."""
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(_Client({"KXMLBHR": _market()}))
    assert "shard probe" in caplog.text


def test_probe_reports_which_shards_are_funded(caplog):
    """Kalshi requires collateral preallocated on a shard before it takes an order
    there, so an unfunded index is a funding problem no routing code can fix.
    Reporting it beside the market-side diff is what stops us writing routing for
    a shard we were never provisioned on."""
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(_Client({}, balance=_FUNDED_SHARDS))

    text = caplog.text
    assert "shard probe funding" in text
    assert "no per-index breakdown" not in text
    assert '{"exchange_index": 0, "funded": true}' in text
    assert '{"exchange_index": 3, "funded": false}' in text


def test_probe_never_logs_a_balance_amount(caplog):
    """These lines are read back through the ops channel, which commits results to
    a PUBLIC branch. The breakdown must carry indexes and a bool, never money."""
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(_Client({}, balance=_FUNDED_SHARDS))

    assert "4213" not in caplog.text
    assert "42.13" not in caplog.text


def test_the_breakdown_is_found_by_shape_not_by_key_name(caplog):
    """Kalshi renamed a payload key on us before (`_tier_of` carries three fallbacks
    for it), and guessing this one wrong is what broke the first version of the
    probe. Any list whose entries carry `exchange_index` is the breakdown."""
    renamed = {"balance": 1, "per_exchange": [{"exchange_index": 7, "balance": "9.99"}]}
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(_Client({}, balance=renamed))

    assert '{"exchange_index": 7, "funded": true}' in caplog.text


def test_a_string_amount_counts_as_funded(caplog):
    """Kalshi sends money as a decimal string here. Treating only numbers as money
    would report every funded shard as unfunded — wrong in the direction that looks
    safe, and it would have sent us to ask for funds we already have."""
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(_Client({}, balance={
            "balance_breakdown": [{"exchange_index": 0, "balance": "0.01"}]}))

    assert '"funded": true' in caplog.text


def test_an_unparseable_amount_is_not_funded(caplog):
    """An amount we cannot read must not be optimistically called funded — that
    would close the funding question on a value nobody understood."""
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(_Client({}, balance={
            "balance_breakdown": [{"exchange_index": 0, "balance": "n/a"}]}))

    assert '"funded": false' in caplog.text


def test_an_account_with_no_shard_breakdown_says_so(caplog):
    """A pre-sharding balance payload has no per-index breakdown. That must read as
    "none present", not as an empty list of shards — the two point at opposite
    conclusions."""
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(_Client({}, balance={"balance": 4213,
                                                   "balance_dollars": "42.13"}))

    assert "no per-index breakdown" in caplog.text
    assert "4213" not in caplog.text


def test_a_failing_balance_read_does_not_stop_the_probe(caplog):
    """The market-side diff is the more important half; losing the balance read
    must not cost us both."""
    client = _Client({"KXMLBHR": _market()}, balance="boom")
    with caplog.at_level(logging.INFO):
        _probe_exchange_shard(client)

    assert "balance read failed" in caplog.text
    assert "KXMLBHR" in client.series_asked


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
