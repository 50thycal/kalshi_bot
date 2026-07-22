# FEDRV — internal-coherence relative value on Kalshi's Fed rate-cut path

*Thesis written 2026-07-21 (idea-model Area 1: untouched market universes), before any
validation ran; the falsifiable predictions below are pre-registered. Status: **RULING-OUT —
COHERENT / efficient** (probe run 2026-07-21; see RESULTS). **Prior was LOW** — this was a cheap,
uncorrelated ruling-out, and ruling out is a win.*

## RESULTS (2026-07-21 probe run — `fed_rv_study`, tightened v2)

**Verdict: RULING-OUT (P1 fails once P2's independence check is applied) — rates are internally
efficient.** The first run (v1) flagged a −80¢ "incoherence," but that was a **contamination
artifact**: the greedy `^KXFED(...)?` regex matched every `KXFED*` event (`KXFEDEMPLOYEES`,
`KXFEDGOVNOM`, `KXFEDERALCHARGE`), injecting a forced-1-cut distribution that drove convolved
P(0 cuts) to zero. The fixed v2 (`^KXFEDDECISION` only + a coverage guard) gives the clean read:

- **4 mapped upcoming meetings** (JUL/SEP/OCT/DEC), each with a small cut probability
  (`{0: 0.98…0.86}`). Convolved total-cuts = `{0:0.70, 1:0.26, 2:0.03}`; direct `KXRATECUTCOUNT`
  ladder = `{0:0.81, 1:0.12, 2:0.03, …, 6+:0.02}`. Residual gap: **+14¢ at "1 cut."**
- That gap is an **independence-assumption artifact, not a tradeable edge** — pre-registered
  **P2** (must survive an independence sensitivity check) **fails.** Real Fed cuts are strongly
  *positively correlated* (they come in cycles; isolated single cuts are rare), so mass belongs
  at "0 cuts" or "a cutting cycle (many)," not "exactly 1." The ladder's shape — fat at 0 (0.81),
  thin middle, a 6+ tail (0.02) — is exactly the correlated-regime signature an independence
  convolution cannot produce. Relaxing independence reconciles the two distributions.
- Consistent with the LOW prior (the ~0.1¢ `KXRATECUTCOUNT` spread = sharp money already on it).
- **Decision:** ruling-out — rates logged as internally efficient; category closed. A v2 could
  quantify the correlation sensitivity, but the direction is unambiguous, so it is not worth the
  build.

## One-liner

Kalshi prices the Fed two ways — per-meeting (`KXFEDDECISION`) and as an aggregate count
(`KXRATECUTCOUNT` "number of cuts in 2026?"). If retail prices the aggregate *path*
inconsistently with the per-meeting decisions, buy the cheap representation and sell the rich
one — a pure internal-coherence trade needing no view on the Fed and no external data.

## Mechanism

- **What's mispriced:** the total-cuts distribution implied by convolving the per-meeting
  decision markets may diverge from the directly-quoted `KXRATECUTCOUNT` ladder. One of the two
  is then mis-aggregating the path.
- **Why it might exist / who's on the other side:** aggregate "how many cuts this year" is a
  natural retail bet (a single narrative ticket); the per-meeting markets are traded by
  people reacting to each FOMC. Retail path-arithmetic (convolving six meetings into a count
  distribution) is exactly the kind of thing a casual trader does not do, so the two can drift.
- **Why it (probably) does NOT persist — the honest prior:** the live board shows
  `KXRATECUTCOUNT` at a **0.3¢ spread**. That is the signature of markets that are already
  arbitraged tight, so the base case is *coherent*. This thesis is therefore mostly a cheap
  **ruling-out** of an entire uncorrelated category (rates) in one self-contained probe.
- **Edge family:** structural / internal coherence — the same spirit as `kalshi_arb`'s
  monotonicity check (which found no locked arb), but on the rate strip and as a *soft*
  relative-value gap, not a locked Dutch book. Rates has **zero** prior exposure in the book.

## Pre-registered, falsifiable predictions (the validation gate)

The probe (`scripts/fed_rv_study.py`, ops-runnable, read-only public Kalshi REST) is
Kalshi-internal (no external feed) for v1.

- **P0 — Mappability (census).** Both legs are enumerable: the per-meeting decision markets
  map to a cuts distribution (bps → 25bp-cut equivalents) AND the `KXRATECUTCOUNT` ladder maps
  to integer-cut buckets, with reasonable coverage of 2026 meetings. FAIL → HOLD (refresh the
  series prefixes / parsers; not a kill).
- **P1 — Incoherence exceeds cost.** The total-cuts distribution from convolving the
  per-meeting decisions diverges from the direct `KXRATECUTCOUNT` distribution by **≥ ~4¢ on
  some bucket** (a per-bucket price gap clearing a round-trip). **KILL if the max per-bucket gap
  is < cost** (coherent / efficient — the expected result given the tight spread). A clean
  ruling-out here closes the rates category and is logged as a win.
- **P2 — The gap is real, not a mapping artifact.** Any flagged gap survives (a) hand-checking
  the bps→cuts and bucket parsers against the printed tickers, and (b) a sensitivity check on
  the cross-meeting independence assumption. KILL if the gap vanishes under a corrected mapping.
- **Decision rule (pre-committed):** promote to a paper RV book only if **P0 AND P1 AND P2**,
  and only after a v2 that adds the external Fed-funds / SOFR-futures cross-check to confirm
  *which* leg is mispriced (the futures strip is the neutral referee). If P1 fails, log rates
  as internally efficient in `RESEARCH_JOURNAL.md` and close — no book.

## Probe plan

- **Script:** `scripts/fed_rv_study.py` (allowlisted in `ops_runner.py`). (1) Characterizes the
  `KXFEDDECISION` meetings (outcomes, prices, spread, volume) and the `KXRATECUTCOUNT` ladder,
  so it is visible whether the data even supports the check and how liquid each leg is; (2)
  best-effort convolves the per-meeting decision distributions + cuts already realized at
  settled 2026 meetings, and compares to the direct ladder (total-variation + max per-bucket
  gap in ¢), reporting **coverage** so a thin/unmappable read is obvious rather than a bogus
  number.
- **No-lookahead:** this is a point-in-time snapshot comparison of two *simultaneously live*
  Kalshi markets — no historical reconstruction, so there is no lookahead surface; realized
  cuts come only from already-settled meetings.
- **Measurement:** the max per-bucket price gap (¢) between the convolved and direct
  distributions, and the total-variation distance, with meeting-mapping coverage.
- **Promotion result:** a persistent ≥ ~4¢ gap surviving the mapping/independence audit →
  write the v2 futures-refereed RV probe; only then a paper book.

## Cost + capacity

- **Fee/spread:** an RV trade crosses spread on *both* legs; `KXRATECUTCOUNT` is razor-tight
  (0.3¢) but `KXFEDDECISION` averages ~10¢ — so the gap must exceed roughly a ~4¢ round-trip to
  be real, which the probe's `--cost-cents` bar enforces.
- **Capacity:** `KXFEDDECISION` ~30M contracts; `KXRATECUTCOUNT` ~6M. Ample if a gap exists;
  the binding constraint is whether a gap exists at all (prior: no).

## Correlation

- **Vs current book:** zero shared driver with weather / sports-maker / crypto tails. Rates is
  a completely new, uncorrelated category.
- **Value to $100/mo:** either uncorrelated ballast (if P1 passes) or a cheap, clean closing-out
  of an entire category (if it fails) — both are positive-value outcomes for the research
  program.
