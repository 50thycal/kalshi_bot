# MLB totals and spreads — a live loss cell, held for a pre-registered 2027 test

**Investigated 2026-10-13, Research Lab.** Issue **`XOS-000046`** (STRATEGY, owner
`RESEARCH_LAB`). Related: `XOS-000020` (the game is the correlation unit), `XOS-000022`
(the KXNFLSPREAD loss cell, whose structure this follows). Descriptive evidence; this document
changes no universe, epoch, Version, gate or exposure.

> ## Verdict: **HOLD — too thin to call, and probably not an MLB-market finding.**
>
> Live MMSELL lost money on MLB totals and spreads, but the cell was picked after looking at
> 13 series, and at that selection the result is what noise produces about half the time.
> The paper family on the **same series, window and price band** is break-even on totals and
> positive on spreads. If anything is real here, it is in which quotes get filled live, not in
> the markets themselves. Nothing changes now (no live exposure until the 2027 season); the
> question is pre-registered below and read on 2027 data only.

---

## 1. The evidence

All live MMSELL, source-ledger economics. Ops reads `rl46-live-cal3`, `rl46-live-all`,
`rl46-band-kxmlbtotal`, `rl46-band-kxmlbspread`, `rl46-sp-total`, `rl46-sp-spread`
(2026-10-13). The SQL reproduces the catalog scorecard: spreads −$4.91 over 82 games exactly,
totals −$5.03 against the catalog's −$5.06 over 88 games.

### Live, per game (one game = one bet)

| cell | games | net $ | mean ± s.e. per game | z |
|---|---:|---:|---:|---:|
| KXMLBTOTAL | 88 | −5.03 | −0.057 ± 0.050 | −1.14 |
| KXMLBSPREAD | 82 | −4.91 | −0.060 ± 0.046 | −1.30 |
| **TOTAL + SPREAD on one game, counted once** | **144** | **−9.94** | **−0.069 ± 0.040** | **−1.72** |
| all MLB (catalog family, PR #609) | 332 | −12.55 | −0.038 ± 0.026 | −1.46 |

The combined row is new. It is the cell the question asks about, keyed on the game the way
`XOS-000020` says the correlation runs. 26 of the 144 games lost money.

### Live, against break-even (market level)

MMSELL buys NO at about 92¢. It breaks even when the share of losing markets equals the
average tail price.

| cell | markets | contracts | break-even loss % | observed loss % | gap |
|---|---:|---:|---:|---:|---:|
| KXMLBTOTAL | 117 | 133 | 8.25 | 11.97 | **+3.7pp** |
| KXMLBSPREAD | 104 | 126 | 8.01 | 13.46 | **+5.5pp** |
| **all live MMSELL since 2026-07-01** | 2,879 | 3,514 | 7.43 | 7.88 | **+0.45pp** |

Other live cells with 40+ markets range from −5.9pp (KXRAIN) to +5.6pp (KXWTI), with KXBTCD at
+4.3pp. MLB totals and spreads are in the bad tail of that spread, not outside it.

### Paper, same series, same window, same price band

`mmsell_series_pnl --days 90 --maxyes 11 --split-subjects` (fill-everything paper, twins
excluded):

| series | contests | loss % | break-even % | edge | c/trade | net $ |
|---|---:|---:|---:|---:|---:|---:|
| KXMLBTOTAL | 516 | 8.1 | 7.4 | −0.7pp | −0.66 | −31.40 |
| KXMLBSPREAD | 525 | 5.6 | 7.1 | **+1.4pp** | +1.39 | +83.04 |

All time, all prices: totals −0.6pp over 663 contests, spreads +2.1pp over 570 contests.

---

## 2. Why this is a HOLD

1. **The cell was found by looking at all of them.** The scorecard shows 13 series. If every
   series truly broke even, simulation puts the **second-worst** of 13 z-scores at −1.14 or
   lower **51%** of the time. Totals (−1.14) and spreads (−1.30) are the second- and
   third-worst. Combining them was also chosen after seeing that both were negative. The
   combined one-sided p of 0.043 becomes 0.55 after a Bonferroni correction over the 13 series.
2. **The paper family does not show it.** On about 520 games each, in the same window and band,
   paper totals are close to break-even and paper spreads are positive. A truly mispriced MLB
   market would lose on paper too. KXNFLSPREAD (`XOS-000022`) did: −11.1pp in paper.
3. **The gap is between paper and live.** Live spreads lose on 13.5% of markets against paper's
   5.6%. At the market level that is z ≈ 3.5, but the comparison was chosen after the fact, and
   paper loss % is counted per trade, not per market. Read it as a pointer: the problem may be
   in which resting quotes get filled live (adverse selection), which a universe cut would only
   hide.
4. **The money is small.** Totals and spreads together lost about $4 a month over 2.5 months.
   Stopping them cannot move the $100/month goal. Keeping them costs little while the question
   is open.
5. **There is nothing to stop right now.** The last total traded 2026-09-27 and the last spread
   2026-10-04. Any postseason exposure is a few dollars at most.

---

## 3. Pre-registered 2027 test (frozen 2026-10-13, before any 2027 data exists)

| element | value |
|---|---|
| population | live MMSELL fills on KXMLBTOTAL and KXMLBSPREAD, 2027 **regular season** only. 2026 data is not pooled because it produced the hypothesis. Postseason is excluded. |
| unit | the **game**: all markets of both series on one game, summed across live books |
| statistic | mean net $ per game, with standard error, from source-ledger economics (catalog scorecard family method, or this document's query as a fallback) |
| floor | **n ≥ 150 games**, about the 144 that 2026 produced, so both seasons are read at equal power |
| **CONFIRM — stop** | at the floor, mean + 1.96·s.e. < 0 |
| **REFUTE — keep** | at the floor, mean ≥ 0 |
| INCONCLUSIVE | otherwise. Extend to **n ≥ 300 games** and read once more. CONFIRM if mean + 1.645·s.e. < 0, otherwise REFUTE. |
| short sample | fewer than 150 games by the end of the 2027 regular season means the exposure is too small to matter. Close with no action. |
| power | at 2026's per-game s.d. of $0.48, 300 games detect a true −$0.069/game with 80% power (one-sided α = 0.05). At 2026's rate of about 1.75 games a day, that is most of a season. |

The branches cover every outcome and are not to be re-interpreted after results.

**Diagnostic, not part of the decision:** the live market loss % minus the paper loss % in the same
window and band (≤ 11¢). On CONFIRM with paper edge ≥ 0, record the finding as a live-fill
selection cell as well as a universe cut. The live-fill work belongs to the queue/fill research
(`docs/MMSELL_QUEUE_DEPTH_THESIS.md`, `docs/MMSELL_FILL_MODEL.md`), not to this ticket.

**What CONFIRM authorizes:** nothing by itself. Removing the two series from any live book's
universe is a recorded Experiment OS transition (new epoch or Version) for each live book, and
it needs operator confirmation. Paper keeps trading both series.

---

## 5. Addendum 2026-10-14: the selection behind the live losses

New evidence: catalog scorecard v2 (WS-023 "Paper beside live", 2026-10-13). All MMSELL paper
on MLB spreads made +$167.85, +$0.234 ± $0.090 per game over 717 games. Live lost −$4.91. On
live's own markets, paper also lost (−2.26¢ per contract), and the twin tracked live. That
raised one question: does live *pick* spreads differently from the profitable paper books?

**Does not change §3.** The 2027 test is frozen. This addendum explains the mechanism and does
not re-score the gate. Ops reads: `rl46b-books`, `rl46b-livesubset`, `rl46b-xseries`.
All are SELECT-only and aggregate-only.

### 5.1 Live picks spreads the way its own paper lineage does

| | tail ¢ | lines 1–2 / 3–4 / 5+ | entry vs listed start | net ¢/contract | z (per game) |
|---|---:|---|---:|---:|---:|
| **live** | 7.9 | 22 / 64 / 14 % | −1.8 h (99% in-game) | −3.90 | −1.30 |
| cheap-band paper (`mmsell10`, `Cmmsell10`, `Dmmsell10`, `Lmmsell10`, `mmsell9`, `Tmmsell6`, `mmsell6`, `Gmmsell0`) | 6.5–7.7 | ~21 / 64 / 15 % | −1.6 to −1.8 h (97–100%) | +0.34 to +2.20 | 0.2 to 1.9 |
| **profitable paper** (`mmsell`, `mmsell1`, `mmsell2`, `W*`) | **14–22** | ~27 / 52 / 21 % | −1.7 to +4.2 h (36–84%) | +4.0 to +6.3 | 1.4 to 2.8 |

Line mix and timing are the same for live and its paper lineage. The money-makers differ in
one way that matters: they sell **14–22¢ tails**, a band live never trades. After correcting
for ~30 paper variants, no single book is established. The best, `mmsell1` at z = 2.81, has a
Bonferroni-corrected p of 0.07; `mmsell` (z = 2.25) has 0.37. The `W*` books are three
copies of one book. The pooled family (+1.98¢, per-game z ≈ 2.4) is not selected, but it is
still fill-everything paper, mostly in a band live does not trade.

### 5.2 What loses is the market that got filled live, not the selection rule

Cheap-band paper (tail ≤ 11¢), the same 2026-07-15 → 10-04 window, split by whether live got a
fill on that market:

| KXMLBSPREAD paper | markets | games | ¢/contract | trade loss % | per game |
|---|---:|---:|---:|---:|---:|
| markets live was filled on | 104 | 82 | **−4.65** | 11.6 | −0.494 ± 0.318 |
| other markets, same games | 159 | 70 | +5.09 | 2.0 | +0.481 ± 0.102 |
| games live never filled | 1,126 | 430 | +2.10 | 5.0 | +0.207 ± 0.083 |

Same tail price (6.8–7.6¢), same rule. Paper loses only on the markets where a live resting
bid got hit. In a game the live book traded, the *other* cheap markets in that game won. So
the fill is the bad news: in-play flow hits the resting NO bid when the tail is about to
come true.

### 5.3 This penalty is specific to MLB game lines

The same split across 8 series (cheap band; market-level loss %; z uses a 1.3 design effect
for clustering by game; Bonferroni over 8):

| series | live-filled ¢/ct (loss %) | not-live ¢/ct (loss %) | z | corrected p |
|---|---|---|---:|---:|
| **KXMLBSPREAD** | **−4.65 (13.5)** | +2.50 (6.5) | 2.36 | 0.07 |
| **KXMLBTOTAL** | **−5.06 (13.7)** | +0.46 (7.8) | 1.94 | 0.21 |
| KXMLBHR | −0.20 (7.5) | +1.14 (6.2) | 0.90 | 1 |
| KXMLBKS | +0.80 (7.9) | +0.02 (6.4) | 0.30 | 1 |
| KXITFWMATCH | +0.03 (7.4) | +1.50 (7.2) | 0.06 | 1 |
| KXITFMATCH | +3.16 (2.8) | +2.65 (5.8) | −1.07 | 1 |
| KXNFLTOTAL | +6.58 (0.0) | +1.77 (6.3) | −1.24 | 1 |
| KXRAIN | +5.75 (1.8) | +5.83 (3.7) | −0.62 | 1 |

Totals and spreads together: z = 3.04, Bonferroni-corrected p ≈ 0.01. The combination is
counted as a ninth look, and the result still holds. Six other series show no penalty, and
rain and NFL totals actually do better on live-filled markets. This is a finding about
**resting bids on in-play MLB score lines**, not about MMSELL fills in general.

### 5.4 What this changes about the recommendation

- **Stop the series?** Still not now. A stop would remove the loss, but it would aim at the wrong
  variable. Paper on the same series, band and games is profitable where live is not filled.
- **The likelier fix is mechanical:** don't rest cheap-band bids in-play on MLB score lines
  (pre-game only), or cancel when the line moves. That work belongs to the fill/queue
  research (`docs/MMSELL_QUEUE_AWARE_CANCEL.md`, the WITHDRAWAL probe in
  `docs/MMSELL_CHATGPT_PROBES_20261004.md`), not to this ticket.
- **Wide-band spreads (14–22¢)** look good on paper but are not established after correction,
  and live has never traded them. This is not a recommendation.
- **Caveats:**
  - every number in §5 is from 2026, the same data that suggested the question;
  - "live-filled" is defined by an event paper cannot see at entry, so it describes the
    mechanism and is not a usable selection rule;
  - the 2027 §3 read remains the decision.

---

## 4. Reproduce

The live per-game and per-market query is below. Ops results keep only the newest 80 files, so
this copy is the durable one. It joins live MMSELL fills to settled paper outcomes on the same market. Per WS-023's
canonical-direction finding, it reads the deprecated `NO/sell` fill label as the bot's NO/buy
intent. It prints aggregates only. The paper rows come from:

```
{"type":"script","name":"mmsell_series_pnl","args":["--days","90","--maxyes","11","--split-subjects","--series","KXMLBTOTAL"]}
{"type":"script","name":"mmsell_series_pnl","args":["--all-time","--split-subjects","--series","KXMLBSPREAD"]}
```

Run it as `{"type":"db","sql":"<query>","max_rows":50}`. For 2027, change the `filled_at`
lower bound to Opening Day.

```sql
WITH f AS (
  SELECT fl.market_ticker AS t, split_part(fl.market_ticker,'-',1) AS ser,
         split_part(fl.market_ticker,'-',2) AS game, lower(fl.side) AS side, 'buy'::text AS act /* WS-023: deprecated NO/sell label = bot intent NO/buy */,
         fl.price::numeric AS px, coalesce(fl.quantity,0)::numeric AS q, coalesce(fl.fee,0)::numeric AS fee
  FROM fills fl JOIN live_orders lo ON lo.kalshi_order_id = fl.kalshi_order_id
  WHERE lo.strategy ILIKE '%mmsell%'
    AND split_part(fl.market_ticker,'-',1) IN ('KXMLBTOTAL','KXMLBSPREAD')
    AND fl.filled_at >= '2026-07-01'),
r AS (
  SELECT market_ticker AS t,
         max(CASE WHEN resolved_value=100 THEN lower(side) WHEN resolved_value=0 THEN CASE WHEN lower(side)='yes' THEN 'no' ELSE 'yes' END END) AS res
  FROM paper_trades WHERE status='settled' AND resolved_value IS NOT NULL
    AND split_part(market_ticker,'-',1) IN ('KXMLBTOTAL','KXMLBSPREAD') GROUP BY 1),
m AS (
  SELECT f.t, f.ser, f.game, f.side, r.res,
         sum(CASE WHEN act='buy' THEN q ELSE -q END) AS netq,
         sum(CASE WHEN act='buy' THEN q ELSE 0 END) AS bq,
         sum(CASE WHEN act='buy' THEN px*q ELSE 0 END) AS bcost,
         sum(CASE WHEN act='sell' THEN px*q ELSE 0 END) AS sproc,
         sum(fee) AS fee
  FROM f LEFT JOIN r ON r.t=f.t GROUP BY 1,2,3,4,5),
mp AS (
  SELECT *, (CASE WHEN res IS NULL THEN NULL WHEN res=side THEN 100*netq ELSE 0 END - bcost + sproc)/100.0 - fee AS pnl
  FROM m),
g AS (SELECT ser, game, sum(pnl) AS gp FROM mp WHERE res IS NOT NULL GROUP BY 1,2),
gc AS (SELECT 'COMBINED' AS ser, game, sum(pnl) AS gp FROM mp WHERE res IS NOT NULL GROUP BY 2)
SELECT 'mkt' AS k, ser, side, count(*) AS mkts, count(*) FILTER (WHERE res IS NULL) AS unresolved,
       sum(bq) AS contracts, round(sum(bcost)/nullif(sum(bq),0),2) AS avg_px,
       round(100*sum(bq) FILTER (WHERE res IS NOT NULL AND res<>side)/nullif(sum(bq) FILTER (WHERE res IS NOT NULL),0),2) AS loss_pct_contracts,
       round(100*count(*) FILTER (WHERE res IS NOT NULL AND res<>side)::numeric/nullif(count(*) FILTER (WHERE res IS NOT NULL),0),2) AS loss_pct_mkts,
       round(100 - sum(bcost)/nullif(sum(bq),0),2) AS breakeven_loss_pct,
       round(sum(pnl),2) AS net, NULL::numeric AS games, NULL::numeric AS mean_g, NULL::numeric AS se_g, NULL::numeric AS games_lost
FROM mp GROUP BY ser, side
UNION ALL
SELECT 'game', ser, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, round(sum(gp),2), count(*), round(avg(gp),4),
       round((stddev_samp(gp)/sqrt(count(*)::numeric))::numeric,4), count(*) FILTER (WHERE gp<0)
FROM (SELECT * FROM g UNION ALL SELECT * FROM gc) x GROUP BY ser
ORDER BY 1,2,3
```
