"""MARKTANGLE-2 — conditional dependence alpha (docs/MARKTANGLE_2_SPEC.md).

Read-only, public Kalshi + Coinbase APIs, stdlib only. Places nothing, writes
nothing. Emits the whole research package to stdout as marked sections —
five documents and one CSV — because the ops channel persists stdout and
nothing else. `scripts/marktangle2_package.py` splits a result file into files.

THE QUESTION
------------
MARKTANGLE-1 asked "after a streak, is the opposite outcome due?" and reached
HOLD on thin holdouts, with one hard finding on the way: daily crypto threshold
families resolve near 50/50 marginally while repeating their previous outcome
~97% of the time. MARKTANGLE-2 asks the more general and more precise question:

  When a recurring Kalshi market exhibits measurable serial dependence —
  reversion OR continuation — does the market price already account for it,
  or can knowing the current state, its duration and the relevant structural
  variables generate executable positive expected value after fees?

Two tracks, graded independently, neither rescuing the other:

  TRACK A  cross-family conditional reversion in homogeneous fresh-event
           classes (sports totals, weather buckets), pooled with family effects
           because no single family is deep enough. Crypto is EXCLUDED here.
  TRACK B  crypto daily-threshold persistence as a state-duration process on a
           slow-moving underlying, with the distance to the strike (in
           realized-vol units, no lookahead) as the structural variable.

WHAT IS FROZEN HERE
-------------------
Every threshold, bucket edge, penalty and floor is a module constant, named,
and printed in the reproducibility block. None is an argument that could be
chosen after seeing output. The holdout is read exactly once, to grade.

  * no gambler's-fallacy assumption: every conditional probability is
    ESTIMATED from history, never assumed;
  * no Martingale: position size is 1 unit for the primary test, and the
    secondary sizing study is a function of estimated edge only — a test
    asserts that no trade's size depends on the previous trade's outcome;
  * prediction is not sufficient: every arm is graded on net P&L against the
    taker price it would actually have paid at T-60m, worst-case fees,
    modeled slippage and a liquidity screen;
  * MARKTANGLE-1 is untouched: this is a separate instrument with its own
    contract, and `scripts/marktangle_probe.py` is imported only for two
    shared utilities (the fee schedule and the Wilson bound).

Usage (ops channel):
  {"type":"script","name":"marktangle2_probe","id":"m2-run-1"}
  {"type":"script","name":"marktangle2_probe","args":["--max-fetch","3000"],"id":"m2-run-2"}
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import json
import math
import os
import re
import time
from collections import defaultdict

import marktangle_probe as m1  # taker_fee_c, wilson_lower, discover_series — shared, frozen
import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com"

# =============================================================================
# FROZEN CONSTANTS — the pre-registration in executable form. Changing any of
# these after a run is a new Version, not a retune.
# =============================================================================

#: Chronological split per CLASS: the first 70% of a class's prediction points by
#: decision time are TRAIN, the rest HOLDOUT. 70/30 rather than 60/20/20 because
#: the spec allows either, MARKTANGLE-1 used 70/30, and no validation search is
#: performed here — every model-selection degree of freedom is fixed below, so a
#: validation segment would have nothing to select.
TRAIN_FRAC = 0.70

#: Minutes before the NEXT market's close at which the decision is taken and the
#: quote is read. Same as MARKTANGLE-1.
DECISION_MIN = 60

#: A family needs this many usable resolutions to be analysable at all (§4.3).
MIN_FAMILY_N = 40

#: Track B: each outcome must occur at least this often in the family, or there
#: is no transition structure to estimate on one side.
MIN_STATE_N = 5

#: Track A streak-length axis: k = 1..5 individually, k >= 6 pooled. Beyond this
#: the fresh-event classes have no support, and printing empty rows invites
#: reading noise.
MAX_K_A = 6

#: Track B state-duration buckets (inclusive ranges). Persistence at 97% makes
#: runs of 30+ routine; these are the buckets the hazard is read on.
DURATION_BUCKETS_B: tuple[tuple[int, int], ...] = (
    (1, 1), (2, 2), (3, 3), (4, 5), (6, 9), (10, 19), (20, 10**9),
)

#: Track B normalized-distance bins for the descriptive table (z = ln(spot /
#: strike) / trailing realized daily vol, signed toward the current state).
Z_BINS: tuple[float, ...] = (-6.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 6.0)
#: |z| is capped here before it enters the logistic model.
Z_CAP = 6.0
#: Trailing window, in daily closes, for the realized vol that normalizes z.
VOL_WINDOW_DAYS = 20
#: Fewer daily returns than this and the vol is not estimated (z = None).
MIN_VOL_RETURNS = 10

#: Shrinkage toward the class rate, in pseudo-observations, for every
#: count-based estimate (A0, A1, A2, B1, B2). One number, everywhere.
SHRINK_M = 20.0

#: L2 penalty on the family effects of the hierarchical model (A3). A ridge on
#: the one-hot family effects is the fixed-variance random-effects estimator;
#: 1.0 is the pre-registered value and is not searched.
RIDGE_FAMILY = 1.0
#: A vanishing L2 on the slopes, for numerical stability under separation only.
RIDGE_SLOPE = 1e-3

#: Net edge bar, cents per contract, after fee AND slippage (§12: >= 3pp).
EDGE_BAR_C = 3.0
#: Modeled slippage per contract on top of the touch, cents. One contract at the
#: touch is realistic; the extra cent is the conservative allowance.
SLIPPAGE_C = 1.0
#: Liquidity screen: a quote wider than this at T-60m is not executable.
MAX_SPREAD_C = 10.0

#: Sample floors (§16).
FLOOR_TRAIN_POINTS = 500
FLOOR_HOLDOUT_TRADES = 100
#: If fewer than this fraction of holdout prediction points could be priced, the
#: execution reconstruction is inadequate and the verdict is HOLD (§18).
PRICE_COVERAGE_FLOOR = 0.50

#: Treatment must beat its mirror by at least this, cents per trade (§17.5).
MIRROR_DELTA_C = 3.0
#: Robustness (§17.7): net P&L must stay positive after removing the top 1% of
#: trades (rounded up) and after removing the single most profitable family.
TOP_TRADE_FRAC = 0.01

#: Secondary sizing study: quarter-Kelly, one unit per 2% bankroll fraction,
#: capped at 4 units. Reported, never gated (§13).
KELLY_FRACTION = 0.25
KELLY_UNIT_FRAC = 0.02
KELLY_CAP_UNITS = 4

#: Default hand-picked series, on top of the structural discovery: the families
#: MARKTANGLE-1 already showed have the right shape, so they are never lost to
#: an enumeration accident. Everything is still classified structurally.
DEFAULT_SERIES = (
    "KXBTCD,KXETHD,KXSOLD,KXXRPD,KXDOGED,"
    "KXHIGHNY,KXHIGHCHI,KXHIGHLAX,KXHIGHMIA,KXHIGHAUS,KXHIGHDEN,KXHIGHPHIL,"
    "KXHIGHSFO,KXHIGHDC,KXHIGHATL,KXHIGHSEA,KXHIGHDAL,KXHIGHHOU,KXHIGHLV,"
    "KXUSLTOTAL,KXLIGAMXSPREAD,KXMLSTOTAL,KXEPLTOTAL,KXNBATOTAL,KXWNBATOTAL,"
    "KXMLBTOTAL,KXNHLTOTAL,KXNFLTOTAL,KXNCAAFTOTAL"
)

COINBASE_PRODUCT = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "XRP": "XRP-USD",
    "DOGE": "DOGE-USD", "LTC": "LTC-USD", "BCH": "BCH-USD", "LINK": "LINK-USD",
    "AVAX": "AVAX-USD", "ADA": "ADA-USD", "DOT": "DOT-USD", "SUI": "SUI-USD",
}

# =============================================================================
# Structural family classifier (§4.2) — market STRUCTURE only, never behaviour.
# =============================================================================

CRYPTO_DAILY_RE = re.compile(r"^KX([A-Z]{2,5})D$")
WEATHER_RE = re.compile(r"^KX(HIGH|LOW)([A-Z]{2,6})$")
TOTAL_RE = re.compile(r"^KX([A-Z0-9]{2,12})TOTAL$")
SPREAD_RE = re.compile(r"^KX([A-Z0-9]{2,12})SPREAD$")

#: League prefix -> sport group. A totals market pools only with totals of the
#: same SPORT: a soccer 2.5-goal line and a basketball 220.5-point line are not
#: "equivalent construction" (§4.2) and are never one class.
SPORT_GROUPS: dict[str, tuple[str, ...]] = {
    "SOCCER": ("USL", "LIGAMX", "MLS", "EPL", "UCL", "UEL", "LALIGA", "SERIEA",
               "BUNDESLIGA", "LIGUE1", "NWSL", "EREDIVISIE", "PRIMEIRA", "FIFA",
               "COPA", "EURO", "WC", "CHAMPIONSHIP", "SPL", "BRASILEIRAO"),
    "BASKETBALL": ("NBA", "WNBA", "NCAAB", "NCAAMB", "NCAAWB", "EUROLEAGUE", "CBB"),
    "FOOTBALL": ("NFL", "NCAAF", "CFB", "UFL", "CFL"),
    "BASEBALL": ("MLB", "KBO", "NPB"),
    "HOCKEY": ("NHL", "KHL"),
}


def sport_group(league: str) -> str | None:
    for group, prefixes in SPORT_GROUPS.items():
        for p in prefixes:
            if league.startswith(p):
                return group
    return None


def classify_series(series: str) -> tuple[str, str] | None:
    """(track, class_stem) for a series ticker, or None when the series is not a
    structurally recognised recurring type. Weather classes are completed per
    family with the strike type (bucket vs threshold) in `classify_family`."""
    s = series.upper()
    m = CRYPTO_DAILY_RE.match(s)
    if m and m.group(1) in COINBASE_PRODUCT:
        return "B", f"CRYPTO_DAILY:{m.group(1)}"
    m = WEATHER_RE.match(s)
    if m:
        return "A", f"WEATHER_{m.group(1)}"
    m = TOTAL_RE.match(s)
    if m:
        g = sport_group(m.group(1))
        return ("A", f"{g}_TOTAL") if g else None
    m = SPREAD_RE.match(s)
    if m:
        g = sport_group(m.group(1))
        return ("A", f"{g}_SPREAD") if g else None
    return None


def family_key(market: dict) -> str:
    """`SERIES|SUFFIX` — one recurring binary question through time (rungs of a
    ladder are separate families; never pooled into one sequence)."""
    event = market["event"]
    ticker = market["ticker"]
    series = event.split("-", 1)[0]
    suffix = ticker[len(event) + 1:] if ticker.startswith(event + "-") else ""
    return f"{series}|{suffix}"


def _suffix_strike(suffix: str) -> float | None:
    m = re.match(r"^[TB]?(-?\d+(?:\.\d+)?)$", suffix or "")
    return float(m.group(1)) if m else None


def classify_family(key: str, rows: list[dict]) -> dict | None:
    """Complete the class for one family from its market structure.

    Returns {track, cls, strike, yes_is_above, threshold} or None (unclassified).
    Uses the MAJORITY strike_type across the family's markets; families are per
    suffix, so it is uniform in practice and the majority is a guard."""
    series, suffix = key.split("|", 1)
    base = classify_series(series)
    if base is None:
        return None
    track, stem = base
    types = defaultdict(int)
    for r in rows:
        types[(r.get("strike_type") or "").lower()] += 1
    stype = max(types.items(), key=lambda kv: (kv[1], kv[0]))[0] if types else ""
    strike = None
    for r in rows:
        v = r.get("floor_strike") if stype != "less" else r.get("cap_strike")
        if v is not None:
            strike = float(v)
            break
    if strike is None:
        strike = _suffix_strike(suffix)
    threshold = stype in ("greater", "less") or (not stype and (suffix or "").startswith("T"))
    if stem.startswith("WEATHER_"):
        cls = stem + ("_THRESHOLD" if threshold else "_BUCKET")
    else:
        cls = stem
    if track == "B" and not threshold:
        return None  # Track B is threshold families only (§7); buckets are not a level crossing
    return {"track": track, "cls": cls, "strike": strike,
            "yes_is_above": stype != "less", "threshold": threshold}


# =============================================================================
# Data acquisition
# =============================================================================

def _iso_to_unix(iso: str) -> int:
    try:
        return calendar.timegm(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def _iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_settled(series: str, pages: int, min_vol: float) -> list[dict]:
    """Settled binaries of one series with the structural fields kept."""
    out: list[dict] = []
    cursor = ""
    for _ in range(max(1, pages)):
        page = xl._get(f"{KALSHI}/markets?status=settled&limit=1000&cursor={cursor}"
                       f"&series_ticker={series}")
        mkts = (page or {}).get("markets") or []
        for m in mkts:
            res = (m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            vol = xl._num(m.get("volume_fp")) or xl._num(m.get("volume"))
            if vol < min_vol:
                continue
            close = _iso_to_unix(m.get("close_time"))
            ticker = m.get("ticker") or ""
            event = m.get("event_ticker") or ""
            if not close or not ticker or not event:
                continue
            out.append({
                "ticker": ticker, "event": event, "close": close, "result": res,
                "vol": vol, "strike_type": m.get("strike_type"),
                "floor_strike": m.get("floor_strike"), "cap_strike": m.get("cap_strike"),
            })
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not mkts:
            break
    return out


def build_families(markets: list[dict]) -> tuple[dict[str, list[dict]], dict]:
    """family -> resolutions oldest-first. Same-close ties are dropped, never
    ordered by guess. Returns (families at the floor, funnel)."""
    fams: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        fams[family_key(m)].append(m)
    out: dict[str, list[dict]] = {}
    ties = 0
    below = 0
    for key, rows in fams.items():
        rows.sort(key=lambda r: (r["close"], r["ticker"]))
        deduped = [r for i, r in enumerate(rows)
                   if i == 0 or r["close"] != rows[i - 1]["close"]]
        ties += len(rows) - len(deduped)
        if len(deduped) >= MIN_FAMILY_N:
            out[key] = deduped
        else:
            below += 1
    return out, {"families_seen": len(fams), "tie_rows_dropped": ties,
                 "below_floor": below, "at_floor": len(out)}


def decision_quote(series: str, ticker: str, close_ts: int) -> dict | None:
    """The 1-minute candle whose period ENDS at or before T-DECISION_MIN, from
    whichever archive holds the market. Returns {bid, ask, ts} in cents or None."""
    at = close_ts - DECISION_MIN * 60
    for url in (
        f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
        f"?start_ts={at - 600}&end_ts={at}&period_interval=1",
        f"{KALSHI}/historical/markets/{ticker}/candlesticks"
        f"?start_ts={at - 600}&end_ts={at}&period_interval=1",
    ):
        data = xl._get(url)
        candles = [c for c in ((data or {}).get("candlesticks") or [])
                   if int(xl._num(c.get("end_period_ts"))) <= at]
        if candles:
            c = candles[-1]
            bid = _candle_cents(c.get("yes_bid"))
            ask = _candle_cents(c.get("yes_ask"))
            if bid is None or ask is None:
                return None
            return {"bid": bid, "ask": ask, "ts": int(xl._num(c.get("end_period_ts")))}
    return None


def _candle_cents(leg: dict | None) -> float | None:
    """A candle leg's close in CENTS. Kalshi's candlesticks carry `close_dollars`
    (a dollar string, e.g. "0.4400"); the older integer-cent `close` is read as a
    fallback. Run 1 (m2-run-1, 2026-09-02) read only `close`, got None on all
    6,000 candles and priced nothing — every other candle reader in this
    repository multiplies `close_dollars` by 100."""
    if not leg:
        return None
    if leg.get("close_dollars") is not None:
        return xl._num(leg["close_dollars"]) * 100.0
    if leg.get("close") is not None:
        return xl._num(leg["close"])
    return None


def coinbase_closes(product: str, granularity: int, start: int, end: int) -> dict[int, float]:
    """{candle_start_ts: close} from Coinbase's public candles, chunked under the
    300-candle cap. Newest-first in the response; order does not matter here."""
    out: dict[int, float] = {}
    # The cap is 300 candles and the range is inclusive; 290 leaves no edge case.
    span = 290 * granularity
    t = start
    while t < end:
        t2 = min(end, t + span)
        url = (f"{COINBASE}/products/{product}/candles?granularity={granularity}"
               f"&start={_iso(t)}&end={_iso(t2)}")
        rows = xl._get(url)
        for row in rows or []:
            try:
                out[int(row[0])] = float(row[4])
            except (TypeError, ValueError, IndexError):
                continue
        t = t2
        time.sleep(0.12)  # Coinbase public limit is ~10 req/s
    return out


class SpotSeries:
    """No-lookahead spot and realized vol for one asset."""

    def __init__(self, hourly: dict[int, float], daily: dict[int, float]):
        self.hourly = hourly
        self.daily = daily
        self._daily_keys = sorted(daily)

    def spot_at(self, decision_ts: int) -> float | None:
        """Close of the last hourly candle that had fully COMPLETED by the
        decision time — never the candle in progress."""
        start = (decision_ts // 3600) * 3600 - 3600
        for back in range(6):
            v = self.hourly.get(start - back * 3600)
            if v is not None:
                return v
        return None

    def sigma_daily(self, decision_ts: int) -> float | None:
        """Std dev of the last VOL_WINDOW_DAYS daily log returns whose candles
        closed strictly before the decision's UTC day."""
        day = (decision_ts // 86400) * 86400
        closes = [self.daily[k] for k in self._daily_keys if k < day][-(VOL_WINDOW_DAYS + 1):]
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False) if a > 0 and b > 0]
        if len(rets) < MIN_VOL_RETURNS:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        sigma = math.sqrt(var) if var > 0 else 0.0
        return sigma if sigma >= 1e-6 else None  # a flat series has no scale to normalize by


def load_spot(asset: str, start: int, end: int) -> SpotSeries | None:
    product = COINBASE_PRODUCT.get(asset)
    if not product:
        return None
    hourly = coinbase_closes(product, 3600, start - 86400, end + 3600)
    daily = coinbase_closes(product, 86400, start - (VOL_WINDOW_DAYS + 5) * 86400, end + 86400)
    if not hourly or not daily:
        return None
    return SpotSeries(hourly, daily)


# =============================================================================
# Prediction points (§4, §5, §9)
# =============================================================================

def prediction_points(key: str, rows: list[dict], fam: dict,
                      spot: SpotSeries | None) -> list[dict]:
    """One record per position i >= 1: the market at i is the one being
    predicted, everything else is strictly earlier information."""
    pts: list[dict] = []
    series = key.split("|", 1)[0]
    run = 0
    for i, row in enumerate(rows):
        if i == 0:
            run = 1
            continue
        prev = rows[i - 1]["result"]
        run = run + 1 if i >= 2 and prev == rows[i - 2]["result"] else 1
        decision = row["close"] - DECISION_MIN * 60
        pt = {
            "family": key, "series": series, "cls": fam["cls"], "track": fam["track"],
            "ticker": row["ticker"], "event": row["event"], "close": row["close"],
            "decision": decision, "prev": prev, "k": run, "result": row["result"],
            "strike": fam.get("strike"), "spot": None, "sigma": None, "z_dir": None,
        }
        if fam["track"] == "B" and spot is not None and fam.get("strike"):
            s = spot.spot_at(decision)
            sig = spot.sigma_daily(decision)
            if s and sig:
                z = math.log(s / fam["strike"]) / sig
                sign = (1.0 if prev == "yes" else -1.0) * (1.0 if fam["yes_is_above"] else -1.0)
                pt.update(spot=s, sigma=sig, z_dir=max(-Z_CAP, min(Z_CAP, z * sign)))
        pts.append(pt)
    return pts


def split_by_time(points: list[dict], frac: float = TRAIN_FRAC) -> tuple[list[dict], list[dict], int]:
    """Chronological split of one class. Returns (train, holdout, cut_ts). The cut
    is the decision time at the TRAIN_FRAC quantile; ties at the cut go to
    HOLDOUT so nothing at the boundary is fitted on."""
    ordered = sorted(points, key=lambda p: (p["decision"], p["family"], p["ticker"]))
    if not ordered:
        return [], [], 0
    cut_idx = int(len(ordered) * frac)
    cut_ts = ordered[cut_idx]["decision"] if cut_idx < len(ordered) else ordered[-1]["decision"] + 1
    train = [p for p in ordered if p["decision"] < cut_ts]
    hold = [p for p in ordered if p["decision"] >= cut_ts]
    for p in train:
        p["split"] = "train"
    for p in hold:
        p["split"] = "holdout"
    return train, hold, cut_ts


# =============================================================================
# Models (§6, §8, §10, §14). Each `fit_*` reads TRAIN only and returns a
# predictor: point -> P(next = YES).
# =============================================================================

def _shrunk(s: float, n: float, prior: float, m: float = SHRINK_M) -> float:
    return (s + m * prior) / (n + m) if (n + m) > 0 else prior


def _yes(p: dict) -> int:
    return 1 if p["result"] == "yes" else 0


def fit_a0(train: list[dict]):
    """A0 — independence baseline: the family's unconditional YES rate, shrunk
    toward the class rate. A family unseen in TRAIN gets the class rate."""
    cls_n = len(train)
    cls_yes = sum(_yes(p) for p in train)
    cls_rate = cls_yes / cls_n if cls_n else 0.5
    fam = defaultdict(lambda: [0, 0])
    for p in train:
        fam[p["family"]][0] += _yes(p)
        fam[p["family"]][1] += 1
    rates = {f: _shrunk(s, n, cls_rate) for f, (s, n) in fam.items()}
    return lambda p: rates.get(p["family"], cls_rate)


def fit_a1(train: list[dict]):
    """A1 / B1 — one-step transition: P(YES | previous), per family, shrunk
    toward the class transition rate."""
    cls = {"yes": [0, 0], "no": [0, 0]}
    fam = defaultdict(lambda: {"yes": [0, 0], "no": [0, 0]})
    for p in train:
        cls[p["prev"]][0] += _yes(p)
        cls[p["prev"]][1] += 1
        fam[p["family"]][p["prev"]][0] += _yes(p)
        fam[p["family"]][p["prev"]][1] += 1
    cls_rate = {s: (v[0] / v[1] if v[1] else 0.5) for s, v in cls.items()}

    def predict(p: dict) -> float:
        s, n = fam[p["family"]][p["prev"]] if p["family"] in fam else (0, 0)
        return _shrunk(s, n, cls_rate[p["prev"]])
    return predict


def k_bucket_a(k: int) -> int:
    return min(k, MAX_K_A)


def k_bucket_b(k: int) -> int:
    for i, (lo, hi) in enumerate(DURATION_BUCKETS_B):
        if lo <= k <= hi:
            return i
    return len(DURATION_BUCKETS_B) - 1


def fit_streak_table(train: list[dict], bucket):
    """A2 / B2 — class-pooled P(YES | previous, streak-length bucket), shrunk
    toward the class one-step rate for that direction. Direction is never
    collapsed: YES-streaks and NO-streaks are separate rows."""
    cls = {"yes": [0, 0], "no": [0, 0]}
    cell = defaultdict(lambda: [0, 0])
    for p in train:
        cls[p["prev"]][0] += _yes(p)
        cls[p["prev"]][1] += 1
        c = cell[(p["prev"], bucket(p["k"]))]
        c[0] += _yes(p)
        c[1] += 1
    cls_rate = {s: (v[0] / v[1] if v[1] else 0.5) for s, v in cls.items()}
    est = {key: _shrunk(s, n, cls_rate[key[0]]) for key, (s, n) in cell.items()}

    def predict(p: dict) -> float:
        return est.get((p["prev"], bucket(p["k"])), cls_rate[p["prev"]])
    predict.table = {key: (s, n, est[key]) for key, (s, n) in cell.items()}  # type: ignore[attr-defined]
    return predict


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting. Deterministic."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            m[piv][col] = 1e-12
        m[col], m[piv] = m[piv], m[col]
        inv = 1.0 / m[col][col]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col] * inv
            if f == 0.0:
                continue
            rr, rc = m[r], m[col]
            for c in range(col, n + 1):
                rr[c] -= f * rc[c]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit_logistic(rows: list[tuple[list[float], int, int]], n_dense: int, n_groups: int,
                 group_penalty: float, slope_penalty: float, iters: int = 40):
    """Penalized logistic regression by Newton-Raphson.

    rows: (dense_features, group_index or -1, y). The intercept is dense[0] and
    is unpenalized; other dense slopes carry `slope_penalty`; each group one-hot
    carries `group_penalty` (the ridge/random-effect on family). Returns (beta,
    dense_se) where beta = dense coefficients + group effects."""
    p = n_dense + n_groups
    beta = [0.0] * p
    pen = [0.0] + [slope_penalty] * (n_dense - 1) + [group_penalty] * n_groups
    for _ in range(iters):
        g = [0.0] * p
        h = [[0.0] * p for _ in range(p)]
        for x, gi, y in rows:
            eta = sum(b * v for b, v in zip(beta[:n_dense], x, strict=False))
            if gi >= 0:
                eta += beta[n_dense + gi]
            mu = _sigmoid(eta)
            w = mu * (1.0 - mu)
            r = y - mu
            for j in range(n_dense):
                g[j] += r * x[j]
                hj = h[j]
                wx = w * x[j]
                for kk in range(n_dense):
                    hj[kk] += wx * x[kk]
                if gi >= 0:
                    hj[n_dense + gi] += wx
            if gi >= 0:
                gj = n_dense + gi
                g[gj] += r
                hg = h[gj]
                for kk in range(n_dense):
                    hg[kk] += w * x[kk]
                hg[gj] += w
        for j in range(p):
            g[j] -= pen[j] * beta[j]
            h[j][j] += pen[j]
        step = _solve(h, g)
        # Damped: halve the step while it would overshoot, for stability under
        # near-separation. Deterministic.
        scale = 1.0
        while max(abs(s) for s in step) * scale > 5.0:
            scale *= 0.5
        beta = [b + scale * s for b, s in zip(beta, step, strict=False)]
        if max(abs(s) for s in step) * scale < 1e-7:
            break
    se: list[float] = []
    for j in range(n_dense):
        e = [0.0] * p
        e[j] = 1.0
        col = _solve(h, e)
        se.append(math.sqrt(max(col[j], 0.0)))
    return beta, se


def _a3_features(p: dict) -> list[float]:
    s = 1.0 if p["prev"] == "yes" else -1.0
    lk = math.log(p["k"])
    return [1.0, s, lk, s * lk]


A3_FEATURE_NAMES = ("intercept", "prev_dir (+1 YES / -1 NO)", "ln(k)", "prev_dir x ln(k)")


def fit_a3(train: list[dict]):
    """A3 — hierarchical logistic: P(YES) on streak direction, ln(streak length),
    their interaction, and a ridge-penalized family effect (the family baseline).
    `prev_dir x ln(k)` is the coefficient the whole track turns on: positive
    means persistence grows with k, negative means reversion grows with k."""
    fams = sorted({p["family"] for p in train})
    idx = {f: i for i, f in enumerate(fams)}
    rows = [(_a3_features(p), idx[p["family"]], _yes(p)) for p in train]
    if not rows:
        return None
    beta, se = fit_logistic(rows, 4, len(fams), RIDGE_FAMILY, RIDGE_SLOPE)

    def predict(p: dict) -> float:
        x = _a3_features(p)
        eta = sum(b * v for b, v in zip(beta[:4], x, strict=False))
        gi = idx.get(p["family"])
        if gi is not None:
            eta += beta[4 + gi]
        return _sigmoid(eta)
    predict.beta = beta[:4]  # type: ignore[attr-defined]
    predict.se = se  # type: ignore[attr-defined]
    predict.family_effects = {f: beta[4 + i] for f, i in idx.items()}  # type: ignore[attr-defined]
    return predict


def _b3_features(p: dict) -> list[float]:
    s = 1.0 if p["prev"] == "yes" else -1.0
    lk = math.log(p["k"])
    z = p["z_dir"]
    return [1.0, s, lk, z, z * lk]


B3_FEATURE_NAMES = ("intercept", "state (+1 YES / -1 NO)", "ln(duration)",
                    "z_dir (signed distance / vol)", "z_dir x ln(duration)")


def fit_b3(train: list[dict]):
    """B3 — continuation logistic on state, ln(duration), signed normalized
    distance to the strike and their interaction. Points without spot data are
    not fitted and are not predicted (the arm abstains, and the count is
    reported). Outcome modelled is CONTINUATION; P(YES) follows from the state."""
    rows = [(_b3_features(p), -1, 1 if p["result"] == p["prev"] else 0)
            for p in train if p["z_dir"] is not None]
    if len(rows) < 50:
        return None
    beta, se = fit_logistic(rows, 5, 0, 0.0, RIDGE_SLOPE)

    def predict(p: dict) -> float | None:
        if p["z_dir"] is None:
            return None
        eta = sum(b * v for b, v in zip(beta, _b3_features(p), strict=False))
        pc = _sigmoid(eta)
        return pc if p["prev"] == "yes" else 1.0 - pc
    predict.beta = beta  # type: ignore[attr-defined]
    predict.se = se  # type: ignore[attr-defined]
    predict.n_fit = len(rows)  # type: ignore[attr-defined]
    return predict


#: arm key -> (track, label, fitter). The mirror controls are derived per
#: treatment, not fitted. A0 is the comparator every treatment must beat.
ARMS: dict[str, tuple[str, str, object]] = {
    "A0": ("A", "independence baseline (family base rate)", fit_a0),
    "A1": ("A", "one-step transition", fit_a1),
    "A2": ("A", "streak-length reversion (direction-specific)",
           lambda tr: fit_streak_table(tr, k_bucket_a)),
    "A3": ("A", "hierarchical reversion (family effects)", fit_a3),
    "B0": ("B", "independence baseline (family base rate)", fit_a0),
    "B1": ("B", "one-step persistence", fit_a1),
    "B2": ("B", "state duration", lambda tr: fit_streak_table(tr, k_bucket_b)),
    "B3": ("B", "state + duration + threshold distance", fit_b3),
}
PRIMARY = {"A": "A3", "B": "B3"}
BASELINE = {"A": "A0", "B": "B0"}
TREATMENTS = {"A": ("A1", "A2", "A3"), "B": ("B1", "B2", "B3")}

# =============================================================================
# Execution and economics (§12, §13)
# =============================================================================

def side_economics(p_yes: float, quote: dict) -> dict | None:
    """Best side by net edge, or None if the quote fails the liquidity screen.

    YES costs the ask; NO costs 100 - bid. Net edge per contract, cents:
        100 * P(side wins) - price - fee(price) - slippage."""
    bid, ask = quote["bid"], quote["ask"]
    if not (1.0 <= bid <= 99.0 and 1.0 <= ask <= 99.0) or ask < bid or ask - bid > MAX_SPREAD_C:
        return None
    cands = []
    for side, price, pw in (("yes", ask, p_yes), ("no", 100.0 - bid, 1.0 - p_yes)):
        fee = m1.taker_fee_c(price)
        edge = 100.0 * pw - price - fee - SLIPPAGE_C
        cands.append({"side": side, "price": price, "fee": fee, "edge": edge, "p_win": pw})
    return max(cands, key=lambda c: c["edge"])


def opposite(econ: dict, quote: dict) -> dict:
    """The mirror: the other side of the same book at the same instant."""
    if econ["side"] == "yes":
        price, pw = 100.0 - quote["bid"], 1.0 - econ["p_win"]
        side = "no"
    else:
        price, pw = quote["ask"], 1.0 - econ["p_win"]
        side = "yes"
    fee = m1.taker_fee_c(price)
    return {"side": side, "price": price, "fee": fee,
            "edge": 100.0 * pw - price - fee - SLIPPAGE_C, "p_win": pw}


def kelly_units(edge_c: float, price_c: float, p_win: float) -> int:
    """Secondary sizing (§13): quarter-Kelly, one unit per KELLY_UNIT_FRAC of
    bankroll, capped. A function of (edge, price, p) ONLY."""
    b = (100.0 - price_c) / price_c if price_c > 0 else 0.0
    if b <= 0:
        return 1
    f = (p_win * b - (1.0 - p_win)) / b
    units = int(KELLY_FRACTION * f / KELLY_UNIT_FRAC)
    return max(1, min(KELLY_CAP_UNITS, units))


def settle(econ: dict, result: str) -> tuple[float, float]:
    """(gross, net) P&L in cents for one contract on `econ['side']`."""
    won = (result == econ["side"])
    gross = (100.0 - econ["price"]) if won else -econ["price"]
    return gross, gross - econ["fee"] - SLIPPAGE_C


def simulate(points: list[dict], predictor, arm: str, quotes: dict[str, dict],
             mirror: bool) -> tuple[list[dict], dict]:
    """Every priced point becomes a decision; a trade when the best side clears
    the bar. The mirror takes the opposite side of exactly the treatment's
    trades. Returns (trade rows, prediction stats)."""
    trades: list[dict] = []
    n_pred = n_priced = n_abstain = 0
    brier = 0.0
    correct = 0
    for p in sorted(points, key=lambda q: (q["decision"], q["family"], q["ticker"])):
        pr = predictor(p)
        if pr is None:
            n_abstain += 1
            continue
        n_pred += 1
        y = _yes(p)
        brier += (pr - y) ** 2
        correct += 1 if (pr >= 0.5) == (y == 1) else 0
        q = quotes.get(p["ticker"])
        if not q:
            continue
        n_priced += 1
        econ = side_economics(pr, q)
        if econ is None or econ["edge"] < EDGE_BAR_C:
            continue
        leg = opposite(econ, q) if mirror else econ
        gross, net = settle(leg, p["result"])
        trades.append({
            "market_ticker": p["ticker"], "series": p["series"], "family": p["family"],
            "class": p["cls"], "timestamp": _iso(p["decision"]), "track": p["track"],
            "arm": arm + ("_mirror" if mirror else ""), "prior_outcome": p["prev"],
            "streak_direction": p["prev"], "streak_length": p["k"],
            "strike": p["strike"], "spot": p["spot"], "z_dir": p["z_dir"],
            "model_prob_yes": round(pr, 6), "market_yes_bid": q["bid"],
            "market_yes_ask": q["ask"], "exec_price": leg["price"],
            "edge": round(leg["edge"], 4), "fee": leg["fee"], "slippage": SLIPPAGE_C,
            "side": leg["side"], "size": 1,
            "kelly_units": kelly_units(econ["edge"], econ["price"], econ["p_win"]),
            "resolution": p["result"], "gross_pnl": gross, "net_pnl": net,
            "split": p["split"],
        })
    stats = {"n_pred": n_pred, "n_priced": n_priced, "n_abstain": n_abstain,
             "brier": (brier / n_pred) if n_pred else None,
             "accuracy": (correct / n_pred) if n_pred else None}
    return trades, stats


def economics(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "gross": 0.0, "fees": 0.0, "slippage": 0.0, "net": 0.0,
                "ev": None, "ror": None, "avg_edge": None, "mdd": 0.0,
                "worst_streak": 0, "profit_factor": None, "yes_net": 0.0, "no_net": 0.0}
    gross = sum(t["gross_pnl"] for t in trades)
    fees = sum(t["fee"] for t in trades)
    slip = sum(t["slippage"] for t in trades)
    net = sum(t["net_pnl"] for t in trades)
    risk = sum(t["exec_price"] for t in trades)
    wins = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    losses = -sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0)
    cum = peak = mdd = 0.0
    streak = worst = 0
    for t in sorted(trades, key=lambda t: t["timestamp"]):
        cum += t["net_pnl"]
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
        streak = streak + 1 if t["net_pnl"] < 0 else 0
        worst = max(worst, streak)
    return {
        "n": len(trades), "gross": gross, "fees": fees, "slippage": slip, "net": net,
        "ev": net / len(trades), "ror": (net / risk) if risk else None,
        "avg_edge": sum(t["edge"] for t in trades) / len(trades),
        "mdd": mdd, "worst_streak": worst,
        "profit_factor": (wins / losses) if losses > 0 else (None if wins == 0 else float("inf")),
        "yes_net": sum(t["net_pnl"] for t in trades if t["side"] == "yes"),
        "no_net": sum(t["net_pnl"] for t in trades if t["side"] == "no"),
    }


def by_family(trades: list[dict]) -> dict[str, dict]:
    fam = defaultdict(list)
    for t in trades:
        fam[t["family"]].append(t)
    return {f: economics(ts) for f, ts in sorted(fam.items())}


def robustness(trades: list[dict]) -> dict:
    """§17.6-7: drop the top family; drop the top 1% of trades."""
    if not trades:
        return {"net_ex_top_family": None, "top_family": None, "net_ex_top_trades": None,
                "top_trades_removed": 0}
    fam = by_family(trades)
    top_fam = max(fam.items(), key=lambda kv: kv[1]["net"])[0]
    ex_fam = sum(t["net_pnl"] for t in trades if t["family"] != top_fam)
    k = max(1, math.ceil(TOP_TRADE_FRAC * len(trades)))
    ordered = sorted(trades, key=lambda t: -t["net_pnl"])
    ex_top = sum(t["net_pnl"] for t in ordered[k:])
    return {"net_ex_top_family": ex_fam, "top_family": top_fam,
            "net_ex_top_trades": ex_top, "top_trades_removed": k}


# =============================================================================
# Verdicts (§17, §18)
# =============================================================================

def grade(arm: str, res: dict, base: dict, mirror: dict, n_train_points: int,
          coverage: float) -> tuple[str, list[str]]:
    """Verdict for one (class, arm) on HOLDOUT. Every clause is reported, pass
    or fail, so a verdict can be audited line by line."""
    econ = res["econ"]
    reasons: list[str] = []
    if n_train_points < FLOOR_TRAIN_POINTS:
        reasons.append(f"train points {n_train_points} < {FLOOR_TRAIN_POINTS}")
    if coverage < PRICE_COVERAGE_FLOOR:
        reasons.append(f"price coverage {coverage:.0%} < {PRICE_COVERAGE_FLOOR:.0%}")
    if econ["n"] < FLOOR_HOLDOUT_TRADES:
        reasons.append(f"holdout trades {econ['n']} < {FLOOR_HOLDOUT_TRADES}")
    if reasons:
        return "HOLD", reasons
    checks = [
        ("net P&L > 0", econ["net"] > 0),
        ("EV/trade > 0", (econ["ev"] or 0) > 0),
        ("Brier < baseline Brier",
         res["stats"]["brier"] is not None and base["stats"]["brier"] is not None
         and res["stats"]["brier"] < base["stats"]["brier"]),
        ("net P&L > baseline net P&L", econ["net"] > base["econ"]["net"]),
        (f"EV/trade - mirror EV/trade >= {MIRROR_DELTA_C}c",
         mirror["econ"]["n"] > 0 and (econ["ev"] - (mirror["econ"]["ev"] or 0)) >= MIRROR_DELTA_C),
        ("net P&L > 0 without the top family", (res["robust"]["net_ex_top_family"] or 0) > 0),
        ("net P&L > 0 without the top 1% of trades", (res["robust"]["net_ex_top_trades"] or 0) > 0),
    ]
    failed = [name for name, ok in checks if not ok]
    passed = [name for name, ok in checks if ok]
    if failed:
        return "FAIL", [f"failed: {n}" for n in failed] + [f"ok: {n}" for n in passed]
    return "PASS", [f"ok: {n}" for n in passed]


# =============================================================================
# Rendering
# =============================================================================

def _f(v, nd=2, pct=False):
    if v is None:
        return "—"
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    return f"{100 * v:.{nd}f}%" if pct else f"{v:.{nd}f}"


def md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def section(name: str, body: str) -> None:
    print(f"### BEGIN {name}")
    print(body.rstrip())
    print(f"### END {name}")


CSV_COLUMNS = [
    "market_ticker", "series", "family", "class", "timestamp", "track", "arm",
    "prior_outcome", "streak_direction", "streak_length", "strike", "spot", "z_dir",
    "model_prob_yes", "market_yes_bid", "market_yes_ask", "exec_price", "edge", "fee",
    "slippage", "side", "size", "kelly_units", "resolution", "gross_pnl", "net_pnl", "split",
]


def trades_csv(trades: list[dict]) -> str:
    lines = [",".join(CSV_COLUMNS)]
    for t in sorted(trades, key=lambda t: (t["track"], t["class"], t["arm"], t["timestamp"],
                                           t["family"], t["market_ticker"])):
        vals = []
        for c in CSV_COLUMNS:
            v = t.get(c)
            vals.append("" if v is None else (f"{v:.6g}" if isinstance(v, float) else str(v)))
        lines.append(",".join(vals))
    return "\n".join(lines) + "\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# =============================================================================
# The run
# =============================================================================

def run(markets_by_series: dict[str, list[dict]], spot_loader, quote_fetcher,
        max_fetch: int, code_sha: str, config: dict) -> dict:
    """The whole analysis on already-fetched settled markets. Pure given its
    inputs (spot_loader and quote_fetcher are the two I/O seams), which is what
    makes the fingerprint claim testable."""
    t0 = time.time()
    # ---- families and classification (data report)
    all_markets = [m for rows in markets_by_series.values() for m in rows]
    families, funnel = build_families(all_markets)
    classified: dict[str, dict] = {}
    unclassified: dict[str, int] = defaultdict(int)
    constant = 0
    thin_state = 0
    for key, rows in sorted(families.items()):
        fam = classify_family(key, rows)
        if fam is None:
            unclassified[key.split("|", 1)[0]] += 1
            continue
        yes = sum(1 for r in rows if r["result"] == "yes")
        if yes == 0 or yes == len(rows):
            constant += 1
            continue
        if fam["track"] == "B" and min(yes, len(rows) - yes) < MIN_STATE_N:
            thin_state += 1
            continue
        classified[key] = fam
    data_cutoff = max((m["close"] for m in all_markets), default=0)
    data_start = min((m["close"] for m in all_markets), default=0)

    # ---- spot series per asset (Track B)
    spots: dict[str, SpotSeries | None] = {}
    for fam in classified.values():
        if fam["track"] == "B":
            asset = fam["cls"].split(":", 1)[1]
            if asset not in spots:
                spots[asset] = spot_loader(asset, data_start, data_cutoff)

    # ---- prediction points per class, split per class
    by_cls: dict[str, list[dict]] = defaultdict(list)
    for key, fam in classified.items():
        sp = spots.get(fam["cls"].split(":", 1)[1]) if fam["track"] == "B" else None
        by_cls[fam["cls"]].extend(prediction_points(key, families[key], fam, sp))
    splits: dict[str, tuple[list[dict], list[dict], int]] = {}
    for cls, pts in by_cls.items():
        splits[cls] = split_by_time(pts)

    # ---- quotes: holdout first (Track A, then B), chronological; then train
    order: list[dict] = []
    for track in ("A", "B"):
        for cls in sorted(by_cls):
            if by_cls[cls] and by_cls[cls][0]["track"] == track:
                order.extend(sorted(splits[cls][1], key=lambda p: p["decision"]))
    for cls in sorted(by_cls):
        order.extend(sorted(splits[cls][0], key=lambda p: p["decision"]))
    quotes: dict[str, dict] = {}
    fetched = 0
    for p in order:
        if p["ticker"] in quotes:
            continue
        if fetched >= max_fetch:
            break
        fetched += 1
        q = quote_fetcher(p["series"], p["ticker"], p["close"])
        if q:
            quotes[p["ticker"]] = q

    # ---- fit, simulate, grade
    results: dict[str, dict] = {}   # cls -> {arm: {...}}
    all_trades: list[dict] = []
    for cls, (train, hold, cut) in sorted(splits.items()):
        track = (train + hold)[0]["track"]
        cov_h = (sum(1 for p in hold if p["ticker"] in quotes) / len(hold)) if hold else 0.0
        cov_t = (sum(1 for p in train if p["ticker"] in quotes) / len(train)) if train else 0.0
        arms_out: dict[str, dict] = {}
        for arm, (atrack, label, fitter) in ARMS.items():
            if atrack != track:
                continue
            pred = fitter(train) if train else None
            rec = {"label": label, "fitted": pred is not None, "model": pred}
            for split_name, pts in (("train", train), ("holdout", hold)):
                if pred is None:
                    rec[split_name] = None
                    continue
                trades, stats = simulate(pts, pred, arm, quotes, mirror=False)
                mtrades, _ = simulate(pts, pred, arm, quotes, mirror=True)
                rec[split_name] = {
                    "trades": trades, "stats": stats, "econ": economics(trades),
                    "mirror": {"trades": mtrades, "econ": economics(mtrades)},
                    "robust": robustness(trades), "families": by_family(trades),
                }
                all_trades.extend(trades)
                all_trades.extend(mtrades)
            arms_out[arm] = rec
        base_key = BASELINE[track]
        for arm, rec in arms_out.items():
            h = rec.get("holdout")
            b = arms_out[base_key].get("holdout")
            if h is None or b is None:
                rec["verdict"] = ("HOLD", ["no fitted model or no holdout"])
            elif arm == base_key:
                rec["verdict"] = ("—", ["baseline: comparator, not graded"])
            else:
                rec["verdict"] = grade(arm, h, b, h["mirror"], len(train), cov_h)
        results[cls] = {"track": track, "train": train, "hold": hold, "cut": cut,
                        "coverage_holdout": cov_h, "coverage_train": cov_t,
                        "arms": arms_out}

    return {
        "funnel": funnel, "classified": classified, "unclassified": dict(unclassified),
        "constant": constant, "thin_state": thin_state, "families": families,
        "data_start": data_start, "data_cutoff": data_cutoff, "spots": spots,
        "results": results, "trades": all_trades, "quotes_fetched": fetched,
        "quotes_found": len(quotes), "code_sha": code_sha, "config": config,
        "elapsed_s": time.time() - t0, "markets_by_series": markets_by_series,
    }


# ----------------------------------------------------------------------- report

def track_verdict(out: dict, track: str) -> tuple[str, str]:
    """PASS if the PRIMARY treatment passes in >= 1 class; FAIL if it is
    adequately powered and fails everywhere; else HOLD. The primary is fixed
    before the data (A3, B3); the other arms are read, never promoted."""
    primary = PRIMARY[track]
    verdicts = [(cls, r["arms"][primary]["verdict"]) for cls, r in out["results"].items()
                if r["track"] == track and primary in r["arms"]]
    if not verdicts:
        return "HOLD", "no class in this track reached the family floor"
    passed = [c for c, (v, _) in verdicts if v == "PASS"]
    failed = [c for c, (v, _) in verdicts if v == "FAIL"]
    if passed:
        return "PASS", f"{primary} passes in {len(passed)} of {len(verdicts)} classes: {', '.join(passed)}"
    if failed and len(failed) == len(verdicts):
        return "FAIL", f"{primary} adequately powered and failing in every class ({len(failed)})"
    if failed:
        return "HOLD", (f"{primary} fails in {len(failed)} classes and is under-powered in "
                        f"{len(verdicts) - len(failed)}; the track is not adequately answered")
    return "HOLD", f"{primary} under-powered in every class ({len(verdicts)})"


def render_data_report(out: dict) -> str:
    L = ["# MARKTANGLE-2 data report", "",
         f"Code SHA `{out['code_sha']}` · data {_iso(out['data_start'])} → {_iso(out['data_cutoff'])} "
         f"(cutoff = latest settled close in the pull) · generated {_iso(int(time.time()))}", "",
         "## Universe pull", "",
         md_table(["series", "settled markets", "structural class"],
                  [[s, len(rows), (classify_series(s) or ("—", "UNCLASSIFIED"))[1]]
                   for s, rows in sorted(out["markets_by_series"].items())]),
         "", "## Family funnel", "",
         md_table(["stage", "count"], [
             ["families seen", out["funnel"]["families_seen"]],
             ["same-close tie rows dropped", out["funnel"]["tie_rows_dropped"]],
             [f"below the {MIN_FAMILY_N}-resolution floor", out["funnel"]["below_floor"]],
             ["at the floor", out["funnel"]["at_floor"]],
             ["unclassified (series structure not recognised)", sum(out["unclassified"].values())],
             ["constant (0% or 100% YES)", out["constant"]],
             [f"Track B with < {MIN_STATE_N} observations of one outcome", out["thin_state"]],
             ["analysed", len(out["classified"])],
         ]), ""]
    if out["unclassified"]:
        L += ["Unclassified series (families at the floor): " +
              ", ".join(f"{s} ({n})" for s, n in sorted(out["unclassified"].items())), ""]
    L += ["## Classes", ""]
    rows = []
    for cls, r in sorted(out["results"].items()):
        fams = {p["family"] for p in r["train"] + r["hold"]}
        rows.append([cls, r["track"], len(fams), len(r["train"]), len(r["hold"]), _iso(r["cut"]),
                     _f(r["coverage_train"], 0, True), _f(r["coverage_holdout"], 0, True)])
    L += [md_table(["class", "track", "families", "train points", "holdout points",
                    "split cut (decision ts)", "train priced", "holdout priced"], rows), ""]
    L += ["## Families", "",
          md_table(["family", "class", "n", "YES%", "strike"],
                   [[k, f["cls"], len(out["families"][k]),
                     _f(sum(1 for r in out["families"][k] if r["result"] == "yes") / len(out["families"][k]), 1, True),
                     _f(f["strike"], 4) if f["strike"] is not None else "—"]
                    for k, f in sorted(out["classified"].items())]), ""]
    L += ["## Price-data coverage", "",
          f"Kalshi 1-minute candle at T-{DECISION_MIN}m: {out['quotes_fetched']} fetched, "
          f"{out['quotes_found']} returned a two-sided quote. Fetch budget "
          f"{out['config']['max_fetch']} (holdout first, then train). Coverage per class above.", ""]
    for asset, sp in sorted(out["spots"].items()):
        L.append(f"- Coinbase spot {asset}: " + (
            f"{len(sp.hourly)} hourly closes, {len(sp.daily)} daily closes" if sp else "UNAVAILABLE"))
    L += ["", "## Exclusions", "",
          "- Same-close ties dropped rather than ordered by guess.",
          "- Constant families excluded: no conditional structure exists.",
          "- Track A excludes daily crypto thresholds by construction (§3).",
          "- Track B excludes bucket (between) crypto markets: not a level crossing.",
          "- Sports totals/spreads pool only within one SPORT; leagues outside the "
          "structural table are unclassified and reported, never pooled."]
    return "\n".join(L)


def _arm_rows(r: dict, split: str) -> list[list]:
    rows = []
    for arm, rec in r["arms"].items():
        s = rec.get(split)
        if not s:
            rows.append([arm, rec["label"], "not fitted"] + ["—"] * 13)
            continue
        e, me, st = s["econ"], s["mirror"]["econ"], s["stats"]
        rows.append([arm, rec["label"], st["n_pred"], st["n_priced"], _f(st["accuracy"], 1, True),
                     _f(st["brier"], 4), e["n"], _f(e["gross"], 0), _f(e["fees"] + e["slippage"], 0),
                     _f(e["net"], 0), _f(e["ev"]), _f(e["avg_edge"]), _f(e["ror"], 1, True),
                     _f(e["mdd"], 0), e["worst_streak"], _f(e["profit_factor"]),
                     _f(me["ev"]), f"{_f(e['yes_net'], 0)} / {_f(e['no_net'], 0)}"])
    return rows


ARM_HEADERS = ["arm", "rule", "N pred", "N priced", "accuracy", "Brier", "N trades", "gross c",
               "fees+slip c", "net c", "EV/trade c", "avg edge c", "return on risk", "max DD c",
               "worst streak", "profit factor", "mirror EV/trade c", "YES / NO net c"]


def render_track_a(out: dict) -> str:
    L = ["# MARKTANGLE-2 Track A — cross-family conditional reversion", "",
         "Direction is never collapsed: every conditional row is P(NO next | k YES) or "
         "P(YES next | k NO), with its n and a one-sided Wilson 95% lower bound.", ""]
    any_cls = False
    for cls, r in sorted(out["results"].items()):
        if r["track"] != "A":
            continue
        any_cls = True
        train, hold = r["train"], r["hold"]
        fams = sorted({p["family"] for p in train + hold})
        L += [f"## {cls}", "",
              f"families {len(fams)} · train points {len(train)} · holdout points {len(hold)} · "
              f"split at {_iso(r['cut'])} · holdout priced {_f(r['coverage_holdout'], 0, True)}", "",
              "### A-SIMPLE — streak count and directional reversal by k (TRAIN | HOLDOUT)", ""]
        rows = []
        for direction in ("yes", "no"):
            for kb in range(1, MAX_K_A + 1):
                cells = []
                for pts in (train, hold):
                    n = sum(1 for p in pts if p["prev"] == direction and k_bucket_a(p["k"]) == kb)
                    rev = sum(1 for p in pts if p["prev"] == direction and k_bucket_a(p["k"]) == kb
                              and p["result"] != direction)
                    cells += [n, _f(rev / n, 1, True) if n else "—",
                              _f(m1.wilson_lower(rev, n) / 100, 1, True) if n else "—"]
                label = f"{kb}" if kb < MAX_K_A else f">={MAX_K_A}"
                rows.append([direction.upper(), label] + cells)
        L += [md_table(["streak of", "k", "train n", "P(rev)", "lb95", "hold n", "P(rev)", "lb95"], rows), ""]
        yes_tr = sum(_yes(p) for p in train)
        L += [f"Class YES rate: train {_f(yes_tr / len(train), 1, True) if train else '—'}, "
              f"holdout {_f(sum(_yes(p) for p in hold) / len(hold), 1, True) if hold else '—'}.", ""]
        a3 = r["arms"].get("A3", {}).get("model")
        if a3 is not None:
            L += ["### A-HIERARCHICAL — logistic P(YES) with ridge family effects (TRAIN fit)", "",
                  md_table(["coefficient", "estimate", "approx SE", "z"],
                           [[n, _f(b, 4), _f(s, 4), _f(b / s if s else None)]
                            for n, b, s in zip(A3_FEATURE_NAMES, a3.beta, a3.se, strict=False)]),
                  "", f"Family effects (ridge λ={RIDGE_FAMILY}): " +
                  ", ".join(f"{f.split('|')[1] or f}={_f(v, 2)}" for f, v in sorted(a3.family_effects.items())[:40]),
                  ""]
        L += ["### Economics — TRAIN (in-sample, descriptive)", "",
              md_table(ARM_HEADERS, _arm_rows(r, "train")), "",
              "### Economics — HOLDOUT (the verdict)", "",
              md_table(ARM_HEADERS, _arm_rows(r, "holdout")), "",
              "### Verdicts (HOLDOUT)", ""]
        for arm, rec in r["arms"].items():
            v, reasons = rec["verdict"]
            L.append(f"- **{arm} — {v}**: " + "; ".join(reasons))
        L += ["", "### Per-family HOLDOUT net P&L (A3, treatment)", ""]
        h = r["arms"].get("A3", {}).get("holdout")
        if h:
            L += [md_table(["family", "trades", "net c", "EV/trade c"],
                           [[f, e["n"], _f(e["net"], 0), _f(e["ev"])] for f, e in h["families"].items()]),
                  "", f"Robustness: net without top family `{h['robust']['top_family']}` = "
                  f"{_f(h['robust']['net_ex_top_family'], 0)}c; net without top "
                  f"{h['robust']['top_trades_removed']} trade(s) = {_f(h['robust']['net_ex_top_trades'], 0)}c.", ""]
    if not any_cls:
        L += ["No Track A class reached the family floor.", ""]
    v, why = track_verdict(out, "A")
    L += [f"## TRACK A VERDICT: {v}", "", why]
    return "\n".join(L)


def render_track_b(out: dict) -> str:
    L = ["# MARKTANGLE-2 Track B — crypto threshold persistence", "",
         "State = previous resolution; duration = consecutive settlements in that state; "
         "z_dir = ln(spot/strike) / trailing 20-day realized daily vol, signed so that "
         "positive means spot is on the side that CONTINUES the state. Spot is the last "
         "completed Coinbase hourly close before T-60m; never a candle in progress.", ""]
    any_cls = False
    for cls, r in sorted(out["results"].items()):
        if r["track"] != "B":
            continue
        any_cls = True
        train, hold = r["train"], r["hold"]
        L += [f"## {cls}", "",
              f"train points {len(train)} · holdout points {len(hold)} · split at {_iso(r['cut'])} · "
              f"holdout priced {_f(r['coverage_holdout'], 0, True)} · "
              f"holdout with spot {_f(sum(1 for p in hold if p['z_dir'] is not None) / len(hold), 0, True) if hold else '—'}",
              "", "### Families (all history)", ""]
        rows = []
        for key in sorted({p["family"] for p in train + hold}):
            seq = [x["result"] for x in out["families"][key]]
            n = len(seq)
            yy = sum(1 for a, b in zip(seq, seq[1:], strict=False) if a == "yes" and b == "yes")
            ny = sum(1 for a in seq[:-1] if a == "yes")
            nn = sum(1 for a, b in zip(seq, seq[1:], strict=False) if a == "no" and b == "no")
            nno = sum(1 for a in seq[:-1] if a == "no")
            rows.append([key.split("|")[1], n, _f(seq.count("yes") / n, 1, True),
                         _f(yy / ny, 1, True) if ny else "—", _f(nn / nno, 1, True) if nno else "—"])
        L += [md_table(["strike", "n", "YES%", "P(Y|Y)", "P(N|N)"], rows), "",
              "### Continuation by state duration (TRAIN | HOLDOUT)", ""]
        rows = []
        for direction in ("yes", "no"):
            for i, (lo, hi) in enumerate(DURATION_BUCKETS_B):
                cells = []
                for pts in (train, hold):
                    n = sum(1 for p in pts if p["prev"] == direction and k_bucket_b(p["k"]) == i)
                    c = sum(1 for p in pts if p["prev"] == direction and k_bucket_b(p["k"]) == i
                            and p["result"] == direction)
                    cells += [n, _f(c / n, 1, True) if n else "—",
                              _f(m1.wilson_lower(c, n) / 100, 1, True) if n else "—"]
                rows.append([direction.upper(), f"{lo}" if lo == hi else (f"{lo}-{hi}" if hi < 10**9 else f"{lo}+")] + cells)
        L += [md_table(["state", "duration", "train n", "P(cont)", "lb95", "hold n", "P(cont)", "lb95"], rows), "",
              "### Continuation by normalized distance z_dir (TRAIN | HOLDOUT)", ""]
        rows = []
        for lo, hi in zip(Z_BINS, Z_BINS[1:], strict=False):
            cells = []
            for pts in (train, hold):
                sel = [p for p in pts if p["z_dir"] is not None and lo <= p["z_dir"] < hi]
                n = len(sel)
                c = sum(1 for p in sel if p["result"] == p["prev"])
                cells += [n, _f(c / n, 1, True) if n else "—"]
            rows.append([f"[{lo:+.1f}, {hi:+.1f})"] + cells)
        L += [md_table(["z_dir bin", "train n", "P(cont)", "hold n", "P(cont)"], rows), ""]
        b3 = r["arms"].get("B3", {}).get("model")
        if b3 is not None:
            L += ["### B3 — continuation logistic (TRAIN fit)", "",
                  md_table(["coefficient", "estimate", "approx SE", "z"],
                           [[n, _f(b, 4), _f(s, 4), _f(b / s if s else None)]
                            for n, b, s in zip(B3_FEATURE_NAMES, b3.beta, b3.se, strict=False)]),
                  "", f"Fitted on {b3.n_fit} train points with spot data.", ""]
        else:
            L += ["### B3 — NOT FITTED (fewer than 50 train points with spot data)", ""]
        L += ["### Model vs market — HOLDOUT, priced points, primary arm B3", ""]
        h = r["arms"].get("B3", {}).get("holdout")
        if h and h["trades"]:
            L += [f"Trades {h['econ']['n']}: mean model P(win) at entry "
                  f"{_f(sum(t['model_prob_yes'] if t['side'] == 'yes' else 1 - t['model_prob_yes'] for t in h['trades']) / len(h['trades']), 1, True)}, "
                  f"mean executable price {_f(sum(t['exec_price'] for t in h['trades']) / len(h['trades']), 1)}c, "
                  f"realized win rate {_f(sum(1 for t in h['trades'] if t['resolution'] == t['side']) / len(h['trades']), 1, True)}.", ""]
        L += ["### Economics — TRAIN (in-sample, descriptive)", "",
              md_table(ARM_HEADERS, _arm_rows(r, "train")), "",
              "### Economics — HOLDOUT (the verdict)", "",
              md_table(ARM_HEADERS, _arm_rows(r, "holdout")), "",
              "### Verdicts (HOLDOUT)", ""]
        for arm, rec in r["arms"].items():
            v, reasons = rec["verdict"]
            L.append(f"- **{arm} — {v}**: " + "; ".join(reasons))
        if h:
            days = {t["timestamp"][:10] for t in h["trades"]}
            L += ["", f"Effective independence: {h['econ']['n']} holdout trades on {len(days)} distinct "
                  "settlement days. Strikes on one asset resolve off ONE spot print, so the day, "
                  "not the trade, is the independent unit.",
                  f"Robustness: net without top family `{h['robust']['top_family']}` = "
                  f"{_f(h['robust']['net_ex_top_family'], 0)}c; net without top "
                  f"{h['robust']['top_trades_removed']} trade(s) = {_f(h['robust']['net_ex_top_trades'], 0)}c.", ""]
    if not any_cls:
        L += ["No Track B class reached the family floor.", ""]
    v, why = track_verdict(out, "B")
    L += [f"## TRACK B VERDICT: {v}", "", why]
    return "\n".join(L)


def render_summary(out: dict, csv_fp: str, universe_fp: str) -> tuple[str, str]:
    va, wa = track_verdict(out, "A")
    vb, wb = track_verdict(out, "B")
    survivors = []
    stat_only = []
    for cls, r in sorted(out["results"].items()):
        base = r["arms"][BASELINE[r["track"]]].get("holdout")
        for arm, rec in r["arms"].items():
            v = rec["verdict"][0]
            if v == "PASS":
                survivors.append(f"{arm} in {cls}")
            h = rec.get("holdout")
            if (h and base and v != "PASS" and arm != BASELINE[r["track"]]
                    and h["stats"]["brier"] is not None and base["stats"]["brier"] is not None
                    and h["stats"]["brier"] < base["stats"]["brier"] and h["econ"]["n"] > 0
                    and h["econ"]["net"] <= 0):
                stat_only.append(f"{arm} in {cls}")
    results_fp = sha(json.dumps({
        cls: {arm: [rec["verdict"][0], (rec.get("holdout") or {}).get("econ", {}).get("net")]
              for arm, rec in r["arms"].items()} for cls, r in out["results"].items()
    }, sort_keys=True))
    L = ["# MARKTANGLE-2 summary", "",
         md_table(["track", "verdict", "why"], [["A", va, wa], ["B", vb, wb]]), "",
         "## Arms surviving on untouched holdout", "",
         ("- " + "\n- ".join(survivors)) if survivors else "- none", "",
         "## Statistical but not economic", "",
         "Arms whose holdout Brier beats the base rate while net P&L is non-positive — "
         "forecastability the price already carries:", "",
         ("- " + "\n- ".join(stat_only)) if stat_only else "- none", "",
         "## Per-class primary verdicts", "",
         md_table(["class", "track", "primary", "verdict", "holdout trades", "net c", "EV/trade c", "mirror EV c"],
                  [[cls, r["track"], PRIMARY[r["track"]], r["arms"][PRIMARY[r["track"]]]["verdict"][0],
                    (r["arms"][PRIMARY[r["track"]]].get("holdout") or {"econ": {"n": 0}})["econ"]["n"],
                    _f((r["arms"][PRIMARY[r["track"]]].get("holdout") or {"econ": {"net": None}})["econ"]["net"], 0),
                    _f((r["arms"][PRIMARY[r["track"]]].get("holdout") or {"econ": {"ev": None}})["econ"]["ev"]),
                    _f((r["arms"][PRIMARY[r["track"]]].get("holdout") or {"mirror": {"econ": {"ev": None}}})["mirror"]["econ"]["ev"])]
                   for cls, r in sorted(out["results"].items())]), "",
         "## Exact next gate", "",
         "- A PASS authorizes NOTHING live. The next gate for a passing track is a prospective "
         "paper/twin experiment registered in Experiment OS with its own pre-registered floors "
         "(`paper_to_live_canary_<track>` on the frozen v1 contract), never a live canary.",
         "- A FAIL retires that track's thesis (§19). No broader classes, no re-read bars.",
         "- A HOLD is no result: the named floor is the thing to satisfy (forward collection or "
         "price-data reconstruction), and the instrument is re-run unchanged.", "",
         "## Reproducibility", "",
         md_table(["item", "value"], [
             ["code SHA", out["code_sha"]],
             ["data cutoff (latest settled close)", _iso(out["data_cutoff"])],
             ["universe fingerprint (sha256 of ticker,close,result)", universe_fp],
             ["trades fingerprint (sha256 of MARKTANGLE_2_TRADES.csv)", csv_fp],
             ["results fingerprint (sha256 of per-arm verdicts + holdout net)", results_fp],
             ["split", f"chronological per class, first {TRAIN_FRAC:.0%} of decision times TRAIN"],
             ["decision offset", f"T-{DECISION_MIN}m"],
             ["fee model", "worst-case Kalshi taker, ceil(7 p (1-p)) c/contract, entry only"],
             ["slippage", f"{SLIPPAGE_C}c/contract; spread screen <= {MAX_SPREAD_C}c"],
             ["edge bar", f">= {EDGE_BAR_C}c net"],
             ["floors", f"train >= {FLOOR_TRAIN_POINTS} points; holdout >= {FLOOR_HOLDOUT_TRADES} trades; "
                        f"price coverage >= {PRICE_COVERAGE_FLOOR:.0%}"],
             ["mirror delta", f">= {MIRROR_DELTA_C}c/trade"],
             ["shrinkage / ridge", f"m={SHRINK_M}; family ridge={RIDGE_FAMILY}; slope ridge={RIDGE_SLOPE}"],
             ["buckets", f"Track A k<= {MAX_K_A - 1} then pooled; Track B {DURATION_BUCKETS_B}; z bins {Z_BINS}"],
             ["vol window", f"{VOL_WINDOW_DAYS} daily closes, min {MIN_VOL_RETURNS} returns; |z| cap {Z_CAP}"],
             ["config", json.dumps(out["config"], sort_keys=True)],
             ["elapsed", f"{out['elapsed_s']:.0f}s"],
         ])]
    return "\n".join(L), results_fp


def emit_package(out: dict) -> dict:
    csv_text = trades_csv(out["trades"])
    csv_fp = sha(csv_text)
    universe = sorted(f"{m['ticker']},{m['close']},{m['result']}"
                      for rows in out["markets_by_series"].values() for m in rows)
    universe_fp = sha("\n".join(universe))
    summary, results_fp = render_summary(out, csv_fp, universe_fp)
    section("MARKTANGLE_2_DATA_REPORT.md", render_data_report(out))
    section("MARKTANGLE_2_TRACK_A.md", render_track_a(out))
    section("MARKTANGLE_2_TRACK_B.md", render_track_b(out))
    section("MARKTANGLE_2_SUMMARY.md", summary)
    section("MARKTANGLE_2_TRADES.csv", csv_text)
    print(f"FINGERPRINTS trades={csv_fp} universe={universe_fp} results={results_fp}")
    return {"trades": csv_fp, "universe": universe_fp, "results": results_fp}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--series", default=DEFAULT_SERIES,
                    help="comma-separated series always pulled (structurally classified like "
                         "everything else)")
    ap.add_argument("--discover-pages", type=int, default=25,
                    help="pages of /events?status=open to enumerate extra series from; "
                         "only structurally classifiable series are pulled. 0 disables")
    ap.add_argument("--pages", type=int, default=60,
                    help="settled pages per series (1000 each). Run 1 hit the old cap of 25 on "
                         "every crypto series; the budget is acquisition depth, not a threshold")
    ap.add_argument("--min-vol", type=float, default=0.0)
    ap.add_argument("--max-fetch", type=int, default=6000,
                    help="cap on Kalshi candle fetches (holdout first, then train)")
    ap.add_argument("--no-spot", action="store_true", help="skip Coinbase (Track B z unavailable)")
    args = ap.parse_args(argv)

    series = [s.strip().upper() for s in args.series.split(",") if s.strip()]
    if args.discover_pages > 0:
        found = m1.discover_series(args.discover_pages, args.min_vol)
        extra = [s for s in found if classify_series(s) and s not in series]
        print(f"discovered {len(found)} series on the live board; {len(extra)} structurally "
              f"classifiable and not already listed: {', '.join(extra[:60])}")
        series.extend(extra)
    markets_by_series: dict[str, list[dict]] = {}
    for s in series:
        got = fetch_settled(s, args.pages, args.min_vol)
        print(f"  {s}: {len(got)} settled markets")
        if got:
            markets_by_series[s] = got
    if not markets_by_series:
        print("no data — nothing to say. This is not a negative result.")
        return 0
    config = {"series": series, "pages": args.pages, "min_vol": args.min_vol,
              "max_fetch": args.max_fetch, "spot": not args.no_spot}
    spot_loader = (lambda a, s, e: None) if args.no_spot else load_spot
    out = run(markets_by_series, spot_loader, decision_quote, args.max_fetch,
              os.environ.get("OPS_CODE_SHA", "unknown"), config)
    emit_package(out)
    return 0


if __name__ == "__main__":  # pragma: no cover - ops channel calls main()
    raise SystemExit(main())
