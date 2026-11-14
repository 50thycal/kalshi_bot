# Idea-model run 2026-11-14: new studies for both lines (brainstorm only)

**Session role:** Research Lab. **Scope:** set by the owner's request (new studies across the
incentive line, MMSELL and anything new), so the Phase 0.5 menu was skipped. **Brainstorm
only:** nothing here is built, registered or changed until the owner locks the ideas. Experiment
OS stays canonical; this doc moves no gate, verdict, lifecycle state or exposure.

## Plain summary

- **The board has moved and our instrument has not.** Two new families now hold about **60%** of
  the incentive money we can see: spot-driven "event" ladders (gold, silver, copper, six FX pairs,
  four coins) and the next election cycle's nomination and race ladders. The collector watches
  **almost none of them** (1 market of about 4,560).
- **So the best next step is not a new strategy. It is pointing the instrument at the new money,**
  then testing the two ideas that money suggests (picks 1 and 2).
- **Pick 3 makes the incentive numbers trustworthy** before real money goes into the S2 live test.
- **MMSELL:** one cheap new question (do our resting MMSELL orders already earn rewards?) and two
  holds. Nothing in MMSELL looks bigger than the incentive line right now.

## Grounding (read, not re-run)

- **Incentive line.** S2 (rest a $10 bid at the best price) is the only arm that looks good:
  backtest run 2 gave +$1.86 per $10 bid-day at the touch and +$1.70 one tick behind (descriptive;
  the 11-24 forward read decides). S3 (step aside for releases) and S4 (the $50 burst) do not pay.
  Study 5: the only two series that clearly paid after fills were **2026-11-03 election ladders**
  (`KXGENERALCOUNTY` +$2.64/market-day, `KXSTATEBALLOTMEASURE` +$2.12); Rotten Tomatoes,
  the biggest watched series, lost (−$0.39). Study 9: bids above 10¢ do not help. Programme age:
  early hours do not pay more. **The weakest number under S2 is the 1.34 reward calibration,**
  which came from mention and YouTube payouts, not from the ladders S2 picks.
- **MMSELL.** Live books earn about +7¢ per contract with no losing market since the restart
  (scorecard 11-07). Politics is out (losses). P-SLOW v2 reads 12-04, gas ladders 12-07. NFL team
  props: +3.8¢ on 36 entries, re-run at about 100. NHL periods have no cell; MLB waits for spring.
- **Graveyard this month** (not re-proposed): BANDREV (over-reaction trading, KILL), LEVELHOLD
  (technical levels, KILL), MOMO-H (recent move, KILL), VOLCLOCK (clock, effectively closed), the
  live mention listener (closed by owner), mid-band bids (NO-LEAD), burst sizing (not paying).
  FAVHELD (BTC favourite side) and GRIDPIN (ERCOT pin) are already scheduled.
- **Scorecard base rate:** one paper book (PIN15) from about 30 promotions; most die at probe or
  come back untestable because the data did not exist yet. Hence the census-first rule below.

## Board survey (two read-only ops counts, 2026-11-14)

**Where the live incentive money is** (`idea-lims-1114a`, live liquidity programmes, summed
programme terms per day; the dollar unit is the derived `period_reward_usd`, so read the shares,
not the levels):

| family | example series | share of the top-40 pool |
|---|---|---|
| spot "event" ladders (new) | `KXGOLDEVENT`, `KXSILVEREVENT`, `KXCOPPEREVENT`, `KXUSDJPYEVENT` and 5 more FX, `KXBTCEVENT`/`ETH`/`SOL`/`HYPE` | **33%** |
| nomination and race ladders (new cycle) | `KXSENATENOM`, `KXGOVNOM`, `KXDPRESPRIMARY`, `KXRPRESPRIMARY`, state race series | **27%** |
| Rotten Tomatoes | `KXRT` | 14% (study 5: NO-LEAD) |
| everything else | art, YouTube, shipping weeklies, awards, gas | 26% |

**Does the collector see them?** (`idea-lims-1114c`, live markets vs markets with a snapshot in
the last 2 days): spot event ladders 3,013 markets, **1 watched**; nomination/race 1,551, **0
watched**. The collector's 150-market daily-pay cap and its watch pins were set before these
programmes existed. **Every idea about this money is untestable today until it is watched.**

## The slate (7 ideas, ranked by expected value to $100/month)

| # | idea (line) | thesis in one line | why it might work | cheapest falsifiable test | data needed | value to $100/mo | call |
|---|---|---|---|---|---|---|---|
| 1 | **NOM-LADDERS** (incentive) | Slow political ladders pay rewards net of fills, and the new cycle's nomination ladders are the next batch of them | Study 5's only two clean winners were election ladders; news hits them rarely, so a resting cheap-side bid is seldom picked off. 27% of the pool sits here | Watch them for 3 weeks, then re-run study 5's script on these series alone (same measures, same bars): NET-LEAD on ≥ 3 series with ≥ 20 market-days and ≥ 5 events each | Collector snapshots + prints on the nom/race series (collector watch change, paper-only) | **High.** If they pay like study 5's leads (about +$2/market-day), 5–10 bids clear $100/mo alone. Uncorrelated with MMSELL (politics-free) | **PROMOTE after census** |
| 2 | **SPOT-GUARD** (incentive) | On spot-driven ladders we can see a fill coming: pull the cheap-side bid when live spot moves toward its strike | The one family that has ever worked here is "we know fair value before the quote does". Mention fills were unforecastable; these are tied to a price we already record (BTC/ETH spot) | Census first. Then replay S2's rules on the crypto event ladders with and without a spot-distance pull: pass = net ≥ $0.15 per $10 bid-day with lower bound > 0 **and** the pull beats no-pull | Collector snapshots on the event ladders; crypto spot (already in `crypto_ladder_snapshots`); metals/FX spot is not recorded (start with crypto) | **Medium-high, risky.** The largest new pool, but past maker work on informative markets lost to adverse selection. Correlated with the theta/FAVHELD BTC exposure | **PROMOTE after census** (crypto first) |
| 3 | **PAYOUT-TRUTH** (incentive, instrument) | Read what Kalshi actually paid per programme from balance changes, per market type | S2's +$1.86 rests on a 1.34 multiplier measured on mentions and YouTube. If ladders pay 0.8×, S2 is marginal; if 1.8×, it is the main book | Match `incentive_balance_observations` residuals to programme end dates on the pre-11-08 history; pass = the method reproduces ≥ 15 of the 17 screenshot-confirmed payouts within 10% | Balance observations (exist), programme terms (exist), owner screenshots as the check | **Indirect but gating:** it decides whether the S2 live test's numbers can be trusted, and replaces screenshots | **PROMOTE** (read-only now; candidate list item since 11-01) |
| 4 | **STACK** (MMSELL × incentive) | Our resting MMSELL orders may already qualify for incentive rewards; if so, prefer programme markets as a tie-break | Free money with no added risk: same order, same fills, extra reward | One read-only join: MMSELL live/paper resting order hours × programmes on the same market and side, scored with the collector's estimator; report estimated reward per settled MMSELL market | Live and paper order history, programme terms (exist) | **Low** (3-contract clips earn a small queue share), maybe a few $/month, but zero risk and one query | **PROMOTE (one-query study)** |
| 5 | **EVENT-TAIL** (MMSELL) | The MMSELL cheap-tail sell cell (YES 5–10¢) may also pay on the new spot event ladders | Longshot overpricing is MMSELL's whole edge; these ladders list hundreds of far strikes a day | Lookalike probe census on the event series: count first in-cell entries; full run only at ≥ 100 entries | Kalshi candles for settled event markets (public API, the lookalike probe already reads them) | Medium if it passes; but price ladders have been efficient (theta, LEVELHOLD) and the series look new (venue-age gate) | **HOLD** → census only |
| 6 | **NFL-PROPS re-run** (MMSELL) | Existing hold: NFL team props +3.8¢ on 36 entries | Season adds entries weekly | Re-run lookalike §18 bars at ≥ 100 entries | Exists | Low-medium | **HOLD** (trigger: ~100 entries, likely early December) |
| 7 | **XVENUE15 census** (new, cross-venue) | Kalshi vs Polymarket 15-min BTC on the same quarter hour | Reopened 11-06: Polymarket now lists it | Divergence census from public price histories | Public histories | Low: gaps are usually 1–1.6¢ against a 2–4¢ round trip, and the venues settle on different sources | **HOLD** (only if the owner wants a cross-venue direction) |

**Dropped this run** (and why, in one line each): a separate Rotten Tomatoes study (study 5 says
it loses to fills; the 11-24 read is its test); $1-minimum harvest (S4 says the burst nets about
$0); bids above 10¢ (study 9); any new BTC-ladder taker feature (0-for-3 this month); mention
speed trading (closed by owner).

## Top picks

1. **Watch the new money, then test NOM-LADDERS (#1).** The census step is a paper-only collector
   change (add watch pins for the nom/race and crypto event series). It is the cheapest move with
   the largest upside, it serves #1 and #2 at once, and it uses a slot in a line at 5 of 10.
2. **PAYOUT-TRUTH (#3).** Read-only, cheap, and it protects the S2 live test after 11-24.
3. **SPOT-GUARD (#2), crypto ladders only,** once three weeks of snapshots exist.

STACK (#4) is a one-query extra that can ride along with any of these.

## Pre-registration sketch for the picks (fixed only when the owner locks them)

- **#1 NOM-LADDERS.** Census: ≥ 3 nom/race series with ≥ 20 market-days and ≥ 5 events in
  the collector by the read date (about 2026-12-08). Measures and labels: `LIMM_FILL_HAZARD.md`
  §3–§4 unchanged. Kill: no series NET-LEAD, or the family pools below +$0.15/market-day.
- **#2 SPOT-GUARD.** Pull rule fixed before any data: pull when spot is within 1.0 hourly
  standard deviation of our side's strike, re-enter beyond 1.5. Bar as in the table, pairwise
  pull minus no-pull decided only when its 95% interval excludes 0; floor 100 bid-days in 10
  events per arm, else HOLD.
- **#3 PAYOUT-TRUTH.** Pass as in the table; if it fails, screenshots stay the source and the
  per-type calibration waits for the S2 live test's own payouts.

## Holds queue (reconciled 2026-11-14)

| hold | trigger | state |
|---|---|---|
| NOM-LADDERS, SPOT-GUARD (new) | collector watches the series for 3 weeks | not fired (0 watched today) |
| EVENT-TAIL (new) | census ≥ 100 in-cell entries | not run |
| NFL team props | ~100 entries | not fired |
| XVENUE15/5 | owner wants cross-venue | unchanged |
| GRIDPIN | re-run about 2026-12-06 | scheduled elsewhere |
| ECON-REACT, TOKENPIN, HURR (retire if nothing by 11-30), others | unchanged | parked |

## Ops requests made by this run (read-only)

`idea-lims-1114a` (incentive pool by series), `idea-lims-1114b` (timed out, no result),
`idea-lims-1114c` (collector coverage of the new families). `ops/request.json` reset to noop.
