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

## Snapshot — 2026-08-02 09:57 AM CDT (run #78)

**HEADLINE: `mmsell10_pt`'s parity verdict flipped from TOO EARLY to EXECUTION GAP — but the epoch
is already known-compromised by the still-unresolved PARAM DRIFT anomaly, so read this with real
skepticism, not as a clean finding.** Both sides finally cleared n≥30 (twin 70, live 33).
Matched-market accounting is still clean (gap −0.49¢, unchanged from every prior run — NOT an
accounting gap). But the blended books diverge: twin (all 70 trades) averages +4.71¢/ct at 98.6%
win, live (33 settled) averages +6.68¢/ct at 100% win — a −1.98¢/ct gap the script's own logic
labels EXECUTION GAP (matched markets agree, but the full books don't). The catch: `docs/
LIVE_PAPER_TWIN.md`'s own interpretation traps say param drift **voids the epoch** — "the old
epoch's numbers describe a configuration that no longer exists" — and this exact epoch has an
unresolved PARAM DRIFT anomaly logged since run #76. The verdict may be real, or may be an artifact
of mixing pre/post-drift populations. **This elevates the "start a fresh twin tag" suggestion from
a nice-to-have to the load-bearing fix** — no clean verdict is possible until it happens.

Second: theta4's live pilot has grown to 11 fills (twin) / 9 (live), still TOO EARLY, but its first
loser showed up (twin win% dropped to 90.9% from 100%) — small-n noise, not a flag yet.

Third: `mmsellA3` and `mmsellA4` both went cumulative-negative for the first time this run (still
tiny n, tracked in the table below, not yet gate-relevant).

**Live P&L (real money — `mmsell10`, epoch started 2026-07-26 21:09 UTC):**
| metric | this run | last run (#77) |
|---|---|---|
| settled live positions | 33 (100% win) | 29 |
| realized P&L | +$4.41, +6.68¢/ct | +$3.91, +6.74¢/ct |
| fill rate | 65.8% (77 placed, 48 filled, 25 canceled) | 62.7% |
| open footprint | **19 positions, $27.90 deployed** | 14 positions, $24.44 |

Open footprint has now grown for **4 consecutive runs** (5→11→14→19, deployed capital
$7.70→$18.90→$24.44→$27.90) with fill economics and win rate still stable — remains a soft watch
item pending a settlement-rate check.

Parity verdict: **EXECUTION GAP** (see headline — read with the param-drift caveat). Legacy
`mmsell3` live unchanged: 367 settled, +$1.33, +0.36¢/ct. `mmsell3_closeout` still inert.

**Live P&L (real money — `theta4`, Stage 1 pilot):** 9 settled (was 4), 88.9% win (was 100%),
+$1.65 total, +6.11¢/ct blended (twin: 11 settled, 90.9% win, +$2.35, +7.12¢/ct blended). Matched
markets (n=9): twin +4.96¢/ct vs live +6.11¢/ct, gap −1.15¢ — no accounting-gap signal, consistent
direction with mmsell10's read (live still running ahead of twin on matched trades). Still **TOO
EARLY** (need ≥30 each side) and nowhere near Stage 1's ≥80-round-trip gate.

**Trading books (Δ vs run #77):**

| book | n (Δ) | P&L |
|---|---|---|
| mmsell (control) | 4,406 (+23) | +$79.00 |
| mmsell1 | 2,898 (+21) | +$68.93 |
| mmsell2 | 1,912 (+14) | +$60.69 |
| mmsell3 (shadow) | 1,278 (+20) | +$28.96 |
| mmsell4 | 427 (+20) | +$13.18 |
| mmsell5 | 185 (+0) | +$3.07 |
| mmsell6 | 559 (+20) | +$17.88 |
| mmsell7 | 122 (+5) | +$2.50 |
| mmsell8 | 69 (+1) | +$2.62 |
| mmsell9 | 81 (+1) | +$4.46 |
| mmsell10 | 281 (+20) | +$10.50 |
| mmsell10_pt | 70 (+14) | +$6.59 |
| mmsell11 | 495 (+20) | +$19.99 |
| mmsellA1 | 11 (+7) | +$0.61 |
| mmsellA2 | 11 (+7) | +$0.59 |
| mmsellA3 | 12 (+8) | −$0.35 |
| mmsellA4 | 10 (+7) | −$0.46 |
| mmsellA5 | 0 (+0) | $0.00 |
| theta (control) | 560 (+0) | +$0.97 |
| theta1 | 201 (+0) | +$9.69 |
| theta2 | 98 (+0) | −$11.55 |
| theta3 | 134 (+0) | −$11.62 |
| **theta4** | 125 (+6) | +$52.38 |
| theta4_pt | 11 (+6) | +$2.35 |
| weather_con (all) | 646 (+28) | −$18.90 |
| weather_concity | 148 (+11) | −$9.16 |

Shelved/killed (pin15, tfav, weather rest) unchanged, quiet — not tabulated.

Notable moves: mmsell10_pt's P&L (+$6.93→+$6.59) actually *dropped* despite +14 new settled trades
— the new batch was net negative, the first sign of the twin's blended win rate slipping (98.6%,
was ~100%), consistent with the EXECUTION GAP headline. theta4_pt similarly dropped slightly
(+$2.98→+$2.35) on its first loser. weather_concity improved this batch (−$10.14→−$9.16) but
remains net negative and past its gate. weather_con(all) keeps getting worse (5th straight
negative-leaning run).

**theta fill-model re-read:** still theta's own calibration, still 0% coverage everywhere (largest
cell has 2 fills, need 8 to trust) — expected at this pilot size, will strengthen as fills
accumulate. theta4 paper itself: n=125, holding well above its paper gate.

**Gate sweep (step 3b):** theta4 **125/80 CLEARED** (holding; live pilot at 9-11 fills, nowhere
near ≥80) · mmsell10 **281/150 CLEARED + LIVE** (holding, parity now EXECUTION GAP pending
twin-tag fix) · mmsell6/mmsell11 clear on blended paper, still MIRAGE under fill model · mmsell9
81/100 (81%) · mmsell7 122/150 (81%) · mmsell8 69/100 (69%) · weather_concity **148/120** (past
gate, RETIRE verdict from #75 still unrecorded, 4th run reinforcing it) · FREEZE settled
grain+soft **8/100** (unchanged, still not fired, 30 runs) · mmsellA1-5 all still well pre-gate
(n≥100/82), A3/A4 now cumulative-negative.

**Data (last-24h rows / latest, ~02:51 PM UTC / 9:51 AM CDT run):** crypto_spot 2,878 (2 products,
9:51 AM ✓) · crypto_ladder 61,673 all with model_p (9:51 AM ✓) · weather forecasts 10,248 (9:51 AM
✓) · observations 661 (9:51 AM ✓) · ensembles 1,712 (9:20 AM ✓) · bucket snapshots 14,106 (9:51 AM
✓). All fresh. xgame_matches/tapes still dark (expected — book KILLED, collector-only).

**Research probes (on-demand):** none standing (TFAV/WCPROP/XGAME/PINNED/DECAY families closed).

**Headline (repeated for chat-report lead):** mmsell10_pt's parity verdict moved from TOO EARLY to
EXECUTION GAP now that both sides cleared n≥30 — but the epoch has an unresolved PARAM DRIFT
anomaly that, per the twin harness's own documented interpretation traps, voids a clean read. The
fresh-twin-tag fix (flagged since run #76) is no longer just good hygiene — it's now required
before this verdict can be trusted. theta4's live pilot keeps growing (9-11 fills) with its first
loser. mmsellA3/A4 turned cumulative-negative for the first time. Both changes are small-n and not
yet actionable, but worth watching.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[REWRITTEN · now the load-bearing fix, not a nice-to-have — mmsell10_pt needs a fresh twin tag
   before its EXECUTION GAP verdict can be trusted] Parity flipped from TOO EARLY to EXECUTION GAP
   this run (both sides cleared n≥30), but the epoch has carried an unresolved PARAM DRIFT anomaly
   since run #76.** `docs/LIVE_PAPER_TWIN.md`'s own interpretation traps say param drift **voids
   the epoch** — the fix is a new twin tag (e.g. `mmsell10_pt2`), not a quiet re-read. Until that
   happens, no clean verdict is possible on whether mmsell10's live execution is genuinely
   underperforming its twin or whether the −1.98¢/ct gap is a drift artifact. This has been flagged
   for 3 runs; it's now blocking a real finding, not just data hygiene.

2. **[theta4's live pilot keeps growing, first loser appeared] n=9 live-settled (was 4), 11 twin
   (was 5), win% dipped to 88.9%/90.9% from 100% on both sides — small-n noise, not a flag.**
   Matched-market gap −1.15¢ (no accounting-gap signal, same direction as mmsell10's read: live
   running ahead of twin so far). Nothing to do — keep watching toward Stage 1's ≥80-round-trip
   gate. `theta_fill_model` stays at 0% coverage (largest cell has 2 of the needed 8 fills).

3. **[mmsell10's live open footprint — now 4 consecutive runs of growth] 5→11→14→19 open
   positions, $7.70→$18.90→$24.44→$27.90 deployed, fill economics and win rate still stable.**
   Escalating slightly from a soft watch to worth a real settlement-rate check next run if the
   open count keeps climbing without settlements catching up.

4. **[NEW · mmsellA3/A4 turned cumulative-negative for the first time] mmsellA3 (stop-loss L30):
   n=12, −$0.35. mmsellA4 (vol entry gate): n=10, −$0.46.** Still far too small (n≥100 gate) to
   mean anything — flagging only because it's a first-time sign change worth tracking alongside
   mmsellA1/A2, which remain modestly positive (+$0.61/+$0.59 at similar n).

5. **[weather_concity · RETIRE verdict from #75, still unrecorded, 4th run reinforcing it] n=148
   (+11), improved this batch (−$9.16, was −$10.14) but still deeply net negative, still behind
   weather_con(all) (also worse again this run, 5th straight negative-leaning run).** A fable
   session should record the retire verdict and separately reconsider `weather_con`'s viability.

6. **[registry drift on the mmsell10 LIVE book — unresolved 4 runs later] `docs/BOOK_REGISTRY.md`
   still lists `mmsell10` as `paper`, and `mmsell10_pt` (70 settled / 19 open) still has no row.**
   mmsell10 has been live with real money since 2026-07-26 — still needs a fable session to fix.

7. **[mmsell4's KILL verdict continues to be contradicted] n=427, +$13.18 (was +$12.65) — five
   consecutive improving runs now.** Do not record the old kill.

8. **[mmsell6 / mmsell11 promote question — gate on REALIZABLE, not blended paper] mmsell6 n=559
   +$17.88, mmsell11 n=495 +$19.99 on blended paper, both still recorded MIRAGE under the
   live-calibrated fill model in the registry.** Re-run `mmsell fill model` before any promotion.

9. **[mmsell7 · improving trend, 81% to gate] n=122 (+5), +$2.50 (was +$2.24).** Continuing to
   track without over-reading a small-n book that has flipped sign before.

10. **[idea-model queue] MMX — premise (extend the mmsell edge into new categories) should be built
    against mmsell10, the promoted/live mechanism. NEST — gate cleared (#74, theta4 n≥80); ready to
    build on the paper gate alone (independent of theta4's live pilot status).**

11. **[FREEZE gate · not fired] Settled grain+soft = 8 of the n≥100 trigger, unchanged this run,
    30 runs now.** Standing background check.

12. **[correlated-event risk · standing interpretive note] Run #73's whole-cohort loss traced to
    a single shared ticker (`KXNBATEAMANNOUNCE-...LJAMES23`) and fully washed out by #74.** Keep
    checking for a single shared ticker before reading any cohort-wide batch move as a
    strategy-wide signal.

13. **[path to raise theta4's fill-model coverage without a live pilot — still secondary] The live
    pilot (see #2) is producing theta's own calibration data directly. `theta_fill_replay.py`
    (mirroring `mmsell_fill_replay.py` against `crypto_ladder_snapshots`) remains a valid
    fallback/parallel path but stays lower priority while live data is accumulating.**

14. **[mmsellA1/A2 · still modestly positive] n=11 each, +$0.61/+$0.59.** See #4 for A3/A4's
    sign flip. mmsellA5 (strangle) still shows 0 rows — selectivity gate hasn't paired yet.

*(Changed this run: #1 ESCALATED — mmsell10_pt's parity verdict changed (TOO EARLY → EXECUTION
GAP) but the fresh-twin-tag fix is now the load-bearing blocker for trusting it, not routine
hygiene; moved to top slot. #2 — theta4 live pilot restated, first loser noted as noise. #3 —
mmsell10 footprint escalated slightly after a 4th straight growth run. #4 NEW — mmsellA3/A4 sign
flip. #5 — weather_concity restated, 4th run reinforcing RETIRE. #6 — registry drift restated (4
runs unresolved). #7 — mmsell4 restated, 5th consecutive improving run. #8/#9 restated. #10 — NEST
unchanged. #11 — FREEZE restated (unchanged this run). #12 restated unchanged. #13 restated. #14
NEW — split out from the old combined mmsellA item now that A3/A4 diverged from A1/A2.)*
