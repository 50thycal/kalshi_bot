# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-11 03:13 AM CDT (run #34)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell3** (5-10c) | 76 | +$2.71 | **+3.6** | 9 | **strengthening** (+2.9→+3.6c); ~half to n≥150 gate |
| **theta4** (fat-tail) | 0 | — | — | 0 | still 0 at ~30h — decision stands (loosen or conclude) |
| **weather_concity** | 0 | — | — | 7 | 7 open; first settlements due ~9 AM CDT batch today |
| mmsell (control) | 1,822 | +$11.99 | +0.7 | 17 | breakeven+ |
| mmsell1 / mmsell2 | 1,008 / 667 | +$10.97 / +$7.20 | +1.1 / +1.1 | 16 / 8 | breakeven+ |
| weather con (all) | 307 | −$5.13 | −1.7 | 15 | idle ~13h (overnight); 15 open await settle |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — quiet run; mmsell3 keeps strengthening.** mmsell3 rose to **+3.6c/trade at n=76**
(from +2.9c at n=64) — still clearly above its +1.5c gate and the other bands (+1.1c), ~half way
to the n≥150 gate. theta4 remains at 0 (~30h; the loosen-or-conclude decision from run #33 is
unchanged and awaits a fable pass). Weather is in its overnight lull — con idle ~13h, concity's 7
edge-city opens still pending; both con and concity settle at today's ~9 AM CDT batch, which is
when the first concity A/B data lands. Everything else steady; collectors all fresh.

**Data (last-24h / latest CDT):** crypto_spot 2,870 (03:09 AM ✓, 2 products), crypto_ladder
57,200 (03:09 AM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh
(03:01–03:13 AM ✓). xgame_matches 19 (0 new — WC over), xgame_tapes 77,862 (03:11 AM ✓, flat).
All green.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell3 strengthening (+3.6c/trade, n=76, ~half to gate — the near-term win);
theta4 still 0 at ~30h (decision stands); concity's first A/B settlements land at the ~9 AM batch
today. Shelved books quiet; collectors fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta4 · DECISION DUE — loosen or conclude (fable)] Still 0 at ~30h — bar unreachable.**
   The mult=2.0 + edge=10c gate is almost never cleared. Recommended fable action: set
   `theta4:hi=20,ttemax=35,mult=2.0,edge=6` (loosen the edge) to get a testable n and measure
   the 2x-fattened model's calibration; if it then trades negative/miscalibrated or still
   barely trades, conclude the fat-tail revival is impractical and leave theta fully shelved.
   Not urgent (paper), but this experiment is stuck at n=0 until the edge is loosened.

2. **[mmsell3 · PROMISING, strengthening — hold to gate] +3.6c/trade at n=76** (up from +2.9c @
   n=64; beats the +1.5c gate and mmsell1/mmsell2 at +1.1c). First new book with a real positive
   signal, and it's improving as n grows. Gate: n≥150, keep only if per-trade > +1.5c AND beats
   mmsell1/2 — on track. If it holds, promote (narrow mmsell to 5-10c, retire the diluted wider
   bands) AND it unblocks MMX (#6). Do NOT act at n=76; let it reach the gate.

3. **[weather_concity · WATCH — first settlements due ~9 AM CDT today] 7 open (AUS/CHI/NYC).**
   Gate: n≥120, keep only if >+3c AND clearly beats full con. The first direct A/B vs the
   bleeding all-city con (−$5.13) lands at today's settlement batch. Still ~1-2 months to the
   full gate (con is low-frequency).

4. **[weather con (all) · context] −$5.13, idle ~13h.** No action; concity is the test of
   whether restricting to edge cities beats it.

5. **[mmsell existing · unchanged] control/mmsell1/mmsell2 ~breakeven-positive** (+0.7 to +1.1c,
   n≈3,500); data books. mmsell3 is the live improvement candidate.

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
     idea-model pass needed). mmsell3 is now +3.6c @ n=76 — trending toward that trigger. Optional
     cheap prep: `kalshi_flb.py` calibration cut on the target categories (not yet run).
   - **NEST** (crypto RV, non-theta) — blocked on **item #1 (theta4's decision)**. Only revisit
     if theta4 resolves to a real, calibrated edge (unlikely per the DECISION note above).
   - **RTPIN / BOXPIN** (entertainment obs-pinning) — blocked on building a new scraper/collector
     (no gate, just unbuilt infra); not urgent, revisit only if a cheap public-data angle appears.
   - **RATELAG** (KXFED coherence) — blocked on an actual live Fed-shock event; nothing to do
     until one occurs.
   Do NOT re-run the idea-model skill again until one of the two gates above (#1 or #2) clears —
   the board was mined hard across 4 total idea-model runs; re-running against unchanged state
   will just re-score the same graveyard.

*(Changed this run: mmsell3 strengthened to +3.6c @ n=76 (from +2.9c @ n=64) — #2 trending toward
its gate + the MMX trigger (#6). theta4 still 0 at ~30h — decision unchanged (#1). concity's first
A/B settlements now due at today's ~9 AM CDT batch (#3). #6 idea-model queue carried forward from
the parallel session. Shelved books quiet; deploy healthy.)*
