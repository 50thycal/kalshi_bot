# ECON-REACT — post-release reaction-lag pin on Kalshi's scheduled economic-print markets

*Thesis written 2026-07-21 (idea-model Area 1: untouched market universes), before any
validation ran; the falsifiable predictions below are pre-registered so the test can't be
quietly re-scoped after the fact. Status: **pending probe** (`scripts/econ_react_study.py`).*

## One-liner

Right after a scheduled data release (CPI, non-farm payrolls, initial jobless claims, GDP,
PCE, PPI, ISM, retail sales, unemployment — mostly 8:30am ET), the winning temperature-style
bucket is *already determined by the printed number* but the Kalshi ladder converges to 100¢
slowly — buy the now-known winner while its quote still lags, held to settlement.

## Mechanism

- **What's mispriced:** in the minutes after the number lands, the winning bucket trades below
  its (now-certain) $1.00 settle. The information is public; the quote is slow.
- **Why it exists / who's on the other side:** Kalshi's econ ladders are retail-dominated and
  thinner/slower than the professional rates/equity complex that trades the same release. At
  8:30am the marginal Kalshi participant is slower to map "CPI printed 3.1%" onto "so bucket
  3.0–3.2% is the winner" than an HFT would be on a liquid instrument. This is an
  **observation-pin on a scheduled release** — the same information-lag family as the weather
  `obs`/`con` edge, but the "thermometer" is the just-released government statistic.
- **Why it persists:** the category is new-ish and retail-facing; the sharp money that would
  arbitrage it trades the deep rates/equity markets, not Kalshi's econ buckets, and the
  per-event capacity is too small to attract a dedicated desk.
- **Edge family:** event-conditional reaction / obs-pin on a scheduled release — the *one* econ
  angle the edge map calls worth screening. Directly promotes the **parked PINNED residual**
  (post-jobs-print +43.4¢/ct, n=36) that the scorecard said "needs its own pre-registration and
  a release-time audit." This IS that pre-registration.

## Honest prior (screen against the graveyard)

The research record is explicit that off-weather *inattention* pins die — "even backwater
gas/econ quotes converge once the answer is public; the inattention window that powers weather
`con` does not generalize off-weather at tradeable size." **This thesis only survives that
lesson if the edge is post-release *latency* (the quote takes minutes to finish converging a
KNOWN answer), not pre-release forecasting or a market that simply hasn't noticed a public
number.** The probe is built to tell those apart: it measures buyable EV *after* the release
moment, and the kill criteria fire if convergence is instant. If the winner snaps to ~100¢ at
release, this is dead — and that is a perfectly good ruling-out.

## Pre-registered, falsifiable predictions (the validation gate)

The probe (`scripts/econ_react_study.py`, ops-runnable, read-only public Kalshi REST) runs on
settled econ-print markets; it is a **structure/testability pre-stage** whose numbers feed
these predictions.

- **P0 — Testability (census).** There exist ≥ ~30 SETTLED econ-print markets with 1-min candle
  coverage and volume. FAIL → HOLD (untestable, re-run after more settle) — not a kill.
- **P1 — A post-release tradeable window exists.** On settled winning buckets, after the
  release (largest ≥10¢ up-jump), the market **keeps trading** and the winner is buyable for a
  window. PASS if ≥ ~40% of probed winners trade post-release AND the buyable EV (held to
  settlement, net of the `ceil(7·p·(1−p))` entry fee) at release+{1..30}min averages
  **≥ +2¢/ct**. **KILL if the winner snaps to ≥97¢ at release** (no lag) or there is no
  post-release tape.
- **P2 — Survives a release-time / contamination audit.** The effect holds when restricted to
  clean single-release events (exclude days with overlapping ADP/JOLTS/Fed prints and any event
  whose detected "release jump" is not near the true 8:30am ET slot). KILL if the apparent edge
  is only present on ambiguous-timestamp events (a mismatched-clock artifact).
- **P3 — Cadence / capacity.** Report per series. PASS is allowed even if carried by the
  highest-cadence series (**weekly initial jobless claims** — ~52 events/yr) since that is what
  makes the track record statistically readable and Kelly-sizable.
- **Decision rule (pre-committed):** build the paper book only if **P0 AND P1 AND P2**. A
  claims-only pass is acceptable if it carries the n. If P1 fails, log the ruling-out in
  `RESEARCH_JOURNAL.md` and close the econ-print family — no book. No re-scoping the window or
  the EV bar after seeing results.

## Probe plan

- **Script:** `scripts/econ_react_study.py` (this repo, allowlisted in `ops_runner.py`).
  Kalshi-public-API-only for v1 — no external release calendar, no futures feed — so it is
  self-contained and cheap. It (1) censuses the econ-print series' structure + settled capacity
  with a matched-series diagnostic, (2) measures winning-bucket convergence by time-to-close,
  and (3) detects the release jump and measures the post-release buyable-EV window.
- **No-lookahead construction:** EV is computed only from prices at/after the detected release
  minute, held to the known settlement; the winner label comes from `result='yes'` on the
  settled market (an outcome, used only to *select* the path, never to price a point before it
  occurred).
- **Measurement:** post-release buyable EV at release+{1,5,15,30}min and late-window (T-5..0)
  EV, in ¢ net of fee, with the fraction of markets that trade post-release; sliced by series
  and structure (bucket ladder vs threshold).
- **Promotion result:** a post-release (or robust late-window) buyable EV ≥ +2¢/ct that
  survives the P2 audit → build a paper book that, on each print, buys the released-number's
  bucket in the post-release window and holds to settlement.
- **v2 (only if v1 promotes):** add the public BLS/BEA/DoL release calendar to anchor the exact
  release timestamp (removes the jump-detection heuristic) and a consensus-vs-actual surprise
  feature.

## Cost + capacity

- **Fee/spread:** the `ceil(0.07·qty·P·(1−P)·100)`¢ fee is small when the winner is already
  ≥80¢ (~1¢) and near-zero at the tails; the live-board avg econ spread is wide (~17¢), so the
  probe measures EV against the actual `yes_ask` a taker crosses, not mid — an edge that only
  survives on mid is dead.
- **Capacity:** Economics is a live-board top category (~92M contracts, widest liquid spread).
  Monthly prints (CPI/jobs/PCE/PPI/GDP/retail) + **weekly claims** → a real sample accrues in
  months, not quarters, unlike lumpy one-off events.

## Correlation

- **Vs current book:** zero shared return driver with weather (temperature), `mmsell` (sports
  maker-sell), `theta` (crypto tails). Macro-print reaction is orthogonal to all of them.
- **Value to $100/mo:** uncorrelated ballast — worth more than its raw edge, since it does not
  move with any existing book. This is the most on-brief Area-1 promote.
