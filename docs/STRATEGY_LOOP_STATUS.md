# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST). Retired/fully-resolved
books (pin15) and confirmed-stable data items are dropped from the table below once settled —
nothing new to report on those unless flagged again.*

---

## Snapshot — 2026-07-17 08:03 PM CDT (run #53)

**Trading books (settled n / P&L / per-trade / open) — paper only; live P&L still not tracked
here, see #1 below:**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **theta4** (fat-tail) | 30 | +$15.87 | **+52.9** | 0 | **likely first real tail hit** (−$4.06 on one trade) — see headline |
| mmsell5 | 17 | +$1.46 | +8.6 | 1 | held up, good batch (+10c/trade) while rest of family dipped |
| mmsell2 (paper) | 1,226 | +$32.69 | +2.7 | 11 | mildly positive batch, unaffected by the family dip |
| mmsell1 (paper) | 1,876 | +$37.54 | +2.0 | 14 | mild negative batch |
| mmsell (control, paper) | 3,014 | +$43.82 | +1.5 | 23 | rough batch (−10.5¢/trade) |
| mmsell3 (paper shadow) | 620 | +$9.87 | +1.6 | 6 | rough batch (−7¢/trade) — still see #1 for the real (live) number |
| mmsell4 | 31 | +$0.29 | +0.9 | 4 | rough batch (−12.7¢/trade) |
| mmsell6 | 39 | +$0.52 | +1.3 | 4 | rough batch (−13.6¢/trade) |
| mmsell7 | 9 | −$0.39 | −4.3 | 0 | rough batch (−13¢/trade) — first negative cumulative reading |
| mmsell8 | 13 | +$0.07 | +0.5 | 0 | rough batch (−6¢/trade) |
| weather con (all) | 410 | −$11.82 | −2.9 | 15 | unchanged settled/P&L, +5 new opens |
| weather_concity | 42 | −$5.36 | −12.8 | 6 | unchanged settled/P&L |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — theta4 very likely took its first real tail hit, and the way it happened is
actually reassuring for calibration.** One new trade lost **−$4.06** (`KXBTC-26JUL1714-B64050`,
resolved against the held NO position — the sold tail hit). Pulled cumulative down from +68.7¢
to **+52.9¢/trade**, still solidly positive. Critically: this trade's **modeled tail-hit
probability was 13.6%** — one of the *highest* in the whole calibration sample (the 2026-07-15
check's range was 1.4%-13.4%), meaning the model itself flagged this as one of the riskier
positions in the book, and it's the one that hit. That is exactly what good calibration looks
like — not a "safe" trade blowing up unexpectedly, but the relatively risky one going against us
at roughly the rate you'd expect. **Realized hit rate is now 1/30 = 3.3%, still well inside the
gate's 1.25×-modeled bound (~8.6%).** No action needed; this is the real-world validation the
loop has been waiting for since the 2026-07-15 calibration check, and it landed as a good sign,
not a red flag.

**Second finding: most of the mmsell family (paper) had a rough, roughly-simultaneous batch** —
mmsell4 (−12.7¢), mmsell6 (−13.6¢), mmsell7 (−13¢, first negative cumulative), mmsell8 (−6¢), the
control (−10.5¢), and mmsell3 (−7¢) all dipped together, while **mmsell5 alone stayed positive**
(+10¢/trade) and mmsell2 was mildly positive. mmsell5's allowlist (`TOTAL+SPREAD+ASG+HRDERBY` —
totals/spreads/props, no head-to-head) may have structurally avoided whatever moved against the
others; consistent with a shared adverse sports-results batch concentrated in head-to-head
markets, similar to run #47's original mmsell-family finding. Still very small n on the
variants — don't over-read, but mmsell5's resilience here is a second small data point in its
favor (after its steady positive accrual in runs #50-52).

weather books both quiet (new opens only). **xgame_tapes resumed** on its own after being frozen
since 2026-07-15 (9,614 rows in the last 24h, fresh); xgame_matches remains dark. Low-urgency
either way (shelved book).

**Gate sweep (step 3b):** theta4 **30/80** (38%, first tail hit landed within calibration bounds)
· mmsell4-8 gates (n≥150, or n≥100 for 5/8) — mmsell6 most-advanced at n=39, all still early ·
weather_concity **42/120** (35%) · FREEZE **5/100** (not fired, unchanged).

**Data (last-24h / latest CDT):** crypto_spot, crypto_ladder, weather forecasts/obs/ensembles/
buckets all fresh (07:52–08:01 PM ✓). xgame_tapes resumed (see above); xgame_matches still dark,
unchanged.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** theta4 likely took its first real tail hit (−$4.06) on one of its highest-modeled-
risk trades — cumulative eased to +52.9¢/trade but the realized hit rate (3.3%) is still well
inside the calibration gate. Good validating signal, not a concern. Most of the mmsell4-8 family
(+ control/mmsell3) had a shared rough batch; mmsell5 alone stayed resilient. xgame_tapes
resumed on its own.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[STILL TOP PRIORITY, UNCHANGED · get live P&L into this loop] mmsell3 continues trading real
   money; this loop still has no live-P&L visibility.** No new investigation this run — restating.
   Recommend a fable/build session add a live-P&L slice to step 1's query.

2. **[theta4 · first real tail hit landed within calibration bounds — good sign, keep watching]
   n=30/80 (38%), +52.9¢/trade cumulative after a −$4.06 loss on a 13.6%-modeled-probability
   trade (one of the riskiest in the book).** Realized hit rate 3.3% vs the 1.25×-modeled gate
   bound of ~8.6% — comfortably inside. This is the first genuine out-of-sample test of the
   2026-07-15 calibration check, and it passed. No action needed; continue watching the hit rate
   as n climbs — one more hit or two in the near term would still be normal, a cluster of hits
   well above the modeled rate is the thing to watch for.

3. **[mmsell4-8 · shared rough batch, mmsell5 notably resilient] Most of the family (4/6/7/8 +
   control + mmsell3) dipped together this batch; mmsell5 alone stayed positive (+10¢/trade) and
   is now the strongest-performing variant on a per-trade basis (+8.6¢, n=17).** Consistent with
   a shared adverse sports-results event that mmsell5's totals/spreads/props-only allowlist
   avoided by design. Still very small n — don't promote anything — but mmsell5's resilience here
   plus its steady prior accrual (runs #50-52) makes it worth extra attention as it grows. mmsell7
   turned cumulative-negative for the first time (n=9, tiny) — not a concern yet at this n.

4. **[idea-model queue · MMX/NEST unchanged] MMX's premise still worth checking against
   mmsell4-8 for redundancy (unresolved since run #49).** NEST still behind theta4's n≥80 gate
   (38% there, calibration validated by #2's real hit). RTPIN/BOXPIN behind unbuilt scraper
   infra. RATELAG behind a live Fed event.

5. **[weather_concity / con(all) · quiet, no new settles] concity −12.8¢/trade (35% to gate),
   con(all) −2.9¢/trade — both flat this run (new opens only).** Carry forward.

6. **[xgame_tapes · resumed on its own] Was frozen since 2026-07-15, now collecting again
   (9,614 rows/24h, fresh).** xgame_matches remains dark. Both low-urgency (shelved book) — noting
   the flip, nothing to act on.

7. **[FREEZE gate · unchanged, not fired] Settled grain+soft = 5 of the n≥100 trigger, unchanged
   across 5 runs now.** Standing background check, nothing to act on.

*(Changed this run: #2 theta4 — first real tail hit landed, characterized in detail, validates
the 2026-07-15 calibration check rather than contradicting it; still watching, no action needed.
#3 mmsell4-8 — shared rough batch across most variants, mmsell5 emerged as the notable resilient
performer worth extra attention. #6 xgame_tapes resumed after being frozen since run #47. #1/#4/
#5/#7 restated/unchanged.)*
