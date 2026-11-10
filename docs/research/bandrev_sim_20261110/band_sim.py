"""BANDREV offline model — the operator's band design on a FAIR (martingale) in-play binary.

Not an ops probe and not evidence about Kalshi: a stylized model used in
docs/IDEA_MODEL_20261110_BANDREV.md to show (1) a fair market gives the design zero gross edge,
(2) where the losses come from (stop fees, spread, jump gaps), and (3) how large a transient
over-reaction would have to be before any variant pays. The real-tape test is
scripts/bandrev_probe.py.

Model: an NFL-like score margin on a 0.1-point lattice, 600 six-second steps (60 game minutes).
Each step draws a small play-by-play move (sd 0.27 pt) or, rarely, a surprise score (+-7 pt at
0.16%/side, +-3 pt at 0.12%/side). Final margin sd ~12 pt. The price is the exact win probability
(dynamic programme), so it is a martingale. Optional AR(1) "over-reaction" noise is added on top.

Run: python docs/research/bandrev_sim_20261110/band_sim.py  (numpy; ~10 s)
"""

from __future__ import annotations

import math

import numpy as np

T = 600     # 6-second steps
STEP = 5    # steps per 30 s
RNG = np.random.default_rng(11)


def _small(sd_units: float, maxk: int) -> dict[int, float]:
    ks = np.arange(-maxk, maxk + 1)
    w = np.exp(-0.5 * (ks / sd_units) ** 2)
    w /= w.sum()
    return {int(k): float(p) for k, p in zip(ks, w, strict=True)}


def _mix(small: dict[int, float], jumps: dict[int, float]) -> dict[int, float]:
    pj = sum(jumps.values())
    m = {k: v * (1 - pj) for k, v in small.items()}
    for k, v in jumps.items():
        m[k] = m.get(k, 0.0) + v
    return m


NFL = _mix(_small(2.7, 10), {70: .0016, -70: .0016, 30: .0012, -30: .0012})
SMOOTH = _small(4.9, 20)  # same total variance, no jumps (the control)


def build(inc: dict[int, float], x_max: int = 1500):
    ks = np.array(list(inc))
    ps = np.array([inc[k] for k in ks])
    ps = ps / ps.sum()
    xs = np.arange(-x_max, x_max + 1)
    p = np.zeros((T + 1, len(xs)))
    p[T] = (xs > 0) + 0.5 * (xs == 0)
    idx = np.arange(len(xs))
    for t in range(T - 1, -1, -1):
        nxt = np.zeros(len(xs))
        for k, pk in zip(ks, ps, strict=True):
            nxt += pk * p[t + 1][np.clip(idx + k, 0, len(xs) - 1)]
        p[t] = nxt
    return ks, ps, x_max, p


def taker_fee(pc: float, q: int) -> float:
    p = pc / 100
    return math.ceil(0.07 * q * p * (1 - p) * 100) / q


def maker_fee(pc: float, coeff: float) -> float:
    p = pc / 100
    return coeff * 100 * p * (1 - p)


def paths(model, n: int, amp: float = 0.0, hl: float = 4):
    ks, ps, x_max, p = model
    steps = RNG.choice(ks, size=(n, T), p=ps)
    x = np.clip(np.concatenate([np.zeros((n, 1), int), np.cumsum(steps, 1)], 1), -x_max, x_max)
    fair = p[np.arange(T + 1)[None, :], x + x_max] * 100
    mid = fair.copy()
    if amp:
        phi = 0.5 ** (1 / (hl * STEP))
        e = np.zeros((n, T + 1))
        s = amp * math.sqrt(1 - phi * phi)
        for t in range(1, T + 1):
            e[:, t] = phi * e[:, t - 1] + RNG.normal(0, s, n)
        mid = np.clip(fair + e * fair * (100 - fair) / 2500, 1, 99)
        mid[:, -1] = fair[:, -1]
    return mid, fair


def run(mid, fair, d, tp, stop, *, clip=10, cm=0.0, hs=1.0, fees=True, window=(30, 70), last=500):
    """Rest YES bid at ref-d and NO bid at ref+d; on fill rest a take-profit at entry+tp (maker)
    and stop with a taker sell when our bid <= entry-stop. tp/stop None disables. Re-arm on exit."""
    out, gapin, slip = [], [], []
    for g in range(mid.shape[0]):
        m, f = mid[g], fair[g]
        flat, ref = True, m[0]
        for t in range(1, T + 1):
            px = m[t]
            if flat:
                if t > last or not (window[0] <= px <= window[1]):
                    ref = px
                    continue
                if px <= ref - d:
                    side, entry = 1, ref - d
                elif px >= ref + d:
                    side, entry = -1, ref + d
                else:
                    continue
                e = entry if side == 1 else 100 - entry
                gapin.append((f[t] if side == 1 else 100 - f[t]) - e)
                fin = maker_fee(e, cm) if fees else 0.0
                flat = False
                continue
            v = px if side == 1 else 100 - px
            if t == T:
                out.append(((f[T] if side == 1 else 100 - f[T]) - e - fin, "hold"))
            elif tp is not None and v >= e + tp:
                out.append((tp - fin - (maker_fee(e + tp, cm) if fees else 0.0), "tp"))
            elif stop is not None and v - hs <= e - stop:
                xp = max(1.0, v - hs)
                slip.append(xp - (e - stop))
                out.append((xp - e - fin - (taker_fee(xp, clip) if fees else 0.0), "stop"))
            else:
                continue
            flat, ref = True, px
    pnl = np.array([o[0] for o in out])
    kinds = [o[1] for o in out]
    n = len(pnl)
    return {"per_game": n / mid.shape[0], "ev": pnl.mean(), "se": pnl.std() / math.sqrt(n),
            "win": (pnl > 0).mean(), "tp": kinds.count("tp") / n, "stop": kinds.count("stop") / n,
            "hold": kinds.count("hold") / n, "cgame": pnl.sum() / mid.shape[0],
            "gapin": float(np.mean(gapin)), "slip": float(np.mean(slip)) if slip else 0.0}


def row(lbl: str, r: dict) -> None:
    print(f"{lbl:30s} trips/g {r['per_game']:5.2f}  EV {r['ev']:+6.2f}c (se {r['se']:.2f})  "
          f"win {r['win'] * 100:3.0f}%  TP {r['tp'] * 100:3.0f}% STOP {r['stop'] * 100:3.0f}% "
          f"HOLD {r['hold'] * 100:3.0f}%  entry-gap {r['gapin']:+5.2f}c  stop-slip {r['slip']:+5.2f}c  "
          f"per-game {r['cgame']:+6.1f}c")


def main() -> None:
    n = 1500
    nfl, smooth = build(NFL), build(SMOOTH)
    for name, mdl in (("SMOOTH (no jumps) control", smooth), ("NFL-like (jumps)", nfl)):
        mid, fair = paths(mdl, n)
        dm = np.abs(mid[:, STEP:500:STEP] - mid[:, :500 - STEP:STEP])
        print(f"\n### {name}: median |30s move| {np.median(dm):.2f}c  p90 {np.percentile(dm, 90):.2f}c  "
              f"p99 {np.percentile(dm, 99):.2f}c")
        print(" frictionless (no fee, no spread):")
        for d, tp, st in ((5, 10, 2), (5, 5, 5), (8, 16, 3)):
            row(f"  d={d} tp={tp} stop={st}", run(mid, fair, d, tp, st, fees=False, hs=0))
        row("  d=5 tp=10 no stop", run(mid, fair, 5, 10, None, fees=False, hs=0))
        print(" with fees (taker stop, clip 10, maker entry free) + 1c half-spread:")
        for d, tp, st in ((3, 3, 1), (5, 10, 2), (5, 5, 5), (8, 16, 3), (10, 20, 5)):
            row(f"  d={d} tp={tp} stop={st}", run(mid, fair, d, tp, st))
        row("  d=5 tp=10 NO stop", run(mid, fair, 5, 10, None))
        row("  d=5 tp=10 stop=2 maker.0175", run(mid, fair, 5, 10, 2, cm=.0175))
    print("\n### NFL-like + transient OVER-REACTION noise (amp = sd in c at 50c, hl in 30 s units)")
    for amp in (2, 4, 6, 10):
        for hl in (2, 8):
            mid, fair = paths(nfl, n // 2, amp=amp, hl=hl)
            for d, tp, st in ((5, 10, 2), (5, 5, None), (5, None, None)):
                row(f"amp={amp:2d}c hl={hl} d={d} tp={tp} st={st}", run(mid, fair, d, tp, st))


if __name__ == "__main__":
    main()
