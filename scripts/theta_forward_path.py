"""Forward spot path after each theta decision — the toxic-flow research product.

`docs/RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md` §2.5 could not test the momentum/regime hypothesis:
only 11,435 of 63,758 tail quotes had a usable trailing move, and NOTHING recorded what the
underlying did AFTER the decision. Longer candle retention makes that join possible; it does not
make it true. This script is the proof, and the product.

For every decision snapshot it computes, from retained 1-minute closes:

  * forward log return at +5m, +15m, +30m and at the market's CLOSE, in basis points;
  * maximum favourable and adverse excursion over the hold, in the direction the book SOLD —
    favourable means spot moved away from the strike it sold, adverse means toward it;
  * whether each offset resolved, so coverage is a reported number rather than an assumption.

COVERAGE IS THE POINT. A forward-path feature set with silent gaps is worse than none: the
markets whose path is missing are exactly the ones near a data outage, and dropping them
silently selects on that. Section 1 reports coverage per offset BEFORE any economics, and every
later table states the denominator it survived on.

Read-only; stdlib + psycopg only.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mmsell_market_types import RO_OPTIONS, _to_libpq_url  # noqa: E402

OFFSETS_MIN = (5, 15, 30)
PRODUCTS = ("BTC", "ETH")

# Minimum share of the expected hold that must actually be observed before MFE/MAE mean
# anything. An excursion is a statement about a PATH; computing it from two surviving candle
# points and reporting it beside a complete one is how a feed gap becomes a finding. Below this
# the excursion is missing, not small.
MIN_PATH_COMPLETENESS = 0.90


def product_of(series: str | None) -> str:
    return "ETH" if (series or "").upper().startswith("KXETH") else "BTC"


def load_closes(cur, since: str) -> dict[str, dict[int, float]]:
    cur.execute(
        "SELECT CASE WHEN product LIKE 'ETH%%' THEN 'ETH' ELSE 'BTC' END,"
        "       minute_ts, close FROM crypto_spot_candles WHERE minute_ts >= %s", (since,))
    out: dict[str, dict[int, float]] = {p: {} for p in PRODUCTS}
    for p, m, v in cur.fetchall():
        out.setdefault(p, {})[int(m.timestamp())] = float(v)
    return out


def load_decisions(cur, since: str, tte_max: float) -> list[dict]:
    """One decision per market: the snapshot at the far edge of theta's entry window."""
    cur.execute(
        "SELECT DISTINCT ON (market_ticker) market_ticker, series, strike_type,"
        "       floor_strike, cap_strike, mid_cents, model_p, model_excess_cents,"
        "       minutes_to_close, spot, captured_at"
        "  FROM crypto_ladder_snapshots"
        " WHERE captured_at >= %s AND spot IS NOT NULL AND minutes_to_close IS NOT NULL"
        "   AND minutes_to_close <= %s AND minutes_to_close >= 10"
        " ORDER BY market_ticker, minutes_to_close DESC", (since, tte_max))
    rows = []
    for (tkr, series, st, fs, cs, mid, mp, excess, mtc, spot, ts) in cur.fetchall():
        rows.append({
            "ticker": tkr, "series": series, "product": product_of(series),
            "strike_type": (st or "").lower(), "floor": fs, "cap": cs,
            "mid": float(mid) if mid is not None else None,
            "model_p": float(mp) if mp is not None else None,
            "excess": float(excess) if excess is not None else None,
            "mtc": float(mtc), "spot": float(spot), "captured_at": ts,
        })
    return rows


def _at(closes: dict[int, float], ts: int, tol_min: int = 3) -> float | None:
    """Close at `ts`, or within `tol_min` minutes before it. A tolerance, not a guess: the feed
    occasionally misses a minute and refusing the row outright would drop it from coverage for a
    reason unrelated to the market."""
    m = ts // 60 * 60
    for k in range(0, tol_min * 60 + 1, 60):
        v = closes.get(m - k)
        if v:
            return v
    return None


def forward(row: dict, closes: dict[int, float]) -> dict:
    """Forward returns, excursions, and which of them resolved."""
    t0 = int(row["captured_at"].timestamp())
    s0 = _at(closes, t0)
    out: dict = {"has_t0": s0 is not None}
    if s0 is None or s0 <= 0:
        return out
    for off in OFFSETS_MIN:
        s = _at(closes, t0 + off * 60)
        out[f"fwd_{off}m"] = (math.log(s / s0) * 1e4) if s else None
    close_ts = t0 + int(row["mtc"] * 60)
    sc = _at(closes, close_ts)
    out["fwd_close"] = (math.log(sc / s0) * 1e4) if sc else None
    out["hold_min"] = row["mtc"]

    # Excursions over the whole hold, oriented by what the book SOLD. theta sells the overpriced
    # side, so for a `greater` strike it is short the up-move: spot rising is ADVERSE.
    expected = max(1, int(round((close_ts - t0) / 60.0)) + 1)
    path = [closes[t] for t in range(t0 // 60 * 60, close_ts + 60, 60) if t in closes]
    out["path_minutes"] = len(path)
    out["expected_path_minutes"] = expected
    out["path_completeness"] = len(path) / expected
    st = row["strike_type"]

    # BETWEEN is EXCLUDED from directional excursion, not approximated. For a sold `greater` or
    # `less` the adverse direction is fixed by the strike. For a sold BETWEEN — the book is short
    # "spot finishes inside [floor, cap]" — it is not: from inside the band a move toward either
    # edge is favourable, from outside a move toward the band is adverse, and which case applies
    # changes as spot travels. A single signed excursion cannot represent that, and an earlier
    # version reported `max(up, -dn)` as adverse and 0.0 as favourable, which is wrong from
    # inside the band and meaningless from outside. Boundary-aware orientation is possible but
    # needs its own design and tests; until then these markets are counted and skipped.
    if st not in ("greater", "less"):
        out["excursion_excluded"] = "non-directional strike type"
        return out
    if out["path_completeness"] < MIN_PATH_COMPLETENESS or len(path) < 2:
        out["excursion_excluded"] = "path incomplete"
        return out

    rets = [math.log(p / s0) * 1e4 for p in path]
    up, dn = max(rets), min(rets)
    if st == "greater":
        out["mae_bps"], out["mfe_bps"] = up, -dn
    else:                                   # `less`
        out["mae_bps"], out["mfe_bps"] = -dn, up
    return out


def head(t: str) -> None:
    print()
    print("=" * 92)
    print(t)
    print("=" * 92)


def section_coverage(rows: list[dict]) -> None:
    head("1. COVERAGE — is the join actually complete?")
    n = len(rows)
    if not n:
        print("  no decisions in window")
        return
    print(f"  decisions: {n}")
    print(f"  {'feature':<16} {'resolved':>10} {'coverage':>10}")
    print("  " + "-" * 40)
    keys = ["has_t0"] + [f"fwd_{o}m" for o in OFFSETS_MIN] + ["fwd_close", "mae_bps"]
    for k in keys:
        got = sum(1 for r in rows if r.get(k) is not None and r.get(k) is not False)
        print(f"  {k:<16} {got:>10} {got / n * 100:>9.1f}%")
    print()
    print("  PATH COMPLETENESS — observed minutes / expected minutes over the hold. MFE/MAE are")
    print(f"  withheld below {MIN_PATH_COMPLETENESS:.0%}; an excursion computed from a few")
    print("  surviving candle points is not a smaller excursion, it is a different quantity.")
    comp = sorted(r["path_completeness"] for r in rows if r.get("path_completeness") is not None)
    if comp:
        def pct(q):
            return comp[min(len(comp) - 1, int(q * (len(comp) - 1)))]
        full = sum(1 for c in comp if c >= MIN_PATH_COMPLETENESS)
        print(f"    p10={pct(.1):.2%}  p50={pct(.5):.2%}  p90={pct(.9):.2%}   "
              f"at or above threshold: {full}/{len(comp)} ({full / len(comp):.1%})")
    excl: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.get("excursion_excluded"):
            excl[r["excursion_excluded"]] += 1
    if excl:
        print("    excursion exclusions:")
        for reason, c in sorted(excl.items(), key=lambda kv: -kv[1]):
            print(f"      {reason:<28} {c:>7} ({c / n * 100:.1f}%)")
    print()
    print("  A feature below ~95% is not usable as a control: the missing rows cluster around")
    print("  feed outages, so dropping them silently selects on exactly the regime being")
    print("  studied. Report the denominator wherever such a feature is used.")


def section_by_regime(rows: list[dict]) -> None:
    head("2. FORWARD PATH BY DECISION-TIME MODEL PROBABILITY")
    print("  The tail population is where theta trades. If forward moves after a SELECTED-looking")
    print("  quote are systematically adverse, that is the toxic-flow signature the randomized")
    print("  quote-and-decline experiment would confirm causally.")
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        p = r.get("model_p")
        if p is None:
            continue
        lab = ("0.00-0.02" if p < 0.02 else "0.02-0.05" if p < 0.05 else
               "0.05-0.10" if p < 0.10 else "0.10-0.20" if p < 0.20 else "0.20+")
        buckets[lab].append(r)
    print(f"  {'modeled P':<12} {'n':>6} " + " ".join(f"{'+' + str(o) + 'm':>9}" for o in OFFSETS_MIN)
          + f" {'close':>9} {'MAE':>9} {'MFE':>9}")
    print("  " + "-" * 78)
    for lab in ("0.00-0.02", "0.02-0.05", "0.05-0.10", "0.10-0.20", "0.20+"):
        rs = buckets.get(lab) or []
        if not rs:
            continue
        cells = []
        for k in [f"fwd_{o}m" for o in OFFSETS_MIN] + ["fwd_close", "mae_bps", "mfe_bps"]:
            vals = [r[k] for r in rs if r.get(k) is not None]
            cells.append(f"{sum(vals) / len(vals):>9.1f}" if vals else f"{'n/a':>9}")
        print(f"  {lab:<12} {len(rs):>6} " + " ".join(cells))
    print()
    print("  Values are basis points of the UNDERLYING, signed so that positive MAE means spot")
    print("  moved toward the strike the book sold.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2026-08-15")
    ap.add_argument("--tte-max", type=float, default=35.0)
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            closes = load_closes(cur, args.since)
            decisions = load_decisions(cur, args.since, args.tte_max)
    print("retained 1-minute closes: "
          + ", ".join(f"{p}={len(closes.get(p, {}))}" for p in PRODUCTS))
    rows = [{**d, **forward(d, closes.get(d["product"]) or {})} for d in decisions]
    section_coverage(rows)
    section_by_regime(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
