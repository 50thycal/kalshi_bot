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

## What the large disagreements actually are (probed 2026-08-13)

The gate failed on the safe margin: to miss nothing it needed 71¢ (tight) / 48.5¢ (wide) against
a 3¢ cap, because ~0.9% of quotes disagree by more than a few cents and one was 90¢ off. Those
outliers set an irreducible ~1% miss floor that no margin removes, so the whole decision turns on
what they are. `scripts/kalshi_quote_probe.py` fetches the event page and each market's
orderbook back-to-back and compares them directly.

**The definitional hypothesis is disproven.** It looked like Kalshi's inline `yes_ask` might be a
literal resting YES offer while ours is derived as `100 − no_bid`. It is not: on every market
where both sides carry depth, `inline yes_ask == 100 − best_no_bid` **exactly**, and the inline
`no_bid` matches the book's best NO level. Our derivation is right, and the inline sides are not
independently useful — that possible cheap fix is closed.

Two real drivers, only one of which matters:

1. **Empty book sides.** Where a side has no levels, Kalshi reports `0` (bid) or `100` (ask)
   while we report `None`. This produced most of the raw mismatches in the probe — and it is
   **already excluded** from the parity measurement, which drops markets with no two-sided
   orderbook into `ob_not_two_sided` before scoring. Not a source of the outliers.
2. **Genuine movement between the two calls.** The event page is ONE snapshot covering every
   market; each orderbook is fetched separately, seconds to minutes later, and the scan takes
   1–3 minutes end to end. With both sides present the two quotes agree exactly or within 1–3¢
   — except where the market is moving fast.

**Which is why the outliers concentrate where they do.** Six series carry 74% of them on ~2% of
candidate flow (17–120× concentration): `KXWNBAPTS`, `KXWTASETWINNER`, `KXLEAGUESCUPSCORE`,
`KXWNBA1HTOTAL`, `KXMLBTEAMTOTAL`, `KXWNBASPREAD` — all **live in-play sports props**, the
fastest-moving thing on the board. Volume does not predict them (38% sit in the ≥10k bucket) and
neither does time-to-close, whose "1–3 days" reading is itself the known in-play artifact:
`close_time` is a far-future fallback on live sports (measured elsewhere in this repo,
`KXUFCFIGHT` reporting ~335h to close on a fight that resolved in 0.4h).

So the disagreement is **the scan's own latency**, not a bad feed — and it is predictable from
the market's settle mode rather than from anything needing an orderbook.

**The blocker is now cleared (2026-08-13).** The taxonomy was extended over every series with
real flow, and doing so turned up something larger than the pre-filter question:

> **Half of all candidate flow — 49 of 80 series, 49.5% of ticks — was `unclassified`.**
> An unclassified series is admitted by no `mtype=`/`mode=` allowlist, so every type book ever
> run had been selecting from roughly **half** the universe, and nothing surfaced that. The
> `Wmmsell*`/`Tmmsell*` results in `docs/MMSELL_TYPE_BOOKS.md` were measured under that
> constraint. It does not invalidate them — each book was still compared against a control
> seeing the same universe — but "type X has no edge" was only ever a statement about the
> classified half.

Coverage is now **99.6% of flow** (46 series added, each classified from its own live subtitle
rather than guessed from the ticker), and **63.5% of flow is identifiable as `in_play`** — which
is what makes a settle-mode distrust rule expressible at all.

The in-play exclusion is therefore buildable: a pre-filter can trust the inline quote for
`scheduled`/`discrete` markets and fetch the orderbook anyway for `in_play` ones, using only the
series taxonomy, with no orderbook needed to make the decision. The expected effect is that the
~1.4% blended miss rate drops toward the non-in-play rate; that remains to be measured rather
than assumed, and the parity telemetry already collects what is needed to measure it.

Confidence: the definitional finding is solid (exact agreement, multiple markets). The in-play
mechanism is a strong hypothesis from n=30 probed markets plus 50 sampled outliers, consistent
with every axis measured, but not yet independently confirmed.

## What happens after the verdict

- **PASS (tight, or both)** → build the pre-filter with the measured margin, then the A/B union
  scan on top of the freed budget. The A/B still needs its own selector-tag design so existing
  books' candidate streams stay byte-identical.
- **FAIL** → the top-150 cap stays, and widening the scan means either a higher Kalshi tier or
  the tiered-rotation design (Tier 1 always-scan short-dated, Tier 2 rotating).
