# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query. As of
run #65 (corrected), the live P&L query filters `strategy='mmsell3'` exactly — NOT `LIKE
'mmsell%'`, which incorrectly sweeps in the `mmsell3_closeout` wind-down tag and double-counts
tickers. mmsell3 LIVE trading itself was wound down 2026-07-19 (see run #65's snapshot and
`docs/MMSELL_LIVE_POSTMORTEM.md`) — new live settlements should be rare/none going forward; flat
live P&L is expected, not a red flag. **CLOSED 2026-07-22 (post-run-#68 investigation): the
account has been genuinely, verifiably 100% flat since 2026-07-20 10:20:56 CT — confirmed via
`live_orders`' last-ever row (closeout retries stopped 08:58 CT) and `positions`' last-ever row
(the final two stuck NO positions, `KXTRUMPSAY-26JUL20-URAN` and `KXRT-ODY-95`, settled
NATURALLY — not via the broken closeout mechanism — at 9:51 AM and 10:20:56 AM CT respectively,
realizing +$0.06/+$0.11, both already included in the running total). `mmsell3_closeout` is
gated by `mmsell_closeout_enabled` (defaults False in code, toggled via a Railway env var not
visible in git) — it silently returns 0 every cycle now, which is why the retry-storm stopped
with no errors logged. The loop's flat live P&L across runs #65-68 was accurate the whole time,
not stale — do not re-flag this as a data-staleness concern going forward unless something
actually changes.** Suggestions are **recommendations only** — the loop never acts on them; the
user reviews and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

*Reporting convention (confirmed 2026-07-23, standing going forward): every run's chat report
and this file must state, for both the live P&L table and each paper book in the books table,
the **realized P&L (total $)** AND the **per-trade profit (¢/trade)** side by side — not one or
the other. This has been the practice since run #56 (live) / since inception (paper per-trade
column); this note locks it in explicitly so it doesn't drift.*

---

## Snapshot — 2026-07-23 12:04 PM CDT (run #70)

**No gate crossings this run.** Entire mmsell cohort had zero new settlements again (open counts
rose a lot instead — mmsell 33→42, mmsell6 14→18, mmsell10 14→18, mmsell11 17→20 — the same
entries-outpacing-settlements pattern seen in run #68, not a concern). Weather books, by
contrast, had a good batch this run.

**Weather books reversed positive after the weaker batch two runs ago:**
| book | n | realized P&L | ¢/trade (cum) | this batch |
|---|---|---|---|---|
| weather_con(all) | 504 (+15) | −$12.66 | −2.51¢ (was −2.79¢) | +6.7¢/trade batch |
| weather_concity | 84 (+8) | −$7.97 | −9.49¢ (was −11.68¢) | +11.4¢/trade batch — **70% to gate** |

**theta4** settled 2 more trades, another solidly positive batch (+58¢/trade) — cumulative
realized +$17.69, +37.6¢/trade, now 47/80 (59%) to its gate.

**Live P&L (real money — mmsell3):** unchanged, as expected — the account remains confirmed
100% flat since 2026-07-20 10:20:56 CT.
| bucket | n settled | wins | realized P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 202 | 185 | +$1.75 | +0.86¢ |
| World Cup | 165 | 150 | −$0.41 | −0.25¢ |
| **TOTAL** | **367** | **335** | **+$1.33** | **+0.36¢** |

**Trading books (settled n / realized P&L / ¢-per-trade / open) — PAPER only, separate from live
above:**
| book | n | realized P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 346 | +$9.01 | +2.60 | 18 | PROMOTE confirmed, no new settlements, +4 open |
| **mmsell11** | 247 | +$8.52 | +3.45 | 20 | PROMOTE confirmed, no new settlements, +3 open |
| mmsell10 | 120 | +$4.51 | +3.76 | 18 | 80% to its own gate, no new settlements, +4 open |
| mmsell9 | 25 | +$1.36 | +5.4 | 10 | no new settlements |
| mmsell control (paper) | 3,880 | +$68.62 | +1.77 | 42 | no new settlements, +9 open |
| mmsell2 (paper) | 1,651 | +$50.66 | +3.07 | 23 | no new settlements, +7 open |
| mmsell1 (paper) | 2,509 | +$53.97 | +2.15 | 29 | no new settlements, +7 open |
| mmsell3 (paper shadow) | 1,021 | +$16.88 | +1.65 | 20 | no new settlements, +3 open |
| mmsell5 | 115 | −$0.09 | −0.08 | 0 | no new settlements |
| mmsell4 | 195 | +$1.88 | +0.96 | 19 | KILLED (run #61) — still not recorded, no new settlements |
| mmsell7 | 54 | −$0.09 | −0.17 | 4 | no new settlements |
| mmsell8 | 29 | −$0.65 | −2.24 | 12 | no new settlements |
| **theta4** (fat-tail) | 47 | +$17.69 | +37.6 | 0 | +2 settlements, positive batch, 59% to gate |
| weather con (all) | 504 | −$12.66 | −2.51 | 4 | +15 settled, good batch (see above) |
| weather_concity | 84 | −$7.97 | −9.49 | 1 | +8 settled, good batch, 70% to gate |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — no gate crossings, but two solid pieces of good news: weather_con/weather_concity
both had a strong batch (reversing the weaker run two cycles ago), and theta4 continues its
positive streak, now 59% to its own gate. mmsell cohort itself was quiet on settlements again
(entries outpacing closes), unchanged from the last two similar runs.**

**Gate sweep (step 3b):** theta4 **47/80** (59%) · **mmsell6 CLEARED-PROMOTE** · **mmsell11
CLEARED-PROMOTE** · **mmsell4 KILLED** (unchanged, still not recorded — now 9 runs) · mmsell7
gate n≥150 (36%) · mmsell8 gate n≥100 (29%) · mmsell9 gate n≥100 (25%) · mmsell10 gate n≥150
(80%, unchanged) · weather_concity **84/120** (70%, up from 63%) · FREEZE **6/100** (not fired,
unchanged, 22 runs).

**Data (last-24h / latest CDT, ~12:04 PM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (11:55 AM–12:04 PM ✓). xgame_matches still dark (expected — book
KILLED, collector-only). xgame_tapes still 0 rows/24h — consistent with the confirmed
healthy-lull explanation, not re-flagging.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** no gate events. Weather books (con + concity) both had a strong reversal batch —
weather_concity now 70% to its gate. theta4 continues positive, 59% to its own gate. mmsell
cohort quiet on settlements again but entries are active (open counts way up). Live P&L
unchanged, confirmed stable.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=346, +2.60¢/trade.
   mmsell11: n=247, +3.45¢/trade.** No new settlements this run. Unchanged recommendation: a
   fable session should decide whether to promote one, both, or combine the mechanisms into the
   paper config — live mmsell3 itself is currently wound down, so any promotion is about the
   paper book / a future live restart.

2. **[mmsell3_closeout — resolved, no remaining exposure] Both stuck positions settled naturally
   on 2026-07-20; the mechanism is disabled and inert.** No further tracking needed unless a
   future live restart reuses it.

3. **[mmsell4 · KILL verdict — still not recorded, 9 runs now] n=195, +0.96¢/trade cumulative,
   still below mmsell3's +1.65¢.** Recommend a fable session record the verdict in
   `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md`.

4. **[mmsell10 · very close, gate 80%, unchanged] n=120/150, +3.76¢/trade cumulative.** Likely
   resolves once settlements resume.

5. **[weather_concity · strong batch, gate 70%] n=84/120, −9.49¢/trade cumulative (was
   −11.68¢) — a clearly positive reversal batch (+11.4¢/trade).** Getting close to its gate;
   worth a closer look once it crosses n≥120.

6. **[theta4 · 59% to gate, consistent positive streak] n=47/80, cumulative +37.6¢/trade, another
   solidly positive batch.** Continue tracking toward the gate.

7. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted.** NEST still behind
   theta4's n≥80 gate (59% there, getting close).

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 6 of the n≥100 trigger, unchanged
   across 22 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 mmsell6/mmsell11 — restated, no new activity. #2 mmsell3_closeout —
restated as resolved. #3 mmsell4 — restated, 9 runs unrecorded. #4 mmsell10 — unchanged (80%),
no new activity. #5 weather_concity — strong reversal batch, closer to gate (70%, was 63%). #6
theta4 — consistent positive streak, closer to gate (59%, was 56%). #7 MMX/NEST — restated,
NEST getting close. #8 restated/unchanged.)*
