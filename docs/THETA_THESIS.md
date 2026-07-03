# THETA — model-anchored tail-selling on Kalshi's recurring hourly crypto ladders

*Claude's own book. Thesis written 2026-07-03, before any validation ran; the falsifiable
predictions below are pre-registered so the validation can't be quietly re-scoped after the
fact. Status: **validated 2026-07-03** (P1 FAIL / P2 PASS / P3 PASS — see Results) →
data collection + paper book built the same day → forward-testing → live gate.*

## RESULTS (2026-07-03 probe run — `kalshi_theta_study`, defaults)

Sample: KXBTCD 1,663 / KXBTC 682 / KXETHD 762 / KXETH 267 settled markets (vol ≥ 200),
2.9–7.0 days of history; 87,539 tape trades across 720 markets; 12 days of 1-min spot.

- **P1 — FAIL.** Selling *every* 3–40¢ tail at the posted quote is ~0 EV (T-30 gap +0.7¢,
  sellEV −0.15¢, n=494; per-series mixed/noisy). The quotes are calibrated on average —
  there is no naive band edge at rest. (Side-finding parked for later: hourly favorites at
  65–90¢ ran *under*priced, win% ≈ +9–11¢ over mid, small n.)
- **P2 — PASS.** Realized maker-SELL flow on these series: **+5.21¢/contract net of
  worst-case fees** (21,552 trades / 2.09M contracts), split-half **+5.11 / +5.32**.
  Sliced by time-to-expiry the edge lives **inside the final hour** (10–20¢: +9 to +11¢;
  20–35¢: +1 to +18¢ under 60m) and is *negative* beyond 60m (−7 to −22¢). Mirror
  maker-BUY: −9.26¢, as the FLB structure predicts.
- **P3 — PASS.** At the same quotes, the spot-model split separates cleanly in 3–40¢:
  model-OVERpriced (mid − 100·P ≥ 5¢): **sellEV +4.44¢** (n=114, win 18.4% vs ~22 implied);
  model-fair: **−1.52¢** (n=380). Strongest cell: 10–20¢ overpriced → +13.2¢ (n=39, win
  2.6% vs 15.4 implied) — consistent with quote-staleness after spot moves, the same
  information-lag family as the weather `obs` edge.
- **P4 — mixed but acceptable.** P2 halves agree near-perfectly; P1 per-series/halves are
  noise around zero (expected once P1 is understood as "no unconditional edge").

**Decision (per the pre-registered criteria):** build the paper book **with the model
filter as a required gate** (P1's failure means the filter is load-bearing, not optional)
plus a **final-hour entry window** (P2's tte structure). Honest caveats: P3's n=114 spans
only ~3–7 days in one vol regime — the paper book *is* the out-of-sample test, at ~100s of
trades/week; and paper assumes our resting ask is the one that fills (same limitation as
mmsell, partially derisked by P2 measuring realized passive fills).

## One-liner

Sell overpriced lottery tickets to retail gamblers on Kalshi's hourly BTC/ETH price ladders
— resting offers on far-from-spot strikes whose price materially exceeds what a live
realized-volatility model says they're worth — small size, both tails, dozens of independent
expiries a day, always held the <1h to settlement.

## Why this, specifically

Everything this repo has learned in ~1,500 paper trades and nine structural probes points the
same direction:

1. **Taker strategies are dead on Kalshi.** Weather directional, momentum/reversion TA,
   cross-venue divergence, no-arb scans, favorite-longshot as a taker — every one loses the
   spread+fee round trip (−5¢/trade cost floor; portfolio currently −$225 over 4,585 settled
   weather paper trades). The market is efficient on anything derivable from its own prices.
2. **The one confirmed +EV structure is the maker-SELL side of cheap contracts.** The
   `kalshi_mm` trade-tape backtest (70,861 trades / 349 markets / 68M contracts): selling YES
   at 5–35¢ as the resting side, held to settlement, nets **+5..+22¢/contract after
   worst-case fees**, survives split-half OOS (+0.187/+0.185) and holds off-sports. The
   favorite-longshot bias is real on Kalshi; its premium accrues to whoever *sells* the
   longshot.
3. **Hold-to-settlement is the proven exit everywhere** (two independent exit sweeps: stops
   are poison, TP just donates mean). A strategy whose positions expire on their own within
   an hour sidesteps the whole exit problem — and recycles capital 24×/day.
4. **Kalshi's crypto recurrings are a retail lottery venue at industrial scale.** Crypto runs
   ~$20M/day (~7% of Kalshi volume); ~86% of that flows through the 15-minute BTC up/down
   coin-flips. The **hourly ladders** (`KXBTCD` "$X or above" thresholds, `KXBTC` $500-wide
   range buckets, ETH twins) settle every hour around the clock against the CF Benchmarks
   BRRNY 60s average, with 30k–460k contracts per market and ~100+ strikes per event — deep
   tails permanently on offer to lottery buyers.
5. **Selling short-dated crypto optionality is structurally paid.** A far-OTM hourly strike is
   a short-dated digital option; BTC carries a persistent variance risk premium
   (implied > realized), and options literature + our own tape data agree the tails are where
   the overpricing concentrates.

The 15-minute up/down markets themselves are **not** the venue: they trade at ~50¢ (the
maximum-fee zone), have no tail structure, and reward reaction speed we don't have (300s
cycles). The hourly ladders are the venue: the edge is in the *tails*, tails don't need
millisecond reaction, and fees near 10–20¢ prices are tiny (maker fee = 25% of taker,
≈0.2–0.4¢).

## Where my edge comes from (and why it isn't arbitraged away)

- **Behavioral supply of overpriced tails**: lottery-ticket demand ("BTC to $63k this hour!?")
  and momentum-chasing after spot moves. The buyers are not price-sensitive at 8¢ vs 5¢ — the
  ticket is cheap either way. This is the FLB premium the tape backtest measured.
- **The premium is payment for unhedgeable tail risk held to settlement.** A market maker who
  sells the tail can't cheaply hedge a binary that gaps on a 1% hourly BTC move; they charge
  for it. Harvesting it requires *warehousing* that risk across many small, independent,
  uncorrelated-in-time expiries — exactly what a tiny automated book can do and a
  balance-sheet-constrained MM desk prices up.
- **A fair-value anchor most flow lacks**: a live spot feed + realized-vol model tells us the
  *actual* P(strike hit in remaining minutes). We only sell when price ≥ model + costs +
  margin — the filter that should cut the adverse-selection tail (never sell a "longshot"
  that the last 5 minutes of spot movement has made live).
- **What we deliberately do NOT compete on**: speed to the mid, near-money quoting, intra-window
  scalping. Our 300s loop only needs to catch each hourly window once, early, in the tail.

## The strategy, concretely

- **Universe**: hourly `KXBTCD` (threshold) + `KXBTC` (range) + ETH equivalents; later the
  daily versions and index (`KXINX*`) equivalents if the edge generalizes.
- **Model**: distribution of the remaining-window move built from live 1-min spot candles
  (Coinbase Exchange API, free) — EWMA realized vol + empirical tail quantiles of
  minutes-scale returns (fat tails respected, no Gaussian assumption at the tail), horizon =
  minutes to settlement. `P_model(YES)` per strike/bucket.
- **Entry** (per hourly event, evaluated each cycle from T-50 to T-10 min): sell YES — booked
  as buy NO at the no-bid, the same maker-price assumption `mmsell` uses — on strikes where
  `market_yes_price − P_model ≥ edge_min` **and** `yes_price` in the 3–40¢ band **and**
  two-sided book **and** per-event/per-hour concentration caps allow. Both tails qualify
  (an overpriced "≥ $63k" and an overpriced "< $61k" are the same trade).
- **Exit**: none. Hold to settlement, always. Positions live <1h.
- **Risk**: qty small (paper: 1–5), max N positions per event and per hour, spread across
  strikes/assets/hours; scheduled-macro-release hours (CPI, FOMC) skippable by config; the
  existing daily-loss breaker + kill switch apply unchanged on any live path.
- **Fees**: modeled worst-case (maker fee rounded up to 1¢/contract) exactly like `kalshi_mm`.

### Sizing the prize (why this can reach $100/month)

~24 hourly events/day × 2 ladder types × 2 assets ≈ 96 events/day; if only ~⅓ offer one
sellable tail clearing the threshold, that's ~30 trades/day — **~900/month, a real sample in
weeks, not quarters** (weather produces ~7 events/day). At the tape-measured +5..+22¢/contract
on 5-35¢ sells: 30 trades/day × 5 contracts × +3¢ (conservative) ≈ **$4.5/day ≈ $135/month**,
deploying ≲$50 at a time (NO at 60–95¢, recycled hourly). Even the pessimistic half of that
clears the goal; size scales afterward if live fills confirm.

## Pre-registered, falsifiable predictions (the validation gate)

The probe (`scripts/kalshi_theta_study.py`, ops-runnable, read-only public API) tests on
settled hourly markets — thousands settle per month, so no waiting for sample:

- **P1 — Tail overpricing (calibration)**: on settled hourly BTC/ETH ladder markets, contracts
  priced 3–35¢ at T-30min settle YES at least ~2¢ less often than their price implies,
  pooled and per asset.
- **P2 — Maker-sell tape EV**: replaying real trade tapes on these series (kalshi_mm method),
  maker-SELL in the 3–40¢ band is positive net of worst-case fees (`net_ceil > 0`), overall
  and split-half OOS.
- **P3 — Model filter adds power**: at the same market price, the subset the spot-vol model
  flags as overpriced settles YES materially less often than the subset it calls fair — i.e.
  the model separates dead tails from live ones better than price alone.
- **P4 — Robustness**: P1/P2 hold on both halves of the date range, on BTC and ETH
  separately, and don't invert on high-vol days.

**Kill criteria (pre-committed)**: if P1 and P2 both fail → the venue is efficient; write it
up in `docs/RESEARCH_JOURNAL.md` and abandon — no strategy build. If P1/P2 pass but P3 fails
→ build the book WITHOUT the model filter (pure band rule, an mmsell specialization) and keep
the model as a research feature. Magnitudes get re-checked against the paper book before any
live dollar.

## Build plan (phased, mirrors how every validated book got built here)

1. **Probe** (no new infra): `kalshi_theta_study.py` via the ops channel → P1–P4 verdicts.
2. **Data collection** (only if validated): ride-along collectors in the worker —
   1-min spot candles (Coinbase) into `crypto_spot_candles`; hourly-ladder snapshots into
   `crypto_ladder_snapshots` (reuses the weather_bucket_snapshots pattern); settlement capture.
   All fail-soft, throttled, provenance-separated — same discipline as the weather tables.
3. **Paper book** (`theta` strategy tag): a tracker in `kalshi_bot/theta/` riding the existing
   cycle like `mmsell` does (shared paper engine settles/marks it); entries per the rule above;
   forward P&L lands in the standard `paper_trades` rollups next to every other book.
4. **Live gate**: only after the paper book's realized P&L confirms the backtest at real
   sample size — through the existing `LIVE_STRATEGIES` allowlist, dollar caps, daily-loss
   breaker. Entries are plain YES/NO buys (proven live); no closes needed (settlement exit).

## Honest limitations

- Paper (like mmsell) assumes our resting offer is the one that fills — queue realism is only
  provable with a small live test. The tape backtest partially derisks this: it measures the
  *realized* passive side of actual trades, informed flow included.
- The model is only as good as the spot feed's proximity to BRRNY (both aggregate the same
  major exchanges; basis is cents, not dollars — but P3 measures the model as-used, so any
  real basis cost shows up in the validation, not in production surprises).
- A structural regime change (pro MMs tightening the hourly tails, a fee change, Kalshi
  delisting the ladders) kills the edge; the paper book's rolling P&L is the tripwire, same
  as every other book here.
