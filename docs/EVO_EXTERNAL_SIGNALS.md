# Evo external signals — the DSL metrics that are not the order book

**Question this answers:** the fleet has run 900+ backtests, saved 78 strategies, and every
active one is net negative. Is that a tuning problem or a structural one?

Structural. Until now every metric the strategy DSL offered —
`yes_bid · yes_ask · no_bid · no_ask · spread · mid · last_price · volume · open_interest ·
hours_to_close` — was a property of **Kalshi's own order book**. So every hypothesis an agent
could *state* was a price pattern: "buy when cheap", "buy when the spread is tight", "buy near
close". On a roughly efficient book those earn the spread minus two fees, which is exactly what
the control arm measures (`market_cheap` / `market_expensive`, see `evo/controls.py`). The
agents were not searching badly; they were searching a space with almost nothing in it.

## The two metrics

| metric | definition | sign convention | applies to |
|---|---|---|---|
| `pm_divergence` | Polymarket's implied probability **minus** our mid, in cents, for the same weather bucket (re-binned across the two venues' grids — see below) | **positive => our YES is cheap** vs the other venue | weather (`KXHIGH*`/`KXLOW*`, only cities Polymarket posts: LAX/MIA/AUS) |
| `spot_vs_strike` | percent distance from BTC/ETH spot to the market's decision boundary | **positive => YES is currently winning**, for every `strike_type` | crypto (`KXBTC*`/`KXETH*`) |

Neither requires our forecast model to be any good, which is why they came first. `pm_divergence`
asks only *"do two venues disagree about the same event?"* — no prediction, just a comparison.
`spot_vs_strike` is arithmetic on a published price.

`spot_vs_strike` uses one sign convention across strike types deliberately (`greater`:
`(spot−floor)/floor`; `less`: `(cap−spot)/cap`; `between`: distance to the nearer edge, negative
outside the band). Without that, an agent needs three different rules to express one idea.

**`pm_divergence` is a signal, not an arbitrage.** We trade only Kalshi, so a disagreement is
information about mispricing, not a two-sided trade we can capture.

### The two venues do not share a bucket grid

Measured in production the day after this shipped: over a 30-minute window, **60** Kalshi weather
buckets had a fresh mid and **33** Polymarket buckets had a fresh price, and an exact
`(city, kind, target_date, low_f, high_f)` join matched **4** — all of them LAX. The metric was not
broken, it was inert.

The cause is not staleness. Each venue picks its own 2°F grid, and the grids interleave:

```
AUS  Kalshi  <=98  99-100  101-102  103-104  105-106  107+
     Poly    <=91  92-93   94-95    96-97    98-99    100-101 ...
```

Kalshi's boundaries fall at 98.5 / 100.5 / 102.5; Polymarket's at 97.5 / 99.5 / 101.5. Disjoint
sets — so no Austin bucket can *ever* equal a Polymarket bucket. Same in Miami. LAX matched only
because Kalshi happened to start that ladder on an even degree.

So we stop requiring the grids to agree. Polymarket's ladder is a distribution over the same
temperatures; a Kalshi bucket is a range of them. `pm_probability_for_bucket` re-bins one onto the
other by overlap:

```
P(Kalshi 99-100) = 0.5 * P(Poly 98-99) + 0.5 * P(Poly 100-101)
```

**Read the estimate with its assumption.** Overlap allocation assumes mass is spread *uniformly
inside* each Polymarket bucket. That is wrong in the third decimal — a peaked distribution puts
more mass on the side nearer the mode — so a re-binned divergence carries roughly 1–3¢ of binning
error, which matters if you gate at `>= 5`. An exact match, when one exists, is still used verbatim
rather than estimated.

Two cases fail closed rather than estimate, both being ways re-binning could invent information:

- **the ladder does not fully cover the bucket** — a half-covered bucket would report about half
  its true probability, i.e. a large fake *negative* divergence, which is the direction that makes
  an agent sell;
- **the bucket would have to split an open-ended tail** (`107+`, `<=98`) — unbounded support has no
  uniform to allocate over.

Coverage after the fix is bounded by Polymarket's own city list, not by the grid: cities it does
not cover (CHI, DEN, NYC, PHIL) and dates it has not posted still yield no signal at all. Every
cycle logs `evo pm_divergence coverage` with `kalshi_buckets` / `pm_buckets` / `matched_exact` /
`matched_rebinned`, so inertness is visible instead of silent.

### It cannot be backtested

No dataset can replay `pm_divergence` — `sandbox.DATASET_SIGNALS` maps `backfill_weather` and
`mmsell` to the empty set, and a backtest whose spec uses it is **rejected** rather than run
silently. So an agent can gate a live strategy on it but cannot produce backtest evidence for it,
which cuts against the standing "cite evidence" steering. Closing that needs a replay dataset
joining `polymarket_snapshots` + `weather_bucket_snapshots` with settlement from
`weather_settlements`. Until then `pm_divergence` is a live-only, forward-tested metric, and
`spot_vs_strike` (replayable on the `crypto` dataset) is the one an agent can actually validate.

## Why the bots read a database and not the APIs

The evo worker calls **no external API**. The main worker (`BOT_MODE=live`) already collects NWS,
Open-Meteo, Coinbase and Polymarket into provenance-labeled tables; `evo/signals.py` reads those.

That is a correctness requirement, not a shortcut. A value fetched inside a heartbeat could never
be reproduced in a backtest, so **no strategy using it could ever be validated** — the same trap
as assuming a resting maker order always fills (`docs/MMSELL_FILL_MODEL.md`). Reading a collected
table means the live path and the replay path see the same number by construction, which is the
invariant the whole DSL rests on (`strategy_spec.py`: one interpreter, live and sandbox).

Nor is it stale. Measured lag when this shipped:

| feed | lag | upstream republish cadence |
|---|---|---|
| `weather_forecasts` (NWS) | 2 min | ~hourly |
| `weather_bucket_snapshots` | 2 min | continuous |
| `polymarket_snapshots` | 2 min | continuous (capture throttled) |
| `crypto_spot_candles` (Coinbase) | 6 min | continuous |
| `weather_ensembles` | 10 min | ~6-hourly |

The database copy refreshes **faster than most of the sources change**. Hitting the APIs directly
would gain nothing and cost replayability.

## Fail closed

Collectors die quietly — the metric simply stops appearing, which is indistinguishable from "no
edge today" unless something checks. So:

- Every signal carries the age of its **oldest input**; past `EVO_SIGNAL_MAX_AGE_MINUTES`
  (default 30) the value is dropped to `None`.
- A `None` metric **fails its condition** (`strategy_spec._metric_value`), so a dead feed blocks
  entries instead of authorizing trades on a number nobody refreshed.
- **Both legs** of a difference must be fresh. A current Polymarket price against an hour-old
  Kalshi mid is a fabricated divergence, not a signal.
- The per-cycle signal map is **replaced, never merged**, so last cycle's value cannot linger.

## No silent dead specs

`DATASET_SIGNALS` (`evo/sandbox.py`) declares which signals each backtest dataset can actually
reconstruct. A spec using a metric its dataset cannot compute is **rejected with an explanation**,
not run.

Without that guard, an agent gates on `pm_divergence`, replays over `backfill_weather` (a Kalshi
REST archive with no Polymarket join), gets zero trades, and concludes the signal is worthless —
when the truth is it was never evaluated. Same class of lie as the maker-fill assumption.

| dataset | replays |
|---|---|
| `crypto` | `spot_vs_strike` |
| `backfill_weather` | — |
| `mmsell` | — |

**`pm_divergence` has no backtest dataset yet.** It can be traded and measured forward in paper,
but not replayed historically. Building one means joining `polymarket_snapshots` to
`weather_bucket_snapshots` with settlement from `weather_settlements` — the obvious follow-up, and
until it exists the honest status of any `pm_divergence` strategy is "forward-tested only".

## Requesting a source we do not have

`register_data_source` used to accept any name and return `ok` while changing nothing: no fetcher,
no rows, no path to use it. It taught agents that naming a feed obtained one.

It now checks whether anything actually collects the named source and, if not, **files an
`external_data_pipeline` capability ticket** for the operator, returning `collected: false` plus a
note saying not to build a strategy on it. The tempting action and the effective action are the
same action.

The promotion pipeline is therefore explicit:

> agent proposes (ticket) → operator builds the collector into the **main** worker → it lands in
> Postgres with provenance → it becomes an `inspect_data` source, a DSL metric, and a backtest
> dataset

## What to watch

The point of this change is not that these two metrics are profitable — that is untested. It is
that the fleet can now *state* a hypothesis of a different kind. The signal to watch is whether
any saved strategy uses a non-book metric at all, and then whether those strategies beat the
control arm (`participation_cents`), which price-pattern strategies do not.
