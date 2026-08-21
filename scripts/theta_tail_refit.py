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
import json
import math
import os
import pathlib
import sys
import time
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

INCUMBENT_TRAIL_DAYS = 5.0   # theta_trail_days — the incumbent is scored AS IT RUNS

# A configuration must power at least this share of TRAIN quotes to be eligible for freezing.
# Without it the sweep rewards configurations that power almost nothing: an unnormalised
# deviance falls simply because there is less data, and the quotes a thin configuration DOES
# power are the ones carrying the most tail evidence — a biased subsample, not a better fit.
MIN_POWERED_COVERAGE = 0.90

# The scoring population is defined by the MARKET, never by a model's own output. Scoring each
# configuration on its self-defined `p_new < 0.02` set let quotes cross the objective boundary
# between configurations, so the numbers being compared described different populations. The
# common set is every eligible quote whose yes mid is at or below this — theta's own band ceiling,
# fixed in advance and independent of any model.
COMMON_POPULATION_MAX_MID = 20.0
H_BUCKETS = (10, 15, 20, 25, 30, 35)
PRODUCTS = ("BTC", "ETH")
COINBASE = "https://api.exchange.coinbase.com"
COINBASE_PRODUCT = {"BTC": "BTC-USD", "ETH": "ETH-USD"}


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
    """Spliced fits keyed by (product, hour, horizon), each built ONLY from blocks whose window
    closed before that hour started.

    Two corrections over the first draft, both load-bearing:

    * the sample is NON-OVERLAPPING h-minute blocks (step h), not one return per minute. An
      overlapping sample turns a single shock into ~h neighbouring extremes and biases the
      fitted shape, scale and exceedance count all at once.
    * the window is `fit_days`, an explicit pre-registered parameter, NOT the incumbent's 5-day
      model window. The first draft retained 90 days and still fitted on 5, which made every
      claim about what retention bought false.

    Refitting hourly rather than per quote approximates a window that moves by one block per
    hour; refitting per quote would be ~100k fits and would not move a reported digit.
    """

    # Block samples depend only on (product, hour, horizon, fit_days) — not on tail_q — so they
    # are shared across the sweep's FitCache instances.
    _BLOCKS: dict[tuple[str, int, int, float], list[float]] = {}

    def __init__(self, spot: dict[str, dict[int, float]], tail_q: float, fit_days: float):
        self.spot = spot
        self.tail_q = tail_q
        self.fit_days = float(fit_days)
        self._cache: dict[tuple[str, int, int], object] = {}
        self.misses = 0

    def _blocks(self, product: str, as_of: int, h_min: int) -> list[float]:
        """Non-overlapping h-minute log returns ending at or before `as_of`, in TIME ORDER.

        Mirrors `SpotModel.block_returns`; duplicated here rather than imported because the ops
        runner has no SQLAlchemy and the package import would pull it in. `tests/
        test_theta_tailmodel.py::TestRefitHarness` pins that the two agree.
        """
        key = (product, as_of, h_min, self.fit_days)
        got = FitCache._BLOCKS.get(key)
        if got is not None:
            return got
        closes = self.spot.get(product) or {}
        h = h_min * 60
        lo = as_of - int(self.fit_days * 86400)
        out: list[float] = []
        t = as_of - h
        while t >= lo:
            a, b = closes.get(t), closes.get(t + h)
            if a and b and a > 0 and b > 0:
                out.append(math.log(b / a))
            t -= h
        out.reverse()
        FitCache._BLOCKS[key] = out
        return out

    def get(self, product: str, at: dt.datetime, h_min: int):
        hour = int(at.replace(minute=0, second=0, microsecond=0).timestamp())
        key = (product, hour, h_min)
        if key in self._cache:
            return self._cache[key]
        model = tm.build(self._blocks(product, hour, h_min), h_min,
                         tail_q=self.tail_q, fit_days=self.fit_days)
        if model is None:
            self.misses += 1
        self._cache[key] = model
        return model

    def empirical_p(self, product: str, at: dt.datetime, h_min: int, spot: float,
                    strike_type: str, floor, cap, vol_mult: float) -> float | None:
        """The INCUMBENT's answer, over its OWN 5-day OVERLAPPING window.

        Deliberately not the block sample: the incumbent is being scored as it actually runs,
        not as a version of it that adopted the replacement's sampling. `SpotModel.
        prob_from_returns` inlined, since the ops runner cannot import the package.
        """
        hour = int(at.replace(minute=0, second=0, microsecond=0).timestamp())
        closes = self.spot.get(product) or {}
        h = h_min * 60
        lo = hour - int(INCUMBENT_TRAIL_DAYS * 86400)
        rets = []
        for t in range(lo, hour - h + 60, 60):
            a, b = closes.get(t), closes.get(t + h)
            if a and b and a > 0 and b > 0:
                rets.append(math.log(b / a))
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
                    "blocks": model.n,
                    "upper_xi": model.upper.xi, "lower_xi": model.lower.xi,
                    "upper_clusters": model.upper.n_clusters,
                    "lower_clusters": model.lower.n_clusters,
                    "active_xi": model.active_xi(r["strike_type"]),
                    "active_clusters": model.active_clusters(r["strike_type"]),
                    # Power is judged on the tail THIS strike prices off, not on both.
                    "powered": model.powered_for(r["strike_type"]),
                    "underpowered": not model.powered_for(r["strike_type"])})
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
    if not rows:
        print("  no quotes scored — nothing to report")
        return
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


def log_loss(p: float, outcome: bool, eps: float = 1e-9) -> float:
    """Bernoulli log loss for ONE prediction. Lower is better; a proper scoring rule.

    Proper means it is minimised in expectation only by the true probability, so a configuration
    cannot win by being systematically over- or under-confident — which an aggregate count
    statistic can be gamed by.
    """
    q = min(1.0 - eps, max(eps, p))
    return -(math.log(q) if outcome else math.log(1.0 - q))


def brier(p: float, outcome: bool) -> float:
    """Squared error of one probabilistic prediction. Also proper; reported beside log loss
    because it is bounded and therefore less dominated by a single confident miss."""
    return (p - (1.0 if outcome else 0.0)) ** 2


def score_population(rows: list[dict], key: str) -> dict:
    """Mean log loss and Brier over a COMMON population — the same quotes for every
    configuration, selected on the market's own price rather than on any model's output.

    Two earlier statistics failed here and are worth naming. `|log(o/e)|` on a deep bucket was
    undefined at zero observed. Aggregate Poisson deviance replaced it but was still computed on
    a set each configuration defined for itself (`p_new < 0.02`), so quotes crossed the boundary
    between configurations and the numbers described different populations. Both were also
    aggregate counts rather than per-prediction scores, which cannot distinguish a model that is
    right about individual markets from one whose errors happen to cancel.
    """
    if not rows:
        return {"n": 0}
    ll = sum(log_loss(r[key], r["yes_resolved"]) for r in rows) / len(rows)
    br = sum(brier(r[key], r["yes_resolved"]) for r in rows) / len(rows)
    return {"n": len(rows), "log_loss": ll, "brier": br}


def sweep(rows_by_cfg: dict[tuple[float, float], list[dict]], train_end: str
          ) -> tuple[float, float] | None:
    """Score every (fit_days, tail_q) on TRAIN, pick ONE, and report it. TEST is not read here.

    Two rules make the comparison mean something:

    * **a common population.** Every configuration is scored on the SAME quotes, selected by the
      market's own price (`mid <= COMMON_POPULATION_MAX_MID`) and never by a model's output. An
      earlier version let each configuration define its own deep set from `p_new < 0.02`, so
      quotes crossed the boundary between configurations and the numbers compared described
      different populations.
    * **a proper scoring rule.** Mean Bernoulli log loss per prediction, with Brier beside it.
      Proper means only the true probability minimises it, so a configuration cannot win by being
      systematically over-confident — which an aggregate count statistic can be.

    A configuration must also power at least `MIN_POWERED_COVERAGE` of TRAIN quotes: one that
    powers a thin subset is answering a different question on the quotes that happened to carry
    the most tail evidence.

    Returns the winning configuration so `main` can score it on the holdout exactly once.
    """
    head("3. CONFIGURATION SELECTION — scored on TRAIN only")
    cut = dt.datetime.fromisoformat(train_end).replace(tzinfo=dt.timezone.utc)
    print(f"  train: quotes before {train_end}. The holdout is not consulted in this section.")
    print(f"  common population: yes mid <= {COMMON_POPULATION_MAX_MID:.0f}c, identical for every")
    print(f"  configuration. Eligibility: powers >= {MIN_POWERED_COVERAGE:.0%} of TRAIN quotes.")
    print("  Score: mean Bernoulli log loss per prediction (proper; lower is better).")
    print()
    print(f"  {'fit_days':>9} {'tail_q':>7} {'powered':>9} {'cover':>7} {'common n':>9} "
          f"{'log loss':>10} {'brier':>9} {'R':>7} {'':>4}")
    print("  " + "-" * 88)
    total_train = len({(r["ticker"], r["captured_at"]) for rows in rows_by_cfg.values()
                       for r in rows if r["captured_at"] < cut}) or 1
    best: tuple[float, float] | None = None
    best_score = float("inf")
    for cfg in sorted(rows_by_cfg):
        fit_days, q = cfg
        rs = [r for r in rows_by_cfg[cfg] if r["captured_at"] < cut and r["powered"]]
        cover = len(rs) / total_train
        common = [r for r in rs if (r["mid"] or 999) <= COMMON_POPULATION_MAX_MID]
        if not common:
            print(f"  {fit_days:>9.0f} {q:>7.2f} {len(rs):>9} {cover:>6.1%}  "
                  "no quotes in the common population")
            continue
        sc = score_population(common, "p_new")
        exp = sum(r["p_new"] for r in common)
        obs = sum(1 for r in common if r["yes_resolved"])
        r_all = obs / exp if exp > 0 else float("nan")
        eligible = cover >= MIN_POWERED_COVERAGE
        mark = "" if eligible else "  (coverage too low — not a candidate)"
        print(f"  {fit_days:>9.0f} {q:>7.2f} {len(rs):>9} {cover:>6.1%} {sc['n']:>9} "
              f"{sc['log_loss']:>10.5f} {sc['brier']:>9.5f} {r_all:>7.2f}{mark}")
        if eligible and sc["log_loss"] < best_score:
            best_score, best = sc["log_loss"], cfg
    print()
    if best is None:
        print("  NO configuration produced a powered, scorable TRAIN fit. Nothing is frozen and")
        print("  no holdout score may be reported — see the blocker in the summary.")
        return None
    print(f"  FROZEN on TRAIN: fit_days={best[0]:.0f}, tail_q={best[1]:.2f} "
          f"(mean log loss = {best_score:.5f})")
    return best


def test_once(rows: list[dict], train_end: str, cfg: tuple[float, float]) -> None:
    """The frozen configuration scored on the held-back period.

    Labelled HISTORICAL VALIDATION rather than a pristine holdout, deliberately. Run `refit-7`
    exposed results from this same August period BEFORE the selection statistic and coverage rule
    were changed for `refit-8`, so the window has informed the scoring design and cannot also
    certify it. A genuine one-look holdout has to be a period that comes AFTER the specification
    is frozen; see the reservation in the write-up.
    """
    head("4. HISTORICAL VALIDATION — frozen configuration on the held-back period")
    print("  NOT a pristine holdout: this period was seen before the scoring rule was fixed.")
    cut = dt.datetime.fromisoformat(train_end).replace(tzinfo=dt.timezone.utc)
    rs = [r for r in rows if r["captured_at"] >= cut and r["powered"]]
    print(f"  frozen: fit_days={cfg[0]:.0f}, tail_q={cfg[1]:.2f}   "
          f"test quotes with a powered fit: {len(rs)}")
    if not rs:
        print("  no powered fits in the TEST period — no out-of-sample score exists.")
        return
    print()
    calib_table("SPLICED, out of sample:", calib(rs, "p_new"), "modeled P bucket")
    print()
    calib_table("INCUMBENT, same quotes:", calib(rs, "p_old"), "modeled P bucket")


def selection(rows: list[dict]) -> None:
    head("5. SELECTION — the diagnosis's split, recomputed under each model's own excess")
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
    head("6. FIT HEALTH — what each tail was actually fitted on")
    if not rows:
        return

    def pct(vals, q):
        v = sorted(x for x in vals if x is not None)
        return v[min(len(v) - 1, int(q * (len(v) - 1)))] if v else float("nan")

    print(f"  {'quantity':<34} {'p10':>10} {'p50':>10} {'p90':>10}")
    print("  " + "-" * 68)
    for label, key in (("non-overlapping blocks", "blocks"),
                       ("upper-tail declustered exceedances", "upper_clusters"),
                       ("lower-tail declustered exceedances", "lower_clusters"),
                       ("upper xi", "upper_xi"), ("lower xi", "lower_xi")):
        vals = [r[key] for r in rows]
        fmt = "{:>10.3f}" if "xi" in key else "{:>10.0f}"
        print(f"  {label:<34} " + " ".join(fmt.format(pct(vals, q)) for q in (.1, .5, .9)))
    powered = sum(1 for r in rows if r["powered"])
    print()
    print(f"  quotes whose ACTIVE tail is powered: {powered}/{len(rows)} "
          f"({powered / len(rows) * 100:.1f}%)")
    print(f"  bar: >= {tm.MIN_TAIL_EXCEEDANCES_FOR_POWER} declustered exceedances on the tail")
    print(f"  the strike prices off, run separation {tm.RUN_SEPARATION} blocks.")
    neg = [r for r in rows if (r["active_xi"] or 0) < 0]
    print(f"  fits with a BOUNDED active tail (xi < 0): {len(neg) / len(rows) * 100:.1f}% — "
          "extrapolated with max(xi, 0).")


# --- main --------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2026-07-11", help="first decision quote to score")
    ap.add_argument("--train-end", default="2026-08-11",
                    help="TRAIN/TEST boundary. Configuration is chosen on TRAIN and scored "
                         "once on TEST.")
    ap.add_argument("--fit-days", default="5,30,90",
                    help="candidate fit windows for the PAPER model, in days. Never the "
                         "incumbent's window, which stays at 5 whatever this says.")
    ap.add_argument("--tail-qs", default="0.90,0.95,0.99")
    ap.add_argument("--spot-source", default="coinbase",
                    choices=("coinbase", "candles", "ladder"),
                    help="coinbase = true 1-minute closes fetched from the public endpoint, "
                         "deep enough for a 90-day fit window and writing nothing; "
                         "candles = crypto_spot_candles (retained window only); "
                         "ladder = ~5-minute reconstruction, too sparse to fit blocks on")
    ap.add_argument("--vol-mult", type=float, default=1.0,
                    help="incumbent's vol_mult (1.0 = base model, 2.0 = theta4's)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    qs = [float(x) for x in args.tail_qs.split(",") if x.strip()]
    fds = [float(x) for x in args.fit_days.split(",") if x.strip()]

    # Fetch spot back far enough for the LONGEST candidate window before the first quote.
    spot_since = (dt.datetime.fromisoformat(args.since).replace(tzinfo=dt.timezone.utc)
                  - dt.timedelta(days=max(fds) + 1)).date().isoformat()

    # The database work is done and the connection CLOSED before any Coinbase fetch. Fetching
    # ~1,000 chunks inside an open transaction trips `idle_in_transaction_session_timeout` and
    # kills the run at the very end, after all the work.
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            quotes = load_quotes(cur, args.since, 35.0)
            spot = (None if args.spot_source == "coinbase"
                    else load_spot(cur, spot_since, args.spot_source))
    if spot is None:
        print(f"fetching 1-minute closes from Coinbase since {spot_since} "
              "(no database connection held)...")
        spot = load_spot_coinbase(spot_since)
    print(f"spot source: {args.spot_source}   window from {spot_since}   minutes: "
          + ", ".join(f"{p}={len(spot.get(p, {}))}" for p in PRODUCTS))
    print(f"ladder quotes with a derivable outcome and mid <= 40c: {len(quotes)}")
    print(f"incumbent window fixed at {INCUMBENT_TRAIL_DAYS:.0f}d; paper fit windows: "
          + ", ".join(f"{d:.0f}d" for d in fds))

    by_cfg: dict[tuple[float, float], list[dict]] = {}
    for fd in fds:
        for q in qs:
            cache = FitCache(spot, q, fd)
            scored = score(quotes, cache, args.vol_mult)
            by_cfg[(fd, q)] = scored
            powered = sum(1 for r in scored if r["powered"])
            print(f"  fit_days={fd:>4.0f} tail_q={q:.2f}: scored {len(scored):>6}  "
                  f"powered {powered:>6}  ({cache.misses} horizons unfittable)")

    # Degeneracy is a property of the model form, not of the configuration, so it is reported
    # on the largest scored set rather than waiting for a frozen config.
    widest = max(by_cfg.values(), key=len)
    if not widest:
        print()
        print("NO QUOTE COULD BE SCORED. Every fit window came back empty, which means the spot "
              "series does not cover the decision timestamps — almost always a truncated "
              "Coinbase fetch (see any 'INCOMPLETE' line above). Nothing below would be "
              "meaningful, so nothing is reported.", file=sys.stderr)
        return 1
    degeneracy(widest)

    frozen = sweep(by_cfg, args.train_end)
    if frozen is None:
        head("4. OUT-OF-SAMPLE SCORE — withheld")
        print("  No configuration was frozen on TRAIN, so there is nothing to score on TEST.")
        print("  Reporting a TEST number here would be choosing on the test set.")
    else:
        test_once(by_cfg[frozen], args.train_end, frozen)

    primary = by_cfg[frozen] if frozen else widest
    powered = [r for r in primary if r["powered"]]
    selection(powered if powered else primary)
    fit_health(primary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
