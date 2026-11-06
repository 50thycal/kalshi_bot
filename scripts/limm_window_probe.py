"""LIMM WINDOW PROBE — would the "say X before <date>" windows pay on the cheap YES side?

Pre-registered in docs/LIMM_WINDOW_PROBE.md (2026-11-06, study 8). Constants marked
"pre-registered" are frozen there; changing one is a new probe.

Thesis §9.55 dropped KXTRUMPSAY-style windows because a word can land at any moment, so a bid has
no time to step aside. That argument holds for a NO bid (the word is said, sellers dump NO into
it). A YES bid on a word the market thinks unlikely is the other way round: when the word is said,
YES jumps and nobody sells YES into our bid. This probe measures both halves:

  A. NOW  — every running liquidity programme in the window series, its book read live, a $10 YES
     bid priced by the live rule (§9.72: one tick behind a reference that is the touch) and scored
     with the book's own estimator. Output: $/market-day and how many markets qualify.
  B. HISTORY — settled window markets: hourly candlesticks from open to close. Entry at the first
     hour >= ENTRY_DELAY_H after open where the YES bid is 1..10c (same price rule on the candle's
     YES bid). The bid rests until close - STEP_ASIDE_H (or the market's own close if earlier). A
     fill is any later hour whose YES trade LOW is <= our price (conservative: a trade at our price
     counts). P&L per filled contract = 100*[YES] - price; unfilled = 0.

Read-only: DATABASE_URL_RO + Kalshi's public REST. Places nothing.

Usage: {"type":"script","name":"limm_window_probe","args":[],"id":"wp-run1-<date>"}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- pre-registered constants ----------------------------------------------------------------
SERIES = ("KXTRUMPSAY", "KXTRUMPSAYMONTH", "KXTRUMPSAYCOMPANY")
MAX_PRICE, MIN_PRICE = 10, 1           # cheap side band, cents
BID_DOLLARS, MAX_QTY = 10.0, 500       # §9.72 sizing
ENTRY_DELAY_H = 6.0                    # history: first entry this long after the market opens
STEP_ASIDE_H = 48.0                    # history: bids leave this long before close
HISTORY_DAYS = 60                      # settled events closed within this many days
# Verdict (pre-registered): PROMISING when A shows >= $0.50/market-day on >= 10 qualifying
# markets AND B's filled YES bids lose <= 40% of the reward a market earns over its rest (using
# A's $/market-day), on >= 100 entered markets. Anything else is NOT-YET or AVOID (B's net < 0).
PROMISING_USD_PER_DAY = 0.50
PROMISING_MIN_MARKETS = 10
PROMISING_MAX_LOSS_SHARE = 0.40
MIN_ENTERED = 100

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

PROG_SQL = """
SELECT market_ticker, event_ticker, series_ticker, period_reward_usd, start_date, end_date,
       target_size, discount_factor_bps, close_time
FROM incentive_programs
WHERE incentive_type = 'liquidity' AND end_date > now() AND start_date <= now()
  AND split_part(market_ticker, '-', 1) = ANY(%(series)s)
"""


def _get(path: str, tries: int = 3) -> dict | None:
    url = f"{BASE}{path}"
    for i in range(tries):
        req = urllib.request.Request(url, method="GET", headers={
            "User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and i + 1 < tries:
                time.sleep(2.0 * (i + 1))
                continue
            print(f"  HTTP {exc.code} for {path[:120]}", file=sys.stderr)
            return None
        except Exception as exc:  # noqa: BLE001
            print(f"  error for {path[:120]}: {exc}", file=sys.stderr)
            return None
    return None


def _cents(v) -> int | None:
    """A dollar string/number or a cents int -> int cents."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(round(f * 100)) if f <= 1.0 and isinstance(v, str) else int(round(f))


def _ts(s) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def price_rule(best_yes_bid: int | None, ref: int | None) -> int | None:
    """§9.72 on the YES side: the reference price, one tick behind when it is the touch."""
    if best_yes_bid is None or not (MIN_PRICE <= best_yes_bid <= MAX_PRICE):
        return None
    p = ref if ref is not None else best_yes_bid
    if p >= best_yes_bid and p - 1 >= 1:
        p -= 1
    return p if MIN_PRICE <= p <= MAX_PRICE else None


def qty_for(price: int) -> int:
    return min(MAX_QTY, int(BID_DOLLARS * 100 // price))


# ------------------------------------------------------------------------------- part A (now)

def part_a(rows) -> tuple[float, int]:
    from kalshi_bot.liquidity_incentive.scoring import discount_factor, estimate, side_score
    from kalshi_bot.scanner.metrics import parse_orderbook

    print("== A. NOW — running window programmes, $10 YES bid by the live rule")
    by_series = defaultdict(lambda: {"mk": 0, "yes_cheap": 0, "no_cheap": 0, "qual": 0, "usd_day": 0.0})
    per_market = []
    for (tk, _ev, _series, pool, start, end, target, dbps, close) in rows:
        s = by_series[tk.split("-", 1)[0]]
        s["mk"] += 1
        ob = _get(f"/markets/{tk}/orderbook")
        if not ob:
            continue
        yl, nl = parse_orderbook(ob)
        ymap = {int(p): float(q) for p, q in yl}
        nmap = {int(p): float(q) for p, q in nl}
        by = max(ymap) if ymap else None
        bn = max(nmap) if nmap else None
        if bn is not None and MIN_PRICE <= bn <= MAX_PRICE and not (by is not None and by <= MAX_PRICE):
            s["no_cheap"] += 1
        if by is None or not (MIN_PRICE <= by <= MAX_PRICE):
            continue
        s["yes_cheap"] += 1
        tgt = float(target) if target is not None else None
        ref = side_score(ymap, tgt, discount_factor(dbps)).reference_price
        p = price_rule(by, ref)
        if p is None or pool is None or start is None or end is None:
            continue
        q = qty_for(p)
        est = estimate(yes_levels=ymap, no_levels=nmap, target_size=tgt, discount_factor_bps=dbps,
                       our_yes_price=p, our_yes_size=q, our_no_price=None, our_no_size=0.0,
                       period_reward_usd_value=float(pool),
                       period_seconds=(end - start).total_seconds())
        rps = est.reward_per_second_usd or 0.0
        if rps <= 0:
            continue
        usd_day = rps * 86400.0
        s["qual"] += 1
        s["usd_day"] += usd_day
        days_left = max(0.0, ((close or end) - dt.datetime.now(dt.timezone.utc)).total_seconds()
                        / 86400.0 - STEP_ASIDE_H / 24.0)
        per_market.append((usd_day, tk, by, p, q, float(pool), days_left))
        time.sleep(0.05)
    print(f"  {'series':22} {'mkts':>5} {'YEScheap':>8} {'NOcheap':>7} {'qual':>5} {'$/day all':>9}")
    tot_q, tot_usd = 0, 0.0
    for k, s in sorted(by_series.items()):
        print(f"  {k:22} {s['mk']:5d} {s['yes_cheap']:8d} {s['no_cheap']:7d} {s['qual']:5d} "
              f"{s['usd_day']:9.2f}")
        tot_q += s["qual"]
        tot_usd += s["usd_day"]
    per_market.sort(reverse=True)
    print("  top markets: $/day  ticker  yes_bid -> our price x qty  pool  days-left-before-step-aside")
    for usd_day, tk, by, p, q, pool, dl in per_market[:15]:
        print(f"    {usd_day:6.2f}  {tk:42} {by:2d}c -> {p:2d}c x{q:<3d} ${pool:5.0f}  {dl:4.1f}d")
    mean = tot_usd / tot_q if tot_q else 0.0
    print(f"  qualifying markets {tot_q}, mean ${mean:.2f}/market-day, "
          f"best 10 together ${sum(m[0] for m in per_market[:10]):.2f}/day")
    return mean, tot_q


# --------------------------------------------------------------------------- part B (history)

def _candles(series: str, tk: str, start: int, end: int) -> list[tuple[int, int | None, int | None]]:
    """[(end_ts, yes_bid_close_c, trade_low_c)] hourly."""
    data = _get(f"/series/{series}/markets/{tk}/candlesticks?start_ts={start}&end_ts={end}"
                f"&period_interval=60")
    out = []
    for c in (data or {}).get("candlesticks") or []:
        yb = c.get("yes_bid") or {}
        pr = c.get("price") or {}
        ybc = _cents(yb.get("close_dollars")) if yb.get("close_dollars") is not None \
            else _cents(yb.get("close"))
        low = _cents(pr.get("low_dollars")) if pr.get("low_dollars") is not None \
            else _cents(pr.get("low"))
        out.append((int(c.get("end_period_ts") or 0), ybc, low))
    out.sort()
    return out


def part_b(usd_per_day: float, max_markets: int) -> tuple[int, float, float]:
    print("\n== B. HISTORY — settled window markets, $10 YES bid, rest until close - "
          f"{STEP_ASIDE_H:g}h, conservative fill (trade low <= our price)")
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=HISTORY_DAYS)
    entered = filled = yes_won = 0
    pnl_c = 0.0
    rest_days = 0.0
    fill_pnls = []
    n_seen = 0
    for series in SERIES:
        cursor = ""
        for _page in range(20):
            data = _get(f"/events?series_ticker={series}&status=settled&with_nested_markets=true"
                        f"&limit=100" + (f"&cursor={cursor}" if cursor else ""))
            if not data:
                break
            for ev in data.get("events") or []:
                for m in ev.get("markets") or []:
                    close = _ts(m.get("close_time"))
                    opened = _ts(m.get("open_time"))
                    if close is None or opened is None or close < since:
                        continue
                    if n_seen >= max_markets:
                        break
                    n_seen += 1
                    res = (m.get("result") or "").lower()
                    if res not in ("yes", "no"):
                        continue
                    stop = close - dt.timedelta(hours=STEP_ASIDE_H)
                    cs = _candles(series, m["ticker"], int(opened.timestamp()),
                                  int(close.timestamp()))
                    entry = None
                    for ts, ybc, _low in cs:
                        t = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
                        if t < opened + dt.timedelta(hours=ENTRY_DELAY_H) or t >= stop:
                            continue
                        p = price_rule(ybc, None)
                        if p is not None:
                            entry = (t, p)
                            break
                    if entry is None:
                        continue
                    t0, p = entry
                    q = qty_for(p)
                    entered += 1
                    hit = None
                    for ts, _ybc, low in cs:
                        t = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
                        if t <= t0 or t >= stop:
                            continue
                        if low is not None and low <= p:
                            hit = t
                            break
                    end_rest = hit or min(stop, close)
                    rest_days += max(0.0, (end_rest - t0).total_seconds() / 86400.0)
                    if hit is not None:
                        filled += 1
                        g = (100 if res == "yes" else 0) - p
                        yes_won += res == "yes"
                        pnl_c += g * q
                        fill_pnls.append(g * q / 100.0)
                    time.sleep(0.05)
            cursor = data.get("cursor") or ""
            if not cursor:
                break
    print(f"  markets scanned {n_seen}; entered {entered}; filled {filled} "
          f"({(filled / entered * 100) if entered else 0:.0f}%); filled that won YES {yes_won}")
    fill_usd = pnl_c / 100.0
    reward_usd = rest_days * usd_per_day
    print(f"  fill P&L ${fill_usd:+.2f} over {entered} entries (${(fill_usd / entered) if entered else 0:+.3f}"
          f"/entry); rested {rest_days:.0f} market-days -> reward at A's rate "
          f"${reward_usd:.2f}")
    if fill_pnls:
        fill_pnls.sort()
        print(f"  worst fills: {', '.join(f'${x:+.2f}' for x in fill_pnls[:5])}")
    net = reward_usd + fill_usd
    loss_share = (-fill_usd / reward_usd) if reward_usd > 0 and fill_usd < 0 else 0.0
    print(f"  net ${net:+.2f}; fills cost {loss_share * 100:.0f}% of the reward")
    return entered, net, loss_share


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-history-markets", type=int, default=900)
    args = ap.parse_args(argv)

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    print("LIMM window probe — say-X-before-date windows, cheap YES side "
          "(pre-registered docs/LIMM_WINDOW_PROBE.md)\n")
    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        cur.execute(PROG_SQL, {"series": list(SERIES)})
        rows = cur.fetchall()
    print(f"running programmes in {', '.join(SERIES)}: {len(rows)}\n")
    usd_day, n_q = part_a(rows)
    entered, net, loss_share = part_b(usd_day, args.max_history_markets)

    print("\n== VERDICT (pre-registered; authorizes nothing)")
    if entered >= MIN_ENTERED and net < 0:
        verdict = "AVOID"
    elif (usd_day >= PROMISING_USD_PER_DAY and n_q >= PROMISING_MIN_MARKETS
          and entered >= MIN_ENTERED and loss_share <= PROMISING_MAX_LOSS_SHARE):
        verdict = "PROMISING"
    else:
        verdict = "NOT-YET"
    print(f"  {verdict}: ${usd_day:.2f}/market-day on {n_q} qualifying markets; "
          f"{entered} historical entries, net {net:+.2f}, fills {loss_share * 100:.0f}% of reward")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
