"""No-arbitrage / Dutch-book scanner for Kalshi — pure structure, no forecasting. Where a
locked arb exists it's risk-free profit (the literal 'bot that makes money').

Two checks across open Kalshi events:
  1) DUTCH BOOK on a mutually-exclusive+exhaustive set (range buckets, multi-candidate
     winners): exactly one market resolves YES, so fair Sum(yes)=1. If Sum(yes_ask) < $1 you
     buy ALL legs' YES for < $1 and one pays $1 -> locked profit (net of fees). If
     Sum(yes_bid) > $1 you buy ALL legs' NO for < $(N-1) and N-1 pay $1 -> locked profit.
  2) MONOTONICITY on a threshold ladder ('BTC >= $X'): P(>=X) must fall as X rises. If a
     HIGHER strike's yes_bid exceeds a LOWER strike's yes_ask, buy the low-strike YES and the
     high-strike NO -> the low strike resolves YES whenever the high one does = locked profit.

Reports actual violations (net of the ceil(7*p*(1-p)) per-leg fee) AND the tightness
distribution (how close Sum(yes_ask) gets to $1) so we see how arbitraged the exchange is.
Read-only public API, stdlib only. Usage: {"type":"script","name":"kalshi_arb","args":["--days","30"]}
"""

from __future__ import annotations

import argparse
import calendar
import math
import re
import time

import xvenue_leadlag as xl  # _get, _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_NUM = re.compile(r"\$?\s*([0-9][0-9,]*\.?[0-9]*)")


def fee(p: float) -> float:
    """Kalshi taker fee (dollars) for one contract at price p (0..1)."""
    return math.ceil(7.0 * p * (1 - p)) / 100.0


def _parse_bucket(sub: str):
    """Return ('range', lo, hi) | ('ge', x) | ('le', x) | None from a Kalshi subtitle."""
    s = (sub or "").lower()
    nums = [float(x.replace(",", "")) for x in _NUM.findall(sub or "")]
    if not nums:
        return None
    if any(w in s for w in ("or above", "or higher", "above", "or more", ">=")):
        return ("ge", nums[0])
    if any(w in s for w in ("or below", "or lower", "below", "under", "or less", "<=")):
        return ("le", nums[0])
    if " to " in s and len(nums) >= 2:
        return ("range", min(nums[:2]), max(nums[:2]))
    return None


def _close_ts(m: dict) -> int:
    """Unix close time from a Kalshi market's close_time (ISO8601), 0 if unparseable."""
    ct = (m.get("close_time") or "")[:19]
    try:
        return calendar.timegm(time.strptime(ct, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def scan_event(e: dict, fee_buf: float, max_close: int):
    """Return a dict of any arb found + tightness metrics for one event, or None if skipped."""
    mkts = []
    for m in e.get("markets") or []:
        ct = _close_ts(m)
        if max_close and ct and ct > max_close:      # closes beyond the horizon -> skip
            continue
        yb, ya = xl._num(m.get("yes_bid_dollars")), xl._num(m.get("yes_ask_dollars"))
        if not (0 < ya <= 1) or yb < 0:
            continue
        mkts.append({"yb": yb, "ya": ya, "sub": m.get("yes_sub_title") or "",
                     "tk": m.get("ticker"), "parse": _parse_bucket(m.get("yes_sub_title") or "")})
    if len(mkts) < 3:
        return None
    out = {"event": e.get("event_ticker"), "n": len(mkts), "title": (e.get("title") or "")[:40]}

    # (1) Dutch book — only for a true mutually-exclusive set
    mece = bool(e.get("mutually_exclusive")) or all(m["parse"] and m["parse"][0] == "range"
                                                    for m in mkts)
    if mece:
        sum_ask = sum(m["ya"] for m in mkts)
        sum_bid = sum(m["yb"] for m in mkts)
        buy_all = 1.0 - sum_ask - sum(fee(m["ya"]) for m in mkts)          # buy every YES
        # buy-all-NO profit = Sum(yes_bid) - 1 - fees (each NO costs 1-yb, N-1 of N pay $1)
        sell_all = sum_bid - 1.0 - sum(fee(m["yb"]) for m in mkts)
        out.update(mece=True, sum_ask=sum_ask, sum_bid=sum_bid,
                   arb_buy=buy_all, arb_sell=sell_all)
        if buy_all > fee_buf:
            out["ARB"] = ("BUY-ALL-YES", buy_all)
        elif sell_all > fee_buf:
            out["ARB"] = ("BUY-ALL-NO", sell_all)
        return out

    # (2) Monotonicity on a 'ge' threshold ladder
    ge = sorted(((m["parse"][1], m) for m in mkts if m["parse"] and m["parse"][0] == "ge"),
                key=lambda x: x[0])
    best = None
    for i in range(len(ge)):
        for j in range(i + 1, len(ge)):
            lo_m, hi_m = ge[i][1], ge[j][1]         # lo strike, hi strike (hi >= lo)
            # buy YES at low strike (ask), buy NO at high strike (1-bid): profit if low resolves
            # yes whenever high does -> guaranteed. cost = ya_low + (1 - yb_high); pays $1.
            prof = 1.0 - lo_m["ya"] - (1.0 - hi_m["yb"]) - fee(lo_m["ya"]) - fee(1 - hi_m["yb"])
            if best is None or prof > best[0]:
                best = (prof, ge[i][0], ge[j][0])
    if best:
        out.update(mece=False, mono_best=best[0], mono_lo=best[1], mono_hi=best[2])
        if best[0] > fee_buf:
            out["ARB"] = ("MONO-VERTICAL", best[0])
        return out
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=30.0, help="events closing within N days")
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--fee-buf", type=float, default=0.0, help="min net profit ($) to flag")
    args = ap.parse_args(argv)
    max_close = int(time.time() + args.days * 86400)

    scanned = arbs = 0
    results = []
    cursor = ""
    for _ in range(args.max_pages):
        page = xl._get(f"{KALSHI}/events?status=open&with_nested_markets=true&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for e in evs:
            if (e.get("series_ticker") or "").startswith("KXMVE"):    # parlay noise
                continue
            r = scan_event(e, args.fee_buf, max_close)
            if r is None:
                continue
            scanned += 1
            if "ARB" in r:
                arbs += 1
            results.append(r)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break

    print(f"=== Kalshi no-arbitrage scan — {scanned} multi-outcome events, {arbs} locked arbs ===\n")
    live = [r for r in results if "ARB" in r]
    if live:
        print("  *** LOCKED ARBS ***")
        for r in sorted(live, key=lambda r: -r["ARB"][1])[:30]:
            print(f"    +${r['ARB'][1]:.3f}  {r['ARB'][0]:>14}  n={r['n']}  {r['event']}"
                  f"  [{r['title']}]")
    else:
        print("  (no locked arbs found)")

    mece = [r for r in results if r.get("mece") and "sum_ask" in r]
    if mece:
        mece.sort(key=lambda r: r["sum_ask"])
        print("\n  --- tightest Dutch-book sets (Sum(yes_ask) closest to $1 = most exploitable) ---")
        print(f"  {'sum_ask':>7} {'sum_bid':>7} {'buyYes$':>8} {'buyNo$':>8} {'n':>3}  event")
        for r in mece[:12]:
            print(f"  {r['sum_ask']:7.3f} {r['sum_bid']:7.3f} {r['arb_buy']:+8.3f}"
                  f" {r['arb_sell']:+8.3f} {r['n']:3d}  {r['event']} [{r['title']}]")
        widest = max(mece, key=lambda r: r["sum_bid"])
        print(f"  widest Sum(yes_bid)={widest['sum_bid']:.3f} ({widest['event']})"
              f"  (>1.0 = buy-all-NO arb)")
    mono = [r for r in results if not r.get("mece") and "mono_best" in r]
    if mono:
        mono.sort(key=lambda r: -r["mono_best"])
        print("\n  --- best threshold-ladder monotonicity edges (>0 = vertical arb) ---")
        for r in mono[:8]:
            print(f"    {r['mono_best']:+.3f}  {r['event']}  strikes {r['mono_lo']:.0f}/{r['mono_hi']:.0f}")
    print("\n  net of ceil(7*p*(1-p)) per-leg fee; top-of-book only (depth/latency not modeled).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
