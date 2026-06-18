"""Ratchet / trailing-exit study: compare HOLD vs fixed take-profit vs break-even vs a
ratcheting trailing stop, by entry window, replayed on the real recorded bid paths.

The premise (from the consensus/exit work): a fixed TP either banks too early or gives back
gains on a reversal. A RATCHET lets the position run but steps a profit *floor* up as the bid
crosses thresholds, exiting only if it falls back to the current floor — the continuous form
is a trailing stop (arm at +A, then trail by W); the discrete form is tiered floors.

For every settled favorite trade we walk its post-entry `weather_bucket_snapshots` bid path
in time order and apply each rule (exit at the bid when triggered, else settle). Reports
per-window net cents/contract + exit rate, so we can see whether ratcheting beats hold/TP.
Self-contained (stdlib + psycopg).

Usage:
    {"type":"script","name":"weather_ratchet_study","args":["--city","LAX","--windows","20,14"]}
"""

from __future__ import annotations

import argparse
import os
import sys
from statistics import mean

RO_OPTIONS = (
    "-c default_transaction_read_only=on -c statement_timeout=90000 "
    "-c idle_in_transaction_session_timeout=90000"
)


# --- exit rules: each returns decide(bid, peak, entry) -> True to exit now ----------


def _rules():
    rules: list[tuple[str, object]] = [("hold", lambda b, p, e: False)]
    for tp in (10, 15, 20):
        rules.append((f"tp{tp}", (lambda t: lambda b, p, e: (b - e) >= t)(tp)))
    for arm in (8, 12):
        rules.append((f"be{arm}", (lambda a: lambda b, p, e: (p - e) >= a and b <= e)(arm)))
    for arm, trail in ((10, 5), (15, 5), (15, 8), (20, 10)):
        rules.append((f"trail{arm}/{trail}",
                      (lambda a, w: lambda b, p, e: (p - e) >= a and b <= p - w)(arm, trail)))
    # discrete tiers: (peak-threshold above entry -> profit floor above entry)
    for label, tiers in (("tier10>5,20>12", [(10, 5), (20, 12)]),
                         ("tier8>3,15>8,25>18", [(8, 3), (15, 8), (25, 18)])):
        rules.append((label, (lambda ts: lambda b, p, e: _tier_exit(b, p, e, ts))(tiers)))
    return rules


def _tier_exit(bid, peak, entry, tiers) -> bool:
    floor = None
    for thresh, flr in tiers:
        if (peak - entry) >= thresh:
            floor = flr if floor is None else max(floor, flr)
    return floor is not None and (bid - entry) <= floor


def replay(path: list[int], entry: float, won: int, decide) -> float:
    """Net cents/contract: exit at the first bid the rule triggers on, else settle YES/NO."""
    peak = None
    for bid in path:
        peak = bid if peak is None else (bid if bid > peak else peak)
        if decide(bid, peak, entry):
            return bid - entry
    return 100.0 * won - entry


# --- report ------------------------------------------------------------------------


def report(trades: dict, windows: list[float], min_rows: int) -> None:
    rules = _rules()
    by_win: dict[int, list] = {}
    for (win_h, entry, won), path in trades.values():
        by_win.setdefault(win_h, []).append((path, entry, won))
    print(f"  {'window':>6} {'n':>4} " + " ".join(f"{lbl:>14}" for lbl, _ in rules))
    for w in sorted(by_win, reverse=True):
        rows = by_win[w]
        if w not in [int(x) for x in windows]:
            continue
        cells = []
        for _lbl, decide in rules:
            nets = [replay(p, e, won, decide) for p, e, won in rows]
            exits = sum(1 for p, e, won in rows if decide_triggers(p, e, decide))
            cells.append(f"{mean(nets):+6.1f}({exits * 100 // len(rows):>3}%)")
        flag = " *" if len(rows) < min_rows else ""
        print(f"  h{int(w):<5d} {len(rows):4d} " + " ".join(f"{c:>14}" for c in cells) + flag)
    print("  cell = avg net cents/contract (exit-rate%). hold = settle. * = n<min_rows.")
    print("  trailA/W = arm at +A then trail by W; beA = arm +A then exit at entry; tierX>Y = "
          "cross +X -> lock +Y floor.")


def decide_triggers(path: list[int], entry: float, decide) -> bool:
    peak = None
    for bid in path:
        peak = bid if peak is None else (bid if bid > peak else peak)
        if decide(bid, peak, entry):
            return True
    return False


# --- main --------------------------------------------------------------------------


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--city", default="ALL", help="city code (e.g. LAX) or ALL")
    ap.add_argument("--kind", choices=("high", "low"), default="high")
    ap.add_argument("--windows", default="20,14")
    ap.add_argument("--min-rows", type=int, default=12)
    args = ap.parse_args(argv)
    windows = [float(x) for x in args.windows.split(",") if x.strip()]
    series = "KXHIGH" if args.kind == "high" else "KXLOWT"
    book = "weather_fav_h%" if args.kind == "high" else "weather_low_fav_h%"

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    like = f"{series}{'%' if args.city == 'ALL' else args.city}-%"

    import psycopg

    sql = (
        "SELECT pt.id, (split_part(pt.strategy,'_h',2))::int AS win_h, pt.assumed_price AS entry,"
        " (pt.pnl>0)::int AS won, b.yes_bid_cents AS bid"
        " FROM paper_trades pt JOIN weather_bucket_snapshots b"
        "   ON b.market_ticker = pt.market_ticker AND b.captured_at >= pt.created_at"
        " WHERE pt.strategy LIKE %s AND pt.market_ticker LIKE %s AND pt.pnl IS NOT NULL"
        "   AND b.yes_bid_cents IS NOT NULL"
        " ORDER BY pt.id, b.captured_at"
    )
    trades: dict = {}
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql, (book, like))
            for tid, win_h, entry, won, bid in cur:
                if tid not in trades:
                    trades[tid] = [(win_h, float(entry), int(won)), []]
                trades[tid][1].append(int(bid))
    # collapse to (meta, path)
    trades = {tid: (meta, path) for tid, (meta, path) in trades.items()}

    if not trades:
        print(f"No settled {args.kind} favorite trades with bid paths for city={args.city}.")
        return 0
    print(f"=== Ratchet study — {args.kind} favorite, city={args.city} "
          f"({len(trades)} trades) ===")
    report(trades, windows, args.min_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
