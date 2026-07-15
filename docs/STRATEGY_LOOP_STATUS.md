# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-15 05:36 AM CDT (run #45)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell3 (5-10c) | 490 | +$14.22 | **+2.9** | 9 | great batch (+8.4c/trade) — gap to mmsell2 nearly closed again (0.06c) |
| mmsell2 | 1,071 | +$31.74 | +3.0 | 12 | still nominally ahead, but the gap keeps oscillating |
| mmsell1 | 1,648 | +$40.14 | +2.4 | 14 | |
| mmsell (control) | 2,676 | +$47.78 | +1.8 | 20 | |
| **pin15** | 331 | −$10.06 | **−3.0** | 0 | 2nd positive batch of the last 3 (+10.8c/trade), still net negative |
| **theta4** (fat-tail) | 17 | +$11.91 | +70.1 | 0 | +1 trade, magnitude unchanged, calibration check still pending |
| weather con (all) | 370 | −$6.06 | −1.6 | 16 | unchanged settled/P&L, +4 new opens |
| weather_concity | 28 | −$5.04 | −18.0 | 5 | unchanged settled/P&L, +2 new opens |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — both open questions (mmsell2-vs-mmsell3, pin15) keep oscillating rather than
converging; that oscillation is itself the finding, not any single run's reading.**

**mmsell2 vs mmsell3:** the gap that widened to 0.6¢/trade over runs #42-44 (looked like a real,
settling lead for mmsell2) just **collapsed back to 0.06¢/trade** this run on a strong mmsell3
batch (+8.4¢/trade, n=61). Combined with run #41's "tied" call and the 4 flips before that, this
comparison has now swung between "real gap" and "essentially tied" at least 6 times across 8
runs. **Recommend retiring "which mmsell variant is ahead" as a run-to-run tracked question** —
it isn't resolving, and re-litigating it each run adds narration without adding information. If
it matters for a fable decision (e.g. MMX design), pull the full cumulative series and look at
variance/confidence intervals directly rather than reading the latest per-trade delta.

**pin15** posted its 2nd positive batch in the last 3 (+10.8¢/trade, n=25), continuing to
oscillate exactly as flagged last run — cumulative improved to −3.0¢/trade (from −4.2¢) but this
is well within the batch-to-batch noise band already established (recent batches: −3.9, +1.6,
+1.6, +21.2, −29.5, +10.8¢/trade). **Restating run #44's recommendation rather than re-narrating
the swing:** the T-window slice (`docs/PIN15_THESIS.md`) is still the right next step, not more
batch-watching — nobody has run it yet.

**theta4** added one more trade at the same ~70¢/trade magnitude (n=17/80, 21%) — consistent
with, not contradicting, the calibration-check request from runs #43-44. Still pending.

weather books both quiet (new opens only, no new settlements) — normal.

**Gate sweep (step 3b):** pin15 **331/150** (2.2x past gate, still recommend the T-window slice
over more waiting) · theta4 **17/80** (21%, calibration check pending) · mmsell3 **490/150**
(clears own bar; family-internal ranking oscillating, not tracking further per-run) ·
weather_concity **28/120** (23%).

**Data (last-24h / latest CDT):** crypto_spot 2,870 (05:31 AM ✓), crypto_ladder 59,218 (05:32 AM
✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (05:28–05:35 AM ✓).
xgame_tapes 61,916 (05:34 AM ✓, healthy). xgame_matches: 7th consecutive run frozen at the
identical timestamp (2026-07-12 10:18:09 UTC, ~72h stale) — unchanged, long-standing, already
established as not crash-related; no new note needed going forward unless it changes.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell2-vs-mmsell3 gap collapsed again (6th flip in 8 runs) — recommend dropping
this as a tracked per-run question. pin15 posted another positive batch, consistent with ongoing
oscillation, not a new trend — T-window slice recommendation restated, still not run. theta4
flat at its large magnitude, calibration check pending. Data all healthy except the long-standing
xgame_matches gap.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[pin15 · T-window slice still recommended, not yet run] n=331, −3.0c/trade cumulative
   (improved from −4.2c on a +10.8c/trade batch) — 6th batch in the ongoing oscillation.** Same
   recommendation as run #44: a fable session should run the P&L-by-T-at-entry slice
   (`docs/PIN15_THESIS.md`, using `fill_assumption`) rather than waiting for more batches — the
   batch variance here (range from −29.5c to +21.2c per batch) makes run-to-run reading
   unproductive. Not repeating the "kill" or "don't kill" framing from earlier runs; the
   structural question is what needs answering.

2. **[mmsell2 vs mmsell3 · RETIRE as a per-run tracked comparison] Gap has flipped between
   "real" (~0.3-0.6c) and "tied" (~0.01-0.06c) at least 6 times across runs #38-45.** Recommend
   this stop being re-narrated every run — it isn't converging on a stable answer at this n, and
   the loop calling a "leader" each run has been wrong about as often as right. If a fable
   session needs an answer (e.g. for MMX design, #3), pull the full cumulative time series and
   compute a confidence interval, not the latest single delta.

3. **[idea-model queue · MMX — design should not assume either mmsell variant is settled-ahead]
   Given #2, MMX (`IDEA_MODEL_20260710_run2.md`) should be scoped around "the mmsell 5-10c
   maker-sell edge broadly" rather than picking mmsell2 or mmsell3 specifically as the template —
   neither has proven durably ahead. NEST still behind theta4's calibration check (#4).
   RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed event.

4. **[theta4 · calibration check still pending] n=17/80 (21%), +70.1c/trade, magnitude stable
   across the last 2 runs (no longer just a 1-trade blip).** Still need the realized-tail-hit
   ratio check against `docs/THETA_THESIS.md`'s gate before trusting this as a real edge rather
   than a lucky small sample — same ask as runs #43-44, now with slightly more data behind it.

5. **[weather_concity · WATCH, fully quiet] 28 settled −$5.04, unchanged settled/P&L across 3
   runs now (+2 new opens only).** Gate: n≥120 (23% there). Nothing to read; carry forward.

6. **[xgame_matches · unchanged, still long-standing] 7th consecutive run frozen at the same
   timestamp, ~72h stale.** No new information since run #42; not restating detail every run
   going forward unless it changes — will just note "unchanged" briefly.

7. **[mmsell existing · context] control ~breakeven-positive (+1.8c), mmsell1 +2.4c/trade.**
   All four variants (control/1/2/3) are now within a fairly tight band (1.8-3.0c) — the whole
   family may just be one edge with sampling noise across sub-variants rather than genuinely
   different edges, worth keeping in mind alongside #2/#3.

*(Changed this run: #1 pin15 — another oscillation (2nd positive batch of last 3), restating the
T-window-slice recommendation rather than re-narrating the swing. #2 mmsell2-vs-mmsell3 — gap
collapsed again (6th flip in 8 runs); formally recommending this stop being tracked/re-narrated
per-run. #3 MMX — sharpened to explicitly not assume either variant as the template. #4 theta4 —
magnitude now stable across 2 runs, calibration ask restated. #6 xgame_matches — noting future
runs will compress this to "unchanged" rather than repeat detail. #5/#7 otherwise
unchanged/updated context.)*
