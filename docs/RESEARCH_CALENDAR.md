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
| 2026-11-13 15:47 | LIMM fill hazard, study 5 (read once) | Which incentive series pay net of the fills a resting bid takes? Nominates series to add (net above the live universe) or remove (reward eaten by fills). Needs ≥ 3 series with ≥ 20 market-days and ≥ 5 events, else HOLD and one re-read on 2026-11-27. Nominations are owner decisions. | `LIMM_FILL_HAZARD.md` §4 | `trig_015Lh4zakJj9qZQ23Hbk45mM` | scheduled |
| 2026-11-17 16:17 | LIMM step-aside, study 6, Part B (read once) | In the 48h→24h band before close, does a cheap-side bid earn more reward than its fills cost? NOMINATE 24H (owner decision), KEEP 48H, or HOLD. Needs ≥ 20 live-universe band markets in ≥ 8 events, else one re-read on 2026-12-01. Part A (backtest, 2026-11-03): live HOLD (accrual); `all` band +$1.48/md vs +$0.43 base, descriptive. | `LIMM_STEP_ASIDE.md` §4–§5 | `trig_01H9kxG5d74PesCBcfbyGYkw` | scheduled |
| 2026-12-11 16:37 | LIMM fill early-warning, study 8, Part B (read once) | Did a snapshot signal (a print in the last 5 min, or depth at best halving) predict a fill in the next hour, and would pulling the bid then have saved more than it gave up? USEFUL nominates a pull-on-warning rule (owner decision). Needs ≥ 30 fills in ≥ 10 events. Part A (backtest, 2026-11-04): NO-SIGNAL — warning predicts fills (lift 37) but pulling bids loses more reward than it saves (−$330). | `LIMM_FILL_WARNING.md` §3–§4 | `trig_018fN69FNib5J6xZUihqbmE8` | scheduled |
| 2026-12-03 16:47 | LIMM mid-band probe, study 9, Part B (read once) | Do $10 incentive bids where both sides bid 11–50¢ earn more estimated reward than their fills cost (CHEAP, DEAR or BOTH sides)? PROMISING (≥ $0.15 per leg-day, LCB > 0) makes a mid-band live trial an owner decision. Needs ≥ 100 leg-days in ≥ 10 events. Part A (backtest, 2026-11-08): NO-LEAD on all three policies (CHEAP +$0.07/leg-day, DEAR −$0.04, BOTH +$0.01; negative under the optimistic model). | `LIMM_BAND_PROBE.md` §4–§5 | `trig_01GNyKAr66gJ8pPBaaSspKyC` | CANCELLED 2026-11-08 (owner, DEC-043): Part A was NO-LEAD on all policies; routine deleted |
| 2026-11-24 18:23 | LIMM paper strategies S1–S4, read once | Do the four incentive paper strategies (seven arms) earn more calibrated reward, after Kalshi's $1 minimum, than their simulated fills cost? PROMISING ≥ $0.15 net per $10 bid-day with LCB > 0 (conservative fills; whole-order must not turn it into a loss); pairwise S2/S3/S4 DECIDE when the 95% CI excludes 0. Floors 100 bid-days and 10 events per arm, else HOLD and one re-read 2026-12-08. A PROMISING arm is a nomination (owner decision). Recording from 2026-11-09T00:00Z; Experiment OS `limm-paper-strategies` (PROBE, tagless). | `LIMM_PAPER_STRATEGIES.md` §5–§6 | `trig_01WY5w1cwuAEkKULvzsTxaLP` | scheduled |
| 2026-11-14 18:37 | LIMM paper strategies backtest, run 2 (S2–S4 on full history, descriptive) | Replays the forward arms' rules on 2026-09-08 → 11-08 15:00Z, after study 5 has read. Labels and pairwise are descriptive and move no forward bar. Run 1 (to 10-30) ran 2026-11-08. | `LIMM_PAPER_STRATEGIES_BACKTEST.md` §4 | `trig_01BBhvtXE1hNYfSQpD84BwQe` | scheduled |
| 2026-11-08 14:51 | LIMM programme-age study, read once on existing history | Does a programme pay more per contract in its first hours (thesis §9.42)? Sets S4's burst window. DEC-044. | `LIMM_PROGRAMME_AGE.md` §4 (result §9) | run by the build session (`rl-age-run1-1108`) | DONE — NOT-EARLY (within-programme R 1.23, 95% CI 0.85–1.63; late hours still pay $0.31/$10 bid-day): S4 has no burst window and drops to $10 after the burst |
| 2026-12-04 15:07 | P-SLOW v2 fill read (read once) | Do slow-information MMSELL markets still earn ≥ +2¢ on real fills? Needs ≥ 100 fills. A PASS makes sizing above 3 contracts an owner decision. | `MMSELL_FILL_FORWARD_READ.md` §6 | `trig_018N8ZxREHNrgABVh2C2HCxm` | scheduled |
| 2026-12-07 15:23 | State gas ladder screen, re-run | Does the 7-state gas ladder family reach CANDIDATE (≥ 100 entries, t ≥ 3.0, both halves positive)? A CANDIDATE leads to a taker-price read, not a paper book. | `MMSELL_LOOKALIKE_PROBE.md` §16 (first result §17) | `trig_01N3tddvLTSEY2jebghEz9UA` | scheduled |

## Conditional follow-ups (added only if triggered)

| when | follow-up | condition |
|---|---|---|
| 2027-01-08 | P-SLOW v2 extension, read once, same bars | the 2026-12-04 read has fewer than 100 cell fills |
| +2 weeks after each LIMM read | LIMM exits vs holding, next run | the read is still under the 30-market / 15-event floor |
| 2026-12-01 16:17 | LIMM step-aside, re-read once, same bars | the 2026-11-17 read is HOLD (accrual) |
| 2026-11-27 16:07 | LIMM fill hazard, re-read once, same bars | the 2026-11-13 read is HOLD under V0 (fewer than 3 series at the floor) |

## Other standing routines (operations, not this research)

These are owned by other sessions and listed here so the full schedule is visible in one place. Their
own prompts and docs are canonical.

| cadence | routine | owner doc |
|---|---|---|
| every 2 h | LIMM check-in (Alimm1 positions, fills, rewards) | `docs/liquidity_incentive/` |
| every 72 h (from 2026-11-01) | Research review (72h): which experiments are done or need action, close-out candidates, and whether to start one new experiment per line (caps: 10 incentive, 20 MMSELL). Covers both lines. Latest: `research_reviews/2026-11-07.md`. Routine `trig_011bcVM8wWmtqMgLUoWGhLPg` | `docs/RESEARCH_REVIEW.md` |
| every 4 h (01:13, 05:13, 09:13, 13:13, 17:13, 21:13 Central) | Research check-in (4h): read-only progress and health on the checks above (collector, LIMM and P-SLOW accrual counts, live tags, these routines). Counts only, never an early read. Routine `trig_013hWGXAquPc28pjj8AFu1jX` | `docs/handoffs/HANDOFF-research-checkin.md` |
| every 72 h | LIMM scorecard and trend review | `docs/liquidity_incentive/scorecards/README.md` |
| every 72 h | MMSELL scorecard and trend review | `docs/mmsell/scorecards/README.md` |
| daily 08:57 Central | Railway usage check (read-only) | WS-024 |
| 2026-11-14 16:32 | Mentions forward read (progress toward 60 trades). Sample: `mmsell10` through 2026-11-02, then `Basemmsell10` (DEC-041). Fresh-session routine `trig_01WsVZJfPKbYRDn9eFgVwMYn`; the original routine's session is archived | `MMSELL_LIVE_CANARY_PATTERNS.md` §6 |

The operations routines above wake an existing session rather than starting a fresh one. They also
run while that session is closed in the app, but stop if that session is archived.
