# Research calendar — scheduled check-ins

**What this is:** one page that shows when each scheduled research check runs, what it reads, and
how to find it. It is a pointer. The verdicts live in the linked docs and in Experiment OS. This page
authorizes nothing.

**How they run:** each check is a Claude **Routine** stored on the server, so it does not depend on
any chat session staying open. At the time shown it starts a **fresh cloud session**. That session
attaches the repo, runs the pre-registered command through the ops channel, writes the result, opens
and merges a docs PR, and sends a push notification. You can see or cancel them in the Claude app,
under Routines (names start with `Kalshi:`).

**Rule for the session that runs a check:** mark its row `DONE — <verdict>` here in the same PR as
the result. If the check schedules a follow-up, add a row for it.

Times are UTC (US Central is UTC−6 after 2026-11-01).

## Scheduled research checks

| date (UTC) | check | what it decides | pre-registration | routine | status |
|---|---|---|---|---|---|
| 2026-11-13 15:17 | LIMM exits vs holding, run 3 | Do the liquidity book's stop/take exits beat holding to settlement? Needs ≥ 30 settled markets and ≥ 15 events, else HOLD. A FAIL makes a looser stop an owner decision. Also checks the shadow collector is still writing. | `LIMM_PROGRAMME_OUTCOMES.md` §9 (results §11) | `trig_01MtzLc4H1BoASXHY2AeePSJ` | scheduled |
| 2026-12-04 15:07 | P-SLOW v2 fill read (read once) | Do slow-information MMSELL markets still earn ≥ +2¢ on real fills? Needs ≥ 100 fills. A PASS makes sizing above 3 contracts an owner decision. | `MMSELL_FILL_FORWARD_READ.md` §6 | `trig_018N8ZxREHNrgABVh2C2HCxm` | scheduled |
| 2026-12-07 15:23 | State gas ladder screen, re-run | Does the 7-state gas ladder family reach CANDIDATE (≥ 100 entries, t ≥ 3.0, both halves positive)? A CANDIDATE leads to a taker-price read, not a paper book. | `MMSELL_LOOKALIKE_PROBE.md` §16 (first result §17) | `trig_01N3tddvLTSEY2jebghEz9UA` | scheduled |

## Conditional follow-ups (added only if triggered)

| when | follow-up | condition |
|---|---|---|
| 2027-01-08 | P-SLOW v2 extension, read once, same bars | the 2026-12-04 read has fewer than 100 cell fills |
| +2 weeks after each LIMM read | LIMM exits vs holding, next run | the read is still under the 30-market / 15-event floor |

## Other standing routines (operations, not this research)

These are owned by other sessions and listed here so the full schedule is visible in one place. Their
own prompts and docs are canonical.

| cadence | routine | owner doc |
|---|---|---|
| every 2 h | LIMM check-in (Alimm1 positions, fills, rewards) | `docs/liquidity_incentive/` |
| every 72 h | LIMM scorecard and trend review | `docs/liquidity_incentive/scorecards/README.md` |
| every 72 h | MMSELL scorecard and trend review | `docs/mmsell/scorecards/README.md` |
| daily 08:57 Central | Railway usage check (read-only) | WS-024 |
| 2026-11-14 16:32 | Mentions forward read (progress toward 60 trades) | `MMSELL_LIVE_CANARY_PATTERNS.md` §6 |

The operations routines above wake an existing session rather than starting a fresh one. They also
run while that session is closed in the app, but stop if that session is archived.
