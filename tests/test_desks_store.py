"""Budget, ownership, evidence, and reconciliation invariants for isolated desks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from kalshi_bot.desks.contracts import Decision, DeskError, Evidence, OrderReport, Settlement
from kalshi_bot.desks.store import DeskStore

D = Decimal
NOW = datetime(2026, 9, 20, 15, tzinfo=timezone.utc)


def decision(n=1, *, desk="chatgpt", at=NOW, event=None):
    return Decision(
        decision_id=f"decision_{desk}_{n}",
        desk_id=desk,
        round_id="round-1",
        ticker=f"MARKET-{n}",
        event_id=event or f"EVENT-{n}",
        side="yes",
        observed_price=D("0.4"),
        quote_at=at,
        max_price=D("0.4"),
        max_spend=D(1),
        probability=D("0.8"),
        probability_low=D("0.7"),
        probability_high=D("0.9"),
        expected_net_profit=D("0.3"),
        settlement_source="https://example.com/settle",
        settlement_rule="The exact settlement rule",
        rules_sha256="a" * 64,
        thesis="An evidence-backed hypothesis",
        counterargument="A substantial alternative",
        invalidation="New observation differs",
        edge_class="information",
        evidence=[
            Evidence(
                url="https://example.com/data",
                retrieved_at=at,
                excerpt="The evidence",
                sha256="b" * 64,
            )
        ],
        created_at=at,
        expires_at=at + timedelta(minutes=10),
        author_model="model-test",
    )


def start(store):
    store.initialize("round-1", NOW)
    store.ready("chatgpt", NOW)
    store.ready("claude", NOW)
    store.start_round("round-1", NOW)
    return store


@pytest.fixture
def store(tmp_path):
    return start(DeskStore(f"sqlite:///{tmp_path / 'desks.db'}"))


def reserve(store, n=1, *, at=NOW, desk="chatgpt", event=None):
    return store.reserve(decision(n, desk=desk, at=at, event=event), 2, D("0.84"), at)


def report(store, row, *, quantity="2", cost="0.80", fees="0.04", status="terminal", at=NOW):
    return store.record_order(
        row["decision_id"],
        OrderReport(
            client_order_id=row["client_order_id"],
            order_id=f"order-{row['decision_id']}",
            status=status,
            filled_quantity=D(quantity),
            fill_cost=D(cost),
            fees=D(fees),
            observed_at=at,
        ),
        at,
    )


def test_round_shared_start_and_only_isolated_tables(tmp_path):
    s = DeskStore(f"sqlite:///{tmp_path / 'setup.db'}")
    s.initialize("round-1", NOW)
    s.ready("chatgpt", NOW)
    with pytest.raises(DeskError, match="both_desks"):
        s.start_round("round-1", NOW)
    with pytest.raises(DeskError, match="round_not_started"):
        reserve(s)
    s.ready("claude", NOW)
    assert s.start_round("round-1", NOW)["started_at"] == NOW.isoformat()
    assert s.start_round("round-1", NOW + timedelta(days=1))["started_at"] == NOW.isoformat()
    assert all(t.startswith("desk_") for t in inspect(s.engine).get_table_names())


def test_duplicate_reservation_and_claim_are_exactly_once(store):
    first = reserve(store)
    assert reserve(store)["client_order_id"] == first["client_order_id"]
    assert store.claim_submission(first["decision_id"], NOW)
    assert not store.claim_submission(first["decision_id"], NOW)
    modified = decision().model_copy(update={"thesis": "A different later hypothesis"})
    with pytest.raises(DeskError, match="decision_id_conflict"):
        store.reserve(modified, 2, D("0.84"), NOW)
    assert store.snapshot(NOW)["desks"][0]["attempts_today"] == 1


def test_pending_slots_partial_fills_unfilled_release(store):
    rows = [reserve(store, n) for n in range(3)]
    with pytest.raises(DeskError, match="daily_pick_limit"):
        reserve(store, 3)
    report(store, rows[0], quantity="0", cost="0", fees="0")
    reserve(store, 3)
    report(store, rows[1], quantity="0.5", cost="0.20", fees="0.01012345")
    desk = store.snapshot(NOW)["desks"][0]
    assert desk["filled_today"] == 1
    assert D(desk["cash"]) == D("29.78987655")
    assert D(desk["committed"]) == D("1.89012345")
    with pytest.raises(DeskError, match="daily_pick_limit"):
        reserve(store, 4)


def test_ten_unfilled_attempts_ceiling(store):
    for n in range(10):
        row = reserve(store, n)
        report(store, row, quantity="0", cost="0", fees="0")
    assert store.snapshot(NOW)["desks"][0]["filled_today"] == 0
    with pytest.raises(DeskError, match="daily_attempt_limit"):
        reserve(store, 10)


def test_one_event_per_desk_and_independent_desks(store):
    row = reserve(store, 1, event="SHARED")
    with pytest.raises(DeskError, match="event_already_picked"):
        reserve(store, 2, event="SHARED")
    reserve(store, 2, desk="claude", event="SHARED")
    report(store, row, quantity="0", cost="0", fees="0")
    second = reserve(store, 3, event="SHARED")
    report(store, second)
    store.settlement(
        second["decision_id"],
        Settlement(ticker="MARKET-3", yes_payout=D(1), settled_at=NOW, source="source"),
    )
    with pytest.raises(DeskError, match="event_already_picked"):
        reserve(store, 4, event="SHARED")


def test_unknown_keeps_reservation_and_does_not_enable_resubmit(store):
    row = reserve(store)
    assert store.claim_submission(row["decision_id"], NOW)
    report(store, row, quantity="0", cost="0", fees="0", status="unknown")
    desk = store.snapshot(NOW)["desks"][0]
    assert desk["paused"] and D(desk["committed"]) == D("0.84")
    assert not store.claim_submission(row["decision_id"], NOW)
    with pytest.raises(DeskError, match="unreconciled_order"):
        store.resume("chatgpt")
    report(store, row, quantity="0", cost="0", fees="0")
    store.resume("chatgpt")
    assert not store.snapshot(NOW)["desks"][0]["paused"]


def test_reconciliation_idempotent_settlement_and_calibration(store):
    row = reserve(store)
    report(store, row)
    report(store, row)
    settlement = Settlement(ticker="MARKET-1", yes_payout=D(1), settled_at=NOW, source="source")
    result = store.settlement(row["decision_id"], settlement)
    store.settlement(row["decision_id"], settlement)
    assert D(result["pnl"]) == D("1.16")
    assert D(result["brier_score"]) == D("0.04")
    desk = store.snapshot(NOW)["desks"][0]
    assert D(desk["cash"]) == D("31.16")
    assert D(desk["committed"]) == 0
    assert D(desk["pnl"]) == D("1.16")


def test_unfilled_forecast_is_graded_but_void_excluded(store):
    first = reserve(store, 1)
    report(store, first, quantity="0", cost="0", fees="0")
    graded = store.settlement(
        first["decision_id"],
        Settlement(ticker="MARKET-1", yes_payout=D(0), settled_at=NOW, source="source"),
    )
    assert D(graded["brier_score"]) == D("0.64")
    assert D(graded["pnl"]) == 0
    second = reserve(store, 2)
    report(store, second)
    graded = store.settlement(
        second["decision_id"],
        Settlement(ticker="MARKET-2", yes_payout=D("0.5"), settled_at=NOW, source="source"),
    )
    assert "brier_score" not in graded


def test_risk_limit_across_days(store):
    for n in range(11):
        at = NOW + timedelta(days=n)
        report(store, reserve(store, n, at=at), at=at)
    with pytest.raises(DeskError, match="outstanding_risk_limit"):
        reserve(store, 12, at=NOW + timedelta(days=12))


def test_money_breach_persists_pause_and_evidence(store):
    row = reserve(store)
    with pytest.raises(DeskError, match="order_budget_breach"):
        report(store, row, cost="0.80", fees="0.20")
    desk = store.snapshot(NOW)["desks"][0]
    assert desk["paused"] and desk["pause_reason"] == "order_budget_breach"
    assert store.get_decision(row["decision_id"])["status"] == "unknown"


def test_evidence_database_immutable_and_publication_provenance(store):
    row = reserve(store)
    ident = store.publish("chatgpt", "lesson", {"finding": "price matters"}, NOW, "lesson-1")
    assert ident == store.publish(
        "chatgpt", "lesson", {"finding": "price matters"}, NOW, "lesson-1"
    )
    with pytest.raises(DeskError, match="publication_id_conflict"):
        store.publish("claude", "lesson", {"finding": "price matters"}, NOW, "lesson-1")
    for table in ("desk_decisions", "desk_publications", "desk_audit"):
        with pytest.raises(DBAPIError, match="append-only"):
            with store.engine.begin() as conn:
                conn.execute(text(f"DELETE FROM {table}"))
    assert store.get_decision(row["decision_id"])


def test_concurrent_store_instances_cannot_overreserve_or_duplicate_claim(store):
    url = str(store.engine.url)
    stores = [DeskStore(url) for _ in range(8)]

    def attempt(pair):
        n, db = pair
        try:
            return reserve(db, n)
        except DeskError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, enumerate(stores)))
    winners = [r for r in results if r]
    assert len(winners) == 3
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(
            pool.map(lambda db: db.claim_submission(winners[0]["decision_id"], NOW), stores)
        )
    assert sum(claims) == 1


def test_chicago_midnight_pending_slot_and_first_fill_day(store):
    before = datetime(2026, 9, 21, 4, 59, tzinfo=timezone.utc)
    after = before + timedelta(minutes=2)
    first = reserve(store, 1, at=before)
    report(store, first, at=after)
    assert store.snapshot(after)["desks"][0]["filled_today"] == 1
    assert store.snapshot(before)["desks"][0]["filled_today"] == 0
    reserve(store, 2, at=after)
    reserve(store, 3, at=after)
    with pytest.raises(DeskError, match="daily_pick_limit"):
        reserve(store, 4, at=after)


@pytest.mark.skipif(
    not __import__("os").environ.get("XOS_TEST_POSTGRES_URL"),
    reason="XOS_TEST_POSTGRES_URL unset (CI provides a PostgreSQL service)",
)
def test_postgres_concurrent_reservations_and_exactly_once_submission():
    """Exercise real row locks in a random schema; never drop shared tables."""
    import os
    import threading
    import uuid

    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["XOS_TEST_POSTGRES_URL"])
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    schema = "desk_test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    stores = []
    try:
        stores = [DeskStore(scoped_url.render_as_string(hide_password=False)) for _ in range(4)]
        start(stores[0])
        barrier = threading.Barrier(4)

        def attempt(pair):
            n, database = pair
            barrier.wait(timeout=10)
            try:
                return reserve(database, n)
            except DeskError as exc:
                assert exc.code == "daily_pick_limit"
                return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            winners = [row for row in pool.map(attempt, enumerate(stores)) if row]
        assert len(winners) == 3
        barrier = threading.Barrier(4)

        def claim(database):
            barrier.wait(timeout=10)
            return database.claim_submission(winners[0]["decision_id"], NOW)

        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(claim, stores)) == 1
        # Each database-backed desk owns its own three slots.
        assert reserve(stores[0], 1, desk="claude")["desk_id"] == "claude"
    finally:
        for database in stores:
            database.engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_unsubmitted_release_races_claim_atomically(store):
    import threading

    row = reserve(store)
    second = DeskStore(str(store.engine.url))
    barrier = threading.Barrier(2)
    now = NOW + timedelta(seconds=121)

    def claim():
        barrier.wait(timeout=5)
        return store.claim_submission(row["decision_id"], now)

    def release():
        barrier.wait(timeout=5)
        return second.release_unsubmitted(row["decision_id"], now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed, released = pool.submit(claim), pool.submit(release)
        assert claimed.result() + released.result() == 1
    final = store.get_decision(row["decision_id"])
    assert final["status"] in {"submitting", "terminal"}
    assert not store.snapshot(now)["desks"][0]["paused"]
