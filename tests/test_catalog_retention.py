import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from kalshi_bot.catalog import headroom, retention, selection
from kalshi_bot.catalog.headroom import GIB, measure
from kalshi_bot.catalog.store import Store, unpack

OLD = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "catalog.db")


def market(ticker, **changes):
    return {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "series_ticker": ticker.split("-", 1)[0],
        "market_type": "binary",
        "status": "active",
        "rules_primary": "If the official value is above 1, the market resolves to Yes. " * 4,
        "rules_secondary": "The official source is the agency release.",
        "title": "Will it be above 1?",
        "close_time": "2026-10-12T00:00:00Z",
        "created_time": "2026-10-11T00:00:00Z",
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.42",
        "volume_fp": "10.00",
        **changes,
    }


def settle(store, ticker, padding="", rules_primary=None):
    series = ticker.split("-", 1)[0]
    first = {"rules_primary": rules_primary} if rules_primary else {}
    store.upsert("market", ticker, market(ticker, notes=padding, **first), series)
    store.upsert("market", ticker, market(ticker, status="closed", notes=padding), series)
    store.upsert(
        "market", ticker,
        market(ticker, status="finalized", result="no", settlement_ts="2026-10-12T01:00:00Z",
               notes=padding),
        series,
    )


def revisions(store, ticker):
    with store.connect() as db:
        return [
            (row[0], row[1], unpack(row[2]))
            for row in db.execute(
                "SELECT rowid,hash,document FROM revisions WHERE kind='market' AND ticker=? "
                "ORDER BY rowid",
                (ticker,),
            )
        ]


def ready(store):
    while not selection.membership_ready_for(store):
        selection.bootstrap_page(store)


@pytest.fixture
def world(store):
    settle(store, "KXOLD-26OCT11-T1")  # settled, nothing selects it: thinned
    store.upsert("market", "KXOPEN-26OCT11-T1", market("KXOPEN-26OCT11-T1"), "KXOPEN")
    settle(store, "KXKEEP-26OCT11-T1")  # settled but traded: kept
    # Settled, unselected, its rules changed once: notes keep the change; final_only skips it.
    settle(store, "KXEDIT-26OCT11-T1", rules_primary="Original wording, later amended.")
    store.upsert("event", "KXOLD-26OCT11", {"title": "Old event"}, "KXOLD")
    ready(store)
    with store.connect() as db:
        selection.select_in(db, "market", "KXKEEP-26OCT11-T1", "paper_evidence", "paper")
    store.assessment("scope-a", {"as_of": OLD, "edge": 1})  # superseded below, old: deleted
    store.assessment("scope-a", {"as_of": datetime.now(timezone.utc).isoformat(), "edge": 2})
    store.assessment("scope-b", {"as_of": OLD, "edge": 3})  # current: kept however old
    store.assessment("scope-c", {"as_of": datetime.now(timezone.utc).isoformat(), "edge": 4})
    store.assessment("scope-c", {"as_of": datetime.now(timezone.utc).isoformat(), "edge": 5})
    backup = retention.backup_path(store)
    backup.write_bytes(b"x" * 4096)
    store.set_state("storage:compression", {"complete": True})
    return store


def run(store, mode, treatment="notes"):
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(retention, "TREATMENT", treatment)
        return retention.run(store, threading.Event(), mode=mode, pause=0)


def snapshot(store):
    with store.connect() as db:
        return (
            db.execute("SELECT rowid,kind,ticker,hash,document FROM revisions ORDER BY rowid")
            .fetchall(),
            db.execute("SELECT id FROM assessments ORDER BY rowid").fetchall(),
            db.execute("SELECT kind,ticker,document FROM objects ORDER BY kind,ticker").fetchall(),
        )


def statuses(store, ticker):
    return [
        (doc.get("lifecycle") or doc.get("raw") or {}).get("status")
        for _rowid, _key, doc in revisions(store, ticker)
    ]


def test_off_by_default_and_unknown_modes_do_nothing(world):
    before = snapshot(world)
    assert retention.MODE == "off"
    assert retention.run(world, threading.Event()) is None
    assert retention.run(world, threading.Event(), mode="delete_everything") is None
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(retention, "TREATMENT", "everything")
        assert retention.run(world, threading.Event(), mode="execute") is None
    assert snapshot(world) == before


def test_dry_run_changes_nothing_and_measures_both_treatments(world):
    before = snapshot(world)
    progress = run(world, "dry_run")
    assert snapshot(world) == before
    assert retention.backup_path(world).exists()
    assert not world.state(retention.BACKUP_RETIRED)
    tally = progress["revisions"]
    assert tally["markets_eligible"] == 2 and tally["rows_full"] == 6
    assert tally["selected_kept"] == 1 and tally["open_kept"] == 1
    assert tally["final_only_markets"] == 1 and tally["final_only_rows"] == 3
    assert tally["rules_changed_kept"] == 1
    assert tally["sample_rows"] == 3 and tally["sample_bytes_after"] > 0
    assert progress["assessments"]["superseded_rows"] == 1
    assert progress["assessments"]["current_kept"] == 3
    assert progress["assessments"]["recent_kept"] == 1
    assert progress["backup"] == {"exists": True, "bytes": 4096, "compression_complete": True}
    summary = world.state(retention.LAST_REPORT)
    assert {row["series"] for row in summary["top_series"]} == {"KXOLD", "KXEDIT"}
    assert summary["notes_size_ratio"] > 0
    assert summary["disk_bytes_freed_estimate"]["final_only"] > 4096
    # A completed pass is not repeated.
    assert run(world, "dry_run")["completed_at"] == progress["completed_at"]


def test_notes_compact_only_settled_unselected_history_in_order(world):
    old_before = revisions(world, "KXOLD-26OCT11-T1")
    keep_before = revisions(world, "KXKEEP-26OCT11-T1")
    open_before = revisions(world, "KXOPEN-26OCT11-T1")
    current = world.get("market", "KXOLD-26OCT11-T1")
    progress = run(world, "execute")
    assert progress["revisions"]["rows_compacted"] == 6

    old_after = revisions(world, "KXOLD-26OCT11-T1")
    assert statuses(world, "KXOLD-26OCT11-T1") == ["active", "closed", "finalized"]
    for (_rowid, key, doc), (_r, semantic, _full) in zip(old_after, old_before, strict=True):
        assert selection.representation(doc) == selection.COMPACT
        assert key == selection.compact_key(semantic)
        assert doc["semantic_hash"] == semantic
        assert "raw" not in doc
    assert old_after[0][2]["rules"]["rules_primary"] == old_before[0][2]["raw"]["rules_primary"]
    assert [row[2]["rules"] for row in old_after[1:]] == [None, None]
    edits = revisions(world, "KXEDIT-26OCT11-T1")
    assert edits[0][2]["rules"]["rules_primary"] == "Original wording, later amended."
    assert edits[1][2]["rules"]["rules_primary"] != edits[0][2]["rules"]["rules_primary"]
    # The current object keeps the full final document.
    assert world.get("market", "KXOLD-26OCT11-T1")["raw"] == current["raw"]
    assert revisions(world, "KXKEEP-26OCT11-T1") == keep_before
    assert revisions(world, "KXOPEN-26OCT11-T1") == open_before
    with world.connect() as db:
        assert db.execute("SELECT count(*) FROM revisions WHERE kind='event'").fetchone()[0] == 1
        coverage = selection.coverage_in(db, "market", "KXOLD-26OCT11-T1")
        assert "compact_revisions_without_full_payload" in coverage["missing"]
        # A thinned market selected later is recoverable through hydration.
        assert selection._needs_hydration(db, "market", "KXOLD-26OCT11-T1")
        kept = {row[0] for row in db.execute("SELECT scope FROM assessments")}
    assert kept == {"scope-a", "scope-b", "scope-c"}
    assert progress["assessments"]["deleted_rows"] == 1
    assert world.assessments()  # current ones still resolve


def test_final_only_deletes_unchanged_history_and_says_so(world):
    edits = revisions(world, "KXEDIT-26OCT11-T1")
    keep = revisions(world, "KXKEEP-26OCT11-T1")
    current = world.get("market", "KXOLD-26OCT11-T1")
    progress = run(world, "execute", treatment="final_only")
    assert progress["treatment"] == "final_only"
    assert progress["revisions"]["rows_deleted"] == 3
    assert revisions(world, "KXOLD-26OCT11-T1") == []
    assert world.get("market", "KXOLD-26OCT11-T1")["raw"] == current["raw"]
    assert revisions(world, "KXEDIT-26OCT11-T1") == edits  # a rule change is never dropped
    assert revisions(world, "KXKEEP-26OCT11-T1") == keep
    with world.connect() as db:
        coverage = selection.coverage_in(
            db, "market", "KXOLD-26OCT11-T1", world.get("market", "KXOLD-26OCT11-T1")
        )
    assert "revisions_retired_current_object_only" in coverage["missing"]
    assert not coverage["full_payload_history_complete"]


def test_execute_retires_backup_only_after_compression_and_startup_skips_it(world):
    world.set_state("storage:compression", {"complete": False})
    progress = run(world, "execute")
    assert progress["backup"]["kept"] == "compression_incomplete"
    assert retention.backup_path(world).exists()

    world.set_state("storage:compression", {"complete": True})
    world.set_state(retention.state_key("execute"), None)
    progress = run(world, "execute")
    assert progress["backup"]["removed"]
    assert not retention.backup_path(world).exists()
    assert world.state(retention.BACKUP_RETIRED)["bytes"] == 4096
    assert world.backup_before_compression() is None


def test_execute_is_idempotent(world):
    run(world, "execute")
    after = snapshot(world)
    world.set_state(retention.state_key("execute"), None)
    progress = run(world, "execute")
    assert progress["revisions"]["rows_compacted"] == 0
    assert progress["revisions"]["rows_already_compact"] == 6
    assert snapshot(world)[0] == after[0]


def test_execute_repeats_on_its_cadence(world, monkeypatch):
    stopped = threading.Event()
    waits = []

    def wait(seconds):
        if seconds == 0:  # the pause between pages
            return False
        waits.append(seconds)  # the cadence: allow one repeat, then stop
        if len(waits) > 1:
            stopped.set()
        return stopped.is_set()

    monkeypatch.setattr(stopped, "wait", wait)
    monkeypatch.setattr(retention, "TREATMENT", "notes")
    retention.run(world, stopped, mode="execute", pause=0, repeat_hours=0.01)
    assert len(waits) == 2
    # The second pass started fresh and found the first pass's work already done.
    assert world.state(retention.state_key("execute"))["revisions"]["rows_already_compact"] > 0


def test_unready_membership_never_thins(store):
    settle(store, "KXOLD-26OCT11-T1")
    before = revisions(store, "KXOLD-26OCT11-T1")
    progress = retention._fresh("execute")
    progress["phase"] = "revisions"
    assert retention.step(store, progress, "execute") is None
    assert revisions(store, "KXOLD-26OCT11-T1") == before


def test_failed_page_resumes_from_stored_progress(world, monkeypatch):
    real = retention._apply_notes
    calls = []

    def flaky(db, ticker, history, notes, tally):
        calls.append(1)
        if len(calls) == 1:
            tally["rows_compacted"] += 99  # counted, then rolled back
            raise retention.sqlite3.OperationalError("database is locked")
        return real(db, ticker, history, notes, tally)

    monkeypatch.setattr(retention, "_apply_notes", flaky)
    monkeypatch.setattr(retention, "TREATMENT", "notes")
    stopped = threading.Event()
    monkeypatch.setattr(stopped, "wait", lambda seconds: False)
    progress = retention.run(world, stopped, mode="execute", pause=0)
    assert progress["revisions"]["rows_compacted"] == 6
    assert all(
        selection.representation(row[2]) == selection.COMPACT
        for row in revisions(world, "KXOLD-26OCT11-T1")
    )


@pytest.mark.parametrize("treatment", ["notes", "final_only"])
def test_thinning_frees_pages_that_the_bulk_guard_counts(store, monkeypatch, treatment):
    for index in range(150):
        # Incompressible padding, like real payloads after the per-row encoding.
        settle(store, f"KXBIG-26OCT11-T{index}", os.urandom(600).hex())
    ready(store)
    run(store, "execute", treatment=treatment)
    with store.connect() as db:
        free_pages = db.execute("PRAGMA freelist_count").fetchone()[0]
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
    assert free_pages > 0

    usage = SimpleNamespace(total=20 * GIB, free=3 * GIB, used=17 * GIB)
    monkeypatch.setattr(headroom.shutil, "disk_usage", lambda path: usage)
    snapshot = measure(store)
    assert snapshot["reusable_bytes"] == free_pages * page_size
    assert snapshot["bulk_headroom_bytes"] == usage.free + free_pages * page_size


def test_bulk_guard_counts_reusable_pages_but_critical_tier_does_not(store, monkeypatch):
    usage = SimpleNamespace(total=20 * GIB, free=3 * GIB, used=17 * GIB)
    monkeypatch.setattr(headroom.shutil, "disk_usage", lambda path: usage)
    assert measure(store)["bulk_paused"]  # 3 GiB < 4 GiB pause point

    class Reusable:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, sql, *args):
            if sql == "PRAGMA freelist_count":
                return SimpleNamespace(fetchone=lambda: (3 * GIB // 4096,))
            if sql == "PRAGMA page_size":
                return SimpleNamespace(fetchone=lambda: (4096,))
            return self.inner.execute(sql, *args)

    original = store.connect

    @contextmanager
    def connect():
        with original() as db:
            yield Reusable(db)

    monkeypatch.setattr(store, "connect", connect)
    assert not measure(store)["bulk_paused"]  # 3 + 3 GiB clears the 5 GiB resume point
    usage.free = GIB // 2
    assert measure(store)["writes_paused"]  # freelist pages never satisfy the critical floor
