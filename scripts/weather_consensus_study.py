"""Offline consensus study — does requiring independent signals to CONVERGE on a bucket
beat following any single signal? (sharpened edition)

Replays the persisted forecast->settlement dataset (`weather_forecast_outcomes`, one
labeled row per intraday market-state cycle) and, per cycle, maps every independent signal
to the bucket it points at, then measures accuracy/EV as a function of:
  - K, how many signal families agree (within +/-tol buckets),
  - whether an INDEPENDENT confirmer (obs or pm) is in the agreement (forecast-only
    agreement is correlated error),
  - a skill-WEIGHTED blend (obs/pm weighted above the forecasts),
and breaks the consensus-vs-favorite edge down BY WINDOW.

Entry price uses the REAL resting ask joined from `weather_bucket_snapshots` at the cycle
time (falling back to bucket mid + a haircut when no snapshot matches), so EV is realistic.

Independent signal FAMILIES (cal is nws+bias, so not a second vote; HRRR+NWS are one
forecast family, HRRR preferred): fc, ens, obs, pm. The market favorite is the baseline.

Self-contained (stdlib + psycopg). Usage:
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
    "-c statement_timeout=90000 "
    "-c idle_in_transaction_session_timeout=90000"
)

FAMILIES = ("fc", "ens", "obs", "pm")
CONFIRMERS = ("obs", "pm")  # independent of the forecast models
DEFAULT_WEIGHTS = {"fc": 1.0, "ens": 1.0, "obs": 2.0, "pm": 2.0}
ASK_MATCH_SECONDS = 120


# --- pricing -----------------------------------------------------------------------


def fee_cents(entry: float) -> int:
    p = max(0.0, min(1.0, entry / 100.0))
    return math.ceil(7.0 * p * (1.0 - p))


def trade_pnl(win: bool, entry: float) -> float:
    return (100.0 if win else 0.0) - entry - fee_cents(entry)


# --- bucket mapping ----------------------------------------------------------------


def bucket_for_temp(temp, buckets: list[dict]) -> int | None:
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
    return {
        "id": row.get("id"),
        "kind": kind,
        "htc": row.get("hours_to_close"),
        "event": row.get("event_ticker"),
        "buckets": buckets,
        "votes": {f: bucket_for_temp(t, buckets) for f, t in temps.items()},
        "winner": win_idx,
        "fav": fav_index(buckets),
    }


def consensus_pick(votes: dict, *, k: int, tol: int, require_confirmer: bool = False):
    """Bucket with the most family votes within +/-tol; (bucket, count) if it reaches k
    (and includes an obs/pm voter when require_confirmer), else None."""
    present = {f: idx for f, idx in votes.items() if idx is not None}
    if len(present) < k:
        return None
    centroid = mean(present.values())
    best = None  # (count, -dist, cand)
    for cand in set(present.values()):
        voters = [f for f, idx in present.items() if abs(idx - cand) <= tol]
        if len(voters) < k:
            continue
        if require_confirmer and not any(f in CONFIRMERS for f in voters):
            continue
        key = (len(voters), -abs(cand - centroid))
        if best is None or key > best[:2]:
            best = (len(voters), -abs(cand - centroid), cand)
    return (best[2], best[0]) if best else None


def weighted_pick(votes: dict, *, weights: dict, tol: int, min_mass: float):
    """Bucket with the most skill-WEIGHTED vote mass within +/-tol; (bucket, mass) if mass
    reaches min_mass, else None."""
    present = {f: idx for f, idx in votes.items() if idx is not None}
    if not present:
        return None
    centroid = mean(present.values())
    best = None
    for cand in set(present.values()):
        mass = sum(weights.get(f, 1.0) for f, idx in present.items() if abs(idx - cand) <= tol)
        key = (mass, -abs(cand - centroid))
        if best is None or key > best[:2]:
            best = (mass, -abs(cand - centroid), cand)
    if best is None or best[0] < min_mass:
        return None
    return (best[2], best[0])


def entry_cost(cycle: dict, idx: int, haircut: float, asks: dict) -> float:
    b = cycle["buckets"][idx]
    real = asks.get(cycle["id"], {}).get(b.get("subtitle")) if asks else None
    if real is not None:
        return max(1.0, min(99.0, float(real)))
    mid = b.get("mid_cents")
    return max(1.0, min(99.0, (mid if mid is not None else 50.0) + haircut))


# --- aggregation -------------------------------------------------------------------


def _nearest_window(htc, windows: list[float]):
    return min(windows, key=lambda w: abs(htc - w)) if htc is not None else None


class Cell:
    __slots__ = ("n", "wins", "pnl", "entry")

    def __init__(self):
        self.n = self.wins = 0
        self.pnl = self.entry = 0.0

    def add(self, win: bool, entry: float):
        self.n += 1
        self.wins += 1 if win else 0
        self.pnl += trade_pnl(win, entry)
        self.entry += entry

    def row(self) -> str:
        if not self.n:
            return f"{0:5d} {'n/a':>4} {'n/a':>6} {'n/a':>8}"
        return (f"{self.n:5d} {self.wins / self.n * 100:3.0f}% {self.entry / self.n:5.1f}c"
                f" {self.pnl / self.n:+6.1f}c")


def _cells_by(cycles, windows, kind, w):
    return [c for c in cycles if c["kind"] == kind and _nearest_window(c["htc"], windows) == w]


# --- sections ----------------------------------------------------------------------


def report_coverage(cycles, windows, asks):
    print("=== Signal coverage (graded cycles with a usable ladder + winner) ===")
    events = {c["event"] for c in cycles}
    matched = sum(1 for c in cycles if c["fav"] is not None
                  and asks.get(c["id"], {}).get(c["buckets"][c["fav"]].get("subtitle")) is not None)
    src = (f"real ask matched on {matched / len(cycles) * 100:.0f}% of favorite buckets"
           if asks else "real asks OFF — using mid+haircut")
    print(f"  cycles: {len(cycles)}   distinct settled events: {len(events)}   ({src})")
    print(f"  {'window':>6} {'n':>5}   " + "  ".join(f"{f}%" for f in FAMILIES) + "   avg_fams")
    for w in windows + [None]:
        sel = [c for c in cycles if (_nearest_window(c["htc"], windows) == w if w else True)]
        if not sel:
            continue
        cov = [sum(1 for c in sel if c["votes"][f] is not None) / len(sel) * 100 for f in FAMILIES]
        avg = mean(sum(1 for f in FAMILIES if c["votes"][f] is not None) for c in sel)
        label = f"h{int(w)}" if w else "ALL"
        print(f"  {label:>6} {len(sel):5d}   " + "  ".join(f"{c:3.0f}" for c in cov) + f"   {avg:.2f}")
    print("  (fc=forecast hrrr|nws, ens=ensemble, obs=running extreme, pm=polymarket)")


def report_consensus(cycles, windows, haircut, asks, tol, min_rows):
    print(f"\n=== Consensus by agreement K (tol +/-{tol}, REAL asks) — does converging help? ===")
    print(f"  {'kind':>4} {'window':>6} {'K':>2}  {'n':>5} {'win%':>4} {'avg_buy':>7}"
          f" {'net/trade':>9} {'vs_fav':>7}")
    for kind in ("high", "low"):
        for w in windows:
            sel = _cells_by(cycles, windows, kind, w)
            if not sel:
                continue
            fav = Cell()
            for c in sel:
                if c["fav"] is not None:
                    fav.add(c["fav"] == c["winner"], entry_cost(c, c["fav"], haircut, asks))
            fav_net = fav.pnl / fav.n if fav.n else None
            for k in (1, 2, 3, 4):
                cell = Cell()
                for c in sel:
                    p = consensus_pick(c["votes"], k=k, tol=tol)
                    if p:
                        cell.add(p[0] == c["winner"], entry_cost(c, p[0], haircut, asks))
                if not cell.n:
                    continue
                net = cell.pnl / cell.n
                vs = f"{net - fav_net:+5.1f}c" if fav_net is not None else "  n/a"
                flag = " *" if cell.n < min_rows else ""
                print(f"  {kind:>4} h{int(w):<5d} {k:>2}  {cell.n:5d} {cell.wins / cell.n * 100:3.0f}%"
                      f" {cell.entry / cell.n:5.1f}c {net:+8.1f}c {vs:>7}{flag}")
    print("  (* = n<min_rows; win% & net should rise with K; vs_fav = net minus buying the favorite)")


def report_confirmer(cycles, windows, haircut, asks, tol, k, min_rows):
    print(f"\n=== Confirmer test: K>={k} plain vs K>={k} requiring an obs/pm voter (tol +/-{tol}) ===")
    print(f"  {'kind':>4} {'window':>6} |        plain  n  win  buy     net |    +confirmer  n  win  buy     net")
    for kind in ("high", "low"):
        for w in windows:
            sel = _cells_by(cycles, windows, kind, w)
            if not sel:
                continue
            plain, conf = Cell(), Cell()
            for c in sel:
                p = consensus_pick(c["votes"], k=k, tol=tol)
                if p:
                    plain.add(p[0] == c["winner"], entry_cost(c, p[0], haircut, asks))
                pc = consensus_pick(c["votes"], k=k, tol=tol, require_confirmer=True)
                if pc:
                    conf.add(pc[0] == c["winner"], entry_cost(c, pc[0], haircut, asks))
            print(f"  {kind:>4} h{int(w):<5d} |  {plain.row()} |  {conf.row()}")
    print("  (does forcing an independent obs/pm signal into the agreement lift win%/net?)")


def report_weighted(cycles, windows, haircut, asks, weights, tol, min_masses, min_rows):
    wtxt = ",".join(f"{f}={weights[f]:g}" for f in FAMILIES)
    print(f"\n=== Skill-weighted blend ({wtxt}, tol +/-{tol}) — mass threshold vs equal votes ===")
    print(f"  {'kind':>4} {'window':>6} {'mass>=':>6}  {'n':>5} {'win%':>4} {'avg_buy':>7} {'net/trade':>9}")
    for kind in ("high", "low"):
        for w in windows:
            sel = _cells_by(cycles, windows, kind, w)
            if not sel:
                continue
            for mm in min_masses:
                cell = Cell()
                for c in sel:
                    p = weighted_pick(c["votes"], weights=weights, tol=tol, min_mass=mm)
                    if p:
                        cell.add(p[0] == c["winner"], entry_cost(c, p[0], haircut, asks))
                if not cell.n:
                    continue
                flag = " *" if cell.n < min_rows else ""
                print(f"  {kind:>4} h{int(w):<5d} {mm:6.0f}  {cell.n:5d} {cell.wins / cell.n * 100:3.0f}%"
                      f" {cell.entry / cell.n:5.1f}c {cell.pnl / cell.n:+8.1f}c{flag}")
    print("  (obs/pm weighted 2x; mass>=4 ~ 'two independent confirmers or one + both forecasts')")


def report_pairs(cycles, tol, min_rows):
    print(f"\n=== Which families agreeing matters — pairwise co-agreement (tol +/-{tol}) ===")
    print(f"  {'pair':>9} {'kind':>4}  {'n':>5} {'agree_hit%':>10}")
    pairs = [("fc", "ens"), ("fc", "obs"), ("fc", "pm"), ("ens", "obs"), ("ens", "pm"), ("obs", "pm")]
    for a, b in pairs:
        for kind in ("high", "low"):
            agree = hit = 0
            for c in cycles:
                if c["kind"] != kind:
                    continue
                ia, ib = c["votes"][a], c["votes"][b]
                if ia is None or ib is None or abs(ia - ib) > tol:
                    continue
                agree += 1
                if ia == c["winner"]:
                    hit += 1
            if agree:
                flag = " *" if agree < min_rows else ""
                print(f"  {a + '+' + b:>9} {kind:>4}  {agree:5d} {hit / agree * 100:9.0f}%{flag}")
    print("  (agree_hit% = the agreed bucket actually won; forecast-only fc+ens is correlated error)")


def report_edge(cycles, windows, haircut, asks, tol, k, min_rows):
    print(f"\n=== Consensus vs favorite, BY WINDOW (K>={k}, tol +/-{tol}) — where to deviate ===")
    print(f"  {'kind':>4} {'window':>6} | conc==fav  n win net | conc!=fav  n win net | fav_on_diff win%")
    for kind in ("high", "low"):
        for w in windows:
            sel = _cells_by(cycles, windows, kind, w)
            same, diff, fav_diff = Cell(), Cell(), Cell()
            for c in sel:
                p = consensus_pick(c["votes"], k=k, tol=tol)
                if p is None or c["fav"] is None:
                    continue
                idx = p[0]
                entry = entry_cost(c, idx, haircut, asks)
                if idx == c["fav"]:
                    same.add(idx == c["winner"], entry)
                else:
                    diff.add(idx == c["winner"], entry)
                    fav_diff.add(c["fav"] == c["winner"], entry_cost(c, c["fav"], haircut, asks))
            if not (same.n or diff.n):
                continue
            favw = f"{fav_diff.wins / fav_diff.n * 100:3.0f}%" if fav_diff.n else " n/a"
            print(f"  {kind:>4} h{int(w):<5d} |  {same.row()} |  {diff.row()} |  {favw}")
    print("  (deviating from the favorite only pays where conc!=fav win% > fav_on_diff win%)")


# --- main --------------------------------------------------------------------------


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _parse_weights(text: str) -> dict:
    weights = dict(DEFAULT_WEIGHTS)
    for part in (text or "").split(","):
        if "=" in part:
            f, v = part.split("=", 1)
            if f.strip() in FAMILIES:
                weights[f.strip()] = float(v)
    return weights


def load_asks(conn, clause: str, params: list) -> dict:
    """Real resting ask per (cycle, bucket): the nearest snapshot ask within +/-120s of the
    cycle, keyed by wfo row id -> {subtitle: ask}. Empty dict if the table/columns are absent."""
    sql = (
        "SELECT w.id AS cyc_id, b.subtitle, b.yes_ask_cents FROM "
        f"(SELECT id, event_ticker, captured_at FROM weather_forecast_outcomes WHERE {clause}) w "
        "JOIN LATERAL (SELECT DISTINCT ON (subtitle) subtitle, yes_ask_cents "
        "FROM weather_bucket_snapshots s WHERE s.event_ticker = w.event_ticker "
        f"AND s.captured_at BETWEEN w.captured_at - interval '{ASK_MATCH_SECONDS} seconds' "
        f"AND w.captured_at + interval '{ASK_MATCH_SECONDS} seconds' AND s.yes_ask_cents IS NOT NULL "
        "ORDER BY subtitle, abs(extract(epoch FROM s.captured_at - w.captured_at))) b ON true"
    )
    asks: dict = {}
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            for cyc_id, subtitle, ask in cur:
                if ask is not None:
                    asks.setdefault(cyc_id, {})[subtitle] = float(ask)
    except Exception as exc:  # noqa: BLE001 — fall back to the mid+haircut proxy
        print(f"  (real-ask join unavailable: {exc}; using mid+haircut)", file=sys.stderr)
        return {}
    return asks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", default="20,14,8")
    ap.add_argument("--kind", choices=("high", "low", "both"), default="both")
    ap.add_argument("--tol", type=int, default=1)
    ap.add_argument("--haircut", type=float, default=2.0, help="ask proxy when no snapshot matches")
    ap.add_argument("--edge-k", type=int, default=3)
    ap.add_argument("--weights", default="", help="e.g. fc=1,ens=1,obs=2,pm=2")
    ap.add_argument("--min-mass", default="3,4", help="weighted-mass thresholds to report")
    ap.add_argument("--no-real-asks", action="store_true")
    ap.add_argument("--since", default=None)
    ap.add_argument("--min-rows", type=int, default=20)
    args = ap.parse_args(argv)
    windows = [float(x) for x in args.windows.split(",") if x.strip()]
    weights = _parse_weights(args.weights)
    min_masses = [float(x) for x in args.min_mass.split(",") if x.strip()]

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
                "SELECT id, event_ticker, city, kind, hours_to_close, target_date,"
                " forecast_f, hrrr_f, ens_mean_f, obs_running_max_f, obs_running_min_f,"
                " pm_implied_mean_f, market_implied_mean_f, winning_low_f, winning_high_f,"
                " winning_subtitle, raw_json"
                f" FROM weather_forecast_outcomes WHERE {clause}",
                params,
            )
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        asks = {} if args.no_real_asks else load_asks(conn, clause, params)

    cycles = [c for c in (build_cycle(r) for r in rows) if c is not None]
    if not cycles:
        print(f"No usable graded cycles yet ({len(rows)} rows scanned).")
        return 0

    report_coverage(cycles, windows, asks)
    report_consensus(cycles, windows, args.haircut, asks, args.tol, args.min_rows)
    report_confirmer(cycles, windows, args.haircut, asks, args.tol, args.edge_k, args.min_rows)
    report_weighted(cycles, windows, args.haircut, asks, weights, args.tol, min_masses, args.min_rows)
    report_pairs(cycles, args.tol, args.min_rows)
    report_edge(cycles, windows, args.haircut, asks, args.tol, args.edge_k, args.min_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
