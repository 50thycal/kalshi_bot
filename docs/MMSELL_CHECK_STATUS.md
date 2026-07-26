# mmsell check status — state for the `mm_check_1` skill

**Owned by the `mm_check_1` skill.** Rewritten on every run; never hand-edit except to
fix a corrupted snapshot. Lives on the `mmsell-check-status` branch only — never merged
into the default branch, never touched by `ops`. Diffed against on the next run.

**Run #3 — 2026-07-26 03:00 PM CDT**

## Standing realizable read (mmsell fill model)

| book | n | realizable ¢/ct | verdict |
|---|---|---|---|
| mmsell | 4119 | +0.30 | low coverage |
| mmsell1 | 2678 | +0.36 | thin + |
| mmsell10 | 185 | +1.37 | REALIZABLE EDGE |
| mmsell11 | 349 | -0.73 | MIRAGE |
| mmsell2 | 1764 | +5.09 | low coverage |
| mmsell3 | 1126 | -0.85 | MIRAGE |
| mmsell4 | 283 | -0.71 | MIRAGE |
| mmsell5 | 155 | +0.89 | thin + |
| mmsell6 | 428 | -0.21 | MIRAGE |
| mmsell7 | 78 | -0.80 | dead (paper- too) — flipped from MIRAGE (opt went negative) |
| mmsell8 | 47 | +0.88 | thin + (flat n for 2nd straight check) |
| mmsell9 | 56 | +1.33 | REALIZABLE EDGE |

## Exit study — best exit per book (mmsell exit study)

| book | replay n | HOLD mean/tail | best rule | Δmean | Δtail | gate (n>=100 + Δtail up + Δmean>=-0.3) |
|---|---|---|---|---|---|---|
| mmsell | 286 | +3.62 / -75 | none beats hold (best: stop L60 K2 +2.62/-63, Δmean -1.00) | -1.00 | +12 | NO — confirmed fail, control book decay continues |
| **mmsell1** | 189 | +4.07 / -83 | **stop L50 K2: +4.92/-48** | **+0.84** | **+35** | **YES — HELD on 2nd check (was +0.94/+31 last run)** |
| mmsell10 | 69 | +4.10 / +5 | none beats hold | 0 | 0 | not yet (n<100) |
| mmsell11 | 109 | +5.67 / +5 | stop L50 K2: +5.39/+5 | -0.28 | 0 | NO — just crossed n=100, tail flat, doesn't qualify |
| **mmsell2** | 129 | +4.03 / -84 | **stop L50 K2: +5.03/-57** | **+1.00** | **+27** | **YES — HELD on 2nd check (was +1.10/+19 last run)** |
| mmsell3 | 115 | +5.77 / +5 | stop L50 K2: +5.51/+5 | -0.26 | 0 | NO — same borderline-mean/flat-tail pattern as last run |
| mmsell4 | 98 | +6.53 / +5 | none beats hold | 0 | 0 | not yet (n<100, close) |
| mmsell5 | 42 | +4.14 / +5 (tail HEALED — was -89 at n=35 last run; percentile-artifact, not a fix) | stop L40 K2: +5.40/+5 (Δmean+1.26, no tail to cut) | +1.26 | 0 | not yet (n<100) |
| mmsell6 | 89 | +5.29 / +5 | none beats hold | 0 | 0 | not yet (n<100) |
| mmsell7 | 31 | +4.19 / +5 | none beats hold | 0 | 0 | **stalled — same n as last run, zero new replayable** |
| mmsell8 | 24 | +7.92 / +5 | none beats hold | 0 | 0 | **stalled — same n as last run, zero new replayable** |
| mmsell9 | 35 | +5.57 / +5 | none beats hold | 0 | 0 | not yet (n<100) |

## Notes carried into the next run

- **mmsell1 and mmsell2's gate-clear HELD on a second, larger-n check** (mmsell1: n
  169->189, Δmean +0.94->+0.84, Δtail +31->+35; mmsell2: n 117->129, Δmean +1.10->+1.00,
  Δtail +19->+27). Both still comfortably clear (n>=100, Δp5 up, Δmean well above
  -0.3). This is now a real, twice-confirmed signal — stop L50 K2 is the rule on both.
  One more confirming check at meaningfully larger n would make this solid enough to
  discuss promoting into config.
- **mmsell5's exit-study tail "healed" again** (was -89 at n=35 last run, now +5 at
  n=42) — this is the percentile-statistic artifact explained in this skill's
  background section (below ~n=20 the tail stat is the single worst trade; the earlier
  -89 read was likely already stale by the time it was reported). Treat any single
  sharp tail swing on a thin-n book with suspicion until it's confirmed at n>=~40-50.
- **mmsell7 flipped verdict** in the standing read: MIRAGE -> "dead (paper- too)" as
  its blended-paper number went negative for the first time. Its exit-study replay n
  (31) and mmsell8's (24) were BOTH completely flat vs last run — zero new replayable
  trades for either book in this window, worth a look if they stay flat next run too.
- **mmsell (control) continued decaying** — best rule's Δmean went from -0.78 (last
  run) to -1.00 (now) as n grew to 286. No longer ambiguous: hold-to-settlement is
  simply better than any exit rule for this book at scale.
- **mmsell11 and mmsell3 both just crossed n=100** but neither clears the gate — same
  "Δmean borderline negative, Δtail flat at +5" shape both times, because HOLD's own
  tail is already clean (no second disaster yet) so there's nothing for a stop to
  usefully cut. Structurally different from mmsell1/mmsell2, which both have a real,
  persistent tail loss the stop is rescuing.
- Overall growth this run (1090->1216, +126) was more modest than the two prior
  near-doublings — plausibly normal week-to-week variance rather than a renewed lull;
  confirm pace next run before flagging it either way.
