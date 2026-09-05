"""Regime vocabulary for the settled-history capture (worker side).

DELIBERATE DUPLICATE of `scripts/mmsell_seasonal.py`'s REGIMES map. The ops-channel analysis
scripts must stay self-contained (stdlib + psycopg only — they run on a GitHub Actions runner
that never installs this package), so the worker cannot import them and they cannot import the
worker. `tests/test_regime_history.py` asserts the two maps are identical, so the copy cannot
drift silently; change one and the test tells you to change the other.

Why the worker needs a regime at all: the capture stamps each settled market with the regime it
was classified into AT CAPTURE TIME, so a later change to the map cannot silently rewrite the
history a backtest reads.
"""

from __future__ import annotations

# Order matters — first matching prefix wins, so longer/more specific prefixes come first.
REGIMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("NFL",        ("KXNFL", "KXSB", "KXSUPERBOWL", "KXPRO")),
    ("NCAAF",      ("KXNCAAF", "KXCFP", "KXHEISMAN")),
    ("NBA",        ("KXNBA",)),
    ("NHL",        ("KXNHL", "KXSTANLEY")),
    # NCAA BASEBALL must precede NCAAB: KXNCAABB*/KXNCAABASEBALL are baseball series that the
    # basketball prefix would otherwise swallow, putting a spring sport in a winter regime.
    ("NCAABase",   ("KXNCAABB", "KXNCAABASEBALL")),
    ("NCAAB",      ("KXNCAAB", "KXMARCH")),
    ("MLB",        ("KXMLB", "KXWORLDSERIES", "KXHRDERBY", "KXASG")),
    ("Soccer",     ("KXWC", "KXEPL", "KXUCL", "KXLALIGA", "KXSERIEA", "KXBUNDES", "KXLIGUE",
                    "KXMLS", "KXLIGAMX", "KXCLUBF", "KXBRASILEIRO", "KXARGPREM", "KXECULP",
                    "KXALLSVENSKAN", "KXCANPL", "KXSOCCER")),
    ("Tennis",     ("KXATP", "KXWTA", "KXITF", "KXTENNIS", "KXUSOPEN")),
    ("Cricket",    ("KXT20", "KXODI", "KXTEST", "KXCRICKET", "KXIPL")),
    ("Golf",       ("KXPGA", "KXLPGA", "KXLIV", "KXRYDER", "KXGOLF")),
    ("OtherSport", ("KXWNBA", "KXUFC", "KXBOX", "KXF1", "KXNASCAR", "KXNPB", "KXKBO",
                    "KXESPORT", "KXLOL", "KXCS", "KXDOTA", "KXCHESS", "KXOLY")),
    ("Elections",  ("KXPRES", "KXSENATE", "KXHOUSE", "KXGOV", "KXPOTUS", "KXELECTION",
                    "KXMIDTERM", "KXBALLOT", "KXPRIMARY", "KXNOMINEE", "KXMAYOR",
                    "KXCONTROL", "KXSEAT")),
    ("Politics",   ("KXTRUMP", "KXPARDON", "KXAPPROVAL", "KXPOLL", "KXCABINET", "KXIMPEACH",
                    "KXSHUTDOWN", "KXTARIFF", "KXNOBEL")),
    ("Crypto",     ("KXBTC", "KXETH", "KXSOL", "KXDOGE", "KXXRP", "KXCRYPTO")),
    ("Econ",       ("KXCPI", "KXPAYROLL", "KXGDP", "KXFED", "KXRETAIL", "KXPCE", "KXUNRATE",
                    "KXJOBLESS", "KXPPI", "KXRATE", "KXISM", "KXTRADEBAL", "KXWTI", "KXBRENT",
                    "KXGAS", "KXAAAGAS", "KXNATGAS")),
    ("Culture",    ("KXNETFLIX", "KXROTTEN", "KXRT", "KXOSCAR", "KXEMMY", "KXGRAMMY",
                    "KXALBUM", "KXMOVIE", "KXSPOTIFY", "KXBILLBOARD", "KXRANK", "KXART",
                    "KXTOPMODEL", "KXBOXOFFICE")),
    ("Space",      ("KXSPACEX", "KXNASA", "KXLAUNCH", "KXSTARSHIP")),
)


def regime_of(series: str) -> str:
    s = (series or "").upper()
    for name, prefixes in REGIMES:
        if any(s.startswith(p) for p in prefixes):
            return name
    return "Other"


def event_ticker_of(market_ticker: str | None) -> str | None:
    """KXNFLGAME-25SEP07DALPHI-DAL -> KXNFLGAME-25SEP07DALPHI (strip the outcome token)."""
    if not market_ticker or market_ticker.count("-") < 2:
        return market_ticker
    return market_ticker.rsplit("-", 1)[0]


#: Regimes whose tickers encode ONE contest that several series price separately —
#: `SERIES-<date><time><matchup>-<outcome>`, so KXMLBTOTAL / KXMLBSPREAD / KXMLBHR
#: on the same game share a middle token. ONLY these group across series. Outside
#: them the contest key stays the event ticker, i.e. exactly today's behaviour,
#: because a short middle token elsewhere is a DATE rather than a contest:
#: `KXPAYROLLS-26SEP` and `KXCPI-26SEP` share "26SEP" and share no result at all.
#: Grouping those would refuse genuinely independent entries, and a cap that
#: refuses good trades invisibly is worse than the gap it closes.
CONTEST_GROUPED_REGIMES: frozenset[str] = frozenset({
    "NFL", "NCAAF", "NBA", "NHL", "NCAABase", "NCAAB", "MLB",
    "Soccer", "Tennis", "Cricket", "Golf", "OtherSport",
})


def contest_key_of(market_ticker: str | None) -> str | None:
    """The underlying CONTEST a market resolves on, shared across series.

        KXMLBTOTAL-26SEP022138NYYLAA-8        -> MLB:26SEP022138NYYLAA
        KXMLBTEAMTOTAL-26SEP022138NYYLAA-NYY6 -> MLB:26SEP022138NYYLAA
        KXMLBHR-26SEP022138NYYLAA-AARONJUDGE1 -> MLB:26SEP022138NYYLAA
        KXPAYROLLS-26SEP                      -> KXPAYROLLS-26SEP  (ungrouped)

    WHY THIS EXISTS (XOS-000020). `event_ticker_of` returns SERIES x contest, so
    one baseball game appears as up to five distinct "events" -- one under
    KXMLBTOTAL, one under KXMLBTEAMTOTAL, one under KXMLBSPREAD, and so on. Every
    concentration cap keys on that event ticker, so a book can hold three rungs
    under each of five series on ONE game and no cap notices fifteen positions
    riding a single result. Measured on Dmmsell10's live canary: the 2026-09-02
    NYYLAA game carried 6 markets across 5 series, STLLAD 5 across 3, NYMTB 5
    across 2 -- those three contests alone held -5.66 USD of a -6.78 USD drawdown.
    A high-scoring game resolves TOTAL, TEAMTOTAL, SPREAD and HR against a seller
    at the same instant; they are one bet wearing several tickers.

    The key is REGIME-NAMESPACED so two sports that happen to share a token cannot
    collide, and grouping is restricted to `CONTEST_GROUPED_REGIMES`. Everywhere
    else this returns the event ticker unchanged, which is what every existing cap
    already keys on -- so switching the cap on cannot change behaviour outside the
    regimes whose convention was actually verified.
    """
    event = event_ticker_of(market_ticker)
    if not event:
        return None
    series, sep, contest = event.partition("-")
    if not (sep and contest):
        return event
    regime = regime_of(series)
    if regime not in CONTEST_GROUPED_REGIMES:
        return event
    return f"{regime}:{contest}"
