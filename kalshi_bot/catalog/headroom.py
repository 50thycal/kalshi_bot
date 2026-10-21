"""Reversible catalog collection deferrals; retain evidence instead of deleting it."""

import shutil
import sqlite3
from pathlib import Path

from .store import now

GIB = 1024**3


class StorageDeferred(Exception):
    def __init__(self, tier, snapshot):
        self.tier = tier
        self.snapshot = snapshot
        super().__init__("Catalog storage headroom required")


def measure(store):
    previous = store.state("storage:guard", {})
    try:
        usage = shutil.disk_usage(Path(store.path).parent)
        if usage.total <= 0 or usage.free < 0:
            raise ValueError("Invalid storage measurement")
        # Pages on SQLite's freelist (after retention, DEC-038) are reused before the file grows,
        # so bulk collection may count them. The critical tier reads the volume alone: the WAL
        # and other files cannot use them.
        with store.connect() as db:
            reusable = (
                db.execute("PRAGMA freelist_count").fetchone()[0]
                * db.execute("PRAGMA page_size").fetchone()[0]
            )
        bulk_free = usage.free + reusable
        bulk_pause = max(2 * GIB, int(usage.total * 0.20))
        bulk_resume = max(3 * GIB, int(usage.total * 0.25))
        critical_pause, critical_resume = GIB, 3 * GIB // 2
        bulk_paused = bulk_free < (bulk_resume if previous.get("bulk_paused") else bulk_pause)
        writes_paused = usage.free < (
            critical_resume if previous.get("writes_paused") else critical_pause
        )
        snapshot = {
            "captured_at": now(),
            "volume_free_bytes": usage.free,
            "volume_total_bytes": usage.total,
            "reusable_bytes": reusable,
            "bulk_headroom_bytes": bulk_free,
            "bulk_paused": bulk_paused,
            "writes_paused": writes_paused,
            "bulk_pause_below_bytes": bulk_pause,
            "bulk_resume_at_bytes": bulk_resume,
            "writes_pause_below_bytes": critical_pause,
            "writes_resume_at_bytes": critical_resume,
            "measurement_error": None,
            "automatic_resume": True,
        }
    except (OSError, ValueError, sqlite3.Error) as error:
        snapshot = {
            **previous,
            "captured_at": now(),
            "bulk_paused": True,
            "writes_paused": True,
            "measurement_error": type(error).__name__,
            "automatic_resume": True,
        }
    store.set_state("storage:guard", snapshot)
    return snapshot


def require_space(store, bulk=False):
    snapshot = measure(store)
    if snapshot["writes_paused"]:
        raise StorageDeferred("writes", snapshot)
    if bulk and snapshot["bulk_paused"]:
        raise StorageDeferred("bulk", snapshot)
    return snapshot
