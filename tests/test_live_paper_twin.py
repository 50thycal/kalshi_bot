"""The live/paper parallel-run harness (kalshi_bot/twin) and its first consumer, the mmsell book.
(theta is a second consumer, with its own fixtures — see tests/test_theta_live_twin.py — since
the harness itself, tested here, is fully generic over which book uses it.)

The properties that make a twin a one-to-one control — and that a future refactor must not
silently break:

  * a twin exists ONLY while its live tag is genuinely armed (so both sides cover one window);
  * a twin is a FRESH tag with no prior history, and its epoch `started_at` never moves;
  * a twin is priced and sized by the LIVE rules (maker no-bid + offset, dollar-cap sizing, live
    open cap), not by the paper book's own knobs — the only difference from live is the fill;
  * a twin NEVER places a real order;
  * every candidate decision is taped with the reason live did or didn't follow its twin;
  * changing the live knobs mid-epoch is flagged as param drift, not silently absorbed.
"""

from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.live.sizing import maker_no_price, order_quantity
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.paper.engine import PaperTradingEngine
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.twin import TwinHarness

# --- fixtures ----------------------------------------------------------------


def _mkt(ticker, sub, yes_bid_c, yes_ask_c, vol=500, hours=48):
    close = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    return {
        "ticker": ticker,
        "yes_sub_title": sub,
        "close_time": close,
        "volume_fp": f"{vol}.0",
        "yes_bid_dollars": f"{yes_bid_c / 100:.4f}",
        "yes_ask_dollars": f"{yes_ask_c / 100:.4f}",
    }


def _event(markets, event="KXTEAM-26", series="KXTEAM"):
    return {"event_ticker": event, "series_ticker": series, "markets": markets}


def _ob(yes_bid_c, yes_ask_c, depth=300):
    no_bid_c = 100 - yes_ask_c
    return {"orderbook_fp": {
        "yes_dollars": [[f"{yes_bid_c / 100:.4f}", str(depth)]],
        "no_dollars": [[f"{no_bid_c / 100:.4f}", str(depth)]],
    }}


class FakeClient:
    """Scan + orderbook + order placement, so one tracker cycle can drive paper, twin and live."""

    def __init__(self, events, books, market_state=None):
        self._events = events
        self._books = books
        self._state = market_state or {}
        self.placed: list[dict] = []

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200, cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]

    def get_market(self, ticker):
        return {"market": self._state[ticker]}

    def create_events_order(self, order):
        self.placed.append(order)
        return {"order": {"order_id": f"K-{len(self.placed)}", "status": "resting"}}

    def get_balance(self):
        return {"balance": 100_000}


def _armed(settings, **over):
    """A fully armed live deployment trading mmsell10, with its twin auto-derived."""
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = "mmsell10"
    settings.live_max_order_dollars = 1.0
    settings.max_order_size = 100
    settings.max_market_exposure = 25.0
    settings.mmsell_live_max_open_positions = 60
    settings.mmsell_live_price_offset_cents = 0
    settings.mmsell_live_max_spread_cents = 40
    settings.mmsell_variants = "mmsell10:lo=5,hi=10,maxyes=7"
    settings.mmsell_capture_candidates = False
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()
    return settings


def _tracker(settings, client):
    ex = LiveExecutor(client, settings, RiskManager(settings))
    tr = MmSellTracker(client, settings, live_executor=ex,
                       twin_harness=TwinHarness(settings))
    tr._account_state = {"cash_balance": 150.0}
    return tr


# a cheap longshot: yes 5/7 -> mid 6 (in mmsell10's 5-10 band), yes-ask 7 <= maxyes 7,
# so the maker buys NO at 100-7 = 93.
def _cheap_event():
    return _event([_mkt("KXTEAM-26-A", "A", 5, 7)]), {"KXTEAM-26-A": _ob(5, 7)}


# --- config ------------------------------------------------------------------


def test_twin_pairs_auto_derive_from_live_strategies(settings):
    settings.live_strategies = "mmsell10,weather_fav"
    assert settings.live_paper_twin_pairs == [
        ("mmsell10", "mmsell10_pt"), ("weather_fav", "weather_fav_pt")]


def test_twin_pairs_explicit_override_and_custom_suffix(settings):
    settings.live_strategies = "mmsell10"
    settings.live_paper_twins = "mmsell10:mm10shadow"
    assert settings.live_paper_twin_pairs == [("mmsell10", "mm10shadow")]
    settings.live_paper_twins = ""
    settings.live_paper_twin_suffix = "_twin"
    assert settings.live_paper_twin_pairs == [("mmsell10", "mmsell10_twin")]


def test_an_explicit_twin_entry_that_shadows_the_suffix_is_reported(settings):
    """The 2026-08-11 silent no-op. Cutting a fresh twin epoch is done by bumping the SUFFIX, but
    LIVE_PAPER_TWINS outranks it — so with the old tags named explicitly the bump changed nothing,
    the redeploy came up clean, and the previous epoch kept accumulating for hours. Nothing in the
    system disagreed with anything; the losing setting was simply silent."""
    settings.live_strategies = "mmsell10a,mmsell10b"
    settings.live_paper_twin_suffix = "_pt3"
    settings.live_paper_twins = "mmsell10a:mmsell10a_pt2,mmsell10b:mmsell10b_pt2"
    assert settings.live_paper_twin_shadowed_pairs == [
        ("mmsell10a", "mmsell10a_pt2", "mmsell10a_pt3"),
        ("mmsell10b", "mmsell10b_pt2", "mmsell10b_pt3"),
    ]
    # and the pairs really do follow the explicit entry, which is what made it invisible
    assert settings.live_paper_twin_pairs == [
        ("mmsell10a", "mmsell10a_pt2"), ("mmsell10b", "mmsell10b_pt2")]

    # clearing the explicit map is the fix — suffix takes over, nothing left to warn about
    settings.live_paper_twins = ""
    assert settings.live_paper_twin_shadowed_pairs == []
    assert settings.live_paper_twin_pairs == [
        ("mmsell10a", "mmsell10a_pt3"), ("mmsell10b", "mmsell10b_pt3")]


def test_no_shadow_warning_when_the_explicit_entry_agrees_or_omits_the_tag(settings):
    """Only a genuine DISAGREEMENT is worth a warning: an explicit entry naming exactly what the
    suffix would produce is harmless, and an entry with no twin tag at all already follows it.
    Warning on those would train everyone to ignore the log line."""
    settings.live_strategies = "mmsell10a"
    settings.live_paper_twin_suffix = "_pt3"
    settings.live_paper_twins = "mmsell10a:mmsell10a_pt3"     # agrees
    assert settings.live_paper_twin_shadowed_pairs == []
    settings.live_paper_twins = "mmsell10a"                    # no tag -> derives from suffix
    assert settings.live_paper_twin_shadowed_pairs == []
    settings.live_paper_twin_enabled = False
    settings.live_paper_twins = "mmsell10a:mmsell10a_pt2"
    assert settings.live_paper_twin_shadowed_pairs == []       # twins off -> nothing to shadow


def test_twin_tag_can_never_be_a_live_tag(settings):
    # A twin tag that is itself allowlisted for live could place real orders — drop the pair.
    settings.live_strategies = "mmsell10,mmsell10_pt"
    assert ("mmsell10", "mmsell10_pt") not in settings.live_paper_twin_pairs


def test_twins_can_be_disabled_entirely(settings):
    settings.live_strategies = "mmsell10"
    settings.live_paper_twin_enabled = False
    assert settings.live_paper_twin_pairs == []
    settings.live_paper_twin_enabled = True
    settings.live_paper_twin_auto = False
    assert settings.live_paper_twin_pairs == []


def test_twin_tag_fits_the_paper_strategy_column(settings):
    settings.live_strategies = "a" * 30
    for _live, twin in settings.live_paper_twin_pairs:
        assert len(twin) <= 24   # paper_trades.strategy is String(24)


# --- arming ------------------------------------------------------------------


def test_twin_is_inactive_unless_live_is_genuinely_armed(settings):
    _armed(settings)
    h = TwinHarness(settings)
    assert h.enabled and h.active_for("mmsell10")
    for over in ({"kill_switch": True}, {"live_enabled": False},
                 {"bot_mode": "weather"}, {"live_strategies": ""}):
        for k, v in over.items():
            prev = getattr(settings, k)
            setattr(settings, k, v)
            assert not TwinHarness(settings).active_for("mmsell10"), f"armed despite {over}"
            setattr(settings, k, prev)


def test_no_twin_book_runs_while_live_is_paused(settings):
    _armed(settings, kill_switch=True)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    with db.session_scope() as session:
        _tracker(settings, client).run_once(session)
    with db.session_scope() as session:
        # the paper book still runs; the twin does not exist, and no epoch was opened
        assert session.scalar(select(func.count()).select_from(m.LivePaperTwin)) == 0
        tags = set(session.scalars(select(m.PaperTrade.strategy)))
        assert not any((t or "").endswith("_pt") for t in tags)


# --- the twin book -----------------------------------------------------------


def test_twin_opens_beside_live_priced_and_sized_LIKE_LIVE(settings):
    _armed(settings, live_max_order_dollars=5.0)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    with db.session_scope() as session:
        summ = _tracker(settings, client).run_once(session)

    assert summ.opened >= 1 and summ.twin_opened == 1
    assert len(client.placed) == 1          # exactly ONE real order: the live book's, not the twin's

    with db.session_scope() as session:
        twin = session.scalar(select(m.PaperTrade).where(m.PaperTrade.strategy == "mmsell10_pt"))
        parent = session.scalar(select(m.PaperTrade).where(m.PaperTrade.strategy == "mmsell10"))
        live = session.scalar(select(m.LiveOrder))
        # twin price == the live maker price (no-bid + offset, capped at the no-ask)
        assert twin.assumed_price == live.limit_price == 93
        # twin size == the live dollar-cap size ($5 / 0.93 -> 5), NOT paper's 1-contract clip
        assert twin.quantity == live.quantity == 5
        assert parent.quantity == settings.paper_order_size == 1
        assert twin.side == "no" and twin.action == "buy"


def test_twin_of_an_AB_ARM_book_sizes_and_prices_off_THAT_BOOKS_knobs(settings):
    """Regression, found on the live dashboard: mmsell10b_pt2 showed 282 contracts across 141
    positions (2.0/position) beside live mmsell10b's 98/98 (1.0), and ~3x the capital deployed.

    Cause: the twin entry block inlined its own copy of the live price/size arithmetic, reading
    the GLOBAL max_order_size and the GLOBAL a/b arm config instead of the arm book's own `size`
    and `abarm`. So the twin of a 1-contract arm booked the global clip, and the twin of arm 1
    could price at arm 0's offset. Both halves of a twin comparison have to come from the same
    helper as live or `twin - live` stops meaning "the cost of not filling"."""
    _armed(settings, live_max_order_dollars=5.0,      # global sizing alone would buy 5
           live_strategies="mmsell10a,mmsell10b",
           mmsell_live_offset_ab_arms="0,1",
           mmsell_live_offset_ab_salt="t",
           mmsell_variants=("mmsell10a:lo=5,hi=10,maxyes=7,abarm=0,size=1;"
                            "mmsell10b:lo=5,hi=10,maxyes=7,abarm=1,size=1"))
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    with db.session_scope() as session:
        summ = _tracker(settings, client).run_once(session)

    # exactly one arm admits the ticker, so exactly one live order and one twin entry
    assert summ.twin_opened == 1
    assert len(client.placed) == 1

    with db.session_scope() as session:
        live = session.scalar(select(m.LiveOrder))
        twin = session.scalar(
            select(m.PaperTrade).where(m.PaperTrade.strategy.endswith("_pt")))
        assert twin.strategy == f"{live.strategy}_pt"       # the twin of the arm that traded
        assert twin.quantity == live.quantity == 1          # the BOOK's size=1, not the global 5
        assert twin.assumed_price == live.limit_price       # and the arm's own offset


def test_twin_places_no_real_order_even_when_allowlisted_by_prefix(settings):
    # "mmsell10" is a prefix of "mmsell10_pt": the executor's prefix allowlist must not be the
    # only thing standing between a twin and real money — the twin path never calls it at all.
    _armed(settings)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    with db.session_scope() as session:
        _tracker(settings, client).run_once(session)
    assert len(client.placed) == 1
    with db.session_scope() as session:
        strategies = list(session.scalars(select(m.LiveOrder.strategy)))
        assert strategies == ["mmsell10"]


def test_executor_refuses_a_twin_tag_even_if_the_prefix_allowlist_matches(settings):
    # "mmsell10_pt".startswith("mmsell10") is True, so the prefix allowlist alone would admit a
    # twin. Defense in depth for a future refactor that routes a twin through the executor.
    _armed(settings)
    ex = LiveExecutor(FakeClient([], {}), settings, RiskManager(settings))
    assert ex._allowed("mmsell10") is True
    assert ex._allowed("mmsell10_pt") is False


def test_twin_uses_the_live_open_cap_not_the_paper_cap(settings):
    _armed(settings, mmsell_live_max_open_positions=1, mmsell_max_open_positions=200)
    markets = [_mkt(f"KXTEAM-26-{i}", f"m{i}", 5, 7) for i in range(3)]
    books = {f"KXTEAM-26-{i}": _ob(5, 7) for i in range(3)}
    with db.session_scope() as session:
        summ = _tracker(settings, FakeClient([_event(markets)], books)).run_once(session)
    assert summ.twin_opened == 1            # capped like live
    assert summ.opened >= 3                 # the paper books were not
    with db.session_scope() as session:
        capped = session.scalars(
            select(m.LivePaperParityEvent).where(
                m.LivePaperParityEvent.twin_outcome == "skip_cap")).all()
        assert len(capped) == 2             # and the cap skips are on the tape


def test_twin_respects_the_live_spread_sanity_gate(settings):
    # a market-quality gate the LIVE book applies must apply to the twin too (same candidate set)
    _armed(settings, mmsell_live_max_spread_cents=1)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        summ = _tracker(settings, FakeClient([ev], books)).run_once(session)
    assert summ.twin_opened == 0
    with db.session_scope() as session:
        ev_row = session.scalar(select(m.LivePaperParityEvent))
        assert ev_row.twin_outcome == "skip_spread"


def test_twin_settles_through_the_shared_paper_engine(settings):
    _armed(settings)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    with db.session_scope() as session:
        _tracker(settings, client).run_once(session)
    client._state["KXTEAM-26-A"] = {"status": "settled", "result": "no"}   # longshot missed
    engine = PaperTradingEngine(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        engine.manage_open_positions(session)
        twin = session.scalar(select(m.PaperTrade).where(m.PaperTrade.strategy == "mmsell10_pt"))
        assert twin.status == "settled" and float(twin.pnl) > 0


# --- epoch -------------------------------------------------------------------


def test_epoch_is_written_once_and_started_at_never_moves(settings):
    _armed(settings)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    with db.session_scope() as session:
        _tracker(settings, client).run_once(session)
    with db.session_scope() as session:
        row = session.scalar(select(m.LivePaperTwin))
        first_start = row.started_at
        assert row.twin_tag == "mmsell10_pt" and row.live_tag == "mmsell10"
        assert row.params_json["band_cents"] == [5.0, 10.0]
        assert row.params_json["maxyes"] == 7

    # a later cycle (fresh harness = a redeploy) must NOT restart the epoch
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)
    with db.session_scope() as session:
        rows = session.scalars(select(m.LivePaperTwin)).all()
        assert len(rows) == 1 and rows[0].started_at == first_start


def test_param_drift_is_flagged_not_absorbed(settings):
    _armed(settings)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)

    # operator retunes the LIVE knobs mid-epoch: the comparison is no longer one-to-one
    settings.mmsell_live_price_offset_cents = 3
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)
    with db.session_scope() as session:
        drift = session.scalars(
            select(m.SystemEvent).where(m.SystemEvent.component == "twin")).all()
        assert len(drift) == 1 and "drifted" in drift[0].message
        # the stored snapshot is left intact so the epoch stays interpretable
        row = session.scalar(select(m.LivePaperTwin))
        assert row.params_json["live_price_offset_cents"] == 0


def test_harness_reports_drift_via_return_value(settings):
    _armed(settings)
    h = TwinHarness(settings)
    spec = h.specs[0]
    with db.session_scope() as session:
        assert h.ensure_epoch(session, spec, {"a": 1}) is True
    with db.session_scope() as session:
        assert TwinHarness(settings).ensure_epoch(session, spec, {"a": 2}) is False


# --- retirement ----------------------------------------------------------------


def test_retiring_a_live_book_closes_its_stale_epoch(settings):
    # mmsell10 was live, got an epoch, then the operator drops it from LIVE_STRATEGIES in favor
    # of a replacement (e.g. mmsell10a/mmsell10b) -- the old epoch must not sit open forever.
    _armed(settings)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)
    with db.session_scope() as session:
        row = session.scalar(select(m.LivePaperTwin))
        assert row.ended_at is None  # sanity: the fixture actually opened one

    closed = None
    with db.session_scope() as session:
        closed = repo.reconcile_stale_twin_epochs(
            session, live_strategy_prefixes=["theta4", "mmsell10a", "mmsell10b"])
    assert closed == ["mmsell10_pt"]

    with db.session_scope() as session:
        row = session.scalar(select(m.LivePaperTwin))
        assert row.ended_at is not None
        assert row.live_tag in row.notes and "no longer trading" in row.notes


def test_reconcile_leaves_a_still_configured_epoch_open(settings):
    _armed(settings)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)

    with db.session_scope() as session:
        closed = repo.reconcile_stale_twin_epochs(
            session, live_strategy_prefixes=["mmsell10", "theta4"])
    assert closed == []
    with db.session_scope() as session:
        assert session.scalar(select(m.LivePaperTwin)).ended_at is None


def test_reconcile_uses_prefix_matching_like_active_for(settings):
    # mirrors TwinHarness.active_for's own startswith semantics: a live_tag counts as still
    # configured if it starts with ANY configured prefix, not just an exact match.
    _armed(settings)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)

    with db.session_scope() as session:
        closed = repo.reconcile_stale_twin_epochs(session, live_strategy_prefixes=["mmsell1"])
    assert closed == []  # "mmsell10" starts with the "mmsell1" prefix -> still counts as live


def test_reconcile_is_idempotent_on_an_already_closed_epoch(settings):
    _armed(settings)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)

    with db.session_scope() as session:
        first = repo.reconcile_stale_twin_epochs(session, live_strategy_prefixes=[])
    assert first == ["mmsell10_pt"]

    # a second reconcile pass (e.g. a later restart) must not re-touch an already-ended epoch
    with db.session_scope() as session:
        second = repo.reconcile_stale_twin_epochs(session, live_strategy_prefixes=[])
    assert second == []


# --- the parity tape ---------------------------------------------------------


def test_parity_tape_records_all_three_actors(settings):
    _armed(settings)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)
    with db.session_scope() as session:
        row = session.scalar(select(m.LivePaperParityEvent).where(
            m.LivePaperParityEvent.twin_tag == "mmsell10_pt"))
        assert row.live_tag == "mmsell10" and row.market_ticker == "KXTEAM-26-A"
        assert row.parent_outcome == "opened" and row.parent_price == 93
        assert row.twin_outcome == "opened" and row.twin_price == 93 and row.twin_quantity == 1
        assert row.live_outcome == "placed" and row.live_price == 93
        assert row.series == "KXTEAM" and row.no_bid == 93 and row.yes_mid == 6.0


def test_parity_tape_names_the_gate_that_stopped_live(settings):
    # live is capped at zero open positions -> it places nothing, but the twin still trades;
    # the tape must attribute the divergence to the cap rather than leave it unexplained.
    _armed(settings, mmsell_live_max_open_positions=0)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    with db.session_scope() as session:
        _tracker(settings, client).run_once(session)
    assert client.placed == []
    with db.session_scope() as session:
        row = session.scalar(select(m.LivePaperParityEvent))
        assert row.live_outcome == "gate:open_cap"
        # the twin is capped by the same knob, so it too stands down: capacity, not edge
        assert row.twin_outcome == "skip_cap"


def test_live_is_re_consulted_when_paper_dedups_but_will_not_double_up(settings):
    """Paper holding a ticker must not lock LIVE out of it — paper's position is an assumption,
    live's fill is a fact (see MmSellTracker._maybe_retry_live). So on the second cycle live is
    genuinely re-consulted rather than recorded as `not_attempted` (which meant "never looked"),
    and it stands down on its OWN dedup because the first order is still resting. A retry can
    therefore never double up on a market live is already working."""
    _armed(settings)
    ev, books = _cheap_event()
    client = FakeClient([ev], books)
    with db.session_scope() as session:
        _tracker(settings, client).run_once(session)
    with db.session_scope() as session:      # second cycle: both books already hold the ticker
        _tracker(settings, client).run_once(session)
    assert len(client.placed) == 1           # the retry did NOT place a duplicate real order
    with db.session_scope() as session:
        rows = session.scalars(select(m.LivePaperParityEvent).order_by(
            m.LivePaperParityEvent.id)).all()
        assert rows[-1].parent_outcome == "skip_already_open"
        assert rows[-1].twin_outcome == "skip_already_open"
        assert rows[-1].live_outcome == "gate:dedup"


def test_parity_tape_can_be_switched_off(settings):
    _armed(settings, live_paper_twin_parity_events=False)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        summ = _tracker(settings, FakeClient([ev], books)).run_once(session)
    assert summ.twin_opened == 1              # the twin still trades
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(m.LivePaperParityEvent)) == 0


def test_parity_tape_is_capped_per_cycle(settings):
    _armed(settings, live_paper_twin_parity_max=2)
    markets = [_mkt(f"KXTEAM-26-{i}", f"m{i}", 5, 7) for i in range(5)]
    books = {f"KXTEAM-26-{i}": _ob(5, 7) for i in range(5)}
    with db.session_scope() as session:
        _tracker(settings, FakeClient([_event(markets)], books)).run_once(session)
    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(m.LivePaperParityEvent)) == 2


# --- shared live sizing ------------------------------------------------------


class _FakeCursor:
    """Routes SQL to a canned result by a matching substring — enough to test
    live_paper_parity's report functions without a real Postgres connection."""

    def __init__(self, routes: list[tuple[str, list[tuple]]]):
        self._routes = routes
        self._result: list[tuple] = []

    def execute(self, sql, params=()):
        for substr, result in self._routes:
            if substr in sql:
                self._result = result
                return
        raise AssertionError(f"unrouted query in fake cursor: {sql[:120]}")

    def fetchall(self):
        return self._result


def test_report_execution_does_not_double_convert_fill_price(capsys):
    """Regression: fills.price is recorded on the SIDE TRADED (executor.reconcile stores
    no_price_dollars directly for side='no'), so for mmsell it is ALREADY the NO cost basis —
    the same quantity as live_orders.limit_price. A `100 - f.price` conversion (the original,
    buggy version of this query) double-flips it, turning a sane ~93c fill into a nonsense
    ~7c one and a ~-87c px_gap. This pins avg(f.price) used directly, with fill_px landing
    close to live_px rather than being its complement."""
    cur = _FakeCursor([
        ("GROUP BY 1", [("filled", 1)]),
        ("avg(limit_price)", [(93.0,)]),
        ("avg(f.price)", [(93.2,)]),          # fills.price is already the NO cost basis
        ("avg(assumed_price)", [(93.4,)]),
    ])
    epoch = {"twin_tag": "mmsell10_pt", "live_tag": "mmsell10",
             "started_at": "2026-07-26T21:09:46Z"}
    lpp.report_execution(cur, [epoch], days=0)
    out = capsys.readouterr().out
    assert "93.2c" in out            # fill_px reported as-is, not flipped to ~6.8c
    assert "6.8c" not in out
    assert "-86" not in out          # the old bug's px_gap magnitude
    assert "-0.20c" in out           # gap = fill_px(93.2) - twin_px(93.4), a sane small number


def test_live_sizing_helpers_are_the_single_source_of_truth():
    """The twin and the executor must price/size identically; both call these. Args are
    explicit (not read off settings) so a caller can never silently borrow another book's
    live knobs — pin the exact call shape both mmsell's executor and its twin use."""

    class _M:
        best_no_bid = 90
        best_no_ask = 93

    # post_only means the ceiling is one cent BELOW the ask -- resting AT it is a cross, which
    # Kalshi rejects outright rather than filling (see maker_no_price).
    assert maker_no_price(_M(), None, 5) == 92          # offset capped just under the no-ask
    assert maker_no_price(_M(), 80, 5) == 85            # explicit base + offset
    assert order_quantity(93, 5.0, 100) == 5            # $5 / 0.93 -> 5 contracts
    assert order_quantity(93, 5.0, 2) == 2              # hard cap wins
    assert order_quantity(0, 5.0, 100) == 0


def test_mmsell_live_sizing_call_sites_pass_mmsells_own_knobs_unchanged(settings):
    """Regression pin for the sizing.py signature change: mirror_mmsell_entry and the twin's
    own pricing must derive the identical price/qty they did before the refactor — a second
    live book (theta) getting its own knobs must not perturb mmsell10's already-live values."""
    settings.mmsell_live_price_offset_cents = 5
    settings.live_max_order_dollars = 5.0
    settings.max_order_size = 100

    class _M:
        best_no_bid = 90
        best_no_ask = 93

    price = maker_no_price(_M(), None, settings.mmsell_live_price_offset_cents)
    assert price == 92                                   # capped just BELOW the no-ask (post_only)
    qty = order_quantity(price, settings.live_max_order_dollars, settings.max_order_size)
    assert qty == 5                                       # $5 / 0.92 -> 5 contracts
    settings.max_order_size = 2
    assert order_quantity(price, settings.live_max_order_dollars, settings.max_order_size) == 2


def test_executor_returns_the_outcome_code_for_the_tape(settings):
    _armed(settings)
    from kalshi_bot.scanner.metrics import MarketMetrics

    metrics = MarketMetrics(
        ticker="KXTEAM-26-A", best_yes_bid=5, best_yes_ask=7, best_no_bid=93, best_no_ask=95,
        midpoint=6.0, spread=2, depth_at_best_bid=300, depth_at_best_ask=300, top_depth=300,
        volume=1000, open_interest=500, last_price=7, time_to_close_seconds=48 * 3600,
        liquidity_score=10.0, two_sided=True, raw_orderbook={},
    )
    ex = LiveExecutor(FakeClient([], {}), settings, RiskManager(settings))
    with db.session_scope() as session:
        assert ex.mirror_mmsell_entry(
            session, strategy="mmsell10", event_ticker="KXTEAM-26", ticker="KXTEAM-26-A",
            metrics=metrics, no_price=93, account_state=None) == "gate:no_balance"
        assert ex.mirror_mmsell_entry(
            session, strategy="not_allowlisted", event_ticker="KXTEAM-26", ticker="KXTEAM-26-A",
            metrics=metrics, no_price=93,
            account_state={"cash_balance": 1.0}) == "gate:switches"
        assert ex.mirror_mmsell_entry(
            session, strategy="mmsell10", event_ticker="KXTEAM-26", ticker="KXTEAM-26-A",
            metrics=metrics, no_price=93, account_state={"cash_balance": 150.0}) == "placed"
        assert ex.mirror_mmsell_entry(   # same ticker again
            session, strategy="mmsell10", event_ticker="KXTEAM-26", ticker="KXTEAM-26-A",
            metrics=metrics, no_price=93, account_state={"cash_balance": 150.0}) == "gate:dedup"


# --- the parity report's judgement -------------------------------------------

_SPEC = importlib.util.spec_from_file_location(
    "live_paper_parity",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "live_paper_parity.py",
)
lpp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lpp)  # type: ignore[union-attr]

_EPOCH = {"twin_tag": "mmsell10_pt", "live_tag": "mmsell10"}


def _side(n, wins, pnl, qty):
    return (n, wins, pnl, qty)


def test_verdict_waits_for_a_sample():
    v = lpp._verdict(_EPOCH, _side(5, 5, 0.1, 5), _side(2, 2, 0.02, 2),
                     {"n": 0, "gap": None, "twin_per_ct": None, "live_per_ct": None})
    assert "TOO EARLY" in v


def test_verdict_calls_a_matched_market_gap_an_accounting_error():
    # same markets, same window, but paper's own arithmetic disagrees with the real fill -> the
    # SIMULATOR is wrong, which is the finding that would invalidate every paper gate we use.
    matched = {"n": 40, "twin_per_ct": 3.0, "live_per_ct": 0.5, "gap": 2.5}
    v = lpp._verdict(_EPOCH, _side(100, 94, 3.0, 100), _side(100, 80, 0.5, 100), matched)
    assert "ACCOUNTING GAP" in v


def test_verdict_calls_a_book_level_gap_an_execution_problem():
    # per-market accounting agrees; the books diverge because live couldn't capture the trades
    matched = {"n": 40, "twin_per_ct": 1.0, "live_per_ct": 0.9, "gap": 0.1}
    v = lpp._verdict(_EPOCH, _side(100, 94, 3.0, 100), _side(60, 45, 0.2, 60), matched)
    assert "EXECUTION GAP" in v


def test_verdict_confirms_alignment_when_both_agree():
    matched = {"n": 40, "twin_per_ct": 1.0, "live_per_ct": 0.95, "gap": 0.05}
    v = lpp._verdict(_EPOCH, _side(100, 94, 1.0, 100), _side(100, 93, 0.98, 100), matched)
    assert "ALIGNED" in v


def test_per_contract_is_none_without_contracts():
    assert lpp._per_ct(1.0, 0) is None
    assert abs(lpp._per_ct(1.0, 100) - 1.0) < 1e-9


def test_a_superseded_twin_tag_is_closed_even_though_its_live_book_runs_on(settings):
    """The PARAM DRIFT warning says to "start a new twin tag", so an operator bumps the suffix
    (mmsell10a_pt -> mmsell10a_pt2). The old epoch's live tag is STILL armed, so the retired-book
    rule can't catch it, and it sits open forever accruing nothing. Seen in production: three
    orphaned `_pt` epochs still open beside their live `_pt2` successors."""
    _armed(settings)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)  # opens mmsell10_pt

    # operator rotates the suffix; mmsell10 itself keeps trading
    settings.live_paper_twin_suffix = "_pt2"
    with db.session_scope() as session:
        closed = repo.reconcile_stale_twin_epochs(
            session, live_strategy_prefixes=settings.live_strategy_list,
            configured_pairs=settings.live_paper_twin_pairs)
    assert closed == ["mmsell10_pt"]

    with db.session_scope() as session:
        row = session.scalar(select(m.LivePaperTwin).where(
            m.LivePaperTwin.twin_tag == "mmsell10_pt"))
        assert row.ended_at is not None
        assert "superseded by mmsell10_pt2" in row.notes
        assert "still live" in row.notes   # distinguishes it from a retired book


def test_the_currently_configured_twin_is_never_closed(settings):
    _armed(settings)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)
    with db.session_scope() as session:
        closed = repo.reconcile_stale_twin_epochs(
            session, live_strategy_prefixes=settings.live_strategy_list,
            configured_pairs=settings.live_paper_twin_pairs)
    assert closed == []


def test_omitting_configured_pairs_keeps_the_retired_book_rule_only(settings):
    # back-compat: without the pair list, only rule 1 applies and a suffix bump is invisible
    _armed(settings)
    ev, books = _cheap_event()
    with db.session_scope() as session:
        _tracker(settings, FakeClient([ev], books)).run_once(session)
    settings.live_paper_twin_suffix = "_pt2"
    with db.session_scope() as session:
        assert repo.reconcile_stale_twin_epochs(
            session, live_strategy_prefixes=settings.live_strategy_list) == []
