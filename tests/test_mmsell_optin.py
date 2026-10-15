"""Per-book OPT-IN to globally skipped series (`optin=`) — the temperature paper tape.

`mmsell_skip_series` (KXMVE, KXHIGH, KXLOW) is applied to the whole scan before any book sees an
event, so the only way to put ONE book on daily temperature without touching the rest is a
per-book key (docs/MMSELL_LOOKALIKE_PROBE.md). These tests pin the properties that make it
safe to ship into a tracker every mmsell book shares:

  * a book without `optin` never sees a skipped series — the entire running cohort;
  * opted-in events are ranked in their OWN list after the normal ones, so adding them cannot
    push a normal event past another book's top-N cut;
  * the opted-in book trades them, within its own depth, and only the series it named;
  * the live mirror is refused on every skipped series, whatever LIVE_STRATEGIES says — the
    opt-in is a paper-only reach;
  * a malformed or pointless opt-in drops the book rather than running it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot import db
from kalshi_bot.experiment_os import temperature_season as pkg
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.risk.manager import RiskManager

TEMP = f"{pkg.CELL_TAG}:{pkg.CELL_PARAMS}"


def _close(hours=20):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0)


def _mkt(ticker, close_dt, yes_bid_c=6, yes_ask_c=7, vol=500):
    return {"ticker": ticker, "yes_sub_title": ticker, "close_time": close_dt.isoformat(),
            "volume_fp": f"{vol}.0",
            "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
            "yes_ask_dollars": f"{yes_ask_c / 100:.4f}"}


def _ob(yes_bid_c=6, yes_ask_c=7):
    return {"orderbook_fp": {"yes_dollars": [[f"{yes_bid_c / 100:.4f}", "300"]],
                             "no_dollars": [[f"{(100 - yes_ask_c) / 100:.4f}", "300"]]}}


def _event(series, suffix, vol=500, hours=20):
    ev = f"{series}-{suffix}"
    ticker = f"{ev}-B70"
    return ({"event_ticker": ev, "series_ticker": series, "mutually_exclusive": True,
             "markets": [_mkt(ticker, _close(hours), vol=vol)]}, ticker)


class FakeClient:
    def __init__(self, events):
        self._events = [e for e, _t in events]
        self._books = {t: _ob() for _e, t in events}
        self.orderbooks: list[str] = []

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        self.orderbooks.append(ticker)
        return self._books[ticker]


def _setup(settings, variants, **over):
    settings.bot_mode = "mmsell"
    settings.mmsell_variants = variants
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()


def _run(settings, events, **kw):
    client = FakeClient(events)
    with db.session_scope() as session:
        summ = MmSellTracker(client, settings, **kw).run_once(session)
    return summ, client


# ------------------------------------------------------------------ isolation


def test_without_an_opted_in_book_a_skipped_series_is_dropped_as_before(settings):
    _setup(settings, "Xmmsell0:lo=5,hi=10,maxyes=7")
    summ, client = _run(settings, [_event("KXHIGHNY", "26OCT16")])
    assert summ.per_book == {}
    assert summ.events_optin == 0 and summ.markets_considered_optin == 0
    assert summ.markets_considered == 0
    assert client.orderbooks == [], "a skipped series must not even cost an orderbook fetch"


def test_only_the_opted_in_book_trades_the_skipped_series(settings):
    _setup(settings, f"{TEMP};Xmmsell0:lo=5,hi=10,maxyes=7")
    summ, _ = _run(settings, [_event("KXHIGHNY", "26OCT16"), _event("KXLOWTCHI", "26OCT16")])
    assert summ.per_book.get(pkg.CELL_TAG) == 2
    assert summ.per_book.get("Xmmsell0") is None
    assert summ.per_book.get("mmsell") is None      # the control is a book like any other
    assert summ.events_optin == 2 and summ.markets_considered_optin == 2
    # The control funnel does not count the opted-in reach.
    assert summ.events_seen == 0 and summ.markets_considered == 0 and summ.in_band == 0


def test_the_opted_in_book_trades_nothing_outside_its_own_series(settings):
    _setup(settings, TEMP)
    summ, _ = _run(settings, [_event("KXMLBTOTAL", "26OCT16NYYLAA"), _event("KXHIGHNY", "26OCT16")])
    assert summ.per_book.get(pkg.CELL_TAG) == 1
    assert summ.per_book.get("mmsell") == 1          # the normal event, for the control


def test_opted_in_events_never_displace_a_normal_event_from_the_top_n(settings):
    """The rank property. The temperature event carries far more volume than the normal one; if
    it were ranked in the shared list it would take rank 0 and push the normal event past a
    top-1 cut, silently changing every incumbent book's candidates."""
    _setup(settings, f"{TEMP};Xmmsell0:lo=5,hi=10,maxyes=7", mmsell_top_events=1)
    summ, _ = _run(settings, [_event("KXHIGHNY", "26OCT16", vol=1_000_000),
                              _event("KXMLBTOTAL", "26OCT16NYYLAA", vol=600)])
    assert summ.per_book.get("Xmmsell0") == 1
    assert summ.per_book.get("mmsell") == 1
    assert summ.per_book.get(pkg.CELL_TAG) == 1
    assert summ.events_seen == 1 and summ.events_optin == 1


def test_the_opted_in_book_is_cut_at_its_own_depth_among_opted_in_events(settings):
    _setup(settings, TEMP, mmsell_top_events=1)
    summ, client = _run(settings, [_event("KXHIGHNY", "26OCT16", vol=900),
                                   _event("KXHIGHCHI", "26OCT16", vol=5_000)])
    assert summ.per_book.get(pkg.CELL_TAG) == 1
    assert client.orderbooks == ["KXHIGHCHI-26OCT16-B70"], "the higher-volume event ranks first"


def test_the_book_respects_its_entry_window(settings):
    """`htcmax=48` mirrors the probe's T-48h..T-1h window; the global window stays 1h..336h."""
    _setup(settings, TEMP)
    summ, _ = _run(settings, [_event("KXHIGHNY", "26OCT20", hours=72)])
    assert summ.per_book.get(pkg.CELL_TAG) is None


# ------------------------------------------------------------------ live


def test_the_live_mirror_is_refused_on_a_skipped_series(settings):
    """Even with the tag allowlisted and the tier bar switched off, no live order fires: the
    skip list is a live bar for every book, so the opt-in can only ever reach paper."""
    _setup(settings, TEMP)
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = pkg.CELL_TAG
    settings.mmsell_live_min_tier = "unclassified"
    settings.live_max_order_dollars = 1.0
    settings.max_order_size = 100
    settings.max_market_exposure = 25.0
    settings.mmsell_live_max_spread_cents = 40

    class LiveClient:
        placed: list = []

        def create_events_order(self, order):
            self.placed.append(order)
            return {"order": {"order_id": "K-1", "status": "resting"}}

        def get_balance(self):
            return {"balance": 100_000}

    live = LiveClient()
    ex = LiveExecutor(live, settings, RiskManager(settings))
    client = FakeClient([_event("KXHIGHNY", "26OCT16")])
    tracker = MmSellTracker(client, settings, live_executor=ex)
    tracker._account_state = {"cash_balance": 150.0}
    with db.session_scope() as session:
        summ = tracker.run_once(session)
    assert summ.per_book.get(pkg.CELL_TAG) == 1     # the paper position stands
    assert live.placed == []


# ------------------------------------------------------------------ config


def test_optin_parses_and_defaults_to_empty(settings):
    settings.mmsell_variants = f"{TEMP};Xmmsell0:lo=5,hi=10"
    books = {b["tag"]: b for b in settings.mmsell_variant_list}
    assert books[pkg.CELL_TAG]["optin"] == ["KXHIGH", "KXLOW"]
    assert books["Xmmsell0"]["optin"] == []


def test_an_optin_naming_a_series_the_skip_list_does_not_drop_rejects_the_book(settings):
    """`optin=KXRAIN` admits nothing new while reading as a reach into a skipped family."""
    settings.mmsell_variants = "Xmmsell1:lo=5,hi=10,optin=KXRAIN;Xmmsell2:lo=5,hi=10,optin=KXHIGH"
    tags = {b["tag"] for b in settings.mmsell_variant_list}
    assert "Xmmsell1" not in tags and "Xmmsell2" in tags


def test_no_configured_book_opts_in_by_default(settings):
    """The shipped default: the opt-in exists, nothing uses it until the paper spec is appended."""
    assert all(not b["optin"] for b in settings.mmsell_variant_list)
