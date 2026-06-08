# Kalshi Trading Bot — Scanner MVP

A Railway-ready Python worker that safely connects to [Kalshi](https://kalshi.com),
pulls market and order-book data, stores snapshots in Postgres, scores markets with a
deterministic signal engine, and logs a ranked list of candidate markets.

This is **Phase 1 + 2** of the larger build plan: a **scanner**. It does **not** place
orders, paper-trade, or use any LLM to make decisions. Everything **fails closed** — if
config, the database, or Kalshi authentication is bad, the worker exits without doing
anything trade-like.

## Architecture

```
kalshi_bot/
  config.py            Fail-closed settings (pydantic-settings)
  logging_config.py    Structured JSON logging to stdout + secret redaction
  db.py / models.py    SQLAlchemy 2.0 engine + full 13-table schema
  repository.py        DB write helpers
  kalshi/
    auth.py            RSA-PSS request signing
    client.py          Authenticated REST client (retries, fail-closed auth)
  scanner/
    metrics.py         Order-book parsing + spread/depth/liquidity metrics
    signals.py         Deterministic 0-100 scoring -> ignore/watch/candidate
    scanner.py         One scan cycle (fetch -> store -> score -> rank)
  risk/manager.py      Fail-closed Risk Manager (gates any future trade)
  main.py              Entrypoint: load config -> DB -> Kalshi -> scan loop/once
```

### Scan cycle

1. Verify exchange status and fetch account balance (connectivity proof).
2. Page through open markets; keep those in the target categories that clear the
   volume / open-interest floors.
3. For each: fetch the order book, compute metrics, persist `market_snapshot` and
   `orderbook_snapshot`, score a `signal`, and (for candidates) record a `risk_event`.
4. Log a ranked candidate list and finish the `bot_run`.

## Kalshi API notes

- **Order books are resting bids only.** The YES ask is derived: `yes_ask = 100 - best_no_bid`.
- Requests are signed with RSA-PSS (SHA-256, salt = digest length) over
  `timestamp_ms + METHOD + path`, where `path` includes `/trade-api/v2/...` with the query
  string stripped. Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`,
  `KALSHI-ACCESS-SIGNATURE`.
- Prices are integer cents (1–99); balance is in cents.
- Demo base URL: `https://demo-api.kalshi.co/trade-api/v2`;
  production: `https://api.elections.kalshi.com/trade-api/v2`.

> **Category mapping:** Kalshi market objects don't always carry a usable `category`.
> The scanner first checks `category`, then `TARGET_SERIES_PREFIXES`, then a keyword match
> on the title. Tune `TARGET_SERIES_PREFIXES` once you've seen live demo data.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `KALSHI_ENV` | `demo` | `demo` or `production` |
| `KALSHI_API_KEY_ID` | _(required)_ | Kalshi API key id |
| `KALSHI_PRIVATE_KEY` | _(required)_ | RSA private key PEM (single-line `\n`-escaped is fine) |
| `DATABASE_URL` | _(required)_ | Postgres URL (auto-normalized to `psycopg`) |
| `BOT_MODE` | `scanner` | `scanner` \| `paper` \| `approval` \| `live` (MVP runs `scanner`) |
| `KILL_SWITCH` | `true` | Master safety switch; keep `true` until live trading is enabled |
| `MAX_ORDER_SIZE` | `1` | Max contracts per order (future use) |
| `MAX_MARKET_EXPOSURE` | `25` | Per-market exposure cap, dollars |
| `MAX_TOTAL_EXPOSURE` | `100` | Total exposure cap, dollars |
| `MAX_DAILY_LOSS` | `25` | Daily loss cap, dollars |
| `SCAN_INTERVAL_SECONDS` | `300` | Seconds between scans |
| `RUN_ONCE` | `false` | Run one scan and exit |
| `TARGET_CATEGORIES` | `Economics,Fed,Jobs,Financials` | Categories to scan |
| `TARGET_SERIES_PREFIXES` | _(empty)_ | Series-ticker prefixes when category is absent |
| `MAX_SPREAD_CENTS` | `5` | Max spread for a candidate |
| `MIN_VOLUME` / `MIN_OPEN_INTEREST` | `100` / `50` | Liquidity floors |
| `MIN_HOURS_TO_CLOSE` | `1` | Reject markets closing too soon |
| `MAX_MARKETS_PER_SCAN` | `25` | Order books fetched per scan |
| `ORDERBOOK_DEPTH` | `10` | Order-book depth requested |
| `LOG_LEVEL` | `INFO` | Log level |

See `.env.example`. **Never commit a real private key.**

## Run locally (demo)

```bash
pip install -r requirements-dev.txt
cp .env.example .env            # fill in demo creds + DATABASE_URL
alembic upgrade head            # create tables
RUN_ONCE=true python -m kalshi_bot.main
```

## Deploy on Railway

1. Create a Railway project with a **Postgres** service and a **Python worker** service
   from this repo.
2. Set the worker's environment variables (table above). `DATABASE_URL` is provided by
   the Postgres plugin; reference it from the worker.
3. The start command (`railway.json` / `Procfile`) runs migrations then the worker:
   `alembic upgrade head && python -m kalshi_bot.main`.
4. Start in `demo` with `KILL_SWITCH=true` and `BOT_MODE=scanner`. Move to `production`
   only after demo connectivity and the stored snapshots look correct.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check .
pytest -q
```

Unit tests cover RSA signing correctness, fail-closed config, order-book math
(including YES-ask derivation), signal labels, and risk rules. An end-to-end test runs a
full scan against a fake Kalshi client + sqlite. CI additionally applies the Alembic
migration against a real Postgres 16 service.

## Paper trading (Phase 3)

Set `BOT_MODE=paper` to run everything the scanner does **plus** simulate trades from
the candidate signals — no real orders are ever placed. Paper trading uses a simulated
bankroll (`PAPER_STARTING_BANKROLL`), so the real account balance is irrelevant.

Each cycle the worker first **manages open paper positions**, then **scans and opens new
ones**. Multiple strategies run as **parallel books** (`PAPER_STRATEGIES`, default
`buy_favorite,momentum,ladder`) — one position per `(market, strategy)`, so their P&L can be
compared head-to-head:

- **`buy_favorite`** (control): buy the side implied ≥ 50% at its ask; edge = 0.
- **`momentum`**: fit recent `market_snapshots.midpoint` drift and project it
  `PAPER_MOMENTUM_PROJECT_HOURS` forward → model probability; trade the side the edge favors
  when `|edge| ≥ PAPER_MIN_EDGE_CENTS`. Bets recent drift continues.
- **`reversion`**: same model with the drift sign flipped — bets a recent move overshoots and
  fades. Run alongside `momentum` to compare the two hypotheses head-to-head.
- **`ladder`**: isotonic "fair curve" relative value across a series' strike ladder; trade
  cheap/rich rungs beyond the edge threshold (auto-skips non-monotone groups).

Market selection is **stratified by category** (up to `MAX_MARKETS_PER_CATEGORY` per category,
then filled to `MAX_MARKETS_PER_SCAN` by volume) so thinner, less-efficient categories get
scanned rather than only the highest-volume economic markets.

Edge is measured against the price actually paid (the ask), so it nets out the spread; entries
are also `PAPER_ORDER_SIZE` capped by order-book depth (depth 0 → `no_fill`). Every entry
passes the Risk Manager in paper mode (`for_paper=True`): the live-only gates (kill switch,
mode, real balance) are skipped, but all spread/liquidity/closes-soon and exposure caps apply.
- **Exit**: close at payoff (0/100) on settlement; otherwise close at the current bid after
  `PAPER_MAX_HOLD_HOURS`, or on optional `PAPER_TAKE_PROFIT_CENTS` / `PAPER_STOP_LOSS_CENTS`.
- **Fees**: Kalshi's `ceil(0.07 × C × P × (1−P))` is modeled on entry and early-exit sells
  (not on settlement) when `PAPER_FEES_ENABLED=true`.

Results land in `paper_trades` and `paper_positions`. Each cycle logs a `paper cycle`
summary (opened / no_fill / already_open / risk_blocked / closed_* / fillability) and a
`paper portfolio` rollup (open positions, open unrealized P&L, realized P&L to date). The
signal is non-directional, so this is a measurement harness;
`kalshi_bot/paper/engine.py::choose_entry` is the single seam where a future forecasting
model plugs in.

For an on-demand performance report (status breakdown, realized P&L + win rate, open
unrealized, fillability, and P&L by category):

```bash
DATABASE_URL=postgresql://... python scripts/paper_stats.py
```

## Weather mode (Phase 4)

`BOT_MODE=weather` runs a focused pipeline on Kalshi's **daily high-temperature** markets
instead of the broad scanner. Each city ("Highest temperature in `<CITY>` today?") is a daily
event with ~6 mutually-exclusive 2° buckets settling on the NWS Daily Climate Report.

- **Baseline**: for the top `WEATHER_TOP_N` cities by volume, buy the **favorite bucket** (highest
  implied probability) at several hours-to-settlement snapshots (`WEATHER_ENTRY_HOURS`, default
  `12,8,4`) — separate paper books `weather_fav_h12/h8/h4` — held to settlement. Comparing the
  windows shows how much entry timing matters.
- **Forecast collection**: each cycle fetches the NWS daily-high forecast (`api.weather.gov`, free,
  needs `NWS_USER_AGENT`) per city and stores it in `weather_forecasts` — the dataset for a future
  forecast-edge strategy (forecast high → bucket probabilities → trade the mispriced bucket).
- On startup it abandons any open paper positions from prior experiments
  (`PAPER_ABANDON_FOREIGN_ON_START`).

City→station/lat-lon mapping lives in `kalshi_bot/weather/cities.py`; verify the `KXHIGH*` series
tickers against the first run's logs. Each cycle also captures the actual winning bucket of recently
**settled** events into `weather_settlements` (the ground truth). Grade the forecast vs the market:

```bash
DATABASE_URL=postgresql://... python scripts/weather_score.py
```

reports the favorite baseline win-rate/P&L by entry window, and a head-to-head — **NWS-implied
bucket vs market favorite** (who was right on settled events; "NWS-only-right ≫ market-only-right"
over enough events means a real forecast edge).

## Safety

The bot must fail closed. It will not do anything trade-like if config is missing, the
database is unavailable, or Kalshi auth fails. Live order placement is guarded and
requires `BOT_MODE=live` **and** `KILL_SWITCH=false` — out of scope for this MVP.
