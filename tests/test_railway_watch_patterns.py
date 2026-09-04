"""Which file changes are allowed to redeploy which Railway service.

An ops request is an ordinary commit on the `ops` branch, so with no watch paths
set every request — including a read-only `logs` pull — triggered a rebuild. That
made the dashboard undiagnosable through the very channel used to diagnose it:
asking for the logs restarted the container and rotated away the lines being asked
for. Measured 2026-09-03: a `script` request pushed at ~17:04:20 produced livedash
deployment `512f8a0a` created 17:04:30, and a read-only `logs` request pushed at
~17:08:0x produced main deployment `9a957e40` created 17:08:16.

These tests pin the two halves of the fix so neither can rot silently:

* livedash rebuilds for its own code and build inputs, and for nothing else;
* any service that reads `docs/` AT RUNTIME keeps `docs/**` in its watch paths.

The second is the trap. `kalshi_bot/evo/knowledge.py` serves the agent fleet's
`read_doc` channel out of `docs/*.md`, so for the worker a thesis document is not
documentation — it is data the deploy ships. Narrowing that service to source
directories would quietly stop research reaching the fleet, and nothing would fail;
the bots would simply keep reading last week's library.

Watch paths gate GIT-PUSH deploys only. A variable change redeploys regardless,
which is what the `EXPERIMENT_OS_ISSUE_COMMAND` / `EXPERIMENT_OS_EXPERIMENT_COMMAND`
transports rely on, and nothing here touches that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Service config -> whether that service reads `docs/` while running.
CONFIGS = {
    "railway.json": True,           # the worker: evo knowledge.py serves docs/*.md
    "railway.dashboard.json": True,  # the evo dashboard, same package
    "railway.livedash.json": False,  # live-vs-paper: database and its own static file
}


def _load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _watches(patterns: list[str], path: str) -> bool:
    """Coarse gitignore-style match, good enough for the paths asserted here:
    `dir/**` covers everything beneath `dir/`, anything else is a literal path."""
    for pattern in patterns:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif pattern == path:
            return True
    return False


def test_livedash_watches_its_own_code_and_build_inputs():
    patterns = _load("railway.livedash.json")["build"]["watchPatterns"]
    for path in ("kalshi_bot/livedash/server.py",
                 "kalshi_bot/livedash/static/index.html",
                 "kalshi_bot/models.py",
                 "kalshi_bot/db.py",
                 "requirements.txt",
                 "railway.livedash.json"):
        assert _watches(patterns, path), f"a change to {path} would not redeploy livedash"


def test_an_ops_request_does_not_redeploy_the_dashboard():
    """The observability fix. Every path here changes on ops traffic or on work that
    the running dashboard cannot observe, and none of them is livedash's code."""
    patterns = _load("railway.livedash.json")["build"]["watchPatterns"]
    for path in ("ops/request.json",
                 "ops/results/livedash-probe-1.txt",
                 "docs/OPS_RUNBOOK.md",
                 "tests/test_livedash_server.py",
                 ".github/workflows/ops.yml",
                 "scripts/livedash_probe.py"):
        assert not _watches(patterns, path), (
            f"a change to {path} still redeploys livedash, which rotates the logs "
            f"an investigation is reading")


@pytest.mark.parametrize("name", [n for n, reads in CONFIGS.items() if reads])
def test_a_service_that_reads_docs_at_runtime_still_watches_them(name):
    """No watch paths at all is fine — everything redeploys, which is the old
    behaviour. Narrowing them WITHOUT `docs/**` is not: the fleet's research library
    is shipped by the deploy, so it would stop updating and nothing would fail."""
    build = _load(name).get("build", {})
    patterns = build.get("watchPatterns")
    if patterns is None:
        return
    assert _watches(patterns, "docs/PMDIV_THESIS.md"), (
        f"{name} narrows its watch paths but drops docs/, so kalshi_bot/evo/"
        f"knowledge.py would serve the fleet a stale research library")


@pytest.mark.parametrize("name", list(CONFIGS))
def test_every_service_still_watches_the_package_it_runs(name):
    """Whatever else a service narrows to, the code it executes has to be in it."""
    patterns = _load(name).get("build", {}).get("watchPatterns")
    if patterns is None:
        return
    assert _watches(patterns, "kalshi_bot/main.py"), (
        f"{name} would not redeploy on a change to the package it runs")
