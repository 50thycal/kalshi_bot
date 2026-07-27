"""Why isn't this evo paper order filling? Replay the fill check against the LIVE book.

An evo order can sit `open` forever with no feedback: the agent saw a valid
quote, priced against it, and the order still never fills. The paper fill engine
(kalshi_bot/evo/paper.evaluate_order) decides that from the ORDERBOOK DEPTH
LEVELS, not from top-of-book prices — so an orderbook that arrives empty (or in
a shape the parser doesn't recognize) makes every taker order unfillable at any
price, while `get_quote` still returns a healthy-looking quote built from the
market object's top-of-book fields. That failure is invisible from the DB alone:
the order just says "open".

This probe closes that gap. For each open order it fetches the live public
market + orderbook, prints the RAW orderbook JSON (so a format change is
immediately visible rather than silently parsed to nothing), parses the depth
levels, and replays the same arithmetic evaluate_order uses to report whether
the order SHOULD fill right now — and if not, exactly which condition blocks it.

Read-only end to end: SELECTs the order rows, and only GETs Kalshi's public
market-data endpoints (no auth, no order placement). A browser User-Agent clears
Cloudflare 1010 (same trick as railway_logs.py). Stdlib + psycopg only, so it
runs on the ops-runner.

NOTE: the level-parsing and fill arithmetic below MIRROR
kalshi_bot/evo/marketdata._parse_levels / Quote.taker_levels and
kalshi_bot/evo/paper.evaluate_order. They are duplicated (not imported) because
the ops-runner installs only psycopg — no sqlalchemy/pydantic. The point of this
tool is to detect divergence between what the engine believes and what the
exchange actually returns, so it deliberately ALSO prints a tolerant parse: when
strict (engine) parsing yields nothing but tolerant parsing finds real levels,
the engine's parser has drifted from the live API shape.

Usage (via the ops channel):
  {"type":"script","name":"evo_order_probe"}                 # all open orders
  {"type":"script","name":"evo_order_probe","args":["37"]}   # specific order id(s)
  {"type":"script","name":"evo_order_probe","args":["KXHIGHNY-26JUL27-B85.5"]}
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_DEPTH = 10
_RAW_CAP = 1200  # chars of raw orderbook JSON to echo


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _get(path: str) -> dict | None:
    req = urllib.request.Request(f"{BASE}{path}", method="GET", headers={
        "User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
        print(f"  ! fetch {path} failed: {exc}")
        return None


# --- level parsing -----------------------------------------------------------


def _parse_levels_strict(raw: object) -> list[tuple[int, int]]:
    """EXACT mirror of marketdata._parse_levels — what the engine actually sees,
    including its except clause. Note it does NOT catch KeyError, so a dict-shaped
    level (`{"price_dollars": ...}`) RAISES here rather than parsing to empty;
    _strict_or_error() reports that as its own distinct failure mode, because in
    the engine that exception escapes quote_from_kalshi (which sits outside
    LiveMarketData.get_quote's try/except)."""
    out: list[tuple[int, int]] = []
    for lvl in raw or []:  # type: ignore[union-attr]
        try:
            price, qty = int(lvl[0]), int(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 < price < 100 and qty > 0:
            out.append((price, qty))
    out.sort(key=lambda pq: -pq[0])
    return out


def _strict_or_error(raw: object) -> tuple[list[tuple[int, int]], str | None]:
    """Engine parse, plus the exception text if the engine would blow up on it."""
    try:
        return _parse_levels_strict(raw), None
    except Exception as exc:  # noqa: BLE001 — reporting the engine's own failure
        return [], f"{type(exc).__name__}: {exc}"


def _num(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_levels_tolerant(raw: object) -> list[tuple[int, int]]:
    """Accept the shapes the live API might use: [[c,q]], [["0.93","30.0"]] and
    [{"price_dollars":..,"size_fp":..}]. Prices <1 are read as dollars->cents."""
    out: list[tuple[int, int]] = []
    for lvl in raw or []:  # type: ignore[union-attr]
        price = qty = None
        if isinstance(lvl, dict):
            for k in ("price", "price_dollars", "price_cents"):
                if k in lvl:
                    price = _num(lvl[k])
                    break
            for k in ("size", "size_fp", "quantity", "count"):
                if k in lvl:
                    qty = _num(lvl[k])
                    break
        elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            price, qty = _num(lvl[0]), _num(lvl[1])
        if price is None or qty is None:
            continue
        cents = round(price * 100) if 0 < price < 1 else round(price)
        n = int(qty)
        if 0 < cents < 100 and n > 0:
            out.append((cents, n))
    out.sort(key=lambda pq: -pq[0])
    return out


def _taker_levels(side: str, yes_levels, no_levels) -> list[tuple[int, int]]:
    """Mirror of Quote.taker_levels: buying `side` consumes the OPPOSITE side's
    resting bids at a cost of 100 - that bid."""
    opposite = no_levels if side == "yes" else yes_levels
    return [(100 - p, q) for p, q in opposite if 0 < 100 - p < 100]


# --- reporting ---------------------------------------------------------------


def _verdict(order: dict, yes_l, no_l) -> str:
    side, style, action = order["side"], order["style"], order["action"]
    limit = order["limit_price_cents"]
    remaining = int(order["quantity"]) - int(order["filled_quantity"])
    if remaining <= 0:
        return "nothing remaining (already filled)"

    if style == "taker":
        levels = _taker_levels(side, yes_l, no_l) if action == "buy" else (
            yes_l if side == "yes" else no_l)
        if not levels:
            return ("WOULD NOT FILL — no depth levels on the side this order must "
                    "consume. evaluate_order iterates levels and fills nothing when "
                    "the list is empty, at ANY limit price.")
        fillable = 0
        for price, depth in levels:
            if limit is not None:
                if action == "buy" and price > limit:
                    break
                if action == "sell" and price < limit:
                    break
            fillable += min(remaining - fillable, depth)
            if fillable >= remaining:
                break
        best = levels[0][0]
        if fillable <= 0:
            return (f"would NOT fill — best available {best}c vs limit {limit}c "
                    f"({'too expensive' if action == 'buy' else 'too cheap'})")
        return (f"WOULD FILL {fillable}/{remaining} now at best {best}c "
                f"(limit {limit if limit is not None else 'none/market'})")

    # maker: strict trade-through only
    if limit is None:
        return "invalid maker order (no limit price)"
    if action == "buy":
        tl = _taker_levels(side, yes_l, no_l)
        mkt = tl[0][0] if tl else None
        ok = mkt is not None and mkt < limit
        return (f"maker buy rests at {limit}c; market taker cost {mkt}c — "
                + ("WOULD FILL (traded through)" if ok
                   else "no fill (needs to trade strictly BELOW the limit)"))
    bids = yes_l if side == "yes" else no_l
    bid = bids[0][0] if bids else None
    ok = bid is not None and bid > limit
    return (f"maker sell rests at {limit}c; best bid {bid}c — "
            + ("WOULD FILL" if ok else "no fill (needs a bid strictly ABOVE the limit)"))


def _fetch_orders(args: list[str]) -> list[dict]:
    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO")
                        or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return []
    ids = [int(a) for a in args if a.isdigit()]
    tickers = [a for a in args if not a.isdigit()]
    where = "o.status IN ('open','partial')"
    params: list = []
    if ids:
        where = "o.id = ANY(%s)"
        params = [ids]
    elif tickers:
        where = "o.market_ticker = ANY(%s) AND o.status IN ('open','partial')"
        params = [tickers]

    import psycopg

    sql = (
        "SELECT o.id, o.agent_uuid, o.market_ticker, o.side, o.action, o.style, "
        "o.status, o.quantity, o.filled_quantity, o.limit_price_cents, o.created_at "
        f"FROM evo_orders o WHERE {where} ORDER BY o.id DESC LIMIT 40"
    )
    options = ("-c default_transaction_read_only=on -c statement_timeout=30000")
    with psycopg.connect(url, options=options, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql, params or None)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    orders = _fetch_orders(args)
    if not orders:
        print("no matching orders (nothing open?)")
        return 0

    books: dict[str, tuple] = {}
    for o in orders:
        t = o["market_ticker"]
        print(f"\n=== order {o['id']} · {t} · {o['side']}/{o['action']}/{o['style']} "
              f"· qty {o['quantity']} filled {o['filled_quantity']} "
              f"· limit {o['limit_price_cents']} · {o['status']} "
              f"· created {o['created_at']} ===")
        if t not in books:
            mkt = (_get(f"/markets/{t}") or {}).get("market") or {}
            ob_raw = _get(f"/markets/{t}/orderbook?depth={_DEPTH}") or {}
            ob = ob_raw.get("orderbook", ob_raw)
            raw = json.dumps(ob)
            print(f"  market status={mkt.get('status')!r} result={mkt.get('result')!r} "
                  f"yes_bid={mkt.get('yes_bid_dollars') or mkt.get('yes_bid')} "
                  f"yes_ask={mkt.get('yes_ask_dollars') or mkt.get('yes_ask')} "
                  f"no_bid={mkt.get('no_bid_dollars') or mkt.get('no_bid')}")
            print(f"  RAW orderbook: {raw[:_RAW_CAP]}{'…' if len(raw) > _RAW_CAP else ''}")
            y_s, y_err = _strict_or_error((ob or {}).get("yes"))
            n_s, n_err = _strict_or_error((ob or {}).get("no"))
            y_t = _parse_levels_tolerant((ob or {}).get("yes"))
            n_t = _parse_levels_tolerant((ob or {}).get("no"))
            print(f"  ENGINE parse (strict): yes={y_s or '[]'} no={n_s or '[]'}")
            print(f"  tolerant parse:        yes={y_t or '[]'} no={n_t or '[]'}")
            if y_err or n_err:
                print(f"  ** ENGINE RAISES on this orderbook shape: {y_err or n_err} "
                      "— _parse_levels does not catch KeyError, and quote_from_kalshi "
                      "sits OUTSIDE get_quote's try/except, so this propagates.")
            elif not (y_s or n_s) and (y_t or n_t):
                print("  ** PARSER DRIFT: the engine parses NO levels from a book that "
                      "clearly HAS levels — every taker order on this market is "
                      "unfillable at any price until the parser matches the API shape.")
            books[t] = (y_s, n_s, y_t, n_t)
        y_s, n_s, y_t, n_t = books[t]
        print(f"  engine verdict:   {_verdict(o, y_s, n_s)}")
        print(f"  tolerant verdict: {_verdict(o, y_t, n_t)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
