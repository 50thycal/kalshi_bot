"""Agent-facing READ access to the operator's research library (docs/*.md).

Two research efforts share this repo: the operator's (thesis docs, BOOK_REGISTRY,
study writeups accumulated over ~200 PRs) and the fleet's. The docs already ship
inside the deployed image — nixpacks copies the whole repo — so every merge to the
default branch puts the latest text on the worker's own disk. Nothing here syncs
anything; this module is only the missing READ PATH, and its pair is the
`book_performance` source in data_access.py: thesis without the scoreboard invites
cargo-culting a losing book, the scoreboard without thesis invites copying numbers
with no mechanism.

Safety is by construction, like data_access: the readable set is exactly the .md
files directly inside docs/ — resolved by basename against a listdir, so an
agent-supplied name containing a separator or a parent reference can never address
anything else on the filesystem (which also holds env-shaped config and source).
Output is chunk-capped; each read costs a `data_reads` budget unit and resurfaces
through the same recent-reads channel as inspect_data.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# ~1,500-2,000 tokens per chunk: enough that a thesis doc is 1-2 reads, small enough
# that a read can never flood the next heartbeat's prompt (recent_reads carries it).
CHUNK_CHARS = 6_000

_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"


def _docs_dir() -> Path:
    return _DOCS_DIR


@lru_cache(maxsize=1)
def _library() -> dict[str, Path]:
    """name (uppercase stem) -> path, for .md files DIRECTLY in docs/. The lru_cache
    is per-process: the library only changes on deploy, which restarts the worker."""
    root = _docs_dir()
    if not root.is_dir():  # pragma: no cover — a broken checkout, not a code path
        return {}
    return {p.stem.upper(): p for p in sorted(root.glob("*.md")) if p.is_file()}


def _title(path: Path) -> str:
    """First markdown heading — every doc in this repo opens with `# <title>`."""
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("#"):
                    return line.lstrip("#").strip()[:120]
                if line.strip():
                    return line.strip()[:120]
    except OSError:  # pragma: no cover
        pass
    return ""


def doc_index() -> list[dict]:
    """The whole library as {name, title} — what exists, so an agent never has to
    guess a doc name."""
    return [{"name": name, "title": _title(path)} for name, path in _library().items()]


def doc_names() -> list[str]:
    return list(_library())


def _resolve(name: str) -> Path | None:
    """Basename-only resolution against the library listing. A name that is not
    literally a key (after stripping one .md suffix, case-insensitively) matches
    nothing — so traversal sequences simply fail the dict lookup and no
    agent-supplied string is ever joined onto a filesystem path."""
    cleaned = str(name).strip()
    if cleaned.lower().endswith(".md"):
        cleaned = cleaned[:-3]
    return _library().get(cleaned.upper())


def read_doc(name: str, *, chunk: int = 0) -> tuple[dict | None, str | None]:
    """One bounded chunk of one doc. Returns (result, None) or (None, error).

    `headings` is the doc's full section map regardless of which chunk this is —
    an 1,800-line journal is unnavigable without it — and `total_chunks` tells the
    agent whether (and where) to keep reading."""
    path = _resolve(name)
    if path is None:
        return None, (
            f"unknown doc {str(name)[:80]!r}; available: {', '.join(doc_names())}"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover — deploy-image read failure
        return None, f"could not read {path.name}: {type(exc).__name__}"
    try:
        chunk = max(0, int(chunk))
    except (TypeError, ValueError):
        chunk = 0
    total_chunks = max(1, -(-len(text) // CHUNK_CHARS))
    chunk = min(chunk, total_chunks - 1)
    headings = [
        line.strip()[:120] for line in text.splitlines() if line.startswith("#")
    ][:60]
    return {
        "doc": path.stem.upper(),
        "title": _title(path),
        "chunk": chunk,
        "total_chunks": total_chunks,
        "headings": headings,
        "text": text[chunk * CHUNK_CHARS:(chunk + 1) * CHUNK_CHARS],
    }, None
