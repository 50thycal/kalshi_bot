# mmsell check status — state for the `mm_check_1` skill

**Owned by the `mm_check_1` skill.** Rewritten on every run; never hand-edit except to
fix a corrupted snapshot. Lives on the `mmsell-check-status` branch only — never merged
into the default branch, never touched by `ops`. Diffed against on the next run.

**Run #6 — 2026-08-03 21:22 UTC**

## Headline: A1's matched counterfactual INVERTED, exactly as pre-warned

Run #5 measured A1's stop at **+7.0¢ saved/trade** on 4 matched-settled pairs, with 13
pending. Those pending pairs resolved: at **n=25 matched, A1's stop now reads −2.4¢** —
the stop costs more than it saves. This is the guardrail from the skill's step 3b doing
its job; do not report a matched number without its pending count.

## Standing realizable read (mmsell fill model)

| book | n (run5→6) | realizable ¢/ct (5→6) | total P&L $ | verdict |
|---|---|---|---|---|
| mmsell | 4383→4523 | +0.29→+0.26 | **+$67.36** | low coverage (32.8%) |
| mmsell1 | 2877→2988 | +0.34→+0.33 | **+$60.44** | thin + |
| mmsell10 | 261→314 | +1.33→+1.33 | **+$9.30** | REALIZABLE EDGE (4th consecutive) |
| mmsell11 | 475→555 | −0.74→−0.79 | +$16.56 | MIRAGE |
| mmsell2 | 1898→1969 | +5.01→+5.00 | **+$52.85** | low coverage (20.4%) |
| mmsell3 | 1258→1338 | −0.84→−0.85 | +$25.47 | MIRAGE |
| mmsell4 | 407→486 | −0.72→−0.77 | +$9.69 | MIRAGE |
| mmsell5 | 185→203 | +0.83→+0.88 | **−$1.38** | thin + — **UNSTALLED** (was 0 new on run 5) |
| mmsell6 | 539→607 | −0.22→−0.24 | +$14.01 | MIRAGE |
| mmsell7 | 117→136 | −0.65→−0.61 | +$2.47 | MIRAGE (2nd repeat — stabilizing) |
| mmsell8 | 68→75 | +0.56→+0.79 | +$2.10 | thin + |
| mmsell9 | 80→92 | +1.33→+1.34 | +$3.06 | REALIZABLE EDGE |

No verdict flips. **Ignore the A1–A4 rows this script emits** — they exclude `closed_sl`
(see step 3b); the anchor table below is authoritative.

**Whole-family realized total: ~+$262 paper.** Note the dollar leaders (`mmsell` +$67,
`mmsell1` +$60, `mmsell2` +$53) are all LOW-COVERAGE books whose realizable read is
+0.26 to +0.33¢ — the dollars are volume, not edge. `mmsell10`'s +$9.30 at +1.33¢
realizable is the only high-coverage positive.

## Exit study — best exit per book

**Family-wide: every book's HOLD mean fell sharply this run** (mmsell10 +4.89→+3.02,
mmsell8 +8.22→+6.21, mmsell +2.79→+0.73, mmsell5 to NEGATIVE −1.26). A bad stretch hit
the whole family, not one book. Read every Δ below against a weaker base than run #5.

| book | replay n (5→6) | HOLD mean/tail (5→6) | best rule this run | Δmean | Δtail | gate |
|---|---|---|---|---|---|---|
| mmsell | 549→688 | +2.79/−76 → +0.73/−80 | none beats hold (L60 K2 −0.16) | −0.89 | +16 | NO |
| mmsell1 | 386→496 | +4.18/−83 → +1.82/−85 | stop L50 K2 +1.63/−61 | −0.19 | +24 | **BOUNDARY** |
| mmsell10 | 144→197 | +4.89/+5 → +3.02/+5 | stop L50 K2 +3.03/+5 | +0.01 | 0 | NO — tail already clean |
| mmsell11 | 234→313 | +4.91/+5 → +2.73/+4 | stop L50 K2 +2.67/−50 | −0.06 | **−54** | NO — tail worse |
| mmsell2 | 261→332 | +4.60/−84 → +1.65/−86 | stop L50 K2 +1.74/−65 | **+0.08** | **+21** | **CLEARS — but see notes** |
| mmsell3 | 246→325 | +5.01/+5 → +2.89/+5 | stop L50 K2 +2.83/−50 | −0.06 | −55 | NO |
| mmsell4 | 221→300 | +5.21/+5 → +2.86/+4 | stop L50 K2 +2.69/−50 | −0.16 | −54 | NO |
| mmsell5 | 71→89 | +4.69/+5 → **−1.26/−89** | **stop L30 K2 +0.48/−44** | **+1.74** | **+45** | not yet (n=89<100) |
| mmsell6 | 199→266 | +5.04/+5 → +2.39/+5 | stop L50 K2 +2.57/+5 | +0.18 | 0 | NO — tail already clean |
| mmsell7 | 70→89 | +5.53/+5 → +4.61/+5 | none beats hold | −0.93 | 0 | NO |
| mmsell8 | 45→52 | +8.22/+5 → +6.21/+5 | none beats hold (L50 K2 −0.44) | −0.44 | 0 | NO |
| mmsell9 | 58→70 | +5.53/+5 → +2.66/+5 | stop L40 K2 +2.89/−38 | +0.23 | −43 | NO — buys mean with tail |

## Anchor set — direct read (step 3b; stops included)

| book | entries | open | settled | stops | resolved | total P&L $ | ¢/trade |
|---|---|---|---|---|---|---|---|
| mmsellA1 (12¢) | 65 | 4 | 35 | **26** | 61 | **−$1.41** | −2.31 |
| mmsellA2 (20¢) | 53 | 5 | 37 | 11 | 48 | **−$0.71** | −1.48 |
| mmsellA3 (30¢) | 51 | 5 | 39 | 7 | 46 | **−$1.94** | −4.22 |
| mmsellA4 (vol gate) | 47 | 5 | 42 | 0 | 42 | **−$1.73** | −4.12 |
| mmsellA5 (strangle) | **5** | 5 | 0 | 0 | 0 | — | — |
| **mmsell10 (CONTROL)** | 319 | 5 | 314 | 0 | 314 | **+$9.30** | **+2.96** |

**Anchor set combined: −$5.79.** Control over the same period: +$9.30. Against the
$100/month north star the anchor set is currently a −$5.79 information purchase.

### Matched counterfactual (the deciding read)

| book | matched-settled n | stop avg | control avg | ctrl worst | **stop saved** | pending |
|---|---|---|---|---|---|---|
| mmsellA1 (12¢) | 25 | −12.8¢ | −10.4¢ | −95¢ | **−2.4¢** | 1 |
| mmsellA2 (20¢) | 11 | −24.8¢ | −30.9¢ | −95¢ | **+6.1¢** | 0 |
| mmsellA3 (30¢) | 7 | −43.9¢ | −37.4¢ | −95¢ | **−6.4¢** | 0 |

The mechanism is now visible in the control-average column. A1's stopped markets averaged
only −10.4¢ under the control — most of its 26 stops fired on markets that were fine, so
it pays ~12.8¢ to dodge ~10.4¢. A2's stopped markets averaged −30.9¢ — its stop set is
genuinely enriched for disasters, which is why it's the only level saving money. A3 exits
at −43.9¢ on markets the control resolved at −37.4¢: by the time 30¢ confirms, the damage
is done and some of those recover.

## Notes carried into the next run

- **A1's +7.0¢ from run #5 was an artifact of n=4.** At n=25 it is −2.4¢. Never quote a
  matched number without its pending count; this is the second time in this program that a
  favorable early reading reversed as n grew (mmsell1/mmsell2 in run #4 was the first).
- **A2 (20¢) is the only stop level saving money** (+6.1¢ matched) but n=11. If a level
  promotes it will be this one, not the tight one the crypto backtest pointed at. Needs
  ≥3 checks holding.
- **mmsell2 technically CLEARS the exit gate this run** (Δmean +0.08, Δtail +21, n=332).
  Do NOT act on it. Its Δmean sequence is now **+1.00 → −0.45 → −0.30 → +0.08** — four
  checks, four different signs/values. This is oscillation around zero, and the standing
  3-consecutive-checks rule is not met. Treat a single clear as noise until it repeats.
- **mmsell5 is the one genuinely new signal**: it unstalled, its HOLD went NEGATIVE
  (−1.26¢), and stop L30 K2 improves **both** mean (+1.74) and tail (+45) — the only book
  in the family where a stop does that. n=89, so 11 short of the gate. Check it next run.
- **mmsell5 is also the only book with a negative total P&L (−$1.38)** among the legacy
  twelve — consistent with it being the book that actually has an uncut tail.
- **A5 finally took 5 entries** after ~4 days at zero. All open. The both-tails condition
  is restrictive but not impossible; at this rate 82 pairs is many months away, so decide
  whether to loosen it rather than wait.
- Family-wide HOLD means dropped sharply this run across every book. If run #7 shows the
  same, this is a regime change, not a bad week — and every "REALIZABLE EDGE" verdict
  above should be re-examined rather than assumed durable.
- mmsell10 held REALIZABLE EDGE for a 4th consecutive check (+1.35/+1.33/+1.33/+1.33) at
  growing n (227→261→314). Still the only book meeting the multi-check standard.
