"""Universe review tiers — the bar between "we trade this" and "we understand this".

WHY THIS EXISTS. Measured 2026-09-05 across the mmsell family: 81 of 400 traded series are in no
taxonomy at all, and 20.2% of the LIVE canary's 30-day trades were in them. 68% of that flow is
the new season arriving (NCAAF, EPL, Serie A, Bundesliga, Ligue 1) faster than anyone classified
it. The live book was selling tails in contracts nobody had reviewed.

What must never break, in the order in which breaking it would matter:

  * **an existing book must be completely unaffected.** Every book that names no `universe` and
    the live bar set to `unclassified` must behave EXACTLY as before. If this regresses, every
    running book's candidate stream moves at once and every number collected before the change
    becomes incomparable with every number after it.
  * **the live bar gates LIVE ONLY.** Paper must keep trading unreviewed series, because paper
    is how a series accumulates the history that graduates it. Barring paper too would make the
    quarantine permanent by construction.
  * **UNCLASSIFIED beats the manifest.** A series with no market-type entry can never read as
    graduated, even if its prefix is in GRADUATED_SERIES by mistake — we would still not know
    how it settles.
  * **the two copies of the tables cannot drift.** The ops script duplicates SERIES_TYPES and
    GRADUATED_SERIES because it must run without this package installed.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

from kalshi_bot import db
from kalshi_bot.mmsell.market_types import SERIES_TYPES
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.mmsell.universe import (
    GRADUATED,
    GRADUATED_SERIES,
    IN_REVIEW,
    UNCLASSIFIED,
    admits,
    tier_of,
)

# A graduated series (MLB totals: classified, 1,881 settled markets of own history) and an
# unclassified one (NCAA football spreads: 204 settled markets, in no taxonomy).
GRAD_SERIES, GRAD_EV = "KXMLBTOTAL", "KXMLBTOTAL-26SEP022138NYYLAA"
UNCL_SERIES, UNCL_EV = "KXNCAAFSPREAD", "KXNCAAFSPREAD-26SEP06BAMAUGA"


def _script():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mmsell_universe_review.py"
    spec = importlib.util.spec_from_file_location("mmsell_universe_review", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ the tiers


def test_a_graduated_series_is_graduated():
    assert tier_of(GRAD_SERIES) == GRADUATED


def test_an_unknown_series_is_unclassified():
    assert tier_of(UNCL_SERIES) == UNCLASSIFIED
    assert tier_of("KXSOMETHINGKALSHILISTEDTODAY") == UNCLASSIFIED


def test_classified_but_not_in_the_manifest_is_in_review():
    """The middle tier has to be reachable, or the design collapses to a two-way switch."""
    classified = {p for p, _, _ in SERIES_TYPES}
    thin = sorted(p for p in classified
                  if not any(p.startswith(g) for g in GRADUATED_SERIES))
    assert thin, "no classified-but-unmanifested series exists; IN_REVIEW is unreachable"
    assert tier_of(thin[0]) == IN_REVIEW


def test_unclassified_beats_the_manifest():
    """Belt and braces: a prefix wrongly added to GRADUATED_SERIES must not promote a series the
    taxonomy cannot classify. Knowing its history is not knowing how it settles."""
    assert tier_of("KXNOTATAXONOMYENTRY") == UNCLASSIFIED


def test_admits_is_ordered_and_fails_open_on_an_unknown_tier():
    assert admits(GRAD_SERIES, GRADUATED) and admits(GRAD_SERIES, IN_REVIEW)
    assert not admits(UNCL_SERIES, GRADUATED)
    assert not admits(UNCL_SERIES, IN_REVIEW)
    assert admits(UNCL_SERIES, UNCLASSIFIED)      # the "off" setting admits everything
    assert admits(UNCL_SERIES, None)
    assert admits(UNCL_SERIES, "typo")            # config validation rejects these separately


# ------------------------------------------------------------------ no drift between copies


def test_the_ops_script_tables_match_the_workers():
    mod = _script()
    assert mod.SERIES_TYPES == SERIES_TYPES
    assert mod.GRADUATED_SERIES == GRADUATED_SERIES


def test_the_ops_script_tiers_identically():
    mod = _script()
    for series in (GRAD_SERIES, UNCL_SERIES, "KXNFLSPREAD", "KXBRANDNEW"):
        assert mod.tier_of(series) == tier_of(series), series


# ------------------------------------------------------------------ through the tracker


def _anchor():
    return (datetime.now(timezone.utc) + timedelta(days=3)).replace(
        hour=12, minute=0, second=0, microsecond=0)


def _mkt(ticker, close_dt, yes_bid_c=6, yes_ask_c=7):
    return {"ticker": ticker, "yes_sub_title": "o8.5", "close_time": close_dt.isoformat(),
            "volume_fp": "500.0", "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
            "yes_ask_dollars": f"{yes_ask_c / 100:.4f}"}


def _ob():
    return {"orderbook_fp": {"yes_dollars": [["0.0600", "300"]],
                             "no_dollars": [["0.9300", "300"]]}}


class FakeClient:
    def __init__(self, events, books):
        self._events, self._books = events, books

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


class RecordingExecutor:
    """Stands in for the live executor and records which tickers reached it.

    Carries `_switches_on`/`_allowed` because the tier bar asks the executor whether this book
    would have placed a real order at all before COUNTING a refusal. A stub without them makes
    the bar's fail-open path fire and the counter tick regardless — the test would then pass
    without exercising the thing it claims to."""

    def __init__(self, allowed=True):
        self.mirrored = []
        self._is_allowed = allowed

    def _switches_on(self):
        return True

    def _allowed(self, strategy):
        return self._is_allowed

    def mirror_mmsell_entry(self, session, *, strategy, event_ticker, ticker, **kw):
        self.mirrored.append(ticker)
        return None


def _both_events(day):
    return ([{"event_ticker": GRAD_EV, "series_ticker": GRAD_SERIES,
              "markets": [_mkt(f"{GRAD_EV}-8", day)]},
             {"event_ticker": UNCL_EV, "series_ticker": UNCL_SERIES,
              "markets": [_mkt(f"{UNCL_EV}-3", day)]}],
            {f"{GRAD_EV}-8": _ob(), f"{UNCL_EV}-3": _ob()})


def _setup(settings, variants, **over):
    settings.bot_mode = "mmsell"
    settings.mmsell_variants = variants
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()


def test_a_book_requiring_graduated_skips_the_unclassified_series(settings):
    _setup(settings, "Ummsell1:lo=5,hi=10,maxyes=7,universe=graduated")
    events, books = _both_events(_anchor())
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings).run_once(session)

    assert summ.per_book.get("Ummsell1") == 1     # the graduated one only
    assert summ.per_book.get("mmsell") == 2       # the untiered control takes both


def test_an_untiered_book_is_completely_unaffected(settings):
    """The entire existing cohort. If this breaks, every running book's universe silently
    narrows and every pre-change number becomes incomparable."""
    _setup(settings, "Tmmsell9:lo=5,hi=10,maxyes=7",
           mmsell_live_min_tier=UNCLASSIFIED)
    events, books = _both_events(_anchor())
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings).run_once(session)

    assert summ.per_book.get("Tmmsell9") == 2
    assert summ.skipped_live_tier == 0


def test_the_live_bar_refuses_live_but_paper_still_trades(settings):
    """The load-bearing asymmetry. Paper MUST keep trading an unreviewed series — that is how it
    accumulates the history that graduates it. Only the live mirror is refused."""
    _setup(settings, "", mmsell_live_min_tier=GRADUATED)
    events, books = _both_events(_anchor())
    ex = RecordingExecutor()
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings,
                             live_executor=ex).run_once(session)

    assert summ.per_book.get("mmsell") == 2                  # paper took BOTH
    assert ex.mirrored == [f"{GRAD_EV}-8"]                   # live took only the graduated one
    assert summ.skipped_live_tier == 1


def test_a_book_that_is_not_live_is_refused_but_NOT_counted(settings):
    """`skipped_live_tier` must read as "real-money entries this bar refused". The tier check
    sits ahead of the executor's own gates, so a book absent from LIVE_STRATEGIES would
    otherwise inflate the counter with refusals of calls that were already no-ops — making the
    bar look far more load-bearing than it is. The entry is still refused; it is just not
    counted as a save."""
    _setup(settings, "", mmsell_live_min_tier=GRADUATED)
    events, books = _both_events(_anchor())
    ex = RecordingExecutor(allowed=False)
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings,
                             live_executor=ex).run_once(session)

    assert summ.per_book.get("mmsell") == 2      # paper unaffected either way
    # The GRADUATED series still reaches the executor, which refuses it by its own allowlist —
    # this bar is a tier gate, not a second allowlist, and must not quietly become one.
    assert ex.mirrored == [f"{GRAD_EV}-8"]
    assert summ.skipped_live_tier == 0           # the unclassified refusal is NOT a live save


def test_the_live_bar_can_be_switched_off(settings):
    """`unclassified` restores the pre-2026-09-05 behaviour exactly."""
    _setup(settings, "", mmsell_live_min_tier=UNCLASSIFIED)
    events, books = _both_events(_anchor())
    ex = RecordingExecutor()
    with db.session_scope() as session:
        summ = MmSellTracker(FakeClient(events, books), settings,
                             live_executor=ex).run_once(session)

    assert len(ex.mirrored) == 2
    assert summ.skipped_live_tier == 0


def test_config_validates_the_tier_name(settings):
    settings.mmsell_variants = ("Ummsell1:lo=5,hi=10,universe=graduated;"
                                "Ummsell2:lo=5,hi=10,universe=nonsense")
    by_tag = {b["tag"]: b for b in settings.mmsell_variant_list}
    assert by_tag["Ummsell1"]["universe"] == GRADUATED
    # An unknown tier admits EVERYTHING, so a book naming one would read as gated and trade
    # ungated. Drop it rather than run it.
    assert "Ummsell2" not in by_tag
