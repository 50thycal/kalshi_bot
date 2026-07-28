# mmsell check status — state for the `mm_check_1` skill

**Owned by the `mm_check_1` skill.** Rewritten on every run; never hand-edit except to
fix a corrupted snapshot. Lives on the `mmsell-check-status` branch only — never merged
into the default branch, never touched by `ops`. Diffed against on the next run.

**Run #4 — 2026-07-28 03:48 PM CDT**

## Standing realizable read (mmsell fill model)

| book | n | realizable ¢/ct | verdict |
|---|---|---|---|
| mmsell | 4315 | +0.29 | low coverage |
| mmsell1 | 2818 | +0.34 | thin + |
| mmsell10 | 227 | +1.35 | REALIZABLE EDGE |
| mmsell11 | 431 | -0.80 | MIRAGE |
| mmsell2 | 1858 | +5.06 | low coverage |
| mmsell3 | 1214 | -0.87 | MIRAGE |
| mmsell4 | 363 | -0.79 | MIRAGE |
| mmsell5 | 185 | +0.83 | thin + |
| mmsell6 | 500 | -0.23 | MIRAGE |
| mmsell7 | 89 | -0.88 | MIRAGE (flipped back from "dead" — opt +0.49 again) |
| mmsell8 | 53 | +0.55 | thin + (moved after 2 flat checks) |
| mmsell9 | 69 | +1.36 | REALIZABLE EDGE |

## Exit study — best exit per book (mmsell exit study)

**IMPORTANT: the mmsell1/mmsell2 gate-clear reported as "held" last run has REVERSED
this run** — both flipped to a negative Δmean as n roughly doubled again. See notes.

| book | replay n | HOLD mean/tail | best rule | Δmean | Δtail | gate (n>=100 + Δtail up + Δmean>=-0.3) |
|---|---|---|---|---|---|---|
| mmsell | 482 | +1.63 / -76 | none beats hold (best: stop L60 K2 +0.11/-64) | -1.52 | +12 | NO |
| mmsell1 | 328 | +3.43 / -84 | stop L50 K2: +2.86/-56 | **-0.57 (was +0.84 last run — REVERSED)** | +28 | **NO — no longer clears** |
| mmsell10 | 110 | +4.65 / +5 | none beats hold | 0 | 0 | NO — just crossed n=100, HOLD already clean |
| mmsell11 | 190 | +4.34 / +5 | stop L50 K2: +4.01/+5 | -0.34 | 0 | NO — same borderline/flat-tail shape |
| mmsell2 | 222 | +3.66 / -84 | stop L50 K2: +3.20/-58 | **-0.45 (was +1.00 last run — REVERSED)** | +26 | **NO — no longer clears** |
| mmsell3 | 202 | +4.50 / +5 | stop L50 K2: +4.18/+5 | -0.32 | 0 | NO — same shape as prior 2 checks |
| mmsell4 | 177 | +4.68 / +5 | none beats hold | 0 | 0 | NO — just crossed n=100, HOLD clean |
| mmsell5 | 71 | +4.69 / +5 | none beats hold (stop L40K2 +0.08 mean but Δtail -41) | ~0 | negative | not yet (n<100) |
| mmsell6 | 160 | +4.74 / +5 | stop L50/L60 K2: +4.75/+5 | +0.01 | 0 | NO — negligible, tail already clean |
| mmsell7 | 42 | +4.93 / +5 | none beats hold | 0 | 0 | not yet (n<100), unstalled this run |
| mmsell8 | 30 | +7.97 / +5 | none beats hold | 0 | 0 | not yet (n<100), unstalled this run |
| mmsell9 | 47 | +5.49 / +5 | none beats hold | 0 | 0 | not yet (n<100) |

## Notes carried into the next run

- **CORRECTION to last run's headline: mmsell1 and mmsell2's gate-clear did NOT hold.**
  As replay n roughly doubled again (mmsell1: 189->328, mmsell2: 129->222), both
  books' best rule (stop L50 K2) flipped from a clearly positive Δmean (+0.84, +1.00)
  to a clearly NEGATIVE one (-0.57, -0.45). This is the exact "shrinking benefit"
  pattern seen on every other book, just delayed — it did not stop at zero, it kept
  going negative. **Lesson reinforced: a gate-clear needs to hold across MULTIPLE
  checks at growing n before it's trustworthy — two checks was not enough.** No book
  currently clears the gate. Do not report a gate-clear as solid until it has held
  across at least 3 consecutive checks with materially growing n each time.
- No book clears the promote gate this run. The pattern across every book with a real
  (non-percentile-artifact) tail loss so far: the confirmed stop's benefit peaks early
  in a book's sample history and decays toward and through zero as n grows — consistent
  with hold-to-settlement + diversification being the right risk control, not an exit
  rule, for this family. Treat any future "positive Δmean" reading on a fresh book with
  the same skepticism now, not optimism.
- mmsell10, mmsell4, and mmsell6 all newly crossed n=100 replayable this run. None
  qualify — all have a clean HOLD tail (no second disaster yet), so there's nothing
  for a stop to usefully cut; this is the "healthy book, no rule needed" case, distinct
  from mmsell1/mmsell2/mmsell3/mmsell11's "real tail loss, stop doesn't clearly help
  enough" case.
- mmsell7 and mmsell8, flagged as stalled last run, both resumed adding replayable
  trades this run (31->42, 24->30) — the stall was transient, not a real issue.
- mmsell7's standing-read verdict flipped MIRAGE->dead->MIRAGE again across the last
  three checks (opt hovering near zero) — treat this book's verdict as noisy/unsettled
  rather than reporting each flip as a real change until it stabilizes.
- Growth this run (1216->2061, +845) was the largest jump yet — entry/settlement pace
  looks strong and sustained across 3 consecutive strong-growth checks now.
