# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 4-hourly status loop (`kalshi_loop_checker_phase_3` skill).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-05 03:14 PM CDT (run #21)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 510 | +$6.52 | +1.3 | 82 | baseline |
| **mmsell1** (5-20¢) | 31 | +$0.77 | **+2.5** | 62 | still ahead (4 runs); edge regressing to realism |
| **mmsell2** (10-20¢) | 21 | +$1.33 | **+6.3** | 32 | still well ahead |
| tfav (NEW) | 1 | +$0.99 | — | 0 | first settle + (n=1 noise) |
| theta (control) | 135 | −$16.49 | −12.2 | 0 | negative |
| **theta3** | **60** | **−$7.98** | −13.3 | 0 | **HIT THE GATE — negative → fails pre-registered rule** |
| theta1 / theta2 | 8 / 2 | −$3.64 / −$3.79 | — | 0/0 | never reached gate (sparse) |
| weather con | 242 | +$7.25 | +3.0 | 8 | healthy, trading |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**XGAME FIX IS LIVE AND WORKING.** The tag fix deployed: `data:xgame_matches` **0 → 13**
(the 13 World Cup games pair now) and `data:xgame_tapes` **0 → 30,160** rows in 24h — both
venues' in-play trade tapes are collecting. The XGAME research dataset is finally filling.

**theta — VERDICT REACHED (negative).** theta3, the best revision, hit its ≥60 gate at
**−$7.98 / −13.3¢per trade** — negative, so it fails the pre-registered "keep only positive
AND calibrated" rule; the control (135) is −$16.49 and theta1/theta2 never reached n=60. Per
the rule, **the theta family should be shelved.** (Report-only — the operator acts via fable.)

**mmsell A/B — variants still ahead, 4 runs.** mmsell1 +2.5¢, mmsell2 +6.3¢ vs control +1.3¢.
Edges are regressing toward realism as n grows (mmsell2 10.1→6.3) but stay clearly above the
control. n at 31 / 21 — hold to ~150.

**Data (last-24h / latest CDT):** crypto_spot 2,876 (03:12 PM ✓), ladder 61,842 (✓, 100%
model-priced), forecasts/obs/ensembles/buckets ✓. **xgame_matches 13 / tapes 30,160 ✓ (FIXED).**

**Research probes (on-demand):** WCPROP · TFAV now a live book. XGAME's `xgame_tape_study`
probe is now runnable (tape data exists) once a WC match plays live.

**Headline:** the XGAME matcher fix is validated in production (13 matches, 30k tapes). theta
reached its pre-registered gate negative → shelve-the-family decision is ready for fable.
mmsell variants keep beating the control; weather con-only healthy.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · VERDICT — shelve the family (fable)] theta3 hit n=60 at −$7.98; all books
   negative.** The pre-registered rule (positive AND calibrated at the gate) is not met by any
   theta book. Recommended fable action: disable the theta books (`THETA_ENABLED=false` /
   drop the variants) but **keep the crypto_spot + ladder collectors** — that labeled dataset
   is exactly what a future recalibrated (fatter-tail) model would be rebuilt from. Write the
   post-mortem in RESEARCH_JOURNAL. Not urgent (paper, no money lost), but the experiment is
   decided.

2. **[mmsell A/B · early-positive, 4 runs] Variants ahead of control** (+2.5 / +6.3¢ vs +1.3¢).
   Hold to ~150 settled each; if the edge holds above the control, promote the narrowed band
   and retire the wide 5-40¢ control.

3. **[XGAME · FIXED — next step is the study] Collector now matching (13) + taping (30k).**
   Once a matched WC game plays live, run `xgame_tape_study` to grade the lead-lag thesis
   (P1-P4 in docs/IDEA_MODEL_20260704.md). No collector action needed.

4. **[tfav · watch] First settle +$0.99 (n=1).** Let it accumulate.

5. **[weather · resolved] con-only stable** (+$7.25, 8 open); pruned books 0 open.

*(Resolved: XGAME matcher bug — fixed + deployed, now collecting. theta "decision imminent"
→ decision REACHED (negative). Added #1 shelve-theta + #3 run-the-study.)*
