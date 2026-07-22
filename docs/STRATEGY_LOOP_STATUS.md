# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query (total
$ P&L + WC/non-WC split — `mmsell_live` itself computes but never prints either). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs fable to
change anything. Newest snapshot replaces the one above it; the suggestion list carries over
run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-21 08:08 PM CDT (run #65) — CORRECTED same-day after operator review

**Correction to this run's original report:** the finding below was first written up as a "real-
money incident" (zero live orders for 2.5 days). The operator flagged that read as wrong and
follow-up investigation confirmed it — this is NOT an incident. Leaving both the corrected facts
and a note on what was wrong, so the record is honest rather than quietly overwritten.

**What's actually happening:** `mmsell3` LIVE trading was **deliberately wound down on
2026-07-19** (commit `cea0f72`, "Wind down mmsell3 live trading: closeout mechanism +
postmortem" — full writeup in `docs/MMSELL_LIVE_POSTMORTEM.md`). A fable session paused new live
entries and built a one-shot `mirror_mmsell_entry`-adjacent closeout path
(`LiveExecutor.close_mmsell_positions`, tagged `strategy='mmsell3_closeout'`) to flatten the
handful of open NO positions early, after the live book's edge kept eroding across two
decompositions (World Cup, then a head-to-head/price-band pattern that reattached to other
sports). Paper trading (`mmsell1-11`) was explicitly left running unaffected — exactly what the
loop has observed every run since.

**The closeout mechanism itself has a real, still-open bug** — the huge rejected-order count
(1,972+ over 2.5 days) is that one-shot closeout retrying every cycle and failing on API body
issues. There's an active fix chain in git history (`fc95d48` → `e9c451d` → `282bf1b` →
`ad98021`, the last reverting the previous fix attempt because it broke worse, 0/48 accepted) —
still unresolved as of the latest commit. **But it is financially inert:** every attempt has been
rejected or errored (zero fills, zero capital moved), and the one position it's failing to close
carries **$0.24 of exposure** (an MLB market fragment) — dust, not a real position. Confirmed
directly against `positions`: total open live exposure across the whole account is ~$0.48 (two
tiny fractional remnants, one weather, one mmsell-related, both effectively closed out already).

**What was wrong in the original write-up:** framed this as urgent/ongoing real-money risk
("REAL-MONEY INCIDENT," "real capital sitting completely out of the live book") when it's a
known, already-documented, intentional wind-down with a cosmetic retry-loop bug — not a fresh
operational failure. Escalating loudly was the right instinct for "live book stopped trading,"
but the loop should have found the postmortem doc / recent commit history before concluding it
was unexplained. Filing this as a process note: **future runs should check `git log` /
`docs/*POSTMORTEM*.md` for a deliberate-change explanation before escalating a live-trading
change as an incident.**

**Live P&L (real money — mmsell3) — also corrected: the prior number was inflated by a query
bug.** The loop's recurring query filtered `strategy LIKE 'mmsell%'`, which swept the
`mmsell3_closeout` tag in alongside `mmsell3` and double-counted a handful of tickers that
appeared under both tags in the join. Corrected to `strategy='mmsell3'` exactly:
| bucket | n settled | total P&L | note |
|---|---|---|---|
| **TOTAL (corrected)** | **367** | **+$1.33** | was reported as n=376 / +$2.02 for runs #61-65 — inflated by the closeout-tag double-count |

Still net positive, just smaller than previously reported. **Going forward, the loop's live P&L
query must use `strategy='mmsell3'` (exact match), not `LIKE 'mmsell%'`**, to avoid resweeping
the closeout tag. New live settlements will be rare/none going forward since new entries are
paused by the wind-down decision — flat live P&L runs ahead are expected, not a red flag.

**Paper books (settled n / P&L / per-trade / open) — all unaffected by the live incident:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 343 | +$8.81 | **+2.57** | 14 | PROMOTE confirmed, still strengthening |
| **mmsell11** | 243 | +$8.14 | +3.35 | 15 | PROMOTE confirmed, negative batch but well above mmsell3 |
| mmsell10 | 118 | +$4.40 | +3.73 | 13 | **79% to its own gate (n≥150)** — very close now |
| mmsell9 | 25 | +$1.36 | +5.4 | 6 | small n, positive |
| mmsell control (paper) | 3,861 | +$66.58 | +1.72 | 42 | positive batch |
| mmsell2 (paper) | 1,646 | +$49.89 | +3.03 | 18 | positive batch |
| mmsell1 (paper) | 2,503 | +$53.13 | +2.12 | 23 | positive batch |
| mmsell3 (paper shadow) | 1,017 | +$16.50 | +1.62 | 15 | slight negative batch |
| mmsell5 | 113 | −$0.32 | −0.28 | 2 | no new settlements |
| mmsell4 | 191 | +$1.50 | +0.79 | 14 | KILLED (run #61) — still unrecorded, edging up but still < mmsell3 |
| mmsell7 | 52 | −$0.30 | −0.58 | 2 | improving, gate n≥150 (35%) |
| mmsell8 | 29 | −$0.65 | −2.24 | 7 | improving, gate n≥100 (29%) |
| **theta4** (fat-tail) | 44 | +$15.78 | +35.9 | 0 | positive batch this run, 55% to its n≥80 gate |
| weather con (all) | 472 | −$10.60 | −2.25 | 17 | flat settled/P&L, +6 new opens |
| weather_concity | 68 | −$5.62 | −8.26 | 8 | flat settled/P&L, +2 new opens |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE (corrected) — mmsell3 LIVE was deliberately wound down 2026-07-19 (not an incident);
its closeout retry-loop bug is cosmetic (zero fills, $0.24 total stray exposure). Live P&L
corrected to +$1.33 (n=367) after fixing a strategy-filter double-count in the loop's own query.
Paper trading fully healthy and unaffected throughout.**

Otherwise a quiet paper picture. mmsell6/mmsell11 both still PROMOTE. mmsell10 is 79%
to its gate — very close. theta4 had a positive batch and crossed the halfway point of its own
gate at 55%.

**Gate sweep (step 3b):** theta4 **44/80** (55%) · **mmsell6 CLEARED-PROMOTE** · **mmsell11
CLEARED-PROMOTE** · **mmsell4 KILLED** (unchanged, still not recorded — now 4 runs) · mmsell7
gate n≥150 (35%) · mmsell8 gate n≥100 (29%) · mmsell9 gate n≥100 (25%) · **mmsell10 gate n≥150
(79%)** · weather_concity **68/120** (57%, unchanged) · FREEZE **5/100** (not fired, unchanged,
17 runs).

**Data (last-24h / latest CDT, ~8:08 PM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (7:46–8:01 PM ✓). xgame_matches still dark (expected). xgame_tapes
still 0 rows/24h (confirmed healthy lull, not re-flagging). **Deploy status: a new Railway
deployment was `DEPLOYING` at check time (started ~8:05 PM CT)** — separate from the collector
health picture, noted for context on the live-order incident above.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** Corrected same-day — mmsell3 LIVE's order flood was its known, intentional
2026-07-19 wind-down closeout mechanism retrying and failing (cosmetic bug, zero fills, ~$0.24
stray exposure), not a live incident. Live P&L corrected to +$1.33 (n=367). Paper trading fully
healthy. Otherwise: quiet paper picture, mmsell6/11 still PROMOTE, mmsell10 at 79% to its gate.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell3_closeout retry-loop — cosmetic bug, low priority, not urgent] The wind-down
   closeout mechanism (`docs/MMSELL_LIVE_POSTMORTEM.md`, commit `cea0f72`) has been retrying and
   failing every cycle since 2026-07-19 5:25 PM CT (API body bug — several fix attempts in git
   history, most recently reverted in `ad98021`). Zero fills throughout; total stray live
   exposure is ~$0.24 (one MLB market fragment).** Worth a fable session fixing the closeout body
   eventually so the log noise stops and that last fractional position actually closes, but there
   is no real money or urgency behind it — demoted from a top item.

2. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=343, +2.57¢/trade,
   still strengthening. mmsell11: n=243, +3.35¢/trade (negative batch this run but still well
   above mmsell3's +1.62¢).** Unchanged recommendation: a fable session should decide whether to
   promote one, both, or combine the mechanisms into the live mmsell3 config — though live
   mmsell3 itself is currently wound down (see `docs/MMSELL_LIVE_POSTMORTEM.md`), so any
   promotion decision is about the paper config / a future live restart, not an active live book.

3. **[mmsell4 · KILL verdict — still not recorded, 4 runs now] n=191, +0.79¢/trade cumulative,
   still below mmsell3's +1.62¢.** Recommend a fable session record the verdict in
   `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md`.

4. **[mmsell10 · very close, gate 79%] n=118/150, +3.73¢/trade cumulative, positive.** Likely
   resolves within the next run or two.

5. **[weather_concity · gate 57%, unchanged] n=68/120, −8.26¢/trade cumulative.** No change this
   run; still approaching its decision point.

6. **[theta4 · 55% to gate, positive batch] n=44/80, cumulative +35.9¢/trade, this batch
   positive (reversing the two prior negative batches).** Continue tracking toward the gate.

7. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted.** NEST still behind
   theta4's n≥80 gate (55% there).

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 17 runs now.** Standing background check, nothing to act on.

*(Changed this run, then CORRECTED same-day after operator review: the original #1 ("mmsell3 LIVE
down for 2.5 days, needs urgent confirmation") was wrong — it's the known, intentional 2026-07-19
wind-down's closeout mechanism retrying and failing (cosmetic, zero fills, ~$0.24 stray exposure),
not an incident. Demoted to a low-priority cleanup item. Live P&L also corrected: the loop's own
query had a strategy-filter bug (`LIKE 'mmsell%'` swept in the `mmsell3_closeout` tag) that
double-counted a few tickers — corrected from +$2.02/n=376 to +$1.33/n=367. #2 mmsell6/mmsell11 —
restated, noted that live mmsell3 itself is wound down so any promotion is about paper/a future
restart. #3 mmsell4 — restated, 4 runs unrecorded. #4 mmsell10 — very close (79%). #5
weather_concity — restated. #6 theta4 — positive batch, past halfway. #7 MMX/NEST — restated.
#8 restated/unchanged. Process note: future runs should check `git log` / postmortem docs for a
deliberate-change explanation before escalating a live-trading change as an incident.)*
