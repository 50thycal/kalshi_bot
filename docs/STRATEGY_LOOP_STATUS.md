# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-14 05:36 AM CDT (run #42)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell2** | 958 | +$20.99 | **+2.2** | 9 | pulled a real lead over mmsell3 this run (not noise-margin) |
| mmsell3 (5-10c) | 374 | +$7.02 | +1.9 | 7 | still clears its own +1.5c bar, but mmsell2's lead is now a real gap |
| mmsell1 | 1,464 | +$25.36 | +1.7 | 14 | |
| mmsell (control) | 2,433 | +$30.33 | +1.2 | 25 | |
| **pin15** | 236 | −$10.88 | **−4.6** | 0 | 2nd positive batch in a row (+1.6c/trade), cumulative still clearly negative |
| weather_concity | 21 | −$3.89 | −18.5 | 7 | no change at all since run #41 |
| weather con (all) | 355 | −$7.61 | −2.1 | 16 | +1 new open, no new settles |
| **theta4** (fat-tail) | 4 | +$2.99 | — | 0 | still n=4, **revisit trigger from run #41 is now DUE** |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — mmsell2 pulls a real lead over mmsell3 (not the noise-margin flip of the last 4
runs); pin15 posts a 2nd straight positive batch; theta4's revisit trigger is now due.**

**mmsell2 vs mmsell3:** last run I called this "statistically tied" after 4 straight flips at a
~0.01c margin. This run mmsell2 is at **+2.2c/trade** vs mmsell3's **+1.9c** — a **0.31c gap**,
clearly outside the noise band that was flipping the sign run to run. mmsell3 still clears its
own +1.5c keep-bar on its own, but the "beats mmsell1 AND mmsell2" leg of its gate now fails by a
real margin, not a coin-flip one. This is worth a fresh look rather than assuming last run's
"tied" read still holds — the picture just moved.

**pin15** posted its **second consecutive positive batch** (+1.6c/trade this batch, following
run #41's +1.6c... wait, run #41's cumulative improved via a −3.9c/trade batch; this run's batch
is actually **positive**, +1.6c/trade on 45 trades) pulling cumulative from −6.1c to **−4.6c/trade**
at n=236. Still decisively below the +1.5c keep-bar and the KILL recommendation from run #40
stands — but two batches of genuine improvement in a row is worth noting plainly rather than
mechanically repeating "still KILL" unchanged. Not reversing the call; flagging the trend.

**theta4's revisit trigger (set in run #41) is now due:** still only n=4, unchanged since run
#40 (no new settles in two full runs). Per the standing suggestion, this is the point to surface
the "loosen the edge or conclude" decision to a fable session rather than let it keep sliding.

**Gate sweep (step 3b):** pin15 **236/150** (KILL verdict holds, improving trend noted) · mmsell3
**374/150** (own bar cleared, now clearly behind mmsell2) · theta4 **4/80** (revisit due) ·
weather_concity **21/120** (17.5%, unchanged).

**Data (last-24h / latest CDT):** crypto_spot 2,873 (05:33 AM ✓), crypto_ladder 54,557 (05:33 AM
✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (05:15–05:35 AM ✓).
xgame_tapes 11,726 (05:35 AM ✓, fully recovered). **xgame_matches: now 4 consecutive runs frozen
at the identical timestamp (2026-07-12 10:18:09 UTC, ~72h stale), 0 rows in 24h.** Worth
clarifying: this predates the run #39 crash entirely (it was already flagged stale back in run
#38, before the outage even started) — this is a **separate, long-standing, likely permanently
broken** collector, not an outage symptom. Book is shelved/killed so still low-urgency, but it's
been dark for 3+ days now, not hours.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell2 now clearly ahead of mmsell3 (real gap, not noise) — worth a fresh look, not
a repeat of "tied." pin15's 2nd straight positive batch narrows its cumulative loss but the KILL
verdict still holds. theta4's revisit-the-edge decision is now due. xgame_matches has been dark
72h+, predates the crash, likely permanently broken (low priority, book already shelved).

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[pin15 · KILL verdict holds, but 2nd straight positive batch — note the trend] n=236,
   −4.6c/trade cumulative (was −6.1c), this batch +1.6c/trade** (2nd positive batch running,
   following run #41's improvement too). **Recommendation unchanged: still recommend formal
   retirement** — cumulative is still decisively below the +1.5c bar at 236/150 of its gate.
   But two improving batches in a row is real enough to flag: if a fable session is going to look
   at this anyway, it's worth checking whether the recent trades cluster in the T≈120-180s window
   the original thesis called for (`docs/PIN15_THESIS.md`) — if the recent improvement is
   T-window-driven, that's actual signal about *when* the edge might work, not just noise.

2. **[mmsell2 vs mmsell3 · REOPENED — real gap this run, not noise] mmsell2 +2.2c/trade (n=958)
   vs mmsell3 +1.9c/trade (n=374) — a 0.31c gap**, clearly bigger than the ~0.01c flip-margin
   that made runs #38-#41 call this "tied." Un-flag the "settled-tied" read from run #41 — this
   needs fresh eyes rather than an assumption it stays tied. mmsell3 still clears its own +1.5c
   bar solo but no longer "beats mmsell1 AND mmsell2" by a comfortable margin.

3. **[theta4 · REVISIT TRIGGER DUE] Still n=4/80, unchanged for two full runs (no new settles
   since run #40).** Per the standing note: this is the point to surface "loosen theta4's edge
   or conclude the family is dead" to a fable session — accrual at this rate will take
   effectively forever to reach n≥80. Gate: keep only if per-trade > 0 AND realized-tail-hit ≤
   1.25x modeled — currently untestable at n=4.

4. **[idea-model queue · MMX — recheck given #2's reopened comparison] mmsell3's n≥150 trigger
   is long past, but which mmsell variant is actually "ahead" just changed materially (#2).**
   MMX (`IDEA_MODEL_20260710_run2.md`, extend the 5-10c maker-sell into new categories) should
   be scoped against whichever variant proves out, not assumed to be "the mmsell3 edge"
   specifically — this keeps moving, so hold off finalizing MMX's design until the mmsell
   family's internal ranking settles for a few runs. NEST still behind theta4 (#3). RTPIN/BOXPIN
   behind unbuilt scraper infra. RATELAG behind a live Fed event.

5. **[weather_concity · WATCH, fully quiet] 21 settled −$3.89, unchanged since run #41 (literally
   no new opens or settles).** Gate: n≥120 (17.5% there). Nothing to read; carry forward.

6. **[xgame_matches · long-standing, NOT crash-related — clarify] 4 consecutive runs frozen at
   the identical timestamp (2026-07-12 10:18:09 UTC), now ~72h stale, 0 rows in 24h.** This
   predates run #39's crash by over a day — it's a separate, likely permanently-broken collector,
   not an outage symptom. `xgame_tapes` (the other half) fully recovered and is healthy. Book is
   shelved/killed, so still low-urgency; just correcting the framing from "recovering" to
   "probably needs an actual fix if anyone ever revisits xgame."

7. **[mmsell existing · context, updated] control/mmsell1 ~breakeven-positive (+1.2c/+1.7c).**
   mmsell2 is now the clear top performer in the family (see #2) — reverse of the "tied" framing
   from last run.

*(Changed this run: #1 pin15 — 2nd straight positive batch noted, KILL recommendation unchanged
but trend flagged, added a T-window check suggestion. #2 mmsell2-vs-mmsell3 — REOPENED from
"settled-tied" (run #41) to "real gap this run, needs fresh eyes," since the margin (0.31c) is
now well outside the noise band that drove the earlier flips. #3 theta4 — revisit trigger now
DUE, escalated per the standing instruction. #4 MMX — updated to reflect the reopened mmsell
ranking. #6 xgame_matches — reframed from "recovering" to "long-standing, not crash-related,
probably permanently broken." #5/#7 otherwise unchanged/updated context.)*
