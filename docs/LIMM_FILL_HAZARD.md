# LIMM fill hazard — which incentive series pay net of the fills a resting bid takes

**Status:** READ 2026-11-13 (§8): V0 OK, V1 nominates 2 series (both model-sensitive), V2 none. PRE-REGISTERED 2026-11-01 (Research Lab), before any run. Nothing in the window was
read except collector row counts (the 4-hour check-in). **Read once on 2026-11-13** (see §7).

Study 5 of the incentive-programme research (the first from the 72-hour research review,
`docs/RESEARCH_REVIEW.md`). Probe: `scripts/limm_fill_hazard.py` (read-only, ops-allowlisted).
Book: `Alimm1` (`docs/LIQUIDITY_INCENTIVE_THESIS.md`). Experiment OS is canonical. The probe is
**descriptive**: it ranks and nominates. Any universe or rule change is an owner decision,
recorded as a new epoch.

## 1. Question

Study 3 v2 (`LIMM_PROGRAMME_OUTCOMES.md` §10) found series outside the live universe that pay
about twice the reward per market-day. It could not say whether they pay **net**, because a
resting bid gets filled and a fill costs money. On the book's own record, fills cost more than
rewards paid: −$105 of fills against $86 of rewards, lifetime (scorecard 2026-10-31).

> For each series the shadow collector watches, what does a cheap-side bid at the live rule
> earn per market-day in reward, **minus** what the fills it would have taken cost?

This targets the two ways to improve the programme: better markets (more net per market-day),
and avoiding the markets whose fills eat the reward.

## 2. Data and window (no lookahead)

- **Window:** 2026-10-30T16:00Z (the shadow collector back on, `rl-limm-shadow-on-20261030`)
  → 2026-11-13T15:00Z. Fixed now.
- **Hours:** `incentive_market_snapshots`, the last valid snapshot per market per clock hour,
  joined to its programme row. A qualifying hour and its cheap side, price, size and reward use
  `LIMM_PROGRAMME_OUTCOMES.md` §3 unchanged: both sides at Target Size, programme active,
  liquidity type, ≥ 48 h before close, cheap side 1–10¢, $20 at ≥ 4¢ else $10, ≤ 500
  contracts, reward = pool/hour × qty/(field + qty) / 2, hours with share > 0.5 excluded from
  reward and counted.
- **Fills:** the public trade tape (`incentive_trade_events`), timed by the exchange timestamp
  (`ts_ms`, else `received_at`). The bid placed on hour *h*'s snapshot rests through the next
  clock hour *h*+1 and meets only that hour's prints, so price and queue are known before the
  trades that score them.
- **Fill model: `conservative`** (`kalshi_bot/liquidity_incentive/fills.py`). We join the back
  of the cheap side's best level. The depth at best in the snapshot is ahead of us, and
  only prints at our price, or through it, on our side consume it. The `optimistic` model (any
  touch fills us fully) is reported beside it as a sensitivity. It never decides.
- **After a simulated fill,** that market earns nothing more in the window. §9.70 cancels the
  rest of the bid, and the book does not re-enter a market it holds.
- **Fill cost (markout):** filled contracts × (fill price − the same side's best bid 6 h later),
  from the snapshot of hour *h*+7. If that hour has no snapshot, the nearest one within
  *h*+5…*h*+9 is used. If none exists, the fill is charged half its price, the live stop's
  trigger. A bid that rose counts as a gain. Fees are excluded (§5).

## 3. Measures (per series)

- **market-days:** distinct (market, date) over qualifying hours.
- **reward:** the summed reward over each market's hours up to its first fill.
- **fill loss:** the summed markout cost of the fills.
- **net per market-day `R`** = (reward − fill loss) / market-days. It is a ratio estimate with
  an event-clustered standard error (linearised: residual per event = net − `R` × market-days).
- **fill rate:** fills per 100 market-days.

## 4. Pre-registered labels and verdicts

Floor per series: **≥ 20 market-days and ≥ 5 events**, else `THIN`.

| label | rule |
|---|---|
| `NET-LEAD` | `R` ≥ $0.50 and `R` − 1.96·se > 0 |
| `FILL-TRAP` | reward per market-day ≥ $0.50 and `R` ≤ 0 |
| `NO-LEAD` | every other series at the floor |

- **V0 — instrument.** If fewer than 3 series reach the floor, the verdict is **HOLD
  (accrual)**. There is one re-read on 2026-11-27, with the window end moved to
  2026-11-27T15:00Z and the same bars.
- **V1 — better markets exist.** Any series outside the live universe that is `NET-LEAD`, with
  `R` above the live universe's pooled `R`, is **nominated**. A nomination is an owner decision
  to add the series as a new epoch, a live change, never automatic.
  - The live universe keeps today's definition: `MENTION` series and `KXYTVIEWSHIGH`.
- **V2 — markets to avoid.** Any series inside the live universe that is `FILL-TRAP` is
  **nominated for removal**. This is also an owner decision.
- **Model-sensitive:** a series whose label differs under `optimistic` is marked `*`. A
  nomination that is model-sensitive is reported as such. It still stands, because
  `conservative` decides.

## 5. Known limits (fixed now, not re-scoped later)

- **Hourly placement.** Each hour is a fresh bid at the best bid. The live book rests longer
  and reprices.
- **Queue ahead counts only depth at best;** cancellations ahead of us are ignored. So the
  conservative model under-fills.
- **The markout at 6 h is a proxy for the stop's exit;** it is not the settlement.
- **Maker fees are excluded.** Fills are rare, so fees move `R` little. A nomination still goes
  through the owner and the book's own fee accounting.
- **The population is what the collector watches** (the top ~150 programmes by reward), not all
  of Kalshi.
  - **Disclosed after registration, not a re-scope (2026-11-08):** DEC-043.2 changes the
    collector to pick its ~150 markets by **daily pay** instead of total reward, from its deploy
    (~2026-11-08/09) to the end of this window. Series that enter or leave the watched set then
    contribute fewer hours; the per-series rules, bars and read date are unchanged. The read
    should state each series' hour count, which it already does.
- **The live universe's own hours count only when the collector snapshotted them.**

## 6. Cost

Read-only. One snapshot query and one trade query over 14 days (about 50k hourly snapshots and
a few thousand prints). The statement timeout is raised as in study 3.

## 7. Command (read once on 2026-11-13, after the LIMM exits-vs-hold read)

`{"type":"script","name":"limm_fill_hazard","args":[],"id":"rl-s5-run1-20261113"}`

A re-read under V0: `{"type":"script","name":"limm_fill_hazard","args":["--until","2026-11-27T15:00:00+00:00"],"id":"rl-s5-run2-20261127"}`.

## 8. Results

**Read once: `rl-s5-run1-20261113`, 2026-11-13T16:11Z, code `c6f85c7`.** The scheduled
fresh-session routine fired at 15:57Z but could not push to `ops`, because it had no write
access to the repo. It ran nothing. The 72h-review session ran the identical command an hour
later. The window end is fixed by the script (15:00Z), so the data read is the same.

```
# ops request [READ]
# type=script  id=rl-s5-run1-20261113
# code c6f85c70e649e847ea2ebcaaf2212ec1a4c1c3ec · started 2026-11-13T16:11:16Z
LIMM fill hazard — reward minus fill markout per market-day (pre-registered docs/LIMM_FILL_HAZARD.md)

window 2026-10-30T16:00 -> 2026-11-13T15:00; hourly snapshots (incl. mark tail) 64030; prints 48910
qualifying cheap-side hours 31814 (thin, no reward: 4); markets 581; markets with prints 364

== series (deciding model: conservative; * = label differs under optimistic)
  series                     live mkt-d evts  rew/md loss/md R net/md   ±se fills /100md   R opt  label
  KXCABALLOTMEASURES                 12    1   +4.05   +0.00    +4.05   n/a     1    8.3   +0.50  THIN
  KXGENERALCOUNTY                    77    6   +3.58   +0.94    +2.64  0.80    14   18.2   +2.99  NET-LEAD*
  KXSTATEBALLOTMEASURE               43    5   +3.44   +1.32    +2.12  0.62     8   18.6   +0.31  NET-LEAD*
  KXREPRODUCTIVEMEASURES              1    1   +1.21   +0.00    +1.21   n/a     0    0.0   +1.21  THIN
  KXTRUMPENDORSEMENTS                22    1   +1.72   +0.64    +1.08   n/a     2    9.1   +0.00  THIN
  KXHORMUZMAX                        12    1   +0.88   +0.00    +0.88   n/a     0    0.0   -1.19  THIN
  KXNYCSUBWAY                         5    1   +0.77   +0.00    +0.77   n/a     1   20.0   +0.48  THIN
  KXRETIREMM                          2    1   +0.75   +0.00    +0.75   n/a     0    0.0   +0.03  THIN
  KXBRRATECUT                         7    1   +0.67   +0.00    +0.67   n/a     0    0.0   +0.09  THIN
  KXNYSECEEMP                         7    1   +0.58   +0.00    +0.58   n/a     0    0.0   +0.58  THIN
  KXWEEKSNUM1                         4    1   +0.56   +0.00    +0.56   n/a     0    0.0   +0.56  THIN
  KXTXINCOMETAX                       4    1   +0.40   +0.00    +0.40   n/a     0    0.0   +0.40  THIN
  KXTRUTHSOCIAL                      31    1   +0.52   +0.21    +0.31   n/a     7   22.6   +0.49  THIN
  KXMLBTEAMSALE                       7    1   +0.28   +0.00    +0.28   n/a     0    0.0   +0.21  THIN
  KXBAA                              10    1   +0.24   +0.00    +0.24   n/a     0    0.0   -0.09  THIN
  KXBWAYATTENDANCE                    7    1   +0.24   +0.00    +0.24   n/a     0    0.0   -1.23  THIN
  KXDWTSTOP3                          2    1   +0.24   +0.00    +0.24   n/a     0    0.0   +0.07  THIN
  KXEOWEEK                           21    1   +0.90   +0.67    +0.23   n/a     2    9.5   +0.19  THIN
  KXDWTSRANK                          6    2   +0.20   +0.00    +0.20  0.06     0    0.0   +0.19  THIN
  KXPJMEMERGENCY                     28    1   +0.18   +0.00    +0.18   n/a     1    3.6   +0.18  THIN
  KXTRUMPSAY                          2    1   +0.17   +0.00    +0.17   n/a     0    0.0   +0.17  THIN
  KXBIGSKYOPEN                       56    1   +0.44   +0.27    +0.17   n/a     2    3.6   +0.01  THIN
  KXCAGOVLAMAYOR                      3    1   +1.15   +0.99    +0.17   n/a     2   66.7   -7.51  THIN
  KXMAMDANIEO                         1    1   +0.15   +0.00    +0.15   n/a     0    0.0   +0.15  THIN
  KXPANAMAWEEKLY                     33    2   +0.14   +0.00    +0.14  0.04     0    0.0   -0.13  THIN
  KXHORMUZWEEKLY                     23    2   +0.59   +0.45    +0.14  0.01     2    8.7   +0.19  THIN
  KXSUEZWEEKLY                       11    1   +0.13   +0.00    +0.13   n/a     0    0.0   +0.09  THIN
  KXPAMFGEMP                         63    1   +0.13   +0.00    +0.13   n/a     0    0.0   +0.08  THIN
  KXASPENMTNOPEN                     54    1   +0.42   +0.30    +0.12   n/a     3    5.6   -0.12  THIN
  KXWALLSTBONUS                      38    1   +0.12   +0.00    +0.12   n/a     0    0.0   +0.09  THIN
  KXNYCASKRENT                       48    1   +0.11   +0.00    +0.11   n/a     0    0.0   -0.39  THIN
  KXOPENVREQ                         10    2   +0.10   +0.00    +0.10  0.05     0    0.0   +0.10  THIN
  KXILHPIYOY                         10    1   +0.09   +0.00    +0.09   n/a     0    0.0   +0.00  THIN
  KXNYNJPORTTEU                      48    1   +0.09   +0.00    +0.09   n/a     0    0.0   +0.06  THIN
  KXBABELMANDEBWEEKLY                18    1   +0.09   +0.00    +0.09   n/a     0    0.0   -0.88  THIN
  KXORDPAX                           10    1   +0.09   +0.00    +0.09   n/a     0    0.0   +0.05  THIN
  KXROLEINPRODUCTIONLI               11    1   +0.09   +0.00    +0.09   n/a     0    0.0   +0.09  THIN
  KXCABUILDPERMITS                   37    1   +0.08   +0.00    +0.08   n/a     0    0.0   +0.08  THIN
  KXCITYCHAMPS                       22    2   +0.08   +0.00    +0.08  0.02     0    0.0   +0.08  THIN
  KXHORMUZPEAK                       25    1   +0.08   +0.00    +0.08   n/a     1    4.0   -1.01  THIN
  KXSENDEMNO3                        13    1   +0.08   +0.00    +0.08   n/a     0    0.0   +0.08  THIN
  KXTXERCOTPEAK                      67    1   +0.08   +0.00    +0.08   n/a     0    0.0   +0.02  THIN
  KXTRUMPSAYCOMPANY                   3    1   +0.07   +0.00    +0.07   n/a     0    0.0   +0.07  THIN
  KXNFLPLAYOFFC                       7    1   +0.07   +0.00    +0.07   n/a     0    0.0   +0.05  THIN
  KXOPENVSPEND                        6    2   +0.06   +0.00    +0.06  0.01     0    0.0   +0.06  THIN
  KXPRESDEBATEFIRSTD                  3    1   +0.05   +0.00    +0.05   n/a     0    0.0   +0.05  THIN
  KXPRESSTATE                        20   10   +0.05   +0.00    +0.05  0.01     0    0.0   +0.05  NO-LEAD
  KXMOONVSPEND                        5    2   +0.05   +0.00    +0.05  0.02     0    0.0   +0.05  THIN
  KXONIONACT                         15    1   +0.05   +0.00    +0.05   n/a     0    0.0   +0.01  THIN
  KXILNUCLEAR                        57    1   +0.08   +0.04    +0.04   n/a     1    1.8   -0.01  THIN
  KXWAAEROEMP                       135    1   +0.08   +0.05    +0.03   n/a     1    0.7   +0.01  THIN
  KXGOOGVREQ                          2    1   +0.03   +0.00    +0.03   n/a     0    0.0   +0.03  THIN
  KXWATECHEMP                        93    1   +0.03   +0.00    +0.03   n/a     0    0.0   +0.03  THIN
  KXPRESDEBATEFIRSTR                  4    1   +0.03   +0.00    +0.03   n/a     0    0.0   +0.03  THIN
  KXAOCMAMDANICOMBO                  10    1   +0.02   +0.00    +0.02   n/a     0    0.0   +0.02  THIN
  KXTXOIL                            40    1   +0.14   +0.12    +0.02   n/a     1    2.5   -0.14  THIN
  KXYTVIEWSHIGH               yes   148   15   +0.17   +0.16    +0.01  0.10     3    2.0   -0.03  NO-LEAD
  KXFEAR                              3    3   +0.01   +0.00    +0.01  0.00     0    0.0   -3.32  THIN
  KXDPRESPRIMARY                     22    2   +0.00   +0.00    +0.00  0.00     0    0.0   +0.00  THIN
  KXFLVISITORCOUNT                   42    1   +0.10   +0.12    -0.02   n/a     1    2.4   -0.20  THIN
  KXAAAGASM                         212    1   +0.32   +0.40    -0.08   n/a    14    6.6   -0.22  THIN
  KXFEDERALCHARGE                    28    1   +0.22   +0.36    -0.14   n/a     1    3.6   -0.75  THIN
  KXRT                              318   14   +0.25   +0.64    -0.39  0.30    31    9.7   -0.97  NO-LEAD
  KXNVDACEO                           9    1   +0.11   +0.57    -0.46   n/a     2   22.2   -1.59  THIN
  KXPAPER                            17    1   +0.16   +0.74    -0.57   n/a     3   17.6   -1.70  THIN
  KXMARATHONUSAWIN                    6    2   +1.88   +2.50    -0.62  0.53     2   33.3   -0.51  THIN
  KXTRUMPACT                         16    1   +1.10   +1.83    -0.73   n/a     3   18.8   -1.35  THIN
  KXNJPHARMAEMP                      12    1   +0.19   +1.04    -0.85   n/a     1    8.3   -0.85  THIN
  KXAPRPOTUS                         13    1   +0.68   +1.55    -0.87   n/a     3   23.1   -0.22  THIN
  KXTRUMPNOMNUM                       5    1   +0.48   +2.00    -1.51   n/a     2   40.0   -1.51  THIN
  KXGOPSIXSENATE                      3    1   +0.54   +2.33    -1.80   n/a     1   33.3   +0.21  THIN
  KXMAMDANIMENTION            yes     9    1   +0.14   +2.22    -2.08   n/a     1   11.1   -2.08  THIN
  KXDANCINGWITHTHESTARS              10    1   +0.09   +3.25    -3.16   n/a     4   40.0   -1.73  THIN
  KXTRUMPPHOTO                        8    1   +0.70   +4.71    -4.01   n/a     2   25.0   -4.01  THIN
  KXSENATESEATSD                      6    1   +0.09   +9.79    -9.70   n/a     4   66.7   -9.90  THIN
  KXHUANGTESTIFY                      1    1   +0.30  +10.00    -9.70   n/a     1  100.0   -9.70  THIN
  unmarked fills (charged half price): 18

== live universe pooled
  R -0.11 ± 0.17 per market-day over 157 market-days, 16 events

== VERDICT (descriptive; nominations are OWNER decisions, never automatic)
  V0 instrument: OK — 5 series at the floor
  V1 better markets: NOMINATE KXGENERALCOUNTY*, KXSTATEBALLOTMEASURE*
  V2 markets to avoid: none
  NET-LEAD     2  KXGENERALCOUNTY*, KXSTATEBALLOTMEASURE*
  FILL-TRAP    0  
  NO-LEAD      3  KXPRESSTATE, KXYTVIEWSHIGH, KXRT
  (* = model-sensitive: label differs under optimistic)
```

**Reading.**

- **V0 instrument: OK.** 5 series reached the floor of 20 market-days and 5 events, out of 581
  markets.
- **V1: two series nominated, both model-sensitive (`*`).**
  - `KXGENERALCOUNTY`: R +$2.64 ± 0.80 per market-day over 77 market-days in 6 events.
  - `KXSTATEBALLOTMEASURE`: R +$2.12 ± 0.62 over 43 market-days in 5 events.
  - Both beat the live universe's pooled R (−$0.11).
  - Under the optimistic fill model, STATEBALLOTMEASURE falls to +$0.31 per market-day.
  - Context, not a re-scope: both are county-election and ballot-measure markets that resolved
    with the **2026-11-03 election**. The nomination stands as registered. In practice it is a
    question for the next election cycle, because these programmes do not recur weekly.
- **V2: none.** No live-universe series is a FILL-TRAP.
  - `KXYTVIEWSHIGH` is NO-LEAD: +$0.17 reward against $0.16 in fills per market-day, so about
    break-even.
  - The mention series are mostly THIN; `KXMAMDANIMENTION` is −$2.08 on only 9 market-days.
- **Descriptive (not verdicts; the paper strategies read on 11-24).**
  - `KXRT` (Rotten Tomatoes) is NO-LEAD at −$0.39 per market-day: 31 fills on 318 market-days
    lose more than the reward.
  - `KXAAAGASM` is −$0.08 on 212 market-days, though only 1 event.
  - Many of the high-pool weekly series that S1 targets are THIN here, so S1's forward read is
    the real test.
- **What it moves:** this fills the ranker's fill-cost column (`LIMM_MARKET_RANKER.md` §3). It
  moves none of the paper strategies' bars.
