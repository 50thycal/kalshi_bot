# Idea-model run 2026-09-29: taking liquidity outside the maker cell

**Scope.** This run was scoped by the operator's request, so the Phase 0.5 menu was skipped. Calvin
asked for new Kalshi strategy ideas **outside the market-making / maker-fill cell**. Ideas should take
liquidity, or rest on fills that do not select against us: settlement mechanics, information lag,
structural or rules edges, cross-venue, and scheduled data. Session role: Research Lab (read plus
research-write; no live promotion).

**Result.** 18 candidates were screened. **One new promotion goes to a census:** GRIDPIN, ERCOT daily
peak-load settlement mechanics. Its venue-age gate still applies, so the census may return HOLD. Two
**existing holds whose triggers have fired** are the other advanceable items: the SEASONPIN census
re-run, because the MLB regular season ended 2026-09-27, and the ECON-REACT re-run, overdue since
2026-08-08. One HOLD is new: TOKENPIN, which now has only 5 settled weeks. 11 candidates were killed.

Nothing here changes a lifecycle state, a gate, a verdict, the scorecard, or any book. Experiment OS
remains canonical. No probe was written. The operator asked that the scorecard not be touched, so
the rows to append are listed at the end.

---

## Phase 0: grounding (what the record already settled)

**Experiment OS standing** (`xos control-tower`, ops `ideas0929-ct`, as of 2026-09-29 15:53 CDT):
**IDEA 0 / PROBE 0.** So nothing below duplicates an open line. There are five PAPER experiments,
all in the mmsell family. There are four LIVE_CANARY lines (three mmsell price-ceiling lines and
`liquidity-incentive-mm`), all currently `BLOCKED_PLATFORM` behind the unapplied
`EXECUTION_ENGINE:shared_primary_ownership_20260921` impact. `theta-tail-sell` is PAUSED. Paper
realized over 30 days is $815.49, but paper assumes fills it does not get. The north star is
real money, and real money is Fmmsell10's roughly +$1.40 over three weeks.

**The correlation baseline is one return driver.** The whole portfolio is the favorite-longshot
maker-sell (mmsell) plus the liquidity-incentive shadow, and both are maker. PORT (07-22) already
found one independent +EV cluster where at least two are needed. So any candidate uncorrelated with
the sports cheap-tail maker-sell is worth more than its raw edge.

**Today's binding lesson** (Fmmsell10, ops `mmideas-q1b-0929`, `chase-probe-20260929-1`,
`veto-probe-20260929-1`):

| read | result |
|---|---|
| twin trades live **filled** | +0.14¢ (n=674) |
| twin trades live **rested, never filled** | +6.56¢, 99.0% win (n=197) |
| paper vs live on the same market | −0.09¢, so paper prices fills correctly; the whole gap is *which* orders fill |
| RUNAWAY-CHASE (take when passed) | KILL, −0.40¢ (n=86) |
| FLOW-VETO (skip into YES-taker flow) | KILL: flow predicts fast fills, not losing ones |
| series-level past P&L → future live fills | not predictive |

**Translated into a screen rule for this run.** Edges that need a passive fill are capped near
breakeven. So every candidate here either **takes** liquidity or states why the fill cannot select
against it. And because taking pays spread plus fee, the edge has to come from **information the
resting side does not have at the moment we take**. That information is a settlement mechanic, an
observation, or a scheduled print. A price pattern does not count.

**Graveyard entries this run had to steer around** (full list in `IDEA_MODEL_SCORECARD.md` and
`RESEARCH_JOURNAL.md`):

- **Weather `obs` book: −3.7¢ at n=171 forward (pruned 07-04).** It bought the bucket containing
  the day's observed running max. This is the direct ancestor of any running-max pin, and the
  reason GRIDPIN below must name a *mechanics* difference, not an inattention one.
- **PINNED v4:** the AAA gas weekly-average post-pin was +0.02¢, perfectly efficient.
  **TOUCH-LOCK, VOTEPIN, CLINCHMATH, SPORTLOCK, WCPROP:** once a watched outcome is public, it
  converges within a cycle.
- **TWIN / locked arb:** no credit across roughly 2,700 events and five false-positive classes.
- **XGAME / PMDIV / lead-lag:** 0-for-3; the shared feed is symmetric.
- **TFAV:** the favorite side of the favorite-longshot bias (FLB) goes to makers, not takers.
- **OFLOW / FLOW-VETO:** tape flow predicts neither price nor fill quality.
- **FEDRV:** the internal-coherence "gaps" were independence-assumption artifacts.
- **MLBWX / THETA:** homegrown models lose (model-vs-quote 0-for-2).
- **METALHALT / FREEZE / COMPIN:** new-venue data absence and a continuous settlement feed.

**Base rate.** 19 idea-model promotions produced one book that passed (PIN15, later retired), and
none are live today. Per family, **observation-pin / mechanics-blindness is 1-for-7 and the only
family that has ever passed**. What separated PIN15 from the rest was **testability-NOW** plus a
**mechanics** gap (a 60 s average vs spot), not source inattention. This run is calibrated to
promote at most one or two ideas, census-first.

**New evidence since 09-12 that this run uses.** The discretionary desk's ledger
(`docs/desk/ledger.csv`, 09-19 → 09-29) is an unplanned field study of scheduled-data taking:

- **Diesel "tomorrow":** the AAA-print edge "closes within hours" (09-29 note), and the weekly
  diesel contract settles on the *same single-day print* as the daily contract (POSTMORTEMS 1a).
- **KXTOKENUSE:** the ladder lagged a public partial count twice (T128 at 58¢ settled YES; T144 at
  56¢ settled YES). That is n=2, which is anecdote, not evidence.
- **KXTXERCOTPEAKD:** "the first candidate all week whose contract text alone names a real source".
- **Mortgage PMMS:** a window average whose first days are already known.

## Phase 1: the board, within scope

**Survey** (`kalshi_market_survey`, ops `ideas0929-survey`, 14 days, open markets; 117,419 scanned):

| category | 14-day volume | markets | avg spread |
|---|---|---|---|
| Sports | $652M | 22,368 | 14.2¢ |
| Elections | $619M | 9,678 | 7.3¢ |
| Politics | $109M | 2,259 | 11.1¢ |
| Crypto | $98M | 757 | 12.2¢ |
| Economics | $81M | 3,960 | 15.0¢ |
| Science & Tech | $62M | 771 | 12.5¢ |
| Entertainment | $56M | 6,876 | 25.6¢ |
| Financials | $40M | 4,639 | 14.4¢ |
| Companies | $16.5M | 536 | 9.3¢ |
| Climate & Weather | $6.0M | 1,180 | 10.8¢ |
| Commodities | $3.5M | 784 | 7.6¢ |

Three series matter for this scope:

- **`KXNFLWINS`:** 524 markets, $8.3M, **15.7¢ average spread**. These are the NFL season win-total
  ladders, open now; SEASONPIN's NFL leg is live.
- **`KXMLBGAME`:** postseason, confirming the MLB regular season is over.
- **`KXRT`:** Rotten Tomatoes ladders, 187 markets, $4.9M.

**New settlement families checked directly** (`kalshi_market_probe`, ops `ideas0929-probe1`,
`-probe2`; `kalshi_desk_board --event`, ops `ideas0929-ercot-live`):

| series | what settles it | listing age | cadence | live book read |
|---|---|---|---|---|
| **`KXTXERCOTPEAKD`** | "the **highest hourly total system load** reported for the ERCOT grid on <date>"; greater-than strikes 500 MW apart; Kalshi adds strikes intraday | first event found is Sep 1 (`-26SEP02`); `-26AUG25` returns 404. About **4 weeks old** | daily; the market **opens the day before and closes 23:55 CT on the day itself**, so it trades through and after the afternoon peak | 29 Sep at 16:00 CT, mid-peak: 19 rungs, about 11k contracts of volume on the day, spreads of **28–69¢ on the live rungs**, 1¢ asks on the dead ones. Thin and wide |
| **`KXTOKENUSE`** | "OpenRouter total token usage for <Mon–Sun week> above N T"; about 8–21 strikes | first event found is Aug 24–30; `-26AUG17` returns 404. About **5 settled weeks** | weekly; closes Mon 03:59Z, about 4 h after the week ends | desk: spreads of 26–43 points, the book sometimes inverted |

---

## Phase 2 + 3: slate and screen

**Axes:**

- **corr:** shares a driver with the mmsell maker book; `++` means uncorrelated.
- **edge:** prior given the meta-lessons.
- **cost:** net of both-leg fees and spread; for a taker there is no adverse-selection haircut, but
  the stale-quote side is who we trade against.
- **test-now:** settled data exists today.
- **cap/age:** capacity and venue age.
- **infra:** reuse.

Scale −− … ++. Mechanics are the outer loop. Anti-anchor slots were forced (energy, AI usage,
entertainment).

| # | candidate (mechanic × market; the fresh signal) | corr | edge | cost | test-now | cap/age | infra | call |
|---|---|---|---|---|---|---|---|---|
| **S1** | **GRIDPIN**: take the ERCOT daily-peak ladder after the day's peak using ERCOT's published hourly load. The signal is the **hourly-integrated** running max versus the instantaneous 5-min demand the public dashboard shows (obs-pin / mechanics × energy) | ++ | + | o | o | −− | + | **PROMOTE to census (venue-age gate: the census may return HOLD)**. Named difference from the dead `obs` book below |
| S2 | TOKENPIN: take the weekly OpenRouter token ladder from the public in-progress weekly bucket (slow-accretion pin × AI usage) | ++ | o | − | −− | −− | − | **HOLD (new).** About 5 settled weeks, and the independent unit is the *week* (n=5 against a floor near 20). No archived intra-week partials, so a backtest needs a forward logger, which is a build and an operator decision. Mid-week projection is velocity extrapolation, the fragile half of the family (VIEWLOCK kill). Two desk wins are an anecdote. Trigger below |
| S3 | SEASONPIN re-run: cumulative-bound win-total rungs (MLB 2026 complete; NFL live at 15.7¢ spreads) | o | + | + | **++** | o | ++ | **FIRED HOLD → run the existing census now.** Existing pre-registration (`docs/SEASONPIN_THESIS.md`), not a new promotion. The 07-12 blocker was "0 settled MLB rungs", and every 2026 MLB rung settled on 09-27 |
| S4 | DIESELDAY / GASDAY: one-day-ahead AAA diesel/gas print ladders (scheduled data × commodities) | + | −− | − | + | − | + | **KILL: regeneration.** PINNED v4 measured AAA post-pin at +0.02¢. Two weeks of desk reads found the fresh-AAA-print gap closing "within hours, not days". A random walk of about 1.5¢/day against 1–2¢ strike spacing is a coin flip plus fee |
| S5 | PMMSPIN: Freddie Mac 30-year PMMS window average, first days known from a daily rate index (settlement arithmetic × rates) | + | o | o | −− | −− | − | **HOLD.** One weekly series, no free archived daily-rate history in the repo or allowlist, and n of about 50 settles a year. Trigger: a keyless daily 30-year rate history reachable from ops |
| S6 | ECON-REACT re-run: post-release quote lag on scheduled prints, adding weekly claims (scheduled data × economics) | ++ | + | o | o | + | ++ | **FIRED HOLD, overdue since 08-08: run it.** One ops request (`econ_react_study` v2), no build. Existing pre-registration |
| S7 | NOWCAST-CPI: Cleveland Fed CPI nowcast versus the KXCPI ladder (external public model × economics) | ++ | −− | o | + | o | o | **KILL.** The nowcast is *the* reference Kalshi CPI traders anchor on, so there is no counterparty who has not seen it. Economics has been efficient every time it was measured (FEDRV, KXRATECUTCOUNT at 0.2¢) |
| S8 | ERCOT-FORECAST: morning ladder versus ERCOT's own published day-ahead load forecast (external public forecast × energy) | ++ | − | − | o | −− | + | **FOLD → GRIDPIN census.** The morning ladder is a forecast market, so this is model-vs-quote (0-for-2). The census reports morning mid versus ERCOT's forecast for free; no separate thesis |
| S9 | SIBLAG: in-play intra-Kalshi lead-lag. The liquid game moneyline reprices and thin spread/total tails lag; take the stale tail (lead-lag × sports) | + | − | − | o | o | o | **KILL (for us).** Our fast losing fills prove someone already snipes stale tails, and our scan takes 1–3 minutes end to end (`MMSELL_QUOTE_PARITY.md`). That is a latency race against the counterparty who is already beating us. SPORTLOCK/WCPROP: in-play derived ladders reprice within a cycle. The defensive half (pull quotes when the sibling moneyline moves) is a maker-cell question and out of scope |
| S10 | SHARPLINE: take Kalshi game lines when they deviate from a sharp sportsbook's no-vig line (cross-venue × sports) | o | − | − | −− | + | − | **KILL.** No free historical odds tape (untestable NOW). Pre-game Kalshi tracks books; lead-lag is 0-for-3; TFAV |
| S11 | DIESEL-TWIN: the daily and weekly/monthly diesel series settle on the *same* single-day print. Trade them against each other when they diverge (structural × commodities) | ++ | −− | − | o | −− | + | **KILL.** This is TWIN / locked arb (0 credit across the board). The one live instance was a desk mis-read, not a divergence. `kalshi_arb` already monitors for dislocations |
| S12 | POSTSEASON-COHERENCE: MLB series and pennant prices versus game moneylines under independence (internal coherence × sports) | − | −− | − | + | + | + | **KILL.** FEDRV again: the "gap" is the independence assumption. The internal-coherence family is 0-for-3. The 60¢ average `KXMLB` spread is WIDEQUOTE's dead-rung artifact |
| S13 | RESOLVE-DRIFT: buy outcomes already decided at 97–99¢ before a slow settlement (settlement timing × any) | + | − | −− | + | − | + | **KILL.** TOUCH-LOCK/PINNED: post-pin converges. The taker fee is `ceil` per order, so 1 contract at 98¢ pays 1¢, half the edge, plus capital lockup |
| S14 | INVERSE-MMSELL: be the YES taker in the series where our maker fills lose (MLB spread/total, MLS, WTA) | −− | − | −− | + | o | ++ | **KILL.** Fast maker fills lose about 0.73¢, so the YES taker nets about +0.7¢ gross, minus a fee of at least 1¢ (ceil at 5¢). Today's read also showed series-level past P&L does not predict future fills |
| S15 | NO-TAKER: the taker version of mmsell, crossing to NO on cheap tails instead of resting (FLB × sports) | −− | −− | −− | + | + | ++ | **KILL: regeneration of TFAV.** Filled maker fills make +0.14¢. Crossing adds 1–2¢ of spread plus the fee. The FLB goes to makers |
| S16 | LLMBOARD: reaction to LMArena leaderboard updates on "best AI" markets (event reaction × AI) | ++ | − | o | −− | −− | − | **KILL.** A watched feed (VOTEPIN), and `KXLLM1` is a year-end one-off. No recurring cadence to grind |
| S17 | RTPIN: Rotten Tomatoes score ladders pinned by accumulating reviews (slow-accretion pin × entertainment) | ++ | + | o | −− | + | − | **HOLD (existing, unchanged).** Capacity is now real (`KXRT` 187 markets, $4.9M/14d), but there is still no public history of *partial* scores to grade against. Trigger unchanged |
| S18 | EARNBEAT: consensus anchoring on KPI ladders (existing) | ++ | + | o | −− | − | + | **HOLD (existing).** Re-run the week of 2026-11-09, as already scheduled |

**Tally:** 1 promotion to census (S1), 2 fired holds to run (S3, S6), 1 new HOLD (S2), 1 new
unchanged-shape HOLD (S5), 1 fold (S8), 2 existing holds carried (S17, S18), 11 kills.

**Why only GRIDPIN survives, and why it is still likely to come back HOLD.** It is the only new
candidate that is all four of these:

1. A **taker** trade.
2. Priced against a **named, public settlement source**.
3. Supported by a **mechanics** gap, the PIN15 shape, rather than an attention gap, the shape the
   weather `obs` book lost with.
4. Uncorrelated with every live book.

Its weak axes are real and stated up front. The listing is about 4 weeks old, the book is thin
(about 11k contracts a day), and the running-max ancestor lost. The census is built to kill it
cheaply on any of those.

**Why the fired holds rank above most of the slate.** SEASONPIN and ECON-REACT are already
pre-registered, and their blocker was *data absence, which has now lifted*. Running them costs one
ops request each and zero new code. By the pipeline's own record they are the highest
expected-value next actions in scope: the only pass came from an observation pin with a gradeable
tape.

---

## Phase 4: promotion (pre-registered thesis + staged probe plan)

### GRIDPIN: ERCOT daily-peak settlement pin

*Thesis written 2026-09-29, before any validation ran. The falsifiable predictions below are
pre-registered. Status: pending census. To be materialized as `docs/GRIDPIN_THESIS.md` plus a census
script by `kalshi-probe-builder`. This run built neither, by operator instruction.*

**One-liner.** Late in the Texas day, after the afternoon peak, the day's maximum *hourly-integrated*
ERCOT load is essentially known from ERCOT's own published hourly values. Yet the
`KXTXERCOTPEAKD` ladder trades until 23:55 CT. Buy the rung sides the published hourly running max
already decides, where the ask still leaves a margin. Lean hardest on rungs that the *instantaneous*
dashboard peak crossed but the *hourly* value did not.

**Mechanism**

- **What is mispriced.** After the peak hour (typically 16:00–19:00 CT), load falls into the evening,
  so the ladder should collapse to about 0 or 100 around the hourly running max. The claim is that
  some rungs keep trading at interior prices after that.
- **The mechanics gap, which is the load-bearing difference from the dead `obs` book.** The contract
  settles on the **highest *hourly* total system load**. The number a casual trader watches is the
  ERCOT dashboard's **5-minute instantaneous demand**, and its intraday peak exceeds the hourly
  integral of the same hour. If that excess is comparable to the 500 MW strike spacing, then rungs
  between the hourly max and the instantaneous max are **overpriced YES**. The trader who saw the
  dashboard cross 81,500 MW believes YES is locked, while the settlement value says NO. That is
  PIN15's "60 s average versus spot" shape, which is the one pin shape that has passed. It is not
  `obs`'s "the thermometer is public and the quote is slow" shape, which lost at −3.7¢ (n=171).
- **Who is on the other side.** Launch-era retail on a 4-week-old, thin listing, together with
  resting orders that are not re-marked after the peak. Power traders who model ERCOT load
  professionally are unlikely to bother with a ladder of about 11k contracts a day. This is the same
  reason the new-venue counterparty is attractive and the same reason the venue is data-poor.
- **Why the taker is not adversely selected here.** We cross only when a published hourly value
  already decides the rung. The resting side we hit is stale, not informed. The hourly value
  **arrives with a lag** and may be restated between the real-time posting and the "reported"
  settlement figure. That publication and revision risk is the one thing that could put the informed
  trader on the other side, so C2 below measures it before anything else.
- **Edge family.** Observation pin / mechanics blindness (the only family with a pass), on an energy
  universe with zero portfolio exposure.

**Pre-registered predictions.** The unit is a (rung, event-day) pair. Entries are taker-at-ask, one
contract, held to settlement, net of the taker fee
`ceil(0.07·qty·P·(1−P)·100)`.

- **C0: census, the universe exists.** At least **40 settled event-days**, **≥ 150 settled rungs with
  any volume**, and **≥ 60 rungs with at least one trade after 19:00 CT** (the post-peak window).
  Otherwise **HOLD (accrual)**. Trigger: re-run when the day count reaches 60. The census may return
  HOLD today: about 28 days are listed.
- **C1: census, a gradeable settlement source is reachable.** A keyless ERCOT hourly-load history
  (the actual system load report or the hourly load archive) must be fetchable from the ops runner,
  and it must reproduce Kalshi's `expiration_value` / result on **≥ 95%** of sampled settled rungs.
  Otherwise **BLOCKED_DATA**, with no full probe.
- **C2: census, the publication lag and revisions are tolerable.** The median lag from hour end to the
  hourly value's first public posting must be **≤ 90 min**, and posted values must not flip any
  sampled rung's outcome at settlement (revision flips **≤ 2%** of rungs). Otherwise **KILL
  (premise)**, because the "known" value is not known in time or is not the value that settles.
- **P1: the decided-side discount exists and clears cost.** Take decided rungs after the posted hourly
  running max decides them: YES for strikes below the max, and NO for strikes at least 1,000 MW above
  the max after 21:00 CT. Mean net P&L must be **≥ +2.0¢/contract** with **≥ 60 entries** and a
  settlement accuracy of **≥ 98%**. **KILL** if the mean is **≤ +0.5¢** or accuracy is **< 95%**.
- **P2: it is not just favorite drift** (PINNED's control). The P1 entries must beat a control of
  *undecided* rungs quoted in the same 85–97¢ band at the same hour by **≥ 1.5¢/contract**.
  **KILL** if they do not.
- **P3: the mechanics sub-claim** (graded only if a 5-min history exists; see below). In the gap
  class, rungs whose strike lies between the day's hourly max and its instantaneous 5-min max, the
  YES side must trade above its settled value on average. **PASS** if taking NO at ask in that class
  nets **≥ +3¢** at **n ≥ 20**. If P3 FAILS while P1 passes, the edge is the attention shape that
  lost on weather: it is **not promoted to paper** without a fresh pre-registration.
- **P4: capacity.** The median depth available at or better than the entry price on qualifying
  entries must be **≥ 5 contracts**, and **≥ 1 qualifying entry per 2 event-days**. Otherwise this is
  a hobby: HOLD, never a book.
- **Decision rule.** A paper book (a new XOS experiment) is proposed only if C0–C2 pass **and** P1,
  P2 and P4 pass, with P3 PASS **or** P3 untestable. If P3 is untestable, the paper book carries a
  pre-registered P3 forward check at n=20. Any KILL closes the ERCOT pin. Economic *forecasting* of
  the ladder (S8) is not reopened by a GRIDPIN pass.

**Probe plan (staged: census first)**

- **Step 1, recon census** (about 60–80 lines, read-only, stdlib; new
  `scripts/kalshi_gridpin_census.py`; needs an `ops_runner.py` allowlist entry, which is an
  operator-merge item). It answers only C0–C2:
  - Enumerate `KXTXERCOTPEAKD` events by date back to the first 404.
  - Per rung, report status, result, `expiration_value`, volume and open interest.
  - Pull `/markets/trades` for a sample of rungs and count post-19:00 CT prints.
  - Fetch ERCOT hourly load for the same days, confirm reachability, compare against
    `expiration_value`, and measure posting lag and revisions on the days the source exposes them.
  - Also print, for free, the morning mid versus ERCOT's day-ahead forecast (the S8 fold) as
    description only.
  - Grep the Kalshi series list for sibling grid series (other ISOs, monthly peaks) to size a larger
    universe.

  Below the floors, it returns **HOLD/BLOCKED_DATA**, and **no full probe is written**.
- **Step 2, full probe** (only if the census clears): `scripts/kalshi_gridpin_study.py`.
  - **Data:** Kalshi public trades plus 1-min candles per rung (the `xvenue_crypto.kalshi_candles`
    pattern), and ERCOT hourly actuals. For P3 only, a 5-min system-demand history *if* one is
    reachable. If not, P3 is graded forward from a small read-only logger, which is a separate
    operator decision.
  - **Provenance:** ERCOT data goes to a new, clearly named table or file, never mixed into
    `weather_*`.
  - **No lookahead:** at decision time *t*, the only information used is the hourly values whose
    measured first-posting time is at or before *t*, taken from C2's lag table, not the hour-end
    time. The entry price is the ask at *t* from the book or trade record, never the settle.
  - **Measurement:** P1–P4 as above, sliced by hour of day and by margin to strike, with a
    split-half check across calendar halves.
- **Reuse:** `kalshi_desk_board` / `kalshi_market_probe` fetch patterns, the PINNED/SEASONPIN
  control design (P2), and `desk_fetch`'s ERCOT allowlist entry (added 09-25) as the reachability
  precedent.

**Cost and capacity note.** Decided-side entries sit at 85–99¢, where the per-order taker fee is
`ceil`'d to 1¢ for a single contract at 90–99¢. That is why P1's bar is +2¢ net and why multi-contract
orders matter. The 09-29 live book shows 1–5 contract depth and very wide spreads, so the realistic
size is **5–20 contracts per event-day**. At +3–5¢, that is roughly **$5–30/month** if it works:
small, but **uncorrelated ballast**. As the day count grows and if sibling grid series exist,
roughly $30–60/month is plausible. It will not reach $100/month on its own.

**Correlation note.** Zero shared driver with the mmsell family (sports cheap-tail maker-sell) or the
liquidity-incentive shadow. Its driver is Texas weather-driven load, and no weather book is live. It
does overlap the **discretionary desk's** universe (R10, one unit per print). A systematic GRIDPIN
book and desk picks on the same print must not both trade, and that boundary goes into the thesis
doc.

### The fired holds (run these; no new thesis)

| hold | what fired | the one action | what it closes |
|---|---|---|---|
| **SEASONPIN** | MLB regular season ended 2026-09-27, so every 2026 MLB win-total rung is settled. The 07-12 blocker was 0 settled | `{"type":"script","name":"kalshi_seasonpin_census","id":"<new id>"}` (existing, allowlisted). Also add NFL (`KXNFLWINS`, 524 open, 15.7¢ spreads) to discovery if the census does not already cover it | the census verdict under the pre-registered 07-12 floors (n ≥ 40 candle-covered decided rungs; median decided→settled window ≥ 24 h) |
| **ECON-REACT** | re-run due 2026-08-08; about 2.5 months of CPI/PCE/jobs/claims prints have settled since the 20-market run | `{"type":"script","name":"econ_react_study","id":"<new id>"}` (existing, allowlisted). The v2 allowlist should include `KXJOBLESS`/`KXICSA` as the thesis says | P0 testability, then the pre-registered P1/P2 |

---

## Holds queue: reconciled (trigger state as of 2026-09-29)

| hold | trigger | state |
|---|---|---|
| **SEASONPIN** (MLB; NFL extension) | MLB rungs settle | **FIRED 2026-09-27: run the census** |
| **ECON-REACT** | genuine prints settled | **FIRED, overdue since 08-08: run it** |
| **EARNBEAT** | Q3 reports settle | scheduled for the week of 2026-11-09 |
| **TOKENPIN** (new, S2) | ≥ 20 settled weeks **and** an intra-week partial history (OpenRouter daily granularity confirmed reachable, or a forward logger approved) | parked; earliest about mid-January 2027 on weeks alone |
| **PMMSPIN** (new, S5) | a keyless daily 30-year mortgage-rate history reachable from ops | parked |
| **RTPIN / BOXPIN** | a public partial-score history | parked (capacity now confirmed, $4.9M/14d on `KXRT`) |
| **STREAMPIN / STREAMRANK** | intra-window tape appears | parked (unchanged) |
| **OPTRV** | a free options-implied source for CME commodities | parked (unchanged) |
| **COMPIN** | a keyless intraday reference history | parked, BLOCKED_DATA-shaped (unchanged) |
| **MENTION-corpus** (absorbs MENTIONLOCK) | cheap timestamped transcripts | parked |
| **XLOCK-P1** | a rules-text matcher that finds ≥ 200 pairs | parked |
| **RATELAG** | a live macro shock to the front Fed contract | parked |
| **HURR** | the first landfall-threat storm | parked (season winding down; **retire** if nothing by 2026-11-30) |
| **ART: GUARPIN** | Oct/Nov evening sales settle | parked; check after the November sales |
| **EQUITY-HUB / single-stock perps** | first settled month after listing | parked |
| **SPOT-PERP-CARRY** | WS-018's named unblockers | operator line, **not** an idea-model hold; listed only so it is not regenerated |
| **ERCOT-FORECAST** (S8) | — | **folded into GRIDPIN's census** (description only) |

No retirements this run beyond those already closed on 09-12. **Newly killed this run and not to be
regenerated without a mechanically new premise:** SIBLAG, SHARPLINE, DIESELDAY/GASDAY, DIESEL-TWIN,
NOWCAST-CPI, POSTSEASON-COHERENCE, RESOLVE-DRIFT, INVERSE-MMSELL, NO-TAKER, LLMBOARD.

## Scorecard rows to append (NOT written, by operator instruction)

| date | idea | family | scope source | verdict date | verdict | outcome |
|---|---|---|---|---|---|---|
| 2026-09-29 | **GRIDPIN** | obs-pin / mechanics blindness (hourly-integrated vs instantaneous ERCOT peak) | Calvin request (outside the maker cell), `docs/IDEA_MODEL_20260929.md` | — | **pending census** | Census-first: C0 universe (≥ 40 days / ≥ 60 post-peak-traded rungs), C1 source reproduces settlement, C2 lag ≤ 90 min and ≤ 2% revision flips. Venue about 4 weeks old; HOLD is a live possibility |

SEASONPIN and ECON-REACT are re-runs of existing rows. When their verdicts land, they update those
rows and are not new promotions.

## Ops requests made by this run (all read-only)

`ideas0929-ct` (xos control-tower), `ideas0929-survey` (`kalshi_market_survey --days 14 --top-series
60`), `ideas0929-probe1` and `ideas0929-probe2` (`kalshi_market_probe` series dating for
`KXTXERCOTPEAKD` / `KXTOKENUSE`), `ideas0929-ercot-live` (`kalshi_desk_board --event
KXTXERCOTPEAKD-26SEP30`). Results files live on the shared `ops` branch and rotate after 80 files.
The load-bearing numbers are copied into this doc.

## What this run does NOT do

- It does not register anything in Experiment OS, create a thesis doc or census script, touch
  `ops_runner.py`'s allowlist, update the scorecard, or change any book.
- It does not run the SEASONPIN or ECON-REACT re-runs. Those are one ops request each, for the
  session that owns them.
- It does not reopen the weather `obs` book, PINNED, TWIN, XGAME, TFAV or FEDRV. Each candidate that
  touches one of them names its parent and the material difference, or is killed.
