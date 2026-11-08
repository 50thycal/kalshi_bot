# Handoff — build three incentive paper strategies (S1–S3)

- **From:** the Research Lab check-in session, 2026-11-08.
- **To:** a new **Research Lab** session that builds and starts all of it.
- **Owner approval:** DEC-043 (`docs/DECISIONS.md`). Everything below is approved scope. The only
  hard stops are the ones listed in §7.
- **Goal:** within about two days, three paper strategies (five arms) are recording a simulated
  $-sized resting bid in live incentive markets. For every hour each arm records:
  - the estimated reward it earns;
  - whether it would have been filled;
  - what a fill costs.

  They are read once around 2026-11-24 against bars fixed before any data.

## 1. Read first (in this order)

1. `CLAUDE.md` (the session role, the ops recipe, and that Experiment OS is canonical).
2. `docs/LIMM_MARKET_RANKER.md`: the measure, the 7 build cycles and the findings this work comes
   from. Run the ranker once to see the live board:
   `{"type":"script","name":"limm_market_ranker","args":["--json"],"id":"<unique>"}`.
3. `docs/LIMM_BAND_PROBE.md` (study 9, NO-LEAD above 10¢): why the strategies stay on the cheap
   side.
4. `kalshi_bot/liquidity_incentive/`:
   - `collector.py`: the shadow engine. It is GET-only; it keeps local books, replays the public
     tape and writes `incentive_*` tables.
   - `scoring.py`: the LIP rules R1–R6 and `estimate()`.
   - `fills.py`: `ShadowLeg`, with conservative and optimistic models.
   - `quotes.py`: today's two-sided policies A/B/C.
5. `docs/LIQUIDITY_INCENTIVE_THESIS.md` §9.52 (why a `paper_trades` twin is invalid for this book)
   and §9.72–§9.76 (the live rules today).
6. The owner preference: brief, plain, non-technical messages.

## 2. What the owner locked (DEC-043)

| # | Decision |
|---|---|
| 1 | Build **S1, S2 and S3** as below. Fold late-exit rules for one-event ladders into S3's step-aside arm. |
| 2 | The collector **picks markets by daily pay** (pool per day), not by total programme reward. |
| 3 | **Size each strategy for its job** (guardrails in §4). |
| 4 | The incentive cap is now **8**. Study 9 Part B is cancelled. |
| 5 | **Read once around 2026-11-24**: the later of 11-24 or 14 days after recording starts. |

## 3. The strategies

All arms are **one-sided resting bids** on the side the ranker picks (normally the cheap side,
≤ 10¢). There are **no real orders**. Every arm uses the same measure as the ranker and study 9.

| Strategy | Arm | Universe | Placement rule | Thesis it tests |
|---|---|---|---|---|
| **S1 Weekly money-finder** | `S1` | The highest **pool per day per market** live liquidity programmes, mainly the short weekly ones the collector skips today (shipping chokepoints, EO counts, Rotten Tomatoes, Trump act). Top N by the ranker's estimated reward per $ once books exist. | At the best bid on the ranker's side. | The ~$89k/day we cannot see includes uncrowded markets paying more per $ than anything we trade. |
| **S2 At-best vs behind** | `S2-best` | The ranker's top quotable markets (both arms use the same markets at the same times). | At the best bid. | Being at the touch doubles the score (discount_factor 5000). Do sweep fills eat the doubled reward? |
| | `S2-behind` | Same as `S2-best`. | One tick behind the best bid (today's live §9.72 rule; 1¢ stays at 1¢). | The control for `S2-best`. |
| **S3 Release-aware** | `S3-aside` | State employment ladders (`…EMP`), AAA gas, and the one-event ladders (ski openings: `KXASPENMTNOPEN`, `KXBIGSKYOPEN`, and similar). | At the best bid. **Pull** for verified release windows: BLS state employment from 2 h before to 48 h after (next: **2026-11-20 15:00Z**, `KNOWN_RELEASES` in the ranker); AAA gas 12–16Z daily. **One-event ladders:** pull when our side's best bid falls to 1¢ after being higher, or inside the final 3 days before close. | Fill losses cluster around scheduled releases and late certainty dumps. Stepping aside keeps most of the reward. |
| | `S3-control` | Same as `S3-aside`. | At the best bid, never pulls (only the 48 h pre-close step-aside every arm has). | The control for `S3-aside`. |

## 4. Sizing guardrails ("scale appropriately for each strategy")

Fix the exact numbers in the pre-registration before recording starts.

- **S2 (both arms) and S3 (both arms): $10 per bid,** the live book's size, so the results map onto
  `Alimm1` directly. Use at most 1,000 contracts and at most 3 markets per event. Keep at most 10
  markets per arm at a time, the live $100 cap.
- **S1: up to $20 per bid** where the market's pool is at least $50/day, else $10. Use at most
  20 markets and at most 3 per event. It is a discovery sweep, so it needs breadth.
- **Every arm, every hour:** our size must keep `snapshot_qualifies`. Log our share, and flag any
  hour where we would hold more than 50% of a side (study 3's "thin" rule: no reward credited).

## 5. How to build it (the recommended route)

1. **Collector selection by daily pay (DEC-043 item 2).**
   - In `ShadowState._reconcile` (`collector.py`), sort `wanted` by `period_reward_usd ÷
     programme days` instead of by `period_reward_usd`. Keep the pinned markets, which the live
     book trades.
   - Keep `max_markets` at 150 first. Raise it only after checking WebSocket and database load: the
     Railway usage routine runs daily, and the `ops` `logs` request is available.
   - Add a test. Confirm after deploy that `rc-a1` (snapshots/markets per 4 h) stays healthy, and
     that the ranker's "NOT WATCHED" pool falls.
2. **One-sided paper legs in the shadow engine.**
   - Add a new policy family (for example `quotes.ONE_SIDED_POLICIES`), or a small sibling module.
     Reuse `ShadowLeg` for fills and `scoring.estimate()` with one side's size set to 0 for the
     reward.
   - Tag every row with the arm (`S1`, `S2-best`, …) in `IncentiveShadowQuote.policy`. Fill and
     outcome rows already carry the policy.
   - Arms S2 and S3 must quote the **same markets at the same hours** as their partner arm, so the
     difference is clean.
   - Extend the existing markouts with a **24 h** mark. Settlement is already stamped.
   - Make it a **paper-only change to the collector**. The GET-only client must stay GET-only, and
     no trading table may be touched.
3. **The read script** (`scripts/limm_paper_strategies_read.py`, ops-allowlisted, read-only):
   - per arm: reward per bid-day, fill rate, fill cost at 24 h and at settlement, and net per
     bid-day with an event-clustered SE (reuse `ratio_cluster`);
   - **pairwise**: `S2-best − S2-behind` and `S3-aside − S3-control` on matched market-hours;
   - add tests, including an end-to-end run on synthetic rows like the other `limm_*` probes.
4. **Pre-registration doc** (`docs/LIMM_PAPER_STRATEGIES.md`), written and merged **before**
   recording starts:
   - arms, universes, sizes, pull rules, the read date and the bars;
   - suggested bars, consistent with study 9: an arm is PROMISING at ≥ $0.15 net per $10 bid-day
     with LCB > 0 (scale for S1's $20 bids); a pairwise difference DECIDES when its 95% CI excludes 0;
   - floors: ≥ 100 bid-days and ≥ 10 events per arm, else HOLD (accrual) with one re-read two
     weeks later.
5. **Experiment OS.**
   - Under NEW_ONLY, register the programme as an experiment, at PROBE (paper instrument; no
     trading tag). Follow the `kalshi_bot/experiment_os/mentions_forward.py` package pattern, run
     `xos package-preflight`, then REGISTER_PACKAGE through the single-slot transport.
   - If preflight says shadow policies in the collector do not need a package, record that answer
     in the PR. Do not invent a workaround.
6. **Calendar and routine.**
   - Replace the "~2026-11-24 LIMM paper strategies" row in `docs/RESEARCH_CALENDAR.md` with the
     real date and routine id.
   - Create a **fresh-session** read routine (`create_trigger`, `create_new_session_on_fire=true`)
     with the standard SETUP block. Copy it from any `Kalshi:` routine (`list_triggers`).
7. **The ranker.** Once S1's markets have books, its top table covers them automatically. No
   change is needed.

Ship as small PRs (collector selection, paper legs, read script and pre-registration, Experiment
OS package). Merge each when CI is green, under research scope (DEC-012).

## 6. Hard rules

- **No peeking.**
  - Do not compute any arm's net, fill cost or P&L before the read date.
  - Counts are fine: rows, markets, bid-hours.
  - S2 and S3 data from 11-10 to 11-13 overlaps study 5's window. They are separate questions;
    nothing from S1–S3 feeds study 5, and study 5's result does not change S1–S3's bars.
- **No live changes.**
  - Do not change `Alimm1`'s universe, price rule or step-aside, or any env var that steers it.
  - A passing arm becomes a **nomination**: one plain question to the owner.
- **The collector stays GET-only.** It writes only `incentive_*` tables.
- **The ops channel stays read-only.** Use unique ids, and reset `ops/request.json` to noop when
  done.
- **No model identifiers** in commits or PRs.

## 7. Stop and ask the owner only if

- the collector change would need more than the current Railway plan (cost) or makes the worker
  unhealthy;
- Experiment OS preflight refuses, and the fix is not a paper-scope step;
- anything would touch real money or a live safeguard.

## 8. Done when

- [ ] The collector picks by daily pay; it is deployed and healthy (snapshots/4 h ≥ before); the "NOT WATCHED" pool has fallen.
- [ ] Five arms are recording. Row counts per arm are visible through ops (counts only).
- [ ] The pre-registration is merged **before** the first recorded hour of the read window.
- [ ] The read script and tests are merged and allowlisted.
- [ ] The Experiment OS registration receipt is SUCCEEDED (or preflight's "not needed" is recorded).
- [ ] The read routine exists and the calendar row is updated.
- [ ] The owner has a 3–5 line plain summary: what is running, the read date, and that nothing in the live bot changed.
