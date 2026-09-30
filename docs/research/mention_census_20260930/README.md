# Earnings-mention coverage evidence, September 30, 2026

Counts-only supporting evidence for [the census](../../MENTION_CORPUS_CENSUS_20260930.md).
This is a research attachment, not an ops module, collector or trading probe.

Run `python docs/research/mention_census_20260930/audit.py` from the repository
root. It recomputes the reported counts, checks candle times/units, rejects
retained target-label fields, checks archive metadata and verifies file hashes.
`summary.json` is the saved output. No network or third-party dependencies.

## Evidence files

- `series.json`: 177 exact `KXEARNINGSMENTION` prefix matches from the public
  `/series` response (14,494 total series). Selected source/title fields only.
- `markets.jsonl.gz`: all 6,586 unique market tickers returned by the two market
  routes, including unsettled listings. Sorted, gzip mtime zero. Whitelist
  excludes target results, final/current prices and settlement values. Rules,
  timestamps and status are retained; status is used only to count finalized
  listings. Duplicate source tiers are explicit. Stable lexical tier ordering
  chooses the live representation on overlap; no overlapping status conflicts
  were observed. Event IDs and series IDs are not yet independent call IDs.
- `requests.json`: each series/tier request, row count, cursor truncation/error
  status, exact URL, retrieval UTC time and raw-response SHA-256. Raw-response
  hashes intentionally differ from hashes of the masked projections. They
  establish request provenance; unmasked label-bearing responses are not retained.
- `cutoff.json`: observed historical partition cutoff plus receipt.
- `quote-audit.json`: fixed three-event, first-five-tickers-per-event audit,
  contract rules and pre-decision candles, with request receipts. The field
  `eligible_quote_rows` means period-end alignment only: it is not a statement
  of executable quantity or a verified exchange quote-update timestamp.
- `msft-archive-audit.json`: eight prior-quarter Wayback CDX records, selected
  capture timestamps, response hashes, title/Q&A checks and HEAD readbacks.
  Hashes cover raw downloaded bodies, which sometimes were gzip encoded.
  Full third-party transcripts are not republished. The content checks support
  transcript presence, not a completed speaker/rule parsing audit.
- `alternative-sources.json`: bounded source-access screen, including the
  inspected public order-book dataset's immutable revision and file-date census.
- `sha256.json`: hashes of the retained data files (not of this explanatory text).

## Acquisition recipe and bounds

Use the public base `https://api.elections.kalshi.com/trade-api/v2`, JSON accept
header and a browser user agent. Read `/series`, filter exact prefix, then call
both `/markets?series_ticker={ticker}&limit=1000` and
`/historical/markets?series_ticker={ticker}&limit=1000` for every matched series.
Consume cursors; maximum ten pages per series/tier, six concurrent requests,
35-second timeouts and at most three attempts. All 354 queries completed in one
page. Union on market ticker; never sum the tiers without deduplicating.
Read `/historical/cutoff`; do not infer history absence from the live route.
This was a dated retrieval, not an atomic exchange snapshot; reruns can differ.

Quote fixtures were chosen for verified official scheduled call times, before
inspecting their quotes: Microsoft and Meta July 29, NVIDIA August 26, 2026.
Within each event choose the first five market tickers lexicographically,
irrespective of price/result. Query a one-minute candle series over the ten
minutes ending exactly one hour before the call. July fixtures used
`/historical/markets/{ticker}/candlesticks`; August used
`/series/{series}/markets/{ticker}/candlesticks`. The receipt URLs retain exact
epoch arguments. Future refreshes must recheck the archive cutoff; do not
permanently route by those calendar dates. Historical `yes_bid/yes_ask.close`
and live `close_dollars` were decimal dollars in these responses. Never use
trade `price.close`, extrema, midpoints, volume or open interest as a substitute.

For Microsoft, query Wayback CDX for each of FY24 Q4 through FY26 Q3 official
earnings-page URLs with `filter=statuscode:200`, `collapse=digest`, fields
`timestamp,original,digest`, and `to=20260728235959`. Select the latest returned
capture, retrieve its `id_` body, decompress gzip when indicated by magic bytes,
and inspect title, substantive transcript text and Q&A. Follow redirects and
verify the final capture timestamp / `Memento-Datetime` precedes July 29's
20:30 UTC decision. Some original paths use different letter case; retain the
CDX original URL. Direct current Microsoft IR requests returned 403 in this
runtime; the publicly accessible archive succeeded. No access controls were
bypassed. Three initial archive timeouts were retried successfully.

The bounds stop before extracting candidate word frequencies, target labels,
returns or fitted parameters. Insufficient size evidence ends this census at
HOLD; there is no automatic next-stage analysis.
