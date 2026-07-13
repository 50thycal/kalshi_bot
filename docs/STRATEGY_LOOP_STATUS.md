# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the strategy status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06, then to fixed 5:30 AM / 12:00 PM / 8:00 PM CT on 2026-07-12).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-13 12:06 PM CDT (run #40)

**✅ RECOVERED — the bot is back up and healthy.** `bot_runs` is completing normally right now
(runs every ~70-80s, all `status=completed`), and every collector is fresh as of this run. The
outage flagged in run #39 (CRASHED since ~9:31 PM CDT 7/12 on a Kalshi 401 auth error) is over —
recovery happened sometime between run #39 (5:31 AM CDT) and now; the earliest fresh activity
found is theta4's new trade at **~8:43 AM CDT**, so treat that as the approximate recovery point
(~6.5-7h outage total). **No further action needed on the outage itself** — but see the new #7
below: not everything came back.

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **pin15** | 152 | −$10.10 | **−6.6** | 0 | **GATE REACHED (n≥150) — clear KILL verdict.** This batch (n=19) ran −24.8c/trade |
| mmsell3 (5-10c) | 311 | +$5.21 | +1.7 | 13 | back above +1.5c bar but now trails mmsell2 — still doesn't clear its full gate |
| mmsell2 | 897 | +$15.78 | +1.8 | 16 | now the best of the mmsell family, ahead of mmsell3 |
| mmsell1 | 1,372 | +$19.88 | +1.5 | 25 | |
| mmsell (control) | 2,305 | +$22.24 | +1.0 | 37 | |
| **weather_concity** | 21 | −$3.89 | **−18.5** | 1 | rough batch (n=7, −44.4c/trade) — still n=21/120 |
| weather con (all) | 355 | −$7.61 | −2.1 | 5 | also a rough batch (n=16, −32.9c/trade) — same settlement window as concity |
| theta4 (fat-tail) | 4 | +$2.99 | — | 0 | +1 trade, still noise (n=4/80) |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet, unchanged |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet, unchanged |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — pin15's gate resolved this run: KILL.** At n=152 (past the n≥150 gate), pin15 is
running **−6.6¢/trade cumulative**, with this batch alone at −24.8¢/trade. The pre-registered
rule (`docs/PIN15_THESIS.md`, `BOOK_REGISTRY.md`) was: keep only if per-trade **> +1.5¢** AND
profit concentrates in T≈120-180s entries. It fails the first leg outright — there's no positive
P&L to even check for T-window concentration. **This is not ambiguous; recommend a fable session
formally retire pin15** (stop entries, keep the book/data for the record per the registry's own
provenance note about not silently reverse-engineering history).

mmsell3 recovered to +1.7¢/trade (above its own +1.5¢ bar again) on a strong post-outage batch
(+3.7¢/trade, n=26) — but **mmsell2 pulled ahead to +1.8¢/trade**, so mmsell3 no longer clears
the "beats mmsell1 AND mmsell2" leg of its own gate. Which of the two mmsell variants is "ahead"
has now flipped at least twice across recent runs — reinforces run #38's read that this edge is
too thin for single-run swings to resolve cleanly. Still hold, still don't promote.

weather_concity and weather_con(all) both had a rough batch in the same window (con: −32.9¢/trade
on 16 trades; concity: −44.4¢/trade on 7 trades) — likely the same adverse weather settlement
hitting both books (they share underlying markets), not book-specific. concity is still only
17.5% to its own n≥120 gate; too early to read.

**Gate sweep (step 3b):** pin15 **152/150 — RESOLVED, KILL** · mmsell3 **311/150** (n-gate long
cleared, performance leg flipping run to run, still hold) · theta4 **4/80** · weather_concity
**21/120** (17.5%).

**Data (last-24h / latest CDT):** crypto_spot 2,872 (12:01 PM ✓), crypto_ladder 36,720 (12:02 PM
✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (11:59 AM–12:04 PM ✓).
**xgame_matches and xgame_tapes did NOT recover with everything else** — both still show **zero
rows in the last 24h** and an unchanged `latest` timestamp from before the outage (2026-07-12).
Every other collector resumed cleanly on the same restart; xgame specifically staying dark is a
new, real finding now that the outage explanation no longer covers it. Book is shelved/killed so
not urgent, but worth a quick check that the xgame collector itself didn't silently die separately
from the crash (it may need its own restart/config check).

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** ✅ Bot recovered (~6.5-7h outage, back to normal ~8:43 AM CDT). pin15's gate resolved
to a clean KILL (n=152, −6.6c/trade). mmsell3 back above +1.5c but now trails mmsell2 — still
hold. weather books both had a rough shared-market batch. xgame collectors did NOT resume with
everything else — new, separate, low-urgency finding.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[pin15 · GATE RESOLVED — recommend formal KILL] n=152 (past n≥150), −6.6c/trade cumulative,
   this batch −24.8c/trade.** Clearly fails the pre-registered keep-bar (>+1.5c AND T-window
   concentration) — no ambiguity, nothing to wait on. **Recommended: a fable session formally
   retires pin15** (disable entries; keep the book and its data for the record, per the
   registry's own provenance principle). This is the loop's first clean KILL-side gate
   resolution since the registry existed (mirrors mmsell3's clean-PROMOTE near-miss).

2. **[Outage — RESOLVED, no action needed] Bot was CRASHED ~9:31 PM CDT 7/12 to ~8:43 AM CDT
   7/13 (~6.5-7h) on a Kalshi 401 auth error.** Confirmed recovered: `bot_runs` completing
   normally, all collectors fresh. Whatever credential fix or auto-recovery happened, it worked —
   no further loop action, dropping this from "urgent" back to informational. If anyone knows
   what specifically fixed the auth (key rotation, Kalshi-side fix, manual redeploy), worth a
   one-line note in `RESEARCH_JOURNAL.md` for the record, but that's optional.

3. **[mmsell3 · still hold, lead over mmsell1/2 keeps flipping] +1.7c/trade at n=311** — back
   above its own +1.5c bar, but mmsell2 (+1.8c) now leads it, so the "beats mmsell1 AND mmsell2"
   leg fails. This has flipped across at least 3 recent runs — treat the edge as real but too
   thin for any single run to resolve cleanly. Do NOT promote; consider whether the fable
   session wants to just let this run longer before revisiting, since the n-gate is long past
   and the performance answer isn't stabilizing quickly.

4. **[idea-model queue · MMX — still "recheck before building"] mmsell3's n≥150 trigger is long
   past, but #3 shows the performance leg still hasn't stabilized.** MMX
   (`IDEA_MODEL_20260710_run2.md`) stays "recheck before building," not a green light — if
   anything, mmsell3's persistent instability at n>300 is itself useful signal that this whole
   maker-sell edge family may just be thin, which MMX should account for before committing build
   time. NEST still behind theta4 (n=4/80, far off). RTPIN/BOXPIN behind unbuilt scraper infra.
   RATELAG behind a live Fed event.

5. **[weather_concity · WATCH — rough batch, still early] 21 settled −$3.89 (−18.5c cum), this
   batch (n=7) −44.4c/trade** — same settlement window hit weather con(all) too (−32.9c/trade,
   n=16), suggesting a shared adverse market move rather than a concity-specific problem. Gate:
   n≥120 (17.5% there), keep only if it beats all-city con. Too early and too correlated with
   con's own move to read meaningfully; carry forward.

6. **[theta4 · unchanged pace, still noise] 4 trades, +$2.99 (n=4/80).** Gate: keep only if
   per-trade > 0 AND realized-tail-hit ≤ 1.25x modeled. Still accruing very slowly; if still
   <~10 by run #42, revisit the loosen-edge idea to get a testable n.

7. **[NEW · xgame collectors did not resume with the rest of the bot] `xgame_matches` and
   `xgame_tapes` show zero rows in the last 24h and an unchanged pre-outage `latest` timestamp,
   while every other collector recovered cleanly on the same restart.** Book is shelved/killed
   already so this is low-urgency, but it's a real, separate finding now that the outage
   explanation (run #39) no longer covers it — worth a quick check whether the xgame collector
   needs its own restart or has a config/credential issue distinct from the main auth fix.

8. **[mmsell existing · context] control/mmsell1/mmsell2 ~breakeven-positive** (+1.0c/+1.5c/
   +1.8c). mmsell2 is now nominally the best-performing variant in the family — worth keeping in
   view alongside mmsell3 rather than treating mmsell3 as the sole "improvement candidate."

*(Changed this run: NEW #1 — pin15's gate RESOLVED to a clean KILL (n=152, −6.6c/trade),
recommend formal retirement. #2 — outage from run #39 CONFIRMED RESOLVED, downgraded from urgent
to informational. #3 mmsell3 recovered above +1.5c but now trails mmsell2 — the lead keeps
flipping, still hold. #7 NEW — xgame collectors did not resume with the rest of the bot, a fresh
low-urgency finding now that the outage no longer explains it. #5/#6 continue accruing, #4/#8
updated context.)*
