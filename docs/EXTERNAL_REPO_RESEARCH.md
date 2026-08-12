# External Kalshi-repo research — what other people do that we should steal

*Run 2026-08-11. Requested target: `sswadkar/kalshi-interface`, plus independent comparison repos.*

Ordered by **expected impact on our $100/month realized-P&L goal**, not by how interesting the
code is. Every item is scored against what our own docs already say is broken.

**Safety note first.** Four repos were cloned and read; **none were executed**. A scan for
`eval`/`exec`/`b64decode`/`pickle.loads`/shell-pipe-install/install hooks across all of them found
nothing malicious — the only `subprocess` calls (in `ryanfrigo`) launch a local dashboard and run
`pip install`. The complete set of outbound hosts referenced across all four is ordinary:
Kalshi, Polymarket, Open-Meteo, api.weather.gov, exchange APIs, OpenAI/OpenRouter, news RSS. See
§8 for the specific *anti*-patterns worth not copying.

## Repos read

| Repo | What it is | Why it earned a read |
|---|---|---|
| [`sswadkar/kalshi-interface`](https://github.com/sswadkar/kalshi-interface) | ~700 LOC FastAPI position tracker + order endpoints for one event ticker | The requested target. Small, but surfaces one API endpoint we don't use at all |
| [`brianeide/kalshi-mm`](https://github.com/brianeide/kalshi-mm) | ~1.5k LOC single-market Avellaneda-Stoikov maker: WS orderbook, amend-based quoting, token bucket | **The most relevant repo found.** It is a real Kalshi *maker*, which is what mmsell is |
| [`rodlaf/KalshiMarketMaker`](https://github.com/rodlaf/KalshiMarketMaker) | Multi-market A-S maker with a dynamic market selector and a shared global risk budget | Operational patterns: orderly drain, global contract budget, 429 handling |
| [`suislanchez/polymarket-kalshi-weather-bot`](https://github.com/suislanchez/polymarket-kalshi-weather-bot) | Ensemble weather bot on KXHIGH + Polymarket, Kelly sizing | Directly overlaps our weather book |

---

## 1. Queue position is a first-class Kalshi API field — and we are currently spending real money to infer it

**This is the highest-impact finding by a wide margin, because it attacks the exact mechanism our
own docs name as the #1 profit leak.**

`docs/MMSELL_FILL_MODEL.md` states the whole paper→live gap is maker adverse selection worth
**~2.0¢/contract**, and explains we cannot replay it because "the paper books throw away the exact
data a fill model needs." `docs/MMSELL_OFFSET_AB.md` then spends live money on a two-book A/B
(`mmsell10a` vs `mmsell10b`) to find out what 1¢ of *queue priority* buys — measured indirectly,
through downstream P&L.

Kalshi exposes queue position directly:

- `GET /trade-api/v2/portfolio/orders/{order_id}/queue_position` — one order
- `GET /trade-api/v2/portfolio/orders/queue_positions` — batch, filterable by `market_tickers` /
  `event_ticker`

Queue position is assigned by **price-time priority**. `sswadkar/kalshi-interface` polls the batch
endpoint every 3s for its resting orders (`kalshi_positions.py:get_queue_positions`);
`brianeide/kalshi-mm` reads `queue_position` straight off the order object it already fetches
(`order_manager.py:179`), i.e. it may come back on the order payload with **no extra request**.
Independent confirmation the endpoints are real and current: they are in CCXT's Kalshi module and
in at least six unrelated Python SDKs.

**What this buys us.** Our resting mmsell orders currently live 4 hours
(`live_order_timeout_seconds = 14_400`) as black boxes — we learn only "filled" or "timed out." If
we sample `queue_position` (plus contracts ahead) once per live cycle per resting order and store
it, we can fit the thing the fill model is currently forced to approximate:

    P(fill)  ~  f(queue rank at rest, price cell, depth ahead, time-to-close)

from **our own orders**, on data that accumulates whether or not an A/B is armed. That converts
the offset experiment from a noisy P&L horse-race into a mechanism measurement: *did +1¢ actually
move us up the queue, and by how many contracts?* If it turns out +1¢ buys nothing at our sizes
(plausible on 1¢-wide books — see `maker_no_price`'s post-only ceiling note, where the offset arm
can't even apply), we stop paying 1¢/fill for it immediately instead of waiting for n to build.

**Cost to implement:** one client method, one storage table, one call per live cycle. It is the
cheapest large thing on this list.

## 2. `amend` and `decrease` exist — our cancel-and-repost donates queue priority

Our executor cancels a resting order on timeout (`executor.py:811`) and re-enters with a fresh
`client_order_id`, which sends us to the **back of the queue**. Kalshi has:

- `POST /trade-api/v2/portfolio/events/orders/{order_id}/amend` — change price/size in place
- `POST /trade-api/v2/portfolio/orders/{order_id}/decrease` — reduce size only

The rule that matters: **an amend preserves queue position only when it decreases size.** A price
change loses priority either way — but an amend is *atomic*, so unlike cancel-then-repost there is
no window with no order resting and no `409_already_exists` race (we currently carry explicit
handling for that at `executor.py:363`).

Both maker repos encode the discipline we're missing:

- `rodlaf` (`avellaneda.py:handle_order_side`) **keeps** an existing order untouched when the
  desired price and size match, and only cancels the rest. Re-posting a still-correct order is a
  pure donation of queue priority.
- `brianeide` (`strategy.py:_update_quotes`) calls `amend_order` when the price moves, and only
  creates a new order when none exists.

**For mmsell specifically:** a timeout-cancel followed by a re-entry at *the same price* is
strictly worse than leaving the order alone — same price, worse queue rank. Worth auditing whether
our 4-hour timeout is buying anything at all, which item 1 would answer with data.

## 3. WebSocket (`/trade-api/ws/v2`) is the durable fix for the data we admit we throw away

We have **zero** WebSocket usage — a grep across the whole repo finds it mentioned only in a skill
reference doc. Everything is REST polling. Kalshi's WS channels are `orderbook_delta`, `ticker`,
`trade`, and `fill`.

Two concrete payoffs, both tied to open problems in our docs:

1. **`orderbook_delta` gives us the per-ticker price path for free.** `MMSELL_FILL_MODEL.md` §2
   says the faithful fix ("would this resting order have been lifted before close?") is impossible
   because we don't persist book state for mmsell tickers. A WS book subscription is exactly that
   data, at a fraction of the request cost of polling. It also grows
   `mmsell_position_ticks` coverage, which `MMSELL_EXIT_STUDY.md` currently calls a
   data-maturity wait.
2. **`fill` gives immediate fill notification** instead of a poll cycle — relevant to the exit
   path and to reacting before adverse selection compounds.

**Non-negotiable implementation detail, and this is where the repos disagree.**
`brianeide/kalshi-mm` does it right (`orderbook.py:check_sequence`): every delta carries a `seq`,
and a gap raises rather than being silently applied, forcing a snapshot resync. `ryanfrigo`'s WS
client subscribes to `orderbook_delta` and has **no sequence handling at all** — that book drifts
silently and you'd never know. If we build this, copy `brianeide`, not the popular repo.

## 4. Weighted mid (microprice) — the untested third entry mechanic

`brianeide` quotes off a size-weighted mid, not the raw mid (`orderbook.py:get_wmid`):

    wmid = (bid × ask_size + ask × bid_size) / (bid_size + ask_size)

This is the classic microstructure predictor of short-term direction, and for a resting maker it is
*directly* the adverse-selection signal: when size is stacked on the side that would lift you,
you're about to be run over. `rodlaf` gets at the same idea from the other end with inventory skew
and a dynamic `gamma` that widens quotes as position builds.

We already gate entries on **volatility** (anchor A4) and on **momentum** (`is_hot_entry`). Book
imbalance is the obvious third and it is computable from the orderbook we *already fetch at entry*
— we have `best_no_bid`/`best_no_ask` in `metrics`; we'd need the sizes at those levels. Cheap to
add as a paper variant on the mmsell10 entry, so it reads against the same control as the anchor
set.

## 5. Rate-limit hygiene: `Retry-After`, jitter, and a token bucket

Our client (`kalshi/client.py`) retries on 429/5xx with **fixed** backoffs `(2, 4, 8)`, ignores the
`Retry-After` header, and has **no client-side rate limiter** anywhere in the repo. Three upgrades,
in order:

- `rodlaf` reads `Retry-After` and takes `max(retry_after, 0.5·2^attempt) + uniform(0, 0.25)` —
  **honour the server's number, and add jitter** so our own parallel callers don't resynchronise
  into a thundering herd.
- `brianeide` runs a proper **token bucket** (`kalshiapi.py:75`) so bursts are shaped before they
  hit the wire, rather than being punished after.
- `rodlaf`'s selector loop (`dynamic.py`) has the best failure posture: on a 429 it **reuses the
  previous market selection and backs off exponentially** (5s → 120s cap) instead of dropping the
  cycle. Degrade, don't stall.

This matters more than it looks: the seasonal history capture pages aggressively, and item 3 would
change our request profile substantially.

## 6. Orderly drain — cancel resting orders when a book stands down

`rodlaf`'s `stop_worker_then_cancel` stops the worker, *then* cancels its resting orders, *then*
**verifies the cleanup succeeded** before dropping worker state — if cleanup fails the worker stays
registered for a retry next cycle. Same routine runs on shutdown.

Our stand-down story is asymmetric in a way our own postmortem already documents. `KILL_SWITCH=true`
blocks order *placement* at the client (`_ensure_live_enabled`), which means it also blocks the
**closeout** — `_closeout_can_place` exists precisely because 1,913 dead `pending` rows were
generated by a closeout loop retrying forever against a kill switch. What I did not find is a path
that **cancels already-resting orders** when a book stands down; the only cancel path is the
per-order 4-hour timeout. A `cancel_all_resting(book)` that runs on stand-down — and that reports
whether it fully succeeded — is a small, high-value safety addition.

Also worth noting from `rodlaf`: a **shared global contract budget** across markets
(`max_global_contracts` minus a `reserve_contracts_buffer`, divided equal-weight across active
markets) rather than only per-market caps. We cap per-order dollars and per-book contracts; a
portfolio-level ceiling that all books draw from is a different guarantee.

## 7. Weather: NBM percentiles as an independent second distribution

The weather bot uses the **same** primary source we do (Open-Meteo ensemble, per-member daily
extremes → fraction of members above threshold), so there is no edge to steal in the core method.
Its `VALIDATED_RESEARCH.md` is a genuinely useful negative-results doc, and two things in it are
new to us:

- **NBM (National Blend of Models)** publishes explicit MaxT/MinT **percentiles** (5/10/25/50/75/
  90/95) derived from ~200 members, via NOMADS/AWS in GRIB2. That is a second, methodologically
  independent probabilistic distribution to blend against or validate our Open-Meteo members with —
  which is exactly what `weather_validation` is built to score. Cost: GRIB2 parsing (`cfgrib`), so
  not free.
- Their corrections table is worth reading before anyone proposes a source: **NWS API returns
  deterministic point estimates only** (no probability ranges), **HRRR is deterministic**, ECMWF
  open data is 50 perturbed + 1 control (not 51).

Two things they do that we already do better: settlement-source alignment (we have obs-confirmed
entry in `weather_entry_study`; they only note that Kalshi settles on the NWS Daily Climate Report)
and clipping unanimous ensembles (they hard-clip to [0.05, 0.95]).

Their Kelly sizing (fractional Kelly, capped at 5% of bankroll per trade) is a real difference from
our fixed per-order dollar cap — but for a 91%-win cheap-tail seller with a ~0.2¢/contract realized
edge, Kelly would size on an edge estimate we don't trust yet. **Not recommended until item 1
lands**; sizing on a mis-measured edge is how you lose faster.

## 8. Anti-patterns — what not to copy

- **`sswadkar/kalshi-interface` commits `api_keys/{demo,prod}/private_key.pem` into the repo.** The
  checked-in files are placeholders (`<INSERT KALSHI PRIVATE KEY HERE>`), so nothing leaked — but
  the layout teaches storing your signing key in the source tree, and its `Dockerfile` does
  `WORKDIR /` + `COPY . .`, so a real key ships into the image. Our GitHub-Actions-secrets model is
  correct; keep it.
- **`sswadkar`'s `track_position` is broken** (`kalshi_positions.py:147`): it divides by the *old*
  `abs_pos`, which is `0` on the first buy (ZeroDivisionError), and only handles buy-YES so sells
  never update the average. It's dead code — `compute_positions` now reads Kalshi's server-side
  `realized_pnl_dollars` / `market_exposure_dollars` instead, which is the right call and which we
  already do (`executor.py:790`). The lesson is the migration, not the function.
- **A 0.5s poll loop** (`server.py:poll_markets`) re-fetching markets + positions + balance is
  ~6 req/s sustained against one event. That is what item 5 is for.
- **`ryanfrigo`'s WS client applies `orderbook_delta` with no sequence checking** (see item 3).
  It is the most-starred repo of the four; stars are not correctness.

---

## Suggested order of work

1. **Sample and store `queue_position` for every resting live order** (item 1). Unblocks a real
   fill model and makes the offset A/B interpretable. Small change, biggest payoff.
2. **Stop cancel-and-reposting at unchanged prices; add `amend`/`decrease`** (item 2). Directly
   recovers queue priority we are giving away today.
3. **`Retry-After` + jitter + a token bucket** (item 5). Half a day, removes a whole class of
   failure, and is a prerequisite for anything that increases request volume.
4. **Cancel-all-resting on book stand-down** (item 6). Safety, and it closes a known asymmetry.
5. **WebSocket `orderbook_delta` + `fill`, with sequence-gap resync** (item 3). Bigger build;
   the durable fix for the data we throw away.
6. **Book-imbalance (wmid) entry gate as a paper variant** (item 4), read against mmsell10.
7. **NBM as a second weather distribution** (item 7) — only if the weather book is worth more
   investment on current P&L.

Items 1, 2 and 4 all attack maker adverse selection, which is the only mechanism our own data says
is worth ~2¢/contract. Nothing else on this list is close.
