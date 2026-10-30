# LIMM programme outcomes — which incentive programmes pay, net of how their cheap side settles

**Status:** PRE-REGISTERED 2026-10-30 (Research Lab), before the probe ran and before any settlement
of this population was read. **Verdict 2026-10-30 (`rl-s3-run1-20261030`): HOLD (instrument).**
- **S1** reads PASS only vacuously: no series outside the live universe was collected in the
  reward window.
- **S2:** the cheap side is not shown overpriced. It settled above its price, on a thin sample.

Catalog research plan, study 3 (`docs/CATALOG_RESEARCH_PLAN.md`, WS-023). Owner chose study 3 over
study 2 on 2026-10-30. Probe: `scripts/limm_programme_outcomes.py` (read-only, ops-allowlisted).
Book: `Alimm1` (WS-020; `docs/LIQUIDITY_INCENTIVE_THESIS.md`). Experiment OS is canonical. The
book's gates are BLOCKED_PLATFORM, so this study is **descriptive**: it ranks, it authorizes
nothing, and any universe change is an owner decision recorded as a new epoch.

## 1. Question

The liquidity-incentive book is the one line paying confirmed real money. Rewards were about
$4–5.5 a day in October against about $3.5 a day of fill losses (§9.69). Its universe is
event-tied mention markets plus `KXYTVIEWSHIGH`, chosen by hand. The question:

> Across every incentive programme the collector watched, which series pay the most reward per
> market-day at the live rule, and how does their cheap side actually settle? The cheap side is
> what a lone fill leaves us holding.

A bid on the cheap side buys a long shot when it fills. If long shots there are overpriced (the
favourite-longshot bias MMSELL sells), every lone fill held to settlement loses on average, and
the reward has to cover that.

## 2. What is already known (not re-tested)

- **Placement runs 1–3** (`LIMM_PLACEMENT_THESIS.md`): cheap side at the reference beats both
  sides; exiting after a fill pays for itself; resting deeper adds nothing at a 0.5 discount
  factor.
- **The placement probe's reward model read 50–250× above realised payouts (L0).** Its error
  comes from near-empty books, where the model credits one bid with most of the pool.
- **The dashboard's per-order estimator matched Kalshi's payouts on 2026-10-16:** $33.1
  projected against $33.43 paid (§9.64). It computes share = our weighted size / the side's
  field score.
- **`limm_scorecard` already reports realised reward against realised fill P&L** on the events
  the book traded. This study adds the events it did **not** trade.

## 3. Data and construction (no lookahead)

- **Snapshots:** `incentive_market_snapshots`, one per market per clock hour (the last valid one
  in the hour), joined to its own programme row (`program_row_id` → `incentive_programs`). The
  population is every market the collector or runner snapshotted, not all of Kalshi.
- **Qualifying hour:** the book is valid; both sides meet Target Size; the programme is active
  (`start ≤ hour < end`); the programme is a liquidity programme; and the hour is at least 48 h
  before `close_time` (a proxy for the live rule's step-aside before an event).
- **Cheap side:** the side whose best bid is between 1¢ and 10¢, the cheaper one if both are
  (P1). **Price = that side's best bid.** At or above the reference this scores the full
  multiplier, and a best-bid price buys fewer contracts than the reference would, so the
  reward is understated, never overstated.
- **Size (live rule §9.69):** $20 when price ≥ 4¢, else $10; `qty = min(500, floor(dollars / price))`.
- **Reward per hour:** `pool_per_hour × share / 2`, with `share = qty / (field + qty)`, where
  `field` is the cheap side's `est_*_score_total` (our order added to the book). This is the
  dashboard estimator.
- **Thin-book exclusion (the L0 fix, fixed now):** an hour where `share > 0.5` is excluded from
  the reward figures and counted separately. That is the regime that inflated the placement
  model.
- **Reward window:** 2026-10-16 → run time, so current pool sizes and the current capital rule
  apply.
- **Reward per market-day:** summed hourly reward over the market-days (distinct market × UTC
  date) with at least one counted hour.
- **Settlement population:** every market with at least one qualifying cheap-side hour since
  collection began (2026-09-17), thin hours included. Per market:
  - the cheap side is the side that was cheap in most of its qualifying hours;
  - `p` is that side's mean best bid over those hours;
  - the outcome is Kalshi's public `result`, fetched batched (yes/no only; anything else is
    excluded and counted).
- **Settlement P&L of a lone fill held to settlement:** `G = 100·[cheap side won] − p`, in cents
  per contract, clustered by event (all words of one speech resolve together). The live book
  exits early (stop and take-profit), so G is the held-to-settlement view: it measures the
  long-shot pricing, not the book's realised fill P&L.
- **Unit:** the series.

## 4. Pre-registered labels (per series)

Floors: **≥ 20 market-days** for a reward read; **≥ 100 settled markets and ≥ 30 events** for a
settlement read. Intervals are 95%, event-clustered.

| label | rule |
|---|---|
| `ADD-CANDIDATE` | reward ≥ $0.50 per market-day AND settlement floor met AND G ≥ −1.0¢ |
| `COSTLY-REWARD` | reward ≥ $0.50 per market-day AND upper bound of G < 0 (pays, but lone fills lose; exit discipline matters) |
| `REWARD-ONLY` | reward ≥ $0.50 per market-day, settlement floor not met |
| `AVOID` | reward < $0.50 per market-day AND upper bound of G < 0 |
| `NO-CALL` | otherwise, at the reward floor |
| `THIN` | under 20 market-days |

The probe also prints:
- whether the series is in the live universe (`MENTION` in the series, or `KXYTVIEWSHIGH`);
- the reward share coming from thin hours;
- `limm_scorecard`'s realised view, run separately and cited.

**Study reading** (descriptive):
- **Prediction S1 (the universe is not the bottleneck):** no series outside the live universe
  is `ADD-CANDIDATE` with a reward per market-day above the live universe's pooled figure.
- **S1 fails** if one or more is. That nominates those series for an owner decision on a new
  epoch, after their own forward read.
- **Prediction S2 (the cheap side is overpriced in the live universe):** pooled live-universe
  G < 0 with the upper bound below 0. Reported either way. It sizes how much each lone fill
  costs if held.

## 5. Known limits

- The trade tape stopped on 2026-10-07, so fill frequency per series is not measured here. The
  ranking puts reward next to settlement pricing; it is not a full net.
- The population is what the collector snapshotted.
- The 48 h-before-close proxy is not the event time for every series.
- The best-bid price understates reward.

## 6. Cost

Read-only: one windowed SQL pass over the snapshots, then batched Kalshi results for the
settlement population. No orders, no writes.

## 7. Results

Ops run `rl-s3-run1-20261030` (default-branch code `ef0ef89a`); `rl-s3-run1b-20261030` is the
same run with every series printed.

**Coverage, the binding fact.**
- **Snapshots:** 90,088 hourly snapshots and 42,726 qualifying cheap-side hours, on **811
  markets** across **26 series**. Kalshi lists about 171k liquidity-programme markets.
- **The out-of-universe shadow collector stopped on 2026-10-07.** It ran on the evo service
  (`liquidity_incentive_shadow_enabled`, top 150 programmes by reward), and that service was
  deleted. Since then only the live runner snapshots, and only the markets it fetches, which
  are mostly the mention universe.
- **So in the reward window (from 10-16) only the four live-universe series have
  market-days.** The 22 other series appear only before 10-07, each with 1–11 settled markets.
- **Settlement:** 95 of 811 markets had settled. 716 are still open, mostly weeks-out
  programmes.

**Live universe (reward window 10-16 → 10-30, thin hours: 0%).**

| series | market-days | reward $/md | settled | events | G ¢/ct (held to settlement) | label |
|---|---|---|---|---|---|---|
| KXTRUMPMENTIONB | 26 | $0.87 | 8 | 2 | +18.5 ± 5.5 | REWARD-ONLY |
| KXTRUMPMENTION | 67 | $0.42 | 32 | 11 | +12.8 ± 6.9 | NO-CALL |
| KXYTVIEWSHIGH | 42 | $0.29 | 0 | 0 | n/a | NO-CALL |
| KXMAMDANIMENTION | 11 | $0.21 | 0 | 0 | n/a | THIN |
| **pooled** | **146** | **$0.45** | **40** | **13** | **+13.9 ± 5.6** | |

- **The estimator agrees with realised payouts.** $0.45–0.87 per market-day is consistent with the
  book's realised ~$4–5.5 a day over 4–10 quoted markets (§9.69). No thin-book hours occurred
  in the live universe, so the regime that broke the placement model's L0 does not arise here.
- **S1 — PASS, vacuous.** No out-of-universe series reached the 20-market-day floor, because none
  was collected after 10-07. This is not evidence that the universe is right.
- **S2 — the cheap side is not overpriced; it settled above its price.** +13.9¢ per contract over 40
  markets in 13 events: mention long shots won more often than priced. That is thin (13 events)
  and descriptive. It runs against the book's stop at half price, which sells exactly these
  contracts, but it does not test that stop.

**Reading.**
- Study 3 cannot rank the opportunity population from current data. The pre-registered answer
  is **HOLD (instrument)**.
- **What would answer it** (an owner decision, not taken here):
  - (a) Turn the shadow collector back on for one worker (`LIQUIDITY_INCENTIVE_SHADOW_ENABLED`,
    paper only, book-event tape still off). It costs Postgres growth from snapshots of about
    150 markets.
  - (b) Pre-register a v2 on the 2026-09-17 → 10-07 shadow window. Its reward side is unread; its
    settlement side was partly printed here, so it would be read as reward-only.
