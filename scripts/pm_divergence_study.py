"""PMDIV — when Polymarket and Kalshi disagree about tomorrow's temperature, who is right?

`pm_divergence` ships as a DSL metric for the evo agents on an assumption-free premise ("do two
venues disagree?" needs no forecasting skill of ours). The premise has never been tested. This
probe tests it over the ~200 settled weather events where both venues quoted, and gates roughly
two PRs of build. See docs/PMDIV_THESIS.md for the pre-registered predictions.

Urgency: `weather_validation` just showed our NWS/ensemble forecast is right 0%/4% of the time in
the two extreme forecast-vs-market divergence bands — bigger disagreement, MORE wrong. Divergence
is not automatically signal in this domain, so the same test must run on the Polymarket side.

THE BATCHING TRAP this probe exists to avoid. `repository.insert_polymarket_snapshots` stamps
`captured_at=_now()` INSIDE its row loop, so every bucket of one ladder capture carries a distinct
microsecond timestamp (confirmed: all 451,198 timestamps in the table hold exactly one row). Any
code that selects "the newest ladder" by exact-equality against max(captured_at) therefore gets a
SINGLE BUCKET, and its "implied mean" is that bucket's midpoint. That is the live defect in
`weather/validation.py::_pm_implied_mean`, and it is why the reported pm_err=9.38F measures
nothing. Here ladders are rebuilt by CLUSTERING rows on a time gap (`--batch-gap-s`), and the
broken number is recomputed alongside the corrected one so the artifact is proven, not asserted.
Kalshi's own inserts hoist `now` out of the loop, so its ladders group by exact captured_at.

NO LOOKAHEAD: each Kalshi cycle is scored using only the Polymarket ladder captured at or before
that cycle's timestamp. Settlement labels the outcome; it never selects or prices a point.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/pm_divergence_study.py
    # or:  {"type": "script", "name": "pm_divergence_study", "id": "pmdiv-1"}
    #      {"type": "script", "name": "pm_divergence_study", "args": ["--max-hours", "20"]}

The measurement math lives in pure functions (implied_mean / cluster_batches / bucket_wins /
band_of / settle_cents) unit-tested in tests/test_pm_divergence_study.py, independent of the DB.
"""

from __future__ import annotations

import math
import os
import sys
from collections import defaultdict

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

# A ladder capture writes its buckets within milliseconds; successive captures are minutes apart.
# Any gap threshold between those two scales recovers the batches; 120s is comfortably inside it.
DEFAULT_BATCH_GAP_S = 120.0

# Representative temperature for an open-ended bucket ("83 or below"), as an offset from its one
# finite edge. Mirrors weather/validation.py's OPEN_BUCKET_HALF_WIDTH so the two agree.
OPEN_BUCKET_HALF_WIDTH = 1.5

# Pre-registered divergence bands (degF), from docs/PMDIV_THESIS.md P2. Do not re-scope.
BANDS = ((-1e9, -3.0), (-3.0, -1.0), (-1.0, 1.0), (1.0, 3.0), (3.0, 1e9))

# P2's floors: an outer band needs this many cycles before its rate is read as anything.
MIN_BAND_N = 300


def fee_c(price_c: float) -> float:
    """Kalshi entry fee in CENTS per contract: ceil(0.07 * p*(1-p) * 100), p in [0,1]."""
    p = min(max(price_c / 100.0, 0.0), 1.0)
    return math.ceil(7.0 * p * (1.0 - p))


def bucket_mid_temp(low: float | None, high: float | None) -> float | None:
    """Representative temperature for a bucket; open-ended sides use the finite edge offset by a
    half-width. Matches weather/validation.py::_bucket_mid_temp."""
    if low is not None and high is not None:
        return (low + high) / 2.0
    if high is not None:
        return high - OPEN_BUCKET_HALF_WIDTH
    if low is not None:
        return low + OPEN_BUCKET_HALF_WIDTH
    return None


def implied_mean(ladder: list[tuple[float | None, float | None, float]]) -> float | None:
    """Probability-weighted mean temperature of a ladder of (low_f, high_f, weight).

    Normalized by the weight sum, so it works on Kalshi mids (which sum above 1 because of the
    spread) and Polymarket probabilities alike. None when nothing usable is present."""
    num = den = 0.0
    for low, high, w in ladder:
        mt = bucket_mid_temp(low, high)
        if mt is None or w is None or w <= 0:
            continue
        num += w * mt
        den += w
    return (num / den) if den > 0 else None


def cluster_batches(rows: list[tuple], gap_s: float = DEFAULT_BATCH_GAP_S) -> list[list[tuple]]:
    """Group time-sorted rows into capture batches, splitting where the gap exceeds `gap_s`.

    Rows are (captured_at, ...) tuples. This is what makes a Polymarket ladder recoverable at all
    given the per-row timestamps — see the batching trap in the module docstring."""
    out: list[list[tuple]] = []
    for row in sorted(rows, key=lambda r: r[0]):
        if out and (row[0] - out[-1][-1][0]).total_seconds() <= gap_s:
            out[-1].append(row)
        else:
            out.append([row])
    return out


def bucket_wins(low: float | None, high: float | None, actual: float) -> bool:
    """Did this bucket contain the settled extreme? Open sides are unbounded on that side."""
    if low is not None and actual < low:
        return False
    if high is not None and actual > high:
        return False
    return low is not None or high is not None


def band_of(divergence_f: float) -> tuple[float, float]:
    for lo, hi in BANDS:
        if lo <= divergence_f < hi:
            return (lo, hi)
    return BANDS[-1]


def band_label(band: tuple[float, float]) -> str:
    lo, hi = band
    left = "-inf" if lo <= -1e8 else f"{lo:+.0f}"
    right = "+inf" if hi >= 1e8 else f"{hi:+.0f}"
    return f"[{left},{right})"


def settle_cents(ask_c: float | None, won: bool) -> float | None:
    """P&L in cents of buying one YES contract at the ask and holding to settlement, net of the
    entry fee. Settlement itself is free; holding to it pays no second leg."""
    if ask_c is None or not (1.0 <= ask_c <= 99.0):
        return None
    return (100.0 if won else 0.0) - ask_c - fee_c(ask_c)


def favored_bucket(
    buckets: list[tuple[float | None, float | None, float | None]], pm_mean: float
) -> int | None:
    """Index of the Kalshi bucket whose representative temperature is nearest Polymarket's implied
    mean — the bucket a pm-following trade would buy. None if none is usable."""
    best = None
    for i, (low, high, _ask) in enumerate(buckets):
        mt = bucket_mid_temp(low, high)
        if mt is None:
            continue
        d = abs(mt - pm_mean)
        if best is None or d < best[0]:
            best = (d, i)
    return None if best is None else best[1]


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.0f}%" if d else "-"


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def load(cur, max_hours: float):
    """Settled events -> (kalshi cycles, polymarket batches, actual extremes). One pass each."""
    cur.execute(
        "select event_ticker, city, kind, target_date, actual_high_f, actual_low_f"
        " from weather_settlements"
        " where city is not null and target_date is not null"
    )
    settle = {}
    for ev, city, kind, target, hi, lo in cur.fetchall():
        k = kind or "high"
        actual = hi if k == "high" else lo
        if actual is not None:
            settle[ev] = (city, k, target, float(actual))
    if not settle:
        return {}, {}, {}

    cur.execute(
        "select event_ticker, captured_at, low_f, high_f, mid_cents, yes_ask_cents, hours_to_close"
        " from weather_bucket_snapshots"
        " where mid_cents is not null and hours_to_close is not null and hours_to_close <= %s"
        " order by event_ticker, captured_at",
        (max_hours,),
    )
    kalshi: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for ev, cap, low, high, mid, ask, htc in cur.fetchall():
        if ev in settle:
            kalshi[ev][cap].append((low, high, mid, ask, htc))

    cur.execute(
        "select city, kind, target_date, captured_at, low_f, high_f, yes_prob"
        " from polymarket_snapshots"
        " where yes_prob is not null"
        " order by city, kind, target_date, captured_at"
    )
    pm_rows: dict[tuple, list] = defaultdict(list)
    for city, kind, target, cap, low, high, prob in cur.fetchall():
        pm_rows[(city, kind, target)].append((cap, low, high, float(prob)))
    return kalshi, pm_rows, settle


def report(cur, *, max_hours: float, gap_s: float) -> None:
    kalshi, pm_rows, settle = load(cur, max_hours)
    print(f"PMDIV — Polymarket vs Kalshi divergence (read-only; htc<= {max_hours:.0f}h)\n")
    if not kalshi:
        print("  no settled events with bucket snapshots yet — nothing to measure.")
        return

    # Rebuild Polymarket ladders per (city, kind, target_date) by clustering on the time gap.
    pm_batches: dict[tuple, list[tuple]] = {}
    for key, rows in pm_rows.items():
        batches = cluster_batches(rows, gap_s)
        pm_batches[key] = [
            (b[-1][0], [(low, high, prob) for _c, low, high, prob in b]) for b in batches
        ]

    sizes = [len(b[1]) for bs in pm_batches.values() for b in bs]
    print("=== Polymarket ladder reconstruction ===")
    print(f"  pm rows: {sum(len(v) for v in pm_rows.values())}   (city,kind,date) keys:"
          f" {len(pm_rows)}   batches: {len(sizes)}")
    if sizes:
        print(f"  buckets per batch: mean {_mean(sizes):.1f}  min {min(sizes)}  max {max(sizes)}")
        print("  (a mean near 1 would mean clustering FAILED and every number below is the"
              " same artifact this probe exists to avoid)")

    cycles = []  # (city, kind, ev, htc, pm_mean, k_mean, actual, buckets, pm_age_s)
    for ev, by_cap in kalshi.items():
        city, kind, target, actual = settle[ev]
        batches = pm_batches.get((city, kind, target)) or []
        for cap, rows in sorted(by_cap.items()):
            usable = [(low, high, mid, ask, htc) for low, high, mid, ask, htc in rows]
            if len(usable) < 2:
                continue
            k_mean = implied_mean([(low, high, mid) for low, high, mid, _a, _h in usable])
            if k_mean is None:
                continue
            prior = [b for b in batches if b[0] <= cap]  # NO LOOKAHEAD
            if not prior:
                continue
            pm_cap, ladder = prior[-1]
            pm_mean = implied_mean(ladder)
            if pm_mean is None:
                continue
            # What validation.py actually computes: max(captured_at) matches exactly one row —
            # the last-inserted bucket of the newest batch — so its "mean" is that midpoint.
            artifact = bucket_mid_temp(ladder[-1][0], ladder[-1][1])
            cycles.append((
                city, kind, ev, usable[0][4], pm_mean, k_mean, actual,
                [(low, high, ask) for low, high, _m, ask, _h in usable],
                (cap - pm_cap).total_seconds(), artifact,
            ))

    print(f"\n=== Coverage ===\n  scored cycles: {len(cycles)}"
          f"   events: {len({c[2] for c in cycles})}"
          f"   cities: {sorted({c[0] for c in cycles})}")
    if not cycles:
        print("  no cycle had both a Kalshi ladder and a prior Polymarket ladder — HOLD.")
        return
    ages = [c[8] / 60.0 for c in cycles]
    print(f"  polymarket ladder age at decision: mean {_mean(ages):.1f} min,"
          f" median {sorted(ages)[len(ages)//2]:.1f} min"
          "   (staleness biases AGAINST finding pm-leads)")

    # --- P0/P1: accuracy, corrected vs the broken single-bucket computation -------------
    pm_errs = [abs(c[4] - c[6]) for c in cycles]
    k_errs = [abs(c[5] - c[6]) for c in cycles]
    broken = [abs(c[9] - c[6]) for c in cycles if c[9] is not None]
    pm_err, k_err = _mean(pm_errs), _mean(k_errs)
    print("\n=== P0/P1 — Polymarket accuracy (corrected vs the artifact) ===")
    print(f"  corrected pm_err={pm_err:.2f}F   kalshi mkt_err={k_err:.2f}F"
          f"   ratio={pm_err / k_err:.2f}x   pm_better="
          f"{_pct(sum(1 for a, b in zip(pm_errs, k_errs, strict=True) if a < b), len(pm_errs))}")
    if broken:
        print(f"  single-bucket artifact (what validation.py computes): pm_err={_mean(broken):.2f}F"
              f"  over n={len(broken)}")
    p0 = pm_err < 3.0
    p1 = pm_err <= 1.5 * k_err
    print(f"  P0 (corrected pm_err < 3.0F): {'PASS' if p0 else 'FAIL'}"
          f"   |   P1 (pm_err <= 1.5x mkt_err): {'PASS' if p1 else 'FAIL'}"
          f"   [KILL if > 2.0x: {'YES' if pm_err > 2.0 * k_err else 'no'}]")

    # --- P2: the divergence bands -------------------------------------------------------
    print("\n=== P2 — divergence (pm_mean - kalshi_mean) vs who was right ===")
    print("      band(degF)     n  pm_err  mkt_err   pm_better%")
    by_band: dict[tuple, list] = defaultdict(list)
    for c in cycles:
        by_band[band_of(c[4] - c[5])].append(c)
    outer_rates = {}
    for band in BANDS:
        rows = by_band.get(band) or []
        if not rows:
            print(f"  {band_label(band):>12} {0:5d}       -        -            -")
            continue
        pe = [abs(r[4] - r[6]) for r in rows]
        ke = [abs(r[5] - r[6]) for r in rows]
        better = sum(1 for a, b in zip(pe, ke, strict=True) if a < b)
        rate = 100.0 * better / len(rows)
        flag = "" if len(rows) >= MIN_BAND_N else "  (thin)"
        print(f"  {band_label(band):>12} {len(rows):5d} {_mean(pe):7.2f} {_mean(ke):8.2f}"
              f" {rate:11.0f}%{flag}")
        if band in (BANDS[0], BANDS[-1]):
            outer_rates[band] = (rate, len(rows))
    print("  (pm_better% > 50 in an OUTER band => Polymarket is right when it disagrees:"
          " the tradable direction)")

    strong = [b for b, (r, n) in outer_rates.items() if r >= 55.0 and n >= MIN_BAND_N]
    weak = [r for _b, (r, _n) in outer_rates.items()]
    p2 = bool(strong) and (min(weak) >= 45.0 if weak else False)
    p2_kill = bool(weak) and max(weak) <= 50.0
    print(f"  P2: {'PASS' if p2 else 'FAIL'}"
          f"   [KILL if both outer bands <= 50%: {'YES' if p2_kill else 'no'}]")

    # --- P3: does the signal clear the fee? ---------------------------------------------
    print("\n=== P3 — buying the Kalshi bucket Polymarket favors, held to settlement ===")
    print("      band(degF)     n   c/trade   win%")
    p3_best = None
    for band in (BANDS[0], BANDS[-1]):
        pnl = []
        wins = 0
        for c in by_band.get(band) or []:
            idx = favored_bucket(c[7], c[4])
            if idx is None:
                continue
            low, high, ask = c[7][idx]
            won = bucket_wins(low, high, c[6])
            v = settle_cents(ask, won)
            if v is not None:
                pnl.append(v)
                wins += int(won)
        if not pnl:
            print(f"  {band_label(band):>12} {0:5d}         -      -")
            continue
        per = _mean(pnl)
        print(f"  {band_label(band):>12} {len(pnl):5d} {per:9.2f} {_pct(wins, len(pnl)):>6}")
        if p3_best is None or per > p3_best:
            p3_best = per
    print("  (net of the entry fee; settlement is free. Pre-registered bar: > +2c/contract)")
    p3 = p3_best is not None and p3_best > 2.0

    # --- P4: robustness ------------------------------------------------------------------
    print("\n=== P4 — robustness (outer-band pm_better%, by half and by city) ===")
    outer = [c for c in cycles if band_of(c[4] - c[5]) in (BANDS[0], BANDS[-1])]
    if outer:
        mid = sorted(outer, key=lambda c: c[3], reverse=True)
        halves = (mid[: len(mid) // 2], mid[len(mid) // 2:])
        for name, part in (("early-htc", halves[0]), ("late-htc", halves[1])):
            if part:
                b = sum(1 for c in part if abs(c[4] - c[6]) < abs(c[5] - c[6]))
                print(f"  {name:>10}: n={len(part):5d}  pm_better={_pct(b, len(part))}")
        for city in sorted({c[0] for c in outer}):
            part = [c for c in outer if c[0] == city]
            b = sum(1 for c in part if abs(c[4] - c[6]) < abs(c[5] - c[6]))
            print(f"  {city:>10}: n={len(part):5d}  pm_better={_pct(b, len(part))}")
    else:
        print("  no outer-band cycles.")

    # --- verdict --------------------------------------------------------------------------
    print("\n== verdict ==")
    if pm_err > 2.0 * k_err:
        print("  KILL — Polymarket is more than 2x less accurate than Kalshi (P1 kill criterion).")
    elif p2_kill:
        print("  KILL — both outer divergence bands at or below 50%: when Polymarket disagrees it"
              " is the one that is wrong. Same signature the NWS forecast produced. Strip"
              " pm_divergence from the DSL rather than leave a metric that predicts nothing.")
    elif p2 and p1 and p3:
        print("  PROMOTE — P1/P2/P3 pass. Build the grid-free pm_mean_gap_f metric + the"
              " Polymarket backtest dataset; hand off to kalshi-strategy.")
    else:
        print(f"  HOLD — P0={'PASS' if p0 else 'FAIL'} P1={'PASS' if p1 else 'FAIL'}"
              f" P2={'PASS' if p2 else 'FAIL'} P3={'PASS' if p3 else 'FAIL'}."
              " No kill criterion tripped; see the thin-band flags above.")
    print("  (P0 is a data-integrity check, not an edge claim: it says whether the 9.38F in"
          " weather_validation was the single-bucket artifact. Fix + re-materialize regardless.)")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    max_hours, gap_s = 24.0, DEFAULT_BATCH_GAP_S
    if "--max-hours" in argv:
        i = argv.index("--max-hours")
        if i + 1 < len(argv):
            max_hours = float(argv[i + 1])
    if "--batch-gap-s" in argv:
        i = argv.index("--batch-gap-s")
        if i + 1 < len(argv):
            gap_s = float(argv[i + 1])

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            report(cur, max_hours=max_hours, gap_s=gap_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
