"""Live-canary pattern checks on independent paper history (docs/MMSELL_LIVE_CANARY_PATTERNS.md).

Reads an ops `db` text dump of `mmsell10` paper trades on markets no live canary traded
(ops id `paper-mmsell10-nolive-20261015a`) and runs the three pre-registered checks:

  1. category WIN set vs LOSE set;
  2. a bad-day brake (entries in the 6h after a realized loss);
  3. sports entry timing (hours from entry to close).

Plus the mentions forward-read counter. Offline analysis: stdlib only, no database access.

    python scripts/live_canary_patterns.py <dump.txt> [--since 2026-10-15]
"""

from __future__ import annotations

import argparse
import random
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

try:  # the Politics regime comes from the shipped map; absent outside the repo, it reads Other
    from kalshi_bot.mmsell.regimes import regime_of
except Exception:  # noqa: BLE001
    def regime_of(series: str) -> str:  # type: ignore[misc]
        return "Other"

MONTHS = {m: i for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
#: Fixed before the paper history was read; identical to the mapping applied to live.
CATEGORY_KEYS = (("MENTION", "mentions"), ("RAIN", "weather"), ("HIGH", "weather"),
                 ("LOWT", "weather"), ("TOTAL", "sports-total"), ("SPREAD", "sports-spread"),
                 ("GAME", "sports-game"), ("MATCH", "tennis/match"), ("BTC", "crypto"),
                 ("ETH", "crypto"), ("SOL", "crypto"), ("XRP", "crypto"), ("DOGE", "crypto"),
                 ("INX", "index"), ("NASDAQ", "index"), ("WTI", "oil"), ("GAS", "gas"))
WIN = {"weather", "index", "politics", "mentions"}
LOSE = {"crypto", "sports-total", "sports-spread"}
SPORTS = {"sports-total", "sports-spread", "sports-game", "tennis/match"}
HTC_BUCKETS = ((0, 6, "<6h"), (6, 12, "6-12h"), (12, 24, "12-24h"), (24, 48, "24-48h"),
               (48, 336, ">48h"))
BRAKE_HOURS = 6


def category_of(series: str) -> str:
    s = (series or "").upper()
    for key, cat in CATEGORY_KEYS:
        if key in s:
            return cat
    return "politics" if regime_of(s) == "Politics" else "other"


def event_day(ticker: str) -> str:
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ticker)
    if m and m.group(2) in MONTHS:
        return f"20{m.group(1)}-{MONTHS[m.group(2)]:02d}-{m.group(3)}"
    return ""


def _ts(text: str) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None


def parse_dump(path: str) -> list[dict]:
    lines = open(path, encoding="utf-8").read().splitlines()
    head = next(i for i, ln in enumerate(lines) if ln.startswith("market_ticker"))
    spans = [m.span() for m in re.finditer(r"-+", lines[head + 1])]
    names = [lines[head][a:b].strip() for a, b in spans]
    rows = []
    for ln in lines[head + 2:]:
        if not ln.strip() or ln.startswith("("):
            continue
        vals = [ln[a:(spans[k + 1][0] if k + 1 < len(spans) else len(ln))].strip()
                for k, (a, _b) in enumerate(spans)]
        r = dict(zip(names, vals, strict=True))
        try:
            qty = float(r["quantity"] or 0)
            pnl = float(r["pnl"])
        except (ValueError, KeyError):
            continue
        if qty <= 0:
            continue
        series = r.get("series_ticker") or r["market_ticker"].split("-")[0]
        created, closed, close = _ts(r["created_at"]), _ts(r["closed_at"]), _ts(r["close_time"])
        rows.append({"ticker": r["market_ticker"], "series": series, "cat": category_of(series),
                     "day": event_day(r["market_ticker"]) or (closed.date().isoformat()
                                                              if closed else ""),
                     "created": created, "closed": closed, "close": close,
                     "pnl": pnl, "qty": qty, "cents": 100.0 * pnl / qty})
    return rows


def rate(rows: list[dict]) -> float:
    q = sum(r["qty"] for r in rows)
    return 100.0 * sum(r["pnl"] for r in rows) / q if q else float("nan")


def boot_diff(a: list[dict], b: list[dict] | None = None, n: int = 1500,
              seed: int = 7) -> tuple[float, float, float]:
    """Point estimate and 90% interval of rate(a) (or rate(a) - rate(b)), resampling whole event
    days jointly so a day's markets move together."""
    days = sorted({r["day"] for r in a} | ({r["day"] for r in b} if b else set()))
    by_a, by_b = defaultdict(list), defaultdict(list)
    for r in a:
        by_a[r["day"]].append(r)
    for r in b or []:
        by_b[r["day"]].append(r)
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        pick = [rng.choice(days) for _ in days]
        aa = [r for d in pick for r in by_a.get(d, [])]
        if b is None:
            stats.append(rate(aa))
        else:
            bb = [r for d in pick for r in by_b.get(d, [])]
            stats.append(rate(aa) - rate(bb))
    stats = sorted(s for s in stats if s == s)
    point = rate(a) - (rate(b) if b else 0.0)
    return point, stats[int(0.05 * len(stats))], stats[int(0.95 * len(stats)) - 1]


def fmt(t: tuple[float, float, float]) -> str:
    return f"{t[0]:+.2f}c  [90% {t[1]:+.2f}, {t[2]:+.2f}]"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dump")
    ap.add_argument("--since", default="2026-10-15", help="mentions forward-read start (UTC date)")
    args = ap.parse_args(argv)
    rows = parse_dump(args.dump)
    print(f"sample: {len(rows)} settled mmsell10 paper trades on markets no live canary traded, "
          f"{len({r['day'] for r in rows})} event days, all {fmt(boot_diff(rows))}")

    print("\nBY CATEGORY")
    cats = defaultdict(list)
    for r in rows:
        cats[r["cat"]].append(r)
    for c, rs in sorted(cats.items(), key=lambda kv: -len(kv[1])):
        print(f"  {c:14s} n={len(rs):5d}  {fmt(boot_diff(rs))}")

    win = [r for r in rows if r["cat"] in WIN]
    lose = [r for r in rows if r["cat"] in LOSE]
    w, d = boot_diff(win), boot_diff(win, lose)
    passed = w[0] >= 2.0 and w[1] > 0 and d[0] >= 2.0 and d[1] > 0
    print(f"\nCHECK 1  WIN n={len(win)} {fmt(w)} | LOSE n={len(lose)} {fmt(boot_diff(lose))}"
          f"\n         WIN-LOSE {fmt(d)}  -> {'PASS' if passed else 'NO EFFECT'}")

    losses = sorted(r["closed"] for r in rows if r["pnl"] < 0 and r["closed"])
    after, other = [], []
    for r in rows:
        if not r["created"]:
            continue
        hit = any(t <= r["created"] < t + timedelta(hours=BRAKE_HOURS) for t in losses)
        (after if hit else other).append(r)
    d2 = boot_diff(after, other)
    passed2 = d2[0] <= -2.0 and d2[2] < 0
    print(f"\nCHECK 2  entries <= {BRAKE_HOURS}h after a realized loss n={len(after)} "
          f"{fmt(boot_diff(after))} | others n={len(other)}\n         after-others {fmt(d2)}"
          f"  -> {'PASS' if passed2 else 'NO EFFECT'}")

    sports = []
    for r in rows:
        if r["cat"] in SPORTS and r["created"] and r["close"]:
            h = (r["close"] - r["created"]).total_seconds() / 3600
            if 0 <= h <= 336:
                sports.append({**r, "htc": h})
    print(f"\nCHECK 3  sports with a usable close time n={len(sports)} {fmt(boot_diff(sports))}")
    for lo, hi, name in HTC_BUCKETS:
        b = [r for r in sports if lo <= r["htc"] < hi]
        print(f"  {name:7s} n={len(b):5d}  {fmt(boot_diff(b)) if b else '-'}")
    mid = [r for r in sports if 12 <= r["htc"] < 24]
    rest = [r for r in sports if not 12 <= r["htc"] < 24]
    d3 = boot_diff(mid, rest)
    gain = rate(rest) - rate(sports)
    passed3 = gain >= 1.0 and d3[2] < 0
    print(f"         12-24h minus rest {fmt(d3)}; dropping 12-24h moves sports by {gain:+.2f}c"
          f"  -> {'PASS' if passed3 else 'NO EFFECT'}")

    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    fwd = [r for r in rows if r["cat"] == "mentions" and r["closed"] and r["closed"] >= since]
    print(f"\nMENTIONS FORWARD READ since {args.since}: {len(fwd)}/60 settled, "
          f"{rate(fwd):+.2f}c" if fwd else f"\nMENTIONS FORWARD READ since {args.since}: 0/60")
    print("  (note: this dump excludes live-traded markets; the forward read itself counts every "
          "mmsell10 paper mention trade)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
