# Live trading — demo dry-run checklist

The live execution layer (`kalshi_bot/live/`) is validated against a `FakeLiveClient`, not
the real Kalshi API. Before risking real money, run this checklist against the **Kalshi demo
environment** (`KALSHI_ENV=demo`) with a demo API key to confirm auth, the order/fill/position
**response shapes** the executor parses, and the full entry→reconcile→settle loop.

The executor parses these Kalshi response keys — confirm each matches a real demo response
(adjust `kalshi_bot/live/executor.py` parsing if Kalshi differs):

| Call | Expected key(s) the executor reads |
|---|---|
| `place_order` | `resp["order"]["order_id"]`, `["order"]["status"]` |
| `get_orders` | `{"orders": [{order_id, client_order_id, status}]}` |
| `get_fills` | `{"fills": [{trade_id\|fill_id, order_id, ticker, side, action, yes_price\|price, count\|quantity, fee}]}` |
| `get_positions` | `{"market_positions": [{ticker, position, market_exposure, realized_pnl}]}` |
| `get_balance` | `{"balance": <cents>}` |

## 0. Pre-flight (still inert)
- [ ] `KALSHI_ENV=demo`, valid demo `KALSHI_API_KEY_ID` + `KALSHI_PRIVATE_KEY`.
- [ ] `BOT_MODE=weather`, `KILL_SWITCH=true`, `LIVE_ENABLED=false`, `LIVE_STRATEGIES=` (empty).
- [ ] Deploy; confirm startup log shows the redacted summary with `live_enabled=false`,
      `live_strategies=[]`, and that ZERO `live_orders` rows are created over a few cycles.
- [ ] `alembic upgrade head` applied cleanly (the `e1f2a3b4c5d6` live-order columns exist).

## 1. Auth + read endpoints (no orders)
- [ ] In a one-off demo script (or `RUN_ONCE`), call `get_balance()`, `get_exchange_status()`,
      `get_orders()`, `get_fills()`, `get_positions()` and **print the raw JSON**.
- [ ] Verify the keys above match. Note any differences (e.g. `count` vs `quantity`,
      `market_positions` vs `positions`, price field names) and reconcile the parser.
- [ ] Confirm `get_balance()["balance"]` is in **cents**.

## 2. Switch to live (demo), one tiny book
- [ ] `BOT_MODE=live`, `KILL_SWITCH=false`, `LIVE_ENABLED=true`.
- [ ] `LIVE_MAX_ORDER_DOLLARS=1` (smallest), `LIVE_STRATEGIES=weather_low_fav` (one book).
- [ ] `LIVE_ENTRY_STYLE=marketable`, `LIVE_EXIT_MODE=settlement`.
- [ ] Confirm the worker **refuses to start** if the demo balance call fails (fail-closed),
      and starts cleanly when balance is available.

## 3. Entry path
- [ ] On the next cycle with an eligible `weather_low_fav` favorite, confirm exactly one
      `place_order` is sent: a `limit` buy on the YES side, `count` from the dollar cap,
      `client_order_id = "weather_low_fav_h<window>:<event>"`.
- [ ] A `live_orders` row is written `pending` then updated to `submitted`/`filled` with the
      real `kalshi_order_id`.
- [ ] A matching `risk_event` row is recorded.
- [ ] Re-run the cycle: the `(event, strategy)` dedup prevents a second order.

## 4. Reconciliation
- [ ] After a fill, `get_fills` is recorded once in `fills` (re-run a cycle → still one row;
      dedup on `kalshi_fill_id` holds).
- [ ] A `positions` snapshot row appears with the right `ticker`, `quantity`, `realized_pnl`.
- [ ] `live_orders.status` reflects fill state (`filled`/`partial`).

## 5. Passive style + timeout
- [ ] Set `LIVE_ENTRY_STYLE=passive`, `LIVE_PASSIVE_OFFSET_CENTS=2`,
      `LIVE_ORDER_TIMEOUT_SECONDS=120` on a market that won't immediately fill.
- [ ] Confirm a resting limit below the ask, and that it is **canceled** (one `cancel_order`)
      once older than the timeout, with status `canceled`/`timeout`.

## 6. Dynamic exits (optional)
- [ ] Set `LIVE_EXIT_MODE=tp_sl`, `LIVE_TAKE_PROFIT_CENTS=10` on an open demo position.
- [ ] When the bid rises ≥ entry+10, confirm a single `sell` limit closing order
      (`client_order_id = "exit:..."`), and that re-running the cycle does not double-sell.
- [ ] Repeat for `LIVE_STOP_LOSS_CENTS` and `LIVE_BREAK_EVEN_ARM_CENTS`.

## 7. Settlement + daily-loss breaker
- [ ] Let a demo position settle; confirm realized P&L lands in a `positions` snapshot and
      `live_realized_pnl_today` reflects it.
- [ ] Set `MAX_DAILY_LOSS` low and force a loss; confirm the CRITICAL "circuit breaker
      tripped" log and that new entries are blocked.

## 8. Restart recovery
- [ ] Kill the worker mid-cycle (or right after a `place_order`); restart.
- [ ] Confirm `recover()` reconciles the in-flight order via `client_order_id` (no duplicate
      order, fill recorded once), and an untracked demo position logs CRITICAL without
      auto-trading it.

## Recommended config for a small ($50 wallet) live test

The cross-validation (`docs/RESEARCH_JOURNAL.md`) says the defensible first live edge is the
**late-window high favorite in stable cities** (DEN/MIA/AUS/CHI/LAX at h8) — NOT the low
favorite (which fails to confirm out-of-sample). With a $50 wallet, size tiny and cap risk
well under the balance. Set these as Railway env vars (committed defaults stay inert):

```
BOT_MODE=live
KILL_SWITCH=false
LIVE_ENABLED=true
LIVE_STRATEGIES=weather_fav            # high favorite only (low fav = weather_low_fav)
LIVE_CITIES=DEN,MIA,AUS,CHI,LAX        # stable marine/continental cities (cross-validated)
LIVE_WINDOWS=8                         # late entry only (the high has formed)
LIVE_ENTRY_STYLE=marketable
LIVE_MAX_ORDER_DOLLARS=2               # ~1-4 contracts per order
LIVE_EXIT_MODE=settlement              # hold to settlement (exits don't help these books)
MAX_ORDER_SIZE=5
MAX_MARKET_EXPOSURE=10                 # << $50
MAX_TOTAL_EXPOSURE=40                  # leave headroom under the $50 balance
MAX_DAILY_LOSS=15                      # circuit breaker trips well before the wallet is gone
LIVE_KILL_ON_DAILY_LOSS=true
```

This trades ~one tiny high-favorite order per eligible stable city per day, hold-to-settle.
Confirm on demo first (sections 1–8); the wallet test then validates real fills + settlement
with at most a few dollars at risk. Scale `LIVE_MAX_ORDER_DOLLARS` / add cities only after a
clean run.

## Go-live gate (production)
Only after all of the above pass on demo:
- [ ] `KALSHI_ENV=production`, production demo→real key swapped.
- [ ] Start with `LIVE_MAX_ORDER_DOLLARS` tiny and a single proven book in `LIVE_STRATEGIES`
      (e.g. `weather_low_fav`).
- [ ] Watch the first `live cycle` logs and the `live_orders`/`fills`/`positions` tables for a
      full day before scaling the dollar cap or adding books.

## mmsell maker NO-buy dry-run (a DIFFERENT order shape)

The weather books above are YES-taker entries; the mmsell books (`mmsell3`) place a **resting
maker buy-NO**, held to settlement, via `LiveExecutor.mirror_mmsell_entry`. On Kalshi's current V2
endpoint (`POST /portfolio/events/orders`, which quotes from the YES side) that is expressed as a
**`side:"ask"` maker order** (sell YES == buy NO) at `price = (100 − no_price)/100` dollars,
`post_only:true`, with `count`/`price` as decimal strings and a UUID `client_order_id` — see the
"Kalshi V2 order endpoint" note in `docs/MMSELL_LIVE_PLAN.md` §3. Verify it separately on demo
before real money (full plan + gates: `docs/MMSELL_LIVE_PLAN.md`):

- [ ] `BOT_MODE=live`, `LIVE_STRATEGIES=mmsell3`, Stage-1 config from the plan §4.
- [ ] Confirm a placed order logs `mmsell live order placed (resting maker no-buy)` (with
      `sell_yes_price`) and a `live_orders` row with `side="no"`, `status="resting"`, `limit_price`
      ≈ the no-bid (the NO cost basis; the wire order is a YES-side ask).
- [ ] Confirm it **rests** (a maker limit may not fill immediately) and either fills → a `fills`
      row with a real `fee`, or ages out → `canceled` after `LIVE_ORDER_TIMEOUT_SECONDS`.
- [ ] Confirm a held-to-settlement NO position settles via `/portfolio/settlements` on reconcile
      (realized P&L flows into `positions`, feeding the daily-loss breaker + `scripts/mmsell_live.py`).
- [ ] Run `{"type":"script","name":"mmsell_live"}` — the scorecard should render (empty is fine
      pre-fills) with fill-rate, fee/contract, and the live-vs-paper win-rate read.

Stage 1 go-live env (per the plan; ~$150 funded):

```
BOT_MODE=live
KILL_SWITCH=false
LIVE_ENABLED=true
LIVE_STRATEGIES=mmsell3
MMSELL_LIVE_PRICE_OFFSET_CENTS=0       # join the queue at the no-bid
LIVE_MAX_ORDER_DOLLARS=1               # 1 contract at ~92c
MMSELL_LIVE_MAX_OPEN_POSITIONS=60      # near the observed paper peak (68)
MMSELL_LIVE_MAX_SPREAD_CENTS=40        # generous (NOT the weather 5c gate)
LIVE_ORDER_TIMEOUT_SECONDS=600
LIVE_EXIT_MODE=settlement
MAX_ORDER_SIZE=1
MAX_MARKET_EXPOSURE=2
MAX_TOTAL_EXPOSURE=120
MAX_DAILY_LOSS=15
LIVE_KILL_ON_DAILY_LOSS=true
```
