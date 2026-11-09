"""Cycle 6: does a constant-hazard in-play model disagree with the market usefully?

Input: the 17 `kalshi_desk_board --ticker ... --trades 1000` results for event
KXWORLDNEWSMENTION-26NOV06 (ABC World News Tonight, aired 23:30-00:00 UTC), fetched through the
ops channel 2026-11-09 (ids rl-mt-wn06-*). Output: tape_population_20261106.json (derived only).

For every market and checkpoint minute m in (5,10,15,20,25): market = last trade at or before
23:30+m; model = HazardModel(pre-show price).p_remaining(m/30, said=0) while the word is not yet
said (a market already >= 95c is treated as said and skipped). A naive rule trades one contract
when |model - market| >= 10c, paying market +1c slippage + Kalshi taker fee. ONE show: this
describes, it does not test. No outcome was used to pick the rule or the checkpoints.
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mention_listener as ml  # noqa: E402

SHOW0 = datetime.fromisoformat("2026-11-06T23:30:00")
SHOW_MIN = 30.0
CHECKS = (5, 10, 15, 20, 25)
GAP_C = 10
TRADE_RE = re.compile(r"\n\s+(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\s+yes\s+(\d+)c\s+x(\d+)\s+taker=(\w+)")


def taker_fee_c(p_c: int) -> int:
    return math.ceil(7 * (p_c / 100) * (1 - p_c / 100))  # ceil(0.07*P*(1-P)) dollars, in cents


def load(path):
    txt = open(path).read()
    word = re.search(r"outcome\s+(.*)", txt)[1].strip()
    res = re.search(r"result\s+(\w+)", txt)[1]
    rows = [(datetime.fromisoformat(m[1]), int(m[2]), int(m[3]), m[4])
            for m in TRADE_RE.finditer(txt)][::-1]
    return word, res, rows


def last_before(rows, t):
    p = [r for r in rows if r[0] <= t]
    return p[-1][1] if p else None


def main(src_glob, out):
    markets, trades = [], []
    for f in sorted(glob.glob(src_glob)):
        word, res, rows = load(f)
        k = 5 if "5+" in word else 1
        p0 = last_before(rows, SHOW0)
        if p0 is None or p0 >= 95 or p0 <= 2:
            markets.append({"word": word, "result": res, "pre_show_c": p0, "skipped": True})
            continue
        h = ml.HazardModel(p0 / 100, threshold=k)
        path = []
        for m in CHECKS:
            t = SHOW0 + timedelta(minutes=m)
            mk = last_before(rows, t)
            said = mk is not None and mk >= 95
            model = None if said else round(100 * h.p_remaining(m / SHOW_MIN, 0))
            path.append({"min": m, "market_c": mk, "model_c": model, "said_by_market": said})
            if model is None or mk is None:
                continue
            gap = model - mk
            if abs(gap) >= GAP_C:
                side = "YES" if gap > 0 else "NO"
                px = (mk if side == "YES" else 100 - mk) + 1
                if px >= 99:
                    continue
                win = (res == "yes") == (side == "YES")
                pnl = (100 - px if win else -px) - taker_fee_c(px)
                trades.append({"word": word, "min": m, "side": side, "price_c": px,
                               "model_c": model, "market_c": mk, "won": win, "pnl_c": pnl})
        markets.append({"word": word, "result": res, "pre_show_c": p0, "threshold": k,
                        "path": path})
    summ = {
        "event": "KXWORLDNEWSMENTION-26NOV06", "rule": f"|model-market|>={GAP_C}c, +1c slip, taker fee",
        "n_trades": len(trades), "n_markets_traded": len({t["word"] for t in trades}),
        "pnl_c": sum(t["pnl_c"] for t in trades),
        "yes_side": sum(t["pnl_c"] for t in trades if t["side"] == "YES"),
        "no_side": sum(t["pnl_c"] for t in trades if t["side"] == "NO"),
        "market_below_model_share": round(sum(1 for mk in markets for p in mk.get("path", [])
                                               if p["model_c"] is not None and p["market_c"] is not None
                                               and p["market_c"] < p["model_c"]) /
                                           max(1, sum(1 for mk in markets for p in mk.get("path", [])
                                                      if p["model_c"] is not None and p["market_c"] is not None)), 2),
    }
    json.dump({"summary": summ, "markets": markets, "trades": trades}, open(out, "w"), indent=1)
    return summ, markets, trades


if __name__ == "__main__":
    s, mk, tr = main(sys.argv[1], sys.argv[2])
    print(json.dumps(s, indent=1))
    for m in mk:
        if m.get("skipped"):
            print(f"  {m['word'][:26]:26} {m['result']:3} pre {m['pre_show_c']}c  (skipped: near-certain pre-show)")
            continue
        cells = "  ".join(f"{p['min']:2d}m mk{p['market_c'] if p['market_c'] is not None else '-':>3}/md{p['model_c'] if p['model_c'] is not None else 'SAID':>4}" for p in m["path"])
        print(f"  {m['word'][:26]:26} {m['result']:3} pre {m['pre_show_c']:2d}c  {cells}")
    for t in tr:
        print("   trade", t)
