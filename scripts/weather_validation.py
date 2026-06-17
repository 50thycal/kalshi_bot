"""Validation report over the persisted forecast->settlement dataset.

Reads `weather_forecast_outcomes` — the clean, labeled join the worker materializes at
settlement (one row per intraday market-state cycle, with the actual outcome attached) —
and reports the things that make cal/dist/pm validatable:

  1. coverage / growth (so we can watch the dataset accumulate),
  2. forecast-vs-market skill (who predicts the daily extreme more accurately),
  3. the probabilistic edge of the ensemble vs the market on the eventual winner
     (prob-on-winner + log-loss — the `dist` book's thesis),
  4. market-vs-forecast divergence vs who was right (the edge signal),
  5. Polymarket accuracy where present (`pm`) and the NWS-vs-observed forecast split (`cal`).

All metrics are read straight off pre-stored columns, so this is mostly aggregation. The
sigma-dependent piece (ensemble prob-on-winner) is precomputed at materialization time.
Self-contained (stdlib + psycopg only) so it runs on the ops runner.

Usage:
    DATABASE_URL_RO=postgresql://... python scripts/weather_validation.py
    # or via the ops channel:
    {"type": "script", "name": "weather_validation", "args": ["--windows", "20,14,8"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from statistics import mean

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

PROB_FLOOR = 1e-4  # keep log-loss finite when a distribution zeroes the winner
DIVERGENCE_BANDS = ((None, -3.0), (-3.0, -1.0), (-1.0, 1.0), (1.0, 3.0), (3.0, None))


def _logloss(p: float) -> float:
    return -math.log(max(p, PROB_FLOOR))


def _nearest_window(htc: float, windows: list[float]) -> float:
    return min(windows, key=lambda w: abs(htc - w))


def _actual_extreme(row: dict) -> float | None:
    return row["actual_high_f"] if (row["kind"] or "high") == "high" else row["actual_low_f"]


def _fmt(x: float | None, spec: str = "+.2f") -> str:
    return format(x, spec) if x is not None else "n/a"


def _avg(vals: list[float]) -> float | None:
    return mean(vals) if vals else None


# --- sections ----------------------------------------------------------------------


def report_coverage(rows: list[dict], sentinels: int) -> None:
    print("=== Coverage / growth ===")
    events = {r["event_ticker"] for r in rows}
    dates = sorted({r["target_date"] for r in rows if r["target_date"]})
    print(f"  rows (graded cycles): {len(rows)}   distinct events: {len(events)}"
          f"   sentinel (no-ladder) events: {sentinels}")
    if dates:
        print(f"  target_date range: {dates[0]} .. {dates[-1]}  ({len(dates)} days)")
    by_day: dict[str, int] = {}
    for r in rows:
        if r["target_date"]:
            by_day[r["target_date"]] = by_day.get(r["target_date"], 0) + 1
    if by_day:
        recent = sorted(by_day.items())[-7:]
        print("  rows/day (last 7 target dates): "
              + ", ".join(f"{d}={n}" for d, n in recent))


def report_skill(rows: list[dict], windows: list[float], min_rows: int) -> None:
    print("\n=== Forecast vs market skill (abs error on the daily extreme, by window x kind) ===")
    print(f"  {'kind':>4} {'window':>6} {'n':>5} {'fc_err':>7} {'mkt_err':>7}"
          f" {'mkt-fc':>7} {'fc_better%':>10}")
    cells: dict[tuple, list[tuple[float, float]]] = {}
    for r in rows:
        fe, me = r["forecast_abs_err_f"], r["market_abs_err_f"]
        if fe is None or me is None:
            continue
        key = ((r["kind"] or "high"), _nearest_window(r["hours_to_close"], windows))
        cells.setdefault(key, []).append((fe, me))
    for kind in ("high", "low"):
        for w in windows:
            pairs = cells.get((kind, w))
            if not pairs:
                continue
            n = len(pairs)
            fc = mean(p[0] for p in pairs)
            mk = mean(p[1] for p in pairs)
            better = sum(1 for fe, me in pairs if fe < me) / n * 100.0
            flag = "  (small n)" if n < min_rows else ""
            print(f"  {kind:>4} h{int(w):<5d} {n:5d} {fc:7.2f} {mk:7.2f}"
                  f" {mk - fc:+7.2f} {better:9.0f}%{flag}")
    print("  (mkt-fc > 0 => forecast closer to the actual extreme than the market's implied mean)")


def report_prob_edge(rows: list[dict], windows: list[float], min_rows: int) -> None:
    print("\n=== Probabilistic edge on the winning bucket: ensemble vs market (by window) ===")
    print(f"  {'window':>6} {'n':>5} {'mkt_p':>6} {'ens_p':>6} {'mkt_LL':>7} {'ens_LL':>7}"
          f" {'edge':>6}")
    cells: dict[float, list[tuple[float, float]]] = {}
    mkt_only: dict[float, list[float]] = {}
    for r in rows:
        w = _nearest_window(r["hours_to_close"], windows)
        if r["market_prob_winner"] is not None:
            mkt_only.setdefault(w, []).append(r["market_prob_winner"])
        if r["market_prob_winner"] is not None and r["ens_prob_winner"] is not None:
            cells.setdefault(w, []).append((r["market_prob_winner"], r["ens_prob_winner"]))
    for w in windows:
        pairs = cells.get(w)
        mo = mkt_only.get(w)
        if not mo:
            continue
        if pairs:
            n = len(pairs)
            mp = mean(p[0] for p in pairs)
            ep = mean(p[1] for p in pairs)
            mll = mean(_logloss(p[0]) for p in pairs)
            ell = mean(_logloss(p[1]) for p in pairs)
            flag = "  (small n)" if n < min_rows else ""
            print(f"  h{int(w):<5d} {n:5d} {mp:6.3f} {ep:6.3f} {mll:7.3f} {ell:7.3f}"
                  f" {mll - ell:+6.3f}{flag}")
        else:
            print(f"  h{int(w):<5d} {len(mo):5d} {mean(mo):6.3f} {'n/a':>6} "
                  f"{mean(_logloss(p) for p in mo):7.3f} {'n/a':>7} {'n/a':>6}")
    print("  (prob-on-winner: higher = sharper-and-right; LL: lower = better;"
          " edge = mkt_LL - ens_LL > 0 => ensemble beats market)")


def report_divergence(rows: list[dict], min_rows: int) -> None:
    print("\n=== Divergence (forecast - market) vs who was right ===")
    print(f"  {'band(degF)':>12} {'n':>5} {'mkt-fc_err':>10} {'fc_better%':>10}")
    for lo, hi in DIVERGENCE_BANDS:
        sel = []
        for r in rows:
            d = r["divergence_f"]
            fe, me = r["forecast_abs_err_f"], r["market_abs_err_f"]
            if d is None or fe is None or me is None:
                continue
            if (lo is None or d >= lo) and (hi is None or d < hi):
                sel.append((fe, me))
        if not sel:
            continue
        n = len(sel)
        adv = mean(me - fe for fe, me in sel)
        better = sum(1 for fe, me in sel if fe < me) / n * 100.0
        label = f"[{_fmt(lo, '+.0f') if lo is not None else '-inf'},"\
                f"{_fmt(hi, '+.0f') if hi is not None else '+inf'})"
        flag = "  (small n)" if n < min_rows else ""
        print(f"  {label:>12} {n:5d} {adv:+10.2f} {better:9.0f}%{flag}")
    print("  (mkt-fc_err > 0 => forecast wins in that divergence band — the tradable signal)")


def report_hrrr(rows: list[dict], windows: list[float], min_rows: int) -> None:
    """HRRR vs NWS vs market abs error on the daily extreme (HRRR is collect+grade only —
    this section is what decides whether it earns its way into the books later)."""
    print("\n=== HRRR vs NWS vs market skill (abs error on the daily extreme, by window x kind) ===")
    graded = [r for r in rows if r["hrrr_abs_err_f"] is not None]
    cov = len(graded)
    print(f"  HRRR-graded cycles: {cov} / {len(rows)}"
          f"   distinct events with HRRR: {len({r['event_ticker'] for r in graded})}")
    if not graded:
        print("  (no HRRR-graded rows yet — HRRR accumulates forward from deploy; "
              "give it a few settlements)")
        return
    def _num(x: float | None) -> str:
        return f"{x:.2f}" if x is not None else "n/a"

    def _pct(hits: int, tot: int) -> str:
        return f"{hits / tot * 100.0:.0f}%" if tot else "n/a"

    print(f"  {'kind':>4} {'window':>6} {'n':>5} {'hrrr_err':>8} {'nws_err':>8} {'mkt_err':>8}"
          f" {'nws-hrrr':>8} {'hrrr<nws':>8} {'hrrr<mkt':>8}")
    # (hrrr_err, nws_err, mkt_err) per cell; nws/mkt may be None for a given row.
    cells: dict[tuple, list[tuple]] = {}
    for r in graded:
        key = ((r["kind"] or "high"), _nearest_window(r["hours_to_close"], windows))
        cells.setdefault(key, []).append(
            (r["hrrr_abs_err_f"], r["forecast_abs_err_f"], r["market_abs_err_f"])
        )
    for kind in ("high", "low"):
        for w in windows:
            trips = cells.get((kind, w))
            if not trips:
                continue
            n = len(trips)
            he = mean(t[0] for t in trips)
            nws_pairs = [(t[0], t[1]) for t in trips if t[1] is not None]
            mkt_pairs = [(t[0], t[2]) for t in trips if t[2] is not None]
            ne = _avg([p[1] for p in nws_pairs])
            me = _avg([p[1] for p in mkt_pairs])
            nws_minus_hrrr = _avg([nw - hr for hr, nw in nws_pairs])
            flag = "  (small n)" if n < min_rows else ""
            print(f"  {kind:>4} h{int(w):<5d} {n:5d} {he:8.2f} {_num(ne):>8} {_num(me):>8}"
                  f" {_num(nws_minus_hrrr):>8}"
                  f" {_pct(sum(1 for hr, nw in nws_pairs if hr < nw), len(nws_pairs)):>8}"
                  f" {_pct(sum(1 for hr, mk in mkt_pairs if hr < mk), len(mkt_pairs)):>8}{flag}")
    print("  (nws-hrrr > 0 => HRRR closer to the actual extreme than NWS; hrrr<nws / hrrr<mkt"
          " = share of cycles HRRR beat NWS / market)")


def report_pm_and_cal(rows: list[dict], min_rows: int) -> None:
    pm = []
    for r in rows:
        a = _actual_extreme(r)
        if r["pm_implied_mean_f"] is not None and a is not None and r["market_abs_err_f"] is not None:
            pm.append((abs(r["pm_implied_mean_f"] - a), r["market_abs_err_f"]))
    print("\n=== Polymarket (pm) accuracy where present ===")
    if pm:
        n = len(pm)
        better = sum(1 for pe, me in pm if pe < me) / n * 100.0
        flag = "  (small n)" if n < min_rows else ""
        print(f"  n={n}  pm_err={mean(p[0] for p in pm):.2f}  mkt_err={mean(p[1] for p in pm):.2f}"
              f"  pm_better={better:.0f}%{flag}")
    else:
        print("  (no rows with a Polymarket implied mean yet)")

    print("\n=== Forecast source split (cal: NWS point vs observed-extreme fallback) ===")
    by_src: dict[str, list[float]] = {}
    for r in rows:
        if r["forecast_abs_err_f"] is not None and r["forecast_source"]:
            by_src.setdefault(r["forecast_source"], []).append(r["forecast_abs_err_f"])
    if not by_src:
        print("  (no graded forecasts yet)")
    for src, errs in sorted(by_src.items()):
        print(f"  {src:>6}: n={len(errs):5d}  fc_err={mean(errs):.2f}")


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
    ap.add_argument("--windows", default="20,14,8",
                    help="hours-to-close windows to bin cycles into (nearest wins)")
    ap.add_argument("--kind", choices=("high", "low", "both"), default="both")
    ap.add_argument("--since", default=None, help="only target_date >= this ISO date")
    ap.add_argument("--min-rows", type=int, default=20,
                    help="flag cells below this many rows as small-sample")
    args = ap.parse_args(argv)
    windows = [float(x) for x in args.windows.split(",") if x.strip()]

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    where = ["hours_to_close IS NOT NULL"]
    params: list = []
    if args.kind != "both":
        where.append("kind = %s")
        params.append(args.kind)
    if args.since:
        where.append("target_date >= %s")
        params.append(args.since)
    clause = " AND ".join(where)

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT event_ticker, city, kind, target_date, hours_to_close,"
                " forecast_f, forecast_source, market_implied_mean_f, divergence_f,"
                " market_prob_winner, ens_prob_winner, pm_implied_mean_f,"
                " forecast_abs_err_f, market_abs_err_f, actual_high_f, actual_low_f,"
                " hrrr_f, hrrr_abs_err_f, hrrr_divergence_f"
                f" FROM weather_forecast_outcomes WHERE {clause}",
                params,
            )
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
            cur.execute("SELECT count(*) FROM weather_forecast_outcomes"
                        " WHERE hours_to_close IS NULL")
            sentinels = cur.fetchone()[0]

    if not rows:
        print("No graded rows in weather_forecast_outcomes yet"
              f" (sentinel/no-ladder events: {sentinels}).")
        print("The dataset materializes at settlement; give the worker a few cycles after deploy.")
        return 0

    report_coverage(rows, sentinels)
    report_skill(rows, windows, args.min_rows)
    report_prob_edge(rows, windows, args.min_rows)
    report_hrrr(rows, windows, args.min_rows)
    report_divergence(rows, args.min_rows)
    report_pm_and_cal(rows, args.min_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
