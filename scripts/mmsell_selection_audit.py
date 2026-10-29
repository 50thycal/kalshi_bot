"""MMSELL SELECTION-BIAS AUDIT — does the tail-sell edge hold across EVERY market the scan saw?

Pre-registered in docs/MMSELL_SELECTION_BIAS_AUDIT.md (2026-10-29, catalog research plan study 1,
WS-023). Every constant marked "pre-registered" below is frozen there; changing one is a new probe.

Population: every ticker in `mmsell_candidate_ticks` (the control scan's in-band tape, entered or
not) whose Kalshi result is yes/no. Primary entry = the ticker's FIRST candidate tick; the
hours-to-expiration cut uses the first tick inside each HTE bucket. Each market is scored per
contract in cents at two prices:

  taker  sell YES into the bid (buy NO at 100 - yes_bid): yes_bid - 100*Y - taker fee at
         q = min(SIZE, depth_at_best_bid) contracts. Realizable floor; no fill assumption.
  maker  rest at the no bid (sell YES at yes_ask), FILL ASSUMED: yes_ask - 100*Y - maker fee.
         Upper bound — resting fills are adversely selected.
  proxy  maker, filled only when a later tick shows yes_bid >= entry yes_ask (diagnostic only).

Unit of independence: the event (cluster-robust SE). Cells: series, strike structure, price band,
HTE bucket, market type / settle mode, plus the auditable skip rules. XOS-000046 and XOS-000047
series are printed without a label: their frozen forward tests own those decisions.

No lookahead: the entry uses only the entry tick's quote; the settlement result only scores.
Provenance: prices from the tape, outcomes from Kalshi public REST (D1 option (a)), "entered"
labels from live_orders / paper_trades — never paper P&L as a yardstick (XOS-000031).

Read-only: DATABASE_URL_RO in a read-only transaction plus public Kalshi REST. Places no orders,
writes no tables. Stdlib + psycopg (+ sibling scripts xvenue_leadlag, mmsell_market_types).

Usage: {"type":"script","name":"mmsell_selection_audit","args":[],"id":"rl-sba-run1-<date>"}
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=1500000 "
    "-c idle_in_transaction_session_timeout=1500000"
)
BATCH = 100
PAUSE_S = 0.08

# --- pre-registered constants (docs/MMSELL_SELECTION_BIAS_AUDIT.md) --------------------------
SIZE = 10                          # nominal clip; fill capped at depth_at_best_bid
TAKER_COEFF = 0.07
MAKER_COEFF = 0.0175
LIVE_CELL_MAX_ASK = 7              # the live rule's maxyes
MAXYES_REMOVED = (8, 10)           # ask band the maxyes=7 ceiling drops inside the 5-10 mid band
BANDS = (("<=7", 0, 7), ("8-10", 8, 10), ("11-15", 11, 15), ("16-20", 16, 20),
         ("21-30", 21, 30), ("31+", 31, 100))
HTE_BUCKETS = (("<=0", None, 0.0), ("0-1", 0.0, 1.0), ("1-3", 1.0, 3.0), ("3-6", 3.0, 6.0),
               ("6-24", 6.0, 24.0), ("24-72", 24.0, 72.0), (">72", 72.0, None))
MIN_MARKETS, MIN_EVENTS = 100, 30
SIZE_SAFE_EVENTS, SIZE_SAFE_DEPTH, SIZE_SAFE_DEPTH_SHARE = 60, 10, 0.50
MONTH_MIN_MARKETS, MONTH_POS_SHARE = 30, 2.0 / 3.0
Z_POP, Z_STRUCT, Z_SERIES = 1.96, 2.58, 3.29
P1_MIN_CENTS, P1_KILL_EVENTS = 1.0, 300
P3_MARGIN = 2.0
CRYPTO_SUBSTRINGS = ("BTC", "ETH", "SOL", "DOGE", "XRP", "CRYPTO")   # XOS-000009's skip list
LIVE_SKIP_PREFIXES = ("KXNFLSPREAD", "KXATP", "KXWTA", "KXITF", "KXBTCD")  # mmsell_live_skip_series
LIVE_MIN_TIER = "graduated"
#: Exact-match fallback when a series' Kalshi category cannot be read (scripts/mmsell_canary_slices).
CRYPTO_SERIES_FALLBACK = frozenset({
    "KXBTC", "KXBTCD", "KXBTCMAXMON", "KXBTCMINMON", "KXBTCRANGE",
    "KXETH", "KXETHD", "KXETHMAXMON", "KXETHMINMON", "KXETHRANGE",
    "KXSOL", "KXSOLD", "KXDOGE", "KXDOGED", "KXXRP", "KXXRPD",
})
FORWARD_TESTS = {  # series whose decision a frozen forward test owns: numbers, never a label
    "KXMLBTOTAL": "XOS-000046", "KXMLBSPREAD": "XOS-000046",
    "KXNFLTOTAL": "XOS-000047", "KXITFMATCH": "XOS-000047", "KXITFWMATCH": "XOS-000047",
}


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_mmsell_selection_audit.py)


def taker_fee_per_ct(no_price_c: int, q: int) -> float:
    """Kalshi taker fee per contract, cents, for one order of q contracts at NO price (cents)."""
    q = max(int(q), 1)
    p = min(max(no_price_c / 100.0, 0.0), 1.0)
    return math.ceil(TAKER_COEFF * q * p * (1.0 - p) * 100 - 1e-9) / q


def maker_fee_per_ct(no_price_c: int) -> float:
    """Maker fee per contract, cents, paper-engine convention (published rate, no ceiling)."""
    p = min(max(no_price_c / 100.0, 0.0), 1.0)
    return MAKER_COEFF * p * (1.0 - p) * 100


def fill_qty(depth_at_best_bid) -> int:
    """Taker clip: SIZE capped at the YES-bid depth; 1 when depth is unknown or empty."""
    try:
        d = int(depth_at_best_bid)
    except (TypeError, ValueError):
        return 1
    return max(1, min(SIZE, d))


def taker_pnl(yes_bid, yes_won: bool, depth) -> float | None:
    """Sell YES into the bid: + yes_bid if NO settles, yes_bid - 100 if YES, less the taker fee."""
    if yes_bid is None or yes_bid <= 0 or yes_bid >= 100:
        return None
    return yes_bid - (100 if yes_won else 0) - taker_fee_per_ct(100 - yes_bid, fill_qty(depth))


def maker_pnl(yes_ask, yes_won: bool) -> float | None:
    """Sell YES at the ask (rest at the no bid), fill assumed, less the maker fee."""
    if yes_ask is None or yes_ask <= 0 or yes_ask >= 100:
        return None
    return yes_ask - (100 if yes_won else 0) - maker_fee_per_ct(100 - yes_ask)


def proxy_pnl(yes_ask, later_max_bid, yes_won: bool) -> float | None:
    """Maker with a quote-crossing fill proxy: 0 unless a later tick's bid reached our ask."""
    m = maker_pnl(yes_ask, yes_won)
    if m is None:
        return None
    return m if (later_max_bid is not None and later_max_bid >= yes_ask) else 0.0


def price_band(yes_ask) -> str | None:
    if yes_ask is None:
        return None
    for name, lo, hi in BANDS:
        if lo <= yes_ask <= hi:
            return name
    return None


def hte_bucket(hte) -> str:
    if hte is None:
        return "na"
    for name, lo, hi in HTE_BUCKETS:
        if (lo is None or hte > lo) and (hi is None or hte <= hi):
            return name
    return "na"


def strike_struct(strike_type, floor_strike, cap_strike) -> str:
    return (f"{(strike_type or 'none').lower()}|"
            f"{'F' if floor_strike is not None else '-'}{'C' if cap_strike is not None else '-'}")


def event_of(ticker: str, event_ticker: str | None) -> str:
    if event_ticker:
        return event_ticker
    parts = (ticker or "").rsplit("-", 1)
    return parts[0] if len(parts) == 2 else (ticker or "")


def crypto_substring_hit(series: str) -> bool:
    """XOS-000009's rule exactly as the arms apply it: a SUBSTRING of the upper-cased series."""
    s = (series or "").upper()
    return any(tok in s for tok in CRYPTO_SUBSTRINGS)


def is_crypto_category(category: str) -> bool:
    return (category or "").strip().lower().startswith("crypto")


def live_paused(series: str) -> bool:
    s = (series or "").upper()
    return any(s.startswith(p) for p in LIVE_SKIP_PREFIXES)


def tier_of(series: str, manifest: dict[str, str], classify) -> str:
    """kalshi_bot.registry.state_of, re-derived stdlib-only: barred > unclassified -> identified >
    no manifest row -> in_review > the row's state (longest-prefix row)."""
    s = (series or "").upper()
    best, best_len = None, -1
    for prefix, state in manifest.items():
        if s.startswith(prefix) and len(prefix) > best_len:
            best, best_len = state, len(prefix)
    if best == "barred":
        return "barred"
    if classify(s) == ("unclassified", "unknown"):
        return "identified"
    return best if best is not None else "in_review"


def cluster_stats(rows: list[dict], key: str) -> dict | None:
    """Mean of rows[key] per market with an event-clustered standard error."""
    vals = [(r["event"], r[key]) for r in rows if r.get(key) is not None]
    n = len(vals)
    if n == 0:
        return None
    mean = sum(v for _, v in vals) / n
    resid: dict[str, float] = defaultdict(float)
    for ev, v in vals:
        resid[ev] += v - mean
    g = len(resid)
    se = (math.sqrt(g / (g - 1) * sum(x * x for x in resid.values())) / n) if g > 1 else float("nan")
    return {"n": n, "events": g, "mean": mean, "se": se}


def lcb(s: dict | None, z: float) -> float | None:
    return None if s is None or math.isnan(s["se"]) else s["mean"] - z * s["se"]


def ucb(s: dict | None, z: float) -> float | None:
    return None if s is None or math.isnan(s["se"]) else s["mean"] + z * s["se"]


def depth_profile(rows: list[dict]) -> tuple[float | None, float | None]:
    ds = sorted(r["depth_bid"] for r in rows if r.get("depth_bid") is not None)
    if not ds:
        return None, None
    med = ds[len(ds) // 2] if len(ds) % 2 else (ds[len(ds) // 2 - 1] + ds[len(ds) // 2]) / 2
    return med, sum(1 for d in ds if d >= SIZE_SAFE_DEPTH) / len(ds)


def month_stability(rows: list[dict]) -> tuple[int, int]:
    """(months with >= MONTH_MIN_MARKETS taker-scored markets, how many of them have mean > 0)."""
    by: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("taker") is not None:
            by[r["month"]].append(r["taker"])
    big = [v for v in by.values() if len(v) >= MONTH_MIN_MARKETS]
    return len(big), sum(1 for v in big if sum(v) / len(v) > 0)


def label_cell(rows: list[dict], z: float, series: str | None = None) -> str:
    """The pre-registered cell label (docs/MMSELL_SELECTION_BIAS_AUDIT.md §5), first match wins."""
    if series is not None and series in FORWARD_TESTS:
        return f"({FORWARD_TESTS[series]})"
    mk = cluster_stats(rows, "maker")
    tk = cluster_stats(rows, "taker")
    if mk is None or mk["n"] < MIN_MARKETS or mk["events"] < MIN_EVENTS:
        return "THIN"
    m_ucb, m_lcb, t_lcb = ucb(mk, z), lcb(mk, z), lcb(tk, z)
    if m_ucb is not None and m_ucb < 0:
        return "CUT"
    if t_lcb is not None and t_lcb > 0:
        med, share = depth_profile(rows)
        months, pos = month_stability(rows)
        if (tk["events"] >= SIZE_SAFE_EVENTS and med is not None and med >= SIZE_SAFE_DEPTH
                and share is not None and share >= SIZE_SAFE_DEPTH_SHARE
                and months > 0 and pos >= MONTH_POS_SHARE * months):
            return "SIZE-SAFE"
        return "HOLDS"
    if m_lcb is not None and m_lcb > 0:
        return "MAKER-ONLY"
    if mk["mean"] < 0:
        return "CUT-LEANING"
    return "NO-CALL"


def capacity_usd_30d(rows: list[dict], span_days: float) -> float | None:
    """Taker dollars per 30 days at SIZE, fill capped at depth, over markets with known depth."""
    tot, seen = 0.0, False
    for r in rows:
        if r.get("taker") is not None and r.get("depth_bid") is not None:
            tot += min(SIZE, max(int(r["depth_bid"]), 0)) * r["taker"] / 100.0
            seen = True
    if not seen or span_days <= 0:
        return None
    return tot * 30.0 / span_days


# ---------------------------------------------------------------------------------------------
# data

TAPE_SQL = """
WITH t AS (
  SELECT market_ticker, captured_at, series, hours_to_close, hours_to_expiration,
         strike_type, floor_strike, cap_strike, depth_at_best_bid, depth_at_best_ask,
         yes_bid, yes_ask, volume,
         CASE WHEN hours_to_expiration IS NULL THEN 'na'
              WHEN hours_to_expiration <= 0 THEN '<=0'
              WHEN hours_to_expiration <= 1 THEN '0-1'
              WHEN hours_to_expiration <= 3 THEN '1-3'
              WHEN hours_to_expiration <= 6 THEN '3-6'
              WHEN hours_to_expiration <= 24 THEN '6-24'
              WHEN hours_to_expiration <= 72 THEN '24-72'
              ELSE '>72' END AS hb,
         max(yes_bid) OVER (PARTITION BY market_ticker ORDER BY captured_at DESC
                            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS later_max_bid
    FROM mmsell_candidate_ticks
   WHERE captured_at >= %(since)s
)
SELECT DISTINCT ON (t.market_ticker, t.hb)
       t.market_ticker, t.captured_at, t.series, t.hours_to_close, t.hours_to_expiration,
       t.strike_type, t.floor_strike, t.cap_strike, t.depth_at_best_bid, t.depth_at_best_ask,
       t.yes_bid, t.yes_ask, t.volume, t.hb, t.later_max_bid, m.event_ticker
  FROM t LEFT JOIN mmsell_settlement_meta m ON m.market_ticker = t.market_ticker
 ORDER BY t.market_ticker, t.hb, t.captured_at
"""

LIVE_SQL = """
SELECT o.market_ticker, bool_or(f.kalshi_order_id IS NOT NULL) AS filled
  FROM live_orders o
  LEFT JOIN fills f ON f.kalshi_order_id = o.kalshi_order_id
 WHERE o.strategy ILIKE %(pat)s AND o.action = 'buy'
 GROUP BY o.market_ticker
"""

PAPER_SQL = "SELECT DISTINCT market_ticker FROM paper_trades WHERE strategy = %(tag)s"

CAPDIAG_SQL = """
SELECT count(*) AS buckets, count(*) FILTER (WHERE n >= %(cap)s) AS at_cap
  FROM (SELECT date_trunc('hour', captured_at)
               + floor(extract(minute FROM captured_at) / 20) * interval '20 min' AS b,
               count(*) AS n
          FROM mmsell_candidate_ticks WHERE captured_at >= %(since)s GROUP BY 1) x
"""


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def fetch_markets(tickers: list[str]) -> dict[str, dict]:
    """Market objects by ticker: batched live endpoint, then the batched historical archive."""
    import xvenue_leadlag as xl  # browser UA + retries; deferred so tests need no network
    out: dict[str, dict] = {}
    todo = sorted(set(tickers))
    for base in (f"{KALSHI}/markets", f"{KALSHI}/historical/markets"):
        missing = [t for t in todo if t not in out or not _result(out[t])]
        for i in range(0, len(missing), BATCH):
            chunk = missing[i:i + BATCH]
            q = urllib.parse.urlencode({"tickers": ",".join(chunk), "limit": len(chunk)})
            page = xl._get(f"{base}?{q}") or {}
            for m in page.get("markets") or []:
                t = m.get("ticker")
                if t in chunk and (t not in out or _result(m)):
                    out[t] = m
            time.sleep(PAUSE_S)
    return out


def _result(m: dict) -> str:
    return (m.get("result") or "").strip().lower()


def fetch_categories(series: list[str]) -> dict[str, str]:
    import xvenue_leadlag as xl
    out = {}
    for s in sorted(set(series)):
        data = xl._get(f"{KALSHI}/series/{urllib.parse.quote(s, safe='')}") or {}
        out[s] = (data.get("series") or {}).get("category") or ""
        time.sleep(PAUSE_S)
    return out


def load_manifest() -> dict[str, str]:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "kalshi_bot", "registry", "series_manifest.json")
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except OSError:
        return {}
    return {str(r.get("series", "")).upper(): str(r.get("state", ""))
            for r in doc.get("series", ()) if r.get("series")}


# ---------------------------------------------------------------------------------------------
# reporting


def _f(x, spec="{:+.2f}") -> str:
    return "  n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else spec.format(x)


HDR = (f"  {'cell':30s} {'mkts':>6} {'evts':>5} {'taker':>7} {'±se':>5} {'maker':>7} {'±se':>5}"
       f" {'proxy':>6} {'fill%':>5} {'dep50':>5} {'d>=10':>5} {'$/30d':>8}  label")


def row_line(name: str, rows: list[dict], z: float, span: float, label: str) -> str:
    tk, mk, px = (cluster_stats(rows, k) for k in ("taker", "maker", "proxy"))
    filled = [r for r in rows if r.get("proxy") is not None]
    fill = (sum(1 for r in filled if r["proxy_filled"]) / len(filled)) if filled else None
    med, share = depth_profile(rows)
    cap = capacity_usd_30d(rows, span)
    return (f"  {name[:30]:30s} {len(rows):6d} {(mk or tk or {'events': 0})['events']:5d}"
            f" {_f(tk and tk['mean']):>7} {_f(tk and tk['se'], '{:.2f}'):>5}"
            f" {_f(mk and mk['mean']):>7} {_f(mk and mk['se'], '{:.2f}'):>5}"
            f" {_f(px and px['mean']):>6} {_f(fill and 100 * fill, '{:.0f}'):>5}"
            f" {_f(med, '{:.0f}'):>5} {_f(share and 100 * share, '{:.0f}'):>5}"
            f" {_f(cap, '{:+.0f}'):>8}  {label}")


def table(title: str, groups: dict[str, list[dict]], z: float, span: float, *,
          series_cells: bool = False, order: list[str] | None = None, min_print: int = 0) -> None:
    print(f"\n== {title}  (z={z})")
    print(HDR)
    keys = order or sorted(groups, key=lambda k: -(cluster_stats(groups[k], "maker") or
                                                    {"mean": -1e9})["mean"])
    thin = 0
    for k in keys:
        rows = groups.get(k) or []
        if not rows:
            continue
        lab = label_cell(rows, z, series=k if series_cells else None)
        if len(rows) < min_print:
            thin += 1
            continue
        print(row_line(k, rows, z, span, lab))
    if thin:
        print(f"  ... {thin} more cells under {min_print} markets not printed (all THIN)")


def compare(title: str, a: list[dict], b: list[dict], key: str = "maker") -> dict | None:
    sa, sb = cluster_stats(a, key), cluster_stats(b, key)
    if not sa or not sb:
        print(f"  {title}: n/a")
        return None
    d = sa["mean"] - sb["mean"]
    se = math.sqrt(sa["se"] ** 2 + sb["se"] ** 2) if not (math.isnan(sa["se"]) or
                                                        math.isnan(sb["se"])) else float("nan")
    print(f"  {title}: {sa['n']} vs {sb['n']} mkts, {key} {sa['mean']:+.2f} vs {sb['mean']:+.2f}"
          f" -> diff {d:+.2f} ± {se:.2f}  95% CI [{d - 1.96 * se:+.2f}, {d + 1.96 * se:+.2f}]")
    return {"diff": d, "se": se}


def rule_audit(name: str, removed: list[dict]) -> str:
    tk, mk = cluster_stats(removed, "taker"), cluster_stats(removed, "maker")
    if not mk:
        verdict = "n/a (nothing removed)"
    elif lcb(tk, Z_POP) is not None and lcb(tk, Z_POP) > 0:
        verdict = "THROWS AWAY PROFIT (taker LCB > 0)"
    elif lcb(mk, Z_POP) is not None and lcb(mk, Z_POP) > 0:
        verdict = "maker-only, weak (maker LCB > 0, taker LCB <= 0)"
    else:
        verdict = "ok — removed set does not pay"
    print(f"  {name:44s} mkts {len(removed):6d} evts {(mk or {'events': 0})['events']:5d}"
          f" taker {_f(tk and tk['mean'])} [{_f(lcb(tk, Z_POP))},{_f(ucb(tk, Z_POP))}]"
          f" maker {_f(mk and mk['mean'])} [{_f(lcb(mk, Z_POP))},{_f(ucb(mk, Z_POP))}]  {verdict}")
    return verdict


# ---------------------------------------------------------------------------------------------


def build_rows(raw: list[tuple], markets: dict[str, dict], classify) -> tuple[list[dict], dict]:
    """Score every tape row with a yes/no result. Returns (rows, exclusion counts)."""
    excl: dict[str, int] = defaultdict(int)
    out = []
    for (ticker, cap_at, series, _htc, hte, st, fl, cp, dbid, _dask, ybid, yask, _vol, hb,
         later_max, ev) in raw:
        m = markets.get(ticker)
        res = _result(m) if m else ""
        if res not in ("yes", "no"):
            excl["unsettled/void" if m else "not found"] += 1
            continue
        y = res == "yes"
        ser = (series or ticker.split("-", 1)[0]).upper()
        mtype, mode = classify(ser)
        r = {
            "ticker": ticker, "series": ser, "event": event_of(ticker, ev), "at": cap_at,
            "month": cap_at.strftime("%Y-%m") if cap_at else "?", "hte": hte, "hb": hb,
            "struct": strike_struct(st, fl, cp), "floor": fl, "depth_bid": dbid,
            "yes_bid": ybid, "yes_ask": yask, "band": price_band(yask), "won_yes": y,
            "mtype": mtype, "mode": mode,
            "taker": taker_pnl(ybid, y, dbid), "maker": maker_pnl(yask, y),
            "proxy": proxy_pnl(yask, later_max, y),
            "proxy_filled": later_max is not None and yask is not None and later_max >= yask,
            "mid": (ybid + yask) / 2.0 if ybid is not None and yask is not None else None,
        }
        out.append(r)
    return out, excl


def group(rows: list[dict], key) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        k = key(r)
        if k is not None:
            g[k].append(r)
    return g


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="2026-07-01T00:00:00+00:00")
    ap.add_argument("--max-tickers", type=int, default=0, help="0 = all (bounds API cost)")
    ap.add_argument("--series-min-print", type=int, default=MIN_MARKETS)
    ap.add_argument("--no-capdiag", action="store_true")
    args = ap.parse_args(argv)

    import mmsell_market_types as mt
    import psycopg

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    print("MMSELL selection-bias audit — every candidate the scan saw, entered or not")
    print("pre-registered: docs/MMSELL_SELECTION_BIAS_AUDIT.md (2026-10-29)\n")
    t0 = time.time()
    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        cur.execute(TAPE_SQL, {"since": args.since})
        raw = cur.fetchall()
        print(f"tape rows (ticker x HTE-bucket first ticks): {len(raw)}  [{time.time() - t0:.0f}s]")
        cur.execute(LIVE_SQL, {"pat": "%mmsell%"})
        live = {t: bool(f) for t, f in cur.fetchall()}
        cur.execute(PAPER_SQL, {"tag": "mmsell10"})
        paper10 = {t for (t,) in cur.fetchall()}
        cap = None
        if not args.no_capdiag:
            cur.execute(CAPDIAG_SQL, {"cap": 400, "since": args.since})
            cap = cur.fetchone()
    print(f"live mmsell tickers ordered: {len(live)} (filled {sum(live.values())});"
          f" mmsell10 paper tickers: {len(paper10)}")
    if cap:
        print(f"capture-cap diagnostic: {cap[1]} of {cap[0]} 20-min buckets hold >= 400 ticks"
              " (cap may have truncated those cycles)")

    tickers = sorted({r[0] for r in raw})
    if args.max_tickers:
        tickers = tickers[:args.max_tickers]
        keep = set(tickers)
        raw = [r for r in raw if r[0] in keep]
    t1 = time.time()
    markets = fetch_markets(tickers)
    print(f"Kalshi markets fetched: {len(markets)} of {len(tickers)} tickers"
          f" [{time.time() - t1:.0f}s]")

    rows, excl = build_rows(raw, markets, mt.classify)
    # Primary entry = the ticker's earliest tick across its HTE-bucket firsts.
    first: dict[str, dict] = {}
    for r in rows:
        if r["ticker"] not in first or r["at"] < first[r["ticker"]]["at"]:
            first[r["ticker"]] = r
    prim = list(first.values())
    for r in prim:
        r["live"] = r["ticker"] in live
        r["live_filled"] = live.get(r["ticker"], False)
        r["paper10"] = r["ticker"] in paper10
    ats = [r["at"] for r in prim if r["at"]]
    span = (max(ats) - min(ats)).total_seconds() / 86400 if ats else 0.0
    print(f"scored: {len(prim)} markets (primary entry), {len(rows)} HTE-bucket entries;"
          f" excluded {dict(excl)}; span {span:.1f} days"
          f" ({min(ats):%Y-%m-%d} .. {max(ats):%Y-%m-%d})" if ats else "")
    if not prim:
        print("\nVERDICT: HOLD — nothing scored")
        return 0

    cell = [r for r in prim if r["yes_ask"] is not None and r["yes_ask"] <= LIVE_CELL_MAX_ASK]

    # --- population ----------------------------------------------------------------------
    print("\n== POPULATION (primary entry, ¢/contract; taker=realizable floor, maker=fill-assumed"
          " upper bound, proxy=maker with quote-crossing fill)")
    print(HDR)
    print(row_line("all bands", prim, Z_POP, span, ""))
    print(row_line(f"live cell ask<={LIVE_CELL_MAX_ASK}", cell, Z_POP, span, ""))
    for mo, rs in sorted(group(cell, lambda r: r["month"]).items()):
        print(row_line(f"  live cell {mo}", rs, Z_POP, span, ""))

    # --- predictions ---------------------------------------------------------------------
    mk, tk = cluster_stats(cell, "maker"), cluster_stats(cell, "taker")
    print("\n== PRE-REGISTERED PREDICTIONS")
    m_l = lcb(mk, Z_POP)
    if mk and mk["mean"] >= P1_MIN_CENTS and m_l is not None and m_l > 0:
        p1 = "PASS"
    elif mk and mk["mean"] <= 0 and mk["events"] >= P1_KILL_EVENTS:
        p1 = "KILL"
    else:
        p1 = "HOLD"
    print(f"  P1 edge across the population (live cell, maker): {_f(mk and mk['mean'])}"
          f" LCB95 {_f(m_l)}, {mk and mk['events']} events -> {p1}")
    t_l, t_u = lcb(tk, Z_POP), ucb(tk, Z_POP)
    p2 = "PASS" if t_l is not None and t_l > 0 else "FAIL" if t_u is not None and t_u < 0 else "HOLD"
    print(f"  P2 survives crossing (live cell, taker): {_f(tk and tk['mean'])}"
          f" CI95 [{_f(t_l)}, {_f(t_u)}] -> {p2}")
    c = compare("P3 live-traded vs never-live (live cell, maker)",
                [r for r in cell if r["live"]], [r for r in cell if not r["live"]])
    if c is None or math.isnan(c["se"]):
        p3 = "HOLD"
    elif c["diff"] < -P3_MARGIN and c["diff"] + 1.96 * c["se"] < 0:
        p3 = "FAIL (live entry selects adversely)"
    else:
        p3 = "PASS (representative)"
    print(f"     -> {p3}")
    compare("    diag: live-filled vs live-ordered-unfilled (maker)",
            [r for r in cell if r["live_filled"]], [r for r in cell if r["live"] and
                                                     not r["live_filled"]])
    compare("    diag: mmsell10-paper-entered vs not (maker)",
            [r for r in cell if r["paper10"]], [r for r in cell if not r["paper10"]])

    # --- P4 skip rules -------------------------------------------------------------------
    print("\n== P4 SKIP RULES (removed set, primary entry, 95% CI)")
    crypto_series = sorted({r["series"] for r in prim if crypto_substring_hit(r["series"])})
    cats = fetch_categories(crypto_series) if crypto_series else {}
    manifest = load_manifest()
    for s in crypto_series:  # category read failed: fall back to the exact crypto series list
        if not cats.get(s):
            cats[s] = "Crypto (list)" if s in CRYPTO_SERIES_FALLBACK else "? (no category)"
    for r in prim:
        r["crypto_hit"] = r["series"] in cats
        r["crypto_true"] = r["crypto_hit"] and is_crypto_category(cats.get(r["series"], ""))
        r["tier"] = tier_of(r["series"], manifest, mt.classify)
    p4 = {}
    p4["crypto skip, true crypto (live cell)"] = rule_audit(
        "crypto skip=, category Crypto (live cell)",
        [r for r in cell if r["crypto_true"]])
    p4["crypto skip, NOT crypto (live cell)"] = rule_audit(
        "crypto skip=, NOT crypto — XOS-000009 (cell)",
        [r for r in cell if r["crypto_hit"] and not r["crypto_true"]])
    rule_audit("crypto skip=, NOT crypto — XOS-000009 (all)",
               [r for r in prim if r["crypto_hit"] and not r["crypto_true"]])
    p4["live pause (live cell)"] = rule_audit(
        "live pause mmsell_live_skip_series (cell)", [r for r in cell if live_paused(r["series"])])
    p4["live tier (live cell)"] = rule_audit(
        f"live tier < {LIVE_MIN_TIER} (live cell)",
        [r for r in cell if r["tier"] != LIVE_MIN_TIER])
    p4["maxyes=7 ceiling"] = rule_audit(
        "maxyes=7 drops ask 8-10 in the 5-10 mid band",
        [r for r in prim if r["yes_ask"] is not None and r["mid"] is not None
         and MAXYES_REMOVED[0] <= r["yes_ask"] <= MAXYES_REMOVED[1] and r["mid"] <= 10])
    fp = defaultdict(int)
    for r in prim:
        if r["crypto_hit"] and not r["crypto_true"]:
            fp[f"{r['series']} [{cats.get(r['series']) or '?'}]"] += 1
    print("  XOS-000009 false positives (series [category]: markets): "
          + (", ".join(f"{k}: {v}" for k, v in sorted(fp.items(), key=lambda kv: -kv[1]))
             or "none"))
    print("  true-crypto series hit: " + (", ".join(s for s in crypto_series
                                                   if is_crypto_category(cats.get(s, "")))
                                         or "none"))
    paused_by = group([r for r in cell if live_paused(r["series"])], lambda r: r["series"])
    for s, rs in sorted(paused_by.items()):
        print(row_line(f"    paused {s}", rs, Z_SERIES, span, label_cell(rs, Z_SERIES, s)))
    tiers = group(cell, lambda r: r["tier"])
    for tname, rs in sorted(tiers.items()):
        print(row_line(f"    tier {tname}", rs, Z_STRUCT, span, ""))

    # --- structural cells ----------------------------------------------------------------
    table("PRICE BAND (entry YES ask)", group(prim, lambda r: r["band"]), Z_STRUCT, span,
          order=[b[0] for b in BANDS])
    table("HOURS TO EXPIRATION (first tick inside each bucket; all bands)",
          group(rows, lambda r: r["hb"]), Z_STRUCT, span,
          order=[b[0] for b in HTE_BUCKETS] + ["na"])
    table("HOURS TO EXPIRATION — live cell only",
          group([r for r in rows if r["yes_ask"] is not None
                 and r["yes_ask"] <= LIVE_CELL_MAX_ASK], lambda r: r["hb"]), Z_STRUCT, span,
          order=[b[0] for b in HTE_BUCKETS] + ["na"])
    table("STRIKE STRUCTURE (strike_type|Floor Cap)", group(prim, lambda r: r["struct"]),
          Z_STRUCT, span)
    table("SETTLE MODE", group(prim, lambda r: r["mode"]), Z_STRUCT, span)
    table("MARKET TYPE", group(prim, lambda r: r["mtype"]), Z_STRUCT, span)
    table("MARKET TYPE — live cell", group(cell, lambda r: r["mtype"]), Z_STRUCT, span)

    # --- series --------------------------------------------------------------------------
    sgroups = group(prim, lambda r: r["series"])
    table("SERIES (all bands)", sgroups, Z_SERIES, span, series_cells=True,
          min_print=args.series_min_print)
    table("SERIES — live cell", group(cell, lambda r: r["series"]), Z_SERIES, span,
          series_cells=True, min_print=args.series_min_print)

    # --- issue views ---------------------------------------------------------------------
    print("\n== XOS-000046 VIEW (informs the frozen 2027 test; never re-scores it)")
    mlb = [r for r in prim if r["series"] in ("KXMLBTOTAL", "KXMLBSPREAD")]
    print(HDR)
    for (s, b), rs in sorted(group(mlb, lambda r: (r["series"], r["band"])).items(),
                             key=lambda kv: (kv[0][0], str(kv[0][1]))):
        print(row_line(f"{s} {b}", rs, Z_SERIES, span, ""))
    for (s, ln), rs in sorted(group([r for r in mlb if r["band"] == "<=7"],
                                    lambda r: (r["series"], r["floor"])).items(),
                              key=lambda kv: (kv[0][0], kv[0][1] if kv[0][1] is not None else -1)):
        print(row_line(f"{s} <=7 line {ln}", rs, Z_SERIES, span, ""))
    for s in ("KXMLBTOTAL", "KXMLBSPREAD"):
        sc = [r for r in mlb if r["series"] == s and r["band"] == "<=7"]
        compare(f"  {s} <=7 live-traded vs never-live (maker)",
                [r for r in sc if r["live"]], [r for r in sc if not r["live"]])
    print("\n== XOS-000047 VIEW (informs the frozen forward test; never re-scores it)")
    print(HDR)
    for s in ("KXNFLTOTAL", "KXITFMATCH", "KXITFWMATCH"):
        for b in ("<=7", "8-10"):
            rs = [r for r in prim if r["series"] == s and r["band"] == b]
            if rs:
                print(row_line(f"{s} {b}", rs, Z_SERIES, span, ""))

    # --- summary -------------------------------------------------------------------------
    print("\n== CELL SUMMARY (series, live cell, labelled)")
    lab: dict[str, list[str]] = defaultdict(list)
    for s, rs in group(cell, lambda r: r["series"]).items():
        lab[label_cell(rs, Z_SERIES, s)].append(s)
    for k in ("SIZE-SAFE", "HOLDS", "MAKER-ONLY", "NO-CALL", "CUT-LEANING", "CUT"):
        print(f"  {k:12s} {len(lab.get(k, [])):4d}  {', '.join(sorted(lab.get(k, [])))}")
    print(f"  {'THIN':12s} {len(lab.get('THIN', [])):4d}")
    owned = sorted(s for k, v in lab.items() if k.startswith("(XOS") for s in v)
    print(f"  {'forward test':12s} {len(owned):4d}  {', '.join(owned)}  (numbers above, no label)")
    print("\n== VERDICT")
    study = "PASS (edge is broad)" if p1 == "PASS" else \
        "KILL (paper edge is selection)" if p1 == "KILL" else "HOLD"
    print(f"  P1 {p1} | P2 {p2} | P3 {p3}")
    print("  P4 " + " | ".join(f"{k}: {v}" for k, v in p4.items()))
    print(f"  STUDY: {study}.  This probe authorizes nothing; every change is an owner decision"
          " recorded through Experiment OS.")
    print(f"\n[done in {time.time() - t0:.0f}s]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
