# mmsell check status — state for the `mm_check_1` skill

**Owned by the `mm_check_1` skill.** Rewritten on every run; never hand-edit except to
fix a corrupted snapshot. Lives on the `mmsell-check-status` branch only — never merged
into the default branch, never touched by `ops`. Diffed against on the next run.

**Run #1 — 2026-07-25 02:01 PM CDT**

## Standing realizable read (mmsell fill model)

| book | n | realizable ¢/ct | verdict |
|---|---|---|---|
| mmsell | 3951 | +0.29 | low coverage |
| mmsell1 | 2560 | +0.34 | thin + |
| mmsell10 | 147 | +1.39 | REALIZABLE EDGE |
| mmsell11 | 281 | -0.79 | MIRAGE |
| mmsell2 | 1688 | +5.05 | low coverage |
| mmsell3 | 1058 | -0.87 | MIRAGE |
| mmsell4 | 226 | -0.78 | MIRAGE |
| mmsell5 | 115 | +0.66 | thin + (flat for 5 consecutive checks — no new settled trades) |
| mmsell6 | 377 | -0.24 | MIRAGE |
| mmsell7 | 75 | -0.77 | MIRAGE |
| mmsell8 | 45 | +0.86 | thin + |
| mmsell9 | 40 | +1.37 | REALIZABLE EDGE |

## Exit study — best exit per book (mmsell exit study)

| book | replay n | HOLD mean/tail | best rule | Δmean | Δtail |
|---|---|---|---|---|---|
| mmsell | 123 | +1.13 / -74 | vol V25 W6: +1.84/-62 | +0.71 | +12 |
| mmsell1 | 76 | +3.67 / -84 | stop L50 K1: +3.83/-47 | +0.16 | +37 |
| mmsell10 | 34 | +2.56 / +5 | none beats hold | 0 | 0 |
| mmsell11 | 45 | +2.91 / +5 | none beats hold | 0 | 0 |
| mmsell2 | 55 | +5.00 / -84 | stop L50 K1: +5.51/-43 | +0.51 | +41 |
| mmsell3 | 51 | +3.47 / +5 | none beats hold | 0 | 0 |
| mmsell4 | 42 | +5.00 / +5 | none beats hold | 0 | 0 |
| mmsell5 | 2 | +11.50 / +10 | none — exits hurt | negative | negative |
| mmsell6 | 41 | +3.66 / +5 | none beats hold | 0 | 0 |
| mmsell7 | 29 | +4.03 / +5 | none beats hold | 0 | 0 |
| mmsell8 | 22 | +7.86 / +5 | none beats hold (not fully confirmed — check full table) | ~0 | ~0 |
| mmsell9 | 19 | +5.53 / +5 | none beats hold | 0 | 0 |

## Notes carried into the next run

- No book has reached the exit-study n≥100 replayable gate yet (mmsell leads at 123
  settled-total but only 123 *replayable* is actually already past 100 — re-verify:
  this is `mmsell` control book's replay n, so it may already qualify for the gate
  check next run; confirm Δmean/Δtail sign is stable before calling it a promote).
- mmsell5 has had zero new settled trades for 5 consecutive checks — worth a
  standalone look at why (config, market availability) if it stays flat again.
- The 50¢ confirmed stop's apparent benefit has been shrinking as n grows on most
  books (mmsell1: +1.05→+0.16 mean improvement across recent checks; several books'
  earlier "disaster" got diluted by new wins rather than repeatedly confirmed). mmsell2
  is the one book with a durable, repeated positive result — watch whether it holds as
  n climbs further, since every other book's early positive read has decayed toward
  zero with more data.
- Entry pace: was in a post-World-Cup lull (~2-20 mmsell entries/day) for several
  checks; picked back up materially this run (replay pool nearly doubled from the
  prior check). Confirm current pace next run before assuming it's fully recovered.
