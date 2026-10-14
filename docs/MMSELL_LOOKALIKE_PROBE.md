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

Gate readings are appended after the corrected runs.
