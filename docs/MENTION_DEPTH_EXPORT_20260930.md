# Historical earnings-market order-book export: access investigation

SESSION: Research Lab / MODE: DATA-SOURCE VERIFICATION / ENFORCEMENT: NEW_ONLY / AS OF: 2026-09-30

**Access-blocked, not verified.** A promising free historical snapshot endpoint
was identified, but no earnings-market order-book export was obtained. The
[C0 HOLD](MENTION_CORPUS_CENSUS_20260930.md) remains unchanged. Calvin authorized
pursuing the export; no purchase, account creation, vendor contact, collector,
predictive analysis or live action occurred.

## Recommended route: free historical snapshots

Predexon's [sub-cent order-book documentation](https://docs.predexon.com/api-reference/kalshi/orderbooks-subcent.md)
describes `GET /v2/kalshi/orderbooks-subcent`, history beginning January 8, 2026,
and no usage charge on any plan. Its schema supplies bid/ask levels and
quantities, decimal-cent prices, millisecond timestamps, source and pagination.
Authentication uses `x-api-key`. These are provider claims, not demonstrated
coverage for our earnings tickers.

No `PREDEXON_API_KEY` is configured in this research runtime. Only its presence
was checked; no other credentials were searched or displayed. No authenticated
request ran. The smallest unblocker is a securely configured free-account key,
or an uploaded snapshot export. Do not place keys in chat, documents or commits.
Account setup needs Calvin's direction; adding a card or buying credits is not
necessary for this first test and is not authorized.

[sample-requests.json](research/mention_depth_export_20260930/sample-requests.json)
preserves the same 15 lexically selected contracts and ten-minute pre-decision
windows as the prior census. No replacement based on prices, outcomes or data
availability. Three fixtures test the source, not the full C0 sample floor.

## Paid changes are not the same as starting books

Predexon's [bulk tick documentation](https://docs.predexon.com/data-signals/ticks/kalshi.md)
lists changes from March 5, but bulk starting snapshots only from September 18.
Older changes alone cannot establish July/August books without a trustworthy
earlier state and complete intervening history. Prose says the product is live,
while embedded OpenAPI text still says private preview; access cannot be assumed.
This does not establish a defect in the separate historical-snapshot endpoint.

The [credit documentation](https://docs.predexon.com/data-signals/ticks/data-credits.md)
says bulk charges occur when signed URLs are issued, not when downloaded.
No paid route was called. It is unnecessary for the initial snapshot test.

## Other sources and observed access

| Source | Finding | Verified export? |
|---|---|---|
| [PMXT](https://archive.pmxt.dev/Kalshi) | Indexed listing advertises Kalshi Parquet and CC BY 4.0. Direct root/listing reads returned HTTP 502; web retrieval also failed, including timeout. | No file/schema/earnings coverage verified. Hourly files do not establish hourly observation cadence. |
| [CryptoStruct](https://cryptostruct.com/exchanges/kalshi) | Advertises L2 since February and free series samples. Public catalog page and documented keyless MCP `tools/list` request returned HTTP 403 here. | No; stopped at the restriction, without evasion or alternate credentials. Exact earnings coverage unknown. |
| [Lychee](https://lycheedata.com/kalshi-historical-data) | Browser exports advertised; book history qualified as available where retained. | No earnings sample or dated depth manifest established; no account opened. |
| [Kingsets](https://kingsets.com/) | Market/trade datasets found. | No historical earnings depth verified. |
| [AskSurf](https://agents.asksurf.ai/docs/data-api/prediction-market/kalshi-orderbooks) | Documents historical levels/sizes and roughly five-minute refresh. | No sample or earnings dates verified. Refresh wording alone does not establish observation freshness. |

CryptoStruct's [catalog documentation](https://cryptostruct.com/docs/mcp) describes
free coverage/sample discovery; only tool listing was attempted, not purchase
or account operations. Access/server errors are not zero-coverage findings.
Other limitations in the prior census remain. No vendors were contacted.

## First verification pass, fixed before any export is inspected

Preserve exact tickers/windows, request receipts, response hashes, pagination,
empty results and errors. No target labels or profitability calculations.

1. Establish exact historical identity, not a current book presented as history.
2. Require a usable observation at or before decision time, age 0–60 seconds.
   Verify timestamp meaning and historical availability; snapshot time is not
   automatically exchange-update or receive time. Unresolved provenance stays
   a limitation, not an assumed pass.
3. Normalize decimal cents using decimal arithmetic; inspect level quantities,
   not aggregate depth. Reject invalid/crossed states. Do not round fractional
   quantity upward. Test one-contract availability and five-contract capacity
   separately.
4. Check ordering, duplicate sources and pagination boundaries. Report gaps;
   shared-connection sequence jumps need not be missing per-ticker data. Quiet
   intervals prove neither disconnection nor continuous collection. Delta replay
   requires an earlier starting snapshot and defensible continuity; never seed
   an earlier book with a later snapshot.
5. Compare with saved pre-call candles as a sanity check, not a forced match.
   Sampling differences need inspection, not replacement with a candle or trade.

Displayed historical quantity supports an execution assumption, not a guarantee
of a submitted order filling. All original independent-call, transcript and
performance gates remain unchanged.

## Decision

**Test the free snapshot route before purchasing an export.** Ask Calvin to
enable its account/key path or upload the requested JSON. After access, inspect
the fixed sample; only usable identity, timing and quantity evidence warrants
a broader counts-only 60-call / 10-issuer / two-quarter / 20-date coverage sweep
and complete transcript join. None of those thresholds is declared satisfied.

Completed: source discovery and exact sample request. **Pending: actual export
verification, blocked on access.** Zero promotions or XOS lifecycle actions;
no scorecard denominator, runtime, ops runner or live-money changes.
