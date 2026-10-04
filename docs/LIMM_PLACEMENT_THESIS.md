# LIMM-PLACEMENT — where the liquidity-incentive book should rest its bids

**Status:** run 1 (ops `limmplace-20261004-1`, 3 days, 400 markets): **HOLD on every policy**, L0 flagged the
reward model OPTIMISTIC. See Result below. Probe:
`scripts/limm_placement_probe.py`. Book: `Alimm1` (WS-020, thesis
[§9.41–§9.47](LIQUIDITY_INCENTIVE_THESIS.md)). Operator request (Calvin, 2026-10-04): "create a
probe and test all three of these ideas"; operator leaning: Idea 1 + Idea 3.

## One-liner

The book has earned $2.21 in rewards because its 1–2¢ bids usually sit far below Kalshi's
reference price, where each cent below costs 10% of the score. Resting AT the reference price, on
the cheap side only (P1) or on both sides sized to a $10 lone-fill loss (P2), with or without
re-pricing every cycle and pulling on activity spikes (the "F" add-on), should earn far more
reward per market-day without a matching rise in lone-fill losses.

## Mechanism

- **Who pays:** Kalshi's liquidity-incentive pool, split by score share. Scoring rules R1–R6 in
  `kalshi_bot/liquidity_incentive/scoring.py`: an order at or above the side's reference price
  (where cumulative resting size from the best bid reaches Target/5) scores 1.0 per contract;
  k cents below scores `discount^k` (0.9 on these programmes).
- **Who is on the other side of a fill:** a taker selling into our bid. The cost is adverse
  selection: a fill tends to arrive just before the price moves against us. Resting nearer the
  touch raises reward share and fill rate together, so the question is the NET.
- **Why it might persist:** most weeks-out incentive markets carry thin, wide books; the
  reference price there is cheap on at least one side.

## Policies simulated (all at most one market position at a time per market)

| code | rule | requote |
|---|---|---|
| B0 | today: both sides one tick behind the best bid, both legs ≤ 10¢ | every 4 h |
| P1 | cheap side only: the side whose reference price ≤ 10¢ (cheaper if both), at the reference | every 4 h |
| P2 | both sides at their reference prices, yes + no ≤ 99¢ | every 4 h |
| P1F | P1, re-priced to the reference every snapshot, pulled while a spike/close rule is on | every snapshot |
| P2F | P2, likewise | every snapshot |

Sizing: `qty = min(500, floor($10 / dearer leg price))`, the live book's caps. Common gates:
both sides meet Target Size in the snapshot (else nobody is paid), programme still running,
market open. Pull rule (F only): `trades_last_5m ≥ 3` OR `price_range_5m ≥ 3¢` OR the market closes
within 48 h.

## Measurement (no lookahead)

Data: `incentive_market_snapshots` (book levels, field score, activity, every ~6 min per market)
and `incentive_trade_events` (public tape), live-collected by the shadow collector; programme
terms from `incentive_programs`. Every decision at snapshot `t` uses only the snapshot at `t`.
Reward accrues over `[t, t_next)` (capped at 15 min) at
`pool_per_second × our_side_share / 2` per resting side, where
`share = q·mult / (field_score + q·mult)`. Fills replay the tape under two models, reported side by
side: **optimistic** (a print on our side at or through our price fills us) and **conservative**
(a print at our price fills only after the depth ahead of us at placement has traded; a print
through our price fills us). After a fill the policy places nothing new on that market; an unfilled leg of the same pair keeps
resting unchanged, as the live book leaves it. Both legs filled lock `100 − yes − no`; a lone leg is
marked to its side's best bid at fill + 24 h (or the last snapshot, labelled). Fees: maker
`ceil(0.0175 · q · P · (1−P) · 100)` cents per filled leg.

Unit of comparison: **net $ per market-day quoted** (reward + fill P&L − fees, divided by the
market-days with at least one resting leg), plus market-days available. The live book holds two
markets, so ≈ 2 × net/market-day is its daily figure.

## Pre-registered predictions and decision rule

- **L0 (model sanity):** B0's simulated reward per market-day is reported beside the live book's
  realised rewards ($2.21 over 2026-09-17 → 10-04). If B0 sim exceeds 5× the realised rate per
  market-day, every reward figure is flagged OPTIMISTIC and no policy can PROMOTE on this run.
- **L1 (reward):** P1 and P2 earn at least 3× B0's reward per market-day.
- **L2 (net, the decision):** a policy PROMOTEs when its **conservative** net per market-day is
  ≥ $0.50 AND its optimistic net is > 0 AND it has ≥ 20 market-days quoted AND no single market
  supplies more than 50% of its net.
- **KILL** a policy when its conservative net per market-day ≤ 0.
- Otherwise **HOLD**.
- **Choice:** among PROMOTEs, the highest conservative net per market-day wins; P1F wins a tie
  within 20% (lowest loss per contract).
- **F add-on:** the F variant is kept only if its conservative net per market-day beats its base
  policy's.

Nothing here is re-scoped after the run. A KILL on every policy is a clean ruling-out of
"placement is the bottleneck" and is logged as a win.

## Cost / capacity / correlation

Capacity is two markets × at most $10 lone-fill loss each, unchanged. Honest ceiling: two slots at
$0.50–$5 per market-day ≈ $30–$300/month in reward, before the measured fill losses. No new
correlation: the same book, same markets; only where the bid rests changes.

## Graveyard check

Passive-on-informative without an adverse-selection model is a killed family. This probe carries
that model explicitly (two fill models, a 24 h mark) and decides on the conservative one.

## Result — run 1 (2026-10-04, ops `limmplace-20261004-1`)

3 days, 400 markets, conservative fill model (optimistic in brackets):

| policy | market-days | reward/md | net/md | fills | verdict |
|---|---|---|---|---|---|
| B0 | 5.8 | $9.65 | $9.62 [$1.47] | 1 [6] | — |
| P1 | 212.7 | $5.08 | **$4.48** [$2.94] | 34 [53] | HOLD |
| P2 | 466.3 | $0.64 | $0.57 [$0.30] | 69 [164] | HOLD |
| P1F | 194.3 | $0.39 | $0.41 [$0.47] | 4 [20] | HOLD |
| P2F | 441.5 | $0.23 | $0.21 [$0.17] | 10 [103] | HOLD |

- **L0 failed:** the live book realised about $0.19 per market-day (69 pairs × 4 h against the $2.21
  ledger), and B0 simulated $9.65, 50× higher. Per the pre-registration every reward figure is
  flagged OPTIMISTIC and nothing promotes. Kalshi pays after a programme ends, so most of the live
  book's programmes have not paid yet; the realised side is a floor, not a settled number.
- **L1 failed** for P1 and P2 against B0: B0 is available only on the rare empty books where both
  sides are ≤ 10¢ (5.8 market-days in 3 days), and there the model credits it with most of the pool.
- **L2:** every policy is net positive under both fill models, so none is KILLED. Held only by L0.
- **F add-on:** neither F variant beats its base. Pulling and re-pricing cost more reward than the
  fills they avoided.
- 186 fills had less than 24 h of book after them and were marked at the last snapshot.

Reading inside the run (relative, same model): P1 earns about 8× P2 per market-day and has 37× B0's
availability; the F add-on lowers net. The absolute level is not usable until the reward model is
calibrated against real payouts. The November payouts (programmes the live book quoted) are that
calibration: re-run with the ledger's realised reward then, unchanged rules.

## Protections — run 2 pre-registration (2026-10-04, before run 2)

Operator decision after run 1: go with P1 (cheap side at the reference), and find what keeps a
lone fill from costing the full $10. Three loss controls are layered on P1 and replayed with the
same data window, fill models and marks:

| code | protection |
|---|---|
| P1A | **Size to the pool:** at most $3 at risk (`qty ≤ 300 / price`) and no more contracts than the side's field score (min 50). |
| P1B | **Exit after a fill:** an offer back at entry is taken once the side's best bid is back at entry (scratch, maker fee); a stop sells into the bid once it is at or below half the entry (taker fee). Unresolved legs are marked at +24 h as before. |
| P1C | **Real long shots only:** reference ≤ 5¢, and stand aside while the market is active (`trades_last_5m > 0` or `price_range_5m > 0`). The scheduled-event filter is not testable on this data; activity stands in for it. |
| P1ABC | all three. |

**Decision rule (conservative fill model, relative to P1 in the same run):** a protection PASSES
when it cuts P1's summed fill losses by at least 40% AND keeps at least 70% of P1's net per
market-day. Among passes the highest net per market-day is chosen. If none passes, P1 runs
unprotected. L0 still applies: absolute dollars stay flagged while the reward model reads far
above realised rewards, so this run ranks protections; it does not certify their dollar value.

## Result — run 2 (2026-10-04, ops `limmplace-20261004-2`)

Same 3-day window, 400 markets, conservative fill model:

| policy | net/md | fill losses | worst fill | loss cut vs P1 | net kept | verdict |
|---|---|---|---|---|---|---|
| P1 | $4.61 | −$239.65 | −$10.00 | — | — | reference |
| P1A size to pool | $2.25 | −$85.28 | −$3.00 | 64% | 49% | fail (net) |
| **P1B exit after fill** | **$4.77** | −$89.94 | −$8.30 | **62%** | **103%** | **PASS — chosen** |
| P1C long shots only | $8.68 | −$195.64 | −$10.00 | 18% | 188% | fail (loss) |
| P1ABC all three | $3.71 | −$29.45 | −$3.00 | 88% | 80% | PASS |

- By the pre-registered rule the protection is **P1B**: it cuts fill losses 62% at no cost to net.
- P1ABC also passes and has the smallest worst case (−$3 a fill, 88% of losses gone) for 20% less net.
- P1C's activity filter raised net (fewer quoted hours, better ones) without cutting losses: its
  fills are as large as P1's because the price cap, not the filter, sets the size.
- L0 still holds (B0 simulated $10.15/md vs ~$0.19 realised): the ranking is the result, not the
  dollar level. 321 fills had under 24 h of book after them and were marked at the last snapshot.
