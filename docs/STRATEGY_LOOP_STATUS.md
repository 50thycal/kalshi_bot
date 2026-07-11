# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-11 04:41 PM CDT (run #36)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell3** (5-10c) | 97 | +$3.37 | **+3.5** | 41 | still climbing to gate; +3.5c beats mmsell1/2 (+1.0c); **65% to n≥150** |
| **pin15** (NEW→TRACKED) | 21 | −$3.53 | **−16.8** | 0 | registry row NOW EXISTS (`PIN15_THESIS.md`, gate n≥150) — resolves last run's UNTRACKED flag; but first settlements NEGATIVE (n=21) |
| **theta4** (fat-tail) | 2 | +$1.54 | — | 0 | unchanged (n=2 = noise); gate n≥80 |
| **weather_concity** | 7 | −$1.25 | −17.9 | 7 | unchanged this batch (n=7 noise); gate n≥120 |
| mmsell (control) | 1,869 | +$9.66 | +0.5 | 95 | breakeven+ (drifted down from +$14.3 as recent settles net-neg) |
| mmsell1 / mmsell2 | 1,042 / 687 | +$10.89 / +$7.09 | +1.0 / +1.0 | 64 / 33 | breakeven+ |
| weather con (all) | 322 | −$3.83 | −1.2 | 15 | flat this batch (no new settles) |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — pin15 now TRACKED (registry row added) but losing early; mmsell3 holding ~+3.5c,
still the near-term win; no gate cleared.** The big change since run #35: **`pin15` got its
thesis + pre-registered gate documented** (`docs/PIN15_THESIS.md`, gate n≥150, keep only if
per-trade >+1.5c AND profit concentrates in the T≈120–180s entries). That resolves last run's
UNTRACKED-book flag — it's now graded like the others. BUT its first 21 settlements came in at
**−$3.53 / −16.8c per trade** (0 open now) — clearly negative, though n=21 of 150 is early and
the gate specifically tests *where* in the T-window the P&L sits. **mmsell3** settled 12 more
(85→97), easing to **+3.5c/trade** (from +4.0c) — still well above its +1.5c gate and still
beating mmsell1/2 (+1.0c), **65% to the n≥150 gate** and the clearest near-term win. theta4 (n=2)
and concity (n=7) unchanged — pure noise. **No gate cleared this run**, so nothing to build yet.

**Gate sweep (step 3b):** mmsell3 **97/150** (53 to go, MMX blocked on it) · pin15 **21/150**
(129 to go, currently −EV) · theta4 **2/80** (NEST behind it) · weather_concity **7/120**.

**Data (last-24h / latest CDT):** crypto_spot 2,872 (04:37 PM ✓, 2 products), crypto_ladder
59,280 (04:38 PM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh
(04:24–04:40 PM ✓), xgame_tapes 41,765 (04:39 PM ✓, tapering post-WC), xgame_matches +2 in 24h
(latest 05:37 AM CDT; collector matching, book shelved). All green.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** pin15 now tracked w/ gate but −16.8c @ n=21 (watch, early); mmsell3 +3.5c @ n=97
(65% to gate, still the win); theta4/concity noise-level n; no gate cleared; collectors all fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta4 · UNSTUCK — watch toward gate] Still 2 trades, +$1.54 (n=2 = noise).** No change
   this run. Gate: n≥80, keep only if per-trade > 0 AND realized-tail-hit ≤ 1.25x modeled. It
   accrues VERY slowly; if it's still <~10 by run #38, revisit the loosen-edge idea to get a
   testable n. Do not read n=2.

2. **[mmsell3 · PROMISING — hold to gate] +3.5c/trade at n=97** (eased from +4.0c @ n=85 as 12
   more settled; still beats the +1.5c gate and mmsell1/2 at +1.0c). Gate: n≥150, keep only if
   > +1.5c AND beats mmsell1/2 — on track. **65% to the gate.** If it holds, promote (narrow
   mmsell to 5-10c, retire the diluted wider bands) AND it unblocks MMX (#6). Do NOT act yet.

3. **[pin15 · NOW TRACKED — watch, first batch NEGATIVE] 21 settled −$3.53 (−16.8c), 0 open.**
   RESOLVED last run's untracked flag: `docs/PIN15_THESIS.md` + `BOOK_REGISTRY.md` row now exist
   (15-min crypto endgame observation-pin; gate **n≥150**, keep only if per-trade > +1.5c AND
   profit concentrates in the T≈120–180s entries). First settlements are clearly −EV, but n=21 is
   early and the gate is really about *which T-window* earns. Watch: if it's still deeply negative
   with no T≈120–180s concentration by n~60–80, the ~300s loop likely can't capture it → shelve.
   Do not over-read n=21.

4. **[weather_concity · WATCH — n=7, unchanged] 7 settled −$1.25 (−17.9c); con flat this batch.**
   No new settlements. Gate: n≥120, keep only if it beats all-city con. Let it accrue; if still
   losing / not beating con by n~40–60, the by-city edge was historical noise → drop. Do not read n=7.

5. **[mmsell existing · unchanged] control/mmsell1/mmsell2 ~breakeven-positive** (+0.5 to +1.0c,
   n≈3,600). Control drifted +$14.3→+$9.7 as recent settles ran net-negative — still positive,
   still a data book. mmsell3 is the live improvement candidate.

6. **[idea-model queue · MMX still waiting on mmsell3's gate] Two idea-model runs 2026-07-10
   (`IDEA_MODEL_20260710.md`, `..._run2.md`): 42 candidates, PINNED + DECAY probed & KILLED, 9
   held, 19 killed.** MMX (extend mmsell 5-10c maker-sell into uncorrelated non-sports categories)
   is the highest-value hold, **blocked on #2 (mmsell3 n≥150 — now 65% there)** — trigger: re-run
   `kalshi-strategy` on MMX the moment mmsell3 gates (material in `..._run2.md`). NEST behind theta4
   (#1). RTPIN/BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed event. Do NOT re-run
   idea-model until #1 or #2 clears (board already mined across 4 runs).

*(Changed this run: #3 pin15 RESOLVED from untracked → now has `PIN15_THESIS.md` + registry row and
a real gate (n≥150), but its first 21 settlements are −16.8c/trade (early, watch the T-window). #2
mmsell3 eased +4.0c→+3.5c @ n=97 (65% to gate, still the win). Prior run #35's #7 (document pin15)
is now folded into #3 as resolved. theta4/concity unchanged, noise-level n. No gate cleared.)*
