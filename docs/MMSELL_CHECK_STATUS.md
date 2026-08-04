# mmsell check status — state for the `mm_check_1` skill

**Owned by the `mm_check_1` skill.** Rewritten on every run; never hand-edit except to
fix a corrupted snapshot. Lives on the `mmsell-check-status` branch only — never merged
into the default branch, never touched by `ops`. Diffed against on the next run.

**Run #7 — 2026-08-04 12:39 UTC**

## Headline: the whole mmsell family is stalled — worker is healthy, market supply is dry

Every number below except `mmsellA5` is **byte-identical to run #6** (same n, same
cents/trade, same $ to the cent, across both scripts). Verified this wasn't a caching/
staleness bug before reporting it: `bot_runs` shows **100 cycles in the last 2 hours** and
the most recent run was seconds before this check, so the worker is alive and cycling.
But the last **mmsell-family** entry before this run was **04:47 UTC** (~8h prior), and
in the preceding 10 hours the only strategies to trade at all were two `weather_con*`
books plus a single `mmsellA5` fill. **No mmsell candidates are clearing the scan** —
this reads as a market-supply lull (same shape as the earlier World-Cup-driven slowdown
this program root-caused before), not a code break. Didn't chase it further this run per
the skill's scope; worth a dedicated look if it persists past run #8.

## Standing realizable read (mmsell fill model) — UNCHANGED from run #6

| book | n (6→7) | realizable ¢/ct (6→7) | total P&L $ | verdict |
|---|---|---|---|---|
| mmsell | 4523→4523 | +0.26→+0.26 | +$67.36 | low coverage (32.8%) — **stalled** |
| mmsell1 | 2988→2988 | +0.33→+0.33 | +$60.44 | thin + — **stalled** |
| mmsell10 | 314→314 | +1.33→+1.33 | +$9.30 | REALIZABLE EDGE (5th check, but flat n) |
| mmsell11 | 555→555 | −0.79→−0.79 | +$16.56 | MIRAGE — **stalled** |
| mmsell2 | 1969→1969 | +5.00→+5.00 | +$52.85 | low coverage — **stalled** |
| mmsell3 | 1338→1338 | −0.85→−0.85 | +$25.47 | MIRAGE — **stalled** |
| mmsell4 | 486→486 | −0.77→−0.77 | +$9.69 | MIRAGE — **stalled** |
| mmsell5 | 203→203 | +0.88→+0.88 | −$1.38 | thin + — **stalled** |
| mmsell6 | 607→607 | −0.24→−0.24 | +$14.01 | MIRAGE — **stalled** |
| mmsell7 | 136→136 | −0.61→−0.61 | +$2.47 | MIRAGE — **stalled** |
| mmsell8 | 75→75 | +0.79→+0.79 | +$2.10 | thin + — **stalled** |
| mmsell9 | 92→92 | +1.34→+1.34 | +$3.06 | REALIZABLE EDGE — **stalled** |

Every book shows zero new settled trades this run. Normally 2+ consecutive stalls on a
single book is the flag threshold; this run every book crossed it simultaneously, which
is itself the signal — treat as one family-wide event, not 12 separate stalls.

## Exit study — UNCHANGED from run #6 (same replayable n and stats, every book)

Not re-tabulated in full since nothing moved; see run #6 for the last live numbers
(mmsell2 sat on the exit-gate boundary at +0.08/+21 across 4 oscillating checks —
still don't act on it; mmsell5 was 11 trades short of n=100 with the one genuinely
promising both-directions stop in the family — still 11 short, unchanged).

## Anchor set — direct read (step 3b; stops included)

| book | entries (6→7) | open | settled | stops | resolved | total P&L $ | ¢/trade |
|---|---|---|---|---|---|---|---|
| mmsellA1 (12¢) | 65→65 | 4 | 35 | 26 | 61 | −$1.41 | −2.31 |
| mmsellA2 (20¢) | 53→53 | 5 | 37 | 11 | 48 | −$0.71 | −1.48 |
| mmsellA3 (30¢) | 51→51 | 5 | 39 | 7 | 46 | −$1.94 | −4.22 |
| mmsellA4 (vol gate) | 47→47 | 5 | 42 | 0 | 42 | −$1.73 | −4.12 |
| mmsellA5 (strangle) | **5→7** | 7 | 0 | 0 | 0 | — | — |
| **mmsell10 (CONTROL)** | 319→319 | 5 | 314 | 0 | 314 | +$9.30 | +2.96 |

**Anchor set combined: still −$5.79** (unchanged — no new resolutions). `mmsellA5` is
the only book that moved at all, +2 entries, still 0 settled. All 7 open.

### Matched counterfactual — unchanged (stop counts identical to run #6, so this cannot
have moved; not re-queried)

| book | matched-settled n | stop saved | pending |
|---|---|---|---|
| mmsellA1 (12¢) | 25 | −2.4¢ | 1 |
| mmsellA2 (20¢) | 11 | **+6.1¢** | 0 |
| mmsellA3 (30¢) | 7 | −6.4¢ | 0 |

## Notes carried into the next run

- **This is run #1 of a family-wide stall.** If run #8 still shows zero new mmsell
  entries, escalate to an actual investigation (candidate-scan diagnostics, not just a
  DB freshness check) rather than reporting a third consecutive "unchanged."
- Worker health confirmed good (100 bot_runs in 2h, most recent seconds old) — this is
  not the worker being down. It's specifically the mmsell candidate scan finding
  nothing in range, while `weather_con*` continued trading normally in the same window.
- A2 (20¢) is still the only stop level saving money (+6.1¢ matched, n=11) — unchanged,
  still needs more matched pairs before it means anything.
- mmsell5's stop L30 K2 (+1.74 mean, +45 tail) is still the standout candidate at n=89,
  still 11 short of the n=100 gate — will very likely clear on the next run with fresh
  data, whenever the lull ends.
- mmsell2's exit-gate boundary reading is unchanged (+0.08/+21) — still 1 of 4 oscillating
  checks, still not a clear.
- mmsellA5 ticked from 5→7 entries during the lull — worth noting the strangle's
  both-tails condition is apparently less rate-limited by market supply than the
  ordinary mmsell10 entry, or this is just noise at n=7.
