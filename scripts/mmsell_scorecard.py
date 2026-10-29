#!/usr/bin/env python3
"""mmsell scorecard: which live book earns, in which category, and which markets lose.

Rows are per live BOOK (tag), then per CATEGORY (owner decision 2026-10-29: weather, index,
politics, mentions, sports, other). Each book's economics come from its OWN fills
(`fills` x `live_orders` on `kalshi_order_id`), priced at the market's settlement, so two books
that share a market (the WS-027 pair) are each charged only for what they bought — the same
formula as `kalshi_bot/experiment_os/live_attribution.py`:

    pnl(tag, market) = sum over the tag's fills of  s * q * (settle - px) / 100  - fees

with direction from the ORDER (an mmsell maker NO-buy fills as `side=no, action=sell`), the fill
price mapped into the order's side frame, and `count_fp` as the quantity.

Per market:
  * settled -> the newest `positions` snapshot is flat and carries the settlement value;
  * open    -> no P&L yet; shown with its cost.

TWIN GAP: each book's paper twin (`<tag>_pt4`) books its own `paper_trades.pnl`. The gap is live
minus twin cents per contract on the SAME settled markets (the parity script's matched-pair
read, `scripts/live_paper_parity.py`). Tags are fresh at every arming, so a tag never spans two
Experiment OS epochs and nothing here pools across one.

Read-only, stdlib + psycopg. It decides nothing and writes nothing; the last line is one JSON
object so a later review can compare runs (`docs/mmsell/scorecards/`). A recommendation in a
review is not a gate verdict.

    {"type": "script", "name": "mmsell_scorecard", "args": ["--days", "3"]}
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

#: The live books, incumbent first. `Nmmsell10` drains after the 2026-10-27 swap (WS-027).
LIVE_TAGS = ("Kmmsell10", "Vmmsell10", "Nmmsell10")
TWIN_SUFFIX = "_pt4"
#: The liquidity-incentive book also quotes KXTRUMPMENTION; the overlap gets its own section.
LIMM_TAG = "Alimm1"
OVERLAP_SERIES = "KXTRUMPMENTION"
DUST = 0.01
TAXONOMY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                             "kalshi_bot", "mmsell", "market_types.py")

CATEGORIES = ("weather", "index", "politics", "mentions", "sports", "other")
WEATHER_PREFIXES = ("KXRAIN", "KXHIGH", "KXLOW", "KXSNOW")
INDEX_PREFIXES = ("KXINX", "KXNASDAQ", "KXDOW", "KXSPX")
#: `mmsell/market_types.py` types that are games or tournaments.
SPORTS_TYPES = frozenset({"h2h", "h2h_period", "spread", "total", "player_prop", "game_prop",
                          "exact_score", "outright"})


# ------------------------------------------------------------------ pure helpers


def load_series_types(path: str = TAXONOMY_PATH) -> dict[str, str]:
    """`SERIES_TYPES` from the taxonomy's source as {series prefix: type}.

    Read as source text, never imported: the ops runner installs psycopg and nothing else for a
    `script` request. Each row is `(prefix, type, mode)`; the mode is a module constant, so only
    the two string fields are read."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target == "SERIES_TYPES" and isinstance(node.value, ast.Tuple):
            out = {}
            for row in node.value.elts:
                if isinstance(row, ast.Tuple) and len(row.elts) >= 2 \
                        and all(isinstance(e, ast.Constant) for e in row.elts[:2]):
                    out[row.elts[0].value] = row.elts[1].value
            return out
    raise ValueError(f"SERIES_TYPES not found in {path}")


def series_of(ticker: str) -> str:
    return ticker.split("-", 1)[0].upper()


def event_of(ticker: str) -> str:
    """The event a market belongs to: its ticker less the last segment."""
    return ticker.rsplit("-", 1)[0] if ticker.count("-") >= 2 else ticker


def market_type(series: str, types: dict[str, str]) -> str | None:
    """Longest-prefix match, as `market_types.classify` does."""
    best = None
    for prefix in types:
        if series.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return types[best] if best else None


def category(ticker: str, types: dict[str, str]) -> str:
    s = series_of(ticker)
    if s.startswith(WEATHER_PREFIXES):
        return "weather"
    if s.startswith(INDEX_PREFIXES):
        return "index"
    if "MENTION" in s:
        return "mentions"
    t = market_type(s, types)
    if s.startswith("KXTRUMP") or t in ("politics", "mention"):
        return "politics"
    if t in SPORTS_TYPES:
        return "sports"
    return "other"


def leg(order_side: str, order_action: str, fill_side: str, price: int, qty: float,
        fee: float) -> tuple[int, float, int, float]:
    """(sign, quantity, price in the ORDER's side frame, fee) for one fill."""
    px = price if (fill_side or order_side) == order_side else 100 - price
    return (1 if order_action == "buy" else -1), float(qty), int(px), float(fee)


def market_pnl(legs: list[tuple[int, float, int, float]], order_side: str,
               yes_value: float | None) -> float | None:
    """Dollars for one tag on one market, or None while unsettled."""
    if yes_value is None:
        return None
    settle = yes_value if order_side == "yes" else 100.0 - yes_value
    return round(sum(s * q * (settle - px) for s, q, px, _f in legs) / 100.0
                 - sum(f for *_x, f in legs), 4)


def _blank():
    return {"settled": 0, "contracts": 0.0, "pnl": 0.0, "losing": 0, "open": 0,
            "open_cost": 0.0, "recent_settled": 0, "recent_pnl": 0.0}


def _finish(d: dict) -> dict:
    for k in ("contracts", "pnl", "open_cost", "recent_pnl"):
        d[k] = round(d[k], 2)
    d["cents_per_contract"] = round(100.0 * d["pnl"] / d["contracts"], 2) if d["contracts"] else None
    return d


def summarise(fills: list[tuple], settlements: dict[str, dict], twin_rows: list[tuple],
              limm_markets: set[str], types: dict[str, str], *, now: dt.datetime,
              days: int, tags=LIVE_TAGS) -> dict:
    """Everything the report prints, as plain data.

    `fills`: (tag, ticker, order_side, order_action, fill_side, price, qty, fee, time).
    `settlements`: ticker -> {"yes_value": float|None, "open_qty": float, "settled_at": iso}.
    `twin_rows`: (twin tag, ticker, quantity, pnl $) for settled paper trades."""
    since = now - dt.timedelta(days=days)
    per: dict[tuple[str, str], dict] = {}
    for tag, tkr, oside, oact, fside, price, qty, fee, t in fills:
        m = per.setdefault((tag, tkr), {"side": oside, "legs": [], "times": []})
        m["legs"].append(leg(oside, oact, fside, price, qty, fee))
        if t:
            m["times"].append(t)

    markets = {}
    for (tag, tkr), m in per.items():
        st = settlements.get(tkr) or {}
        flat = abs(st.get("open_qty") or 0.0) < DUST
        pnl = market_pnl(m["legs"], m["side"], st.get("yes_value")) if flat else None
        bought = [(q, px) for s, q, px, _f in m["legs"] if s > 0]
        contracts = sum(q for q, _px in bought)
        markets[(tag, tkr)] = {
            "tag": tag, "ticker": tkr, "event": event_of(tkr), "series": series_of(tkr),
            "category": category(tkr, types), "pnl": pnl, "contracts": round(contracts, 2),
            "entry_px": round(sum(q * px for q, px in bought) / contracts, 1) if contracts else None,
            "cost": round(sum(q * px for q, px in bought) / 100.0, 2),
            "fills": len(m["legs"]), "first_fill": min(m["times"]) if m["times"] else None,
            "settled_at": st.get("settled_at") if pnl is not None else None,
        }

    books = {tag: _blank() for tag in tags}
    cells: dict[tuple[str, str], dict] = {}
    for mk in markets.values():
        for d in (books.setdefault(mk["tag"], _blank()),
                  cells.setdefault((mk["tag"], mk["category"]), _blank())):
            if mk["pnl"] is None:
                d["open"] += 1
                d["open_cost"] += mk["cost"]
                continue
            d["settled"] += 1
            d["contracts"] += mk["contracts"]
            d["pnl"] += mk["pnl"]
            d["losing"] += 1 if mk["pnl"] < 0 else 0
            at = mk["settled_at"]
            if at and dt.datetime.fromisoformat(at.replace("Z", "+00:00")) >= since:
                d["recent_settled"] += 1
                d["recent_pnl"] += mk["pnl"]

    twins: dict[str, dict[str, list[float]]] = {}
    for twin, tkr, qty, pnl in twin_rows:
        t = twins.setdefault(twin, {}).setdefault(tkr, [0.0, 0.0])
        t[0] += float(qty or 0)
        t[1] += float(pnl or 0)
    for tag, b in books.items():
        paired = [(mk, twins.get(tag + TWIN_SUFFIX, {}).get(mk["ticker"]))
                  for (tg, _t), mk in markets.items() if tg == tag and mk["pnl"] is not None]
        paired = [(mk, tw) for mk, tw in paired if tw and tw[0] > 0 and mk["contracts"] > 0]
        if paired:
            live_c = 100.0 * sum(mk["pnl"] for mk, _ in paired) / sum(mk["contracts"] for mk, _ in paired)
            twin_c = 100.0 * sum(tw[1] for _, tw in paired) / sum(tw[0] for _, tw in paired)
            b["twin"] = {"pairs": len(paired), "live_cpc": round(live_c, 2),
                         "twin_cpc": round(twin_c, 2), "gap": round(live_c - twin_c, 2)}
        else:
            b["twin"] = {"pairs": 0, "live_cpc": None, "twin_cpc": None, "gap": None}

    overlap = {}
    for mk in markets.values():
        if mk["series"] != OVERLAP_SERIES:
            continue
        o = overlap.setdefault(mk["tag"], {"markets": 0, "settled": 0, "pnl": 0.0, "with_limm": 0})
        o["markets"] += 1
        o["with_limm"] += 1 if mk["ticker"] in limm_markets else 0
        if mk["pnl"] is not None:
            o["settled"] += 1
            o["pnl"] = round(o["pnl"] + mk["pnl"], 2)

    losers = sorted((mk for mk in markets.values() if mk["pnl"] is not None and mk["pnl"] < 0),
                    key=lambda mk: (mk["pnl"], mk["ticker"]))
    return {"as_of": now.isoformat(timespec="minutes"), "days": days,
            "books": {t: _finish(b) for t, b in books.items()},
            "cells": {k: _finish(v) for k, v in cells.items()},
            "overlap": overlap, "losers": losers, "markets": markets}


def _hours(a: str | None, b: str | None) -> float | None:
    if not (a and b):
        return None
    x, y = (dt.datetime.fromisoformat(v.replace("Z", "+00:00")) for v in (a, b))
    return round((y - x).total_seconds() / 3600, 1)


def _money(v) -> str:
    return "     -" if v is None else f"{v:>6.2f}"


def render(s: dict, *, max_losers: int = 25) -> str:
    out = [f"MMSELL scorecard  as of {s['as_of']}  (live books, own fills, settled markets)", ""]
    head = (f"  {'book / category':<22}{'settled':>8}{'contr':>8}{'P&L $':>9}{'c/ct':>7}"
            f"{'losing':>7}{'open':>6}{'open $':>8}   last {s['days']}d: {'n':>4}{'P&L $':>8}")

    def line(name, d):
        cpc = "     -" if d["cents_per_contract"] is None else f"{d['cents_per_contract']:>6.2f}"
        return (f"  {name:<22}{d['settled']:>8}{d['contracts']:>8.1f}{d['pnl']:>9.2f} {cpc}"
                f"{d['losing']:>7}{d['open']:>6}{d['open_cost']:>8.2f}   "
                f"{'':>9}{d['recent_settled']:>4}{d['recent_pnl']:>8.2f}")
    out.append("BY BOOK, THEN CATEGORY (lifetime of the tag = its epoch)")
    out.append(head)
    for tag, b in s["books"].items():
        out.append(line(tag, b))
        for cat in CATEGORIES:
            d = s["cells"].get((tag, cat))
            if d and (d["settled"] or d["open"]):
                out.append(line("  " + cat, d))
        tw = b["twin"]
        if tw["pairs"]:
            out.append(f"    twin {tag}{TWIN_SUFFIX}: {tw['pairs']} same-market pairs, live "
                       f"{tw['live_cpc']:+.2f}c/ct vs twin {tw['twin_cpc']:+.2f}c/ct, "
                       f"gap {tw['gap']:+.2f}c")
        else:
            out.append(f"    twin {tag}{TWIN_SUFFIX}: no settled same-market pairs yet")
    out.append("")
    out.append(f"{OVERLAP_SERIES} (also quoted by {LIMM_TAG} for rewards)")
    if s["overlap"]:
        for tag, o in s["overlap"].items():
            out.append(f"  {tag:<12} markets {o['markets']:>3}  settled {o['settled']:>3}  "
                       f"P&L {o['pnl']:>6.2f}  also traded by {LIMM_TAG}: {o['with_limm']}")
    else:
        out.append("  (none yet)")
    out.append("")
    out.append(f"LOSING MARKETS (worst {max_losers}): entry price, fills, hours from "
               "first fill to settlement")
    for mk in s["losers"][:max_losers]:
        hrs = _hours(mk["first_fill"], mk["settled_at"])
        out.append(f"  {mk['ticker']:<46} {mk['tag']:<11}{mk['category']:<10}{mk['pnl']:>7.2f}"
                   f"  {mk['contracts']:>4.1f} @ {mk['entry_px'] or 0:>4.1f}c  {mk['fills']} fills"
                   + (f"  {hrs}h" if hrs is not None else ""))
    if not s["losers"]:
        out.append("  (none)")
    out.append("")
    summary = {"as_of": s["as_of"], "days": s["days"],
               "books": {t: {k: b[k] for k in ("settled", "contracts", "pnl",
                                                "cents_per_contract", "losing", "open",
                                                "recent_settled", "recent_pnl", "twin")}
                         for t, b in s["books"].items()},
               "categories": {f"{t}/{c}": {"settled": d["settled"], "pnl": d["pnl"],
                                           "recent_pnl": d["recent_pnl"]}
                              for (t, c), d in s["cells"].items()},
               "overlap": s["overlap"], "loss_markets": len(s["losers"]),
               "avg_loss": round(sum(m["pnl"] for m in s["losers"]) / len(s["losers"]), 2)
               if s["losers"] else 0.0}
    out.append("JSON " + json.dumps(summary, sort_keys=True))
    return "\n".join(out)


def as_rows(s: dict) -> dict:
    """Flat row lists for the dashboard datasets: by_tag, by_book, by_event, losers, overlap."""
    by_tag = [{"book": t, "category": c, **{k: d[k] for k in (
        "settled", "contracts", "pnl", "cents_per_contract", "losing", "open", "open_cost",
        "recent_settled", "recent_pnl")}} for (t, c), d in sorted(s["cells"].items())]
    by_book = [{"book": t, **{k: b[k] for k in (
        "settled", "contracts", "pnl", "cents_per_contract", "losing", "open", "open_cost",
        "recent_settled", "recent_pnl")}, "twin_pairs": b["twin"]["pairs"],
        "twin_gap_cents": b["twin"]["gap"]} for t, b in s["books"].items()]
    since = dt.datetime.fromisoformat(s["as_of"]) - dt.timedelta(days=s["days"])
    events: dict[tuple[str, str], dict] = {}
    for mk in s["markets"].values():
        at = mk["settled_at"]
        if mk["pnl"] is None or not at \
                or dt.datetime.fromisoformat(at.replace("Z", "+00:00")) < since:
            continue
        e = events.setdefault((mk["tag"], mk["event"]), {
            "book": mk["tag"], "event": mk["event"], "category": mk["category"],
            "markets": 0, "contracts": 0.0, "pnl": 0.0, "losing": 0})
        e["markets"] += 1
        e["contracts"] = round(e["contracts"] + mk["contracts"], 2)
        e["pnl"] = round(e["pnl"] + mk["pnl"], 2)
        e["losing"] += 1 if mk["pnl"] < 0 else 0
    losers = [{"market": mk["ticker"], "book": mk["tag"], "category": mk["category"],
               "pnl": round(mk["pnl"], 2), "contracts": mk["contracts"],
               "entry_px": mk["entry_px"], "fills": mk["fills"],
               "hours_to_settle": _hours(mk["first_fill"], mk["settled_at"]),
               "settled_at": mk["settled_at"]} for mk in s["losers"]]
    overlap = [{"book": t, **o} for t, o in s["overlap"].items()]
    return {"as_of": s["as_of"], "days": s["days"], "by_tag": by_tag, "by_book": by_book,
            "by_event": sorted(events.values(), key=lambda e: e["pnl"]),
            "losers": losers, "overlap": overlap}


# ------------------------------------------------------------------ database


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def fetch(cur, tags):
    cur.execute(
        """SELECT DISTINCT ON (f.id) o.strategy, f.market_ticker, o.side, o.action, f.side,
                  f.price, COALESCE((f.raw_fill_json->>'count_fp')::numeric, f.quantity),
                  COALESCE(f.fee, 0),
                  COALESCE(f.raw_fill_json->>'created_time', f.filled_at::text)
           FROM fills f JOIN live_orders o ON o.kalshi_order_id = f.kalshi_order_id
           WHERE o.strategy = ANY(%s)""", (list(tags),))
    fills = [(tag, tkr, osd, oact, fsd, int(px or 0), float(q or 0), float(fee or 0), t)
             for tag, tkr, osd, oact, fsd, px, q, fee, t in cur.fetchall()]
    tickers = sorted({f[1] for f in fills})
    settlements = {}
    if tickers:
        cur.execute(
            """SELECT DISTINCT ON (market_ticker) market_ticker,
                      COALESCE(quantity_fp, quantity), raw_json->>'value',
                      raw_json->>'market_result',
                      COALESCE(raw_json->>'settled_time', captured_at::text)
               FROM positions WHERE market_ticker = ANY(%s)
               ORDER BY market_ticker, captured_at DESC, id DESC""", (tickers,))
        for tkr, qty, value, result, at in cur.fetchall():
            yes = None
            try:
                yes = float(value) if value is not None else None
            except ValueError:
                yes = None
            if yes is None and (result or "").lower() in ("yes", "no"):
                yes = 100.0 if result.lower() == "yes" else 0.0
            settlements[tkr] = {"yes_value": yes, "open_qty": float(qty or 0), "settled_at": at}
    cur.execute(
        """SELECT strategy, market_ticker, quantity, pnl FROM paper_trades
           WHERE strategy = ANY(%s) AND pnl IS NOT NULL""",
        ([t + TWIN_SUFFIX for t in tags],))
    twin_rows = cur.fetchall()
    limm = set()
    if any(t[1].startswith(OVERLAP_SERIES + "-") for t in fills):
        cur.execute(
            """SELECT DISTINCT market_ticker FROM live_orders
               WHERE strategy = %s AND market_ticker LIKE %s""",
            (LIMM_TAG, OVERLAP_SERIES + "-%"))
        limm = {r[0] for r in cur.fetchall()}
    return fills, settlements, twin_rows, limm


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="mmsell scorecard (live books).")
    ap.add_argument("--days", type=int, default=3, help="recent window in days (default 3)")
    ap.add_argument("--tags", default=",".join(LIVE_TAGS),
                    help="comma-separated live tags (default: the current books)")
    ap.add_argument("--json", action="store_true",
                    help="print the full data as one JSON object (for the dashboard)")
    args = ap.parse_args(argv)
    tags = tuple(t.strip() for t in args.tags.split(",") if t.strip())
    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    import psycopg

    types = load_series_types()
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            fills, settlements, twin_rows, limm = fetch(cur, tags)
    s = summarise(fills, settlements, twin_rows, limm, types,
                  now=dt.datetime.now(dt.timezone.utc), days=args.days, tags=tags)
    print(json.dumps(as_rows(s), sort_keys=True, default=str) if args.json else render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
