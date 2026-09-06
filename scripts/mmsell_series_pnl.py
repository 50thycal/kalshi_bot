"""mmsell SERIES P&L LEADERBOARD — the standing per-series loss detector.

WHY THIS EXISTS
---------------
On 2026-09-05, while seeding the universe-review manifest, `KXNFLSPREAD` turned up with **382
settled markets and -$166.55** — more than a third of the whole mmsell family's 30-day paper
P&L, given back by ONE series in three weeks. Nobody was looking for it. Nothing in the repo
read per-series P&L: `mmsell_market_types` aggregates series into 15 contract TYPES,
`mmsell_universe_review` ranks series by *coverage* and prints P&L only as a footnote, and no
gate scores a series at all. The cell could have doubled and the first sign would have been
someone reading a different report.

This is that missing read: **every traded series, ranked by realized P&L, with the sample size
and the independence denominator next to it**, so the next KXNFLSPREAD surfaces in days.

THIS IS A REPORT, NOT A GATE, and the distinction is load-bearing.
`docs/MMSELL_ROADMAP.md` §1: measured per-trade sd is $0.2343 against a mean of $0.0065 — noise
is 36x signal, and a single series needs n ~ 800 DISTINCT markets before its confidence interval
excludes break-even. A gate scoring series on P&L would fire constantly on noise and would have
killed profitable cells long before it caught this one. Gates decide promotions; this decides
where a human looks next. It authorizes nothing and changes no lifecycle state.

WHAT TO READ, IN ORDER
----------------------
1. **`edge`** — `be% - loss%` in percentage points, the ONE column comparable across series.
   Each series is entered at a different premium, so a raw loss rate is scored against a
   different break-even: 12% is a disaster at 6c of premium and comfortable at 17c. Selling a
   tail for an average of W and losing an average of L, a cell breaks even at `W/(W+|L|)`.
   `edge <= 0` means the cell is not paying for its tail. (`mmsell_market_types` "trap 4".)
2. **`contests`, not `mkts`, is the independence denominator.** One NFL game carries a whole
   nested spread ladder, and a blowout resolves every deep rung against a seller at the same
   instant. KXNFLSPREAD's 382 markets are 44 preseason games; 2 of those games carried 48% of
   the loss. A series whose loss is spread over many contests is a different finding from one
   that is two bad afternoons, and `worst3%` — the share of a cell's contest-level losses
   carried by its three worst contests — separates them without anyone running a query.
3. **`live`** — whether the live lineage actually touched the series in the window. A negative
   cell no live book trades is research; a negative cell the live books are in is exposure.

Every number here is PAPER, fill-everything, no maker adverse-selection haircut
(`docs/MMSELL_FILL_MODEL.md`). The live column says who was exposed, not what live earned.

DELIBERATELY SELF-CONTAINED (stdlib + psycopg only), like every ops-channel script: the runner
never installs this package. Read-only; runs locally or through the ops channel:

    {"type": "script", "name": "mmsell_series_pnl"}
    {"type": "script", "name": "mmsell_series_pnl", "args": ["--days", "7"]}
    {"type": "script", "name": "mmsell_series_pnl", "args": ["--all-time", "--min-n", "50"]}
    {"type": "script", "name": "mmsell_series_pnl", "args": ["--maxyes", "7"]}   # live band only
    {"type": "script", "name": "mmsell_series_pnl", "args": ["--series", "KXNFLSPREAD"]}
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

RO_OPTIONS = "-c default_transaction_read_only=on"

#: Regimes whose series share an underlying CONTEST across series prefixes, so the honest
#: independence unit is the contest and not the market. Mirrors the intent of
#: `kalshi_bot/mmsell/regimes.contest_key_of` (XOS-000020) without importing it — a script on
#: the ops runner has no package to import from. The grouping here is deliberately WEAKER than
#: the worker's: it keys on the raw event token, so it groups rungs and sides within one series
#: but never claims to group across series. That under-counts correlation; it never invents it.
CONTEST_TOKEN_INDEX = 1  # KXNFLSPREAD-25AUG14ATLDET-DET3 -> 25AUG14ATLDET


def series_of(ticker: str) -> str:
    return (ticker or "").split("-", 1)[0].upper()


def contest_of(ticker: str) -> str:
    """The underlying contest, as far as a ticker alone can say.

    `KXNFLSPREAD-25AUG14ATLDET-DET3` -> `KXNFLSPREAD:25AUG14ATLDET`. A ticker with no event
    token (`KXPAYROLLS-26SEP`) is its own contest, which is the correct read: nothing else
    settles with it.
    """
    parts = (ticker or "").split("-")
    if len(parts) <= CONTEST_TOKEN_INDEX:
        return (ticker or "").upper()
    return f"{parts[0].upper()}:{parts[CONTEST_TOKEN_INDEX].upper()}"


def breakeven(pnls_c: list[float]) -> tuple[float | None, float | None]:
    """(break-even loss rate, edge in percentage points) from this cell's own realized sizes.

    Same definition as `mmsell_market_types._breakeven`, duplicated for the same reason the
    taxonomy is: an ops script cannot import the package. `tests/test_mmsell_series_pnl.py`
    pins the two against each other on shared fixtures so they cannot drift.

    Returns (None, None) when the cell has no losses or no wins — there is nothing to normalize
    against, and a cell that has never lost has not yet been measured.
    """
    wins = [p for p in pnls_c if p > 0]
    losses = [p for p in pnls_c if p < 0]
    if not wins or not losses:
        return None, None
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    be = avg_win / (avg_win + avg_loss)
    return be, 100.0 * (be - len(losses) / len(pnls_c))


def summarize(rows: list[dict]) -> dict:
    """One series cell. `rows` are per-trade dicts normalized to ONE contract."""
    pnls = [r["pnl_c"] for r in rows]
    n = len(pnls)
    losses = [p for p in pnls if p < 0]
    by_contest: dict[str, float] = defaultdict(float)
    for r in rows:
        by_contest[r["contest"]] += r["pnl_c"]
    contest_pnls = sorted(by_contest.values())
    worst3 = sum(w for w in contest_pnls[:3] if w < 0)
    # Denominator: the cell's GROSS contest-level losses, not its net total. Dividing by the
    # net is unbounded above — a cell whose winners nearly offset its losers has a tiny
    # denominator, so the share explodes. The first production run (2026-09-06) printed
    # KXBTCD at 322% and KXWTI at 153%, which is not a share of anything. Against gross
    # losses the column is a real proportion in [0, 1] and means what it says.
    gross_loss = sum(w for w in contest_pnls if w < 0)
    total_c = sum(pnls)
    be, edge = breakeven(pnls)
    return {
        "n": n,
        "mkts": len({r["ticker"] for r in rows}),
        "contests": len(by_contest),
        "books": len({r["book"] for r in rows}),
        "total": total_c / 100.0,          # dollars, at one contract per trade
        "mean": total_c / n,               # cents per trade
        "loss_rate": len(losses) / n,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "be": be,
        "edge": edge,
        "entry": sum(r["entry_c"] for r in rows) / n,
        # How much of a LOSING cell's damage came from its three worst contests, as a share of
        # ALL its losing contests. A broad drift and two catastrophic afternoons need different
        # responses, and this is the cheapest column that tells them apart. None when the cell
        # made money — there is no loss worth triaging, and printing the column there invites
        # reading a concentration figure as a problem.
        "worst3_share": (worst3 / gross_loss) if (total_c < 0 and gross_loss < 0) else None,
        "live": sorted({r["book"] for r in rows if r["live"]}),
    }


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        return "postgresql://" + url.split("://", 1)[1]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def load_trades(cur, days: int | None, statuses: tuple[str, ...], maxyes: int | None,
                all_strategies: bool) -> list[dict]:
    """Every mmsell trade with a realized P&L, normalized to one contract.

    `settled,closed_sl` rather than `settled` alone: filtering to settled drops every position
    a stop actually closed, the exact reading error recorded against the anchor books in
    `docs/BOOK_REGISTRY.md`. `closed_void` stays out — a voided market is a no-trade.

    Twin books are excluded: a twin enters at the LIVE maker price with live sizing, so pooling
    it mixes two entry conventions into one cell.
    """
    where = ["status = ANY(%s)", "NOT coalesce(legacy, false)",
             "pnl IS NOT NULL", "quantity IS NOT NULL", "quantity > 0",
             "assumed_price IS NOT NULL",
             "strategy NOT IN (SELECT twin_tag FROM live_paper_twins)"]
    params: list[object] = [list(statuses)]
    if not all_strategies:
        where.append("strategy LIKE '%%mmsell%%'")
    if days is not None:
        where.append("created_at >= now() - make_interval(days => %s)")
        params.append(days)
    cur.execute(
        "SELECT market_ticker, strategy, side, assumed_price, quantity, pnl"
        "  FROM paper_trades WHERE " + " AND ".join(where), params)
    rows = cur.fetchall()

    cur.execute("SELECT DISTINCT strategy FROM live_orders WHERE strategy IS NOT NULL")
    live_books = {r[0] for r in cur.fetchall()}

    out: list[dict] = []
    for ticker, book, side, px, qty, pnl in rows:
        # The premium we collected, in cents. mmsell sells a cheap YES tail by BUYING NO at
        # `assumed_price`, so the tail's own price is 100 - assumed_price; the strangle's
        # mirror leg buys YES and its price is already the tail's.
        entry_c = float(100 - int(px)) if (side or "no") == "no" else float(px)
        if maxyes is not None and entry_c > maxyes:
            continue
        out.append({
            "ticker": ticker,
            "series": series_of(ticker),
            "contest": contest_of(ticker),
            "book": book,
            "live": book in live_books,
            "entry_c": entry_c,
            "pnl_c": float(pnl) / int(qty) * 100.0,
        })
    return out


HDR = (f"  {'series':<24} {'n':>6} {'mkts':>6} {'cnts':>5} {'bks':>4} {'total$':>9}"
       f" {'c/trade':>8} {'entry':>6} {'loss%':>6} {'be%':>6} {'edge':>7}"
       f" {'worst3%':>8}  live")


def _f(x, spec="{:+.1f}") -> str:
    return spec.format(x) if x is not None else "n/a"


def _row(series: str, s: dict) -> str:
    live = ",".join(s["live"][:3]) + ("..." if len(s["live"]) > 3 else "") or "-"
    return (f"  {series:<24} {s['n']:>6} {s['mkts']:>6} {s['contests']:>5} {s['books']:>4}"
            f" {s['total']:>+9.2f} {s['mean']:>+7.2f}c {s['entry']:>5.1f}"
            f" {100.0*s['loss_rate']:>5.1f}%"
            f" {_f(100.0*s['be'] if s['be'] is not None else None, '{:5.1f}'):>5}%"
            f" {_f(s['edge'], '{:+6.1f}'):>7}"
            f" {_f(100.0*s['worst3_share'] if s['worst3_share'] is not None else None, '{:6.0f}'):>7}%"
            f"  {live}")


def report(trades: list[dict], min_n: int, top: int, window: str, maxyes: int | None,
           only: str | None) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[t["series"]].append(t)
    cells = {k: summarize(v) for k, v in groups.items() if len(v) >= min_n}

    band = f", entry <= {maxyes}c" if maxyes is not None else ", all entry prices"
    print(f"=== mmsell SERIES P&L — {window}{band} ===")
    print(f"{len(trades)} trades across {len(groups)} series; {len(cells)} clear min-n={min_n}.")
    print("PAPER, fill-everything. A report, never a gate — see the module docstring.\n")

    if only:
        s = cells.get(only.upper()) or (summarize(groups[only.upper()])
                                        if groups.get(only.upper()) else None)
        if s is None:
            print(f"  no trades for {only.upper()} in this window.")
            return
        print(HDR)
        print(_row(only.upper(), s))
        return

    ordered = sorted(cells.items(), key=lambda kv: kv[1]["total"])
    losers = [(k, s) for k, s in ordered if s["total"] < 0]
    print(f"--- WORST {min(top, len(losers))} SERIES BY REALIZED P&L "
          f"({len(losers)} of {len(cells)} are negative) ---")
    print(HDR)
    for series, s in ordered[:top]:
        print(_row(series, s))

    print(f"\n--- BEST {top} ---")
    print(HDR)
    for series, s in list(reversed(ordered))[:top]:
        print(_row(series, s))

    exposed = [(k, s) for k, s in ordered if s["total"] < 0 and s["live"]]
    print(f"\n--- NEGATIVE AND TOUCHED BY A LIVE BOOK ({len(exposed)}) ---")
    print("    Real-money lineage was in these cells. The dollars below are still PAPER; this")
    print("    says who was exposed, not what live earned.")
    if not exposed:
        print("    (none)")
    else:
        print(HDR)
        for series, s in exposed[:top]:
            print(_row(series, s))

    print("\nHOW TO READ THIS")
    print("  edge     be% - loss%, in percentage points. The ONE column comparable across")
    print("           series, because each is entered at a different premium. edge <= 0 means")
    print("           the cell is not paying for its tail.")
    print("  cnts     distinct CONTESTS — the honest independence denominator. One game carries")
    print("           a whole nested ladder and settles it against a seller at one instant, so")
    print("           `mkts` overstates n. Read c/trade against `cnts`, not against `n`.")
    print("  worst3%  of everything this cell lost at the contest level, the share that came from")
    print("           its three worst contests. High means a few catastrophic afternoons (a")
    print("           concentration problem, which the contest cap addresses); low means a broad")
    print("           negative drift (a selection problem, which it does not).")
    print("\n  A negative cell here is a PLACE TO LOOK, not a verdict. At this book's measured")
    print("  variance a single series needs n ~ 800 distinct markets before its CI excludes")
    print("  break-even (docs/MMSELL_ROADMAP.md §1); almost nothing on this page has that.")
    print("  Acting on a cell is a universe change to a running book, which under NEW_ONLY is")
    print("  a new epoch or version — never a config tweak, and never a side effect of a report.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30, help="lookback window (default 30)")
    ap.add_argument("--all-time", action="store_true", help="ignore --days")
    ap.add_argument("--min-n", type=int, default=20,
                    help="hide series below this many trades (default 20)")
    ap.add_argument("--top", type=int, default=20, help="rows per table (default 20)")
    ap.add_argument("--maxyes", type=int, default=None,
                    help="restrict to entries at or below this tail price in cents "
                         "(7 = the live mmsell10 band); default is every price")
    ap.add_argument("--series", default=None, help="print one series only")
    ap.add_argument("--status", default="settled,closed_sl",
                    help="comma-separated paper_trades statuses (default settled,closed_sl)")
    ap.add_argument("--all-strategies", action="store_true",
                    help="include non-mmsell books (they trade a different universe)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    days = None if args.all_time else args.days
    statuses = tuple(s.strip() for s in args.status.split(",") if s.strip())
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            trades = load_trades(cur, days, statuses, args.maxyes, args.all_strategies)
    window = "all time" if days is None else f"last {days} days"
    report(trades, args.min_n, args.top, window, args.maxyes, args.series)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
