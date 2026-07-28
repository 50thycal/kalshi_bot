"""The migration graph must have exactly ONE head.

This is a deploy-blocking invariant, not a style preference. When two PRs are
developed in parallel, each `alembic revision` runs against the same then-current
head, and once both merge the graph has two tips. `alembic upgrade head` cannot
choose between them and hard-fails:

    FAILED: Multiple head revisions are present for given argument 'head'

The worker then crashloops on boot and never reaches the trading loop — which is
exactly what happened live on 2026-07-28 (PR #128's evo dashboard indexes and
PR #129's positions index both branched off `d5e6f7a8b9c0`). Nothing in CI
caught it, because every test used a create_all() database and never ran the
migration chain. This test closes that gap: it fails in CI on the PR that
*creates* the second head, before it can ever reach a deploy.

The fix when this fails is a merge revision:
    alembic merge -m "merge <what> heads" <head1> <head2>
"""

from __future__ import annotations

import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = REPO_ROOT / "alembic" / "versions"
_REVISION_RE = re.compile(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))


def _revision_ids_on_disk() -> set[str]:
    """Every revision id declared by a file in versions/, read from the source
    rather than from alembic's graph — an orphan is invisible to the graph, so
    the graph cannot be its own witness."""
    ids = set()
    for path in VERSIONS_DIR.glob("*.py"):
        m = _REVISION_RE.search(path.read_text())
        assert m, f"{path.name} declares no revision id"
        ids.add(m.group(1))
    return ids


def test_migration_graph_has_exactly_one_head():
    heads = _script_directory().get_heads()
    assert len(heads) == 1, (
        f"the migration graph has {len(heads)} heads: {sorted(heads)}. "
        "`alembic upgrade head` cannot resolve this and the worker will crashloop "
        "on boot. Rejoin them with: "
        f"alembic merge -m 'merge heads' {' '.join(sorted(heads))}"
    )


def test_every_revision_is_reachable_from_the_head():
    """No orphan revision: walking down from the single head must visit every
    file in versions/. A revision pointing at a down_revision that no longer
    exists would otherwise sit silently unapplied."""
    reachable = {rev.revision for rev in _script_directory().walk_revisions()}
    on_disk = _revision_ids_on_disk()
    assert on_disk, "no migration files found — is the versions/ path right?"
    assert on_disk - reachable == set(), (
        "unreachable migration(s) — a file exists but nothing walks to it from the "
        f"head, so it will never be applied: {sorted(on_disk - reachable)}"
    )
