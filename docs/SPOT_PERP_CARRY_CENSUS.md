# SPOT-PERP-CARRY — funding availability and total-capital census

**Status:** pre-registered 2026-09-13; 12 local tests pass; direct API run HOLD (HTTP403), approved-runner execution pending.
**Request:** Calvin selected this previously parked idea and requested testing with
$2,000 preferred total capital, $1,000 comparison and $4,000 ceiling. Research only.
**Workstream:** [WS-018](workstreams/WS-018-spot-perp-funding-census.md).

## Mechanism and novelty

Long BTC spot plus an equal-unit short BTC perp; same construction for ETH. Funding
received by the short must exceed all costs and losses in the spot/perp basis. Equal
units hedge the common price component; they do not guarantee funding income or protect
the short's separate margin account. This differs from PERP-V1's cross-asset carry arm,
which retains relative asset-price risk, and from PASSIVE-PERP's outright reversion.
Neither historical strategy is reopened. No registered contract or shared metric changes.

## Data-source correction

The existing `KalshiClient.get_perp_funding_history` and old collector query
`/margin/funding_history`: authenticated **account payments**, not market-rate history.
An account that never held the perp may have no payment history. Its empty response
does not prove market funding rates do not exist. The current official documents name:

- [Market history](https://docs.kalshi.com/margin-rest/funding/get-historical-funding-rates):
  `https://external-api.kalshi.com/trade-api/v2/margin/funding_rates/historical`,
  ticker and Unix-second start/end parameters; `funding_rates` entries with market ticker,
  funding time, numeric funding rate and mark price.
- [Current estimate](https://docs.kalshi.com/margin-rest/funding/get-funding-rate-estimate):
  `/margin/funding_rates/estimate?ticker=...`. The estimate changes through its period
  and finalizes at the next funding time. It cannot select historical entries.
- [Account fees](https://docs.kalshi.com/margin-rest/fees/get-fee-tiers): authenticated
  maker/taker rates per market as decimal fractions. Never assume this also establishes
  funding-rate units. [Coinbase Advanced fees](https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/advanced-trade-fees)
  depend on account tier; the exact spot fee has not been retrieved.

Documentation verified 2026-09-13. Our preceding direct public request returned HTTP403;
no funding observations were retrieved. A docs page is evidence of a documented surface,
not evidence that the research runner can read it. No access-denial retry or credential
workaround is part of this census.

## Frozen first-stage protocol

`scripts/spot_perp_funding_census.py` is stdlib-only and read-only, with no credentials,
orders, database or environment changes. Fixed BTC/ETH tickers, fixed history window
`[2026-08-14T00:00:00Z, 2026-09-13T00:00:00Z)` (30 days), five <=7-day chunks per
asset; maximum 12 GETs including current estimates. Fifteen-second timeout per request,
2 MB response limit, no retries. An unsuccessful history request stops that asset's
history fetches. No follow-up requests to guessed hosts or account routes.

Preserve signed raw rates and marks, timestamp-normalize to UTC, reject unexpected
tickers, nonfinite values, nonpositive marks, missing fields and naive timestamps.
Reject duplicate payment times and unexpected pagination. Chunk ownership is half-open:
an entry exactly at the right boundary belongs to the next chunk. Record response and
normalized dataset hashes, explicit errors, counts, observed dates, gaps and sign counts.
The current estimate is separately labelled context, never mixed into settled rates.

**C1 floor:** no historical errors, >=30 payments and >=14 UTC dates in each asset.
This is a minimum to inspect units/cadence, not proof of complete coverage or statistical
power. Print C1 false on thin/empty/inaccessible data; do not interpret missing as zero.
**C2:** authoritative confirmation of rate units, payer sign, assessment timing and complete
payment coverage is still required. The API's generic numeric field is insufficient.
**C3:** synchronized executable spot/perp quotes and actual account fees are required.
Until C1–C3 are established, **HOLD only**, realized net P&L null; no annualized rate or
funding-profit claim. No profitability PASS, paper book or trade tag can emerge from stage 1.

## Capital contract and offline test

Fixed modeling allocation, not a user-authorized deployment: **40% spot / 40% perp
collateral / 20% reserve**. BTC and ETH split the spot and collateral buckets equally.
All capital, including idle reserves, remains in the return denominator.

| Total cash | Combined spot | Combined perp collateral | Reserve | Each asset's spot |
|---|---:|---:|---:|---:|
| $1,000 | $400 | $400 | $200 | $200 |
| $2,000 primary | $800 | $800 | $400 | $400 |
| $4,000 maximum | $1,600 | $1,600 | $800 | $800 |

The short quantity must match spot units using actual contract multipliers and lot sizes.
One-to-one notional collateral here is a conservative modeling choice, not a claim about
exchange margin requirements or guaranteed survival of a price rally.

Offline sensitivities assume one entry and exit over a month, flat prices for fee
arithmetic only, spot/perp per-leg fee pairs `(10,2)`, `(40,12)`, `(60,12)` bps, plus
50 bps of combined basis/slippage stress on hedge notional. These are **hypothetical
inputs**, neither measured costs nor downside limits. Both sides of both positions pay.
Reserve cash earns zero in this screen; taxes and infrastructure expenses excluded.

At $2,000, the favorable fee scenario costs $1.92 plus $4 stress, so funding would need
to supply **$105.92 per modeled month on $800 notional (13.24%)** to leave $100. The
middle scenario needs $112.32 (14.04%). These are hurdles, **not observed yields**.
No leverage increase or collateral reduction may be used to make a negative result pass.

## Conditional full capital test (after testability is established)

Pre-register an equal-weight always-hedged baseline before inspecting profitability;
any funding-filtered candidate must use information available at entry, not the final
funding payment. Use actual units and the complete payment ledger:

`P&L = q*(spot_exit - spot_entry) + q*(perp_entry - perp_exit) + signed funding cash - all costs`.

Charge fees on each leg's own notional, include bid/ask and missed/partial legs, track
perp equity and maintenance margin through the full path, and price forced exits. Spot
gains at a different venue are not automatically available collateral. Report funding,
basis, fees, slippage, peak cash requirement, drawdown and dollars per total capital
separately. Do not call terminal profit feasible if the path would have liquidated.
One 30-day sample is descriptive; it cannot establish a sustainable annual return.

## Run and continuation

Offline: `python scripts/spot_perp_funding_census.py --capital-only`.
After the new allowlist addition is approved and merged:

```json
{"type":"script","name":"spot_perp_funding_census","args":[],"id":"spot-carry-20260913-1"}
```

Read the matching result path, reconcile C1 and schema against the sources, and log the
verdict here, in the journal and scorecard. Continue the same workstream through unit
verification and the conditional capital test, or name the exact data blocker. Do not
create adjacent tickets. No live-money authorization was requested or granted.

## Local verification — 2026-09-13

All 12 targeted tests pass; ruff and compilation pass. The offline capital-only mode
executed successfully, including the $105.92 and $112.32 primary-budget funding hurdles.
The frozen public-data mode also executed: BTC and ETH history each returned HTTP403,
zero payments retrieved, `history_request_failed`, HOLD, realized net P&L null. This is
an access result, not a carry backtest or a negative economic verdict. The new module's
allowlist addition is prepared for explicit ops-runner merge approval; it has not run
against production through ops yet.
