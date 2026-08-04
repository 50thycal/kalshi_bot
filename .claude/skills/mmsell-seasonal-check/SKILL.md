---
name: mmsell-seasonal-check
description: Run one iteration of the recurring mmsell seasonal forward-look check — pull settled-history capture health and near-term market supply via the ops channel, diff against the last check, and post a report ONLY when something is notable (otherwise re-arm silently). Every 4th run also refreshes the regime-edge backtest. Use when the weekly mmsell-seasonal-check trigger fires, or the user asks "check the mmsell forecast", "how's the history capture doing", "any new regime supply arriving", or "seasonal check".
---

# mmsell seasonal check — one iteration of the weekly forward-look loop

**Why this exists:** `docs/MMSELL_SEASONAL_FORECAST.md` established that mmsell's entire
paper history is ONE regime (summer sports) and that Kalshi only retains ~70 days of
settled history — so NFL/NBA/NHL/elections are being learned about in two ways right
now: a slow, continuous CAPTURE job (`kalshi_bot/mmsell/history.py`) building next
season's backtest data forward, and the LIVE supply calendar telling us what's about to
become tradeable this season. Neither changes hour to hour. This loop is the low-frequency
check that catches the capture silently stalling, or a new regime's supply arriving,
without anyone having to remember to look.

**Guardrails (absolute):** this loop REPORTS only. Never edit trading config, never touch
`mmsell_history_series` / the settlement caps / any live switch, never push to the default
branch or `ops` from this skill's own judgment. State lives on the dedicated
`mmsell-seasonal-status` branch only.

**Report only when notable — this is the point of the loop.** If nothing below fires,
re-persist state and stay silent; do not post an "all clear" message every week. Notable:

1. Capture is **STALE** — `mmsell_history_status`'s last-write age > 24h.
2. The pending candle queue **grew** since the last check instead of shrinking (the job
   may be losing a race against new settlements, or erroring every cycle).
3. **BEYOND-WALL didn't grow** across 2+ consecutive checks while pending > 0 — the
   capture is enumerating but not actually storing candles.
4. A regime's live `ELIGIBLE` (or `in-window`) count crossed from 0 to something material,
   or moved by ≥5 — new tradeable supply just arrived (this is the "coming markets"
   signal the whole loop exists to surface).
5. Any script errored, or a series in `MMSELL_HISTORY_SERIES` came back dead
   (0 settled history) two checks running — may be a bad ticker guess worth fixing.
6. Every 4th run (see step 3 below): the refreshed regime-edge backtest, always reported
   when it runs, since it's infrequent enough to be inherently informative.

## Procedure

### 1. Refresh the ops worktree from the default branch

```bash
cd /tmp/ops && git fetch origin claude/confident-goldberg-83u3q ops -q \
  && git checkout -B ops origin/claude/confident-goldberg-83u3q -q \
  && git push origin ops --force-with-lease -q
```
(Substitute the current default branch name if it has changed.) This matters: the
seasonal scripts and their ops-allowlist entries only exist on `ops` after this refresh
if a recent PR touched them.

### 2. Read prior state

```bash
git fetch origin mmsell-seasonal-status -q 2>/dev/null \
  && git show FETCH_HEAD:docs/MMSELL_SEASONAL_STATUS.md 2>/dev/null
```
If the branch/file doesn't exist yet, this is run #1: skip every diff below, treat
nothing as notable except genuine errors, and create the branch in step 6 (orphan-safe:
`git checkout -B mmsell-seasonal-status origin/<default-branch> -q` once, from a
`/tmp/seasonalstate` worktree — reuse `/tmp/ops` if simpler — then fetch+reset that
branch on every later run).

The prior-state file carries: run number, last-check timestamp, last freshness
age/pending/BEYOND-WALL-per-regime, last live-supply eligible-count-per-regime snapshot,
and the date of the last regime-backtest refresh.

### 3. Run the capture health check (every run)

`{"type":"script","id":"seasonal-hist-<short-id>","name":"mmsell_history_status"}` via
the ops channel (write `ops/request.json`, commit, push, poll `ops/results/<id>.txt`
~15s intervals up to ~12 tries). Capture: last-write age, pending count, and the
BEYOND-WALL total per regime (section 2 of its output).

Determine run number `n` from the prior state (+1, or 1 if this is run #1). **Every 4th
run** (`n % 4 == 0`), also run the regime-edge backtest this same pass:
`{"type":"script","id":"seasonal-rb-<short-id>","name":"mmsell_regime_backtest"}`
(defaults are fine — it already covers the regimes with zero paper history plus MLB as
control). Capture the coverage/yield/edge tables.

### 4. Run the live supply check (every run)

`{"type":"script","id":"seasonal-supply-<short-id>","name":"mmsell_supply_forecast","args":["--weeks","12"]}`.
Capture section 1 (live supply per regime — `open`/`in-window`/`ELIGIBLE`/`band rate`)
and the first 4 rows of section 2 (window-entry calendar) — the near-term weeks are what
changes; the far-out rows are noise this often.

### 5. Reset the ops channel

`{"type":"noop"}` to `ops/request.json`, commit, push. Always do this, even if a step
failed partway.

### 6. Diff, decide notability, and persist

Compare against prior state using the six triggers listed above. Overwrite
`docs/MMSELL_SEASONAL_STATUS.md` on the `mmsell-seasonal-status` branch regardless of
whether anything was notable (increment the run counter, timestamp, current
freshness/pending/beyond-wall per regime, current eligible-per-regime snapshot, and —
on a `n % 4 == 0` run — the regime-backtest refresh date + headline numbers). Push to
`mmsell-seasonal-status` **only** — never the default branch, never `ops`.

### 7. Report (only if something from the notable list fired) — or stay silent

If nothing fired: do not post anything in chat. The persisted state file is the record
that the check ran.

If something fired, post a short chat report — no banner wall needed (this loop is
infrequent by design; save the loud banner convention for the higher-frequency
`kalshi_Loop_checker`/`kalshi_loop_checker_phase_3` strategy loop):

```
mmsell seasonal check — run #<n>, <date>
```

Then, only the sections that actually fired:

- **Capture health** (if stale, queue growing, or beyond-wall stalled): last-write age,
  pending (last→now), BEYOND-WALL per regime (last→now). Say plainly if this looks like
  the job is down, not just slow.
- **New supply** (if a regime crossed the ≥5-move threshold): `regime | eligible
  (last→now) | in-window (last→now)`. One sentence on what's arriving (e.g. "NFL's first
  markets are entering the 14-day window").
- **Regime edge refresh** (every 4th run): the coverage/yield/edge table from
  `mmsell_regime_backtest`, with a one-line note on whether the picture changed from the
  last refresh (more/less measurable regimes, yield up/down, edge sign flip). Cite
  `docs/MMSELL_SEASONAL_FORECAST.md`'s pre-registered gates (H1–H7) when a regime's
  numbers are now large enough to speak to one.
- **Dead series** (if any): which `MMSELL_HISTORY_SERIES` ticker(s) have returned zero
  settled history twice running, with the reminder to verify via
  `mmsell_supply_forecast --list-series <regime>` before assuming it's genuinely dormant
  vs. a bad ticker guess.

Close with one sentence: what, if anything, is worth a follow-up in a real (non-loop)
session.
