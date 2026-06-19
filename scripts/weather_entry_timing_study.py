"""Offline entry-TIMING study — should we drop fixed h20/h14/h8 windows and instead enter
when the *information* is ready? Replays the persisted per-cycle dataset
(`weather_forecast_outcomes`) and, for each settled event, pulls the trigger under four
policies that all BUY THE FAVORITE and HOLD to settlement (so only the entry *timing*
differs, not bucket selection or exit):

  fixed   — the current rule: buy at the cycle nearest each fixed window (h20/h14/h8).
  A:conv  — rolling: enter the first cycle the favorite's price (conviction) clears a bar.
  B:obs   — observation-locked: enter the first cycle the running observed extreme
            confirms the favorite bucket (reality has caught up; the market often lags).
  C:stable— convergence+stability: enter the first cycle the independent families agree
            (K>=k within +/-tol) AND the favorite has held the same bucket for N cycles
            (the antidote to chasing a favorite that just shifted).

Reports, by kind/city: trades, event coverage, win%, avg buy, net cents/trade, and the
avg hours-to-close AT ENTRY (when each policy fires). Entry price uses the REAL resting ask
joined from `weather_bucket_snapshots`. Self-contained (stdlib + psycopg). Usage:
    {"type":"script","name":"weather_entry_timing_study","args":["--kind","high","--city","LAX"]}
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on -c statement_timeout=90000 "
    "-c idle_in_transaction_session_timeout=90000"
)
FAMILIES = ("fc", "ens", "obs", "pm")
ASK_MATCH_SECONDS = 120


# --- pricing / buckets (mirrors weather_consensus_study) ---------------------------


def fee_cents(entry: float) -> int:
    p = max(0.0, min(1.0, entry / 100.0))
    return math.ceil(7.0 * p * (1.0 - p))


def trade_pnl(win: bool, entry: float) -> float:
    return (100.0 if win else 0.0) - entry - fee_cents(entry)


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
        "id": row.get("id"), "kind": kind, "city": row.get("city"),
        "htc": row.get("hours_to_close"), "event": row.get("event_ticker"),
        "buckets": buckets,
        "votes": {f: bucket_for_temp(t, buckets) for f, t in temps.items()},
        "winner": win_idx, "fav": fav_index(buckets),
    }


def consensus_k(votes: dict, fav: int, tol: int) -> int:
    """How many families vote within +/-tol of the favorite bucket."""
    return sum(1 for idx in votes.values() if idx is not None and abs(idx - fav) <= tol)


def entry_cost(cycle: dict, idx: int, haircut: float, asks: dict) -> float:
    b = cycle["buckets"][idx]
    real = asks.get(cycle["id"], {}).get(b.get("subtitle")) if asks else None
    if real is not None:
        return max(1.0, min(99.0, float(real)))
    mid = b.get("mid_cents")
    return max(1.0, min(99.0, (mid if mid is not None else 50.0) + haircut))


# --- entry policies: each returns the chosen entry cycle for ONE event, or None -----


def _band(event_cycles, a) -> list[dict]:
    return sorted(
        [c for c in event_cycles if c["htc"] is not None and a.htc_min <= c["htc"] <= a.htc_max
         and c["fav"] is not None],
        key=lambda c: -c["htc"],  # time order: far-from-close -> near
    )


def entry_A(event_cycles, a):
    for c in _band(event_cycles, a):
        mid = c["buckets"][c["fav"]].get("mid_cents")
        if mid is not None and mid >= a.conv_threshold:
            return c
    return None


def entry_B(event_cycles, a):
    for c in _band(event_cycles, a):
        ov = c["votes"]["obs"]
        if ov is not None and abs(ov - c["fav"]) <= a.tol:
            return c
    return None


def entry_C(event_cycles, a):
    run, prev = 0, None
    for c in _band(event_cycles, a):
        sub = c["buckets"][c["fav"]].get("subtitle")
        run = run + 1 if sub == prev else 1
        prev = sub
        if run >= a.stable_cycles and consensus_k(c["votes"], c["fav"], a.tol) >= a.conv_k:
            return c
    return None


def entry_fixed(event_cycles, windows, a):
    """The current rule: nearest cycle to each window (within 3h). May yield several trades."""
    cand = [c for c in event_cycles if c["htc"] is not None and c["fav"] is not None]
    out = []
    for w in windows:
        if not cand:
            break
        c = min(cand, key=lambda c: abs(c["htc"] - w))
        if abs(c["htc"] - w) <= 3.0:
            out.append(c)
    return out


# --- aggregation -------------------------------------------------------------------


class Cell:
    __slots__ = ("n", "wins", "pnl", "buy", "htc", "events")

    def __init__(self):
        self.n = self.wins = 0
        self.pnl = self.buy = self.htc = 0.0
        self.events = set()

    def add(self, c: dict, haircut: float, asks: dict):
        entry = entry_cost(c, c["fav"], haircut, asks)
        win = c["fav"] == c["winner"]
        self.n += 1
        self.wins += 1 if win else 0
        self.pnl += trade_pnl(win, entry)
        self.buy += entry
        self.htc += c["htc"] or 0.0
        self.events.add(c["event"])

    def row(self, total_events: int) -> str:
        if not self.n:
            return f"{0:6d} {'--':>5} {'--':>5} {'--':>6} {'--':>8} {'--':>7} {'--':>9}"
        cov = len(self.events) / total_events * 100 if total_events else 0
        return (f"{self.n:6d} {len(self.events):5d} {cov:4.0f}% {self.wins / self.n * 100:4.0f}%"
                f" {self.buy / self.n:5.1f}c {self.pnl / self.n:+7.1f}c {self.htc / self.n:6.1f}h"
                f" {self.pnl / 100:+8.2f}$")


def run_report(cycles, windows, a, asks, label: str) -> None:
    by_event: dict[str, list] = {}
    for c in cycles:
        by_event.setdefault(c["event"], []).append(c)
    total_events = len(by_event)
    cells = {"fixed": Cell(), "A:conv": Cell(), "B:obs": Cell(), "C:stable": Cell()}
    for ev_cycles in by_event.values():
        for c in entry_fixed(ev_cycles, windows, a):
            cells["fixed"].add(c, a.haircut, asks)
        for name, fn in (("A:conv", entry_A), ("B:obs", entry_B), ("C:stable", entry_C)):
            c = fn(ev_cycles, a)
            if c is not None:
                cells[name].add(c, a.haircut, asks)
    wtxt = "/".join(f"h{int(w)}" for w in windows)
    print(f"\n=== Entry-timing — {label} ({total_events} settled events) ===")
    print("  policy        trades   ev cov%  win%  avg_buy  net/trd  htc@in  total$")
    for name in ("fixed", "A:conv", "B:obs", "C:stable"):
        tag = name if name != "fixed" else f"fixed({wtxt})"
        print(f"  {tag:<13} {cells[name].row(total_events)}")
    print(f"  A:conv = fav price >= {a.conv_threshold}c | B:obs = obs confirms fav (+/-{a.tol})"
          f" | C:stable = K>={a.conv_k} & fav held {a.stable_cycles} cyc")
    print("  all policies BUY THE FAVORITE and HOLD to settlement; band ="
          f" {a.htc_min:g}-{a.htc_max:g}h to close. net/trade is the deciding number.")


# --- main --------------------------------------------------------------------------


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def load_asks(conn, clause: str, params: list) -> dict:
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
    except Exception as exc:  # noqa: BLE001 — fall back to mid+haircut
        print(f"  (real-ask join unavailable: {exc}; using mid+haircut)", file=sys.stderr)
        return {}
    return asks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", default="20,14,8")
    ap.add_argument("--kind", choices=("high", "low", "both"), default="high")
    ap.add_argument("--city", default="ALL", help="city code (e.g. LAX) or ALL")
    ap.add_argument("--tol", type=int, default=1)
    ap.add_argument("--haircut", type=float, default=2.0)
    ap.add_argument("--conv-threshold", type=float, default=60.0, dest="conv_threshold")
    ap.add_argument("--conv-k", type=int, default=3, dest="conv_k")
    ap.add_argument("--stable-cycles", type=int, default=2, dest="stable_cycles")
    ap.add_argument("--htc-min", type=float, default=1.0, dest="htc_min")
    ap.add_argument("--htc-max", type=float, default=24.0, dest="htc_max")
    ap.add_argument("--by-city", action="store_true", help="also break down per city")
    ap.add_argument("--no-real-asks", action="store_true")
    ap.add_argument("--since", default=None)
    args = ap.parse_args(argv)
    windows = [float(x) for x in args.windows.split(",") if x.strip()]

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    where, params = ["hours_to_close IS NOT NULL"], []
    if args.kind != "both":
        where.append("kind = %s")
        params.append(args.kind)
    if args.city != "ALL":
        where.append("city = %s")
        params.append(args.city)
    if args.since:
        where.append("target_date >= %s")
        params.append(args.since)
    clause = " AND ".join(where)

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, event_ticker, city, kind, hours_to_close, target_date, forecast_f,"
                " hrrr_f, ens_mean_f, obs_running_max_f, obs_running_min_f, pm_implied_mean_f,"
                " winning_low_f, winning_high_f, winning_subtitle, raw_json"
                f" FROM weather_forecast_outcomes WHERE {clause}", params)
            cols = [d.name for d in cur.description]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        asks = {} if args.no_real_asks else load_asks(conn, clause, params)

    cycles = [c for c in (build_cycle(r) for r in rows) if c is not None]
    if not cycles:
        print(f"No usable graded cycles ({len(rows)} rows scanned).")
        return 0

    src = "REAL asks" if asks else "mid+haircut"
    print(f"=== Entry-timing study — kind={args.kind} city={args.city} ({src}) ===")
    run_report(cycles, windows, args, asks, f"kind={args.kind} city={args.city}")
    if args.by_city:
        for city in sorted({c["city"] for c in cycles if c["city"]}):
            run_report([c for c in cycles if c["city"] == city], windows, args, asks, f"city={city}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
