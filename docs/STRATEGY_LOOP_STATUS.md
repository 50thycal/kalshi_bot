# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-12 08:07 PM CDT (run #38)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell3** (5-10c) | 282 | +$4.00 | **+1.4** | 25 | **dipped BELOW +1.5c gate threshold** — this batch (n=29) ran −2.8c/trade |
| **pin15** | 128 | −$6.47 | −5.1 | 0 | this batch (n=17) reversed to −9.1c/trade (prior batch was −1.6c) — 85% to gate |
| mmsell1 / mmsell2 | 1,329 / 870 | +$16.28 / +$11.95 | +1.2 / +1.4 | 35 / 22 | mmsell3 still narrowly ahead of both |
| mmsell (control) | 2,246 | +$19.97 | +0.9 | 46 | breakeven+ |
| **weather_concity** | 14 | −$0.78 | −5.6 | 7 | unchanged this run (no new settles) |
| **theta4** (fat-tail) | 3 | +$2.34 | — | 0 | unchanged this run (no new settles) |
| weather con (all) | 339 | −$2.35 | −0.7 | 16 | unchanged this run (no new settles) |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — correction to run #37: mmsell3's edge dipped back BELOW its own +1.5c keep-threshold.**
Last run I called mmsell3's gate cleanly cleared and recommended promoting it. This run's 29 new
trades settled at **−2.8c/trade**, pulling the cumulative average from **+1.9c (n=253) down to
+1.4c (n=282)** — now *below* the pre-registered +1.5c bar, even though it still narrowly leads
both mmsell1 (+1.2c) and mmsell2 (+1.4c). **This is exactly the kind of single-batch swing the
gate's n≥150 threshold was supposed to guard against, and it still happened at n=282** — the
per-trade edge here is thin enough that batch-to-batch variance is comparable in size to the edge
itself. **Revised recommendation: do NOT promote yet.** Treat mmsell3 as "recently gate-adjacent,
unstable at the threshold" rather than "cleanly resolved" — hold for another run or two to see
whether it stabilizes above or below +1.5c before a fable session acts on last run's promote
suggestion. The MMX unblock (mmsell3 crossing n≥150) still formally stands since n≥150 is a
one-way trigger, but the *performance* leg of the gate is now borderline, so treat MMX as
"technically unblocked, worth a quick recheck before investing build time" rather than a clean
green light.

pin15 also reversed direction: last run's improving batch (−1.6c/trade) was followed by a worse
one this run (n=17, −9.1c/trade), pulling cumulative to −5.1c/trade (was −4.4c). Still 85% to its
own n≥150 gate (128/150) and still clearly −EV cumulative — the improving trend from run #37 did
not hold, treat as noise either direction until the gate resolves. theta4, weather_concity, and
weather con(all) had **no new settlements this run** — all unchanged from #37.

**Gate sweep (step 3b):** mmsell3 **282/150** (n-gate cleared, performance leg now borderline —
see above) · pin15 **128/150** (85%, still −EV) · theta4 **3/80** · weather_concity **14/120**.

**Data (last-24h / latest CDT):** crypto_spot 2,870 (08:02 PM ✓), crypto_ladder 65,040 (08:03 PM
✓, 100% model-priced), weather forecasts/obs/buckets fresh (08:05–08:06 PM ✓), ensembles fresh
(07:23 PM ✓, 43min old vs ~60min cadence). xgame_matches +0 new since run #37 (latest still
~05:18 AM CDT; collector quiet, book shelved, not itself alarming at this cadence).
**xgame_tapes anomaly persists and is now confirmed real, not a one-off:** `max(captured_at)` is
byte-identical to run #37's reading (2026-07-11 10:58 PM CDT, now ~21h stale) despite the 24h-row
count changing (114,982 → 43,829 rows). Two consecutive runs with an unmoving max timestamp while
the count keeps churning is a genuine data/query inconsistency, not noise — worth an ops look
(likely a captured_at vs. insert-order mismatch or a stale materialized value), though still
low-priority since the book is shelved and quiet.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell3's gate-clear from run #37 did NOT hold — dipped to +1.4c/trade (below the
+1.5c bar) on a −2.8c/trade batch; hold off promoting, recheck next run. pin15 reversed to
−9.1c/trade this batch (85% to its gate). theta4/concity/con(all) quiet, no new settles.
xgame_tapes timestamp anomaly confirmed repeating — low-priority ops flag.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell3 · REVISED — do NOT promote yet, edge unstable at the threshold] +1.4c/trade at
   n=282**, down from +1.9c @ n=253 last run after a −2.8c/trade batch (n=29). Still narrowly
   beats mmsell1 (+1.2c) and mmsell2 (+1.4c) but is now BELOW its own +1.5c keep-bar. The
   n≥150 gate stays formally crossed (one-way), but the performance leg is borderline — this
   reverses run #37's "ready to promote" call. **Recommended: wait 1-2 more runs to see if it
   settles clearly above or below +1.5c before a fable session promotes it.** If it keeps
   dipping below +1.5c, the honest read may be "the edge is real but too thin to act on with
   this variance," not a clean win.

2. **[idea-model queue · MMX — technically unblocked, but recheck before building] mmsell3
   crossed n≥150 in run #37 (MMX's formal trigger), but #1 above shows the performance leg is
   now borderline.** MMX (extend mmsell 5-10c maker-sell into uncorrelated non-sports
   categories, material in `IDEA_MODEL_20260710_run2.md`) is worth a quick recheck against
   mmsell3's *stabilized* number (once #1 resolves) before committing `kalshi-strategy` build
   time to it — building on a threshold-noise reading would waste the effort. NEST still behind
   theta4 (n=3/80, far off). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live
   Fed event.

3. **[pin15 · WATCH — reversed this batch, still 85% to gate] 128 settled −$6.47 (−5.1c cum), 0
   open.** This batch's 17 new trades ran **−9.1c/trade**, reversing run #37's improving batch
   (−1.6c/trade) — batch-to-batch swings here are large, consistent with the T-window-dependent
   thesis (some batches catch the right window, some don't). Gate: n≥150 (85% there, close),
   keep only if per-trade > +1.5c AND profit concentrates in T≈120–180s entries. Should resolve
   within the next couple of runs — do not act on either direction yet.

4. **[weather_concity · WATCH — no new settles this run, still n=14] 14 settled −$0.78 (−5.6c
   cum), 7 open, unchanged since run #37.** Gate: n≥120 (12% there), keep only if it beats
   all-city con. Far too early and too quiet to read; carry forward.

5. **[theta4 · unchanged, still noise] 3 trades, +$2.34 (n=3), no new settles this run.** Gate:
   n≥80, keep only if per-trade > 0 AND realized-tail-hit ≤ 1.25x modeled. Accruing very
   slowly; if still <~10 by run #41, revisit the loosen-edge idea to get a testable n. Do not
   read n=3.

6. **[mmsell existing · context] control/mmsell1/mmsell2 ~breakeven-positive** (+0.9c / +1.2c /
   +1.4c, n≈4,400 combined). mmsell3 is still narrowly ahead of both variants despite its dip —
   the promote case isn't dead, just not yet clean (see #1).

7. **[data anomaly · xgame_tapes latest-timestamp, low priority but now CONFIRMED repeating]
   `max(captured_at)` identical across run #37 and #38 (2026-07-11 10:58 PM CDT, now ~21h
   stale) despite the last-24h row count changing (114,982 → 43,829).** Two consecutive
   identical-timestamp readings rules out a one-off fluke — likely a real query/data issue
   (captured_at not reflecting true insert order, or a stale cached value), on a shelved/quiet
   book. Still not urgent, but worth an ops look if it persists into run #39.

*(Changed this run: #1 mmsell3 REVISED from "GATE CLEARED — promote" to "hold, edge dipped below
+1.5c threshold on a −2.8c/trade batch" — direct correction of run #37's framing. #2 MMX
downgraded from "unblocked, go build" to "technically unblocked, recheck before building." #3
pin15 reversed to −9.1c/trade this batch (was improving last run), still 85% to gate. #7 xgame_tapes
anomaly upgraded from "flagging for awareness" to "confirmed repeating, low-priority ops item."
theta4/concity/con(all) (#4, #5) unchanged, no new settlements this run.)*
