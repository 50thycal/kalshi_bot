"""Are the outcome labels under theta's calibration work good enough to calibrate against?

THE PROBLEM. Every calibration number in the theta remediation is computed against a DERIVED
outcome: the last ladder snapshot's spot before close, compared to the strike. That is not
Kalshi's settlement print. Kalshi settles crypto ladders off its own index at the close, and
the last snapshot this bot recorded is up to three minutes earlier. The earlier audit put
derived-vs-recorded agreement at 96.9% against its own 97% bar — a failing label quality that
the calibration work then leaned on anyway.

WHAT THIS DOES.

  1  GROUND TRUTH     what recorded Kalshi settlement actually exists for these markets, from
                      every table that could hold one, rather than assuming none does
  2  RESIDUAL SCALE   how far spot typically travels between the last snapshot and the close.
                      This is the size of the proxy's error, measured from the spot series
                      itself and therefore independent of any label
  3  EXCLUSION        a near-strike rule fixed BEFORE the agreement is recomputed: a market
                      whose strike sits within K residual sigmas of the final observed spot is
                      ambiguous by construction, because the unobserved residual move is large
                      enough to have carried it across. Chosen from the measurement geometry,
                      not from which side of the bar the answer lands on
  4  AGREEMENT        derived vs recorded, before and after exclusion, with event-clustered
                      uncertainty — markets on one ladder share one settlement print
  5  VERDICT          against BOTH preregistered bars: agreement and retained coverage

WHAT IT DOES NOT DO. It does not choose K to pass. K, the agreement bar and the coverage bar
are constants at the top of this file, fixed in advance; the run reports where the data falls.
If the bars are not met the correct output is BLOCKED_DATA, and the calibration downstream is
not validated.

Read-only; stdlib + psycopg only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cluster_stats as cs  # noqa: E402
from mmsell_market_types import RO_OPTIONS, _to_libpq_url  # noqa: E402
from theta_tail_diagnosis import _yes_resolved  # noqa: E402

# --- PREREGISTERED, fixed before the measurement -----------------------------------------------

# Derived labels must agree with recorded Kalshi settlement at least this often on the overlap.
AGREEMENT_BAR = 0.97

# ...and the near-strike exclusion must leave at least this share of the population, or the
# rule has bought its agreement by discarding the markets the strategy actually trades.
COVERAGE_BAR = 0.90

# A market is ambiguous when its strike lies within this many residual-move sigmas of the last
# observed spot. Two sigma is a ~95% band on the unobserved move: inside it, the proxy simply
# cannot say which side of the strike the settlement print landed.
NEAR_STRIKE_K = 2.0

# Lags (minutes) at which the residual move scale is measured. The settlement proxy is taken
# from a snapshot at most 3 minutes before close, so 1-5 covers every case with headroom.
RESIDUAL_LAGS = (1, 2, 3, 4, 5)

# A residual-scale cell built on fewer than this many observed (t, t-lag) pairs is not an
# estimate. Markets whose lag falls in such a cell are UNCLASSIFIABLE, not automatically
# clean: the rule refuses to judge them rather than passing them through.
MIN_SCALE_PAIRS = 500

PRODUCTS = ("BTC", "ETH")
COINBASE = "https://api.exchange.coinbase.com"
COINBASE_PRODUCT = {"BTC": "BTC-USD", "ETH": "ETH-USD"}


def product_of(series: str | None) -> str:
    return "ETH" if (series or "").upper().startswith("KXETH") else "BTC"


def load_spot_coinbase(since: str, until: str | None = None) -> dict[str, dict[int, float]]:
    """1-minute closes straight from Coinbase's PUBLIC candles endpoint.

    Exists because the database cannot answer the question. `crypto_spot_candles` was pruned at
    6 days, and the ladder-snapshot reconstruction is ~5-minute sampled — too sparse to build
    non-overlapping blocks from. Coinbase serves 1-minute candles at least 365 days back (probe
    `cb-probe-5`, 2026-08-21), so the fit window can be filled from history rather than waited
    for, and this analysis writes NOTHING: it is a read against a public endpoint.

    stdlib urllib, not httpx — the ops runner installs psycopg alone for `script` requests.
    """
    import urllib.error
    import urllib.request

    start = dt.datetime.fromisoformat(since).replace(tzinfo=dt.timezone.utc)
    end = (dt.datetime.fromisoformat(until).replace(tzinfo=dt.timezone.utc)
           if until else dt.datetime.now(dt.timezone.utc))
    out: dict[str, dict[int, float]] = {p: {} for p in PRODUCTS}
    for key, product in COINBASE_PRODUCT.items():
        cursor = int(start.timestamp())
        stop = int(end.timestamp())
        while cursor < stop:
            chunk_end = min(stop, cursor + 300 * 60)
            url = (f"{COINBASE}/products/{product}/candles?granularity=60"
                   f"&start={dt.datetime.fromtimestamp(cursor, tz=dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
                   f"&end={dt.datetime.fromtimestamp(chunk_end, tz=dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}")
            req = urllib.request.Request(url, headers={
                "User-Agent": "kalshi-bot theta refit (research)", "Accept": "application/json"})
            rows = None
            # Coinbase's public tier throttles around 10 req/s and a 90-day window is ~430
            # requests per product. An unthrottled loop earns a 429 partway through, which
            # truncates the NEWEST data — precisely the minutes the quotes need — and silently
            # scores zero. Pace, and retry the throttle rather than treating it as the end of
            # history.
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                        rows = json.loads(resp.read().decode())
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code == 429:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    print(f"  coinbase fetch stopped for {product}: HTTP {exc.code}")
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"  coinbase fetch stopped for {product}: {str(exc)[:80]}")
                    break
            if rows is None:
                print(f"  coinbase fetch INCOMPLETE for {product} at "
                      f"{dt.datetime.fromtimestamp(cursor, tz=dt.timezone.utc):%Y-%m-%d}; "
                      "results below are not usable")
                break
            for r in rows or []:
                out[key][int(r[0])] = float(r[4])
            cursor = chunk_end
            time.sleep(0.15)
    return out



# --- data --------------------------------------------------------------------------------------

def load_spot_minutes(cur, since: str) -> dict[str, dict[int, float]]:
    """Per-product minute-bar spot, averaged over whatever snapshots landed in that minute.
    The same reconstruction the refit uses, so the residual scale describes the same series
    the settlement proxy is drawn from."""
    cur.execute(
        "SELECT CASE WHEN series LIKE 'KXETH%%' THEN 'ETH' ELSE 'BTC' END AS product,"
        "       date_trunc('minute', captured_at) AS m, avg(spot) AS v"
        "  FROM crypto_ladder_snapshots"
        " WHERE captured_at >= %s AND spot IS NOT NULL"
        " GROUP BY 1, 2", (since,))
    out: dict[str, dict[int, float]] = {p: {} for p in PRODUCTS}
    for p, m, v in cur.fetchall():
        out.setdefault(p, {})[int(m.timestamp())] = float(v)
    return out


def residual_scale(spot: dict[str, dict[int, float]]
                   ) -> dict[str, dict[int, tuple[float, int]]]:
    """(RMS absolute spot move, pair count) over each lag, per product, in dollars.

    This is the magnitude of the error the settlement proxy carries: the snapshot is taken
    `lag` minutes before the close, and whatever spot did in those minutes is unobserved. It
    comes from the spot series alone — no label, no outcome, nothing that could tune it.

    The pair count is returned with the estimate because the first version of this ran on the
    ladder-snapshot reconstruction, which is ~5-minute sampled and only near settlement: for
    ETH almost no (t, t-lag) pair existed and it reported a 4-minute RMS move of $0.20. A
    scale estimate is only as good as the density of the series under it, so the density is
    reported beside it and a thin cell is refused rather than used.
    """
    out: dict[str, dict[int, tuple[float, int]]] = {}
    for product, series in spot.items():
        per_lag: dict[int, tuple[float, int]] = {}
        for lag in RESIDUAL_LAGS:
            sq, n = 0.0, 0
            for t, v in series.items():
                prev = series.get(t - 60 * lag)
                if prev is None:
                    continue
                sq += (v - prev) ** 2
                n += 1
            per_lag[lag] = (math.sqrt(sq / n) if n else float("nan"), n)
        out[product] = per_lag
    return out


def load_recorded_truth(cur, since: str) -> dict[str, bool]:
    """Recorded Kalshi settlement per market ticker, from every paper book that traded one.

    Deliberately NOT restricted to theta. `resolved_value` is the settlement value for that
    trade's own side, so any book that held the market records the same fact, and widening the
    source both enlarges the audit and reduces its dependence on theta's own selection.
    """
    cur.execute(
        "SELECT market_ticker, side, resolved_value FROM paper_trades"
        " WHERE status = 'settled' AND resolved_value IS NOT NULL AND created_at >= %s"
        "   AND (market_ticker LIKE 'KXBTC%%' OR market_ticker LIKE 'KXETH%%')", (since,))
    truth: dict[str, bool] = {}
    for tkr, side, rv in cur.fetchall():
        yes_res = (int(rv) == 100) if (side or "").lower() == "yes" else (int(rv) == 0)
        truth[tkr] = yes_res
    return truth


def other_truth_sources(cur) -> list[tuple[str, int, str]]:
    """Every other table that could hold a real Kalshi result for these markets, counted rather
    than assumed empty. `markets`/`market_snapshots` carry no crypto ladder rows at all — the
    ladder collector writes only to `crypto_ladder_snapshots` — and this proves it per run.

    Existence is checked in the catalog FIRST. A failed statement aborts the whole psycopg
    transaction, so a missing table probed optimistically takes down every query after it.
    """
    candidates = (
        ("markets", "ticker", "no result column; ladder tickers not collected here"),
        ("market_snapshots", "market_ticker", "terminal price would be a settlement print"),
        ("backfill_regime_markets", "ticker", "has a result column"),
        ("backfill_weather_markets", "ticker", "has a result column"),
    )
    cur.execute(
        "SELECT table_name, column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = ANY(%s)",
        ([t for t, _c, _n in candidates],))
    present = {(t, c) for t, c in cur.fetchall()}
    out: list[tuple[str, int, str]] = []
    for table, col, note in candidates:
        if (table, col) not in present:
            out.append((table, -1, "table/column absent"))
            continue
        cur.execute(
            f"SELECT count(*) FROM {table}"                       # noqa: S608 — fixed literals
            f" WHERE {col} LIKE 'KXBTC%' OR {col} LIKE 'KXETH%'")
        out.append((table, int(cur.fetchone()[0]), note))
    return out


def load_universe(cur, since: str, tte_max: float) -> list[dict]:
    """One row per ladder market, with the settlement proxy AND the residual window length.

    `final_mtc` is what the earlier construction threw away: the settlement proxy is not taken
    at the close, it is taken `final_mtc` minutes before it, and that number sets how wrong the
    proxy can be.
    """
    cur.execute(
        "WITH fin AS ("
        "  SELECT DISTINCT ON (event_ticker) event_ticker, spot AS final_spot,"
        "         minutes_to_close AS final_mtc"
        "    FROM crypto_ladder_snapshots"
        "   WHERE captured_at >= %s AND spot IS NOT NULL"
        "     AND minutes_to_close IS NOT NULL AND minutes_to_close <= 3"
        "   ORDER BY event_ticker, minutes_to_close ASC, captured_at DESC"
        "), q AS ("
        "  SELECT DISTINCT ON (s.market_ticker) s.market_ticker, s.event_ticker, s.series,"
        "         s.strike_type, s.floor_strike, s.cap_strike, s.mid_cents, s.minutes_to_close"
        "    FROM crypto_ladder_snapshots s"
        "   WHERE s.captured_at >= %s AND s.spot IS NOT NULL"
        "     AND s.minutes_to_close IS NOT NULL"
        "     AND s.minutes_to_close <= %s AND s.minutes_to_close >= 10"
        "     AND s.mid_cents IS NOT NULL AND s.mid_cents <= 40"
        "   ORDER BY s.market_ticker, s.minutes_to_close DESC"
        ") SELECT q.*, fin.final_spot, fin.final_mtc FROM q JOIN fin USING (event_ticker)",
        (since, since, tte_max))
    rows: list[dict] = []
    for (tkr, ev, series, st, fs, cs_, mid, _mtc, final, final_mtc) in cur.fetchall():
        yr = _yes_resolved(st, fs, cs_, float(final))
        if yr is None:
            continue
        rows.append({
            "ticker": tkr, "event": ev, "series": series, "product": product_of(series),
            "strike_type": (st or "").lower(), "floor": fs, "cap": cs_,
            "mid": float(mid), "final": float(final),
            "final_mtc": float(final_mtc) if final_mtc is not None else 3.0,
            "derived": yr,
        })
    return rows


# --- the rule ------------------------------------------------------------------------------------

def strike_distance(r: dict) -> float | None:
    """Dollars between the settlement proxy and the strike it is being compared against.
    For a BETWEEN market the binding strike is whichever edge is nearer."""
    st, final = r["strike_type"], r["final"]
    if st == "greater" and r["floor"] is not None:
        return abs(final - float(r["floor"]))
    if st == "less" and r["cap"] is not None:
        return abs(final - float(r["cap"]))
    if st == "between" and r["floor"] is not None and r["cap"] is not None:
        return min(abs(final - float(r["floor"])), abs(final - float(r["cap"])))
    return None


def sigma_for(r: dict, scale: dict[str, dict[int, tuple[float, int]]]) -> float:
    """The residual-move sigma for this market's own unobserved window, or NaN if the estimate
    rests on too few observed pairs to mean anything."""
    lag = max(RESIDUAL_LAGS[0], min(RESIDUAL_LAGS[-1], int(round(r["final_mtc"])) or 1))
    sigma, n = scale.get(r["product"], {}).get(lag, (float("nan"), 0))
    return sigma if n >= MIN_SCALE_PAIRS else float("nan")


def ambiguous(r: dict, scale: dict[str, dict[int, float]]) -> bool | None:
    """True when the strike sits inside the band the unobserved residual move can cross."""
    d, s = strike_distance(r), sigma_for(r, scale)
    if d is None or not math.isfinite(s) or s <= 0:
        return None
    return d < NEAR_STRIKE_K * s


# --- report ---------------------------------------------------------------------------------------

def head(t: str) -> None:
    print()
    print("=" * 96)
    print(t)
    print("=" * 96)


def report(rows: list[dict], truth: dict[str, bool], scale: dict[str, dict[int, float]],
           sources: list[tuple[str, int, str]], seed: int) -> bool:
    head("1. RECORDED GROUND TRUTH — what real Kalshi settlement exists for these markets")
    print(f"  {'source':<28} {'ladder rows':>12}  note")
    print("  " + "-" * 88)
    for table, n, note in sources:
        shown = "unavailable" if n < 0 else f"{n:,}"
        print(f"  {table:<28} {shown:>12}  {note}")
    print(f"  {'paper_trades.resolved_value':<28} {len(truth):>12,}  actual settlement, "
          "traded markets only")
    print()
    overlap = [r for r in rows if r["ticker"] in truth]
    cov = len(overlap) / len(rows) if rows else 0.0
    print(f"  ladder markets in the scored universe: {len(rows):,}")
    print(f"  of those with a RECORDED settlement:   {len(overlap):,} ({cov:.2%})")
    print()
    print("  So recorded results cannot label the population: they exist only where a book")
    print("  traded, which is the selected subset, not the universe. The derived label stays")
    print("  in use and this run measures how far it can be trusted.")

    head("2. RESIDUAL MOVE SCALE — how wrong the settlement proxy can be")
    print("  The proxy is the last snapshot at or before 3 minutes to close. RMS |move| over")
    print("  the unobserved remainder, from the spot series alone:")
    print()
    print(f"  {'product':<10}" + "".join(f"{f'{lag}m':>18}" for lag in RESIDUAL_LAGS))
    print("  " + "-" * 100)
    for p in PRODUCTS:
        vals = scale.get(p, {})
        cells = []
        for lag in RESIDUAL_LAGS:
            sigma, n = vals.get(lag, (float("nan"), 0))
            cells.append(f"{sigma:>10,.1f} n={n:<5,}" if n >= MIN_SCALE_PAIRS
                         else f"{'thin':>10} n={n:<5,}")
        print(f"  {p:<10}" + "".join(f"{c:>18}" for c in cells))
    print(f"  cells built on fewer than {MIN_SCALE_PAIRS:,} observed pairs are marked thin and")
    print("  are not used; markets landing in one are unclassifiable, not clean.")
    lags = defaultdict(int)
    for r in rows:
        lags[int(round(r["final_mtc"]))] += 1
    spread = ", ".join(f"{k}m:{v:,}" for k, v in sorted(lags.items()))
    print()
    print(f"  residual window actually used, by market count: {spread}")

    head("3. AGREEMENT — derived label vs recorded Kalshi settlement")
    if len(overlap) < 30:
        print(f"  only {len(overlap)} markets have both labels — too few to audit. BLOCKED_DATA.")
        return False
    for r in rows:
        r["amb"] = ambiguous(r, scale)
    for r in overlap:
        r["agree"] = 1.0 if r["derived"] == truth[r["ticker"]] else 0.0
    kept = [r for r in overlap if r["amb"] is False]
    # COVERAGE is a property of the POPULATION, not of the audit sample. The overlap is the
    # traded subset, and theta sells deep strikes, so measuring retention on it would answer
    # "how many of the markets a book already chose survive the rule" — not "how much of the
    # universe the rule discards", which is the question the bar exists to ask.
    kept_all = [r for r in rows if r["amb"] is False]
    dropped = [r for r in rows if r["amb"] is not False]
    prof = cs.cluster_profile(overlap, "event")
    print(f"  audit sample: {len(overlap):,} markets across {prof['clusters']:,} events "
          f"(mean {prof['mean_size']:.1f}/event, max {prof['max_size']}, "
          f"Kish effective events {prof['kish_effective_clusters']:.1f})")
    print("  Intervals are event-clustered percentile bootstrap at 99%: markets on one ladder")
    print("  share one settlement print and are not independent checks of the derivation.")
    print()
    print(f"  {'population':<34} {'n':>8} {'agreement':>11}   {'99% CI (event-clustered)':<26}")
    print("  " + "-" * 88)
    for label, rs in (("ALL overlapping markets", overlap),
                      (f"near-strike EXCLUDED (K={NEAR_STRIKE_K:g})", kept)):
        if not rs:
            print(f"  {label:<34} {0:>8}   (empty)")
            continue
        m = cs.mean_ci(rs, "event", lambda r: r["agree"], seed=seed)
        print(f"  {label:<34} {len(rs):>8} {m['mean']:>10.2%}   "
              f"{cs.fmt_ci(m['lo'], m['hi'], 4):<26}")
    retained = len(kept_all) / len(rows) if rows else 0.0
    audit_retained = len(kept) / len(overlap) if overlap else 0.0
    print()
    print(f"  on the POPULATION ({len(rows):,} markets) the rule discards {len(dropped):,} "
          f"({1 - retained:.2%}); retained coverage {retained:.2%}  <- the bar applies here")
    print(f"  on the audit overlap ({len(overlap):,} markets) it retains {audit_retained:.2%}, "
          "which is a different and easier question")

    head("4. DISAGREEMENTS — where the derived label goes wrong")
    print("  Distance from the settlement proxy to the binding strike, in residual sigmas. If")
    print("  the proxy is sound, disagreements concentrate near zero and vanish beyond ~2.")
    print()
    bands = ((0.0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 4.0), (4.0, float("inf")))
    print(f"  {'distance (sigma)':<20} {'n':>8} {'disagree':>10} {'rate':>9}")
    print("  " + "-" * 52)
    for lo, hi in bands:
        band = []
        for r in overlap:
            d, s = strike_distance(r), sigma_for(r, scale)
            if d is None or not math.isfinite(s) or s <= 0:
                continue
            if lo <= d / s < hi:
                band.append(r)
        if not band:
            continue
        bad = sum(1 for r in band if r["agree"] == 0.0)
        hi_s = "inf" if hi == float("inf") else f"{hi:g}"
        print(f"  {f'{lo:g} - {hi_s}':<20} {len(band):>8} {bad:>10} {bad / len(band):>8.2%}")

    head("5. VERDICT — against the bars fixed before this run")
    if not kept:
        print("  the exclusion rule leaves nothing. BLOCKED_DATA.")
        return False
    m = cs.mean_ci(kept, "event", lambda r: r["agree"], seed=seed)
    agree_ok = m["mean"] >= AGREEMENT_BAR
    cov_ok = retained >= COVERAGE_BAR
    print(f"  agreement after exclusion   {m['mean']:.2%}  bar {AGREEMENT_BAR:.0%}   "
          f"{'PASS' if agree_ok else 'FAIL'}")
    print(f"  retained coverage           {retained:.2%}  bar {COVERAGE_BAR:.0%}   "
          f"{'PASS' if cov_ok else 'FAIL'}")
    print()
    if agree_ok and cov_ok:
        print("  LABELS USABLE on the retained population. Every calibration number downstream")
        print("  must be computed on that same retained population, not on the full universe.")
    else:
        print("  BLOCKED_DATA. The outcome labels do not meet their own quality bar, so no")
        print("  calibration computed against them is validated — including any that already")
        print("  reports a verdict. This blocks the stage, it does not adjust the bar.")
    return agree_ok and cov_ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2026-07-11")
    ap.add_argument("--tte-max", type=float, default=35.0)
    ap.add_argument("--seed", type=int, default=cs.DEFAULT_SEED)
    ap.add_argument("--spot-source", default="coinbase", choices=("coinbase", "ladder"),
                    help="coinbase = true 1-minute closes, dense enough to measure a 1-5 "
                         "minute move scale; ladder = the ~5-minute snapshot reconstruction, "
                         "which is too sparse and is kept only to show why")
    args = ap.parse_args(argv)

    import psycopg

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("no DATABASE_URL_RO", file=sys.stderr)
        return 2
    # Database work finishes and the connection CLOSES before any Coinbase fetch: holding a
    # transaction open across hundreds of HTTP requests trips idle_in_transaction_session_timeout.
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            sources = other_truth_sources(cur)
            truth = load_recorded_truth(cur, args.since)
            rows = load_universe(cur, args.since, args.tte_max)
            spot = load_spot_minutes(cur, args.since) if args.spot_source == "ladder" else None
    if spot is None:
        print(f"fetching 1-minute closes from Coinbase since {args.since} "
              "(no database connection held)...")
        spot = load_spot_coinbase(args.since)
    print(f"settlement label audit   since={args.since}  tte_max={args.tte_max:g}  "
          f"K={NEAR_STRIKE_K:g}  seed={args.seed}  spot={args.spot_source}")
    print("commit: " + (os.environ.get("GITHUB_SHA") or "unknown (not run from CI)"))
    ok = report(rows, truth, residual_scale(spot), sources, args.seed)
    print()
    print(f"RESULT: {'labels usable on the retained population' if ok else 'BLOCKED_DATA'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
