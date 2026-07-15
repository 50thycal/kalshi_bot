# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-14 08:02 PM CDT (run #44)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **pin15** | 306 | −$12.75 | **−4.2** | 0 | **reversed hard** — this batch −29.5c/trade, wiping out last run's near-breakeven read |
| mmsell2 | 1,030 | +$27.42 | +2.7 | 32 | still the clear family leader, gap to mmsell3 widened |
| mmsell3 (5-10c) | 429 | +$9.12 | +2.1 | 46 | good batch (+3.7c/trade), now also edges mmsell1, but mmsell2 well ahead |
| mmsell1 | 1,568 | +$32.91 | +2.1 | 63 | |
| mmsell (control) | 2,579 | +$41.66 | +1.6 | 81 | |
| theta4 (fat-tail) | 16 | +$11.21 | +70.1 | 0 | unchanged — no new settles since run #43, calibration check still pending |
| weather con (all) | 370 | −$6.06 | −1.6 | 12 | unchanged settled/P&L, +8 new opens |
| weather_concity | 28 | −$5.04 | −18.0 | 3 | unchanged settled/P&L, +2 new opens |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — pin15 reversed hard, wiping out last run's optimism; this is itself the real
finding, not the direction of the swing.** Last run's 3rd-straight-positive-batch pulled
cumulative to −1.1¢/trade and I withdrew the standing KILL recommendation. This run's batch came
in at **−29.5¢/trade** (33 trades), pulling cumulative back to **−4.2¢/trade** at n=306 — more
than double the n≥150 gate. Look at the last six batch readings in sequence: **−24.8, −3.9, +1.6,
+1.6, +21.2, −29.5¢/trade.** That is not a book converging on an answer — that's a book whose
batch-to-batch variance is enormous relative to whatever edge (or lack of one) actually exists.
**Revised framing: stop reading any 1-2 run trend as signal here.** Neither "recommend kill" (run
#40-42) nor "withdraw kill, reassess" (run #43) should have been stated with the confidence they
were — the honest state is "this book's cumulative P&L swings by more per batch than its
cumulative average, and no amount of run-to-run narration will resolve that; it needs either a
much larger n or the T-window-concentration slice the thesis actually specifies" (win-rate/tail
decomposition, not more headline-chasing).

**theta4** had no new settlements this run — still n=16, still needs the tail-hit-ratio
calibration check flagged last run before trusting its +70¢/trade magnitude.

**mmsell3** had a good batch (+3.7¢/trade) and now numerically edges mmsell1 too, but **mmsell2's
lead over mmsell3 widened** (2.7¢ vs 2.1¢, a 0.6¢ gap) — consistent with runs #42-43, mmsell2
remains the real family leader.

weather books both quiet (settled/P&L unchanged, new opens only) — normal ahead of the next daily
batch.

**Gate sweep (step 3b):** pin15 **306/150** (past gate 2x over, verdict unstable — see above,
recommend a T-window slice instead of more batch-watching) · theta4 **16/80** (20%, unchanged,
calibration check pending) · mmsell3 **429/150** (clears own bar, behind mmsell2) ·
weather_concity **28/120** (23%).

**Data (last-24h / latest CDT):** crypto_spot 2,876 (08:00 PM ✓), crypto_ladder 58,596 (08:01 PM
✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (08:00–08:01 PM ✓).
xgame_tapes 62,280 (07:58 PM ✓, healthy). xgame_matches: 6th consecutive run frozen at the
identical timestamp (2026-07-12 10:18:09 UTC, ~63h stale) — unchanged, long-standing, already
established as not crash-related.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** pin15 whipsawed hard (−29.5c/trade this batch after 3 positive ones) — the real
lesson is the batch variance itself, not which direction it's pointing this run; recommend a
T-window slice rather than more run-to-run narration. theta4 flat, calibration check still open.
mmsell2 extends its lead over mmsell3. weather/xgame_matches unchanged.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[pin15 · STOP READING SHORT TRENDS — recommend a structural slice instead] n=306,
   −4.2c/trade cumulative, this batch −29.5c/trade** — the 4th direction-reversal in six runs
   (−6.6 → −6.1 → −4.6 → −1.1 → back to −4.2¢/trade). Batch-to-batch swings (see the six-batch
   sequence in the headline) are far larger than the cumulative average itself. **New
   recommendation: instead of watching more batches, have a fable session run the T-window slice
   `docs/PIN15_THESIS.md` actually specifies** (P&L by T-at-entry bucket, using the
   `fill_assumption` field) — that's the pre-registered test for *whether the mechanism works at
   all*, and it doesn't require waiting for n to grow further. This resolves the question
   run-to-run narration can't.

2. **[theta4 · calibration check still pending, unchanged] n=16 (was 16 last run — no new
   settles), +70.1c/trade cumulative.** Still need the realized-tail-hit-ratio check against
   `docs/THETA_THESIS.md`'s gate (per-trade > 0 AND tail-hit ≤ 1.25x modeled) before trusting the
   magnitude — nothing new this run, just restating since it hasn't been done yet.

3. **[mmsell2 vs mmsell3 · gap widened, mmsell2 still leader] mmsell2 +2.7c/trade (n=1,030) vs
   mmsell3 +2.1c/trade (n=429) — gap grew from 0.31c (run #42) to 0.39c (run #43) to 0.6c (this
   run).** Third straight run confirming mmsell2 as the real leader, and the gap is trending
   wider, not narrower. mmsell3 clears its own +1.5c bar and now edges mmsell1 too, but the
   "beats mmsell1 AND mmsell2" gate criterion keeps failing more clearly each run.

4. **[idea-model queue · MMX — recommend scoping around mmsell2, not mmsell3] Given #3's
   3-run-consistent finding, MMX (`IDEA_MODEL_20260710_run2.md`) should extend whichever variant
   is actually winning** — that's now clearly mmsell2, not mmsell3 (the one the gate was written
   around). Worth a fable session explicitly re-checking MMX's design assumptions before
   building. NEST still behind theta4's calibration check (#2). RTPIN/BOXPIN behind unbuilt
   scraper infra. RATELAG behind a live Fed event.

5. **[weather_concity · WATCH, fully quiet] 28 settled −$5.04, unchanged settled/P&L this run
   (+2 new opens only).** Gate: n≥120 (23% there). Nothing to read; carry forward.

6. **[xgame_matches · unchanged, still long-standing] 6th consecutive run frozen at the same
   timestamp, ~63h stale.** No new information; established as pre-existing/likely permanently
   broken in run #42. Book shelved/killed, still low-urgency.

7. **[mmsell existing · context, unchanged] control/mmsell1 ~breakeven-to-positive (+1.6c/+2.1c)
   — mmsell1 has actually caught up to mmsell3 this run.** mmsell2 remains the standout.

*(Changed this run: #1 pin15 — MAJOR reversal (−29.5c/trade batch), and more importantly a
reframe: stop treating any short run of batches as a trend, recommend the pre-registered
T-window slice instead of more waiting. #2 theta4 — unchanged, restating the still-pending
calibration check. #3 mmsell2-vs-mmsell3 — gap widened for a 3rd straight run, strengthening
(not just repeating) the mmsell2-leads finding. #4 MMX — sharpened recommendation to scope
around mmsell2 specifically. #5/#6/#7 otherwise unchanged/updated context.)*
