# Strategy status loop — live report + carried-over suggestions

*Auto-maintained by the 4-hourly status loop (`kalshi_loop_checker_phase_3` skill).
Suggestions are **recommendations only** — the loop never acts on them; the user reviews
and runs fable to change anything. Newest snapshot replaces the one above it; the
suggestion list carries over run-to-run. All times CENTRAL (CDT/CST).*

---

## Snapshot — 2026-07-05 07:15 PM CDT (run #22)

**Trading books (settled n / P&L / per-trade / open):**
| book | n | P&L | ¢/trade | open | note |
|---|---|---|---|---|---|
| mmsell (control) | 631 | −$0.34 | −0.1 | 78 | was +$6.52 last run → gave it all back; ~breakeven |
| **mmsell1** (5-20¢) | 122 | **−$4.64** | **−3.8** | 54 | **EDGE REVERSED** (was +2.5¢ @ n=31) |
| **mmsell2** (10-20¢) | 83 | **−$3.61** | **−4.3** | 27 | **EDGE REVERSED** (was +6.3¢ @ n=21) |
| tfav | 13 | −$5.52 | −42 | 4 | early negative (first settle +$0.99 was n=1 noise) |
| theta (control) | 153 | −$27.42 | −17.9 | 1 | worsening |
| **theta3** | **61** | **−$7.55** | −12.4 | 0 | **AT GATE — still negative → fails rule** |
| theta1 / theta2 | 16 / 5 | +$1.79 / −$1.74 | +11.2 / — | 0/0 | sub-gate; theta1 + but n<60, correlated |
| weather con | 242 | +$7.25 | +3.0 | 9 | healthy, trading |
| weather (rest) | 4,709 | −$238.63 | — | 0 | pruned, done |
| buy_favorite / momentum / reversion / (blank) | 0 | 0 | — | 0 | dormant legacy (last active Jun 6–8) |

**HEADLINE — mmsell variant edge REVERSED.** The narrow-band variants that beat the control
for 4 straight runs have now gone **negative** and dropped **below** the control as their
samples quadrupled: mmsell1 +2.5¢→**−3.8¢** (n 31→122), mmsell2 +6.3¢→**−4.3¢** (n 21→83).
The control also gave back its whole +$6.52 (now −$0.34 / ~breakeven over 631). This is the
textbook negative-skew longshot pattern — early runs collect small premiums (look positive),
then a longshot hits and the tail loss lands. The prior "promote the narrow band" call is now
**withdrawn**; the narrow-band advantage was small-n illusion.

**theta — verdict stands (shelve).** theta3 barely moved (60→61) and is still **−$7.55** at
the gate; the control worsened to −$27.42. theta1 ticked to +$1.79 but only n=16 (well below
the n≥60 gate) and is correlated with the others — it does **not** revive the family. Per the
pre-registered rule (positive AND calibrated at ≥60), no theta book qualifies.

**XGAME — fixed, taping hard.** Matcher still pairing all 13 WC games; `xgame_tapes` last-24h
jumped **30,160 → 93,713** rows. The in-play dataset is filling fast. Study still waits on a
matched WC game to actually play live.

**Data (last-24h / latest CDT):** crypto_spot 2,870 (07:09 PM ✓, 2 products), ladder 60,882
(07:09 PM ✓, 100% model-priced), weather forecasts/obs/ensembles/buckets all fresh (07:10–
07:13 PM ✓). xgame_matches 13 (last new 01:32 PM), xgame_tapes 93,713 (07:13 PM ✓). All green.

**Research probes (on-demand):** WCPROP (`xmarket_wc`) · XGAME study (`xgame_tape_study`,
runnable once a matched game plays) · TFAV is now a live book (see table). None run from the loop.

**Headline:** mmsell narrow-band edge reversed as n grew (variants now below a ~breakeven
control) — the 4-run "variants ahead" story is falsified. theta stays shelved (gate negative).
XGAME taping hard (94k). Weather con-only healthy. All collectors fresh.

---

## Carried-over suggestions (review these; do not expect the loop to act)

1. **[theta · VERDICT — shelve the family (fable)] All gate-reaching books negative.** theta3
   (n=61) −$7.55, control (n=153) −$27.42 and worsening; theta1 is +$1.79 but only n=16 (below
   the n≥60 gate) and correlated, so it does not rescue the family. The pre-registered rule
   (positive AND calibrated at the gate) is met by no theta book. Recommended fable action:
   disable the theta books but **keep the crypto_spot + ladder collectors** (that labeled
   dataset is what a future fatter-tail model rebuilds from). Write the post-mortem in
   RESEARCH_JOURNAL. Not urgent (paper, no money lost) — the experiment is decided.

2. **[mmsell A/B · REVERSED — narrow-band thesis falsified] Variants went negative and below
   the control.** mmsell1 −3.8¢ (n=122), mmsell2 −4.3¢ (n=83) vs control −0.1¢ (n=631). The
   early +2.5/+6.3¢ edge was small-n illusion on a negative-skew book — as n quadrupled a tail
   loss landed and the sign flipped. **Withdraw** the earlier "promote the narrow band, retire
   the control" recommendation. Recommended fable action: do not promote the variants. Decide
   whether mmsell is worth continuing at all — even the wide control is only ~breakeven, and a
   ~breakeven paper book before live-fee realism is a candidate to prune (like theta). If kept,
   let it run purely for data, not as a promote candidate. Non-urgent (paper).

3. **[XGAME · FIXED — next step is the study] Collector matching (13) + taping hard (94k/24h).**
   Once a matched WC game plays live, run `xgame_tape_study` to grade the lead-lag thesis
   (P1–P4 in docs/IDEA_MODEL_20260704.md). No collector action needed.

4. **[tfav · early-negative, watch] n=13, −$5.52 (−42¢/trade).** The first settle (+$0.99) was
   n=1 noise; the early sample is now clearly negative. Too small to judge, but the initial
   positive is gone — let it accumulate to a real n before any read, do not act.

5. **[weather · resolved] con-only stable** (+$7.25, 9 open); pruned books 0 open, done.

*(Changed this run: #2 mmsell FLIPPED from "variants ahead" → "edge reversed / thesis
falsified" as n quadrupled — earlier promote rec withdrawn. #4 tfav moved from "first settle +"
→ early-negative. theta (#1) and XGAME (#3) unchanged in substance. Dormant legacy books
buy_favorite/momentum/reversion surfaced for completeness — inactive ~1 month, no action.)*
