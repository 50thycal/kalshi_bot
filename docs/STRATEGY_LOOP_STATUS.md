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

## Snapshot — 2026-07-24 12:04 PM CDT (run #73)

**No gate crossings, but a broad negative batch across the ENTIRE mmsell cohort — traced to ONE
shared market event, not independent bad luck.** Every mmsell variant lost roughly the same
amount per-trade (~−41 to −49¢/trade) this run. Drilled into the underlying trades: nearly all
of it is a single market, `KXNBATEAMANNOUNCE-...LJAMES23` (an NBA "LeBron James team
announcement" contract) that every variant held a position in and that resolved unfavorably for
almost the whole cohort simultaneously around 11:07 AM CT (an earlier related strike settled
favorably at 7:50 AM CT). This is a correlated single-event loss, not a strategy-wide
degradation — mmsell6/mmsell11 still clear their PROMOTE gates despite the dip.

| book | n | realized P&L | ¢/trade (cum) | this batch |
|---|---|---|---|---|
| mmsell3 (control) | 1,026 (+2) | +$16.26 | +1.59¢ (was +1.67¢) | −41¢/trade |
| mmsell6 | 350 (+2) | +$8.26 | +2.36¢ (was +2.62¢) | −43¢/trade — still clears vs mmsell3 |
| mmsell11 | 252 (+2) | +$7.90 | +3.14¢ (was +3.49¢) | −41¢/trade — still clears vs mmsell3 |
| mmsell7 | 58 (+2) | −$0.77 | −1.33¢ (was +0.09¢) | −41¢/trade — back to negative after crossing positive last run |
| mmsell10 | 124 (+2) | +$3.74 | +3.02¢ (was +3.79¢) | −44¢/trade — 83% to its gate |

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
| **mmsell6** | 350 | +$8.26 | +2.36 | 29 | PROMOTE confirmed, hit by the shared-event loss |
| **mmsell11** | 252 | +$7.90 | +3.14 | 31 | PROMOTE confirmed, hit by the shared-event loss |
| mmsell10 | 124 | +$3.74 | +3.02 | 29 | 83% to its own gate, hit by the shared-event loss |
| mmsell9 | 26 | +$1.41 | +5.42 | 13 | no new settlements |
| mmsell control (paper) | 3,899 | +$62.37 | +1.60 | 44 | hit by the shared-event loss |
| mmsell2 (paper) | 1,659 | +$47.82 | +2.88 | 32 | hit by the shared-event loss |
| mmsell1 (paper) | 2,519 | +$51.23 | +2.03 | 40 | hit by the shared-event loss |
| mmsell3 (paper shadow) | 1,026 | +$16.26 | +1.59 | 34 | hit by the shared-event loss |
| mmsell5 | 115 | −$0.09 | −0.08 | 0 | no new settlements |
| mmsell4 | 199 | +$1.20 | +0.60 | 33 | KILLED (run #61) — still not recorded, hit by shared-event loss |
| mmsell7 | 58 | −$0.77 | −1.33 | 18 | back to negative cumulative, gate n≥150 (39%) |
| mmsell8 | 31 | −$0.51 | −1.65 | 14 | no new settlements |
| **theta4** (fat-tail) | 48 | +$17.78 | +37.0 | 0 | no new activity, 60% to gate |
| weather con (all) | 517 | −$12.03 | −2.33 | 3 | +13 settled, positive batch (improved) |
| weather_concity | 90 | −$8.18 | −9.09 | 0 | +6 settled, roughly flat, 75% to gate |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — a single shared-market event (an NBA "LeBron James team announcement" contract)
dragged nearly every mmsell variant's per-trade cumulative down together this run. This is
exactly the kind of correlated risk worth naming explicitly: the mmsell family isn't fully
independent — variants sharing the same ticker/event will move together on a single surprising
outcome. No gate crossings; mmsell6/mmsell11 still clear PROMOTE despite the hit. mmsell7 fell
back to negative cumulative after crossing positive last run — a reminder that was likely
small-n noise, not a real trend reversal.**

Weather books had a positive batch, in contrast — weather_concity now 75% to its gate.

**Gate sweep (step 3b):** theta4 **48/80** (60%, no new activity) · **mmsell6
CLEARED-PROMOTE** (holding despite the shared-loss hit) · **mmsell11 CLEARED-PROMOTE** (same) ·
**mmsell4 KILLED** (unchanged, still not recorded — now 12 runs) · mmsell7 gate n≥150 (39%,
back to cumulative-negative) · mmsell8 gate n≥100 (31%) · mmsell9 gate n≥100 (26%) · mmsell10
gate n≥150 (83%) · weather_concity **90/120** (75%, up from 70%) · FREEZE **6/100** (not fired,
unchanged, 25 runs).

**Data (last-24h / latest CDT, ~12:04 PM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (11:58 AM–12:03 PM ✓). xgame_matches still dark (expected — book
KILLED, collector-only). xgame_tapes still 0 rows/24h — consistent with the confirmed
healthy-lull explanation, not re-flagging.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** no gate events. A single shared-market event (NBA team-announcement contract) hit
nearly every mmsell variant's batch simultaneously — a real correlated-risk example worth
naming, not independent degradation. mmsell6/11 still PROMOTE despite it. mmsell7 dipped back
negative after one good batch. Weather books had a good batch; weather_concity now 75% to its
gate. Live P&L unchanged, confirmed stable.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=350, +2.36¢/trade
   (was +2.62¢, hit by the shared-event loss but still clears mmsell3). mmsell11: n=252,
   +3.14¢/trade (was +3.49¢, same).** Unchanged recommendation: a fable session should decide
   whether to promote one, both, or combine the mechanisms into the paper config — live mmsell3
   itself is currently wound down, so any promotion is about the paper book / a future live
   restart.

2. **[NEW · correlated-event risk observed directly] A single shared market
   (`KXNBATEAMANNOUNCE-...LJAMES23`) moved nearly every mmsell variant's batch together this
   run — concrete evidence the mmsell family isn't fully independent when variants share a
   ticker/event.** Not actionable by itself (this is inherent to running siblings off the same
   scan), but worth keeping in mind when interpreting any future "whole-cohort" batch move —
   check for a single shared ticker before treating it as a strategy-wide signal.

3. **[mmsell4 · KILL verdict — still not recorded, 12 runs now] n=199, +0.60¢/trade cumulative
   (dipped from +1.03¢ on the shared-event loss), still below mmsell3's +1.59¢.** Recommend a
   fable session record the verdict in `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md`.

4. **[mmsell10 · very close, gate 83%] n=124/150, +3.02¢/trade cumulative (dipped from +3.79¢ on
   the shared-event loss, still well positive).** Likely resolves within the next run or two.

5. **[weather_concity · gate 75%, positive batch] n=90/120, −9.09¢/trade cumulative (roughly
   flat this batch).** Getting close to its gate.

6. **[theta4 · 60% to gate, no new activity] n=48/80, cumulative +37.0¢/trade.** Continue
   tracking toward the gate.

7. **[mmsell7 · back to negative cumulative] n=58, −1.33¢/trade (was +0.09¢) — the positive
   cross last run didn't hold, consistent with it having been small-n noise rather than a real
   trend.** Still 39% to its own gate; continue tracking without over-reading either direction.

8. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted.** NEST still behind
   theta4's n≥80 gate (60% there).

9. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 6 of the n≥100 trigger, unchanged
   across 25 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 mmsell6/mmsell11 — restated, both dipped on the shared-event loss but
still clear their gates. #2 NEW — the correlated-event finding, worth keeping as a standing
interpretive note. #3 mmsell4 — restated, 12 runs unrecorded. #4 mmsell10 — restated (83%). #5
weather_concity — restated, closer to gate. #6 theta4 — restated. #7 mmsell7 — reversed back to
negative, framed as likely-noise consistent with prior small-n caveat. #8 MMX/NEST — restated.
#9 restated/unchanged.)*
