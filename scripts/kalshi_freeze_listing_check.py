"""FREEZE gate check — lightweight, loop-safe re-check of whether Kalshi's commodity-hub
grain/soft listings have grown enough to make the FREEZE thesis testable.

`docs/FREEZE_THESIS.md` / `docs/RESEARCH_JOURNAL.md` (2026-07-11 verdict): the FREEZE probe
came back UNTESTABLE, not killed — the source-frozen universe (grains/softs; metals/energy are
Pyth-continuous and excluded, see kalshi_freeze_study.py) had essentially no settled markets on
the new hub (grain 0, soft 8) and thin open listings (coffee 22, cotton 3, soybeans 2; no
corn/wheat/sugar). The pre-registered revisit trigger is qualitative ("grows into the
hundreds") — this script makes it a number the strategy-status loop can check every run without
re-running the full trade-tape probe (which is much heavier: full settlement scoring + an
80-page open-event scan).

This script does ONLY the enumeration half of kalshi_freeze_study.py (reusing its commodity
classifier so the two never drift apart) — no trade tape, no EV scoring. Cheap enough to run
every loop pass.

Usage (via the ops channel):
    {"type":"script","name":"kalshi_freeze_listing_check","args":[],"id":"freeze-gate-check"}
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict

import xvenue_leadlag as xl  # _get (browser UA + retries)
from kalshi_freeze_study import FREEZE_GROUPS, KALSHI, commodity_of

TRIGGER_THRESHOLD = 100  # settled grain/soft markets -> "grows into the hundreds" (pre-reg)


def settled_commodity_counts(max_pages: int) -> dict[str, int]:
    """Settled grain/soft vs metal/energy market counts, via /events?status=settled."""
    counts: dict[str, int] = defaultdict(int)
    cursor = ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status=settled&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for ev in evs:
            etext = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"),
                                           ev.get("series_ticker")]))
            for m in ev.get("markets") or []:
                if (m.get("result") or "").lower() not in ("yes", "no"):
                    continue
                text = " ".join(filter(None, [etext, m.get("title"), m.get("subtitle"),
                                              m.get("ticker"), m.get("series_ticker")]))
                cm = commodity_of(text)
                if cm:
                    counts[cm[1]] += 1
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return counts


def open_commodity_counts(max_pages: int) -> dict[str, int]:
    """Open grain/soft vs metal/energy market counts, via /events?status=open."""
    counts: dict[str, int] = defaultdict(int)
    cursor = ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status=open&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for ev in evs:
            etext = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"),
                                           ev.get("series_ticker")]))
            for m in ev.get("markets") or []:
                text = " ".join(filter(None, [etext, m.get("title"), m.get("subtitle"),
                                              m.get("ticker"), m.get("series_ticker")]))
                cm = commodity_of(text)
                if cm:
                    counts[cm[1]] += 1
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FREEZE gate re-check (lightweight, loop-safe)")
    ap.add_argument("--max-settled-pages", type=int, default=80)
    ap.add_argument("--max-open-pages", type=int, default=80)
    args = ap.parse_args(argv)

    print("FREEZE gate check — commodity-hub grain/soft listing count "
          "(revisit trigger: docs/FREEZE_THESIS.md)")

    settled = settled_commodity_counts(args.max_settled_pages)
    opens = open_commodity_counts(args.max_open_pages)
    freeze_settled = sum(settled.get(g, 0) for g in FREEZE_GROUPS)
    freeze_open = sum(opens.get(g, 0) for g in FREEZE_GROUPS)

    print(f"\nsettled: grain={settled.get('grain', 0)} soft={settled.get('soft', 0)} "
          f"(freeze-eligible total={freeze_settled})  "
          f"metal={settled.get('metal', 0)} energy={settled.get('energy', 0)}")
    print(f"open:    grain={opens.get('grain', 0)} soft={opens.get('soft', 0)} "
          f"(freeze-eligible total={freeze_open})  "
          f"metal={opens.get('metal', 0)} energy={opens.get('energy', 0)}")

    print(f"\nTRIGGER: freeze-eligible SETTLED markets >= {TRIGGER_THRESHOLD} "
          f"({freeze_settled} now) -> "
          f"{'FIRED — re-run scripts/kalshi_freeze_study.py for a real verdict' if freeze_settled >= TRIGGER_THRESHOLD else 'not yet — still UNTESTABLE, no action needed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
