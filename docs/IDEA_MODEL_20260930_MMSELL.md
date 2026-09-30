# Idea-model run 2026-09-30: how can the MMSELL book make real money?

```
SESSION: Research Lab
MODE: research-write (docs, pre-registrations); no probe built, no book changed, no live action
ENFORCEMENT: NEW_ONLY since 2026-08-16 14:34:42Z (cutover prod-new-only-20260816)
AS OF: 2026-09-30 07:07 CDT (ops `mmideas-ct-0930`, `mmideas-truth-0930`)
```

**Scope.** Operator handoff (Calvin, 2026-09-29): run `kalshi-idea-model` scoped to one question,
"how can the MMSELL book (mmsell10 family, live tag `Fmmsell10`) make real money?" Output is a
screened slate and at most two pre-registered promotions. The handoff also allowed "stop investing
in mmsell and redeploy effort" as a valid outcome; this run treats it as one and scores it.

**Result in one paragraph.** Fifteen in-scope candidates were screened. **Two are promoted**, both
cheap and both read-only: **YOUNG-SERIES** (a universe probe:
[`MMSELL_YOUNG_SERIES_THESIS.md`](MMSELL_YOUNG_SERIES_THESIS.md)) and **CELL-SIZE** (a sizing
census: [`MMSELL_CELL_SIZE_CENSUS.md`](MMSELL_CELL_SIZE_CENSUS.md)). They are the only two levers
left with a live positive number behind them, and together they bound what MMSELL can ever earn.
**The structural answer is already visible without them:** at one contract the book's ceiling with
*perfect* fill selection is about $30/month, and its realized rate is about $3/month. Reaching
$100/month needs a per-fill edge roughly 5–10× today's *and* 3–5× size *and* the same fill count,
jointly. No tested lever has moved the per-fill number by more than noise. **Recommendation:** run
the two reads (one ops request each once built); if either fails, stop investing research effort in
MMSELL as a P&L line, keep `Fmmsell10` running only as the pipeline's real-money canary, and
redeploy to the non-maker queue (GRIDPIN re-run ~end October, EARNBEAT week of 11-09, the
liquidity-incentive shadow verdict). Detail in §5.

Nothing here changes a lifecycle state, a gate, a verdict, an envelope or any book. Experiment OS
remains canonical for every standing cited. No probe script was written and `ops_runner.py` is
untouched.

---

## Phase 0: grounding (verified via ops today, cited by id)

**Experiment OS** (`mmideas-ct-0930`): IDEA 0 / PROBE 0. Five PAPER experiments, all mmsell
family. Four LIVE_CANARY lines, all `BLOCKED_PLATFORM` behind the unapplied
`EXECUTION_ENGINE:shared_primary_ownership_20260921` impact (Platform Change Review owns that; it
blocks the gate reads, not the trading). The live mmsell line is `mmsell-price-ceiling-contest-cap`
(`mmsell-contestcap-live-2`, twin `mmsell-contestcap-twin-2`, 28 open, $26.41 at risk). Two
informational paper standings the run leans on, neither a gate result: `mmsell-correlation-cap`
contest_capped +1.48¢ vs control +1.19¢ (n=1,704, gate NOT_STARTED); `mmsell-reviewed-universe`
reviewed_capped **+0.68¢ vs control +1.41¢** (n=400, gate NOT_STARTED).

**Real money** (`mmideas-truth-0930`, epoch since 2026-09-07T02:03:36Z, 23.4 days):

| figure | value |
|---|---|
| realized | **+$2.49** over 689 settled |
| total incl. unrealized | +$2.65 (28 open, cost basis $24.54) |
| contracts filled | 715; tickers ordered 934, filled 717 (76.8%), never filled 217 |
| twin `Fmmsell10_pt4` realized | +$23.60 over 1,141 |
| paper under the live tag: filled / never filled / never ordered | −$2.09 (n=698) / +$11.36 (n=206) / +$17.48 (n=612) |

**The fill-selection decomposition** (`mmideas-q1b-0929`, `mmideas-parity-0929`, unchanged since):
twin trades live filled +0.14¢ (n=674, 92.9% win); live rested and never filled **+6.56¢** (n=197,
99.0%); never ordered +3.14¢ (n=240). Matched-market gap −0.09¢, so paper prices fills correctly.
Fills inside 10 minutes lose (−0.7¢, n=404); later fills win (+1.5 to +2.3¢, n=270)
(`mmideas-q2b-0929`). Queue at rest: median 962 contracts ahead, p90 19,706, front-of-queue 8.9%
(`mmideas-telem-0929`).

**The graveyard for this book** (all pre-registered, all on record; none is regenerated below
without a stated mechanical difference):

| lever | verdict | where |
|---|---|---|
| 1¢ price improvement (queue priority) | KILL: front of queue is where losers cross; −3.29¢ live | `MMSELL_OFFSET_AB.md` |
| queue-aware early cancel | RETIRED: deep-queue orders are slow, not dead (27.5% later fill, +1.65¢ each) | `MMSELL_QUEUE_AWARE_CANCEL.md` |
| RUNAWAY-CHASE (conditional taker) | KILL: −0.40¢; 62% would have filled anyway | `MMSELL_RUNAWAY_CHASE_THESIS.md` |
| FLOW-VETO (skip into YES-taker flow) | KILL: predicts fast fills, not losing ones | `MMSELL_FLOW_VETO_THESIS.md` |
| THIN-MARKET (low 24 h volume) | KILL: thin fills worse (−0.53¢ / −1.36¢) | `MMSELL_THIN_MARKET_THESIS.md` |
| series keep/drop from past P&L | null: keep −0.98¢ vs drop −0.45¢ | ops `mmnext-oos-0929` |
| stops / TP / SL / volatility gates | dead: −4 to −7¢/trade; A4 no effect at n=1,944 | `MMSELL_ANCHOR_SET.md`, roadmap §10 |
| spread / depth / outcome-count filters | dead in the cheap band (no variance) | roadmap §5–7 |
| type books, deeper scan, timing (maker) | dead after fill haircut; 13¢ paper spread → 1.45¢ realizable | `MMSELL_TYPE_BOOKS.md`, `MMSELL_TIMING_STUDY.md` |
| unconditional taker (NO-TAKER), INVERSE-MMSELL | KILL 2026-09-29 (TFAV regeneration; fee eats it) | `IDEA_MODEL_20260929.md` |
| adding into winners; ultra-cheap band (mmsell6) | dead; mirage under the fill model | roadmap §8, `MMSELL_FILL_MODEL.md` |
| h2h exclusion | gate failed at 42× n | roadmap §9a |
| short strangle A5 | RETIRED on an underpowered confidence bar; point estimate above break-even | `MMSELL_ANCHOR_SET.md` |

**Meta-lessons that bind this run.** (1) Every *execution* lever on this book has been tried and
the front of the queue is structurally toxic; the +6.56¢ bucket cannot be bought. (2) Every
*selection* lever tried on 2026-09-29 was null. (3) The fee is not a lever: maker fills bill
~0.01¢. (4) Paper numbers are fill-everything; only real fills or a replay of real prints count.

## Phase 0.5: scope

Pre-scoped by the operator (the MMSELL book); the five-option menu is skipped.

## Phase 1: the board this book actually trades

Live universe: sports in-play and scheduled cheap tails at YES 5–7¢ (`lo=5,hi=10,maxyes=7`,
`contestcap=1`), 1 contract, post-only at the NO bid, 4 h timeout, hold to settlement. Flow is
~40 orders/day, ~30 fills/day, across ~65 series live may touch (75 more are refused by the
graduated-tier bar, `OPS_FMMSELL10_PARITY_DIAGNOSIS.md` §3). Resting NO interest ahead of us is
deep (median ~1,000 contracts), so **capacity is not the constraint; edge per fill is.**

## Phase 1a: the arithmetic that frames every candidate

Revenue is a product of three factors. Today, and what $100/month needs from each:

| factor | today (`mmideas-truth-0930`) | at the twin's ceiling (perfect fill selection) | for $100/month |
|---|---|---|---|
| fills / day | 30.5 | 30.5 | 30.5 (or more) |
| ¢ / fill | **+0.36** (+2.49 / 689) | +2.07 (twin +23.60 / 1,141) | **≥ 2.2** at size 5; ≥ 11 at size 1 |
| size | 1 | 1 | **3–5** |
| **$/month** | **≈ $3.2** | **≈ $30** | $100 |

Two consequences. The twin's own number is the book's ceiling at one contract, and it is
unreachable in principle: three probes say the missing +6.56¢ bucket is selected *against* a maker
by construction. And $100/month needs the per-fill edge to be at or above the twin's fill-everything
number *while* running 3–5× size. Every candidate below is judged against that product, not against
"is it positive."

## Phase 2: the slate (15 candidates, all inside the MMSELL book)

Each line names the bucket it moves (filled / unfilled / never-ordered) and why the fill would not
select against it.

| # | candidate | lever | bucket moved | why the fill does not select against it |
|---|---|---|---|---|
| M1 | **YOUNG-SERIES**: rest only (or preferentially) in series first listed / first in-band within 30 days | universe | filled (composition); never-ordered (tier bar) | informed takers work established series; launch flow is retail. Mechanically distinct from volume (dead), past P&L (dead), flow (dead) |
| M2 | **BARRED-UNIVERSE canary**: a fresh live tag on the 75 series the graduated-tier bar refuses | universe | never-ordered (+3.14¢ paper, n=240; +$17.48 over 612 today) | it does not: the same maker fill applies. Its paper edge is higher (reviewed universe +0.68¢ vs unrestricted +1.41¢) but the realizable haircut there is unmeasured |
| M3 | **CELL-SIZE**: size ×3 only in a price cell where filled ≈ unfilled | sizing | filled (multiplied) | only if the cell is fill-neutral (the 6¢ cell was, on `mmsell3` in July); a 3-lot otherwise fills on sweeps, which are informed |
| M4 | tail-weighted entry sizing by cell loss rate (roadmap §8) | sizing | filled | same as M3 with more parameters; cells need ≥ 50 distinct events each |
| M5 | **INVERSE-OFFSET**: rest 1¢ worse for the taker (NO bid −1, i.e. ask 8¢ instead of 7¢) | execution | filled → unfilled (fewer, later fills; +1¢ premium each) | the one untested point on the queue-priority curve; fills only after the level ahead clears or the market reprices toward YES, both plausibly *more* informed |
| M6 | price-tenure filter: post only after the market has been in band ≥ 2 scan cycles | selection | filled (drops fast fills) | fast fills lose (−0.7¢ < 10 min); but FLOW-VETO tested the "posting into a moving market" premise via taker flow and it was null |
| M7 | STRANGLE v2: A5 successor with a powered floor (≈ 663 pairs at the observed 94.55%) and the current 93.1% break-even | structural | filled (two premiums vs at most one loss) | it does not; each leg is a maker fill. Value is payoff *shape*, not per-fill edge |
| M8 | ENDGAME-TAKER: cross to NO in the last 15–30 min of in-play contests, keyed on a forward clock | mechanic (taker) | unfilled → filled (taker keeps the whole distribution) | a taker is not selected; `MMSELL_TIMING_STUDY.md` reads +4 to +6.7¢ paper in that window, but on a *realized* clock that survivorship confounds |
| M9 | scheduled non-crypto universe (KXRAIN filled +6.54¢, n=17) | universe | filled | scheduled settlement has no in-play informed taker; n is tiny and the 2×2 design (WS-003) is blocked on WS-002 |
| M10 | day-cap / correlation cap | risk shape | none | drawdown control, not edge; `mmsell-correlation-cap` is already running in paper |
| M11 | ultra-cheap band (`maxyes=5`) | universe | filled | `mmsell6` was a mirage under the fill model (−0.28¢ realizable projection today) |
| M12 | brute size ×10 on the whole book | sizing | filled | it does not; ×10 of +0.36¢ ≈ $32/month before size worsens selection and ×10 tail |
| M13 | multi-book partition (`part=i/n`) to raise fills/day | capacity | none | partition splits the same candidates; it creates no fills |
| M14 | purchased-tail hedge (buy a farther YES tail against the NO) | structural | filled | PARKED by the operator 2026-09-12; not regenerated here |
| M15 | rest mmsell NO bids only in liquidity-incentive markets to collect rewards | structural | filled + reward | KILL: programs require two-sided quotes at target size 1,000 in ~98% of cases; a 1-lot NO bid earns nothing. That book is WS-020, not MMSELL |

## Phase 3: the screen

Axes: **Corr** (correlation to the live book; all are the same driver, so this axis only separates
"refinement" from "multiplier"), **Edge** (prior given the graveyard), **Cost** (fees, spread,
adverse selection), **Test-NOW** (settled data exists today for the gate's floor), **Cap** (fills
and $/month at realistic size), **Reuse** (existing probe/tape). Scale −− to ++.

| # | Corr | Edge | Cost | Test-NOW | Cap | Reuse | honest $/month | call |
|---|---|---|---|---|---|---|---|---|
| M1 YOUNG-SERIES | o (refinement) | − | ++ (maker, ~0 fee) | **++** (2,564 primary orders, DB-only feature) | − ($4–5 @1, $13–15 @3) | ++ (thin-market probe scaffolding) | $4–15 | **PROMOTE (probe)** |
| M2 BARRED-UNIVERSE | o | o | ++ | −− (no live fill counterfactual exists; only a live tag answers it) | o (~$10 @1 if the haircut matches the reachable set) | + | ~$10 | **DECISION** (hard stop: real money on unreviewed series; per-book budgets are a prerequisite if `Fmmsell10` keeps running beside it) |
| M3 CELL-SIZE | o (multiplier) | o | ++ | **++** (698 fills, 3 cells ≈ 230 each) | − ($20 best case) | ++ (`live_book_truth` buckets) | ≤ $20 | **PROMOTE (census)** |
| M4 tail-weighted sizing | o | − | ++ | − (cells below 50 events) | − | + | ≤ $20 | **KILL for now**: a multiplier on ~0; M3 returns the cell numbers it would need |
| M5 INVERSE-OFFSET | o | − (mirror of a KILL; fills at a worse taker price are plausibly more informed) | ++ (+1¢ premium) | + (replay on WS-019 trade prints since 09-16) | −− (fill rate would fall from 77% to a fraction) | ++ (`mmsell_chase_probe.py`) | ≤ $5 | **HOLD**: replay spec below; run only if the operator wants the curve closed |
| M6 price-tenure | o | −− (4th pre-post selection lever; three were null) | ++ | ++ | − | ++ | ≤ $5 | **KILL at screen** |
| M7 STRANGLE v2 | + (shape) | o (point estimate 94.55% vs 93.1%) | ++ | − (needs a new paper tape; the retired tag cannot trade) | − ($8 paper @1) | ++ | ≤ $8 | **HOLD**: revive only as a tail-shape control the operator wants; not a $ lever |
| M8 ENDGAME-TAKER | + (taker, different driver) | o | − (spread ~2¢ + 1¢ fee; TFAV killed the crypto twin at −3.6¢) | −− (worker does not persist `expected_expiration_time`) | o | + | unknown | **HOLD**: data trigger below |
| M9 scheduled non-crypto | + | o | ++ | − (n=17 live) | −− | + | ≤ $3 | **FOLD** into M1's report (KXRAIN row) |
| M10 day-cap | o | n/a | ++ | ++ | n/a | ++ | $0 (shape) | **SKIP**: running as `mmsell-correlation-cap` |
| M11 ultra-cheap | o | −− | ++ | ++ | −− | ++ | < $0 | **KILL** (mirage on record) |
| M12 brute ×10 | o | −− | ++ | ++ | − | ++ | ≤ $32 before selection worsens | **KILL**: ×10 the tail for ×10 of noise |
| M13 partition | o | −− | ++ | n/a | −− | ++ | $0 | **KILL** |
| M14 purchased-tail | + | o | − | − | − | o | unknown | **PARKED** (operator) |
| M15 incentive overlap | + | −− | −− | ++ | −− | ++ | $0 | **KILL** |
| **M0 STOP-INVESTING** | n/a | n/a | n/a | n/a | frees the desk | n/a | redeploys effort to families with a passing shape | **admissible; conditional on M1/M3, see §5** |

**Why only two promote.** M1 and M3 are the only candidates that (a) act on a bucket with a real
live number behind them, (b) are testable today with data already in the DB, and (c) cost one
read-only run each. Everything else is either a hard stop with no cheap prior (M2), a multiplier
with nothing to multiply (M4, M12), a regeneration of a kill in a new coat (M6, M11, M13, M15), or
a genuine idea whose data does not exist yet (M8) or whose value is shape rather than dollars
(M7, M10).

## Phase 4: promotions (pre-registered; nothing here is run)

### P1 — YOUNG-SERIES (universe probe)

Full pre-registration: [`MMSELL_YOUNG_SERIES_THESIS.md`](MMSELL_YOUNG_SERIES_THESIS.md). Feature
`AGE` = days since the series' first appearance in the bot's own history; YOUNG = `AGE ≤ 30`.
Primary sample = the seven pre-9/7 live books (out-of-sample of the 09-29 observation);
Confirmation B = fills from 2026-09-30 forward. Bars Y0–Y4; PASS on all five makes "young series"
a candidate universe treatment for a successor canary (hard stop); KILL on Y2 or Y3 closes universe
selection on this book. Recon census first: Y0 and Y1 are that census (does the young cell have
≥ 150 settled fills?); the full read runs only if they clear.

### P2 — CELL-SIZE (sizing census)

Full pre-registration: [`MMSELL_CELL_SIZE_CENSUS.md`](MMSELL_CELL_SIZE_CENSUS.md). Per NO-price
cell (93–97) on `Fmmsell10`: C1 ≥ 150 fills, C2 filled ≥ +1.0¢ with bootstrap lower bound > 0,
C3 unfilled-minus-filled ≤ 2.0¢, C4 median taker print ≥ 3. A passing cell makes size ×3 in that
cell a candidate sizing treatment for a successor canary (hard stop). No passing cell closes sizing
until a per-fill edge exists.

### Holds written this run (MMSELL-scoped; the global queue from 09-29 is carried unchanged)

| hold | the spec, in one line | trigger |
|---|---|---|
| **INVERSE-OFFSET** (M5) | Replay every `Fmmsell10` order since 2026-09-16 against `execution_trade_events`: "would a rest at `limit − 1` (NO) have been touched before the 4 h timeout?" using the same touch proxy as `mmsell_fill_replay`; score touched orders at the real settlement, at +1¢ premium; PASS only if touched-set ¢/fill exceeds the actual filled set by ≥ 1.0¢ **and** touched count ≥ 40% of actual fills. **Censoring caveat (verified 2026-09-30, raised by the parallel screen in PR #494):** the WS-019 collector unsubscribes a market `execution_telemetry_post_window_seconds` = 900 s after the real order goes terminal (`kalshi_bot/execution/collector.py::refresh_tracked`), so an order that filled in 3 minutes has no tape for the remaining ~4 h a worse-priced rest would have waited. The replay therefore needs a coverage-only census first (share of orders with a full 4 h path), and a fast-fill-heavy sample is the one it cannot see | operator asks to close the queue-priority curve, **after** a coverage census shows a representative full-path sample |
| **STRANGLE v2** (M7) | A5 successor as a new Version: floor from power analysis (≈ 663 clean pairs at 94.55% vs 93.1%), break-even computed at freeze from the current fee model, pair economics off pairs only; paper tape under `DEC-012` | operator wants a tail-shape control; not a $ lever |
| **ENDGAME-TAKER** (M8) | Persist Kalshi's `expected_expiration_time` per in-play candidate at decision time, then re-run `mmsell_timing_study` on the *forward* clock; a taker book is gated on `edge ≥ +3.0pp` at n ≥ 300 within a single type (its existing gate) | the worker persists the forward clock (a Live Ops build ask, not started here) |
| **BARRED-UNIVERSE canary** (M2) | A fresh live tag on the tier-barred series at the same envelope; needs per-book risk budgets first if `Fmmsell10` stays up (parked prerequisite) | operator DECISION; no cheap prior exists |

## 5. The recommendation, and the stop-investing option scored honestly

**What the two reads decide.** Run P2 first (one DB read, hours). Then P1 (one probe run once the
script exists; primary sample is already settled).

| outcome | what it means | recommendation |
|---|---|---|
| P1 PASS **and** P2 finds a passing cell | a universe + size successor is worth $15–40/month at 1–3 contracts, the first live proposal on this book since 09-07 | spec a successor canary (fresh tags, twin, envelope restated for the clip): **operator hard stop** |
| P1 PASS, P2 no cell | young series earn more per fill but size cannot be added | $4–5/month at size 1; **stop investing beyond the successor's universe change**, if the operator wants even that |
| P1 KILL (either Y2 or Y3) | universe selection joins execution and market selection as null | **STOP INVESTING** in MMSELL as a P&L line |
| P1 HOLD on Y1 | the young cell is too small to read on the primary books | wait for Confirmation B (3–5 weeks) with **no further research spend**; the decision is then the same table |

**Why "stop investing" is a legitimate, probably correct, answer.** The book's realized rate is
about $3/month; its ceiling with perfect fill selection is about $30/month at one contract; the
missing +6.56¢ bucket has been shown three times to be unbuyable by execution and once by each of
three selection features. Reaching the north star from this book needs three independent
improvements to compound, and none of the three is established. Meanwhile the one family with a
passing shape in the scorecard (observation pin / mechanics blindness) has GRIDPIN at census with
a re-run date, and EARNBEAT scheduled. **The research desk's marginal hour is worth more there.**

**What "stop investing" does *not* mean.** It does not mean standing `Fmmsell10` down. The canary
is +$2.65, costs ~0.01¢ per fill, proves the arming path, the twin protocol and WS-019 telemetry
end to end, and its gates are blocked only by a platform impact Platform Change Review owns.
Keeping it running is free evidence. Standing it down (or freeing the ~$25 at risk) is the
operator's call and is outside this run's scope.

**What this run does not do.** It does not register anything in Experiment OS, write a probe
script, touch `ops_runner.py`, change any book, relax any live bar, or propose arming. It appends
two scorecard rows and one journal entry (the skill's own ledger step).

## Scorecard rows appended (`IDEA_MODEL_SCORECARD.md`)

YOUNG-SERIES (pending probe) and CELL-SIZE (pending census), both dated 2026-09-30, scope "Calvin
request (MMSELL-scoped run)".

## Ops requests made by this run (all read-only)

`mmideas-ct-0930` (xos control-tower), `mmideas-truth-0930` (`live_book_truth --tag Fmmsell10`).
The 09-29 results reused by id are still on the `ops` branch at the time of writing
(`mmideas-q1b-0929`, `mmideas-q2b-0929`, `mmideas-parity-0929`, `mmideas-telem-0929`,
`mmnext-oos-0929`); the load-bearing numbers are copied above because results rotate after 80
files. Channel reset to `noop`.
