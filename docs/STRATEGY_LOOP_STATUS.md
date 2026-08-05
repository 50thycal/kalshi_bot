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

## Snapshot — 2026-08-05 09:25 AM CDT (run #80)

**HEADLINE: theta4's live money has gone net negative for the first time.** Real-money read: n=23
settled, win% dropped to 78.3% (was 89.5%), −$4.14 total, **−6.00¢/ct** — a swing of roughly −$7 on
just 4 new trades since run #79 (+$2.94 → −$4.14). Matched-market gap is −1.07¢ (twin −7.07¢/ct vs
live −6.00¢/ct) — **not** an accounting-gap signal; both sides are genuinely losing on this batch,
consistent direction with the paper twin's own move (theta4_pt: +$3.67 → **−$3.54**, and theta4
itself dropped from +$54.20 to +$41.91, a −$12.29 hit on the same 4 trades). This is well within
`docs/THETA_LIVE_PLAN.md`'s hard-kill threshold (−$15 cumulative live P&L) — not a stop-trading
event — but it's the first real losing stretch since arming and worth watching closely, especially
since `theta_fill_model`'s own-calibration cells are starting to show some ugly outliers (two thin
cells at −435¢/−444¢) even though none are trusted yet (still 0% coverage, largest cell has 4/8
needed fills).

Everything else was quiet — this was a short (~13h) gap since run #79 and the **entire mmsell
cohort had zero new settlements**: mmsell (control), 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10_pt, 11, and
all of mmsellA1-5 are byte-identical to run #79's numbers. `mmsell10a`/`mmsell10b` are still
building (still 0 settled, 1 and 4 live orders total) and the new `Tmmsell*`/`Wmmsell*` census
books remain at 0 settled too.

**Background (already fixed, no action needed):** two live-executor bugfixes landed on the default
branch since run #79 — `1309246` ("theta live: retry the entry paper's phantom position was
locking live out of...") mirrors the same fix mmsell got a week earlier, applied to theta's live
mirror; `99f6c49` ("mmsell: stop the start-up sweep abandoning the market-type books") likely
explains why the new Tmmsell/Wmmsell books have taken a few days to show any activity.

**Live P&L (real money — `theta4`, Stage 1 pilot):**
| metric | this run | run #79 |
|---|---|---|
| settled live positions | 23 (78.3% win) | 19 (89.5% win) |
| realized P&L | **−$4.14, −6.00¢/ct** | +$2.94, +5.16¢/ct |
| fill rate | 92.0% (26 placed, 23 filled, 2 canceled) | — |

Twin (26 settled, 80.8% win, −$3.54, −4.54¢/ct blended) moved the same direction. Still **TOO
EARLY** for a parity verdict (need ≥30 each side) and nowhere near Stage 1's ≥80-round-trip gate —
but this is the first batch where "watch for the hard-kill threshold" becomes a real, not
theoretical, thing to track.

**Live P&L (real money — `mmsell10`, wound down 2026-08-03 — unchanged):** 60 settled, 95.0% win,
+$2.65, +4.42¢/ct, 7 open positions / $13.37 deployed. No new activity since run #79 (confirms the
wind-down). Legacy `mmsell3` live unchanged: 367 settled, +$1.33, +0.36¢/ct.

**Live P&L (real money — `mmsell10a`/`mmsell10b`):** still building — 1 and 4 live orders total
(all filled, 100%), 0 settled either side. Still far too early.

**Trading books (Δ vs run #79):**

| book | n (Δ) | P&L |
|---|---|---|
| mmsell (control) | 4,523 (+0) | +$67.79 |
| mmsell1 | 2,988 (+0) | +$60.74 |
| mmsell2 | 1,969 (+0) | +$53.08 |
| mmsell3 (shadow) | 1,338 (+0) | +$25.55 |
| mmsell4 | 486 (+0) | +$9.70 |
| mmsell5 | 203 (+0) | −$1.38 |
| mmsell6 | 607 (+0) | +$14.05 |
| mmsell7 | 136 (+0) | +$2.47 |
| mmsell8 | 75 (+0) | +$2.10 |
| mmsell9 | 92 (+0) | +$3.06 |
| mmsell10 | 314 (+0) | +$9.30 |
| mmsell10_pt | 103 (+0) | +$5.37 |
| mmsell11 | 555 (+0) | +$16.58 |
| mmsellA1 | 35 (+0) | +$1.92 |
| mmsellA2 | 37 (+0) | +$2.02 |
| mmsellA3 | 39 (+0) | +$1.13 |
| mmsellA4 | 42 (+0) | −$1.73 |
| mmsellA5 | 0 (+0) | $0.00 |
| mmsell10a / mmsell10a_pt | 0 / 0 (+0) | $0.00 |
| mmsell10b / mmsell10b_pt | 0 / 0 (+0) | $0.00 |
| Tmmsell1/4/5/6 | 0 each (+0) | $0.00 |
| Wmmsell1/4/6/7 | 0 each (+0) | $0.00 |
| theta (control) | 560 (+0) | +$0.97 |
| theta1 | 201 (+0) | +$9.69 |
| theta2 | 98 (+0) | −$11.55 |
| theta3 | 134 (+0) | −$11.62 |
| **theta4** | 140 (+4) | **+$41.91** |
| **theta4_pt** | 26 (+4) | **−$3.54** |
| weather_con (all) | 690 (+13) | −$22.68 |
| weather_concity | 168 (+7) | −$9.57 |

Shelved/killed (pin15, tfav, weather rest) unchanged, quiet — not tabulated.

**theta fill-model re-read:** still theta's own calibration, still 0% coverage — largest cells
(yes 15¢/16¢) have 4 of the needed 8 fills each. Two thin cells (12¢, 14¢) show very large negative
realizable numbers (−435¢/−444¢ on single fills) consistent with this run's bad batch — not
trustworthy yet, but worth watching as more fills land in those cells.

**Gate sweep (step 3b):** theta4 **140/80 CLEARED** (holding on paper cumulative despite this
run's hit; live Stage-1 pilot now net negative, 23 fills, nowhere near ≥80) · mmsell10 **314/150**
(live epoch ended, unchanged) · mmsell6/mmsell11 clear on blended paper, still MIRAGE under fill
model · mmsell9 92/100 · mmsell7 136/150 (91%) · mmsell8 75/100 · weather_concity **168/120** (past
gate, RETIRE verdict from #75 still unrecorded, 6th run reinforcing it) · FREEZE settled grain+soft
**9/100** (unchanged; open grain still 231, unmoved) · mmsellA1-5 and all new families (10a/10b,
T*/W*) still well pre-gate, most at 0 settled.

**Data (last-24h rows / latest, ~02:20 PM UTC / 9:20 AM CDT run):** crypto_spot 2,878 (2 products,
9:19 AM ✓) · crypto_ladder 65,280 all with model_p (9:20 AM ✓) · weather forecasts 10,887 (9:19 AM
✓) · observations 654 (9:14 AM ✓) · ensembles 1,736 (9:18 AM ✓) · bucket snapshots 14,820 (9:18 AM
✓). All fresh. xgame_matches/tapes still dark (expected — book KILLED, collector-only).

**Research probes (on-demand):** none standing (TFAV/WCPROP/XGAME/PINNED/DECAY families closed).

**Headline (repeated for chat-report lead):** theta4's live money went net negative for the first
time this run (−$4.14, 78.3% win, down from +$2.94/89.5%) — well short of the −$15 hard-kill line,
but the first real losing stretch since arming, moving the same direction as its paper twin (which
also flipped negative) and the underlying theta4 paper book (down $12.29 on the same 4 trades). No
accounting-gap signal — this reads as genuine variance, not a simulator bug. Everything else was
quiet: a short gap since #79 meant zero new settlements across the entire mmsell cohort.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[NEW · top actionable — theta4's live money went net negative for the first time] n=23
   settled, win% 78.3% (was 89.5%), −$4.14 total, −6.00¢/ct (was +$2.94, +5.16¢/ct) — a ~$7 swing
   on 4 new trades, moving the same direction as its paper twin (also flipped negative) and the
   theta4 paper book itself (−$12.29 on the same trades).** No accounting-gap signal (matched
   markets: twin −7.07¢ vs live −6.00¢, live actually slightly better) — this reads as genuine
   variance, not a simulator bug. Well within the −$15 hard-kill threshold from `docs/
   THETA_LIVE_PLAN.md`, so nothing to act on yet, but this is the first real losing stretch since
   arming — worth a close watch next run, especially since two theta_fill_model cells just showed
   large negative single-fill outliers (−435¢/−444¢, still unconfirmed at n=1 each).

2. **[mmsell10_pt's epoch ended with an ACCOUNTING GAP verdict — still needs investigation]
   n=60 matched markets: twin +1.62¢/ct vs live +2.21¢/ct (−0.59¢ gap on trades we actually got).**
   Unchanged since #79 (no new mmsell10 settlements this run). The underlying question remains
   open: real simulator accounting bug, or an artifact of the epoch's own unresolved param drift
   mixing two populations right before it closed? A fable session should investigate directly —
   per `docs/LIVE_PAPER_TWIN.md`, an accounting gap means "stop trusting every paper book's gate
   until it's fixed."

3. **[mmsell10 live appears wound down, superseded by the mmsell10a/mmsell10b A/B — still
   unconfirmed] mmsell10a (1 live order) and mmsell10b (4 live orders) are still building, 0
   settled either side.** A fable session should confirm the mmsell10 wind-down was intentional
   and update `docs/BOOK_REGISTRY.md`'s mmsell10a/b row (still says "INERT," now confirmed stale
   for the 2nd run running).

4. **[theta4_pt's PARAM DRIFT anomaly is still unresolved] Same failure mode as mmsell10_pt —
   theta4's live knobs changed mid-epoch, still no fresh twin tag started.** This is now more
   urgent given #1 — a losing-money epoch is exactly when a clean read matters most. Recommend
   `theta4_pt2` before the epoch runs long enough to end the same murky way mmsell10_pt's did.

5. **[Broad mmsell batch loss from run #79 — no new data to update the read] Checked last run and
   found not a single correlated event (no dominant ticker).** No new settlements happened this
   run (short ~13h gap) to say whether it persists or reverts — re-check next run once volume
   picks back up.

6. **[mmsellA1-A4 — unchanged this run, no new settlements] A1 +$1.92, A2 +$2.02, A3 +$1.13
   (positive), A4 −$1.73 (still the one trending negative).** All well under the n≥100 gate.
   mmsellA5 (strangle) still 0 rows.

7. **[New book families — still reconciled, still mostly at 0 settled] `mmsell10a`/`mmsell10b`
   (see #3); `Wmmsell1`-`Wmmsell8` (control=`mmsell`, gate n≥150); `Tmmsell1`-`Tmmsell6`
   (control=`mmsell10`, gate n≥100).** All pre-registered in `docs/BOOK_REGISTRY.md`. A recent
   bugfix (`99f6c49`, "stop the start-up sweep abandoning the market-type books") likely explains
   the slow start — watch for these to pick up activity next run.

8. **[weather_concity · RETIRE verdict from #75, still unrecorded, 6th run reinforcing it] n=168
   (+7), worse again (−$9.57, was −$8.51); weather_con(all) also worse again (7th straight
   negative-leaning run, −$22.68).** A fable session should record the retire verdict.

9. **[registry drift — folded into #3] `docs/BOOK_REGISTRY.md` still lists `mmsell10` as `paper`
   and theta4 as "inert — awaiting arming," both now confirmed stale given both books are live
   with real losses/gains.** Worth a single registry pass covering mmsell10, mmsell10a/b, and
   theta4 together rather than three separate fixes.

10. **[mmsell4's KILL verdict continues to be contradicted] n=486, +$9.70 — unchanged this run
    (no new settlements), still above mmsell3's level.** Do not record the old kill.

11. **[mmsell6 / mmsell11 promote question — gate on REALIZABLE, not blended paper] mmsell6
    +$14.05, mmsell11 +$16.58 on blended paper (unchanged this run), both still recorded MIRAGE
    under the live-calibrated fill model in the registry.** Re-run `mmsell fill model` before any
    promotion.

12. **[mmsell7 · 91% to gate] n=136 (unchanged this run), +$2.47.** Continuing to track.

13. **[idea-model queue] MMX — premise (extend the mmsell edge into new categories) should be built
    against mmsell10 or its A/B successor (see #3). NEST — gate cleared (#74, theta4 n≥80); ready
    to build on the paper gate alone (independent of theta4's live pilot's current dip).**

14. **[FREEZE gate · not fired, open grain unchanged at 231] Settled grain+soft = 9 of the n≥100
    trigger (still far).** Still watching whether the open-grain backlog starts settling faster.

15. **[correlated-event risk · standing interpretive note] Always check for a single shared ticker
    before reading any cohort-wide batch move as a strategy-wide signal — run #79's batch loss was
    checked and found to be broad variance, not a shared event.**

16. **[path to raise theta4's fill-model coverage without a live pilot — still secondary, but two
    new cells hint at trouble] Largest cells now have 4/8 needed fills; two thin cells (12¢/14¢)
    show large negative outliers (−435¢/−444¢) consistent with #1's bad batch, not yet trusted.**
    `theta_fill_replay.py` remains a valid fallback but stays lower priority while live data
    accumulates.

*(Changed this run: #1 NEW — theta4's live money went net negative for the first time, promoted to
top slot. #2 — mmsell10_pt's ACCOUNTING GAP restated, unchanged (no new mmsell10 settlements). #3
— mmsell10 wind-down / A/B restated, still unconfirmed, registry still stale a 2nd run. #4 —
theta4_pt param drift escalated given #1's losing batch. #5 — broad mmsell batch loss restated,
no new data to update it. #6/#7 restated with no new settlements. #8 — weather_concity restated,
6th run reinforcing RETIRE. #9 — registry drift folded together (mmsell10 + mmsell10a/b + theta4,
all now confirmed stale). #10 — mmsell4 restated, unchanged (no new settlements). #11 —
mmsell6/mmsell11 restated, unchanged. #12 — mmsell7 restated, unchanged. #13 — NEST/MMX unchanged.
#14 — FREEZE restated, open grain unchanged at 231. #15 — correlated-event note restated unchanged.
#16 — reframed with the two new fill-model outlier cells noted, still not trusted.)*
