"""The unit of CORRELATION for an mmsell candidate — which open positions are really one bet.

Why this exists (XOS-000020, 2026-09-05). Every concentration cap mmsell has ever run keys on
`event_ticker`, and `event_ticker` is *series x occasion*, not occasion. On 26SEP02 the live
book `Dmmsell10` held 31 markets across 23 distinct event_tickers spanning only ELEVEN real
games: NYYLAA alone carried positions under KXMLBF5TOTAL, KXMLBHR, KXMLBSPREAD, KXMLBTEAMTOTAL
and KXMLBTOTAL, i.e. five "events" that one high-scoring night resolves against the book
together. The rung cap permits 3 per event_ticker, so a single game can legally hold ~15
correlated positions and no cap notices. The book believed it held N independent 7c lottery
tickets; it held one bet on "is this a high-scoring night". That slate was the ENTIRE live
drawdown (-$6.44 gross of -$4.46 net): 10 of 10 losing markets were MLB, 9 on that one slate,
while 33 settled non-MLB markets lost nothing.

The naive fix — "strip the series prefix off event_ticker and group on the rest" — is what the
first counterfactual measured, and it is wrong in a way that flatters it. Measured on the paper
`mmsell10` tape, that key groups `26SEP022210STLLAD` (12 trades across 6 series: a real game)
but ALSO `26AUG1717` (27 trades across 8 unrelated crypto/econ series: merely the same hour) and
`26AUG` (13 trades across 7 series: merely the same MONTH). So its headline gain is a mixture of
a game cap and an accidental DAY cap on every date-suffixed series, and the two cannot be told
apart inside one number.

The correct key is not one string transform, because the unit of correlation is a property of
how the contract SETTLES — which is exactly what `market_types.classify` already answers:

  in_play    the occasion is a CONTEST, and it is shared across series. One game resolves
             TOTAL, TEAMTOTAL, SPREAD and HR simultaneously, so the key must span series:
             the contest token off the event ticker.
  scheduled  the occasion is one underlying read at one instant. `KXBTCD` and `KXETHD` at the
             same hour are DIFFERENT bets (different underlyings) and must NOT merge, so the
             key stays the event ticker — which for a strike ladder is one bet on that
             underlying's path.
  discrete   the occasion is the window itself; the event ticker already names it.

So for `scheduled` and `discrete` the key is the event ticker and this module changes nothing
about WHAT groups together — only the cap applied to it. Only `in_play` gets a genuinely coarser
key than mmsell has ever used. That asymmetry is the point, and it is why the two arms in
`docs/MMSELL_CORRELATION_CAP.md` separate the game axis from the cap-tightening axis instead of
shipping both as one treatment.

An UNCLASSIFIED series falls back to its event ticker and is labelled as such. It is never
merged with anything: a contract nobody has classified must not be silently declared correlated
with a contract that has been.
"""

from __future__ import annotations

from .market_types import IN_PLAY, UNCLASSIFIED, classify

# `kind` values. They are not decoration: a book caps by kind (see Settings.mmsell_variants
# `corrscope=`), so the game axis can be tested WITHOUT also tightening every ladder cap.
GAME = "game"          # in_play — one contest, shared across series
EVENT = "event"        # scheduled / discrete — one occasion, within a series
UNKNOWN = "unknown"    # the series is not in the taxonomy; keyed as its own event, never merged


def event_suffix(event_ticker: str) -> str:
    """The part of an event ticker after its series prefix, or "" when there is no prefix.

    Kalshi writes an event ticker as `<SERIES>-<OCCASION>`; the occasion is what identifies the
    contest/instant/window. Split on the FIRST hyphen only — a series never contains one, while
    an occasion sometimes does (see contest_token)."""
    _, sep, rest = (event_ticker or "").partition("-")
    return rest if sep else ""


def contest_token(event_ticker: str) -> str:
    """The contest identifier for an in-play event ticker: `KXMLBTOTAL-26SEP022210STLLAD` ->
    `26SEP022210STLLAD`, which is date + start time + the two team codes.

    Takes the FIRST segment of the suffix rather than the whole of it, so a series that appends
    a qualifier (a side, a leg, a period) still keys to the game it belongs to. Under-merging is
    the failure mode that matters here: a qualifier left in the key silently re-creates the
    per-series split this module exists to remove, and does it invisibly — the cap would simply
    never fire."""
    return event_suffix(event_ticker).partition("-")[0]


def correlation_key(series: str, event_ticker: str) -> tuple[str, str]:
    """`(kind, key)` — the unit of correlation this candidate belongs to.

    Two candidates share a bet exactly when they return equal tuples. The kind is part of the
    key, so an in-play contest token can never collide with an identically-spelled event ticker
    from another mode.

    Falls back to the event ticker whenever the contest token would be empty (an in-play series
    whose ticker does not carry one). That is the conservative direction: it groups exactly as
    today's caps do rather than keying every such market to the same empty string, which would
    declare unrelated contests correlated and block entries for a reason nobody could read."""
    et = (event_ticker or "").strip()
    if not et:
        return (UNKNOWN, "")
    mtype, mode = classify(series)
    if (mtype, mode) == UNCLASSIFIED:
        return (UNKNOWN, et)
    if mode == IN_PLAY:
        token = contest_token(et)
        return (GAME, token) if token else (EVENT, et)
    return (EVENT, et)


def in_scope(kind: str, scope: str) -> bool:
    """Whether a book whose `corrscope` is `scope` caps this kind of key.

    `game` caps ONLY contests — the single new mechanic, leaving every existing ladder cap
    untouched so the arm differs from its control by one thing. `all` caps every key, which
    additionally tightens scheduled/discrete ladders from the rung cap's 3 to the book's own
    `corrcap`. Running both is how we learn which half of the effect is which."""
    if scope == "all":
        return kind in (GAME, EVENT, UNKNOWN)
    if scope == GAME:
        return kind == GAME
    return False


# Every `corrscope` a book may legally declare. A spec naming anything else is rejected by
# config validation rather than run: an unknown scope caps nothing, so the book would read as
# capped, trade uncapped, and look like a null result for the cap rather than a typo.
KNOWN_CORR_SCOPES = frozenset({GAME, "all"})
