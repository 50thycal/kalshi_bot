"""Real-money exposure pause on a single series — the KXNFLSPREAD bar (XOS-000022).

WHY THIS EXISTS. `KXNFLSPREAD` lost $166.55 across 382 settled markets in three weeks and
nothing stopped it: the series is CLASSIFIED and GRADUATED, so the review-tier bar shipped in
PR #338 passes it through. At the honest independence unit — 44 contests, because one NFL game
carries a nested two-sided spread ladder a blowout resolves against a seller at one instant —
the cell runs -10.0c/trade, bootstrap 95% CI [-18.1, -2.7], and 78% of the loss sits at a tail
price <= 7c, which is the live band. Operator-approved 2026-09-06:
`docs/MMSELL_NFLSPREAD_LOSS_CELL.md`.

What must never break, in the order in which breaking it would matter:

  * **PAPER MUST KEEP TRADING THE PAUSED SERIES.** This is the load-bearing one and it is not a
    nicety: the pause is interim, and paper is what supplies the pre-registered out-of-sample
    evidence that decides whether it is lifted or becomes a real selection rule. Bar paper and
    the exclusion is permanent by construction with nothing left to measure.
  * **it can only ever REFUSE**, never add a live entry, so it moves real-money exposure in the
    safe direction only.
  * **an empty setting is exactly the pre-2026-09-06 behaviour**, so the pause has a clean lift
    path when the gate REFUTES.
  * **it is independent of the tier bar.** A GRADUATED series must still be pausable — that IS
    the KXNFLSPREAD case — and the two counters must not be confused for one another.
  * **the counter means "real-money entries this bar refused"**, so a book that was never live
    must not inflate it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot import db
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.mmsell.universe import GRADUATED, UNCLASSIFIED, exposure_paused, tier_of
from kalshi_bot.twin import harness as twin_codes

# The paused series and a graduated control, both classified and graduated so the TIER bar
# passes both — this bar must be what separates them, not the tier.
PAUSED_SERIES, PAUSED_EV = "KXNFLSPREAD", "KXNFLSPREAD-26SEP07ATLDET"
OK_SERIES, OK_EV = "KXMLBTOTAL", "KXMLBTOTAL-26SEP07NYYLAA"


# ------------------------------------------------------------------ the pure rule


def test_the_paused_series_is_still_fully_graduated():
    """The whole reason this bar had to exist as a second, independent check."""
    assert tier_of(PAUSED_SERIES) == GRADUATED


def test_a_prefix_matches_every_ticker_under_it():
    assert exposure_paused(f"{PAUSED_EV}-DET3", [PAUSED_SERIES])
    assert exposure_paused(PAUSED_SERIES, [PAUSED_SERIES])


def test_matching_is_by_series_prefix_not_substring():
    """`KXNFLSPREAD` must not reach into a neighbouring series that merely contains it."""
    assert not exposure_paused("KXNFLTOTAL-26SEP07ATLDET-40", [PAUSED_SERIES])
    assert not exposure_paused("KXMLBSPREAD-26SEP07NYYLAA-NYY3", [PAUSED_SERIES])


def test_an_empty_list_pauses_nothing():
    assert not exposure_paused(PAUSED_SERIES, [])
    assert not exposure_paused(PAUSED_SERIES, [""])


def test_matching_is_case_insensitive_on_the_ticker():
    assert exposure_paused("kxnflspread-26sep07atldet-det3", [PAUSED_SERIES])


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
    """Records which tickers reached the live path.

    Carries `_switches_on`/`_allowed` because the bar asks the executor whether this book would
    have placed a real order at all before COUNTING a refusal. A stub without them makes the
    fail-open path fire and the counter tick regardless, so the test would pass without
    exercising what it claims to."""

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
    return ([{"event_ticker": PAUSED_EV, "series_ticker": PAUSED_SERIES,
              "markets": [_mkt(f"{PAUSED_EV}-DET3", day)]},
             {"event_ticker": OK_EV, "series_ticker": OK_SERIES,
              "markets": [_mkt(f"{OK_EV}-8", day)]}],
            {f"{PAUSED_EV}-DET3": _ob(), f"{OK_EV}-8": _ob()})


def _setup(settings, **over):
    settings.bot_mode = "mmsell"
    settings.mmsell_variants = ""
    settings.mmsell_live_min_tier = GRADUATED
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()


def _run(settings, executor=None):
    events, books = _both_events(_anchor())
    with db.session_scope() as session:
        return MmSellTracker(FakeClient(events, books), settings,
                             live_executor=executor).run_once(session)


def test_live_is_refused_but_paper_still_trades_the_paused_series(settings):
    """The load-bearing asymmetry. Paper is what the pre-registered test runs on."""
    _setup(settings, mmsell_live_skip_series=PAUSED_SERIES)
    ex = RecordingExecutor()
    summ = _run(settings, ex)

    assert summ.per_book.get("mmsell") == 2          # paper took BOTH
    assert ex.mirrored == [f"{OK_EV}-8"]             # real money took only the un-paused one
    assert summ.skipped_live_paused == 1


def test_the_pause_is_independent_of_the_tier_bar(settings):
    """A GRADUATED series is paused here and does NOT tick the tier counter. Confusing the two
    would make the report claim the review tier caught something it structurally cannot."""
    _setup(settings, mmsell_live_skip_series=PAUSED_SERIES)
    summ = _run(settings, RecordingExecutor())

    assert summ.skipped_live_paused == 1
    assert summ.skipped_live_tier == 0


def test_an_empty_setting_restores_the_previous_behaviour_exactly(settings):
    """The lift path if the pre-registered gate REFUTES."""
    _setup(settings, mmsell_live_skip_series="")
    ex = RecordingExecutor()
    summ = _run(settings, ex)

    assert len(ex.mirrored) == 2
    assert summ.skipped_live_paused == 0


def test_a_book_that_is_not_live_is_refused_but_NOT_counted(settings):
    """`skipped_live_paused` must read as "real-money entries this bar refused". The check sits
    ahead of the executor's own gates, so a book absent from LIVE_STRATEGIES would otherwise
    inflate the counter with refusals of calls that were already no-ops."""
    _setup(settings, mmsell_live_skip_series=PAUSED_SERIES)
    ex = RecordingExecutor(allowed=False)
    summ = _run(settings, ex)

    assert summ.per_book.get("mmsell") == 2          # paper unaffected either way
    assert summ.skipped_live_paused == 0             # refused, but not claimed as a live save


def test_paper_is_untouched_even_with_no_live_executor_at_all(settings):
    """Paper-only deployments must not notice this bar exists."""
    _setup(settings, mmsell_live_skip_series=PAUSED_SERIES)
    summ = _run(settings, None)

    assert summ.per_book.get("mmsell") == 2
    assert summ.skipped_live_paused == 0


def test_the_refusal_is_reported_under_its_own_twin_code(settings):
    """A distinct code, so the parity tape can tell this refusal apart from a tier refusal."""
    assert twin_codes.SKIP_LIVE_PAUSED != twin_codes.SKIP_LIVE_TIER


def test_the_shipped_default_pauses_kxnflspread(settings):
    """The operator-approved decision itself. If this flips to empty by accident, real money
    silently resumes on the cell — the exact failure this shipped to prevent."""
    assert settings.mmsell_live_skip_series_list == [PAUSED_SERIES]


def test_the_setting_parses_a_list(settings):
    settings.mmsell_live_skip_series = " kxnflspread , KXFOO ,, "
    assert settings.mmsell_live_skip_series_list == ["KXNFLSPREAD", "KXFOO"]


def test_the_tier_bar_still_works_beside_it(settings):
    """The two bars are ORed at one call site; adding one must not disable the other."""
    _setup(settings, mmsell_live_skip_series="", mmsell_live_min_tier=UNCLASSIFIED)
    ex = RecordingExecutor()
    summ = _run(settings, ex)
    assert len(ex.mirrored) == 2 and summ.skipped_live_tier == 0
