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

## Snapshot — 2026-07-29 05:45 PM CDT (run #75)

**`weather_concity` gate CROSSED n≥120 (122 now) — but the actual verdict looks like RETIRE, not
KEEP.** The gate was "keep concity (and consider retiring all-city con) only if concity beats
all-city con." It does not: **weather_concity is at −6.85¢/trade cumulative vs weather_con(all)'s
−2.35¢/trade** — con(all) is the less-bad book. Both remain net negative. This reads as a genuine
gate resolution, just the opposite direction from a "promote" — a fable session should record the
verdict (concity does not beat con) rather than treat the crossing as a green light.

No other gate crossed this run (a quiet ~24h since #74). Everything else is incremental:

- **theta4** and **mmsell10** (both cleared last run, #74) continue accumulating cleanly:
  theta4 n=95→112 (+38.62¢/trade, essentially unchanged from +38.65¢); mmsell10 n=228→243
  (+3.84¢/trade, up from +3.73¢).
- **mmsell10 live/twin parity**: twin side now n=39 (≥30, cleared), live side n=25 (still <30) —
  script verdict remains **TOO EARLY**, but only the live leg is now the limiting sample.
  Matched-market gap holds steady at −0.49¢ (was −0.48¢) — still no accounting-gap signal.
- **Registry drift unresolved**: `docs/BOOK_REGISTRY.md` still lists `mmsell10` as `paper` only,
  still no row for `mmsell10_pt`, one run later. Carrying forward.
- **mmsell4's contradicted KILL verdict** keeps strengthening: now +2.80¢/trade (n=385), further
  above mmsell3's +2.15¢/trade. Still unrecorded — do not record the old kill.

**Live P&L (real money — `mmsell10`, epoch started 2026-07-26 21:09 UTC):**
| metric | this run | last run (#74) |
|---|---|---|
| settled live positions | 25 (100% win) | 20 |
| realized P&L | **+$3.35** | +$2.67 |
| per contract | **+6.71¢/ct** | +6.68¢/ct |
| fill rate | 54.7% (53 placed, 29 filled, 24 canceled) | 59.0% |
| fill price | 93.4¢ real vs 93.4¢ twin (px_gap −0.10¢) | −0.08¢ |
| open footprint | 5 positions, $7.70 deployed | 4 pos, $5.84 |

Parity verdict: still **TOO EARLY** (twin n=39 now clears the n≥30 bar; live n=25 does not yet).
Matched-market twin-vs-live gap −0.49¢ (consistent with last run, no ACCOUNTING GAP). Legacy
`mmsell3` live unchanged: 367 settled, +$1.33, +0.36¢/ct. `mmsell3_closeout` still inert.

**Trading books (settled n / realized P&L / ¢-per-trade / open) — PAPER, separate from live:**
| book | n (Δ vs #74) | realized P&L | ¢/trade (was) | open | note |
|---|---|---|---|---|---|
| **mmsell10** | 243 (+15) | +$9.33 | +3.84 (3.73) | 33 | LIVE, gate stays cleared |
| **mmsell10_pt** (twin) | 39 (+13) | +$4.73 | +12.13 (12.15) | 26 | still UNTRACKED — no registry row |
| mmsell11 | 453 (+19) | +$17.58 | +3.88 (3.79) | 37 | blended-paper clear; MIRAGE under fill model |
| mmsell6 | 518 (+16) | +$16.20 | +3.13 (3.05) | 37 | same caveat |
| mmsell9 | 70 (+0) | +$3.83 | +5.47 | 8 | no new settlements, still 70% to gate |
| mmsell4 | 385 (+19) | +$10.77 | +2.80 (2.63) | 37 | KILL verdict further contradicted |
| mmsell8 | 56 (+0) | +$1.47 | +2.63 | 10 | no new settlements |
| mmsell2 (paper) | 1,875 (+14) | +$57.01 | +3.04 (2.96) | 34 | steady |
| mmsell1 (paper) | 2,849 (+27) | +$64.92 | +2.28 (2.20) | 42 | steady |
| mmsell3 (paper shadow) | 1,236 (+19) | +$26.55 | +2.15 (2.09) | 37 | steady improvement |
| mmsell5 | 185 (+0) | +$3.07 | +1.66 | 0 | no new settlements |
| mmsell control (paper) | 4,350 (+29) | +$71.64 | +1.65 (1.59) | 48 | steady |
| mmsell7 | 109 (+17) | +$1.70 | +1.56 (0.71) | 0 | improving further, gate n≥150 (73%) |
| **theta4** (fat-tail) | 112 (+17) | +$43.25 | +38.62 (38.65) | 0 | cleared #74, still strong |
| weather_con (all) | 586 (+14) | −$13.77 | −2.35 (−2.58) | 16 | slight improvement |
| **weather_concity** | 122 (+8) | −$8.36 | −6.85 (−6.86) | 7 | **gate n≥120 CROSSED — reads as RETIRE, not keep** |

Shelved/killed (pin15, theta ctrl-3, tfav, weather rest) unchanged, quiet.

**Gate sweep (step 3b):** theta4 **112/80 CLEARED** (holding) · mmsell10 **243/150 CLEARED + LIVE**
(holding) · mmsell6/mmsell11 clear on blended paper, still MIRAGE under fill model · mmsell9
70/100 (70%, quiet) · mmsell7 109/150 (73%) · mmsell8 56/100 (56%, quiet) · **weather_concity
122/120 CROSSED — verdict is concity does NOT beat con(all), recommend RETIRE concity /
keep evaluating con** · FREEZE **8/100** (unchanged, 27 runs).

**Data (last-24h rows / latest, ~5:40 PM CDT run):** crypto_spot 2,872 (2 products, 5:38 PM ✓) ·
crypto_ladder 55,680 all with model_p (5:38 PM ✓) · weather forecasts 9,203 (5:41 PM ✓) ·
observations 658 (5:38 PM ✓) · ensembles 1,712 (5:24 PM ✓) · bucket snapshots 12,738 (5:41 PM ✓).
All fresh. xgame_matches/tapes still dark (expected — book KILLED, collector-only).

**Research probes (on-demand):** none standing (TFAV/WCPROP/XGAME/PINNED/DECAY families closed).

**Headline:** weather_concity crossed its n≥120 decision point this run, but the resolution is a
RETIRE signal, not a promote — it remains the more-negative of the two weather books
(−6.85¢/trade vs con(all)'s −2.35¢/trade). theta4 and mmsell10 (both cleared last run) continue
to accumulate cleanly with no new red flags; mmsell10's live/twin parity crossed n≥30 on the twin
side but still needs the live side to catch up. Registry drift on mmsell10/mmsell10_pt persists
one more run untouched.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[NEW · top actionable — weather_concity gate CROSSED, verdict = RETIRE] n=122 ≥ 120,
   −6.85¢/trade cumulative vs weather_con(all)'s −2.35¢/trade — concity is worse, not better.**
   The pre-registered rule was keep-concity-only-if-it-beats-con; it doesn't. Recommend a fable
   session record the verdict in `RESEARCH_JOURNAL.md` and retire `weather_concity`, while
   separately deciding whether all-city `weather_con` (still net negative at −2.35¢/trade) is
   worth continuing at all.

2. **[theta4 gate cleared #74 — but fill-model caution now attached, see 2026-07-29 note above]
   n=112 ≥ 80, +38.62¢/trade paper, 92%+ win — collapses to +0.51¢ realizable at only 27.7%
   coverage under `theta_fill_model`'s borrowed (mmsell3) calibration.** NEST is still unblocked on
   the paper gate alone; re-invoke `kalshi-strategy` on it if not already started, but do NOT read
   theta4 as live-ready off this gate — it needs either theta's own live fill data (a small pilot)
   or improved coverage before the realizable number means anything. See suggestion below on
   whether coverage can be raised without a live pilot.

3. **[registry drift on the LIVE book — unresolved 1 run later] `docs/BOOK_REGISTRY.md` still
   lists `mmsell10` as `paper`, and `mmsell10_pt` (39 settled / 26 open) still has no row.**
   mmsell10 has been live with real money since 2026-07-26 — a fable session should fix this.

4. **[mmsell4's KILL verdict continues to be contradicted] n=385, now +2.80¢/trade (was +2.63¢
   last run, +0.60¢ two runs ago), still above mmsell3's +2.15¢.** Do not record the old kill;
   the trend of improvement across two runs makes this look like a real result, not noise.

5. **[mmsell6 / mmsell11 promote question — gate on REALIZABLE, not blended paper] mmsell6 n=518
   +3.13¢, mmsell11 n=453 +3.88¢ on blended paper, both still recorded MIRAGE under the
   live-calibrated fill model in the registry.** Re-run `mmsell fill model` (`mm_check_1`) for
   current realizable ¢/trade before any promotion decision.

6. **[mmsell10 live — parity closing in, twin side now cleared n≥30] live n=25 (+$3.35,
   +6.71¢/ct, 100% win, 54.7% fill rate), twin n=39.** Matched-market gap steady at −0.49¢ (no
   simulator error). Only the live leg's sample (25 < 30) still gates a verdict — should resolve
   within a run or two at current pace.

7. **[mmsell7 · improving trend, 73% to gate] n=109 (+17 this run), +1.56¢/trade cumulative (was
   +0.71¢, and negative two runs ago).** Sign has flipped three times total — still track without
   over-reading, but the trend over the last two runs is consistently positive.

8. **[idea-model queue] MMX — premise (extend the mmsell edge into new categories) should be built
   against mmsell10, the promoted/live mechanism. NEST — gate cleared (#74, theta4 n≥80); ready to
   build, see #2.**

9. **[FREEZE gate · not fired] Settled grain+soft = 8 of the n≥100 trigger, unchanged across 27
   runs now (28→29 open soft listings, still 0 open grain).** Standing background check, nothing
   to act on.

10. **[correlated-event risk · standing interpretive note] Run #73's whole-cohort loss traced to
    a single shared ticker (`KXNBATEAMANNOUNCE-...LJAMES23`) and fully washed out by #74.** Keep
    checking for a single shared ticker before reading any cohort-wide batch move as a
    strategy-wide signal.

11. **[NEW · 2026-07-29 — path to raise theta4's fill-model coverage without a live pilot] Checked
    whether `crypto_ladder_snapshots` (theta's own orderbook-quote research table, already
    collected — unlike mmsell, which had to build brand-new capture tables from scratch) could
    support a genuine per-ticker replay instead of the borrowed mmsell3 calibration. Current state:
    only 29 of theta4's 112 settled tickers (26%) have ANY snapshot row, averaging ~9 rows/ticker
    — thin, and it won't retroactively cover trades from before/outside the capture window.
    Loosening the calibration's trust threshold (`MIN_CELL_FILLS`) is a cheap knob but barely
    moves the needle (27.7%→~32%) and trades reliability for coverage, not a real fix. The
    genuine path is building `theta_fill_replay.py` (mirroring `mmsell_fill_replay.py`'s
    quote-crossing proxy) against the already-collecting `crypto_ladder_snapshots` table — no new
    collection infrastructure needed, and its coverage grows automatically as theta keeps trading
    (the table isn't pruned). Not yet built; recommend as the real next step once the operator
    decides it's worth the build.

*(Changed this run: #1 NEW — weather_concity gate crossed, verdict is RETIRE not promote (replaces
the old "95% to gate" tracking item). #2 — theta4/NEST restated with the new fill-model caution
folded in (2026-07-29 addendum). #3 — registry drift restated, still unresolved. #4 — mmsell4
restated with a second run of improvement, language firmed up ("real result, not noise"). #5 —
mmsell6/mmsell11 restated. #6 — mmsell10 parity restated with twin side now clearing n≥30. #7 —
mmsell7 restated with trend note. #8/#9/#10 restated unchanged. #11 NEW — scoped a coverage-
improvement path for theta_fill_model: crypto_ladder_snapshots exists already but is thin (26%
ticker coverage), a real per-ticker replay is buildable but not yet built.)*
