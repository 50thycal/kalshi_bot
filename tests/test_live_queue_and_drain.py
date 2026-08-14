"""QUEUE-POSITION sampling and the ORDERLY DRAIN — docs/LIVE_QUEUE_POSITION.md.

Both come from docs/EXTERNAL_REPO_RESEARCH.md (§1 and §6), and both touch real money, so the
properties worth pinning are the ones whose failure is SILENT:

  * a queue sampler that writes null ranks forever because Kalshi changed the payload shape looks
    exactly like a book with nothing resting;
  * a drain that reports success without the exchange having cancelled anything leaves live
    orders working while our own state says they are gone;
  * a cancel path behind the placement guard means flipping the kill switch FREEZES resting
    orders on the book instead of pulling them — the switch makes the position less controllable,
    which is the precise inversion of its purpose.

None of those three raise. All three are tested here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.live.queue_position import order_id_of, parse_batch, parse_one
from kalshi_bot.risk.manager import RiskManager

# ---------------------------------------------------------------- payload parsing
#
# Kalshi has already migrated a payload under us once (the orderbook grew `orderbook_fp` with
# `_dollars` STRING prices and dropped the integer keys, making a naive read silently None). The
# parser is shape-tolerant for that reason, and these tests fix what "tolerant" means so it does
# not quietly become "accepts anything".


def test_the_flat_shape_parses():
    s = parse_one({"order_id": "K-1", "market_ticker": "KXA-1", "queue_position": 4})
    assert s["queue_position"] == 4 and s["order_id"] == "K-1"


def test_a_string_rank_parses_because_that_is_the_house_style():
    """`count_fp`, `position_fp` and every `_dollars` price arrive as strings. A numeric field
    delivered as "3" is Kalshi's convention, not an anomaly."""
    assert parse_one({"order_id": "K-1", "queue_position": "3"})["queue_position"] == 3


def test_front_of_queue_is_not_confused_with_missing():
    """Rank 0 is the single most interesting value there is — it is the order about to fill. Any
    truthiness check anywhere in the parser turns it into 'no data'."""
    s = parse_one({"order_id": "K-1", "queue_position": 0})
    assert s is not None and s["queue_position"] == 0


def test_an_envelope_is_unwrapped_one_layer():
    assert parse_one({"order": {"order_id": "K-9", "queue_position": 7}})["queue_position"] == 7
    assert parse_one({"order_id": "K-9", "queue_position": 7})["queue_position"] == 7


def test_nothing_recognisable_returns_none_rather_than_a_zero():
    """The distinction the whole module exists for: 'we could not read this' must never arrive
    downstream as a rank of 0, which would be a confident claim of front-of-queue."""
    assert parse_one({"order_id": "K-1", "status": "resting"}) is None
    assert parse_one({"order_id": "K-1", "queue_position": None}) is None
    assert parse_one("not a dict") is None


def test_contracts_ahead_is_captured_but_never_invented():
    """Rank 3 behind three 1-lots is a different trade from rank 3 behind three 500-lots. When
    Kalshi does not tell us, the column stays null instead of being derived from the rank."""
    assert parse_one({"order_id": "K", "queue_position": 3,
                      "contracts_ahead": 250})["contracts_ahead"] == 250
    assert parse_one({"order_id": "K", "queue_position": 3})["contracts_ahead"] is None


def test_batch_reports_how_many_entries_it_could_not_read():
    """The count is the alarm. A batch of 40 that yields 0 samples is indistinguishable from an
    empty book if you only look at the returned dict."""
    samples, unreadable = parse_batch({"queue_positions": [
        {"order_id": "K-1", "queue_position": 2},
        {"order_id": "K-2", "status": "resting"},          # no rank
        {"queue_position": 5},                             # no order id -> unusable
    ]})
    assert set(samples) == {"K-1"}
    # the ROWS, not just a count — so each failure can be filed against its own order
    assert len(unreadable) == 2
    assert order_id_of(unreadable[0]) == "K-2"
    assert order_id_of(unreadable[1]) is None


def test_an_empty_batch_is_not_a_failure():
    assert parse_batch({"queue_positions": []}) == ({}, [])


# ---------------------------------------------------------------- fakes


class _QueueClient:
    """Only what reconcile's sampler and the drain touch."""

    def __init__(self, batch=None, single=None, cancel_exc=None):
        self._batch = batch if batch is not None else {"queue_positions": []}
        self._single = single
        self.cancel_exc = cancel_exc
        self.canceled: list[str] = []
        self.batch_calls = 0
        self.single_calls = 0

    def get_queue_positions(self, **kw):
        self.batch_calls += 1
        if isinstance(self._batch, Exception):
            raise self._batch
        return self._batch

    def get_order_queue_position(self, order_id):
        self.single_calls += 1
        if isinstance(self._single, Exception):
            raise self._single
        return self._single

    def cancel_events_order(self, order_id):
        if self.cancel_exc is not None:
            raise self.cancel_exc
        self.canceled.append(order_id)
        return {}


def _live(settings, **over):
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = "mmsell10a"
    for k, v in over.items():
        setattr(settings, k, v)
    return settings


def _db(settings):
    db.init_engine(settings.database_url)
    db.create_all()


def _resting(session, *, strategy="mmsell10a", koid="K-1", price=93, age_s=60):
    row = m.LiveOrder(
        market_ticker=f"KXT-{koid}", event_ticker="KXT", strategy=strategy,
        side="no", action="buy", limit_price=price, quantity=1, status="resting",
        kalshi_order_id=koid, client_order_id=f"c-{koid}",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=age_s),
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------- sampling


def test_a_rank_is_recorded_for_each_resting_order(settings):
    _live(settings)
    _db(settings)
    client = _QueueClient({"queue_positions": [
        {"order_id": "K-1", "queue_position": 0, "contracts_ahead": 0},
        {"order_id": "K-2", "queue_position": 12, "contracts_ahead": 340},
    ]})
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, koid="K-1")
        _resting(session, koid="K-2", price=94)
        assert ex.sample_queue_positions(session) == 2
        ticks = {t.kalshi_order_id: t for t in session.scalars(
            select(m.LiveOrderQueueTick)).all()}
    assert ticks["K-1"].queue_position == 0 and ticks["K-1"].contracts_ahead == 0
    assert ticks["K-2"].queue_position == 12 and ticks["K-2"].contracts_ahead == 340
    # the covariates any P(fill) fit needs, stored alongside so analysis never re-derives them
    assert ticks["K-2"].limit_price == 94
    assert ticks["K-1"].rest_seconds >= 60
    assert client.batch_calls == 1, "one batch call, not one call per order"


def test_only_resting_orders_are_sampled(settings):
    """`pending`/`submitted` rows are in flight — they are not on the book, so there is no queue
    rank to ask for and asking would just burn requests."""
    _live(settings)
    _db(settings)
    client = _QueueClient()
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        row = _resting(session, koid="K-1")
        row.status = "submitted"
        session.flush()
        assert ex.sample_queue_positions(session) == 0
    assert client.batch_calls == 0


def test_an_unreadable_payload_is_counted_not_swallowed(settings):
    """The silent-failure case. Rows still land (so the gap is visible in the data) AND the
    unparsed counter fires (so it is visible in the cycle summary)."""
    _live(settings)
    _db(settings)
    client = _QueueClient({"queue_positions": [{"order_id": "K-1", "rank_but_renamed": 3}]},
                          single=RuntimeError("no fallback either"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, koid="K-1")
        assert ex.sample_queue_positions(session) == 1
        tick = session.scalar(select(m.LiveOrderQueueTick))
        assert tick.queue_position is None
        assert tick.raw_json is not None, "the raw payload must survive for shape inspection"
    assert ex.summary.queue_unparsed == 1
    assert ex.summary.queue_sampled == 0


def test_a_dead_batch_endpoint_degrades_to_per_order_instead_of_stalling(settings):
    """The bug the first production deploy shipped. A total batch failure returned early and
    collected NOTHING — when the per-order path was available and would have collected
    everything. A batch failure is just the extreme case of 'the batch did not cover this
    order', which is exactly what the fallback exists for. Degrade, don't stall."""
    _live(settings)
    _db(settings)
    client = _QueueClient(batch=RuntimeError("400 bad request"),
                          single={"order_id": "K-1", "queue_position": 6})
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, koid="K-1")
        assert ex.sample_queue_positions(session) == 1
        assert session.scalar(select(m.LiveOrderQueueTick)).queue_position == 6
    assert client.single_calls == 1
    assert ex.summary.queue_sampled == 1


def test_both_paths_failing_still_records_the_attempt_and_never_raises(settings):
    _live(settings)
    _db(settings)
    client = _QueueClient(batch=RuntimeError("500"), single=RuntimeError("404"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session)
        assert ex.sample_queue_positions(session) == 1   # a row, so the gap is countable
        assert session.scalar(select(m.LiveOrderQueueTick)).queue_position is None
    assert ex.summary.queue_sampled == 0


def test_the_batch_call_is_scoped_to_the_tickers_we_have_resting(settings):
    """Smaller response, and the reference implementation always scopes this call — the
    unfiltered form may not be accepted at all."""
    seen = {}

    class _Recorder(_QueueClient):
        def get_queue_positions(self, **kw):
            seen.update(kw)
            return super().get_queue_positions(**kw)

    _live(settings)
    _db(settings)
    ex = LiveExecutor(_Recorder(), settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, koid="K-1")
        _resting(session, koid="K-2")
        ex.sample_queue_positions(session)
    assert seen.get("market_tickers") == "KXT-K-1,KXT-K-2"


def test_the_per_order_fallback_is_bounded(settings):
    """A Kalshi change that empties the batch response must not silently multiply our request
    rate forever. Above the cap the sampler still records rows — it just stops paying per order."""
    _live(settings)
    _db(settings)
    client = _QueueClient({"queue_positions": []}, single={"queue_position": 1})
    ex = LiveExecutor(client, settings, RiskManager(settings))
    over = ex._QUEUE_FALLBACK_MAX + 5
    with db.session_scope() as session:
        for i in range(over):
            _resting(session, koid=f"K-{i}")
        assert ex.sample_queue_positions(session) == over
    assert client.single_calls == 0, "over the cap, no per-order calls at all"


def test_sampling_can_be_switched_off_without_a_deploy(settings):
    _live(settings, live_queue_position_sampling=False)
    _db(settings)
    client = _QueueClient()
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session)
        assert ex.sample_queue_positions(session) == 0
    assert client.batch_calls == 0


# ---------------------------------------------------------------- the drain


def test_a_de_allowlisted_book_has_its_resting_orders_pulled(settings):
    """Turning a book off must turn its EXPOSURE off. Before this, a strategy dropped from
    LIVE_STRATEGIES stopped placing but its resting orders kept working, reachable only by the
    4-hour per-order timeout."""
    _live(settings, live_strategies="mmsell10a")
    _db(settings)
    client = _QueueClient()
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, strategy="mmsell10a", koid="KEEP")
        _resting(session, strategy="mmsell10b", koid="GONE")   # no longer allowlisted
        assert ex.drain_stood_down_books(session) == 1
        rows = {r.kalshi_order_id: r for r in session.scalars(select(m.LiveOrder)).all()}
    assert client.canceled == ["GONE"]
    assert rows["GONE"].status == "canceled" and rows["GONE"].cancel_reason == "book_stood_down"
    assert rows["KEEP"].status == "resting"


def test_the_kill_switch_drains_every_book(settings):
    """The inversion this fixes: the kill switch used to leave orders working AND block the
    cancel path, so flipping it made the position less controllable, not more."""
    _live(settings, kill_switch=True)
    _db(settings)
    client = _QueueClient()
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, strategy="mmsell10a", koid="A")
        _resting(session, strategy="theta4", koid="B")
        assert ex.drain_stood_down_books(session) == 2
        for r in session.scalars(select(m.LiveOrder)).all():
            assert r.status == "canceled" and r.cancel_reason == "kill_switch"
    assert sorted(client.canceled) == ["A", "B"]


def test_a_failed_cancel_leaves_the_order_resting_for_retry(settings):
    """`rodlaf`'s discipline (§6): verify before dropping state. Marking a row `canceled` on a
    cancel we could not confirm is how our state drifts away from the exchange's — the order
    would still be working while we believed it gone."""
    _live(settings, live_strategies="")
    _db(settings)
    client = _QueueClient(cancel_exc=RuntimeError("503"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, strategy="mmsell10a", koid="STUCK")
        assert ex.drain_stood_down_books(session) == 0
        assert session.scalar(select(m.LiveOrder)).status == "resting"
    assert ex.summary.drain_failed == 1
    assert ex.summary.drained_canceled == 0


def test_the_drain_can_be_switched_off(settings):
    _live(settings, live_strategies="", live_drain_stood_down=False)
    _db(settings)
    client = _QueueClient()
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, strategy="mmsell10a")
        assert ex.drain_stood_down_books(session) == 0
    assert client.canceled == []


def test_orders_with_no_exchange_id_are_not_drained(settings):
    """Nothing to cancel and no way to name it. Marking such a row `canceled` would be a lie
    about an order that may yet land."""
    _live(settings, live_strategies="")
    _db(settings)
    client = _QueueClient()
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        row = _resting(session, strategy="mmsell10a")
        row.kalshi_order_id = None
        session.flush()
        assert ex.drain_stood_down_books(session) == 0
        assert session.scalar(select(m.LiveOrder)).status == "resting"


def test_resting_query_ignores_non_resting_and_filters_by_book(settings):
    _live(settings)
    _db(settings)
    with db.session_scope() as session:
        _resting(session, strategy="mmsell10a", koid="R1")
        row = _resting(session, strategy="mmsell10a", koid="P1")
        row.status = "pending"
        _resting(session, strategy="theta4", koid="R2")
        session.flush()
        assert {r.kalshi_order_id for r in repo.get_resting_live_orders(session)} == {"R1", "R2"}
        assert {r.kalshi_order_id
                for r in repo.get_resting_live_orders(session, ["theta4"])} == {"R2"}
