# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via `mmsell_live` — see the
Live P&L section below, tracked separately from the paper books table since the two numbers
will diverge by design.) Suggestions are **recommendations only** — the loop never acts on them;
the user reviews and runs fable to change anything. Newest snapshot replaces the one above it;
the suggestion list carries over run-to-run. All times CENTRAL (CDT/CST). Retired/fully-resolved
books (pin15) and confirmed-stable data items are dropped from the table below once settled.*

---

## Snapshot — 2026-07-18 08:03 PM CDT (run #56)

**Live P&L (real money — mmsell3, first report from the newly-wired `mmsell_live` check):**
| book | n settled | live win% | live ¢/ct | paper win% | paper ¢/ct | open | capital deployed |
|---|---|---|---|---|---|---|---|
| mmsell3 (LIVE) | 278 | 91.4% | **+0.31¢** | 93.9% | +1.43¢ | 6 | $4.82 |

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell3 (paper shadow) | 717 | +$10.12 | +1.4 | 18 | roughly flat batch — see live table above for the real number |
| mmsell2 (paper) | 1,332 | +$26.82 | +2.0 | 13 | large negative batch (−8.4¢/trade, n=80) |
| mmsell1 (paper) | 2,049 | +$31.14 | +1.5 | 28 | large negative batch (−5.0¢/trade, n=137) |
| mmsell (control, paper) | 3,266 | +$41.00 | +1.3 | 39 | large negative batch (−3.1¢/trade, n=187) |
| mmsell5 | 48 | −$1.87 | **−3.9** | 0 | **reversed from standout (+8.7¢) to negative** — big batch (n=25, −15.4¢/trade) |
| mmsell4 | 53 | −$0.11 | −0.2 | 9 | turned negative (batch −6.3¢/trade, n=15) |
| mmsell6 | 110 | +$0.15 | +0.1 | 16 | dropped near zero (batch −0.9¢/trade, n=62) |
| mmsell7 | 11 | −$1.25 | −11.4 | 0 | small n, rough batch (−43¢/trade, n=2) |
| mmsell8 | 15 | −$0.78 | −5.2 | 0 | small n, rough batch (−42.5¢/trade, n=2) |
| theta4 (fat-tail) | 30 | +$15.87 | +52.9 | 0 | unchanged, 3rd run with no new settles since the tail hit |
| weather con (all) | 425 | −$15.04 | −3.5 | 19 | unchanged settled/P&L, +4 new opens |
| weather_concity | 48 | −$7.43 | −15.5 | 8 | unchanged settled/P&L, +2 new opens |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — two things: the loop's first live P&L report (fixed last session), and a large,
broad negative batch across the whole mmsell paper family tonight (Saturday — heavy weekend
sports volume).**

**Live P&L, reported for the first time:** mmsell3's real money is at **n=278 settled, +0.31¢/
contract, 91.4% win rate** — vs paper's +1.43¢/contract at 93.9% win. The adverse-selection gap
(real fills getting picked off in ways paper's free-fill assumption can't see) is real and
persists at roughly **1.1¢/contract**, but it has **improved since the ad-hoc check 3 days ago**
(then: n=222, −0.27¢/contract, 90.5% win — live was actually negative). Live P&L is now
positive again, just still trailing paper by a real margin. This is exactly the number this loop
was blind to for 3+ days — now it's tracked every run.

**The mmsell paper family took a large, broad hit tonight** — much bigger in volume than the
smaller oscillations of runs #53-55 (the control alone settled 187 new trades this batch,
consistent with a heavy Saturday-night sports slate). mmsell2/1/control all had clearly negative
batches (−3.1¢ to −8.4¢/trade); mmsell3 was roughly flat. Among the new variants, **mmsell5 —
last run's standout at +8.7¢/trade — reversed hard** on a real batch of 25 new trades at
−15.4¢/trade, pulling its cumulative to −3.9¢/trade. mmsell4 also turned negative; mmsell6
dropped near zero; mmsell7/8 are still too small-n to read (2 trades each this batch). Given the
size of tonight's batch (187+ trades across the family) this reads as a genuine bad night for
favorite-longshot maker-selling — likely a real correlated sports-results event — rather than
pure statistical noise, though it's still one batch and the standing "don't over-read single
swings" caution applies, especially for the small-n variants.

theta4 remains quiet — third straight run with no new settlements since the tail hit in run #53.

**Gate sweep (step 3b):** theta4 **30/80** (38%, quiet) · mmsell4-8 gates (n≥150, or n≥100 for
5/8) — mmsell6 most-advanced at n=110, all now negative-to-flat after tonight's batch ·
weather_concity **48/120** (40%) · FREEZE **5/100** (not fired, unchanged).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (07:58–08:01 PM ✓). xgame_tapes very active (114,800 rows/24h — a busy
Saturday for the shelved collector too); xgame_matches still dark, unchanged.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** first-ever live P&L report — mmsell3 live is +0.31¢/contract (n=278), improved
from negative 3 days ago but still trailing paper's +1.43¢ by ~1.1¢ (persistent adverse
selection). Separately, a large Saturday-night sports batch hit the whole mmsell paper family;
mmsell5 reversed from standout to negative on real volume (n=25). theta4 quiet. FREEZE unchanged.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[RESOLVED · live P&L now tracked] The top-priority gap from runs #49-55 is fixed — this
   loop now reports real mmsell3 P&L every run via `mmsell_live` (PR #50, merged).** Current
   read: +0.31¢/contract live vs +1.43¢/contract paper (n=278), a ~1.1¢ adverse-selection gap
   that has been present since the first ad-hoc check but is trending better (was negative 3
   days ago). **New standing watch: track this gap run-to-run** — if it widens rather than
   closes as n grows, that's the signal mmsell3's live execution has a structural problem paper
   can't see; if it closes, the gap was just early-sample noise.

2. **[mmsell4-8 · large real batch, mmsell5 reversed from standout — watch closely, don't
   over-read yet] All five variants negative-to-flat after a big Saturday-night batch** (mmsell5
   −3.9¢ cum after a 25-trade batch at −15.4¢/trade; mmsell4 −0.2¢; mmsell6 +0.1¢; mmsell7/8
   still tiny-n). This is a bigger, more voluminous swing than the runs #53-55 oscillations —
   plausibly a real correlated sports-results event (heavy weekend volume across the whole
   family) rather than pure noise, but still just one batch. **Recommend watching the next 1-2
   runs before revising any read on mmsell5 specifically** — it went from best-performer to
   worst-performer in one batch, which is exactly the kind of single-batch swing this loop has
   learned (via pin15, mmsell2-vs-3) not to over-read either direction.

3. **[theta4 · quiet, 3rd run with no new settles] n=30/80 (38%), +52.9¢/trade, unchanged since
   the tail hit in run #53.** No new action. Continue watching the realized hit rate as more
   trades settle.

4. **[idea-model queue · MMX/NEST unchanged] MMX's premise still worth checking against
   mmsell4-8 for redundancy (unresolved since run #49) — tonight's reversal is itself relevant
   context for that check (the family's edge may be less stable than the early reads suggested).**
   NEST still behind theta4's n≥80 gate (38% there). RTPIN/BOXPIN behind unbuilt scraper infra.
   RATELAG behind a live Fed event.

5. **[weather_concity / con(all) · quiet, no new settles] concity −15.5¢/trade (40% to gate),
   con(all) −3.5¢/trade — both flat this run (new opens only).** Carry forward.

6. **[xgame_tapes / xgame_matches · stable, unchanged] xgame_tapes very active tonight (busy
   Saturday); xgame_matches still dark.** Both low-urgency, no new information.

7. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 8 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 RESOLVED — live P&L is now tracked every run (PR #50 merged); reframed as
a standing watch on the live-vs-paper adverse-selection gap rather than "still missing." #2
mmsell4-8 — large real batch reversed mmsell5 from standout to worst-performer; flagged as
possibly-real (heavy weekend volume) but still one batch, don't over-read yet. #3 theta4 —
unchanged. #4 MMX — noted tonight's reversal as relevant context. #5/#6/#7 restated/unchanged.)*
