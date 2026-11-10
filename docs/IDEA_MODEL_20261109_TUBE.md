# Idea-model run 2026-11-09: strategy ideas from the tube_bot trading videos

**Scope.** Calvin asked to look at the trading videos the tube_bot repo had analyzed and find
strategy ideas the Kalshi bot has not already tried. The request named the scope, so the Phase 0.5
menu was skipped. Session role: Research Lab.

**Result.** 16 transplants were screened from four videos. **One was promoted: LEVELHOLD**, technical
levels on the hourly BTC ladder. It is pre-registered in `LEVELHOLD_PREREG.md`; the probe is built and tested
and **ran after the owner merged its allowlist PR (#782): KILL** (SWING L −0.63¢, 90% CI −2.27..+1.01, n = 3,538). See the update at the end.
Five transplants are new kills by argument, one is a new hold, one is an owner-decision note, and seven are restatements of what the
record already settled. **Most of what these videos teach is already in the Kalshi record,
learned on Kalshi's own tape.**

Nothing here changes a lifecycle state, a gate, a verdict or a book. Experiment OS stays canonical.

---

## Sources (tube_bot `videos/`, all AI Pathways, analyzed 2026-11-06)

| id | title | what it actually found (futures/ETF data, the video's claims) |
|---|---|---|
| `z97PnFIPoFg` | Claude tested 100,000 price action strategies | Real S/R levels hold 51% of touches vs 42% for random lines, and flip zones add +11 pts. 4h change of character (CHoCH) is the only setup that survived out of sample. Costs eat 46% of gross on 1-min bars and 4% on daily. Win rate barely tracks profit (r = 0.27). |
| `KML09tRtHM8` | Claude tested 25,000 ICT strategies | The sweep is the only rule that adds edge (Sharpe 0.33 → 0.69), and the edge is short-term mean reversion. Killzone hours are ordinary. The 15-min bias subtracts edge. Adverse selection on limit fills is unpriced. |
| `zGmsARTATvQ` | Claude ranks every trading style | Net Sharpe rises at every step from 1-min to daily, because cost drag falls. Premium selling has Sharpe 0.69 with a −29% drawdown. Buy-and-hold ranks first. |
| `9rBgnMnDp_0` | Which trading psychology concepts work | For a profitable, non-streaky trader, skipping trades or stopping costs money (about −$600 per 1% of trades skipped). Daily profit targets cost money. Sizing 1.5× on top-half setups needs only +0.62 pp of win-rate spread. 1% risk survives tail gaps; 5% does not. |

The fifth video in the folder (`-pxPSU9uiYY`, DC neighbourhoods) is not about trading and is out of scope.

## Phase 0: grounding

- **Experiment OS** (`rl-ct-1109tube`, as of 2026-11-09 13:10 CST): MMSELL successors, the
  liquidity-incentive line, `limm-paper-strategies` at PROBE, theta paused. **No open experiment conditions
  on technical levels**, so LEVELHOLD duplicates nothing open.
- **The record already took the "price action" angle on 11-06** (`IDEA_MODEL_20261106.md`):
  - **MOMO-H was KILLED.** The hourly ladder prices the last 15 minutes of movement. Its pre-commitment closes
    follow/fade threshold variants.
  - **VOLCLOCK was HOLD and recommended for close.** The ladder is calibrated to the scheduled-vol hours.
  - The **early TA books** (`momentum`, `reversion`) lost about −5¢ over 1,000+ trades.
  - **OFLOW** found order flow does not predict price (large-trade slice included).
  - **MARKTANGLE** found streak length carries nothing.
  - **TYPE-RANK, CELL-SIZE, RUNAWAY-CHASE and FLOW-VETO** were MMSELL sizing and selection reads.
- **Priors.** Obs-pin is the only family that has ever passed. Price-history edges on liquid
  ladders start low, and quote calibration is 0-for-2 (MOMO-H, VOLCLOCK). A 1–3 minute bot cannot win latency races.

## Phase 2–3: slate and screen

Axes as in the 11-06 run: **Corr**, **Edge**, **Cost**, **Test** (gradeable now), **Cap**, **Reuse**.

### From the price-action video (`z97PnFIPoFg`)

| # | transplant | Corr | Edge | Cost | Test | Cap | Reuse | call |
|---|---|---|---|---|---|---|---|---|
| T1 | **LEVELHOLD**: do `KXBTCD` strikes on a 4h swing level, flip zone or round $1,000 settle on their current side more often than quoted? | + | o | o | ++ | + | ++ | **PROMOTE → probe built, unrun** (operator merge) |
| T2 | MOMO-D / CHoCH-4h: condition the ladder on multi-day runs ("skip trades into a 3+ day run") | ++ | − | o | − | + | ++ | **KILL**: a lookback variant of MOMO-H's follow/fade, which its pre-commitment forbids. Model-vs-quote is 0-for-2, and the window holds only about 15–20 runs of 3+ days |
| T3 | TA on Kalshi's own YES-price charts (CHoCH/flip zones on event prices) | ++ | −− | − | + | o | o | **KILL**: the early TA books lost about −5¢; meta-lesson 1; OFLOW |
| T4 | "Trade slower: costs fall with the timeframe" | | | | | | | **restatement**: the books already hold to settlement as makers (fee ≈ 0.003¢) |

### From the ICT video (`KML09tRtHM8`)

| # | transplant | Corr | Edge | Cost | Test | Cap | Reuse | call |
|---|---|---|---|---|---|---|---|---|
| T5 | SWEEP-FADE on spot: BTC runs the prior hour's high or low and reverses; does the hourly ladder overreact? | ++ | − | o | ++ | + | ++ | **KILL**: MOMO-H's fade legs were all negative after fee. It is the same recent-move feature |
| T6 | DISPLACE-FADE on the Kalshi tape: fade a large taker sweep that moves a no-news market by N¢ | ++ | − | − | −− | − | o | **PARK**: OFLOW's large-trade slice moved +0.05¢ gross, which is sign-free, so a fade is about 0 too. A price-displacement trigger is materially different, but there is no tape outside the WC slice, and OFLOW ruled that collection not worth building |
| T7 | Killzone / session-hour filters | | | | | | | **restatement**: VOLCLOCK and `MMSELL_TIMING_STUDY` |
| T8 | "Paper limit fills overstate: adverse selection is unpriced" | | | | | | | **restatement**: meta-lesson 3; fill models; live-paper twins |

### From the trading-styles video (`zGmsARTATvQ`)

| # | transplant | Corr | Edge | Cost | Test | Cap | Reuse | call |
|---|---|---|---|---|---|---|---|---|
| T9 | Premium selling (CBOE PUT analogue) | | | | | | | **restatement**: this *is* MMSELL maker-sell, and theta |
| T10 | Donchian swing/position trend on the S&P/Nasdaq Kalshi ladders | ++ | − | −− | o | −− | − | **KILL**: 40¢ / 12¢ spreads and dead rungs (`rl-swc-1106n`). The ladder is a terminal binary, not a holdable trend |
| T11 | Long-dated tail selling ("slow is better") | − | −− | o | + | o | o | **KILL**: DECAY found by-date hope *under*priced; WKNDVAR was killed |

### From the psychology video (`9rBgnMnDp_0`)

| # | transplant | Corr | Edge | Cost | Test | Cap | Reuse | call |
|---|---|---|---|---|---|---|---|---|
| T12 | Conviction sizing: 1.5× on the top half | | | | | | | **restatement: TYPE-RANK is exactly this** (HOLD, accrual; earliest decisive read late November). Advance that hold rather than open a new one |
| T13 | Streakiness audit: do loss-cooldowns pay? | | | | | | | **restatement**: MMSELL's streakiness is contest correlation. That is the caps, and **XOS-000020** (caps key on event, the unit is the game) is the open item |
| T14 | Cost of the daily-loss stop: the video says skip/stop rules cost a profitable, non-streaky trader money | | | | | | | **OWNER DECISION note, not a probe**: `MAX_DAILY_LOSS` is a live safeguard sized as a canary risk budget. Measuring its cost read-only is allowed (`gate:daily_loss` refused 3 entries in the 10-04 data). Changing it is a hard stop. Relevant only if the per-book risk budgets in `ACTIVE.md` get built |
| T15 | Tail survival: 1% risk per trade with gap losses | | | | | | | **restatement**: MMSELL's payoff (risk about 94¢ to win about 6¢) is exactly this shape, and the canary loss budgets and correlation caps cover it |
| T16 | Desk conviction audit: does a desk's stated confidence minus price predict realized edge, so it can size 1.5×? | ++ | o | o | −− | − | + | **HOLD (new)**: the legacy ledger has 24 picks, and R6 makes no calibration claim before about 30. A sizing read needs a few hundred. Trigger: ≥ 200 settled desk picks |

**Tally:** 16 screened. 1 promotion (T1). 5 new kills by argument (T2, T3, T5, T10, T11). 1 park (T6). 1 new hold (T16).
1 owner-decision note (T14). 7 restatements (T4, T7, T8, T9, T12, T13, T15).

## T1 LEVELHOLD: why it is the one

- **It is new.** No probe on this repo has used *where the strike sits relative to price structure*.
  MOMO-H used the direction of recent movement, and VOLCLOCK used the clock. 07-11 ROUNDPIN was killed only because
  the 15-minute contract's target is the open price; `KXBTCD` strikes are fixed prices on a **$100
  grid** (census `rl-levelhold-census-1109a`).
- **It is gradeable now.** About 106 decision markets a day and BTC 1-minute candles with about 100% coverage since
  08-01 (143,997 rows / 99 days). That projects to about 1,100 round-$1,000 decisions and an estimated 1,000–2,000
  swing-level decisions over 2026-08-06..11-08, against a 600 floor.
- **It is cheap.** The probe imports MOMO-H's tested plumbing: exact tickers, official labels with the
  historical tier, the fee and the day-block bootstrap.
- **The prior is low, and the test is honest about it.** It detects effects of about 4¢ or more, which is
  roughly the smallest effect worth taking at a ~2.3¢ cost. On synthetic nulls the statistic was calibrated
  (primary false-signal 4/60 at nominal 10%). Because three arms raise the family-wise rate, a PASS buys only a forward confirmation
  (`LEVELHOLD_PREREG.md` note A0, P3).
- **What it is worth if it works.** About 2¢ net × 10 contracts × about 20 level decisions a day is about $4/day, which is the scale of the
  $100/month north star.

## Holds queue: reconciled (as of 2026-11-09)

| hold | trigger | state |
|---|---|---|
| **FAVHELD** (new, from LEVELHOLD's observation) | look 1 data complete 2027-01-08 | **pre-registered 11-10, forward-only**; run on/after 2027-01-09 (`FAVHELD_PREREG.md`) |
| ~~LEVELHOLD~~ | | **KILLED 2026-11-09** run 1 (`rl-levelhold-run-1109b`): SWING L −0.63¢, CI spans 0; see `LEVELHOLD_PREREG.md` Results |
| **EARNBEAT** | Q3 reports settle | **due this week** (week of 2026-11-09) |
| **TYPE-RANK** | leg F reaches 150 TOP / 100 REST fills | earliest decisive read late November (absorbs T12) |
| **DESK-CONVICTION** (new) | ≥ 200 settled desk picks with confidence | not fired |
| **DISPLACE-FADE** (new, parked) | a non-in-play trade tape exists for another reason | not fired |
| **GRIDPIN** | full probe P1 thin | HOLD (11-06 run); unchanged |
| **XVENUE15/5** | operator wants a cross-venue direction | census-ready; unchanged |
| **ECON-REACT** | genuine prints settled | thin; monthly re-check |
| **TOKENPIN** | ≥ 20 settled weeks | about mid-January 2027 |
| **HURR** | first landfall-threat storm | retire if nothing by 2026-11-30 |
| ~~VOLCLOCK~~ | | run 1 HOLD(instrument), P3 FAIL; recommended closed 11-06 |
| others (GRID-SIBLINGS, RTPIN/BOXPIN, STREAMPIN, OPTRV, COMPIN, MENTION-corpus, XLOCK-P1, RATELAG, ART, EQUITY-HUB) | unchanged | parked |

## Ops requests made by this run (all read-only)

`rl-ct-1109tube` (control tower); `rl-levelhold-census-1109a` (one-day strike-grid count plus spot-candle
coverage, no outcomes read).

## Update 2026-11-09: LEVELHOLD verdict

Run 1 (`rl-levelhold-run-1109b`) returned **KILL**: technical levels are priced on `KXBTCD`. **This run therefore
promoted 1 and killed 1, leaving 0 open lines from the videos.** One post-hoc observation, the ladder-wide
favourite-side residual, is recorded in `LEVELHOLD_PREREG.md` as an observation only.
