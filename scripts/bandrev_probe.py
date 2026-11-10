"""BANDREV — do near-50/50 in-play binaries over-react and snap back far enough to trade a band?
(operator idea 2026-11-09, screen docs/IDEA_MODEL_20261110_BANDREV.md; gate docs/BANDREV_PREREG.md)

The operator's design: on a ~50/50 binary, rest a YES bid at ref-d and a NO bid at ref+d; once
filled, rest a take-profit on the far side and protect the entry with a stop at (about) the entry
price; re-arm after every exit. In a fair (martingale) market that design has zero gross edge, so
the only thing it can harvest is TRANSIENT OVER-REACTION: a move that partly reverts within
minutes. This probe measures that directly, per market class, on Kalshi's own 1-minute candles:

  A  reversion after a >=5c mid move inside 3 minutes (start mid 30..70), H = 1/3/5/10/15 min
  C  the same statistic on small moves (1c <= |move| < 5c) — the control a real effect must beat
  B  a taker fade of each trigger (buy the faded side at the ask, sell at the bid 5 min later)
  D  a replay of the operator's exact band design on the real bid/ask/trade path

Classes (fixed in the pre-registration): NFL game winners (`KXNFLGAME`, one market per event —
the two team markets are mirrors) and speech-mention markets (the series in MENTION_SERIES).

No lookahead: every trigger, fill and stop is decided from candles that have already closed (a
fill inside a minute uses that minute's own low/high, i.e. the path it actually traded). The
settlement `result` only marks a position still open when the window ends — the real outcome of
holding it. The 1-minute window is chosen by VOLUME (the busiest hour of the market's life),
never by price, so it cannot select for a direction.

Read-only, stdlib only, public Kalshi REST (live tier, then the historical tier, via
`kalshi_settled` for listings and the same two URLs for candles). Places no orders, writes no
tables. Usage:
    {"type":"script","name":"bandrev_probe","args":[],"id":"rl-bandrev-run-1"}
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import random
import time
from collections import defaultdict

import kalshi_settled as ks
import xvenue_leadlag as xl

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# --- pre-registered constants (docs/BANDREV_PREREG.md; never re-scoped after a run) -------------
WINDOW_START = "2026-09-01T00:00:00Z"   # market close_time >= this
WINDOW_END = "2026-11-10T00:00:00Z"     # market close_time <  this
NFL_SERIES = ("KXNFLGAME",)
MENTION_SERIES = (
    "KXTRUMPMENTION", "KXTRUMPMENTIONB", "KXFEDMENTION", "KXMAMDANIMENTION",
    "KXWORLDNEWSMENTION", "KXEARNINGSMENTIONNVDA", "KXEARNINGSMENTIONMSFT",
    "KXEARNINGSMENTIONMETA",
)
MIN_VOLUME = {"NFL": 5000.0, "MENTION": 1000.0}
MAX_MARKETS = 400            # per class, top by volume (deterministic)
PRE_H, POST_H = 3, 4         # 1-min window = [busiest hour - 3h, busiest hour + 4h]
LOOKBACK_S = 3 * 60          # move measured over 3 minutes
MOVE_C = 5.0                 # trigger: |mid move| >= 5c
CTRL_LO, CTRL_HI = 1.0, 5.0  # control: 1c <= |move| < 5c
BAND_LO, BAND_HI = 30.0, 70.0  # "near 50/50": mid at the START of the move
MAX_SPREAD = 6.0             # trigger minute's spread, cents
HORIZONS = (1, 3, 5, 10, 15)
PRIMARY_H = 5
COST_C = 3.5                 # round-trip cost bar (taker fee ~1.75c x2 at 50c, spread ignored)
CTRL_MARGIN = 2.0            # treatment R5 must beat control R5 by this much
MIN_EVENTS = 500             # per class, for a verdict
MIN_MARKETS = 40             # P0: usable markets per class
MIN_COVERAGE = 0.80          # P0: share of window minutes with a two-sided quote
MIN_TRIPS = 300              # P4: band replay round trips for its own clause
CLIP = 10                    # contracts, for the taker fee
BOOT = 2000
SEED = 20261110
DESIGN = (5, 10, 2)          # the operator's drawing: d, take-profit, stop (cents)
GRID = ((3, 3, 1), (5, 5, 5), (8, 16, 3), (5, 10, None), (5, 5, None))  # descriptive only


def taker_fee_c(price_c: float, qty: int = CLIP) -> float:
    """Per-contract taker fee in cents: ceil(0.07*q*P*(1-P)*100)/q."""
    p = min(max(price_c / 100.0, 0.0), 1.0)
    return math.ceil(0.07 * qty * p * (1.0 - p) * 100) / qty


def maker_fee_c(price_c: float, coeff: float) -> float:
    p = min(max(price_c / 100.0, 0.0), 1.0)
    return coeff * 100.0 * p * (1.0 - p)


def _iso(s: str) -> int:
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def _leg(c: dict, key: str, field: str) -> float | None:
    leg = c.get(key) or {}
    v = leg.get(f"{field}_dollars")
    if v is not None:
        return xl._num(v) * 100.0
    v = leg.get(field)
    return xl._num(v) if v is not None else None


def candles(series: str, ticker: str, start: int, end: int, period: int) -> list[dict]:
    """Candles from the live tier, else the historical tier. Fail-soft -> []."""
    q = f"?start_ts={start}&end_ts={end}&period_interval={period}"
    for url in (f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks{q}",
                f"{KALSHI}/historical/markets/{ticker}/candlesticks{q}"):
        data = xl._get(url)
        rows = (data or {}).get("candlesticks") or []
        if rows:
            return rows
    return []


def minute_path(series: str, ticker: str, close_ts: int) -> tuple[list[dict], dict]:
    """The busiest-hour 1-minute window as a contiguous per-minute list (quotes forward-filled,
    trade low/high only where a trade printed). Returns (path, diagnostics)."""
    hourly = candles(series, ticker, close_ts - 14 * 86400, close_ts, 60)
    if not hourly:
        return [], {"reason": "no_hourly"}
    peak = max(hourly, key=lambda c: xl._num(c.get("volume_fp")) or xl._num(c.get("volume")))
    peak_ts = int(xl._num(peak.get("end_period_ts")))
    start, end = peak_ts - (PRE_H + 1) * 3600, min(close_ts, peak_ts + POST_H * 3600)
    rows = candles(series, ticker, start, end, 1)
    if not rows:
        return [], {"reason": "no_minute"}
    by_ts = {int(xl._num(c.get("end_period_ts"))): c for c in rows}
    t0, t1 = min(by_ts), max(by_ts)
    path: list[dict] = []
    bid = ask = None
    raw_quoted = 0
    for ts in range(t0, t1 + 60, 60):
        c = by_ts.get(ts)
        if c is not None:
            b, a = _leg(c, "yes_bid", "close"), _leg(c, "yes_ask", "close")
            if b is not None and a is not None and 0 < b < a < 100:
                bid, ask = b, a
                raw_quoted += 1
        lo = _leg(c, "price", "low") if c is not None else None
        hi = _leg(c, "price", "high") if c is not None else None
        ok = bid is not None and ask is not None
        path.append({"ts": ts, "bid": bid, "ask": ask, "ok": ok,
                     "mid": (bid + ask) / 2.0 if ok else None,
                     "lo": lo if lo and lo > 0 else None, "hi": hi if hi and hi > 0 else None})
    return path, {"reason": "ok", "minutes": len(path), "raw_quoted": raw_quoted}


# --- A/C/B: event study -----------------------------------------------------------------------

def event_study(path: list[dict]) -> tuple[list[dict], list[dict]]:
    """(triggers, controls). Each row: move sign, R_H per horizon, taker-fade net. Non-overlapping:
    after an event at t the next one of the same kind may start at t + max(HORIZONS)."""
    k = LOOKBACK_S // 60
    hmax = max(HORIZONS)
    trig, ctrl = [], []
    next_t = next_c = 0
    for t in range(k, len(path) - hmax):
        p0, p1 = path[t - k], path[t]
        if not (p0["ok"] and p1["ok"]) or not (BAND_LO <= p0["mid"] <= BAND_HI):
            continue
        if p1["ask"] - p1["bid"] > MAX_SPREAD:
            continue
        if not all(path[t + h]["ok"] for h in HORIZONS):
            continue
        move = p1["mid"] - p0["mid"]
        size = abs(move)
        sgn = 1.0 if move > 0 else -1.0
        row = {"move": move, "R": {h: -sgn * (path[t + h]["mid"] - p1["mid"]) for h in HORIZONS}}
        if size >= MOVE_C and t >= next_t:
            # taker fade: down move -> buy YES at ask, sell at bid; up move -> buy NO at 100-bid,
            # sell NO at 100-ask. Exit at PRIMARY_H.
            q = path[t + PRIMARY_H]
            if sgn < 0:
                ent, ext = p1["ask"], q["bid"]
            else:
                ent, ext = 100 - p1["bid"], 100 - q["ask"]
            row["fade_net"] = ext - ent - taker_fee_c(ent) - taker_fee_c(ext)
            trig.append(row)
            next_t = t + hmax
        elif CTRL_LO <= size < CTRL_HI and t >= next_c:
            ctrl.append(row)
            next_c = t + hmax
    return trig, ctrl


# --- D: the operator's band design, replayed ----------------------------------------------------

def band_replay(path: list[dict], result: str, d: int, tp: int | None, stop: int | None,
                maker_coeff: float = 0.0, optimistic: bool = False) -> list[tuple[float, str]]:
    """Round trips [(net cents, exit kind)]. Fill rule (conservative): a resting order fills in a
    minute only if a trade printed THROUGH its price (YES bid at L: trade low < L; YES offer at U:
    trade high > U). `optimistic` fills on touch (<= / >=). Stop: a minute CLOSE with our side's bid
    <= entry - stop sells at that bid, taker fee. TP and stop in the same minute -> stop.
    A position open when the window ends is marked at settlement (100/0)."""
    out: list[tuple[float, str]] = []
    settle_yes = 100.0 if result == "yes" else 0.0
    pos = None  # (side, entry_in_side_cents, fee_in)
    ref = None

    def through_down(px: dict, lvl: float) -> bool:
        return px["lo"] is not None and (px["lo"] <= lvl if optimistic else px["lo"] < lvl)

    def through_up(px: dict, lvl: float) -> bool:
        return px["hi"] is not None and (px["hi"] >= lvl if optimistic else px["hi"] > lvl)

    for px in path:
        if not px["ok"]:
            continue
        if pos is None:
            mid = px["mid"]
            if ref is None:
                if BAND_LO <= mid <= BAND_HI and px["ask"] - px["bid"] <= MAX_SPREAD:
                    ref = mid
                continue
            lo_lvl, hi_lvl = round(ref - d), round(ref + d)
            down, up = through_down(px, lo_lvl), through_up(px, hi_lvl)
            if down and up:          # both sides crossed inside one minute: order unknown, skip
                ref = None
                continue
            if down:
                e = float(lo_lvl)
                pos = ("yes", e, maker_fee_c(e, maker_coeff))
            elif up:
                e = 100.0 - hi_lvl
                pos = ("no", e, maker_fee_c(e, maker_coeff))
            continue
        side, e, fin = pos
        # our side's executable bid and the trade-through test for our TP offer
        if side == "yes":
            my_bid = px["bid"]
            tp_hit = tp is not None and through_up(px, e + tp)
        else:
            my_bid = 100.0 - px["ask"]
            tp_hit = tp is not None and through_down(px, 100.0 - (e + tp))
        stop_hit = stop is not None and my_bid <= e - stop
        if stop_hit:
            xp = max(1.0, my_bid)
            out.append((xp - e - fin - taker_fee_c(xp), "stop"))
        elif tp_hit:
            out.append((tp - fin - maker_fee_c(e + tp, maker_coeff), "tp"))
        else:
            continue
        pos, ref = None, None   # re-arm around the next quote
    if pos is not None:
        side, e, fin = pos
        s = settle_yes if side == "yes" else 100.0 - settle_yes
        out.append((s - e - fin, "settle"))
    return out


# --- stats ------------------------------------------------------------------------------------

def cluster_boot(groups: list[list[float]], seed: int = SEED, n: int = BOOT) -> tuple[float, float, float]:
    """(mean, 5th pct, 95th pct) of the pooled mean, resampling MARKETS with replacement."""
    groups = [g for g in groups if g]
    if not groups:
        return float("nan"), float("nan"), float("nan")
    tot = sum(sum(g) for g in groups) / sum(len(g) for g in groups)
    rng = random.Random(seed)
    sums = [(sum(g), len(g)) for g in groups]
    means = []
    for _ in range(n):
        s = c = 0.0
        for _ in range(len(sums)):
            a, b = sums[rng.randrange(len(sums))]
            s += a
            c += b
        means.append(s / c)
    means.sort()
    return tot, means[int(0.05 * n)], means[int(0.95 * n) - 1]


def fmt(m: float, lo: float, hi: float) -> str:
    return f"{m:+6.2f}c (90% CI {lo:+.2f}..{hi:+.2f})"


# --- per class --------------------------------------------------------------------------------

def load_class(name: str, series_list: tuple[str, ...], max_markets: int, pause: float) -> list[dict]:
    lo_ts, hi_ts = _iso(WINDOW_START), _iso(WINDOW_END)
    count = ks.ScanCount()
    picked: list[dict] = []
    per_series: dict[str, int] = defaultdict(int)
    for s in series_list:
        seen_events: set[str] = set()
        mk = sorted(ks.iter_series_markets(s, status="settled", min_close_ts=lo_ts,
                                           max_close_ts=hi_ts - 1, count=count),
                    key=lambda m: m.get("ticker") or "")
        for m in mk:
            res = (m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            ev = m.get("event_ticker") or ""
            if name == "NFL":
                if ev in seen_events:      # two mirror markets per game: keep the first ticker
                    continue
                seen_events.add(ev)
            vol = xl._num(m.get("volume_fp")) or xl._num(m.get("volume"))
            ct = ks.close_ts(m)
            if vol < MIN_VOLUME[name] or not ct:
                continue
            picked.append({"series": s, "ticker": m["ticker"], "result": res, "vol": vol,
                           "close": ct})
            per_series[s] += 1
    print(f"[{name}] {count.line()}")
    print(f"[{name}] eligible by series: " + ", ".join(f"{k} {v}" for k, v in sorted(per_series.items())))
    picked.sort(key=lambda r: (-r["vol"], r["ticker"]))
    if len(picked) > max_markets:
        print(f"[{name}] {len(picked)} eligible; keeping the top {max_markets} by volume (pre-registered)")
        picked = picked[:max_markets]
    for r in picked:
        r["path"], r["diag"] = minute_path(r["series"], r["ticker"], r["close"])
        time.sleep(pause)
    return picked


def run_class(name: str, mkts: list[dict]) -> dict:
    usable = [m for m in mkts if len(m["path"]) >= 120]
    minutes = sum(m["diag"].get("minutes", 0) for m in usable)
    quoted = sum(m["diag"].get("raw_quoted", 0) for m in usable)
    cov = quoted / minutes if minutes else 0.0
    reasons = defaultdict(int)
    for m in mkts:
        reasons[m["diag"].get("reason", "?") if m["path"] else m["diag"].get("reason", "empty")] += 1
    print(f"\n==== {name} ====")
    print(f"markets fetched {len(mkts)}  usable (>=120 min) {len(usable)}  fetch: {dict(reasons)}")
    print(f"raw two-sided quote coverage {cov:.1%} of {minutes:,} window minutes "
          f"(forward-filled between candles)")

    trig_by, ctrl_by = [], []
    for m in usable:
        tr, cr = event_study(m["path"])
        trig_by.append(tr)
        ctrl_by.append(cr)
    n_trig = sum(len(g) for g in trig_by)
    n_ctrl = sum(len(g) for g in ctrl_by)
    print(f"\nA. trigger events (|3-min move| >= {MOVE_C:.0f}c from mid {BAND_LO:.0f}-{BAND_HI:.0f}): "
          f"n={n_trig} over {sum(1 for g in trig_by if g)} markets   control (1-5c moves): n={n_ctrl}")
    print("   reversion R_H = -sign(move) * (mid[t+H] - mid[t]); positive = snaps back")
    res: dict = {"name": name, "usable": len(usable), "cov": cov, "n": n_trig}
    for h in HORIZONS:
        t = cluster_boot([[r["R"][h] for r in g] for g in trig_by])
        c = cluster_boot([[r["R"][h] for r in g] for g in ctrl_by])
        tag = "  <- PRIMARY" if h == PRIMARY_H else ""
        print(f"   H={h:2d}m  trigger {fmt(*t)}   control {fmt(*c)}{tag}")
        if h == PRIMARY_H:
            res["R5"], res["R5_ctrl"] = t, c
    # size buckets, descriptive
    for lo, hi in ((5, 8), (8, 12), (12, 100)):
        sub = [[r["R"][PRIMARY_H] for r in g if lo <= abs(r["move"]) < hi] for g in trig_by]
        k = sum(len(x) for x in sub)
        if k:
            print(f"   descriptive: move {lo}-{hi}c n={k} R5 {fmt(*cluster_boot(sub))}")
    fade = cluster_boot([[r["fade_net"] for r in g] for g in trig_by])
    res["fade"] = fade
    print(f"\nB. taker fade (enter ask, exit bid at +{PRIMARY_H}m, fee q={CLIP} both legs): {fmt(*fade)}")

    print("\nD. operator band design replay (maker entry+TP fee 0, stop = taker sell at the bid)")
    print("   design             fill   trips  /mkt   EV/trip (90% CI)                 win   TP  STOP SETTLE")
    for (d, tp, st), opt, coeff in [(DESIGN, False, 0.0), (DESIGN, True, 0.0), (DESIGN, False, 0.0175)] + \
            [(g, False, 0.0) for g in GRID]:
        trips = [band_replay(m["path"], m["result"], d, tp, st, coeff, opt) for m in usable]
        flat = [x for g in trips for x in g]
        if not flat:
            print(f"   d={d} tp={tp} stop={st}: no trips")
            continue
        b = cluster_boot([[x[0] for x in g] for g in trips])
        kinds = defaultdict(int)
        for _, k in flat:
            kinds[k] += 1
        n = len(flat)
        label = f"d={d} tp={tp} st={st}" + (" mk.0175" if coeff else "")
        print(f"   {label:18s} {'touch' if opt else 'thru':5s} {n:6d} {n / max(1, len(usable)):5.1f}   "
              f"{fmt(*b)}  {sum(1 for x in flat if x[0] > 0) / n:4.0%} {kinds['tp'] / n:4.0%} "
              f"{kinds['stop'] / n:4.0%} {kinds['settle'] / n:4.0%}"
              + ("   <- PRE-REGISTERED DESIGN" if (d, tp, st) == DESIGN and not opt and not coeff else ""))
        if (d, tp, st) == DESIGN and not opt and not coeff:
            res["band"], res["band_n"] = b, n
    return res


def verdict(r: dict) -> str:
    name = r["name"]
    lines = [f"[{name}]"]
    if r["usable"] < MIN_MARKETS or r["cov"] < MIN_COVERAGE:
        lines.append(f"  P0 FAIL: usable markets {r['usable']} (need {MIN_MARKETS}), "
                     f"coverage {r['cov']:.1%} (need {MIN_COVERAGE:.0%}) -> HOLD (instrument)")
        return "\n".join(lines)
    lines.append(f"  P0 PASS: {r['usable']} markets, coverage {r['cov']:.1%}")
    m, lo, _ = r.get("R5", (float("nan"),) * 3)
    mc = r.get("R5_ctrl", (float("nan"),) * 3)[0]
    if r["n"] < MIN_EVENTS:
        lines.append(f"  P1 n={r['n']} < {MIN_EVENTS} -> HOLD (accrual); R5 {m:+.2f}c read descriptively only")
        p1 = None
    else:
        p1 = m >= COST_C and lo > 0
        lines.append(f"  P1 R5 {m:+.2f}c (LB {lo:+.2f}) vs bar {COST_C}c at n={r['n']} -> "
                     f"{'PASS' if p1 else 'FAIL'}")
    p2 = (m - mc) >= CTRL_MARGIN
    lines.append(f"  P2 treatment - control = {m - mc:+.2f}c (bar +{CTRL_MARGIN}c) -> {'PASS' if p2 else 'FAIL'}")
    fm, flo, _ = r["fade"]
    lines.append(f"  P3 taker fade {fm:+.2f}c (LB {flo:+.2f}) -> {'PASS' if fm > 0 and flo > 0 else 'FAIL'} (reported)")
    if "band" in r:
        bm, blo, _ = r["band"]
        if r["band_n"] >= MIN_TRIPS:
            closed = bm <= 0
            lines.append(f"  P4 operator design {bm:+.2f}c/trip at n={r['band_n']} -> "
                         f"{'CLOSED (EV <= 0)' if closed else 'not closed (EV > 0)'}")
        else:
            lines.append(f"  P4 operator design n={r['band_n']} < {MIN_TRIPS} trips -> no clause")
    if p1 is None:
        lines.append(f"  VERDICT {name}: HOLD (accrual)")
    elif p1 and p2:
        lines.append(f"  VERDICT {name}: PASS -> write a paper-package pre-registration (no live)")
    else:
        lines.append(f"  VERDICT {name}: KILL (no tradeable over-reaction in this class)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="BANDREV probe (read-only)")
    ap.add_argument("--max-markets", type=int, default=MAX_MARKETS,
                    help="per class; the pre-registered read uses the default")
    ap.add_argument("--classes", default="NFL,MENTION")
    ap.add_argument("--pause", type=float, default=0.05)
    args = ap.parse_args(argv)
    print("BANDREV — over-reaction / band-reversion on near-50/50 binaries (read-only Kalshi REST)")
    print(f"window close_time [{WINDOW_START}, {WINDOW_END}); trigger |3m move| >= {MOVE_C}c, "
          f"mid {BAND_LO}-{BAND_HI}; primary H={PRIMARY_H}m; bar {COST_C}c at n>={MIN_EVENTS}")
    if args.max_markets != MAX_MARKETS:
        print(f"NOTE: --max-markets {args.max_markets} differs from the pre-registered {MAX_MARKETS}; "
              "this run is DESCRIPTIVE and decides nothing")
    results = []
    for name in [c.strip().upper() for c in args.classes.split(",") if c.strip()]:
        series = NFL_SERIES if name == "NFL" else MENTION_SERIES if name == "MENTION" else None
        if series is None:
            print(f"unknown class {name}")
            continue
        results.append(run_class(name, load_class(name, series, args.max_markets, args.pause)))
    print("\n== verdict ==")
    for r in results:
        print(verdict(r))
    cut = ks.cutoff()
    if cut:
        print(f"historical cutoff: {cut}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
