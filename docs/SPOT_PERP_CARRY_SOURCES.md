# SPOT-PERP-CARRY — contract and price-source verification

Research Lab, 2026-09-13. Continuation of WS-018, not a new experiment.
Scope: public documentation, bounded unauthenticated access checks and conditional
arithmetic. No orders, account reads, credentials, runtime changes or XOS promotion.

## Resolved definitions

The official [asset specifications](https://help.kalshi.com/en/articles/15357566-what-perpetuals-are-available-on-kalshi)
identify KXBTCPERP as 0.0001 BTC per contract and KXETHPERP as 0.001 ETH per contract;
orders use whole contracts. For N short contracts, hold N times that asset quantity
in spot, rounded to an executable spot increment without silently retaining a material hedge mismatch.
Do not multiply the per-contract mark by the asset multiplier again when valuing contracts.

[Funding definitions](https://help.kalshi.com/en/articles/15357613-how-funding-works)
confirm positive rates pay shorts and negative rates charge shorts, three times daily
at 00:00, 08:00 and 16:00 Eastern. The frozen August 14–September 13 window is daylight
saving time; 04:00/12:00/20:00 UTC are the matching timestamps. Do not hard-code this UTC
schedule for winter. The 90 rows per asset cover all scheduled events in that window.

The [API estimate schema](https://docs.kalshi.com/margin-rest/funding/get-funding-rate-estimate)
calls funding_rate a double but does not explicitly state decimal fraction versus percent.
The documented 0.01% zero threshold and the observed approximately 0.0001 smallest
nonzero magnitudes support decimal fractions as an inference, not explicit schema proof.
Fee-rate decimal documentation is not evidence for funding-rate units.
An authoritative API conversion statement or a reconciled known payment would settle it.
No new trade should be made merely to produce that payment.

## Price-source map

| Purpose | Documented request | Interpretation / limitation |
| --- | --- | --- |
| Current perp depth | GET https://external-api.kalshi.com/trade-api/v2/margin/markets/{ticker}/orderbook?depth=20 | bids and asks in dollars per contract with quantities; consume depth, not just best price |
| Historical perp quotes | GET same base /markets/{ticker}/candlesticks?start_ts=...&end_ts=...&period_interval=1 | bid/ask OHLC present in schema; no executable depth guarantee |
| Current spot depth | GET https://api.exchange.coinbase.com/products/{BTC-USD,ETH-USD}/book?level=2 | prices per coin and base-asset quantities; snapshot, not a fill |
| Historical spot prices | GET same Coinbase base /products/{product}/candles?start=...&end=...&granularity=60 | trade OHLCV, not historical bid/ask; at most 300 candles per request |

Sources: [Kalshi depth](https://docs.kalshi.com/margin-rest/market/get-market-orderbook),
[Kalshi candles](https://docs.kalshi.com/margin-rest/market/get-market-candlesticks),
[Coinbase depth](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-book),
[Coinbase candles](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles).
These REST pages show unauthenticated examples. Actual access was not established here.
Kalshi WebSocket connections, unlike these REST examples, require authentication.

Coinbase candle time is the bucket START; Kalshi end_period_ts is the bucket END.
Normalize before joining. Coinbase omits intervals without trades and may return earlier
rows; filter, sort and detect gaps. Never replace a missing executable quote with a last trade.
A historical replay with assumed spot spreads can be useful, but must be labeled modeled
execution, not measured fills. A prospective paired depth capture can test costs at size;
it cannot retrospectively supply missing August quotes.

## Access checks performed

On this continuation, bounded curl GETs (15-second timeout, no credentials or retries):
Kalshi KXBTCPERP orderbook depth=1 returned HTTP403; Coinbase BTC-USD book level=1 timed
out with no response. Web retrieval could not open the Coinbase BTC/ETH book URLs either.
No price snapshots or new historical price rows were obtained. No alternate host,
disguised user agent or extracted credentials were used to evade the refusal.
The earlier approved runner's successful funding census remains valid evidence; it did
not fetch these orderbooks. Permission to extend collection through an authorized transport
must be resolved before trying to route around the current access restriction.

## Conditional capital screen — not a return backtest

Keep the original 40% spot / 40% perp collateral / 20% reserve allocation and equal BTC/ETH
split. ASSUME, without upgrading C2, raw funding numbers are decimal fractions and each
asset maintains constant dollar notional. Gross funding coefficient is then
0.012270507428775305 for BTC and -0.0012384200729308001 for ETH over the frozen 30 days.
Provenance: [original census](SPOT_PERP_CARRY_CENSUS.md), request spot-carry-20260913-1,
result blob 82a14c2f50f01924417f53afaf97118468e41bf6. Gross = 0.2 * total_cash * (BTC_sum + ETH_sum).

| Total cash | Gross conditional funding | After low fee assumption | After middle fee assumption |
| --- | --- | --- | --- |
| $1,000 | $2.21 | $1.25 | -$1.95 |
| $2,000 primary | $4.41 | $2.49 | -$3.91 |
| $4,000 ceiling | $8.83 | $4.99 | -$7.81 |

Low spot/perp fees = 10/2 bps per leg; middle = 40/12 bps per leg, as in the original
hurdle scenarios, NOT verified account tiers. One opening and closing per leg at flat
prices gives fees = 0.4 * cash * 2 * (spot_bps + perp_bps) / 10000.
The original extra 50 bps combined basis/slippage stress subtracts $4 at $2,000 cash,
making the low-fee screen -$1.51. This is sensitivity arithmetic, not realized net P&L.

Constant dollar notional is not the intended fixed-unit hedge when prices move: maintaining
it requires rebalancing, whose trades/costs are not modeled here. This table also excludes
the actual spot/perp basis path, margin/liquidation path, lot rounding, missed fills,
transfers, tax and interest. It cannot prove a strategy profitable or unprofitable and
cannot be annualized into an expected return. Both assets remain in the frozen test;
choosing BTC only after observing these results would require a separately frozen test.

## Disposition and next action

Contract sizes and documented price-source selection are resolved. Overall HOLD remains:
API rate conversion and actual paired price acquisition are not verified. Exact account
fees are not a blocker to continued sensitivity work, only to an account-specific result.
No credentials or capital are requested. Obtain explicit permission for a bounded public
quote/candle check through the existing approved runner after this workspace's refusal;
do not change the ops runner or start a recurring collector as part of documentation work.
Any runner allowlist change retains the repository's named merge hard stop.

Verification: arithmetic recomputed from original 180 rows; source links inspected;
git diff --check. No runtime code changed and no independent review claimed.
