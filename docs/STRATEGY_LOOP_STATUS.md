# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-15 08:03 PM CDT (run #47)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell2 | 1,144 | +$29.04 | +2.5 | 17 | rough batch too (−3.5c/trade) but held up best of the family |
| mmsell1 | 1,760 | +$33.88 | +1.9 | 19 | rough batch (−6.1c/trade) |
| mmsell (control) | 2,826 | +$43.79 | +1.5 | 34 | mildest hit of the family (−2.0c/trade) |
| **mmsell3** (5-10c) | 556 | +$9.19 | **+1.7** | 9 | **worst batch of the whole family** (−8.6c/trade) — widest gap yet vs mmsell2 |
| **pin15** | 396 | −$15.59 | **−3.9** | 0 | negative batch again (−14.6c/trade) — oscillation continues, no new extreme |
| theta4 (fat-tail) | 25 | +$16.85 | +67.4 | 0 | unchanged, no new settles — calibration check still the priority |
| weather con (all) | 382 | −$9.38 | −2.5 | 14 | unchanged settled/P&L, +5 new opens |
| weather_concity | 31 | −$3.84 | −12.4 | 6 | unchanged settled/P&L, +1 new open |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — the whole mmsell family had a rough batch simultaneously, with mmsell3 hit
hardest; this reads as a shared adverse market move, not a mmsell3-specific problem.**

All four mmsell variants posted negative batches this run — mmsell3 **−8.6¢/trade**, mmsell1
−6.1¢/trade, mmsell2 −3.5¢/trade, control −2.0¢/trade (137 combined settlements across the
family in this window). Because every variant moved the same direction at roughly the same
time, the more likely read is a batch of sports results that broke against favorites broadly
(the FLB mechanism this family sells), not something specific to mmsell3's narrower band. The
side effect: mmsell3 vs mmsell2's gap, which had collapsed to ~0.01-0.06¢ the last two runs, is
now **0.885¢/trade — the widest recorded yet**. Consistent with the standing "don't re-narrate
this weekly" call from run #45: this is one more data point in an oscillating series, not a
new trend to chase. If it matters for a design decision, look at the shared-batch pattern (all
variants down together) rather than the mmsell3-vs-mmsell2 ranking in isolation.

**pin15** posted another negative batch (−14.6¢/trade), continuing the established oscillation
— nothing new, same standing recommendation (T-window slice, not more batch-watching).

**theta4** had zero new settlements this run — still n=25/80 (31%), same +67.4¢/trade. No new
information; the calibration-check recommendation from run #46 stands untouched and unactioned.

weather books both quiet (new opens only, no new settlements).

**Gate sweep (step 3b):** theta4 **25/80** (31%, calibration check still the priority, unactioned)
· pin15 **396/150** (T-window slice still recommended) · mmsell3 **556/150** (own bar cleared,
family-internal ranking still oscillating) · weather_concity **31/120** (26%).

**Data (last-24h / latest CDT):** crypto_spot 2,870 (07:57 PM ✓), crypto_ladder 58,333 (07:57 PM
✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (07:36–08:01 PM ✓).
xgame_tapes latest is **~4h old** (09:06 PM CDT) despite 58,875 rows in the last 24h — likely a
live-game scheduling lull (fewer games at this hour) rather than a collector issue, worth a
glance next run but not escalating; distinct from xgame_matches' multi-day staleness.
xgame_matches: unchanged, 9th consecutive run frozen (per run #46's note, not repeating detail).

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** whole mmsell family had a rough batch together, mmsell3 hit hardest — reads as a
shared market move, not mmsell3-specific; gap to mmsell2 widened to its largest recorded (0.885c)
but still just one more oscillation point. pin15 continues its established swing pattern. theta4
flat, calibration check still unactioned. xgame_tapes ~4h stale, likely just a lull.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta4 · calibration check DONE 2026-07-15 (user-requested) — PASSES, safe direction, but
   untested] n=25/80.** Realized tail-hit rate **0/25 = 0%** vs mean modeled hit prob **6.9%**
   (model expected ~1.7 hits). Gate (realized ≤ 1.25× modeled) PASSES, and critically it is NOT
   the original-theta failure mode (there realized 37% >> modeled 19%; here realized < modeled,
   the safe direction). BUT 0 hits in 25 is ~16% likely even if the model is perfectly calibrated
   — the sample hasn't met a tail yet, so this rules out gross under-pricing, it does not confirm
   calibration. Netting the not-yet-seen losses at the modeled rate, honest edge ≈ +39c/trade (vs
   observed +67c) — still strongly +EV if the model holds. **Recommendation: keep running, no
   action; re-check the realized hit rate as n approaches the n≥80 gate — the first few tail hits
   are the real test.**

2. **[pin15 · T-window slice DONE 2026-07-15 (user-requested) — THESIS FALSIFIED, recommend
   RETIRE] n=405 settled, sliced by entry T (seconds-to-close):** <60s n=2 +4.0c · **60-120s n=37
   −53.3c/trade (−$19.73, the entire book's loss)** · **120-180s (the thesis target) n=218
   +0.27c/trade** · 180-240s n=148 +0.76c/trade. The pre-registered claim (profit concentrates in
   T≈120-180s at >+1.5c/trade) FAILS: the target window earns only +0.27c, no window clears the
   +1.5c bar, and the whole cumulative loss is one sub-window (final 1-2 min entries blow up at
   −53c/trade). The batch oscillation the loop chased for runs #40-47 was just the ~5% negative-skew
   blowouts landing in different batches — not signal. Even the charitable salvage (hard-exclude
   <120s entries) lands ~+0.5c/trade, still sub-bar. **Recommendation: a fable session formally
   retires pin15** (disable entries, keep the book/data for the record). This resolves the item
   cleanly — no more batch-watching needed.

3. **[mmsell3 vs mmsell2 · widest gap yet (0.885c), but likely a shared-batch effect, not a
   ranking signal] All four mmsell variants had a simultaneously negative batch this run** —
   mmsell3 hit hardest (−8.6c/trade), which is why the gap widened so much, but every variant
   moved together. Per run #45's standing call, still not tracking this comparison per-run; if
   anything, this run's data argues for looking at *shared* batch variance (does something —
   e.g. a bad week for favorites broadly — hit the whole family at once?) rather than relative
   ranking between variants.

4. **[idea-model queue · MMX/NEST unchanged] MMX still shouldn't assume either mmsell variant
   as template (#3).** NEST behind theta4's calibration check (#1, still unactioned).
   RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed event.

5. **[weather_concity / con(all) · quiet, no new settles] concity −12.4c/trade (26% to gate),
   con(all) −2.5c/trade — both flat this run** (new opens only). Carry forward.

6. **[xgame_matches · unchanged, 9th consecutive run] No new detail — still frozen, long-standing
   per run #42/45/46's notes.**

7. **[NEW · xgame_tapes ~4h stale, likely benign] Latest row is ~4 hours old despite 58,875 rows
   in the last 24h — probably a live-game scheduling lull, not a collector fault (distinct from
   xgame_matches' multi-day, clearly-broken staleness). Worth a glance next run; not escalating
   yet.**

*(Changed this run: #1 theta4 — still unactioned, 3rd run restating the same ask, flat with no
new settlements. #2 pin15 — one more oscillation point, no new information. #3 mmsell3-vs-mmsell2
— widest gap yet (0.885c) but reframed as likely a shared-batch effect across the whole family,
not a ranking signal; suggests looking at correlated batch variance instead. #7 NEW — xgame_tapes
modest staleness (~4h), flagged as probably benign and distinct from xgame_matches' real problem.
#4/#5/#6 unchanged/compressed.)*
