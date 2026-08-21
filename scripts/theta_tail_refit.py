"""Stage 1-3 of the theta remediation: score the spliced EVT model against the incumbent one,
strictly out of sample.

The incumbent (`kalshi_bot/theta/spot.py`) returns the raw empirical frequency of trailing
overlapping h-minute returns, so it has no mass beyond its own sample maximum: 53.7% of its
ladder output is exactly 0 and 39.4% exactly 1. The replacement
(`kalshi_bot/theta/tailmodel.py`) keeps the empirical body and splices a fitted Generalized
Pareto onto both tails.

This script asks whether that actually helps, on the only evidence that counts:

  1  DEGENERACY     what fraction of each model's output is exactly 0 or 1
  2  CALIBRATION    observed vs modeled hit rate by probability bucket, with the 0-5% region
                    broken out finely. A model that is calibrated at 10-50% and understates
                    1-5% is useless to a short-tail seller, so the deep buckets are the test.
  3  TAIL_Q SWEEP   the peaks-over-threshold quantile, chosen on a TRAIN period and scored on a
                    disjoint TEST period. Choosing it on the test period would be the same
                    outcome-aware fitting this whole programme exists to stop.
  4  SELECTION      the diagnosis's SELECTED-vs-REJECTED split recomputed under the new
                    probabilities. Stage 3's question is not whether the miss shrinks but how
                    much of it was selection rather than calibration.

NO LOOKAHEAD BY CONSTRUCTION. Every fit for a quote at time t uses returns whose whole window
closed at or before t. The spot series is reconstructed from `crypto_ladder_snapshots.spot` —
the feed the model itself read — because `crypto_spot_candles` only retains ~6 days.

WHAT THIS DOES NOT DO. It changes no book. theta prices off `SpotModel` exactly as before; the
new model is scored here and nowhere else until an experiment is registered to use it.

Read-only; stdlib + psycopg only. The tail model is loaded from its file directly, so the ops
runner never imports the SQLAlchemy-bearing package.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import math
import os
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mmsell_market_types import RO_OPTIONS, _to_libpq_url  # noqa: E402
from theta_tail_diagnosis import (  # noqa: E402
    THETA4_BAND,
    THETA4_EDGE_CENTS,
    THETA_MIN_VOLUME,
    _yes_resolved,
    calib_table,
    poisson_ratio_ci,
)

# Load kalshi_bot/theta/tailmodel.py by PATH. Importing `kalshi_bot.theta.tailmodel` normally
# would execute the package __init__ chain, which pulls SQLAlchemy — not installed for `script`
# ops requests. The module itself needs only math + dataclasses, so this keeps ONE definition of
# the model rather than a copy that can drift from the one the worker would run.
_TM_PATH = pathlib.Path(__file__).resolve().parent.parent / "kalshi_bot" / "theta" / "tailmodel.py"
_TM_SPEC = importlib.util.spec_from_file_location("theta_tailmodel", _TM_PATH)
tm = importlib.util.module_from_spec(_TM_SPEC)
# Register BEFORE executing: @dataclass resolves annotations through sys.modules[__module__],
# so a module loaded by path that is not registered raises inside dataclasses, not here.
sys.modules[_TM_SPEC.name] = tm
_TM_SPEC.loader.exec_module(tm)

TRAIL_DAYS = 5.0            # theta_trail_days
H_BUCKETS = (10, 15, 20, 25, 30, 35)
PRODUCTS = ("BTC", "ETH")


def h_bucket(minutes: float) -> int:
    """Round a quote's minutes-to-close onto the horizon grid the fits are cached on."""
    best = H_BUCKETS[0]
    for h in H_BUCKETS:
        if abs(h - minutes) < abs(best - minutes):
            best = h
    return best


def product_of(series: str | None) -> str:
    return "ETH" if (series or "").upper().startswith("KXETH") else "BTC"


# --- data ------------------------------------------------------------------------------------

def load_spot(cur, since: str, source: str) -> dict[str, dict[int, float]]:
    """{product: {minute_unix: close}}.

    `candles` is `crypto_spot_candles` — a TRUE 1-minute series, but pruned at
    theta_trail_days + 1 = 6 days, so it exists only for the most recent week.

    `ladder` reconstructs a series from `crypto_ladder_snapshots.spot`, which reaches back over
    the whole history but is sampled on theta's ~5-minute cycle. That sparsity is not cosmetic:
    an h-minute return needs closes at BOTH t and t + h, so a 5-minute grid yields ~1/5 the
    samples and roughly 12 INDEPENDENT observations per window at h=35. A tail cannot be fitted
    on 12 observations, and `SplicedReturnModel.underpowered` says so rather than returning a
    floor dressed up as an estimate."""
    if source == "candles":
        cur.execute(
            "SELECT CASE WHEN product LIKE 'ETH%%' THEN 'ETH' ELSE 'BTC' END AS p,"
            "       minute_ts, close FROM crypto_spot_candles WHERE minute_ts >= %s", (since,))
        out: dict[str, dict[int, float]] = {p: {} for p in PRODUCTS}
        for p, m, v in cur.fetchall():
            out.setdefault(p, {})[int(m.timestamp())] = float(v)
        return out
    cur.execute(
        "SELECT CASE WHEN series LIKE 'KXETH%%' THEN 'ETH' ELSE 'BTC' END AS p,"
        "       date_trunc('minute', captured_at) AS m, avg(spot)"
        "  FROM crypto_ladder_snapshots"
        " WHERE captured_at >= %s AND spot IS NOT NULL"
        " GROUP BY 1, 2", (since,))
    out = {p: {} for p in PRODUCTS}
    for p, m, v in cur.fetchall():
        out.setdefault(p, {})[int(m.timestamp())] = float(v)
    return out


def load_quotes(cur, since: str, tte_max: float) -> list[dict]:
    """One quote per market at the far edge of theta's entry window, with the event's
    settlement spot — the same construction the diagnosis used, so the two are comparable."""
    cur.execute(
        "WITH fin AS ("
        "  SELECT DISTINCT ON (event_ticker) event_ticker, spot AS final_spot"
        "    FROM crypto_ladder_snapshots"
        "   WHERE captured_at >= %s AND spot IS NOT NULL"
        "     AND minutes_to_close IS NOT NULL AND minutes_to_close <= 3"
        "   ORDER BY event_ticker, minutes_to_close ASC, captured_at DESC"
        "), q AS ("
        "  SELECT DISTINCT ON (s.market_ticker) s.market_ticker, s.event_ticker, s.series,"
        "         s.strike_type, s.floor_strike, s.cap_strike, s.mid_cents, s.model_p,"
        "         s.model_excess_cents, s.minutes_to_close, s.spot, s.captured_at, s.volume"
        "    FROM crypto_ladder_snapshots s"
        "   WHERE s.captured_at >= %s AND s.spot IS NOT NULL"
        "     AND s.minutes_to_close IS NOT NULL"
        "     AND s.minutes_to_close <= %s AND s.minutes_to_close >= 10"
        "     AND s.mid_cents IS NOT NULL AND s.mid_cents <= 40"
        "   ORDER BY s.market_ticker, s.minutes_to_close DESC"
        ") SELECT q.*, fin.final_spot FROM q JOIN fin USING (event_ticker)",
        (since, since, tte_max))
    rows: list[dict] = []
    for (tkr, _ev, series, st, fs, cs, mid, mp, excess, mtc, spot, ts, vol, final) in cur.fetchall():
        yr = _yes_resolved(st, fs, cs, float(final))
        if yr is None:
            continue
        rows.append({
            "ticker": tkr, "series": series, "product": product_of(series),
            "strike_type": st, "floor": fs, "cap": cs,
            "mid": float(mid), "stored_p": float(mp) if mp is not None else None,
            "stored_excess": float(excess) if excess is not None else None,
            "mtc": float(mtc), "spot": float(spot), "captured_at": ts,
            "volume": float(vol) if vol is not None else None,
            "yes_resolved": yr,
        })
    return rows


# --- model plumbing --------------------------------------------------------------------------

class FitCache:
    """Spliced fits keyed by (product, hour, horizon), each built ONLY from returns whose window
    closed before that hour started. Refitting hourly rather than per quote is an approximation
    of a 5-day trailing window that moves by 1/7200 per minute; refitting per quote would be
    111k fits and would not move a single reported digit."""

    # Return SAMPLES depend only on (product, hour, horizon) — not on tail_q — so they are
    # shared across the sweep's FitCache instances. Rebuilding them per tail_q would triple the
    # dominant cost of the run for identical numbers.
    _RETS: dict[tuple[str, int, int], list[float]] = {}

    def __init__(self, spot: dict[str, dict[int, float]], tail_q: float):
        self.spot = spot
        self.tail_q = tail_q
        self._cache: dict[tuple[str, int, int], object] = {}
        self._rets = FitCache._RETS
        self.misses = 0

    def _returns(self, product: str, as_of: int, h_min: int) -> list[float]:
        key = (product, as_of, h_min)
        got = self._rets.get(key)
        if got is not None:
            return got
        closes = self.spot.get(product) or {}
        h = h_min * 60
        lo = as_of - int(TRAIL_DAYS * 86400)
        out: list[float] = []
        for t in range(lo, as_of - h + 60, 60):
            a, b = closes.get(t), closes.get(t + h)
            if a and b and a > 0 and b > 0:
                out.append(math.log(b / a))
        self._rets[key] = out
        return out

    def get(self, product: str, at: dt.datetime, h_min: int):
        hour = int(at.replace(minute=0, second=0, microsecond=0).timestamp())
        key = (product, hour, h_min)
        if key in self._cache:
            return self._cache[key]
        model = tm.build(self._returns(product, hour, h_min), h_min, tail_q=self.tail_q)
        if model is None:
            self.misses += 1
        self._cache[key] = model
        return model

    def empirical_p(self, product: str, at: dt.datetime, h_min: int, spot: float,
                    strike_type: str, floor, cap, vol_mult: float) -> float | None:
        """The INCUMBENT model's answer, recomputed from the same return sample so the two are
        compared on identical inputs rather than against a stored column captured on a different
        cadence. This is `SpotModel.prob_from_returns` inlined — one expression, and copying it
        here keeps the ops script free of the SQLAlchemy-bearing package."""
        hour = int(at.replace(minute=0, second=0, microsecond=0).timestamp())
        rets = self._returns(product, hour, h_min)
        if not rets or spot is None or spot <= 0:
            return None
        k = vol_mult if vol_mult and vol_mult > 0 else 1.0
        n = len(rets)
        st = (strike_type or "").lower()
        try:
            if st == "greater" and floor:
                x = math.log(float(floor) / spot) / k
                return sum(1 for r in rets if r > x) / n
            if st == "less" and cap:
                x = math.log(float(cap) / spot) / k
                return sum(1 for r in rets if r < x) / n
            if st == "between" and floor and cap:
                lo_x = math.log(float(floor) / spot) / k
                hi_x = math.log(float(cap) / spot) / k
                return sum(1 for r in rets if lo_x <= r <= hi_x) / n
        except (TypeError, ValueError):
            return None
        return None


def score(rows: list[dict], cache: FitCache, vol_mult: float) -> list[dict]:
    """Attach p_old (incumbent, at `vol_mult`) and p_new (spliced) to every quote."""
    out = []
    for r in rows:
        h = h_bucket(r["mtc"])
        model = cache.get(r["product"], r["captured_at"], h)
        p_new = tm.p_yes(model, r["spot"], r["strike_type"], r["floor"], r["cap"])
        p_old = cache.empirical_p(r["product"], r["captured_at"], h, r["spot"],
                                  r["strike_type"], r["floor"], r["cap"], vol_mult)
        if p_new is None or p_old is None:
            continue
        out.append({**r, "p_new": p_new, "p_old": p_old, "h": h,
                    "n_eff": model.n_eff, "upper_xi": model.upper.xi,
                    "lower_xi": model.lower.xi, "underpowered": model.underpowered})
    return out


# --- reporting --------------------------------------------------------------------------------

DEEP_EDGES = (0.0, 0.002, 0.005, 0.01, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 1.01)


def deep_bucket(p: float) -> str:
    for lo, hi in zip(DEEP_EDGES, DEEP_EDGES[1:], strict=False):
        if lo <= p < hi:
            return f"{lo:.3f}-{hi:.3f}"
    return "?"


def head(t: str) -> None:
    print()
    print("=" * 96)
    print(t)
    print("=" * 96)


def calib(rows: list[dict], key: str) -> dict:
    b: dict[str, list] = defaultdict(lambda: [0, 0.0, 0])
    for r in rows:
        c = b[deep_bucket(r[key])]
        c[0] += 1
        c[1] += r[key]
        c[2] += 1 if r["yes_resolved"] else 0
    order = [f"{lo:.3f}-{hi:.3f}" for lo, hi in zip(DEEP_EDGES, DEEP_EDGES[1:], strict=False)]
    return {k: tuple(b[k]) for k in order if k in b}


def degeneracy(rows: list[dict]) -> None:
    head("1. DEGENERACY — how much of each model's output is not a probability")
    print(f"  {'model':<28} {'n':>8} {'exactly 0':>12} {'exactly 1':>12} {'in (0,1)':>12}")
    print("  " + "-" * 76)
    for label, key in (("incumbent (empirical)", "p_old"), ("spliced EVT", "p_new")):
        n = len(rows)
        z = sum(1 for r in rows if r[key] <= 0.0)
        o = sum(1 for r in rows if r[key] >= 1.0)
        print(f"  {label:<28} {n:>8} {z:>7} {z / n * 100:>4.1f}% {o:>7} {o / n * 100:>4.1f}% "
              f"{n - z - o:>7} {(n - z - o) / n * 100:>4.1f}%")


def compare_calibration(rows: list[dict], title: str) -> None:
    head(title)
    print("  A tail 'hits' when YES resolves — the side theta sells. R = observed / modeled.")
    print("  The 0.000-0.020 rows are the test: this is a short-tail book and they are where it")
    print("  lives. Intervals are two-sided 99% Poisson.")
    print()
    calib_table("INCUMBENT (empirical frequency):", calib(rows, "p_old"), "modeled P bucket")
    print()
    calib_table("SPLICED (empirical body + fitted GPD tails):", calib(rows, "p_new"),
                "modeled P bucket")


def sweep(rows_by_q: dict[float, list[dict]], train_end: str) -> None:
    head("3. TAIL_Q SWEEP — chosen on TRAIN, scored on TEST")
    cut = dt.datetime.fromisoformat(train_end).replace(tzinfo=dt.timezone.utc)
    print(f"  train: quotes before {train_end}   test: on/after it")
    print(f"  {'tail_q':>7} {'split':<6} {'n':>7} {'expected':>10} {'observed':>9} {'R':>7} "
          f"{'|log R| deep':>13}")
    print("  " + "-" * 68)
    for q in sorted(rows_by_q):
        for split in ("train", "test"):
            rs = [r for r in rows_by_q[q]
                  if (r["captured_at"] < cut) == (split == "train")]
            if not rs:
                continue
            exp = sum(r["p_new"] for r in rs)
            obs = sum(1 for r in rs if r["yes_resolved"])
            deep = [r for r in rs if r["p_new"] < 0.02]
            de = sum(r["p_new"] for r in deep)
            do = sum(1 for r in deep if r["yes_resolved"])
            dl = abs(math.log((do / de) if de > 0 and do > 0 else 1e-9)) if de > 0 else float("nan")
            r_all = obs / exp if exp > 0 else float("nan")
            print(f"  {q:>7.2f} {split:<6} {len(rs):>7} {exp:>10.2f} {obs:>9} {r_all:>7.2f} "
                  f"{dl:>13.3f}")
    print()
    print("  `|log R| deep` is the miss on the sub-2% population, on a log scale so a 4x")
    print("  understatement and a 4x overstatement score the same. Lower is better. Pick the")
    print("  tail_q that minimises it ON TRAIN, then read its TEST row and nothing else.")


def selection(rows: list[dict]) -> None:
    head("4. SELECTION — the diagnosis's split, recomputed under each model's own excess")
    print(f"  A quote is SELECTED when mid - 100*P >= {THETA4_EDGE_CENTS:.0f}c, mid is in")
    print(f"  {THETA4_BAND[0]:.0f}..{THETA4_BAND[1]:.0f}c and volume >= {THETA_MIN_VOLUME:.0f}.")
    print("  Each model is judged by the trades IT would have chosen, not by the incumbent's.")
    for label, key in (("INCUMBENT", "p_old"), ("SPLICED", "p_new")):
        sel_, rej = [], []
        for r in rows:
            excess = r["mid"] - 100.0 * r[key]
            ok = (excess >= THETA4_EDGE_CENTS
                  and THETA4_BAND[0] <= r["mid"] <= THETA4_BAND[1]
                  and (r["volume"] or 0) >= THETA_MIN_VOLUME)
            (sel_ if ok else rej).append(r)
        print()
        for name, rs in ((f"{label} SELECTED", sel_), (f"{label} REJECTED", rej)):
            exp = sum(r[key] for r in rs)
            obs = sum(1 for r in rs if r["yes_resolved"])
            rr = obs / exp if exp > 0 else float("nan")
            lo, hi = poisson_ratio_ci(obs, exp)
            ci = f"[{lo:.2f}, {hi:.2f}]" if lo is not None else "n/a"
            print(f"    {name:<22} n={len(rs):>6}  expected={exp:>8.2f}  observed={obs:>5}  "
                  f"R={rr:>6.2f}  99% CI {ci}")
    print()
    print("  Stage 3's question is what REMAINS after calibration is repaired. A SELECTED R that")
    print("  falls but stays above REJECTED is residual selection bias, and no re-fit removes")
    print("  it — that is what the stage-4 selection-rule A/B is for.")


def fit_health(rows: list[dict]) -> None:
    head("5. FIT HEALTH — what the tails were actually fitted on")
    xs = sorted(r["upper_xi"] for r in rows)
    ns = sorted(r["n_eff"] for r in rows)
    if not xs:
        return
    def pct(v, q):
        return v[min(len(v) - 1, int(q * (len(v) - 1)))]
    print(f"  upper xi   p10={pct(xs, .1):+.3f}  p50={pct(xs, .5):+.3f}  p90={pct(xs, .9):+.3f}")
    print(f"  n_eff      p10={pct(ns, .1):>6}  p50={pct(ns, .5):>6}  p90={pct(ns, .9):>6}")
    neg = sum(1 for x in xs if x < 0) / len(xs) * 100
    print(f"  fits with a BOUNDED tail (xi < 0): {neg:.1f}% — these are the ones whose deep")
    print("  extrapolation is least trustworthy and where the resolution floor still applies.")


# --- main --------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2026-07-11")
    ap.add_argument("--train-end", default="2026-08-11",
                    help="train/test boundary for the tail_q sweep")
    ap.add_argument("--tail-qs", default="0.90,0.95,0.99")
    ap.add_argument("--primary-q", type=float, default=0.95,
                    help="tail_q used for the calibration and selection sections")
    ap.add_argument("--spot-source", default="candles", choices=("candles", "ladder"),
                    help="candles = true 1-minute feed (last ~6 days only); "
                         "ladder = ~5-minute reconstruction over the full history")
    ap.add_argument("--vol-mult", type=float, default=1.0,
                    help="incumbent's vol_mult (1.0 = base model, 2.0 = theta4's)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    qs = [float(x) for x in args.tail_qs.split(",") if x.strip()]
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            spot = load_spot(cur, args.since, args.spot_source)
            quotes = load_quotes(cur, args.since, 35.0)
    print(f"spot source: {args.spot_source}   minutes: "
          + ", ".join(f"{p}={len(spot.get(p, {}))}" for p in PRODUCTS))
    print(f"ladder quotes with a derivable outcome and mid <= 40c: {len(quotes)}")

    by_q: dict[float, list[dict]] = {}
    for q in qs:
        cache = FitCache(spot, q)
        by_q[q] = score(quotes, cache, args.vol_mult)
        print(f"  tail_q={q}: scored {len(by_q[q])} quotes ({cache.misses} horizons unpriceable)")

    primary = by_q.get(args.primary_q) or by_q[qs[0]]
    powered = [r for r in primary if not r["underpowered"]]
    under = [r for r in primary if r["underpowered"]]
    print(f"  well-powered fits (n_eff >= {tm.MIN_N_EFF_FOR_TAIL}): {len(powered)}   "
          f"underpowered: {len(under)}")
    if not powered:
        print()
        print("  NO fit in this window clears the independent-observation bar. Every spliced")
        print("  probability below is floor-dominated and must NOT be read as an estimate;")
        print("  the tables run on the underpowered set purely to show the shape of the gap.")
        powered = primary
    degeneracy(primary)
    compare_calibration(powered,
                        f"2. CALIBRATION — incumbent (vol_mult={args.vol_mult}) vs spliced "
                        f"(tail_q={args.primary_q})")
    sweep(by_q, args.train_end)
    selection(powered)
    fit_health(primary)
    if under and len(under) != len(primary):
        print()
        print(f"  ({len(under)} of {len(primary)} quotes were scored on an underpowered fit and")
        print("   are excluded from sections 2 and 4.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
