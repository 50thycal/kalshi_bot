"""Read-only probe of Kalshi PUBLIC market-data structure (no auth, no orders).

Why this exists: closing a YES position is rejected `invalid_parameters` on the
weather RANGE-BUCKET markets (`...-B##.#`) in BOTH directions (sell-yes AND
buy-no) even while the market is open, yet a buy-no close fills on a THRESHOLD
market (`...-T##`). The order payloads match Kalshi's OpenAPI spec, so the cause
must be something market/event-structural the order body doesn't carry. This
script fetches the public event + market objects for the tickers in question and
prints their full structure so we can spot the distinguishing field
(e.g. `mutually_exclusive`, a different `market_type`, `response_price_units`,
`can_close_early`, a strike-group / order-group hint).

Read-only: only GETs Kalshi's public market-data endpoints (which need no API
key). A browser User-Agent clears Cloudflare's 1010 (same trick as
railway_logs.py). Stdlib only — runs on the ops-runner (open egress).

Usage (via the ops channel):
  {"type":"script","name":"kalshi_market_probe",
   "args":["KXHIGHLAX-26JUN14-B72.5","KXHIGHDEN-26JUN14-T69"]}
Args may be EVENT tickers (fetched with nested markets) or MARKET tickers
(fetched directly, plus their parent event). With no args, a default
bucket-vs-threshold pair is probed.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_DEFAULT = ["KXHIGHLAX-26JUN14-B72.5", "KXHIGHDEN-26JUN14-T69"]

# A market ticker = an event ticker plus a trailing strike token (-B74.5 / -T69).
_MARKET_RE = re.compile(r"^(?P<event>.+)-(?P<strike>[BT][0-9.]+)$")

# Fields most likely to explain a close being accepted vs rejected.
_MARKET_KEYS = (
    "ticker", "market_type", "status", "can_close_early", "response_price_units",
    "tick_size", "notional_value", "strike_type", "floor_strike", "cap_strike",
    "yes_bid", "yes_ask", "no_bid", "no_ask", "open_time", "close_time",
    "expiration_time", "settlement_timer_seconds", "rules_primary",
)
_EVENT_KEYS = (
    "event_ticker", "series_ticker", "title", "mutually_exclusive",
    "category", "strike_period", "price_units",
)


def _get(path: str) -> dict | None:
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        print(f"  HTTP {exc.code} for {path}: {body}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"  error for {path}: {exc}", file=sys.stderr)
    return None


def _summary(d: dict, keys) -> dict:
    return {k: d.get(k) for k in keys if k in d}


def _probe_event(event_ticker: str) -> None:
    print(f"\n=== EVENT {event_ticker} ===")
    data = _get(f"/events/{event_ticker}?with_nested_markets=true")
    if not data:
        return
    ev = data.get("event") or data
    print("event fields:", json.dumps(_summary(ev, _EVENT_KEYS), indent=2))
    # show ALL top-level event keys so we don't miss an undocumented flag
    print("event all keys:", sorted(ev.keys()))
    markets = ev.get("markets") or data.get("markets") or []
    print(f"nested markets: {len(markets)}")
    for mk in markets[:4]:
        print("  market:", json.dumps(_summary(mk, _MARKET_KEYS)))
        print("  market all keys:", sorted(mk.keys()))


def _probe_market(market_ticker: str) -> None:
    print(f"\n=== MARKET {market_ticker} ===")
    data = _get(f"/markets/{market_ticker}")
    if not data:
        return
    mk = data.get("market") or data
    print("market fields:", json.dumps(_summary(mk, _MARKET_KEYS), indent=2))
    print("market all keys:", sorted(mk.keys()))


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv else list(_DEFAULT)
    seen_events: set[str] = set()
    for tk in args:
        m = _MARKET_RE.match(tk)
        if m:
            _probe_market(tk)
            ev = m.group("event")
            if ev not in seen_events:
                seen_events.add(ev)
                _probe_event(ev)
        else:
            if tk not in seen_events:
                seen_events.add(tk)
                _probe_event(tk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
