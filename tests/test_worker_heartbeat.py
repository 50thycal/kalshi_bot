"""The dead-worker alarm (XOS-000015).

On 2026-08-30 the worker crash-looped on an invalid config for 16h51m — no
scanning, no paper trades, no live orders, across every strategy — and nothing
noticed. It surfaced only because a human asked an unrelated question about fills.

What these tests pin is not "the query works" but the three properties that decide
whether an alarm is real: it fires when the worker is dead, it stays quiet when the
worker is merely idle, and its failure actually reaches someone.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "worker-heartbeat.yml"


def _load():
    spec = importlib.util.spec_from_file_location(
        "worker_heartbeat", ROOT / "scripts" / "worker_heartbeat.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hb = _load()


class _Cur:
    def __init__(self, row):
        self._row = row

    def execute(self, *a, **k):
        return None

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _Cur(self._row)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(monkeypatch, latest, now=None, capsys=None):
    now = now or datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    fake = type("psycopg", (), {"connect": staticmethod(lambda *a, **k: _Conn((latest, now)))})
    monkeypatch.setitem(__import__("sys").modules, "psycopg", fake)
    monkeypatch.setenv("DATABASE_URL_RO", "postgres://ro")
    return hb.main()


def test_fires_when_the_worker_is_dead(monkeypatch, capsys):
    """The actual outage: 16h51m of silence must exit non-zero."""
    now = datetime(2026, 8, 31, 11, 28, tzinfo=timezone.utc)
    latest = datetime(2026, 8, 30, 18, 37, tzinfo=timezone.utc)

    assert _run(monkeypatch, latest, now) == 1
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "WORKER APPEARS DOWN" in out


def test_quiet_market_is_not_an_outage(monkeypatch, capsys):
    """`bot_runs` is written per cycle whether or not anything is tradeable. An
    alarm that fired on a quiet night would be muted within a week, and a muted
    alarm is the same as no alarm."""
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    latest = now - timedelta(minutes=4)          # idle, but cycling

    assert _run(monkeypatch, latest, now) == 0
    assert "OK" in capsys.readouterr().out


def test_threshold_leaves_room_for_a_redeploy(monkeypatch):
    """A deploy takes ~2-3 minutes and must not page anyone."""
    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    assert _run(monkeypatch, now - timedelta(minutes=5), now) == 0


def test_no_rows_at_all_reads_as_dead_not_as_missing_data(monkeypatch, capsys):
    """A healthy worker cannot produce an empty table. Treating this as 'no data,
    nothing to say' is how a silent outage stays silent."""
    assert _run(monkeypatch, None) == 1
    assert "NO bot_runs rows" in capsys.readouterr().out


def test_a_missing_database_url_fails_loudly(monkeypatch, capsys):
    """A check that cannot run must not report success — that is an outage in the
    alarm itself, and it has to be as loud as an outage in the worker."""
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert hb.main() == 1
    assert "::error::" in capsys.readouterr().out


# --- the alarm has to actually reach someone ------------------------------


def test_the_workflow_sets_pipefail_so_a_failure_is_not_swallowed():
    """GitHub Actions runs `run:` under `bash -e` WITHOUT pipefail, so the exit
    status of `script | tee` is tee's — always 0. Without `set -o pipefail` this
    alarm would print "WORKER APPEARS DOWN" into the run summary and pass the job,
    and would never once have fired. Caught in review before merge; pinned here
    because the bug is invisible until the day it matters.
    """
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["heartbeat"]["steps"]
    run_steps = [s for s in steps if "run" in s and "worker_heartbeat.py" in s["run"]]

    assert run_steps, "the workflow must run the heartbeat script"
    for step in run_steps:
        if "|" in step["run"]:
            assert "pipefail" in step["run"], (
                "piping the check through tee without pipefail swallows its exit "
                "code and silently disables the alarm"
            )


def test_the_workflow_is_scheduled_and_independent_of_the_worker():
    """A heartbeat the worker emits goes silent exactly when it matters. This must
    be driven by an external schedule and read the database directly."""
    wf = yaml.safe_load(WORKFLOW.read_text())
    triggers = wf.get("on") or wf.get(True)      # PyYAML parses bare `on:` as True

    assert "schedule" in triggers, "must run on a schedule, not on demand only"
    assert triggers["schedule"][0]["cron"], "needs a real cron expression"
    body = WORKFLOW.read_text()
    assert "DATABASE_URL_RO" in body, "must use the SELECT-only URL"


@pytest.mark.parametrize("secret", ["DATABASE_URL:", "RAILWAY_TOKEN"])
def test_the_workflow_takes_no_credential_it_does_not_need(secret):
    """Least privilege: liveness needs one read-only URL and nothing else."""
    assert secret not in WORKFLOW.read_text()
