# MMSELL selection-bias audit — does the tail-sell edge hold across everything the scan saw?

**Status:** PRE-REGISTERED 2026-10-29 (Research Lab), written before the probe ran and before any
outcome of this population was read. **Verdict 2026-10-29 (`rl-sba-run1-20261029`): HOLD** —
P1 HOLD, P2 FAIL, P3 PASS; P4 per rule in §8. No cell is SIZE-SAFE; the edge is MAKER-ONLY.

Catalog research plan, study 1 (WS-023; plan in PR #692, `docs/CATALOG_RESEARCH_PLAN.md`).
Probe: `scripts/mmsell_selection_audit.py` (ops-allowlisted, read-only). Experiment OS is
canonical for experiment state; this document records a probe and its pre-registered reading.
**It authorizes nothing.** A cell labelled SIZE-SAFE or CUT here is evidence for an owner
decision taken through Experiment OS (a new epoch, Version or experiment), never a change by
itself.

## 1. The question

Paper MMSELL realized about $8.5k over 30 days; real money is about $79 in liquidity rewards plus
small MMSELL canaries. On the markets live actually traded, paper roughly matched live (WS-023
paper-beside-live snapshot, 2026-10-13); the gap is coverage — paper trades about 30× more
markets. So the question is not "is paper honest on live's markets" but:

> Across **every market the MMSELL scan considered, entered or not**, does selling the cheap YES
> tail make money — by series, strike structure, price band and hours to expiration — and which
> skip rules throw away markets that would have paid?

`catalog/research.py` lists `opportunity_population_not_imported` as the missing denominator for
every cohort. This study supplies it.

## 2. Data and provenance

- **Prices:** `mmsell_candidate_ticks` (Postgres, read-only via `DATABASE_URL_RO`). One row per
  in-band candidate per scan cycle, taped by the control book `mmsell` whether or not anything
  entered: bid/ask, depth at the touch, strike structure, volume, hours to close and to expected
  expiration. Since 2026-07-23; about 1.37M rows, about 23k+ tickers, 523 series (pg_stats read
  `rl-sba-shape3-20261029`, counts only).
- **Event key:** `mmsell_settlement_meta.event_ticker` (fallback: ticker minus its last segment).
- **Outcomes:** Kalshi public REST, `GET /markets?tickers=` then `/historical/markets?tickers=`,
  for this bounded candidate set only (decision D1 option (a): no new access). `result` in
  {yes, no} scores; anything else (void, scalar, unsettled) is excluded and counted.
- **Series category** (for the XOS-000009 split): `GET /series/{s}` → `category`, read only for
  series the substring rule hits (fallback when a read fails: the exact crypto series list in
  `scripts/mmsell_canary_slices.py`).
- **Who entered:** `live_orders` (any `%mmsell%` buy) and `paper_trades` (`mmsell10`, the live
  rule's paper control). Used only to label tickers, never as a P&L yardstick (XOS-000031).

Sources are not mixed inside a measurement: every price comes from the tape, every outcome from
Kalshi, every "entered" label from the bot's own order tables.

### What the tape can and cannot audit

The tape is written **after** these gates, so markets they drop never appear and cannot be scored
here: global `mmsell_skip_series` (`KXMVE,KXHIGH,KXLOW`), `mmsell_min_volume` (100), the control's
hours-to-close window (1–336h), the control mid band (5–40¢), two-sided books, the per-cycle
capture cap (400). Gates **after** capture are auditable: the per-book `skip=` substring list
(XOS-000009), `maxyes`, the live tier (`graduated`), `mmsell_live_skip_series`
(`KXNFLSPREAD,KXATP,KXWTA,KXITF,KXBTCD`, prefix match), and market-type filters.

## 3. Construction (no lookahead)

- **Primary entry:** each ticker's **first** candidate tick — the moment the scan first saw it
  in band, i.e. what an always-enter scanner takes. Entry uses only that tick's quote; the
  settlement result only scores.
- **HTE entries** (for the hours-to-expiration cut only): the first tick of each ticker inside
  each `hours_to_expiration` bucket — "if we entered only at this distance from resolution".
  Buckets: `<=0`, `0-1`, `1-3`, `3-6`, `6-24`, `24-72`, `>72` hours, `na`.
- **Pricing, per contract, in cents** (Y = 1 if YES settled):
  - **Taker** (sell YES into the bid = buy NO at `100 − yes_bid`):
    `yes_bid − 100·Y − taker_fee`. Fee `ceil(0.07·q·p·(1−p)·100)/q`, `p = (100 − yes_bid)/100`,
    `q = min(10, depth_at_best_bid)` (1 when depth is unknown). Fill size is **capped at
    `depth_at_best_bid`**. This is the realizable floor: it needs no fill assumption.
  - **Maker, fill assumed** (rest at the no bid = sell YES at the ask, MMSELL's real rule):
    `yes_ask − 100·Y − 0.0175·p·(1−p)·100`, `p = (100 − yes_ask)/100` (paper-engine convention,
    no per-order ceiling). This is an **upper bound**: resting orders fill adversely
    (`docs/MMSELL_FILL_MODEL.md`), so unconditional maker P&L overstates realized maker P&L.
  - **Maker, fill proxy** (diagnostic only): counted filled when a later tick of the same ticker
    shows `yes_bid >= entry yes_ask`; unfilled books 0. Biased toward missing adverse fills,
    because the tape stops recording once the mid leaves 5–40¢.
- **Price band** on the entry YES ask (what MMSELL sells at; matches `maxyes`):
  `<=7` (the live cell), `8-10`, `11-15`, `16-20`, `21-30`, `31+`.
- **Strike structure:** `strike_type` × whether `floor_strike` / `cap_strike` is set; within
  KXMLBTOTAL / KXMLBSPREAD also by line (`floor_strike`).
- **Unit of independence:** the **event**. Each market counts once (one contract); the mean is
  ¢/contract; the standard error is cluster-robust over events (markets of one game or one ladder
  move together).
- **Capacity:** per cell, `Σ min(10, depth_at_best_bid) × taker P&L` over markets with known depth,
  scaled to $/30 days over the tape's span. Reported with median depth and the share of markets
  with depth ≥ 10.

## 4. Pre-registered predictions

All on the primary entry. "Maker" means fill-assumed unless stated. Intervals are mean ± z·SE.

| # | Claim | PASS | KILL / FAIL |
|---|---|---|---|
| P1 | The edge exists across the whole opportunity population in the live cell (ask ≤ 7¢) | maker ≥ +1.0¢/ct **and** 95% LCB > 0 | maker mean ≤ 0 with ≥ 300 events → paper's profit is selection, not edge |
| P2 | It survives crossing the spread (size can come from taking) | taker 95% LCB > 0 in the ≤ 7¢ band | taker 95% UCB < 0 |
| P3 | Live's entries are representative of the population | live-traded minus never-live-traded maker ¢/ct (≤ 7¢) within ±2¢, or CI includes 0 | live-traded worse by > 2¢ with 95% CI excluding 0 → live entry selects adversely (descriptive; feeds XOS-000046's fill-mechanics reading) |
| P4 | Each auditable skip rule removes markets that do not pay | removed set's maker point ≤ 0, or 95% LCB ≤ 0 | removed set's **taker** 95% LCB > 0 → the rule throws away profitable markets (maker-only LCB > 0 is reported as "maker-only, weak") |

**Study decision rule.** P1 PASS → the edge is broad; read cells (§5) for where. P1 KILL → the
paper edge is selection; recommend no size growth anywhere and a review of paper as a yardstick.
Otherwise HOLD.

## 5. Cell labels (pre-registered)

A cell needs **≥ 100 markets and ≥ 30 events**, else `THIN`. z = 2.58 for structural cells
(price band, HTE bucket, strike structure, settle mode, market type — about 35 cells) and z = 3.29
for series cells (one-sided 5% Bonferroni over about 100 series). Labels, first match wins:

1. `CUT` — maker (fill-assumed, the optimistic price) UCB < 0. Even the best case loses.
2. `SIZE-SAFE` — taker LCB > 0, ≥ 60 events, median `depth_at_best_bid` ≥ 10, ≥ 50% of markets
   with depth ≥ 10, and taker mean > 0 in ≥ 2/3 of months that hold ≥ 30 markets.
3. `HOLDS` — taker LCB > 0 (but fails a SIZE-SAFE depth or stability test).
4. `MAKER-ONLY` — maker LCB > 0, taker LCB ≤ 0. The edge depends on resting fills, which select
   losers; not a place to add size by taking.
5. `CUT-LEANING` — maker mean < 0, not confident.
6. `NO-CALL` — otherwise.

**XOS-000047 (KXNFLTOTAL, KXITFMATCH, KXITFWMATCH)** and **XOS-000046 (KXMLBTOTAL, KXMLBSPREAD)**
are printed with their numbers but **no label**: each has a frozen forward test that owns the
decision. This study informs them; it never re-scores them.

**Epochs.** The tape is the control scanner's capture, not any Experiment OS arm's evidence, so no
arm epoch is pooled. The capture conditions did drift (columns added, scan depth changed), so
every label carries the month-stability check and per-month numbers are printed for the
population cells.

## 6. Known limits

- Rules applied before capture (§2) cannot be audited.
- The per-cycle capture cap (400) may truncate some cycles; the probe reports how often.
- Taker assumes the touch is still there when we cross; depth is the snapshot's, not a fill.
- Fill-assumed maker is an upper bound; the fill proxy is biased low on adverse fills.
- Many cells are read at once; the z-levels above are the multiplicity control, fixed now.

## 7. Cost and correlation

Read-only: one windowed scan of the tape (a few minutes), two small label queries, about 300–600
batched public Kalshi calls plus one per series. No orders, no writes. Correlation with existing
books is total by design: this audits MMSELL's own universe.

## 8. Results

Ops run `rl-sba-run1-20261029` (2026-10-29 14:46Z, default-branch code `c1c0169b`, 402 s).
**102,587 settled markets** from 104,023 taped tickers (2,475 unsettled or void excluded), 23,904
events, 2026-07-23 → 2026-10-29. Cents per contract, event-clustered SE. Labels are applied exactly as
§4–§5 define them; nothing was re-scoped.

### 8.1 Predictions

| # | Measured (live cell, ask ≤ 7¢, 7,806 markets / 4,363 events) | Result |
|---|---|---|
| P1 | maker (fill assumed) **+0.87 ± 0.32**, 95% LCB +0.24 | **HOLD**: positive, below the +1.0¢ bar, not a kill |
| P2 | taker **−0.99**, 95% CI [−1.62, −0.35] | **FAIL**: crossing the spread loses |
| P3 | live-traded +0.52 vs never-live +0.95, diff −0.43 ± 0.72 | **PASS**: live picks representative markets |
| P4 | per rule below | see §8.3 |

**Study: HOLD.** The opportunity population carries a small fill-assumed edge, but nothing survives
crossing the spread, and the maker edge exists only if resting orders fill without adverse
selection. They do not:

- **Live orders, same population, maker price at the tape's first tick:** filled 1,079 markets at −0.98¢
  vs ordered-but-unfilled 461 markets at +4.04¢ (diff −5.03 ± 1.08). The losers fill and the winners do
  not. This is XOS-000031's mechanism, measured on the whole candidate set.
- **Quote-crossing fill proxy:** −1.65¢ in the live cell, and negative in every price band and every
  hours-to-expiration bucket. The proxy catches only 29% of fills, against about 70% live, so treat it as
  directional only.
- **Selection is in the fill, not the market:** paper `mmsell10` entered vs not differs by +0.23 ± 0.71,
  and live-chosen markets match the population (P3).

### 8.2 Cells

- **Price band:** every band is MAKER-ONLY. Maker +0.87 (≤7) / +1.57 (8–10) / +1.81 / +2.19 / +2.61 /
  +5.82 (31+); taker −0.99 / −1.34 / −1.78 / −2.13 / −2.54 / −3.89. The maker-minus-taker gap is the
  spread, and the outcome edge is negative at the bid.
- **Hours to expiration (live cell):** maker declines with distance. `<=0` +2.12, `0-1` +1.32, `1-3`
  +0.91, `3-6` +0.60, `6-24` +0.57, `24-72` **−0.21**, `>72` **−0.81** (both CUT-LEANING).
- **Strike structure / mode / type:** every cell with a floor is MAKER-ONLY or NO-CALL; none holds at
  taker. In the live cell `price_strike` (−2.06) and `outright` (−0.70) are CUT-LEANING, and `h2h`
  (+2.00) and `player_prop` (+1.25) are MAKER-ONLY.
- **Series, live cell (z 3.29):**
  - **HOLDS: KXRAIN only** (taker +3.38 ± 0.89, maker +5.44, median depth 32). It misses SIZE-SAFE only
    on events (51 < 60). Its registered gate (`mmsell-rain-season`) owns any growth.
  - **CUT-LEANING:** KXBTCD (−5.02) and KXNFLSPREAD (−5.13), both already live-paused; KXMLBKS (−0.41),
    KXNBASPREAD (−1.04), KXNFLRECYDS (−0.33).
  - **NO-CALL:** 9 series. **THIN:** 429. **No confident CUT anywhere.**
- **Series, all bands:** CUT-LEANING concentrates in soccer and team game-winner markets (KXMLBGAME,
  KXEPLGAME, KXMLSGAME, KXLALIGAGAME, KXNFLGAME …) and in KXWTI, KXUFCFIGHT, KXSERIEATOTAL, KXBTCD.

### 8.3 Skip rules (P4, removed set, 95% CI)

| Rule | Removed | taker | maker | Reading |
|---|---|---|---|---|
| crypto `skip=`, true crypto (live cell) | 349 / 132 ev | −7.72 [−14.8, −0.7] | −5.69 | correct: it removes losers |
| crypto `skip=`, **not** crypto (XOS-000009) | **2** / 2 ev | +4.63 | +5.90 | mechanical label "throws away profit", but **n = 2 in 98 days** (KXHEGSETHANNOUNCEOUT, KXHEGSETHOUT) |
| live pause (`KXNFLSPREAD,KXATP,KXWTA,KXITF,KXBTCD`) | 1,018 / 760 ev | −2.60 [−5.44, +0.25] | −0.92 | ok: the paused set does not pay |
| live tier < graduated | 3,686 / 1,785 ev | −0.68 | +1.20 [+0.38, +2.02] | maker-only, weak |
| `maxyes=7` (ask 8–10) | 12,205 / 6,034 ev | −1.34 | +1.57 [+1.01, +2.12] | maker-only, weak |

### 8.4 Issue views (inform, never re-score)

- **XOS-000046:**
  - KXMLBTOTAL all bands: taker −4.82, maker −1.23 ± 1.32. In the live cell it is 43 markets at maker −2.78.
  - KXMLBSPREAD all bands: maker +1.61. Live cell +2.45, but live-traded −0.92 vs never-live +3.39
    (diff −4.3 ± 5.5).
  - Same direction as the §5 addendum (live fills select losers). The frozen 2027 test is unchanged.
- **XOS-000047:**
  - KXNFLTOTAL ≤7 maker +0.42 ± 3.83 (84 markets); 8–10 +3.48 ± 2.03.
  - ITF men ≤7 +2.87 ± 1.56 vs ITF women +2.09 ± 1.58: the two do not separate.
  - The forward test is unchanged.

### 8.5 What it means

- **Where size is safe:** nowhere by the pre-registered test. KXRAIN is the one cell whose edge
  survives the taker price; it waits on its own gate.
- **Coverage is not the route to real money.** Paper's extra ~30× coverage earns fill-assumed spread
  capture. On every cut of the population, realized maker P&L (fill proxy; live filled orders) is at
  or below zero. Expanding live to more markets would buy more adverse fills.
- **What to cut:** nothing with confidence. The leaning cuts are entries ≥ 24h from expiration,
  `price_strike`/`outright` in the live cell, and the five series in §8.2. KXBTCD and KXNFLSPREAD are
  already paused, and this keeps them paused.
- **Skip rules:** the crypto list and the live pause remove losers. XOS-000009's false positives are
  real but negligible (2 markets). The tier and `maxyes` drop only maker-only edge.
- **Limits that bind this reading:** the capture cap truncated about half of all scan cycles (2,196 of
  4,415 twenty-minute buckets at ≥ 400 ticks), so the tape over-represents what the scan reaches first.
  The fill proxy misses most fills.

A study authorizes nothing. Each item above is an owner decision recorded through Experiment OS.
