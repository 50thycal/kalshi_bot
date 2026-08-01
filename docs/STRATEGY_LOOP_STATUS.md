# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book. **As of run #74 the LIVE book
is `mmsell10` (live since 2026-07-26 21:09 UTC, with paper twin `mmsell10_pt`) — not mmsell3.**
mmsell3 LIVE was wound down 2026-07-19 (`docs/MMSELL_LIVE_POSTMORTEM.md`) and its account was
confirmed 100% flat since 2026-07-20 10:20:56 CT (post-run-#68 investigation, CLOSED 2026-07-22 —
do not re-flag its flat P&L as staleness). **As of run #76, `theta4` is ALSO live** (Stage 1 pilot,
armed 2026-07-30, see the run #76 snapshot below) — the loop now tracks TWO live books. Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs fable to
change anything. Newest snapshot replaces the one above it; the suggestion list carries over
run-to-run. All times CENTRAL (CDT/CST).*

*Reporting convention (confirmed 2026-07-23, standing): every run's chat report and this file must
state, for both the live P&L table and each paper book, the **realized P&L (total $)** AND the
**per-trade profit (¢/trade)** side by side.*

**[2026-07-29] `theta_fill_model` built and merged (`docs/THETA_FILL_MODEL.md`).** theta uses the
identical maker-sell convention as mmsell, which paper trading overstates via adverse selection on
resting fills. theta has never traded live (zero `theta*` rows in `live_orders`), so the script
falls back to mmsell3's live calibration, clearly labeled BORROWED (cross-market-series, unproven
transfer). First live run, all theta books at once: **theta4 (n=112) +38.62¢ optimistic → +0.51¢
realizable at 27.7% coverage** — the same order-of-magnitude mirage that hit mmsell3. The other
theta books (control/1/2/3) are all under 50% coverage too (theta3 at just 1.5%), and two
paper-negative books (theta2, theta3) invert to realizable-positive under the borrowed calibration —
a sign the calibration barely reaches these price cells, not a real result. **Every theta book
reads "low coverage" — none of this is gate-worthy yet, treat it as a standing caution alongside
theta4's paper-gate pass, not a verdict.**

**[2026-07-31, run #77] `theta_fill_model` auto-upgraded from BORROWED to theta's OWN live
calibration** the moment theta4's live pilot produced its first fills — exactly the designed
behavior, no code change needed. With only 4 live fills so far, every price cell is still "thin"
(below the 8-fill trust threshold), so coverage reads 0% everywhere — expected, not a bug. This
will strengthen automatically as the pilot accumulates fills.

---

## Snapshot — 2026-07-31 08:26 PM CDT (run #77)

**HEADLINE: theta4's live Stage-1 pilot got its FIRST FILLS — 4 filled, 100% win, and
`theta_fill_model` auto-upgraded from BORROWED to theta's OWN calibration the moment it happened.**
Small sample (n=4), nothing gate-worthy yet, but the mechanism worked exactly as designed with zero
code changes needed. Decision alignment: twin opened 5, live placed 4 (1 rejected) — 80% overlap.
Execution realism: 100% fill rate (4/4, no cancels), live px 78.8¢ vs twin 78.8¢ (px_gap +0.20¢ on
the one filled-price comparison — small, not concerning at this n). Matched-market read (n=4): twin
+19.67¢/ct vs live +21.00¢/ct — **live so far running slightly BETTER than its twin**, no
accounting-gap signal. `theta_fill_model` now reads "theta's OWN live orders" as its calibration
source, but every price cell is still "thin" (<8 fills), so coverage is 0% everywhere — expected,
watch this fill in as the pilot accumulates. Nowhere near Stage 1's ≥80-filled-round-trip gate yet.

Second: **`mmsell10_pt`'s PARAM DRIFT anomaly from run #76 is still unresolved** — `live_paper_parity`
continues to flag it every run; no fresh twin tag has been started yet.

Third: a real live-infra bugfix landed on the default branch since #76 (`fc341cd`, "mmsell live:
retry the entry paper's phantom position was locking live out of..."): live's own entry mirror was
being skipped every cycle after the first because the paper book's `skip_already_open` check was
also gating the live attempt underneath it — live got exactly one shot per ticker, ever. Already
merged and fixed; noted here as background, not an open item.

**Live P&L (real money — `mmsell10`, epoch started 2026-07-26 21:09 UTC):**
| metric | this run | last run (#76) |
|---|---|---|
| settled live positions | 29 (100% win) | 26 |
| realized P&L | +$3.91, +6.74¢/ct | +$3.49, +6.72¢/ct |
| fill rate | 62.7% (71 placed, 42 filled, 25 canceled) | 60.0% |
| open footprint | **14 positions, $24.44 deployed** | 11 positions, $18.90 |

Open footprint has now grown for 3 consecutive runs (5→11→14 positions, $7.70→$18.90→$24.44) with
fill economics and P&L staying flat throughout — still reads as normal accumulation given win rate
and ¢/ct are stable, but worth a settlement-rate sanity check if it's still climbing next run.

Parity verdict: still **TOO EARLY** (twin n=56, live n=29 — just 1 short of the n≥30 bar).
Matched-market gap −0.48¢ (steady, no ACCOUNTING GAP). Legacy `mmsell3` live unchanged: 367
settled, +$1.33, +0.36¢/ct. `mmsell3_closeout` still inert.

**Live P&L (real money — `theta4`, Stage 1 pilot):** first fills this run — 4 settled, 100% win,
+$2.52 total, +21.00¢/ct (twin: 5 settled, +$2.98, +19.87¢/ct). See headline for detail. Far too
early for any verdict (need ≥80 filled round-trips for Stage 1).

**Trading books (settled n / realized P&L / ¢-per-trade / open) — PAPER, separate from live:**
| book | n (Δ vs #76) | realized P&L | ¢/trade (was) | open | note |
|---|---|---|---|---|---|
| mmsell10 | 261 (+17) | +$10.39 | +3.98 (3.85) | 33 | live, holding |
| mmsell10_pt (twin) | 56 (+16) | +$6.93 | +12.38 | 27 | PARAM DRIFT still unresolved |
| mmsell11 | 475 (+20) | +$19.46 | +4.10 (3.90) | 33 | mirage under fill model |
| mmsell6 | 539 (+20) | +$17.64 | +3.27 (3.13) | 33 | mirage under fill model |
| mmsell9 | 80 (+9) | +$4.41 | +5.51 (5.48) | 0 | 80% to gate |
| mmsell4 | 407 (+20) | +$12.65 | +3.11 (2.82) | 33 | KILL verdict still contradicted |
| mmsell8 | 68 (+10) | +$2.57 | +3.78 (2.79) | 0 | improving, 68% to gate |
| mmsell2 (paper) | 1,898 (+22) | +$59.59 | +3.14 (3.05) | 26 | steady |
| mmsell1 (paper) | 2,877 (+25) | +$67.76 | +2.36 (2.29) | 36 | steady |
| mmsell3 (paper shadow) | 1,258 (+20) | +$28.43 | +2.26 (2.16) | 33 | steady improvement |
| mmsell5 | 185 (+0) | +$3.07 | +1.66 | 0 | quiet |
| mmsell control (paper) | 4,383 (+28) | +$77.97 | +1.78 (1.67) | 38 | steady |
| mmsell7 | 117 (+6) | +$2.24 | +1.91 (1.67) | 4 | still improving, 78% to gate |
| mmsellA1/A2/A3 (stop-loss L12/L20/L30) | 4 each (NEW settled) | +$0.21 each | ~+5.25 each | 20/20/21 | too new to read |
| mmsellA4 (vol entry gate) | 3 (NEW settled) | +$0.16 | ~+5.33 | 19 | too new to read |
| mmsellA5 (strangle) | 0 | $0 | — | 0 | selectivity gate still hasn't fired |
| **theta4** (fat-tail) | 119 (+5) | +$49.67 | +41.74 (39.21) | 0 | holding; live pilot got first fills |
| **theta4_pt** (twin) | 5 (NEW) | +$2.98 | +59.60 | 0 | brand new, see headline |
| weather_con (all) | 618 (+16) | −$17.44 | −2.82 (−2.63) | 15 | negative batch, worse again |
| weather_concity | 137 (+8) | −$10.14 | −7.40 (−7.51) | 6 | still past gate, RETIRE unresolved |

Shelved/killed (pin15, theta ctrl-3, tfav, weather rest) unchanged, quiet.

**theta fill-model re-read:** calibration source auto-switched to theta's own live orders (see
headline) — 0% coverage everywhere until cells clear the 8-fill trust bar. theta4 paper itself:
n=119, +41.74¢/trade cumulative (was +39.21¢), holding well above its paper gate.

**Gate sweep (step 3b):** theta4 **119/80 CLEARED** (holding; live Stage-1 pilot now has its first
4 fills, nowhere near the ≥80-round-trip gate) · mmsell10 **261/150 CLEARED + LIVE** (holding) ·
mmsell6/mmsell11 clear on blended paper, still MIRAGE under fill model · mmsell9 80/100 (80%) ·
mmsell7 117/150 (78%) · mmsell8 68/100 (68%) · weather_concity **137/120** (past gate, RETIRE
verdict from #75 still unrecorded, 3rd run reinforcing it) · FREEZE settled grain+soft **8/100**
(up 1 from 7 — grain 0→1, soft unchanged at 7, still not fired, 29 runs) · mmsellA1-5 all still
well pre-gate (n≥100/82).

**Data (last-24h rows / latest, ~01:20 AM UTC / 8:20 PM CDT run):** crypto_spot 2,878 (2 products,
8:20 PM ✓) · crypto_ladder 62,738 all with model_p (8:20 PM ✓) · weather forecasts 8,555 (8:19 PM
✓) · observations 644 (8:14 PM ✓) · ensembles 1,744 (8:19 PM ✓) · bucket snapshots 14,148 (8:17 PM
✓). All fresh. xgame_matches/tapes still dark (expected — book KILLED, collector-only).

**Research probes (on-demand):** none standing (TFAV/WCPROP/XGAME/PINNED/DECAY families closed).

**Headline (repeated for chat-report lead):** theta4's live pilot got its first 4 fills this run —
100% win, running slightly ahead of its twin, and the fill model auto-upgraded to theta's own
calibration exactly as designed (still 0% coverage, far too early to read). mmsell10's live
footprint keeps growing (now 3 straight runs) with P&L staying flat — a soft watch item, not a
flag yet. The mmsell10_pt PARAM DRIFT anomaly from run #76 remains unresolved. Both weather books
had another negative batch, reinforcing weather_concity's still-unrecorded RETIRE verdict.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[NEW · top actionable — theta4's live pilot has its first fills] n=4 filled, 100% win, twin
   n=5 (1 rejected). Live +21.00¢/ct vs twin +19.87¢/ct — no accounting-gap signal, live slightly
   ahead so far.** Nothing to do — watch it accumulate toward Stage 1's ≥80-filled-round-trip gate.
   `theta_fill_model` has auto-switched to theta's own calibration (0% coverage until cells clear 8
   fills) — track this rising each run as the real replacement for the old borrowed-calibration
   caution.

2. **[mmsell10_pt PARAM DRIFT — unresolved 2nd run running] `live_paper_parity` continues to flag
   that mmsell10's live knobs changed mid-epoch.** Still no fresh twin tag started. A fable session
   should action this — the longer it runs unaddressed, the less trustworthy the parity numbers
   built on the current epoch become.

3. **[mmsell10's live open footprint keeps growing — 3rd straight run] 5→11→14 open positions,
   $7.70→$18.90→$24.44 deployed, while fill economics and P&L stay flat (100% win, ~6.7¢/ct
   steady).** Not a red flag yet — reads as normal accumulation — but worth a settlement-rate
   sanity check if it keeps climbing without more settlements next run.

4. **[weather_concity · RETIRE verdict from #75, still unrecorded, 3rd run reinforcing it] n=137
   (+8), −7.40¢/trade cumulative (was −7.51¢, marginal improvement but still deeply negative),
   still behind weather_con(all)'s −2.82¢/trade (also worse this run).** A fable session should
   record the retire verdict and separately reconsider all-city `weather_con`'s own viability.

5. **[registry drift on the mmsell10 LIVE book — unresolved 3 runs later] `docs/BOOK_REGISTRY.md`
   still lists `mmsell10` as `paper`, and `mmsell10_pt` (56 settled / 27 open) still has no row.**
   mmsell10 has been live with real money since 2026-07-26 — still needs a fable session to fix.

6. **[mmsell4's KILL verdict continues to be contradicted] n=407, +3.11¢/trade (was +2.82¢,
   +2.80¢, +2.63¢ across the last four runs — a clean improving trend), still above mmsell3's
   +2.26¢.** Do not record the old kill.

7. **[mmsell6 / mmsell11 promote question — gate on REALIZABLE, not blended paper] mmsell6 n=539
   +3.27¢, mmsell11 n=475 +4.10¢ on blended paper, both still recorded MIRAGE under the
   live-calibrated fill model in the registry.** Re-run `mmsell fill model` before any promotion.

8. **[mmsell7 · improving trend, 78% to gate] n=117 (+6), +1.91¢/trade cumulative (was +1.67¢).**
   Continuing to track without over-reading a small-n book that has flipped sign before.

9. **[idea-model queue] MMX — premise (extend the mmsell edge into new categories) should be built
   against mmsell10, the promoted/live mechanism. NEST — gate cleared (#74, theta4 n≥80); ready to
   build on the paper gate alone (independent of theta4's live pilot status).**

10. **[FREEZE gate · not fired] Settled grain+soft = 8 of the n≥100 trigger (up 1 from 7 — grain
    0→1, soft unchanged at 7), unchanged trend across 29 runs now.** Standing background check.

11. **[correlated-event risk · standing interpretive note] Run #73's whole-cohort loss traced to
    a single shared ticker (`KXNBATEAMANNOUNCE-...LJAMES23`) and fully washed out by #74.** Keep
    checking for a single shared ticker before reading any cohort-wide batch move as a
    strategy-wide signal.

12. **[path to raise theta4's fill-model coverage without a live pilot — now secondary] The live
    pilot (see #1) is producing theta's own calibration data directly, which is the stronger fix
    the moment coverage clears. `theta_fill_replay.py` (mirroring `mmsell_fill_replay.py` against
    the already-collecting `crypto_ladder_snapshots` table) remains a valid fallback/parallel path
    but is even lower priority now that live data is actually arriving.**

13. **[mmsellA1-A5 anchor set · first settlements this run, still far too early] mmsellA1/A2/A3
    (stop-loss L12/L20/L30) each show 4 settled (+$0.21), mmsellA4 (vol entry gate) shows 3
    (+$0.16); A5 (strangle) still shows 0 rows (selectivity gate hasn't paired yet).** Gates are
    n≥100 (A1-A4) / n≥82 clean pairs (A5) in `docs/BOOK_REGISTRY.md` — track as they accumulate.

*(Changed this run: #1 REWRITTEN — theta4's live pilot has real fills now (n=4, 100% win, live
ahead of twin) and the fill model auto-switched calibration source; reframed from "watch for the
first fill" to "watch it accumulate." #2 — mmsell10_pt PARAM DRIFT restated, now unresolved 2
runs. #3 NEW — mmsell10's growing live open footprint, flagged as a soft watch item after 3
straight runs of growth. #4 — weather_concity restated, 3rd run reinforcing the RETIRE verdict.
#5 — registry drift restated (3 runs unresolved). #6 — mmsell4 restated, 4th consecutive run of
improvement. #7/#8 restated. #9 — NEST unchanged. #10 — FREEZE restated (ticked up 1, not
material). #11 restated unchanged. #12 — reframed as lower-priority now that live data is
actually arriving via the pilot. #13 — the mmsellA anchor set's first settlements, still far too
early to read.)*
