# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query. As of
run #65 (corrected), the live P&L query filters `strategy='mmsell3'` exactly — NOT `LIKE
'mmsell%'`, which incorrectly sweeps in the `mmsell3_closeout` wind-down tag and double-counts
tickers. mmsell3 LIVE trading itself was wound down 2026-07-19 (see run #65's snapshot and
`docs/MMSELL_LIVE_POSTMORTEM.md`) — new live settlements should be rare/none going forward; flat
live P&L is expected, not a red flag. Suggestions are **recommendations only** — the loop never
acts on them; the user reviews and runs fable to change anything. Newest snapshot replaces the
one above it; the suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-22 05:36 AM CDT (run #66)

**Quiet run — no gate crossings.** Small positive batches across most of the mmsell cohort,
nothing changed status.

| book | n | ¢/trade (cum) | Δ this run |
|---|---|---|---|
| mmsell3 (control) | 1,020 | +1.64¢ | +3 n, +9¢/trade batch |
| mmsell6 | 345 | +2.59¢ | +2 n, +7¢/trade batch — PROMOTE confirmed |
| mmsell11 | 246 | +3.42¢ | +3 n, +9¢/trade batch — PROMOTE confirmed |
| mmsell4 | 194 | +0.91¢ | +3 n, +9¢/trade batch — still below mmsell3, KILL verdict stands |
| mmsell5 | 115 | −0.08¢ | +2 n, positive batch — now nearly exactly breakeven |
| mmsell7 | 53 | −0.38¢ | +1 n, one strong win — improved a lot from −1.76¢ |

**Live P&L (real money — mmsell3, corrected query):** stable, as expected — mmsell3 live was
wound down 2026-07-19 and no new live settlements are expected going forward.
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 202 | 185 | +$1.75 | +0.86¢ |
| World Cup | 165 | 150 | −$0.41 | −0.25¢ |
| **TOTAL** | **367** | **335** | **+$1.33** | **+0.36¢** |

(Unchanged from the run #65 correction — confirms the wind-down is holding and no new live
capital is being risked.)

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 345 | +$8.95 | **+2.59** | 12 | PROMOTE confirmed, incremental gain |
| **mmsell11** | 246 | +$8.41 | +3.42 | 13 | PROMOTE confirmed, incremental gain |
| mmsell10 | 119 | +$4.45 | +3.74 | 12 | 79% to its own gate (n≥150), flat batch |
| mmsell9 | 25 | +$1.36 | +5.4 | 6 | no new settlements |
| mmsell control (paper) | 3,879 | +$68.30 | +1.76 | 24 | positive batch |
| mmsell2 (paper) | 1,650 | +$50.47 | +3.06 | 14 | positive batch |
| mmsell1 (paper) | 2,508 | +$53.78 | +2.14 | 18 | positive batch |
| mmsell3 (paper shadow) | 1,020 | +$16.77 | +1.64 | 13 | positive batch |
| mmsell5 | 115 | −$0.09 | −0.08 | 0 | nearly exactly breakeven now |
| mmsell4 | 194 | +$1.77 | +0.91 | 12 | KILLED (run #61) — still not recorded, edging up but still < mmsell3 |
| mmsell7 | 53 | −$0.20 | −0.38 | 2 | big improvement this batch, gate n≥150 (35%) |
| mmsell8 | 29 | −$0.65 | −2.24 | 7 | no new settlements, gate n≥100 (29%) |
| **theta4** (fat-tail) | 44 | +$15.78 | +35.9 | 0 | no new activity this run, 55% to gate |
| weather con (all) | 472 | −$10.60 | −2.25 | 19 | flat settled/P&L, +2 new opens |
| weather_concity | 68 | −$5.62 | −8.26 | 9 | flat settled/P&L, +1 new open |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — genuinely quiet run: small positive batches across most of the paper cohort,
nothing crossed a gate, live P&L holding stable at the corrected +$1.33 with no new settlements
(expected, given the wind-down).**

mmsell5 is now essentially exactly breakeven (−0.08¢/trade) after a string of positive batches.
mmsell7 had a standout single-trade win that pulled its cumulative from −1.76¢ to −0.38¢/trade —
still negative but a notable jump; too small n (53) to read much into one trade.

**Gate sweep (step 3b):** theta4 **44/80** (55%, no new activity) · **mmsell6
CLEARED-PROMOTE** · **mmsell11 CLEARED-PROMOTE** · **mmsell4 KILLED** (unchanged, still not
recorded — now 5 runs) · mmsell7 gate n≥150 (35%) · mmsell8 gate n≥100 (29%) · mmsell9 gate
n≥100 (25%) · mmsell10 gate n≥150 (79%, unchanged) · weather_concity **68/120** (57%, unchanged)
· FREEZE **5/100** (not fired, unchanged, 18 runs).

**Data (last-24h / latest CDT, ~5:36 AM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (5:16–5:36 AM ✓). xgame_matches still dark (expected — book
KILLED, collector-only). xgame_tapes still 0 rows/24h — consistent with the healthy-lull
explanation confirmed a few runs back, not re-flagging.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** quiet run, no gate events. Small positive batches across most of the mmsell
cohort — mmsell5 now essentially exactly breakeven, mmsell7 jumped on one big win. mmsell6/11
still PROMOTE. Live P&L holding stable at the corrected +$1.33, no new settlements as expected
post-wind-down.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=345, +2.59¢/trade.
   mmsell11: n=246, +3.42¢/trade.** Both continue to edge up incrementally. Unchanged
   recommendation: a fable session should decide whether to promote one, both, or combine the
   mechanisms (mmsell6's 5-8¢ band, mmsell11's `htcmin=6` no-late-entry) into the paper config —
   note live mmsell3 itself is currently wound down (`docs/MMSELL_LIVE_POSTMORTEM.md`), so any
   promotion is about the paper book / a future live restart, not an active live position.

2. **[mmsell3_closeout retry-loop — cosmetic bug, low priority] The wind-down closeout mechanism
   has been retrying and failing since 2026-07-19 5:25 PM CT (API body bug, several fix attempts
   in git history, most recently reverted). Zero fills; total stray live exposure ~$0.24.** Worth
   a fable session eventually fixing the closeout body so the retry noise stops, but no real
   money or urgency behind it.

3. **[mmsell4 · KILL verdict — still not recorded, 5 runs now] n=194, +0.91¢/trade cumulative,
   still below mmsell3's +1.64¢.** Recommend a fable session record the verdict in
   `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md` — this has now sat unrecorded across 5
   runs since resolving.

4. **[mmsell10 · very close, gate 79%, unchanged] n=119/150, +3.74¢/trade cumulative.** Likely
   resolves within the next run or two.

5. **[weather_concity · gate 57%, unchanged] n=68/120, −8.26¢/trade cumulative.** No change this
   run; still approaching its decision point.

6. **[theta4 · 55% to gate, no new activity] n=44/80, cumulative +35.9¢/trade.** Continue
   tracking toward the gate.

7. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted.** NEST still behind
   theta4's n≥80 gate (55% there).

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 18 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 mmsell6/mmsell11 — restated, both edged up. #2 mmsell3_closeout —
restated, cosmetic/low-priority. #3 mmsell4 — restated, 5 runs unrecorded. #4 mmsell10 —
unchanged (79%). #5 weather_concity — unchanged (57%). #6 theta4 — unchanged, no new activity.
#7 MMX/NEST — restated. #8 restated/unchanged. mmsell5/mmsell7 — noted as individually improving
in the snapshot table but not carried as their own suggestion items; neither is near a gate.)*
