"""Read-only web dashboard for the evolutionary agent system (v0.1).

A single self-contained page + one JSON API, served by the Python stdlib HTTP
server (no new dependencies). Runs as its OWN Railway web service against the
same Postgres database; it never writes to the DB and never touches Kalshi or
the LLM, so it cannot change any agent behavior.

Deploy: a Railway service on this repo with start command
    python -m kalshi_bot.dashboard
and env var DATABASE_URL (same DB as the evo worker). Railway sets PORT and
generates the public URL. See docs/EVO_RUNBOOK.md.
"""
