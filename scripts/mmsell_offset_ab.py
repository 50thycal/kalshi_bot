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

DESIGN — two live books, partitioned by a per-ticker hash
---------------------------------------------------------
The arms are two separate live books — mmsell10a (arm 0, rests AT the no-bid = control) and
mmsell10b (arm 1, rests 1c better) — so each has its own tag, its own paper twin and its own P&L
line. The incumbent mmsell10 is untouched.

The hash is what makes two books a valid experiment rather than a race.
repository.live_open_order_exists(ticker) is strategy-AGNOSTIC, so any in-flight live order on a
market blocks every other book from it; two books over the same entry spec would be split by book
EVALUATION ORDER, not at random. Assigning each ticker to exactly one arm by a deterministic hash
(live/sizing.py arm_book_offset) means:
  * no ticker is ever contested, so neither arm can block or queue against the other,
  * both arms see the same market flow over the same window — a genuine randomized experiment,
    not a before/after comparison contaminated by regime,
  * a ticker keeps one arm for life, so the entry-retry path cannot blend two prices,
  * assignment is recomputable from the ticker, so no schema change was needed.

Caveat this script cannot fix: mmsell10 evaluates FIRST, so the arm books trade only the flow it
did not claim. That keeps the A-vs-B comparison internally valid (mmsell10 blocks both arms
symmetrically) but reduces power. Watch the per-arm order counts below — a starved experiment is
the signal to stand mmsell10 down, not to reinterpret the result.

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

Compare arm 1 against ARM 0, never against mmsell10: the incumbent trades a different slice of
flow and a different clip size, so it is not a valid control. Arm 0 exists to be that control.

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
            SELECT o.id, o.market_ticker, o.kalshi_order_id, o.status, o.created_at,
                   coalesce(o.strategy, '?') AS strategy
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
        SELECT ord.market_ticker, ord.strategy,
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
    ap.add_argument("--by-hash", action="store_true",
                    help="single-book form: recompute each ticker's arm from the salt instead of "
                         "grouping by the live strategy tag")
    ap.add_argument("--control", default="mmsell10a",
                    help="the offset-0 arm book to compare against (NOT the incumbent mmsell10)")
    ap.add_argument("--arm-books", default="mmsell10a=0,mmsell10b=1",
                    help="tag=arm-index map, so each book's offset can be labelled")
    args = ap.parse_args(argv)

    try:
        arms = tuple(int(a) for a in args.arms.split(",") if a.strip())
    except ValueError:
        print(f"--arms must be comma-separated integers, got {args.arms!r}", file=sys.stderr)
        return 2
    if len(arms) < 2:
        print(f"--arms needs at least 2 offsets to be an experiment, got {arms}", file=sys.stderr)
        return 2

    arm_by_tag: dict[str, int] = {}
    for tok in args.arm_books.split(","):
        tag, _, idx = tok.strip().partition("=")
        if tag.strip() and idx.strip().isdigit():
            arm_by_tag[tag.strip()] = int(idx)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO", ""))
    if not url:
        print("DATABASE_URL_RO is not set", file=sys.stderr)
        return 2

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            rows = _fetch(cur, args.strategy)

    # Two-book form (the deployed shape): each arm IS a strategy tag, so group by the tag —
    # ground truth of what was actually sent, with no dependence on the salt still matching.
    # --by-hash falls back to recomputing the assignment, for the single-book form where one
    # tag carries both arms.
    by_arm: dict[str, list] = defaultdict(list)
    arm_offset_of: dict[str, int] = {}
    hot_dropped = 0
    for ticker, strategy, is_filled, px, qty, realized, was_hot in rows:
        if was_hot and not args.include_hot:
            hot_dropped += 1
            continue
        if args.by_hash:
            idx, off = offset_arm(ticker, arms=arms, salt=args.salt)
            key = f"arm{idx}"
            arm_offset_of[key] = off
        else:
            key = strategy
            book_arm = arm_by_tag.get(strategy)
            if book_arm is not None and book_arm < len(arms):
                arm_offset_of[key] = arms[book_arm]
        by_arm[key].append((bool(is_filled), px, qty, realized))

    print("=" * 88)
    print(f"MMSELL QUEUE-POSITION A/B — arms {arms} (cents above the no-bid)"
          f"{', salt ' + repr(args.salt) if args.by_hash else ''}")
    print(f"grouping: {'per-ticker hash (single-book form)' if args.by_hash else 'live strategy tag'}"
          f"   orders considered: {sum(len(v) for v in by_arm.values())}"
          f"   hot excluded: {hot_dropped}"
          f"{' (INCLUDED via --include-hot)' if args.include_hot else ''}")
    print("=" * 88)

    if not by_arm:
        print("\nNo live mmsell buy orders found. Either the books have never been armed, or the")
        print("experiment has not started (MMSELL_LIVE_OFFSET_AB_ARMS is empty by default, and an")
        print("arm book claims NO tickers until it is set).")
        return 0

    stats = {k: summarize_arm(v) for k, v in sorted(by_arm.items())}

    def _off(k):
        o = arm_offset_of.get(k)
        return f"{o:+d}c" if o is not None else "  -"

    print(f"\n{'book/arm':<14} {'offset':>7} {'orders':>7} {'filled':>7} {'fill%':>7} "
          f"{'fill% 95CI':>15} {'avg fill px':>12}")
    for k, st in stats.items():
        lo, hi = st["fill_ci"]
        print(f"{k:<14} {_off(k):>7} {st['orders']:>7} {st['filled']:>7} "
              f"{100 * st['fill_rate']:>6.1f}% [{100 * lo:>5.1f},{100 * hi:>5.1f}]% "
              f"{st['avg_fill_px']:>12.2f}")

    print(f"\n{'book/arm':<14} {'offset':>7} {'settled pos':>12} {'contracts':>10} "
          f"{'c/contract':>12} {'total c':>10}")
    for k, st in stats.items():
        print(f"{k:<14} {_off(k):>7} {st['settled_positions']:>12} "
              f"{st['settled_contracts']:>10} {st['cents_per_contract']:>12.2f} "
              f"{st['total_cents']:>10.1f}")

    # The decision, stated against the pre-registered gate so it cannot be re-scoped after the
    # fact. The control is the OFFSET-0 ARM, never the incumbent mmsell10 — that book trades a
    # different slice of flow (it claims candidates first) and a different clip size.
    ctrl_key = args.control if args.by_hash is False else "arm0"
    base = stats.get(ctrl_key)
    print("\nGATE (docs/MMSELL_OFFSET_AB.md): at n>=150 settled CONTRACTS per arm, promote a")
    print(f"non-zero offset only if it beats the control ({ctrl_key}) by >= 0.5c/contract.")
    if base is None:
        print(f"  control {ctrl_key!r} has no rows — pass --control <tag> if the arm books are "
              f"named differently.")
        return 0
    for k, st in stats.items():
        if k == ctrl_key:
            continue
        if arm_offset_of.get(k) is None and not args.by_hash:
            print(f"  {k}: not an arm book (no configured arm) — reference only, not compared")
            continue
        n_ok = st["settled_contracts"] >= 150 and base["settled_contracts"] >= 150
        delta = st["cents_per_contract"] - base["cents_per_contract"]
        if not n_ok:
            verdict = (f"UNDERPOWERED (need 150 settled contracts/arm; control has "
                       f"{base['settled_contracts']}, this arm {st['settled_contracts']})")
        elif delta >= 0.5:
            verdict = f"PROMOTE ({delta:+.2f}c vs control)"
        elif delta <= 0:
            verdict = f"KILL ({delta:+.2f}c vs control)"
        else:
            verdict = f"NO ({delta:+.2f}c, short of the +0.5c bar)"
        print(f"  {k}: {verdict}")

    # Sanity checks — a silently-broken experiment looks exactly like a null result.
    if not args.by_hash and len(stats) >= 2:
        keys = [k for k in stats if arm_offset_of.get(k) is not None]
        if len(keys) >= 2:
            counts = [stats[k]["orders"] for k in keys]
            pxs = [stats[k]["avg_fill_px"] for k in keys]
            if min(counts) and max(counts) / max(1, min(counts)) > 1.25:
                print(f"\n  WARNING: arm order counts are imbalanced ({dict(zip(keys, counts, strict=True))}) — "
                      "the\n  partition may not be working, or one arm is being starved. The "
                      "comparison is confounded.")
            if all(p == p for p in pxs) and max(pxs) - min(pxs) < 0.25:
                print("\n  WARNING: the arms' average fill prices are nearly identical — the "
                      "offset does not\n  appear to be taking effect, so there is no treatment "
                      "to measure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
