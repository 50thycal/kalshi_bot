# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book. **As of run #74 the LIVE book
is `mmsell10` (live since 2026-07-26 21:09 UTC, with paper twin `mmsell10_pt`) — not mmsell3.**
mmsell3 LIVE was wound down 2026-07-19 (`docs/MMSELL_LIVE_POSTMORTEM.md`) and its account was
confirmed 100% flat since 2026-07-20 10:20:56 CT (post-run-#68 investigation, CLOSED 2026-07-22 —
do not re-flag its flat P&L as staleness). Suggestions are **recommendations only** — the loop
never acts on them; the user reviews and runs fable to change anything. Newest snapshot replaces
the one above it; the suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

*Reporting convention (confirmed 2026-07-23, standing): every run's chat report and this file must
state, for both the live P&L table and each paper book, the **realized P&L (total $)** AND the
**per-trade profit (¢/trade)** side by side.*

---

## Snapshot — 2026-07-28 05:10 PM CDT (run #74)

**TWO GATES CLEARED, and the LIVE book changed identity since the last run.** This snapshot spans
4 days (run #73 was 2026-07-24), so batches are large and the run-#73 correlated NBA-event loss has
been fully washed out — every mmsell variant recovered and most improved their cumulative ¢/trade.

1. **`theta4` gate CLEARED — n=95 ≥ 80.** +$36.72 realized, **+38.65¢/trade**, 92.6% win,
   realized tail-hit **7.4%** at an avg entry of 84.96¢ (market-implied tail ≈15%). Per-trade
   positive ✓; the tail-hit side of the gate reads comfortably calibrated but a fable session
   should confirm the exact modeled-tail number from the signal rows before writing the KEEP
   verdict. **This unblocks NEST** (idea-model queue item that was waiting on theta4's n≥80).
2. **`mmsell10` gate CLEARED — n=228 ≥ 150, +3.73¢/trade — and it is ALREADY LIVE** (real money
   since 2026-07-26 21:09 UTC) with a paper twin `mmsell10_pt` running beside it.
3. **Registry drift (step 3a):** `docs/BOOK_REGISTRY.md` still lists `mmsell10` as **paper** and
   has **no row for `mmsell10_pt`** — the twin is an UNTRACKED book (26 settled, 24 open). Both
   need a registry update from a fable session.

**Live P&L (real money — `mmsell10`, epoch started 2026-07-26 21:09 UTC):**
| metric | value |
|---|---|
| settled live positions | 20 (100% win) |
| realized P&L | **+$2.67** |
| per contract | **+6.68¢/ct** (twin assumed +6.20¢/ct on the SAME matched markets → gap −0.48¢) |
| fill rate | 59.0% (39 placed, 23 filled, 16 canceled) |
| fill price | 93.3¢ real vs 93.4¢ twin-assumed (px_gap −0.08¢ — pricing assumption is sound) |
| open footprint | 4 positions, $5.84 deployed |

Parity verdict: **TOO EARLY** (needs n≥30 each side; twin 26 / live 20). No ACCOUNTING GAP —
matched-market twin-vs-live is −0.48¢, i.e. live is running slightly *better* than its twin. The
gap that exists is CAPACITY (11 of 50 twin entries live never attempted), not simulator error.

**Legacy live (mmsell3, wound down):** 367 settled, 91.3% win, **+$1.33 total, +0.36¢/ct** —
unchanged, as expected. `mmsell3_closeout` remains inert (2010 orders, 1972 rejected, 0 filled).

**Trading books (settled n / realized P&L / ¢-per-trade / open) — PAPER, separate from live:**
| book | n (Δ vs #73) | realized P&L | ¢/trade (was) | open | note |
|---|---|---|---|---|---|
| **mmsell10** | 228 (+104) | +$8.51 | **+3.73** (3.02) | 34 | **GATE CLEARED n≥150; LIVE since 07-26** |
| **mmsell10_pt** (twin) | 26 (new) | +$3.16 | +12.15 | 24 | **UNTRACKED — no registry row** |
| mmsell11 | 434 (+182) | +$16.43 | +3.79 (3.14) | 39 | clears on blended paper; MIRAGE under fill model |
| mmsell6 | 502 (+152) | +$15.31 | +3.05 (2.36) | 37 | clears on blended paper; MIRAGE under fill model |
| mmsell9 | 70 (+44) | +$3.83 | +5.47 (5.42) | 8 | best ¢/trade in cohort, gate n≥100 (70%) |
| mmsell4 | 366 (+167) | +$9.62 | +2.63 (0.60) | 39 | **KILL verdict now contradicted** — beats mmsell3 |
| mmsell8 | 56 (+25) | +$1.47 | +2.63 (−1.65) | 9 | flipped positive, gate n≥100 (56%) |
| mmsell2 (paper) | 1,861 (+202) | +$55.00 | +2.96 (2.88) | 36 | steady |
| mmsell1 (paper) | 2,822 (+303) | +$62.20 | +2.20 (2.03) | 44 | steady |
| mmsell3 (paper shadow) | 1,217 (+191) | +$25.40 | +2.09 (1.59) | 39 | strong recovery batch |
| mmsell5 | 185 (+70) | +$3.07 | +1.66 (−0.08) | 0 | flipped positive |
| mmsell control (paper) | 4,321 (+422) | +$68.54 | +1.59 (1.60) | 49 | flat cumulative |
| mmsell7 | 92 (+34) | +$0.65 | +0.71 (−1.33) | 0 | flipped positive again, gate n≥150 (61%) |
| **theta4** (fat-tail) | 95 (+47) | +$36.72 | **+38.65** (37.0) | 0 | **GATE CLEARED n≥80** |
| weather_con (all) | 572 (+55) | −$14.75 | −2.58 (−2.33) | 14 | negative batch, drifting worse |
| weather_concity | 114 (+24) | −$7.82 | −6.86 (−9.09) | 8 | positive batch (+$0.36), **95% to gate** |
| pin15 | 445 | −$19.74 | −4.43 | 0 | RETIRED 2026-07-16, quiet |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**Gate sweep (step 3b):** theta4 **95/80 CLEARED** (+38.65¢/trade, tail-hit 7.4%) · mmsell10
**228/150 CLEARED + LIVE** · mmsell6 502 / mmsell11 434 clear on blended paper but both read
MIRAGE under the fill model — re-read realizable before any promote · mmsell9 70/100 (70%) ·
mmsell7 92/150 (61%) · mmsell8 56/100 (56%) · mmsell4 366 (KILL verdict contradicted by 4 days of
data: +2.63¢ now beats mmsell3's +2.09¢) · weather_concity **114/120 (95%)** · FREEZE **8/100**
(up from 6, not fired, 26 runs).

**Data (last-24h rows / latest, ~5:05 PM CDT run):** crypto_spot 2,878 (2 products, 5:04 PM ✓) ·
crypto_ladder 35,055 all with model_p (5:04 PM ✓) · weather forecasts 6,000 (5:04 PM ✓) ·
observations 404 (4:58 PM ✓) · ensembles 1,176 (5:04 PM ✓) · bucket snapshots 8,214 (5:00 PM ✓).
All fresh. xgame_matches/tapes still dark (expected — book KILLED, collector-only).

**Research probes (on-demand):** none standing (TFAV/WCPROP/XGAME/PINNED/DECAY families closed).

**Headline:** two gates cleared in one run — theta4 (n=95, +38.65¢/trade) which unblocks NEST, and
mmsell10 (n=228) which is *already trading real money* and is +$2.67 / +6.68¢/ct across 20 settled
live positions with no accounting gap vs its twin. The registry hasn't caught up: mmsell10 is still
listed as paper and the twin `mmsell10_pt` has no row at all. The whole mmsell cohort recovered from
the run-#73 shared-event loss; mmsell4/5/7/8 all flipped positive, which contradicts mmsell4's
standing (unrecorded) KILL verdict. Weather remains the only negative family; weather_concity is
95% to its gate and improving, all-city weather_con is drifting worse.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[NEW · top actionable — theta4 gate CLEARED, NEST unblocked] n=95 ≥ 80, +$36.72,
   +38.65¢/trade, 92.6% win, realized tail-hit 7.4% (avg entry 84.96¢).** Per-trade side of the
   pre-registered gate passes outright. A fable session should (a) confirm the realized-vs-modeled
   tail ratio from the signal rows and record the KEEP verdict in `docs/THETA_THESIS.md` /
   `RESEARCH_JOURNAL.md`, and (b) **re-invoke `kalshi-strategy` on NEST**, which has been waiting
   on exactly this gate.

2. **[NEW · registry drift on the LIVE book] `docs/BOOK_REGISTRY.md` still lists `mmsell10` as
   `paper`, and `mmsell10_pt` (26 settled / 24 open) has no row at all.** mmsell10 has been live
   with real money since 2026-07-26. Registry rows are how this loop reconciles books — a fable
   session should update mmsell10's status to LIVE and add the twin row.

3. **[NEW · mmsell4's KILL verdict is contradicted by the data] n=366, now +2.63¢/trade (was
   +0.60¢), above mmsell3's +2.09¢.** The recommendation for 13 runs was "record the kill" — do
   NOT record it now; re-evaluate instead. The variant's earlier weakness looks like it was the
   small-n / shared-event window, not a real defect.

4. **[mmsell6 / mmsell11 promote question — gate on REALIZABLE, not blended paper] mmsell6 n=502
   +3.05¢, mmsell11 n=434 +3.79¢ on blended paper, but `BOOK_REGISTRY.md` records both as
   MIRAGE under the live-calibrated fill model.** Before any promotion decision, re-run
   **`mmsell fill model`** (`mm_check_1`) for current realizable ¢/trade — the blended number is
   the one that has already misled us once (mmsell3 live).

5. **[mmsell10 live — running, parity TOO EARLY] live n=20 (+$2.67, +6.68¢/ct, 100% win, 59% fill
   rate), twin n=26.** Parity needs n≥30 per side for a verdict; matched-market gap is −0.48¢ (no
   simulator error) and px_gap −0.08¢. Keep both running untouched — the epoch IS the sample. The
   only live-vs-twin divergence so far is CAPACITY (11 twin entries live never attempted).

6. **[weather_concity · 95% to gate] n=114/120, −6.86¢/trade cumulative (improved from −9.09¢ on a
   positive batch).** Resolves next run or two. Note the gate is "beats all-city con" — all-city
   `weather_con` is at −2.58¢/trade and drifting worse, so the comparison is between two negative
   books; a fable session should decide whether "beats con" is still the right bar or whether both
   weather books should be wound down.

7. **[mmsell4/5/7/8 all flipped positive this run] mmsell5 +1.66¢ (n=185), mmsell7 +0.71¢ (n=92),
   mmsell8 +2.63¢ (n=56).** Small-n discipline still applies — mmsell7 has now crossed sign three
   times. Track, don't act.

8. **[idea-model queue] MMX — premise (extend the mmsell edge into new categories) should be built
   against mmsell10, which is now the promoted/live mechanism. NEST — gate CLEARED this run
   (theta4 n=95≥80); ready to build, see #1.**

9. **[FREEZE gate · not fired] Settled grain+soft = 8 of the n≥100 trigger (was 6; 28 open soft
   listings now).** Standing background check, nothing to act on.

10. **[correlated-event risk · standing interpretive note] Run #73's whole-cohort loss traced to a
    single shared ticker (`KXNBATEAMANNOUNCE-...LJAMES23`), and it fully washed out over the
    following 4 days.** Keep checking for a single shared ticker before reading any cohort-wide
    batch move as a strategy-wide signal.

*(Changed this run: #1 NEW — theta4 gate cleared, NEST unblocked. #2 NEW — registry drift on
mmsell10/mmsell10_pt. #3 mmsell4 — REVERSED: the 13-run "record the kill" recommendation is
dropped, the data now contradicts the kill. #4 — restated mmsell6/mmsell11 with the explicit
realizable-vs-blended caveat. #5 NEW — mmsell10 live + twin status. #6 weather_concity — restated
at 95% with a new question about the bar. #7 — the sign flips. #8 — NEST moved from blocked to
ready. #9/#10 restated. Dropped: mmsell10's "83% to gate" item — resolved, gate cleared and the
book is live.)*
