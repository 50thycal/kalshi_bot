"""mmsell QUEUE-POSITION A/B — is paying 1c to improve queue position worth it?

THE QUESTION
------------
`mmsell_live_price_offset_cents` has always been 0 (rest at the no-bid, join the queue) and has
never been varied, so we have no data on what queue position is worth. It is the only untested
live knob that acts on the mechanism that actually decided the mmsell3 live test: maker adverse
selection, worth roughly 2c/contract (paper gross ~+2.2c vs live realized +0.18c once the maker-fee
correction in docs/MMSELL_ROADMAP.md Sec 2 is applied).

The trade is explicit and two-sided:
  * bidding 1c above the no-bid COSTS 1c of edge on every fill, but
  * it buys a higher fill rate AND earlier queue priority — and queue priority is precisely what
    decides whether you get the quiet fills or only the ones informed flow chooses to hand you.

The live retry data already showed the tickers live MISSED earned the same in paper as the ones it
captured (6.15 vs 6.26 c/contract), i.e. lost volume rather than dodged bullets. That is the
argument for paying to fill more of them — but it is an argument, not a measurement. This is the
measurement.

DESIGN — randomized WITHIN one book, not two books
--------------------------------------------------
Two live books at different offsets would compete for the same tickers, double the exposure, and
cross each other (self-trade prevention). Instead each TICKER is assigned to an arm by a
deterministic hash (kalshi_bot/live/sizing.py offset_arm), so:
  * total live footprint is unchanged from today,
  * both arms see the same market flow over the same window — this is a genuine randomized
    experiment, not a before/after comparison contaminated by regime,
  * a ticker keeps one arm for life, so the entry-retry path cannot blend two prices,
  * assignment is recomputable from the ticker, so no schema change was needed.

HOT entries are excluded: they are priced by the momentum guard, not the arm, so counting them
would measure the guard. This script drops them by reading the `hot_entry` risk-event code.

WHAT IT REPORTS, per arm
------------------------
  * orders placed / filled -> FILL RATE (the thing the offset is supposed to buy)
  * average fill price     -> confirms the arm actually priced differently (a sanity check: if the
                              arms show the same average price the experiment is not running)
  * realized c/contract on SETTLED positions -> the thing that actually decides it

Read the last column, not the fill rate. A higher fill rate with WORSE realized P&L is the
signature of buying adverse selection, and is a kill rather than a puzzle.

Read-only, self-contained (stdlib + psycopg):
    DATABASE_URL_RO=postgresql://... python scripts/mmsell_offset_ab.py
    # or:  {"type": "script", "name": "mmsell_offset_ab"}

Arm assignment is recomputed here by importing the SAME offset_arm the executor used, so the
analysis and the trading path can never disagree about which arm a ticker was in.
"""

from __future__ import annotations

import argparse
import math
import os
import pathlib
import sys
from collections import defaultdict

# Import the live arithmetic itself rather than re-deriving it — a second copy of the hash would
# be a silent attribution bug the moment either side changed.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from kalshi_bot.live.sizing import offset_arm  # noqa: E402

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

DEFAULT_SALT = "mmsell-offset-ab-v1"


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval on a rate — the same interval the roadmap uses, so fill rates and loss
    rates are read on one convention."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def summarize_arm(rows) -> dict:
    """Aggregate one arm's per-order rows into the decision numbers.

    rows: iterable of (filled: bool, fill_px: float|None, qty: int|None, realized: float|None)
    where `realized` is dollars on a SETTLED position (None = not settled yet).

    Pure, so tests/test_mmsell_offset_ab.py can pin the arithmetic without a DB."""
    orders = filled = 0
    px_num = px_den = 0.0
    settled_n = 0
    settled_c = 0.0
    settled_contracts = 0
    for is_filled, fill_px, qty, realized in rows:
        orders += 1
        if not is_filled:
            continue
        filled += 1
        q = int(qty or 0)
        if fill_px is not None and q > 0:
            px_num += float(fill_px) * q
            px_den += q
        if realized is not None and q > 0:
            settled_n += 1
            settled_c += float(realized) * 100.0   # dollars -> cents
            settled_contracts += q
    lo, hi = wilson(filled, orders)
    return {
        "orders": orders,
        "filled": filled,
        "fill_rate": (filled / orders) if orders else 0.0,
        "fill_ci": (lo, hi),
        "avg_fill_px": (px_num / px_den) if px_den else float("nan"),
        "settled_positions": settled_n,
        "settled_contracts": settled_contracts,
        "cents_per_contract": (settled_c / settled_contracts) if settled_contracts else float("nan"),
        "total_cents": settled_c,
    }


def _fetch(cur, strategy_like: str):
    """One row per live mmsell BUY order: which ticker, whether it filled, at what price/size, and
    the realized P&L of the resulting position once the market settled.

    `hot` comes from the risk event written at placement time. Hot entries are priced by the
    momentum guard rather than the arm, so they are excluded from the split."""
    cur.execute(
        """
        WITH ord AS (
            SELECT o.id, o.market_ticker, o.kalshi_order_id, o.status, o.created_at
            FROM live_orders o
            WHERE o.strategy LIKE %s AND o.action = 'buy'
              AND o.status IN ('filled', 'canceled', 'submitted', 'resting')
        ),
        fl AS (
            SELECT f.kalshi_order_id,
                   sum(f.quantity) AS qty,
                   sum(f.price * f.quantity)::float / NULLIF(sum(f.quantity), 0) AS px
            FROM fills f GROUP BY f.kalshi_order_id
        ),
        pos AS (
            SELECT DISTINCT ON (p.market_ticker) p.market_ticker, p.realized_pnl, p.quantity_fp
            FROM positions p
            ORDER BY p.market_ticker, p.captured_at DESC
        ),
        hot AS (
            SELECT DISTINCT r.market_ticker
            FROM risk_events r
            WHERE r.reason_codes_json::text LIKE '%%hot_entry%%'
        )
        SELECT ord.market_ticker,
               (fl.qty IS NOT NULL) AS is_filled,
               fl.px, fl.qty,
               CASE WHEN abs(coalesce(pos.quantity_fp, 0)) < 0.01
                    THEN pos.realized_pnl ELSE NULL END AS realized,
               (hot.market_ticker IS NOT NULL) AS was_hot
        FROM ord
        LEFT JOIN fl ON fl.kalshi_order_id = ord.kalshi_order_id
        LEFT JOIN pos ON pos.market_ticker = ord.market_ticker
        LEFT JOIN hot ON hot.market_ticker = ord.market_ticker
        """,
        (strategy_like,),
    )
    return cur.fetchall()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--salt", default=os.environ.get("MMSELL_OFFSET_AB_SALT", DEFAULT_SALT),
                    help="must match mmsell_live_offset_ab_salt in force during the experiment")
    ap.add_argument("--arms", default=os.environ.get("MMSELL_OFFSET_AB_ARMS", "0,1"),
                    help="comma-separated offsets, matching mmsell_live_offset_ab_arms")
    ap.add_argument("--strategy", default="mmsell%", help="live strategy LIKE pattern")
    ap.add_argument("--include-hot", action="store_true",
                    help="do NOT exclude momentum-guard (hot) entries — diagnostic only")
    args = ap.parse_args(argv)

    try:
        arms = tuple(int(a) for a in args.arms.split(",") if a.strip())
    except ValueError:
        print(f"--arms must be comma-separated integers, got {args.arms!r}", file=sys.stderr)
        return 2
    if len(arms) < 2:
        print(f"--arms needs at least 2 offsets to be an experiment, got {arms}", file=sys.stderr)
        return 2

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO", ""))
    if not url:
        print("DATABASE_URL_RO is not set", file=sys.stderr)
        return 2

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            rows = _fetch(cur, args.strategy)

    by_arm: dict[int, list] = defaultdict(list)
    hot_dropped = 0
    for ticker, is_filled, px, qty, realized, was_hot in rows:
        if was_hot and not args.include_hot:
            hot_dropped += 1
            continue
        idx, _off = offset_arm(ticker, arms=arms, salt=args.salt)
        by_arm[idx].append((bool(is_filled), px, qty, realized))

    print("=" * 82)
    print(f"MMSELL QUEUE-POSITION A/B — arms {arms} (cents above the no-bid), salt {args.salt!r}")
    print(f"orders considered: {sum(len(v) for v in by_arm.values())}"
          f"   hot entries excluded: {hot_dropped}"
          f"{' (INCLUDED via --include-hot)' if args.include_hot else ''}")
    print("=" * 82)

    if not by_arm:
        print("\nNo live mmsell buy orders found. Either the book has never been armed, or the")
        print("experiment has not run yet (mmsell_live_offset_ab_arms is empty by default).")
        return 0

    stats = {idx: summarize_arm(by_arm[idx]) for idx in sorted(by_arm)}

    print(f"\n{'arm':>4} {'offset':>7} {'orders':>7} {'filled':>7} {'fill%':>7} "
          f"{'fill% 95CI':>15} {'avg fill px':>12}")
    for idx, st in stats.items():
        lo, hi = st["fill_ci"]
        print(f"{idx:>4} {arms[idx]:>6}c {st['orders']:>7} {st['filled']:>7} "
              f"{100 * st['fill_rate']:>6.1f}% [{100 * lo:>5.1f},{100 * hi:>5.1f}]% "
              f"{st['avg_fill_px']:>12.2f}")

    print(f"\n{'arm':>4} {'offset':>7} {'settled pos':>12} {'contracts':>10} "
          f"{'c/contract':>12} {'total c':>10}")
    for idx, st in stats.items():
        print(f"{idx:>4} {arms[idx]:>6}c {st['settled_positions']:>12} "
              f"{st['settled_contracts']:>10} {st['cents_per_contract']:>12.2f} "
              f"{st['total_cents']:>10.1f}")

    # The decision, stated against the pre-registered gate so the reader cannot re-scope it.
    base = stats.get(0)
    if base and len(stats) >= 2:
        print("\nGATE (docs/MMSELL_OFFSET_AB.md): at n>=150 FILLS per arm, promote a non-zero")
        print("offset only if its realized c/contract beats arm 0 by >= 0.5c. Any arm at or below")
        print("arm 0 is a KILL for that offset.")
        for idx, st in stats.items():
            if idx == 0:
                continue
            n_ok = st["settled_contracts"] >= 150 and base["settled_contracts"] >= 150
            delta = st["cents_per_contract"] - base["cents_per_contract"]
            if not n_ok:
                verdict = (f"UNDERPOWERED (need 150 settled contracts/arm; have "
                           f"{base['settled_contracts']} vs {st['settled_contracts']})")
            elif delta >= 0.5:
                verdict = f"PROMOTE (+{delta:.2f}c vs arm 0)"
            elif delta <= 0:
                verdict = f"KILL ({delta:+.2f}c vs arm 0)"
            else:
                verdict = f"NO (+{delta:.2f}c, short of the +0.5c bar)"
            print(f"  arm {idx} ({arms[idx]:+d}c): {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
