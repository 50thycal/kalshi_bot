"""Read-only web dashboard for the evolutionary agent system (v0.2).

A single self-contained page + a small JSON API, served by the Python stdlib HTTP
server (no new dependencies, no build step). Runs as its OWN Railway web service
against the same Postgres database; it never writes to the DB and never touches
Kalshi or the LLM, so it cannot change any agent behavior.

Modules:
  server.py        routing (GET/HEAD only) and query-parameter parsing
  data.py          one payload builder per route + the publishable-text policy
  metrics.py       all financial aggregation (bots, fleet, generations, trades)
  events.py        the categorised, paginated activity feed + fleet health
  strategy_view.py deterministic plain-English rendering of a bot's strategy

Deploy: a Railway service on this repo with start command
    python -m kalshi_bot.dashboard
and env var DATABASE_URL (same DB as the evo worker). Railway sets PORT and
generates the public URL. See docs/EVO_RUNBOOK.md.
"""
