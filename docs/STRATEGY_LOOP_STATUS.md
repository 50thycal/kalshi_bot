# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query (total
$ P&L + WC/non-WC split — `mmsell_live` itself computes but never prints either). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs fable to
change anything. Newest snapshot replaces the one above it; the suggestion list carries over
run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-19 08:03 PM CDT (run #59)

**🟢 GATE CLEARED — mmsell6 crossed n≥150 with a favorable preliminary read: n=251,
+1.55¢/trade, beats mmsell3's own +1.17¢/trade (one of two PROMOTE criteria).** Its
pre-registered gate (`docs/MMSELL_VARIANTS_THESIS.md`) is: PROMOTE if per-trade > mmsell3 AND
win% ≥ mmsell3. This loop's query doesn't carry per-book win% (only P&L), so the per-trade leg
is confirmed favorable but **the win% comparison still needs a fable-session check before calling
this a clean promote** — flagging it now rather than letting a cleared gate go quiet. **Next
action: a fable session pull win% for mmsell6 vs mmsell3 to close out the promote decision.**

**Live P&L (real money — mmsell3):**
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 200 | 183 | +$1.58 | +0.8¢ |
| World Cup | 160 | 145 | −$0.91 | −0.6¢ |
| **TOTAL** | **360** | **328** | **+$0.66** | **+0.2¢** |

Live flipped back to **positive** (was −$3.60 at n=298) on a big batch (62 new trades, +$4.26) —
and notably, **World Cup actively helped this time** (+$3.60 on 54 new WC trades), a reversal of
its usual drag pattern. WC's cumulative average improved from −4.3¢ to −0.6¢/contract.

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 251 | +$3.89 | **+1.55** | 42 | **GATE CLEARED (n≥150)** — see headline |
| mmsell9 | 10 | +$0.53 | +5.3 | 3 | first real data, positive |
| mmsell10 | 60 | +$2.23 | +3.7 | 33 | first real data, positive — the "highest-value" variant |
| mmsell11 | 133 | +$2.93 | +2.2 | 48 | 89% to its own n≥150 gate |
| mmsell control (paper) | 3,574 | +$54.07 | +1.5 | 79 | strong positive batch (+8.9¢/trade) |
| mmsell1 (paper) | 2,317 | +$40.71 | +1.8 | 67 | strong positive batch (+5.5¢/trade) |
| mmsell2 (paper) | 1,518 | +$39.76 | +2.6 | 44 | strong positive batch (+7.6¢/trade) |
| mmsell3 (paper shadow) | 897 | +$10.51 | +1.2 | 51 | positive batch (+2.9¢/trade) |
| mmsell4 | 117 | −$3.11 | −2.7 | 25 | mild positive batch, still negative cumulative |
| mmsell5 | 73 | −$2.94 | −4.0 | 8 | positive batch, still negative cumulative |
| mmsell7 | 16 | −$0.85 | −5.3 | 21 | positive batch, still negative cumulative |
| mmsell8 | 17 | −$0.59 | −3.5 | 0 | positive batch, still negative cumulative |
| theta4 (fat-tail) | 30 | +$15.87 | +52.9 | 0 | unchanged, 6th run with no new settles |
| weather con (all) | 444 | −$12.52 | −2.8 | 14 | unchanged settled/P&L, +4 new opens |
| weather_concity | 56 | −$5.64 | −10.1 | 6 | unchanged settled/P&L |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — the drawdown from runs #56-57 is confirmed over: this was a broadly strong batch
across the entire mmsell cohort, live and paper, and a real gate cleared.** Every single mmsell
variant (control through mmsell11) had a positive batch tonight — the first time since the
drawdown began that ALL of them moved the same direction. mmsell6 crossed its n≥150 gate with a
favorable per-trade read (needs win% confirmation). The new 2nd-cohort variants (mmsell9/10/11)
posted their first real settlements, all positive — mmsell11 is 89% to its own gate.

theta4 remains quiet — 6th straight run with no new settlements since the tail hit in run #53.
weather books had no new settlements this run (opens only).

**Gate sweep (step 3b):** theta4 **30/80** (38%, quiet) · **mmsell6 251/150 — CLEARED, win%
check needed** · mmsell4/7 gates n≥150 (78%/11%) · mmsell5/8 gates n≥100 (73%/17%) · mmsell9
gate n≥100 (10%) · mmsell10 gate n≥150 (40%) · mmsell11 gate n≥150 (89%, closest) ·
weather_concity **56/120** (47%) · FREEZE **5/100** (not fired, unchanged, 11 runs).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (07:39–08:01 PM ✓). xgame_tapes very active (111,549 rows/24h);
xgame_matches still dark, unchanged.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** GATE CLEARED — mmsell6 crossed n≥150 with a favorable read, win% check needed to
confirm promote. Live P&L flipped positive (+$0.66), WC helped for once. Every mmsell variant
had a positive batch — the drawdown is over. mmsell9/10/11 posted first real data, all positive.
theta4 quiet. FREEZE unchanged.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[GATE CLEARED · mmsell6 — win% confirmation needed to close out PROMOTE] n=251/150,
   +1.55¢/trade beats mmsell3's +1.17¢/trade.** Pre-registered gate needs win% ≥ mmsell3 too,
   which this loop's query doesn't carry. **Recommend a fable session pull the win% comparison**
   and, if it holds, treat mmsell6 as a promote candidate per `docs/MMSELL_VARIANTS_THESIS.md`.
   This is the top actionable item this run.

2. **[Live P&L · flipped positive, WC helped for once] Total +$0.66 (n=360), up from −$3.60.**
   The runs #56-57 drawdown is resolved on the live side too. WC's usual drag reversed this
   batch (+$3.60 on 54 new WC trades) — don't read this as "WC problem solved," just note the
   variance goes both directions. Continue tracking total-$ and the WC/non-WC split every run.

3. **[mmsell paper cohort · drawdown CONFIRMED OVER — every variant positive this batch]
   control/1/2/3/4/5/6/7/8 all had positive batches tonight, the first time since the drawdown
   began that every variant moved the same direction.** Downgrading from run #58's "not yet
   confirmed recovery" — this is confirmed. The fable-session investigation suggested in run #57
   is no longer urgent given the recovery, though still fine to do for general understanding.

4. **[mmsell9/10/11 · first real data, all positive] mmsell9 +5.3¢ (n=10), mmsell10 +3.7¢
   (n=60, the flagged "highest-value" variant), mmsell11 +2.2¢ (n=133, 89% to its own gate —
   closest of any variant to resolving next).** Watch mmsell11 closely; it may gate within the
   next run or two.

5. **[theta4 · quiet, 6th run with no new settles] n=30/80 (38%), +52.9¢/trade, unchanged since
   the tail hit in run #53.** No new action. Continue watching the realized hit rate as more
   trades settle.

6. **[idea-model queue · MMX/NEST unchanged] MMX's premise still worth checking against the
   now-8-variant mmsell4-11 cohort for redundancy (unresolved since run #49) — with mmsell6
   gating and mmsell11 close behind, this check is increasingly relevant.** NEST still behind
   theta4's n≥80 gate (38% there). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a
   live Fed event.

7. **[weather_concity / con(all) · quiet, no new settles] concity −10.1¢/trade (47% to gate),
   con(all) −2.8¢/trade — both flat this run (new opens only).** Carry forward.

8. **[xgame_tapes / xgame_matches · stable, unchanged] xgame_tapes very active; xgame_matches
   still dark.** Both low-urgency, no new information.

9. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 11 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 NEW — mmsell6 gate CLEARED, top actionable item, needs win% confirmation.
#2 live P&L — confirmed flipped positive. #3 mmsell paper cohort — drawdown CONFIRMED OVER
(all variants positive), downgraded from "watch." #4 mmsell9/10/11 — first real data, all
positive, mmsell11 closest to its gate. #6 MMX — updated urgency given mmsell6's clearance.
#5/#7/#8/#9 restated/unchanged.)*
