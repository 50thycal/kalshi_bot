"""Discretionary desk board — the daily read of Kalshi's open markets for a HUMAN picker.

The Claude sandbox cannot reach the Kalshi API (egress policy), so the desk's board read
runs here, on the ops runner, against Kalshi's PUBLIC market-data endpoints (no key, no
account, no order surface). Stdlib only. Read-only by construction: every call is a GET.

Three modes:

  scan (default)  every open market closing within --hours, filtered by --min-volume /
                  --category / --series / --search, ranked by 24h volume. One line per
                  market: ticker, close-in, yes bid/ask, spread, taker fee at the ask,
                  volume, open interest, title. This is the candidate list the desk
                  narrows by hand.
  --ticker T      one market in depth: the full rules text, the resting book (top
                  levels, both sides), the last trades, and the break-even win rate at
                  the current ask. Read this BEFORE a pick, never after.
  --event E       every market of one event (ladders, multi-outcome), same columns.

Usage (via the ops channel):
    {"type":"script","name":"kalshi_desk_board","args":["--hours","72","--min-volume","500"]}
    {"type":"script","name":"kalshi_desk_board","args":["--ticker","KXHIGHNY-26SEP20-B75"]}
    {"type":"script","name":"kalshi_desk_board","args":["--search","fed","--hours","720"]}

Fee model: Kalshi's taker fee is ceil(0.07 x qty x P x (1-P)) dollars per order, rounded
UP to the cent (docs/MMSELL_FEE_RECON.md). Maker fills on standard markets bill ~0.
Both are shown so the desk sizes the cost floor before it sizes the trade.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Output is public (the ops branch) and bounded: never print more than this many rows.
MAX_ROWS = 150


# ---------------------------------------------------------------------------
# HTTP (fixed public host, retried, no credentials)
# ---------------------------------------------------------------------------

def _get(path: str, params: dict | None = None) -> dict:
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}{path}?{qs}" if qs else f"{BASE}{path}"
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310 (fixed host)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            body = exc.read().decode("utf-8", "replace")[:200]
            print(f"  HTTP {exc.code} for {path}: {body}", file=sys.stderr)
            return {}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            print(f"  error for {path}: {exc}", file=sys.stderr)
            return {}
    return {}


def _paginate(path: str, key: str, params: dict, max_pages: int):
    cursor = ""
    for _ in range(max_pages):
        page = _get(path, {**params, "cursor": cursor})
        rows = page.get(key) or []
        yield from rows
        cursor = page.get("cursor") or ""
        if not cursor or not rows:
            return


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested; no network)
# ---------------------------------------------------------------------------

def cents(market: dict, field: str) -> int | None:
    """A price in whole cents from either the integer-cent field or the *_dollars string.

    Kalshi's v2 payload carries both spellings; the integer form is being retired in
    favour of fixed-point dollar strings, so prefer the dollar form when present.
    """
    dollars = market.get(f"{field}_dollars")
    if dollars not in (None, ""):
        try:
            return int(round(float(dollars) * 100))
        except (TypeError, ValueError):
            pass
    raw = market.get(field)
    if raw in (None, ""):
        return None
    try:
        return int(round(float(raw)))
    except (TypeError, ValueError):
        return None


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def count(obj: dict, field: str) -> int:
    """A contract count from either the fixed-point `<field>_fp` string or the legacy integer.

    Kalshi's v2 payload moved `volume`, `volume_24h`, `open_interest` and trade `count` to
    `*_fp` strings ('1234.0000'); the bare field is absent on current payloads, so a reader
    that only knows the old name silently sees 0 (the desk board's first run kept 0 of
    8,000 events for exactly that reason).
    """
    fp = obj.get(f"{field}_fp")
    if fp not in (None, ""):
        return int(_num(fp))
    return int(_num(obj.get(field)))


def taker_fee_cents(price_c: int, qty: int = 1) -> float:
    """Kalshi taker fee for one ORDER of `qty` contracts at `price_c`, in cents, rounded up."""
    if price_c is None or not 0 < price_c < 100 or qty <= 0:
        return 0.0
    p = price_c / 100.0
    dollars = 0.07 * qty * p * (1.0 - p)
    return math.ceil(dollars * 100 - 1e-9)  # cents, ceil'd


def breakeven_win_pct(ask_c: int, qty: int = 1) -> float | None:
    """Win rate needed to break even buying at `ask_c` as a taker, fee included, held to settle."""
    if ask_c is None or not 0 < ask_c < 100:
        return None
    fee = taker_fee_cents(ask_c, qty) / qty
    return float(ask_c + fee)  # payout is 100c, so P(win) in % equals the all-in cost in cents


def hours_to(close_iso: str | None, now: datetime | None = None) -> float | None:
    if not close_iso:
        return None
    try:
        dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    return (dt - now).total_seconds() / 3600.0


def series_of(event_ticker: str) -> str:
    return (event_ticker or "").split("-", 1)[0] or "?"


def row_of(market: dict, event: dict | None = None, now: datetime | None = None) -> dict:
    """Flatten one market (+ its event) into the columns the desk reads."""
    event = event or {}
    yes_bid = cents(market, "yes_bid")
    yes_ask = cents(market, "yes_ask")
    no_bid = cents(market, "no_bid")
    no_ask = cents(market, "no_ask")
    if yes_ask is None and no_bid is not None:
        yes_ask = 100 - no_bid
    if no_ask is None and yes_bid is not None:
        no_ask = 100 - yes_bid
    spread = (yes_ask - yes_bid) if (yes_ask is not None and yes_bid is not None) else None
    ev_ticker = market.get("event_ticker") or event.get("event_ticker") or ""
    return {
        "ticker": market.get("ticker") or "",
        "event": ev_ticker,
        "series": series_of(ev_ticker),
        "category": event.get("category") or market.get("category") or "",
        "title": (event.get("title") or market.get("title") or "").strip(),
        "sub": (market.get("yes_sub_title") or market.get("subtitle") or "").strip(),
        "yes_bid": yes_bid, "yes_ask": yes_ask, "no_bid": no_bid, "no_ask": no_ask,
        "spread": spread,
        "last": cents(market, "last_price"),
        "vol": count(market, "volume"),
        "vol24": count(market, "volume_24h"),
        "oi": count(market, "open_interest"),
        "close": market.get("close_time") or "",
        "htc": hours_to(market.get("close_time"), now),
        "fee_yes": taker_fee_cents(yes_ask) if yes_ask else None,
        "fee_no": taker_fee_cents(no_ask) if no_ask else None,
        "strike_type": market.get("strike_type") or "",
        "status": market.get("status") or "",
    }


def keep(row: dict, *, hours: float, min_volume: int, category: str, series: str,
         search: str, max_spread: int | None) -> bool:
    if row["htc"] is not None and (row["htc"] < 0 or row["htc"] > hours):
        return False
    if max(row["vol"], row["vol24"]) < min_volume:
        return False
    if category and category.lower() not in row["category"].lower():
        return False
    if series and not row["series"].upper().startswith(series.upper()):
        return False
    if search:
        hay = f"{row['title']} {row['sub']} {row['ticker']}".lower()
        if search.lower() not in hay:
            return False
    if max_spread is not None and (row["spread"] is None or row["spread"] > max_spread):
        return False
    return True


def _fmt_c(v) -> str:
    return "  -" if v is None else f"{v:3d}"


def format_row(row: dict) -> str:
    htc = row["htc"]
    htc_s = "   -" if htc is None else (f"{htc:4.0f}h" if htc < 96 else f"{htc / 24:3.0f}d ")
    title = row["title"]
    if row["sub"] and row["sub"] not in title:
        title = f"{title} | {row['sub']}"
    title = title[:70]
    return (f"{row['ticker'][:32]:<32} {htc_s:>5} "
            f"{_fmt_c(row['yes_bid'])}/{_fmt_c(row['yes_ask'])} sp{_fmt_c(row['spread'])} "
            f"f{_fmt_c(row['fee_yes'])} v{row['vol24']:>7d} oi{row['oi']:>7d} "
            f"{row['category'][:14]:<14} {title}")


HEADER = (f"{'ticker':<32} {'close':>5} {'yes b/a':>7} {'spr':>5} {'fee':>4} {'vol24':>8} "
          f"{'oi':>9} {'category':<14} title")


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def scan(args) -> int:
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    events = 0
    for ev in _paginate("/events", "events",
                        {"status": "open", "with_nested_markets": "true", "limit": 200},
                        args.max_pages):
        events += 1
        for mk in ev.get("markets") or []:
            if (mk.get("status") or "open") not in ("open", "active"):
                continue
            row = row_of(mk, ev, now)
            if keep(row, hours=args.hours, min_volume=args.min_volume, category=args.category,
                    series=args.series, search=args.search, max_spread=args.max_spread):
                rows.append(row)
    rows.sort(key=lambda r: (r["vol24"], r["vol"]), reverse=True)
    shown = rows[: min(args.top, MAX_ROWS)]
    print(f"# desk board — {now.isoformat(timespec='seconds')} — events scanned {events}, "
          f"markets kept {len(rows)}, shown {len(shown)}")
    print(f"# filters: hours<={args.hours} min_volume>={args.min_volume} "
          f"category~'{args.category}' series^'{args.series}' search~'{args.search}' "
          f"max_spread={args.max_spread}")
    print("# prices in cents; 'fee' = taker fee (cents) for ONE contract bought at the yes ask;")
    print("# a maker order pays ~0. break-even win% = (ask + fee) for YES, (no_ask + fee) for NO.")
    print(HEADER)
    for r in shown:
        print(format_row(r))
    if not shown:
        print("(nothing matched — widen --hours or lower --min-volume)")
    # category rollup so the desk sees WHERE the volume is today
    by_cat: dict[str, list[int]] = {}
    for r in rows:
        c = by_cat.setdefault(r["category"] or "?", [0, 0])
        c[0] += 1
        c[1] += r["vol24"]
    print("\n# by category (kept markets, 24h volume)")
    for cat, (n, v) in sorted(by_cat.items(), key=lambda kv: kv[1][1], reverse=True)[:20]:
        print(f"  {cat[:28]:<28} n={n:<5d} vol24={v}")
    return 0


def _book_levels(book: dict, side: str, depth: int) -> list[tuple[int, int]]:
    # `yes`/`no` carry [cents, count]; newer payloads add `yes_dollars`/`no_dollars` with
    # ["0.4100", "7.0000"] pairs. Prefer the dollar spelling when present (same rule as `cents`).
    levels = (book or {}).get(f"{side}_dollars") or (book or {}).get(side) or []
    out: list[tuple[int, int]] = []
    for lv in levels:
        try:
            price, qty = lv[0], lv[1]
            if isinstance(price, str):
                price = float(price) * (100 if "." in price else 1)
            out.append((int(round(float(price))), int(round(float(qty)))))
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda pq: pq[0], reverse=True)
    return out[:depth]


def detail(args) -> int:
    now = datetime.now(timezone.utc)
    data = _get(f"/markets/{args.ticker}")
    mk = data.get("market") or data
    if not mk:
        print(f"market {args.ticker!r} not found")
        return 1
    ev_data = _get(f"/events/{mk.get('event_ticker')}") if mk.get("event_ticker") else {}
    ev = ev_data.get("event") or ev_data or {}
    row = row_of(mk, ev, now)
    print(f"# market {row['ticker']} — {now.isoformat(timespec='seconds')}")
    print(f"  title      {row['title']}")
    if row["sub"]:
        print(f"  outcome    {row['sub']}")
    print(f"  category   {row['category']}   series {row['series']}   event {row['event']}")
    htc_s = "-" if row["htc"] is None else f"{row['htc']:.1f}h"
    print(f"  status     {row['status']}   close {row['close']}   ({htc_s})")
    print(f"  expiration {mk.get('expiration_time') or '-'}   strike {row['strike_type'] or '-'} "
          f"floor={mk.get('floor_strike')} cap={mk.get('cap_strike')}")
    print(f"  yes bid/ask {_fmt_c(row['yes_bid'])}/{_fmt_c(row['yes_ask'])}   "
          f"no bid/ask {_fmt_c(row['no_bid'])}/{_fmt_c(row['no_ask'])}   "
          f"spread {_fmt_c(row['spread'])}   last {_fmt_c(row['last'])}")
    print(f"  volume {row['vol']}   vol24 {row['vol24']}   open interest {row['oi']}   "
          f"liquidity {mk.get('liquidity') or mk.get('liquidity_dollars') or '-'}")
    for side, ask in (("YES", row["yes_ask"]), ("NO", row["no_ask"])):
        if ask:
            be = breakeven_win_pct(ask)
            print(f"  buy {side:<3} @ {ask:3d}c  taker fee {taker_fee_cents(ask):.0f}c  "
                  f"break-even win {be:.1f}%   (maker at bid: fee ~0)")
    print("\n  RULES (primary):")
    print("   ", (mk.get("rules_primary") or "-").strip().replace("\n", "\n    "))
    if mk.get("rules_secondary"):
        print("  RULES (secondary):")
        print("   ", mk["rules_secondary"].strip().replace("\n", "\n    "))

    book = _get(f"/markets/{args.ticker}/orderbook", {"depth": args.depth}).get("orderbook") or {}
    yes_levels = _book_levels(book, "yes", args.depth)
    no_levels = _book_levels(book, "no", args.depth)
    print(f"\n  BOOK (resting bids; a NO bid at p is a YES offer at 100-p)  depth {args.depth}")
    print("    YES bids           NO bids")
    for i in range(max(len(yes_levels), len(no_levels))):
        y = f"{yes_levels[i][0]:3d}c x{yes_levels[i][1]:<6d}" if i < len(yes_levels) else " " * 12
        n = f"{no_levels[i][0]:3d}c x{no_levels[i][1]:<6d}" if i < len(no_levels) else ""
        print(f"    {y}      {n}")

    trades = _get("/markets/trades", {"ticker": args.ticker, "limit": args.trades}).get("trades") or []
    print(f"\n  LAST {len(trades)} TRADES (newest first)")
    for t in trades:
        yp = cents(t, "yes_price")
        print(f"    {str(t.get('created_time') or '')[:19]}  yes {_fmt_c(yp)}c  "
              f"x{count(t, 'count'):<5d} taker={t.get('taker_side') or '-'}")
    return 0


def event_view(args) -> int:
    now = datetime.now(timezone.utc)
    data = _get(f"/events/{args.event}", {"with_nested_markets": "true"})
    ev = data.get("event") or data
    markets = ev.get("markets") or data.get("markets") or []
    if not markets:
        print(f"event {args.event!r} not found or has no markets")
        return 1
    print(f"# event {args.event} — {ev.get('title') or ''} — category {ev.get('category') or ''}")
    print(f"# {len(markets)} markets; sum of yes asks = "
          f"{sum((cents(m, 'yes_ask') or 0) for m in markets)}c (overround check on ladders)")
    print(HEADER)
    rows = [row_of(m, ev, now) for m in markets]
    rows.sort(key=lambda r: (r["yes_ask"] if r["yes_ask"] is not None else 999))
    for r in rows[:MAX_ROWS]:
        print(format_row(r))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticker", default="", help="one market in depth (rules, book, trades)")
    ap.add_argument("--event", default="", help="every market of one event")
    ap.add_argument("--hours", type=float, default=96.0, help="scan: keep markets closing within N hours")
    ap.add_argument("--min-volume", type=int, default=200, help="scan: min(volume, volume_24h) floor")
    ap.add_argument("--category", default="", help="scan: substring match on event category")
    ap.add_argument("--series", default="", help="scan: series prefix (e.g. KXHIGH)")
    ap.add_argument("--search", default="", help="scan: substring on title/outcome/ticker")
    ap.add_argument("--max-spread", type=int, default=None, help="scan: drop markets wider than N cents")
    ap.add_argument("--top", type=int, default=80, help=f"scan: rows to print (cap {MAX_ROWS})")
    ap.add_argument("--max-pages", type=int, default=40, help="scan: pagination cap on /events")
    ap.add_argument("--depth", type=int, default=6, help="detail: book levels per side")
    ap.add_argument("--trades", type=int, default=20, help="detail: recent trades to print")
    args = ap.parse_args(argv)
    if args.ticker:
        return detail(args)
    if args.event:
        return event_view(args)
    return scan(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
