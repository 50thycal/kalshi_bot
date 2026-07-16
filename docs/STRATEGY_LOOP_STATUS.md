# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-16 05:36 AM CDT (run #48)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell2 | 1,165 | +$29.94 | +2.6 | 13 | still family leader, quiet batch |
| mmsell1 | 1,788 | +$35.26 | +2.0 | 15 | |
| mmsell (control) | 2,867 | +$42.15 | +1.5 | 20 | |
| mmsell3 (5-10c) | 570 | +$9.18 | +1.6 | 10 | flat tiny batch, unchanged |
| **pin15** | 435 | −$17.06 | −3.9 | 0 | **thesis FALSIFIED last session — recommend retire**; bled another −$1.47 this batch |
| theta4 (fat-tail) | 25 | +$16.85 | +67.4 | 0 | **calibration PASSED last session (safe direction, untested)**; no new settles |
| weather con (all) | 382 | −$9.38 | −2.5 | 16 | unchanged settled/P&L, +2 new opens |
| weather_concity | 31 | −$3.84 | −12.4 | 7 | unchanged settled/P&L, +1 new open |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — two research verdicts from last session now stand as the actionable items: RETIRE
pin15 (thesis falsified), KEEP theta4 (calibration clean, but untested). Everything else quiet.**

Both user-requested checks were run 2026-07-15 (results folded into the suggestions below):
- **pin15 — thesis FALSIFIED → recommend retire.** The T-window slice showed the pre-registered
  edge (profit concentrated in T≈120-180s at >+1.5¢) doesn't exist: the target window earns only
  +0.27¢/trade, no entry window clears the +1.5¢ bar, and the whole cumulative loss traces to one
  sub-window (60-120s entries at −53¢/trade). This run it bled another −$1.47 (n=39, −3.8¢/trade),
  exactly the negative-skew blowout pattern the slice diagnosed. **The batch oscillation the loop
  chased for runs #40-47 is resolved — it was noise around a sub-threshold edge.** Standing rec:
  a fable session disables pin15 entries.
- **theta4 — calibration PASSED (safe direction) → keep running, no action.** 0/25 realized
  tail-hits vs 6.9% modeled (expected ~1.7); not the original-theta failure mode. But 0 hits in 25
  is ~16% likely even if calibrated — untested, not confirmed. No new settlements this run (still
  n=25/80). Watch the realized hit rate as it climbs to the gate.

mmsell family quiet (small flat batches; mmsell2 still nominally on top at +2.6¢, not tracking the
intra-family ranking per-run per run #45). weather books quiet (new opens only).

**Gate sweep (step 3b):** theta4 **25/80** (31%, calibration clean/untested — keep) · pin15
**435/150** (gate long past, thesis falsified — retire, not a gate question anymore) · mmsell3
**570/150** (own bar cleared, ranking noise) · weather_concity **31/120** (26%).

**Data (last-24h / latest CDT):** crypto_spot 2,870 (05:31 AM ✓), crypto_ladder 48,075 (05:31 AM
✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (05:22–05:35 AM ✓).
**xgame collectors FLIPPED state this run:** `xgame_matches` **RECOVERED** — 4 new matches in 24h,
latest 2026-07-15 10:12 PM CDT, after ~9 runs frozen (so it was a transient stall, not permanently
broken as run #46-47 assumed). But `xgame_tapes` is now **frozen ~13.5h** at the exact run #47
timestamp (2026-07-15 04:06 PM CDT) — last run's "benign ~4h lull" has become a real stall, and
it's the opposite collector from before. Both on the shelved xgame book, so still low-urgency, but
the state genuinely inverted.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** the two checks are resolved — RETIRE pin15 (thesis falsified, still bleeding),
KEEP theta4 (calibration clean but untested). mmsell/weather quiet. xgame collectors flipped:
matches recovered, tapes now stalled ~13.5h (both shelved, low-urgency).

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[pin15 · RETIRE — thesis falsified 2026-07-15, still bleeding] n=435, −$17.06 (−3.9¢/trade),
   another −$1.47 this batch.** The T-window slice settled it: target window (120-180s) earns only
   +0.27¢/trade, no window clears +1.5¢, entire loss is the 60-120s blowout sub-window (−53¢/trade).
   **Recommendation: a fable session disables pin15 entries** (keep book/data for the record). This
   is the top actionable item now — every run it stays live loses a little more to the same
   negative-skew tail the slice already diagnosed. Even the charitable fix (exclude <120s entries)
   stays sub-bar, so a "restrict, don't kill" variant isn't worth it.

2. **[theta4 · KEEP, calibration clean but untested 2026-07-15] n=25/80 (31%), +67.4¢/trade, 0/25
   tail-hits vs 6.9% modeled.** Passes the gate in the safe direction (not the original-theta
   under-pricing failure). No action — keep running. **Re-check the realized tail-hit rate as n
   approaches the n≥80 gate**; the first few hits are the real test of whether the +67¢ (honest
   estimate ~+39¢ once modeled losses land) holds. If theta4's calibration stays clean through the
   gate, it may be the strongest book in the portfolio — which also unblocks NEST (#3).

3. **[idea-model queue · NEST unblocking path clarified; MMX unchanged] NEST is behind theta4's
   gate — theta4's calibration is now clean (#2), so if it holds through n≥80, NEST becomes
   buildable (was the main uncertainty).** MMX (`IDEA_MODEL_20260710_run2.md`) still shouldn't
   assume a specific mmsell variant as template (family ranking is noise, run #45/#47). RTPIN/BOXPIN
   behind unbuilt scraper infra. RATELAG behind a live Fed event.

4. **[mmsell family · quiet, not tracking intra-family ranking per-run] control/mmsell1/2/3 at
   +1.5/+2.0/+2.6/+1.6¢, all within a tight band.** Per run #45's standing call, not re-narrating
   which variant leads each run — treat as one edge with sampling noise. mmsell3's gate is long
   past; nothing pending here.

5. **[weather_concity · WATCH, fully quiet] 31 settled −$3.84 (−12.4¢/trade cum), unchanged 4 runs
   running (new opens only).** Gate: n≥120 (26% there). Carry forward.

6. **[xgame collectors · state FLIPPED, both low-urgency] `xgame_matches` recovered (was a
   transient multi-day stall, not permanently broken); `xgame_tapes` now frozen ~13.5h.** Book is
   shelved/killed, so neither is urgent — but the run #46-47 framing ("matches permanently broken,
   tapes benign") was backwards and is corrected here. If anyone revisits xgame, tapes is the one
   currently stuck.

*(Changed this run: #1 pin15 — check RESOLVED (thesis falsified), now the top actionable retire
recommendation, still bleeding as predicted. #2 theta4 — check RESOLVED (calibration clean but
untested), keep + re-check at gate. #3 NEST — unblocking path clarified now that theta4's
calibration is clean. #6 xgame — corrected the collector framing (matches recovered, tapes now the
stuck one). #4/#5 quiet/unchanged. No gate cleared; no untracked books.)*
