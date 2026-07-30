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

---

## Snapshot — 2026-07-30 04:48 PM CDT (run #76)

**HEADLINE: `theta4` went LIVE (Stage 1 pilot) on 2026-07-30 — directly the outcome the
`theta_fill_model` caution existed to gate.** The operator built a fully pre-registered staged plan
(`docs/THETA_LIVE_PLAN.md`, written before any live theta order) that explicitly cites this loop's
2026-07-29 finding as its rationale, then followed the plan's own rollout sequence: sizing knobs
first, then `LIVE_STRATEGIES=theta4` as a separate deliberate step. The twin epoch `theta4_pt`
confirmed open at 2026-07-30 18:03:04 UTC (age 3.7h at read time) — but **zero live orders have
fired yet on either side** (twin_tr=0, live_ord=0), so there is nothing to assess against Stage 1's
gates (≥80 filled round-trips, fill rate ≥50%, no accounting gap) — too early, not a red flag.
Pre-registered stakes are small and explicit: `$3.00`/order, 5-contract cap, 15-position cap
(~$35-55 deployed at capacity), hard kill at −$15 cumulative live P&L.

Second finding: **`mmsell10_pt` now has a logged PARAM DRIFT anomaly** — `live_paper_parity`
reports the live knobs changed mid-epoch, so the twin-vs-live comparison is no longer strictly
one-to-one; the script's own recommendation is to start a fresh twin tag.

Third: a new **mmsell "anchor set" (`mmsellA1`-`mmsellA5`)** appeared this run — built and
registered together (`docs/MMSELL_ANCHOR_SET.md`, `docs/BOOK_REGISTRY.md`), forward-testing three
tail-mitigation mechanics (stop-loss level sweep, volatility entry gate, short strangle) on the
mmsell10 base. **Not untracked** — reconciled cleanly against the registry, each with its own
pre-registered gate. A1-A4 show 13 open positions each and 0 settled (too new to read); A5 shows 0
rows entirely (its both-tails-cheap selectivity gate hasn't found a qualifying pair yet — expected,
not a bug).

**Live P&L (real money — `mmsell10`, epoch started 2026-07-26 21:09 UTC):**
| metric | this run | last run (#75) |
|---|---|---|
| settled live positions | 26 (100% win) | 25 |
| realized P&L | +$3.49, +6.72¢/ct | +$3.35, +6.71¢/ct |
| fill rate | 60.0% (63 placed, 36 filled, 24 canceled) | 54.7% |
| open footprint | **11 positions, $18.90 deployed** | 5 positions, $7.70 |

Open footprint more than doubled this run (5→11 positions, $7.70→$18.90) with fill economics and
P&L essentially flat — reads as normal position accumulation, not a size-limit or config change;
flag only if it keeps compounding without settlements catching up.

Parity verdict: still **TOO EARLY** (twin n=40, live n=26 — live still short of the n≥30 bar).
Matched-market gap steady at −0.49¢ (consistent, no ACCOUNTING GAP). Legacy `mmsell3` live
unchanged: 367 settled, +$1.33, +0.36¢/ct. `mmsell3_closeout` still inert.

**Live P&L (real money — `theta4`, Stage 1 pilot armed 2026-07-30 18:03 UTC):** no fills yet on
either side (twin n=0, live n=0) — too early to report a number. Watching for the first fill.

**Trading books (settled n / realized P&L / ¢-per-trade / open) — PAPER, separate from live:**
| book | n (Δ vs #75) | realized P&L | ¢/trade (was) | open | note |
|---|---|---|---|---|---|
| mmsell10 | 244 (+1) | +$9.39 | +3.85 (3.84) | 42 | live, holding |
| mmsell10_pt (twin) | 40 (+1) | +$4.86 | +12.15 | 35 | PARAM DRIFT logged — see headline |
| mmsell11 | 455 (+2) | +$17.73 | +3.90 (3.88) | 47 | mirage under fill model |
| mmsell6 | 519 (+1) | +$16.26 | +3.13 (3.13) | 47 | mirage under fill model |
| mmsell9 | 71 (+1) | +$3.89 | +5.48 (5.47) | 8 | quiet, still ~70% to gate |
| mmsell4 | 387 (+2) | +$10.92 | +2.82 (2.80) | 47 | KILL verdict still contradicted |
| mmsell8 | 58 (+2) | +$1.62 | +2.79 (2.63) | 10 | improving |
| mmsell2 (paper) | 1,876 (+1) | +$57.16 | +3.05 (3.04) | 44 | steady |
| mmsell1 (paper) | 2,852 (+3) | +$65.22 | +2.29 (2.28) | 56 | steady |
| mmsell3 (paper shadow) | 1,238 (+2) | +$26.70 | +2.16 (2.15) | 47 | steady |
| mmsell5 | 185 (+0) | +$3.07 | +1.66 | 0 | quiet |
| mmsell control (paper) | 4,355 (+5) | +$72.54 | +1.67 (1.65) | 63 | steady |
| mmsell7 | 111 (+2) | +$1.85 | +1.67 (1.56) | 4 | still improving |
| **mmsellA1-A4** (anchor set) | 0 each (NEW) | $0 | — | 13 each | too new to read, see headline |
| mmsellA5 (strangle) | 0 (NEW) | $0 | — | 0 | selectivity gate hasn't fired yet |
| **theta4** (fat-tail) | 114 (+2) | +$44.70 | +39.21 (38.62) | 0 | holding; NOW ALSO LIVE, see headline |
| weather_con (all) | 602 (+16) | −$15.81 | −2.63 (−2.35) | 15 | negative batch, worse |
| weather_concity | 129 (+7) | −$9.69 | −7.51 (−6.85) | 7 | negative batch, worse — reinforces RETIRE |

Shelved/killed (pin15, theta ctrl-3, tfav, weather rest) unchanged, quiet.

**theta fill-model re-read (borrowed calibration, all theta books):** theta4 essentially unchanged
— n=114, +39.21¢ optimistic → **+0.47¢ realizable at 28.1% coverage**. theta/theta1/theta2/theta3
all still under 50% coverage (theta3 at 1.5%); theta2/theta3 still invert paper-negative to
realizable-positive under the borrowed calibration. No change to the standing caution.

**Gate sweep (step 3b):** theta4 **114/80 CLEARED** (holding; now also the subject of a live
Stage-1 pilot, see headline) · mmsell10 **244/150 CLEARED + LIVE** (holding) · mmsell6/mmsell11
clear on blended paper, still MIRAGE under fill model · mmsell9 71/100 (71%) · mmsell7 111/150
(74%) · mmsell8 58/100 (58%) · weather_concity **129/120** (past gate, verdict RETIRE per run #75,
still unrecorded, batch got worse again) · FREEZE settled grain+soft **7/100** (down from 8 — grain
2→0, soft 6→7, net −1, still not fired, 28 runs) · mmsellA1-A5 all pre-80/82/100-gated, too new.

**Data (last-24h rows / latest, ~9:42 PM UTC / 4:42 PM CDT run):** crypto_spot 2,880 (2 products,
4:42 PM ✓) · crypto_ladder 46,800 all with model_p (4:42 PM ✓) · weather forecasts 8,835 (4:42 PM
✓) · observations 649 (4:38 PM ✓) · ensembles 1,728 (4:42 PM ✓) · bucket snapshots 12,618 (4:42 PM
✓). All fresh. xgame_matches/tapes still dark (expected — book KILLED, collector-only).

**Research probes (on-demand):** none standing (TFAV/WCPROP/XGAME/PINNED/DECAY families closed).

**Headline (repeated for chat-report lead):** theta4 went live (Stage 1 pilot) this run — no fills
yet, nothing to assess, but this is the single biggest state change since #75 and the one item to
lead with. mmsell10_pt has a PARAM DRIFT anomaly worth a fresh twin tag. The new mmsellA1-5 anchor
set is registered and traded-in cleanly (no untracked-book flag). Both weather books had a worse
batch, reinforcing weather_concity's still-unrecorded RETIRE verdict from run #75.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[NEW · top actionable — theta4 is now LIVE, Stage 1 pilot, watch don't act] Armed
   2026-07-30 18:03 UTC per the pre-registered `docs/THETA_LIVE_PLAN.md`. Zero fills yet on twin
   or live.** Nothing to do yet — the loop will report the first fill and track progress toward
   Stage 1's gates (≥80 filled round-trips, fill rate ≥50%, matched-market gap not an ACCOUNTING
   GAP) as they accumulate. This supersedes the old "don't read theta4 as live-ready" caution —
   it's no longer a hypothetical, it's now the live test in progress.

2. **[NEW · mmsell10_pt PARAM DRIFT logged] `live_paper_parity` flagged that mmsell10's live
   knobs changed mid-epoch, breaking the twin's one-to-one comparison.** The script's own
   recommendation is to start a fresh twin tag (e.g. `mmsell10_pt2`) rather than keep reading the
   current one as apples-to-apples. A fable session should action this — it directly affects how
   much to trust the current parity numbers.

3. **[weather_concity · RETIRE verdict from #75, still unrecorded, reinforced by a worse batch]
   n=129 (+7), −7.51¢/trade cumulative (was −6.85¢) — worse again, still behind weather_con(all)'s
   −2.63¢/trade (also worse this run).** Both weather books had a negative batch. A fable session
   should record the retire verdict and separately reconsider whether all-city `weather_con` is
   worth continuing given it's still net negative too.

4. **[registry drift on the mmsell10 LIVE book — unresolved 2 runs later] `docs/BOOK_REGISTRY.md`
   still lists `mmsell10` as `paper`, and `mmsell10_pt` (40 settled / 35 open) still has no row.**
   mmsell10 has been live with real money since 2026-07-26 — still needs a fable session to fix.

5. **[mmsell4's KILL verdict continues to be contradicted] n=387, +2.82¢/trade (was +2.80¢, +2.63¢,
   +0.60¢ across the last three runs), still above mmsell3's +2.16¢.** Do not record the old kill.

6. **[mmsell6 / mmsell11 promote question — gate on REALIZABLE, not blended paper] mmsell6 n=519
   +3.13¢, mmsell11 n=455 +3.90¢ on blended paper, both still recorded MIRAGE under the
   live-calibrated fill model in the registry.** Re-run `mmsell fill model` before any promotion.

7. **[mmsell7 · improving trend, 74% to gate] n=111 (+2), +1.67¢/trade cumulative (was +1.56¢).**
   Continuing to track without over-reading a small-n book that has flipped sign before.

8. **[idea-model queue] MMX — premise (extend the mmsell edge into new categories) should be built
   against mmsell10, the promoted/live mechanism. NEST — gate cleared (#74, theta4 n≥80); ready to
   build on the paper gate alone (independent of theta4's live pilot status).**

9. **[FREEZE gate · not fired] Settled grain+soft = 7 of the n≥100 trigger (down 1 from 8 — grain
   2→0, soft 6→7), unchanged trend across 28 runs now.** Standing background check.

10. **[correlated-event risk · standing interpretive note] Run #73's whole-cohort loss traced to
    a single shared ticker (`KXNBATEAMANNOUNCE-...LJAMES23`) and fully washed out by #74.** Keep
    checking for a single shared ticker before reading any cohort-wide batch move as a
    strategy-wide signal.

11. **[path to raise theta4's fill-model coverage without a live pilot — partly overtaken by
    events] Scoped 2026-07-29: `crypto_ladder_snapshots` exists already (unlike mmsell, no new
    capture needed) but is thin for theta4 (26% ticker coverage, ~9 rows/ticker). A real
    per-ticker replay (`theta_fill_replay.py`, mirroring `mmsell_fill_replay.py`) remains buildable
    and not yet built — but theta4's live pilot (see #1) will produce its own calibration data
    directly, which is the stronger fix. Keep this as a fallback/parallel path, not the priority,
    now that live data is arriving.

12. **[mmsellA1-A5 anchor set · new, too early to read] mmsellA1-4 show 13 open positions each,
    0 settled; A5 (strangle) shows 0 rows at all (selectivity gate hasn't paired yet).** Each has
    its own pre-registered gate in `docs/BOOK_REGISTRY.md`/`docs/MMSELL_ANCHOR_SET.md` (n≥100 for
    A1-A4, n≥82 clean pairs for A5). Track as they accumulate; nothing to report yet.

*(Changed this run: #1 REWRITTEN — theta4's fill-model caution is superseded by the live pilot now
actually running; reframed from "don't treat as live-ready" to "watch the live test in progress."
#2 NEW — mmsell10_pt PARAM DRIFT anomaly. #3 — weather_concity restated, reinforced by a worse
batch on both weather books. #4 — registry drift restated (2 runs unresolved). #5 — mmsell4
restated, third consecutive run of improvement. #6/#7 restated. #8 — NEST clarified as
independent of theta4's live-pilot status. #9 — FREEZE restated (settled count dipped slightly,
not material). #10 restated unchanged. #11 — reframed as secondary now that theta4's live pilot is
the stronger path to real calibration data. #12 NEW — the mmsellA anchor set, reconciled against
the registry, tracked from its first run.)*
