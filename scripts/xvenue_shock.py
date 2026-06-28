"""Event-conditional cross-venue lead-lag — the RIGHT test. Averaging divergence over all
minutes washes out the signal (both venues sit flat between news). Instead: find the minutes
one venue SHOCKS (a big one-minute jump = a goal / news / BTC move), then measure whether the
OTHER venue follows over the next few minutes, and by how much. That isolates the
"Polymarket pops, Kalshi catches up 1-2 min later" effect a small trader could act on.

For every matched pair (World Cup winner + crypto EOY, the markets we can cleanly match on
both venues) it pulls 1-min history on both, aligns, and for each shock measures:
  - SAME-MIN: did the other venue already move with it that minute (no lag = no edge)?
  - FOLLOW: over the next `horizon` minutes, did the other venue move in the shock's
    direction, and by how many cents (the tradeable lag)?
Reports both directions (PM->Kalshi and Kalshi->PM) so we see who leads, pooled + by source.
1-min is the floor of the public history APIs; a 1-2 min lag is visible at this resolution.

Read-only public APIs, stdlib only. Usage:
    {"type":"script","name":"xvenue_shock","args":["--days","4","--shock","2","--horizon","3"]}
"""

from __future__ import annotations

import argparse
import time

import xvenue_crypto as xc
import xvenue_leadlag as xl


class Shock:
    __slots__ = ("n", "follow", "follow_sum", "same_min", "shock_sum")

    def __init__(self):
        self.n = self.follow = self.same_min = 0
        self.follow_sum = self.shock_sum = 0.0

    def merge(self, o: Shock):
        self.n += o.n
        self.follow += o.follow
        self.same_min += o.same_min
        self.follow_sum += o.follow_sum
        self.shock_sum += o.shock_sum

    def row(self, label: str) -> str:
        if not self.n:
            return f"  {label:>16}  (no shocks)"
        return (f"  {label:>16} {self.n:5d} {self.shock_sum / self.n * 100:5.1f}c"
                f" {100.0 * self.same_min / self.n:4.0f}% {100.0 * self.follow / self.n:5.0f}%"
                f" {self.follow_sum / self.n * 100:+6.2f}c")


def shock_study(a: list[float], b: list[float], shock: float, horizon: int):
    """Shocks in A -> does B follow? Returns Shock acc (a leads, b follows)."""
    acc = Shock()
    for t in range(1, len(a) - horizon):
        da = a[t] - a[t - 1]
        if abs(da) < shock:
            continue
        d = 1.0 if da > 0 else -1.0
        b_future = (b[t + horizon] - b[t]) * d      # B's move in A's shock direction, after
        b_now = (b[t] - b[t - 1]) * d                # B already moving same minute
        acc.n += 1
        acc.shock_sum += abs(da)
        acc.follow_sum += b_future
        acc.follow += 1 if b_future > 0 else 0
        acc.same_min += 1 if b_now > 0 else 0
    return acc


def _pairs_worldcup():
    pm = xl.pm_worldcup()
    kal = xl.kalshi_worldcup()
    out = []
    for t in sorted(set(pm) & set(kal)):
        if max(pm[t][1], kal[t][1]) >= 0.03:        # contenders that actually move
            out.append(("wc:" + t, "KXMENWORLDCUP", kal[t][0], pm[t][0]))
    return out


def _pairs_crypto(strike_tol: float):
    kc = xc.fetch_kalshi_crypto(400)
    pc = xc.fetch_pm_crypto(16)
    out = []
    for k, p, _d in xc.match(kc, pc, strike_tol):
        out.append((f"cx:{k['asset']}{k['strike']:.0f}", k["series"], k["ticker"], p["token"]))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=4.0)
    ap.add_argument("--shock", type=float, default=2.0, help="one-min jump threshold (cents)")
    ap.add_argument("--horizon", type=int, default=3, help="follow-through window (min)")
    ap.add_argument("--max-pairs", type=int, default=30)
    ap.add_argument("--strike-tol", type=float, default=0.005)
    args = ap.parse_args(argv)
    end = int(time.time())
    start = end - int(args.days * 86400)
    shock = args.shock / 100.0

    pairs = _pairs_worldcup() + _pairs_crypto(args.strike_tol)
    pairs = pairs[:args.max_pairs]
    print(f"=== Event-conditional cross-venue lead-lag ({args.days:g}d, 1-min,"
          f" shock>={args.shock:g}c, follow={args.horizon}min) ===")
    print(f"  pairs: {len(pairs)}")

    by_src = {"wc": (Shock(), Shock()), "cx": (Shock(), Shock())}  # (PM->Kal, Kal->PM)
    analyzed = 0
    for label, series, ticker, token in pairs:
        ks = xc.kalshi_candles(series, ticker, start, end)
        ps = xl.pm_series(token, start, end)
        kal, pm = xl.align(ks, ps)
        if len(kal) < 60:
            continue
        analyzed += 1
        src = label[:2]
        by_src[src][0].merge(shock_study(pm, kal, shock, args.horizon))   # PM shocks -> Kalshi
        by_src[src][1].merge(shock_study(kal, pm, shock, args.horizon))   # Kalshi shocks -> PM

    print(f"  analyzed: {analyzed}\n")
    print(f"  {'direction':>16} {'n':>5} {'jump':>6} {'same':>4} {'foll%':>5} {'follow_move':>11}")
    print("  (same = other venue already moved that minute; follow% / move = next-horizon"
          " move in the shock direction)")
    total_pm_kal, total_kal_pm = Shock(), Shock()
    for src, (pmkal, kalpm) in by_src.items():
        name = "World Cup" if src == "wc" else "Crypto"
        print(f"  -- {name} --")
        print(pmkal.row("PM->Kalshi"))
        print(kalpm.row("Kalshi->PM"))
        total_pm_kal.merge(pmkal)
        total_kal_pm.merge(kalpm)
    print("  -- POOLED --")
    print(total_pm_kal.row("PM->Kalshi"))
    print(total_kal_pm.row("Kalshi->PM"))
    print("\n  READ: if PM->Kalshi follow% > ~55% AND follow_move clears Kalshi's ~2-4c"
          " round-trip AND > Kalshi->PM, then Kalshi lags Polymarket tradeably.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
