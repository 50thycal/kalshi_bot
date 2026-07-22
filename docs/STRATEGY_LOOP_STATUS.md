# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query (total
$ P&L + WC/non-WC split — `mmsell_live` itself computes but never prints either). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs fable to
change anything. Newest snapshot replaces the one above it; the suggestion list carries over
run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-21 08:08 PM CDT (run #65)

**🔴 REAL-MONEY INCIDENT — mmsell3 LIVE has placed ZERO orders for over 2.5 days.** Live P&L
being flat for 5 straight loop runs was not "no fills happened to settle" — it's "the live book
hasn't traded at all." Root cause found via `live_orders.cancel_reason`:

- **2026-07-19 5:25 PM CT:** order submissions to `/trade-api/v2/portfolio/events/orders` started
  failing with `missing_parameters` (`CreateOrderV2Request.SelfTradePreventionType`) — 30 orders,
  6 minutes.
- **2026-07-19 5:31 PM CT → 2026-07-20 8:58 AM CT:** every subsequent order attempt (**1,942 of
  them**) failed with a generic `invalid_parameters` 400 from the same endpoint — whatever change
  was made after the first error didn't fix the request shape.
- **2026-07-20 8:59 AM CT → now (2026-07-21 8:08 PM CT, ~35 hours):** **zero order attempts of
  any kind** — not even more rejects. The live execution path appears to have stopped trying
  entirely, not just failing.

The paper-trading engine is unaffected — `bot_runs` shows continuous "completed" scan cycles
every ~90 seconds with no gaps, and every paper mmsell variant kept accumulating trades normally
through this whole window. This is isolated to the live order-placement path for mmsell3 only.

**One relevant coincidence, not a diagnosis:** a new Railway deployment was mid-`DEPLOYING` at
the moment this run pulled logs (started 2026-07-22 01:05 UTC / ~8:05 PM CT, ~3 minutes before
this check) — logs for it weren't available yet. Possible a fix is already in flight from a
parallel session; this loop can't tell from here. **Recommend confirming directly:** whether this
deploy addresses the order-parameter error, and whether mmsell3 live has resumed placing orders
after it completes.

This is squarely a "report loudly" event per the loop's own guardrails — the loop does not
diagnose further or act, but this is real capital sitting completely out of the live book for
going on 3 days.

**Live P&L (real money — mmsell3):** unchanged for a fifth straight run — confirmed now to be
because of the incident above, not routine settlement lag.
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 204 | 187 | +$1.92 | +0.94¢ |
| World Cup | 172 | 157 | +$0.11 | +0.06¢ |
| **TOTAL** | **376** | **344** | **+$2.02** | **+0.54¢** |

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

**HEADLINE — mmsell3 LIVE has been unable to place any real-money orders since 2026-07-19 5:25 PM
CT (order-parameter errors, then total silence for the last ~35 hours). Paper trading is fully
healthy and unaffected. A new deploy was mid-flight at check time — may be an in-progress fix,
unconfirmed from here.**

Below the incident: a quiet paper picture. mmsell6/mmsell11 both still PROMOTE. mmsell10 is 79%
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

**Headline:** 🔴 mmsell3 LIVE has placed zero real-money orders since 2026-07-19 5:25 PM CT (~2.5
days) — first parameter-mismatch rejections against `/trade-api/v2/portfolio/events/orders`,
then total silence since 2026-07-20 8:59 AM CT. Paper trading fully healthy. A deploy was
mid-flight at check time — recommend confirming whether it fixes this and whether live order
placement has resumed. Otherwise: quiet paper picture, mmsell6/11 still PROMOTE, mmsell10 at 79%
to its gate.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[🔴 NEW/TOP · mmsell3 LIVE down for ~2.5 days — needs human confirmation] Zero real-money
   orders placed since 2026-07-19 5:25 PM CT: first `missing_parameters`/`invalid_parameters`
   rejections (1,942 of them) against `/trade-api/v2/portfolio/events/orders`, then total silence
   since 2026-07-20 8:59 AM CT — the live execution path appears to have stopped attempting
   entirely.** Paper trading is unaffected and fully healthy. A new Railway deploy was
   `DEPLOYING` at check time (~8:05 PM CT) — may already be an in-flight fix, but this loop
   cannot confirm that from here. **Recommend the user check directly: has this deploy fixed the
   order-parameter issue, and has mmsell3 live resumed placing orders?** This is real capital
   sitting completely idle, the highest-priority item in this report.

2. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item once live is fixed] mmsell6:
   n=343, +2.57¢/trade, still strengthening. mmsell11: n=243, +3.35¢/trade (negative batch this
   run but still well above mmsell3's +1.62¢).** Unchanged recommendation: a fable session should
   decide whether to promote one, both, or combine the mechanisms into the live mmsell3 config —
   though this is moot until live order placement itself is confirmed working again.

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

*(Changed this run: #1 NEW/TOP — the live-orders incident, the most significant finding of the
whole loop's history so far: real money idle for 2.5+ days, root cause identified, deploy in
flight unconfirmed. #2 mmsell6/mmsell11 — restated, now explicitly noted as moot until live is
confirmed working. #3 mmsell4 — restated, 4 runs unrecorded. #4 mmsell10 — very close (79%).
#5 weather_concity — restated. #6 theta4 — positive batch, past halfway. #7 MMX/NEST — restated.
#8 restated/unchanged.)*
