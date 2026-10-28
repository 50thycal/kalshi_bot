"""Owner-approved catalog retention (DEC-038): thin superseded history, never current state.

`CATALOG_RETENTION_MODE` is `off` (default), `dry_run` (measure and report, change nothing) or
`execute`. One pass runs three steps in order, each resumable from persisted state:

1. **backup** — the one-time pre-compression copy (`<db>.before-compression-v1`) is retired
   once compression has completed; startup then stops recreating it.
2. **assessments** — rows no `current_assessments` entry points at, older than the grace
   window, are deleted. Current assessments are never touched; evaluation recomputes them.
3. **revisions** — only markets that are settled (status `finalized`/`settled`) and that no
   active selection member covers. `CATALOG_RETENTION_REVISIONS` picks the treatment:
   - `notes`: every full snapshot becomes the compact rules/lifecycle observation selective
     history writes (`selection.compact_observation`);
   - `final_only`: when every snapshot carries the current object's rules hash (no rule or
     parent change was ever observed), the snapshots are deleted and the current object's full
     final document is the record. A market with a rule change is left untouched.
   The current object, every open or selected market, and every event and series keep their
   full history. A dry run measures both treatments.

Rows are deleted and (for notes) re-inserted in their original order, so whole pages reach
SQLite's freelist: shrinking a row in place frees no page. Freed pages are reused before the
file grows and the bulk storage guard counts them (`headroom.measure`); only a VACUUM shrinks
the file. `CATALOG_RETENTION_REPEAT_HOURS` re-runs an execute pass on that cadence.
"""

import json
import logging
import os
import sqlite3
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import selection
from .store import Store, now, pack, unpack

LOG = logging.getLogger("market_catalog")
MODE = os.environ.get("CATALOG_RETENTION_MODE", "off")
MODES = ("dry_run", "execute")
TREATMENT = os.environ.get("CATALOG_RETENTION_REVISIONS", "notes")
TREATMENTS = ("notes", "final_only")
REPEAT_HOURS = max(0.0, float(os.environ.get("CATALOG_RETENTION_REPEAT_HOURS", "0")))
TERMINAL = frozenset({"finalized", "settled"})
ASSESSMENT_GRACE_DAYS = max(0, int(os.environ.get("CATALOG_RETENTION_ASSESSMENT_DAYS", "7")))
BACKUP_SUFFIX = ".before-compression-v1"
BACKUP_RETIRED = "retention:backup_retired"
LAST_REPORT = "retention:last_report"
MARKET_PAGE = 100
ASSESSMENT_PAGE = 5000
# A dry run re-encodes one eligible market in this many to measure the notes' size.
SAMPLE_EVERY = 25
# A dry run persists its tally (with the per-series table) and cursor together this often;
# execute persists both inside every page's own write transaction.
PERSIST_EVERY = 100
TOP_SERIES = 30
PROGRESS_SECONDS = 300


def state_key(mode):
    return "retention:" + mode


def backup_path(store):
    return Path(store.path + BACKUP_SUFFIX)


def _fresh(mode, treatment=None):
    return {
        "mode": mode,
        "treatment": (treatment or TREATMENT) if mode == "execute" else None,
        "started_at": now(),
        "phase": "backup",
        "cursor": "",
        "backup": None,
        "assessments": {"after": 0, "examined": 0, "current_kept": 0, "recent_kept": 0,
                        "superseded_rows": 0, "superseded_bytes": 0, "deleted_rows": 0},
        "revisions": {
            "markets_examined": 0, "open_kept": 0, "selected_kept": 0, "unreadable": 0,
            "markets_eligible": 0, "rows_full": 0, "bytes_full": 0,
            # final_only: markets with no observed rule change, and what deleting them frees.
            "final_only_markets": 0, "final_only_rows": 0, "final_only_bytes": 0,
            "rules_changed_kept": 0,
            # notes: measured on a sample in a dry run, exactly in execute.
            "sample_rows": 0, "sample_bytes_before": 0, "sample_bytes_after": 0,
            "rows_already_compact": 0, "rows_compacted": 0, "rows_deleted": 0,
            "bytes_removed": 0, "bytes_written": 0,
        },
        "by_series": {},
        "completed_at": None,
    }


def _backup_step(store, progress, mode):
    path = backup_path(store)
    size = path.stat().st_size if path.exists() else 0
    compression_done = bool((store.state("storage:compression") or {}).get("complete"))
    record = {"exists": path.exists(), "bytes": size, "compression_complete": compression_done}
    if mode == "execute" and path.exists():
        if compression_done:
            # State first: a crash between the two leaves only a file to remove, where the
            # reverse order would let the next start rebuild a full-size copy.
            store.set_state(BACKUP_RETIRED, {"at": now(), "bytes": size})
            path.unlink(missing_ok=True)
            Path(str(path) + ".incomplete").unlink(missing_ok=True)
            record["removed"] = True
        else:
            record["kept"] = "compression_incomplete"
    progress["backup"] = record
    progress["phase"] = "assessments"
    return True


def _assessment_step(store, progress, mode):
    tally = progress["assessments"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ASSESSMENT_GRACE_DAYS)).isoformat()
    with store.connect() as db:
        if mode == "execute":
            db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            "SELECT rowid,id,as_of,length(CAST(document AS BLOB)) FROM assessments "
            "WHERE rowid>? ORDER BY rowid LIMIT ?",
            (tally["after"], ASSESSMENT_PAGE),
        ).fetchall()
        if not rows:
            progress["phase"] = "revisions"
            return True
        # current_assessments has no index on assessment_id: one set per page beats a scan per
        # row, and read inside the write transaction it cannot go stale.
        current = {row[0] for row in db.execute("SELECT assessment_id FROM current_assessments")}
        doomed = []
        for rowid, assessment_id, as_of, size in rows:
            tally["examined"] += 1
            if assessment_id in current:
                tally["current_kept"] += 1
            elif not as_of or as_of >= cutoff:
                tally["recent_kept"] += 1
            else:
                tally["superseded_rows"] += 1
                tally["superseded_bytes"] += size or 0
                doomed.append((rowid,))
        if mode == "execute" and doomed:
            db.executemany("DELETE FROM assessments WHERE rowid=?", doomed)
            tally["deleted_rows"] += len(doomed)
        tally["after"] = rows[-1][0]
        if mode == "execute":
            Store.put_state(db, state_key(mode), progress)
    return True


def _history(db, ticker):
    """Every revision of one market in stored order, decoded."""
    return [
        {"rowid": row[0], "hash": row[1], "captured_at": row[2], "size": row[3] or 0,
         "stored": row[4], "document": unpack(row[4])}
        for row in db.execute(
            "SELECT rowid,hash,captured_at,length(CAST(document AS BLOB)),document "
            "FROM revisions WHERE kind='market' AND ticker=? ORDER BY rowid",
            (ticker,),
        )
    ]


def _size(stored):
    return len(stored.encode() if isinstance(stored, str) else stored)


def _notes(history):
    """(key, stored) per row as notes: full rows compacted, compact rows unchanged. None when a
    full row lacks a field the compact form needs."""
    out, previous_rules = [], None
    for row in history:
        document = row["document"]
        if selection.representation(document) == selection.COMPACT:
            out.append((row["hash"], row["stored"]))
        else:
            try:
                include_rules = previous_rules is None or document["rules_hash"] != previous_rules
                compact = selection.compact_observation(document, row["hash"], include_rules)
            except (KeyError, TypeError):
                return None
            out.append(
                (selection.compact_key(row["hash"]), pack(compact, selection.COMPACT_PACK_MINIMUM))
            )
        previous_rules = document.get("rules_hash")
    return out


def _apply_notes(db, ticker, history, notes, tally):
    full = sum(selection.representation(row["document"]) != selection.COMPACT for row in history)
    tally["rows_already_compact"] += len(history) - full
    if not full:
        return
    # Delete and re-insert the whole market: its rows keep their relative order, and the
    # emptied pages reach the freelist (an in-place UPDATE frees none).
    db.executemany("DELETE FROM revisions WHERE rowid=?", [(row["rowid"],) for row in history])
    for row, (key, stored) in zip(history, notes, strict=True):
        db.execute(
            "INSERT OR IGNORE INTO revisions VALUES ('market',?,?,?,?)",
            (ticker, key, row["captured_at"], stored),
        )
        tally["bytes_written"] += _size(stored)
    tally["rows_compacted"] += full
    tally["bytes_removed"] += sum(row["size"] for row in history)


def _revision_step(store, progress, mode):
    tally = progress["revisions"]
    treatment = progress.get("treatment")
    with store.connect() as db:
        if not selection.membership_ready(db):
            # Unready membership would read every market as unselected: never thin then.
            return None
        if mode == "execute":
            db.execute("BEGIN IMMEDIATE")
        # Ticker order through the primary-key index: stable while upserts REPLACE rows.
        page = db.execute(
            "SELECT ticker,series,rules_hash,document FROM objects WHERE kind='market' "
            "AND ticker>? ORDER BY ticker LIMIT ?",
            (progress["cursor"], MARKET_PAGE),
        ).fetchall()
        if not page:
            progress["phase"] = "done"
            return True
        for ticker, series, current_rules, stored in page:
            tally["markets_examined"] += 1
            try:
                raw = unpack(stored).get("raw") or {}
            except (ValueError, TypeError, zlib.error):
                tally["unreadable"] += 1
                continue
            if raw.get("status") not in TERMINAL:
                tally["open_kept"] += 1
                continue
            if selection.selected_in(db, "market", ticker, series, raw.get("event_ticker")):
                tally["selected_kept"] += 1
                continue
            try:
                history = _history(db, ticker)
            except (ValueError, TypeError, zlib.error):
                tally["unreadable"] += 1
                continue
            if not history:
                continue
            tally["markets_eligible"] += 1
            size = sum(row["size"] for row in history)
            tally["rows_full"] += len(history)
            tally["bytes_full"] += size
            unchanged = all(row["document"].get("rules_hash") == current_rules for row in history)
            if unchanged:
                tally["final_only_markets"] += 1
                tally["final_only_rows"] += len(history)
                tally["final_only_bytes"] += size
            else:
                tally["rules_changed_kept"] += 1
            if mode == "execute":
                if treatment == "final_only":
                    if unchanged:
                        db.executemany(
                            "DELETE FROM revisions WHERE rowid=?",
                            [(row["rowid"],) for row in history],
                        )
                        tally["rows_deleted"] += len(history)
                        tally["bytes_removed"] += size
                    continue
                notes = _notes(history)
                if notes is None:
                    tally["unreadable"] += 1
                    continue
                _apply_notes(db, ticker, history, notes, tally)
                continue
            # The per-series table is the dry run's report; execute keeps totals only, so its
            # every-page state write stays small.
            entry = progress["by_series"].setdefault(series or "?", [0, 0, 0, 0])
            entry[0] += 1
            entry[1] += len(history)
            entry[2] += size
            entry[3] += size if unchanged else 0
            if tally["markets_eligible"] % SAMPLE_EVERY == 1:
                notes = _notes(history)
                if notes is not None:
                    tally["sample_rows"] += len(history)
                    tally["sample_bytes_before"] += size
                    tally["sample_bytes_after"] += sum(_size(stored) for _key, stored in notes)
        progress["cursor"] = page[-1][0]
        if mode == "execute":
            Store.put_state(db, state_key(mode), progress)
    return True


def report(store, progress):
    """Totals, both treatments' projected saving, and the largest series."""
    tally = progress["revisions"]
    detail = store.state("storage:detail") or {}
    objects_bytes = detail.get("objects_bytes") or {}
    revisions = (detail.get("tables") or {}).get("revisions") or {}
    table, index = objects_bytes.get("revisions"), objects_bytes.get("sqlite_autoindex_revisions_1")
    documents, rows_total = revisions.get("document_bytes"), revisions.get("rows")
    # Table bytes on disk per stored document byte, and primary-key index bytes per row.
    overhead = table / documents if table and documents else 1.0
    index_row = index / rows_total if index and rows_total else 0.0
    if tally["sample_bytes_before"]:
        ratio = tally["sample_bytes_after"] / tally["sample_bytes_before"]
        notes_saved = tally["bytes_full"] * (1 - ratio) * overhead
    else:
        ratio, notes_saved = None, None
    final_saved = tally["final_only_bytes"] * overhead + tally["final_only_rows"] * index_row
    assessments = progress["assessments"]
    backup = progress["backup"] or {}
    fixed = assessments["superseded_bytes"] + (backup.get("bytes") or 0)
    top = sorted(progress["by_series"].items(), key=lambda item: -item[1][2])[:TOP_SERIES]
    return {
        "mode": progress["mode"],
        "treatment": progress.get("treatment"),
        "phase": progress["phase"],
        "started_at": progress["started_at"],
        "completed_at": progress["completed_at"],
        "backup": backup,
        "assessments": assessments,
        "revisions": tally,
        "notes_size_ratio": round(ratio, 4) if ratio is not None else None,
        "disk_bytes_freed_estimate": {
            "notes": int(notes_saved + fixed) if notes_saved is not None else None,
            "final_only": int(final_saved + fixed),
        },
        "series_eligible": len(progress["by_series"]),
        "top_series": [
            {"series": name, "markets": entry[0], "rows": entry[1], "bytes": entry[2],
             "final_only_bytes": entry[3]}
            for name, entry in top
        ],
        "kept": "current objects; open markets; selected markets/events/series; event and "
        "series revisions; evidence; reviews; current assessments; contract documents",
    }


def step(store, progress, mode):
    """Advance one bounded unit. True: more to do; None: waiting; False: pass complete."""
    phase = progress["phase"]
    if phase == "backup":
        return _backup_step(store, progress, mode)
    if phase == "assessments":
        return _assessment_step(store, progress, mode)
    if phase == "revisions":
        return _revision_step(store, progress, mode)
    return False


def _pass(store, stopped, mode, pause):
    key = state_key(mode)
    progress = store.state(key) or _fresh(mode)
    if progress.get("completed_at"):
        return progress
    LOG.info(
        "catalog_retention status=starting mode=%s treatment=%s phase=%s",
        mode, progress.get("treatment"), progress["phase"],
    )
    pages = 0
    phase = progress["phase"]
    last_log = time.monotonic()
    while not stopped.is_set():
        # Every write in here can meet a busy writer (the evaluation job holds the lock for
        # longer than the 30 s busy timeout), the state writes as much as a page.
        try:
            advanced = step(store, progress, mode)
            if advanced is None:
                stopped.wait(60)
                continue
            pages += 1
            finished = advanced is False
            if finished:
                progress["completed_at"] = now()
            if finished or progress["phase"] != phase or pages % PERSIST_EVERY == 0:
                store.set_state(key, progress)
            phase = progress["phase"]
            if finished:
                summary = report(store, progress)
                store.set_state(LAST_REPORT, summary)
                LOG.info("catalog_retention_report=%s", json.dumps(summary, sort_keys=True))
                return progress
            if time.monotonic() - last_log >= PROGRESS_SECONDS:
                store.set_state(key, progress)
                summary = report(store, progress)
                summary.pop("top_series")
                LOG.info("catalog_retention_progress=%s", json.dumps(summary, sort_keys=True))
                last_log = time.monotonic()
        except sqlite3.OperationalError as error:  # a busy writer: retry from what is stored
            LOG.warning("catalog_retention status=retry error=%s", error)
            # A failed page may have counted before it rolled back, and a dry run's unpersisted
            # tally and cursor move together: resume from the stored pair either way.
            progress = store.state(key) or _fresh(mode)
            phase = progress["phase"]
            stopped.wait(30)
            continue
        stopped.wait(pause)
    store.set_state(key, progress)
    return progress


def run(store, stopped, mode=None, pause=0.05, repeat_hours=None):
    """The configured mode's pass, on its own thread. A completed pass is not repeated unless
    an execute cadence is set; then the next starts that long after the last one finished."""
    mode = mode or MODE
    if mode not in MODES:
        return None
    if mode == "execute" and TREATMENT not in TREATMENTS:
        LOG.error("catalog_retention status=refused treatment=%s", TREATMENT)
        return None
    repeat = REPEAT_HOURS if repeat_hours is None else repeat_hours
    while True:
        try:
            progress = _pass(store, stopped, mode, pause)
        except Exception:  # noqa: BLE001 — a failed pass must be visible, never kill silently
            LOG.exception("catalog_retention status=error mode=%s", mode)
            return None
        if stopped.is_set() or mode != "execute" or not repeat:
            return progress
        due = datetime.fromisoformat(progress["completed_at"]) + timedelta(hours=repeat)
        wait = (due - datetime.now(timezone.utc)).total_seconds()
        if wait > 0 and stopped.wait(wait):
            return progress
        store.set_state(state_key(mode), _fresh(mode))
