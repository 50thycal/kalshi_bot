"""Exit sweep: would stop-loss / take-profit exits have beaten holding the weather
paper trades to settlement — and which (TP, SL) pair is best?

The tracker already snapshots every bucket's bid/ask every ~15 minutes
(weather_bucket_snapshots), so each settled weather paper trade has a recorded
price path from entry to close. This replays every such trade under a grid of
(take-profit, stop-loss) exits — every combo graded on the IDENTICAL trades, a
perfectly paired comparison no set of live SL/TP books could match without
months of data.

Replay semantics mirror kalshi_bot/paper/engine.py::_mark_or_exit exactly:
walk the bucket's yes-bid snapshots after entry; exit at the first snapshot
where (bid - entry) >= TP or <= -SL, selling at that snapshot's bid and paying
the Kalshi fee on the exit price; otherwise settle at 0/100 with no exit fee.
Limitations: 15-minute sampling (a spike that reverts between snapshots never
triggers) and gap-through fills (a price that jumps past the SL exits at the
gapped bid, not the trigger level) — both conservative-realistic for binaries.

Read-only and self-contained (stdlib + psycopg only) so it runs on the ops
runner. Usage:
    DATABASE_URL_RO=postgresql://... python scripts/weather_exit_sweep.py
    # or via the ops channel:
    {"type": "script", "name": "weather_exit_sweep", "args": ["--tp-grid", "none,10,20"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

MIN_TRADES = 30  # below this, combo rankings are noise — banner it

# Book prefixes by kind; low prefixes are longer so they match first
# (mirrors scripts/weather_score.py).
BOOKS = {
    "low": {"fav": "weather_low_fav", "nws": "weather_low_nws", "cal": "weather_low_cal",
            "pm": "weather_low_pm"},
    "high": {"fav": "weather_fav", "nws": "weather_nws", "cal": "weather_cal",
             "pm": "weather_pm"},
}


def book_of(strategy: str | None) -> tuple[str, str] | None:
    s = strategy or ""
    for kind in ("low", "high"):
        for book, prefix in BOOKS[kind].items():
            if s.startswith(prefix):
                return (kind, book)
    return None


def fee_cents(price_cents: float, enabled: bool = True) -> float:
    """Kalshi fee in cents at qty=1 (mirrors paper/engine.py::kalshi_fee)."""
    if not enabled:
        return 0.0
    p = price_cents / 100.0
    return float(math.ceil(0.07 * p * (1 - p) * 100))


@dataclass
class Trade:
    trade_id: int
    market_ticker: str
    strategy: str
    entry_cents: int
    entry_fee_cents: float
    resolved_value: int
    bids: list[float]  # yes-bid path, snapshots strictly after entry, time order


def replay(trade: Trade, tp: float | None, sl: float | None) -> tuple[float, str]:
    """P&L in cents (qty=1) and exit kind ('tp' | 'sl' | 'settle') for this exit rule.

    Mirrors engine semantics: trigger on (bid - entry), exit at that snapshot's bid
    minus the exit fee; settlement pays 0/100 with no exit fee.
    """
    for bid in trade.bids:
        gain = bid - trade.entry_cents
        if tp is not None and gain >= tp:
            return (bid - trade.entry_cents - trade.entry_fee_cents - fee_cents(bid), "tp")
        if sl is not None and gain <= -sl:
            return (bid - trade.entry_cents - trade.entry_fee_cents - fee_cents(bid), "sl")
    return (trade.resolved_value - trade.entry_cents - trade.entry_fee_cents, "settle")


@dataclass
class ComboResult:
    tp: float | None
    sl: float | None
    n: int
    pnl_cents: float
    exits_tp: int
    exits_sl: int
    settled: int

    @property
    def per_trade(self) -> float:
        return self.pnl_cents / self.n if self.n else math.nan


def sweep(trades: list[Trade], grid: list[tuple[float | None, float | None]]) -> list[ComboResult]:
    out = []
    for tp, sl in grid:
        pnl, ntp, nsl, nst = 0.0, 0, 0, 0
        for t in trades:
            p, kind = replay(t, tp, sl)
            pnl += p
            if kind == "tp":
                ntp += 1
            elif kind == "sl":
                nsl += 1
            else:
                nst += 1
        out.append(ComboResult(tp, sl, len(trades), pnl, ntp, nsl, nst))
    return out


def parse_grid(spec: str) -> list[float | None]:
    vals: list[float | None] = []
    for tok in spec.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        vals.append(None if tok in ("none", "off") else float(tok))
    return vals


def _lvl(v: float | None) -> str:
    return "hold" if v is None else f"{v:g}c"


def report(trades: list[Trade], grid, args, skipped_no_path: int) -> None:
    print("=== Exit sweep: TP/SL grid replayed on settled weather trades "
          "(fees on, exit at snapshot bid) ===")
    print(f"  replayable trades: {len(trades)}   skipped (no price path): {skipped_no_path}")
    print("  (paths exist only for trades entered after ladder collection began Jun 10;"
          " coverage grows daily)")
    if len(trades) < MIN_TRADES:
        print(f"  *** SAMPLE TOO SMALL (have {len(trades)}, want >= {MIN_TRADES}) —"
              f" rankings are noise, treat as plumbing check ***")
    if not trades:
        return

    results = sweep(trades, grid)
    hold = next((r for r in results if r.tp is None and r.sl is None), None)
    hold_pt = hold.per_trade if hold else math.nan

    print("\n  --- Pooled grid (all weather books), sorted by P&L/trade ---")
    print(f"  {'tp':>5} {'sl':>5} {'n':>4} {'exit_tp':>7} {'exit_sl':>7} {'settle':>6}"
          f" {'total':>8} {'per-trade':>9} {'vs hold':>8}")
    for r in sorted(results, key=lambda r: -r.per_trade):
        d = r.per_trade - hold_pt
        print(f"  {_lvl(r.tp):>5} {_lvl(r.sl):>5} {r.n:4d} {r.exits_tp:7d} {r.exits_sl:7d}"
              f" {r.settled:6d} {r.pnl_cents / 100.0:+8.2f}$ {r.per_trade:+8.1f}c {d:+7.1f}c")

    print("\n  --- Best exits per book (pooled windows; needs n >= 5) ---")
    by_book: dict[tuple[str, str], list[Trade]] = {}
    for t in trades:
        kb = book_of(t.strategy)
        if kb:
            by_book.setdefault(kb, []).append(t)
    for (kind, book), ts in sorted(by_book.items()):
        if len(ts) < 5:
            print(f"  {kind} {book}: n={len(ts)} (too few to rank)")
            continue
        res = sweep(ts, grid)
        bhold = next(r for r in res if r.tp is None and r.sl is None)
        best = sorted(res, key=lambda r: -r.per_trade)[:3]
        cells = ", ".join(
            f"tp={_lvl(r.tp)}/sl={_lvl(r.sl)} {r.per_trade:+.1f}c" for r in best
        )
        print(f"  {kind} {book} (n={len(ts)}): hold={bhold.per_trade:+.1f}c | best: {cells}")


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def fetch_trades(conn) -> tuple[list[Trade], int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, market_ticker, strategy, assumed_price, fees, resolved_value,"
            " created_at FROM paper_trades"
            " WHERE strategy LIKE 'weather%' AND status='settled' AND side='yes'"
            " AND assumed_price IS NOT NULL AND resolved_value IS NOT NULL"
        )
        rows = cur.fetchall()
        tickers = sorted({r[1] for r in rows})
        paths: dict[str, list[tuple]] = {}
        if tickers:
            cur.execute(
                "SELECT market_ticker, captured_at, yes_bid_cents FROM weather_bucket_snapshots"
                " WHERE market_ticker = ANY(%s) ORDER BY captured_at",
                (tickers,),
            )
            for ticker, cap, bid in cur.fetchall():
                paths.setdefault(ticker, []).append((cap, bid))

    trades: list[Trade] = []
    skipped = 0
    for tid, ticker, strategy, entry, fees, resolved, created_at in rows:
        bids = [
            float(bid) for cap, bid in paths.get(ticker, [])
            if cap > created_at and bid is not None
        ]
        if not bids:
            skipped += 1
            continue
        trades.append(Trade(
            trade_id=tid,
            market_ticker=ticker,
            strategy=strategy or "",
            entry_cents=int(entry),
            entry_fee_cents=float(fees or 0.0) * 100.0,
            resolved_value=int(resolved),
            bids=bids,
        ))
    return trades, skipped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tp-grid", default="none,5,10,15,20,30",
                    help="take-profit levels in cents ('none' = no TP)")
    ap.add_argument("--sl-grid", default="none,5,10,15,20,30",
                    help="stop-loss levels in cents ('none' = no SL)")
    args = ap.parse_args(argv)
    tps, sls = parse_grid(args.tp_grid), parse_grid(args.sl_grid)
    grid = [(tp, sl) for tp in tps for sl in sls]
    if not any(tp is None and sl is None for tp, sl in grid):
        grid.insert(0, (None, None))  # always include the hold-to-settlement baseline

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg  # deferred so the pure replay helpers import without the driver

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        trades, skipped = fetch_trades(conn)

    report(trades, grid, args, skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
