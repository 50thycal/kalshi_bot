"""Threshold study (2026-11-08): what a resting bid earns per market-hour against what its fills
cost per market-hour, by market type, from the book's own history. Read-only; its inputs are two
ops reads saved beside it:

  orders-2026-11-08.json     every Alimm1 entry bid (live_orders, closing orders excluded) with
                             its end time: the last canceled/executed `execution_order_events`
                             row, else the last `live_order_queue_ticks` sample
  scorecard-2026-11-08.json  `scripts/limm_scorecard.py --json` (confirmed rewards, fill P&L)

Resting time is the union of a market's bid intervals, so overlapping bids count once.
Usage: python3 tools/review/limm/study/threshold_study.py > study-2026-11-08.json
"""
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
from limm_scorecard import category, event_of  # noqa: E402

HERE = pathlib.Path(__file__).parent
AS_OF = dt.datetime(2026, 11, 8, 1, 30)
#: Events whose programmes have not paid out yet: their fills count, their rewards are unknown.
def still_open(e: str) -> bool:
    return e.endswith("26DEC") or e in ("KXMAMDANIMENTION-26NOV18", "KXTRUMPSAY-26NOV09")


def union_hours(spans):
    spans = sorted(spans)
    total, (cs, ce) = 0.0, spans[0]
    for a, b in spans[1:]:
        if a > ce:
            total += (ce - cs).total_seconds()
            cs, ce = a, b
        else:
            ce = max(ce, b)
    return (total + (ce - cs).total_seconds()) / 3600


def main():
    orders = json.loads((HERE / "orders-2026-11-08.json").read_text())
    sc = json.loads((HERE / "scorecard-2026-11-08.json").read_text())
    spans = {}
    for r in orders:
        if r["s"] in ("rejected", "not_landed"):
            continue
        a = dt.datetime.fromisoformat(r["c"])
        end = r["ee"] or r["lt"]
        b = AS_OF if r["s"] == "resting" or not end else dt.datetime.fromisoformat(end)
        if b > a:
            spans.setdefault(r["t"], []).append((a, b))
    hours = {t: union_hours(s) for t, s in spans.items()}
    ev = {b["event"]: dict(b, hours=0.0) for b in sc["by_event"]}
    for t, h in hours.items():
        e = event_of(t)
        ev.setdefault(e, {"event": e, "type": category(e), "rewards": 0.0, "fills": 0.0,
                          "payout_recorded": False, "hours": 0.0})["hours"] += h
    rows = sorted(ev.values(), key=lambda o: o["event"])
    for o in rows:
        o["open"] = still_open(o["event"])
        o["hours"] = round(o["hours"], 1)

    def agg(sel):
        h = sum(o["hours"] for o in sel)
        r = sum(o["rewards"] for o in sel)
        f = sum(o["fills"] for o in sel)
        return {"events": len(sel), "market_hours": round(h), "rewards": round(r, 2),
                "fills": round(f, 2), "net": round(r + f, 2),
                "reward_c_per_h": round(100 * r / h, 1) if h else None,
                "fill_c_per_h": round(-100 * f / h, 1) if h else None}
    closed = [o for o in rows if not o["open"]]
    me = [o for o in closed if o["type"] == "mention_event" and o["event"] >= "KXTRUMPMENTION-26OCT"]
    groups = {
        "mention_paid_5_plus": agg([o for o in me if o["rewards"] >= 5]),
        "mention_paid_1_to_5": agg([o for o in me if 0 < o["rewards"] < 5]),
        "mention_paid_0": agg([o for o in me if o["rewards"] == 0]),
        **{f"closed_{ty}": agg([o for o in closed if o["type"] == ty])
           for ty in ("mention_event", "youtube_views", "moving_number", "ai_share")},
        "still_open": agg([o for o in rows if o["open"]]),
    }
    json.dump({"as_of": AS_OF.isoformat() + "Z", "groups": groups, "events": rows,
               "mamdani_market_hours": {t: round(h, 1) for t, h in hours.items()
                                        if t.startswith("KXMAMDANIMENTION-26NOV18")}},
              sys.stdout, indent=1)


if __name__ == "__main__":
    main()
