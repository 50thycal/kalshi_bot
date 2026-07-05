# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 4-hourly status loop (`kalshi_loop_checker_phase_3` skill).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-05 03:14 AM CDT (run #18) — both fable changes confirmed working

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 490 | +$7.19 | +1.5 | 19 | baseline |
| **mmsell1** (5-20¢) | 17 | **+$0.87** | **+5.1** | 14 | first settles — ahead of control |
| **mmsell2** (10-20¢) | 11 | **+$1.56** | **+14.2** | 7 | first settles — well ahead (tiny n) |
| theta (control) | 102 | −$9.73 | — | 2 | recovered +$2.2 |
| theta1 / theta2 | 7 / 2 | −$4.29 / −$3.79 | — | 0/0 | idle |
| **theta3** (wide, ×1.25) | 40 | **−$0.18** | — | 2 | **bounced +$6.7 → ~breakeven at 40/60** |
| weather con | 228 | +$10.82 | +4.1 | 14 | healthy (see below) |
| weather (rest) | 4,659 | −$235.77 | — | 50 | pruned; 50 legacy opens settling out |

**Weather prune — CONFIRMED, con healthy.** Zero weather entries (con OR pruned) since the
10:54 PM deploy → no pruned book is trading. con's 10.3h quiet is normal: its max historical
gap is **24.7h** (avg 1.69h, 33 entries in the last 3 days), so it's just off-window overnight
and will fire at its next qualifying setup. The weather program is now genuinely con-only.

**mmsell A/B — first settlements favor the variants.** mmsell1 +5.1¢/trade and mmsell2
+14.2¢/trade both clear the control's +1.5¢, directionally exactly as the cheap-longshot
decomposition predicted. n is tiny (17 / 11) — not a verdict, but the right sign + magnitude.

**Data collection (last-24h / latest CDT):** crypto_spot 2,876 (03:12 AM ✓), ladder 61,680
(03:13 AM ✓, 100% model-priced), forecasts/obs/ensembles/buckets ✓ within minutes.
**xgame_matches 0 / tapes 0** — matcher still 0 pairs (known bug, kal=14/pm=169).

**Research probes (on-demand, verdicts in RESEARCH_JOURNAL):** TFAV · WCPROP · XGAME.

**Headline:** the fable changes are validated live — mmsell variants trading and early-beating
the control, weather cleanly pruned to a healthy con. theta3 bounced from −$6.90 to −$0.18 at
40/60 (the variance the ≥60 gate exists for). Only broken thing: the XGAME matcher.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[mmsell A/B · early-positive] Variants leading the control on first settles.** mmsell1
   +5.1¢, mmsell2 +14.2¢ vs control +1.5¢/trade — the cheap-longshot thesis showing forward.
   Hold to ~150 settled each before promoting; the sign is right, the n isn't there yet.

2. **[theta · IN FLIGHT — theta3 the one to watch] theta3 at 40/60, ~breakeven after its
   bounce.** The −$5..−$7 reads were variance; it recovered +$6.7 in 8 trades. Let it (and the
   control, 102 and still negative) reach the gate before judging. theta1/theta2 near-idle.

3. **[XGAME · real bug] Matcher makes 0 pairs from kal=14 / pm=169 games.** Fable fix: team-
   name normalization across venues + Kalshi ticker-derived game-day vs PM "on YYYY-MM-DD".

4. **[weather · resolved] Prune confirmed + con healthy** — no action; con trades at its next
   window (verified normal cadence, max gap 24.7h).

*(Resolved: run #17's "prune confirmation pending" — now confirmed, con verified healthy.
Updated #1 with the first-settle A/B reads; #2 reframed around theta3's bounce.)*
