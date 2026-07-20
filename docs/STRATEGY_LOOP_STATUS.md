# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query (total
$ P&L + WC/non-WC split — `mmsell_live` itself computes but never prints either). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs fable to
change anything. Newest snapshot replaces the one above it; the suggestion list carries over
run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-20 12:04 PM CDT (run #61)

**🔴 mmsell4 GATE RESOLVED — KILL.** Crossed its pre-registered n≥150 gate this run (n=155).
Per `docs/MMSELL_VARIANTS_THESIS.md`: *"PROMOTE if per-trade >+2¢ AND >mmsell3; KILL if per-trade
<mmsell3."*

| book | n | win% | ¢/trade | gate | verdict |
|---|---|---|---|---|---|
| mmsell3 (control) | 964 | 93.9% | +1.53¢ | — | baseline |
| **mmsell4** | 155 | 92.3% | **−0.06¢** | PROMOTE if >+2¢ AND >mmsell3; KILL if <mmsell3 | **KILL — below mmsell3 on both win% and per-trade** |

Clean bar's edges: "removing the −EV cohorts" (WC/tennis/cricket) did not lift the clean book
above the unfiltered mmsell3 control — the decomposition-driven hypothesis behind mmsell4 is
falsified. No further tracking needed once a fable session confirms; recommend flipping mmsell4
to collect-only like the theta1-3 pattern, or just letting it keep running as a free continued
observation (its own thesis doc should get a verdict entry either way).

**mmsell6 / mmsell11 — still PROMOTE, both strengthened further:**
| book | n | ¢/trade | note |
|---|---|---|---|
| mmsell6 | 302 (+15) | +2.06¢ | up from +1.84¢ — still clears >mmsell3 AND win%≥mmsell3 |
| mmsell11 | 196 (+12) | +3.47¢ | up from +3.27¢ — still clears >mmsell3, 2.3x+ the edge |

No new action beyond run #60's carried suggestion — still awaiting a fable session's promote
decision.

**Live P&L (real money — mmsell3):**
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 204 | 187 | +$1.92 | +0.94¢ |
| World Cup | 172 | 157 | +$0.11 | +0.06¢ |
| **TOTAL** | **376** | **344** | **+$2.02** | **+0.54¢** |

Continuing to improve (was +$1.68 at n=372) — non-WC bucket carried this run's gain, WC flat.

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 302 | +$6.23 | **+2.06** | 14 | PROMOTE confirmed, strengthened further |
| **mmsell11** | 196 | +$6.81 | **+3.47** | 14 | PROMOTE confirmed, strengthened further |
| **mmsell4** | 155 | −$0.09 | **−0.06** | 14 | **GATE RESOLVED — KILL**, see headline |
| mmsell10 | 92 | +$2.96 | +3.22 | 14 | 61% to its own gate (n≥150), batch improved |
| mmsell9 | 13 | +$0.68 | +5.2 | 9 | holding steady, still small n |
| mmsell control (paper) | 3,695 | +$65.95 | +1.79 | 33 | positive batch |
| mmsell2 (paper) | 1,578 | +$47.06 | +2.98 | 15 | positive batch |
| mmsell1 (paper) | 2,406 | +$49.77 | +2.07 | 24 | positive batch |
| mmsell3 (paper shadow) | 964 | +$14.73 | +1.53 | 16 | positive batch |
| mmsell5 | 95 | −$0.86 | −0.9 | 0 | no new settlements |
| mmsell7 | 38 | −$0.30 | **−0.79** | 7 | improved sharply from −4.2¢, still negative |
| mmsell8 | 17 | −$0.59 | −3.5 | 11 | no new settlements |
| **theta4** (fat-tail) | 37 | +$19.06 | +51.5 | **0** | **first settlements since run #60's "life" note** — 7 new, batch avg +45.6¢/trade |
| weather con (all) | 458 | −$10.54 | −2.30 | 6 | improved, still negative |
| weather_concity | 62 | −$5.39 | −8.69 | 2 | improved, 52% to gate |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — mmsell4's gate resolved to KILL (first gate-family member to fail cleanly);
mmsell6/mmsell11 both continue to strengthen past their already-cleared PROMOTE bars; theta4
settled its first trades since going quiet, still a strong batch average; live P&L up to +$2.02.**

mmsell4 crossed n≥150 this run and resolved exactly as its pre-registered gate specifies: per-
trade −0.06¢ and 92.3% win, both below mmsell3's +1.53¢/93.9% — the "clean book" hypothesis
(strip WC/tennis/cricket) did not lift performance above the unfiltered control. This is a
genuine falsification, not noise — the very batch that pushed it to nearly-breakeven last run
(+8.5¢) settled into a flat/negative one this run, and cumulative n is now large enough to trust.

mmsell6 and mmsell11 remain confirmed PROMOTE and both edges widened again this run (+1.84→
+2.06¢, +3.27→+3.47¢) — no change to the standing recommendation, just further confirmation.

theta4 posted its first real settlements (7 trades) since the "first sign of life" note last
run — batch averaged +45.6¢/trade, consistent with its historical fat-tail-favorable skew so
far, still only 37/80 (46%) to its own decision gate.

**Gate sweep (step 3b):** theta4 **37/80** (46%) · **mmsell6 CLEARED-PROMOTE (strengthening)** ·
**mmsell11 CLEARED-PROMOTE (strengthening)** · **mmsell4 GATE RESOLVED — KILL** · mmsell7 gate
n≥150 (25%) · mmsell5/8 gates n≥100 (95%/17%) · mmsell9 gate n≥100 (13%) · mmsell10 gate n≥150
(61%) · weather_concity **62/120** (52%) · FREEZE **5/100** (not fired, unchanged, 13 runs).

**Data (last-24h / latest CDT, ~12:04 PM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (11:55 AM–12:04 PM ✓). xgame_matches still dark, unchanged.
**xgame_tapes latest timestamp is UNCHANGED again** since run #59/#60 (still 2026-07-19
22:28:58 UTC — now ~18.6h stale) despite a large trailing 24h row count. This is the second
consecutive run with zero new rows — worth escalating from "glance" to an actual check next run
(the trailing-count-without-new-rows pattern is exactly what preceded the earlier confirmed
staleness episode).

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell4's pre-registered gate resolved — KILL (below mmsell3 on both legs at
n=155). mmsell6/mmsell11 both still PROMOTE and strengthening further. theta4 settled its first
trades since going quiet (+45.6¢/trade batch, 46% to gate). Live P&L up to +$2.02. xgame_tapes
now confirmed stale two runs running — needs an actual look, not just a glance.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[GATE RESOLVED · mmsell4 = KILL] n=155 (≥150 gate), −0.06¢/trade and 92.3% win — both below
   mmsell3 (+1.53¢/93.9%).** The "clean book" hypothesis (strip WC+tennis+cricket) is falsified:
   removing those cohorts did not lift the book above the unfiltered control. Recommend a fable
   session record the verdict in `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md` and
   decide whether to flip mmsell4 to collect-only (like theta1-3) or just let it keep running
   inert. Not urgent — it's paper capital — but the gate is resolved and shouldn't sit unrecorded.

2. **[mmsell6 AND mmsell11 still PROMOTE, strengthening further] mmsell6: n=302, +2.06¢/trade,
   still clears both legs vs mmsell3. mmsell11: n=196, +3.47¢/trade, still clears cleanly.**
   Unchanged recommendation from run #60: a fable session should decide whether to promote one,
   both, or combine the mechanisms (mmsell6's 5-8¢ band, mmsell11's `htcmin=6` no-late-entry)
   into the live mmsell3 config. This is now the top actionable item alongside #1.

3. **[mmsell10 · watch, gate 61%] n=92/150, +3.22¢/trade cumulative, batch improved from last
   run's weaker read (+0.6¢) to +5.5¢/trade.** Still the "highest-value result" candidate per its
   own thesis if it holds above mmsell3 at gate. No longer flagged as underperforming siblings —
   watch continues as it approaches its gate.

4. **[Live P&L · continuing to strengthen] Total +$2.02 (n=376), up from +$1.68.** This run's
   gain came entirely from the non-WC bucket (+0.94¢/contract, up from +0.8¢); WC stayed flat at
   ~breakeven. Continue tracking.

5. **[theta4 · first settlements since going quiet] 7 new settled trades this run, batch average
   +45.6¢/trade — consistent with its fat-tail-favorable historical skew.** Still only 37/80
   (46%) to its own n≥80 decision gate. No action; continue tracking toward the gate.

6. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted — unchanged from run #60,
   still the concrete next step once a promote decision is made.** NEST still behind theta4's
   n≥80 gate (46% there). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed
   event.

7. **[weather_concity / con(all) · improving, no gate yet] concity −8.69¢/trade (52% to gate, up
   from 47%), con(all) −2.30¢/trade (improved from −2.8¢).** Both moved in the right direction
   this run for the first time in several runs. Carry forward, not a gate event yet.

8. **[xgame_tapes · CONFIRMED stale two runs running — escalated from "glance"] Latest row
   timestamp unchanged for a second consecutive run (still 2026-07-19 22:28:58 UTC, ~18.6h now)
   despite a large trailing 24h count.** This is the same pattern that preceded the earlier
   confirmed staleness episode. Low-urgency (shelved book, no capital at risk), but recommend an
   actual look next run (collector logs / process health) rather than another "watch and see."

9. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 13 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 NEW/TOP — mmsell4's gate resolved to KILL, a genuine falsification, not a
promote. #2 mmsell6/mmsell11 — restated, both strengthened further. #3 mmsell10 — no longer
flagged weak, batch improved. #4 live P&L — up to +$2.02. #5 theta4 — first real settlements,
strong batch. #6 MMX — restated. #7 weather — both metrics improved this run. #8 xgame_tapes —
escalated from "worth a glance" to "confirmed stale, needs an actual look" after a second
unchanged-timestamp run. #9 restated/unchanged.)*
