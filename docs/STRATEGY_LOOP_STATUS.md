# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-13 08:03 PM CDT (run #41)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **pin15** | 191 | −$11.62 | **−6.1** | 0 | still trading despite run #40's KILL recommendation — 39 more trades, −3.9c/trade this batch (better than the −24.8c batch, still clearly −EV) |
| mmsell3 (5-10c) | 323 | +$6.12 | +1.9 | 32 | edges mmsell2 again by 0.01c — 4th run in a row this exact comparison has flipped |
| mmsell2 | 906 | +$17.05 | +1.9 | 25 | razor-thin behind mmsell3 |
| mmsell1 | 1,387 | +$21.59 | +1.6 | 47 | |
| mmsell (control) | 2,332 | +$23.90 | +1.0 | 68 | |
| weather_concity | 21 | −$3.89 | −18.5 | 7 | unchanged settled/P&L, +6 new opens (no new settles) |
| weather con (all) | 355 | −$7.61 | −2.1 | 15 | unchanged settled/P&L, +10 new opens (no new settles) |
| theta4 (fat-tail) | 4 | +$2.99 | — | 0 | unchanged, no new settles |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — pin15's KILL verdict from run #40 still holds, and it's still trading.** Since I
flagged pin15 as a clean gate-fail last run (n=152, −6.6¢/trade), it has settled **39 more
trades** and lost another **$1.52** (−3.9¢/trade this batch — better than the prior −24.8¢/trade
batch, but still clearly negative). Cumulative is now −6.1¢/trade at n=191, well past the n≥150
gate with no ambiguity in the verdict. This isn't a loop failure — the loop reports/suggests
only, and no fable session has acted on the recommendation yet — but it's worth restating plainly:
**every run this sits unactioned costs a small amount of real (paper) money, and the case for
retiring it hasn't changed or weakened.**

**mmsell3 vs mmsell2 keeps flipping — 4th consecutive run.** mmsell3 (+1.895¢) now edges mmsell2
(+1.882¢) by a mere 0.013¢/trade — technically clears all three keep-criteria again, but the
margin is far smaller than noise. Across runs #38-#41 this exact "does mmsell3 beat mmsell2"
question has flipped essentially every run. **Read this as settled, not pending:** the honest
conclusion is these two variants are statistically indistinguishable at this n, and no future
run is likely to resolve it cleanly — this is itself useful information for a fable session
(narrowing to mmsell3 alone vs keeping both may not matter much either way).

weather_concity and weather con(all) had **no new settlements** this run (both picked up new
opens only) — normal ahead of the next daily batch. theta4 also unchanged.

**Gate sweep (step 3b):** pin15 **191/150 — KILL verdict holds, unactioned** · mmsell3 **323/150**
(keep-criteria met again this run, 4th flip vs mmsell2) · theta4 **4/80** · weather_concity
**21/120** (17.5%).

**Data (last-24h / latest CDT):** crypto_spot 2,876 (08:00 PM ✓), crypto_ladder 34,601 (08:00 PM
✓, 100% model-priced), weather forecasts/obs/buckets fresh (07:56–08:01 PM ✓), ensembles fresh
(07:22 PM ✓, 39min old vs ~60min cadence). **xgame_tapes partially RECOVERED** — 8,801 rows in
the last 24h, latest fresh at 08:01 PM ✓ (was 0 rows / frozen last run). **xgame_matches is still
dark** — 0 rows in 24h, latest still frozen at 2026-07-12 (now ~39h stale). So the tape-capture
side of the xgame collector came back on its own, but match-detection specifically remains down —
narrows last run's finding rather than resolving it.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** pin15's KILL verdict holds and it's still trading (39 more trades, −$1.52, unactioned
recommendation). mmsell3/mmsell2 flip for the 4th straight run — treat as statistically tied, not
pending. weather/theta quiet, no new settles. xgame_tapes came back on its own; xgame_matches
still dark — partial, not full, recovery.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[pin15 · KILL verdict holds, still unactioned — restating] n=191 (well past n≥150),
   −6.1c/trade cumulative, −$1.52 more lost this run (39 trades at −3.9c/trade).** Same
   recommendation as run #40, now with more evidence behind it and a small real cost to waiting:
   **a fable session should formally retire pin15** (stop entries; keep book/data for the
   record). Nothing here should change anyone's mind — the batch-level number improved
   (−24.8c → −3.9c) but cumulative is still decisively negative at 4x the gate's n.

2. **[mmsell3 vs mmsell2 · read as SETTLED-TIED, not pending] mmsell3 +1.895c vs mmsell2 +1.882c
   at n=323/906** — a 0.013c margin, and this exact comparison has flipped every run for 4
   straight runs (#38-#41). **Recommend treating this as resolved: the two variants are
   statistically indistinguishable at this sample size**, not as an open question that a future
   run will settle. Useful for a fable session deciding whether narrowing to mmsell3-only
   (vs keeping both mmsell2/mmsell3) is worth the complexity — the P&L case for choosing one
   over the other is not there.

3. **[idea-model queue · MMX — still "recheck before building," now with sharper evidence]**
   mmsell3's n≥150 trigger is long past. Given #2's finding (mmsell3 and mmsell2 are tied, not
   mmsell3 uniquely ahead), MMX's premise — extend mmsell3's *specific* edge into new categories —
   deserves a second look at whether "the mmsell 5-10c maker-sell family broadly" (not mmsell3
   specifically) is the better framing before committing build time
   (`IDEA_MODEL_20260710_run2.md`). NEST still behind theta4 (n=4/80, far off). RTPIN/BOXPIN
   behind unbuilt scraper infra. RATELAG behind a live Fed event.

4. **[weather_concity · WATCH, quiet this run] 21 settled −$3.89 (−18.5c cum), +6 new opens, no
   new settlements.** Gate: n≥120 (17.5% there). Nothing new to read; carry forward.

5. **[theta4 · unchanged, still noise] 4 trades, +$2.99 (n=4/80), no new settlements.** Gate:
   keep only if per-trade > 0 AND realized-tail-hit ≤ 1.25x modeled. Still accruing very slowly;
   if still <~10 by run #42 (next run), revisit the loosen-edge idea to get a testable n — this
   trigger is now due.

6. **[xgame collectors · UPDATE, partial recovery] `xgame_tapes` resumed on its own (8,801 rows
   in the last 24h, fresh as of this run) — but `xgame_matches` (the match-detection layer)
   remains dark (0 rows, ~39h-stale latest).** Book is shelved/killed already, so still
   low-urgency, but this narrows last run's finding: it's specifically match-detection that's
   stuck, not the whole xgame pipeline. Worth a quick look if anyone's touching that code anyway;
   not worth a dedicated session on its own.

7. **[mmsell existing · context] control/mmsell1 ~breakeven-positive** (+1.0c/+1.6c). mmsell2 and
   mmsell3 are essentially tied at the top of the family (see #2) — stop treating mmsell3 as the
   sole "improvement candidate" in future reports; it's a two-way tie.

*(Changed this run: #1 pin15 — KILL verdict reaffirmed with more data (n=191, −6.1c/trade), still
unactioned, restated plainly with the small-but-real cost of waiting. #2 mmsell3-vs-mmsell2 —
reframed from "still hold, keeps flipping" to "read as settled-tied" after a 4th consecutive
flip; this is now the takeaway, not an open question. #3 MMX — sharpened to question whether the
mmsell3-specific framing is even right given #2. #6 xgame — updated from "both collectors dark"
to "tapes recovered on their own, matches still dark" (partial, not full, recovery). #5 theta4's
"revisit at run #42" trigger is now due next run. #4/#7 otherwise unchanged, quiet.)*
