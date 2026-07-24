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

## Snapshot — 2026-07-23 08:01 PM CDT (run #71)

**No gate crossings, but a notable split batch worth flagging without overreacting.** The
original, unfiltered mmsell books (control/1/2) all had a **negative** batch this run, while
every promoted/newer variant (mmsell3/6/7/8/9/10/11) had a **positive** batch on the same market
flow — the opposite pattern from run #62's version of this split. Single 8-hour window on
books this large (2,500–3,900 n) — small-n discipline says don't call it a trend yet.

| book | n | realized P&L | ¢/trade (cum) | this batch |
|---|---|---|---|---|
| mmsell (control) | 3,892 (+12) | +$65.73 | +1.69¢ | **−24.1¢/trade** |
| mmsell1 | 2,516 (+7) | +$52.69 | +2.09¢ | **−18.3¢/trade** |
| mmsell2 | 1,656 (+5) | +$49.28 | +2.98¢ | **−27.6¢/trade** |
| mmsell3 (shadow) | 1,024 (+3) | +$17.08 | +1.67¢ | +6.7¢/trade |
| mmsell7 | 56 (+2) | +$0.05 | **+0.09¢** | +7¢/trade — **crossed to positive cumulative for the first time** |

**Live P&L (real money — mmsell3):** unchanged, as expected — the account remains confirmed
100% flat since 2026-07-20 10:20:56 CT.
| bucket | n settled | wins | realized P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 202 | 185 | +$1.75 | +0.86¢ |
| World Cup | 165 | 150 | −$0.41 | −0.25¢ |
| **TOTAL** | **367** | **335** | **+$1.33** | **+0.36¢** |

**Trading books (settled n / realized P&L / ¢-per-trade / open) — PAPER only, separate from
live above:**
| book | n | realized P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 348 | +$9.12 | +2.62 | 18 | PROMOTE confirmed, essentially flat this run |
| **mmsell11** | 250 | +$8.72 | +3.49 | 18 | PROMOTE confirmed, positive batch |
| mmsell10 | 122 | +$4.62 | +3.79 | 18 | 81% to its own gate, positive batch |
| mmsell9 | 26 | +$1.41 | +5.42 | 10 | small n, positive |
| mmsell control (paper) | 3,892 | +$65.73 | +1.69 | 33 | negative batch (see above), still strongly cumulative-positive |
| mmsell2 (paper) | 1,656 | +$49.28 | +2.98 | 20 | negative batch (see above) |
| mmsell1 (paper) | 2,516 | +$52.69 | +2.09 | 24 | negative batch (see above) |
| mmsell3 (paper shadow) | 1,024 | +$17.08 | +1.67 | 18 | positive batch |
| mmsell5 | 115 | −$0.09 | −0.08 | 0 | no new settlements |
| mmsell4 | 197 | +$2.02 | +1.03 | 17 | KILLED (run #61) — still not recorded, closing the gap to mmsell3 but still below |
| **mmsell7** | 56 | +$0.05 | **+0.09** | 3 | **crossed to positive cumulative for the first time**, gate n≥150 (37%) |
| mmsell8 | 31 | −$0.51 | −1.65 | 11 | improving, gate n≥100 (31%) |
| **theta4** (fat-tail) | 48 | +$17.78 | +37.0 | 0 | +1 settlement, 60% to gate |
| weather con (all) | 504 | −$12.66 | −2.51 | 13 | no new settlements, +9 open |
| weather_concity | 84 | −$7.97 | −9.49 | 6 | no new settlements, +5 open |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — a split batch (older unfiltered mmsell books negative, newer/promoted variants
positive) — a single-window curiosity, not a trend. mmsell7 notably crossed to positive
cumulative for the first time. No gate crossings; theta4 crossed 60% to its own gate.**

**Gate sweep (step 3b):** theta4 **48/80** (60%) · **mmsell6 CLEARED-PROMOTE** · **mmsell11
CLEARED-PROMOTE** · **mmsell4 KILLED** (unchanged, still not recorded — now 10 runs) · mmsell7
gate n≥150 (37%, now cumulative-positive) · mmsell8 gate n≥100 (31%) · mmsell9 gate n≥100 (26%)
· mmsell10 gate n≥150 (81%) · weather_concity **84/120** (70%, unchanged) · FREEZE **6/100**
(not fired, unchanged, 23 runs).

**Data (last-24h / latest CDT, ~8:01 PM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (7:51 PM–1:02 AM ✓ — some rows dated just past midnight UTC,
still within the last 24h window). xgame_matches still dark (expected — book KILLED,
collector-only). xgame_tapes still 0 rows/24h — consistent with the confirmed healthy-lull
explanation, not re-flagging.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** no gate events. A split batch (older mmsell books negative, newer variants
positive) — flagged, not alarmed, single window on large-n books. mmsell7 crossed to positive
cumulative for the first time. theta4 now 60% to its gate. Live P&L unchanged, confirmed
stable.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=348, +2.62¢/trade.
   mmsell11: n=250, +3.49¢/trade.** Unchanged recommendation: a fable session should decide
   whether to promote one, both, or combine the mechanisms into the paper config — live mmsell3
   itself is currently wound down, so any promotion is about the paper book / a future live
   restart.

2. **[mmsell4 · KILL verdict — still not recorded, 10 runs now] n=197, +1.03¢/trade cumulative
   (closing the gap but still below mmsell3's +1.67¢).** Recommend a fable session record the
   verdict in `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md` — this has now sat
   unrecorded for 10 runs.

3. **[mmsell10 · very close, gate 81%] n=122/150, +3.79¢/trade cumulative, positive batch.**
   Likely resolves within the next run or two.

4. **[weather_concity · gate 70%, unchanged] n=84/120, −9.49¢/trade cumulative.** No new
   settlements this run; still approaching its decision point.

5. **[theta4 · 60% to gate] n=48/80, cumulative +37.0¢/trade.** Continue tracking toward the
   gate.

6. **[mmsell7 · crossed to positive cumulative for the first time] n=56, now +0.09¢/trade (was
   −0.17¢).** Still small n and 37% to its own gate (n≥150) — worth watching whether this holds
   as n grows, not a gate event yet.

7. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted.** NEST still behind
   theta4's n≥80 gate (60% there, getting close).

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 6 of the n≥100 trigger, unchanged
   across 23 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 mmsell6/mmsell11 — restated. #2 mmsell4 — restated, 10 runs unrecorded,
gap closing slightly. #3 mmsell10 — restated (81%). #4 weather_concity — restated, unchanged
this run. #5 theta4 — restated, closer (60%). #6 NEW — mmsell7 crossed to positive cumulative
for the first time, worth watching. #7 MMX/NEST — restated. #8 restated/unchanged. Split-batch
observation (older vs newer mmsell books) noted in the snapshot but not carried as its own
numbered item — single window, small-n discipline applies.)*
