# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-10 08:11 PM CDT (run #33)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell3** (5-10c) | 64 | +$1.87 | **+2.9** | 12 | **early-strong** — beats gate (+1.5c) & mmsell1/2; n→150 |
| **theta4** (fat-tail) | 0 | — | — | 0 | **STILL 0 at ~23.5h → DECISION triggered (below)** |
| **weather_concity** | 0 | — | — | 7 | 7 open (AUS/CHI/NYC), none settled yet (weather ~daily) |
| mmsell (control) | 1,769 | +$11.39 | +0.6 | 30 | good window (+$8.6); breakeven+ |
| mmsell1 / mmsell2 | 975 / 640 | +$9.58 / +$6.19 | +1.0 / +1.0 | 21 / 11 | breakeven+ |
| weather con (all) | 307 | −$5.13 | −1.7 | 15 | static since run #32 (con idle) |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — mmsell3 is the first new book with a real (early) positive signal; theta4 hit its
decision point at 0.** mmsell3 jumped n=10 → **64** as its open positions settled, landing at
**+2.9c/trade** — above its +1.5c gate and well above mmsell1 (+1.0c) / mmsell2 (+1.0c). Not yet
the n≥150 gate, but the "5-10c is the pure sweet spot" thesis is looking right so far. Meanwhile
**theta4 is still at 0 trades ~23.5h after deploy** — the pre-registered decision point. con +
concity are static (con idle since yesterday afternoon; concity's 7 opens await daily settlement).

**theta4 — DECISION (pre-registered, now due): the mult=2.0 + 10c bar is effectively
unreachable.** 0 entries in ~23.5h of live evaluation (mechanism unit-tested; ladder collector
fresh, so the tracker IS running). Per the run #32 pre-registration, the recommended fable
action is: **loosen theta4's edge from 10c → ~6c** so it actually trades and we can measure
whether the 2x-fattened model's tails are calibrated (`theta_variants` → `theta4:...,edge=6`);
if it STILL barely trades or trades negative/miscalibrated, the fat-tail revival is impractical
and theta stays fully shelved. Report-only — this is the operator's fable call. (Interpretation
note: that nothing is 10c+ overpriced after a 2x fatten is itself weak evidence the base model's
tail miss really was ~2x — but it gives no tradeable signal, hence loosen-or-conclude.)

**mmsell3 — promising, hold to the gate.** +2.9c/trade at n=64 vs the +1.5c bar. If it holds
through n≥150, it's the portfolio's first genuinely positive book since the cleanup — promote
candidate (narrow mmsell to 5-10c, retire the diluted wider bands). Do NOT call it yet (n=64).

**Data (last-24h / latest CDT):** crypto_spot 2,874 (08:09 PM ✓, 2 products), crypto_ladder
57,920 (08:09 PM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh
(08:10–08:11 PM ✓). xgame_matches 19 (0 new — WC final done), xgame_tapes 77,098 (08:11 PM ✓,
tapering). All green.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell3 early-strong (+2.9c/trade, n=64 — beats its gate & the other bands); theta4
hit its pre-registered decision point at 0 → recommend loosening edge 10→6c or concluding.
concity 7 open awaiting settlement; con idle. Shelved books quiet; collectors fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta4 · DECISION DUE — loosen or conclude (fable)] Still 0 at ~23.5h — bar unreachable.**
   The mult=2.0 + edge=10c gate is almost never cleared. Recommended fable action: set
   `theta4:hi=20,ttemax=35,mult=2.0,edge=6` (loosen the edge) to get a testable n and measure
   the 2x-fattened model's calibration; if it then trades negative/miscalibrated or still
   barely trades, conclude the fat-tail revival is impractical and leave theta fully shelved.
   Not urgent (paper), but this experiment is stuck at n=0 until the edge is loosened.

2. **[mmsell3 · PROMISING — hold to gate] +2.9c/trade at n=64** (beats the +1.5c gate and
   mmsell1/mmsell2 at +1.0c). First new book with a real positive signal. Gate: n≥150, keep
   only if per-trade > +1.5c AND beats mmsell1/2 — it's on track. If it holds, promote (narrow
   mmsell to 5-10c, retire the diluted wider bands). Do NOT act at n=64; let it reach the gate.

3. **[weather_concity · WATCH — 7 open, awaiting first settlements] Wiring confirmed (run #32).**
   Gate: n≥120, keep only if >+3c AND clearly beats full con. First A/B data lands when the 7
   AUS/CHI/NYC opens settle. Still ~1-2 months to the full gate (con is low-frequency; idle since
   yesterday afternoon).

4. **[weather con (all) · context] −$5.13, static.** No action; concity is the test of whether
   restricting to edge cities beats it.

5. **[mmsell existing · unchanged] control/mmsell1/mmsell2 ~breakeven-positive** (+0.6 to +1.0c,
   n≈3,380); data books. mmsell3 is the live improvement candidate.

6. **[idea-model queue · WAITING ON GATES ABOVE — nothing to build yet] Two idea-model runs on
   2026-07-10 (`docs/IDEA_MODEL_20260710.md`, `docs/IDEA_MODEL_20260710_run2.md`) — 42 candidates
   total, 2 pre-registered theses probed and KILLED (PINNED, DECAY — both do-not-promote at the
   probe), 9 held, 19 killed. Nothing is currently promotable to `kalshi-strategy`; every hold is
   blocked on a gate already tracked in this file, not a missing probe:**
   - **MMX (highest-value hold)** — extend mmsell's 5-10c FLB maker-sell into uncorrelated
     non-sports categories (politics fields / awards / box-office / catastrophe / mention-word
     longshots). Blocked on **item #2 (mmsell3 reaching n≥150)** — do not stack a second maker
     book mid-A/B. **Trigger: the moment mmsell3 clears its gate, re-run `kalshi-strategy` on
     MMX** (thesis material + candidate scoring already in `IDEA_MODEL_20260710_run2.md`, no new
     idea-model pass needed). Optional cheap prep in the meantime: `kalshi_flb.py` calibration
     cut on the target categories (not yet run).
   - **NEST** (crypto RV, non-theta) — blocked on **item #1 (theta4's decision)**. Only revisit
     if theta4 resolves to a real, calibrated edge (unlikely per the DECISION note above).
   - **RTPIN / BOXPIN** (entertainment obs-pinning) — blocked on building a new scraper/collector
     (no gate, just unbuilt infra); not urgent, revisit only if a cheap public-data angle appears.
   - **RATELAG** (KXFED coherence) — blocked on an actual live Fed-shock event; nothing to do
     until one occurs.
   Do NOT re-run the idea-model skill again until one of the two gates above (#1 or #2) clears —
   the board was mined hard across 4 total idea-model runs; re-running against unchanged state
   will just re-score the same graveyard.

*(Changed this run: theta4 escalated to DECISION DUE (still 0 at ~23.5h → loosen edge 10→6c or
conclude) — #1. mmsell3 upgraded to PROMISING (+2.9c/trade at n=64, beats its gate & the other
bands) — #2, watch to n≥150. concity still 0 settled (7 open). Shelved books quiet; deploy
healthy. NEW #6: idea-model queue logged — MMX ready to build the moment mmsell3 (#2) gates;
NEST behind theta4 (#1); do not re-run idea-model until a gate clears.)*
