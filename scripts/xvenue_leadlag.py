"""Foundational cross-venue measurement — does Kalshi FOLLOW Polymarket, and are the
divergences big/slow enough to trade (one-venue, on Kalshi)?

Pilot market: 2026 FIFA World Cup winner — both venues list it per-country (clean,
unambiguous match), it's liquid, and it's actively moving (tournament in progress).

For each matched contender team it pulls minute price history from BOTH public APIs
(Kalshi candlesticks yes-mid; Polymarket CLOB prices-history), aligns to a common minute
grid (forward-filled), and reports:
  - DIVERGENCE: |Polymarket - Kalshi| distribution (how big, how often > a tradeable gap).
  - LEAD-LAG: cross-correlation of per-minute changes at lags k=-L..L. A peak at k>0
    (past Polymarket move predicts current Kalshi move) = Polymarket leads / Kalshi follows.
  - GAP-CLOSING (the tradeable test): when |gap| exceeds a threshold, how much does KALSHI
    move toward Polymarket over the next N minutes (vs Kalshi's own spread+fee cost)?

Read-only public APIs, stdlib only. Usage:
    {"type":"script","name":"xvenue_leadlag","args":["--days","5","--gap","4"]}
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXMENWORLDCUP"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Kalshi 3-letter suffix -> normalized country name (contenders that actually move).
CODE2NAME = {
    "FRA": "france", "ESP": "spain", "ARG": "argentina", "BRA": "brazil",
    "ENG": "england", "GBR": "england", "GER": "germany", "POR": "portugal",
    "NED": "netherlands", "BEL": "belgium", "USA": "usa", "MEX": "mexico",
    "CAN": "canada", "JPN": "japan", "KOR": "south korea", "CRO": "croatia",
    "MAR": "morocco", "COL": "colombia", "URU": "uruguay", "SUI": "switzerland",
    "SEN": "senegal", "ITA": "italy", "NOR": "norway", "ECU": "ecuador",
}
_ALIAS = {"united states": "usa", "korea republic": "south korea", "korea": "south korea"}


def _norm_country(s: str) -> str:
    s = (s or "").strip().lower()
    return _ALIAS.get(s, s)


def _get(url: str):
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310 (fixed hosts)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return None
    return None


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --- data sources ------------------------------------------------------------------


_PM_Q = re.compile(r"will\s+(.+?)\s+win the 2026 fifa world cup", re.I)


def pm_worldcup() -> dict:
    """normalized country -> (full clob YES token, current yes price)."""
    out: dict[str, tuple[str, float]] = {}
    for page in range(14):
        batch = _get(f"{GAMMA}/markets?closed=false&active=true&limit=100&offset={page * 100}")
        if not isinstance(batch, list) or not batch:
            break
        for m in batch:
            mq = _PM_Q.search(m.get("question") or "")
            if not mq:
                continue
            toks = m.get("clobTokenIds")
            if isinstance(toks, str):
                try:
                    toks = json.loads(toks)
                except json.JSONDecodeError:
                    toks = None
            if not (isinstance(toks, list) and toks):
                continue
            bid, ask = m.get("bestBid"), m.get("bestAsk")
            yes = (_num(bid) + _num(ask)) / 2.0 if bid is not None and ask is not None else 0.0
            out[_norm_country(mq.group(1))] = (str(toks[0]), yes)
        if len(batch) < 100:
            break
    return out


def kalshi_worldcup() -> dict:
    """normalized country -> (market_ticker, current yes mid)."""
    out: dict[str, tuple[str, float]] = {}
    page = _get(f"{KALSHI}/markets?series_ticker={SERIES}&status=open&limit=200")
    for m in (page or {}).get("markets") or []:
        tk = m.get("ticker") or ""
        code = tk.rsplit("-", 1)[-1]
        name = CODE2NAME.get(code)
        if not name:
            sub = _norm_country(m.get("yes_sub_title") or "")
            if sub in CODE2NAME.values():
                name = sub
        if not name:
            continue
        mid = (_num(m.get("yes_bid_dollars")) + _num(m.get("yes_ask_dollars"))) / 2.0
        out[name] = (tk, mid)
    return out


def kalshi_series(ticker: str, start: int, end: int) -> dict[int, float]:
    """minute -> Kalshi yes-mid (dollars 0..1), from 1-min candlesticks."""
    url = (f"{KALSHI}/series/{SERIES}/markets/{ticker}/candlesticks"
           f"?start_ts={start}&end_ts={end}&period_interval=1")
    data = _get(url)
    out: dict[int, float] = {}
    for c in (data or {}).get("candlesticks") or []:
        ts = c.get("end_period_ts")
        yb = (c.get("yes_bid") or {}).get("close_dollars")
        ya = (c.get("yes_ask") or {}).get("close_dollars")
        if ts is None:
            continue
        if yb is not None and ya is not None:
            mid = (_num(yb) + _num(ya)) / 2.0
        else:
            mid = _num((c.get("price") or {}).get("close_dollars"))
        if mid > 0:
            out[int(ts) // 60] = mid
    return out


def pm_series(token: str, start: int, end: int) -> dict[int, float]:
    """minute -> Polymarket yes price (0..1), from CLOB prices-history fidelity=1."""
    url = f"{CLOB}/prices-history?market={token}&startTs={start}&endTs={end}&fidelity=1"
    data = _get(url)
    out: dict[int, float] = {}
    for p in (data or {}).get("history") or []:
        t, pr = p.get("t"), p.get("p")
        if t is not None and pr is not None:
            out[int(t) // 60] = float(pr)
    return out


# --- alignment + stats -------------------------------------------------------------


def align(a: dict[int, float], b: dict[int, float]) -> tuple[list[float], list[float]]:
    """Forward-fill both onto the union minute grid over their overlapping span."""
    if not a or not b:
        return [], []
    lo = max(min(a), min(b))
    hi = min(max(a), max(b))
    if hi <= lo:
        return [], []
    xs, ys = [], []
    la = lb = None
    for t in range(lo, hi + 1):
        la = a.get(t, la)
        lb = b.get(t, lb)
        if la is not None and lb is not None:
            xs.append(la)
            ys.append(lb)
    return xs, ys


def _corr(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3:
        return float("nan")
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=False))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else float("nan")


def leadlag(kal: list[float], pm: list[float], max_lag: int):
    """Cross-corr of per-minute CHANGES at lags. k>0 => PM change leads Kalshi change."""
    dk = [kal[i] - kal[i - 1] for i in range(1, len(kal))]
    dp = [pm[i] - pm[i - 1] for i in range(1, len(pm))]
    best = (0, 0.0)
    rows = []
    for k in range(-max_lag, max_lag + 1):
        if k >= 0:
            a, b = dk[k:], dp[:len(dp) - k] if k else dp
        else:
            a, b = dk[:len(dk) + k], dp[-k:]
        n = min(len(a), len(b))
        c = _corr(a[:n], b[:n]) if n >= 5 else float("nan")
        rows.append((k, c, n))
        if c == c and abs(c) > abs(best[1]):
            best = (k, c)
    return best, rows


class GapAcc:
    __slots__ = ("n", "closed", "move_sum", "gap_sum")

    def __init__(self):
        self.n = self.closed = 0
        self.move_sum = self.gap_sum = 0.0

    def add(self, gap_signed: float, kal_move_toward: float):
        self.n += 1
        self.gap_sum += abs(gap_signed)
        self.move_sum += kal_move_toward
        self.closed += 1 if kal_move_toward > 0 else 0


def gap_closing(kal: list[float], pm: list[float], gap_c: float, horizon: int) -> GapAcc:
    """When |PM-Kalshi| > gap (cents), does Kalshi move TOWARD PM over the next `horizon` min?"""
    acc = GapAcc()
    g = gap_c / 100.0
    for t in range(len(kal) - horizon):
        gap = pm[t] - kal[t]                 # +: PM higher than Kalshi (Kalshi should rise)
        if abs(gap) < g:
            continue
        move = kal[t + horizon] - kal[t]
        acc.add(gap, move * (1.0 if gap > 0 else -1.0))   # toward PM = positive
    return acc


# --- main --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=5.0)
    ap.add_argument("--gap", type=float, default=4.0, help="divergence threshold (cents)")
    ap.add_argument("--horizon", type=int, default=15, help="gap-closing look-ahead (min)")
    ap.add_argument("--max-lag", type=int, default=10)
    ap.add_argument("--min-price", type=float, default=0.03, help="skip flat longshots")
    args = ap.parse_args(argv)
    end = int(time.time())
    start = end - int(args.days * 86400)

    pm = pm_worldcup()
    kal = kalshi_worldcup()
    teams = sorted(set(pm) & set(kal))
    print(f"=== Cross-venue lead-lag — World Cup winner ({args.days:g}d, 1-min) ===")
    print(f"  Polymarket teams: {len(pm)}   Kalshi teams: {len(kal)}   matched: {len(teams)}")
    movers = [t for t in teams if max(pm[t][1], kal[t][1]) >= args.min_price]
    print(f"  contenders (price >= {args.min_price:g}): {', '.join(movers) or '(none)'}")

    print(f"\n  {'team':>12} {'pts':>5} {'|div|':>6} {'>gap%':>6} {'bestLag':>8} {'corr':>6} |"
          f" {'gapN':>5} {'close%':>6} {'kMove':>6}")
    pooled_div, pooled_gap = [], GapAcc()
    lag_votes = defaultdict(float)
    for t in movers:
        ks = kalshi_series(kal[t][0], start, end)
        ps = pm_series(pm[t][0], start, end)
        x, y = align(ks, ps)      # x=kalshi, y=pm  (dollars)
        if len(x) < 30:
            print(f"  {t:>12} {len(x):5d}  (too few aligned points: kal={len(ks)} pm={len(ps)})")
            continue
        div = [abs(b - a) * 100 for a, b in zip(x, y, strict=False)]
        avg_div = sum(div) / len(div)
        pct_gap = 100.0 * sum(1 for d in div if d >= args.gap) / len(div)
        (bk, bc), _rows = leadlag(x, y, args.max_lag)
        ga = gap_closing(x, y, args.gap, args.horizon)
        pooled_div.extend(div)
        pooled_gap.n += ga.n
        pooled_gap.closed += ga.closed
        pooled_gap.move_sum += ga.move_sum
        pooled_gap.gap_sum += ga.gap_sum
        if bc == bc:
            lag_votes[bk] += abs(bc)
        cl = f"{100.0 * ga.closed / ga.n:5.0f}%" if ga.n else "  n/a"
        km = f"{ga.move_sum / ga.n * 100:+5.1f}c" if ga.n else "  n/a"
        print(f"  {t:>12} {len(x):5d} {avg_div:5.1f}c {pct_gap:5.0f}% {bk:+8d} {bc:+5.2f} |"
              f" {ga.n:5d} {cl:>6} {km:>6}")

    if pooled_div:
        ad = sum(pooled_div) / len(pooled_div)
        pg = 100.0 * sum(1 for d in pooled_div if d >= args.gap) / len(pooled_div)
        print(f"\n  POOLED: avg|div|={ad:.1f}c  %>{args.gap:g}c={pg:.0f}%  "
              f"gap-events={pooled_gap.n}  closed={100.0 * pooled_gap.closed / pooled_gap.n:.0f}%"
              f"  avg Kalshi move toward PM in {args.horizon}m="
              f"{pooled_gap.move_sum / pooled_gap.n * 100:+.1f}c" if pooled_gap.n else "")
        if lag_votes:
            top = max(lag_votes.items(), key=lambda kv: kv[1])
            lead = ("PM LEADS Kalshi" if top[0] > 0 else
                    "Kalshi LEADS PM" if top[0] < 0 else "no lead (contemporaneous)")
            print(f"  dominant lead-lag: k={top[0]:+d} min  -> {lead}")
        print(f"  (tradeable if: %>{args.gap:g}c is non-trivial AND closed% > ~55% AND avg move"
              f" toward PM clears Kalshi's ~2-4c round-trip)")
    else:
        print("\n  No usable aligned series — check endpoints / token IDs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
