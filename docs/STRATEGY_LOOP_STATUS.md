# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-14 12:05 PM CDT (run #43)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **theta4** (fat-tail) | 16 | +$11.21 | **+70.1** | 0 | **broke out of its stall** — n went 4→16 in one run, this batch alone +68.5c/trade |
| **pin15** | 273 | −$3.02 | **−1.1** | 0 | **3rd straight positive batch** (+21.2c/trade) — cumulative loss nearly gone |
| mmsell2 | 965 | +$22.05 | +2.3 | 19 | still the clear family leader |
| mmsell3 (5-10c) | 376 | +$7.14 | +1.9 | 16 | flat this run, gap to mmsell2 held/widened slightly |
| mmsell1 | 1,474 | +$26.74 | +1.8 | 24 | |
| mmsell (control) | 2,452 | +$32.97 | +1.3 | 38 | |
| weather con (all) | 370 | −$6.06 | −1.6 | 4 | positive batch (+10.3c/trade), recovering |
| weather_concity | 28 | −$5.04 | −18.0 | 1 | another negative batch (−16.4c/trade), still n=28/120 |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — two reversals worth leading with: theta4 finally started accruing with a big edge,
and pin15's KILL case has weakened sharply.**

**theta4** went from stuck at n=4 (flagged as revisit-due last run) to **n=16** in a single run —
12 new trades, all strongly positive (+68.5c/trade this batch, +70.1c/trade cumulative). This
resolves last run's "loosen the edge or conclude" trigger by data rather than by decision: it
just started trading again at a healthy pace. **Caveat: the pre-registered gate is per-trade > 0
AND realized-tail-hit ≤ 1.25x modeled** — the magnitude here is well past the ">0" bar, but
nobody has checked the tail-hit-ratio half of the criterion yet, and this book exists specifically
*because* raw P&L can look great right up until a tail event hits (that's the whole class of risk
theta was built to study). Worth a drill-down before getting excited, not just watching n climb to
80.

**pin15's cumulative loss has nearly disappeared:** −1.1¢/trade at n=273, after a **third straight
positive batch** (+21.2¢/trade this run, following +1.6¢ and +1.6¢ the last two runs). Three runs
ago this loop was recommending formal retirement at −6.6¢/trade; the trend has now pulled
cumulative to within a hair of breakeven. **Revising the standing recommendation:** holding off on
retiring pin15 is now clearly the right call — if this trend continues one more run, cumulative
could cross positive, which would be a genuinely different story than the one that drove the
original KILL call. Not a promote signal yet (still negative, still below the +1.5¢ keep-bar), but
the KILL urgency from runs #40-#42 no longer reflects the data.

**mmsell family:** essentially unchanged from run #42 — mmsell2 (+2.3¢) still clearly ahead of
mmsell3 (+1.9¢), gap holding. weather_con(all) had a positive batch (+10.3¢/trade); weather_concity
had another negative one (−16.4¢/trade) — the two continue to move somewhat independently despite
sharing underlying markets.

**Gate sweep (step 3b):** theta4 **16/80** (20%, huge early edge — verify calibration) · pin15
**273/150** (past gate, verdict now genuinely uncertain, was KILL) · mmsell3 **376/150** (own bar
cleared, behind mmsell2) · weather_concity **28/120** (23%).

**Data (last-24h / latest CDT):** crypto_spot 2,880 (12:04 PM ✓), crypto_ladder 58,157 (12:04 PM
✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (12:03–12:04 PM ✓).
xgame_tapes 18,401 (12:03 PM ✓, healthy). **xgame_matches: 5th consecutive run frozen at the
identical timestamp** (2026-07-12 10:18:09 UTC, now ~103h / 4+ days stale) — unchanged, long-
standing, not crash-related (already established run #42), no new action needed, just noting
continuation.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** theta4 broke its stall hard (n=4→16, +70c/trade) — verify tail-calibration before
trusting the magnitude. pin15's KILL case has weakened sharply (3rd positive batch, −1.1c/trade
cumulative) — recommend holding off on retirement, reassess next run. mmsell2 still leads mmsell3.
weather books moved in opposite directions this batch. xgame_matches still dark, unchanged.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta4 · REVISIT TRIGGER RESOLVED BY DATA — now verify calibration, not just wait] n=16
   (was 4), +70.1c/trade cumulative, this batch +68.5c/trade — a real breakout, not noise at this
   magnitude.** The "loosen the edge or conclude" question from run #42 is moot; it's trading
   again. **New recommendation: before treating this as good news, check the realized-tail-hit
   ratio against the model** (`docs/THETA_THESIS.md`'s gate: keep only if per-trade > 0 AND
   tail-hit ≤ 1.25x modeled) — raw per-trade P&L alone doesn't verify the half of the gate that
   actually matters for a fat-tail-sell book. A quick ops drill-down (settled theta4 trades vs
   their modeled tail probability) would resolve this cheaply.

2. **[pin15 · KILL RECOMMENDATION WITHDRAWN — reassess, don't retire yet] n=273, −1.1c/trade
   cumulative (was −4.6c, −6.1c, −6.6c the three runs before) — 3rd straight positive batch
   (+21.2c/trade this run).** This reverses three runs of "recommend formal retirement." **Do
   NOT retire pin15 based on the run #40 recommendation** — the data has moved too far since then.
   Still below the +1.5c keep-bar, so not a promote signal either; watch 1-2 more runs. If it
   crosses positive, check whether the recent trades concentrate in the T≈120-180s window the
   original thesis specified (`docs/PIN15_THESIS.md`) — that would confirm the mechanism, not
   just a lucky run.

3. **[mmsell2 vs mmsell3 · unchanged from run #42, gap holding] mmsell2 +2.3c/trade (n=965) vs
   mmsell3 +1.9c/trade (n=376)** — same real gap as last run, nothing new to resolve. mmsell3
   still clears its own +1.5c bar solo but doesn't beat mmsell1 AND mmsell2 together.

4. **[idea-model queue · MMX — still holding for the mmsell ranking to settle] No change from
   run #42** — mmsell2 remains the family's real leader for a second straight run, which is
   itself a small step toward "settled" rather than still-moving. `IDEA_MODEL_20260710_run2.md`.
   NEST behind theta4 — **note theta4 just started moving (#1)**, so NEST's blocker may resolve
   faster than expected if theta4's calibration checks out. RTPIN/BOXPIN behind unbuilt scraper
   infra. RATELAG behind a live Fed event.

5. **[weather_concity · WATCH, another negative batch] n=28 (23% to gate), −$5.04 cumulative
   (−18.0c/trade), this batch −16.4c/trade — the third batch running that's been negative or
   flat while con(all) itself had a positive batch this time.** The two books are diverging batch
   to batch rather than moving together, worth remembering when concity's gate resolves at n≥120
   — its independent-of-con performance is exactly the question the gate asks.

6. **[xgame_matches · unchanged, still long-standing] 5th consecutive run frozen at the same
   timestamp, now 4+ days stale.** No new information; established as a pre-existing, likely
   permanently broken collector in run #42. Book shelved/killed, still low-urgency.

7. **[mmsell existing · context, unchanged] control/mmsell1 ~breakeven-positive (+1.3c/+1.8c).**
   No change from run #42's framing.

*(Changed this run: #1 theta4 — MAJOR reversal, broke its stall (n=4→16, +70c/trade); revisit
trigger resolved by data, new suggestion to verify tail-calibration rather than just watch n
climb. #2 pin15 — MAJOR reversal, 3-run KILL recommendation formally WITHDRAWN as cumulative
nearly reached breakeven (−1.1c/trade); reassess in 1-2 runs rather than retire. #4 MMX — noted
theta4's blocker may resolve sooner than expected. #3/#5/#6/#7 otherwise unchanged from run #42.)*
