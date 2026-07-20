# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query (total
$ P&L + WC/non-WC split — `mmsell_live` itself computes but never prints either). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs fable to
change anything. Newest snapshot replaces the one above it; the suggestion list carries over
run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-20 05:36 AM CDT (run #60)

**🟢 TWO GATES CONFIRMED PROMOTE — mmsell6 and mmsell11 both cleanly clear their pre-registered
criteria.** Pulled the win%/per-trade comparison flagged as outstanding last run:

| book | n | win% | ¢/trade | gate | verdict |
|---|---|---|---|---|---|
| mmsell3 (control) | 948 | 93.8% | +1.43¢ | — | baseline |
| **mmsell6** | 287 | **95.1%** | **+1.84¢** | PROMOTE if >mmsell3 AND win%≥mmsell3 | **CLEARED — both legs beat mmsell3** |
| **mmsell11** | 184 | 95.1% | **+3.27¢** | PROMOTE if >mmsell3 | **CLEARED — beats mmsell3 (2.3x the edge)** |

Both are now confirmed promote candidates per `docs/MMSELL_VARIANTS_THESIS.md`. **Next action for
a fable session:** mmsell11's price-ceiling-adjacent mechanism (`htcmin=6`, no-late-entry) and
mmsell6's tighter 5-8¢ band are both one-line changes promotable toward the live `mmsell3`
config — worth deciding which (or both) to build into the next live iteration.

**Live P&L (real money — mmsell3):**
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 200 | 183 | +$1.58 | +0.8¢ |
| World Cup | 172 | 157 | +$0.11 | +0.06¢ |
| **TOTAL** | **372** | **340** | **+$1.68** | **+0.5¢** |

Continuing to improve (was +$0.66 at n=360) — WC's cumulative average is now essentially
breakeven, a further reversal of its earlier drag.

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 287 | +$5.28 | **+1.84** | 20 | **PROMOTE confirmed** — see headline |
| **mmsell11** | 184 | +$6.02 | **+3.27** | 17 | **PROMOTE confirmed** — see headline |
| mmsell10 | 81 | +$2.35 | +2.9 | 16 | 54% to its own gate (n≥150), positive but weaker batch |
| mmsell9 | 13 | +$0.68 | +5.2 | 1 | holding steady, still small n |
| mmsell control (paper) | 3,662 | +$63.05 | +1.7 | 35 | strong positive batch (+10.2¢/trade) |
| mmsell2 (paper) | 1,565 | +$45.25 | +2.9 | 14 | strong positive batch (+11.7¢/trade) |
| mmsell1 (paper) | 2,384 | +$47.46 | +2.0 | 27 | positive batch (+10.1¢/trade) |
| mmsell3 (paper shadow) | 948 | +$13.60 | +1.4 | 21 | positive batch (+6.1¢/trade) |
| mmsell5 | 95 | −$0.86 | −0.9 | 0 | strong positive batch (+9.5¢/trade), nearly breakeven now |
| mmsell4 | 141 | −$1.07 | −0.8 | 19 | strong positive batch (+8.5¢/trade), nearly breakeven now |
| mmsell7 | 26 | −$1.09 | −4.2 | 14 | mild negative batch |
| mmsell8 | 17 | −$0.59 | −3.5 | 2 | no new settlements |
| **theta4** (fat-tail) | 30 | +$15.87 | +52.9 | **1** | **first sign of life in 6 runs** — new open position, no settlement yet |
| weather con (all) | 444 | −$12.52 | −2.8 | 15 | unchanged settled/P&L, +1 new open |
| weather_concity | 56 | −$5.64 | −10.1 | 7 | unchanged settled/P&L, +1 new open |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — two clean promote-gate clears (mmsell6, mmsell11), continued strengthening across
the whole cohort overnight, and theta4 shows its first sign of life in 6 runs.**

The recovery that began in run #59 continued: every mmsell variant had another positive batch
overnight. mmsell4 and mmsell5 (both still cumulative-negative) had their strongest batches yet
(+8.5¢ and +9.5¢/trade) and are now nearly breakeven. mmsell6 strengthened further past its
already-cleared gate (+1.55¢→+1.84¢/trade). **mmsell11 crossed its own n≥150 gate this run and
cleared cleanly** (+3.27¢/trade vs mmsell3's +1.43¢ — 2.3x the edge, no win% requirement to
clear).

theta4 opened its first new position in 6 runs (still 0 new settlements) — not a gate event, but
worth noting after such a long quiet stretch.

**Gate sweep (step 3b):** theta4 **30/80** (38%, first activity in 6 runs) · **mmsell6
CLEARED-PROMOTE** · **mmsell11 CLEARED-PROMOTE** · mmsell4/7 gates n≥150 (94%/17%) · mmsell5/8
gates n≥100 (95%/17%) · mmsell9 gate n≥100 (13%) · mmsell10 gate n≥150 (54%) · weather_concity
**56/120** (47%) · FREEZE **5/100** (not fired, unchanged, 12 runs).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (05:28–05:36 AM ✓). xgame_matches still dark, unchanged. xgame_tapes latest
timestamp unchanged since run #59 (2026-07-19 22:28:58 UTC, ~12h now) despite a large trailing
24h count — worth a glance next run to confirm it's still actually collecting live, not just
riding a large trailing window like the earlier stale-timestamp pattern.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell6 AND mmsell11 both confirmed PROMOTE (win%/per-trade both beat mmsell3) —
top actionable item for a fable session. Whole mmsell cohort strengthened overnight, mmsell4/5
nearly breakeven now. Live P&L continuing to improve (+$1.68), WC essentially breakeven.
theta4's first activity in 6 runs. xgame_tapes timestamp worth a glance next run.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[GATES CLEARED · mmsell6 AND mmsell11 both PROMOTE — top actionable item] mmsell6: n=287,
   +1.84¢/trade, 95.1% win (mmsell3: 93.8%) — both legs beat mmsell3, clean PROMOTE. mmsell11:
   n=184, +3.27¢/trade (2.3x mmsell3's +1.43¢) — clean PROMOTE, no win% requirement.**
   Recommend a fable session decide how to act: mmsell6 (tighter 5-8¢ band) and mmsell11
   (`htcmin=6`, skip the final in-play window) are both one-line changes toward the live
   mmsell3 config per `docs/MMSELL_VARIANTS_THESIS.md`. Worth deciding whether to promote one,
   both, or combine the mechanisms.

2. **[mmsell4/5 · nearly breakeven after strongest batches yet] mmsell4 −0.76¢/trade (was
   −2.66¢), mmsell5 −0.91¢/trade (was −4.03¢) — both had their best single batch of the whole
   cohort's history (+8.5¢ and +9.5¢/trade respectively).** Both still cumulative-negative but
   closing fast on breakeven. mmsell4 is 94% to its n≥150 gate — likely resolves within a run
   or two.

3. **[mmsell10 · watch, weaker batch than siblings] n=81/150 (54%), +2.9¢/trade cumulative, but
   this batch was notably weaker (+0.6¢/trade) than mmsell6/9/11's strong batches.** Still the
   "highest-value result" candidate per its own thesis (a pure price-ceiling change) if it holds
   above mmsell3 at gate. Watch closely — it's the one variant that didn't share fully in this
   run's strength.

4. **[Live P&L · continuing to strengthen] Total +$1.68 (n=372), up from +$0.66.** WC's
   cumulative average is now essentially breakeven (+0.06¢/contract) — a further reversal from
   its earlier −4.3¢/contract drag. Continue tracking; don't declare the WC drag "solved," just
   note it's currently not hurting.

5. **[theta4 · first sign of life in 6 runs] New open position (n still 30 settled).** Not a
   gate event — just noting the quiet stretch broke. No new action; watch for the first
   settlement next run.

6. **[idea-model queue · MMX/NEST — now more urgent given 2 promotes] With mmsell6 AND mmsell11
   both confirmed PROMOTE, MMX's premise (extend the mmsell edge into new categories) should be
   revisited against whichever mechanism(s) get promoted — this is no longer a hypothetical
   redundancy check, there's now a concrete winning config to extend.** NEST still behind
   theta4's n≥80 gate (38% there, first activity in 6 runs). RTPIN/BOXPIN behind unbuilt
   scraper infra. RATELAG behind a live Fed event.

7. **[weather_concity / con(all) · quiet, no new settles] concity −10.1¢/trade (47% to gate),
   con(all) −2.8¢/trade — both flat this run (new opens only).** Carry forward.

8. **[xgame_tapes · timestamp unchanged since run #59, worth a glance] Latest row unchanged
   (~12h now) despite a large trailing 24h count — the same pattern that preceded earlier
   staleness flags.** Low-urgency (shelved book), but worth confirming next run whether it's
   still actually collecting or has gone quiet again.

9. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 12 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 NEW/TOP — mmsell6 AND mmsell11 both confirmed clean PROMOTE with win%/
per-trade data pulled. #2 mmsell4/5 — nearly breakeven after strongest batches yet. #3 mmsell10
— flagged as the one variant with a weaker batch, worth watching. #4 live P&L — WC now
essentially breakeven. #5 theta4 — first activity in 6 runs. #6 MMX — elevated urgency given 2
confirmed promotes. #8 NEW — xgame_tapes timestamp flagged for a follow-up glance. #7/#9
restated/unchanged.)*
