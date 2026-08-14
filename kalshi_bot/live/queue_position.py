"""Parsing for Kalshi's order QUEUE-POSITION payloads.

WHY THIS IS A SEPARATE MODULE
-----------------------------
Queue position is the measurement that makes maker adverse selection — the ~2¢/contract leak
`docs/MMSELL_FILL_MODEL.md` names as the whole paper→live gap — into something we can observe
directly instead of infer from downstream P&L. `docs/MMSELL_OFFSET_AB.md` is currently trying to
infer it: two live books, one resting at the no-bid and one 1¢ better, compared on realized
¢/contract. That comparison is not merely slow, it is **impractical** — the gate's own arithmetic
says detecting +0.5¢ needs ~47,106 contracts per arm against the ~270 we have.

Queue position answers the same question mechanically and immediately: *did the extra cent move
us up the queue, and past how many contracts?*

The parsing lives here, apart from the client and the executor, because it is the part with a
real chance of being WRONG and it is the part worth unit-testing. Kalshi has already migrated a
payload under us once — the orderbook grew `orderbook_fp` with `yes_dollars`/`no_dollars` STRING
prices and dropped the legacy integer keys, which made a naive `market.get("yes_bid")` silently
`None` (see `scanner.metrics.market_price_cents`). A silent `None` here would be worse than an
error: the sampler would run every cycle, write rows forever, and the column would simply be
empty — and we would not find out until someone tried to analyse it weeks later.

So this module is deliberately shape-tolerant, and — more importantly — it reports the difference
between "the payload said there is no queue position" and "we could not find one", so the caller
can log the second loudly instead of persisting a null.
"""

from __future__ import annotations

from typing import Any

# Keys that have carried a queue figure. Ordered: the first present wins.
#
# `queue_position_fp` is the one production actually sends, confirmed live 2026-08-14:
#
#     {"queue_position_fp": "0.00"}        <- front of the queue
#     {"queue_position_fp": "2028.55"}     <- 2028.55 contracts resting ahead of us
#
# This module's own docstring warned about exactly this — Kalshi's fixed-point migration, the one
# that grew `count_fp` / `position_fp` / `volume_fp` and the `_dollars` string prices — and the
# first version of this tuple still omitted the `_fp` spelling. The design held even though the
# key list did not: the failure was loud, counted, and the raw payload was persisted, so the true
# shape was recovered from the database rather than guessed at.
_POSITION_KEYS = ("queue_position_fp", "queue_position", "queue_pos",
                  "position_in_queue", "queue_rank")

# Contracts resting AHEAD of us at our price. Strictly more useful than the rank itself — rank 3
# behind three 1-lots is a different trade from rank 3 behind three 500-lots — so it is captured
# when offered and left null when not, rather than being derived from the rank.
_AHEAD_KEYS = ("contracts_ahead", "quantity_ahead", "queue_quantity_ahead", "ahead_quantity",
               "size_ahead")

# Envelopes a single-order response has been seen to use, and the plural for the batch endpoint.
_SINGLE_ENVELOPES = ("queue_position", "order", "data", "result")
_LIST_ENVELOPES = ("queue_positions", "orders", "data", "results")

_ORDER_ID_KEYS = ("order_id", "kalshi_order_id", "id")
_TICKER_KEYS = ("market_ticker", "ticker")


def _int_or_none(value: Any) -> int | None:
    """An integer count from an int, a float, or a numeric STRING.

    The string case is not defensive padding: Kalshi's newer fixed-point fields (`count_fp`,
    `position_fp`, the `_dollars` prices) are all delivered as strings, so a numeric field
    arriving as `"3"` is the house style rather than an anomaly."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _is_quantity_like(value: Any) -> bool:
    """True when the raw figure is Kalshi's fixed-point CONTRACT COUNT rather than an ordinal.

    Decided on the delivery format, not on the value: `"0.00"` is as much a quantity as
    `"2028.55"` is, and testing `float(v) != int(v)` would classify the front of the queue — the
    single most important observation there is — as an ordinal purely because it happened to be a
    round number."""
    return isinstance(value, str) and "." in value


def _first_key(payload: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in payload and payload[k] is not None:
            return payload[k]
    return None


def parse_one(payload: Any) -> dict | None:
    """One order's queue sample, or None if this payload carries no recognisable rank.

    Returns `{"queue_position": int, "contracts_ahead": int|None, "order_id": str|None,
    "market_ticker": str|None}`. `None` means *we did not find a rank* — the caller must treat
    that as a parse failure worth reporting, NOT as "this order has no queue position".

    A rank of 0 is meaningful (front of the queue) and must survive, which is why every check
    here is `is None` rather than truthiness."""
    if not isinstance(payload, dict):
        return None

    # Unwrap one envelope layer if the rank is not at the top. Only one layer: deeper nesting has
    # never been observed and blindly recursing would let an unrelated integer field masquerade
    # as a queue rank.
    node = payload
    if _first_key(payload, _POSITION_KEYS) is None:
        for env in _SINGLE_ENVELOPES:
            inner = payload.get(env)
            if isinstance(inner, dict) and _first_key(inner, _POSITION_KEYS) is not None:
                node = inner
                break
            # `{"queue_position": 4}` — the envelope name IS the value.
            if env in _POSITION_KEYS and _int_or_none(inner) is not None:
                return {"queue_position": _int_or_none(inner), "contracts_ahead": None,
                        "order_id": _first_key(payload, _ORDER_ID_KEYS),
                        "market_ticker": _first_key(payload, _TICKER_KEYS)}

    raw_pos = _first_key(node, _POSITION_KEYS)
    pos = _int_or_none(raw_pos)
    if pos is None:
        return None
    # WHAT THE NUMBER IS. Kalshi sends one fixed-point figure, and a value of `2028.55` settles
    # what it means: an ordinal rank cannot be fractional, so this is the QUANTITY of contracts
    # resting ahead of us at our price — consistent with every other `_fp` field being a contract
    # count. That is the more useful of the two measures anyway, and the one the read leads on:
    # rank 3 behind three 1-lots is a completely different queue from rank 3 behind three 500-lots.
    #
    # So it populates `contracts_ahead`. It ALSO populates `queue_position` (rounded) because
    # every downstream coverage check keys off that column, and leaving it null would make a
    # working sampler indistinguishable from a broken one — the precise confusion this module
    # exists to prevent. An explicit `contracts_ahead` field, if Kalshi ever sends one, still wins.
    ahead = _int_or_none(_first_key(node, _AHEAD_KEYS))
    if ahead is None and _is_quantity_like(raw_pos):
        ahead = pos
    return {
        "queue_position": pos,
        "contracts_ahead": ahead,
        # Identity may sit on either the envelope or the inner node, so check both.
        "order_id": _first_key(node, _ORDER_ID_KEYS) or _first_key(payload, _ORDER_ID_KEYS),
        "market_ticker": _first_key(node, _TICKER_KEYS) or _first_key(payload, _TICKER_KEYS),
    }


def parse_batch(payload: Any) -> tuple[dict[str, dict], list]:
    """The batch endpoint's response as `({order_id: sample}, [rows we could not read])`.

    Returning the failed ROWS rather than a count is deliberate. A batch of 40 that yields 0
    samples is indistinguishable from an empty book if you only look at the first value — so the
    caller needs to know a failure happened (the length) *and* be able to persist the offending
    row against the specific order it belonged to (the content). A count alone would leave the
    diagnostic stranded in a log line, which is precisely how a shape change stays invisible: the
    sampler keeps writing rows, every rank is null, and nobody finds out until someone tries to
    analyse it weeks later.

    Entries with no usable `order_id` are failures too: a rank we cannot attach to an order is
    not usable, and dropping it silently would understate the problem."""
    rows: list = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for env in _LIST_ENVELOPES:
            if isinstance(payload.get(env), list):
                rows = payload[env]
                break
        else:
            # A single-order response handed to the batch parser — accept it rather than
            # reporting a spurious failure.
            one = parse_one(payload)
            if one and one.get("order_id"):
                return {str(one["order_id"]): one}, []
            return {}, [payload] if payload else []

    out: dict[str, dict] = {}
    unreadable: list = []
    for row in rows:
        sample = parse_one(row)
        if sample is None or not sample.get("order_id"):
            unreadable.append(row)
            continue
        out[str(sample["order_id"])] = sample
    return out, unreadable


def order_id_of(row: Any) -> str | None:
    """Best-effort order id from an UNPARSED row, so a failed sample can still be filed against
    the order it belonged to. A row that lost its rank field usually still carries its id."""
    if not isinstance(row, dict):
        return None
    for env in (None, *_SINGLE_ENVELOPES):
        node = row if env is None else row.get(env)
        if isinstance(node, dict):
            val = _first_key(node, _ORDER_ID_KEYS)
            if val is not None:
                return str(val)
    return None
