# Evo verification loop — operating procedure

An automated **test → fix → verify** loop that keeps the evolutionary agent system
operating to spec without a human babysitting it. It runs the verification plan
(the 30 checks reviewed with the operator) on a cadence, fixes what it can, and
reports.

## The two engines (run every pass)

1. **Correctness engine — fast, deterministic, no cost.**
   ```bash
   python -m pytest -q
   python scripts/evo_simulation.py --seed 42 --cohorts 5
   ```
   Proves every mechanic and the whole selection loop (retire / reproduce /
   wildcard, adversaries going extinct) in ~1 minute. Covers the `unit` and `sim`
   checks in the plan — including Tier 6 selection — without waiting on live trades.

2. **Live-health engine — the deployed system, via the ops channel.**
   ```jsonc
   {"type": "script", "name": "evo_selftest", "id": "selftest-<n>"}
   ```
   `scripts/evo_selftest.py` fires the production probes and prints a per-check
   verdict — `PASS` / `NOT_YET` / `BROKEN` / `INFO` — with a summary line and a
   non-zero exit if anything is `BROKEN`. `NOT_YET` means healthy-but-not-yet-
   exercised (e.g. no trades yet); it is tracked, not fixed.

## One iteration

1. **Correctness engine.** Run pytest + the simulation. Any failure is a code
   regression → fix it (this is always in-scope) before touching production.
2. **Live-health engine.** Run `evo_selftest` via ops; read the digest's
   `degraded by reason` line for context.
3. **Triage.** For each check:
   - `PASS` → note it.
   - `NOT_YET` → record what it's waiting on (usually "first trades" or "first
     cohort boundary"); do not fix.
   - `BROKEN` → diagnose. If it's a **code** bug, reproduce it in a failing test
     first, then fix (§ scope below). If it's **operational** (billing, an env
     var, a missing redeploy), surface it to the operator — don't guess at infra.
4. **Ship the fix.** Branch → commit → push → open PR → wait for CI.
5. **Self-merge on green** (see scope), let Railway redeploy, then **re-run the
   failing probe** to confirm the fix landed live. A fix isn't done until the
   live probe flips to `PASS`.
6. **Report** a short summary of the pass (what was checked, what changed, what's
   still `NOT_YET`).

## Autonomous-merge scope (operator-approved)

The loop may merge its own PR via the GitHub API **only when both hold**:
- CI is green, and
- the diff is confined to **`kalshi_bot/evo/**`, `kalshi_bot/dashboard/**`,
  `scripts/evo_*`, `docs/EVO_*`, and their tests** (plus a one-line `ops_runner`
  allowlist entry for a new evo probe).

Anything wider, anything **architectural**, and — always — anything touching the
**real-order guard or the paper-only boundary** stops and asks the operator first.
Every autonomous change ships behind the correctness gate above; the loop never
merges red.

## What it must never do

- Never place, enable, or reference a real-money order path. The system is
  paper-only; a probe (`PAP-4`) and a unit test guard this, and the loop treats
  any breach as stop-the-line.
- Never widen scope silently. Out-of-scope fixes are proposed to the operator,
  not merged.
- Never "fix" a `NOT_YET` by faking activity — it waits for the system to reach
  that stage naturally.

## Scheduling

Runs on a recurring trigger (every few hours is plenty — cohorts move on the
scale of days). Each firing executes one iteration against the latest deploy and
re-arms. Pause by disabling the trigger; nothing is lost between passes since all
state lives in the database and the repo.
