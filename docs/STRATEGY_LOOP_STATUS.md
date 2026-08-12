# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book. **As of run #74 the LIVE book
is `mmsell10` (live since 2026-07-26 21:09 UTC, with paper twin `mmsell10_pt`) — not mmsell3.**
mmsell3 LIVE was wound down 2026-07-19 (`docs/MMSELL_LIVE_POSTMORTEM.md`) and its account was
confirmed 100% flat since 2026-07-20 10:20:56 CT (post-run-#68 investigation, CLOSED 2026-07-22 —
do not re-flag its flat P&L as staleness). **As of run #76, `theta4` is ALSO live** (Stage 1 pilot,
armed 2026-07-30) — the loop now tracks TWO+ live books. **As of run #79, `mmsell10` itself wound
down live (2026-08-03), superseded by the `mmsell10a`/`mmsell10b` queue-position A/B** (both live
since 2026-08-04). Suggestions are **recommendations only** — the loop never acts on them; the user
reviews and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

*Reporting convention (confirmed 2026-07-23, standing): every run's chat report and this file must
state, for both the live P&L table and each paper book, the **realized P&L (total $)** AND the
**per-trade profit (¢/trade)** side by side.*

**[2026-08-11] MAJOR: the paper-engine fee model was fixed, resolving the standing ACCOUNTING GAP
finding.** Root cause (diagnosed run #78-81, confirmed by the team and fixed in `b061fe9`): paper
billed every resting maker entry at Kalshi's **taker** fee rate (ceiled to a whole cent), when
Kalshi actually charges makers ~0.003¢/contract on these series (measured, n=342/366 live fills).
Fix: `kalshi_fee(..., maker=True)` bills the real maker rate. Size of the correction: **+0.85 to
+0.89¢/contract** in the 5-10¢ yes band — every maker-book number reads that much better now; nothing
about the trades themselves changed. `docs/BOOK_REGISTRY.md` has a full "FEE RE-BASELINE" section
explaining the boundary (a date: 2026-08-11 deploy) and how to read gates that span it. The three
live pairs got fresh `_pt3` twin epochs at the same deploy. Confirmed empirically in run #82: the
newest twins' matched-market gaps are ~0 (−0.04¢, +0.07¢) vs the prior generation's −1.0 to −1.5¢.

---

## Snapshot — 2026-08-12 10:28 AM CDT (run #82)

**HEADLINE 1: the fee-model fix (above) is confirmed working.** `mmsell10a_pt3`/`mmsell10b_pt3`
(fresh twins minted at the 2026-08-11 fee-fix deploy) show matched-market gaps of −0.04¢ and
+0.07¢ — essentially zero, versus the prior `_pt2` generation's −1.08¢/−1.47¢ ACCOUNTING GAP. The
standing "investigate the accounting gap" ask from runs #78-81 is now fully resolved with a
verified fix, not just a diagnosis.

**HEADLINE 2: two book families were formally RETIRED today (2026-08-12).** `mmsellA1-3`
(stop-loss levels) — pre-registered gate FAILED on both halves (strongly negative, and the stop
makes the tail worse, firing on 52% of positions). `Wmmsell1-8` (wide-band market-type census) —
RETIRED as **UNMEASURABLE, not disproven**: its control book itself loses money even fee-corrected
(−0.93¢/trade), AND fill coverage is only 19-41% (the live fee/fill calibration comes from the
cheap band; the wide band's 10-40¢ entries have no live evidence). The `Tmmsell*` family tests the
same contract-type axis at 99-100% coverage, so the underlying hypothesis survives — only the wide
band specifically died. This closes suggestion items open since run #80-81.

**HEADLINE 3: the mmsell10 offset A/B kill signal holds and has strengthened.** mmsell10a
(control) now +1.49¢/ct (n=299, was +1.29¢); mmsell10b (1¢-better offset) now −1.37¢/ct (n=256,
was −0.99¢) — the gap between arms widened, not narrowed, with more data. Still reads as a clean
KILL for the offset lever per `docs/MMSELL_OFFSET_AB.md`'s pre-registered rule.

**Fourth: another twin-tag generation.** `theta4_pt2`/`mmsell10a_pt2`/`mmsell10b_pt2` (minted by
this loop mid-week) have themselves ended, superseded by `_pt3` at the 2026-08-11 fee-fix deploy —
expected and correct (the fee change is exactly the kind of event that should trigger a fresh
twin). `theta4_pt2`'s final read before ending showed an EXECUTION GAP (twin +4.13¢ vs live
+7.57¢) — live beating paper by a lot, but on small n; not concerning, just noting the pattern.
The new `_pt3` twins are still small (n=18-54) and mostly TOO EARLY, though `mmsell10a_pt3` and
`mmsell10b_pt3` already show small EXECUTION GAP reads (opposite directions, n in the 15-27 range
matched) — too little data to interpret yet, flagged for next run.

**theta_fill_model update:** two trusted cells now (14¢: +5.95¢/ct, 15¢: +20.78¢/ct), coverage up
to 21% for theta4 (was 3-13%) and now showing a clearly positive realizable read (+13.54¢/ct vs
+32.72¢ optimistic) — still under the 50% coverage bar to be gate-worthy, but trending the right
direction fast as the live pilot accumulates fills.

**FREEZE gate:** still FIRED (241 settled grain+soft, unchanged from run #81), no evidence yet
that `scripts/kalshi_freeze_study.py` has been re-run — `docs/RESEARCH_JOURNAL.md`'s freeze entry
is still the old "provisionally shelved" verdict. Still an open action item.

**Live P&L (real money):**

| book | n | win% | total | ¢/ct | note |
|---|---|---|---|---|---|
| theta4 | 67 | 88.1% | +$5.93 | +2.94¢ | strong, holding |
| mmsell10a | 299 | 94.0% | +$4.47 | **+1.49¢** | offset A/B control — positive, gap widening |
| mmsell10b | 256 | 91.0% | −$3.50 | **−1.37¢** | offset A/B test — negative, KILL signal stronger |
| mmsell10 (wound down) | 65 | 95.4% | +$3.58 | +2.75¢ | inert since 08-03, unchanged in kind |
| mmsell3 (legacy) | 367 | 91.3% | +$1.33 | +0.36¢ | unchanged |

Open live footprint: 55 positions, $50.20 deployed across all live books combined.

**Trading books (Δ vs run #81):**

| book | n (Δ) | P&L |
|---|---|---|
| mmsell (control) | 5,706 (+256) | +$71.35 |
| mmsell1 | 4,067 (+103) | +$84.59 |
| mmsell2 | 3,316 (+153) | +$91.10 |
| mmsell3 (shadow) | 2,417 (+92) | +$42.81 |
| mmsell4 | 1,419 (+78) | +$25.07 |
| mmsell5 | 993 (+176) | +$10.52 |
| mmsell6 | 1,587 (+130) | +$29.30 |
| mmsell7 | 327 (+22) | +$9.49 |
| mmsell8 | 109 (+0) | +$4.92 |
| mmsell9 | 433 (+89) | +$11.85 |
| mmsell10 | 1,115 (+118) | +$22.18 |
| mmsell10_pt (frozen) | 108 (+1) | +$5.96 |
| mmsell11 | 1,585 (+88) | +$28.27 |
| **mmsellA1-3** | 757/781/795 (+89/+91/+91) | +$33.09/+$30.44/+$29.27 — **RETIRED, see headline (FAIL)** |
| mmsellA4 | 649 (+103) | +$7.14 |
| mmsellA5 | 721 (+153) | +$12.56 |
| mmsell10a | 564 (+118) | +$9.61 |
| mmsell10a_pt2 (ended) | 238 (+7) | +$16.74 |
| mmsell10a_pt3 | 54 (new) | +$0.08 |
| mmsell10b | 559 (+115) | +$1.25 |
| mmsell10b_pt2 (ended) | 213 (+10) | +$5.29 |
| mmsell10b_pt3 | 54 (new) | −$0.14 |
| Tmmsell1 | 30 (+3) | −$1.32 |
| Tmmsell2 | 14 (+0) | +$0.77 |
| Tmmsell3 | 456 (+124) | +$7.42 |
| Tmmsell4 | 690 (+89) | +$11.42 |
| Tmmsell5 | 52 (+5) | −$0.10 |
| Tmmsell6 | 434 (+89) | +$8.84 |
| **Wmmsell1-8** | (all frozen at run #81 levels) | — **RETIRED today, see headline (UNMEASURABLE)** |
| theta (control) | 560 (+0) | +$0.97 |
| theta1 | 201 (+0) | +$9.69 |
| theta2 | 98 (+0) | −$11.55 |
| theta3 | 134 (+0) | −$11.62 |
| **theta4** | 193 (+20) | +$63.15 |
| theta4_pt (frozen) | 28 (+0) | −$2.68 |
| theta4_pt2 (ended) | 31 (+0) | +$3.92 |
| theta4_pt3 | 20 (new) | +$8.81 |
| weather_con (all) | 788 (+13) | −$27.61 |
| weather_concity | 208 (+5) | −$17.03 |

Shelved/killed (pin15, tfav, weather rest, and now mmsellA1-3 + Wmmsell1-8) unchanged, quiet — not
individually tabulated beyond the retirement note above.

**Gate sweep (step 3b):** FREEZE **241/100 FIRED**, still awaiting `kalshi_freeze_study.py` re-run
· mmsell10 offset A/B both arms past gate, KILL signal strengthened (see headline) · theta4
**193/80 CLEARED**, live strong (+2.94¢/ct) · mmsellA1-3 **RETIRED (FAIL)** · Wmmsell1-8 **RETIRED
(UNMEASURABLE)** · mmsellA4 (649, +$7.14) and mmsellA5 (721, +$12.56) both well past their n
thresholds, still need a fresh gate read (not yet retired or promoted) · Tmmsell1-6 all past their
n≥100 gate, no retirement recorded — worth checking if the same coverage issue that killed
Wmmsell applies (Tmmsell trades the tight/cheap band, which per the retirement note DOES have
live fill coverage, so this family is more likely to get a real verdict than Wmmsell did) ·
weather_concity **208/120** (past gate, RETIRE verdict from #75 still unrecorded, 8th run
reinforcing it).

**Data (last-24h rows / latest, ~03:20 PM UTC / 10:20 AM CDT run):** crypto_spot 2,874 (2 products,
10:20 AM ✓) · crypto_ladder 41,528 all with model_p (10:20 AM ✓) · weather forecasts 3,594 (10:20
AM ✓, still reading lower than the ~9-11k baseline from two weeks ago — now two runs in a row at
this lower level, worth a real look next run rather than just a glance) · observations 599 (10:20
AM ✓) · ensembles 1,672 (10:08 AM ✓) · bucket snapshots 9,516 (10:16 AM ✓). All fresh by staleness
criteria. xgame_matches/tapes still dark (expected — book KILLED, collector-only).

**Research probes (on-demand):** FREEZE still fired, still needs `scripts/kalshi_freeze_study.py`
re-run for a verdict.

**Headline (repeated for chat-report lead):** the fee-model fix diagnosed over the last several
runs is confirmed working (matched-market gaps now ~0 on the newest twins). Two book families
(mmsellA1-3, Wmmsell1-8) were formally retired today with clear, well-documented verdicts. The
mmsell10 offset A/B kill signal strengthened with more data. FREEZE is still fired and still
awaiting its real verdict — this is now the single most actionable open item.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[top actionable, unresolved 2 runs — FREEZE gate FIRED, still needs the actual study run]
   Settled grain+soft = 241, unchanged from run #81, still no `kalshi_freeze_study.py` re-run
   recorded in `docs/RESEARCH_JOURNAL.md`.** This is the single most actionable open item right
   now — the gate has been fireable for a full day-plus with no verdict yet produced.

2. **[mmsell10 offset A/B — KILL signal strengthened, still unrecorded] mmsell10a +1.49¢/ct
   (n=299, was +1.29¢), mmsell10b −1.37¢/ct (n=256, was −0.99¢) — the gap widened.** Still no
   registry verdict recorded for this A/B despite two consecutive runs of a clean kill read. A
   fable session should record it and decide whether to keep the test arm running or fold back to
   control-only.

3. **[RESOLVED — accounting gap fixed and verified] The fee-model fix (`b061fe9`, 2026-08-11) is
   confirmed working via the newest `_pt3` twins' near-zero matched-market gaps.** No further
   action; this closes out the multi-run investigation from #78-81.

4. **[RESOLVED — mmsellA1-3 and Wmmsell1-8 formally retired today] Both retirements are
   well-documented in `docs/BOOK_REGISTRY.md` with clear verdicts (FAIL and UNMEASURABLE
   respectively).** No loop action needed; noting for the record since these closed two
   multi-run-standing suggestion items.

5. **[NEW · Tmmsell family needs a gate check] All 6 Tmmsell books are past their n≥100 gate,
   with no retirement or promotion recorded — unlike its wide-band sibling Wmmsell (retired
   today), Tmmsell trades the cheap/tight band that DOES have live fill coverage per the
   retirement note, so this family is more likely to produce a real verdict rather than another
   "unmeasurable."** Worth a `mm_check`/fable session pass.

6. **[mmsellA4 / mmsellA5 need a fresh gate read] A4: n=649, +$7.14. A5: n=721, +$12.56 — both
   well past their n thresholds (100 / 82 clean pairs) with no verdict recorded yet.** Unlike
   A1-3, neither has been retired — worth a dedicated read now that both have real sample size.

7. **[registry drift — still overdue] `docs/BOOK_REGISTRY.md`'s `mmsell10a`/`mmsell10b` row still
   says "INERT," and `theta4`'s row still says "awaiting arming."** Both have been live with real
   money for over a week. This has been flagged for 5+ runs now — recommend a single consolidated
   registry-update PR covering mmsell10 (wound down), mmsell10a/b (live, A/B result), and theta4
   (live, Stage 1 progressing, gate bar updated to +0.87¢ per the fee re-baseline).

8. **[weather_concity · RETIRE verdict from #75, still unrecorded, 8th run reinforcing it] n=208
   (+5), −$17.03 (was −$16.83); weather_con(all) also worse again (−$27.61, was −$27.14).** Both
   weather books have had 8 straight runs without improvement — this is the longest-standing
   unrecorded verdict in the loop's suggestion history.

9. **[NEW · weather forecast collector volume — two runs low] Last-24h weather_forecasts row
   count: 3,456 this run, 3,594 last run — both well below the ~9,000-11,000 baseline from two
   weeks ago, though still "fresh" by the staleness check (latest row is current).** Worth an
   actual look next run rather than a passing glance, since it's now a pattern, not a one-off.

10. **[idea-model queue] MMX — build against mmsell10a (the surviving positive control arm),
    not mmsell10b. NEST — theta4's gate cleared (#74, n≥80); ready to build on the paper gate
    alone, and now has a much stronger positive live signal behind it too.**

11. **[new small-n execution-gap reads on the fresh `_pt3` twins — watch, don't read yet]
    `mmsell10a_pt3` and `mmsell10b_pt3` already show EXECUTION GAP verdicts at n=15-27 matched,
    in opposite directions.** Too little data on brand-new twins to mean anything; flagged so
    next run's read has context if it persists.

12. **[correlated-event risk · standing interpretive note] Always check for a single shared
    ticker before reading any cohort-wide batch move as a strategy-wide signal.**

*(Changed this run: #1 restated, now flagged more urgently — FREEZE fired for 2 runs with no
study yet. #2 restated, KILL signal strengthened with more data. #3 NEW/RESOLVED — the accounting
gap investigation (open since #78) is closed, fix verified. #4 NEW/RESOLVED — mmsellA1-3 and
Wmmsell1-8 retirements recorded, closing the run-#80/81 "needs fresh same-window read" item for
those two families specifically. #5 NEW — split out Tmmsell as the remaining unread family from
last run's combined item. #6 NEW — split out mmsellA4/A5 similarly. #7 — registry drift restated,
now 5+ runs overdue, updated with the new fee-baseline gate detail for theta4. #8 — weather_concity
restated, 8th run, now the longest-standing unrecorded item. #9 NEW — weather forecast volume
flagged as a real pattern after 2 low runs, not just a glance-worthy blip. #10 — MMX/NEST restated
with theta4's stronger live signal noted. #11 NEW — the fresh _pt3 twins' early execution-gap
reads, flagged for context not action. #12 restated unchanged. Dropped: the old #3/#4/#6 fresh-twin
items from run #81 (superseded by the #11 fee-deploy retwin), the old #7's Wmmsell/mmsellA1-3
portions (resolved by #4).)*
