"""The twin must share live's UNIVERSE, not just its pricing.

WHY THIS EXISTS. `docs/LIVE_PAPER_TWIN.md` states the twin's invariant as "the ONLY remaining
difference is that the twin assumes its resting order fills." The 2026-09-05 review-tier bar and
the 2026-09-06 series pause gate the LIVE MIRROR ONLY, so from that date the sentence was false:
the twin kept trading series live may not touch, and `live_paper_parity` therefore reported a
UNIVERSE difference as an EXECUTION GAP. Measured on `Fmmsell10`/`Fmmsell10_pt4`: 177 of the 194
candidates live "never attempted" were refused at the tier bar and 15 at the series pause, while
`gate:open_cap` refused none (`docs/OPS_FMMSELL10_PARITY_DIAGNOSIS.md`).

What must never break, in the order in which breaking it would matter:

  * **default OFF is byte-identical to today.** Turning this on changes what a RUNNING twin
    trades, and retuning a live comparison mid-epoch voids it. Merging this must change nothing.
  * **the INCUMBENT paper book stays unbarred.** Paper is how a series accumulates the history
    that graduates it; the twin is not paper, it is live's mirror. Only the twin moves.
  * **the live mirror is untouched.** This changes the measuring instrument, never the book
    that risks money.
  * **a twin refusal is counted separately** (`twin_skipped_live_*`), so it can never be read
    as a real-money entry the bar saved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot import db
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.twin import TwinHarness

# A graduated series and an unclassified one, same pair the universe-review tests use.
GRAD_SERIES, GRAD_EV = "KXMLBTOTAL", "KXMLBTOTAL-26SEP022138NYYLAA"
UNCL_SERIES, UNCL_EV = "KXNCAAFSPREAD", "KXNCAAFSPREAD-26SEP06BAMAUGA"


def _anchor():
    return (datetime.now(timezone.utc) + timedelta(days=3)).replace(
        hour=12, minute=0, second=0, microsecond=0)


def _mkt(ticker, close_dt, yes_bid_c=5, yes_ask_c=7):
    return {"ticker": ticker, "yes_sub_title": "o8.5", "close_time": close_dt.isoformat(),
            "volume_fp": "500.0", "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
            "yes_ask_dollars": f"{yes_ask_c / 100:.4f}"}


def _ob():
    return {"orderbook_fp": {"yes_dollars": [["0.0500", "300"]],
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

    def get_balance(self):
        return {"balance": 100_000}


class RecordingExecutor:
    """Records which tickers reached the live mirror. Carries the two probes the live-only
    bars ask before COUNTING a refusal — a stub without them makes the fail-open path fire."""

    def __init__(self):
        self.mirrored = []

    def _switches_on(self):
        return True

    def _allowed(self, strategy):
        # Exactly what LIVE_STRATEGIES=mmsell10 means: the variant book is the only one that
        # may risk money. The base `mmsell` control and the twin are both refused here, which
        # is what makes `skipped_live_*` readable as "real-money entries this bar refused"
        # rather than a count that also includes already-no-op mirror calls.
        return strategy == "mmsell10"

    def mirror_mmsell_entry(self, session, *, strategy, event_ticker, ticker, **kw):
        # (strategy, ticker): the bars do not stop the mirror CALL for a book that is not
        # allowlisted — the executor refuses that internally — so the tag has to be recorded
        # or a per-book assertion cannot be made at all.
        self.mirrored.append((strategy, ticker))
        return "placed"

    def live_tickers(self):
        return [t for s, t in self.mirrored if s == "mmsell10"]


def _both_events(day):
    return ([{"event_ticker": GRAD_EV, "series_ticker": GRAD_SERIES,
              "markets": [_mkt(f"{GRAD_EV}-8", day)]},
             {"event_ticker": UNCL_EV, "series_ticker": UNCL_SERIES,
              "markets": [_mkt(f"{UNCL_EV}-3", day)]}],
            {f"{GRAD_EV}-8": _ob(), f"{UNCL_EV}-3": _ob()})


def _armed(settings, **over):
    """mmsell10 live with its twin mmsell10_pt auto-derived, tier bar at `graduated`."""
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = "mmsell10"
    settings.live_paper_twin_enabled = True
    settings.live_paper_twin_suffix = "_pt"
    settings.live_max_order_dollars = 1.0
    settings.max_order_size = 100
    settings.max_market_exposure = 25.0
    settings.mmsell_live_max_open_positions = 60
    settings.mmsell_live_price_offset_cents = 0
    settings.mmsell_live_max_spread_cents = 40
    settings.mmsell_live_min_tier = "graduated"
    settings.mmsell_live_skip_series = ""
    settings.mmsell_variants = "mmsell10:lo=5,hi=10,maxyes=7"
    settings.mmsell_capture_candidates = False
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()
    return settings


def _run(settings):
    events, books = _both_events(_anchor())
    client = FakeClient(events, books)
    ex = RecordingExecutor()
    tr = MmSellTracker(client, settings, live_executor=ex,
                       twin_harness=TwinHarness(settings))
    tr._account_state = {"cash_balance": 150.0}
    with db.session_scope() as session:
        summ = tr.run_once(session)
    return summ, ex


# --- the regression guard: default OFF must change nothing --------------------

def test_default_off_leaves_the_twin_trading_both(settings):
    """The whole cohort. If this breaks, every running twin's universe silently narrows
    mid-epoch and every parity number collected before the change becomes incomparable."""
    _armed(settings)
    assert settings.mmsell_twin_applies_live_bars is False
    summ, _ = _run(settings)

    assert summ.per_book.get("mmsell10_pt") == 2     # twin took BOTH, as it does today
    assert summ.twin_skipped_live_tier == 0
    assert summ.twin_skipped_live_paused == 0


# --- switched on --------------------------------------------------------------

def test_on_the_twin_shares_lives_universe(settings):
    """The fix. The twin stops trading the series live is forbidden to touch."""
    _armed(settings, mmsell_twin_applies_live_bars=True)
    summ, ex = _run(settings)

    assert summ.per_book.get("mmsell10_pt") == 1     # the graduated one only
    assert summ.twin_skipped_live_tier == 1
    # ...and live's own behaviour is exactly what it was.
    assert ex.live_tickers() == [f"{GRAD_EV}-8"]
    assert summ.skipped_live_tier == 1


def test_on_the_incumbent_paper_book_is_still_unbarred(settings):
    """The load-bearing asymmetry, restated. Paper must keep trading an unreviewed series —
    that is how it earns graduation. The twin is live's mirror, not paper, so only it moves."""
    _armed(settings, mmsell_twin_applies_live_bars=True)
    summ, _ = _run(settings)

    assert summ.per_book.get("mmsell10") == 2        # the live-tag paper book: BOTH
    assert summ.per_book.get("mmsell10_pt") == 1     # its twin: the graduated one only


def test_on_the_series_pause_applies_to_the_twin_too(settings):
    """The second bar. A series real money is paused on must not keep accruing twin trades,
    or the twin reports an edge on flow live was deliberately standing down from."""
    _armed(settings, mmsell_twin_applies_live_bars=True,
           mmsell_live_min_tier="unclassified", mmsell_live_skip_series=GRAD_SERIES)
    summ, _ = _run(settings)

    assert summ.per_book.get("mmsell10_pt") == 1     # the un-paused one only
    assert summ.twin_skipped_live_paused == 1
    assert summ.twin_skipped_live_tier == 0


def test_a_twin_refusal_is_never_counted_as_a_real_money_save(settings):
    """`skipped_live_*` reads as "real-money entries this bar refused". A twin refusal is a
    bookkeeping decision about a simulated book and must stay in its own counter."""
    _armed(settings, mmsell_twin_applies_live_bars=True)
    summ, _ = _run(settings)

    assert summ.twin_skipped_live_tier == 1          # the twin's refusal
    assert summ.skipped_live_tier == 1               # live's own, separately
