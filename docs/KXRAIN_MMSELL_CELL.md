# KXRAIN × MMSELL — is the rain cell real, and does it survive winter?

Research Lab, 2026-10-14. Pre-registration for Experiment OS experiment `mmsell-rain-season`
(package `kalshi_bot/experiment_os/rain_season.py`). Experiment OS is canonical for its state,
its gates and every verdict; this document records the evidence that set the contract, and the
contract itself. It authorizes nothing.

## 1. The question

MMSELL on Kalshi's daily rain markets (`KXRAIN`, "will the named station record more than
0.00 in today", one market per city-day) is the most consistent positive cell the bot has.
Calvin asked four things:
1. Is it robust across cities, strike, price band and time to close?
2. Does winter break it?
3. How much size can it take?
4. What exactly is the winning selection, and is it already one registered book?

## 2. Duplicate and revival check (Control Tower, 2026-10-14 02:23Z, `rain-ct-1014a`)

- **Not a duplicate.** No open experiment isolates KXRAIN or gates any winter question. The
  cell runs inside several other experiments' books:
  - `mmsell10`, `Gmmsell0/1`, `Rmmsell1/2`, the wide `mmsell` control (paper);
  - live `Kmmsell10` (`mmsell-price-ceiling-cell-size`), which sizes `event_stat` markets,
    KXRAIN included, at 3 contracts.

  Each of those books shares its settlement-date cap with every other series it trades, and
  each is judged by another contract's gate.
- **Not a revival.** No rain MMSELL family has been killed. The retired `WXRAIN` idea was a
  different mechanism: observation-pinning on precipitation accumulation
  (`docs/IDEA_MODEL_20260710.md` N6). It was never built.

## 3. Evidence, Aug 8 – Oct 13 (ops `db` reads `rain-q1`…`rain-q7`, catalog `catalog_scorecard_series`)

### 3.1 The edge lives in the cheapest prices only

Paper results at market level, twins excluded. A "lost" market is one that settled YES.

| NO entry price (YES sold) | markets | dates | lost | implied YES | realized YES | ¢/contract |
|---|---|---|---|---|---|---|
| < 85 (YES 15–40¢, wide control book) | 74 | 41 | 21 | 27.2% | 28.4% | **−0.36** |
| 85–89 (YES 11–15¢) | 58 | 33 | 5 | 12.2% | 8.6% | +8.07 |
| 90–92 (YES 8–10¢) | 157 | 59 | 7 | 8.5% | 4.5% | +6.11 |
| ≥ 93 (YES ≤ 7¢ — **the cell**) | 226 | 62 | **1** | ~6.5% | **0.4%** | **+5.74** |

- **The 15–40¢ band is calibrated** and only the cheap tail is rich. That is the shape of a
  favourite–longshot floor, not a forecasting edge:
  - YES buyers pay a few cents for "rain at all" on days the forecast calls dry;
  - nobody sells YES below about 6¢, because it ties up 94¢ to earn 6¢.

  A structural floor is more believable than a quirk, and it is the reason winter is the
  question. The floor survives only if winter still has many days that are truly ~1% but
  priced at 5–7¢.
- **Statistics:**
  - The break-even YES rate at a 93.4¢ NO entry is ~6.6%.
  - 1 loss in 226 markets has a binomial probability of about 5×10⁻⁶ at that rate.
  - Clustering is modest: markets per settlement date are mostly 3 (the rung cap), and the one
    losing date lost one of three cities.

### 3.2 Robustness

- **Strike.** Not applicable: every market has one strike (> 0.00 in). The price band in
  §3.1 is the analogue of strike distance.
- **Cities.**
  - All 31 traded cities are net positive except a few. Weak or negative:
    - SEA −5.4¢ (one loss, ten books on it);
    - MKE −2.0¢ (5 markets);
    - IND (1 market);
    - OKC +1.1¢, SFO +1.4¢, ATL +1.7¢, DAL +2.1¢.
  - In the wider bands, losses cluster in convective South and Plains cities (ATL, DEN, LV,
    MIA, OKC, DAL, HOU) and on the West Coast at the start of the wet season (SFO, SEA).
  - In the cell, the only loss is SEA on 10 Oct.
- **Time to close.** Results by hours from first entry to close:

  | hours to close | markets (cell) | ¢/contract |
  |---|---|---|
  | 0–6 h | 32 | +6.1 |
  | 6–12 h | 40 | +1.8 (the SEA loss) |
  | 12–24 h | 107 | +6.4 |
  | 24 h+ | 47 | +6.2 |

  Across all bands, the last 3 hours are negative (−2.1¢, 12 markets): there the market
  already knows about rain in progress.
- **Month (cell).** Aug +6.1¢ (103 markets, 0 lost); Sep +6.5¢ (89, 0 lost); **Oct +3.5¢ (34,
  1 lost)**. The one loss and the weakest month are both October, the first wet-season month
  for the Pacific Northwest. This is the early warning for §4.
- **Books.** Every book is positive, from mmsell +7.3¢ down to mmsell6 +4.3¢. The paper books
  are not independent: they trade the same markets. The independent units are markets and
  settlement dates, and those are what §3.1 counts.
- **Paper versus live.**
  - On the same markets: live +5.78¢, twin +5.61¢. Paper tracks live here.
  - Live overall: 66 attributed markets, 39 date-bets, 38 won. The one losing date netted
    −$0.52 (SEA −$0.94 against two winners).
- **The 5 "blocked" live markets.** These are markets not yet settled when the scorecard ran,
  not missing data:
  - At 08:49Z on 13 Oct they were OCT12-PIT, OCT12-CHI, OCT13-MIN, OCT13-SGF and OCT13-NYC.
  - PIT and CHI settled as wins later that morning; the 12:30Z snapshot then showed 67
    attributed and 4 blocked.
  - The three OCT13 markets close on 14 Oct.

  No hidden loss sits behind the totals. The 12:30Z snapshot also shows the attribution count
  rising as settlements land.

### 3.3 Capacity (`mmsell_candidate_ticks`, `live_orders` + `fills`)

- **Depth in the cell** (YES ask 5–7¢):
  - YES-bid depth, which a taker hits: median 41–50 contracts (p10 4–5, p90 400–1,500).
  - NO-bid queue ahead of a maker order: median 38–173.
  - Spread: 1.5–1.9¢.
  - Market lifetime volume: median 2k–50k contracts.
- **Live fills:**
  - 74 of 117 accepted orders filled (63%), and 107 of 171 contracts.
  - Median time to fill: 30–65 minutes.
  - Kmmsell10's 3-lots fill: 7 filled, 3 timed out.
- **Supply.** Each book is held to **3 cities per settlement date** by the global rung cap
  (KXRAIN events are not mutually exclusive). The union of books saw up to 10 cell markets on
  one date, and 3.6 per date on average. The rung cap, not depth, is what limits markets per
  day.
- **Reading:**
  - A 10-lot is under 1% of a typical market's volume and well inside median depth.
  - As a maker, a 10-lot should mostly fill, partially and more slowly. As a taker, it costs
    about 1.7¢ spread plus fee, leaving about 3¢ of the 5–6¢ edge.
  - Fills should start to degrade from about 25–50 contracts per market (the median queue
    and YES-bid depth).
  - Size per market is therefore not the binding constraint up to ~10–25. The real ceiling
    is the tail: one loss costs ~93¢ × size.

### 3.4 Winter (`kxrain_season_probe`, `kalshi_market_probe` `rain-hist-probe-1014`)

Kalshi lists no KXRAIN event before July 2026: Dec 2025, Jan, Feb, Mar and May 2026 all
return 404, while Jul 15 and Aug 1, 2026 exist. **No winter tape exists anywhere, so winter
cannot be backtested.** It can only be observed going forward, which is what §5 registers.

Why winter could break the cell:
- melted snow counts, and light snow of about 0.1 in gives about 0.01 in of water, which
  settles YES;
- the wet season starts on the West Coast (SEA, SFO, LAX);
- rain is frequent on many winter days in BOS, NYC, CHI and MIN, so fewer days are truly dry.

Why it might not:
- winter precipitation comes from large-scale systems, which are more predictable than summer
  pop-up storms, so dry days are more confidently dry;
- trace and missing data settle No, which favours the NO seller.

The data cannot decide this, so the contract has to.

### 3.5 Independent replay on Kalshi's own quotes (`kxrain_season_probe`, `rain-probe-full-1014`)

The bot's paper tape covers only the markets its books happened to enter, at most 3 cities per
date per book. The probe replays the cell rule on **every** settled KXRAIN market Kalshi has
listed:
- 2,044 markets listed, 1,818 settled and priced;
- hourly candles from T−1h to T−48h;
- the earliest in-cell hourly quote taken as the entry;
- gross P&L, 1 contract, at the maker price.

| slice | markets | dates | implied YES | realized YES | losses | ¢/contract | ¢/date ± s.e. |
|---|---|---|---|---|---|---|---|
| **all** | 264 | 61 | 6.55% | **2.27%** | 6 | **+4.28** | +18.5 ± 4.4 |
| Aug | 117 | 26 | 6.56% | 0.00% | 0 | +6.56 | |
| Sep | 121 | 24 | 6.50% | 4.13% | 5 | **+2.36** | |
| Oct (to 13th) | 26 | 11 | 6.73% | 3.85% | 1 | **+2.88** | |
| entry ≤ 4h to close | 44 | 32 | 6.57% | 0.00% | 0 | +6.57 | |
| entry 6–12h | 56 | 30 | 6.59% | 5.36% | 3 | +1.23 | |
| entry 18–24h | 86 | 44 | 6.60% | 1.16% | 1 | +5.44 | |
| entry > 24h | 78 | 39 | 6.45% | 2.56% | 2 | +3.88 | |
| YES sold at 6¢ | 119 | 46 | 6.00% | 3.36% | 4 | +2.64 | |
| YES sold at 7¢ | 145 | 58 | 7.00% | 1.38% | 2 | +5.62 | |

Losses: Sep 13 OKC, Sep 20 DEN, Sep 24 CMH, Sep 28 LV, Sep 28 PIT and Oct 10 SEA. They were
spread over 5 dates and 6 cities, with no front hitting many cities at once.

What this changes:
- **The edge is real but smaller than the paper tape shows.**
  - Replay: +4.3¢ at about 4 standard errors per date.
  - Bot paper: +5.7¢, 1 loss in 226.
  - The tape's near-zero loss rate is partly which 3 cities won each date's slots. Most of the
    replay's losers were not entered by the cell books.
- **The edge is decaying with the season.**
  - August was perfect; September and October run at about +2.5¢.
  - That is at the +2¢ pass bar, before any live fill gap.
  - Winter, the main risk, starts from an already thinner autumn.
- **Time to close.**
  - Entries in the final 4 hours are cleanest (0 losses in 44).
  - The 6–12h window, typically an afternoon storm forming, carries half the losses.
  - This is a candidate rule for a future Version only. It is post hoc, so it is not adopted
    here.
- **Rules check.** Every city settles on the NWS climate report (`CLI<station>`) for the named
  station, which matches the approved rules (DEC-035).

## 4. What the winning selection is (question 4)

**The selection:** buy NO at the no-bid on a KXRAIN city-day when 5 ≤ YES mid ≤ 10 and the YES
price actually sold is ≤ 7¢ (`lo=5,hi=10,maxyes=7`, the `mmsell10` rule), 1 contract, held to
settlement.

**It is not one registered book.** It is the `mmsell10` rule as it happens to fall on KXRAIN,
spread across the books in §2. The live expression is `Kmmsell10`, a 3-lot under the
cell-size canary's envelope:
- $3 per market;
- $9 per event;
- −$30 canary loss budget.

`mmsell-rain-season` is the first contract that isolates it.

## 5. Pre-registration — `mmsell-rain-season` v1

### 5.1 Arms (PAPER, 1 contract, KXRAIN only by exact match)

| arm | role | tag | spec |
|---|---|---|---|
| `rain_cell` | control | `Rainmmsell1` | `lo=5,hi=10,maxyes=7,onlyx=KXRAIN` — the winning selection |
| `rain_adjacent` | treatment | `Rainmmsell2` | `lo=8,hi=14,maxyes=12,onlyx=KXRAIN` — the next band up, disjoint from the cell (the capacity lever) |

**No size arm.** A scaled-paper arm would book exactly N times the 1-lot P&L, because a paper
fill assumes the whole clip at the no-bid, so it would measure nothing. Capacity is read from
depth and live fills (§3.3).

**No promotion gate.** That would pre-authorize a transition with no winter evidence behind it.

### 5.2 Gates — one per arm, `kind=kill`, not pooled

| clause | value |
|---|---|
| sample (promotion floor) | `settled_days ≥ 120` |
| horizon | `settled_days = 180` (last permitted look, ~mid-April) |
| pass_all | `pnl_cents_per_contract ≥ +2.0` and `daily_pnl_stability ≥ 0.15` |
| fail_any, early (eligible at `settled_days ≥ 45`) | `pnl_cents_per_contract < 0` |
| fail_any, at floor | `pnl_cents_per_contract < +1.0`, or `daily_pnl_stability < 0` |

**Why the floor covers winter.**
- The arms open in mid-October, and a book settles at most one day per calendar day.
- So 120 settled days cannot be reached before **mid-February**, and at least ~73 of the
  counted days fall in December–February. A slow book reaches the floor later, which only adds
  more winter.
- The gate is evaluated on the epoch's whole window. Gates cannot window by date, so a strong
  autumn could in principle carry a weak winter. §5.3 covers that.

**Why these bars.**
- +2¢ is the measured paper-to-live maker gap (`docs/MMSELL_FILL_MODEL.md`). A paper edge below
  it would not survive live fills.
- +1¢ at the floor is the level below which the edge cannot pay for live adverse selection.
- Neither is fitted to the +5.7¢ observed.
- The stability bar catches tail losses that arrive together on a front-passage day.

### 5.3 The winter split — pre-registered, observational, authorizes nothing

At the first gate look (and at 1 Jan as an interim), Research Lab reads the **December–February
subset** of `Rainmmsell1` with the ops `db` channel. **Before** recommending any size step, it
requires all of:
- at least **60** settled cell markets on at least **30** distinct December–February dates;
- winter realized YES rate at least **2 percentage points below** the winter mean implied rate;
- winter ¢/contract **≥ +2**.

A winter read that fails any of these blocks the sizing proposal even if the whole-window gate
passes. That is the answer to a strong autumn carrying a weak winter. The read is recorded as
an XOS issue's evidence, never as a verdict.

## 6. Sizing recommendation for Calvin

**Now: keep rain at Kmmsell10's 3 contracts. Do not scale before winter evidence exists.**

The independent replay (§3.5) puts the realistic autumn edge at about **+2.5–3¢ per contract**,
not the +5.7¢ the paper tape shows, and it is shrinking month by month. That is reason enough
not to add size into the season most likely to hurt it.

Risk at the current size:
- **One losing market:** −$2.82.
- **Worst day:** −$8.46. The rung cap allows at most 3 rain markets per date.
- **Canary loss budget:** −$30, unchanged.
- **Expected:** about +$5 a month (about 55 filled markets × 3 × 3¢).

55 filled markets a month is the upper end: 3 per date at a 63% fill rate. The observed live
pace under earlier caps and stand-downs was about 1 a day, so read every figure below as a
ceiling.

Then, conditional on the gate. The expected column uses +3¢:

| step | when | size on KXRAIN | expected / month | one loss | bad day (3 cities) | if winter erases the edge (YES rate 12%) |
|---|---|---|---|---|---|---|
| hold | now → first look | 3 | ~+$5 | −$2.8 | −$8.5 | ~−$9 |
| optional early step (Calvin's call) | after a clean December per §5.3 interim | 5 | ~+$8 | −$4.7 | −$14 | ~−$15 |
| scale | `cell_winter_keep` PASS **and** §5.3 winter split passes (~mid-Feb) | 10 | ~+$17 | −$9.4 | −$28 | ~−$30 |
| ceiling | only with live fill evidence at 10 | 25 | ~+$41 | −$23.5 | −$70 | ~−$75 |

Each step above 3 contracts is a **Live Ops canary on its own Version**, with its own risk
envelope. A size increase expands real-money exposure, so it is a hard stop that needs Calvin's
explicit confirmation. Nothing in this document or its experiment arms it.

Even at the 10-lot step, rain alone is about a sixth of the $100/month north star. It is worth
keeping and worth growing carefully, but it is not the whole answer.

If `rain_adjacent` also passes, the same step applies to the 8–12¢ band. That roughly doubles
the qualifying markets per day at a smaller edge.

## 7. Cost and status

Two paper books and two worker redeploys (the registration and the `MMSELL_VARIANTS` append).
No real money.

Registered 2026-10-14 03:20:41Z (receipt `mmsell-rain-season-register-202610140320`,
SUCCEEDED; approver Calvin, per the PR body). Both tags sit on the PAPER deployment
`mmsell-rain-paper-1`, and both books were appended to `MMSELL_VARIANTS` at about 03:33Z.
Experiment OS holds the live state; this section is not updated.
