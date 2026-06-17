"""Offline consensus study — does requiring independent signals to CONVERGE on a bucket
beat following any single signal?

Replays the persisted forecast->settlement dataset (`weather_forecast_outcomes`, one
labeled row per intraday market-state cycle) and, for each cycle, maps every independent
signal to the bucket it points at, then measures accuracy/EV as a function of how many
signals agree (K), the bucket tolerance (T), and which signal families agree. This is the
gate-before-live check for a layered "consensus" book: only build/trade it if convergence
demonstrably beats the single-signal books.

Independent signal FAMILIES (deliberately de-duplicated — cal is nws+bias, so it is not a
second vote; HRRR and NWS are one forecast family, HRRR preferred when present):
  fc   forecast   = hrrr_f if present else forecast_f
  ens  ensemble   = ens_mean_f          (GFS/ECMWF/ICON/GEM blend mean)
  obs  observed   = running max (high) / min (low)   [a one-sided bound; sharpest late]
  pm   polymarket = pm_implied_mean_f    (only the PM-tracked cities)
The market (market_implied_mean_f / the favorite bucket) is the PRIOR/baseline we try to
beat, not a vote.

Entry price is approximated as the bucket's implied mid + a spread haircut (the ladder
stores mids, not asks); win% is exact. Relative comparisons across K are robust to the
haircut. Self-contained (stdlib + psycopg) so it runs on the ops runner.

Usage:
    DATABASE_URL_RO=postgresql://... python scripts/weather_consensus_study.py
    {"type": "script", "name": "weather_consensus_study", "args": ["--tol", "1"]}
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from statistics import mean

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

FAMILIES = ("fc", "ens", "obs", "pm")


# --- pricing -----------------------------------------------------------------------


def fee_cents(entry: float) -> int:
    """Kalshi trading fee per contract (cents): ceil(7% * P * (1-P)), P in dollars."""
    p = max(0.0, min(1.0, entry / 100.0))
    return math.ceil(7.0 * p * (1.0 - p))


def trade_pnl(win: bool, entry: float) -> float:
    """Net cents on a 1-contract YES buy held to settlement (fee on entry)."""
    return (100.0 if win else 0.0) - entry - fee_cents(entry)


# --- bucket mapping ----------------------------------------------------------------


def bucket_for_temp(temp, buckets: list[dict]) -> int | None:
    """Index of the ladder bucket containing temp; nearest-by-edge if none strictly
    contains it (open-ended below/above buckets carry a None edge)."""
    if temp is None:
        return None
    for i, b in enumerate(buckets):
        lo, hi = b.get("low"), b.get("high")
        if (lo is None or temp >= lo) and (hi is None or temp <= hi):
            return i
    best, best_d = None, None
    for i, b in enumerate(buckets):
        edges = [e for e in (b.get("low"), b.get("high")) if e is not None]
        d = min(abs(temp - e) for e in edges) if edges else 0.0
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def winner_index(buckets: list[dict], row: dict) -> int | None:
    sub = row.get("winning_subtitle")
    if sub:
        for i, b in enumerate(buckets):
            if b.get("subtitle") == sub:
                return i
    lo, hi = row.get("winning_low_f"), row.get("winning_high_f")
    if lo is not None or hi is not None:
        for i, b in enumerate(buckets):
            if b.get("low") == lo and b.get("high") == hi:
                return i
    return None


def fav_index(buckets: list[dict]) -> int | None:
    best, best_mid = None, None
    for i, b in enumerate(buckets):
        mc = b.get("mid_cents")
        if mc is not None and (best_mid is None or mc > best_mid):
            best, best_mid = i, mc
    return best


# --- per-cycle ---------------------------------------------------------------------


def build_cycle(row: dict) -> dict | None:
    """Parse one wfo row into votes/outcome, or None if it has no usable ladder/winner."""
    raw = row.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = None
    buckets = (raw or {}).get("buckets") if isinstance(raw, dict) else None
    if not buckets:
        return None
    win_idx = winner_index(buckets, row)
    if win_idx is None:
        return None

    kind = row.get("kind") or "high"
    fc = row.get("hrrr_f") if row.get("hrrr_f") is not None else row.get("forecast_f")
    obs = row.get("obs_running_max_f") if kind == "high" else row.get("obs_running_min_f")
    temps = {"fc": fc, "ens": row.get("ens_mean_f"), "obs": obs, "pm": row.get("pm_implied_mean_f")}
    votes = {f: bucket_for_temp(t, buckets) for f, t in temps.items()}

    return {
        "kind": kind,
        "htc": row.get("hours_to_close"),
        "event": row.get("event_ticker"),
        "buckets": buckets,
        "votes": votes,
        "winner": win_idx,
        "fav": fav_index(buckets),
    }


def consensus_pick(votes: dict, *, k: int, tol: int) -> tuple[int, int] | None:
    """The bucket with the most family votes within +/-tol; (bucket_idx, agree_count) if
    that count reaches k, else None. Tie -> the candidate closest to the vote centroid."""
    present = [idx for idx in votes.values() if idx is not None]
    if len(present) < k:
        return None
    centroid = mean(present)
    best = None  # (count, -dist_to_centroid, bucket_idx)
    for cand in set(present):
        count = sum(1 for idx in present if abs(idx - cand) <= tol)
        key = (count, -abs(cand - centroid))
        if best is None or key > best[:2]:
            best = (count, -abs(cand - centroid), cand)
    if best is None or best[0] < k:
        return None
    return (best[2], best[0])


def entry_cost(cycle: dict, idx: int, haircut: float) -> float:
    mid = cycle["buckets"][idx].get("mid_cents")
    if mid is None:
        return 50.0 + haircut
    return max(1.0, min(99.0, mid + haircut))


# --- aggregation helpers -----------------------------------------------------------


def _nearest_window(htc, windows: list[float]) -> float | None:
    return min(windows, key=lambda w: abs(htc - w)) if htc is not None else None


class Cell:
    __slots__ = ("n", "wins", "pnl", "entry")

    def __init__(self) -> None:
        self.n = self.wins = 0
        self.pnl = self.entry = 0.0

    def add(self, win: bool, entry: float) -> None:
        self.n += 1
        self.wins += 1 if win else 0
        self.pnl += trade_pnl(win, entry)
        self.entry += entry

    def row(self) -> str:
        if not self.n:
            return f"{0:5d} {'n/a':>5} {'n/a':>7} {'n/a':>9}"
        return (f"{self.n:5d} {self.wins / self.n * 100:4.0f}% {self.entry / self.n:6.1f}c"
                f" {self.pnl / self.n:+7.1f}c")


# --- sections ----------------------------------------------------------------------


def report_coverage(cycles: list[dict], windows: list[float]) -> None:
    print("=== Signal coverage (graded cycles with a usable ladder + winner) ===")
    events = {c["event"] for c in cycles}
    print(f"  cycles: {len(cycles)}   distinct settled events: {len(events)}")
    print(f"  {'window':>6} {'n':>5}   " + "  ".join(f"{f}%" for f in FAMILIES) + "   avg_fams")
    for w in windows + [None]:
        sel = [c for c in cycles if (_nearest_window(c["htc"], windows) == w if w else True)]
        if not sel:
            continue
        cov = [sum(1 for c in sel if c["votes"][f] is not None) / len(sel) * 100 for f in FAMILIES]
        avg_fams = mean(sum(1 for f in FAMILIES if c["votes"][f] is not None) for c in sel)
        label = f"h{int(w)}" if w else "ALL"
        print(f"  {label:>6} {len(sel):5d}   "
              + "  ".join(f"{c:3.0f}" for c in cov) + f"   {avg_fams:.2f}")
    print("  (fc=forecast hrrr|nws, ens=ensemble, obs=running extreme, pm=polymarket;"
          " avg_fams = independent signals present per cycle)")


def report_baselines(cycles: list[dict], windows: list[float], haircut: float, min_rows: int) -> None:
    print("\n=== Single-signal baselines — bucket hit% and net c/trade (what to beat) ===")
    print("  the favorite is the market's own pick; each family is one of the parallel books")
    print(f"  {'kind':>4} {'window':>6} | " + " | ".join(f"{s:>16}" for s in ("fav", *FAMILIES)))
    for kind in ("high", "low"):
        for w in windows:
            sel = [c for c in cycles
                   if c["kind"] == kind and _nearest_window(c["htc"], windows) == w]
            if not sel:
                continue
            cells = {s: Cell() for s in ("fav", *FAMILIES)}
            for c in sel:
                if c["fav"] is not None:
                    cells["fav"].add(c["fav"] == c["winner"], entry_cost(c, c["fav"], haircut))
                for f in FAMILIES:
                    idx = c["votes"][f]
                    if idx is not None:
                        cells[f].add(idx == c["winner"], entry_cost(c, idx, haircut))
            flag = "  (small n)" if len(sel) < min_rows else ""
            print(f"  {kind:>4} h{int(w):<5d} | "
                  + " | ".join(cells[s].row() for s in ("fav", *FAMILIES)) + flag)
    print("  cell = n  hit%  avg_entry  net/trade   (net includes the spread haircut + fee)")


def report_consensus(cycles: list[dict], windows: list[float], haircut: float,
                     tol: int, min_rows: int) -> None:
    print(f"\n=== Consensus by agreement K (tolerance +/-{tol} buckets) — does converging help? ===")
    print(f"  {'kind':>4} {'window':>6} {'K':>2}  {'n':>5} {'win%':>5} {'avg_buy':>7}"
          f" {'net/trade':>9} {'vs_fav':>7}")
    for kind in ("high", "low"):
        for w in windows:
            sel = [c for c in cycles
                   if c["kind"] == kind and _nearest_window(c["htc"], windows) == w]
            if not sel:
                continue
            fav = Cell()
            for c in sel:
                if c["fav"] is not None:
                    fav.add(c["fav"] == c["winner"], entry_cost(c, c["fav"], haircut))
            fav_net = fav.pnl / fav.n if fav.n else None
            for k in (1, 2, 3, 4):
                cell = Cell()
                for c in sel:
                    pick = consensus_pick(c["votes"], k=k, tol=tol)
                    if pick is None:
                        continue
                    idx, _cnt = pick
                    cell.add(idx == c["winner"], entry_cost(c, idx, haircut))
                if not cell.n:
                    continue
                net = cell.pnl / cell.n
                vs = f"{net - fav_net:+5.1f}c" if fav_net is not None else "  n/a"
                flag = "  (small n)" if cell.n < min_rows else ""
                print(f"  {kind:>4} h{int(w):<5d} {k:>2}  {cell.n:5d} {cell.wins / cell.n * 100:4.0f}%"
                      f" {cell.entry / cell.n:6.1f}c {net:+8.1f}c {vs:>7}{flag}")
    print("  (win% & net should RISE with K if convergence has edge; vs_fav = net minus"
          " just buying the favorite that cell)")


def report_pairs(cycles: list[dict], tol: int, min_rows: int) -> None:
    print(f"\n=== Which families agreeing matters — pairwise co-agreement (tol +/-{tol}) ===")
    print(f"  {'pair':>9} {'kind':>4}  {'n':>5} {'both_hit%':>9} {'agree_hit%':>10}")
    pairs = [("fc", "ens"), ("fc", "obs"), ("fc", "pm"), ("ens", "obs"), ("ens", "pm"), ("obs", "pm")]
    for a, b in pairs:
        for kind in ("high", "low"):
            n = agree = agree_hit = 0
            for c in cycles:
                if c["kind"] != kind:
                    continue
                ia, ib = c["votes"][a], c["votes"][b]
                if ia is None or ib is None:
                    continue
                n += 1
                if abs(ia - ib) <= tol:
                    agree += 1
                    # the agreed bucket = the one closer to the favorite-free centroid (here just a)
                    if ia == c["winner"]:
                        agree_hit += 1
            if agree:
                flag = "  (small n)" if agree < min_rows else ""
                print(f"  {a + '+' + b:>9} {kind:>4}  {agree:5d} {agree / n * 100:8.0f}%"
                      f" {agree_hit / agree * 100:9.0f}%{flag}")
    print("  (n = cycles where BOTH present & agree; agree_hit% = the agreed bucket won)")


def report_edge(cycles: list[dict], windows: list[float], haircut: float,
                tol: int, k: int, min_rows: int) -> None:
    print(f"\n=== Consensus vs market favorite — where is the EDGE? (K>={k}, tol +/-{tol}) ===")
    same = Cell()
    diff = Cell()
    diff_cheap = Cell()
    fav_when_diff = Cell()
    for c in cycles:
        pick = consensus_pick(c["votes"], k=k, tol=tol)
        if pick is None or c["fav"] is None:
            continue
        idx, _ = pick
        entry = entry_cost(c, idx, haircut)
        win = idx == c["winner"]
        if idx == c["fav"]:
            same.add(win, entry)
        else:
            diff.add(win, entry)
            fav_when_diff.add(c["fav"] == c["winner"], entry_cost(c, c["fav"], haircut))
            if entry < entry_cost(c, c["fav"], haircut):  # consensus bucket cheaper than fav
                diff_cheap.add(win, entry)
    print(f"  consensus == favorite : {same.row()}")
    print(f"  consensus != favorite : {diff.row()}")
    print(f"    (favorite on those) : {fav_when_diff.row()}")
    print(f"  != favorite & cheaper : {diff_cheap.row()}")
    print("  the tradable edge lives in '!= favorite': consensus must WIN more than the"
          " favorite did on those same cycles")
    if diff.n and diff.n < min_rows:
        print("  (small n — treat as directional only)")


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
    ap.add_argument("--windows", default="20,14,8")
    ap.add_argument("--kind", choices=("high", "low", "both"), default="both")
    ap.add_argument("--tol", type=int, default=1, help="bucket tolerance for 'agree' (+/- buckets)")
    ap.add_argument("--haircut", type=float, default=2.0, help="cents above mid for entry (ask proxy)")
    ap.add_argument("--edge-k", type=int, default=3, help="K for the consensus-vs-favorite edge cut")
    ap.add_argument("--since", default=None, help="only target_date >= this ISO date")
    ap.add_argument("--min-rows", type=int, default=20)
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
                "SELECT event_ticker, city, kind, hours_to_close, target_date,"
                " forecast_f, hrrr_f, ens_mean_f, obs_running_max_f, obs_running_min_f,"
                " pm_implied_mean_f, market_implied_mean_f, market_fav_low_f, market_fav_high_f,"
                " winning_low_f, winning_high_f, winning_subtitle,"
                " actual_high_f, actual_low_f, raw_json"
                f" FROM weather_forecast_outcomes WHERE {clause}",
                params,
            )
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    cycles = [c for c in (build_cycle(r) for r in rows) if c is not None]
    if not cycles:
        print("No usable graded cycles in weather_forecast_outcomes yet"
              f" ({len(rows)} rows scanned).")
        print("Consensus needs cycles with a stored bucket ladder + a settled winner.")
        return 0

    report_coverage(cycles, windows)
    report_baselines(cycles, windows, args.haircut, args.min_rows)
    report_consensus(cycles, windows, args.haircut, args.tol, args.min_rows)
    report_pairs(cycles, args.tol, args.min_rows)
    report_edge(cycles, windows, args.haircut, args.tol, args.edge_k, args.min_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
