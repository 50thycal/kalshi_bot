# mmsell QUOTE PARITY — can the entry scan pre-filter on the event page's inline quote?

Built 2026-08-11. **Measurement only** — no trading decision changes. Pre-registered gate below.

## The constraint this is trying to remove

The mmsell entry scan fetches one `GET /markets/{ticker}/orderbook` per market that clears the
volume + hours-to-close gates. Measured over 30 cycles, 2026-08-10 → 08-11:

| | measured |
|---|---|
| event pages fetched | 44–48 of a 100 cap → **already paging the entire Kalshi universe** |
| `events_seen` | **150, every single cycle** — the top-N cut binds hard |
| eligible events | ~1,890 → roughly **1,740 tradeable events go unscanned per cycle** |
| markets considered | 1,300–2,660 |
| orderbook calls | 1,216–2,570 upper bound; ~650–900 confirmed floor |
| scan wall clock | ~50–160s; cycle-start gaps of 31–35 min against a 30-min interval |

Implied burst rate: **~6–25 requests/sec.** Kalshi's token bucket (since 2026-04-23) gives the
Basic tier 200 tokens/sec at 10 tokens/request — **20 reads/sec**. We straddle it. Our tier is
unresolved: `GET /account/limits` is authoritative but needs auth, and neither the sandbox nor
the ops runner holds Kalshi credentials — only the Railway worker does.

So the orderbook call is what caps the scan at 150 events, and the scan cap is what leaves
~1,740 eligible events unscanned. Removing the call removes both.

## The lever

`GET /events?with_nested_markets=true` — the one call the scan **already makes** to enumerate
the universe — returns a top-of-book quote on every nested market. Verified live 2026-08-11 via
`kalshi_market_survey`:

```
sample: {'ticker': 'KXELONMARS-99', 'vol': 116844, 'oi': 39945, 'bid$': '0.1000', 'ask$': '0.1100'}
```

The mmsell band gate needs only two numbers: the **midpoint** (band) and the **yes-ask** (the
`maxyes` entry-price ceiling, since the cheap YES leg costs `100 − no_bid` = the yes-ask). Both
are in that payload. The orderbook is genuinely needed only for depth and final confirmation —
i.e. for the ~220–450 markets per cycle that are actually in band.

If the inline quote can be trusted, orderbook calls drop 3–7×, and **a wider (A/B union) scan
becomes cheaper than today's narrow one.**

> Read `yes_bid`/`yes_ask` through `scanner.metrics.market_price_cents`, never
> `market.get("yes_bid")`. The live API sends `yes_bid_dollars` as a **string** and omits the
> integer-cent key entirely, so the naive read is silently always-`None` — that is what kept
> `mmsellA5` at zero trades.

## Why this is an experiment and not an assumption

A pre-filter that wrongly **rejects** a market never fetches its orderbook, so that candidate
stops existing — silently, for **every** book including the live one. This is the exact
contamination the A/B scan was designed to avoid, arriving through a side door.

Two ways the quotes can legitimately differ:

1. **Staleness.** The events endpoint may serve a denormalized or cached quote on its own
   refresh schedule; the orderbook is fetched live, seconds later.
2. **Derivation.** Our `best_yes_ask` is computed as `100 − best_no_bid` off the orderbook's NO
   side. Kalshi's inline `yes_ask` is reported, not derived — it need not agree on a one-sided
   book, and `_dollars` strings can round differently into cents.

## How it is measured

`MmSellTracker.run_once` scores the inline quote against **the orderbook it already fetched**,
for every market it fetches one for. Zero extra API calls, no trading effect, fail-soft. Totals
go to `system_events` as `component='mmsell_quote_parity'` every cycle
(`kalshi_bot/mmsell/quote_parity.py`; read it back with `mmsell_quote_parity`).

Two kinds of output:

- **Agreement** — bucketed `|inline − orderbook|` on bid, ask and midpoint. Diagnostic only.
- **The decision table** — the part that decides. For each band and each margin *m*, how many
  orderbooks a pre-filter of *band widened by m* would fetch, and how many real in-band
  candidates it would **miss**.

Two fixed probe bands, **deliberately hard-coded rather than read from live book config** — the
experiment accumulates over days and a book retuned mid-run would silently redefine its own
result:

| probe band | mirrors | lo | hi | maxyes |
|---|---|---|---|---|
| `wide` | the control book `mmsell` | 5 | 40 | — |
| `tight` | `mmsell10`, **the live candidate** | 5 | 10 | 7 |

**Read `tight` first.** It is the band a bad pre-filter would cost real money on.

### Stated limitation

The population is markets the scan **already fetches** (volume + htc gates passed). We have no
orderbook ground truth for anything else, so this validates a pre-filter applied at that point
in the funnel and nothing wider. In particular it cannot, by construction, tell us whether the
inline quote is equally reliable on the low-volume markets the scan currently skips.

## Pre-registered gate

Decided at **n ≥ 50,000 markets scored across ≥ 100 cycles** (both, not either — one quiet hour
is a regime, not a sample):

**PASS**, per band, when all three hold:
1. `miss_no_quote` ≤ **0.1%** of in-band markets — these are unrecoverable at *any* margin, so
   they are a hard floor on the pre-filter's error rate;
2. the smallest margin that misses nothing recoverable is ≤ **3¢** — a wider margin is not a
   pre-filter, it is the band again; and
3. at that margin the pre-filter fetches **< 75%** of today's orderbook calls — otherwise the
   saving does not justify the risk.

**FAIL** on any of the three. A fail means the union scan must fetch orderbooks over the wider
universe, and the Kalshi rate limit becomes the binding constraint on the whole A/B design.

A per-band split verdict is a legitimate outcome and probably the likely one: the `wide` band
(5–40¢) admits most of the board, so a pre-filter can barely shrink it, while `tight` (≤7¢ ask)
should shrink hard. Since `tight` is what runs live, a `tight`-only PASS is still actionable.

## Rate-limit visibility (shipped alongside)

`KalshiClient` now counts retryable responses **by HTTP status** and the scan telemetry
snapshots that counter each cycle, so 429s are queryable. The status is also in the log
*message* text, not only in `extra_fields`.

Both changes exist because a `429` (we are over our tier — actionable) and a `502` (Kalshi
hiccup — noise) hit the same retry branch, logged the same `"kalshi transient response"` line,
and Railway's log endpoint returns the message while dropping structured fields. The difference
existed nowhere queryable. Measured 2026-08-11: **5 transient responses in ~34 minutes** — and
we could not tell which kind they were. Now we can:

```jsonc
{"type":"logs","filter":"transient response 429"}     // greppable
{"type":"script","name":"mmsell_quote_parity"}        // RATE LIMITS section, per status
```

The counters are cumulative since process start, so consecutive telemetry rows subtract to a
per-cycle rate; the reader sums per-restart segments rather than differencing endpoints, because
a restart resets them.

**If 429s are non-zero, the pre-filter stops being an optimization and becomes the fix** — every
429 costs a 2s backoff mid-scan, and on final failure the tracker `break`s out of that market,
dropping its candidates silently rather than erroring.

## The in-play carve-out, and why it ships OFF (2026-08-13)

The rule: trust the inline quote for `scheduled`/`discrete`, always fetch the orderbook for
`in_play`. Decidable from the series alone, so it costs nothing to apply.

**The evidence for it weakened as the sample grew, and that is recorded here rather than
quietly dropped.** At 50 sampled outliers, six live in-play sports-prop series carried **74%**
of them and the story looked clean. At **405** samples the same top six carry **32%**, and two
of them — `KXRAIN` and `KXNATGASD` — are `scheduled`. The worst individual disagreements now
include `KXSOLD` and `KXBTC`, scheduled crypto strikes, at 53–62¢ on the ask. Tennis
(`KXITFMATCH`+`KXITFWMATCH`) is genuinely ~11× concentrated, but the 74% figure was a
small-sample artifact.

The mechanism still looks like **latency × volatility** — the event page is one snapshot and
the scan takes 1–3 minutes to reach a given market. Settle mode is just a poor proxy for it:
an hourly crypto strike near its boundary moves as fast as a live game.

So both halves ship, and neither is armed:

* **The shadow measurement.** Every market is now scored into a SECOND decision table
  restricted to known-non-in-play markets (`bands_ex_inplay`). No fetch is skipped. This is the
  direct test — if the carve-out is the right mechanism the miss rate collapses; if it barely
  moves, the rule buys nothing. `mmsell quote parity` prints it beside the blended numbers.
  An **unclassified** series scores into the blended table only: counting "we do not know what
  this is" as safe would flatter the rule exactly where we know least.
* **The pre-filter itself**, behind `MMSELL_PREFILTER_ENABLED` (default **off**), with
  `MMSELL_PREFILTER_TRUST_IN_PLAY` (default **off**, i.e. always fetch in-play).

### Why this one cannot be A/B'd like `scanmax`

The orderbook fetch is **shared**: one call serves every book that reaches the market. A skipped
fetch removes that candidate from every paper book **and both live arms** at once. Unlike the
per-book scan depth, there is no isolated form of it in production — which is exactly why the
shadow table exists, and why the filter tests the **union** of every interested book's band
rather than any one book's. Skipping on the tight book's opinion would silently starve the wide
one.

Everything else in that filter is a refusal to skip: a missing or half-missing inline quote
never skips (no data is not evidence of being out of band), an unclassified series never skips,
and a market whose cheap ask is under some interested book's `maxyes` never skips even when its
midpoint sits far above the band.

## What happens after the verdict

- **PASS (tight, or both)** → build the pre-filter with the measured margin, then the A/B union
  scan on top of the freed budget. The A/B still needs its own selector-tag design so existing
  books' candidate streams stay byte-identical.
- **FAIL** → the top-150 cap stays, and widening the scan means either a higher Kalshi tier or
  the tiered-rotation design (Tier 1 always-scan short-dated, Tier 2 rotating).
