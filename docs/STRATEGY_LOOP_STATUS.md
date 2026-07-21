# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query (total
$ P&L + WC/non-WC split — `mmsell_live` itself computes but never prints either). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs fable to
change anything. Newest snapshot replaces the one above it; the suggestion list carries over
run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-21 05:35 AM CDT (run #63)

**No gate crossings this run.** Two things worth flagging without overreacting: mmsell4's batch
reversed sharply positive (still below mmsell3 cumulatively — the KILL verdict from run #61
stands), and theta4 took its first real tail-loss since going quiet.

**mmsell4 batch reversal (does NOT change the KILL verdict):**
| book | n | ¢/trade (cum) | this batch | note |
|---|---|---|---|---|
| mmsell3 (control) | 1,001 | +1.64¢ | +7.1¢/trade | baseline |
| mmsell4 | 183 | **+0.52¢** | **+7.3¢/trade** | still below mmsell3 cumulatively — KILL stands |

mmsell4 flipped cumulative-positive this run (was −0.26¢, now +0.52¢) on a strong batch. It's
still below mmsell3's +1.64¢/trade, so the pre-registered "KILL if per-trade < mmsell3" verdict
from run #61 (resolved at n=155) is unaffected — that gate resolved once and doesn't re-open on
later batches. Noting the reversal for completeness, not walking back the verdict.

**theta4 — first real tail-loss since reviving:**
| book | n | Δn | P&L this batch | note |
|---|---|---|---|---|
| theta4 | 39 | +2 | **−$3.32** (2 trades) | large single-trade tail loss, consistent with the fat-tail model |

theta4's 2 new settlements this run cost −$3.32 combined (avg −$1.66/trade vs its usual small
per-trade edge) — this is exactly the kind of occasional large loss a fat-tail-sell strategy is
*supposed* to take; cumulative P&L is still strongly positive (+$15.74, was +$19.06) because the
model prices for infrequent large losses against frequent small wins. Not a red flag by itself —
worth tracking whether the realized tail-hit rate stays inside the pre-registered 1.25x-modeled
bound as n grows toward the 80-trade gate (now 39/80, 49%).

**mmsell control/1/2 — negative batch from run #62 reversed positive.** Closing that watch item:
control +0.8¢/trade, mmsell1 +4.75¢/trade, mmsell2 +4.4¢/trade this batch — all positive again.
Confirms it was noise, not a trend, exactly as the small-n caveat in run #62 anticipated.

**Live P&L (real money — mmsell3):** unchanged for a third straight run — no new live
settlements.
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 204 | 187 | +$1.92 | +0.94¢ |
| World Cup | 172 | 157 | +$0.11 | +0.06¢ |
| **TOTAL** | **376** | **344** | **+$2.02** | **+0.54¢** |

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell6** | 330 | +$8.04 | **+2.44** | 12 | PROMOTE confirmed, still strengthening |
| **mmsell11** | 230 | +$8.28 | **+3.60** | 13 | PROMOTE confirmed, edge back up |
| mmsell10 | 108 | +$3.85 | +3.56 | 12 | 72% to its own gate (n≥150), positive batch |
| mmsell9 | 21 | +$1.14 | +5.4 | 5 | small n, positive |
| mmsell control (paper) | 3,820 | +$62.07 | +1.63 | 30 | positive batch — reversed from run #62 |
| mmsell2 (paper) | 1,631 | +$47.70 | +2.92 | 13 | positive batch |
| mmsell1 (paper) | 2,477 | +$51.27 | +2.07 | 21 | positive batch |
| mmsell3 (paper shadow) | 1,001 | +$16.41 | +1.64 | 13 | positive batch |
| mmsell5 | 113 | −$0.32 | −0.28 | 0 | improving, nearly breakeven |
| **mmsell4** | 183 | +$0.96 | **+0.52** | 12 | KILLED (run #61) — batch reversed but still < mmsell3 |
| mmsell7 | 45 | −$0.79 | −1.76 | 4 | slight improvement, gate n≥150 (30%) |
| mmsell8 | 23 | −$1.13 | −4.9 | 8 | no new settlements, gate n≥100 (23%) |
| **theta4** (fat-tail) | 39 | +$15.74 | +40.4 | **0** | first tail-loss since reviving — see note above |
| weather con (all) | 458 | −$10.54 | −2.30 | 18 | flat settled/P&L, +4 new opens |
| weather_concity | 62 | −$5.39 | −8.69 | 9 | flat settled/P&L, +3 new opens |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — quiet run for gates, but two notable single-run events: mmsell4's batch reversed
positive (doesn't change its resolved KILL verdict) and theta4 took its first real tail-loss
since reviving (expected behavior for a fat-tail model, not a red flag). mmsell6/mmsell11 both
continue to strengthen. The mmsell control/1/2 "negative batch" flagged last run reversed and is
now closed out as noise.**

**Gate sweep (step 3b):** theta4 **39/80** (49%) · **mmsell6 CLEARED-PROMOTE** · **mmsell11
CLEARED-PROMOTE** · **mmsell4 KILLED** (verdict unaffected by this run's batch reversal) ·
mmsell7 gate n≥150 (30%) · mmsell8 gate n≥100 (23%) · mmsell9 gate n≥100 (21%) · mmsell10 gate
n≥150 (72%) · weather_concity **62/120** (52%, unchanged) · FREEZE **5/100** (not fired,
unchanged, 15 runs).

**Data (last-24h / latest CDT, ~5:35 AM run):** crypto_spot, crypto_ladder, weather forecasts/
obs/ensembles/buckets all fresh (5:27–5:35 AM ✓). xgame_matches still dark (expected — book
KILLED, collector-only). xgame_tapes still 0 rows in 24h — consistent with the healthy-but-quiet
collector confirmed via logs last run, not re-flagging as a concern.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** no gate crossings. mmsell4 batch reversed positive but stays below mmsell3 —
KILL verdict stands. theta4's first tail-loss since reviving — expected fat-tail behavior, still
strongly cumulative-positive, 49% to its own gate. mmsell6/mmsell11 both strengthened further.
Live P&L flat for a third run. mmsell control/1/2 "negative batch" watch item closed — reversed
to positive, confirmed as noise.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell6 AND mmsell11 still PROMOTE — top actionable item] mmsell6: n=330, +2.44¢/trade,
   still strengthening. mmsell11: n=230, +3.60¢/trade, edge back up after easing last run.**
   Unchanged recommendation: a fable session should decide whether to promote one, both, or
   combine the mechanisms (mmsell6's 5-8¢ band, mmsell11's `htcmin=6` no-late-entry) into the
   live mmsell3 config.

2. **[mmsell4 · KILL verdict stands despite this run's batch reversal] n=183, cumulative
   per-trade flipped to +0.52¢ (was −0.26¢) on a strong +7.3¢/trade batch — but still below
   mmsell3's +1.64¢, so the pre-registered gate resolution from run #61 (n=155, KILL if
   per-trade < mmsell3) is unaffected.** Recommend a fable session still record the verdict in
   `docs/MMSELL_VARIANTS_THESIS.md`/`RESEARCH_JOURNAL.md` — gates resolve once at their
   threshold, they don't get re-opened by later noise, but this hasn't been written down yet.

3. **[theta4 · first tail-loss since reviving, worth watching not worrying] 2 new settlements
   this run, −$3.32 combined (avg −$1.66/trade) — a real tail-loss, consistent with the model's
   expected behavior (frequent small wins, infrequent large losses).** Still 39/80 (49%) to its
   n≥80 gate; cumulative P&L still strongly positive (+$15.74). Watch whether the realized
   tail-hit rate stays inside the pre-registered ≤1.25x-modeled bound as more of these land.

4. **[mmsell10 · watch, gate 72%] n=108/150, +3.56¢/trade cumulative, positive batch.** Still the
   "highest-value result" candidate per its own thesis if it holds above mmsell3 at gate. Very
   close now — likely resolves within a run or two.

5. **[Live P&L · flat for a third run] Total unchanged at +$2.02 (n=376) since run #61.**
   Continue tracking; nothing to report until the next live fill settles.

6. **[idea-model queue · MMX/NEST] MMX's premise (extend the mmsell edge into new categories)
   should be built against whichever of mmsell6/mmsell11 gets promoted — unchanged, still the
   concrete next step once a promote decision is made.** NEST still behind theta4's n≥80 gate
   (49% there, closer given this run's activity).

7. **[weather_concity / con(all) · quiet, no new settles] concity −8.69¢/trade (52% to gate),
   con(all) −2.30¢/trade — both flat on settled/P&L this run, several new opens each.** Carry
   forward, not a gate event.

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 15 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 mmsell6/mmsell11 — restated, both strengthened. #2 mmsell4 — batch
reversed positive but verdict unaffected, still needs to be recorded. #3 NEW — theta4's first
tail-loss since reviving, framed as expected model behavior not a red flag. #4 mmsell10 — closer
to gate (72%, was 66%). #5 live P&L — flat for a third run. #6 MMX/NEST — restated, NEST closer.
#7 weather — restated, flat. #8 restated/unchanged. DROPPED — the mmsell control/1/2
negative-batch watch item from run #62: reversed positive this run, confirmed as noise, closing
it out rather than carrying it forward.)*
