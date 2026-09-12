"""PERPMM recon census — can a passive (both-legs-maker) premium-reversion variant on Kalshi
perps be graded from the candle feed, and how much passive fill opportunity is there?
(idea-model run 2026-09-12, docs/PERPMM_THESIS.md)

WHY THIS CENSUS EXISTS
----------------------
PERP-V1 (docs/PERP_V1_THESIS.md, RETIRED 2026-09-02) found the premium-reversion mechanism is
real on liquid perp books — +6.75 bps gross on KXBTCPERP quoting 0.52 bps of spread — and that
the taker/taker arm dies to a 24 bps tier-0 round trip. The 2026-09-03 per-ticker split named
exactly one surviving thread: a both-legs-passive variant paying 4 bps of maker fee, worth
roughly +3 to +8 bps/trade BEFORE fills, and said it "cannot be evaluated on this instrument:
the next thing it needs is a trade-print tape and a fill model, which is a build, not a query."

Probe 0 found no trade-print endpoint on the perp surface, but it did find
`/margin/markets/{t}/candlesticks` (bid, ask, price, open_interest, volume per period). A
1-minute candle with a HIGH and LOW is the standard conservative fill model for a resting order:
a bid at p is assumed filled only if the minute traded strictly THROUGH p (low < p), never on a
touch. This census answers, before anyone builds anything:

  C1 FIELDS   — does the perp candle carry high/low (fill model feasible) or only closes
                (prints needed → BLOCKED_DATA, not a probe)? Keys are dumped verbatim so the
                answer is an observation, not a guess.
  C2 ACTIVITY — fraction of 1-minute candles with volume > 0 per ticker (a passive order can
                only fill in an active minute), and volume/notional per active minute.
  C3 RANGE    — mean intra-minute range in bps vs mean quoted spread in bps: a resting order one
                spread away from mid can only fill if minutes routinely trade through it.
  C4 VENUE AGE — the gold/silver perps (launched 2026-09-09) are reported but flagged: under
                ~2 months old they are HOLD-by-default whatever they show.

Read-only, unauthenticated public REST (the runner holds no Kalshi key). Stdlib only. Usage:
    {"type":"script","name":"perp_candle_census","args":["--hours","48"],"id":"perpmm-0"}
"""

from __future__ import annotations

import argparse
import re
import time

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

BASE = "https://api.elections.kalshi.com/trade-api/v2"
CORE = re.compile(r"^KX(BTC|ETH|SOL|XRP)PERP$", re.I)
METAL = re.compile(r"^(KX)?(GOLD|SILVER|XAU|XAG)PERP$", re.I)
ACTIVE_FLOOR = 0.50   # share of active minutes on BTC/ETH for a candle fill model to be usable


def flatten_keys(obj, prefix: str = "") -> list[str]:
    """Dotted key paths of a nested dict (lists are not descended), sorted."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}{k}"
            out.append(path)
            out.extend(flatten_keys(v, path + "."))
    return sorted(out)


def has_high_low(keys: list[str]) -> bool:
    ks = {k.lower() for k in keys}
    return any(k.endswith("high") or k.endswith("high_dollars") for k in ks) and any(
        k.endswith("low") or k.endswith("low_dollars") for k in ks)


def _field(c: dict, group: str, name: str) -> float:
    """Candle sub-field, tolerant of the two shapes seen on Kalshi: nested
    {"price": {"close": ..}} / {"price": {"close_dollars": ..}} or flat "price_close"."""
    g = c.get(group)
    if isinstance(g, dict):
        return xl._num(g.get(f"{name}_dollars")) or xl._num(g.get(name))
    return xl._num(c.get(f"{group}_{name}_dollars")) or xl._num(c.get(f"{group}_{name}"))


def summarize(cs: list[dict]) -> dict:
    n = len(cs)
    active = [c for c in cs if xl._num(c.get("volume")) > 0 or xl._num(c.get("volume_fp")) > 0]
    spreads, ranges = [], []
    for c in cs:
        px = _field(c, "price", "close")
        bid, ask = _field(c, "bid", "close"), _field(c, "ask", "close")
        if px > 0 and bid > 0 and ask > bid:
            spreads.append((ask - bid) / px * 1e4)
        hi, lo = _field(c, "price", "high"), _field(c, "price", "low")
        if px > 0 and hi > 0 and lo > 0 and hi >= lo:
            ranges.append((hi - lo) / px * 1e4)
    vol = [xl._num(c.get("volume")) or xl._num(c.get("volume_fp")) for c in active]
    notional = [xl._num(c.get("volume_notional")) or xl._num(c.get("volume_notional_dollars"))
                for c in active]
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
    return {"n": n, "active": len(active), "active_share": (len(active) / n) if n else 0.0,
            "spread_bps": mean(spreads), "range_bps": mean(ranges),
            "vol_per_active_min": mean(vol), "notional_per_active_min": mean(notional)}


def perp_markets() -> list[dict]:
    data = xl._get(f"{BASE}/margin/markets?limit=200")
    return (data or {}).get("markets") or []


def perp_candles(ticker: str, start: int, end: int) -> list[dict]:
    data = xl._get(f"{BASE}/margin/markets/{ticker}/candlesticks"
                   f"?start_ts={start}&end_ts={end}&period_interval=1")
    return (data or {}).get("candlesticks") or []


def _fmt(v, unit: str = "") -> str:
    return "n/a" if v is None else f"{v:.2f}{unit}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PERPMM recon census (read-only)")
    ap.add_argument("--hours", type=int, default=48, help="look-back for 1-minute candles")
    ap.add_argument("--tickers", default="", help="comma list; default = core crypto + metals")
    args = ap.parse_args(argv)

    print("PERPMM recon census — is a candle-based passive fill model feasible on Kalshi perps? "
          "(read-only, unauthenticated)")
    listed = perp_markets()
    tickers_live = {(m.get("ticker") or "").upper(): m for m in listed}
    print(f"perp markets listed: {len(listed)}")
    if args.tickers:
        wanted = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        wanted = [t for t in tickers_live if CORE.search(t)] + [t for t in tickers_live
                                                                 if METAL.search(t)]
    if not wanted:
        print("\nVERDICT: BLOCKED — no perp tickers matched (list empty or naming changed). "
              "Dump above; not a kill.")
        return 0

    end = int(time.time())
    start = end - args.hours * 3600
    rows = []
    first_keys: list[str] = []
    for t in wanted:
        cs = perp_candles(t, start, end)
        if cs and not first_keys:
            first_keys = flatten_keys(cs[0])
        s = summarize(cs)
        s["ticker"] = t
        s["listed"] = t in tickers_live
        s["metal"] = bool(METAL.search(t))
        rows.append(s)
        time.sleep(0.1)

    print("\n== C1 candle fields (verbatim from the first candle returned) ==")
    if first_keys:
        for k in first_keys:
            print(f"  {k}")
    else:
        print("  (no candles returned for any ticker in the window)")
    hl = has_high_low(first_keys)
    print(f"  high/low present: {hl}")

    print(f"\n== C2/C3 activity + range over the last {args.hours} h (1-minute candles) ==")
    print(f"  {'ticker':14s} {'n':>5s} {'active':>7s} {'share':>6s} {'spread':>9s} "
          f"{'range':>9s} {'vol/min':>9s} {'notional/min':>13s}  note")
    for s in rows:
        note = "NEW VENUE (<2 mo) — HOLD by default" if s["metal"] else ""
        if not s["listed"]:
            note = "NOT LISTED"
        print(f"  {s['ticker']:14s} {s['n']:5d} {s['active']:7d} {s['active_share']:6.0%} "
              f"{_fmt(s['spread_bps'], 'bps'):>9s} {_fmt(s['range_bps'], 'bps'):>9s} "
              f"{_fmt(s['vol_per_active_min']):>9s} {_fmt(s['notional_per_active_min']):>13s}  "
              f"{note}")

    core = [s for s in rows if CORE.search(s["ticker"]) and s["ticker"] in ("KXBTCPERP",
                                                                             "KXETHPERP")]
    core_active = all(s["active_share"] >= ACTIVE_FLOOR for s in core) if core else False
    print("\n== census verdict (pre-stage; the PERPMM probe follows only if this promotes) ==")
    print(f"  fill model feasible from candles (high/low present): {hl}")
    print(f"  BTC+ETH active-minute share >= {ACTIVE_FLOOR:.0%}: {core_active}")
    print("  cost reference: tier-0 maker 2.0 bps/side → 4 bps round trip; the thesis bar is "
          "net >= +2 bps/trade AFTER the through-price fill haircut.")
    if hl and core_active:
        print("  VERDICT: PROMOTE-TO-PROBE — build the candle-backed passive replay "
              "(through-price fills only) as PERPMM probe 1; core crypto tickers only, metals "
              "co-measured but HOLD under the venue-age rule.")
    elif not hl:
        print("  VERDICT: BLOCKED_DATA — candles carry no high/low, so a resting-order fill "
              "cannot be inferred without prints. Record it; do not build a probe that would "
              "manufacture fills from closes (the mmsell6/mmsell11 lesson).")
    else:
        print("  VERDICT: HOLD — high/low exist but the liquid books are quiet at 1-minute "
              "resolution; passive fills would be rare. Re-run over a longer window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
