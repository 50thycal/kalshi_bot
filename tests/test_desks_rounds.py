"""DEC-024: a new round in the SAME database, opened only when the old one is flat."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_desks_store import NOW, decision, report, reserve, start

from kalshi_bot.desks.contracts import DeskError, Settlement
from kalshi_bot.desks.store import DeskStore

D = Decimal
LATER = NOW + timedelta(days=2)


@pytest.fixture
def path(tmp_path):
    return f"sqlite:///{tmp_path / 'rounds.db'}"


def settle_loss(store, n=1):
    row = reserve(store, n)
    report(store, row)  # 2 filled at 0.80 + 0.04 fees
    store.settlement(row["decision_id"], Settlement(
        ticker=f"MARKET-{n}", yes_payout=D(0), settled_at=NOW, source="source"))
    return row


def test_round_two_reuses_the_database_and_carries_each_books_cash(path):
    store = start(DeskStore(path))
    settle_loss(store)
    store.publish("chatgpt", "lesson", {"lesson": "Take two time-spaced reads."}, NOW)
    assert D(store.snapshot(NOW)["desks"][0]["cash"]) == D("29.16")

    round2 = DeskStore(path)
    info = round2.initialize("round-2", LATER)
    assert info["round_id"] == "round-2" and info["started_at"] is None
    assert info["rules"]["bankroll_mode"] == "carry" and info["rules"]["previous_round"] == "round-1"
    snap = round2.snapshot(LATER)
    books = {row["desk_id"]: row for row in snap["desks"]}
    assert D(books["chatgpt"]["initial_bankroll"]) == D(books["chatgpt"]["cash"]) == D("29.16")
    assert D(books["claude"]["initial_bankroll"]) == D("30")
    assert not books["chatgpt"]["ready"] and snap["decisions"] == []
    assert snap["prior_rounds"] == ["round-1"]
    lesson = next(p for p in snap["publications"] if p["kind"] == "lesson")
    assert lesson["round_id"] == "round-1"  # memory carries over in the same database
    round2.publish("chatgpt", "lesson", {"lesson": "new"}, LATER)
    assert {p["round_id"] for p in round2.snapshot(LATER)["publications"]} == {"round-1", "round-2"}
    with pytest.raises(DeskError, match="round_not_started"):
        round2.reserve(decision(9, at=LATER).model_copy(update={"round_id": "round-2"}), 1, D("0.5"), LATER)

def test_winnings_are_not_carried_above_thirty_dollars_and_fresh_mode_resets(path, tmp_path):
    store = start(DeskStore(path))
    row = reserve(store)
    report(store, row)
    store.settlement(row["decision_id"], Settlement(ticker="MARKET-1", yes_payout=D(1),
                                                    settled_at=NOW, source="source"))
    assert D(store.snapshot(NOW)["desks"][0]["cash"]) == D("31.16")
    carried = DeskStore(path)
    carried.initialize("round-2", LATER)
    assert D(carried.snapshot(LATER)["desks"][0]["initial_bankroll"]) == D("30")  # no size increase

    other = f"sqlite:///{tmp_path / 'fresh.db'}"
    first = start(DeskStore(other))
    settle_loss(first)
    fresh = DeskStore(other)
    fresh.initialize("round-2", LATER, bankroll_mode="fresh")
    assert D(fresh.snapshot(LATER)["desks"][0]["initial_bankroll"]) == D("30")


@pytest.mark.parametrize("case", ["reserved", "filled_unsettled", "unknown"])
def test_new_round_refused_while_anything_of_the_old_round_is_open(path, case):
    store = start(DeskStore(path))
    row = reserve(store)
    if case == "filled_unsettled":
        report(store, row)
    elif case == "unknown":
        store.claim_submission(row["decision_id"], NOW)
        report(store, row, quantity="0", cost="0", fees="0", status="unknown")
    with pytest.raises(DeskError, match="prior_round_not_closed"):
        DeskStore(path).initialize("round-2", LATER)
    reopened = DeskStore(path)
    reopened.initialize("round-1", LATER)  # nothing was created; round 1 is still current
    assert reopened.snapshot(LATER)["round_id"] == "round-1"
    assert reopened.snapshot(LATER)["prior_rounds"] == []


def test_operator_pause_survives_the_round_boundary(path):
    store = start(DeskStore(path))
    store.pause("claude", "operator review")
    round2 = DeskStore(path)
    round2.initialize("round-2", LATER)
    claude = next(r for r in round2.snapshot(LATER)["desks"] if r["desk_id"] == "claude")
    assert claude["paused"] and "operator review" in claude["pause_reason"]


def test_current_round_is_explicit_once_two_rounds_exist(path):
    start(DeskStore(path))
    DeskStore(path).initialize("round-2", LATER)
    unselected = DeskStore(path)
    with pytest.raises(DeskError, match="round_not_initialized"):
        unselected.snapshot(LATER)  # never guesses between rounds
    unselected.use_round("round-1")
    assert unselected.snapshot(LATER)["round_id"] == "round-1"
    with pytest.raises(DeskError, match="round_not_initialized"):
        unselected.use_round("round-9")


def test_preflight_requires_the_carried_bankroll_not_thirty(path):
    from test_desks_service import FakeExchange, FakeNotifier, FakeSupervisor, settings

    from kalshi_bot.desks.service import DeskService
    store = start(DeskStore(path))
    settle_loss(store)
    round2 = DeskStore(path)
    round2.initialize("round-2", LATER)
    exchange = FakeExchange(balance="29.16")
    executors = {"chatgpt": SimpleNamespace(exchange=exchange, isolation_verified=False),
                 "claude": SimpleNamespace(exchange=FakeExchange(balance="30"), isolation_verified=False)}
    service = DeskService(settings(round_id="round-2"), round2, FakeSupervisor(), executors,
                          notifier=FakeNotifier())
    blockers = service.check_launch(LATER, refresh=True)["blockers"]
    assert not any("funding_or_isolation" in b for b in blockers)
    exchange.balance = "29.15"
    blockers = service.check_launch(LATER, refresh=True)["blockers"]
    assert "chatgpt_funding_or_isolation_not_verified" in blockers


def test_claim_context_carries_own_prior_round_record(path):
    from kalshi_bot.desks.supervisor import Supervisor
    store = start(DeskStore(path))
    store.publish("claude", "lesson", {"lesson": "Pair ensembles with the exact-point run."}, NOW)
    store.publish("chatgpt", "lesson", {"lesson": "peer lesson"}, NOW)
    round2 = DeskStore(path)
    round2.initialize("round-2", LATER)
    sup = Supervisor(round2, market_reader=lambda now: {"markets": [], "sources": []}, research_mode="session")
    record = sup.claim_external("claude", "claude-app", LATER)["context"]["prior_round_record"]
    assert "exact-point run" in record["own"][0]["payload_excerpt"]
    assert record["own"][0]["round_id"] == "round-1"
    assert "peer lesson" in record["peer"][0]["payload_excerpt"]
