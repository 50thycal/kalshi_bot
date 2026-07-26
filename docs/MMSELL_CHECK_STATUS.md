# mmsell check status — state for the `mm_check_1` skill

**Owned by the `mm_check_1` skill.** Rewritten on every run; never hand-edit except to
fix a corrupted snapshot. Lives on the `mmsell-check-status` branch only — never merged
into the default branch, never touched by `ops`. Diffed against on the next run.

**Run #2 — 2026-07-26 09:25 AM CDT**

## Standing realizable read (mmsell fill model)

| book | n | realizable ¢/ct | verdict |
|---|---|---|---|
| mmsell | 4093 | +0.30 | low coverage |
| mmsell1 | 2657 | +0.35 | thin + |
| mmsell10 | 174 | +1.38 | REALIZABLE EDGE |
| mmsell11 | 334 | -0.78 | MIRAGE |
| mmsell2 | 1751 | +5.07 | low coverage |
| mmsell3 | 1111 | -0.87 | MIRAGE |
| mmsell4 | 272 | -0.76 | MIRAGE |
| mmsell5 | 148 | +0.91 | thin + (first new settled trades after 5 flat checks) |
| mmsell6 | 416 | -0.23 | MIRAGE |
| mmsell7 | 77 | -0.80 | MIRAGE |
| mmsell8 | 47 | +0.88 | thin + |
| mmsell9 | 54 | +1.33 | REALIZABLE EDGE |

## Exit study — best exit per book (mmsell exit study)

| book | replay n | HOLD mean/tail | best rule | Δmean | Δtail | gate (n>=100 + Δtail up + Δmean>=-0.3) |
|---|---|---|---|---|---|---|
| mmsell | 261 | +2.05 / -75 | none beats hold (best: stop L60 K2 +1.27/-64, Δmean -0.78) | -0.78 | +11 | NO — fails Δmean at gate size |
| **mmsell1** | 169 | +3.30 / -83 | **stop L50 K2: +4.24/-52** | **+0.94** | **+31** | **YES — first clean gate-clear** |
| mmsell10 | 59 | +3.85 / +5 | none beats hold | 0 | 0 | not yet (n<100) |
| mmsell11 | 95 | +5.33 / +5 | none beats hold | 0 | 0 | not yet (n<100) |
| **mmsell2** | 117 | +3.03 / -85 | **stop L50 K2: +4.13/-66** | **+1.10** | **+19** | **YES — first clean gate-clear** |
| mmsell3 | 101 | +5.47 / +5 | borderline: stop L50 K2 +5.17/+5 (Δmean -0.30, Δtail 0.0) | -0.30 | 0 | NO — tail not clearly up |
| mmsell4 | 87 | +6.29 / +5 | none beats hold | 0 | 0 | not yet (n<100) |
| mmsell5 | 35 | +3.31 / -89 (first real loss in sample) | stop L40 K2: +4.83/-36 | +1.51 | +53 | not yet (n<100) |
| mmsell6 | 78 | +5.00 / +5 | none beats hold | 0 | 0 | not yet (n<100) |
| mmsell7 | 31 | +4.19 / +5 | none beats hold | 0 | 0 | not yet (n<100) |
| mmsell8 | 24 | +7.92 / +5 | none beats hold | 0 | 0 | not yet (n<100) |
| mmsell9 | 33 | +5.58 / +5 | none beats hold | 0 | 0 | not yet (n<100) |

## Notes carried into the next run

- **mmsell1 and mmsell2 are the first two books to clear the exit-study promote gate**
  cleanly (n>=100 replayable, Δp5 tail clearly up, Δmean positive) — both via
  **stop L50 K2**. Watch whether this holds as n climbs further before treating it as
  final; every earlier "positive at small n" read on other books has decayed toward
  zero with more data, so one confirming check at a larger n would meaningfully
  increase confidence this is real rather than a still-early read.
- **mmsell (control) flipped the other way** — at n=123 (last run) its best rule
  (vol V25 W6) showed Δmean +0.71; at n=261 now the same rule shows Δmean -0.86. No
  rule beats hold for the control book anymore. This is the shrinking-benefit pattern
  fully playing out for the biggest, oldest book.
- **mmsell3 just crossed n=101** but sits right at the Δmean=-0.30 boundary with a
  flat (not improved) tail — doesn't clearly qualify. Worth a clean re-check once it
  has meaningfully more than 101.
- **mmsell5 finally got new settled trades** after 5 consecutive flat checks (115->148
  settled, 2->35 replayable) and immediately shows its first real loss in the
  replayable sample (tail -89) with a promising stop L40K2 rescue (+53 tail, +1.51
  mean) — too small (n=35) to trust yet, but this book is no longer stalled.
- Entry/settlement pace has clearly recovered — replayable pool nearly doubled again
  this run (539->1090), following last run's near-doubling too. Two strong-growth
  checks in a row; no longer describe this as a post-World-Cup lull unless a future
  check shows it stalling again.
