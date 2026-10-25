#!/usr/bin/env python3
"""Liquidity-incentive scorecard: which market types and events earn, and which lose.

Rewards come from the operator-confirmed payout list (`CONFIRMED_REWARD_PAYOUTS` in
`kalshi_bot/liquidity_incentive/reward_ledger.py`, read as source text, never imported: the
ops runner installs psycopg and nothing else for a `script` request). Fill P&L comes from the
book's own fills and Kalshi's settlement rows. Each is attached to its EVENT (the market ticker
less its last segment), so a reward and the fills that cost it sit on one line.

Per market:
  * settled  -> Kalshi's realized P&L for the market;
  * closed   -> matched pairs pay $1, less what every fill cost, less fees (no settlement yet,
                position flat);
  * open     -> no P&L yet; shown with its cost.

Read-only, stdlib + psycopg. It decides nothing and writes nothing; the last line is one JSON
object so a later review can compare runs (`docs/liquidity_incentive/scorecards/`).

    {"type": "script", "name": "limm_scorecard", "args": ["--days", "7"]}
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import re
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

LIVE_TAG = "Alimm1"
LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                           "kalshi_bot", "liquidity_incentive", "reward_ledger.py")
#: Market types the book quotes today (thesis §9.55, §9.69); others show only if they moved money.
CURRENT_UNIVERSE = frozenset({"mention_event", "youtube_views"})
#: Orders that only close a held position — never an entry bid.
CLOSING_PREFIXES = ("limmexit", "limmsell:")
_DATED_EVENT = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})$")
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


# ------------------------------------------------------------------ pure helpers


def load_payouts(path: str = LEDGER_PATH) -> list[tuple[str, int, str]]:
    """`CONFIRMED_REWARD_PAYOUTS` from the ledger's source: [(iso date, cents, event), ...]."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target == "CONFIRMED_REWARD_PAYOUTS" and node.value is not None:
            return [tuple(x) for x in ast.literal_eval(node.value)]
    raise ValueError(f"CONFIRMED_REWARD_PAYOUTS not found in {path}")


def event_of(ticker: str) -> str:
    """The event a market belongs to: its ticker less the last segment."""
    return ticker.rsplit("-", 1)[0] if ticker.count("-") >= 2 else ticker


def event_date(event: str) -> dt.date | None:
    m = _DATED_EVENT.search(event.upper())
    if not m or m.group(2) not in _MONTHS:
        return None
    a, mon, b = int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))
    # Most series write YYMMMDD (26OCT27); a few write DDMMMYY (28SEP26). Take the reading
    # whose year is plausible for this book.
    for yy, dd in ((a, b), (b, a)):
        if 24 <= yy <= 27:
            try:
                return dt.date(2000 + yy, mon, dd)
            except ValueError:
                continue
    return None


def category(ticker_or_event: str) -> str:
    """The market type, from the series (the ticker's first segment)."""
    series = ticker_or_event.split("-", 1)[0].upper()
    if series.startswith("KXYTVIEWS"):
        return "youtube_views"
    if "MENTION" in series:
        return "mention_event" if event_date(event_of(ticker_or_event)) or \
            event_date(ticker_or_event) else "mention_window"
    if series.startswith("KXTRUMPSAY"):
        return "mention_window"
    if series.endswith("VREQ") or series.startswith("KXTOKENUSE"):
        return "ai_share"
    return "moving_number"


def market_pnl(fills: list[tuple[str, int, float, float]], settled_pnl: float | None,
               open_qty: float | None) -> tuple[str, float | None, float]:
    """(status, P&L in dollars or None, cost in dollars) for one market.

    `fills` are (side, price cents, quantity, fee $). Every Kalshi fill ACQUIRES `side` at
    `price` — a sell of YES is booked as a NO acquisition — so a matched YES+NO pair is worth
    exactly $1 at settlement."""
    cost = sum(p * q for _s, p, q, _f in fills) / 100.0
    fees = sum(f for *_x, f in fills)
    if settled_pnl is not None:
        return "settled", float(settled_pnl), cost
    yq = sum(q for s, _p, q, _f in fills if s == "yes")
    nq = sum(q for s, _p, q, _f in fills if s == "no")
    if open_qty is not None and abs(open_qty) >= 1.0:
        return "open", None, cost
    return "closed", round(min(yq, nq) - cost - fees, 4), cost


def summarise(payouts, fills_by_market, settled, positions, bids_by_event, *, now: dt.datetime,
              days: int) -> dict:
    """Everything the report prints, as plain data."""
    since = (now - dt.timedelta(days=days)).date()
    markets = {}
    for tkr, fl in fills_by_market.items():
        status, pnl, cost = market_pnl([(s, p, q, f) for s, p, q, f, _t in fl],
                                       settled.get(tkr), positions.get(tkr))
        times = sorted(t for *_x, t in fl if t)
        markets[tkr] = {"event": event_of(tkr), "category": category(tkr), "status": status,
                        "pnl": pnl, "cost": round(cost, 2), "fills": len(fl),
                        "first_fill": times[0] if times else None,
                        "last_fill": times[-1] if times else None}
    events: dict[str, dict] = {}

    def ev(e):
        return events.setdefault(e, {"category": category(e), "date": event_date(e),
                                     "rewards": 0.0, "fill_pnl": 0.0, "open_cost": 0.0,
                                     "loss_markets": [], "bids": 0, "payouts": 0})
    for iso, cents, e in payouts:
        row = ev(e)
        row["rewards"] += cents / 100.0
        row["payouts"] += 1
        row["last_payout"] = max(row.get("last_payout") or iso, iso)
    for tkr, mk in markets.items():
        row = ev(mk["event"])
        if mk["pnl"] is None:
            row["open_cost"] += mk["cost"]
        else:
            row["fill_pnl"] += mk["pnl"]
            if mk["pnl"] <= -1.0:
                row["loss_markets"].append((tkr, round(mk["pnl"], 2)))
    for e, n in bids_by_event.items():
        ev(e)["bids"] += n

    cats: dict[str, dict] = {}
    recent: dict[str, dict] = {}
    for row in events.values():
        c = cats.setdefault(row["category"], {"rewards": 0.0, "fill_pnl": 0.0, "events": 0,
                                              "events_with_loss": 0, "losses": 0})
        c["rewards"] += row["rewards"]
        c["fill_pnl"] += row["fill_pnl"]
        c["events"] += 1
        c["losses"] += len(row["loss_markets"])
        c["events_with_loss"] += 1 if row["loss_markets"] else 0
    for iso, cents, e in payouts:
        if dt.date.fromisoformat(iso) >= since:
            r = recent.setdefault(category(e), {"rewards": 0.0, "fill_pnl": 0.0})
            r["rewards"] += cents / 100.0
    for mk in markets.values():
        last = mk["last_fill"]
        if mk["pnl"] is not None and last and dt.date.fromisoformat(last[:10]) >= since:
            r = recent.setdefault(mk["category"], {"rewards": 0.0, "fill_pnl": 0.0})
            r["fill_pnl"] += mk["pnl"]
    for d in list(cats.values()) + list(recent.values()):
        for k in ("rewards", "fill_pnl"):
            d[k] = round(d[k], 2)
        d["net"] = round(d["rewards"] + d["fill_pnl"], 2)
    losers = sorted(((t, m) for t, m in markets.items() if m["pnl"] is not None
                     and m["pnl"] <= -1.0), key=lambda x: x[1]["pnl"])
    return {"as_of": now.isoformat(timespec="minutes"), "days": days, "categories": cats,
            "recent": recent, "events": events, "markets": markets, "losers": losers}


def render(s: dict) -> str:
    out = [f"LIMM scorecard  as of {s['as_of']}  (rewards = operator-confirmed payouts)", ""]

    def cat_table(title, rows, with_counts):
        out.append(title)
        out.append(f"  {'type':<16}{'rewards':>9}{'fills':>10}{'net':>9}"
                   + (f"{'events':>8}{'w/loss':>8}" if with_counts else ""))
        for name, d in sorted(rows.items(), key=lambda kv: -kv[1]["net"]):
            line = f"  {name:<16}{d['rewards']:>9.2f}{d['fill_pnl']:>10.2f}{d['net']:>9.2f}"
            if with_counts:
                line += f"{d['events']:>8}{d['events_with_loss']:>8}"
            out.append(line)
        tot = {k: round(sum(d[k] for d in rows.values()), 2)
               for k in ("rewards", "fill_pnl", "net")}
        out.append(f"  {'TOTAL':<16}{tot['rewards']:>9.2f}{tot['fill_pnl']:>10.2f}"
                   f"{tot['net']:>9.2f}")
        out.append("")
    cat_table("BY MARKET TYPE (lifetime)", s["categories"], True)
    cat_table(f"BY MARKET TYPE (last {s['days']} days: payouts dated / fills closed)",
              s["recent"], False)

    out.append("BY EVENT (newest 30 that earned, lost or still hold money, plus current-universe "
               "events quoted; rewards / closed fill P&L / net)")

    def shown(r):
        moved = r["rewards"] or r["fill_pnl"] or r["open_cost"]
        return bool(moved) or (r["bids"] and r["category"] in CURRENT_UNIVERSE)

    def newest(kv):
        r = kv[1]
        last = r.get("last_payout")
        return (r["date"] or (dt.date.fromisoformat(last) if last else dt.date.min), kv[0])
    rows = sorted(((e, r) for e, r in s["events"].items() if shown(r)),
                  key=newest, reverse=True)[:30]
    for e, r in rows:
        flag = ""
        if r["date"] and r["payouts"] == 0 and (r["bids"] or r["fill_pnl"]):
            flag = "  [no payout recorded]"
        if r["open_cost"]:
            flag += f"  [open ${r['open_cost']:.2f}]"
        losses = ", ".join(f"{t.rsplit('-', 1)[-1]} {p:+.2f}" for t, p in r["loss_markets"])
        out.append(f"  {e:<34}{r['category'][:13]:<14}{r['rewards']:>7.2f}"
                   f"{r['fill_pnl']:>8.2f}{r['rewards'] + r['fill_pnl']:>8.2f}  orders {r['bids']}"
                   + (f"  losers: {losses}" if losses else "") + flag)
    out.append("")
    out.append("LOSING MARKETS (≤ -$1): how they filled (lead < 0 = first fill on the event day)")
    for t, m in s["losers"]:
        span = ""
        if m["first_fill"] and m["last_fill"]:
            a = dt.datetime.fromisoformat(m["first_fill"].replace("Z", "+00:00"))
            b = dt.datetime.fromisoformat(m["last_fill"].replace("Z", "+00:00"))
            span = f"{(b - a).total_seconds() / 3600:.1f}h"
        d = event_date(m["event"])
        lead = ""
        if d and m["first_fill"]:
            a = dt.datetime.fromisoformat(m["first_fill"].replace("Z", "+00:00"))
            lead = f"{(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc) - a).total_seconds() / 3600:.0f}h before event day"
        out.append(f"  {t:<36}{m['pnl']:>8.2f}  {m['fills']} fills over {span}  {lead}")
    out.append("")
    summary = {"as_of": s["as_of"], "categories": s["categories"], "recent": s["recent"],
               "loss_markets": len(s["losers"]),
               "avg_loss": round(sum(m["pnl"] for _t, m in s["losers"]) / len(s["losers"]), 2)
               if s["losers"] else 0.0}
    out.append("JSON " + json.dumps(summary, sort_keys=True))
    return "\n".join(out)


def as_rows(s: dict) -> dict:
    """The summary as flat row lists (dashboard datasets): by_type, by_event, losers."""
    def lead_hours(m):
        d = event_date(m["event"])
        if not (d and m["first_fill"]):
            return None
        a = dt.datetime.fromisoformat(m["first_fill"].replace("Z", "+00:00"))
        return round((dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc) - a)
                     .total_seconds() / 3600, 1)

    def span_hours(m):
        if not (m["first_fill"] and m["last_fill"]):
            return None
        a, b = (dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
                for x in (m["first_fill"], m["last_fill"]))
        return round((b - a).total_seconds() / 3600, 1)

    recent = s["recent"]
    by_type = [{"type": t, "trading": t in CURRENT_UNIVERSE, "rewards": d["rewards"],
                "fills": d["fill_pnl"], "net": d["net"], "events": d["events"],
                "events_with_loss": d["events_with_loss"],
                "recent_rewards": recent.get(t, {}).get("rewards", 0.0),
                "recent_fills": recent.get(t, {}).get("fill_pnl", 0.0),
                "recent_net": recent.get(t, {}).get("net", 0.0)}
               for t, d in s["categories"].items()]
    by_event = []
    for e, r in s["events"].items():
        if not (r["rewards"] or r["fill_pnl"] or r["open_cost"]
                or (r["bids"] and r["category"] in CURRENT_UNIVERSE)):
            continue
        by_event.append({"event": e, "type": r["category"],
                         "date": r["date"].isoformat() if r["date"] else None,
                         "rewards": round(r["rewards"], 2), "fills": round(r["fill_pnl"], 2),
                         "net": round(r["rewards"] + r["fill_pnl"], 2),
                         "open_cost": round(r["open_cost"], 2), "orders": r["bids"],
                         "loss_markets": len(r["loss_markets"]),
                         "payout_recorded": r["payouts"] > 0})
    losers = [{"market": t, "event": m["event"], "type": m["category"],
               "pnl": round(m["pnl"], 2), "fills": m["fills"], "span_h": span_hours(m),
               "lead_h": lead_hours(m)} for t, m in s["losers"]]
    return {"as_of": s["as_of"], "days": s["days"], "by_type": by_type,
            "by_event": by_event, "losers": losers}


# ------------------------------------------------------------------ database


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def fetch(cur, tag: str):
    cur.execute(
        """SELECT DISTINCT ON (f.id) f.market_ticker, f.side, f.price, f.quantity,
                  COALESCE(f.fee, 0), f.raw_fill_json->>'created_time'
           FROM fills f JOIN live_orders o ON o.kalshi_order_id = f.kalshi_order_id
           WHERE o.strategy = %s""", (tag,))
    fills: dict[str, list] = {}
    for tkr, side, price, qty, fee, t in cur.fetchall():
        fills.setdefault(tkr, []).append(
            (side, int(price or 0), float(qty or 0), float(fee or 0), t))
    tickers = list(fills)
    settled, positions = {}, {}
    if tickers:
        cur.execute(
            """SELECT DISTINCT ON (market_ticker) market_ticker, realized_pnl FROM positions
               WHERE market_ticker = ANY(%s) AND raw_json ? 'settled_time'
               ORDER BY market_ticker, captured_at DESC""", (tickers,))
        settled = {t: float(p) for t, p in cur.fetchall() if p is not None}
        cur.execute(
            """SELECT DISTINCT ON (market_ticker) market_ticker,
                      COALESCE(quantity_fp, quantity) FROM positions
               WHERE market_ticker = ANY(%s) ORDER BY market_ticker, captured_at DESC""",
            (tickers,))
        positions = {t: float(q or 0) for t, q in cur.fetchall()}
    cur.execute(
        """SELECT market_ticker, client_order_id FROM live_orders
           WHERE strategy = %s AND action = 'buy'""", (tag,))
    bids: dict[str, int] = {}
    for tkr, coid in cur.fetchall():
        if (coid or "").startswith(CLOSING_PREFIXES):
            continue
        e = event_of(tkr)
        bids[e] = bids.get(e, 0) + 1
    return fills, settled, positions, bids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Liquidity-incentive scorecard.")
    ap.add_argument("--days", type=int, default=7, help="recent window in days (default 7)")
    ap.add_argument("--tag", default=LIVE_TAG)
    ap.add_argument("--json", action="store_true",
                    help="print the full data as one JSON object (for the dashboard)")
    args = ap.parse_args(argv)
    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    import psycopg

    payouts = load_payouts()
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            fills, settled, positions, bids = fetch(cur, args.tag)
    s = summarise(payouts, fills, settled, positions, bids,
                  now=dt.datetime.now(dt.timezone.utc), days=args.days)
    print(json.dumps(as_rows(s), sort_keys=True) if args.json else render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
