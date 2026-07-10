# Kalshi API Reference

Everything needed to read market data from and trade on Kalshi. Kalshi is a CFTC-regulated Designated Contract Market; the API is REST v2 + WebSocket + (institutional) FIX 4.4. **Verify specifics against `docs.kalshi.com` — endpoints, base URLs, and fee/tick details drift, and this reference is a starting point, not a substitute for the live docs.**

## Contents
- Base URLs (prod + demo)
- Authentication (RSA-PSS signing)
- Public vs authenticated endpoints
- Pricing and tick model
- Fees
- Rate limits (tiers)
- Order types and the bid-only order book
- Ticker format
- SDKs and WebSocket
- Common failure modes

---

## Base URLs

- **Production:** `https://external-api.kalshi.com/trade-api/v2` (older material may show `trading-api.kalshi.com`; confirm current host in docs).
- **Demo (paper trading):** `https://external-api.demo.kalshi.co/trade-api/v2` — full endpoint parity, fake money, **separate API keys**. Build and test here first (Phase 5). Demo may have simulated/thin liquidity.

Trading is 24/7 with maintenance windows (roughly 3:00–5:00 AM ET; confirm current schedule).

---

## Authentication (RSA-PSS request signing)

There is **no login endpoint and no session token** — every authenticated request is individually signed. There is no simple bearer/API-key header; the "API key" is a key *pair*.

**Setup (one time):**
1. In Kalshi: Settings → API Keys → create a key pair.
2. Download the **API Key ID** (a UUID) and the **private key PEM**. The private key is shown **once** and is **not recoverable** — if lost, regenerate and re-upload a new public key. Store it in a real secret manager (same store as existing bot secrets), never in the repo or a plain `.env` committed to git.

**Per request, build three headers:**
- `KALSHI-ACCESS-KEY` — your API Key ID (UUID; copy exactly, a wrong ID silently 401s every request).
- `KALSHI-ACCESS-TIMESTAMP` — current Unix time in **milliseconds** (not seconds).
- `KALSHI-ACCESS-SIGNATURE` — base64 RSA-PSS signature (below).

**The signature:**
- Message = `timestamp_ms` + `METHOD` (uppercase: GET/POST/…) + `path`.
- **Sign the path WITHOUT query parameters.** For `.../portfolio/orders?limit=5`, sign `/trade-api/v2/portfolio/orders`. (Getting this wrong produces 401s that appear only on requests with params — a classic time-sink.)
- Use **RSA-PSS** with **SHA-256**, **MGF1-SHA256**, salt length = digest length (32 bytes). Base64-encode.

Canonical Python (from Kalshi's official docs):
```python
import base64, datetime, requests
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

def load_private_key(path):
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())

def sign(private_key, timestamp, method, path):
    path_no_query = path.split("?")[0]
    msg = f"{timestamp}{method}{path_no_query}".encode("utf-8")
    sig = private_key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("utf-8")

def auth_headers(private_key, key_id, method, path):
    ts = str(int(datetime.datetime.now().timestamp() * 1000))
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sign(private_key, ts, method, path),
    }
```
Write signing **once** as a helper (one GET, one POST) and reuse everywhere — inconsistent per-endpoint signing is a top bug source. Sanity-check a key with `openssl rsa -in key.pem -check -noout` before debugging signing logic.

---

## Public vs authenticated endpoints

- **Public (no auth needed):** `/series`, `/events`, `/markets`, market details, order books. These accept the auth headers but don't require them — you can pull all market data unauthenticated. Public read limits are higher than authenticated write limits.
- **Authenticated (`/portfolio/*`):** balance, positions, orders (place/cancel/amend), fills, settlements.

Market discovery: `GET /markets` with filters like `status=open`, `event_ticker`, `series_ticker`. Paginated (default ~100/page); iterate the cursor and sleep ~0.25s between pages to stay under limits. Always filter `status=open` to exclude settled/initialized markets.

A market object looks like:
```json
{
  "ticker": "KXHIGHNY-26MAY14-B6869",
  "event_ticker": "KXHIGHNY-26MAY14",
  "yes_bid": 0.42, "yes_ask": 0.45, "no_bid": 0.55, "no_ask": 0.58,
  "last_price": 0.44, "volume": 48250, "volume_24h": 12840,
  "open_interest": 34000, "status": "open", "result": "",
  "close_time": "2026-05-14T...Z", "expiration_time": "2026-05-14T...Z"
}
```
(Field naming/shape can vary by endpoint version — confirm against docs.)

---

## Pricing and tick model

- Prices are **fixed-point dollar strings**, 0.00–1.00, up to 4 decimals (e.g. `"0.6500"` = 65¢). When placing orders use the dollar-string price field (e.g. `yes_price_dollars`). **Legacy integer-cent fields were removed as of March 2026** — use the dollar-string fields.
- Some markets support **subpenny** ticks as small as `$0.001`. Respect each market's tick size.
- **Reciprocal pricing:** YES + NO = $1.00 exactly. A YES contract at $0.42 implies NO at $0.58.

---

## Fees

- Roughly **$0.02 per contract** on executed orders, but the taker fee is **quadratic in price**: `fee ≈ 0.07 × price × (1 − price)` per contract, so it's **maximized near $0.50** (~$0.0175) and approaches **zero near $0.00 or $1.00**.
- **Implication for strategy:** trading mid-probability brackets costs the most per contract, and your edge threshold must clear entry **and** exit fees. Cheap out-of-the-money brackets are nearly fee-free but also lower-probability. Model the exact fee on both legs in the backtest.
- Confirm the current fee schedule in docs; settlement/withdrawal fees may also apply depending on account/market.

---

## Rate limits (tiered, as of 2026)

Token-bucket, per API key; exceeding returns **429** (back off with exponential retry). Query your current limits via `GET /account/limits`.

| Tier | Read/s | Write/s | How to get it |
|------|--------|---------|---------------|
| Basic | 20 | 10 | Comes with signup |
| Advanced | 30 | 30 | Complete a form |
| Premier | 100 | 100 | ~3.75% of monthly exchange volume + technical competency |
| Prime | 400 | 400 | ~7.5% of monthly exchange volume |

Write-limited endpoints: CreateOrder, CancelOrder, AmendOrder, DecreaseOrder, BatchCreateOrders, BatchCancelOrders. In batch APIs each item = 1 write txn, except BatchCancelOrders where each cancel = 0.2 txn. Basic tier is ample for a single-strategy bot; you're unlikely to need more early.

---

## Orders and the bid-only order book

- Place via `POST /portfolio/orders`; cancel/amend via the corresponding endpoints. Limit and market orders are supported.
- **The order book is bid-only by design.** Because YES + NO = $1.00, Kalshi shows YES bids and NO bids — there are **no separate "asks."** To buy YES you effectively hit the best NO bid (crossing), and vice-versa. When constructing orders and modeling fills, reason in terms of the bids available on the opposing side, not a conventional bid/ask ladder.
- Order book shape (field names passed through from Kalshi): `{"yes": [[price, size], ...], "no": [[price, size], ...]}` — each entry is a **bid level** (price in USD, size in contracts), a list of levels, not a single value.
- For fills, assume you **cross to the opposing bid** (pay up), and model partial fills where books are thin. Keep a local cache of market metadata and handle `orderbook_delta` updates asynchronously if streaming.

---

## Ticker format

`SERIES-EVENT[-STRIKE/BRACKET]`, with a `KX` prefix on newer series. Examples:
- `KXBTC-26MAR14-100000` — BTC price series, expiring 2026-03-14, strike $100,000.
- `KXFED-26MAR19` — Fed March 2026 meeting.
- `KXHIGHNY-26MAY14` — NYC daily-high event for 2026-05-14; individual brackets append a range code.
- `KXNFLGAME-25OCT12CLEPIT` — a specific NFL game.

Discover series live from `/series` and `/events` rather than hardcoding — new cities/markets appear over time.

---

## SDKs and WebSocket

- **Official SDKs:** `kalshi-python` (also referenced as `kalshi-python-sync` / `kalshi-python-async`) and TypeScript `@kalshi/sdk`. The official SDK encapsulates the signing block — worth using so you don't re-implement undifferentiated auth plumbing. Community SDKs (Go, etc.) exist but the official ones have the most reliable signing.
- **WebSocket:** for real-time order book (`orderbook_delta`), ticker, trades, and fills. Handshake uses the same signing pattern. Subscribe only to tickers you need; keep the `websockets` keepalive alive. Use REST for snapshots (initial load, periodic refresh) and WebSocket for low-latency streaming in the live/paper bot.
- **FIX 4.4** exists for institutional low-latency needs (2048-bit RSA PKCS#8 auth) — almost certainly overkill; start REST, graduate only if latency becomes a real bottleneck.

---

## Common failure modes (save yourself the hour)

- **401 only on parameterized requests** → you signed the path *with* the query string. Strip it.
- **401 on every request** → wrong Key ID (no validation error, just silent 401), or timestamp in seconds instead of milliseconds, or clock skew.
- **Calling `.json()` on a tuple** → if your helper returns `(response, payload)`, unpack immediately: `res, data = authenticated_get(...)`.
- **Assuming asks exist** → there are none; the book is bid-only (reciprocal pricing).
- **Using integer-cent price fields** → removed March 2026; use dollar-strings.
- **429s during market scans** → add a small sleep between paginated pages and exponential backoff on 429.
