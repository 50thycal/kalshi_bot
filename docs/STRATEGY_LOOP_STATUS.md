# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via a direct SQL query (total
$ P&L + WC/non-WC split — `mmsell_live` itself computes but never prints either). Suggestions
are **recommendations only** — the loop never acts on them; the user reviews and runs fable to
change anything. Newest snapshot replaces the one above it; the suggestion list carries over
run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-19 12:05 PM CDT (run #58)

**Live P&L (real money — mmsell3):**
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 192 | 175 | +$0.91 | +0.5¢ |
| World Cup | 106 | 92 | −$4.51 | −4.3¢ |
| **TOTAL** | **298** | **267** | **−$3.60** | **−1.2¢** |

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell2 (paper) | 1,362 | +$27.93 | +2.1 | 50 | positive batch (+16.0¢/trade) |
| mmsell1 (paper) | 2,097 | +$28.52 | +1.4 | 79 | positive batch (+14.0¢/trade) |
| mmsell (control, paper) | 3,345 | +$33.77 | +1.0 | 86 | positive batch (+22.7¢/trade) |
| mmsell3 (paper shadow) | 742 | +$5.98 | +0.8 | 58 | flat (n=1) — see live table for the real number |
| mmsell6 | 127 | −$0.78 | −0.6 | 50 | flat (n=1), still negative cumulative |
| mmsell4/5/7/8 | 75/61/11/15 | — | −4.6/−6.2/−11.4/−5.2 | 50/0/11/1 | no new settlements this run |
| **mmsell9/10/11 (NEW, 2nd cohort)** | 0/0/0 | — | — | 0/15/29 | built 2026-07-18, no settlements yet — see #3 |
| theta4 (fat-tail) | 30 | +$15.87 | +52.9 | 0 | unchanged, 5th run with no new settles |
| weather con (all) | 444 | −$12.52 | −2.8 | 10 | positive batch (+13.3¢/trade), improving |
| weather_concity | 56 | −$5.64 | −10.1 | 6 | positive batch (+22.4¢/trade), improving |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — the drawdown flagged in runs #56-57 has PAUSED, not worsened: both live and paper
saw small positive-to-flat batches this run.** Live P&L barely moved (−$3.65 → −$3.60, just 2
new trades, roughly flat) — the overnight bleed didn't continue. The paper mmsell core family
(control/1/2) posted genuinely positive batches (+14¢ to +22.7¢/trade), though on small n (4-7
trades each) — too little to call this a confirmed recovery, but it does mean the two rough
batches from #56-57 did NOT continue into a third. mmsell4/5/7/8 had zero new settlements to
read at all this run.

**New books this run: `mmsell9`, `mmsell10`, `mmsell11`** — CONFIRMED TRACKED (registry row
exists, built 2026-07-18, `docs/MMSELL_VARIANTS_THESIS.md` 2nd cohort). These come from a live
2×2 price×type decomposition: cheap (yes≤7¢) × non-winner was found to be the +EV cell. mmsell9
is the sweet-spot cell (gate n≥100), mmsell10 tests an entry-price ceiling alone (**flagged in
the thesis as the highest-value read** — directly promotable into live mmsell3 if it beats
control), mmsell11 tests no-late-entry. mmsell10/11 have opens (15/29) but zero settlements yet;
mmsell9 has no activity at all. Not untracked, just brand new — watch as they accrue.

weather books both had a genuinely positive batch (con(all) +13.3¢/trade, concity +22.4¢/trade)
— also part of the general stabilization this run.

theta4 remains quiet — 5th straight run with no new settlements since the tail hit in run #53.

**Gate sweep (step 3b):** theta4 **30/80** (38%, quiet) · mmsell4-8 gates unchanged this run ·
mmsell9/10/11 gates (n≥150, mmsell9 n≥100) — all at n=0, brand new · weather_concity **56/120**
(47%) · FREEZE **5/100** (not fired, unchanged, 10 runs).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (11:57 AM–12:06 PM ✓). xgame_tapes very active (127,302 rows/24h);
xgame_matches still dark, unchanged.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** the runs #56-57 drawdown paused this run — live P&L flat, paper core family
posted a genuinely positive (if small-n) batch. New 2nd-cohort variants mmsell9/10/11 appeared,
confirmed tracked — mmsell10 (price ceiling) is the highest-value one to watch per its own
thesis. theta4 quiet. FREEZE unchanged.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[Live P&L watch · drawdown paused, not resolved] Total −$3.60 (n=298), essentially
   unchanged from −$3.65 (n=296) — only 2 new trades settled, roughly flat.** The overnight bleed
   from run #57 did not continue this run. Keep tracking total-$ and the WC/non-WC split every
   run — not declaring this resolved or recovered, just noting it stopped getting worse.

2. **[mmsell paper cohort · drawdown paused, small positive batch — not yet a confirmed
   recovery] control/1/2 all positive this batch (+14¢ to +22.7¢/trade) but on small n (4-7
   trades each); mmsell4/5/7/8 had zero new settlements to read.** Downgrading from run #57's
   "watch closely, possible real drawdown" — the third consecutive bad batch didn't happen — but
   the sample sizes here are too small to call this confirmed stabilization either. Continue
   watching; the fable-session investigation suggested in run #57 is still worth doing if
   there's time, just less urgent now.

3. **[NEW · mmsell9/10/11 (2nd cohort) — confirmed tracked, watch as they accrue] Built
   2026-07-18 from a live 2×2 price×type decomposition (cheap × non-winner = the +EV cell).**
   mmsell10 (entry-price ceiling only) is flagged in its own thesis as the highest-value read —
   directly promotable into live mmsell3 if it beats control. mmsell10/11 have opens (15/29) but
   zero settlements; mmsell9 has zero activity at all. Gates: n≥150 (mmsell9 n≥100). Too early to
   read; will track alongside mmsell4-8 going forward.

4. **[theta4 · quiet, 5th run with no new settles] n=30/80 (38%), +52.9¢/trade, unchanged since
   the tail hit in run #53.** No new action. Continue watching the realized hit rate as more
   trades settle.

5. **[idea-model queue · MMX/NEST unchanged] MMX's premise still worth checking against the now-
   larger mmsell4-11 cohort for redundancy (unresolved since run #49) — with 8 live variants now,
   this check is more relevant, not less.** NEST still behind theta4's n≥80 gate (38% there).
   RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed event.

6. **[weather_concity / con(all) · both improved this run] concity −10.1¢/trade (47% to gate,
   up from 40%), con(all) −2.8¢/trade — both had genuinely positive batches this run** (+13.3¢
   and +22.4¢/trade respectively). Carry forward, trend worth noting.

7. **[xgame_tapes / xgame_matches · stable, unchanged] xgame_tapes very active; xgame_matches
   still dark.** Both low-urgency, no new information.

8. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 10 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1/#2 — the runs #56-57 drawdown paused (live flat, paper core positive),
downgraded from "watch closely" to a lighter standing watch, not declared resolved. #3 NEW —
mmsell9/10/11 2nd cohort appeared, confirmed tracked via registry, mmsell10 flagged as
highest-value per its own thesis. #5 MMX — updated to reflect the larger 8-variant cohort now
in play. #6 weather — both books improved this run. #4/#7/#8 restated/unchanged.)*
