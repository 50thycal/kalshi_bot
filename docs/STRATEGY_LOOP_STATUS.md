# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-09 07:18 PM CDT (run #30)

**✅ PORTFOLIO CLEANUP IS LIVE (PR #22 merged + deployed + verified).** All four
decided-against books are shelved/killed and have wound DOWN TO 0 OPEN. collect-only is
confirmed working in the loop's own data: theta made its last entry at 3:11 PM CDT (pre-deploy)
and has opened nothing since, while `crypto_ladder` + `crypto_spot` keep advancing (fresh 7:15
PM) — the collector survived the shelve. Only mmsell + weather_con remain active.

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 1,582 | +$2.99 | +0.2 | 32 | ACTIVE — breakeven data book |
| mmsell1 (5-20¢) | 835 | +$7.39 | +0.9 | 23 | ACTIVE — best mmsell band |
| mmsell2 (10-20¢) | 546 | +$3.96 | +0.7 | 13 | ACTIVE |
| weather con | 294 | −$1.24 | −0.4 | 13 | ACTIVE — diagnosed (city bleed), unchanged P&L |
| theta (control) | 560 | +$0.97 | +0.2 | **0** | SHELVED — wound down; last entry 3:11 PM |
| theta1 | 201 | +$9.69 | +4.8 | **0** | SHELVED — revival candidate (fresh pre-reg only) |
| theta2 / theta3 | 98 / 134 | −$11.55 / −$11.62 | — | 0/0 | SHELVED |
| tfav | 215 | −$7.54 | −3.5 | **0** | KILLED — wound down; last entry 1:11 PM |
| wcprop / xgame | 0 / 0 | — | — | 0/0 | KILLED/SHELVED — never traded |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |
| buy_favorite / momentum / reversion | 0 | 0 | — | 0 | dormant legacy |

**HEADLINE — the cleanup landed cleanly; now the honest part: no live positive earner.** The
shelve/kills stopped the paper bleed and the shelved books are all at 0 open with fresh
collectors behind them. But the surviving portfolio has **no book currently netting positive**:
mmsell is ~breakeven (+0.2 to +0.9¢/trade across bands) and weather_con is −$1.24 cumulative.
Against the +$100/month north star, the next work is offense, not pruning: the clearest path
back to a positive earner is **restricting weather_con to the cities where it actually has edge**
(MIA/PHIL were positive; DEN/NY were 0% win — see run #29 diagnosis). That is now suggestion #1.

**Shelve verification (loop-confirmed).** theta 0 open (last entry 20:11 UTC / 3:11 PM), tfav 0
open (18:11 UTC / 1:11 PM), theta1/2/3 all 0 open; wcprop + xgame have no book rows at all. The
18 theta / 5 tfav settled since run #29 are just the pre-deploy open positions closing out, not
new trades. `theta1` finished at **+$9.69 (+4.8¢/trade, n=201)** — the one revision that ended
net-positive, and the only theta the post-mortem flags as a possible future revival (new
pre-registration required, NOT a quiet re-enable).

**Data (last-24h / latest CDT):** crypto_spot 2,872 (07:15 PM ✓, 2 products), **crypto_ladder
52,450 (07:15 PM ✓, 100% model-priced — collect-only alive)**, weather forecasts/obs/ensembles/
buckets all fresh (07:09–07:19 PM ✓). xgame_matches 19 (0 new; tournament final stage),
xgame_tapes 41,942 (07:18 PM ✓ — collector left on for the final). All green.

**Research probes (on-demand):** WCPROP + XGAME lead-lag families are now CLOSED (both
falsified and disabled). No standing probes to run. TFAV concluded (killed).

**Headline:** PR #22 cleanup is live & verified — theta shelved to collect-only (ladder/spot
collectors confirmed still advancing), tfav/wcprop/xgame off, all at 0 open. Surviving portfolio
= mmsell (breakeven) + weather_con (−$1.24); no positive earner. The path back is the
weather_con city-restriction. Collectors all fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[weather_con · TOP PRIORITY — restrict to its edge cities] The clearest path back to a
   positive earner.** Run #29's by-city diagnosis: MIA +$1.06 / PHIL +$1.50 (positive) vs DEN
   −$3.13 (0% win) / NY −$0.94 (0% win) / LAX −$3.23 / AUS −$3.17 (bleeders). Recommended fable
   pass: cross-check per-city forecast skill in `weather_forecast_outcomes`, then add a con city
   allowlist (keep MIA/PHIL/CHI-tier, drop or shrink DEN/LAX/AUS/NY). This is the one live move
   that could turn the surviving portfolio net-positive. Do NOT scale con until it's done.

2. **[mmsell · keep-or-prune, low priority] All three bands ~breakeven** (control +0.2¢,
   mmsell1 +0.9¢, mmsell2 +0.7¢; pooled ~+0.4¢/trade, n≈2,960). No edge net of realism, not a
   bleeder. Leave running as a zero-attention data book, or prune for simplicity — either is
   fine; not urgent, not a promote candidate.

3. **[theta1 · revival candidate — NOT now] theta1 ended +$9.69 (+4.8¢/trade, n=201).** The only
   theta book that finished net-positive. Per the post-mortem it is the sole candidate for a
   future revival, but ONLY under a fresh pre-registered tail-calibration test (n≥350,
   sold-price vs realized-tail-rate) — the family stays shelved until then. Collectors keep
   feeding the dataset that would power it.

*(RESOLVED this run — all enacted by PR #22 (merged/deployed/verified): theta shelved to
collect-only (#1 prior); tfav killed (#3 prior); wcprop killed (#5 prior); xgame study run →
P2 KILL/P3 FAIL → book shelved (#6 prior). Remaining: weather_con city-restriction (now #1,
the only offense move), mmsell keep/prune (#2), theta1 revival-someday (#3). Portfolio has no
positive earner — the cleanup was defense; weather_con is the offense.)*
