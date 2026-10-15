# WS-026 — Global experiment monitor, daily check-in and status page

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-10-15
**Updated:** 2026-10-15
**Build OS:** v0.12

## Goal

One place that tracks every running experiment (mmsell paper books, live canaries, the
incentive smoke, the autonomous desks, the collectors). A daily check-in confirms each one is
actually running and tells the owner only when something needs them. The output is an
interactive status page: a short, editable top table, then the detail.

## Context

Owner request (2026-10-15), via a handoff from a chat session that left no branch. The Control
Tower already computes lifecycle, gates, silent arms and the paper north-star number, but only
when someone asks. Nothing watches the systems outside Experiment OS. Nothing notices a book
that traded and then stopped: the Tower's silent-arm rule fires only on zero evidence ever.

## Current Mental Model

See `docs/MONITOR.md` (diagram, contract, health rules, notify rule). In short: one read-only
`xos monitor-snapshot` = `control_tower.build_report` + per-arm last evidence + canonical live
realized + the `expect` contract + `monitor/sources.py` probes. Then `monitor/page.py` renders
`dashboards/status.html` into a private Artifact (same URL every day) and decides whether to
message the owner.

## Decisions Made

- **Check-in time → 07:45 America/Chicago daily** (owner, 2026-10-15), plus on demand.
- **Board slot → WS-020 pauses** (owner, 2026-10-15). Its shadow has been stopped since
  2026-10-07. The live limm-smoke keeps running under XOS, and the monitor watches it.
- **Page → private claude.ai Artifact** (owner, 2026-10-15). No new Railway service, no RAM
  (WS-024).
- **Issues → prepare only** (owner, 2026-10-15). The routine lists new untracked problems with
  their owning role and fingerprint, and never sends `EXPERIMENT_OS_ISSUE_COMMAND`. That write
  redeploys the live worker and belongs to the owning role. `DEC-036`.
- **`expect` lives on the package, not by rewriting Versions.** Frozen Versions stay frozen. New
  packages also freeze `expect` into `monitoring`. `DEC-036`.

## Open Decisions

- **D1.** Catalog liveness needs `MONITOR_CATALOG_URL`. The ops workflow file is a hard stop, so
  nothing passes that variable today and the catalog reads UNKNOWN. Options: leave it UNKNOWN,
  hard-code the public `/health` URL in `sources.py` (like the desk default), or set the
  variable through the ops maintenance procedure. Recommendation: hard-code the URL once the
  owner names it.

## Assumptions

- The ops runner keeps passing `DESKS_DIAGNOSTIC_TOKEN` to every request (it does today), so the
  desks probe works inside `monitor-snapshot`.
- `incentive_programs.last_seen_at` is still refreshed by a running poll. If it is not, the
  monitor reads STALE, which is the right answer.

## Non-Goals

- No auto-fix of any kind: env, lifecycle, gates, arming, exposure.
- No issue writes. No new Railway service. No change to the ops workflow file.
- No desk lifecycle under XOS (`DEC-018`). No copy of standings into docs.

## Acceptance Checks

1. The snapshot lists every active XOS experiment and every registered external source.
   A test fails if an active experiment is missing.
2. A silent paper arm (fixture) is SILENT and a blocked gate is BLOCKED.
3. The top-table numbers match `xos control-tower` for the same report: active, blocked,
   retired, paper realized 30d, gap to $100.
4. Changing one entry in the HTML `SUMMARY` config changes the top table, with no other edits.
5. The routine runs once end to end. It publishes the page, and posts a brief only when there
   is something to act on.
6. Coverage includes Tempmmsell1/2/3, Rainmmsell1/2/3, the live mmsell10 canaries, limm-smoke,
   the XOS-000047 forward tests (an open issue on the page), and the autonomous desks.

Checks 1–4 are tests in `tests/test_monitor.py`. Check 6 was verified against production
control-tower output on 2026-10-15. Check 5 needs the merge: the ops channel runs
default-branch code.

## Build Card

Inline in the PR. The design follows the handoff (A snapshot, B expect contract, C page,
D routine), with the deviations listed in the PR.

## Implementation State

PR open on `claude/global-monitor-ws026`. Bootstrap page published from production
control-tower output (fallback mode). Daily routine created; it is inert until the merge.

## Review State

**Verdict:** Not started
**Accepted head:** —
**Reviewed PR:** —
**Finalization:** —

`solo` mode: no independent party has examined this change.

## Related Decisions

DEC-001, DEC-011, DEC-012, DEC-018, DEC-036.

## Related PRs

[#637](https://github.com/50thycal/kalshi_bot/pull/637)

## Parked

None.

## Next Step

Owner merges the PR. Then the next 07:45 CT routine run (or "run the daily check-in") produces
the first full snapshot, which is acceptance check 5.
