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

For a numeric threshold/range ladder, a Dutch-book hit is only trusted if the retained legs
tile the outcome space with no gap (`_tiles_exhaustively`) -- an illiquid leg silently dropped
by the quote filter can otherwise turn "Sum(yes) over a subset" into a fake "Sum(yes)=1" arb,
where every surviving leg is a worthless tail and the real probability mass sits in the missing
gap. Named-candidate MECE sets (no numeric structure to tile) still rely on the printed leg
detail for a manual reality check.

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
# capture an optional leading sign/$ blob, the number, and an optional magnitude unit — so
# 'Above -0.3%' -> -0.3 and 'Above $1.1M' -> 1_100_000 (both were mis-parsed before, which
# scrambled otherwise-monotone ladders into FALSE arbs). The unit is either a spelled-out word
# ('$700 billion', '$1.00 trillion') or a bare letter NOT glued to more letters — the lookahead
# is what stops '5 to 10' parsing as 5e12 and '3 mph' as 3e6.
_NUM = re.compile(
    r"([-$]*)\s*([0-9][0-9,]*\.?[0-9]*)\s*(thousand|million|billion|trillion|[kmbt](?![a-z]))?",
    re.I,
)
_UNIT = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12,
         "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}


def fee(p: float) -> float:
    """Kalshi taker fee (dollars) for one contract at price p (0..1)."""
    return math.ceil(7.0 * p * (1 - p)) / 100.0


def _nums(s: str) -> list[float]:
    """Signed, unit-scaled numbers from a subtitle (handles leading '-' and K/M/B suffixes)."""
    out = []
    for pre, num, unit in _NUM.findall(s or ""):
        v = float(num.replace(",", "")) * _UNIT[unit.lower()]
        out.append(-v if "-" in pre else v)
    return out


def _parse_bucket(sub: str):
    """Return ('range', lo, hi) | ('ge', x) | ('le', x) | None from a Kalshi subtitle."""
    s = (sub or "").lower()
    nums = _nums(sub or "")
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


def _tiles_exhaustively(mkts: list[dict], tol: float = 0.02) -> bool:
    """True if every leg parsed as a numeric bucket (le/range/ge) AND those buckets tile the
    outcome space with no gap wider than `tol` (in the subtitle's own units).

    Guards the one failure mode both real-scan false positives shared: a leg gets silently
    dropped by the illiquid-quote filter in `scan_event` (0 < ya <= 1), so the retained legs
    stop being exhaustive. Summing yes_ask over a mutually_exclusive event's SURVIVING legs
    then measures a subset sum, not Sum(yes)=1 -- and if the missing gap holds the real
    probability mass, every retained leg can look like a worthless-tail "arb" (KXXRP-26JUL2617:
    19 legs, all bid=0/ask=1c, with the entire $0.88-$1.30 band silently missing). A single
    dropped leg always opens a gap strictly wider than one bucket width, so the interval check
    below catches it without needing to compare against the event's raw market count.

    Returns False (unverifiable, not proven exhaustive) for named-candidate MECE sets (election
    winners, "who will be next X") -- there's no numeric structure to tile, so those still rely
    on the printed leg detail for a manual reality check, same as before this guard existed.
    """
    if not mkts or any(not m["parse"] for m in mkts):
        return False
    intervals = []
    n_le = n_ge = 0
    for m in mkts:
        kind, *bounds = m["parse"]
        if kind == "le":
            intervals.append((float("-inf"), bounds[0]))
            n_le += 1
        elif kind == "ge":
            intervals.append((bounds[0], float("inf")))
            n_ge += 1
        else:  # "range"
            intervals.append((bounds[0], bounds[1]))
    if n_le != 1 or n_ge != 1:          # no open tails on both ends -> not a full partition
        return False
    intervals.sort(key=lambda iv: iv[0])
    return all(lo2 - hi1 <= tol
               for (_, hi1), (lo2, _) in zip(intervals, intervals[1:], strict=False))


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
    out = {"event": e.get("event_ticker"), "n": len(mkts), "title": (e.get("title") or "")[:40],
           "excl": bool(e.get("mutually_exclusive")), "legs": mkts}

    # (1) Dutch book — only for a true mutually-exclusive set
    mece = bool(e.get("mutually_exclusive")) or all(m["parse"] and m["parse"][0] == "range"
                                                    for m in mkts)
    if mece:
        sum_ask = sum(m["ya"] for m in mkts)
        sum_bid = sum(m["yb"] for m in mkts)
        buy_all = 1.0 - sum_ask - sum(fee(m["ya"]) for m in mkts)          # buy every YES
        # buy-all-NO profit = Sum(yes_bid) - 1 - fees (each NO costs 1-yb, N-1 of N pay $1)
        sell_all = sum_bid - 1.0 - sum(fee(m["yb"]) for m in mkts)
        # For a numeric threshold/range ladder, require the legs to tile with no gap before
        # trusting Sum(yes) as a real partition sum -- a dropped illiquid leg turns a Dutch
        # book into a subset sum over worthless tails (see _tiles_exhaustively docstring).
        # Named-candidate sets have no numeric structure to verify and keep the prior
        # flag-trusting behavior (still subject to the printed leg-detail reality check).
        numeric = all(m["parse"] for m in mkts)
        gapped = numeric and not _tiles_exhaustively(mkts)
        out.update(mece=True, sum_ask=sum_ask, sum_bid=sum_bid,
                   arb_buy=buy_all, arb_sell=sell_all, gapped=gapped)
        if not gapped:
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
    ap.add_argument("--explain", type=int, default=6, help="dump legs for top N flagged arbs")
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

    gapped = [r for r in results if r.get("gapped")]
    print(f"=== Kalshi no-arbitrage scan — {scanned} multi-outcome events, {arbs} locked arbs ===\n")
    if gapped:
        print(f"  ({len(gapped)} numeric MECE set(s) had a gap in the retained legs -- likely an "
              f"illiquid leg filtered out -- so a would-be Sum(yes) hit was suppressed, not "
              f"trusted; see e.g. {gapped[0]['event']})\n")
    live = [r for r in results if "ARB" in r]
    if live:
        print("  *** LOCKED ARBS ***")
        for r in sorted(live, key=lambda r: -r["ARB"][1])[:30]:
            print(f"    +${r['ARB'][1]:.3f}  {r['ARB'][0]:>14}  n={r['n']}  {r['event']}"
                  f"  [{r['title']}]")
    else:
        print("  (no locked arbs found)")

    if live and args.explain:
        print("\n  --- LEG DETAIL for top flagged arbs (reality check: is it fillable & truly MECE?) ---")
        for r in sorted(live, key=lambda r: -r["ARB"][1])[:args.explain]:
            print(f"\n  {r['event']}  {r['ARB'][0]} +${r['ARB'][1]:.3f}  excl_flag={r['excl']}  [{r['title']}]")
            for m in sorted(r["legs"], key=lambda m: -m["ya"]):
                print(f"      yb={m['yb']:.2f} ya={m['ya']:.2f} parse={m['parse']}  {m['sub'][:48]!r}")

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
