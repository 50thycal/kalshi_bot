# Handoff — build four incentive paper strategies (S1–S4) and the programme-age study

- **From:** two Research Lab sessions on 2026-11-08:
  - the ranker and check-in session, which wrote this file;
  - the Alimm1 session, which stood the live book down. Its leads are in
    `docs/liquidity_incentive/HANDOFF_2026-11-08.md` (PR #767) and are merged in here.
- **To:** a new **Research Lab** session that builds and starts all of it.
- **Owner approval:** DEC-043 and DEC-044 (`docs/DECISIONS.md`). Everything below is approved
  research scope. The only hard stops are listed in §7.
- **Goal:** within about two days:
  - **(a)** four paper strategies with seven arms are recording simulated resting bids in live
    incentive markets. Each hour they record the estimated reward, whether the bid would have
    been filled and what a fill costs.
  - **(b)** the programme-age study has run once on existing history.
  - The paper strategies are read once around 2026-11-24, against bars fixed before any data.
- **Context: the live book is off.** The owner stood `Alimm1` down on 2026-11-08 to survey all
  markets (thesis §9.78, `LIQUIDITY_INCENTIVE_LIVE_ENABLED=false`). If it restarts, it runs the
  §9.77 rules: paid ¢/h per market, enter at ≥ 5¢, pull below 3¢, $1.50 projected. This work is
  the survey that decides **where and how** it should quote next. The restart and any rule
  change are owner decisions.

## 1. Read first (in this order)

1. `CLAUDE.md` (the session role, the ops recipe, and that Experiment OS is canonical).
2. `docs/LIMM_MARKET_RANKER.md`: the measure, the 7 build cycles and the coverage finding. Run
   it once: `{"type":"script","name":"limm_market_ranker","args":["--json"],"id":"<unique>"}`.
3. `docs/liquidity_incentive/HANDOFF_2026-11-08.md`: the stand-down state and the five leads,
   with their evidence:
   - the threshold study in `tools/review/limm/study/`;
   - the calibration in `scripts/limm_estimate_calibration.py`;
   - fills arrive about 1 per 150–160 resting market-hours and usually take the **whole** order.
4. `docs/LIMM_BAND_PROBE.md` (study 9, NO-LEAD above 10¢): why the strategies stay on the cheap
   side.
5. `kalshi_bot/liquidity_incentive/`:
   - `collector.py`: the GET-only shadow engine;
   - `scoring.py`: the LIP rules and `estimate()`;
   - `fills.py`: `ShadowLeg`;
   - `quotes.py`: the policies;
   - `live.py`: the §9.77 constants and the $1-minimum notes.
6. `docs/LIQUIDITY_INCENTIVE_THESIS.md`:
   - §9.42 and §9.49: programme age and the $1 minimum;
   - §9.52: why a `paper_trades` twin is invalid here;
   - §9.72–§9.78: the live rules and the stand-down.
7. Owner preference: brief, plain, non-technical messages.

## 2. What the owner locked

| Ref | Decision |
|---|---|
| DEC-043.1 | Build **S1, S2 and S3** as below. Fold the late-exit rules for one-event ladders into S3's step-aside arm. |
| DEC-043.2 | The collector **picks markets by daily pay**, not by total programme reward. |
| DEC-043.3 | **Size each strategy for its job** (guardrails in §4). |
| DEC-043.5 | **Read once around 2026-11-24**: the later of 11-24 or 14 days after recording starts. |
| DEC-044.1 | Add **S4 burst sizing**. It merges the stand-down session's leads 1 (burst) and 5 (the $1-minimum harvest). |
| DEC-044.2 | Add the **programme-age study** (lead 2) as a one-time read of existing history. It runs **before** S4's timing is pre-registered. |
| DEC-044.3 | Leads 3 (one yardstick) and 4 (per-type calibration) become **ranker cycle 8**, and the read script uses the same measure. They are instrumentation, not experiments. |
| DEC-044.4 | The incentive cap is **10** (it was 8). Study 9 Part B stays cancelled. |

## 3. What to build

### 3a. Paper strategies (one-sided resting bids, no real orders)

Every arm bids on the side the ranker picks, normally the cheap side at 10¢ or less.

| Strategy | Arm | Universe | Placement and size | Thesis |
|---|---|---|---|---|
| **S1 Weekly money-finder** | `S1` | The live programmes with the highest pool per day per market, mainly the short weeklies the collector skips today: shipping chokepoints, EO counts, Rotten Tomatoes, Trump act, AP POTUS. | At the best bid. | The ~$89k/day we cannot see includes uncrowded markets paying more per $ than anything we have traded. |
| **S2 At-best vs behind** | `S2-best` | The ranker's top quotable markets. Both arms use the same markets at the same hours. | At the best bid. | At the touch the score doubles (discount factor 5000). Do sweep fills eat that? |
| | `S2-behind` | Same as `S2-best`. | One tick behind (the §9.72 rule; a 1¢ bid stays at 1¢). | Control for `S2-best`. |
| **S3 Release-aware** | `S3-aside` | State employment ladders (`…EMP`), AAA gas, and one-event ladders (ski openings). | At the best bid. Pulled for verified releases: BLS from 2 h before to 48 h after (next **2026-11-20 15:00Z**); gas 12–16Z daily. One-event ladders: pulled when our side's bid falls to 1¢ after being higher, or in the last 3 days. | Fill losses cluster at releases and late certainty dumps. Stepping aside keeps most of the reward. |
| | `S3-control` | Same as `S3-aside`. | At the best bid, never pulled (only the common 48 h pre-close step-aside). | Control for `S3-aside`. |
| **S4 Burst** | `S4-burst` | The **$1-minimum harvest band**: short or thin programmes where a $10 bid would earn between about $0.30 and $1 over the programme (calibrated). Kalshi forfeits these today. Size the universe with the census in §5 step 0. | **$50** at the best bid until the arm's accrued calibrated reward in that market clears **$1.00 plus a margin**, set in the pre-registration. Then pull, or drop to $10 if the age study says late hours still pay. Burst timing comes from the age study. | Size only changes the outcome where it crosses the $1 minimum. A short burst early in a programme clears it and is out before fills arrive (about 1 per 150 h). |
| | `S4-flat` | Same markets and hours as `S4-burst`. | $10 at the best bid, the whole programme. | Control. Expect many $0 programmes under the $1 minimum. |

### 3b. Programme-age study (one read of existing history, no forward wait)

- **Question (lead 2, thesis §9.42):** does a programme pay more per contract in its first hours?
- **Method:**
  1. Extend `limm_estimate_calibration.py`, or add a sibling script, with programme-age buckets:
     0–6 h, 6–24 h, 1–3 d, 3 d+.
  2. In each bucket, measure the estimated reward per contract-hour from the collector's
     snapshots, and, where the live book's own snapshots and payouts allow, paid ÷ estimated.
  3. Compare the fill rate per resting market-hour in each bucket.
- **Pre-register it first.** Write a short `docs/LIMM_PROGRAMME_AGE.md` with the question, the
  buckets and the bar. Example bar: EARLY-PAYS if 0–24 h earns at least 2× per contract-hour
  versus 3 d+, with a 95% CI excluding 1, and fills per market-hour are no higher. Then run it
  once.
- **What it decides:** the result sets S4's burst window. If EARLY-PAYS, S4 bursts in the first
  24 h. If not, S4 bursts until the $1 threshold whenever it enters. Record which rule was used
  in S4's pre-registration **before** S4 starts recording.

### 3c. Ranker cycle 8: one yardstick and per-type calibration (leads 3 and 4)

- **Calibrated reward.** Show calibrated paid ¢/h = estimate × calibration:
  - per market type, where the type has 10 or more paid events;
  - otherwise the global median, 1.34.
  - Print which factor was used.
- **Net against fill cost.** Set paid ¢/h beside fill cost ¢/h per market type, ranked by the
  net:
  - mention events: about 3.1¢/h from the threshold study;
  - YouTube: from the same study;
  - other types: study 5's per-series fill cost after its **2026-11-13** read; until then,
    "unknown".
- **$1-minimum check.** Flag the markets whose projected calibrated reward to the step-aside is
  under $1 at $10. These are S4's universe.
- Update `docs/LIMM_MARKET_RANKER.md` §5 and §6. The calibration limit there is now partly
  answered by the stand-down session's study.

## 4. Sizing guardrails ("scale appropriately for each strategy")

Fix the exact numbers in the pre-registration before recording starts.

- **S2 and S3 (all four arms):** $10 per bid, the live book's size. At most 1,000 contracts, 3
  markets per event and 10 markets per arm at a time.
- **S1:** $20 per bid where the pool is at least $50/day, else $10. At most 20 markets and 3 per
  event; it is a discovery sweep.
- **S4-burst:** **$50** per bid during the burst, then $10 or off. At most 10 markets in a burst
  at a time and 3 per event. $50 is paper-only. **Live sizing above $10 a bid is a hard stop**
  (stand-down handoff).
- **Every arm, every hour:**
  - log our share of the field, and keep `snapshot_qualifies` true;
  - flag hours where we would be more than 50% of a side (study 3's thin rule: no reward
    credited);
  - S4 at $50 will hit that flag more often. Report it, don't hide it.

## 5. How to build it (recommended order)

0. **Census first (counts only, no outcomes).**
   - Count the live and recent programmes in the $1-minimum band (lead 5): how many, which
     series, and typical length.
   - If fewer than about 10 qualify per week, S4 runs on whatever qualifies, and the
     pre-registration says HOLD is likely. Do not widen the band after seeing results.
1. **Collector selection by daily pay** (DEC-043.2).
   - In `ShadowState._reconcile` (`collector.py`), sort by `period_reward_usd ÷ programme days`.
     Keep the pinned markets.
   - Keep `max_markets` at 150 at first. Raise it only after checking worker and database load.
   - Add a test. After deploy, confirm `rc-a1` (snapshots and markets per 4 h) is healthy and
     the ranker's "NOT WATCHED" pool has fallen.
2. **One-sided paper legs in the shadow engine.**
   - Add a new policy family (for example `quotes.ONE_SIDED_POLICIES`), or a small sibling
     module.
   - Reuse `ShadowLeg` for fills, and `scoring.estimate()` with the other side's size at 0.
   - Tag every row with the arm in `IncentiveShadowQuote.policy`.
   - Paired arms (S2, S3 and S4) quote the **same markets in the same hours**.
   - **Add a third fill model, "whole order":** any trade through our price fills the full
     resting size. Live evidence shows 24 of 30 large fills took 100% (lead 1). The
     conservative queue model would understate S4's fill risk.
   - Add a **24 h markout**; settlement is already stamped.
   - Keep the change paper-only: the client stays GET-only, and only `incentive_*` tables are
     written.
3. **The programme-age study** (§3b): its pre-registration, its script and tests, one run, and
   the result recorded. Then fix S4's burst window.
4. **The read script**, `scripts/limm_paper_strategies_read.py` (ops-allowlisted, read-only).
   - Per arm:
     - calibrated reward per bid-day, **with Kalshi's $1 minimum applied per market per
       programme** (a programme where the arm earned under $1 counts $0);
     - fill rate and fill cost at 24 h and at settlement, under all three fill models;
     - net per bid-day and per $ of capital, with an event-clustered SE (reuse
       `ratio_cluster`);
     - paid ¢/h against fill ¢/h (the cycle-8 yardstick).
   - Pairwise on matched market-hours: `S2-best − S2-behind`, `S3-aside − S3-control`,
     `S4-burst − S4-flat` (for S4, also the share of programmes that clear $1).
   - Tests, including an end-to-end run on synthetic rows like the other `limm_*` probes.
5. **Pre-registration** (`docs/LIMM_PAPER_STRATEGIES.md`), merged **before** recording starts.
   - Contents: arms, universes, sizes, pull rules, the S4 burst rule from step 3, the read date
     and the bars.
   - **Calibration fixed in advance:** the global 1.34, or the per-type factor known on the day
     it is written. It is never changed after data.
   - Suggested bars (consistent with study 9):
     - an arm is PROMISING at $0.15 or more net per $10 bid-day with LCB > 0, scaled by size
       for S1 and S4;
     - a pairwise difference DECIDES when its 95% CI excludes 0;
     - the primary fill model is conservative; the whole-order model is the stress case and
       must not turn PROMISING into a loss;
     - floors: 100 bid-days and 10 events per arm, else HOLD (accrual) with one re-read two
       weeks later.
6. **Ranker cycle 8** (§3c), with tests.
7. **Experiment OS.**
   - Under NEW_ONLY, register the programme as an experiment at PROBE (a paper instrument with
     no trading tag).
   - Follow the `kalshi_bot/experiment_os/mentions_forward.py` package pattern and run
     `xos package-preflight`, then REGISTER_PACKAGE through the single-slot transport.
   - If preflight says shadow policies need no package, record that in the PR. Do not invent a
     workaround.
8. **Calendar and routines.**
   - Replace the "~2026-11-24 LIMM paper strategies" row in `docs/RESEARCH_CALENDAR.md` with
     the real date and routine id, and add the programme-age row (DONE with its verdict).
   - Create a **fresh-session** read routine (`create_trigger`,
     `create_new_session_on_fire=true`) with the standard SETUP block. Copy it from any
     `Kalshi:` routine.

Ship as small PRs, in this order:
1. collector selection;
2. paper legs and the fill model;
3. the age study;
4. the read script and pre-registration;
5. ranker cycle 8;
6. the Experiment OS package.

Merge each when CI is green (research scope, DEC-012).

## 6. Hard rules

- **No peeking.**
  - No arm's net, fill cost or P&L is computed before the read date. Counts (rows, markets,
    bid-hours) are fine.
  - The age study reads history once, after it is pre-registered.
  - Study 5 (11-13) and S1–S4 are separate questions. Study 5's result feeds the ranker's
    fill-cost column; it does not move S1–S4's bars.
- **No live changes.**
  - Do not restart `Alimm1`, and do not change its rules, universe, size or env.
  - A passing arm becomes a **nomination**: one plain question to the owner.
- **The collector stays GET-only** and writes only `incentive_*` tables.
- **The ops channel stays read-only.** Use unique ids, and reset `ops/request.json` to noop.
- **No model identifiers** in commits or PRs.

## 7. Stop and ask the owner only if

- the collector change needs more than the current Railway plan, or makes the worker unhealthy;
- Experiment OS preflight refuses, and the fix is not a paper-scope step;
- anything would touch real money, the live book's switch, or a live safeguard. That includes
  any live bid above $10.

## 8. Done when

- [ ] The census is recorded: the number of programmes in the $1-minimum band per week.
- [ ] The collector picks by daily pay; it is deployed and healthy, and the "NOT WATCHED" pool has fallen.
- [ ] The programme-age study is pre-registered, run once, and its verdict recorded; S4's burst window is fixed from it.
- [ ] Seven arms are recording under three fill models. Row counts per arm are visible through ops (counts only).
- [ ] The pre-registration, with the calibration fixed, is merged **before** the read window's first recorded hour.
- [ ] The read script and tests are merged and allowlisted.
- [ ] Ranker cycle 8 is merged (calibrated ¢/h, the fill-cost column, the $1 flag).
- [ ] The Experiment OS receipt is SUCCEEDED, or preflight's "not needed" is recorded.
- [ ] The read routine exists and the calendar rows are updated.
- [ ] The owner has a 3–5 line plain summary: what is running, the read date, and that nothing live changed.
