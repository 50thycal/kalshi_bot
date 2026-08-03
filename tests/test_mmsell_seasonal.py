"""The seasonal forward-look's shared vocabulary (scripts/mmsell_seasonal) plus the two pure
decision functions the forecast and the regime backtest are built on.

What is worth pinning here is the stuff that would silently produce a CONFIDENT WRONG forecast
rather than an obvious crash:
  * the entry filter must be mmsell10's, exactly. If `eligible` drifts from the config's
    lo=5,hi=10,maxyes=7 the supply forecast counts markets the book would never enter, and the
    regime backtest measures a trade we do not make.
  * a market's entry price is the yes ASK (mmsell rests a BUY-NO at the no-bid == 100-yes_ask),
    not the mid. Using the mid understates every entry by half a spread, which at 5-7c prices is
    a large fraction of the whole edge.
  * regime classification decides which bucket a market's supply and P&L land in. A series
    silently falling through to "Other" is how a whole season goes missing from a forecast.
  * series selection must stay BOUNDED and deterministic — Kalshi lists 3,000+ sports series.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("mmsell_seasonal", _SCRIPTS / "mmsell_seasonal.py")
ms = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ms)  # type: ignore[union-attr]

_RB_SPEC = importlib.util.spec_from_file_location(
    "mmsell_regime_backtest", _SCRIPTS / "mmsell_regime_backtest.py")


# ------------------------------------------------------------------ the entry filter


def test_eligible_matches_the_mmsell10_config():
    """lo=5, hi=10 on the MIDPOINT; maxyes=7 on the ask; htc 1-336h; volume >= 100."""
    assert ms.eligible(mid_c=6.0, yes_ask_c=7.0, htc_h=50.0, volume=500)
    assert not ms.eligible(mid_c=4.9, yes_ask_c=6.0, htc_h=50.0, volume=500)   # below the band
    assert not ms.eligible(mid_c=10.0, yes_ask_c=7.0, htc_h=50.0, volume=500)  # hi is exclusive
    assert not ms.eligible(mid_c=6.0, yes_ask_c=8.0, htc_h=50.0, volume=500)   # ask over maxyes
    assert not ms.eligible(mid_c=6.0, yes_ask_c=7.0, htc_h=0.5, volume=500)    # too late
    assert not ms.eligible(mid_c=6.0, yes_ask_c=7.0, htc_h=400.0, volume=500)  # past 14 days
    assert not ms.eligible(mid_c=6.0, yes_ask_c=7.0, htc_h=50.0, volume=99)    # illiquid


def test_eligible_is_none_safe():
    """A market with no quote must be skipped, never treated as cheap."""
    assert not ms.eligible(None, 7.0, 50.0, 500)
    assert not ms.eligible(6.0, None, 50.0, 500)
    assert not ms.eligible(6.0, 7.0, None, 500)


def test_skip_series_covers_parlays_and_weather():
    assert ms.skip_series("KXMVEPARLAY")
    assert ms.skip_series("KXHIGHNY")
    assert not ms.skip_series("KXNFLGAME")


# ------------------------------------------------------------------ entry price convention


def test_backtest_enters_at_the_yes_ask_not_the_mid():
    """mmsell rests a BUY-NO at the no-bid, which SELLS yes at 100-no_bid == the yes ask. The
    ask is therefore the entry price; pricing the mid would understate every entry by half a
    spread, which at these prices is a big share of the whole edge."""
    rb = importlib.util.module_from_spec(_RB_SPEC)
    _RB_SPEC.loader.exec_module(rb)  # type: ignore[union-attr]
    close_min = 1_000_000
    # one hourly point 50h before close: bid 5c / ask 7c -> mid 6c, in band, ask at the cap
    seq = [(close_min - 50 * 60, 5.0, 7.0)]
    entry, saw = rb.build_entry(
        {"ticker": "KXNFLGAME-25SEP07DALPHI-DAL", "series": "KXNFLGAME",
         "close": close_min * 60, "result": "no"}, seq, close_min)
    assert saw and entry is not None
    assert entry["entry_yes"] == 7.0          # the ASK, not the 6.0 mid
    assert entry["settle_yes"] == 0.0
    assert entry["regime"] == "NFL"
    assert abs(entry["htc_at_entry"] - 50.0) < 1e-9


def test_backtest_separates_never_cheap_from_cheap_but_outside_the_window():
    """A regime that is cheap only in its final minutes is real supply we cannot trade — it must
    be distinguishable from a regime that is never cheap at all, or the forecast reads a
    configuration limit as an absence of markets."""
    rb = importlib.util.module_from_spec(_RB_SPEC)
    _RB_SPEC.loader.exec_module(rb)  # type: ignore[union-attr]
    close_min = 1_000_000
    mkt = {"ticker": "KXNBAGAME-A-B", "series": "KXNBAGAME",
           "close": close_min * 60, "result": "no"}

    # cheap, but only 30 minutes before close -> inside the band, outside htc>=1h
    entry, saw = rb.build_entry(mkt, [(close_min - 30, 5.0, 7.0)], close_min)
    assert entry is None and saw is True

    # never cheap at all
    entry, saw = rb.build_entry(mkt, [(close_min - 50 * 60, 40.0, 42.0)], close_min)
    assert entry is None and saw is False


# ------------------------------------------------------------------ regimes


def test_regimes_split_sports_by_season():
    """kalshi_flb.category lumps every sport into "Sports"; seasonality is the whole point here,
    so NFL/NBA/NHL/MLB must land in separate buckets."""
    assert ms.regime("KXNFLGAME") == "NFL"
    assert ms.regime("KXNBATOTAL") == "NBA"
    assert ms.regime("KXNHLGAME") == "NHL"
    assert ms.regime("KXMLBTOTAL") == "MLB"
    assert ms.regime("KXNCAAFGAME") == "NCAAF"


def test_election_series_are_their_own_correlated_regime():
    """The November concentration risk only gets flagged if these classify as Elections AND
    Elections is declared correlated."""
    for s in ("KXSENATECONTROL", "KXHOUSE", "KXGOVNY", "KXPRESPARTY"):
        assert ms.regime(s) == "Elections", s
    assert "Elections" in ms.CORRELATED_REGIMES
    assert "Elections" in ms.UNSEEN_REGIMES


def test_series_we_actually_traded_this_summer_all_classify():
    """Every series with settled mmsell10 trades must land somewhere real: an unclassified
    series silently becomes "Other" and drops out of every per-regime table."""
    traded = ("KXBTCD", "KXMLBTOTAL", "KXTRUMPSAY", "KXFEDMENTION", "KXWCGOAL", "KXMLBSPREAD",
              "KXWCMENTION", "KXMLBHR", "KXPGATOUR", "KXATPMATCH", "KXWNBATOTAL", "KXWTAMATCH",
              "KXITFMATCH", "KXMLSGAME", "KXLIVTOUR", "KXNBASUMMERGAME", "KXUFCMOV")
    unclassified = [s for s in traded if ms.regime(s) == "Other"]
    assert not unclassified, f"unclassified series: {unclassified}"


def test_series_of_prefers_the_declared_series_ticker():
    """Kalshi series tickers can themselves contain a dash (KXMLBWINS-TEX), so the prefix split
    is only a fallback for feeds that omit series_ticker."""
    assert ms.series_of("KXMLBWINS-TEX-2026", "KXMLBWINS-TEX") == "KXMLBWINS-TEX"
    assert ms.series_of("KXNFLGAME-25SEP07DALPHI-DAL") == "KXNFLGAME"


def test_event_of_groups_ladder_rungs():
    """Ladder rungs of one event share an event key — that is what makes the mutual-exclusivity
    hedge measurable."""
    assert (ms.event_of("KXSENATECONTROL-26NOV03-R")
            == ms.event_of("KXSENATECONTROL-26NOV03-D")
            == "KXSENATECONTROL-26NOV03")


# ------------------------------------------------------------------ series selection


def test_select_series_is_bounded_and_prefers_seeds():
    """Kalshi lists 3,000+ sports series, most per-team spin-offs of one driver, so selection
    must cap per regime. SEEDS outrank even series trading today: the NFL regime has 4,400+ open
    markets across dozens of side-series, and ranking "open" first filled the entire NFL budget
    with short-tickered futures while leaving KXNFLGAME — the series carrying the season's actual
    supply — unpulled. That read as "NFL has no history" in a real forecast run."""
    known = {
        "KXNFLGAME": "seed",
        "KXNFLTOTAL": "open",
        "KXNFLSPREAD": "seed",
        "KXNFLXX": "open",            # a short-tickered side series: must NOT crowd out a seed
        "KXMLBGAME": "open",
        "KXHIGHNY": "open",           # skip-listed: weather has its own book
        "KXNETFLIXRANKSHOW": "open",  # a regime we did not ask for
    }
    # Seeds are exempt from the cap, so KXNFLGAME/KXNFLSPREAD survive alongside 2 open series.
    assert ms.select_series(known, {"NFL", "MLB"}, 2) == [
        "KXMLBGAME", "KXNFLGAME", "KXNFLSPREAD", "KXNFLXX", "KXNFLTOTAL"]
    # With the cap at zero, ONLY the seeds remain — the property that broke before.
    assert ms.select_series(known, {"NFL"}, 0) == ["KXNFLGAME", "KXNFLSPREAD"]


def test_select_series_is_deterministic():
    known = {f"KXNFL{i:03d}": "catalogue:Sports" for i in range(50)}
    assert ms.select_series(known, {"NFL"}, 5) == ms.select_series(known, {"NFL"}, 5)


# ------------------------------------------------------------------ calendar helpers


def test_week_start_is_the_utc_monday():
    # 2026-11-03 is a Tuesday (US election day); its week bucket is Monday the 2nd.
    ts = int(_dt.datetime(2026, 11, 3, 23, 0, tzinfo=_dt.timezone.utc).timestamp())
    assert ms.week_start(ts) == _dt.date(2026, 11, 2)


def test_price_c_handles_both_field_conventions():
    """Kalshi returns yes_bid_dollars ('0.0600') on some feeds and yes_bid (6) on others."""
    assert ms.price_c({"yes_bid_dollars": "0.0600"}, "yes_bid") == 6.0
    assert ms.price_c({"yes_bid": 6}, "yes_bid") == 6.0
    assert ms.price_c({}, "yes_bid") is None


def test_close_unix_round_trips_and_survives_garbage():
    assert ms.close_unix("2026-11-03T23:00:00Z") == int(
        _dt.datetime(2026, 11, 3, 23, 0, tzinfo=_dt.timezone.utc).timestamp())
    assert ms.close_unix("") == 0
    assert ms.close_unix(None) == 0


def test_ncaa_baseball_does_not_land_in_the_basketball_regime():
    """KXNCAABB* / KXNCAABASEBALL are baseball. The basketball prefix KXNCAAB would swallow them
    and put a spring sport in a winter regime, corrupting exactly the seasonality this measures."""
    assert ms.regime("KXNCAABBGAME") == "NCAABase"
    assert ms.regime("KXNCAABASEBALL") == "NCAABase"
    assert ms.regime("KXNCAABGAME") == "NCAAB"
