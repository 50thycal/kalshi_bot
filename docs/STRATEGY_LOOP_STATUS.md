# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-10 03:14 AM CDT (run #31)

**✅ THREE NEW BOOKS DEPLOYED (PR #26) — all three run #30 suggestions are now BUILT.** mmsell3
(5-10c), theta4 (fat-tail revival), weather_concity (AUS/CHI/NYC) are live with pre-registered
gates. mmsell3 is accruing; theta4 + concity are correctly at 0 (both slow/sparse by design).

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell3** (NEW, 5-10c) | 9 | −$0.39 | — | 6 | **live & accruing** (n=9 = noise); the fast experiment |
| **theta4** (NEW, fat-tail) | 0 | — | — | 0 | 0 trades — expected (2x model + 10c bar = sparse) |
| **weather_concity** (NEW) | 0 | — | — | 0 | 0 trades — con itself idle ~13h (see below) |
| mmsell (control) | 1,640 | +$3.80 | +0.2 | 26 | breakeven data book |
| mmsell1 / mmsell2 | 876 / 576 | +$7.78 / +$4.58 | +0.9 / +0.8 | 22 / 13 | breakeven+ |
| weather con (all) | 294 | −$1.24 | −0.4 | 13 | **idle ~13h** (last entry 2:13 PM CDT); overnight quiet |
| theta (ctrl) | 560 | +$0.97 | +0.2 | 0 | SHELVED, quiet |
| theta1 / theta2 / theta3 | 201 / 98 / 134 | +$9.69 / −$11.55 / −$11.62 | — | 0 | SHELVED, quiet |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet |
| wcprop / xgame | 0 | — | — | 0 | KILLED / SHELVED |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — the experiments are live; only mmsell3 gives near-term data.** mmsell3 is trading
(9 settled, 6 open) — P&L is pure noise at n=9, ignore it; what matters is it's accruing toward
its n≥150 gate and should get there in days. **theta4 (0) and weather_concity (0) are correctly
dormant** — theta4 by its deliberate high bar, concity because the con book it rides has not
entered a single trade in ~13h (overnight quiet, a known con pattern — weather data collectors
are all fresh, so the worker is alive). The shelved books stay quiet and collectors keep
advancing — the PR #26 deploy is confirmed healthy in the loop's own data.

**PACING REALITY CHECK (honest).** concity can only trade when con fires AND it's an AUS/CHI/NYC
market. con is very low-frequency (idle 13h here; historically ~a handful/day across all 7
cities), and concity sees only 3 — so at ~1-2 concity trades/day, the n≥120 gate is likely
**1-2 MONTHS** out, not weeks. theta4 similarly slow. This isn't a bug; it's the nature of these
edges — but it means the portfolio's near-term data all comes from mmsell3, and the con-family
edge (even if real) has tiny capacity at this trade frequency.

**Data (last-24h / latest CDT):** crypto_spot 2,878 (03:13 AM ✓, 2 products), crypto_ladder
59,760 (03:14 AM ✓, 100% model-priced — collect-only feeding it), weather forecasts/obs/
ensembles/buckets all fresh (03:03–03:14 AM ✓). xgame_matches 19 (0 new — WC final imminent),
xgame_tapes 43,741 (03:12 AM ✓). All green.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED (disabled). No standing probes.

**Headline:** PR #26's three new books are live and healthy — mmsell3 accruing (n=9, noise),
theta4 + concity correctly at 0 (sparse / con idle overnight). Shelved books quiet, collectors
fresh. Near-term data is mmsell3-only; concity's gate is realistically 1-2 months out.

---

## Carried-over suggestions (review these; do not expect the loop to act)

*(All three run #30 suggestions are now BUILT + deployed via PR #26. The list is now "watch the
experiments toward their pre-registered gates" — no fable action needed unless a gate resolves
or a book misbehaves.)*

1. **[mmsell3 · WATCH toward gate — the fast one] n=9 (noise), 6 open, accruing.** Pre-reg gate:
   at n≥150, keep only if per-trade > +1.5c AND beats mmsell1/mmsell2 — else the "5-10c is the
   pure sweet spot" read was small-n and mmsell3 is dropped. Should reach n≥150 in days. No
   action until the gate; ignore the P&L until n is real.

2. **[theta4 · WATCH — sparse by design] 0 trades yet (expected).** Pre-reg gate: at n≥80,
   keep only if per-trade > 0 AND realized-tail-hit ≤ 1.25x modeled. IF it is STILL 0 after
   ~1-2 days of trading, that itself is a finding: the mult=2.0 + 10c bar is effectively
   unreachable → fable should loosen the edge (e.g. edge=6) or conclude the tail-scale
   hypothesis is dead and stop reviving theta. Watch for the first trade.

3. **[weather_concity · WATCH — very slow; capacity question] 0 trades (con idle ~13h).** Pre-reg
   gate: at n≥120, keep only if per-trade > +3c AND clearly beats full con. Realistically 1-2
   MONTHS to that n given con's low frequency. Meta-note for a future fable pass: if the con
   edge only yields ~1-2 trades/day on 3 cities, its dollar capacity is tiny even if the
   per-trade edge is real — worth deciding whether that's worth pursuing vs. finding a
   higher-frequency edge. Not urgent; let it accrue.

4. **[mmsell existing · unchanged] control/mmsell1/mmsell2 ~breakeven** (pooled ~+0.4c, n≈3,090);
   data books, not promote candidates. mmsell3 is the live improvement test.

*(Changed this run: run #30's three suggestions (con city-restriction, mmsell band, theta1
revival) are all BUILT via PR #26 and become "watch toward gate" items #1-3. Added the pacing
reality check — only mmsell3 gives near-term data; concity's gate is ~months out. theta1-revival
folded into theta4 (#2). Deploy confirmed healthy: new books writing/dormant-as-designed,
shelved quiet, collectors fresh.)*
