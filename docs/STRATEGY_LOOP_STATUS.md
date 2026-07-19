# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12.
As of run #56, the loop also pulls real live P&L for any LIVE book via `mmsell_live` (a direct
SQL query is used instead, since the script computes but never prints total $ P&L or a WC/non-WC
split — both are load-bearing per runs #56-57's findings) — see the Live P&L section below,
tracked separately from paper. Suggestions are **recommendations only** — the loop never acts on
them; the user reviews and runs fable to change anything. Newest snapshot replaces the one above
it; the suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-19 05:36 AM CDT (run #57)

**Live P&L (real money — mmsell3):**
| bucket | n settled | wins | total P&L | ¢/contract |
|---|---|---|---|---|
| non-WC | 192 | 175 | +$0.91 | +0.5¢ |
| World Cup | 104 | 91 | −$4.56 | −4.4¢ |
| **TOTAL** | **296** | **266** | **−$3.65** | **−1.2¢** |

**Trading books (settled n / P&L / per-trade / open) — PAPER only, separate from live above:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell2 (paper) | 1,358 | +$27.29 | +2.0 | 31 | mildly positive batch (+1.8¢/trade) — the exception |
| mmsell3 (paper shadow) | 741 | +$5.92 | +0.8 | 40 | rough batch (−17.5¢/trade) — see live table for the real number |
| mmsell1 (paper) | 2,092 | +$27.82 | +1.3 | 61 | rough batch (−7.7¢/trade) |
| mmsell (control, paper) | 3,338 | +$32.18 | +1.0 | 73 | rough batch (−12.3¢/trade) |
| mmsell4 | 75 | −$3.43 | −4.6 | 32 | rough batch again (−15.1¢/trade), cumulative worsening |
| mmsell5 | 61 | −$3.80 | −6.2 | 0 | rough batch again (−14.9¢/trade), cumulative worsening |
| mmsell6 | 126 | −$0.84 | −0.7 | 36 | turned negative this run (batch −6.2¢/trade) |
| mmsell7 | 11 | −$1.25 | −11.4 | 2 | no new settlements |
| mmsell8 | 15 | −$0.78 | −5.2 | 0 | no new settlements |
| theta4 (fat-tail) | 30 | +$15.87 | +52.9 | 0 | unchanged, 4th run with no new settles |
| weather con (all) | 425 | −$15.04 | −3.5 | 23 | unchanged settled/P&L, +4 new opens |
| weather_concity | 48 | −$7.43 | −15.5 | 10 | unchanged settled/P&L, +2 new opens |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — this is now the SECOND consecutive rough batch across almost the whole mmsell
cohort (paper AND live), and this time the live loss was NOT primarily World Cup.**

**Live P&L turned meaningfully negative overnight:** total dropped from **+$0.85 (n=278) to
−$3.65 (n=296)** — an 18-trade batch that lost about $4.50. Breaking it down: the **non-WC
bucket did almost all the damage this time** (+$5.43→+$0.91, a 17-trade batch losing ~$4.52,
≈−27¢/trade) while **World Cup was nearly flat** (−$4.58→−$4.56, essentially one trade, roughly
breakeven). This is the opposite pattern from the standing narrative — last check's drag was
WC-specific, but overnight's drag was a bad non-WC batch. Worth correcting that assumption
rather than reflexively blaming WC every time; the WC drag is real and persistent on a
cumulative basis but isn't the cause of every bad stretch.

**The paper mmsell cohort had a second straight rough batch** — mmsell3 (−17.5¢/trade),
control (−12.3¢/trade), mmsell1 (−7.7¢/trade), mmsell4 (−15.1¢/trade), mmsell5 (−14.9¢/trade),
mmsell6 (−6.2¢/trade, newly negative) all down; only mmsell2 was mildly positive. Two
consecutive negative batches of this size (following run #56's large dip) is a stronger signal
than the earlier one-off oscillations — **recommend treating this as a real multi-day drawdown
to watch closely, not dismissing it as noise**, while still not concluding anything permanent
given this cohort's history of sharp reversals (mmsell5 alone went standout → worst-performer →
still worsening across the last 2 runs).

theta4 remains quiet — 4th straight run with no new settlements since the tail hit in run #53.

**Gate sweep (step 3b):** theta4 **30/80** (38%, quiet) · mmsell4-8 gates (n≥150, or n≥100 for
5/8) — mmsell6 most-advanced at n=126, all now negative except still-tiny mmsell7/8 ·
weather_concity **48/120** (40%) · FREEZE **5/100** (not fired, unchanged, 9 runs).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (05:17–05:35 AM ✓). xgame_tapes still very active (119,187 rows/24h);
xgame_matches still dark, unchanged.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** live P&L went from +$0.85 to −$3.65 overnight (non-WC-driven this time, not WC —
correcting the standing narrative). Paper mmsell cohort had its 2nd straight rough batch —
worth watching closely as a possible real drawdown, not just noise. theta4 quiet. FREEZE
unchanged.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[Live P&L watch — now showing a real drawdown, WC is NOT the cause this time] Total
   −$3.65 (n=296, was +$0.85 at n=278).** The 18-trade overnight batch lost ~$4.50, and it was
   **non-WC** that drove it (−$4.52 on 17 non-WC trades), not World Cup (flat). **Correct the
   standing assumption**: WC is a real, persistent drag on a cumulative basis (−4.4¢/trade over
   104 trades) but is not the explanation for every rough stretch — this one wasn't WC. Keep
   tracking the total-$ and WC/non-WC split every run (the `mmsell_live` script itself doesn't
   print either, so this loop uses a direct query — see the header note).

2. **[mmsell paper cohort · 2nd consecutive rough batch — escalate from "one batch" to "watch
   closely"] mmsell3/control/1/4/5/6 all had negative batches again** (mmsell4 −15.1¢, mmsell5
   −14.9¢, control −12.3¢, mmsell3 −17.5¢, mmsell1 −7.7¢, mmsell6 −6.2¢/trade); only mmsell2
   mildly positive. This is now 2 straight bad batches following run #56's initial dip —
   stronger signal than the earlier single-run oscillations. **Recommend a fable session take a
   closer look at what changed** (sport mix, specific markets) rather than waiting for the loop
   to keep reporting batch-by-batch — if this is a real regime shift (not just variance), it's
   worth knowing sooner. Still not concluding definitively given this cohort's history of sharp
   reversals both directions.

3. **[theta4 · quiet, 4th run with no new settles] n=30/80 (38%), +52.9¢/trade, unchanged since
   the tail hit in run #53.** No new action. Continue watching the realized hit rate as more
   trades settle.

4. **[idea-model queue · MMX/NEST unchanged] MMX's premise still worth checking against
   mmsell4-8 for redundancy (unresolved since run #49) — the ongoing 2-run drawdown (#2) is
   itself relevant context.** NEST still behind theta4's n≥80 gate (38% there). RTPIN/BOXPIN
   behind unbuilt scraper infra. RATELAG behind a live Fed event.

5. **[weather_concity / con(all) · quiet, no new settles] concity −15.5¢/trade (40% to gate),
   con(all) −3.5¢/trade — both flat this run (new opens only).** Carry forward.

6. **[xgame_tapes / xgame_matches · stable, unchanged] xgame_tapes very active; xgame_matches
   still dark.** Both low-urgency, no new information.

7. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 9 runs now.** Standing background check, nothing to act on.

*(Changed this run: #1 live P&L — real drawdown confirmed (+$0.85→−$3.65), but this batch was
non-WC-driven, correcting the assumption that WC explains every downturn. #2 mmsell paper cohort
— escalated from "one batch, don't over-read" to "2 consecutive rough batches, watch closely" —
recommend a fable session look at what changed. #3 theta4 unchanged. #4 MMX — noted the ongoing
drawdown as context. #5/#6/#7 restated/unchanged.)*
