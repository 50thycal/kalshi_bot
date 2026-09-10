"""QUEUE-AWARE CANCELLATION — docs/MMSELL_QUEUE_AWARE_CANCEL.md.

The properties pinned here are the ones whose failure would be SILENT and whose cost is
real money or a corrupted experiment:

  * an API failure, a stale sample or an unreadable payload must resolve to KEEP, so a
    telemetry outage can never become a wave of cancellations;
  * the treatment may cancel; it may never change an order's price or size;
  * the ordinary 4h timeout applies before and independently of the rule;
  * shadow mode writes the full audit row and sends nothing;
  * live mode sends nothing for a tag that is not registered to an active LIVE treatment
    arm of the queue experiment — the lifecycle bypass NEW_ONLY exists to refuse;
  * the queue step is fail-soft: it cannot take the reconcile down with it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.live import queue_cancel as qc
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.risk.manager import RiskManager

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
RULE = qc.FROZEN_RULE


def _obs(ahead, age_s=0, status=qc.TELEMETRY_OBSERVED, detail=None):
    return qc.QueueObservation(status=status, contracts_ahead=ahead, queue_position=ahead,
                               observed_at=NOW - timedelta(seconds=age_s), detail=detail)


def _decide(age_min, obs, timeout=14_400):
    return qc.decide(RULE, order_age_seconds=age_min * 60, timeout_seconds=timeout,
                     observation=obs, now=NOW)


# ---------------------------------------------------------------- the frozen rule


def test_the_frozen_rule_is_the_baseline_derived_one():
    """The numbers the pre-registration carries. Changing any of them is a new Version."""
    assert RULE.rule_version == "qac-v1-2026-09-07"
    assert RULE.min_age_seconds == 90 * 60
    assert RULE.max_fill_probability_pct == 10.0
    assert RULE.min_cell_n == 20
    assert RULE.max_observation_age_seconds == 600


def test_settings_defaults_equal_the_frozen_rule(settings):
    """`rule_from_settings` is what the worker runs; its defaults ARE the pre-registration."""
    assert qc.rule_from_settings(settings) == RULE
    assert settings.live_queue_cancel_mode == "off", "disabled by default"


def test_deep_and_old_cancels_but_deep_and_young_does_not():
    deep = _obs(9_000)
    assert _decide(60, deep).code == qc.TOO_YOUNG
    d = _decide(95, deep)
    assert d.code == qc.QUEUE_CANCEL and d.depth_bucket == "b5k+" and d.age_checkpoint_min == 90
    assert d.estimated_fill_probability_pct == 10.0 and d.cell_n == 40


def test_front_of_queue_cancels_only_after_three_hours():
    front = _obs(0)
    assert _decide(120, front).code == qc.KEEP            # 16.1% > 10%
    assert _decide(185, front).code == qc.QUEUE_CANCEL    # 4.9%, n=41


def test_a_thin_cell_is_insufficient_evidence_not_a_cancel():
    """b1_10 at 120 min is 0/7 — a 0% that the rule must not act on."""
    d = _decide(125, _obs(5))
    assert d.code == qc.INSUFFICIENT_EVIDENCE and d.cell_n == 7
    assert d.estimated_fill_probability_pct == 0.0, "the number is still recorded"


def test_past_the_timeout_belongs_to_the_normal_timeout_path():
    d = _decide(250, _obs(9_000))
    assert d.code == qc.NORMAL_TIMEOUT and d.remaining_timeout_seconds < 0


def test_missing_stale_malformed_and_errored_telemetry_all_keep():
    """The invariant the whole design rests on. None of these can reach QUEUE_CANCEL."""
    cases = {
        "missing": None,
        "stale": _obs(9_000, age_s=RULE.max_observation_age_seconds + 1),
        "future_stamped": qc.QueueObservation(status=qc.TELEMETRY_OBSERVED, contracts_ahead=9_000,
                                              observed_at=NOW + timedelta(seconds=30)),
        "no_timestamp": qc.QueueObservation(status=qc.TELEMETRY_OBSERVED, contracts_ahead=9_000),
        "malformed": _obs(None, status=qc.TELEMETRY_MALFORMED, detail="payload unreadable"),
        "error": _obs(None, status=qc.TELEMETRY_ERROR, detail="HTTP 500"),
        "observed_without_figure": _obs(None),
    }
    for name, obs in cases.items():
        d = _decide(120, obs)
        assert d.code == qc.TELEMETRY_KEEP, name
        assert not d.cancels, name
    assert _decide(120, cases["stale"]).telemetry_status == qc.TELEMETRY_STALE


def test_negative_or_junk_depth_never_cancels():
    assert _decide(120, _obs(-1)).code == qc.INSUFFICIENT_EVIDENCE
    assert _decide(120, _obs("lots")).code == qc.INSUFFICIENT_EVIDENCE


def test_buckets_and_checkpoints_are_read_conservatively():
    assert qc.depth_bucket(0) == "b0" and qc.depth_bucket(10) == "b1_10"
    assert qc.depth_bucket(100) == "b11_100" and qc.depth_bucket(1000) == "b101_1k"
    assert qc.depth_bucket(5000) == "b1k_5k" and qc.depth_bucket(5001) == "b5k+"
    # a 100-minute-old order reads the 90-minute row, never the more optimistic 120 one
    assert qc.age_checkpoint(100 * 60) == 90 and qc.age_checkpoint(14 * 60) is None


def test_every_survival_cell_is_a_count_pair_over_the_declared_grid():
    for (cp, bucket), (n, filled) in qc.BASELINE_SURVIVAL_2026_09_07.items():
        assert cp in qc.AGE_CHECKPOINTS_MIN and bucket in {b for b, _ in qc.DEPTH_BUCKETS}
        assert 0 <= filled <= n


# ---------------------------------------------------------------- executor integration


class _Client:
    def __init__(self, batch=None, batch_exc=None, single_exc=None, cancel_exc=None):
        self._batch = batch if batch is not None else {"queue_positions": []}
        self._batch_exc = batch_exc
        self._single_exc = single_exc
        self.cancel_exc = cancel_exc
        self.canceled: list[str] = []
        self.single_calls = 0
        self.cancel_shards: list[int | None] = []
        self.index_calls: list[str] = []
        self.index = 3
        self.index_exc = None

    def get_queue_positions(self, **kw):
        if self._batch_exc is not None:
            raise self._batch_exc
        return self._batch

    def get_order_queue_position(self, order_id):
        self.single_calls += 1
        if self._single_exc is not None:
            raise self._single_exc
        return {"order_id": order_id, "queue_position_fp": "9000.00"}

    def cancel_events_order(self, order_id, *, exchange_index=None):
        self.cancel_shards.append(exchange_index)
        if self.cancel_exc is not None:
            raise self.cancel_exc
        self.canceled.append(order_id)
        return {}

    def get_market_exchange_index(self, ticker):
        self.index_calls.append(ticker)
        if self.index_exc is not None:
            raise self.index_exc
        return self.index


def _live(settings, mode="shadow", **over):
    settings.bot_mode = "live"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = "Dmmsell10"
    settings.live_queue_cancel_mode = mode
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()
    return settings


def _resting(session, *, strategy="Dmmsell10", koid="K-1", price=93, age_min=100):
    row = m.LiveOrder(
        market_ticker=f"KXT-{koid}", event_ticker="KXT", strategy=strategy,
        side="no", action="buy", limit_price=price, quantity=1, status="resting",
        kalshi_order_id=koid, client_order_id=f"c-{koid}",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=age_min),
    )
    session.add(row)
    session.flush()
    return row


def _deep_batch(*koids):
    return {"queue_positions": [{"order_id": k, "queue_position_fp": "9000.00"} for k in koids]}


def _decisions(session):
    return list(session.scalars(select(m.LiveOrderQueueDecision)
                                .order_by(m.LiveOrderQueueDecision.id)).all())


def test_off_by_default_writes_nothing(settings):
    _live(settings, mode="off")
    ex = LiveExecutor(_Client(_deep_batch("K-1")), settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session)
        ex.sample_queue_positions(session)
        assert ex.evaluate_queue_cancellations(session) == 0
        assert _decisions(session) == []


def test_shadow_records_the_full_audit_row_and_sends_nothing(settings):
    _live(settings, mode="shadow")
    client = _Client(_deep_batch("K-1"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        row = _resting(session, age_min=100)
        ex.sample_queue_positions(session)
        assert ex.evaluate_queue_cancellations(session) == 1
        (d,) = _decisions(session)
        assert d.decision == qc.QUEUE_CANCEL and d.acted is False and d.mode == "shadow"
        assert d.telemetry_status == qc.TELEMETRY_OBSERVED and d.contracts_ahead == 9000
        assert d.kalshi_order_id == "K-1" and d.live_order_id == row.id
        assert d.strategy == "Dmmsell10" and d.market_ticker == "KXT-K-1"
        assert d.limit_price == 93 and d.quantity == 1 and d.side == "no"
        assert d.rule_version == RULE.rule_version
        assert d.rule_inputs_json["max_fill_probability_pct"] == 10.0
        assert d.rule_inputs_json["depth_bucket"] == "b5k+"
        assert d.estimated_fill_probability_pct == 10.0
        assert 99 * 60 <= d.order_age_seconds <= 101 * 60
        assert d.remaining_timeout_seconds == 14_400 - d.order_age_seconds
        assert d.queue_observed_at is not None and d.filled_quantity_before == 0
        assert d.cap_bound is False and d.book_open_count == 1 and d.book_open_cap > 1
        assert "P(fill later)=10.0%" in d.reason
        # the order itself is untouched
        assert row.status == "resting" and row.limit_price == 93 and row.quantity == 1
    assert client.canceled == []
    assert ex.summary.queue_decisions == 1 and ex.summary.queue_would_cancel == 1
    assert ex.summary.queue_canceled == 0


def test_shadow_records_keeps_too_so_coverage_is_computable(settings):
    _live(settings, mode="shadow")
    ex = LiveExecutor(_Client(_deep_batch("K-1", "K-2")), settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, koid="K-1", age_min=10)      # too young
        _resting(session, koid="K-2", age_min=100)     # would cancel
        ex.sample_queue_positions(session)
        assert ex.evaluate_queue_cancellations(session) == 2
        by = {d.kalshi_order_id: d for d in _decisions(session)}
        assert by["K-1"].decision == qc.TOO_YOUNG and by["K-2"].decision == qc.QUEUE_CANCEL


def test_a_dead_queue_api_keeps_every_order_and_names_the_failure(settings):
    """The invariant in production form: batch AND per-order fail, so the observation is
    an ERROR and the rule keeps — with the error text on the row."""
    _live(settings, mode="live", live_queue_cancel_tags="Dmmsell10")
    client = _Client(batch_exc=RuntimeError("503"), single_exc=RuntimeError("503 again"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, age_min=200)
        ex.sample_queue_positions(session)
        ex.evaluate_queue_cancellations(session)
        (d,) = _decisions(session)
        assert d.decision == qc.TELEMETRY_KEEP and d.telemetry_status == qc.TELEMETRY_ERROR
        assert "503 again" in d.reason and d.acted is False
    assert client.canceled == []


def test_an_unreadable_payload_keeps_as_malformed(settings):
    _live(settings, mode="live", live_queue_cancel_tags="Dmmsell10")
    client = _Client({"queue_positions": [{"order_id": "K-1", "rank_renamed": 3}]},
                     single_exc=RuntimeError("no fallback"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, age_min=200)
        ex.sample_queue_positions(session)
        ex.evaluate_queue_cancellations(session)
        (d,) = _decisions(session)
        assert d.decision == qc.TELEMETRY_KEEP and d.telemetry_status == qc.TELEMETRY_ERROR
    assert client.canceled == []


def test_sampling_off_means_missing_telemetry_means_keep(settings):
    _live(settings, mode="live", live_queue_cancel_tags="Dmmsell10",
          live_queue_position_sampling=False)
    client = _Client(_deep_batch("K-1"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, age_min=200)
        ex.sample_queue_positions(session)
        ex.evaluate_queue_cancellations(session)
        (d,) = _decisions(session)
        assert d.decision == qc.TELEMETRY_KEEP and d.telemetry_status == qc.TELEMETRY_MISSING
    assert client.canceled == []


def test_live_mode_refuses_a_tag_with_no_registered_live_treatment_arm(settings):
    """NEW_ONLY's rule applied to the cancel side: no active LIVE deployment arm of the
    queue experiment carries this tag, so the cancel is refused and recorded as such — even
    though the operator allowlisted the tag."""
    _live(settings, mode="live", live_queue_cancel_tags="Dmmsell10")
    client = _Client(_deep_batch("K-1"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        row = _resting(session, age_min=100)
        ex.sample_queue_positions(session)
        ex.evaluate_queue_cancellations(session)
        (d,) = _decisions(session)
        assert d.decision == qc.REFUSED_UNREGISTERED and d.acted is False
        assert d.experiment_deployment_arm_id is None
        assert row.status == "resting"
    assert client.canceled == []
    assert ex.summary.queue_cancel_refused == 1 and ex.summary.queue_canceled == 0


def test_live_mode_cancels_only_a_registered_allowlisted_tag(settings, monkeypatch):
    """With a registered live treatment arm for the tag, live mode sends the cancel, marks
    the row canceled with the rule version as the reason, and leaves price/size alone.
    A second resting order on an unregistered tag in the same cycle is refused."""
    _live(settings, mode="live", live_queue_cancel_tags="Dmmsell10,Xmmsell10")
    monkeypatch.setattr(
        LiveExecutor, "_queue_cancel_lineage",
        lambda self, session: {"live": {"deployment_key": "qac-live-1", "arm_link_id": 77,
                                        "by_tag": {"Dmmsell10": 77}}},
    )
    client = _Client(_deep_batch("K-1", "K-2"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        a = _resting(session, koid="K-1", age_min=100)
        b = _resting(session, koid="K-2", strategy="Xmmsell10", age_min=100)
        ex.sample_queue_positions(session)
        ex.evaluate_queue_cancellations(session)
        by = {d.kalshi_order_id: d for d in _decisions(session)}
        assert by["K-1"].decision == qc.QUEUE_CANCEL and by["K-1"].acted is True
        assert by["K-1"].cancel_result == "accepted"
        assert by["K-1"].experiment_deployment_arm_id == 77
        assert a.status == "canceled" and a.cancel_reason == f"queue_cancel:{RULE.rule_version}"
        assert a.limit_price == 93 and a.quantity == 1, "price and size are never touched"
        assert by["K-2"].decision == qc.REFUSED_UNREGISTERED and b.status == "resting"
    assert client.canceled == ["K-1"]
    assert ex.summary.queue_canceled == 1 and ex.summary.queue_cancel_refused == 1


def test_a_refused_exchange_cancel_is_recorded_not_raised(settings, monkeypatch):
    _live(settings, mode="live", live_queue_cancel_tags="Dmmsell10")
    monkeypatch.setattr(
        LiveExecutor, "_queue_cancel_lineage",
        lambda self, session: {"live": {"arm_link_id": 1, "by_tag": {"Dmmsell10": 1}}},
    )
    client = _Client(_deep_batch("K-1"), cancel_exc=RuntimeError("404 order gone"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        row = _resting(session, age_min=100)
        ex.sample_queue_positions(session)
        ex.evaluate_queue_cancellations(session)
        (d,) = _decisions(session)
        assert d.decision == qc.EXCHANGE_ERROR and d.acted is False
        assert "404 order gone" in d.cancel_result
        assert row.status == "resting", "our state is not moved on a cancel Kalshi refused"


def test_the_per_cycle_bound_defers_the_rest(settings, monkeypatch):
    _live(settings, mode="live", live_queue_cancel_tags="Dmmsell10",
          live_queue_cancel_max_per_cycle=1)
    monkeypatch.setattr(
        LiveExecutor, "_queue_cancel_lineage",
        lambda self, session: {"live": {"arm_link_id": 1, "by_tag": {"Dmmsell10": 1}}},
    )
    client = _Client(_deep_batch("K-1", "K-2"))
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, koid="K-1", age_min=100)
        _resting(session, koid="K-2", age_min=100)
        ex.sample_queue_positions(session)
        ex.evaluate_queue_cancellations(session)
        codes = sorted(d.decision for d in _decisions(session))
        assert codes == [qc.DEFERRED_CYCLE_CAP, qc.QUEUE_CANCEL]
    assert len(client.canceled) == 1


def test_the_four_hour_timeout_still_applies_in_reconcile_before_the_rule(settings):
    """Reconcile's own timeout loop cancels a 5h-old order with reason 'timeout' and the
    queue step then sees nothing resting — the fallback is intact in every mode."""
    _live(settings, mode="shadow")
    client = _Client(_deep_batch("K-1"))
    client.get_orders = lambda: {"orders": []}
    client.get_fills = lambda: {"fills": []}
    client.get_positions = lambda: {"market_positions": []}
    client.get_settlements = lambda: {"settlements": []}
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        row = _resting(session, age_min=5 * 60)
        ex.reconcile(session)
        assert row.status == "canceled" and row.cancel_reason == "timeout"
        assert _decisions(session) == [], "nothing was resting when the rule ran"
    assert client.canceled == ["K-1"] and ex.summary.timed_out_canceled == 1


def test_the_timeout_cancel_is_routed_to_the_markets_exchange_shard(settings):
    """The fix for XOS-000028. Kalshi's writes are shard-scoped while its reads aggregate, so an
    unrouted cancel for an order resting on a non-default shard is answered 404 not_found for as
    long as the order lives — measured in production as one order sitting 3.5 days past a 4-hour
    timeout while the queue endpoint reported it alive at rank 1.

    The shard must come from the market object, which Kalshi documents as the authoritative
    source, and NOT from a hand-written series-prefix map: Kalshi moved whole categories between
    shards as recently as 2026-09-10, which would silently rot such a map."""
    _live(settings, mode="shadow")
    client = _Client(_deep_batch("K-1"))
    client.index = 3
    client.get_orders = lambda: {"orders": []}
    client.get_fills = lambda: {"fills": []}
    client.get_positions = lambda: {"market_positions": []}
    client.get_settlements = lambda: {"settlements": []}
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        row = _resting(session, age_min=5 * 60)
        ex.reconcile(session)
        assert row.status == "canceled" and row.cancel_reason == "timeout"
    assert client.canceled == ["K-1"]
    assert client.cancel_shards == [3], "the cancel carried the market's shard, not the default"
    assert client.index_calls == [row.market_ticker]


def test_shard_zero_is_sent_and_is_not_confused_with_unknown(settings):
    """0 is a real shard, and `if shard:` would drop it. Falsy-vs-None is the whole bug class."""
    _live(settings, mode="shadow")
    client = _Client(_deep_batch("K-1"))
    client.index = 0
    client.get_orders = lambda: {"orders": []}
    client.get_fills = lambda: {"fills": []}
    client.get_positions = lambda: {"market_positions": []}
    client.get_settlements = lambda: {"settlements": []}
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, age_min=5 * 60)
        ex.reconcile(session)
    assert client.cancel_shards == [0], "an explicit shard 0 is sent, never silently dropped"


def test_a_failing_shard_lookup_cannot_make_the_cancel_worse(settings, caplog):
    """FAIL-SOFT is the safety property. If the lookup raises, the cancel still goes out — just
    unrouted, exactly as it did before this routing existed. A live safeguard must never end up
    weaker because a helper read failed, and the lookup must not be cached as 'unknown' on a
    transient failure or one bad read would pin the market unroutable for the whole process."""
    _live(settings, mode="shadow")
    client = _Client(_deep_batch("K-1"))
    client.index_exc = RuntimeError("markets read blew up")
    client.get_orders = lambda: {"orders": []}
    client.get_fills = lambda: {"fills": []}
    client.get_positions = lambda: {"market_positions": []}
    client.get_settlements = lambda: {"settlements": []}
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        row = _resting(session, age_min=5 * 60)
        with caplog.at_level("WARNING"):
            ex.reconcile(session)
        assert row.status == "canceled" and row.cancel_reason == "timeout"
    assert client.canceled == ["K-1"] and client.cancel_shards == [None]
    assert ex._exchange_index == {}, "a transient lookup failure is not cached"
    assert any("exchange index lookup failed" in r.getMessage() and "RuntimeError" in r.getMessage()
               for r in caplog.records), "and it says why, in the message"


def test_the_shard_is_looked_up_once_per_market_not_once_per_cycle(settings):
    """A permanently-failing cancel is retried every cycle forever. Without caching that is also
    a market read every ~2.5 minutes forever, against an account with a rate-limit budget."""
    _live(settings, mode="shadow")
    client = _Client(_deep_batch("K-1"),
                     cancel_exc=RuntimeError('404 {"code":"not_found"}'))
    client.get_orders = lambda: {"orders": []}
    client.get_fills = lambda: {"fills": []}
    client.get_positions = lambda: {"market_positions": []}
    client.get_settlements = lambda: {"settlements": []}
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, age_min=5 * 60)
        ex.reconcile(session)
        ex.reconcile(session)
        ex.reconcile(session)
    assert len(client.cancel_shards) == 3, "still attempted every cycle"
    assert len(client.index_calls) == 1, "but the shard was resolved once"


def test_a_failing_timeout_cancel_names_its_error_in_the_log_MESSAGE(settings, caplog):
    """The property that was missing when a live order rested 11.8 h past its 4h timeout.

    Railway's log endpoint returns only a log line's MESSAGE and drops structured fields, so an
    error put in `extra` is invisible in production. On 2026-09-07 that made a permanently
    failing cancel undiagnosable: `WARN live cancel failed` fired every cycle for eleven hours
    and named neither the exception class nor Kalshi's error body, so nothing could tell a 404
    from a 400 from a transport error without a code change and a redeploy
    (docs/handoffs/HANDOFF-timeout-cancel-not-clearing.md).

    The drain path in this same file already learned this lesson; the timeout path had not.
    """
    _live(settings, mode="shadow")
    client = _Client(_deep_batch("K-1"),
                     cancel_exc=RuntimeError('Kalshi API error 404: {"code":"order_not_found"}'))
    client.get_orders = lambda: {"orders": []}
    client.get_fills = lambda: {"fills": []}
    client.get_positions = lambda: {"market_positions": []}
    client.get_settlements = lambda: {"settlements": []}
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        row = _resting(session, age_min=5 * 60)
        with caplog.at_level("WARNING"):
            ex.reconcile(session)

        msgs = [r.getMessage() for r in caplog.records if "live cancel failed" in r.getMessage()]
        assert len(msgs) == 1, "the timeout cancel failure is logged exactly once per cycle"
        (msg,) = msgs
        # The three things a diagnosis needs, all in the MESSAGE and not in `extra`.
        assert "RuntimeError" in msg, "the exception CLASS discriminates transport from API error"
        assert "order_not_found" in msg, "Kalshi's own error body is what names the cause"
        assert "K-1" in msg and row.market_ticker in msg, "which order, on which market"

        # Documented, NOT endorsed: a raising cancel shares its `try` with the status write, so
        # the row stays `resting` and the next cycle retries it forever. Bounding that retry is a
        # behavioural change to a live safeguard and is deliberately not made alongside this
        # logging fix. A future bound must change THIS assertion on purpose.
        assert row.status == "resting" and row.cancel_reason is None
        assert ex.summary.timed_out_canceled == 0, "a failed cancel is never counted a success"


def test_the_queue_step_cannot_take_the_cycle_down(settings, monkeypatch):
    """A failure inside the step is logged and swallowed; the rest of reconcile proceeds."""
    _live(settings, mode="shadow")
    ex = LiveExecutor(_Client(_deep_batch("K-1")), settings, RiskManager(settings))
    monkeypatch.setattr(repo, "insert_queue_decision",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db hiccup")))
    with db.session_scope() as session:
        row = _resting(session, age_min=100)
        ex.sample_queue_positions(session)
        assert ex.evaluate_queue_cancellations(session) == 0
        assert row.status == "resting"


def test_partial_fills_before_the_decision_are_recorded(settings):
    _live(settings, mode="shadow")
    ex = LiveExecutor(_Client(_deep_batch("K-1")), settings, RiskManager(settings))
    with db.session_scope() as session:
        _resting(session, age_min=100)
        repo.insert_fill(session, kalshi_fill_id="F-1", kalshi_order_id="K-1",
                         ticker="KXT-K-1", filled_at=None, side="no", action="buy",
                         price=93, quantity=1, fee=0.0, raw_fill_json=None)
        ex.sample_queue_positions(session)
        ex.evaluate_queue_cancellations(session)
        (d,) = _decisions(session)
        assert d.filled_quantity_before == 1


# ---------------------------------------------------------------- baseline script helpers


def test_the_replay_helpers_name_exactly_the_cells_the_rule_opens():
    import sys
    sys.path.insert(0, "scripts")
    import mmsell_queue_cancel_baseline as bl

    cells = bl.qualifying_cells()
    assert cells == {(90, "b5k+"), (120, "b5k+"), (150, "b5k+"), (180, "b0"), (180, "b5k+"),
                     (210, "b0"), (210, "b5k+")}
    # ...and the pure decision function agrees with the replay's cell set at every checkpoint
    for cp in qc.AGE_CHECKPOINTS_MIN:
        for bucket, upper in qc.DEPTH_BUCKETS:
            ahead = 0 if upper == 0 else (upper if upper is not None else 9_999)
            d = qc.decide(RULE, order_age_seconds=cp * 60 + 1, timeout_seconds=14_400,
                          observation=_obs(ahead), now=NOW)
            assert d.cancels == ((cp, bucket) in cells), (cp, bucket, d)
    sql = bl.qualifying_cells_sql(cells, "cp", "b")
    assert "(cp = 90 AND b = 'b5k+')" in sql and "b1k_5k" not in sql
    assert bl.bucket_case_sql("x").startswith("CASE WHEN x IS NULL THEN 'no_tel'")
    assert "WHEN a >= 210 THEN 210" in bl.checkpoint_case_sql("a")
