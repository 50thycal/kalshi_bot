# GRIDPIN — ERCOT daily-peak settlement pin

*Thesis written 2026-09-29, before any validation ran. The predictions below are pre-registered
and are not re-scoped after results. Promoted from [`IDEA_MODEL_20260929.md`](IDEA_MODEL_20260929.md)
(S1) on Calvin's request ("run the GRIDPIN next"). Status: **CENSUS BUILT — not yet run.**
Census script: `scripts/kalshi_gridpin_census.py`.*

**One-liner.** Late in the Texas day, after the afternoon peak, the day's maximum *hourly-integrated*
ERCOT load is essentially known from ERCOT's own published hourly values. Yet the
`KXTXERCOTPEAKD` ladder trades until 23:55 CT. Buy the rung sides the published hourly running max
already decides, where the ask still leaves a margin. Lean hardest on rungs that the *instantaneous*
dashboard peak crossed but the *hourly* value did not.

**Mechanism**

- **What is mispriced.** After the peak hour (typically 16:00–19:00 CT), load falls into the evening,
  so the ladder should collapse to about 0 or 100 around the hourly running max. The claim is that
  some rungs keep trading at interior prices after that.
- **The mechanics gap, which is the load-bearing difference from the dead `obs` book.** The contract
  settles on the **highest *hourly* total system load**. The number a casual trader watches is the
  ERCOT dashboard's **5-minute instantaneous demand**, and its intraday peak exceeds the hourly
  integral of the same hour. If that excess is comparable to the 500 MW strike spacing, then rungs
  between the hourly max and the instantaneous max are **overpriced YES**. The trader who saw the
  dashboard cross 81,500 MW believes YES is locked, while the settlement value says NO. That is
  PIN15's "60 s average versus spot" shape, which is the one pin shape that has passed. It is not
  `obs`'s "the thermometer is public and the quote is slow" shape, which lost at −3.7¢ (n=171).
- **Who is on the other side.** Launch-era retail on a 4-week-old, thin listing, together with
  resting orders that are not re-marked after the peak. Power traders who model ERCOT load
  professionally are unlikely to bother with a ladder of about 11k contracts a day. This is the same
  reason the new-venue counterparty is attractive and the same reason the venue is data-poor.
- **Why the taker is not adversely selected here.** We cross only when a published hourly value
  already decides the rung. The resting side we hit is stale, not informed. The hourly value
  **arrives with a lag** and may be restated between the real-time posting and the "reported"
  settlement figure. That publication and revision risk is the one thing that could put the informed
  trader on the other side, so C2 below measures it before anything else.
- **Edge family.** Observation pin / mechanics blindness (the only family with a pass), on an energy
  universe with zero portfolio exposure.

**Pre-registered predictions.** The unit is a (rung, event-day) pair. Entries are taker-at-ask, one
contract, held to settlement, net of the taker fee
`ceil(0.07·qty·P·(1−P)·100)`.

- **C0: census, the universe exists.** At least **40 settled event-days**, **≥ 150 settled rungs with
  any volume**, and **≥ 60 rungs with at least one trade after 19:00 CT** (the post-peak window).
  Otherwise **HOLD (accrual)**. Trigger: re-run when the day count reaches 60. The census may return
  HOLD today: about 28 days are listed.
- **C1: census, a gradeable settlement source is reachable.** A keyless ERCOT hourly-load history
  (the actual system load report or the hourly load archive) must be fetchable from the ops runner,
  and it must reproduce Kalshi's `expiration_value` / result on **≥ 95%** of sampled settled rungs.
  Otherwise **BLOCKED_DATA**, with no full probe.
- **C2: census, the publication lag and revisions are tolerable.** The median lag from hour end to the
  hourly value's first public posting must be **≤ 90 min**, and posted values must not flip any
  sampled rung's outcome at settlement (revision flips **≤ 2%** of rungs). Otherwise **KILL
  (premise)**, because the "known" value is not known in time or is not the value that settles.
- **P1: the decided-side discount exists and clears cost.** Take decided rungs after the posted hourly
  running max decides them: YES for strikes below the max, and NO for strikes at least 1,000 MW above
  the max after 21:00 CT. Mean net P&L must be **≥ +2.0¢/contract** with **≥ 60 entries** and a
  settlement accuracy of **≥ 98%**. **KILL** if the mean is **≤ +0.5¢** or accuracy is **< 95%**.
- **P2: it is not just favorite drift** (PINNED's control). The P1 entries must beat a control of
  *undecided* rungs quoted in the same 85–97¢ band at the same hour by **≥ 1.5¢/contract**.
  **KILL** if they do not.
- **P3: the mechanics sub-claim** (graded only if a 5-min history exists; see below). In the gap
  class, rungs whose strike lies between the day's hourly max and its instantaneous 5-min max, the
  YES side must trade above its settled value on average. **PASS** if taking NO at ask in that class
  nets **≥ +3¢** at **n ≥ 20**. If P3 FAILS while P1 passes, the edge is the attention shape that
  lost on weather: it is **not promoted to paper** without a fresh pre-registration.
- **P4: capacity.** The median depth available at or better than the entry price on qualifying
  entries must be **≥ 5 contracts**, and **≥ 1 qualifying entry per 2 event-days**. Otherwise this is
  a hobby: HOLD, never a book.
- **Decision rule.** A paper book (a new XOS experiment) is proposed only if C0–C2 pass **and** P1,
  P2 and P4 pass, with P3 PASS **or** P3 untestable. If P3 is untestable, the paper book carries a
  pre-registered P3 forward check at n=20. Any KILL closes the ERCOT pin. Economic *forecasting* of
  the ladder (S8) is not reopened by a GRIDPIN pass.

**Probe plan (staged: census first)**

- **Step 1, recon census** (about 60–80 lines, read-only, stdlib; new
  `scripts/kalshi_gridpin_census.py`; needs an `ops_runner.py` allowlist entry, which is an
  operator-merge item). It answers only C0–C2:
  - Enumerate `KXTXERCOTPEAKD` events by date back to the first 404.
  - Per rung, report status, result, `expiration_value`, volume and open interest.
  - Pull `/markets/trades` for a sample of rungs and count post-19:00 CT prints.
  - Fetch ERCOT hourly load for the same days, confirm reachability, compare against
    `expiration_value`, and measure posting lag and revisions on the days the source exposes them.
  - Also print, for free, the morning mid versus ERCOT's day-ahead forecast (the S8 fold) as
    description only.
  - Grep the Kalshi series list for sibling grid series (other ISOs, monthly peaks) to size a larger
    universe.

  Below the floors, it returns **HOLD/BLOCKED_DATA**, and **no full probe is written**.
- **Step 2, full probe** (only if the census clears): `scripts/kalshi_gridpin_study.py`.
  - **Data:** Kalshi public trades plus 1-min candles per rung (the `xvenue_crypto.kalshi_candles`
    pattern), and ERCOT hourly actuals. For P3 only, a 5-min system-demand history *if* one is
    reachable. If not, P3 is graded forward from a small read-only logger, which is a separate
    operator decision.
  - **Provenance:** ERCOT data goes to a new, clearly named table or file, never mixed into
    `weather_*`.
  - **No lookahead:** at decision time *t*, the only information used is the hourly values whose
    measured first-posting time is at or before *t*, taken from C2's lag table, not the hour-end
    time. The entry price is the ask at *t* from the book or trade record, never the settle.
  - **Measurement:** P1–P4 as above, sliced by hour of day and by margin to strike, with a
    split-half check across calendar halves.
- **Reuse:** `kalshi_desk_board` / `kalshi_market_probe` fetch patterns, the PINNED/SEASONPIN
  control design (P2), and `desk_fetch`'s ERCOT allowlist entry (added 09-25) as the reachability
  precedent.

**Cost and capacity note.** Decided-side entries sit at 85–99¢, where the per-order taker fee is
`ceil`'d to 1¢ for a single contract at 90–99¢. That is why P1's bar is +2¢ net and why multi-contract
orders matter. The 09-29 live book shows 1–5 contract depth and very wide spreads, so the realistic
size is **5–20 contracts per event-day**. At +3–5¢, that is roughly **$5–30/month** if it works:
small, but **uncorrelated ballast**. As the day count grows and if sibling grid series exist,
roughly $30–60/month is plausible. It will not reach $100/month on its own.

**Correlation note.** Zero shared driver with the mmsell family (sports cheap-tail maker-sell) or the
liquidity-incentive shadow. Its driver is Texas weather-driven load, and no weather book is live. It
does overlap the **discretionary desk's** universe (R10, one unit per print). A systematic GRIDPIN
book and desk picks on the same print must not both trade, and that boundary goes into the thesis
doc.

## Census implementation notes (added 2026-09-29, before the census ran)

A read-only reconnaissance through `desk_fetch` (ops `gridpin-recon-1..3`) settled which ERCOT
sources exist. It did not look at any Kalshi price.

- **Settlement report (C1).** NP6-346-CD "Actual System Load by Forecast Zone" is listed at
  `misapp/servlets/IceDocListJsonWS?reportTypeId=14836`. There is one csv and one xml zip per
  operating day, published about 05:50 CT the next morning and retained about 31 days. The census
  downloads the csv zips (`misdownload/servlets/mirDownload?doclookupId=`) and grades each
  settled rung from the daily maximum hourly `TOTAL`.
- **Same-day hourly (C2).** `api/1/services/read/dashboards/loadForecastVsActual.json` carries
  hourly `systemLoad` for `previousDay` and `currentDay`. A 16:05 CT read had values posted
  through HE15. The census measures the lag as minutes since the latest posted hour ended. It
  measures revisions as rung flips between the dashboard's previous-day maximum and NP6-346 for
  the same day.
- **5-minute demand (P3, reported only).** `api/1/services/read/dashboards/supply-demand.json`
  carries same-day 5-minute `demand`. The census prints today's 5-minute peak minus hourly peak,
  and checks whether the hourly mean of the 5-minute values reproduces the posted hourly value.
  If it does, the hourly figure can be computed live.
- **Limit of one run.** Each run sees one previous day for C2 revisions and one current day for
  the 5-minute gap. A C2 pass on one day is a pass of the census bar as written, not proof across
  days. The full probe (if it is ever built) must accumulate these readings forward.
- **Not built:** the S8 morning mid versus day-ahead forecast print, which was description-only
  in the idea report.

## RESULTS

*(not yet run)*
