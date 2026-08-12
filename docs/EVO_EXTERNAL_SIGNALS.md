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

#### The obvious fix is wrong — do not re-bin by uniform allocation

The tempting move is to treat Polymarket's ladder as a distribution over the same temperatures and
split its buckets by overlap:

```
P(Kalshi 99-100) ~= 0.5 * P(Poly 98-99) + 0.5 * P(Poly 100-101)
```

**That was shipped, measured against production, and reverted.** Against the real Austin ladder it
produced:

| bucket | uniform estimate | Kalshi mid | "divergence" |
|---|---|---|---|
| AUS 99-100 | 38.2¢ | 6¢ | **+32.2¢** |
| AUS 101-102 | 47.2¢ | 84¢ | **−36.8¢** |
| MIA 91-92 | 48.8¢ | 0.5¢ | **+48.3¢** |
| MIA 93-94 | 49.2¢ | 98¢ | **−48.8¢** |

All four are artifacts. Polymarket put 72.5% on AUS `100-101` while Kalshi put 84% on `101-102` —
so essentially all of that 72.5% is P(101), and halving it fabricates a 37¢ disagreement between
two venues that agree. Weather distributions are extremely peaked: one or two degrees hold nearly
all the mass, so splitting a bucket is not a small approximation. And it fails in the direction
that makes an agent *trade*.

#### What it does instead: bounds, and silence when they are wide

```
lower = mass of Polymarket buckets wholly INSIDE the Kalshi bucket
upper = lower + mass of every bucket that merely OVERLAPS it
```

A partially-overlapping bucket could contribute all of its mass or none of it — that range is the
honest answer, not a coin flip. If the ambiguous mass exceeds `MAX_REBIN_UNCERTAINTY` (4¢, chosen
to stay meaningfully tighter than the `>= 5` gates agents write) the ladder does not pin the bucket
down and the result is **None**. When it is narrow — the common quiet-tail case — the midpoint is
returned, accurate to within half the band.

On the same production data this keeps every honest value and suppresses every artifact: the four
exact LAX matches (including a genuine **+7.0¢** and **−6.0¢**), plus small bounded tail values
like AUS `105-106` at +0.1¢, while AUS `99-100`, `101-102`, `103-104` and MIA `91-92`, `93-94` all
return nothing.

Three further cases fail closed: a bucket the ladder does not cover, a bucket that would have to
split an **open-ended tail** (`107+`, `<=98` — unbounded support cannot be bounded), and an exact
match is never estimated at all.

**Net effect on coverage: small.** Because the grids are offset by exactly one degree everywhere
observed, most interleaved buckets straddle two Polymarket buckets with none wholly contained, so
the band is wide and the answer is None. `pm_divergence` remains close to an exact-match-only
metric, and it is bounded by Polymarket's city list besides — CHI, DEN, NYC, PHIL and un-posted
dates yield nothing at all. That is the true state of this signal, not a bug to route around.

Every cycle logs `evo pm_divergence coverage: kalshi_buckets=.. pm_buckets=.. matched_exact=..
matched_rebinned=.. unmatched=..` in the message body (not `extra_fields`, which does not survive
the ops log fetch), so inertness stays visible instead of silent.

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
