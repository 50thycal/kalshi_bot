# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 8-hourly status loop (`kalshi_loop_checker_phase_3` skill; cadence
changed from 4h to 8h on 2026-07-06).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-11 11:13 AM CDT (run #35)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| **mmsell3** (5-10c) | 85 | +$3.41 | **+4.0** | 6 | **strengthening again** (+3.6→+4.0c); ~57% to n≥150 gate |
| **theta4** (fat-tail) | 2 | +$1.54 | — | 0 | **UNSTUCK** — first 2 trades (n=2 = noise); no longer 0 |
| **weather_concity** | 7 | −$1.25 | −17.9 | 7 | **first A/B batch NEGATIVE** (n=7 noise); con beat it this batch |
| **pin15** (NEW) | 0 | 0 | — | 2 | **NEW book from a parallel session** — see #7 (tension w/ idea-model kill) |
| mmsell (control) | 1,837 | +$14.28 | +0.8 | 15 | breakeven+ |
| mmsell1 / mmsell2 | 1,021 / 674 | +$12.38 / +$8.16 | +1.2 / +1.2 | 11 / 6 | breakeven+ |
| weather con (all) | 322 | −$3.83 | −1.2 | 12 | +$1.30 this batch (15 settled); recovered a bit |
| theta ctrl/1/2/3 | 560/201/98/134 | +$0.97/+$9.69/−$11.55/−$11.62 | — | 0 | SHELVED, quiet |
| tfav | 215 | −$7.54 | −3.5 | 0 | KILLED, quiet |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |

**HEADLINE — mmsell3 still climbing; theta4 unstuck; concity's first A/B went the wrong way (n=7);
a new `pin15` book appeared.** mmsell3 rose to **+4.0c/trade at n=85** — its edge is *growing* with
n, ~57% to the gate; still the clear near-term win. **theta4 traded for the first time** (2 trades,
+$1.54) — n=2 is noise but it's no longer stuck at 0, which softens the "bar unreachable" read.
The **first weather_concity settlements came in NEGATIVE** (7 settled, −$1.25 / −17.9c) while the
all-city con book it's meant to beat went **+$1.30** on the same batch — the opposite of the
thesis, but at n=7 it is pure noise (the by-city split always had stability concerns). And a **new
book `pin15`** (2 open, 0 settled) showed up from a parallel fable session — flagged below.

**pin15 — NEW, needs accounting.** Appeared this run (2 open, latest 11:13 AM CDT). Not one of the
loop-tracked builds (mmsell3/theta4/concity). Almost certainly a parallel fable session acting on
the idea-model queue — but note the tension: the idea-model runs KILLED the "PINNED" thesis at the
probe (#7). Either pin15 is a refined/paper-only test of that idea or a different pinning book.
Its thesis + pre-registered gate are NOT in this loop's context — they should be captured (likely
in an IDEA_MODEL / journal doc) so pin15 is graded against a fixed rule like the others.

**Data (last-24h / latest CDT):** crypto_spot 2,874 (11:11 AM ✓, 2 products), crypto_ladder
58,400 (11:12 AM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh
(11:04–11:14 AM ✓). xgame_matches 21 (+2 new; collector still matching though the book is
shelved), xgame_tapes 74,760 (11:12 AM ✓, tapering post-WC). All green.

**Research probes (on-demand):** WCPROP + XGAME families CLOSED. No standing probes.

**Headline:** mmsell3 +4.0c @ n=85 (edge growing, ~57% to gate — the win); theta4 unstuck (n=2
noise); concity's first batch negative (n=7 noise, con beat it); NEW pin15 book (2 open) from a
parallel session needs its thesis/gate documented. Shelved books quiet; collectors fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta4 · UNSTUCK — watch toward gate (was: decision due)] First 2 trades, +$1.54 (n=2 =
   noise).** No longer stuck at 0, so the "loosen the edge" action is no longer forced — it CAN
   clear the bar, just rarely. Gate unchanged: n≥80, keep only if per-trade > 0 AND realized-tail
   -hit ≤ 1.25x modeled. It will accrue VERY slowly at this rate; if it's still <~10 by run #38,
   revisit the loosen-edge idea to get a testable n. Do not read n=2.

2. **[mmsell3 · PROMISING, edge growing — hold to gate] +4.0c/trade at n=85** (+2.9→+3.6→+4.0 as
   n grew; beats the +1.5c gate and mmsell1/2 at +1.2c). Gate: n≥150, keep only if > +1.5c AND
   beats mmsell1/2 — on track and improving. If it holds, promote (narrow mmsell to 5-10c, retire
   the diluted wider bands) AND it unblocks MMX (#6). ~57% to the gate. Do NOT act yet.

3. **[weather_concity · WATCH — first batch negative, but n=7] 7 settled −$1.25 (−17.9c); con
   went +$1.30 on the same batch.** The edge-city restriction did NOT beat con in its first tiny
   batch — the opposite of the thesis, but n=7 is noise and the by-city split always had stability
   concerns (the 10-day window disagreed with all-time). Gate: n≥120, keep only if >+3c AND beats
   full con. Let it accrue; if it's still losing / not beating con by n~40-60, the by-city edge was
   likely historical noise and concity should be dropped. Do not read n=7.

4. **[weather con (all) · context] −$3.83 (recovered +$1.30 this batch).** No action; concity is
   the test.

5. **[mmsell existing · unchanged] control/mmsell1/mmsell2 ~breakeven-positive** (+0.8 to +1.2c,
   n≈3,530); data books. mmsell3 is the live improvement candidate.

6. **[idea-model queue · MMX still waiting on mmsell3's gate] Two idea-model runs 2026-07-10
   (`IDEA_MODEL_20260710.md`, `..._run2.md`): 42 candidates, PINNED + DECAY probed & KILLED, 9
   held, 19 killed.** MMX (extend mmsell 5-10c maker-sell into uncorrelated non-sports categories)
   is the highest-value hold, **blocked on #2 (mmsell3 n≥150)** — trigger: re-run `kalshi-strategy`
   on MMX the moment mmsell3 gates (material in `..._run2.md`). NEST behind theta4 (#1). RTPIN/
   BOXPIN behind unbuilt scraper infra. RATELAG behind a live Fed event. Do NOT re-run idea-model
   until #1 or #2 clears (board already mined across 4 runs).

7. **[pin15 · NEW book — document its thesis/gate] Appeared run #35 (2 open, 0 settled) from a
   parallel session.** The idea-model runs KILLED the PINNED thesis at the probe, so pin15's
   rationale (refined variant? paper-only test-anyway? different pinning book?) needs to be written
   down with a pre-registered gate so the loop can grade it like the others — otherwise it's an
   untracked live book. Recommended: point me at its IDEA_MODEL/journal doc, or have the next fable
   session record it. The loop query now surfaces it as `book:pin15`.

*(Changed this run: theta4 UNSTUCK — 2 trades, +$1.54 (#1, was "decision due"). mmsell3 → +4.0c @
n=85, edge growing (#2). concity's first A/B batch NEGATIVE at n=7 (#3, noise but flagged with a
drop-if-still-losing-by-n~50 tripwire). NEW #7: pin15 book appeared from a parallel session — needs
its thesis/gate documented. #6 idea-model queue carried forward.)*
