# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 4-hourly status loop (`kalshi_loop_checker_phase_3` skill).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-05 07:14 AM CDT (run #19)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 493 | +$6.89 | +1.4 | 24 | baseline |
| **mmsell1** (5-20¢) | 18 | +$1.08 | **+6.0** | 18 | still ahead of control |
| **mmsell2** (10-20¢) | 12 | +$1.77 | **+14.8** | 11 | still well ahead (tiny n) |
| theta (control) | 112 | −$11.69 | — | 1 | persistently negative |
| theta3 (wide, ×1.25) | 47 | −$3.17 | — | 1 | gave back the bounce; **47/60**, near gate |
| theta1 / theta2 | 7 / 2 | −$4.29 / −$3.79 | — | 0/0 | idle |
| weather con | 242 | +$7.25 | +3.0 | 3 | **entered post-deploy — alive**; dipped this batch |
| weather (rest) | 4,709 | −$238.63 | — | **0** | **pruned books fully wound down (50→0 open)** |

**Weather prune — DONE.** con entered new positions after the deploy (latest 06:59 AM CDT;
242 settled, up from 228) → definitively alive and trading. The pruned books' 50 open
positions all settled out to **0 open** with no new entries → they can never grow again. The
weather program is now cleanly con-only. Caveat: con's last ~14 settles netted −$3.57 (pooled
+$10.82 → +$7.25, still +3.0¢/trade) — a normal down-batch, not a concern at this n.

**mmsell A/B — variants still leading.** mmsell1 +6.0¢/trade, mmsell2 +14.8¢/trade vs control
+1.4¢. n creeping up (18 / 12). Consistent with the cheap-longshot thesis; hold to ~150.

**theta — revisions nearing the gate.** theta3 at 47/60 gave back its bounce (−$0.18 → −$3.17,
variance); control (112) stays negative. ~13 trades to theta3's evaluation gate.

**Data (last-24h / latest CDT):** crypto_spot 2,874 (07:11 AM ✓), ladder 61,920 (07:11 AM ✓,
100% model-priced), forecasts/obs/ensembles/buckets ✓. **xgame 0/0** — matcher still broken.

**Research probes (on-demand):** TFAV · WCPROP · XGAME (verdicts in RESEARCH_JOURNAL).

**Headline:** weather prune fully complete + con confirmed live; mmsell variants keep beating
the control; theta3 near its gate (variance-bound). Only broken thing: the XGAME matcher.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell A/B · early-positive] Variants still ahead** (+6.0 / +14.8¢ vs +1.4¢). Hold to
   ~150 settled each before promoting; sign is consistent across two runs now.

2. **[theta · gate approaching] theta3 47/60.** Let it (and control, 112, still negative)
   reach ≥60 before judging per the pre-registered rule (positive AND realized-tail ≤ modeled).
   theta1/theta2 near-idle — likely closed for sparsity at the gate.

3. **[XGAME · real bug] Matcher makes 0 pairs from kal=14 / pm=169 games.** Only broken piece.
   Fable fix: team-name normalization + Kalshi ticker game-day vs PM "on YYYY-MM-DD".

4. **[weather · resolved/monitor] Prune complete, con live.** No action; just note con's small
   down-batch this run (−$3.57 / 14) — watch it stays net-positive as its own n grows.

*(Resolved: run #18's prune-confirmation — con has now entered post-deploy and the pruned
books are fully wound down to 0 open. Nothing new added.)*
