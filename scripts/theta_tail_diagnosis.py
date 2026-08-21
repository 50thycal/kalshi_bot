"""Diagnose WHY theta's tail model misses — six candidate mechanisms, separated by data.

The live canary measured a realized-tail-hit ratio of R ~= 4.1 (11 observed hits against 2.66
modeled) over 38 settled markets, with LCB99 = 1.79. R is computed against the probability the
book ACTUALLY used, and theta4 already runs `mult=2.0` — its model tails are fattened 2x before
anything is sold. So the miss is a miss ON TOP of a doubling, and "fatten some more" is a
parameter change dressed up as a diagnosis.

This script asks which mechanism the data actually supports. Each section is chosen so that ONE
hypothesis predicts a distinctive shape and the others do not:

  A  traded-set calibration        where in the probability range the miss lives
  B  full-universe calibration     the same buckets over EVERY ladder quote in the entry
                                   window, traded or not
  C  selection test                B split by whether the quote cleared theta's edge filter.
                                   If the model is calibrated on quotes it did NOT trade and
                                   broken on the ones it did, the failure is THRESHOLD-SELECTION
                                   BIAS (winner's curse) and no re-fit of the model repairs it.
  D  staleness                     the model's own `spot` against the independent candle feed
                                   at the same minute. A lag shows up as a systematic offset
                                   that grows with |spot velocity|.
  E  momentum / regime             R conditional on the trailing move before entry
  F  time-to-expiry                R by minutes-to-close
  G  scale vs shape                R by Gaussian-equivalent standardized strike distance.
                                   FLAT in z  => the level of vol is wrong (a scale error).
                                   RISING in z => the tail SHAPE is wrong (a fat-tail error).
                                   These call for different fixes and are not interchangeable.

Nothing here proposes a parameter. The output is a diagnosis and the paper-only validation it
implies; changing theta is a separate, later decision.

Evidence base: theta4's PAPER history (n~341 settled trades since 2026-07-11) rather than the
38-market live canary, because the mechanism question does not need real money and the paper
book has ~9x the sample. `crypto_spot_candles` only covers the last ~6 days, so the independent
price feed is used for the staleness check alone; everywhere else the spot series is
reconstructed from `crypto_ladder_snapshots.spot`, which is the feed the model itself read.

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

THETA4_EDGE_CENTS = 6.0     # theta4: edge=6
THETA4_BAND = (3.0, 20.0)   # theta4: hi=20 over the base lo=3
THETA4_TTE_MAX = 35.0       # theta4: ttemax=35 minutes
THETA_MIN_VOLUME = 100.0    # theta_min_volume: untraded strikes are skipped
# Sections E/F are only meaningful on the population theta actually sells into. Run over
# every quote they are swamped by the ~50%-and-up side, where R is 1.00 by construction
# and no tail effect could show through.
TAIL_P_MAX = 0.20


# --- normal helpers -------------------------------------------------------------------------

def norm_ppf(p: float) -> float | None:
    """Inverse standard-normal CDF (Acklam's rational approximation, |err| < 1.15e-9).

    Used only to put a modeled probability on a standardized-distance axis for section G, so
    the tail SHAPE can be read against the tail LEVEL. It is a change of axis, not a claim that
    the underlying returns are Gaussian — theta's own model is explicitly not."""
    if p is None or p <= 0.0 or p >= 1.0:
        return None
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _pois_sf(k: int, lam: float) -> float:
    """P(X >= k) for Poisson(lam) — exact summation, no scipy."""
    if k <= 0:
        return 1.0
    term = math.exp(-lam)
    total = term
    for i in range(1, k):
        term *= lam / i
        total += term
    return max(0.0, min(1.0, 1.0 - total))


def poisson_ratio_ci(obs: int, exp: float, conf: float = 0.99) -> tuple[float | None, float | None]:
    """One-sided-style two-bound interval for obs/exp under Poisson(exp * R), by inversion.

    Reported as a bracket rather than a normal approximation because the counts here are small
    (single-digit observed hits per bucket is normal) and the normal interval on a ratio of
    small counts is not trustworthy near the boundary."""
    if exp <= 0:
        return (None, None)
    alpha = (1.0 - conf) / 2.0
    lo, hi = 0.0, 100.0
    for _ in range(200):                       # LOWER bound: largest R with P(X >= obs) <= alpha
        mid = (lo + hi) / 2
        if _pois_sf(obs, exp * mid) < alpha:
            lo = mid
        else:
            hi = mid
    lower = lo
    lo, hi = 0.0, 100.0
    for _ in range(200):                       # UPPER bound: smallest R with P(X <= obs) <= alpha
        mid = (lo + hi) / 2
        if (1.0 - _pois_sf(obs + 1, exp * mid)) > alpha:
            lo = mid
        else:
            hi = mid
    return (lower, hi)


# --- rendering ------------------------------------------------------------------------------

def head(title: str) -> None:
    print()
    print("=" * 94)
    print(title)
    print("=" * 94)


def calib_table(title: str, buckets: dict, key_label: str = "bucket") -> None:
    """buckets: label -> (n, expected, observed). Ordered by the caller's insertion order."""
    print(f"  {title}")
    print(f"    {key_label:<18} {'n':>7} {'expected':>10} {'observed':>9} {'R':>7} "
          f"{'99% CI':>18}")
    print("    " + "-" * 74)
    tn = te = to = 0
    for label, (n, exp, obs) in buckets.items():
        tn += n
        te += exp
        to += obs
        r = (obs / exp) if exp > 0 else None
        lo, hi = poisson_ratio_ci(obs, exp)
        ci = f"[{lo:.2f}, {hi:.2f}]" if lo is not None else "n/a"
        rs = f"{r:.2f}" if r is not None else "n/a"
        print(f"    {label:<18} {n:>7} {exp:>10.2f} {obs:>9} {rs:>7} {ci:>18}")
    r = (to / te) if te > 0 else None
    lo, hi = poisson_ratio_ci(to, te)
    ci = f"[{lo:.2f}, {hi:.2f}]" if lo is not None else "n/a"
    rs = f"{r:.2f}" if r is not None else "n/a"
    print("    " + "-" * 74)
    print(f"    {'ALL':<18} {tn:>7} {te:>10.2f} {to:>9} {rs:>7} {ci:>18}")


def p_bucket(p: float) -> str:
    for lo, hi in ((0.0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.20),
                   (0.20, 0.35), (0.35, 0.50), (0.50, 1.01)):
        if lo <= p < hi:
            return f"{lo:.2f}-{hi:.2f}"
    return "?"


def z_bucket(z: float) -> str:
    for lo, hi in ((-9, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 99)):
        if lo <= z < hi:
            return f"{max(lo, 0):.1f}-{min(hi, 9):.1f}"
    return "?"


# --- sections -------------------------------------------------------------------------------

def section_traded(cur, books: list[str]) -> None:
    head("A. CALIBRATION ON THE TRADED SET (theta paper books)")
    print("  A tail 'hits' when the side the book SOLD loses at settlement. `resolved_value` is")
    print("  the settlement value for that trade's own side, so a hit is resolved_value == 0.")
    cur.execute(
        "SELECT strategy, market_ticker, side, model_probability, resolved_value,"
        "       assumed_price, created_at"
        "  FROM paper_trades"
        " WHERE strategy = ANY(%s) AND status = 'settled'"
        "   AND NOT coalesce(legacy, false)"
        "   AND model_probability IS NOT NULL AND resolved_value IS NOT NULL",
        (books,),
    )
    rows = cur.fetchall()
    if not rows:
        print("  no settled theta trades with a model probability")
        return
    by_book: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0]))
    for book, _tkr, _side, mp, rv, _px, _ts in rows:
        hit = 1 if int(rv) == 0 else 0
        cell = by_book[book][p_bucket(float(mp))]
        cell[0] += 1
        cell[1] += float(mp)
        cell[2] += hit
    for book in sorted(by_book):
        ordered = dict(sorted(by_book[book].items()))
        print()
        calib_table(f"{book}:", {k: tuple(v) for k, v in ordered.items()}, "modeled P bucket")


def _load_universe(cur, since: str, tte_max: float) -> list[tuple]:
    """Every ladder quote in theta's entry window, with the event's settlement spot.

    The settlement proxy is the LAST observed spot for the event (the snapshot closest to
    close). It is validated against traded ground truth in section B before being used.
    """
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
        "         s.model_excess_cents, s.minutes_to_close, s.spot, s.captured_at,"
        "         s.volume"
        "    FROM crypto_ladder_snapshots s"
        "   WHERE s.captured_at >= %s AND s.model_p IS NOT NULL AND s.spot IS NOT NULL"
        "     AND s.minutes_to_close IS NOT NULL"
        "     AND s.minutes_to_close <= %s AND s.minutes_to_close >= 10"
        "   ORDER BY s.market_ticker, s.minutes_to_close DESC"
        ") SELECT q.*, fin.final_spot FROM q JOIN fin USING (event_ticker)",
        (since, since, tte_max),
    )
    return cur.fetchall()


def _yes_resolved(strike_type: str | None, floor_s, cap_s, final_spot: float) -> bool | None:
    st = (strike_type or "").lower()
    if st == "greater" and floor_s is not None:
        return final_spot > float(floor_s)
    if st == "less" and cap_s is not None:
        return final_spot < float(cap_s)
    if st == "between" and floor_s is not None and cap_s is not None:
        return float(floor_s) <= final_spot <= float(cap_s)
    return None


def section_universe(cur, since: str) -> list[dict]:
    head("B. CALIBRATION OVER THE FULL LADDER UNIVERSE (traded or not)")
    raw = _load_universe(cur, since, THETA4_TTE_MAX)
    recs: list[dict] = []
    for (tkr, _ev, series, st, fs, cs, mid, mp, excess, mtc, spot, ts, vol,
         final_spot) in raw:
        yr = _yes_resolved(st, fs, cs, float(final_spot))
        if yr is None:
            continue
        recs.append({
            "ticker": tkr, "series": series, "strike_type": st,
            "mid": float(mid) if mid is not None else None,
            "model_p": float(mp), "excess": float(excess) if excess is not None else None,
            "mtc": float(mtc), "spot": float(spot), "final": float(final_spot),
            "volume": float(vol) if vol is not None else None,
            "yes_resolved": yr, "captured_at": ts,
        })
    print(f"  ladder quotes in the {THETA4_TTE_MAX:.0f}..10-minute entry window since {since}: "
          f"{len(raw)}  usable outcomes: {len(recs)}")
    z0 = sum(1 for r in recs if r["model_p"] <= 0.0)
    z1 = sum(1 for r in recs if r["model_p"] >= 1.0)
    print(f"  model_p reported as EXACTLY 0: {z0} ({z0 / len(recs) * 100:.1f}%)   "
          f"exactly 1: {z1} ({z1 / len(recs) * 100:.1f}%)")
    print("  A probability of exactly zero is not a probability — a tail that hits there is")
    print("  an unbounded surprise. Those rows carry ~0 expected hits into every table below")
    print("  and are excluded from section G, which needs a finite standardized distance.")

    # Validate the derived outcome against traded ground truth before leaning on it.
    cur.execute(
        "SELECT market_ticker, side, resolved_value FROM paper_trades"
        " WHERE strategy LIKE 'theta%%' AND status='settled' AND resolved_value IS NOT NULL"
        "   AND created_at >= %s", (since,))
    truth: dict[str, bool] = {}
    for tkr, side, rv in cur.fetchall():
        yes_res = (int(rv) == 100) if (side or "").lower() == "yes" else (int(rv) == 0)
        truth[tkr] = yes_res
    checked = [(r["yes_resolved"], truth[r["ticker"]]) for r in recs if r["ticker"] in truth]
    if checked:
        agree = sum(1 for a, b in checked if a == b)
        print(f"  derived-vs-recorded settlement agreement: {agree}/{len(checked)} = "
              f"{agree / len(checked) * 100:.1f}%  "
              f"({'usable' if agree / len(checked) >= 0.97 else 'TOO LOW — treat B/C/E/F/G as advisory'})")
    else:
        print("  no overlap with traded ground truth — the derivation is UNVALIDATED here")

    # A quote's tail hits when the side theta WOULD have sold loses. theta sells the
    # overpriced side: the market's price is above the model, so it sells YES.
    buckets: dict[str, list] = defaultdict(lambda: [0, 0.0, 0])
    for r in recs:
        c = buckets[p_bucket(r["model_p"])]
        c[0] += 1
        c[1] += r["model_p"]
        c[2] += 1 if r["yes_resolved"] else 0
    print()
    calib_table("every quote in the window (the model's unconditional calibration):",
                {k: tuple(v) for k, v in sorted(buckets.items())}, "modeled P bucket")
    return recs


def section_selection(recs: list[dict]) -> None:
    head("C. SELECTION TEST — is the miss in the model, or in what the filter picks?")
    print(f"  theta4 sells only where the market is >= {THETA4_EDGE_CENTS:.0f}c above the model")
    print(f"  and the yes price is in {THETA4_BAND[0]:.0f}..{THETA4_BAND[1]:.0f}c. Same model,")
    print("  same window, same markets — the only difference between these two rows is whether")
    print("  the entry filter would have fired.")

    def cell(rs: list[dict]) -> dict:
        b: dict[str, list] = defaultdict(lambda: [0, 0.0, 0])
        for r in rs:
            c = b[p_bucket(r["model_p"])]
            c[0] += 1
            c[1] += r["model_p"]
            c[2] += 1 if r["yes_resolved"] else 0
        return {k: tuple(v) for k, v in sorted(b.items())}

    def passes(r: dict, with_volume: bool = True) -> bool:
        if r["excess"] is None or r["mid"] is None:
            return False
        if with_volume and (r["volume"] is None or r["volume"] < THETA_MIN_VOLUME):
            return False
        return (r["excess"] >= THETA4_EDGE_CENTS
                and THETA4_BAND[0] <= r["mid"] <= THETA4_BAND[1])

    sel_rs = [r for r in recs if passes(r)]
    rej_rs = [r for r in recs if not passes(r)]
    novol = [r for r in recs if passes(r, with_volume=False)]
    print(f"\n  theta also skips untraded strikes (theta_min_volume={THETA_MIN_VOLUME:.0f}).")
    print(f"  quotes clearing edge+band: {len(novol)};  also clearing volume: {len(sel_rs)}")
    print()
    calib_table(f"SELECTED — would have been sold (n={len(sel_rs)}):", cell(sel_rs),
                "modeled P bucket")
    print()
    calib_table(f"REJECTED — filter declined (n={len(rej_rs)}):", cell(rej_rs),
                "modeled P bucket")
    print()
    print("  Reading: R ~= 1 on REJECTED with R >> 1 on SELECTED is threshold-selection bias —")
    print("  the filter finds the markets where the model is most wrong, because that is what a")
    print("  large model-minus-market gap IS when the model has error. R >> 1 on BOTH is a")
    print("  genuine model failure that a re-fit could address.")


def section_stale(cur, since: str) -> None:
    head("D. STALENESS — the model's spot against the independent candle feed")
    cur.execute(
        "SELECT count(*), avg(s.spot - c.close), stddev(s.spot - c.close),"
        "       avg(abs(s.spot - c.close)), max(abs(s.spot - c.close)), avg(c.close)"
        "  FROM crypto_ladder_snapshots s"
        "  JOIN crypto_spot_candles c"
        "    ON c.product = CASE WHEN s.series LIKE 'KXETH%%' THEN 'ETH-USD' ELSE 'BTC-USD' END"
        "   AND c.minute_ts = date_trunc('minute', s.captured_at)"
        " WHERE s.captured_at >= %s AND s.spot IS NOT NULL", (since,))
    n, bias, sd, mad, mx, lvl = cur.fetchone()
    if not n:
        print("  no overlap between the ladder snapshots and the candle feed")
        return
    print(f"  matched minutes: {n}   mean spot level: {float(lvl):,.0f}")
    print(f"  bias (snapshot - candle): {float(bias):+.2f}   sd: {float(sd):.2f}")
    print(f"  mean |difference|: {float(mad):.2f} = {float(mad) / float(lvl) * 1e4:.2f} bps"
          f"   max: {float(mx):.2f}")
    # A LAG shows up as the snapshot tracking the candle from N minutes ago better than now.
    print()
    print("  best-matching candle offset (a lag would make a NON-zero offset fit best):")
    for lag in (-2, -1, 0, 1, 2, 3, 5):
        cur.execute(
            "SELECT avg(abs(s.spot - c.close))"
            "  FROM crypto_ladder_snapshots s"
            "  JOIN crypto_spot_candles c"
            "    ON c.product = CASE WHEN s.series LIKE 'KXETH%%' THEN 'ETH-USD' ELSE 'BTC-USD' END"
            "   AND c.minute_ts = date_trunc('minute', s.captured_at) - make_interval(mins => %s)"
            " WHERE s.captured_at >= %s AND s.spot IS NOT NULL", (lag, since))
        v = cur.fetchone()[0]
        if v is not None:
            print(f"    lag {lag:+d} min: mean |diff| = {float(v):8.2f}")


def section_momentum(cur, recs: list[dict], since: str) -> None:
    head("E. MOMENTUM / REGIME — R conditional on the move BEFORE entry")
    recs = [r for r in recs if r["model_p"] < TAIL_P_MAX]
    print(f"  restricted to the TAIL population (modeled P < {TAIL_P_MAX:.2f}), n={len(recs)}")
    cur.execute(
        "SELECT date_trunc('minute', captured_at) AS m,"
        "       CASE WHEN series LIKE 'KXETH%%' THEN 'ETH' ELSE 'BTC' END AS p,"
        "       avg(spot)"
        "  FROM crypto_ladder_snapshots"
        " WHERE captured_at >= %s AND spot IS NOT NULL GROUP BY 1,2", (since,))
    spot: dict[tuple[str, object], float] = {}
    for m, p, v in cur.fetchall():
        spot[(p, m)] = float(v)
    import datetime as _dt
    buckets: dict[str, list] = defaultdict(lambda: [0, 0.0, 0])
    for r in recs:
        prod = "ETH" if (r["series"] or "").startswith("KXETH") else "BTC"
        m0 = r["captured_at"].replace(second=0, microsecond=0)
        prev = spot.get((prod, m0 - _dt.timedelta(minutes=30)))
        now = spot.get((prod, m0))
        if prev is None or now is None or prev <= 0:
            continue
        mv = abs(now - prev) / prev * 100.0
        lab = ("0.00-0.10%" if mv < 0.10 else "0.10-0.25%" if mv < 0.25 else
               "0.25-0.50%" if mv < 0.50 else "0.50-1.00%" if mv < 1.0 else ">1.00%")
        c = buckets[lab]
        c[0] += 1
        c[1] += r["model_p"]
        c[2] += 1 if r["yes_resolved"] else 0
    order = ["0.00-0.10%", "0.10-0.25%", "0.25-0.50%", "0.50-1.00%", ">1.00%"]
    calib_table("trailing 30-minute |move| at entry:",
                {k: tuple(buckets[k]) for k in order if k in buckets}, "trailing move")


def section_tte(recs: list[dict]) -> None:
    head("F. TIME-TO-EXPIRY — R by minutes to close at entry")
    recs = [r for r in recs if r["model_p"] < TAIL_P_MAX]
    print(f"  restricted to the TAIL population (modeled P < {TAIL_P_MAX:.2f}), n={len(recs)}")
    buckets: dict[str, list] = defaultdict(lambda: [0, 0.0, 0])
    for r in recs:
        m = r["mtc"]
        lab = ("10-15" if m < 15 else "15-20" if m < 20 else "20-25" if m < 25 else
               "25-30" if m < 30 else "30-35")
        c = buckets[lab]
        c[0] += 1
        c[1] += r["model_p"]
        c[2] += 1 if r["yes_resolved"] else 0
    calib_table("minutes to close:",
                {k: tuple(buckets[k]) for k in ("10-15", "15-20", "20-25", "25-30", "30-35")
                 if k in buckets}, "minutes to close")


def section_shape(recs: list[dict]) -> None:
    head("G. SCALE vs SHAPE — R by standardized strike distance")
    print("  z = Phi^-1(1 - model_p): how far out the model thinks this strike is, in its own")
    print("  probability units mapped onto a common axis. FLAT R across z means every tail is")
    print("  understated by the same factor — a VOLATILITY LEVEL error, fixable by a scale.")
    print("  RISING R means the far tails are understated MORE — a tail SHAPE error, which a")
    print("  scale multiplier cannot fix and `mult=2.0` already tried.")
    buckets: dict[str, list] = defaultdict(lambda: [0, 0.0, 0])
    for r in recs:
        z = norm_ppf(1.0 - r["model_p"])
        if z is None:
            continue
        c = buckets[z_bucket(z)]
        c[0] += 1
        c[1] += r["model_p"]
        c[2] += 1 if r["yes_resolved"] else 0
    order = ["0.0-0.5", "0.5-1.0", "1.0-1.5", "1.5-2.0", "2.0-2.5", "2.5-9.0"]
    calib_table("standardized distance z:",
                {k: tuple(buckets[k]) for k in order if k in buckets}, "z bucket")


# --- main -----------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2026-07-11",
                    help="start of the diagnosis window (default: theta4's first trade)")
    ap.add_argument("--candle-since", default="2026-08-15",
                    help="start of the independent candle feed's coverage")
    ap.add_argument("--books", default="theta4,theta4_pt,theta4_pt2,theta4_pt3,theta,"
                                       "theta1,theta2,theta3")
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
            section_traded(cur, books)
            recs = section_universe(cur, args.since)
            section_selection(recs)
            section_stale(cur, args.candle_since)
            section_momentum(cur, recs, args.since)
            section_tte(recs)
            section_shape(recs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
