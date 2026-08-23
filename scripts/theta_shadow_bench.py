"""FIT and MEMORY cost of the paper shadow. **This does not measure PostgreSQL.**

WHAT THIS IS FOR. The replacement model is research riding along inside the loop that prices
real money, and "it's only telemetry" is not a latency argument. This bounds the ARITHMETIC half
of that cost — how many fits a cycle performs, how long they take, how much the close set holds —
deterministically and without a database.

WHAT IT IS NOT. The close set is generated in-process, so the "load" line measures materialising
~43,000 closes per product into Python objects. **Production adds the Postgres round trip, the
row decode and whatever the database is doing at that moment**, and that is the dominant term.
A number from this script is a LOWER BOUND on the production cost and must not be quoted as a
production measurement.

WHERE THE PRODUCTION NUMBER COMES FROM. The worker logs one `theta: shadow cost` line per cycle
with `theta_shadow_ms` (TOTAL: load + decode + construction + fits), `theta_shadow_load_ms`,
`theta_shadow_loads` and `theta_shadow_fits`. Collect it over several hourly reloads with:

    {"type":"logs","service":"main","filter":"theta: shadow cost"}

and report p50/p90/p99/max of `theta_shadow_ms` before calling any budget production-derived.

WHAT BOUNDS THE COST, in order of how much it matters:

  1  the close set is HELD FOR ITS REFIT ANCHOR rather than reloaded every cycle. This is the
     dominant term and it is database work on the trading loop's thread.
  2  the HORIZON GRID caps fits at len(H_BUCKETS) per product per anchor, so a 240-strike ladder
     costs the same as a 6-strike one.
  3  `theta_spliced_budget_ms` bounds TOTAL shadow elapsed time — load, decode, construction and
     fits — and the load additionally carries a database statement timeout, because a
     Python-side deadline cannot interrupt a query that has already been issued. Over budget
     means metadata without a probability, and the scan continues.

Run it directly; it is not an ops script.
"""

from __future__ import annotations

import argparse
import math
import random
import time
import tracemalloc

from kalshi_bot.theta import tailmodel as tm
from kalshi_bot.theta.spot import SpotModel

# theta_snapshot_rows_cap bounds rows WRITTEN per cycle; the scan prices every strike inside
# `theta_snapshot_max_minutes` before that cap applies, and a busy hour publishes several
# ladders per product. 480 is deliberately above what has been observed.
MAX_MARKETS_PER_CYCLE = 480
PRODUCTS = ("BTC", "ETH")


def synthetic_closes(days: float, seed: int) -> dict[int, float]:
    """A full window of 1-minute closes, same size and density as the real store."""
    rng = random.Random(seed)
    base = 1_700_000_000 // 60 * 60
    px, out = 65000.0, {}
    for i in range(int(days * 1440)):
        px *= math.exp(rng.gauss(0.0, 0.0006))
        out[base + i * 60] = px
    return out


class _Shadow:
    """The tracker's shadow path, isolated from the trading loop it normally sits inside.

    Deliberately the same three calls in the same order as `ThetaTracker._spliced` — bucket,
    block_sample, build — with the same per-cycle cache key. Benchmarking a reimplementation
    would measure the wrong thing.
    """

    def __init__(self, model: SpotModel, fit_days: float, tail_q: float, budget_ms: float):
        self.model, self.fit_days, self.tail_q, self.budget_ms = model, fit_days, tail_q, budget_ms
        self.cache: dict[tuple, object] = {}
        self.ms = 0.0
        self.fits = 0
        self.hits = 0
        self.withheld = 0

    def spliced(self, now_unix: int, tte_min: float):
        h = tm.h_bucket(max(1.0, tte_min))
        anchor = tm.refit_anchor(now_unix)
        key = (anchor, h)
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        if self.budget_ms > 0 and self.ms >= self.budget_ms:
            self.withheld += 1
            return None
        t0 = time.perf_counter()
        blocks, meta = self.model.block_sample(anchor, h, window_days=self.fit_days)
        actual = (max(self.model.closes) - min(self.model.closes)) / 86400.0
        built = tm.build(blocks, h, tail_q=self.tail_q,
                         fit_days=min(self.fit_days, actual), requested_fit_days=self.fit_days,
                         expected_blocks=meta["expected_blocks"],
                         max_gap_min=meta["max_gap_min"])
        self.ms += (time.perf_counter() - t0) * 1000.0
        self.fits += 1
        self.cache[key] = built
        return built


def run(fit_days: float, tail_q: float, budget_ms: float, markets: int, cycles: int,
        seed: int) -> None:
    print(f"fit_days={fit_days:g}  tail_q={tail_q:g}  budget_ms={budget_ms:g}  "
          f"markets/cycle={markets}  cycles={cycles}  products={len(PRODUCTS)}")
    print(f"horizon grid: {tm.H_BUCKETS}  -> at most {len(tm.H_BUCKETS)} fits per product/cycle")
    print(f"refit anchor: {tm.REFIT_ANCHOR_SECONDS}s  -> at most "
          f"{len(tm.H_BUCKETS) * len(PRODUCTS)} fits per HOUR across both products")
    print()

    tracemalloc.start()
    t0 = time.perf_counter()
    closes = {p: synthetic_closes(fit_days, seed + i) for i, p in enumerate(PRODUCTS)}
    load_ms = (time.perf_counter() - t0) * 1000.0
    rows = sum(len(c) for c in closes.values())
    per_load_ms = load_ms / len(PRODUCTS)

    t0 = time.perf_counter()
    models = {p: SpotModel(c, trail_days=fit_days) for p, c in closes.items()}
    build_ms = (time.perf_counter() - t0) * 1000.0
    peak_after_load = tracemalloc.get_traced_memory()[1]

    rng = random.Random(seed)
    totals = {"ms": 0.0, "fits": 0, "hits": 0, "withheld": 0, "priced": 0, "loads": 0}
    cycle_ms: list[float] = []
    shadows = {p: _Shadow(models[p], fit_days, tail_q, budget_ms) for p in PRODUCTS}
    seen_anchor: dict[str, int] = {}
    for c in range(cycles):
        # Cycles run FORWARD at the scan cadence, so the hour turns partway through and the
        # benchmark sees both the cached and the refitting case.
        now = min(closes["BTC"]) + int(fit_days * 86400) - 9000 + c * 300
        anchor = tm.refit_anchor(now)
        per_cycle = 0.0
        for p in PRODUCTS:
            # The close-set load, held for the refit anchor exactly as the tracker holds it.
            if seen_anchor.get(p) != anchor:
                seen_anchor[p] = anchor
                per_cycle += per_load_ms
                totals["loads"] += 1
            sh = shadows[p]
            sh.ms = 0.0                       # the budget is per CYCLE
            spot = models[p].spot_at(now)
            for _ in range(markets // len(PRODUCTS)):
                tte = rng.uniform(10.0, 35.0)
                sm = sh.spliced(now, tte)
                if sm is not None:
                    tm.p_yes(sm, spot, "greater", spot * (1.0 + rng.uniform(0.002, 0.01)), None)
                    totals["priced"] += 1
            per_cycle += sh.ms
            totals["ms"] += sh.ms
        cycle_ms.append(per_cycle)
    for sh in shadows.values():
        for k in ("fits", "hits", "withheld"):
            totals[k] += getattr(sh, k)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    quotes = cycles * markets
    ordered = sorted(cycle_ms)
    p50 = ordered[len(ordered) // 2]
    print(f"  {'quantity':<44} {'value':>16}")
    print("  " + "-" * 64)
    print(f"  {'closes per load (both products)':<44} {rows:>16,}")
    print(f"  {'close-set loads performed':<44} {totals['loads']:>16,}")
    print(f"  {'loads if reloaded every cycle':<44} {cycles * len(PRODUCTS):>16,}")
    print(f"  {'cost of one close-set load':<44} {per_load_ms:>13,.0f} ms")
    print(f"  {'SpotModel construction (all products)':<44} {build_ms:>13,.0f} ms")
    print(f"  {'quotes priced by the shadow':<44} {totals['priced']:>16,}")
    print(f"  {'distinct GPD fits performed':<44} {totals['fits']:>16,}")
    print(f"  {'fit cache hit rate':<44} "
          f"{totals['hits'] / max(1, totals['hits'] + totals['fits']):>15.1%}")
    print(f"  {'fits per market priced':<44} {totals['fits'] / max(1, quotes):>16.4f}")
    print(f"  {'shadow ms per cycle  p50':<44} {p50:>13,.1f} ms")
    print(f"  {'shadow ms per cycle  p90':<44} "
          f"{ordered[int(0.9 * (len(ordered) - 1))]:>13,.1f} ms")
    print(f"  {'shadow ms per cycle  MAX':<44} {ordered[-1]:>13,.1f} ms")
    print(f"  {'shadow ms per market priced':<44} "
          f"{sum(cycle_ms) / max(1, quotes):>13,.3f} ms")
    print(f"  {'markets withheld on budget':<44} {totals['withheld']:>16,}")
    print(f"  {'peak memory, one product close set':<44} "
          f"{peak_after_load / len(PRODUCTS) / 1e6:>13,.1f} MB")
    print(f"  {'peak memory overall':<44} {peak / 1e6:>13,.1f} MB")
    print()
    print("  Scan cadence is theta_interval_minutes = 5 min = 300,000 ms. The shadow's median")
    print(f"  cycle costs {p50:,.0f} ms ({p50 / 300_000:.4%} of the interval) and its worst cycle")
    print(f"  — the one where the hour turns and everything refits — costs "
          f"{ordered[-1]:,.0f} ms ({ordered[-1] / 300_000:.3%}).")
    print()
    print("  WHAT BOUNDS IT, in order of how much it matters:")
    print(f"  1. the close-set load is held for the refit anchor: {totals['loads']} loads instead")
    print(f"     of {cycles * len(PRODUCTS)}. This is the dominant cost and it is DATABASE work on the")
    print("     trading loop's thread, not arithmetic — an order of magnitude above the fits.")
    print(f"  2. the horizon grid caps fits at {len(tm.H_BUCKETS)} per product per anchor, so a")
    print(f"     240-strike ladder costs the same as a 6-strike one: {totals['fits']} fits for")
    print(f"     {totals['priced']:,} quotes.")
    if budget_ms > 0:
        print(f"  3. the {budget_ms:g} ms per-cycle budget is the backstop for a pathological window. "
              + ("Never reached here"
                 if totals["withheld"] == 0 else
                 f"REACHED: {totals['withheld']:,} markets recorded metadata with no probability")
              + f"; the worst measured cycle spends {ordered[-1]:,.0f} ms.")
        if ordered[-1] > budget_ms * 0.8:
            print("     WARNING: the worst cycle is within 20% of the budget. A backstop that fires")
            print("     in normal operation is not a backstop — it would gap the research series")
            print("     every hour. Raise it above the measured maximum, or reduce the work.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fit-days", type=float, default=tm.FROZEN_FIT_DAYS)
    ap.add_argument("--tail-q", type=float, default=tm.FROZEN_TAIL_Q)
    ap.add_argument("--budget-ms", type=float, default=3000.0)
    ap.add_argument("--markets", type=int, default=MAX_MARKETS_PER_CYCLE)
    ap.add_argument("--cycles", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20260822)
    args = ap.parse_args(argv)
    run(args.fit_days, args.tail_q, args.budget_ms, args.markets, args.cycles, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
