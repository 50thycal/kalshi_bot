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

## Snapshot — 2026-07-23 05:35 AM CDT (run #69)

**Quiet run — no gate crossings.** A small even batch across most mmsell books (+1 settlement
each), no notable moves.

| book | n | ¢/trade (cum) | Δ this run |
|---|---|---|---|
| mmsell3 (control) | 1,021 | +1.65¢ | +1 n |
| mmsell6 | 346 | +2.60¢ | +1 n, essentially flat — PROMOTE confirmed |
| mmsell11 | 247 | +3.45¢ | +1 n — PROMOTE confirmed |
| mmsell4 | 195 | +0.96¢ | +1 n, still below mmsell3 — KILL verdict stands |
| mmsell10 | 120 | +3.76¢ | +1 n — **80% to its own gate (n≥150)** |
| theta4 | 45 | +36.7¢ | +1 n, positive settlement — 56% to its gate |

**Live P&L (real money — mmsell3):** unchanged, as expected — the account has been confirmed
100% flat since 2026-07-20 10:20:56 CT (see header note above; this is not stale, just accurate).
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 202 | 185 | +$1.75 | +0.86¢ |
| World Cup | 165 | 150 | −$0.41 | −0.25¢ |
| **TOTAL** | **367** | **335** | **+$1.33** | **+0.36¢** |

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 346 | +$9.01 | +2.60 | 14 | PROMOTE confirmed, essentially flat this run |
| **mmsell11** | 247 | +$8.52 | +3.45 | 17 | PROMOTE confirmed, incremental gain |
| mmsell10 | 120 | +$4.51 | +3.76 | 14 | **80% to its own gate** — close |
| mmsell9 | 25 | +$1.36 | +5.4 | 7 | no new settlements |
| mmsell control (paper) | 3,880 | +$68.62 | +1.77 | 33 | +1 settlement |
| mmsell2 (paper) | 1,651 | +$50.66 | +3.07 | 16 | +1 settlement |
| mmsell1 (paper) | 2,509 | +$53.97 | +2.15 | 22 | +1 settlement |
| mmsell3 (paper shadow) | 1,021 | +$16.88 | +1.65 | 17 | +1 settlement |
| mmsell5 | 115 | −$0.09 | −0.08 | 0 | no new settlements |
| mmsell4 | 195 | +$1.88 | +0.96 | 16 | KILLED (run #61) — still not recorded, +1 settlement |
| mmsell7 | 54 | −$0.09 | −0.17 | 3 | improved further, +1 settlement |
| mmsell8 | 29 | −$0.65 | −2.24 | 9 | no new settlements |
| **theta4** (fat-tail) | 45 | +$16.53 | +36.7 | 0 | +1 settlement, positive, 56% to gate |
| weather con (all) | 489 | −$13.67 | −2.79 | 16 | no new settlements |
| weather_concity | 76 | −$8.88 | −11.68 | 8 | no new settlements |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — a quiet, unremarkable run: small even gains across the mmsell cohort, no gate
crossings, live P&L confirmed stable (not stale). mmsell10 is now 80% to its gate — the closest
of any book not yet resolved.**

**Gate sweep (step 3b):** theta4 **45/80** (56%) · **mmsell6 CLEARED-PROMOTE** · **mmsell11
CLEARED-PROMOTE** · **mmsell4 KILLED** (unchanged, still not recorded — now 8 runs) · mmsell7
gate n≥150 (36%) · mmsell8 gate n≥100 (29%) · mmsell9 gate n≥100 (25%) · **mmsell10 gate n≥150
(80%)** · weather_concity **76/120** (63%, unchanged) · FREEZE **6/100** (not fired, unchanged,
21 runs).

**Data (last-24h / latest CDT, ~5:35 AM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (5:11–5:35 AM ✓). xgame_matches still dark (expected — book
KILLED, collector-only). xgame_tapes still 0 rows/24h — consistent with the confirmed
healthy-lull explanation, not re-flagging.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** quiet run, no gate events. mmsell10 now 80% to its gate, closest of any unresolved
book. mmsell6/11 still PROMOTE. theta4 56% to gate with another positive settlement. Live P&L
confirmed stable at +$1.33 — the account genuinely has had zero activity since 2026-07-20.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=346, +2.60¢/trade.
   mmsell11: n=247, +3.45¢/trade.** Unchanged recommendation: a fable session should decide
   whether to promote one, both, or combine the mechanisms into the paper config — live mmsell3
   itself is currently wound down, so any promotion is about the paper book / a future live
   restart.

2. **[mmsell3_closeout retry-loop — cosmetic bug, low priority, mostly resolved] The stuck
   positions it was retrying for both settled naturally on 2026-07-20; the mechanism itself is
   now disabled (`mmsell_closeout_enabled` gate) and inert.** No remaining stray exposure to
   speak of. Still worth a fable session eventually fixing the closeout body if the mechanism
   will be reused for a future live wind-down, but there's no live urgency now.

3. **[mmsell4 · KILL verdict — still not recorded, 8 runs now] n=195, +0.96¢/trade cumulative,
   still below mmsell3's +1.65¢.** Recommend a fable session record the verdict in
   `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md`.

4. **[mmsell10 · very close, gate 80%] n=120/150, +3.76¢/trade cumulative.** Likely resolves
   within the next run or two.

5. **[weather_concity · gate 63%, unchanged] n=76/120, −11.68¢/trade cumulative.** No change
   this run; still approaching its decision point.

6. **[theta4 · 56% to gate, positive settlement] n=45/80, cumulative +36.7¢/trade.** Continue
   tracking toward the gate.

7. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted.** NEST still behind
   theta4's n≥80 gate (56% there).

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 6 of the n≥100 trigger, unchanged
   across 21 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 mmsell6/mmsell11 — restated. #2 mmsell3_closeout — downgraded further
after the investigation confirmed both stuck positions settled naturally and the mechanism is
now fully inert, no stray exposure remaining. #3 mmsell4 — restated, 8 runs unrecorded. #4
mmsell10 — closer to gate (80%, was 79%). #5 weather_concity — unchanged. #6 theta4 — unchanged,
one more positive settlement. #7 MMX/NEST — restated. #8 restated/unchanged.)*
