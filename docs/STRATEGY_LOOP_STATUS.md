# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 4-hourly status loop (`kalshi_loop_checker_phase_3` skill).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-05 11:14 PM CDT (run #23)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 763 | −$11.21 | −1.5 | 31 | now clearly negative (was ~breakeven); bad −8¢ window |
| mmsell1 (5-20¢) | 226 | −$5.40 | −2.4 | 25 | negative; marginal window ~flat |
| mmsell2 (10-20¢) | 150 | −$5.03 | −3.4 | 15 | most-negative of the three |
| tfav | 25 | −$7.35 | −29 | 0 | consistently negative now (n=25) |
| theta (control) | 166 | **−$43.01** | −25.9 | 0 | cratering (−$15.6 in this window) |
| theta3 | 62 | −$11.27 | −18.2 | 0 | at gate, negative |
| theta1 / theta2 | 20 / 6 | −$5.92 / −$6.46 | −29.6 / — | 0/0 | **theta1 FLIPPED negative** (was +$1.79) |
| **wcprop** | **0** | — | — | **0** | **enabled LIVE book, armed, 0 trades — not a probe** |
| weather con | 242 | +$7.25 | +3.0 | 9 | the only positive book |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |
| buy_favorite / momentum / reversion / (blank) | 0 | 0 | — | 0 | dormant legacy (last active Jun 6–8) |

**HEADLINE — every active crypto/sports book is now net-negative; weather_con is the only
green.** theta cratered (control −$27→**−$43**; theta1's brief +$1.79 flipped to −$5.92 — the
whole family is unanimously negative). mmsell's control fell from ~breakeven to −$11.21 (−1.5¢
over 763) so all three mmsell books are losers. tfav −$7.35 at n=25. Only **weather_con
(+$7.25)** is carrying its weight. It's all paper — and finding this out *before* real money is
exactly the job — but no experimental book currently shows a positive edge.

**wcprop — it IS a live paper book, not just a probe (correction to prior runs).** Verified in
code + worker logs: `WcPropTracker` (`strategy="wcprop"`) is enabled (`wcprop_enabled=True`)
and runs every ~10 min in production (logged unbroken this deployment). It has opened **0
trades** because its entry needs a KXWCGAME/KXWCROUND match settled 5–45 min ago AND a
KXMENWORLDCUP winner rung repricing ≥3¢ in that window — a narrow conjunction. With WC games
now playing live (tapes 94k→163k), those trigger windows are occurring; a persistent 0 as
matches settle is early evidence the winner ladder is efficiently priced (the probe's P1 kill).

**theta — verdict now unanimous (shelve).** All four books negative, theta1 flipped, control
−$43 and accelerating. No book meets the pre-registered positive-AND-calibrated gate.

**XGAME — taping hard (163k/24h), games live.** Matcher steady at 13 WC games. The lead-lag
`xgame_tape_study` becomes runnable as soon as one matched game finishes with tape coverage.

**Data (last-24h / latest CDT):** crypto_spot 2,865 (11:08 PM ✓, 2 products), ladder 59,922
(11:08 PM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (11:10–
11:13 PM ✓). xgame_matches 13 (last new 01:32 PM), xgame_tapes 163,065 (11:11 PM ✓). All green.

**Research probes (on-demand):** WCPROP has TWO parts — `xmarket_wc` (offline P1/P2/P3
backtest, on-demand) AND the live `wcprop` book above (armed, 0 trades). XGAME `xgame_tape_study`
(runnable once a matched game plays). WCPROP-offline / XGAME-study not run from the loop.

**Headline:** every active crypto/sports book is net-negative — theta cratering (−$43, family
unanimous), mmsell family all red, tfav −$7.35; weather_con (+$7.25) is the only positive book.
wcprop confirmed a live-but-idle book (0 trades, armed). All collectors fresh; XGAME taping 163k.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · VERDICT — shelve the family (fable), now unanimous] Every theta book negative.**
   control −$43.01 (n=166, −25.9¢, accelerating), theta3 −$11.27 (n=62, at gate), theta1
   flipped to −$5.92 (n=20), theta2 −$6.46 (n=6). No book meets the pre-registered
   positive-AND-calibrated gate — the last bright spot (theta1) is gone. Recommended fable
   action: disable the theta books, **keep the crypto_spot + ladder collectors** (a future
   fatter-tail model rebuilds from that dataset), write the post-mortem in RESEARCH_JOURNAL.
   Still paper (no real money lost), but it is now bleeding paper fast — worth doing on the
   next fable pass.

2. **[mmsell · whole family net-negative — prune candidate] No mmsell book is positive.**
   control −1.5¢ (n=763), mmsell1 −2.4¢ (n=226), mmsell2 −3.4¢ (n=150). The early variant edge
   is gone AND the wide control has now turned clearly negative — this is no longer just
   "don't promote the variants," it's "the maker-sell cheap-longshot edge isn't there at all."
   Recommended fable action: treat mmsell (all bands) as a prune candidate alongside theta;
   if kept, keep it purely for data, not as a live-promotion path. Non-urgent (paper).

3. **[wcprop · NEW — live book, armed, 0 trades] It is enabled and running, not a dormant
   probe.** No action needed on the collector/switch. Two report-only notes: (a) the loop's
   own docs (skill + prior snapshots) mislabeled WCPROP as "on-demand probe, no book" — the
   live `wcprop` book exists and is armed; this snapshot corrects it, and a skill-doc fix was
   offered to the operator (awaiting go-ahead, not done here). (b) Watch whether wcprop stays
   at 0 trades as WC matches settle — a persistent 0 while games play is real evidence the
   winner ladder is efficiently priced (the probe's P1 kill), which would close the family.

4. **[XGAME · FIXED — study nearly runnable] Collector matching (13) + taping hard (163k/24h),
   games live.** Once a matched WC game finishes with tape coverage, run `xgame_tape_study` to
   grade the lead-lag thesis (P1–P4 in docs/IDEA_MODEL_20260704.md). No collector action needed.

5. **[tfav · negative, not just early] n=25, −$7.35 (−29¢/trade).** The initial +$0.99 (n=1)
   was noise; at n=25 it is consistently negative. Let it reach a larger n before a verdict,
   but it is trending toward "no edge." Do not act yet.

6. **[weather · resolved] con-only stable** (+$7.25, 9 open) — the only positive book; pruned
   books 0 open, done. (Book last opened a trade 4:59 PM CDT; weather data collectors all
   fresh at 11:1x PM — normal slow-settlement cadence, not a stall.)

*(Changed this run: theta (#1) now UNANIMOUS negative (theta1 flipped) — escalated. mmsell (#2)
escalated from "variants only" → whole family net-negative / prune candidate (control turned
negative). NEW #3 wcprop — corrected from "probe" to enabled live-but-idle book. #5 tfav
"early-negative" → "negative". Legacy books unchanged/dormant.)*
