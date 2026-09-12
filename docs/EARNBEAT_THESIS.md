# EARNBEAT — the consensus-anchoring bias on Kalshi's company-KPI markets

*Thesis written 2026-09-12, before any validation ran; the falsifiable predictions below are
pre-registered and must not be re-scoped post-hoc. Promoted from `docs/IDEA_MODEL_20260912.md`
(candidate N1, the anti-anchor slot). Status: **pending recon census**
(`scripts/kalshi_kpi_census.py`) — and **HOLD-by-default under the venue-age rule** until that
census shows a gradeable tape.*

**New venue, new mechanic, no ancestor.** Kalshi's Public Companies Hub (launched 2026-08-04;
KPI lines set from Fiscal.ai data, earnings calendar from Benzinga) lists ladders on individual
reported metrics — revenue, EPS, subscribers, deliveries — settling systematically off the
filing. Nothing in the record has touched it: the only earnings-adjacent candidate ever screened
(EARN, 2026-07-10, "one-off, thin") was a post-release *reaction-latency* idea on a handful of
mention markets, killed for capacity. The hub makes the category recurring (hundreds of settles
per earnings season) and the mechanic here is calibration, not latency.

## One-liner

Reported earnings beat the published consensus far more often than they miss; if retail anchors
a KPI ladder's centre on the consensus number, the "at or above consensus" rung is structurally
underpriced — buy it (or maker-sell its complement) at a measured, pre-registered discount.

## Mechanism

- **What mispricing:** the consensus estimate is the most visible number on every earnings page,
  so a ladder rung at the consensus level trades near 50¢ as if consensus were the median
  outcome. It is not: FactSet reports 87% of S&P 500 companies beat EPS consensus in Q2 2026,
  and 76–78% over the 5- and 10-year averages, because companies guide low and analysts follow.
  Revenue beats run lower (~60–65%) and differ by sector — which is why P1 is per-metric.
- **Why it exists / who's on the other side:** retail trading the KPI hub as a stock-picking
  game; the anchoring heuristic is exactly the "instrument's story rather than the settlement
  statistics" flow PIN15 and FREEZE priced. Institutions with whisper numbers exist but are
  size-limited on a five-week-old venue.
- **Why it persists:** the beat base rate is well known *to equity analysts* and unknown to
  most retail; the hub is new; per-market volume is thin enough that nobody has bothered.
- **Edge family:** directional taking on a **public, deterministic base rate** — not a homegrown
  model (the family that is 0-for-2 here) and not price-history calibration on a mature market
  (dead). Closest living relative: the favorite-longshot calibration `kalshi_flb` measured, which
  is real at the calibration level and accrues to whoever is on the right side of a persistent
  bias. The prior is **medium**: the bias is documented; whether Kalshi's ladder actually sits
  at the consensus is the empirical question.

## Confronting the graveyard (required)

- **TFAV** (favorite-buy, −3.6¢): a *price-history* favorite bias on crypto that accrued to
  makers. EARNBEAT's signal is external to the price (the consensus number vs the beat base
  rate); P2 requires the edge to clear both-leg fees as a taker so it cannot hide in a fill
  model.
- **ECON-REACT / PINNED / CPINOW** (economics, killed or held): scheduled *macro* prints are the
  most-analyzed numbers in finance and their Kalshi markets are thin. Company KPIs are hundreds
  of independent prints per quarter on a hub built to list them — a different capacity regime
  and a different counterparty.
- **Testability-NOW** (FREEZE/COMPIN's lesson): the hub is ~5 weeks old, so this thesis is
  written as a HOLD with a census, not a probe. The trigger is named below.

## Pre-registered predictions (¢ per contract, net of both-leg fees `ceil(0.07·P·(1−P)·100)`)

- **P1 — The consensus rung is underpriced, per metric.** For settled KPI threshold markets
  whose strike is within ±1% of the published consensus (Benzinga/Fiscal.ai number as of the
  point-in-time date), the mid quoted **24 h before the scheduled report** is at least **8
  percentage points below** the realized beat rate for EPS markets and **5 points** for revenue
  markets, n ≥ 60 per metric. **KILL if the gap is < 3 points on both metrics.**
- **P2 — It survives cost as a taker.** Buying YES on the consensus rung at the 24 h-before ask
  and holding to settlement nets **≥ +3¢/ct** after both-leg fees over the pooled n ≥ 120.
  **KILL if ≤ +1¢** (a bias that only exists gross is dead).
- **P3 — Not a single-quarter or single-sector artifact.** P2 holds in split-half by report
  date AND after removing the single most profitable sector. **HOLD** (not kill) if one half
  fails at n < 60 — thin sample.
- **P4 — Point-in-time hygiene.** The 24 h-before quote must precede the report timestamp from
  the earnings calendar, never `close_time` (markets close after the filing). Any market whose
  quote window overlaps the report is excluded; if > 20% are excluded, the probe is
  re-specified before any number is read.
- **Decision rule:** paper book only if P1 ∧ P2 ∧ P3 ∧ P4. P1 fail on both metrics → the
  consensus-anchoring premise is closed; record it. Execution variant (taker vs maker-sell of
  the complement) is chosen by the probe's fill read, never by re-reading P2.

## Probe plan (staged — recon census FIRST)

- **Recon census (step 1, cheap, built):** `scripts/kalshi_kpi_census.py` — enumerates
  settled/open KPI-hub markets (strict series/title classifier, matched series dumped), counts
  and volume by type (`kpi_threshold` / `kpi_range` / `mention`), settles per ISO week (accrual
  rate), and a **calibration pre-read**: hourly-candle mid at close−7d and close−48h bucketed by
  price band → n, mean price, realized YES rate, gap. The 7d/48h pair flags post-release
  contamination (a ≥30¢ jump). Verdict TESTABLE-NOW only if ≥ 100 settled threshold markets
  with volume and ≥ 60 readable pre-report quotes; else **HOLD (accrual)** with the trigger
  **re-run the week of 2026-11-09** (after the Q3 season's bulk reports).
- **Full probe (step 2, only if the census clears):** a consensus-keyed study — for each
  settled threshold market, the consensus number and scheduled report time (Benzinga calendar,
  public pages; if not web-fetchable from the runner, the probe is BLOCKED_DATA and says so),
  strike-vs-consensus distance, the T−24h quote, both-leg fees, P1–P4. Needs allowlisting:
  **yes** (new script).
- **Dataset + provenance:** public Kalshi REST (events, candlesticks, trades) + a public
  earnings-calendar source kept in its own table/CSV with the fetch timestamp; never mixed with
  Kalshi rows silently.
- **No-lookahead construction:** consensus as of T−24h (not the final pre-report revision if it
  differs); quotes strictly before the report timestamp; results from the settled market.
- **Measurement:** per-metric gap (P1), net ¢/ct taker (P2), split-half + sector drop (P3),
  exclusion rate (P4); plus the maker-sell-complement variant's realizable ¢/ct through
  `mmsell_fill_model`'s price→fill curve for the execution decision.
- **Promotion result:** P1–P4 pass → register `earnbeat` in Experiment OS as a paper book riding
  the earnings calendar; each quarter is an epoch.

## Cost + capacity

- **Fee/spread math:** buying at ~45–55¢ is the worst fee band — ~1.7¢/leg, ~3.5¢ round trip;
  P2's +3¢ bar is *after* that. Spread on a thin new hub is the second cost and is inside the
  ask-based measurement.
- **Adverse selection:** none as a taker; if the probe chooses the maker-sell-complement
  variant, the mmsell fill-realism haircut (~2¢ live) applies and is measured, not assumed.
- **Capacity:** lumpy by calendar — hundreds of settles in the six weeks of each earnings
  season, near zero between. A track record is quarters, not weeks; size per market is small
  on a young hub. This is the honest weak axis and why the thesis holds until the census clears.

## Correlation

- **Vs current book:** no shared driver with `Fmmsell10` (sports cheap tails), PERPMM (crypto
  perp micro-reversion) or METALHALT (metals calendar). Driver = corporate guidance behaviour
  and retail anchoring; settles on filings.
- **Value to $100/mo:** the anti-anchor slot — a category the portfolio has never touched,
  with a documented external base rate rather than a homegrown model. If the census comes back
  thin, the cost was one script and a dated trigger.

## Results — census run 1, 2026-09-12

Ops `kpi-census-1`, code `2ea29a04`, run after PR #395 merged (16,000 settled / 14,958 open
events scanned). Verdict **as printed: HOLD (ACCRUAL)** — the pre-registered floors are not met.

| measure | value | floor |
|---|---|---|
| settled `kpi_threshold` markets with volume | 43 (46 settled, $43k total) | 100 |
| readable pre-report quotes (48 h before close) | 36 | 60 |
| open `kpi_threshold` markets right now | 396 | — |
| settled `mention` markets | 3,385 ($24.5M) — dominated by *political* mention series (`KXTRUMPMENTION` etc.), not earnings calls | n/a |

**What the calibration pre-read shows, and why it is not evidence.** In the 30–70¢ bands the
realized YES rate ran 50–80% against mean prices of 38–59¢ (gaps +11 to +37 points) at both the
7-day and 48-hour reads — the *sign* the anchoring thesis predicts. Per band n is 4–5. The record's
top process risk is exactly this shape (five small-n mirages before), so this is recorded as a
reason the census is worth re-running, not as a result. One market moved ≥30¢ between the two
reads (the post-release contamination flag); the full probe keys on the earnings-calendar
timestamp, never `close_time`.

**Trigger unchanged:** re-run the week of **2026-11-09**, after the Q3 season's bulk reports; the
396 open threshold markets are the accrual. Nothing is registered; no book, no gate.
