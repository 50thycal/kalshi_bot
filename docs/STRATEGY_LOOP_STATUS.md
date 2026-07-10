# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-10 02:57 PM CDT (run #32)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell3** (5-10c) | 10 | −$0.32 | — | **48** | accruing fast (48 open → n jumps soon); P&L noise |
| **weather_concity** | 0 | — | — | **7** | **NOW LIVE** — first entries (AUS/CHI/NYC); wiring confirmed |
| **theta4** (fat-tail) | 0 | — | — | 0 | still 0 at ~18h — bar very restrictive (decision pt next run) |
| mmsell (control) | 1,655 | +$2.80 | +0.2 | 118 | breakeven |
| mmsell1 / mmsell2 | 883 / 582 | +$7.62 / +$4.35 | +0.9 / +0.7 | 88 / 50 | breakeven+ |
| weather con (all) | 307 | **−$5.13** | −1.7 | 15 | **bad batch** (−$3.89 on 13 today); bleed continues |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — weather_concity is live (7 open); theta4 still 0; con had a bad day.** The concity
book started trading (con resumed daytime and concity rode its AUS/CHI/NYC picks) — 7 open, 0
settled yet (weather settles ~daily), but this **confirms the end-to-end wiring in production**.
mmsell3 is piling up open positions (48) that will settle into real n over the next hours.
theta4 is **still at 0 after ~18h** — the mult=2.0 + 10c bar is proving very restrictive.
Separately, the all-city con book had a **bad batch today** (−$1.24 → −$5.13, ~−30c/trade over
13 settles) — which is exactly the bleed the concity restriction is meant to dodge; the test now
has live open positions on both sides to compare when they settle.

**theta4 — DECISION POINT next run (#33, ~26h post-deploy).** 0 trades at ~18h. The unit test
proves the live-variant mechanism works and the theta tracker is running (ladder collector
fresh), so 0 = the double gate (2x fatter model AND 10c edge) is simply almost never cleared.
Pre-registered call: **if theta4 is STILL 0 at run #33, treat the bar as effectively unreachable
→ fable should either loosen the edge (10→~6c) to get a testable n, or conclude the fat-tail
revival is impractical and stop.** One more run decides it.

**Data (last-24h / latest CDT):** crypto_spot 2,876 (02:56 PM ✓, 2 products), crypto_ladder
59,280 (02:56 PM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh
(02:28–02:57 PM ✓). xgame_matches 19 (0 new), xgame_tapes 68,372 (02:56 PM ✓ — heavy volume, WC
final being played). All green.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED (disabled). No standing probes.

**Headline:** weather_concity live (7 open — wiring confirmed); mmsell3 accruing (48 open, n
jumps soon); theta4 still 0 at ~18h (decision next run). All-city con bled today (−$5.13),
reinforcing the concity rationale. Shelved books quiet; collectors fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

*(All three run #30 build-suggestions shipped via PR #26; the list is "watch toward the
pre-registered gates.")*

1. **[mmsell3 · WATCH toward gate — fast] n=10 settled but 48 OPEN** → n will jump over the next
   hours. Gate: n≥150, keep only if >+1.5c AND beats mmsell1/mmsell2. P&L is noise until n≥~100.

2. **[theta4 · DECISION POINT next run] Still 0 at ~18h.** If STILL 0 at run #33 (~26h), the
   2.0x/10c bar is effectively unreachable → fable should loosen edge (10→~6c) for a testable n,
   OR conclude the fat-tail revival is impractical and leave theta fully shelved. Concrete call
   next run.

3. **[weather_concity · WATCH — now live, 7 open] Wiring confirmed in production.** Gate: n≥120,
   keep only if >+3c AND clearly beats full con. Today the all-city con bled (−$3.89/13); when
   concity's 7 AUS/CHI/NYC opens settle we get the first direct A/B on whether the edge-city
   restriction dodges that bleed. Still ~1-2 months to the full gate (con is low-frequency).

4. **[weather con (all) · context — bleed continues] −$5.13 (−1.7c), −$3.89 today.** No action
   (concity is the test); but if con keeps bleeding, the case to retire all-city con in favor of
   the restricted book strengthens — pending concity's n.

5. **[mmsell existing · unchanged] control/mmsell1/mmsell2 ~breakeven** (pooled ~+0.4c, n≈3,120);
   data books, not promote candidates.

*(Changed this run: weather_concity 0 → LIVE (7 open, wiring confirmed) — #3 upgraded. theta4
given a concrete DECISION POINT for run #33 (#2). Added #4 noting the all-city con bled today
(−$5.13), which reinforces why concity exists. mmsell3 accruing (48 open). Shelved books quiet;
deploy remains healthy.)*
