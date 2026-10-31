# MMSELL look-alikes — where else does the rain cell's edge live?

Research Lab, 2026-10-14. Pre-registration for the probe `scripts/mmsell_lookalike_probe.py`,
written before it ran. Experiment OS is canonical for experiment state; this document records the
screen, the probe and its gate. It authorizes nothing. A PROMOTE reading here only nominates a
paper tape. That tape is created through Experiment OS like any other, under its own gate.

## 1. The question

Calvin asked (2026-10-14): find what makes the best series work, rain especially, and find other
series and markets with the same mechanics to paper trade and collect data on. Option A is the
look-alike screen; option C is the historical-price test.

## 2. What the data already says (option A, ops `db` read `rain-lookalike-rank-20261014a`)

MMSELL paper is a broad scanner, not an allowlist. It traded 346 series with ≥30 settled trades
(twins excluded, statuses as Experiment OS reads them). Total +$1,603.54; 249 series positive.

Of the 146 series with ≥20 events, 111 are positive. Ranked by per-event t, KXRAIN is about
11th (t 4.6). Sports totals score as well or better:

| Series | events | c/ct | t |
|---|---|---|---|
| KXNPBGAME | 76 | +8.39 | 8.99 |
| KXNHLTOTAL | 54 | +8.48 | 5.86 |
| KXUEFANLTOTAL | 54 | +5.87 | 5.82 |
| KXLIGAMXTOTAL | 47 | +5.96 | 5.30 |
| KXRAIN | 64 | +5.10 | 4.60 |
| KXWNBAGAME | 117 | +6.64 | 3.87 |
| KXMLSTOTAL | 68 | +5.26 | 3.15 |

The worst are KXNFLSPREAD (−5.19, t −2.2), KXWTI, KXMLBTB, KXMLBHRR and KXEPLSPREAD. These
are already known negative cells.

Readings, with caveats:
- The sample is two to three months.
- A t over events still ignores same-day correlation across series.
- Many top rows are 100% win rates that have not yet met a tail loss.
- These series are already in paper, so they need forward reading, not new tapes.

**The untested look-alikes are the ones the scanner skips.** `mmsell_skip_series` is
`KXMVE,KXHIGH,KXLOW` (`kalshi_bot/config.py`). The reason recorded is "weather has its own
book", but every weather book was retired by 2026-08-12, so daily temperature ladders, the
closest mechanical twin of rain, have never been MMSELL-traded.

Prior temperature evidence is not this test:
- Backfill probe #4 sold cheap longshots at the taker price (NO = 100 − yes bid) on April–June
  ladders and was refuted, −0.3 to −2.4¢ (`docs/RESEARCH_JOURNAL.md`).
- The maker favorite-buy died to adverse selection.

The MMSELL cell is a maker NO at the no bid on the cheapest YES (≤7¢). That combination has not
been tested on temperature.

## 3. The probe (option C)

`mmsell_lookalike_probe` replays the mmsell10 cell on Kalshi's public history (live and
historical archive, hourly candles), with no auth:
- **Universe:** every series in the Kalshi category "Climate and Weather", or an explicit list.
- **Entry:** the earliest of T−48…T−1h at which lo 5 ≤ yes mid ≤ hi 10 and yes ask ≤ 7.
- **Exit:** buy NO at 100 − yes ask and hold to settlement.
- **Costs:** gross, one contract.
- **Bet unit:** one bet per family-day, so all cities of a family closing on one UTC date count
  as one bet.
- **Control:** KXRAIN is always included as the positive control. It must reproduce
  (≥ +2.0¢/contract, t ≥ 2) or the run is void.

### Pre-registered gate, per family

- **PROMOTE to a paper tape** when all of these hold:
  - ≥100 first entries;
  - ≥30 family-days;
  - ≥ +2.0¢/contract gross;
  - t ≥ 2.5 per family-day (stricter than 2 because about 10–20 families are screened at once);
  - positive in at least two thirds of the months with ≥10 entries.
- **KILL** when there are ≥100 first entries and ≤0¢/contract.
- **HOLD** otherwise (thin or mixed).

These thresholds are fixed now and are not revised after seeing results.

### Known limits

- Paper and replay both assume the maker no bid fills. The live-fill penalty is unmodelled here.
  Rain's live results matched paper, but temperature has a history of adverse selection, so a
  PROMOTE needs a paper tape (and later a live twin), never a direct live step.
- Hourly candles: a quote is the last hourly close at or before the target hour.
- Kalshi's archive bounds the season coverage per series. The listing table prints each span.

## 4. Results

### Run log (2026-10-14); the gate itself is unchanged

| ops id | what | outcome |
|---|---|---|
| `lookalike-probe-20261014a` | category discovery, 60 series, 90 priced each | **Void.** The category lists about 60 retired, pre-`KX` series first, so the cap truncated before today's series. The control was priced on 88 markets, 12 entries. |
| `lookalike-listing-20261014b`/`c` | listing passes, `KX` only | Found the current families listed below. |
| `lookalike-control-20261014d` | KXRAIN, 700 priced | 84 entries, +0.67¢, t 0.25. Not reproduced at this sample. |
| `lookalike-control-20261014e` | KXRAIN, all 1,701 priceable | **Reproduced.** 248 entries over 57 days, +3.73¢/contract, t 3.42, 3 of 3 months positive (Aug +6.6, Sep +1.8, Oct +0.8). This matches the rain cell's own replay (+4.28¢, 264 markets) and its fading trend. |
| `lookalike-khigh-20261014f` | KXHIGH, 8 cities × 600 priced | **Void, parsing defect.** Entries appeared only in Aug–Oct 2026 although markets back to Oct 2024 were priced. The archive endpoint returns prices as dollar strings, which the probe read as cents, so no archived market could pass the cell. Fixed in the follow-up PR, which reads every candle shape the backfill code reads. |

Disclosure: the control's sample size was raised after the 700-market run came back weak. The
pre-registration fixed the gate, not the sample. The full sample is the like-for-like
replication of the rain cell's own replay, and every reading is recorded above.

### Families found (listing passes)

- **Daily high:** KXHIGH (8 cities, 2024-10..2026-10, about 4,300 markets each) and KXHIGHT (16
  cities, since 2026-01/02).
- **Daily low:** KXLOWT (24 cities, since 2025-12/2026-04).
- **Average temperature:** KXAVGTK (Aug–Oct 2026).
- **Monthly rain:** 12 cities, `KXRAIN<city>M`.
- **Older daily NYC rain:** KXRAINNYC, 2025-01..2026-07. This is a possible winter check for rain.
- **Other:** hourly temperature (KXTEMP*H), monthly snow, tornado, hurricane and earthquake series.

### Gate readings (corrected code `c14245a0`, 2026-10-14)

**Control.** `lookalike-control-20261014h` re-ran KXRAIN on the corrected code. It **reproduced**:
300 first entries over 68 days, +4.21¢/contract, t 4.53, 4 of 4 months positive. The archive
fix let July into the sample.

| Family | ops id | entries | family-days | implied YES | realized YES | ¢/contract | t | months + | Gate |
|---|---|---|---|---|---|---|---|---|---|
| KXHIGH (8 cities, 2024-10..2026-10) | `lookalike-khigh-20261014g` | 564 | 120 | 6.54% | 4.08% | **+2.47** | 2.94 | 18/23 | **PROMOTE** |
| KXHIGHT (17 cities, 2026-01..10) | `lookalike-khight-20261014i` | 744 | 70 | 6.57% | 4.03% | **+2.54** | 3.72 | 9/10 | **PROMOTE** |
| KXLOWT (24 cities, 2025-12..2026-10) | `lookalike-klowt-20261014j` | 390 | 48 | 6.65% | 3.59% | **+3.06** | 3.21 | 8/10 | **PROMOTE** |

These runs used `--no-control` to keep each ops job short. The control ran separately on the same
code and the same day. The "control not reproduced" line printed inside each family run reflects
only that flag.

**Reading.** Across about 1,700 historical first entries in three independent temperature families,
the cheap YES side (≤7¢) settles YES roughly 4% of the time against about 6.6% implied. This is the
same overpricing as rain, about 2.5–3¢ per contract gross, and it replicates across families and
cities.

**Risks to carry into the paper tape:**
- **Winter 2024–25 was bad in KXHIGH:** Dec −10.2¢, Jan −9.4¢. Winter 2025–26 was positive (Dec
  +3.2, Jan +4.3; KXHIGHT Jan +6.4, Feb +3.1; KXLOWT Jan +2.6, Feb −1.4). Losses cluster in winter
  without being reliable, so the tape must read winter separately.
- **Correlation:** the three families share weather days. Treat them as one book, with one bet per
  calendar day across all temperature series, not three.
- **Fills:** both the replay and paper assume the maker no bid fills. Temperature has a recorded
  history of maker adverse selection (`docs/edge_research.md`), so only a paper tape followed by a
  live twin can test this. Fees are not included; the paper books record them.
- **City dispersion is mostly noise at about 50 entries per city.** For example, NYC KXHIGH had no
  losses in 72 entries while Philadelphia was −0.7¢. Do not select cities from this table.

**Recommendation.** Nominate one paper tape: the mmsell10 cell (lo 5, hi 10, maxyes 7) on
KXHIGH + KXHIGHT + KXLOWT, paper only, with the gate below.
- **Floor:** 120 settled weather days, including at least 30 from Dec–Feb.
- **Kill:** if mean ≤ 0 at the floor, or if the Dec–Feb half is ≤ −3¢/contract.

Opening the temperature series to this one paper book must not open them to any other book,
and above all not to a live book (`mmsell_skip_series` is global today).

## 5. Paper tape — `mmsell-temperature-season` v1

Package: `kalshi_bot/experiment_os/temperature_season.py`. It is registered in Experiment OS only
after the owner merges. Until then this section is the proposal, not the contract.

| Arm | Role | Tag | Book |
|---|---|---|---|
| `temp_cell` | control | `Tempmmsell1` | `lo=5,hi=10,maxyes=7,htcmax=48,optin=KXHIGH+KXLOW,only=KXHIGH+KXLOW` |

**The reach.** `KXHIGH` and `KXLOW` stay on the global skip list. The book reaches them only
through its own `optin=` key.
- The tracker ranks opted-in events in a separate list after the normal ones, so no other book's
  candidates or ranks move. `tests/test_mmsell_optin.py` checks this.
- The tracker refuses every live mirror on a skipped series. This holds even with the tag
  allowlisted and the tier bar off.
- `optin` must name a prefix the skip list actually drops, or the book is rejected.
- Telemetry `events_optin` / `markets_considered_optin` count the reach apart from the control's
  funnel.

**Gate `temp_cell_winter_keep`** (kill gate, one arm; the same bars as the rain contract):
- **Floor:** 120 settlement days. It cannot be reached before mid-February, so the sample holds
  about 75 Dec–Feb days, against a pre-registered minimum of 30.
- **Horizon:** 180 days.
- **PASS:** ≥ +2.0¢/contract **and** daily stability ≥ 0.15.
- **FAIL:** < 0¢ at any point after 45 days, < +1.0¢ at the floor, or stability < 0.

**Changes from §4's nomination, made before any paper trade:**
- **Kill at the floor:** tightened from ≤ 0¢ to < +1¢, because temperature has a history of maker
  adverse selection and a sub-1¢ edge cannot pay for it.
- **Daily stability:** added, because every city on one front settles together.
- **Entry window:** `htcmax=48` mirrors the replay's T−48h…T−1h window.

**Winter stop, a monitoring read.** Once at least 30 Dec–Feb settlement days exist, a Dec–Feb mean
≤ −3¢/contract means stop the book.
- The gate grammar has no seasonal subset, and adding one would be a shared-metric change
  (Platform Change Review). So this stop is not a verdict.
- When it trips, the result is an Experiment OS issue routed to Research Lab, recommending the
  spec's removal from `MMSELL_VARIANTS`.

**Not gated:** size and promotion, as in the rain contract. A live step needs a gate PASS and then
a Live Ops canary on its own Version. The global caps (open positions, settlement date, rungs)
apply unchanged and bound how many cities one weather day can add.

## 6. Fast-verdict round (2026-10-15) — pre-registration, written before either run

Calvin asked for theses that can be decided within a week. Two were chosen, A and B; C (other
price bands) and D (the same replay on non-weather categories) are **queued next**, to start once
A and B have their verdicts.

A daily market gives one bet per day, so a week of forward paper on it is 7 observations. Only
history can decide that fast. The verdict therefore comes from the replay, and a forward week
is used only where a market settles many times a day.

### A — hourly temperature (`KXTEMP<city>H` / `…HS`)

- **Question:** does the rain cell's overpricing exist on Kalshi's hourly temperature markets?
- **Run:** the same probe and cell (5 ≤ mid ≤ 10, YES ask ≤ 7) on every hourly temperature series
  found in the listing pass, with the KXRAIN control.
- **Bet unit:** one bet per family-day. All hours and cities on one date count as one bet, because
  one weather day drives them all.
- **Gate:** the §3 family gate, unchanged.
- **Feasibility, added and fixed now:** at least 50% of priced markets must carry a quote at
  least 1h before close (printed as LIFETIME). The bot never enters inside its global 1h floor,
  so a series listed later than that cannot be traded by any book. Below 50% the reading is
  NOT FEASIBLE, whatever the edge; changing the floor would be a separate decision, never part
  of this test.
- **If PROMOTE and FEASIBLE:** one paper book, registered like `Tempmmsell1`. The hourly series
  are not on the skip list, so the book needs only `only=`.
- **Its 7-day forward check, fixed now:** after 7 calendar days the book must have
  - at least 50 settled entries (the bot can actually trade it), and
  - mean ≥ 0¢/contract (the fills are not worse than the replay assumed).
  Failing either stops the book. Passing keeps it running toward its full gate.

### B — entry timing (rain and daily temperature)

- **Question:** does buying the cheap tail early, mid-way or late change the edge?
- **Run:** `--timing` replays three entry policies on the same markets: early (T−48..T−24h),
  mid (T−24..T−6h) and late (T−6..T−1h).
- **Comparison:** each policy is compared with the shipped first-entry rule day by day, over the
  family-days on which both policies bet.
- **Reading per family:**
  - **BETTER:** mean difference ≥ +1¢/contract with t ≥ 2 on ≥ 30 shared days;
  - **WORSE:** ≤ −1¢ with t ≤ −2;
  - **SAME:** anything else;
  - **THIN:** under 30 days.
- **Families:** KXRAIN, KXHIGH, KXHIGHT, KXLOWT.
- **Consequence:** a BETTER window becomes a proposal for a new Version (or a treatment arm) of
  the existing rain and temperature contracts. It is never an edit to a running book; a running
  book's rule changes only through Experiment OS.

### Known limits

- Hourly candles: a window is scanned at the replay's fixed hours.
- In-play adverse selection late in the day is only as visible as the candle closes allow.
- Everything is gross, and assumes the maker fill.

## 7. Next round — C and D, pre-registration (2026-10-15, written before either run)

Calvin queued these behind A and B. They start once A's and B's replay verdicts are recorded;
A's 7-day forward check, if it runs, does not hold them back. Both are read-only replays with
the same probe, cell convention, bet unit (one bet per family-day) and §3 family gate, unless
stated otherwise.

### C — other price bands

- **Question:** is the cheap tail overpriced outside the shipped cell?
- **Bands**, each disjoint from the cell (mid 5–10, YES ask ≤ 7):
  - **ultra:** `--lo 1 --hi 5 --maxyes 4`. YES ask ≤ 4 puts the mid ≤ 4, below the cell.
  - **upper:** `--lo 10 --hi 16 --maxyes 15`. A mid ≥ 10 is above the cell's ceiling of 7.
- **Families:** KXRAIN, KXHIGH, KXHIGHT, KXLOWT, plus KXTEMP if A was FEASIBLE. Each family uses
  the same series and sample sizes as its §4/§6 run.
- **Reading:** the §3 gate, unchanged, including the +2¢ bar. In the ultra band the most a
  contract can earn is the YES ask (≤ 4¢), so +2¢ is a hard bar there. It is kept on purpose,
  because it is the paper-to-live maker gap.
- **Consequence:** a PROMOTE band becomes a proposed treatment arm on a new Version of the rain
  or temperature contract. It is never an edit to a running book.

### D — the same replay outside weather

- **Universe:** each Kalshi category except Climate and Weather, `KX` tickers only, in this order:
  Economics, Financials, Crypto, Companies, Entertainment, Mentions, Science and Technology,
  Politics, Elections, World, then Sports last (Sports is already most of the paper evidence).
- **Bet unit:** a series not named in `FAMILIES` is its own family, so one bet per series-day.
- **Reading, stricter than §3 because tens of families are screened at once.** A family is a
  **CANDIDATE** only if all of these hold:
  1. it reads PROMOTE under §3;
  2. its per-day t is ≥ 3.0, not 2.5;
  3. its per-month means (section C of the output), split into earlier and later halves by
     calendar month, are positive in both halves.
- **Consequence, by whether paper already reaches the series:**
  - Already traded by MMSELL paper (≥ 30 settled trades in the 2026-10-14 option-A rank): a
    CANDIDATE confirms the existing book, and no new tape is created.
  - Not reached by paper today (skipped, or never in the top-150 volume cut): a CANDIDATE
    nominates one paper book, registered like `Tempmmsell1`.
  - Only the CANDIDATE with the highest t per category is nominated, so a flood of marginal
    series cannot each become a book.
- **KILL:** at least 100 entries and ≤ 0¢, recorded so the series is not retried.
- **Disclosure:** every category run is logged, including empty discoveries (a category name
  Kalshi does not use returns nothing, which is not a result).

## 8. Results — fast-verdict round (2026-10-15, code `c2767929`)

### A — hourly temperature: NOT FEASIBLE

`fast-a-hourly-20261015a`, 10 series (`KXTEMP<city>H` / `…HS`), 3,381 markets priced:
- **0%** of markets were quoted at least 1h before close, so no in-cell entry exists inside the bot's
  entry window.
- These markets list inside the final hour. No MMSELL book can trade them under the global 1h
  floor.
- The edge itself is **unmeasured**: this is not a KILL.
- Lowering the floor would be a shared, global change (and in-play entry carries the adverse
  selection the 1h floor exists to avoid). It is recorded here, not proposed.
- No paper book. The 7-day forward check does not apply.

### B — entry timing: SAME almost everywhere

| family | ops id | early (T−48..−24h) | mid (T−24..−6h) | late (T−6..−1h) |
|---|---|---|---|---|
| KXRAIN | `fast-b-rain-20261015a` | −0.27¢, t −0.2 → SAME | −0.12¢ → SAME | +0.94¢, t 1.2 → SAME |
| KXHIGH | `fast-b-khigh-20261015a` | **+2.29¢, t 2.11, 69 days → BETTER** | −0.99¢ → SAME | +0.18¢ (29 days) → THIN |
| KXHIGHT | `fast-b-khight-20261015a` | −1.76¢, t −1.1 → SAME | +0.69¢ → SAME | −3.21¢, t −0.8 → SAME |
| KXLOWT | `fast-b-klowt-20261015a` | +1.13¢, t 1.1 → SAME | −0.83¢ → SAME | +0.96¢ → SAME |

Each difference is day-paired against the shipped first-entry rule. The KXRAIN control reproduced
on this code (+4.21¢, t 4.53, the same as §4).

**Reading.** One BETTER in twelve comparisons, at t 2.11, close to what chance alone produces at
a two-sided |t| ≥ 2 across twelve tests. The sister family KXHIGHT points the other way (−1.76¢),
and KXLOWT is flat.

The pre-registered consequence of a BETTER is a *proposal*. It is recorded here with the
recommendation **not to act**: an early-only arm on KXHIGH alone would halve the entries (137 vs
564) on evidence that does not replicate within the temperature family.

**Practical conclusion:** the shipped first-entry rule is not leaving money on the table through
its timing. Timing is not a lever for these books.

### Lifetime, as a by-product

Rain and all three daily temperature families are quoted at least 1h before close on 94–99% of
markets, so every nominated daily book is tradeable under the floor.

## 9. Last-hour test on hourly temperature — pre-registration (2026-10-15, before the run)

Calvin asked for this after A read NOT FEASIBLE. Hourly temperature markets list inside the
final hour, and every MMSELL book is barred from entering then (`mmsell_min_hours_to_close` = 1h).
The bar exists because in the last hour the answer is largely known, and a resting maker order
mostly fills when it is about to lose.

- **Run:** `--minutes 45,30,20,15,10,5` replays the cell on 1-minute candles. Entry is at the
  earliest of those offsets at which the cell passes; quotes are no older than 10 minutes.
- **Scope:** the 10 hourly series of `fast-a-hourly-20261015a`, `--per-series 400`, bet unit one
  family-day.
- **Gate:** §3 with the per-contract bar raised to **+4¢** (`--min-cents 4`), twice the measured
  paper-to-live maker gap.
  - The raise is fixed now, because last-hour adverse selection is exactly what neither paper nor
    replay can see: both assume the resting order fills.
  - KILL as in §3 (≥ 100 entries and ≤ 0¢).
- **Consequence of PROMOTE:** a *proposal* for one paper book with a per-book entry-floor exception
  (a code change, the same shape as `optin=`). The global floor is not lowered.
  - No live step without a live twin, because only real fills can measure this risk.
  - The bot scans about every 30 minutes, so it would see each market at most once or twice in its
    life. A PROMOTE says the price is wrong, not that the bot can catch it often.
- **HOLD or KILL:** recorded, and hourly temperature is closed.

## 10. Results — C, other price bands (2026-10-15)

Ops ids `c-<band>-<family>-20261015a`, same series and samples as §4 and §6. The KXRAIN rows are
the rain family itself, not the control.

| family | ultra (1–5 mid, YES ≤ 4) | upper (10–16 mid, YES ≤ 15) |
|---|---|---|
| KXRAIN | 1,070 entries, **+2.10¢**, t 7.81, 4/4 months → **PROMOTE** | 309, **+4.73¢**, t 2.91, 3/4 → **PROMOTE** (Oct −5.2¢) |
| KXHIGH | 1,908, +1.77¢, t 6.21, 21/24 → HOLD (under +2¢) | 910, **+2.41¢**, t 2.72, 19/24 → **PROMOTE** |
| KXHIGHT | 2,458, **+2.04¢**, t 7.66, 10/10 → **PROMOTE** | 896, **+3.60¢**, t 3.46, 10/10 → **PROMOTE** |
| KXLOWT | 1,788, +1.84¢, t 5.29, 10/11 → HOLD (under +2¢) | 463, +2.36¢, t 1.89, 5/10 → HOLD |

**Reading.**
- **The overpricing covers the whole cheap tail, not just the shipped cell.** Realized YES runs about
  a third to two-thirds below implied from 1¢ to 15¢.
- **Ultra band:** the effect is the most certain anywhere (t 5–8), and the smallest in cents. It
  sits right at the +2¢ fill-gap bar, so it is real but thin.
- **Upper band:** the edge in cents is larger, and so is the noise.

**Consequence**, per §7: each PROMOTE band becomes a paper arm on the families that PROMOTEd it.
That is `mmsell-weather-bands` (`kalshi_bot/experiment_os/weather_bands.py`):

| arm | tag | book |
|---|---|---|
| `temp_upper` | `Tempmmsell2` | `lo=10,hi=16,maxyes=15,htcmax=48,optin=KXHIGH,only=KXHIGH` (KXHIGH and KXHIGHT) |
| `hight_ultra` | `Tempmmsell3` | `lo=1,hi=5,maxyes=4,htcmax=48,optin=KXHIGH,only=KXHIGHT` |
| `rain_ultra` | `Rainmmsell3` | `lo=1,hi=5,maxyes=4,onlyx=KXRAIN` |

- **Gates:** each arm has its own gate, with the same floor, horizon and bars as the rain and
  temperature contracts (120 settlement days, +2¢ pass, < +1¢ fail).
- **Left out:** rain in the upper band, because `Rainmmsell2` (8–12¢) already tests it.
- **Paper only.** None of these running books is edited.

## 11. Last-hour test — first run void for sampling; re-run by day

`lasthour-temp-20261015a`, code `ef2dd22a`:
- **Sample:** 3,414 markets priced; 58% were quoted at least 45 min before close.
- **Entries:** only **53 first entries over 5 family-days**: −4.62¢, realized YES 11.3% against
  6.7% implied, HOLD.
- **Why the run is void:** the per-series sample was spread by **month**. An hourly series
  settles more than 100 markets a day, so the newest few per month all fall on the last day or
  two of each month. The run therefore saw about 5 bet-days, far below the §3 minimum of 30. It
  is void as a test. It is recorded because its direction (the cheap tail settling YES *more*
  often than implied) is the adverse selection §9 was written to catch.
- **Fix:** `--stratify day`. Month mode is unchanged, so every earlier run reproduces.
- **Re-run:** the same series, cell, offsets and +4¢ bar, stratified by day
  (`lasthour-temp-20261015b`). The §9 gate and consequences are unchanged.

## 12. Results — D, outside weather (2026-10-15)

**Listing pass** (`d-list-1..11-20261015a`, `KX` only, a category discovery capped at 400 series):

| category | what it holds |
|---|---|
| Economics | AAA gas (daily, weekly, monthly, by state), diesel |
| Financials | Dow daily, FX (daily, hourly, 15-minute), Treasury rates (hourly) |
| Crypto | 15-minute and daily coin ladders |
| Entertainment | Billboard, streams, app ranks |
| Mentions | broadcast "will X say Y" ladders |
| Science and Technology | LLM and model rankings |
| Politics, Elections, World | one-off events; no series with ≥ 800 settled markets |
| Sports | already the bulk of MMSELL paper evidence, so not deep-run, per §7's ordering |

- **Too late to trade:** hourly and 15-minute series list inside the 1h entry floor (as A found
  for hourly temperature).
- **Already traded by paper:** most liquid daily series (gas daily −2.4¢, Dow, crypto daily,
  the gas weekly and monthly series). For those, §7 asks only for confirmation, and the existing
  books already carry live paper evidence.

**Deep runs** covered series with long histories that paper does not reach (fewer than 30
settled trades), using the cell and the §3 gate, read per series:

| run | series | first entries | reading |
|---|---|---|---|
| `d-deep-fin-20261015a` | KXEURUSD (622 priced) | 18 over 10 days, +1.22¢, t 0.2 | HOLD |
| `d-deep-ent-20261015a` | Billboard ×4, app rank, artist streams | 0–9 each | HOLD / NO-DATA |
| `d-deep-men-20261015a` | 12 mention series (about 230 priced each) | 1–20 each | HOLD |
| `d-deep-sci-20261015a` | KXLLM1 | 4 | HOLD |

**Verdict: no CANDIDATE.** These series rarely sit in the cell at the replay's hours (about 2–3%
of priced markets enter, against about 12% for weather). Reaching 100 entries per series would
need on the order of 3,000 priced markets each.

**A lead, not a result.** Pooled across the 12 mention series, entries were 74, with 1 loss, at
+5.33¢/contract. The pooling was chosen after seeing the per-series rows, so it authorizes
nothing. A confirmatory test would need its own pre-registration:
- a pooled "mentions" family, day-stratified;
- a large sample, on markets other than these;
- the cell and the ultra band.

It is put to the operator as a decision, not started.

## 13. Mentions confirmatory test — pre-registration (2026-10-15, before the run)

Calvin approved testing the §12 lead: across the 12 exploratory mention series, 74 entries with
1 loss, +5.33¢. That pooling was chosen after looking, so this test runs **out of sample**, on
mention series that §12 never priced.

**Hold-out universe.** The 33 `KX…MENTION` series in the Mentions listing pass
(`d-list-6-20261015a`) with at least 150 settled markets. Excluded: the 12 series of
`d-deep-men-20261015a`, `KXMVE…` (globally skipped parlays), and `KXLASTWORDCOUNT` (not a
mention market). The series:

KXPERSONMENTION, KXMENTION, KXPSAKIMENTION, KXNCAAMENTION, KXHOCHULMENTION, KXFEDMENTION,
KXLOVEISLMENTION, KXLATENIGHTMENTION, KXMRBEASTMENTION, KXBESSENTMTPMENTION, KXMADDOWMENTION,
KXNHLMENTION, KXCARNEYMENTION, KXATHLETEMENTION, KXCONGRESSMENTION, KXPRESMENTION, KXMTPMENTION,
KXGOVERNORMENTION, KXHEGSETHMENTION, KXSNFMENTION, KXNEWSOMMENTION, KXFTNMENTION,
KXDEBATEMENTION, KXSNLMENTION, KXMELANIAMENTION, KXCANDACEMENTION, KXALLINMENTION,
KXRUBIOMENTION, KXPOWELLMENTION, KXNYCMAYORDEBATEMENTION, KXKIMMELMENTION, KXFNSMENTION,
KXAOCMENTION.

**Run.**
- **Primary:** the shipped cell (5 ≤ mid ≤ 10, YES ≤ 7), `--family MENTIONS --stratify day
  --per-series 150`, hourly offsets as §3.
- **Secondary, information only:** the same sample in the ultra band (`--lo 1 --hi 5 --maxyes 4`).
- **Bet unit:** one bet per day across all mention series, which is conservative.

**Reading** (primary run only; D's CANDIDATE bar, because the lead came from a screen):
- **PASS** needs all three:
  1. §3 PROMOTE: ≥ 100 entries, ≥ 30 days, ≥ +2¢, t ≥ 2.5, ≥ 2/3 of months positive;
  2. t ≥ 3.0;
  3. positive means in both the earlier and later halves of its months.
- **KILL:** ≥ 100 entries and ≤ 0¢.
- **HOLD:** anything else, including fewer than 100 entries, which is recorded as too thin and
  not retried at a bigger size without a new pre-registration.

**Consequence of PASS:** a nomination for one paper book on mention series. Mention series are
not on the skip list, and an existing paper book already selects mentions by market type
(`Tmmsell2`, `mtype=mention`). Its paper record is read first: if it already covers this cell,
PASS confirms it, and no new tape is created.

## 14. Last-hour test — result: HOLD, hourly temperature closed (2026-10-15)

`lasthour-temp-20261015b`, code `2580f36b`. The run was day-stratified, with the §9 universe,
cell, offsets (45…5 min) and +4¢ bar:
- **Sample:** 8,638 markets priced.
- **Result:** 229 first entries over **80** family-days, implied 6.60%, realized 5.24%,
  **+1.36¢/contract**, t 0.78, 2 of 4 months positive → **HOLD**.
- **By month:** Jul −2.9¢, Aug −3.8¢, Sep +5.5¢, Oct +2.6¢.

**Reading.** In the final hour the cheap tail is priced close to fair. That is what §9 expected
once the outcome is mostly visible, and it is nowhere near the +4¢ that unmodelled last-hour fills
would need. As pre-registered, **hourly temperature is closed**: no per-book entry-floor exception
is proposed. The first run's −4.6¢ (§11) was 5 days of noise, in the same direction as July and
August here.

## 15. Mentions test — result: HOLD (2026-10-15)

Code `e4c13df8`; 33 hold-out series, 4,525 markets priced, pooled `MENTIONS`, day-stratified.

| run | entries | days | implied → realized | ¢/contract | t | months + |
|---|---|---|---|---|---|---|
| **primary** cell, `mentions-cell-20261015a` | 119 | 77 | 6.69% → 2.52% | **+4.17** | **2.94** | 4/5 |
| secondary ultra, `mentions-ultra-20261015a` (information only) | 303 | 129 | 2.84% → 0.66% | +2.18 | 4.25 | 9/11 |

**Reading against §13.**
- The primary run is a §3 PROMOTE but fails the CANDIDATE bar on both extra conditions:
  - t 2.94 is below 3.0;
  - the earlier half of its months is negative. Jun 2025 – Jan 2026 had 33 entries at about
    −2.4¢ (Oct −5.6, Dec −6.0, Jan −1.8). Feb–Sep 2026 had 86 entries at about +6.6¢.
- **Verdict: HOLD.** No paper tape is nominated. As pre-registered, the test is not retried at a
  larger size without a new pre-registration.
- The ultra run would pass §3 easily, but it was declared information-only before the run and
  is not promoted after it.

**What this means in practice.** The broad MMSELL books already trade mention markets when they
reach the volume cut. `mmsell10` and its live twins show settled mention trades at about
+4.5–6.5¢ (ops read `mention-paper-20261015a`; Experiment OS holds the standings). Nothing here
argues for stopping that, and nothing argues for a dedicated mentions book. The recent months are
strong, but the earlier months are not, and the gate was set to catch exactly that.

## 16. State gas ladders — pre-registration (2026-10-31, before the run)

**Origin:** the untraded-capacity census (`docs/UNTRADED_CAPACITY_CENSUS.md` §6) shortlisted the
daily state AAA gas ladders as the rain-shaped group. The owner asked for them to be screened
first (2026-10-31).

- **Series, pooled as one family `KXAAAGASD-STATES`** (one bet per day; every state follows the
  same gasoline market): KXAAAGASDNY, KXAAAGASDCT, KXAAAGASDMA, KXAAAGASDWA, KXAAAGASDTX,
  KXAAAGASDNJ, KXAAAGASDIL.
- **Construction:** the shipped mmsell10 cell (5 ≤ mid ≤ 10, YES ask ≤ 7), first in-cell entry at
  the replay's hours, NO at the no bid, held to settlement, gross, one contract.
  `--per-series 300 --stratify day`.
- **Control:** KXRAIN in a separate run (`--family` pools every series in a run, the control
  included). The control must reproduce (≥ +2.0¢, t ≥ 2) or the family run is void.
- **Gate (§3 plus the stricter §7-D reading, unchanged):** CANDIDATE requires all of:
  - PROMOTE under §3 (≥ 100 first entries, ≥ 30 family-days, ≥ +2.0¢, positive in ≥ 2/3 of
    months);
  - per-day t ≥ 3.0;
  - both calendar halves positive.

  KILL is ≥ 100 entries and ≤ 0¢. Otherwise HOLD.
- **Stated prior, against:** the national daily gas series was already traded by paper at −2.4¢
  (§12), and study 1 read KXAAAGASD as NO-CALL (maker +0.51¢ ± 4.53).
- **Consequence of a CANDIDATE:**
  - a taker-price read comes first (study 1: a fill-assumed maker replay overstates realized
    fills);
  - only then may a paper tape be nominated through Experiment OS;
  - nothing is authorized here.
- **Commands:**
  - `{"type":"script","name":"mmsell_lookalike_probe","args":["--series","KXAAAGASDNY,KXAAAGASDCT,KXAAAGASDMA,KXAAAGASDWA,KXAAAGASDTX,KXAAAGASDNJ,KXAAAGASDIL","--no-control","--family","KXAAAGASD-STATES","--per-series","300","--stratify","day"],"id":"rl-gas-fam-20261031"}`
  - `{"type":"script","name":"mmsell_lookalike_probe","args":["--series","KXRAIN","--per-series","300","--stratify","day"],"id":"rl-gas-ctl-20261031"}`
