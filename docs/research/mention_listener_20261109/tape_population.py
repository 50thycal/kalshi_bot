"""How much of a mention event trades live, and how fast does a said word reprice?

Input: `kalshi_desk_board --ticker <T> --trades 1000` ops results for every market of ONE event
(public Kalshi trades, second-resolution timestamps). Output: a derived JSON summary.

Per market: total contracts, share traded during the broadcast window, pre-show price, and —
for words that resolved YES during the show — two measures of the repricing race:

  stale_lift   YES-taker trades below 95c in the 60 s before the first >=95c print (+5 s),
               priced >= 5c above the pre-window level. Contracts and gross edge (payout 100c
               minus price) = what the fastest detector(s) collected on this word.
  jump_s       seconds from the price crossing halfway between its pre-jump level and 95c to the
               first >=95c print (pre-jump level = median of the last 5 trades >20 s before).
               Words already >= 80c before the jump are excluded (nothing to race for).

A tape returning 1,000 trades may be truncated at its old end; that market is flagged.

Usage: python tape_population.py "<glob of result files>" 2026-11-06T23:30:00 30 out.json
"""

from __future__ import annotations

import glob
import json
import re
import sys
from datetime import datetime, timedelta

TRADE_RE = re.compile(r"\n\s+(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\s+yes\s+(\d+)c\s+x(\d+)\s+taker=(\w+)")


def parse(path):
    txt = open(path).read()
    word = re.search(r"outcome\s+(.*)", txt)[1].strip()
    res = (re.search(r"result\s+(\w+)", txt) or [None, "?"])[1]
    rows = [(datetime.fromisoformat(m[1]), int(m[2]), int(m[3]), m[4])
            for m in TRADE_RE.finditer(txt)][::-1]
    return word, res, rows


def analyse(path, s0: datetime, s1: datetime) -> dict:
    word, res, rows = parse(path)
    tot = sum(r[2] for r in rows)
    ins = sum(r[2] for r in rows if s0 <= r[0] < s1)
    pre = [r for r in rows if r[0] < s0]
    out = {"word": word, "result": res, "contracts": tot, "in_show": ins,
           "in_show_share": round(ins / tot, 3) if tot else None,
           "pre_show_c": pre[-1][1] if pre else None, "truncated_tape": len(rows) >= 1000}
    i95 = next((i for i, r in enumerate(rows) if r[1] >= 95 and s0 <= r[0] < s1), None)
    if res != "yes" or i95 is None:
        return out
    t95 = rows[i95][0]
    out["first_95_utc"] = t95.isoformat()
    before = [r for r in rows if r[0] < t95 - timedelta(seconds=60)]
    pb_win = before[-1][1] if before else 0
    lift = [r for r in rows if t95 - timedelta(seconds=60) <= r[0] <= t95 + timedelta(seconds=5)
            and r[3] == "yes" and r[1] < 95 and r[1] >= pb_win + 5]
    out["stale_lift_contracts"] = sum(r[2] for r in lift)
    out["stale_lift_edge_usd"] = round(sum(r[2] * (100 - r[1]) for r in lift) / 100, 2)
    prior = [r[1] for r in rows[:i95] if r[0] < t95 - timedelta(seconds=20)][-5:]
    if prior:
        pb = sorted(prior)[len(prior) // 2]
        out["pre_jump_c"] = pb
        if pb < 80:
            mid = pb + (95 - pb) / 2
            k = i95
            while k > 0 and rows[k - 1][1] >= mid and (t95 - rows[k - 1][0]).total_seconds() <= 120:
                k -= 1
            out["jump_s"] = (t95 - rows[k][0]).total_seconds()
    return out


def main(src_glob, show_start, show_minutes, out_path):
    s0 = datetime.fromisoformat(show_start)
    s1 = s0 + timedelta(minutes=float(show_minutes), seconds=30)
    mk = [analyse(f, s0, s1) for f in sorted(glob.glob(src_glob))]
    tot = sum(m["contracts"] for m in mk)
    jumps = sorted(m["jump_s"] for m in mk if "jump_s" in m)
    summary = {
        "markets": len(mk), "contracts": tot,
        "in_show_share": round(sum(m["in_show"] for m in mk) / tot, 3) if tot else None,
        "said_in_show": sum(1 for m in mk if "first_95_utc" in m),
        "stale_lift_edge_usd": round(sum(m.get("stale_lift_edge_usd", 0) for m in mk), 2),
        "jump_s_all": jumps, "truncated_tapes": sum(m["truncated_tape"] for m in mk),
    }
    json.dump({"show_start_utc": show_start, "summary": summary, "markets": mk},
              open(out_path, "w"), indent=1, default=str)
    return summary, mk


if __name__ == "__main__":
    s, mk = main(*sys.argv[1:5])
    print(json.dumps(s))
    for m in mk:
        print({k: m[k] for k in m if k in ("word", "result", "contracts", "in_show_share",
                                         "pre_show_c", "stale_lift_edge_usd", "jump_s")})
