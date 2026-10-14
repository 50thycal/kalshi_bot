# NFL totals and ITF men — two positive leads, held for a pre-registered forward test

**Investigated 2026-10-14, Research Lab.** Request: confirm or kill MMSELL's two positive leads
from the WS-023 scorecard (`series-scorecard-v2`, snapshot 2026-10-13): **KXNFLTOTAL** and
**KXITFMATCH**, and give Calvin a keep / grow / stop call for each. Recommend only. This document
changes no universe, epoch, Version, gate or exposure. Related: `XOS-000039` (live tennis pause,
whose lift criterion this proposes for ITF men), `XOS-000046` (the MLB cell, same method),
`XOS-000020` (the game is the correlation unit).

> ## Verdicts
>
> | series | call | in one line |
> |---|---|---|
> | **KXNFLTOTAL** | **KEEP — do not grow** | The live run is real money and has not lost yet, but the paper edge outside the August preseason is about 1 s.e. Leave `Kmmsell10` trading it at 1 contract; the forward test below decides. |
> | **KXITFMATCH** | **KEEP in paper — live stays paused** | Paper is +2.6¢/trade on the live rule (2.0 s.e.), but ITF men is the best of four tennis tours and live tennis as a family is about flat. Do not lift the tennis pause for ITF men until the forward test passes. |
>
> Neither is a STOP: nothing here is negative. Neither is a GROW: both are about what the best
> of 13 series looks like when no series has an edge. And neither can move the $100/month goal
> alone. At 1 contract, NFL totals earns about $3–5 a month even if the edge is real.

---

## 0. Control Tower and screen (2026-10-14 02:24Z, ops `nflitf-ct-1`)

- No open experiment is scoped to either series, so this is **not a duplicate**. `mmsell10` (the
  live rule's paper control), `Rmmsell1/2` (reviewed universe) and `Gmmsell0/1` already trade both
  series in paper.
- **Not a revival.** Neither series has a kill verdict. `KXNFLSPREAD` (`XOS-000022`) is a
  different market.
- **Live today:** `LIVE_STRATEGIES` is `Kmmsell10` (cell-size canary, arm `mmsell10`, epoch 2)
  plus the liquidity book. `Kmmsell10` trades NFL totals. **No live book trades tennis.** The
  `XOS-000039` skip (`KXATP,KXWTA,KXITF`) is live-wide, and `Jmmsell10` is stood down.
- **Paper tape: none built.** `mmsell10` paper is the exact live rule (`lo=5,hi=10,maxyes=7`). It
  is registered on four experiments and kept by the 2026-10-11 fleet review. A new tape scoped to
  these two series would duplicate it and add rows against the WS-024 budget. The forward test
  reads `mmsell10` directly.

## 1. NFL totals (KXNFLTOTAL)

### Fees: verified

The series charges maker fees (`quadratic_with_maker_fees`). Kalshi billed all **41 live fills
0.09–0.16¢ per contract**, rounded to $0.0001 and never to a whole cent (ops `nflitf-q3`). Paper
`kalshi_fee(maker=True)` charges `0.0175·p·(1−p)` with no ceiling: 0.10–0.16¢ at 90–95¢, the
same amount. Paper P&L **includes the real maker fee**. At about 0.1¢ against a 2–7¢ edge,
it does not change any conclusion. (Four retired 2026-08 books show 1¢/contract, from the
pre-fix taker formula. None is in any figure below.)

Overtime counts toward the total. That is already in every settled outcome. Late-game entries
(more than 3 h after kickoff, where overtime lives) are 10 markets with 2 losses, too few to read.

### The headline number is mostly the August preseason

All MMSELL paper, twins excluded, **one row per market** (so the ~10 clone books that trade the
same market count once). Kickoff is Kalshi's `close_time − 48 h`, checked against 1 pm, 4:25 pm
and Monday-night kickoffs (ops `nflitf-q4`, `nflitf-q6b`).

| cell | markets | lost | loss % | break-even % | ¢ per market | games | mean $/game ± s.e. |
|---|---:|---:|---:|---:|---:|---:|---:|
| ≤10¢ tail, **preseason** (Aug) | 197 | 8 | 4.1 | 7.5 | +3.57 | 50 | +0.141 ± 0.072 |
| ≤10¢ tail, **regular season** | 213 | 11 | 5.2 | 7.3 | **+2.00** | 75 | **+0.057 ± 0.063** |
| — of which 6–7¢ tail | 124 | 3 | 2.4 | 6.5 | +3.99 | 66 | +0.075 ± 0.028 |
| — of which 8–10¢ tail | 89 | 8 | 9.0 | 8.4 | −0.76 | 54 | −0.013 ± 0.067 |
| 11–20¢ tail, regular season | 64 | 14 | 21.9 | 13.6 | −8.48 | 45 | −0.121 ± 0.079 |
| >20¢ tail, regular season | 97 | 40 | 41.2 | 32.8 | −8.86 | 45 | −0.191 ± 0.158 |

The preseason was positive in **every** price band. The regular season is positive only in the
6–7¢ tail and negative from 8¢ up. Preseason scoring is low (starters sit), which could leave
overs overpriced. That is a separate hypothesis for August 2027 (parked).

**Per book, regular season, entries at ≥90¢** (ops `nflitf-q10`): `mmsell10` +1.77¢ over 87
trades and 47 games (z = 0.6); `mmsell5` +1.03¢; `Rmmsell1` +0.17¢; `mmsell9` +0.04¢; `mmsell6`
−0.06¢; `Rmmsell2` −0.58¢. `Cmmsell10`, `Dmmsell10` and `Gmmsell0` run the same rule and match
`mmsell10` exactly. The live-mirror books (`Fmmsell10`, `Jmmsell10`, `Kmmsell10` and their twins)
show +4 to +8¢ on 10–39 trades each, all recent, with one loss between them.

### Clock (regular and preseason, ≤10¢ tail)

| entry time vs kickoff | markets | lost | ¢ per market |
|---|---:|---:|---:|
| more than 24 h before | 91 | 4 | +2.96 |
| 2–24 h before | 71 | 5 | +1.07 |
| 0–2 h before | 29 | 0 | +7.47 |
| in play, 0–1.5 h | 132 | 7 | +1.84 |
| in play, 1.5–3 h | 77 | 1 | +5.86 |
| in play, after 3 h | 10 | 2 | −12.52 |

There is no monotone timing effect. Every bucket with 29+ markets is positive.

### Live

| | games | markets×book | contracts | lost | net | ¢/contract |
|---|---:|---:|---:|---:|---:|---:|
| all live, Aug–Oct | 31 | 41 | 52 | **0** | +$3.63 | +6.98 |
| of which regular season | 25 | 27 | 31 | 0 | +$2.15 | — |

The scorecard's 24 bets is the same ledger with 7 markets blocked on ownership. Twin and live
agree on shared markets (+7.23 / +7.24¢). With a 7.1% break-even loss rate, **no losses in 31
independent markets happens 10% of the time with zero edge**. Ladders on one game are correlated,
so the true chance is higher. The streak is encouraging, not evidence.

### NFL read

- Pooled paper of 2,158 trades (+2.76¢, 1.7 s.e.) counts each market up to 10 times. Deduplicated
  to one row per market, the regular season is **+2.0¢ per market, 0.9 s.e. by game**.
- The cell that looks strong (6–7¢ tail, regular season, 3 losses against 8 expected,
  Poisson p = 0.04) was found by splitting after the fact. Its neighbour band is negative.
- The live rule trades 90–95¢ (5–10¢ tail), so it straddles the good band and the flat one.

**Call: KEEP at current size.** The exposure is 1 contract per market on `Kmmsell10` and costs
little while the question is open. Growth would need the forward test to pass, an operator
decision and a recorded Experiment OS transition.

## 2. ITF men (KXITFMATCH) — and why it differs from ITF women

### The tail band, all tours (paper, one row per market, ops `nflitf-q2`)

| tour | markets at ≤10¢ tail | loss % | break-even % | ¢ per market | ≈ z |
|---|---:|---:|---:|---:|---:|
| ITF men | 454 | 5.7 | 7.4 | **+2.05** | 1.7 |
| ITF women | 415 | 6.5 | 7.4 | +1.09 | 0.9 |
| WTA Challenger | 90 | 4.4 | 7.6 | +3.02 | 1.1 |
| ATP Challenger | 404 | 8.2 | 7.6 | +0.19 | 0.2 |

z uses a per-market s.d. of about 25¢. Per book at ≥90¢ (ops `nflitf-q10`), ITF men is positive on
every main book: `mmsell10` +2.60¢ (217 trades, z = 2.0), `mmsell6` +2.45¢ (310, z = 2.1),
`Rmmsell1` +2.41¢, `Gmmsell0` +1.42¢. ITF women on `mmsell10`, `mmsell6` and `Gmmsell0` is +1.18¢,
+0.66¢ and −0.55¢ (`Rmmsell1` does not trade it). The books are the same rule on overlapping
markets, so they are not independent confirmations.

### Why men's ITF looks different, factor by factor

1. **Odds band.** In the band live trades, men (+2.05¢) and women (+1.09¢) differ by about
   1¢ ± 1.7¢: **not distinguishable**. Most of the "men positive, women negative" gap in
   all-book paper comes from the **11–20¢ tail** (men +4.1¢, women −2.8¢), which only the wide
   paper books trade. Live does not trade it.
2. **Walkovers.** ITF settles a match that never starts at $0.50, a 43¢ loss on a 93¢ NO.
   Challenger uses fair price. But **no tennis paper row in any of the four series settled at
   $0.50** (all are 0 or 100, plus 13 voids). MMSELL enters in play: most ITF entries settle
   within an hour. The no-start rule cannot reach a match that has started. A mid-match
   retirement makes the retiree NO. That works for whoever leads, which is usually our side.
   **Not a factor.**
3. **Live book J.** Women's live −$4.75 is the size-split canary `Jmmsell10` (2–3 contract clips,
   2026-10-04 to 10-09): **−$6.12** over 30 markets, 17.9% of contracts lost. Without J, ITF women
   live is **+$1.37** over 96 markets. J also lost $1.85 on ITF men. This is the `XOS-000039`
   episode: in-play fills within minutes of posting that settled against the book.
4. **Tour level (M15 vs M25).** Not measurable this session. The level is in the event title,
   which only the catalog stores, and the sandbox cannot reach Kalshi. Parked.
5. **Clock.** By hours to Kalshi's expected expiration, ITF men at the ≤10¢ tail is +1.87¢ (3–6 h,
   264 markets) and +2.02¢ (1–3 h, 119). It is stable. Realized time to match end is **not** used,
   because a comeback both makes the match longer and makes MMSELL lose, so it biases any
   time split.

**The honest summary: ITF men does not clearly differ from ITF women where live trades.** It is
the best of four tennis tours in paper. Live tennis as a family is about −$3 over ~520 contracts
(ITF men +$3.88, ITF women −$4.75, ATP Challenger −$2.74, WTA Challenger +$0.47).

### Live ITF men

154 contracts on 122 markets, 4.6% lost against a 7.1% break-even, +$3.88 (+2.52¢). Twin and live
agree on shared markets (+2.10 / +2.24¢). By month: Jul–Aug +$4.20 with no loss, Sep +$1.04,
Oct −$1.36 (J).

**Call: KEEP in paper; live stays paused.** The forward test below is the proposed `XOS-000039`
lift criterion **for KXITFMATCH alone**, with ITF women read alongside as a control.

### Live fills are not adversely selected here (cross-check)

The `XOS-000046` addendum (`docs/MMSELL_MLB_TOTALS_SPREADS_CELL.md` §5.3) splits cheap-band paper
by whether live was filled on the market. MLB score lines lose where live gets filled. These two
series do not. KXNFLTOTAL makes +6.58¢ on live-filled markets against +1.77¢ elsewhere. KXITFMATCH
makes +3.16¢ against +2.65¢. ITF women is +0.03¢ against +1.50¢, a small penalty that is not
significant. So the gap between live and paper here is not the MLB fill problem. That keeps live
NFL as a fair read of the paper edge, and makes it unlikely that a lift on ITF men inherits that
problem.

## 3. Why neither is a GROW: selection

These two were picked from 13 series after looking, with about 30 paper variants each. If no
series had an edge, the best of 13 would score **z ≥ 1.7 45% of the time** and **z ≥ 2.1 21%
of the time** (simulation-free: `1 − Φ(z)^13`). NFL (1.7 pooled, 0.9 deduplicated regular season)
and ITF men (2.0–2.1) are exactly what the top of 13 nulls looks like. Only fresh data can
separate them from noise.

---

## 4. Pre-registered forward test (frozen 2026-10-14, before any data it reads exists)

| element | KXNFLTOTAL | KXITFMATCH |
|---|---|---|
| population | `paper_trades`, strategy **`mmsell10`** (the live rule's paper control), status `settled`/`closed_sl`, **created ≥ 2026-10-15T00:00Z** | same |
| unit | the **game**: all `mmsell10` trades on one event ticker, summed | the **match** (event ticker) |
| statistic | mean net $ per game, sample s.e., one-sided 95% lower bound `LCB = mean − 1.645·s.e.` | same, per match |
| floor | **n ≥ 100 games** (about early January at ~9 games/week) | **n ≥ 200 matches** (about end of December at ~18/week) |
| **GROW-eligible** | at the floor, `LCB > 0` | at the floor, `LCB > 0` **and** mean ≥ the ITF-women mean (control below) |
| **STOP** | at the floor, mean ≤ 0 | at the floor, mean ≤ 0 |
| **KEEP** | otherwise. Read once more at season end (after the Super Bowl, all games). GROW-eligible if `LCB > 0`, STOP if mean ≤ 0, otherwise KEEP at 1 contract with no growth until 2027. | otherwise. One extension to **n ≥ 400**. GROW-eligible if `LCB > 0`, otherwise STOP for live (the pause stays). |
| control | — | `mmsell10` on **KXITFWMATCH**, same window and statistic, read descriptively |
| short sample | fewer than the floor by 2027-02-15: read `Rmmsell1` (same band and ceiling) instead; if that is also short, INCONCLUSIVE → KEEP | fewer than the floor by 2027-01-31: same fallback, then INCONCLUSIVE → pause stays |

**What each outcome means for Calvin:**
- *NFL GROW-eligible*: the edge is confirmed at about 3¢/trade or better. Proposing size is then
  a real-money hard stop (operator words naming the action) and a recorded Experiment OS
  transition. Nothing grows by itself.
- *NFL STOP*: remove KXNFLTOTAL from `Kmmsell10`'s live universe. That reduces exposure, but it
  is still a recorded transition (new epoch) for that arm.
- *ITF GROW-eligible*: evidence to lift the `XOS-000039` pause **for KXITFMATCH only**, at the
  live canary's existing size. Lifting adds real-money exposure, so it is an operator hard stop.
- *ITF STOP*: close the ITF-men lead. The pause stays and paper keeps trading.

**Power, stated so a miss is not over-read.** The s.e. is estimated from forward data. From the
2026 per-game spread ($0.35 NFL, $0.19 ITF), the bar for GROW is about **3.1¢/trade** at 100 NFL
games and **2.2¢/trade** at 200 ITF matches (1.55¢ at 400). A true 2.8¢ NFL edge passes 43% of the
time and a true 7¢ edge 98%. A true 2.6¢ ITF edge passes 62% at 200 and 87% at 400. STOP fires
by chance on a true 2.6–2.8¢ edge 3–7% of the time. The 2026 NFL spread came from a sample with
only 4 losing games, so it understates the tail.

**Diagnostics (recorded, never part of the decision):** NFL by tail band (6–7¢ vs 8–10¢) and by
kickoff clock. Live `Kmmsell10` NFL net vs its twin on shared markets. ITF women and ATP
Challenger under the same statistic.

**Live tripwire (NFL, during the test):** if `Kmmsell10`'s live KXNFLTOTAL net since 2026-10-15
reaches **−$3.00**, open a Live Ops issue for review. That is about 4 net losses beyond what the
wins cover, the J-style pattern. It is a prompt to look, not an automatic action.

The branches cover every outcome and are not to be re-interpreted after results.

## 5. Out-of-mission findings (dispositions)

| finding | disposition |
|---|---|
| NFL preseason was positive in every band (+3.6¢ at ≤10¢, +4–5¢ above). A low-scoring-preseason overs hypothesis. | **PARK** — August 2027, pre-register before then |
| NFL regular season, 11¢+ tail is −8.5 to −8.9¢ per market (wide paper books such as `mmsell` only) | **DISCARD** — no live book trades it, and the wide book is a control, not a candidate |
| ITF tour level (M15/M25) split needs catalog event titles | **PARK** — a catalog read, if the ITF test passes |
| Realized time-to-end is biased by comebacks for in-play timing studies | **DISCARD** as a clock here (noted in §2.5) |

## 6. Reproduce

Ops reads, 2026-10-14: `nflitf-ct-1` (Control Tower), `nflitf-q1` (paper by book and fee rate),
`nflitf-q2` (one-row-per-market splits, five series), `nflitf-q3` (live NFL fees), `nflitf-q4`
(kickoff clock), `nflitf-q5` (tennis clocks), `nflitf-q6b` (NFL band, phase, clock), `nflitf-q7`
(tennis clock and settlement values), `nflitf-q8` (live ledger by book, band, month), `nflitf-q9`
(live books in the last 10 days), `nflitf-q10` (per-book paper at ≥90¢), `nflitf-x1/x2` (xos tag,
XOS-000039). Ops results keep only the newest 80 files, so the queries that decide are copied here.

**Gate statistic** (`{"type":"db","sql":"<query>","max_rows":10}`). Swap `mmsell10` for
`Rmmsell1` for the short-sample fallback.

```sql
with p as (
  select split_part(market_ticker,'-',1) ser, split_part(market_ticker,'-',2) game, pnl, quantity q
  from paper_trades
  where strategy = 'mmsell10' and status in ('settled','closed_sl') and pnl is not null and quantity > 0
    and created_at >= '2026-10-15T00:00:00Z'
    and split_part(market_ticker,'-',1) in ('KXNFLTOTAL','KXITFMATCH','KXITFWMATCH')),
g as (select ser, game, sum(pnl)::numeric gp, sum(q)::numeric q, count(*) n from p group by 1,2)
select ser, count(*) games, sum(n) trades, round(100*sum(gp)/sum(q),2) c_per_ct,
  round(avg(gp),4) mean_g, round((stddev_samp(gp)/sqrt(count(*)))::numeric,4) se_g,
  round((avg(gp) - 1.645*stddev_samp(gp)/sqrt(count(*)))::numeric,4) lcb, count(*) filter (where gp < 0) games_lost
from g group by 1 order by 1
```

**Live NFL tripwire and diagnostic.** Live fills, outcomes taken from settled paper on the same
market. Per WS-023, the deprecated `NO/sell` fill label is read as the bot's NO/buy intent.

```sql
with f as (
  select fl.market_ticker t, split_part(fl.market_ticker,'-',2) game, lower(fl.side) side,
    fl.price::numeric px, coalesce(fl.quantity,0)::numeric q, coalesce(fl.fee,0)::numeric fee
  from fills fl join live_orders lo on lo.kalshi_order_id = fl.kalshi_order_id
  where lo.strategy = 'Kmmsell10' and split_part(fl.market_ticker,'-',1) = 'KXNFLTOTAL'
    and fl.filled_at >= '2026-10-15T00:00:00Z'),
r as (
  select market_ticker t, max(case when resolved_value=100 then lower(side)
    when resolved_value=0 then case when lower(side)='yes' then 'no' else 'yes' end end) res
  from paper_trades where status='settled' and resolved_value in (0,100)
    and split_part(market_ticker,'-',1)='KXNFLTOTAL' group by 1),
m as (
  select f.game, f.t, (case when r.res = f.side then 100*sum(q) else 0 end - sum(px*q))/100.0 - sum(fee) pnl
  from f join r on r.t = f.t group by f.game, f.t, f.side, r.res)
select count(distinct game) games, count(*) markets, round(sum(pnl)::numeric,2) net,
  count(*) filter (where pnl < 0) markets_lost from m
```

The one-row-per-market split queries (`nflitf-q2`, `-q6b`, `-q7`) group `paper_trades` by
`market_ticker` (twins excluded), take `100·Σpnl/Σquantity` as cents per market, and sum those per
event ticker for the game figure. NFL kickoff is `mmsell_settlement_meta.close_time − 48 h`.
Tennis expected expiration is `close_time − 330 h` (ITF) or `− 333 h` (Challenger).
