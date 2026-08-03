"""Shared regime/calendar vocabulary for the mmsell seasonal forward-look.

Our whole settled history is ONE regime — ~16 days of summer sports (World Cup props, MLB
totals, tennis, WNBA, BTC daily) plus a few news/econ series. Sept-Nov brings NFL, MLB
playoffs, NBA/NHL openings and the November midterms, and we have zero paper history on any
of them. Two scripts answer "what should we expect" without waiting for the season:

  * scripts/mmsell_supply_forecast.py  — how MANY tradeable markets each regime will supply,
    and when they settle (the concentration risk).
  * scripts/mmsell_regime_backtest.py  — what the maker-sell EDGE was on those regimes in
    Kalshi's own settled history, out-of-sample against our summer books.

Both need the same three things, so they live here: the mmsell entry filter, the mapping from
a series ticker to a trading REGIME, and the seasonal series watchlist. Keeping them in one
module is what makes the two scripts' outputs multiply together honestly — a supply count and
an edge estimate are only combinable if both used the same definition of "tradeable".
"""

from __future__ import annotations

import calendar
import datetime as _dt
import time

# --- the mmsell10 entry filter -------------------------------------------------------------
# mmsell10 is the ONLY config with a positive realizable (fill-model) edge, so it is the book
# the forward-look forecasts. Mirrors kalshi_bot/config.py: base mmsell_* knobs + the
# "mmsell10:lo=5,hi=10,maxyes=7" variant spec. maxyes caps the ACTUAL yes sell price
# (100 - no_bid == yes_ask), which is what the resting maker order really sells at.
BAND_LO_C = 5.0        # yes MIDPOINT floor, cents
BAND_HI_C = 10.0       # yes MIDPOINT ceiling, cents
MAX_YES_C = 7.0        # entry-price ceiling on the yes ask, cents
HTC_MIN_H = 1.0        # hours-to-close floor
HTC_MAX_H = 336.0      # 14 days — bounds how long capital is tied up, and is why a market
#                        that closes in October is NOT tradeable supply until mid-October
MIN_VOLUME = 100.0     # mmsell_min_volume
SKIP_SERIES = ("KXMVE", "KXHIGH", "KXLOW")   # parlays + weather (its own book)


def eligible(mid_c, yes_ask_c, htc_h, volume, *, band_lo=BAND_LO_C, band_hi=BAND_HI_C,
             max_yes=MAX_YES_C, htc_min=HTC_MIN_H, htc_max=HTC_MAX_H,
             min_vol=MIN_VOLUME) -> bool:
    """True when a quote would be an mmsell10 entry candidate right now."""
    if mid_c is None or yes_ask_c is None or htc_h is None:
        return False
    return (band_lo <= mid_c < band_hi and yes_ask_c <= max_yes
            and htc_min <= htc_h <= htc_max and (volume or 0) >= min_vol)


def skip_series(series: str) -> bool:
    s = (series or "").upper()
    return any(s.startswith(p) for p in SKIP_SERIES)


# --- regimes --------------------------------------------------------------------------------
# Coarser than a series, finer than kalshi_flb.category: the point is SEASONALITY, so the
# sports are split by their season rather than lumped into one "Sports" bucket. Order matters —
# first matching prefix wins, so longer/more specific prefixes come first.
REGIMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("NFL",        ("KXNFL", "KXSB", "KXSUPERBOWL", "KXPRO")),
    ("NCAAF",      ("KXNCAAF", "KXCFP", "KXHEISMAN")),
    ("NBA",        ("KXNBA",)),
    ("NHL",        ("KXNHL", "KXSTANLEY")),
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

# Regimes with no history in our paper books at all (docs/MMSELL_SEASONAL_FORECAST.md
# "Finding 1"). Anything here is a pure out-of-sample bet until the season opens.
UNSEEN_REGIMES = ("NFL", "NCAAF", "NBA", "NHL", "NCAAB", "Elections")

# Regimes whose settlements are driven by ONE shared event (a national election day, a single
# game's outcome across many prop ladders). Positions inside one of these are NOT the
# "many small independent positions" mmsell's risk model assumes.
CORRELATED_REGIMES = ("Elections",)


def regime(series: str) -> str:
    s = (series or "").upper()
    for name, prefixes in REGIMES:
        if any(s.startswith(p) for p in prefixes):
            return name
    return "Other"


def series_of(ticker: str, fallback: str = "") -> str:
    """Series ticker for a market. Kalshi market records usually carry series_ticker, but the
    open-markets feed does not always; the prefix before the first '-' is the reliable
    fallback (KXNFLGAME-25SEP07DALPHI-DAL -> KXNFLGAME)."""
    if fallback:
        return fallback.upper()
    return (ticker or "").split("-", 1)[0].upper()


def event_of(ticker: str) -> str:
    """Event a market belongs to: drop the trailing strike/outcome segment. Same convention as
    mmsell_crypto_study.event_key — used to test ladder mutual-exclusivity."""
    return ticker.rsplit("-", 1)[0] if (ticker or "").count("-") >= 2 else ticker


# --- time helpers ---------------------------------------------------------------------------


def close_unix(iso: str) -> int:
    """Kalshi ISO close_time -> unix seconds (0 when unparseable)."""
    try:
        return calendar.timegm(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def week_start(ts: int) -> _dt.date:
    """UTC Monday of the week containing `ts` — the bucket the calendar reports use."""
    d = _dt.datetime.utcfromtimestamp(ts).date()
    return d - _dt.timedelta(days=d.weekday())


def price_c(rec: dict, key: str):
    """Price in CENTS from a Kalshi record, tolerating the cents vs _dollars field pair
    (yes_bid=6 and yes_bid_dollars=0.06 both appear across endpoints/versions)."""
    v = rec.get(f"{key}_dollars")
    if v is not None:
        try:
            return float(v) * 100.0
        except (TypeError, ValueError):
            return None
    v = rec.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
