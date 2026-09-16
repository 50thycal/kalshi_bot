# MMSELL queue / fill telemetry — Phase 1, instrumentation only

**Built 2026-09-16.** Workstream `WS-019`. Read-only measurement around every live MMSELL
resting order. **No entry, cancel, price, size, universe or exposure rule changes.** The
collector cannot place, amend or cancel anything: it holds a client wrapper that exposes only
`GET` methods, and a test enforces that.

Experiment OS stays canonical for every experiment standing; this document restates none.
The live book under observation and its twin are whatever `LIVE_STRATEGIES` /
`LIVE_PAPER_TWIN_SUFFIX` name in production — read them there, not here.

## 0. Why this exists — the question, stated once

Paper assumes the resting maker order fills. Live often does not. `docs/OPS_FMMSELL10_PARITY_DIAGNOSIS.md`
showed that most of the recent paper-vs-live gap was universe composition, and that on the
markets live actually filled, real money slightly beat its own paper model. What remains
unexplained is **fill selection**: which resting orders fill, which do not, and whether the
fills that do land are the ones we wanted.

Two separate questions, answered by two separate models (`docs/MMSELL_FILL_MODEL.md` already
names adverse selection as the mechanism; this is the instrument that lets it be measured
rather than calibrated from price alone):

| model | question | needs |
|---|---|---|
| **A — fill probability** | P(fill within T · queue state, market state) | queue position over time, book, trades |
| **B — fill quality** | E[P&L · fill, state] — was the fill adversely selected? | exact fill time + the price path after it |

`ExpectedOrderValue ≈ P(fill) × E[P&L | fill]`. Neither model is fitted here. Phase 1 makes
every live MMSELL order one **traceable observation** so Phase 2 can fit them from raw data.

## 1. Repository architecture assessment

What the code actually is, measured 2026-09-16 (file:line refer to the head this PR branched from):

- **One synchronous worker loop**, `main.run()`, cycle = `scan_interval_seconds` (300 s). No
  asyncio, no threads, no WebSocket usage anywhere in `kalshi_bot/` — every Kalshi read is REST
  polling. The livedash is a separate read-only Railway service on stdlib `http.server`.
- **The live MMSELL path**: `MmSellTracker.run_once` computes candidates from `GET /events` +
  `GET /markets/{t}/orderbook`, opens the paper trade, then calls
  `LiveExecutor.mirror_mmsell_entry`, which runs nine gates, prices the maker order, persists a
  `pending` `live_orders` row, commits, POSTs `POST /portfolio/events/orders` (post-only, GTC,
  `side=ask` = sell YES = buy NO), and marks the row `resting`.
- **`LiveExecutor.reconcile`** (once per cycle): `GET /portfolio/orders`, `/fills`,
  `/positions` unfiltered; refreshes order status; inserts fills idempotently on `trade_id`;
  cancels orders older than 4 h (`live_order_timeout_seconds`); **samples queue position once
  per cycle** into `live_order_queue_ticks` via `GET /portfolio/orders/queue_positions`
  (batch, filtered to resting tickers) with a bounded per-order fallback; evaluates the retired
  queue-cancel rule (`LIVE_QUEUE_CANCEL_MODE=off`); drains stood-down books.
- **Fills lose their exchange timestamp.** `repository.insert_fill` sets
  `filled_at = filled_at or now()` and `reconcile` always passes `None`, so every
  `fills.filled_at` is the reconcile time (up to 5 min late). The true `created_time` survives
  only inside `raw_fill_json`. Not changed here (shared semantic → Platform Change Review);
  the new fill stream carries the exchange millisecond timestamp instead.
- **Twin linkage** is by convention, not key: `live_paper_twins(live_tag, twin_tag, started_at)`
  + `paper_trades.strategy == twin_tag` + `market_ticker` + window. `live_paper_parity_events`
  records, per candidate per cycle, what parent/twin/live each did (`placed`, `gate:*`,
  `skip_live_tier`, `not_attempted`, …).
- **Candidate context exists at decision time** but is only partially persisted:
  `mmsell_candidate_ticks` (control tag only, one orderbook row per in-band candidate per
  cycle) and the `risk_events` audit row (`hot_entry`, `ab_arm`). `lo/hi/maxyes/htc/hte/
  series/regime/tier/open count` are in scope inside the tracker loop and are not written.
- **Kalshi is sharded**; reads aggregate, writes are routed. Queue reads are unaffected.
- **Account tier**: ADVANCED (300 read / 300 write tokens per second, 10 tokens per default
  request), recorded at boot in `system_events(component='kalshi_limits')`.

## 2. Current-data inventory — what we already collect

| table | writer | cadence | what it holds | limitation for this work |
|---|---|---|---|---|
| `live_orders` | executor | per event | identity, `limit_price` (NO cents), `quantity`, status, `cancel_reason`, raw order/response JSON, arm id | no ack/cancel timestamps, no decision context |
| `fills` | `reconcile` | 5 min poll | one row per REST fill, `fee`, raw JSON | `filled_at` is reconcile time, not exchange time |
| `live_order_queue_ticks` | `sample_queue_positions` | once per 5-min cycle | `queue_position`, `contracts_ahead` (= `queue_position_fp`, contracts ahead), `limit_price`, `rest_seconds`, raw | 5-min cadence; no book/trade context; ~8% of orders fill before the first sample |
| `live_order_queue_decisions` | retired shadow | off | the queue-aware-cancel audit | inert |
| `live_paper_parity_events` | twin harness | per candidate per cycle | parent/twin/live outcome + gate, quote at the instant | no order id link |
| `live_paper_twins` | twin harness | per epoch | live tag ↔ twin tag ↔ started_at | — |
| `mmsell_candidate_ticks` | tracker | per cycle, control tag only | yes/no bid/ask, mid, depth at best, volume, strike | control tag only; `depth_at_best_ask` proved **not** our queue (r = 0.06, `docs/MMSELL_DEPTH_FILL_MODEL.md`) |
| `mmsell_position_ticks` | tracker | per cycle | held-position quote path | 5-min resolution only |
| `mmsell_settlement_meta` | tracker | per market | event/series/close time | — |
| `positions`, `risk_events`, `system_events` | executor / boot | — | position snapshots; `hot_entry`/`ab_arm` audit; tier + limits | — |
| `game_tape_snapshots` | XGAME collector | 3 min poll | public trades (`GET /markets/trades`) for XGAME matches only | wrong universe |

Nothing records the order book or the public trade tape for the markets MMSELL rests in,
at any resolution finer than the 5-minute cycle. Nothing records our own fill at the moment
it happens.

## 3. Kalshi-data inventory — what is available (verified against the docs mirror of 2026-09-16, OpenAPI 3.30.0 / AsyncAPI 2.0.0)

Source of truth for every field below is `https://docs.kalshi.com/openapi.yaml` and
`asyncapi.yaml`; the fields were read from a same-day mirror because the docs host is not
reachable from the sandbox. Anything not confirmed is marked.

**Queue position (REST only — there is no WebSocket queue channel; the AsyncAPI channel list
was grepped for `queue`).**

- `GET /portfolio/orders/{order_id}/queue_position` → `{"queue_position_fp": "10.00"}`.
  Definition: the number of contracts that must match before this order receives a partial or
  full match; price-time priority; `"0.00"` = front. 10 tokens.
- `GET /portfolio/orders/queue_positions?market_tickers=a,b&event_ticker=&subaccount=` →
  `{"queue_positions": [{"order_id", "market_ticker", "queue_position_fp"}]}` for **all**
  resting orders. No `limit`, no `cursor`. 10 tokens regardless of how many orders. The
  existing sampler already parses both (`live/queue_position.py`).

**WebSocket** `wss://<api host>/trade-api/ws/v2`, auth on the upgrade request with the same
RSA-PSS headers signed over `timestamp + "GET" + "/trade-api/ws/v2"`; server pings every 10 s.

| channel | scope | sequenced | fields we persist |
|---|---|---|---|
| `orderbook_delta` | must name tickers; `use_yes_price: true` puts both sides on the YES price scale | yes (`seq` per `sid`) | snapshot: `yes_dollars_fp` / `no_dollars_fp` as `[[price_dollars, count_fp]]`; delta: `price_dollars`, `delta_fp` (signed), `side`, `ts_ms`, `client_order_id` (present only when **we** caused it) |
| `trade` | tickers optional | yes | `trade_id`, `market_ticker`, `yes_price_dollars`, `no_price_dollars`, `count_fp`, `taker_outcome_side`, `taker_book_side`, `is_block_trade`, `ts_ms` |
| `fill` (private) | tickers optional | **no** | `trade_id` (== REST `fill_id`), `order_id`, `client_order_id`, `market_ticker`, `exchange_index`, `is_taker`, `ts_ms`, `yes_price_dollars`, `count_fp`, `fee_cost`, `outcome_side`, `book_side`, `post_position_fp` |
| `user_orders` (private) | tickers optional | no | `order_id`, `ticker`, `status` (`resting|canceled|executed|unknown`), `fill_count_fp`, `remaining_count_fp`, `initial_count_fp`, `maker_fill_cost_dollars`, `maker_fees_dollars`, `created_ts_ms`, `last_updated_ts_ms` |
| `market_lifecycle_v2` | global | yes | `market_ticker`, `event_type` (`created|deactivated|activated|close_date_updated|determined|settled|price_level_structure_updated`), `is_deactivated`, `close_ts`, `determination_ts`, `settled_ts`, `result` |

Resync: `update_subscription {action: "get_snapshot", sid, market_tickers}` re-sends an
`orderbook_snapshot` without disturbing the delta stream. Errors 10 and 25 (buffer overflow)
are terminal for the subscription → resubscribe. Whether a fresh snapshot resets `seq` is
**unverified**; the collector treats the snapshot's own `seq` as the new baseline for that sid.

**REST market data**: `GET /markets/{t}/orderbook` → `orderbook_fp.{yes_dollars,no_dollars}`
(bids only, best last); `GET /markets/trades?ticker&min_ts&max_ts&limit≤1000&cursor`;
`GET /markets/{t}` carries `status`, `open_time`, `close_time`, `expected_expiration_time`,
`settlement_ts`, `volume_fp`, `open_interest_fp`, `result`, `market_type`, `exchange_index`.
There is **no** `category`, `mutually_exclusive` or non-deprecated `liquidity` on the market
object (`mutually_exclusive` lives on the event). Fixed-point `_fp`/`_dollars` strings are
canonical; integer-cent fields were removed 2026-03-12 / 04-02.

**Fills REST** (`GET /portfolio/fills?ticker&order_id&min_ts&max_ts`): `fill_id` (== `trade_id`),
`order_id`, `count_fp`, `yes_price_dollars`, `no_price_dollars`, `is_taker`, `created_time`,
`fee_cost`. **Order REST** (`GET /portfolio/orders/{id}`, 2 tokens): `fill_count_fp`,
`remaining_count_fp`, `initial_count_fp`, `maker_fill_cost_dollars`, `maker_fees_dollars`,
`created_time`, `last_update_time`, `expiration_time`. No `queue_position` on the order.

**Rate limits**: token buckets refilled per second; Read and Write are separate; default
cost 10; ADVANCED = 300/300 tokens per second (30 default reads/s), Read bucket holds two
seconds of budget. 429 body `{"error":"too many requests"}` with **no** `Retry-After` or
`X-RateLimit-*` headers; no penalty. Introspection: `GET /account/limits` (already probed at
boot) and `GET /account/endpoint_costs` (non-default costs; unauthenticated).

## 4. Gaps between current and desired telemetry

| desired (handoff §) | today | Phase 1 answer |
|---|---|---|
| order lifecycle timestamps: submit, ack, cancel request/confirm (§1) | `created_at` only | `execution_order_context` carries `decided_at`, `submitted_at`, `acked_at` (+ `ack_ts_ms` from the create response), `cancel_requested_at`, `cancel_confirmed_at`, `terminal_reason` |
| decision-time strategy context (§9) | not persisted | same table, written **before** the POST from the tracker's own variables; a test asserts no post-order field can appear |
| queue at rest / regular samples / event samples / terminal (§2) | one sample per 5 min | collector samples every `EXECUTION_QUEUE_POLL_SECONDS` (20 s), on the first sighting of a new resting order (`at_rest`), on a trade or book change at our price (`event:*`, debounced and budgeted), and on terminal (`terminal`); each row carries a `trigger` |
| order book reconstruction (§3) | none | `execution_book_events`: raw snapshot + every delta with `seq`, `ts_ms`, `received_at`, and the level quantity after applying it |
| public trades (§5) | none for these markets | `execution_trade_events` |
| our fill stream with exchange time (§6) | REST poll, reconcile-time stamp | `execution_fill_events` from the `fill` channel, reconciled to `fills` by `trade_id` |
| partial-fill / remaining count over time (§6) | none | `execution_order_events` from `user_orders` |
| post-fill price path (§7) | 5-min position ticks | markets stay subscribed for `EXECUTION_TELEMETRY_POST_WINDOW_SECONDS` (900 s) after the last tracked order goes terminal; Phase 2 derives the 100 ms…15 min marks from raw events |
| lifecycle / pauses (§11) | none | `execution_market_events` from `market_lifecycle_v2` |
| latency + data quality (§12) | none | `received_at` beside every `ts_ms`; `execution_collector_events` for connect, disconnect, seq gap, snapshot refresh, subscribe/unsubscribe, poll failure, 429, throttled writes |
| twin comparison labels (§13) | `live_paper_parity_events` | Phase 2 script; Phase 1 preserves the keys: `live_orders.id` ↔ `execution_order_context.twin_tag` + `market_ticker` + `decided_at` |

## 5. Schema (Phase 1)

Raw exchange facts, bot facts and derived fields are separated by table and by column name
(`*_fp`, `*_dollars`, `ts_ms` = exchange; `received_at` = bot; `features_json` = derived).
Prices are stored in integer YES-cents **and** NO-cents where both are known, never one
inferred from the other silently: the collector subscribes with `use_yes_price: true` and the
`price_convention` column records that per row.

```text
live_orders (existing, one row per live order, canonical lifecycle)
  └─1:1─ execution_order_context      decision-time snapshot + submit/ack/cancel stamps
  └─1:n─ live_order_queue_ticks       (+ trigger, source, remaining_count, features_json)
  └─1:n─ execution_fill_events        WS fill stream, exchange ts, fee, is_taker; rest_fill_id when reconciled
  └─1:n─ execution_order_events       WS user_orders: status / fill_count_fp / remaining_count_fp
market_ticker
  └─n─  execution_book_events         WS orderbook snapshot + deltas (seq, ts_ms, level_qty_after)
  └─n─  execution_trade_events        WS public trades (trade_id unique)
  └─n─  execution_market_events       WS market_lifecycle_v2
execution_collector_events            connection / gap / throttle / error record (never silent)
```

`live_order_queue_ticks` gains: `trigger` (`reconcile` default for the executor's existing
write; collector writes `at_rest|interval|event:trade|event:delta|terminal`), `source`
(`rest_batch|rest_single`), `remaining_count` (last known from `user_orders`/fills, nullable —
never invented), `features_json` (derived book/trade features at that instant, see §7).

Every JSON payload is kept verbatim in a `raw_json` column so a payload shape change is
recoverable, exactly as the queue sampler's first deploy proved necessary.

## 6. Collection architecture

```text
                       main worker (BOT_MODE=live)
 ┌──────────────────────────────────────────────────────────────────────┐
 │ trading loop (unchanged)                 execution telemetry thread   │
 │  tracker → mirror_mmsell_entry ──┐        (daemon; fail-soft; own DB  │
 │         writes live_orders(pending)│        sessions; read-only client)│
 │         + execution_order_context  │                                   │
 │  reconcile → fills, status,        │   every 5 s: read live_orders    │
 │         queue tick (trigger=       │     non-terminal ⇒ tracked set    │
 │         reconcile), cancel stamps  │   subscribe orderbook_delta+trade │
 │                                    │     per tracked market            │
 │                                    │   fill + user_orders + lifecycle  │
 │                                    │     once per connection           │
 │                                    │   queue poll: 20 s interval +     │
 │                                    │     event-triggered (debounced,   │
 │                                    │     ≤30/min) via REST batch       │
 │                                    │   post-terminal window 15 min     │
 │                                    │   then unsubscribe                │
 └──────────────────────────────────────────────────────────────────────┘
```

Design rules:

1. **Nothing in the thread can affect an order.** It receives a `ReadOnlyKalshi` wrapper
   exposing `get_queue_positions`, `get_order_queue_position`, `get_orderbook`, `get_market`
   only. A test imports the collector package and asserts no write method name appears.
2. **Nothing in the thread can affect the trading loop.** It is a daemon thread; every loop
   body is wrapped; an exception is logged, recorded as a collector event, and followed by
   exponential backoff (1 s → 60 s). A missing `websockets` dependency disables the collector
   with a recorded event and the worker trades on.
3. **Tracking is derived from the database, not from in-memory executor state**, so it
   survives restarts and observes orders placed before the collector started.
4. **Sequence integrity.** `seq` is checked per `sid`; a gap records `seq_gap`, marks the local
   book invalid, and requests `get_snapshot`; the book is trusted again only after the
   snapshot. Reconnect resubscribes everything and records `reconnect`.
5. **Bounded storage.** Book/trade event writes are capped per minute
   (`EXECUTION_BOOK_EVENTS_MAX_PER_MINUTE`, default 3000); beyond the cap the local book is
   still updated but rows are not persisted and a `throttled` event carries the dropped count.
   Missing telemetry is therefore always **visible**, never zero.
6. **Two clocks, always.** Every exchange event stores `ts_ms` (exchange) and `received_at`
   (bot). Queue samples store `requested_at` and `captured_at`.
7. **Derived features are recomputable.** `features_json` on a queue tick is a convenience
   computed from the local book and trade buffer at that instant; the raw events that
   produced it are in the tables beside it.

### Sampling lifecycle, per order

```text
live_orders row resting with kalshi_order_id
  ├─ first sighting            → subscribe market; queue poll (trigger=at_rest)
  ├─ every 20 s                → queue poll (interval)
  ├─ trade at our price, or delta at our price/better (excluding our own)
  │                            → queue poll (event:trade | event:delta), debounced 2 s,
  │                              budget 30/min shared across orders
  ├─ fill (WS)                 → execution_fill_events; queue poll (event:fill) if still resting
  ├─ user_orders               → execution_order_events; remaining count cached
  └─ terminal (executed/canceled in DB or WS) → queue poll attempt (terminal), then keep the
        market subscribed 15 min for the post-fill path, then unsubscribe
```

## 7. API budget

Measured tier ADVANCED: **300 read tokens/s**, default cost 10 → 30 requests/s sustained,
two-second burst capacity. Existing consumers: the mmsell scan bursts 6–25 req/s for a few
seconds per cycle; reconcile is 3–4 requests per cycle; the queue sampler 1 batch + ≤60
fallback per cycle.

Collector worst case (20 simultaneous resting orders across ~15 markets, one busy period):

| source | rate | tokens/s | share of 300 |
|---|---|---|---|
| interval queue poll (batch, all orders in one call) | 1 / 20 s | 0.5 | 0.2 % |
| at-rest poll for newly sighted orders (one batch per scan at most) | ≤ 1 / 5 s | ≤ 2.0 | 0.7 % |
| event-triggered queue polls, hard cap | 30 / min | 5.0 | 1.7 % |
| snapshot refresh after a seq gap (WS command, not REST) | — | 0 | 0 |
| market metadata read on first sighting (`GET /markets/{t}`) | ≤ 1 per new market | ≈ 0.05 | — |
| **total** | | **≤ 7.6** | **≤ 2.5 %** |

The per-order fallback endpoint is **not** used by the collector (the executor's bounded
fallback is unchanged). WebSocket traffic costs no REST tokens; subscriptions are ≤ ~30
markets against a 500k/session cap. A 429 on a queue poll is counted (`rate_limited`
collector event), backed off 10 s, and never retried in a tight loop. The existing
`kalshi_limits` boot probe stays the authoritative tier reading; the ops script reports
429 counts beside the collector's own request rate.

Order placement and cancellation therefore keep ≥ 97 % of the read budget and 100 % of the
write budget. The event-poll cap and the interval are env-settable
(`EXECUTION_QUEUE_MAX_POLLS_PER_MINUTE`, `EXECUTION_QUEUE_POLL_SECONDS`) so the budget can be
retuned against measured 429s without a deploy.

## 8. Test plan (all implemented in `tests/test_execution_telemetry*.py`)

Mapped to the handoff's 18 required checks:

| # | check | test |
|---|---|---|
| 1 | queue-position responses persist correctly | `test_interval_poll_writes_a_tick_per_tracked_order` |
| 2 | fixed-point counts preserved | `test_fixed_point_fields_are_stored_verbatim` (`"2028.55"` survives as raw and as `contracts_ahead`; `delta_fp` stored as Numeric) |
| 3 | samples map to the right order | `test_ticks_are_keyed_by_kalshi_order_id_not_by_position` |
| 4 | partial fill does not end observation | `test_a_partial_fill_keeps_the_order_tracked` |
| 5 | full fill ends the resting phase | `test_a_full_fill_moves_the_market_to_the_post_window` |
| 6 | cancel records the terminal reason | `test_cancel_stamps_request_and_confirm_times` (executor) |
| 7 | seq gaps detected | `test_a_sequence_gap_is_recorded_and_a_snapshot_requested` |
| 8 | reconnect restores a valid snapshot | `test_reconnect_resubscribes_and_marks_books_invalid_until_snapshot` |
| 9 | deltas reconstruct levels | `test_deltas_reconstruct_price_level_quantities` |
| 10 | yes/no orientation cannot invert | `test_price_convention_is_recorded_and_no_side_is_yes_priced` |
| 11 | trades link to the right window | `test_trades_are_attributed_to_the_tracked_market_only_while_tracked` |
| 12 | WS fills reconcile to REST | `test_reconcile_stamps_ws_fill_events_with_the_rest_fill` |
| 13 | missing telemetry → UNKNOWN not zero | `test_a_failed_poll_writes_a_null_tick_and_a_collector_event` |
| 14 | no post-order leakage into decision features | `test_decision_context_is_written_before_submit_and_carries_no_post_order_keys` |
| 15 | paper/live linkage keeps gate distinctions | `test_context_row_records_twin_tag_and_never_a_paper_outcome` |
| 16 | telemetry cannot change exposure | `test_the_collector_client_has_no_write_methods` + `test_collector_thread_never_touches_live_orders_status` |
| 17 | polling obeys the configured budget | `test_event_polls_are_capped_per_minute_and_debounced` |
| 18 | several resting orders tracked safely | `test_many_orders_on_many_markets_share_one_batch_poll` |

Plus: migration single-head, models/migration column parity for the new tables, ops script
allowlist parity, livedash routes read-only.

## 9. Research questions (Phase 2 — not answered here)

Q1 P(fill within T · queue at rest); Q2 queue **velocity** vs fill; Q3 velocity vs adverse
move; Q4 EV = P(fill) × E[P&L · fill]; Q5 realized P&L by queue cohort; Q6 executions vs
cancellations ahead of us (the `client_order_id`-free deltas at our price that are **not**
matched by a `trade` at that price are cancellations/repricings — this is the signal the
raw book+trade tables exist to separate); Q7 heterogeneity by series / type / htc / price.
Thresholds and buckets are **not** frozen; the data chooses them. `queue position alone has
no predictive power` is an acceptable result.

## 10. Reconciling `queue_aware_cancel`

`docs/MMSELL_QUEUE_AWARE_CANCEL.md`, `WS-015`, RETIRED 2026-09-10. Hypothesis: cancelling
deep-queue orders after 90 min frees a slot cheaply. Queue proxy: **none** — it already used
Kalshi's `queue_position_fp` from `live_order_queue_ticks` (built 2026-08-14, after Kalshi
exposed the endpoint), so its telemetry was ground truth, not a depth proxy. The depth proxy
(`mmsell_candidate_ticks.depth_at_best_ask`) was a separate attempt and failed validation
(`docs/MMSELL_DEPTH_FILL_MODEL.md`). Results that remain valid: deep-queue orders are slow,
not dead (27.5 % of would-cancels later filled); late fills were the winners; the live book is
not capacity-bound. **This work extends, not revives**: it keeps the same table and sampler,
adds cadence, context and the book/trade/fill streams the rule lacked, and changes no
behaviour. The rule engine stays unwired (`LIVE_QUEUE_CANCEL_MODE=off`).

## 11. What starts accumulating after merge — operator brief

After this PR is merged and the worker redeploys (the migration runs at boot), with
`EXECUTION_TELEMETRY_ENABLED` at its default `true`:

- every resting live order gets a queue sample within ~5 s of resting, then every 20 s, plus
  extra samples when the market trades or the book moves at our price;
- for every market with a resting order, the full order book (snapshot + every delta) and every
  public trade are recorded while the order rests and for 15 minutes after it fills or cancels;
- our fills arrive with the exchange millisecond timestamp, fee and maker flag, and are matched
  to the REST fills already recorded;
- every order carries the candidate context the strategy saw when it decided;
- every connection drop, sequence gap, throttled write and failed poll is a row, so "no data"
  is always distinguishable from "nothing happened".

Read it with `{"type":"script","name":"execution_telemetry"}` (coverage first — nothing below
it is trustworthy until coverage is high), or `--order <kalshi_order_id>` for one order's
trace, and on the livedash at `/execution`. Kill switch for the collector only:
`EXECUTION_TELEMETRY_ENABLED=false` (env channel; redeploys the worker; trading unaffected).

**Nothing here changes what the book does.** No new exposure, no new size, no new market, no
new strategy, no cancel rule. Merging is still a hard stop for the operator because the diff
touches the live executor (`docs/STANDING_AUTHORIZATIONS.md`).

## 12. Deliberately not built

- Fitting either model, or any threshold — Phase 2/3.
- Changing `fills.filled_at` semantics — Platform Change Review.
- `ticker` channel — redundant with the book stream for these markets.
- A second Railway service for the collector — a third writer needs Railway config the
  sandbox cannot perform, and one worker process already holds the credentials.
- Archiving markets we have no order in.
