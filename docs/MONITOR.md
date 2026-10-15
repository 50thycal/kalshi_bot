# Global experiment monitor (WS-026)

One place that answers "is everything that should be running actually running,
and what needs me?". It covers every Experiment OS experiment plus the systems
that run outside it. It is a **view**: Experiment OS stays canonical (`DEC-001`),
nothing here writes, and a health label changes no lifecycle state.

```text
  daily routine (Claude, 07:45 America/Chicago)        on demand: same skill
        │  one ops request, unique id
        ▼
  xos monitor-snapshot  ──►  control_tower.build_report   (lifecycle, gates, blocked,
        │                                                  silent arms, collectors,
        │                                                  issues, candidates, paper $)
        ├──► per-arm last evidence + entries 24h/7d   (paper_trades / live buys)
        ├──► live_realized_pnl_usd per live arm        (canonical metric provider)
        ├──► expect contract                           (Version → package → default)
        └──► sources.SOURCES probes                    (worker, incentive poll, desks, catalog)
        │
        ▼  JSON on ops/results/<id>.txt
  python -m kalshi_bot.monitor.page  ──►  dashboards/status.html + embedded snapshot
        │                                 └─► private Artifact, same URL every day
        └──► notify? (broken today, or new since the page last published)
                 yes → five-line brief to the owner     no → silent
```

## The snapshot contract (`monitor-snapshot/1`)

`kalshi_bot/monitor/snapshot.py`. `build_snapshot` reads; `assemble` is pure and
is what the tests drive.

| field | what |
|---|---|
| `summary` | active / paused / blocked (experiments with a blocked gate) / closed (Tower's 7-day window + all-time) / live vs paper / realized: paper 30d (the Tower's number), live 30d (sum of `live_realized_pnl_usd`), the Tower's gap (paper basis), live % of $100 / needs-you count / open issues / untracked problems / unhealthy external systems |
| `experiments[]` | key, stage, kind, tags, **health**, reasons, last evidence, entries 24h/7d, settled, paper realized this epoch, live realized epoch/30d, exposure, gates (recorded + dry run + floor progress), next verdict estimate, open issue ids, owning role, effective `expect` and its source |
| `external[]` | registered sources + the Tower's collectors, each with health, last seen, detail |
| `needs_you[]` | the Tower's operator-tier lines (recorded PASS not yet acted on, horizon exhausted) + `ACTION_REQUIRED` issues + candidates needing operator triage |
| `issues[]`, `untracked[]` | open XOS issues; detected problems no issue covers (with fingerprint and recommended owner) |
| `details[]` | the short "worth knowing" list |

### Health

Worst wins: `ERROR` > `SILENT` > `STALE` > `BLOCKED` > `UNKNOWN` > `OK`; `PAUSED` and
`IDLE` (idea/probe) are neutral.

- **ERROR** an unresolved integrity event (not a recorded stand-down), a gate that
  failed to evaluate, or an activity metric that cannot be answered.
- **SILENT** the Control Tower's own armed-but-silent finding (zero evidence ever).
- **STALE** an arm that *has* traded but has had no new evidence for longer than its
  `max_silence_hours`, or averages under `min_entries_per_day`. This is the case
  the Tower cannot see: a book that worked and then stopped.
- **BLOCKED** at least one gate in the Tower's blocked list.

A recorded stand-down suppresses STALE, the same way it suppresses the Tower's
silent-arm detector.

The "next verdict" date is a **linear estimate** from the dry run's sample-floor
progress. It is a reading aid. It is never a promise, and HOLD on a thin sample
stays correct whatever it says.

## How experiments "report to it": the `expect` contract

Experiments do not push heartbeats. They **declare** what normal looks like
(`kalshi_bot/monitor/expectations.py`):

```python
MONITOR_EXPECT = {"max_silence_hours": 24}            # package module constant
monitoring={"expect": MONITOR_EXPECT, ...}            # NEW packages: also freeze it on the Version
```

Resolution: the Version's `monitoring_json["expect"]` → the package's
`MONITOR_EXPECT` → default (`SILENT_ARM_HOURS`). Frozen Versions are never
rewritten to add it. `tests/test_monitor.py` fails if a package that creates an
experiment declares no valid `MONITOR_EXPECT`. Legacy-imported experiments with no
package get the default. Keys: `max_silence_hours`, `min_entries_per_day`, `note`.

## Systems outside Experiment OS

`kalshi_bot/monitor/sources.py` → `SOURCES`. To add one, append a `Source(...)`
with a read-only probe that returns `health`, `last_seen` and `detail`, and never
raises. Probes print into the public ops result, so they return codes, counts and
timestamps only.

| source | probe |
|---|---|
| worker | newest `bot_runs.started_at` (same signal as the 15-minute heartbeat workflow); 30 min |
| incentive-programs | newest `incentive_programs.last_seen_at`; 6 h |
| desks | the desk service's sanitized `/api/diagnostics`, re-sanitized by `scripts/desk_diag.py`. Read-only, outside XOS (`DEC-018`). UNKNOWN without `DESKS_DIAGNOSTIC_TOKEN` |
| market-catalog | `/health` at `MONITOR_CATALOG_URL`; UNKNOWN when unset (the catalog has its own volume and nothing in Postgres) |
| collectors | the Control Tower's `data_health`, verbatim (INACTIVE stays informational) |

## The page

`dashboards/status.html` is one self-contained file with no build step. **The top
table is the `SUMMARY` list at the very top of the file.** Add, drop or reorder a
row by editing that list only; `tests/test_monitor.py` pins it.
`python -m kalshi_bot.monitor.page` embeds the snapshot and writes the rendered
page. The previous page carries the previous snapshot, which is how "new since
yesterday" is computed with no second store. History: every snapshot stays in the
`ops` branch's git history, and every publish is an Artifact version.

`--tower <control-tower --json result>` is the fallback when `monitor-snapshot` is
unavailable. The page is then marked PARTIAL: per-arm evidence, live realized and
external probes were not read.

## When the owner is messaged

`page.attention`:

- **Every day until fixed:** an experiment ERROR, SILENT or STALE; an external
  system ERROR or STALE.
- **Only when new:** an experiment newly BLOCKED, a new needs-you line, a new
  open issue, a newly detected untracked problem (by fingerprint), or a sample
  floor estimated within 2 days.

Chronic conditions stay on the page and out of the message.

## What it never does

No env changes, no lifecycle moves, no gate verdicts, no arming, no exposure, no
writable Postgres path, no change to the ops workflow file. **It does not open
issues.** Opening one is an `EXPERIMENT_OS_ISSUE_COMMAND` env write that redeploys
the live worker. The owner decided (WS-026) that the routine *prepares* the
`OPEN_CANDIDATE` list, routed to the owning role, and never sends it.

## Running it

- Daily: the Claude routine runs `.claude/skills/daily-checkin/SKILL.md`.
- On demand: say "run the daily check-in", or run the skill.
- By hand: `{"type":"xos","command":"monitor-snapshot","id":"mon-<stamp>"}` on the
  ops channel, then `python -m kalshi_bot.monitor.page --snapshot <result> --out page.html`.
