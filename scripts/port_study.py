"""PORT — portfolio composition, correlation & realizable-allocation measurement.
(idea-model Area 3: portfolio construction, 2026-07-22; see docs/PORT_THESIS.md)

The portfolio-level view the "$100/mo from ANY COMBINATION" north star implies but the bot never
built. It is a DIAGNOSTIC, not an edge: from `paper_trades` it builds each book's daily realized
P&L series and answers — how many genuinely +EV, low-correlation books does the portfolio actually
contain, and does any capital weighting beat flat sizing at equal-or-lower drawdown?

**Realizable, not paper.** The mmsell family's paper P&L is a known fill-model MIRAGE (a resting
maker order can't fill the winners). This probe applies the fill-model per-book realizable ¢/trade
(`MMSELL_FILL_MODEL.md` §4) as a transparent, sourced constant: it scales each mmsell book's daily
P&L by realizable/paper so the correlation + allocation run on realizable series. Non-mmsell books
(weather/theta — taker or unaffected) use paper as realizable.

**Independence, not raw count.** The 11 mmsell variants trade overlapping markets, so they are ONE
correlated cluster, not 11 bets. The effective independent-book count is what the P1 gate measures.

No-lookahead: P2's flat-vs-weighted comparison fits weights on the earlier time-split and evaluates
on the later. Read-only DB (psycopg). Usage:
    {"type":"script","name":"port_study","args":["--active-days","10"],"id":"port-1"}
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from datetime import date, timedelta
from statistics import mean, pstdev

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

# Fill-model realizable ¢/trade per mmsell book (MMSELL_FILL_MODEL.md §4). Only mmsell9/mmsell10
# are realizably +EV; the rest are paper mirages (realizable ~0/neg). Sourced constant, applied
# transparently to scale each mmsell book's paper daily P&L to realizable.
MMSELL_REALIZABLE_CPT = {
    "mmsell": 0.11, "mmsell1": 0.18, "mmsell2": 4.92, "mmsell3": -1.06, "mmsell4": -1.05,
    "mmsell5": 0.80, "mmsell6": -0.43, "mmsell7": -1.23, "mmsell8": 1.89, "mmsell9": 1.49,
    "mmsell10": 1.40, "mmsell11": -0.86,
}


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return float("nan")
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / math.sqrt(sx * sy)


def max_drawdown(daily: list[float]) -> float:
    """Max peak-to-trough drawdown of the cumulative P&L path (in the same $ unit)."""
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for d in daily:
        cum += d
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return mdd


def family(book: str) -> str:
    """Strategy stem — books sharing it trade overlapping markets and are ONE bet (P3, the
    independence gate). Strips window suffixes (_h14) and trailing variant digits:
    mmsell10 -> mmsell, weather_con_h14 -> weather_con, theta4 -> theta."""
    b = re.sub(r"_h\d+$", "", book)
    b = re.sub(r"\d+$", "", b)
    return b


def realizable_scale(book: str, paper_cpt_cents: float) -> float:
    """Multiplier turning a book's paper daily P&L into realizable. Non-mmsell: 1.0. mmsell: the
    fill-model realizable/paper ratio (sign-flipping for mirages); unknown mmsell variant -> 0."""
    if not book.startswith("mmsell"):
        return 1.0
    r = MMSELL_REALIZABLE_CPT.get(book)
    if r is None:
        return 0.0
    if abs(paper_cpt_cents) < 1e-9:
        return 0.0
    return r / paper_cpt_cents


def aligned(series_a: dict, series_b: dict) -> tuple[list[float], list[float]]:
    """Two daily {date: pnl} series aligned over their overlapping date range, 0-filled on
    non-trading days within that range (a book's P&L is 0 on a day it did not trade)."""
    if not series_a or not series_b:
        return [], []
    lo = max(min(series_a), min(series_b))
    hi = min(max(series_a), max(series_b))
    if hi < lo:
        return [], []
    xs, ys = [], []
    d = lo
    while d <= hi:
        xs.append(series_a.get(d, 0.0))
        ys.append(series_b.get(d, 0.0))
        d += timedelta(days=1)
    return xs, ys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PORT portfolio measurement")
    ap.add_argument("--active-days", type=int, default=10,
                    help="a book is 'active' if it traded within this many days of the latest")
    ap.add_argument("--min-n", type=int, default=100, help="trade floor for the P1 +EV gate")
    ap.add_argument("--corr-cluster", type=float, default=0.7, help="|corr| that merges a cluster")
    ap.add_argument("--corr-indep", type=float, default=0.5, help="|corr| below which books count as independent")
    args = ap.parse_args(argv)

    print("PORT — portfolio composition, correlation & realizable-allocation (read-only)\n")

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    import psycopg

    series: dict[str, dict[date, float]] = {}
    ntr: dict[str, int] = {}
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT strategy, closed_at::date AS d, sum(pnl) AS day_pnl, count(*) AS n"
                " FROM paper_trades"
                " WHERE status='settled' AND pnl IS NOT NULL AND legacy=false"
                " AND closed_at IS NOT NULL AND strategy IS NOT NULL"
                " GROUP BY strategy, closed_at::date"
            )
            for strat, d, day_pnl, n in cur.fetchall():
                series.setdefault(strat, {})[d] = float(day_pnl)
                ntr[strat] = ntr.get(strat, 0) + int(n)

    if not series:
        print("No settled paper_trades found.")
        return 0
    global_last = max(max(s) for s in series.values())
    active_cut = global_last - timedelta(days=args.active_days)

    # ---- per-book paper + realizable stats -------------------------------------------
    rows = []
    for book, s in series.items():
        n = ntr[book]
        total = sum(s.values())
        paper_cpt = (total / n) * 100 if n else 0.0  # ¢/trade
        scale = realizable_scale(book, paper_cpt)
        real_daily = {d: v * scale for d, v in s.items()}
        real_total = sum(real_daily.values())
        real_cpt = (real_total / n) * 100 if n else 0.0
        vals = list(real_daily.values())
        dmean = mean(vals) if vals else 0.0
        dsd = pstdev(vals) if len(vals) > 1 else 0.0
        sharpe = dmean / dsd if dsd > 0 else 0.0
        rows.append({
            "book": book, "n": n, "paper_total": total, "paper_cpt": paper_cpt,
            "real_cpt": real_cpt, "real_total": real_total, "sharpe": sharpe,
            "mdd": max_drawdown([real_daily[d] for d in sorted(real_daily)]),
            "last": max(s), "active": max(s) >= active_cut,
            "series": real_daily,        # realizable (for +EV allocation) — may be scaled
            "series_paper": dict(s),     # raw paper daily (for correlation — no sign-flip)
        })
    rows.sort(key=lambda r: -r["real_total"])

    print(f"latest trading day: {global_last}   active cutoff: >= {active_cut}\n")
    print(f"  {'book':>20} {'n':>5} {'paper$':>8} {'paper¢':>7} {'REAL¢':>7} {'real$':>8} "
          f"{'Shrp':>5} {'maxDD$':>7} {'act':>3}")
    for r in rows:
        if r["n"] < 20:
            continue
        print(f"  {r['book']:>20} {r['n']:>5} {r['paper_total']:>8.2f} {r['paper_cpt']:>7.2f} "
              f"{r['real_cpt']:>7.2f} {r['real_total']:>8.2f} {r['sharpe']:>5.2f} "
              f"{r['mdd']:>7.2f} {'Y' if r['active'] else '-':>3}")

    # ---- realizable +EV, active, n>=min_n candidates ---------------------------------
    cand = [r for r in rows if r["active"] and r["n"] >= args.min_n and r["real_cpt"] > 0]
    print(f"\nrealizable +EV & active & n>={args.min_n}: "
          f"{[r['book'] for r in cand] or '(none)'}")

    # ---- correlation matrix + clustering (independence) ------------------------------
    print("\n== cross-book daily-P&L correlation (raw paper series, active books) ==")
    active_books = [r for r in rows if r["active"] and r["n"] >= 20]
    labels = [r["book"] for r in active_books]
    cmat: dict[tuple[str, str], float] = {}
    for i, a in enumerate(active_books):
        for b in active_books[i + 1:]:
            xs, ys = aligned(a["series_paper"], b["series_paper"])
            c = pearson(xs, ys) if len(xs) >= 5 else float("nan")
            cmat[(a["book"], b["book"])] = c
            cmat[(b["book"], a["book"])] = c
    # print a compact matrix (abbreviated labels)
    abbr = {lb: (lb[:10]) for lb in labels}
    print("       " + " ".join(f"{abbr[lb][:6]:>6}" for lb in labels))
    for a in labels:
        cells = []
        for b in labels:
            if a == b:
                cells.append(f"{'1.00':>6}")
            else:
                c = cmat.get((a, b), float("nan"))
                cells.append(f"{c:>6.2f}" if not math.isnan(c) else f"{'--':>6}")
        print(f"  {abbr[a][:6]:>6} " + " ".join(cells))

    # greedy single-linkage clusters at |corr| >= corr_cluster
    parent = {lb: lb for lb in labels}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # P3: same-strategy-family books are ONE cluster by construction (overlapping markets),
    # regardless of measured correlation — a recent low-variance variant (mmsell10) can measure
    # below the cutoff against the older control yet still be the SAME maker-sell bet.
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            if family(a) == family(b):
                parent[find(a)] = find(b)
    # then merge cross-family clusters by measured paper-return correlation
    for (a, b), c in cmat.items():
        if not math.isnan(c) and abs(c) >= args.corr_cluster:
            parent[find(a)] = find(b)
    clusters: dict[str, list[str]] = {}
    for lb in labels:
        clusters.setdefault(find(lb), []).append(lb)
    print(f"\nclusters (|corr|>={args.corr_cluster}): "
          f"{[sorted(v) for v in clusters.values()]}")

    # effective independent realizable-+EV clusters
    cand_books = {r["book"] for r in cand}
    ev_clusters = [v for v in clusters.values() if any(b in cand_books for b in v)]
    n_ev_indep = len(ev_clusters)
    print(f"effective INDEPENDENT realizable-+EV book-clusters: {n_ev_indep} "
          f"{[sorted(v) for v in ev_clusters]}")

    # ---- verdict ---------------------------------------------------------------------
    print("\n== verdict (Area 3: is portfolio allocation worth building yet?) ==")
    print(f"  P1 (>= 2 independent realizable-+EV books, n>={args.min_n}, corr<{args.corr_indep}): "
          f"{n_ev_indep} found")
    if n_ev_indep >= 2:
        # P2: flat vs inverse-variance on the later time-split half (only meaningful here)
        print("  P1 PASS -> evaluating P2 (flat vs inverse-variance, out-of-sample time split)")
        reps = [r for r in rows if r["book"] in cand_books]
        alld = sorted({d for r in reps for d in r["series"]})
        mid = alld[len(alld) // 2]
        def port(daily_weight):
            later = [d for d in alld if d > mid]
            pv = []
            for d in later:
                pv.append(sum(daily_weight[r["book"]] * r["series"].get(d, 0.0) for r in reps))
            return pv
        var = {r["book"]: (pstdev(list(r["series"].values())) or 1.0) ** 2 for r in reps}
        inv = {b: 1.0 / var[b] for b in cand_books}
        tot = sum(inv.values())
        w_iv = {b: inv[b] / tot for b in cand_books}
        w_flat = {b: 1.0 / len(cand_books) for b in cand_books}
        for name, w in (("flat", w_flat), ("inv-var", w_iv)):
            pv = port(w)
            mo = (mean(pv) * 30) if pv else 0.0
            sd = pstdev(pv) if len(pv) > 1 else 0.0
            print(f"    {name:>8}: $/mo {mo:+.2f}  Sharpe {mean(pv)/sd if sd else 0:+.2f}  "
                  f"maxDD ${max_drawdown(pv):.2f}")
        print("  -> compare: inv-var beats flat on $/mo at <= drawdown? build allocation layer; "
              "else flat is fine. (See P2 threshold in docs/PORT_THESIS.md.)")
    else:
        print("  VERDICT: ALLOCATION PREMATURE — fewer than 2 independent realizable-+EV books. "
              "Diversification/sizing cannot manufacture EV from ~0/neg books; the binding "
              "constraint is EDGE SUPPLY, not allocation. Actions: (1) hygiene — collapse the "
              "mmsell variant cluster to its realizable member (mmsell10) and prune the "
              "persistently-negative weather cells; (2) keep this script as the standing portfolio "
              "view (loop-checker / full-update) so the allocation question re-opens automatically "
              "the moment a 2nd independent +EV book lands. A valid, valuable measurement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
