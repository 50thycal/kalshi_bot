# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 4-hourly status loop (`kalshi_loop_checker_phase_3` skill).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-04 11:14 PM CDT (run #17) — **fable branch DEPLOYED**

Merged + deployed ~10:54 PM CDT: **mmsell1/mmsell2 revision books are now trading**, and
the weather prune is live (con-only going forward).

**Trading books (settled n / P&L / open):**
| book | n | P&L | open | note |
|---|---|---|---|---|
| mmsell (control) | 459 | +$5.78 | 33 | +1.3¢/trade |
| **mmsell1** (5-20¢) | 0 | — | **19** | **NEW — cheap-longshot variant live, no settles yet** |
| **mmsell2** (10-20¢) | 0 | — | **12** | **NEW — peak-band variant live** |
| theta (control) | 93 | −$11.90 | 2 | drifted down overnight |
| theta1 / theta2 / theta3 | 7 / 2 / 32 | −$4.29 / −$3.79 / −$6.90 | 0/0/2 | all down this window (crypto session) |
| weather con | 228 | +$10.82 | 14 | the keeper |
| weather (rest) | 4,659 | −$235.77 | 50 | legacy opens holding to settlement; **no new entries since deploy** |

**Data collection (last-24h / latest CDT):** crypto_spot 2,878 (11:13 PM ✓), ladder 61,440
(11:14 PM ✓, 100% model-priced), forecasts/obs/ensembles/buckets all ✓ within minutes.
**xgame_matches 0 / xgame_tapes 0** — matcher still makes no pairs (known bug, kal=14/pm=169).

**Research probes (on-demand, verdicts in RESEARCH_JOURNAL):** TFAV (`kalshi_favbuy_study`) ·
WCPROP (`xmarket_wc`) · XGAME (`xgame_tape_study`) — not continuous books by design.

**Headline:** the deploy landed — mmsell1 (19 open) + mmsell2 (12 open) are live and the
weather program is pruning to con-only. The mmsell-variant A/B and the weather-prune
confirmation are both now *running*; first reads in the next 1-2 runs. theta family slid
overnight but all revisions are still pre-gate (theta3 32/60). All collectors fresh; XGAME
matcher still the one broken thing.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell · NEW A/B live] Watch mmsell1/mmsell2 vs the control as they settle.** Both
   opened immediately (19 + 12) — the cheap-longshot thesis predicts they beat the control's
   +1.3¢/trade. Judge at ~150 settled each; the in-sample decomposition said 5-20¢ = +2.7 to
   +3.6¢/ct, so anything clearly above the control forward validates it.

2. **[weather · prune confirmation pending] Verify con-only at the next weather window.** No
   new weather entries since the 10:54 PM deploy (expected — off-window). Next run should show
   `con` getting fresh entries while weather-other stays flat (its 50 open just settle out).
   If any non-con weather book takes a NEW position after the deploy, a flag/env override
   slipped through — but the env check was clean, so this is a confirm-not-worry.

3. **[XGAME · real bug] Matcher makes 0 pairs from kal=14 / pm=169 games.** The one broken
   collector. Fable session: check team-name normalization across venues + the Kalshi
   ticker-derived game-day vs PM's "on YYYY-MM-DD". Until it pairs, no tape accrues.

4. **[theta · IN FLIGHT] Revisions still pre-gate (theta3 32/60); all slid overnight.**
   Variance, not verdict. Control (93) continues negative/miscalibrated. Hold; judge at the gate.

*(Resolved: "deploy-pending" from run #16 — the branch is merged and mmsell1/2 + the prune are
live. Added #1 mmsell A/B watch + #2 prune-confirmation.)*
