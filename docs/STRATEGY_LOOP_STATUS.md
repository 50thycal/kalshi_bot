# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-08 10:33 PM CDT (run #27 — catch-up)

*Gap note: the loop stalled after run #26 (Jul 6, 11:13 AM) on the account's weekly usage
limit; ~7 scheduled fires were missed. This single run covers the full ~2.6-day window.
No commits landed on the default branch during the gap — no strategy changes deployed;
all books kept trading as configured.*

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 1,421 | +$2.29 | +0.2 | 41 | **flipped positive** (+$10.10 on +623 settles) |
| mmsell1 (5-20¢) | 724 | +$3.25 | +0.4 | 31 | flipped positive (+$8.45) |
| mmsell2 (10-20¢) | 470 | +$2.02 | +0.4 | 22 | flipped positive (+$7.19) |
| tfav | 191 | −$7.08 | −3.7 | 0 | **crossed n≥150 gate NEGATIVE** (3rd straight whipsaw) |
| theta (control) | 495 | **+$15.53** | +3.1 | 2 | **+$61.77 window** — but see calibration below |
| theta1 | 179 | **+$12.22** | +6.8 | 0 | +$41.67 window; past gate, positive P&L |
| theta2 | 86 | −$8.72 | −10.1 | 0 | improved but negative |
| theta3 | 127 | −$11.58 | −9.1 | 0 | past gate, still negative |
| wcprop | 0 | — | — | 0 | still zero trades ever (armed since Jul 4) |
| weather con | 277 | **+$1.46** | +0.5 | 17 | **first real drawdown** (−$6.36 on 26 settles) |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |
| buy_favorite / momentum / reversion / (blank) | 0 | 0 | — | 0 | dormant legacy (last active Jun 6–8) |

**HEADLINE — everything whipsawed across the 2.6-day gap, and the calibration drill-down
separates luck from edge.** The theta family swung **+$127 pooled** (control −$46→+$16, theta1
−$29→+$12 — both cumulatively positive for the first time); all three mmsell books flipped
slightly positive; tfav crashed −$11 through its gate; weather_con gave back most of its
cumulative in its worst week. Three consecutive runs have now flipped signs on three different
families — cumulative P&L endpoints on these negative-skew books are noise; per-trade rates +
calibration at large n are the only trustworthy reads. That is what the drill-down checked:

**theta — P&L flipped, calibration still fails → shelve verdict SURVIVES (per the
pre-registered rule).** The calibration slice (all settled trades, lifetime): realized
tail-hit rates exceed modeled on **every** book — control modeled 15.6% vs realized 21.8%
(1.4×), theta1 5.7% vs 10.6% (1.9×), theta2 5.3% vs 14.0% (2.6×), theta3 17.0% vs 31.5%
(1.9×). The model still underprices tails, exactly the original ~2× diagnosis. And the
positive cumulative is entirely one calm 3-day run: control made **+$58.54 in 3 days vs
+$15.53 lifetime** (it was −$43 before the streak); theta1 +$18.14 in 3 days vs +$12.22
lifetime. A tail-seller that is miscalibrated 1.4–2.6× will look great in calm stretches and
give it back in the next correlated tail (run #26 was that tail). The pre-registered rule is
"positive AND calibrated" — calibration fails everywhere, so the rule still says shelve.
Nuance for fable: theta1's +6.8¢/trade at n=179 is the only number that argues for anything;
if the operator wants to keep one book alive, that's the candidate — but it requires a NEW
pre-registration (e.g. sold-price-vs-realized-tail-rate test at n≥350), not a quiet extension.

**weather_con — the drawdown is a pattern, not a fluke day.** By entry-day (CDT): 07-03
+$1.15 (41% win), 07-04 **−$3.57 (14% win)**, 07-05 +$0.57 (44%), 07-06 **−$3.05 (8% win)**,
07-07 **−$3.31 (14% win)**. Three of the last six entry-days were sharply negative with
8–14% win rates. Cumulative is down to +$1.46 (+0.5¢/trade). The "scale the con book" plan
should be PAUSED until a fable pass checks whether the losing days share a cause (same city /
same window type / a forecast-regime shift the ensembles kept missing). 17 open positions.

**tfav — pre-registered gate crossed, negative → kill rec now active.** n=191 (past the
n≥150 gate), −$7.08, −3.7¢/trade, after a third consecutive window whipsaw (−11 → +15 → −11).
Under the gate registered in the 2026-07-06 fable memo ("keep only if >+2¢/trade at n≥150"),
tfav fails → recommend kill (fable action).

**mmsell — all three books flipped slightly positive; the honest read is breakeven noise.**
Pooled +$7.56 over 2,615 settles ≈ **+0.3¢/trade**, after being −1.5¢ pooled two runs ago.
This book oscillates around zero: no evidence of an edge that would survive live fill
realism, and no longer decisively negative either. Softened recommendation: do NOT promote;
keep as a zero-attention data book or prune for simplicity — either is defensible.

**wcprop — still zero trades ever** (absent from the books table = no rows). Armed through
the entire knockout stage without a single qualifying entry. Kill rec stands (winner ladder
efficiently priced / no lag at ride-along cadence).

**XGAME — collector healthy, schedule thinned.** Matches 19 total (+4 since #26, last new
Jul 8 5:32 AM); tapes 5,966/24h (group-stage flood is over — semifinal lull). Fresh as of
10:27 PM. The `xgame_tape_study` is now runnable on multiple completed matched games — still
the highest-information pending action.

**Data (last-24h / latest CDT):** crypto_spot 2,870 (10:25 PM ✓, 2 products), ladder 61,066
(10:25 PM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (10:18–
10:29 PM ✓). xgame_matches 19 total (2 new in 24h), xgame_tapes 5,966/24h (10:27 PM ✓). All green.

**Research probes (on-demand):** WCPROP = `xmarket_wc` (offline backtest; epitaph run still
suggested) + the live `wcprop` book (armed, 0 trades). XGAME `xgame_tape_study` (now runnable).
Not run from the loop.

**Headline:** the 2.6-day catch-up window flipped P&L signs on theta (now +, but calibration
still fails 1.4–2.6× → shelve survives), mmsell (now marginally +, breakeven noise), and
weather_con (first drawdown — 3 bad days of 6, scale-up paused); tfav crossed its gate
negative → kill rec active. wcprop still 0 forever. Collectors all green.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · shelve per the rule — verdict SURVIVES the P&L flip] Calibration still fails on
   every book (realized tails 1.4–2.6× modeled); the cumulative positive is one calm 3-day
   streak.** control +$15.53 (n=495) but +$58.54 of it came in 3 days; theta1 +$12.22 (n=179,
   +6.8¢) same pattern. The pre-registered "positive AND calibrated" rule fails on the
   calibration half everywhere → shelve the family; keep the crypto_spot + ladder collectors
   (verify collect-only mode so the dataset survives). If keeping anything: theta1 only, under
   a NEW pre-registered test (sold-price vs realized-tail-rate at n≥350) — not a silent
   extension. Post-mortem in RESEARCH_JOURNAL either way.

2. **[tfav · gate crossed NEGATIVE → kill rec active] n=191 ≥ 150, −3.7¢/trade.** Fails the
   pre-registered gate from the 2026-07-06 fable memo. Recommend disabling tfav
   (`TFAV_ENABLED=false`). Third consecutive window whipsaw confirms it's variance around a
   negative mean, not an edge.

3. **[mmsell · breakeven noise — softened from "prune candidate"] All three books now slightly
   positive (pooled +0.3¢/trade, n=2,615).** Oscillating around zero across runs. Do NOT
   promote anything; either keep as a zero-attention data book or prune for simplicity. If
   pruning strategy families this pass, mmsell remains a reasonable cut (no demonstrated edge
   net of realism), but it is no longer "decisively negative."

4. **[weather_con · PAUSE the scale-up — drawdown needs diagnosis] 3 of the last 6 entry-days
   sharply negative (8–14% win) → cumulative down to +$1.46.** Before any sizing/expansion per
   the fable plan, run a fable pass on whether the losing days share a cause (city, window
   type, forecast-regime shift). The book stays on (17 open) — this is a diagnose-first flag,
   not a kill flag.

5. **[wcprop · kill rec stands] Zero trades ever, through the whole knockout stage.** Winner
   ladder shows no harvestable post-match lag at 10-min cadence. `WCPROP_ENABLED=false`;
   optional one-shot `xmarket_wc` run for the post-mortem numbers. Tournament ends in days —
   low stakes either way.

6. **[XGAME · run the tape study — top info action] 19 matched games, tape collection through
   the semifinal lull, multiple completed matched games now in the dataset.** Run
   `xgame_tape_study` (fable/operator) to grade P1–P4; gate `xgame_book_enabled` on the result.

*(Changed this run: #1 theta reframed — P&L flipped positive but calibration drill-down keeps
the shelve verdict; #2 tfav escalated to ACTIVE kill rec (gate crossed negative); #3 mmsell
softened from prune-candidate to breakeven-noise; #4 weather_con NEW pause-the-scale-up flag
after its first real drawdown (3 bad days of 6); #5 wcprop unchanged; #6 XGAME unchanged.
Loop gap Jul 6 PM → Jul 8 PM (usage limit) — this run is a single 2.6-day catch-up.)*
