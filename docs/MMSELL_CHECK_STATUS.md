# mmsell check status — state for the `mm_check_1` skill

**Owned by the `mm_check_1` skill.** Rewritten on every run; never hand-edit except to
fix a corrupted snapshot. Lives on the `mmsell-check-status` branch only — never merged
into the default branch, never touched by `ops`. Diffed against on the next run.

**Run #5 — 2026-08-01 01:22 UTC** (first run with the anchor set live)

## ⚠ KNOWN GAP FOUND THIS RUN — both standing reads are blind to anchor stop exits

`mmsell_fill_model.py` and `mmsell_exit_study.py` both select only `status='settled'`
rows. The anchor stop books close their exits as **`status='closed_sl'`**, so **every
stopped trade is silently dropped from both tables.** This makes A1/A2/A3 read as
identical "+5.25¢, 100% win, REALIZABLE EDGE" books when A1's true resolved P&L
including its 9 stops is **−6.62¢/trade**. Until the scripts are fixed, read the anchor
books from a direct `paper_trades` status breakdown, NOT from the two tables below.

## Standing realizable read (mmsell fill model)

| book | n (run4→5) | realizable ¢/ct (run4→5) | verdict |
|---|---|---|---|
| mmsell | 4315→4383 | +0.29→+0.29 | low coverage |
| mmsell1 | 2818→2877 | +0.34→+0.34 | thin + |
| mmsell10 | 227→261 | +1.35→+1.33 | REALIZABLE EDGE (3rd consecutive check) |
| mmsell11 | 431→475 | −0.80→−0.74 | MIRAGE |
| mmsell2 | 1858→1898 | +5.06→+5.01 | low coverage (20.7%) |
| mmsell3 | 1214→1258 | −0.87→−0.84 | MIRAGE |
| mmsell4 | 363→407 | −0.79→−0.72 | MIRAGE |
| mmsell5 | 185→185 | +0.83→+0.83 | thin + — **STALLED, 0 new settled** |
| mmsell6 | 500→539 | −0.23→−0.22 | MIRAGE |
| mmsell7 | 89→117 | −0.88→−0.65 | MIRAGE |
| mmsell8 | 53→68 | +0.55→+0.56 | thin + |
| mmsell9 | 69→80 | +1.36→+1.33 | REALIZABLE EDGE |
| mmsellA1 | —→4 | —→+1.56 | **DO NOT USE** — excludes 9 stops (see gap above) |
| mmsellA2 | —→4 | —→+1.56 | **DO NOT USE** — excludes 1 stop |
| mmsellA3 | —→4 | —→+1.56 | n=4, no stops fired yet |
| mmsellA4 | —→3 | —→+1.49 | n=3 |

No verdict flips this run. Every book held its run-4 verdict.

## Anchor set — TRUE resolved numbers (direct from `paper_trades`, stops included)

Control = `mmsell10`, same entry (`lo=5,hi=10,maxyes=7`), read over the same window.

| book | mechanic | entries | open | settled | stops | stop rate | **true ¢/trade (all resolved)** |
|---|---|---|---|---|---|---|---|
| mmsellA1 | stop 12¢ K2 | 33 | 20 | 4 | **9** | **27%** | **−6.62** |
| mmsellA2 | stop 20¢ K2 | 25 | 20 | 4 | 1 | 4% | +0.40 |
| mmsellA3 | stop 30¢ K2 | 25 | 21 | 4 | 0 | 0% | +5.25 |
| mmsellA4 | vol entry gate | 22 | 19 | 3 | — | — | +5.33 |
| mmsellA5 | strangle | **0** | 0 | 0 | — | — | — |
| mmsell10 | CONTROL | — | 33 | 261 | — | — | +3.98 |

- Stop rates are monotone in level (27% / 4% / 0%) — the sweep is discriminating properly.
- **A1's settled-vs-stopped split is a resolution-speed artifact**: a stop closes now, a
  winner waits days. With 20 of 33 still open, −6.62¢ is a biased-early read, not a verdict.
- **Stop-and-re-enter churn confirmed**: A1 has 33 entries vs A2/A3's 25 because a stopped
  market is freed for re-entry (`KXTRUMPSAYCOMPANY-26AUG01-CHAT` was stopped twice, −9¢ then
  −23¢). Unmodeled in the backtest, which replayed one path per position. May need a cooldown.
- **A4's vol gate is firing**: 22 entries vs the ungated 25 → **12% rejection**, above the 5%
  floor below which the book would be a dead duplicate of mmsell10.
- **A5 still at zero entries** after ~32h. Needs 82 pairs. If still empty in a few days the
  both-tails condition may be too strict to ever test, not merely slow.
- Entry mix is ~70% the "Trump says word" family (`KXTRUMPSAY*`), not sports — heavily
  correlated legs on related settlement dates. Matters for any future 10× anchor sizing.

## Exit study — best exit per book (mmsell exit study)

| book | replay n (4→5) | HOLD mean/tail (4→5) | best rule this run | Δmean | Δtail | gate |
|---|---|---|---|---|---|---|
| mmsell | 482→549 | +1.63/−76 → +2.79/−76 | none beats hold (L60 K2 +1.27) | −1.51 | +13 | NO |
| mmsell1 | 328→386 | +3.43/−84 → +4.18/−83 | stop L50 K2 +3.75/−52 | −0.42 | +31 | NO |
| mmsell10 | 110→144 | +4.65/+5 → +4.89/+5 | none beats hold | 0 | 0 | NO — HOLD already clean |
| mmsell11 | 190→234 | +4.34/+5 → +4.91/+5 | stop L50 K2 +4.64/+5 | −0.27 | 0 | NO — flat tail |
| mmsell2 | 222→261 | +3.66/−84 → +4.60/−84 | stop L50 K2 +4.30/−56 | **−0.30** | +28 | **BOUNDARY — see notes** |
| mmsell3 | 202→246 | +4.50/+5 → +5.01/+5 | stop L50 K2 +4.75/+5 | −0.26 | 0 | NO — flat tail |
| mmsell4 | 177→221 | +4.68/+5 → +5.21/+5 | none beats hold (L50 K2 −0.43) | −0.43 | 0 | NO |
| mmsell5 | 71→71 | +4.69/+5 → unchanged | L40 K2 +4.77/−36 | +0.08 | −41 | **STALLED**, n<100 |
| mmsell6 | 160→199 | +4.74/+5 → +5.04/+5 | stop L50/L60 K2 +5.05/+5 | +0.01 | 0 | NO — negligible |
| mmsell7 | 42→70 | +4.93/+5 → +5.53/+5 | none beats hold | 0 | 0 | not yet (n<100) |
| mmsell8 | 30→45 | +7.97/+5 → +8.22/+5 | none beats hold | 0 | 0 | not yet (n<100) |
| mmsell9 | 47→58 | +5.49/+5 → +5.53/+5 | none beats hold | 0 | 0 | not yet (n<100) |
| mmsellA1–A4 | —→4/4, 4/4, 4/4, 3/3 | +5.25 or +5.33 / +5 | every rule Δ0, 0% exit | 0 | 0 | vacuous — see gap above |

## Notes carried into the next run

- **FIX THE SCRIPTS.** Both `mmsell_fill_model.py` and `mmsell_exit_study.py` must include
  `closed_sl` rows (or at minimum report them in a separate column) before the anchor set can
  be read from the standing tables at all. This is the single highest-value follow-up: right
  now the check reports the worst-performing anchor book as a "REALIZABLE EDGE".
- **mmsell2 sits EXACTLY on the −0.30 gate boundary** with Δtail +28 at n=261. Do NOT call
  this a clear. Its Δmean has now printed **+1.00 → −0.45 → −0.30** across three consecutive
  checks — it is oscillating around the boundary, not converging to it. The standing rule
  (3+ consecutive checks holding at growing n) is not met; it has never held twice in a row.
- mmsell1 improved from −0.57 → −0.42 but is still short of the gate.
- **mmsell5 is stalled** — 0 new settled trades and 0 new replayable positions since run #4
  (n=185, replay 71 both runs). First observed stall; if it repeats next run, investigate
  whether the book is still taking entries at all.
- The long-running pattern is unchanged: on every book with a genuine tail, the confirmed
  stop's benefit decays toward and through zero as n grows. The anchor set is the forward
  test of whether that holds when the stop executes for real instead of in replay — and A1's
  early 27% stop rate at ~−12¢ a stop is, so far, consistent with the pessimistic reading.
- mmsell10 has now held REALIZABLE EDGE for 3 consecutive checks (+1.35 / +1.33 / +1.33) at
  growing n (227→261). It is the only book meeting the "held across 3 checks" standard.
- mmsell7's verdict has been noisy (MIRAGE→dead→MIRAGE→MIRAGE); it held MIRAGE this run,
  which is the first repeat in four checks. Still treat as unsettled.
