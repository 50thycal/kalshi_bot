"""OFLOW — is there within-market order-flow alpha on Kalshi's own trade tape?
(idea-model Area 2: microstructure / order-flow, 2026-07-22; see docs/OFLOW_THESIS.md)

Feasibility probe on `game_tape_snapshots` — the ~1.08M-trade World Cup in-play tape, the ONLY
real order-flow data the bot has collected (`taker_side` populated). It asks the "is there ANY
microstructure alpha on Kalshi's tape?" gate: does trailing net aggressor imbalance predict the
next ~N-second price move by more than the taker round-trip cost?

**Honest prior: LOW** (cost dominates on liquid markets; this is the killed WC slice; h2h is the
−EV mmsell cell). But the WITHIN-market flow->price question has no prior attempt (xgame measured
cross-venue *price* lead-lag, never within-market *flow*), so a clean ruling-out is new info.

v1 uses **Kalshi venue rows only** — `taker_side` = yes/no maps cleanly to signed flow in the
team-probability direction (+ for a taker buying the team's YES, − for NO). The cross-venue PM-flow
slice is deferred to v2 (PM buy/sell → team-direction is ambiguous without the traded token side).

Method (no-lookahead): per market build ~10s bars (last trade price + net signed aggressor volume);
the trailing imbalance at bar t sums flow STRICTLY before t; the forward move is measured from the
bar-t price to t+H, strictly after. Imbalance is z-normalized per market (scale-free) and pooled.
Reports the raw imbalance->move correlation SIGN so a taker_side mapping flip is visible, the mean
forward directional move (gross + net of a worst-case round-trip taker fee) and hit-rate conditional
on strong imbalance, a large-trade (toxicity) slice, and a per-quantile monotonicity view.

Note: the tape carries trade prices, not bid/ask, so "price" is last-trade and the round-trip fee
stands in for the spread — a feasibility measure, not a fill model. Read-only DB (psycopg). Usage:
    {"type":"script","name":"oflow_study","args":["--bar-s","10"],"id":"oflow-1"}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from statistics import mean, pstdev

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

MIN_SAMPLES = 500  # below this the feasibility read is provisional


def taker_fee_c(p_c: float) -> float:
    p_c = min(99.0, max(1.0, p_c))
    return math.ceil(7.0 * (p_c / 100.0) * (1 - p_c / 100.0))


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return float("nan")
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / math.sqrt(sx * sy)


def build_flow_bars(trades: list[tuple[float, float, float, float]], bar_s: int) -> dict[int, list[float]]:
    """{bar_idx: [last_price, net_flow, big_net_flow]} from time-ordered
    (ts, prob_cents, signed_size, big_signed_size) rows."""
    bars: dict[int, list[float]] = {}
    for ts, p, sf, bsf in trades:
        b = int(ts) // bar_s
        cur = bars.get(b)
        if cur is None:
            bars[b] = [p, sf, bsf]
        else:
            cur[0] = p  # last trade price in the bar
            cur[1] += sf
            cur[2] += bsf
    return bars


def contiguous(bars: dict[int, list[float]]) -> tuple[list[float], list[float], list[float]]:
    """(price[], flow[], big_flow[]) over [min_bar, max_bar], price forward-filled, flow 0 on gaps."""
    if not bars:
        return [], [], []
    lo, hi = min(bars), max(bars)
    price: list[float] = []
    flow: list[float] = []
    big: list[float] = []
    last = bars[lo][0]
    for t in range(lo, hi + 1):
        cur = bars.get(t)
        if cur is not None:
            last = cur[0]
        price.append(last)
        flow.append(cur[1] if cur else 0.0)
        big.append(cur[2] if cur else 0.0)
    return price, flow, big


def signed(side: str | None) -> float:
    s = (side or "").strip().lower()
    if s == "yes":
        return 1.0
    if s == "no":
        return -1.0
    return 0.0  # unknown side -> updates price but contributes no flow


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OFLOW within-market order-flow feasibility probe")
    ap.add_argument("--bar-s", type=int, default=10, help="bar size (seconds)")
    ap.add_argument("--trail-bars", type=int, default=3, help="trailing imbalance window (bars)")
    ap.add_argument("--horizon-bars", type=int, default=6, help="forward move horizon (bars)")
    ap.add_argument("--zthr", type=float, default=1.0, help="strong-imbalance |z| threshold")
    ap.add_argument("--big-thr", type=float, default=10.0, help="large-trade size (contracts)")
    ap.add_argument("--min-bars", type=int, default=30, help="skip markets with fewer bars")
    ap.add_argument("--min-ev-cents", type=float, default=2.0, help="net EV bar that counts as signal")
    args = ap.parse_args(argv)

    print("OFLOW — within-market order-flow alpha on Kalshi's tape? (feasibility, read-only)\n")

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    import psycopg

    per_market: dict[int, list[tuple[float, float, float, float]]] = {}
    n_kal = 0
    n_side = 0
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT match_id, EXTRACT(EPOCH FROM traded_at), team_prob_cents, size, taker_side"
                " FROM game_tape_snapshots"
                " WHERE venue='kalshi' AND team_prob_cents IS NOT NULL"
                " ORDER BY match_id, traded_at ASC"
            )
            for match_id, ts, prob, size, side in cur.fetchall():
                n_kal += 1
                sg = signed(side)
                if sg != 0.0:
                    n_side += 1
                sz = float(size) if size is not None else 1.0
                ssize = sg * sz
                bsize = ssize if sz >= args.big_thr else 0.0
                per_market.setdefault(match_id, []).append((float(ts), float(prob), ssize, bsize))

    side_pct = 100.0 * n_side / n_kal if n_kal else 0.0
    print(f"kalshi tape rows: {n_kal}  |  with usable taker_side: {n_side} ({side_pct:.0f}%)  "
          f"|  markets: {len(per_market)}")
    if n_kal == 0:
        print("\nVERDICT: HOLD (NO DATA) — no Kalshi tape rows. game_tape_snapshots may only hold "
              "Polymarket rows, or the collector never stored the Kalshi venue. Not a kill.")
        return 0
    if n_side < MIN_SAMPLES:
        print("\nVERDICT: HOLD (P0 FAIL) — too few trades carry a usable taker_side to compute "
              f"aggressor flow ({n_side} < {MIN_SAMPLES}). The tape can't answer the flow question; "
              "aggressor side wasn't stored densely enough. Not a kill.")
        return 0

    # ---- build pooled (z-imbalance, forward-move) samples, per-market normalized ----
    zs: list[float] = []
    ms: list[float] = []
    bands: list[float] = []
    zbig: list[float] = []
    W, H = args.trail_bars, args.horizon_bars
    print(f"\n  {'match_id':>9} {'trades':>7} {'bars':>6} {'samples':>8} {'corr(I,M)':>10}")
    for mid, trades in sorted(per_market.items()):
        price, flow, big = contiguous(build_flow_bars(trades, args.bar_s))
        if len(price) < args.min_bars:
            continue
        Is: list[float] = []
        Ibig: list[float] = []
        Ms: list[float] = []
        Bs: list[float] = []
        for t in range(W, len(price) - H):
            Is.append(sum(flow[t - W:t]))       # strictly before t
            Ibig.append(sum(big[t - W:t]))
            Ms.append(price[t + H] - price[t])  # forward from bar-t price
            Bs.append(price[t])
        if len(Is) < 10:
            continue
        sd = pstdev(Is) or 1.0
        sdb = pstdev(Ibig) or 1.0
        mcorr = pearson(Is, Ms)
        for imb, imb_big, mv, bnd in zip(Is, Ibig, Ms, Bs, strict=True):
            zs.append(imb / sd)
            zbig.append(imb_big / sdb)
            ms.append(mv)
            bands.append(bnd)
        print(f"  {mid:>9} {len(trades):>7} {len(price):>6} {len(Is):>8} {mcorr:>10.3f}")

    n = len(zs)
    if n < 10:
        print("\nVERDICT: HOLD (P0 FAIL) — not enough aligned bar samples across markets.")
        return 0

    # ---- pooled analysis ----
    corr = pearson(zs, ms)

    def cond_stats(zsig: list[float]) -> tuple[int, float, float, float]:
        """(n, hit%, mean gross directional c, mean net directional c) for |zsig|>=zthr."""
        gross: list[float] = []
        net: list[float] = []
        for z, m, b in zip(zsig, ms, bands, strict=True):
            if abs(z) < args.zthr:
                continue
            d = m * (1.0 if z > 0 else -1.0)
            gross.append(d)
            net.append(d - 2.0 * taker_fee_c(b))
        if not gross:
            return 0, float("nan"), float("nan"), float("nan")
        hit = 100.0 * sum(1 for g in gross if g > 0) / len(gross)
        return len(gross), hit, mean(gross), mean(net)

    sn, shit, sgross, snet = cond_stats(zs)
    bn, bhit, bgross, bnet = cond_stats(zbig)

    print(f"\n  pooled samples: {n}   corr(imbalance z, forward move): {corr:+.3f}")
    print(f"  {'signal':>16} {'n':>6} {'hit%':>6} {'gross c':>8} {'net c':>8}")
    print(f"  {'all-trade flow':>16} {sn:>6} {shit:>6.1f} {sgross:>+8.2f} {snet:>+8.2f}")
    print(f"  {'large-trade flow':>16} {bn:>6} {bhit:>6.1f} {bgross:>+8.2f} {bnet:>+8.2f}")

    # quantile monotonicity (P3): mean forward move by imbalance quintile
    print("\n  forward move by imbalance quintile (monotone rising => flow predicts direction):")
    order = sorted(range(n), key=lambda i: zs[i])
    q = max(1, n // 5)
    for k in range(5):
        idx = order[k * q:(k + 1) * q] if k < 4 else order[k * q:]
        if not idx:
            continue
        zmean = mean(zs[i] for i in idx)
        mmean = mean(ms[i] for i in idx)
        print(f"    Q{k + 1}  z~{zmean:+6.2f}  mean fwd move {mmean:+6.2f}c   (n={len(idx)})")

    # ---- verdict ----
    print("\n== verdict (FEASIBILITY: within-market order-flow -> next move, net of round-trip) ==")
    provisional = " (PROVISIONAL — n small)" if sn < MIN_SAMPLES else ""
    p1_pass = (not math.isnan(snet)) and snet >= args.min_ev_cents and shit > 50.0 and corr > 0
    p1_kill = (not math.isnan(snet)) and (snet <= 0.0 or shit <= 50.0)
    print(f"  P1 strong-imbalance net directional move >= {args.min_ev_cents:g}c AND hit>50%: "
          f"net={snet:+.2f}c hit={shit:.1f}% corr={corr:+.3f}{provisional}")
    if p1_pass:
        print("  VERDICT: SIGNAL FOUND — order-flow imbalance predicts the next move net of cost. "
              "The family is ALIVE: justify building per-candidate tape/book collection for "
              "tradeable markets (MMSELL_FILL_MODEL.md follow-up #2), then re-probe LIVE tradeable "
              "markets before any book. (No book off this closed WC slice.)")
    elif p1_kill:
        print("  VERDICT: KILL (family ruled out) — flow imbalance does NOT predict the next move "
              "net of the taker round-trip: the quote already reflects the flow / it is retail "
              "noise / cost eats it. Order-flow-as-signal is closed on Kalshi's tape, and the "
              "per-market microstructure collection is NOT worth building. Clean ruling-out — the "
              "expected result given the LOW prior, and the whole point of running it cheaply.")
    else:
        print("  VERDICT: GREY / HOLD — net EV between 0 and the bar, or correlation weak. Not a "
              "clean signal; treat as leaning-KILL unless a larger live tape sharpens it. Do not "
              "build collection on this alone.")
    print(f"  (toxicity check P2: large-trade net {bnet:+.2f}c vs all-trade {snet:+.2f}c — "
          f"{'corroborates' if bnet > snet else 'no toxicity gradient'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
