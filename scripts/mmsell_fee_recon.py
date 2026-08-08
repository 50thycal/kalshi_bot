"""FEE RECONCILIATION — what Kalshi ACTUALLY charged us, vs what the paper engine models.

WHY THIS EXISTS
---------------
`paper/engine.py:kalshi_fee` applies ONE formula to every book: `ceil(0.07 * C * P * (1-P))`,
Kalshi's TAKER rate. But mmsell and theta enter as pure makers (`post_only: true`), and the
published maker rate is a quarter of that — `ceil(0.0175 * C * P * (1-P))`. `docs/MMSELL_ROADMAP.md`
flagged the mismatch as worth ~1c/contract and asked for it to be confirmed against a real
statement before acting. This is that confirmation, run off our own `fills` rows, where
`fills.fee` is the amount Kalshi billed.

It is not a research probe — it is a MEASUREMENT of a modelling error that shifts every gate
stated in absolute cents (`docs/BOOK_REGISTRY.md` is full of them).

WHAT IT FOUND (2026-08-06, n=366 mmsell3 contracts)
--------------------------------------------------
Maker fills on our series cost essentially NOTHING — and less than even the published maker
formula predicts:

    book       contracts   actual c/ct   taker model   maker model
    mmsell3          366         0.013          1.00          1.00
    mmsell10         130         0.009          0.59          0.50
    theta4            69         0.000          1.07          0.33

while the weather books, which enter as YES-TAKERS, land within ~8% of the taker model:

    weather_fav_h20   58         1.601          1.69          0.53
    weather_fav_h14   60         1.411          1.52          0.47

So the taker formula is roughly right for takers and ~77x too high for our makers. The most
likely reading is that these series are not in the fee schedule's "Maker Fees" section at all
(maker fee = 0), and the few cents that do appear are the handful of fills that crossed.

TWO CONSEQUENCES, AND THEY POINT IN OPPOSITE DIRECTIONS
-------------------------------------------------------
1. Every mmsell/theta paper number is ~1c/contract TOO PESSIMISTIC at 1-contract clips. The
   fill model's realizable figures — the numbers books are promoted on — are understated by the
   same amount. `mmsell10` reads +1.40c realizable; corrected it is nearer +2.40c.
2. It RAISES the bar for the taker thesis. A taker really would pay the taker fee that a maker
   avoids entirely, so switching to taking costs the spread AND ~0.5-1c/contract of fee that we
   currently pay nothing of.

WHY THE ENGINE IS NOT PATCHED
-----------------------------
Tempting, but wrong right now: the correction is UNIFORM, so it preserves every ranking, while
changing `kalshi_fee` mid-flight would split each book's history across two fee models. Fourteen
Wmmsell*/Tmmsell* books are currently accumulating toward n>=100-150 settled gates; a change now
would make the first half of each book non-comparable with the second. The correction belongs in
the analysis layer until those gates close.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_fee_recon.py
    # or:  {"type": "script", "name": "mmsell_fee_recon"}

The fee formulas are pure functions unit-tested in tests/test_mmsell_fee_recon.py.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

# Kalshi's published coefficients (July 2026 fee schedule).
TAKER_COEFF = 0.07
MAKER_COEFF = 0.0175

# Books that enter as pure makers (`post_only: true`). Everything else is assumed taker-entry,
# which is what makes the weather rows a CONTROL: if the taker model tracks them but not these,
# the gap is about maker-vs-taker and not about our fee arithmetic being wrong in general.
MAKER_ENTRY_PREFIXES = ("mmsell", "theta")


def modeled_fee(coeff: float, price_cents: float, contracts: int) -> float:
    """Kalshi's fee in DOLLARS: ceil(coeff * C * P * (1-P)) rounded up to the cent.

    Matches paper/engine.py:kalshi_fee at coeff=0.07. The ceiling is applied to the whole fill,
    not per contract — which is why a 58-contract fill costs proportionally less than 58
    one-contract fills, and why clip size interacts with the fee at all."""
    p = price_cents / 100.0
    return math.ceil(coeff * contracts * p * (1 - p) * 100) / 100.0


def is_maker_entry(strategy: str | None) -> bool:
    return (strategy or "").startswith(MAKER_ENTRY_PREFIXES) or "mmsell" in (strategy or "")


def load_rows(cur) -> list[dict]:
    """One row per book: real billed fees beside both modeled fees.

    `fills.fee` is Kalshi's own number, carried through from /portfolio/fills — this is the
    statement, not our bookkeeping."""
    cur.execute(
        # Model PER FILL, not once per book: the ceiling is applied to each fill, so a
        # 58-contract fill costs far less per contract than 58 one-contract fills. Averaging
        # the price first and ceiling once understates what was really charged.
        "SELECT coalesce(lo.strategy, '(unmatched)') AS strategy,"
        "       count(*) AS fills,"
        "       count(f.fee) AS fee_rows,"
        "       sum(f.quantity) AS contracts,"
        "       sum(f.fee) AS actual_usd,"
        "       avg(f.price) AS avg_price,"
        "       sum(ceil(%s * f.quantity * (f.price/100.0)"
        "                * (1 - f.price/100.0) * 100)/100) AS taker_usd,"
        "       sum(ceil(%s * f.quantity * (f.price/100.0)"
        "                * (1 - f.price/100.0) * 100)/100) AS maker_usd,"
        # What paper ACTUALLY books: kalshi_fee(price, qty=1) per paper trade, i.e. the taker
        # formula ceiled at a single contract. This is the number the gates are stated in.
        "       sum(f.quantity * ceil(%s * (f.price/100.0)"
        "                             * (1 - f.price/100.0) * 100)/100) AS paper_usd"
        " FROM fills f"
        " LEFT JOIN live_orders lo ON lo.kalshi_order_id = f.kalshi_order_id"
        " WHERE f.quantity IS NOT NULL AND f.quantity > 0 AND f.price IS NOT NULL"
        " GROUP BY 1 ORDER BY 4 DESC NULLS LAST",
        (TAKER_COEFF, MAKER_COEFF, TAKER_COEFF),
    )
    out = []
    for (strat, fills, fee_rows, contracts, actual, avg_price,
         taker_usd, maker_usd, paper_usd) in cur.fetchall():
        contracts = int(contracts or 0)
        avg_price = float(avg_price or 0)
        out.append({
            "strategy": strat,
            "fills": int(fills),
            "fee_rows": int(fee_rows or 0),
            "contracts": contracts,
            "actual_usd": float(actual or 0.0),
            "avg_price": avg_price,
            "maker_entry": is_maker_entry(strat),
            "taker_usd": float(taker_usd or 0.0),
            "maker_usd": float(maker_usd or 0.0),
            "paper_usd": float(paper_usd or 0.0),
        })
    return out


def _cpc(usd: float, contracts: int) -> float | None:
    return (100.0 * usd / contracts) if contracts else None


def _f(x, spec="{:.3f}") -> str:
    return spec.format(x) if x is not None else "n/a"


def report(rows: list[dict], min_contracts: int) -> None:
    if not rows:
        print("(no fills recorded)")
        return
    print("=== Fee reconciliation: what Kalshi BILLED vs what paper models ===")
    print("  (cents per contract; 'taker' is the formula paper/engine.py applies to EVERY book)")
    print(f"  {'book':22s} {'entry':>6} {'fills':>6} {'ct':>6} {'ACTUAL':>8} {'paper':>8}"
          f" {'taker':>8} {'maker':>8} {'overchg':>8} {'avg px':>7}")
    for r in rows:
        if r["contracts"] < min_contracts:
            continue
        a = _cpc(r["actual_usd"], r["contracts"])
        t = _cpc(r["taker_usd"], r["contracts"])
        m = _cpc(r["maker_usd"], r["contracts"])
        pp = _cpc(r["paper_usd"], r["contracts"])
        over = (pp - a) if (pp is not None and a is not None) else None
        print(f"  {r['strategy']:22s} {'maker' if r['maker_entry'] else 'taker':>6}"
              f" {r['fills']:6d} {r['contracts']:6d} {_f(a):>8} {_f(pp):>8} {_f(t):>8}"
              f" {_f(m):>8} {_f(over, '{:+.3f}'):>8} {r['avg_price']:7.1f}")

    mk = [r for r in rows if r["maker_entry"] and r["contracts"] >= min_contracts]
    tk = [r for r in rows if not r["maker_entry"] and r["contracts"] >= min_contracts]
    print("\n=== VERDICT ===")
    for label, group in (("MAKER-entry books", mk), ("TAKER-entry books (control)", tk)):
        ct = sum(r["contracts"] for r in group)
        if not ct:
            print(f"  {label}: no fills")
            continue
        a = _cpc(sum(r["actual_usd"] for r in group), ct) or 0.0
        pp = _cpc(sum(r["paper_usd"] for r in group), ct) or 0.0
        print(f"  {label}: {ct} contracts — Kalshi billed {a:.3f}c/ct, paper books {pp:.3f}c/ct"
              f"  ->  OVERCHARGE {pp - a:+.3f}c/contract")
    print("\n  If the taker model tracks the TAKER-entry control but not the maker books, the gap"
          "\n  is maker-vs-taker and not our arithmetic. The correction is UNIFORM, so it changes"
          "\n  no ranking — but every gate stated in absolute cents (docs/BOOK_REGISTRY.md) and"
          "\n  every `mmsell fill model` realizable figure is understated by that amount.")
    print("\n  NOT auto-applied to paper/engine.py on purpose: 14 Wmmsell*/Tmmsell* books are"
          "\n  mid-accumulation toward n>=100-150 gates, and switching fee models now would split"
          "\n  each book's own history across two. See docs/MMSELL_FEE_RECON.md.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-contracts", type=int, default=3,
                    help="hide books with fewer filled contracts (default 3)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            rows = load_rows(cur)
    report(rows, args.min_contracts)
    return 0


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


if __name__ == "__main__":
    raise SystemExit(main())
