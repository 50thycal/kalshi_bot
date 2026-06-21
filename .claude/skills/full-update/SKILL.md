---
name: full-update
description: Produce a complete status review of the Kalshi trading bot — every strategy/book, what's live vs paper, data collection, the state of all experiments, and current realized P&L standing against the $100/month goal. Use when the user asks for a "full update", "full review", "where do we stand", or a comprehensive end-to-end bot status.
---

# Full Update — end-to-end bot status review

Produce a comprehensive, grounded review of the entire Kalshi bot and where it stands
against the north-star goal in CLAUDE.md: **$100/month in realized profit from any
combination of strategies.** The goal is realized DOLLARS across the whole portfolio —
not win rate, not book count, not research volume.

## Step 1 — pull fresh live numbers (always do this first)

Use the ops channel (see CLAUDE.md → "Operating the logs + database access system").
Run BOTH standing commands and read each result back from `ops/result.txt`:

- **digest** → `{"type":"script","name":"weather_digest"}` — worker health, today's
  live entries/exits + fills, open positions, realized-today, the per-book all-time
  realized P&L rollup, and the ANOMALIES section (lead with anything flagged there).
- **PnL** → `{"type":"script","name":"weather_pnl"}` — the per-book rollup plus the
  per-(window × strategy) decision table; per-trade is the deciding number.

Run each as its own ops request (push the request file, poll `ops/result.txt`, reset to
`{"type":"noop"}` when finished). The digest is auto-archived to the `digest-archive`
branch.

## Step 2 — review and summarize, grounded in those numbers + the code

Cover all of these, concisely (tables for the per-book standing; short prose elsewhere):

1. **Strategies / books.** Every book and its current standing (n, realized total,
   per-trade): weather `fav / favband / nws / cal / dist / pm / con / cwin / obs`
   (high and low), plus the non-weather scanner books (`buy_favorite / reversion /
   momentum`). Flag which are +EV, which are ~breakeven, and the big bleeders. Treat
   just-started books (e.g. `favband`, `con`) as not-yet-readable and say so.
2. **Live vs paper.** What is actually risking REAL money — the live-execution
   allowlist / live cells (`kalshi_bot/live/executor.py`, config) — vs paper-only.
   State today's realized and the real-money exposure plainly.
3. **Data collection.** What datasets are persisted each cycle and why: NWS + HRRR
   forecasts, station observations, ensembles, bucket-ladder snapshots, Polymarket,
   settlements, the `weather_forecast_outcomes` validation table, account snapshots,
   and the `backfill_weather_*` archive.
4. **Experiments / research state.** What's been validated or ruled out, with the
   `scripts/` study that established it (e.g. entry-timing = no edge; highs calibration
   = the LAX 50–70¢ band → `favband`; lows calibration = no edge; exits = hold-to-
   settlement optimal for the +EV cities, stop-losses are poison; consensus `con` book).
5. **Standing vs the $100/month goal.** Total realized P&L across all books, the
   trajectory (improving or bleeding, and what's driving it), and the gap to +$100/mo.
   Be blunt.

## Step 3 — close with leverage + questions

End with the highest-leverage next moves toward +$100/month (usually: prune the proven
−EV books, let validated edges accrue forward-tested data, graduate a validated edge
from paper to small live size) and any open questions for the user.

## Notes
- Be honest about the bottom line in dollars. Research that proves a book is −EV is a
  *win* — it tells us what to stop trading.
- Reset the ops channel to `{"type":"noop"}` when done.
