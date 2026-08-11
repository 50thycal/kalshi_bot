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

## Snapshot — 2026-08-11 04:35 PM CDT (run #81)

*(6-day gap since run #80 — a LOT moved. Prioritized by materiality below.)*

**HEADLINE 1: the FREEZE gate has FIRED.** Settled grain+soft markets jumped from 9 to **241**
(grain alone went from ~2 to 233 settled — Kalshi's own listings finally grew past the threshold
the thesis was waiting on). Per the pre-registered trigger in `docs/FREEZE_THESIS.md`: **re-run
`scripts/kalshi_freeze_study.py` for a real verdict.** This is a genuine market-driven event, not
something this repo built — the mechanism (commodity-hub grain/soft contracts can genuinely
freeze) is finally testable.

**HEADLINE 2: the mmsell10 offset A/B has a real, gate-clearing read — and it favors KILLING the
1¢ offset.** Both arms are well past their n≥150-contracts-per-arm gate (mmsell10a: 260 live
contracts filled; mmsell10b: 213). Real money: **mmsell10a (control, rests at the no-bid) is
positive at +1.29¢/ct** (240 settled, 93.8% win); **mmsell10b (rests 1¢ better) is negative at
−0.99¢/ct** (193 settled, 91.2% win). Per `docs/MMSELL_OFFSET_AB.md`'s own pre-registered rule
("KILL at or below mmsell10a"), this is a clean kill signal for the 1¢-better lever — resting
closer to the market doesn't help, it hurts.

**HEADLINE 3: the ACCOUNTING GAP finding is now confirmed on two more independent, freshly-built
twins.** `mmsell10a_pt2` (n=144 matched, gap −1.02¢) and `mmsell10b_pt2` (n=82 matched, gap
−1.36¢) both cleared n≥30 and both show the identical signature as `mmsell10_pt`'s original
finding: paper reads worse than live by roughly 1-1.4¢/contract, same direction every time. This
strongly reinforces the root cause already found and reported directly to the user: paper's
`kalshi_fee()` charges the full taker-fee formula on every maker entry, while Kalshi's real fee on
these resting fills is close to zero — paper is subtracting a phantom cost live never pays. Not
new work needed here, just further confirmation the diagnosis was right.

**Fourth: `theta_fill_model` crossed its first-ever trusted cell.** yes=15¢ now has 8 real live
fills, reading +7.50¢/ct at 100% fill rate — the first piece of theta's OWN live-calibrated data
ever to clear the trust bar. Coverage is still low (3-13% across the theta family), so nothing
gate-worthy yet, but this is the mechanism finally starting to work as designed.

**Also this run:** theta4's live book recovered from run #80's losing stretch — now n=49 settled
(was 23), 83.7% win, and matched-market read is positive again (+2.65¢/ct on the latest 26-trade
twin epoch). The fresh twin tags minted mid-week (`theta4_pt2`, `mmsell10a_pt2`, `mmsell10b_pt2`)
are all up and clean. `mmsell10a`'s twin logged an anomaly worth a look: **live placed 242 orders
its own twin never opened** — the twin is more constrained than live somewhere (worth checking its
open-position cap). weather books both got worse again (weather_con now −$27.14, weather_concity
−$16.83, both bigger negative numbers than run #80).

**Live P&L (real money):**

| book | n | win% | total | ¢/ct | note |
|---|---|---|---|---|---|
| theta4 | 49 | 83.7% | −$2.05 (in-epoch) | −1.39¢ | recovering — see headline |
| mmsell10a | 240 | 93.8% | +$3.08 | **+1.29¢** | offset A/B control — positive |
| mmsell10b | 193 | 91.2% | −$1.92 | **−0.99¢** | offset A/B test arm — negative, KILL signal |
| mmsell10 (wound down) | 64 | 95.3% | +$3.39 | +2.65¢ | inert since 08-03, unchanged in kind |
| mmsell3 (legacy) | 367 | 91.3% | +$1.33 | +0.36¢ | unchanged |

Open live footprint: 44 positions, $41.73 deployed across all live books combined.

**Trading books (Δ vs run #80):**

| book | n (Δ) | P&L |
|---|---|---|
| mmsell (control) | 5,450 (+927) | +$70.86 |
| mmsell1 | 3,964 (+976) | +$83.52 |
| mmsell2 | 3,163 (+1,194) | +$88.32 |
| mmsell3 (shadow) | 2,325 (+987) | +$40.99 |
| mmsell4 | 1,341 (+855) | +$23.34 |
| mmsell5 | 817 (+614) | +$3.46 |
| mmsell6 | 1,457 (+850) | +$27.48 |
| mmsell7 | 305 (+169) | +$8.85 |
| mmsell8 | 109 (+34) | +$4.92 |
| mmsell9 | 344 (+252) | +$8.15 |
| mmsell10 | 997 (+683) | +$19.70 |
| mmsell10_pt | 107 (+4) | +$5.83 (frozen, wound down) |
| mmsell11 | 1,497 (+942) | +$25.69 |
| mmsellA1 | 668 (+633) | +$29.48 |
| mmsellA2 | 690 (+653) | +$26.72 |
| mmsellA3 | 704 (+665) | +$25.54 |
| mmsellA4 | 546 (+504) | +$3.64 |
| mmsellA5 | 568 (+568, bug-fixed 08-03) | +$10.86 |
| mmsell10a | 446 (+446) | +$5.07 |
| mmsell10a_pt2 | 231 (new) | +$16.15 |
| mmsell10b | 444 (+444) | +$1.92 |
| mmsell10b_pt2 | 203 (new) | +$4.35 |
| Tmmsell1 | 27 (+27) | −$1.50 |
| Tmmsell2 | 14 (new) | +$0.77 |
| Tmmsell3 | 332 (+332) | +$5.41 |
| Tmmsell4 | 601 (+601) | +$9.80 |
| Tmmsell5 | 47 (+47) | −$0.39 |
| Tmmsell6 | 345 (+345) | +$7.10 |
| Wmmsell1 | 1,219 (+1,219) | −$1.30 |
| Wmmsell2 | 487 (new) | +$8.57 |
| Wmmsell3 | 461 (new) | +$5.65 |
| Wmmsell4 | 44 (+44) | −$3.94 |
| Wmmsell5 | 16 (new) | +$0.43 |
| Wmmsell6 | 624 (+624) | +$10.96 |
| Wmmsell7 | 68 (+68) | −$1.75 |
| Wmmsell8 | 475 (new) | +$7.83 |
| theta (control) | 560 (+0) | +$0.97 |
| theta1 | 201 (+0) | +$9.69 |
| theta2 | 98 (+0) | −$11.55 |
| theta3 | 134 (+0) | −$11.62 |
| **theta4** | 173 (+33) | +$49.21 |
| theta4_pt (frozen) | 28 (+2) | −$2.68 |
| theta4_pt2 | 31 (new) | +$3.92 |
| weather_con (all) | 775 (+85) | −$27.14 |
| weather_concity | 203 (+35) | −$16.83 |

Shelved/killed (pin15, tfav, weather rest) unchanged, quiet — not tabulated.

**Registry cross-checks — already-recorded verdicts worth restating since a lot of trades
accumulated against them:**
- **mmsellA1-3 (stop-loss levels):** registry already records a 2026-08-03 reading-correction FAIL
  — scored on the right status filter, all three levels are strongly negative and fail both halves
  of the gate. Current cumulative numbers (n=668-704, all positive-looking blended) do NOT
  supersede that corrected verdict — the registry's own note explains why the blended number
  misleads (it drops exactly the trades the stop closes). No new action; just don't be misled by
  the top-line P&L in the table above.
- **Wmmsell family:** registry logged a 2026-08-09 gate fix (absolute-floor clause) after the
  control book itself read negative in the relevant window — "beating a losing control isn't an
  edge." Current cumulative control (`mmsell`) is actually positive now (+$70.86 all-time), so a
  fresh same-window read against the current control would be needed before reading any Wmmsell
  book as cleared — that's a `mm_check`/fable-session job, not something this loop should
  eyeball from raw cumulative numbers.
- **mmsellA5:** the pairing-gate bug (fixed 2026-08-03) is now clearly resolved — n=568 and
  growing, +$10.86. Worth a fresh gate read against its n≥82-clean-pairs bar.

**theta fill-model re-read:** own-calibration, first trusted cell (yes=15¢, n=8, +7.50¢/ct, 100%
fill). Coverage still 3-13% across the theta family — not gate-worthy yet, but the mechanism is
now producing real signal for the first time.

**Gate sweep (step 3b):** FREEZE **241/100 FIRED** (see headline) · mmsell10 offset A/B both arms
past n≥150/arm, real-money read favors KILL (see headline) · theta4 **173/80 CLEARED** (holding,
live recovering) · mmsell9/mmsell7/mmsell8 all well past their gates now (344/100, 305/150,
109/100) — due a promote/kill read · weather_concity **203/120** (past gate, RETIRE verdict from
#75 still unrecorded, now 7 runs reinforcing it) · mmsellA1-4 well past n≥100 — A1-3 registry
already reads FAIL (see above), A4 needs a fresh read · mmsellA5 n=568, past its n≥82 pairs bar,
needs a fresh read now that its bug is fixed · Tmmsell/Wmmsell mostly past their gates (100-150)
too — needs the dedicated same-window comparison, not a cumulative eyeball.

**Data (last-24h rows / latest, ~09:28 PM UTC / 4:28 PM CDT run):** crypto_spot 2,874 (2 products,
4:27 PM ✓) · crypto_ladder 43,680 all with model_p (4:28 PM ✓) · weather forecasts 3,456 (4:27 PM
✓, notably lower volume than prior runs — worth a glance if it stays low) · observations 579 (4:23
PM ✓) · ensembles 1,680 (4:19 PM ✓) · bucket snapshots 9,882 (4:23 PM ✓). All fresh. xgame_matches/
tapes still dark (expected — book KILLED, collector-only).

**Research probes (on-demand):** FREEZE just fired — see headline; run `scripts/kalshi_freeze_study.py`.

**Headline (repeated for chat-report lead):** the FREEZE gate fired (settled grain+soft crossed
100, now at 241) — a real verdict is now possible for the first time. The mmsell10 offset A/B is
fully powered and reads as a KILL for the 1¢-better lever (control +1.29¢/ct vs test −0.99¢/ct).
Two more fresh twins independently reproduced the accounting-gap signature, reinforcing the
already-diagnosed paper-fee-model root cause. theta4's live book recovered from its losing
stretch. A LOT of paper books (mmsellA1-5, Tmmsell1-6, Wmmsell1-8) crossed their gates during the
6-day gap and need fresh same-window reads — several already have registry-recorded verdicts
(A1-3 FAIL, Wmmsell needs the absolute floor applied) that the loop is restating, not re-deriving.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[NEW · top actionable — FREEZE gate FIRED] Settled grain+soft crossed the n≥100 trigger,
   now at 241 (grain alone went 2→233).** Re-run `scripts/kalshi_freeze_study.py` for the first
   real verdict this thesis has ever been able to produce — this is a genuine "go do the analysis"
   moment, not a routine restatement.

2. **[NEW · top actionable — mmsell10 offset A/B reads KILL for the 1¢ offset] Both arms cleared
   n≥150 live contracts. mmsell10a (control) +1.29¢/ct (n=240, 93.8% win); mmsell10b (1¢-better
   offset) −0.99¢/ct (n=193, 91.2% win).** Per the pre-registered rule in `docs/MMSELL_OFFSET_AB.md`
   ("KILL at or below mmsell10a"), this is a clean kill. A fable session should record the verdict
   and consider whether to keep running the test arm or fold capital back into the control-only
   config.

3. **[NEW · ACCOUNTING GAP reproduced on two more independent fresh twins] `mmsell10a_pt2`
   (n=144 matched, gap −1.02¢) and `mmsell10b_pt2` (n=82 matched, gap −1.36¢) both show the same
   signature as the original `mmsell10_pt` finding.** This is confirmation, not a new open
   question — the root cause was already found and reported to the user directly: paper's
   `kalshi_fee()` charges the full taker-fee formula on every maker entry, while Kalshi's real fee
   on these fills is near-zero. No further loop action needed; flagging so a fable session sees
   the confirming evidence when it gets to fixing the fee model.

4. **[RESOLVED — theta4/mmsell10a/mmsell10b now have fresh, clean twins] `theta4_pt2`,
   `mmsell10a_pt2`, `mmsell10b_pt2` were minted this week and show no PARAM DRIFT anomalies.**
   Drops the standing "start a fresh twin" recommendation from prior runs. Note for the future: the
   drift on the old twins was traced to a new shared code feature (`live_hot_market_*` defensive
   repricing) shipping after those twins were born, not to anyone actively mistuning a knob — worth
   remembering if drift reappears, since it may again be a new-feature-vs-old-snapshot mismatch
   rather than something to chase down as a config change.

5. **[theta4 live recovered from run #80's losing stretch] n=49 settled (was 23), 83.7% win
   (was 78.3%), matched-market read back to positive (+2.65¢/ct on the new epoch).** The dip flagged
   last run didn't compound — reads as the small-n noise it was expected to be at the time.

6. **[NEW · mmsell10a_pt2 anomaly — twin more constrained than live] Live placed 242 orders its
   own twin never opened.** Worth a look at whether the twin's open-position cap is set tighter
   than live's — not urgent (doesn't affect the settled-trade comparison already read above) but
   worth fixing so the twin's own activity count isn't misleadingly low.

7. **[A wave of paper books crossed their gates during the 6-day gap — needs fresh same-window
   reads, not a cumulative eyeball] mmsellA1-4, mmsellA5, Tmmsell1-6, and Wmmsell1-8 are all now
   past (or close to) their n thresholds.** Two verdicts are ALREADY recorded in
   `docs/BOOK_REGISTRY.md` and should not be re-derived from the cumulative numbers in this run's
   table: **mmsellA1-3 FAIL** (2026-08-03 reading correction — the blended P&L looks positive but
   drops exactly the trades the stop closes; corrected read is strongly negative on both gate
   halves) and **Wmmsell needs the 2026-08-09 absolute-floor clause applied** (a book beating a
   losing control isn't an edge — and the control's cumulative number has since gone positive, so
   this needs a fresh same-window comparison, not today's raw totals). mmsellA5's pairing-gate bug
   is confirmed fixed (n=568 and climbing) — worth a first real gate read now that it's actually
   producing data. Tmmsell hasn't had its absolute-floor fix documented as explicitly as Wmmsell's
   — worth checking if it needs the same treatment. This is real analysis work for a
   `mm_check`/fable session, not something to eyeball from the books table.

8. **[weather_concity · RETIRE verdict from #75, still unrecorded, 7th run reinforcing it] n=203
   (+35), worse again (−$16.83, was −$9.57); weather_con(all) also worse again (−$27.14, was
   −$22.68).** Both weather books have now had 7 straight runs without improvement. A fable session
   should record the retire verdict and reconsider `weather_con`'s own viability at this point —
   the "still historically the only +EV weather book" framing is getting harder to defend as the
   cumulative number keeps sliding.

9. **[registry drift — now genuinely overdue] `docs/BOOK_REGISTRY.md` still describes `mmsell10a`/
   `mmsell10b` as "INERT" and `theta4` as "awaiting arming."** Both have been live with real
   money and real P&L for over a week now. This has been flagged for 4+ runs; recommend a single
   consolidated registry-update PR covering mmsell10 (wound down), mmsell10a/b (live, A/B result
   per #2), and theta4 (live, Stage 1 progressing) together.

10. **[idea-model queue] MMX — premise (extend the mmsell edge into new categories) should be built
    against mmsell10a (the surviving control arm per #2), not mmsell10b. NEST — gate cleared
    (#74, theta4 n≥80); ready to build on the paper gate alone.**

11. **[correlated-event risk · standing interpretive note] Always check for a single shared ticker
    before reading any cohort-wide batch move as a strategy-wide signal.**

12. **[data note] weather forecast row count this run (3,456 last-24h) reads notably lower than
    recent runs (~9,000-11,000) — still "fresh" by staleness criteria (latest row is current), but
    worth a glance next run if the volume stays down.**

*(Changed this run: extensive rewrite given the 6-day gap. #1 NEW — FREEZE fired. #2 NEW — offset
A/B reads KILL. #3 NEW — accounting gap reproduced twice more, confirming not re-opening the
question. #4 RESOLVED — fresh twins are clean, drops the "start a new twin" asks from runs #76-80.
#5 — theta4's run-#80 dip resolved, didn't compound. #6 NEW — a minor twin-cap anomaly on
mmsell10a_pt2. #7 NEW — replaces old #6/#7 mmsellA/T/W items now that they've crossed their gates;
points at already-recorded registry verdicts instead of re-deriving. #8 — weather_concity restated,
7th run. #9 — registry drift restated, now flagged as genuinely overdue. #10 — MMX pointed at the
surviving arm. #11 restated unchanged. #12 NEW — a data-volume note. Dropped: the theta4_pt/
mmsell10_pt PARAM DRIFT asks (resolved by #4), the "mmsell10 wind-down still unconfirmed" item
(confirmed — user said intentional), the broad-batch-loss watch item (superseded by 6 days of
data).)*
