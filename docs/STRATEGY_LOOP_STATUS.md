# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-12 01:56 PM CDT (run #37)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell3** (5-10c) | 253 | +$4.80 | **+1.9** | 30 | **GATE CLEARED** — n≥150 ✓, >+1.5c ✓, beats mmsell1/2 ✓ |
| **pin15** | 111 | −$4.93 | −4.4 | 0 | improving fast — this batch (n=90) was only −1.6c/trade vs −16.8c first batch |
| **weather_concity** | 14 | −$0.78 | −5.6 | 7 | this batch (n=7) flipped **+6.7c/trade** — con(all) also +8.7c/trade this batch |
| **theta4** (fat-tail) | 3 | +$2.34 | — | 0 | still n=3 = noise; gate n≥80 |
| mmsell (control) | 2,170 | +$10.43 | +0.5 | 53 | breakeven+ |
| mmsell1 / mmsell2 | 1,274 / 834 | +$14.74 / +$9.60 | +1.2 / +1.2 | 42 / 26 | breakeven+ — both now beaten by mmsell3 |
| weather con (all) | 339 | −$2.35 | −0.7 | 16 | recovering (+8.7c this batch) |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — mmsell3's gate CLEARED this run: ready to promote, and it unblocks MMX.** At
n=253 (well past the n≥150 gate), mmsell3 is holding **+1.9c/trade** — still above the +1.5c
keep-threshold and still beating both mmsell1 (+1.2c) and mmsell2 (+1.2c). All three
pre-registered criteria are met. Its edge did compress as n grew (+4.0c @ n=85 → +3.5c @ n=97 →
**+1.9c @ n=253**) — converging toward the gate rather than blowing past it, so it's a real but
thinning edge, not a runaway one. **Next action per the pre-registration:** promote mmsell3
(narrow `mmsell` to the 5-10c band, retire the diluted wider bands) — an operator/fable call, not
the loop's. This also **fires the MMX trigger** (#6 below) — MMX was blocked on exactly this gate.

Two smaller positives this batch: **pin15**'s new 90 trades ran only −1.6c/trade (vs −16.8c in
its first batch) — still negative but sharply better, consistent with the thesis that its edge is
T-window-dependent and early trades were mis-timed. **weather_concity**'s new 7 trades went
**+6.7c/trade** (first positive batch), matching con(all)'s own +8.7c/trade recovery this batch —
still n=14 of the n≥120 gate, pure noise, but worth tracking as the trend flips.

**Gate sweep (step 3b):** mmsell3 **253/150 — CLEARED ✓** · pin15 **111/150** (74%, still −EV
cumulative but improving) · theta4 **3/80** · weather_concity **14/120**.

**Data (last-24h / latest CDT):** crypto_spot 2,874 (01:52 PM ✓), crypto_ladder 64,320 (01:53 PM
✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (01:45–01:54 PM ✓).
xgame_matches +2 in 24h (latest ~05:18 AM CDT; collector matching, book shelved).
**xgame_tapes anomaly:** last-24h count is huge (114,982 rows) but the reported `max(captured_at)`
is ~10:58 PM CDT the prior night (~15h old) — internally inconsistent (a max should be ≥ any row
counted in the same 24h window). Likely a query/data quirk on a shelved, quiet book — not
actionable, flagging for awareness only.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell3 GATE CLEARED (n=253, +1.9c, beats mmsell1/2) → promote + MMX unblocked;
pin15 improving fast (−1.6c this batch vs −16.8c first); concity's first positive batch (+6.7c,
n=7); theta4 still noise; collectors fresh (xgame_tapes latest-timestamp anomaly, not urgent).

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell3 · GATE CLEARED — promote, and unblocks MMX (was: hold to gate)] +1.9c/trade at
   n=253**, past the n≥150 gate, still beating +1.5c keep-threshold and both mmsell1/mmsell2
   (+1.2c each). Edge compressed as n grew (+4.0c→+3.5c→+1.9c) — thinning but real, not
   overstated by small-n luck. **Recommended next action for a fable session:** promote mmsell3
   (narrow `mmsell` to 5-10c band, retire the wider diluted bands mmsell/mmsell1/mmsell2). This
   is the loop's first clean, fully-criteria-met promotion since the registry existed.

2. **[idea-model queue · MMX now UNBLOCKED — mmsell3's gate cleared] Two idea-model runs
   2026-07-10 (`IDEA_MODEL_20260710.md`, `..._run2.md`): 42 candidates, PINNED + DECAY probed &
   KILLED, 9 held, 19 killed.** MMX (extend mmsell 5-10c maker-sell into uncorrelated non-sports
   categories) was **blocked on mmsell3 n≥150 — now cleared (n=253)**. **Trigger fired: re-run
   `kalshi-strategy` on MMX** (material already scoped in `..._run2.md`). NEST still behind theta4
   (n=3/80, far off). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed event.

3. **[pin15 · WATCH — improving sharply, still below gate] 111 settled −$4.93 (−4.4c cum), 0
   open.** This batch's 90 new trades ran **−1.6c/trade**, a big improvement over the first
   batch's −16.8c/trade — consistent with the thesis that early trades mistimed the T-window.
   Gate: n≥150 (74% there), keep only if per-trade > +1.5c AND profit concentrates in T≈120–180s
   entries. Getting close enough to the gate that the next run or two should resolve it — watch
   closely, don't act yet.

4. **[weather_concity · WATCH — first positive batch, still n=14] 14 settled −$0.78 (−5.6c cum);
   this batch's 7 new trades went +6.7c/trade** (first positive reading), matching con(all)'s own
   +8.7c/trade recovery same batch — likely a shared market-conditions effect, not concity-specific
   yet. Gate: n≥120 (12% there), keep only if it beats all-city con. Far too early to read the
   trend; carry forward.

5. **[theta4 · unchanged, still noise] 3 trades, +$2.34 (n=3).** Gate: n≥80, keep only if
   per-trade > 0 AND realized-tail-hit ≤ 1.25x modeled. Accruing very slowly; if still <~10 by
   run #40, revisit the loosen-edge idea to get a testable n. Do not read n=3.

6. **[mmsell existing · context, now behind mmsell3] control/mmsell1/mmsell2 ~breakeven-positive**
   (+0.5c / +1.2c / +1.2c, n≈4,300 combined). Both mmsell1/2 are now beaten by mmsell3's +1.9c —
   supports the promote recommendation in #1 rather than standing alone.

7. **[data anomaly · xgame_tapes latest-timestamp, low priority] `max(captured_at)` reads ~15h
   stale despite 114,982 rows counted in the last 24h** — internally inconsistent, likely a query
   artifact on this shelved/quiet book. Not actionable; watch if it recurs or if xgame_matches also
   goes stale (that would indicate a real collector problem).

*(Changed this run: #1 mmsell3 GATE CLEARED (n=253, +1.9c, beats mmsell1/2) — flipped from "hold"
to "promote," first clean gate-clear since the registry existed. #2 MMX trigger FIRED (mmsell3's
gate was its blocker) — escalated to top of queue. #3 pin15 improving fast (−1.6c this batch vs
−16.8c first, 74% to gate). #4 weather_concity's first positive batch (+6.7c, n=7) — still far
from its gate. #7 NEW: xgame_tapes timestamp anomaly, low-priority/informational. theta4 (#5)
unchanged. Loop cadence note: now fires at fixed 5:30 AM / noon / 8 PM CT via durable Routines,
not an 8h interval — this run was a manual off-schedule fire to validate the new Routines.)*
