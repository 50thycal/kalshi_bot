# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 4-hourly status loop (`kalshi_loop_checker_phase_3` skill).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-04 10:43 PM CDT (run #16) — first phase-3 run (all books individual)

Query widened to show **every non-weather strategy individually** so new books surface.
Result: no *new trading books* beyond the known set — the recent Phase-2 work (TFAV /
WCPROP / XGAME, from another session's PR #9) is **probes + a data collector, not paper
books**. My mmsell1/mmsell2 + weather prune are **built but NOT merged**, so they don't
show yet.

**Trading books (settled n / P&L / open):**
| book | n | P&L | open | note |
|---|---|---|---|---|
| theta (control) | 91 | −$9.74 | 2 | unchanged (left running per operator) |
| theta1 (5-20¢, 10-35m) | 6 | −$0.09 | 1 | ~breakeven, best-calibrated |
| theta2 (thr-only) | 2 | −$3.79 | 0 | idle |
| theta3 (wide, edge≥12, ×1.25) | 30 | −$5.04 | 2 | halfway to gate, red after evening cluster |
| mmsell (control) | 449 | +$4.87 | 43 | +1.1¢/trade; **mmsell1/2 not deployed yet** |
| weather con | 228 | +$10.82 | 14 | the keeper |
| weather (rest) | 4,659 | −$235.77 | **50** | **still trading — prune not deployed yet** |
| legacy TA (buy_fav/mom/rev) | 0 | — | 0 | long dead |

**Data collection (last-24h rows / latest CDT):**
| collector | 24h | latest | status |
|---|---|---|---|
| crypto_spot_candles | 2,872 | 10:39 PM | ✓ fresh, 2 products |
| crypto_ladder_snapshots | 61,440 | 10:39 PM | ✓ fresh, 100% model-priced |
| weather_forecasts | 11,373 | 10:42 PM | ✓ fresh |
| weather_observations | 651 | 10:42 PM | ✓ fresh |
| weather_ensembles | 1,712 | 10:29 PM | ✓ fresh (hourly) |
| weather_bucket_snapshots | 13,506 | 10:41 PM | ✓ fresh |
| **game_tape_snapshots (XGAME)** | **0** | **—** | ⚠️ **ZERO — new collector producing nothing** |

**Headline:** the phase-3 query surfaces all books individually (mechanism works). Nothing
new is *trading* yet — my mmsell1/2 + weather prune await a merge, and the Phase-2 additions
are research probes + the XGAME collector (which has collected **0** game tapes — the one real
data-health flag this run). Everything else fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[deploy · NEW] Merge the fable branch to activate the pending changes.** mmsell1 (5-20¢)
   / mmsell2 (10-20¢) revision books and the weather prune-to-con-only are committed + tested
   but unmerged — that's why weather (rest) still has 50 open and no mmsell variants appear.
   A merge + deploy applies both (env verified clean, no overrides block them).

2. **[XGAME · NEW — data-health] game_tape_snapshots = 0.** The new cross-venue in-play
   collector (PR #9) has stored nothing. Likely no matched Kalshi↔Polymarket games in-window
   (the journal noted KXWCGAME close_time is a far-future settlement deadline, re-keyed to the
   game day) — but a collector at zero is worth a look: check the `xgame collector` log line
   (kalshi_games / pm_games / matched) to see whether matching is firing. Fable topic.

3. **[theta · IN FLIGHT] Run the revision experiment untouched to ≥~60 settled/book.** theta3
   30/60 (red after an evening tail cluster — variance, not verdict); theta1 best-calibrated at
   n=6; theta2 near-idle. The control (91) is shaping toward a negative, miscalibrated verdict.

4. **[mmsell · verdict pending on variants] Control +1.1¢/449.** The forward decomposition
   (cheap-longshot edge) is the basis for mmsell1/2 — once deployed, watch whether the narrowed
   bands beat the control forward (~150 settled), not just in the in-sample slice.

5. **[weather · resolved on merge] Prune to con-only** is built; will take effect on deploy.
   con remains the sole +EV weather book (+$10.82 / +4.1¢).

*(Phase-3 changes this run: renamed skill, 4-hourly cadence, all-books-individual query.
Added #1 deploy-pending + #2 XGAME-zero. Dropped the old mmsell "extend to n≈600" — superseded
by the variant experiment awaiting deploy.)*
