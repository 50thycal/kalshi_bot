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

## Snapshot — 2026-08-04 08:32 PM CDT (run #79)

*(2-day gap since run #78 — a lot moved.)*

**HEADLINE 1: `mmsell10_pt`'s epoch has ENDED with the most severe possible verdict — ACCOUNTING
GAP.** On n=60 matched markets (same ticker, same side, same window, both settled — the load-bearing
statistic), twin paper realized +1.62¢/ct while live realized +2.21¢/ct, a −0.59¢ gap on trades we
*did* get. Per `docs/LIVE_PAPER_TWIN.md`, this "can only be our own accounting — entry price, fee
model, or settlement logic," and the standing instruction is **"stop trusting every paper book's
gate until it's fixed."** The important caveat: this epoch carried an unresolved PARAM DRIFT flag
for 3 straight runs (#76-78) before ending, and the same doc says drift **voids** an epoch's
numbers — so this severe verdict may be a genuine accounting bug, or may be an artifact of the
epoch mixing pre/post-drift populations right before it closed. Either reading argues for the same
next step: investigate the accounting discrepancy directly (don't wait for another twin epoch to
average it away).

**HEADLINE 2: the original `mmsell10` live book appears wound down, superseded by a proper
`mmsell10a`/`mmsell10b` queue-position A/B test.** `live_orders` shows mmsell10's last live order
at 2026-08-03 14:43 UTC (matching mmsell10_pt's epoch end); `mmsell10a` (1 live order) and
`mmsell10b` (3 live orders) started placing real orders 2026-08-04, each with a clean new
param-drift-free twin (`mmsell10a_pt`/`mmsell10b_pt`, both TOO EARLY, n=0-1). This is a materially
better fix than the "fresh twin tag" this loop had been recommending — it's a proper two-arm test
of the exact queue-position lever (`docs/MMSELL_OFFSET_AB.md`: 10a rests at the no-bid control,
10b rests 1¢ better) with a deterministic per-ticker split. `docs/BOOK_REGISTRY.md`'s own text
still calls it "INERT" pending `MMSELL_LIVE_OFFSET_AB_ARMS` — that description is now stale (the
switch is evidently flipped) but not itself an error to flag; just note for a fable session to
update it once confirmed.

**HEADLINE 3 (secondary): `theta4_pt` now ALSO has its own PARAM DRIFT anomaly logged** — same
failure mode as mmsell10_pt, a second live knob change mid-epoch. Recommend the same fix: a fresh
twin tag before trusting theta4's parity numbers further.

**Broad mmsell batch loss, checked and NOT a single correlated event this time.** Almost the whole
classic mmsell cohort (control, 1, 2, 3, 4, 6, 9, 10, 10_pt, 11) had a negative 2-day batch —
unlike run #73's single shared NBA ticker, a drill-down on this window's losing trades shows no
dominant ticker (worst single loser only −$12.92 across 13 books; losses spread across many
distinct MLB total/home-run/spread markets, one soccer game, and BTC ladder cells). Reads as
genuine broad variance across the cohort, not a shared shock — still worth watching if it persists
another run.

**Live P&L (real money — `mmsell10`, ended 2026-08-03; see HEADLINE 2 for the successor):**
| metric | final read | run #78 |
|---|---|---|
| settled live positions | 60 (95.0% win) | 33 (100% win) |
| realized P&L | +$2.65, +4.42¢/ct | +$4.41, +6.68¢/ct |
| fill rate | 67.0% (97 placed, 65 filled, 32 canceled) | 65.8% |
| open footprint | 7 positions, $13.37 deployed | 19 positions, $27.90 |

Win rate and ¢/ct both declined materially this batch (first real slippage from 100%/~6.7¢), and
the 4-run-growing open footprint (flagged #76-78) has normalized (19→7) — the settlement catch-up
happened as expected, no longer a watch item. Legacy `mmsell3` live unchanged: 367 settled, +$1.33,
+0.36¢/ct. `mmsell3_closeout` still inert.

**Live P&L (real money — `mmsell10a` / `mmsell10b`, armed 2026-08-04):** brand new — 1 and 3 live
orders respectively, all filled (100%), 0 settled yet. Far too early for any read; watch both arms
build toward their own n≥150-per-arm gate (`docs/MMSELL_OFFSET_AB.md`).

**Live P&L (real money — `theta4`, Stage 1 pilot):** 19 settled (was 9), 89.5% win (was 88.9%),
+$2.94 total, +5.16¢/ct blended (twin: 22 settled, 90.9% win, +$3.67, +5.56¢/ct blended). Matched
markets (n=19): twin +4.09¢/ct vs live +5.16¢/ct, gap −1.07¢ — no accounting-gap signal so far
(consistent direction with mmsell10's earlier reads: live ahead of twin on matched trades). Still
**TOO EARLY** and nowhere near Stage 1's ≥80-round-trip gate — but now carries its own PARAM DRIFT
flag (HEADLINE 3).

**Trading books (Δ vs run #77 — 2-day gap covers what would have been run #78's normal cadence):**

| book | n (Δ) | P&L |
|---|---|---|
| mmsell (control) | 4,523 (+117) | +$67.79 |
| mmsell1 | 2,988 (+90) | +$60.74 |
| mmsell2 | 1,969 (+57) | +$53.08 |
| mmsell3 (shadow) | 1,338 (+60) | +$25.55 |
| mmsell4 | 486 (+59) | +$9.70 |
| mmsell5 | 203 (+18) | −$1.38 |
| mmsell6 | 607 (+48) | +$14.05 |
| mmsell7 | 136 (+14) | +$2.47 |
| mmsell8 | 75 (+6) | +$2.10 |
| mmsell9 | 92 (+11) | +$3.06 |
| mmsell10 | 314 (+33) | +$9.30 |
| mmsell10_pt | 103 (+33) | +$5.37 |
| mmsell11 | 555 (+60) | +$16.58 |
| mmsellA1 | 35 (+24) | +$1.92 |
| mmsellA2 | 37 (+26) | +$2.02 |
| mmsellA3 | 39 (+27) | +$1.13 |
| mmsellA4 | 42 (+32) | −$1.73 |
| mmsellA5 | 0 (+0) | $0.00 |
| mmsell10a | 0 (new) | $0.00 |
| mmsell10a_pt | 0 (new) | $0.00 |
| mmsell10b | 0 (new) | $0.00 |
| mmsell10b_pt | 0 (new) | $0.00 |
| Tmmsell1 | 0 (new) | $0.00 |
| Tmmsell4 | 0 (new) | $0.00 |
| Tmmsell5 | 0 (new) | $0.00 |
| Tmmsell6 | 0 (new) | $0.00 |
| Wmmsell1 | 0 (new) | $0.00 |
| Wmmsell4 | 0 (new) | $0.00 |
| Wmmsell6 | 0 (new) | $0.00 |
| Wmmsell7 | 0 (new) | $0.00 |
| theta (control) | 560 (+0) | +$0.97 |
| theta1 | 201 (+0) | +$9.69 |
| theta2 | 98 (+0) | −$11.55 |
| theta3 | 134 (+0) | −$11.62 |
| **theta4** | 136 (+11) | +$54.20 |
| theta4_pt | 22 (+11) | +$3.67 |
| weather_con (all) | 677 (+31) | −$20.41 |
| weather_concity | 161 (+13) | −$8.51 |

Shelved/killed (pin15, tfav, weather rest) unchanged, quiet — not tabulated.

**New book families this run, reconciled against the registry — NOT untracked:**
- `mmsell10a`/`mmsell10b` (+ their `_pt` twins) — the queue-position A/B, see HEADLINE 2.
  `docs/MMSELL_OFFSET_AB.md`, gate n≥150/arm.
- `Wmmsell1`-`Wmmsell8` — WIDE-band market-TYPE census books (`docs/MMSELL_TYPE_BOOKS.md`), control
  is plain `mmsell`, gate n≥150 vs control +1.0¢ AND realizable >0. Only W1/W4/W6/W7 show activity
  so far (0 settled, 2-9 open each); W2/W3/W5/W8 not yet seen.
- `Tmmsell1`-`Tmmsell6` — TIGHT-band (mmsell10 regime) market-TYPE census books, same doc, control
  is `mmsell10`, gate n≥100 vs control +1.0¢ AND realizable >0. Only T1/T4/T5/T6 show activity (0
  settled, 2-3 open each); T2/T3 not yet seen.
- All fully specified with pre-registered gates in `docs/BOOK_REGISTRY.md` — no action needed,
  just tracking from their first appearance per the loop's per-family convention.

**mmsellA3/A4 update:** A3 flipped back positive (+$1.13, was −$0.35) on a strong batch; A4 deepened
further negative (−$1.73, was −$0.46). A1/A2 both had strong positive batches too. Still all well
under the n≥100 gate.

**theta fill-model re-read:** still theta's own calibration, still 0% coverage (largest cell now
has 4 of the needed 8 fills — closer, not there). theta4 paper itself: n=136, holding well above
its paper gate.

**Gate sweep (step 3b):** theta4 **136/80 CLEARED** (holding; live pilot at 19-22 fills, nowhere
near ≥80) · mmsell10 **314/150 CLEARED**, live epoch ENDED (see headlines) · mmsell6/mmsell11 clear
on blended paper, still MIRAGE under fill model · mmsell9 92/100 (92%) · mmsell7 136/150 (91%) ·
mmsell8 75/100 (75%) · weather_concity **161/120** (past gate, RETIRE verdict from #75 still
unrecorded, 5th run reinforcing it) · FREEZE settled grain+soft **9/100** (up 1; **open grain
jumped 0→231** — a leading-indicator move worth watching, though the gate itself only counts
settled) · mmsellA1-5 all still well pre-gate · new families (10a/10b/T*/W*) all pre-gate, most
still at 0 settled.

**Data (last-24h rows / latest, ~01:26 AM UTC / 8:26 PM CDT run):** crypto_spot 2,870 (2 products,
8:22 PM ✓) · crypto_ladder 62,160 all with model_p (8:22 PM ✓) · weather forecasts 11,417 (8:26 PM
✓) · observations 660 (8:25 PM ✓) · ensembles 1,720 (8:22 PM ✓) · bucket snapshots 14,112 (8:25 PM
✓). All fresh. xgame_matches/tapes still dark (expected — book KILLED, collector-only).

**Research probes (on-demand):** none standing (TFAV/WCPROP/XGAME/PINNED/DECAY families closed).

**Headline (repeated for chat-report lead):** mmsell10_pt's epoch ended with an ACCOUNTING GAP
verdict — the most severe read the twin harness produces — though it's tangled with the epoch's
own unresolved param drift, so the root cause needs direct investigation, not another epoch-average.
Separately, mmsell10's live real-money book appears to have wound down in favor of a proper
mmsell10a/mmsell10b queue-position A/B test, which is now live and building its own clean twins.
theta4_pt picked up its own param-drift flag. A broad-but-not-correlated mmsell batch loss hit
almost the whole cohort over the last 2 days. Several new pre-registered book families
(mmsell10a/b, Tmmsell1-6, Wmmsell1-8) appeared and reconcile cleanly against the registry.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[RESOLVED-BY-EVENTS but escalated to a real finding — mmsell10_pt's epoch ended with an
   ACCOUNTING GAP verdict] n=60 matched markets: twin +1.62¢/ct vs live +2.21¢/ct (−0.59¢ gap on
   trades we actually got).** The 3-run-standing "start a fresh twin tag" recommendation is now
   moot for this specific epoch (it ended on its own when mmsell10 wound down — see #2), but the
   underlying question it was meant to protect against is now live: is this a real simulator
   accounting bug (entry price, fee model, or settlement logic), or an artifact of the epoch's own
   unresolved param drift mixing two populations right before it closed? **A fable session should
   investigate this directly** — per `docs/LIVE_PAPER_TWIN.md`, an accounting gap means "stop
   trusting every paper book's gate until it's fixed," which is a big claim to leave unexamined.

2. **[NEW · mmsell10 live appears wound down, superseded by the mmsell10a/mmsell10b A/B] Original
   mmsell10's last live order was 2026-08-03 14:43 UTC; mmsell10a (1 order) and mmsell10b (3
   orders) started placing live orders 2026-08-04, each with a clean new param-drift-free twin.**
   This is the queue-position lever test (`docs/MMSELL_OFFSET_AB.md`) — a fable session should
   confirm the wind-down was intentional (vs. an outage) and update `docs/BOOK_REGISTRY.md`'s
   mmsell10a/b row, which still says "INERT" pending an arm-flag that now appears to be flipped.

3. **[NEW · theta4_pt now has its own PARAM DRIFT anomaly] Same failure mode as mmsell10_pt —
   theta4's live knobs changed mid-epoch.** Recommend a fresh twin tag (e.g. `theta4_pt2`) before
   this epoch runs long enough to produce its own hard-to-trust verdict, learning from how long
   mmsell10_pt's drift sat unresolved.

4. **[Broad mmsell batch loss over the last 2 days — checked, NOT a single correlated event]
   Control, 1, 2, 3, 4, 6, 9, 10, 10_pt, 11 all had a negative batch.** Drill-down found no
   dominant shared ticker (worst single loser only −$12.92 across 13 books, spread across many
   distinct MLB/BTC/soccer markets) — reads as genuine variance, not a shared shock like run #73's.
   Worth a look next run if the negative trend persists rather than reverting.

5. **[mmsell10's live open footprint — resolved] Was 4 straight runs of growth (5→11→14→19); this
   run it normalized to 7 positions / $13.37 deployed** as settlements caught up, alongside the
   book's wind-down (#2). No longer a watch item.

6. **[mmsellA1-A4 update] A1 n=35 +$1.92, A2 n=37 +$2.02 — both had strong positive batches. A3
   n=39 +$1.13 flipped back positive after a strong batch. A4 n=42 −$1.73 deepened negative.**
   Still all well under the n≥100 gate; A4 is the one variant trending consistently negative so
   far. mmsellA5 (strangle) still shows 0 rows — selectivity gate hasn't paired yet.

7. **[New book families this run — reconciled, not untracked] `mmsell10a`/`mmsell10b` (see #2);
   `Wmmsell1`-`Wmmsell8` (wide-band market-type census, control=`mmsell`, gate n≥150); `Tmmsell1`-
   `Tmmsell6` (tight-band market-type census, control=`mmsell10`, gate n≥100).** All pre-registered
   in `docs/BOOK_REGISTRY.md`/`docs/MMSELL_TYPE_BOOKS.md`/`docs/MMSELL_OFFSET_AB.md`. Only a subset
   of each (W1/W4/W6/W7, T1/T4/T5/T6) show any activity yet (0 settled, single-digit open) — track
   as they accumulate.

8. **[weather_concity · RETIRE verdict from #75, still unrecorded, 5th run reinforcing it] n=161
   (+13), improved this batch (−$8.51, was −$9.16) but still deeply net negative; weather_con(all)
   also worse again (6th straight negative-leaning run, −$20.41).** A fable session should record
   the retire verdict and separately reconsider `weather_con`'s viability.

9. **[registry drift on the mmsell10 LIVE book — largely overtaken by #2] `docs/BOOK_REGISTRY.md`
   still lists `mmsell10` as `paper`.** Since mmsell10 itself appears wound down live, this is now
   folded into #2's registry-update ask rather than a standalone item.

10. **[mmsell4's KILL verdict continues to be contradicted] n=486, +$9.70 — dipped from +$13.18
    on this run's broad batch loss (see #4), but the multi-run improving trend before this batch
    was real.** Do not record the old kill; re-read after the batch settles out.

11. **[mmsell6 / mmsell11 promote question — gate on REALIZABLE, not blended paper] mmsell6 n=607
    +$14.05, mmsell11 n=555 +$16.58 on blended paper (both dipped this batch, see #4), both still
    recorded MIRAGE under the live-calibrated fill model in the registry.** Re-run `mmsell fill
    model` before any promotion.

12. **[mmsell7 · 91% to gate] n=136 (+14), +$2.47 (roughly flat this batch).** Continuing to track.

13. **[idea-model queue] MMX — premise (extend the mmsell edge into new categories) should be built
    against mmsell10 or its A/B successor (see #2). NEST — gate cleared (#74, theta4 n≥80); ready
    to build on the paper gate alone (independent of theta4's live pilot status).**

14. **[FREEZE gate · not fired, but open grain jumped 0→231] Settled grain+soft = 9 of the n≥100
    trigger (still far).** The open-grain jump is a leading indicator worth watching — if a chunk
    of those settle, the settled count could move faster than its historical trickle suggests.

15. **[correlated-event risk · standing interpretive note, reinforced by #4's negative check this
    run] Always check for a single shared ticker (like run #73's) before reading any cohort-wide
    batch move as a strategy-wide signal — this run's batch loss was checked and found to be broad
    variance, not a shared event.**

16. **[path to raise theta4's fill-model coverage without a live pilot — still secondary] The live
    pilot is producing theta's own calibration data directly (largest cell now 4 of 8 needed
    fills). `theta_fill_replay.py` remains a valid fallback but stays lower priority.**

*(Changed this run: #1 — mmsell10_pt's flagged param-drift issue resolved itself when the epoch
ended, but escalated into a real ACCOUNTING GAP finding needing investigation. #2 NEW — mmsell10
live appears wound down, superseded by the mmsell10a/b A/B. #3 NEW — theta4_pt picked up its own
param-drift flag, recommend acting faster than mmsell10_pt's did. #4 NEW — the broad mmsell batch
loss, checked and ruled not-correlated. #5 — mmsell10 footprint item RESOLVED (normalized). #6 —
mmsellA1-4 restated with A3's reversal and A4's deepening. #7 NEW — new book families reconciled.
#8 — weather_concity restated, 5th run reinforcing RETIRE. #9 — registry drift folded into #2. #10
— mmsell4 restated with the batch-loss caveat. #11 — mmsell6/mmsell11 restated. #12 — mmsell7
restated. #13 — NEST/MMX unchanged. #14 — FREEZE restated with the new open-grain trend noted. #15
— correlated-event note restated, reinforced by this run's own check. #16 restated.)*
