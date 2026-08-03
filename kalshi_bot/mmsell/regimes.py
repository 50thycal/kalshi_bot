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
