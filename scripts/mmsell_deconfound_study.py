"""Universe/regime deconfounding for the mmsell `scheduled_settle`-vs-`price_ceiling` question.

The live-canary comparison armed on 2026-08-15 reads `Lmmsell8` (treatment, "scheduled settle")
against `Lmmsell10` (control, "price ceiling") as though the entry RULE were the only thing that
differs between them. It is not. Read straight off `mmsell_variants` in kalshi_bot/config.py:

    mmsell8  / Lmmsell8   lo=5, hi=12, only=BTCD+ETH+ASG+HRDERBY
    mmsell10 / Lmmsell10  lo=5, hi=10, maxyes=7

Three things differ at once — the series universe (`only=`), the entry-price band (5-12 vs an
effective 5-7 once `maxyes` binds), and the settle-mode mix that follows from the universe. A
delta between the two books therefore has no single interpretation, and no amount of sample
repairs that: it is a design defect, not a power problem.

This script measures how much of the apparent difference is UNIVERSE rather than RULE, using the
comparisons that hold one factor fixed at a time:

  A  same logic, different universe   `mmsell5` (non-crypto) vs `mmsell8` (crypto) — byte-identical
                                      band lo=5,hi=12; the ONLY difference is the `only=` list.
  B  same book, different universe    the crypto vs non-crypto slices INSIDE one unfiltered book
                                      (`mmsell10`, `mmsell9`, ...). Rule held exactly fixed.
  C  same universe, different rule    `Lmmsell8` against `Lmmsell10`'s CRYPTO SLICE over the same
                                      calendar window — asset class held fixed, rule varying.
  D  shared support                   the cell where BOTH books' filters admit the same market
                                      (crypto AND yes-price in 5..7). Everything outside it is
                                      flow one book can take and the other structurally cannot.

Deliberately NOT computed: an aggregate `Lmmsell8` minus aggregate `Lmmsell10` "treatment effect".
That number is the confound.

Read-only; stdlib + psycopg only, so it runs on the ops GitHub Actions runner. Prices are integer
cents; the per-market P&L unit is cents per contract.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mmsell_market_types import (  # noqa: E402
    RO_OPTIONS,
    _to_libpq_url,
    classify,
    pctile,
    series_of,
)
from mmsell_seasonal import regime as regime_of  # noqa: E402

# `only=` allowlists are matched as case-insensitive SERIES SUBSTRINGS by
# Settings.mmsell_variant_list, so the test here has to be the same substring test — not a
# prefix test and not a regime lookup, or this script would answer a different question from
# the one the running book asks.
MMSELL8_ONLY = ("BTCD", "ETH", "ASG", "HRDERBY")

CRYPTO_REGIME = "Crypto"


# --- small stats ----------------------------------------------------------------------------

def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _sd(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _se(xs: list[float]) -> float | None:
    s = _sd(xs)
    return s / math.sqrt(len(xs)) if s is not None else None


def _norm_sf(z: float) -> float:
    """One-sided upper tail of the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def welch(a: list[float], b: list[float]) -> dict:
    """Two-sample comparison of means, a - b, with unequal variances.

    Normal rather than t: every cell here is either large enough that the difference is
    immaterial or so small that the interval is reported as advisory anyway.
    """
    ma, mb = _mean(a), _mean(b)
    sa, sb = _se(a), _se(b)
    if ma is None or mb is None:
        return {"delta": None}
    d = ma - mb
    if sa is None or sb is None:
        return {"delta": d, "se": None, "lo": None, "hi": None, "p": None}
    se = math.sqrt(sa * sa + sb * sb)
    if se <= 0:
        return {"delta": d, "se": 0.0, "lo": d, "hi": d, "p": None}
    z = d / se
    return {
        "delta": d, "se": se,
        "lo": d - 1.96 * se, "hi": d + 1.96 * se,
        "p": 2.0 * _norm_sf(abs(z)),
    }


# --- loading --------------------------------------------------------------------------------

def load(cur, since: str | None, until: str | None) -> list[dict]:
    """Every settled mmsell paper row, normalized to one contract, with the fields the
    universe question needs: asset class, settle mode, market type, entry price, model edge,
    and realized hold time. Twin books are KEPT — this study needs them (they are the live
    counterfactual) and tags them explicitly instead."""
    where = [
        "strategy LIKE '%%mmsell%%'",
        "status IN ('settled','closed_sl')",
        "NOT coalesce(legacy, false)",
        "pnl IS NOT NULL",
        "quantity IS NOT NULL AND quantity > 0",
    ]
    params: list[object] = []
    if since:
        where.append("created_at >= %s")
        params.append(since)
    if until:
        where.append("created_at < %s")
        params.append(until)
    cur.execute(
        "SELECT market_ticker, strategy, status, assumed_price, quantity, pnl, fees,"
        "       model_probability, edge, created_at, closed_at"
        "  FROM paper_trades WHERE " + " AND ".join(where),
        params,
    )
    # Drain BEFORE the next execute — re-executing the cursor discards this result set.
    fetched = cur.fetchall()
    twins = set()
    try:
        cur.execute("SELECT twin_tag FROM live_paper_twins")
        twins = {r[0] for r in cur.fetchall()}
    except Exception:  # table may not exist in a stripped environment
        pass

    out: list[dict] = []
    for (tkr, book, status, px, qty, pnl, fees, mp, edge, created, closed) in fetched:
        series = series_of(tkr)
        mtype, mode = classify(series)
        regime = regime_of(series)
        entry_c = (100 - int(px)) if px is not None else None
        hold_h = None
        if created is not None and closed is not None:
            hold_h = (closed - created).total_seconds() / 3600.0
        out.append({
            "ticker": tkr, "series": series, "book": book, "status": status,
            "type": mtype, "mode": mode, "regime": regime,
            "asset": "crypto" if regime == CRYPTO_REGIME else "noncrypto",
            "entry_c": entry_c,
            "edge": float(edge) if edge is not None else None,
            "model_p": float(mp) if mp is not None else None,
            "pnl_c": float(pnl) / int(qty) * 100.0,
            "fees": float(fees) if fees is not None else None,
            "hold_h": hold_h,
            "created": created,
            "is_twin": book in twins,
            "mmsell8_eligible": any(k in series.upper() for k in MMSELL8_ONLY),
        })
    return out


def per_market(rows: list[dict]) -> list[float]:
    """Collapse to one P&L per (book, market): contracts on one market share one settlement,
    so the market is the independent unit and the trade is not."""
    agg: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        agg[(r["book"], r["ticker"])].append(r["pnl_c"])
    return [sum(v) / len(v) for v in agg.values()]


def sel(rows: list[dict], **kw) -> list[dict]:
    out = rows
    for k, v in kw.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple, set)):
            out = [r for r in out if r.get(k) in v]
        else:
            out = [r for r in out if r.get(k) == v]
    return out


def band(rows: list[dict], lo: float, hi: float) -> list[dict]:
    return [r for r in rows if r["entry_c"] is not None and lo <= r["entry_c"] <= hi]


# --- rendering ------------------------------------------------------------------------------

def f(x, spec="{:+.2f}") -> str:
    return spec.format(x) if x is not None else "n/a"


def head(title: str) -> None:
    print()
    print("=" * 92)
    print(title)
    print("=" * 92)


def cell(label: str, rows: list[dict], width: int = 34) -> str:
    pm = per_market(rows)
    m, s, sd = _mean(pm), _se(pm), _sd(pm)
    return (f"  {label:<{width}} n={len(pm):>5}  mean={f(m):>8}  se={f(s, '{:.2f}'):>7}  "
            f"sd={f(sd, '{:.1f}'):>6}")


def contrast(label: str, a: list[dict], b: list[dict], a_name: str, b_name: str) -> None:
    pa, pb = per_market(a), per_market(b)
    w = welch(pa, pb)
    print(f"  {label}")
    print(cell(a_name, a))
    print(cell(b_name, b))
    if w.get("delta") is None:
        print("    delta: n/a — one side is empty")
        return
    if w.get("se") is None:
        print(f"    delta = {f(w['delta'])}c/ct — NOT IDENTIFIED (a side has n<2)")
        return
    line = f"    delta = {f(w['delta'])}c/ct   95% CI [{f(w['lo'])}, {f(w['hi'])}]"
    if w.get("p") is not None:
        line += f"   p={w['p']:.3f}"
    print(line)


# --- sections -------------------------------------------------------------------------------

def section_census(rows: list[dict], min_n: int) -> None:
    head("1. UNIVERSE CENSUS — what each book's flow is actually made of")
    print(f"  {'book':<15} {'mkts':>6} {'crypto%':>8} {'sched%':>7} {'inplay%':>8} "
          f"{'disc%':>6} {'entry':>6} {'c/ct':>7}  top series")
    print("  " + "-" * 88)
    by_book: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_book[r["book"]].append(r)
    for book in sorted(by_book):
        rs = by_book[book]
        mk = {(r["book"], r["ticker"]) for r in rs}
        if len(mk) < min_n:
            continue
        n = len(rs)
        cry = sum(1 for r in rs if r["asset"] == "crypto") / n * 100
        sch = sum(1 for r in rs if r["mode"] == "scheduled") / n * 100
        inp = sum(1 for r in rs if r["mode"] == "in_play") / n * 100
        dis = sum(1 for r in rs if r["mode"] == "discrete") / n * 100
        ent = _mean([r["entry_c"] for r in rs if r["entry_c"] is not None])
        pm = _mean(per_market(rs))
        top = sorted({s: sum(1 for r in rs if r["series"] == s) for s in {r["series"] for r in rs}}
                     .items(), key=lambda kv: -kv[1])[:3]
        tops = ", ".join(f"{s}({c})" for s, c in top)
        print(f"  {book:<15} {len(mk):>6} {cry:>7.1f}% {sch:>6.1f}% {inp:>7.1f}% {dis:>5.1f}% "
              f"{f(ent, '{:.1f}'):>6} {f(pm):>7}  {tops}")


def section_proxy(rows: list[dict]) -> None:
    head("2. IS `scheduled_settle` ELIGIBILITY JUST A PROXY FOR CRYPTO HOURLIES?")
    elig = [r for r in rows if r["mmsell8_eligible"]]
    print(f"  Markets matching mmsell8's own allowlist {MMSELL8_ONLY} across ALL mmsell books:")
    print(f"    settled rows: {len(elig)}   distinct markets: {len({r['ticker'] for r in elig})}")
    by_key = defaultdict(int)
    for r in elig:
        by_key[(r["series"], r["regime"], r["mode"])] += 1
    print(f"  {'series':<20} {'regime':<12} {'mode':<10} {'rows':>6}")
    print("  " + "-" * 52)
    for (s, g, m), c in sorted(by_key.items(), key=lambda kv: -kv[1]):
        print(f"  {s:<20} {g:<12} {m:<10} {c:>6}")
    cry = sum(1 for r in elig if r["asset"] == "crypto")
    print()
    print(f"  crypto share of mmsell8-eligible flow: {cry}/{len(elig)} = "
          f"{(cry / len(elig) * 100) if elig else 0:.1f}%")
    # The converse: how much of the SCHEDULED universe the allowlist actually reaches.
    sched = [r for r in rows if r["mode"] == "scheduled"]
    sched_nc = [r for r in sched if r["asset"] != "crypto"]
    reached = [r for r in sched_nc if r["mmsell8_eligible"]]
    print(f"  scheduled-settle rows overall: {len(sched)}  "
          f"of which NON-crypto: {len(sched_nc)}")
    print(f"  non-crypto scheduled rows the mmsell8 allowlist admits: {len(reached)} "
          f"({(len(reached) / len(sched_nc) * 100) if sched_nc else 0:.1f}%)")
    if sched_nc:
        by_s = defaultdict(int)
        for r in sched_nc:
            by_s[r["series"]] += 1
        print("  non-crypto scheduled series the allowlist MISSES (top 12):")
        for s, c in sorted(by_s.items(), key=lambda kv: -kv[1])[:12]:
            miss = "" if any(k in s.upper() for k in MMSELL8_ONLY) else "  <- missed"
            print(f"    {s:<24} {c:>6}{miss}")


def section_a(rows: list[dict]) -> None:
    head("3. COMPARISON A — same logic, different universe (mmsell5 vs mmsell8)")
    print("  Both books are lo=5,hi=12 with an `only=` allowlist. mmsell5's list is")
    print("  TOTAL+SPREAD+ASG+HRDERBY; mmsell8's is BTCD+ETH+ASG+HRDERBY. Same band, same")
    print("  sizing, same management: the ONLY difference is which series are admitted.")
    a = sel(rows, book="mmsell8")
    b = sel(rows, book="mmsell5")
    if not a or not b:
        print("  one side absent — cannot run")
        return
    da = sorted(r["created"] for r in a)
    db = sorted(r["created"] for r in b)
    lo = max(da[0], db[0])
    hi = min(da[-1], db[-1])
    print(f"  overlapping window: {lo:%Y-%m-%d} .. {hi:%Y-%m-%d}")
    aw = [r for r in a if lo <= r["created"] <= hi]
    bw = [r for r in b if lo <= r["created"] <= hi]
    print()
    contrast("full history (unmatched windows):", a, b, "mmsell8  (crypto universe)",
             "mmsell5  (non-crypto universe)")
    print()
    contrast("window-matched:", aw, bw, "mmsell8  (crypto universe)",
             "mmsell5  (non-crypto universe)")


def section_b(rows: list[dict], books: list[str]) -> None:
    head("4. COMPARISON B — same book, different universe (rule held exactly fixed)")
    print("  Books with no `only=` filter see both universes under ONE rule, so their internal")
    print("  crypto/non-crypto split is an unconfounded asset-class effect.")
    for book in books:
        rs = sel(rows, book=book)
        if not rs:
            continue
        c = sel(rs, asset="crypto")
        nc = sel(rs, asset="noncrypto")
        if len(per_market(c)) < 2 or len(per_market(nc)) < 2:
            continue
        print()
        contrast(f"{book}:", c, nc, "crypto", "non-crypto")


def section_c(rows: list[dict]) -> None:
    head("5. COMPARISON C — same universe, different rule (the deconfounded read)")
    print("  `Lmmsell8` is 100% crypto. Comparing it against ALL of `Lmmsell10` mixes the rule")
    print("  with the universe. Comparing it against `Lmmsell10`'s CRYPTO SLICE holds asset")
    print("  class fixed over the same calendar window, leaving rule + band as the difference.")
    t = sel(rows, book="Lmmsell8")
    call_ = sel(rows, book="Lmmsell10")
    if not t or not call_:
        print("  one side absent — cannot run")
        return
    lo = max(min(r["created"] for r in t), min(r["created"] for r in call_))
    hi = min(max(r["created"] for r in t), max(r["created"] for r in call_))
    tw = [r for r in t if lo <= r["created"] <= hi]
    cw = [r for r in call_ if lo <= r["created"] <= hi]
    print(f"  overlapping window: {lo:%Y-%m-%d} .. {hi:%Y-%m-%d}")
    print()
    contrast("NAIVE (this is the confounded number — reported to be refuted):",
             tw, cw, "Lmmsell8  (all: 100% crypto)", "Lmmsell10 (all: ~96% non-crypto)")
    print()
    contrast("DECONFOUNDED (crypto held fixed):",
             tw, sel(cw, asset="crypto"),
             "Lmmsell8  (crypto)", "Lmmsell10 (crypto slice)")
    print()
    contrast("universe effect INSIDE the control (no rule change at all):",
             sel(cw, asset="crypto"), sel(cw, asset="noncrypto"),
             "Lmmsell10 crypto", "Lmmsell10 non-crypto")
    nv = welch(per_market(tw), per_market(cw))
    dc = welch(per_market(tw), per_market(sel(cw, asset="crypto")))
    if nv.get("delta") is not None and dc.get("delta") is not None and nv["delta"]:
        share = (1.0 - dc["delta"] / nv["delta"]) * 100.0
        print()
        print(f"  naive delta {f(nv['delta'])}c  ->  deconfounded delta {f(dc['delta'])}c")
        print(f"  share of the naive gap explained by asset class alone: {share:.0f}%")


def section_d(rows: list[dict]) -> None:
    head("6. COMPARISON D — shared support: where could BOTH rules have acted?")
    print("  mmsell8 admits crypto/ASG/HRDERBY at yes 5..12. mmsell10 admits ANY series at")
    print("  yes 5..10 with maxyes=7, i.e. an effective 5..7 band. The cell both admit is")
    print("  `mmsell8-eligible AND yes in 5..7`; everything else is exclusive to one book.")
    for pair in (("mmsell8", "mmsell10"), ("Lmmsell8", "Lmmsell10")):
        t, c = sel(rows, book=pair[0]), sel(rows, book=pair[1])
        if not t or not c:
            continue
        print()
        print(f"  --- {pair[0]} vs {pair[1]} ---")
        for name, rs in ((pair[0], t), (pair[1], c)):
            shared = band([r for r in rs if r["mmsell8_eligible"]], 5, 7)
            n = len({(r["book"], r["ticker"]) for r in rs})
            ns = len({(r["book"], r["ticker"]) for r in shared})
            pct = (ns / n * 100) if n else 0.0
            print(f"    {name:<11} markets={n:>5}  in shared cell={ns:>4} ({pct:4.1f}%)  "
                  f"exclusive={n - ns:>5} ({100 - pct:4.1f}%)")
        st = band([r for r in t if r["mmsell8_eligible"]], 5, 7)
        sc = band([r for r in c if r["mmsell8_eligible"]], 5, 7)
        if per_market(st) and per_market(sc):
            print()
            contrast("    economics INSIDE the shared cell:", st, sc, pair[0], pair[1])


def section_variance(rows: list[dict], books: list[str]) -> None:
    head("7. VARIANCE AND TAIL BY ASSET CLASS")
    print(f"  {'book':<15} {'asset':<10} {'mkts':>5} {'mean':>7} {'sd':>7} {'loss%':>6} "
          f"{'avgloss':>8} {'p5':>7} {'worst':>8}")
    print("  " + "-" * 82)
    for book in books:
        for asset in ("crypto", "noncrypto"):
            rs = sel(rows, book=book, asset=asset)
            pm = sorted(per_market(rs))
            if len(pm) < 2:
                continue
            losses = [p for p in pm if p < 0]
            print(f"  {book:<15} {asset:<10} {len(pm):>5} {f(_mean(pm)):>7} "
                  f"{f(_sd(pm), '{:.1f}'):>7} {len(losses) / len(pm) * 100:>5.1f}% "
                  f"{f(_mean(losses) if losses else None, '{:.1f}'):>8} "
                  f"{f(pctile(pm, 0.05), '{:.1f}'):>7} {f(pm[0], '{:.1f}'):>8}")


def section_time(rows: list[dict], books: list[str]) -> None:
    head("8. TIME-TO-SETTLEMENT DISTRIBUTION BY ASSET CLASS (realized hold, hours)")
    print(f"  {'book':<15} {'asset':<10} {'n':>5} {'mean':>7} {'p10':>7} {'p50':>7} "
          f"{'p90':>7} {'<=2h%':>7}")
    print("  " + "-" * 68)
    for book in books:
        for asset in ("crypto", "noncrypto"):
            hs = sorted(r["hold_h"] for r in sel(rows, book=book, asset=asset)
                        if r["hold_h"] is not None)
            if len(hs) < 2:
                continue
            short = sum(1 for h in hs if h <= 2.0) / len(hs) * 100
            print(f"  {book:<15} {asset:<10} {len(hs):>5} {f(_mean(hs), '{:.1f}'):>7} "
                  f"{f(pctile(hs, 0.10), '{:.1f}'):>7} {f(pctile(hs, 0.50), '{:.1f}'):>7} "
                  f"{f(pctile(hs, 0.90), '{:.1f}'):>7} {short:>6.1f}%")


def section_entry(rows: list[dict], books: list[str]) -> None:
    head("9. ENTRY-PRICE AND MODEL-EDGE DISTRIBUTION BY ASSET CLASS")
    print(f"  {'book':<15} {'asset':<10} {'n':>5} {'entry':>7} {'p10':>6} {'p90':>6} "
          f"{'edge':>7} {'edge_p50':>9} {'has_edge%':>10}")
    print("  " + "-" * 78)
    for book in books:
        for asset in ("crypto", "noncrypto"):
            rs = sel(rows, book=book, asset=asset)
            es = sorted(r["entry_c"] for r in rs if r["entry_c"] is not None)
            gs = sorted(r["edge"] for r in rs if r["edge"] is not None)
            if len(es) < 2:
                continue
            print(f"  {book:<15} {asset:<10} {len(es):>5} {f(_mean(es), '{:.2f}'):>7} "
                  f"{f(pctile(es, 0.10), '{:.1f}'):>6} {f(pctile(es, 0.90), '{:.1f}'):>6} "
                  f"{f(_mean(gs) if gs else None, '{:.3f}'):>7} "
                  f"{f(pctile(gs, 0.50) if gs else None, '{:.3f}'):>9} "
                  f"{(len(gs) / len(rs) * 100):>9.1f}%")


def section_family(rows: list[dict], books: list[str], min_n: int) -> None:
    head("10. MARKET-FAMILY COMPOSITION (regime x settle mode), per book")
    for book in books:
        rs = sel(rows, book=book)
        if not rs:
            continue
        by = defaultdict(list)
        for r in rs:
            by[(r["regime"], r["mode"], r["type"])].append(r)
        shown = [(k, v) for k, v in by.items() if len(v) >= min_n]
        if not shown:
            continue
        print(f"\n  --- {book} ---")
        print(f"    {'regime':<12} {'mode':<10} {'type':<14} {'n':>5} {'share':>7} {'c/ct':>8}")
        tot = len(rs)
        for (g, m, t), v in sorted(shown, key=lambda kv: -len(kv[1])):
            print(f"    {g:<12} {m:<10} {t:<14} {len(v):>5} {len(v) / tot * 100:>6.1f}% "
                  f"{f(_mean(per_market(v))):>8}")


def section_fill(cur, since: str | None) -> None:
    head("11. FILL RATE BY ASSET CLASS (live decision tape)")
    try:
        cur.execute(
            "SELECT live_tag, series, live_outcome, count(*)"
            "  FROM live_paper_parity_events"
            + (" WHERE recorded_at >= %s" if since else "")
            + " GROUP BY 1,2,3",
            ([since] if since else []),
        )
        rows = cur.fetchall()
    except Exception as exc:
        print(f"  live_paper_parity_events unavailable: {exc}")
        rows = []
    if rows:
        agg: dict[tuple[str, str, str], int] = defaultdict(int)
        for tag, series, outcome, c in rows:
            asset = "crypto" if regime_of(series or "") == CRYPTO_REGIME else "noncrypto"
            agg[(tag, asset, outcome or "?")] += c
        print(f"  {'live tag':<14} {'asset':<10} {'live_outcome':<24} {'rows':>7} {'share':>7}")
        print("  " + "-" * 66)
        tot: dict[tuple[str, str], int] = defaultdict(int)
        for (tag, asset, _o), c in agg.items():
            tot[(tag, asset)] += c
        for (tag, asset, o), c in sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1], -kv[1])):
            print(f"  {tag:<14} {asset:<10} {o:<24} {c:>7} "
                  f"{c / tot[(tag, asset)] * 100:>6.1f}%")

    print()
    print("  Order -> fill funnel from the order tape itself:")
    try:
        cur.execute(
            "SELECT o.strategy, o.market_ticker,"
            "       coalesce(sum(fl.quantity), 0) AS filled"
            "  FROM live_orders o"
            "  LEFT JOIN fills fl ON fl.kalshi_order_id = o.kalshi_order_id"
            " WHERE o.strategy IS NOT NULL"
            + (" AND o.created_at >= %s" if since else "")
            + " GROUP BY 1,2",
            ([since] if since else []),
        )
        orows = cur.fetchall()
    except Exception as exc:
        print(f"    live_orders/fills unavailable: {exc}")
        return
    agg2: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for tag, tkr, filled in orows:
        asset = "crypto" if regime_of(series_of(tkr)) == CRYPTO_REGIME else "noncrypto"
        agg2[(tag, asset)][0] += 1
        if (filled or 0) > 0:
            agg2[(tag, asset)][1] += 1
    print(f"    {'live tag':<14} {'asset':<10} {'markets ordered':>16} {'filled':>8} {'rate':>7}")
    print("    " + "-" * 58)
    for (tag, asset), (n, fl) in sorted(agg2.items()):
        print(f"    {tag:<14} {asset:<10} {n:>16} {fl:>8} {fl / n * 100 if n else 0:>6.1f}%")


def section_haircut(cur, rows: list[dict]) -> None:
    head("12. FILL-SELECTION HAIRCUT BY ASSET CLASS (twin counterfactual)")
    print("  For each twin book: its settled markets split by whether the LIVE book actually")
    print("  filled that ticker. haircut = unfilled minus filled, on the TWIN's own P&L, so")
    print("  execution price never enters. A positive haircut means live filled the worse ones.")
    try:
        cur.execute("SELECT twin_tag, live_tag FROM live_paper_twins")
        pairs = cur.fetchall()
    except Exception as exc:
        print(f"  live_paper_twins unavailable: {exc}")
        return
    for twin_tag, live_tag in pairs:
        tw = sel(rows, book=twin_tag)
        if not tw:
            continue
        cur.execute(
            "SELECT DISTINCT o.market_ticker FROM live_orders o"
            "  JOIN fills fl ON fl.kalshi_order_id = o.kalshi_order_id"
            " WHERE o.strategy = %s AND fl.quantity > 0",
            (live_tag,),
        )
        filled = {r[0] for r in cur.fetchall()}
        print(f"\n  --- twin {twin_tag} (live {live_tag}) ---")
        for asset in ("crypto", "noncrypto"):
            rs = sel(tw, asset=asset)
            if not rs:
                continue
            fi = [r for r in rs if r["ticker"] in filled]
            un = [r for r in rs if r["ticker"] not in filled]
            pf, pu = per_market(fi), per_market(un)
            if not pf or not pu:
                print(f"    {asset:<10} filled n={len(pf)}  unfilled n={len(pu)}  "
                      f"-> NOT IDENTIFIED (a side is empty)")
                continue
            w = welch(pu, pf)
            line = (f"    {asset:<10} filled {f(_mean(pf)):>7}c (n={len(pf):>3})   "
                    f"unfilled {f(_mean(pu)):>7}c (n={len(pu):>3})   "
                    f"haircut {f(w['delta']):>7}c")
            if w.get("lo") is not None:
                line += f"  95% CI [{f(w['lo'])}, {f(w['hi'])}]"
            else:
                line += "  (CI n/a: n<2 on a side)"
            print(line)


def section_successor(rows: list[dict]) -> None:
    head("13. SUCCESSOR CANDIDATES — the scheduled-settle question asked properly")
    print("  `only=BTCD+ETH+ASG+HRDERBY` is not a settle-mode rule; `mode=scheduled` IS, and it")
    print("  is an existing config knob (Settings.mmsell_variant_list). Books already running")
    print("  it share mmsell10's band, so the scheduled-vs-unfiltered question has paper")
    print("  history TODAY — inside one universe at a time.")
    ctl = "mmsell10"
    base = sel(rows, book=ctl)
    for cand in ("Tmmsell5", "Tmmsell1"):
        rs = sel(rows, book=cand)
        if not rs or not base:
            continue
        lo = max(min(r["created"] for r in rs), min(r["created"] for r in base))
        hi = min(max(r["created"] for r in rs), max(r["created"] for r in base))
        cw = [r for r in rs if lo <= r["created"] <= hi]
        bw = [r for r in base if lo <= r["created"] <= hi]
        print(f"\n  --- {cand} vs {ctl}   window {lo:%Y-%m-%d} .. {hi:%Y-%m-%d} ---")
        sch = sum(1 for r in cw if r["mode"] == "scheduled") / len(cw) * 100 if cw else 0
        cry = sum(1 for r in cw if r["asset"] == "crypto") / len(cw) * 100 if cw else 0
        print(f"    {cand}: {sch:.1f}% scheduled, {cry:.1f}% crypto  "
              f"(vs mmsell8's 92.2% scheduled / 100.0% crypto)")
        print()
        contrast("    pooled (still universe-confounded, shown for contrast):",
                 cw, bw, cand, ctl)
        for asset in ("crypto", "noncrypto"):
            a, b = sel(cw, asset=asset), sel(bw, asset=asset)
            if len(per_market(a)) >= 2 and len(per_market(b)) >= 2:
                print()
                contrast(f"    WITHIN {asset} (rule varies, universe fixed):",
                         a, b, f"{cand} {asset}", f"{ctl} {asset}")

    head("14. SAMPLE REQUIREMENT FOR A CLEAN WITHIN-UNIVERSE COMPARISON")
    print("  Per-market sd measured above, two-sided 95% / one-sided 99% bounds, 80% power,")
    print("  equal cells. n is SETTLED MARKETS PER CELL — the independent unit.")
    print(f"  {'universe':<12} {'sd':>6}   " + "  ".join(f"d={d}c" for d in (1, 2, 3, 5)))
    print("  " + "-" * 52)
    for asset, pool in (("crypto", sel(rows, asset="crypto")),
                        ("noncrypto", sel(rows, asset="noncrypto"))):
        pm = per_market([r for r in pool if r["book"] in
                         ("mmsell10", "mmsell9", "mmsell6", "Tmmsell5", "Tmmsell1")])
        sd = _sd(pm)
        if sd is None:
            continue
        cells = []
        for d in (1, 2, 3, 5):
            # two-sample, equal n: n per arm = 2 (z_a + z_b)^2 sd^2 / d^2
            for za, tag in ((1.96, "95"), (2.326, "99")):
                n = 2 * ((za + 0.8416) ** 2) * sd * sd / (d * d)
                cells.append(f"{tag}:{n:,.0f}")
        row = "  ".join(cells[i] + "/" + cells[i + 1] for i in range(0, len(cells), 2))
        print(f"  {asset:<12} {sd:>6.1f}   {row}")
    print("  (each cell reads `95%:n / 99%:n` markets per arm)")


# --- main -----------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default=None, help="only trades created on/after this date")
    ap.add_argument("--until", default=None, help="only trades created before this date")
    ap.add_argument("--min-n", type=int, default=20, help="hide census/family cells below this n")
    ap.add_argument("--books", default="mmsell8,mmsell5,mmsell9,mmsell10,mmsell6,mmsell7,"
                                       "Lmmsell8,Lmmsell10,Lmmsell8_pt3,Lmmsell10_pt3",
                    help="comma-separated books for the per-book tables")
    ap.add_argument("--live-since", default="2026-08-15",
                    help="window start for the live funnel section")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    books = [b.strip() for b in args.books.split(",") if b.strip()]
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            rows = load(cur, args.since, args.until)
            print(f"loaded {len(rows)} settled mmsell paper rows"
                  + (f" since {args.since}" if args.since else "")
                  + (f" until {args.until}" if args.until else ""))
            section_census(rows, args.min_n)
            section_proxy(rows)
            section_a(rows)
            section_b(rows, ["mmsell10", "mmsell9", "mmsell6", "mmsell7", "Lmmsell10", "mmsell"])
            section_c(rows)
            section_d(rows)
            section_variance(rows, books)
            section_time(rows, books)
            section_entry(rows, books)
            section_family(rows, ["mmsell8", "mmsell10", "Lmmsell8", "Lmmsell10"], args.min_n)
            section_successor(rows)
            section_fill(cur, args.live_since)
            section_haircut(cur, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
