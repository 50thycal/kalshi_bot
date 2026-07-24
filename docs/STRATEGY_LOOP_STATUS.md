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

## Snapshot — 2026-07-24 05:35 AM CDT (run #72)

**No gate crossings — zero new settlements across the ENTIRE mmsell cohort AND theta4/weather,
but a very large jump in open positions (mmsell 33→46, mmsell1 24→36, mmsell11 18→29, mmsell3
18→29, mmsell4 17→28, mmsell7 3→15, etc.). Confirmed healthy** — checked `bot_runs` directly:
167 completed cycles in the last 4 hours, zero non-completed. This is the same
entries-outpacing-settlements pattern seen in runs #68/#70, just larger — reads as an overnight
window where many new markets opened but few have reached their close time yet, not a worker or
data problem.

**Live P&L (real money — mmsell3):** unchanged, as expected — the account remains confirmed
100% flat since 2026-07-20 10:20:56 CT.
| bucket | n settled | wins | realized P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 202 | 185 | +$1.75 | +0.86¢ |
| World Cup | 165 | 150 | −$0.41 | −0.25¢ |
| **TOTAL** | **367** | **335** | **+$1.33** | **+0.36¢** |

**Trading books (settled n / realized P&L / ¢-per-trade / open) — PAPER only, separate from
live above. All figures unchanged from run #71 except open counts:**
| book | n | realized P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 348 | +$9.12 | +2.62 | 25 | PROMOTE confirmed, no new settlements, +7 open |
| **mmsell11** | 250 | +$8.72 | +3.49 | 29 | PROMOTE confirmed, no new settlements, +11 open |
| mmsell10 | 122 | +$4.62 | +3.79 | 25 | 81% to its own gate, no new settlements, +7 open |
| mmsell9 | 26 | +$1.41 | +5.42 | 10 | no new settlements |
| mmsell control (paper) | 3,892 | +$65.73 | +1.69 | 46 | no new settlements, +13 open |
| mmsell2 (paper) | 1,656 | +$49.28 | +2.98 | 28 | no new settlements, +8 open |
| mmsell1 (paper) | 2,516 | +$52.69 | +2.09 | 36 | no new settlements, +12 open |
| mmsell3 (paper shadow) | 1,024 | +$17.08 | +1.67 | 29 | no new settlements, +11 open |
| mmsell5 | 115 | −$0.09 | −0.08 | 0 | no new settlements |
| mmsell4 | 197 | +$2.02 | +1.03 | 28 | KILLED (run #61) — still not recorded, +11 open |
| mmsell7 | 56 | +$0.05 | +0.09 | 15 | still cumulative-positive, +12 open |
| mmsell8 | 31 | −$0.51 | −1.65 | 11 | no new settlements |
| **theta4** (fat-tail) | 48 | +$17.78 | +37.0 | 0 | no new activity, 60% to gate |
| weather con (all) | 504 | −$12.66 | −2.51 | 14 | no new settlements |
| weather_concity | 84 | −$7.97 | −9.49 | 6 | no new settlements, 70% to gate |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — a quiet-for-settlements but very active-for-entries run: no book settled anything
new, but open counts jumped sharply across the board, and the worker's own run history is clean
(167/167 completed in the last 4h). No gate crossings; live P&L unchanged as expected.**

**Gate sweep (step 3b):** theta4 **48/80** (60%, no new activity) · **mmsell6
CLEARED-PROMOTE** · **mmsell11 CLEARED-PROMOTE** · **mmsell4 KILLED** (unchanged, still not
recorded — now 11 runs) · mmsell7 gate n≥150 (37%, cumulative-positive) · mmsell8 gate n≥100
(31%) · mmsell9 gate n≥100 (26%) · mmsell10 gate n≥150 (81%, unchanged) · weather_concity
**84/120** (70%, unchanged) · FREEZE **6/100** (not fired, unchanged, 24 runs).

**Data (last-24h / latest CDT, ~5:35 AM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (5:19–5:35 AM ✓). xgame_matches still dark (expected — book
KILLED, collector-only). xgame_tapes still 0 rows/24h — consistent with the confirmed
healthy-lull explanation, not re-flagging.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** quiet run for settlements (zero across the board) but very active for entries
(open counts up sharply everywhere) — confirmed healthy via bot_runs, not a problem. No gate
crossings. Live P&L unchanged, confirmed stable.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=348, +2.62¢/trade.
   mmsell11: n=250, +3.49¢/trade.** No new settlements this run. Unchanged recommendation: a
   fable session should decide whether to promote one, both, or combine the mechanisms into the
   paper config — live mmsell3 itself is currently wound down, so any promotion is about the
   paper book / a future live restart.

2. **[mmsell4 · KILL verdict — still not recorded, 11 runs now] n=197, +1.03¢/trade cumulative,
   still below mmsell3's +1.67¢.** Recommend a fable session record the verdict in
   `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md`.

3. **[mmsell10 · very close, gate 81%, unchanged] n=122/150, +3.79¢/trade cumulative.** Likely
   resolves once settlements resume.

4. **[weather_concity · gate 70%, unchanged] n=84/120, −9.49¢/trade cumulative.** Still
   approaching its decision point.

5. **[theta4 · 60% to gate, no new activity] n=48/80, cumulative +37.0¢/trade.** Continue
   tracking toward the gate.

6. **[mmsell7 · holding positive cumulative] n=56, +0.09¢/trade, no new settlements this run.**
   Still small n and 37% to its own gate — watching whether this holds as n grows.

7. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted.** NEST still behind
   theta4's n≥80 gate (60% there).

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 6 of the n≥100 trigger, unchanged
   across 24 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1-8 all restated/unchanged — a settlement-quiet but entry-active run,
confirmed healthy via bot_runs (167/167 completed, zero failures in the last 4h). No new
findings; carrying the same picture as run #71 forward with updated open-position counts.)*
