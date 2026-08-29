# WS-009 — The live-vs-paper dashboard: load cost, run selection, and reading a dead book as current

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-08-29
**Updated:** 2026-08-29 (merged; two follow-up defects found on the deployed page)

## Goal

Make the live-vs-paper dashboard (`kalshi_bot/livedash`, the `livedash` Railway service)
usable as an operational surface: it should open in a couple of seconds rather than
half a minute, it should show the run you selected and keep showing it, and it should
not open on a retired book whose final P&L reads as a current position.

## Context

Three operator-reported faults, all in the same page:

1. **~30 seconds to first paint.** The page shows nothing at all until every request
   has returned, and the first of those rebuilds both legs of every paired run ever
   recorded, retired ones included.
2. **The board reverts to the previous run.** Select a different pair while a load is in
   flight — which, at 30 seconds a load, is most of the time — and the older response
   lands last and wins. The dropdown says one pair; the numbers underneath are the
   other one's.
3. **It opens on a pair that stopped trading.** `mmsell10 vs mmsell10_pt` ended on
   2026-08-04 and its live tag has been dropped from `LIVE_STRATEGIES`. It was still
   what the dashboard selected on load, and it appeared in a dropdown that was
   otherwise showing only running pairs, next to an unticked "14 retired" box.

Fault 3 is the one with a real cost attached: a settled book's final realized P&L sits
in a card headed "Current position", on a page whose live column is real money.

## Current Mental Model

Measured on production through the ops channel (`livedash-cost-1/2/3`, 2026-08-29).
15 recorded pairs, 1 of them still open.

```text
GET /api/runs            -- what the page did before anything was on screen
  for each of 15 pairs:
    MarkIndex.load       -> EVERY tick in the pair's epoch for every ticker it touched
                            ......................................  670,852 rows
    _latest_position_snapshots
                         -> EVERY positions row for those tickers, to keep the newest
                            per ticker ..........................  567,547 rows
                                                                 -----------
                            ORM objects built, then discarded:      1,238,399

  ... to render a table of 15 rows whose only figures are realized P&L,
      a market count, and an open/closed split -- none of which is marked at all.

GET /api/runs/mmsell10_pt   -- the run page itself
    MarkIndex.load       -> 147,568 tick rows, to read the LAST one per ticker
    _latest_position_snapshots
                         ->  97,428 position rows, to read the newest per ticker
    incumbent paper leg  ->   4,009 all-time paper trades, rendered nowhere
```

The shape is the same in all three places: a query loads a whole history so the code
can keep one row per ticker, or loads evidence for a figure the route never shows.

The fix is per-route honesty about how much of the tape is needed:

```text
MARKS_FULL    every tick        only /series, which asks what a position was worth at
                                a past instant and is the one caller that can
MARKS_LATEST  one per ticker    the run page: it values what is open NOW
MARKS_NONE    none              the run list: realized P&L, position counts and the
                                open/closed split never touch a tick
```

And on the client, one selection at a time:

```text
selection -> GEN++            every load carries the generation it began under;
             AbortController  a response from an older generation is dropped
                              without touching state or the DOM, and its request
                              is aborted rather than left holding a connection.

phase 1  /api/runs?view=selector   which pairs exist          -> the picker works
phase 2  /api/runs/<tag>           this pair's own numbers    -> top of the page
phase 3  /series /orders /events   in parallel                -> the rest
phase 4  /api/runs                 the all-runs table         -> nothing waits on it
```

## Decisions Made

- **The run list loads no marks.** It never displayed an unrealized figure; reading
  670k tick rows to compute one it throws away was pure cost. Realized P&L, the
  open/closed split and the market count come from settlements and snapshots.
- **`mark_as_of` refuses a latest-only index** rather than answering from the single
  mark it holds. Returning today's price for a past instant is precisely the failure
  the method exists to prevent, so a wrong scope is an error, not a silent answer.
- **The URL records a running pair only.** `?run=` is a link to share, not a bookmark
  of the last thing clicked. Writing a retired pair into it is what pinned the page to
  a dead book across every later visit. A retired pair still opens from the dropdown,
  and a pasted `?run=<retired tag>` still works for the visit that asked for it.
- **A retired pair on show ticks the "retired" box** instead of being slipped into a
  list of running ones. The old exception for "whatever is currently selected" made the
  page assert the run was current in the exact place an operator looks to check.
- **The 60s timer refreshes in place.** It used to re-run the whole selection, silently
  resetting the market being viewed, the order page scrolled to and the timeline
  expanded. It now updates the figures and charts, skips a hidden tab, and never starts
  on top of a load already in flight.
- **The incumbent's all-time paper leg is opt-in** (`?incumbent=1`), fetched when the
  provenance section is opened. It was computed on every load and rendered nowhere.

## Open Decisions

- **D1.** Should `/api/runs` summaries be cached server-side (say 60s)? Every viewer
  currently pays the full rebuild. Deferring the table means nobody waits on it, so
  this is no longer urgent — but a retired pair's numbers cannot change, and rebuilding
  fourteen frozen runs on every page load is work that will never produce a different
  answer. Recommendation: revisit only if the table is still visibly slow after this.
- ~~**D3.**~~ **RESOLVED AND VALIDATED 2026-08-29.** The secret is set, `"livedash"` is a
  targetable service, and a real request round-tripped (`dash-logs-1`), returning the
  deployment status and the service's startup lines:

  ```text
  # latest deployment 10a915dc… (status=SUCCESS, created=2026-08-29T11:45:06.976Z)
  # target service: livedash
  11:46:05  INFO  Starting Container
  11:46:06  INFO  livedash listening on http://0.0.0.0:8080
  ```

  That deployment is #273's own merge, so the receipt doubles as the first deploy check
  the dashboard has ever had. Original statement:
- **D3.** The **livedash service is not reachable from the ops channel.** `env` and `logs`
  requests resolve a service id from a secret, and only two exist — `RAILWAY_SERVICE_ID`
  (main/live) and `RAILWAY_EVO_SERVICE_ID` (evo). So no Claude session can check whether a
  livedash deploy succeeded, read its startup logs, or see it crash-looping; the only
  signal is an operator opening the page. Wiring a `RAILWAY_LIVEDASH_SERVICE_ID` secret
  and adding `"livedash"` to `_SERVICE_ID_SECRET` in `scripts/ops_runner.py` would close
  it. It is small, but it is an ops-surface change and an operator decision, not something
  this workstream makes on its own. Recommendation: do it — this workstream's own closing
  check is blocked on its absence.
- **D2.** Should ended pairs be pruned from the picker entirely after some age, rather
  than only hidden behind the checkbox? 14 retired and growing by one per parameter
  change. Recommendation: no — the history table is where they belong and it lists all
  of them; the checkbox is enough.

## Assumptions

- The operator reads this page top-down: which pair, then its position and comparison,
  then charts, then orders and the timeline, then the all-runs table. The load order
  follows that and is wrong if the reading order is different.
- `positions` and `mmsell_position_ticks` keep growing without retention, so every
  read that scans a whole history gets slower each week. The fixes here are per-row
  reductions, not tuning, and hold as the tables grow.
- Trading behaviour is unchanged by all of this: the dashboard is read-only by
  construction (`do_GET`/`do_HEAD` only, no writes in the package) and nothing here
  touches that.

## Non-Goals

- Any change to what a number MEANS. Pairing stays the explicit `live_paper_twins`
  epoch row, both legs stay scoped to `started_at`, and both sides stay marked off the
  same tick. This workstream changes what gets loaded and when, not the accounting.
- Retention or archival of `mmsell_position_ticks` / `positions`.
- The evo dashboard (`kalshi_bot/dashboard`), which is a separate service.

## Build Card

Inline; the change is bounded and its shape is the mental model above.

- `livedash/marks.py` — `MarkIndex.load_latest`, scope reporting, `mark_as_of` guard
- `livedash/legs.py` — newest-per-ticker snapshot cut moved into SQL
- `livedash/data.py` — `MARKS_FULL/LATEST/NONE`; `build_runs(summaries=…)`;
  `build_run(incumbent=…)`
- `livedash/server.py` — `?view=selector`, `?incumbent=1`
- `livedash/static/index.html` — generation guard, phased load, per-section skeletons
  and errors, retired-pair rules, in-place refresh

## Implementation State

Merged — [#271](https://github.com/50thycal/kalshi_bot/pull/271); the handoff is its body.
What remains is verification on the deployed service, not code.

## Review State

CI green on `ccc383e` — lint, migrations applied on real Postgres, the full suite, and the
exactly-once Postgres claim race (which CI fails on a skip, so it genuinely ran). No review
threads were raised. Merged 2026-08-29.

## Related Decisions

`DEC-001` (the authority boundary) applies in the negative: this is development state
only. Nothing here registers, arms, promotes, pauses or retires anything, and no
lifecycle state, gate, verdict, epoch, Version or exposure changes as a result. The
retired pair this workstream stops the dashboard from opening on was retired in
Experiment OS; the dashboard merely stopped saying otherwise.

## A commit on `ops` that is not in the default branch's history

Actions loads the runner workflow from the branch that triggered it, so the
`RAILWAY_LIVEDASH_SERVICE_ID` passthrough had to land on **`ops`** as well as on the
default branch. That is commit `c35ad24` on `ops`, pushed 2026-08-29 under the runbook's
pre-flight (channel idle at `{"type":"noop"}`, no run in flight, backup ref
`ops-backup-20260829T114525Z` at `6e57552`).

It is recorded here because a transport commit with no counterpart in the default
branch's history is exactly the shape of drift XOS-000005 was: the two copies of that
workflow are meant to stay identical, and nothing in `git log` on the default branch
says this one moved. It was **additive** — an ordinary fast-forward commit, not a
rewrite — so `ops-transport-guard` was never lifted and no request or result was
disturbed. The force-with-lease procedure in the runbook remains for rewrites only.

`tests/test_ops_runner_freshness.py` now asserts that every service in
`_SERVICE_ID_SECRET` has a matching workflow `env:` passthrough, so the halves cannot
silently drift apart again — though that test reads the default branch's copy, and a
future divergence between the two copies is still only caught by the channel failing.

## Related PRs

- [#271](https://github.com/50thycal/kalshi_bot/pull/271)

## Observed on the deployed page (2026-08-29)

The operator opened the dashboard. Two defects, both introduced or left standing by the
first pass, and both since fixed and verified against a real browser at 390x844:

1. **It opened listing all fourteen retired pairs.** Two independent causes, either
   sufficient on its own. The operator's URL still carried a `?run=` naming a retired
   pair — written by the *older* build, which recorded every selection — and the first
   pass honoured an explicit `?run=` above a running pair, treating it as a deliberate
   link when it was inherited state. Landing on a retired pair then ticked the "retired"
   box, because the picker must not misdescribe what it is showing. Separately, browsers
   restore checkbox state across a reload, so the box came back ticked from a previous
   visit regardless. Now: a running pair wins outright, the box is forced off at boot,
   and a finished run opens only via `?run=<tag>&retired=1`, which nothing writes
   automatically.
2. **The sticky header pinned about half a phone screen.** It carried the tags, the run
   state and the whole provenance line, all of which wrap heavily at phone width and are
   read once rather than navigated with. Those moved below the header; the sticky bar now
   holds the run picker and reload only, and does not stick at all below 700px. Measured
   after: 57px of an 844px viewport, `position: static`, and it scrolls away.

The first pass was never checked in a browser — the tests asserted on source strings —
which is exactly why both survived it. Verification now drives a real Chromium against a
fixture shaped like production (one open pair, fourteen ended).

## Next Step

One item left: confirm on the deployed livedash that first paint is seconds rather than
half a minute. It needs an operator or a browser pointed at the public URL — the ops
channel can now report the service's health and startup, but not how long a page takes
to paint. Everything else in this workstream is verified: the selection and layout
faults in a real browser, the read-cost reductions by measurement, and D3 by receipt.
