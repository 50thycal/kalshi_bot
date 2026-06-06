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
ones**:

- **Entry** (`PAPER_STRATEGY`, default `buy_favorite`): buy the side implied ≥ 50% at that
  side's ask, conservatively filled. Quantity is `PAPER_ORDER_SIZE` capped by the order-book
  depth at the entry price (depth 0 → recorded as a `no_fill`). One open position per market.
  Every entry passes the Risk Manager in paper mode (`for_paper=True`): the live-only gates
  (kill switch, mode, real balance) are skipped, but all spread/liquidity/closes-soon and
  exposure caps still apply.
- **Exit**: close at payoff (0/100) on settlement; otherwise close at the current bid after
  `PAPER_MAX_HOLD_HOURS`, or on optional `PAPER_TAKE_PROFIT_CENTS` / `PAPER_STOP_LOSS_CENTS`.
- **Fees**: Kalshi's `ceil(0.07 × C × P × (1−P))` is modeled on entry and early-exit sells
  (not on settlement) when `PAPER_FEES_ENABLED=true`.

Results land in `paper_trades` and `paper_positions`, and each cycle logs a `paper cycle`
summary (opened / no_fill / closed_* / realized P&L / fillability rate). The signal is
non-directional, so this is a measurement harness; `kalshi_bot/paper/engine.py::choose_entry`
is the single seam where a future forecasting model plugs in.

## Safety

The bot must fail closed. It will not do anything trade-like if config is missing, the
database is unavailable, or Kalshi auth fails. Live order placement is guarded and
requires `BOT_MODE=live` **and** `KILL_SWITCH=false` — out of scope for this MVP.
