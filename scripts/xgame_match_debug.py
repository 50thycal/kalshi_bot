"""XGAME matcher diagnostic — why does the (day, team) join produce 0 pairs?

Reproduces the collector's EXACT key extraction (kalshi_bot/xgame: ticker_day + norm_team
on Kalshi, the PM_WIN regex on Polymarket) against live public data, then prints both key
sets, their intersection, and the near-misses (same team, different day / same day,
different team) so the divergence is visible instead of guessed. Read-only public APIs,
stdlib only — safe on the ops runner.

Usage: {"type":"script","name":"xgame_match_debug","args":["--series","KXWCGAME","--tags","soccer"]}
"""

from __future__ import annotations

import argparse
import re

import xvenue_leadlag as xl  # _get (browser UA + retries)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"

# --- verbatim from kalshi_bot/xgame/tracker.py + pm.py (keep in sync) ---
_TICKER_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
PM_WIN = re.compile(r"will\s+(.+?)\s+win on\s+(\d{4}-\d{2}-\d{2})", re.I)


def ticker_day(ticker: str) -> str:
    m = _TICKER_DATE.search(ticker or "")
    if not m:
        return ""
    mon = _MON.get(m.group(2))
    return f"20{m.group(1)}-{mon:02d}-{int(m.group(3)):02d}" if mon else ""


def norm_team(s: str) -> str:
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()


def kalshi_keys(series_list, max_pages=8):
    """{(day, team): ticker} from open per-team game markets (public API)."""
    out = {}
    samples = []
    for series in series_list:
        cursor = ""
        for _ in range(max_pages):
            page = xl._get(f"{KALSHI}/markets?series_ticker={series}&status=open&limit=200&cursor={cursor}")
            mkts = (page or {}).get("markets") or []
            for mkt in mkts:
                ticker = mkt.get("ticker") or ""
                sub_raw = (mkt.get("yes_sub_title") or "")
                sub = sub_raw.split(":")[-1]
                team = norm_team(sub)
                day = ticker_day(ticker)
                if len(samples) < 8:
                    samples.append((ticker, sub_raw, day, team))
                if not ticker or not team or team == "tie" or not day:
                    continue
                out[(day, team)] = ticker
            cursor = (page or {}).get("cursor") or ""
            if not cursor or not mkts:
                break
    return out, samples


def pm_keys(tags, pages=6):
    """{(day, team): question} from PM 'Will <team> win on <date>?' markets."""
    out = {}
    samples = []
    for tag in tags:
        for page in range(pages):
            evs = xl._get(f"{GAMMA}/events?closed=false&tag_slug={tag}&limit=100&offset={page * 100}")
            if not isinstance(evs, list) or not evs:
                break
            for ev in evs:
                for mk in ev.get("markets") or []:
                    q = mk.get("question") or ""
                    mq = PM_WIN.search(q)
                    if len(samples) < 8 and q:
                        samples.append((q, "MATCH" if mq else "no-regex"))
                    if not mq:
                        continue
                    team = norm_team(mq.group(1))
                    if not team:
                        continue
                    out[(mq.group(2), team)] = q[:60]
            if len(evs) < 100:
                break
    return out, samples


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--series", default="KXWCGAME")
    ap.add_argument("--tags", default="soccer")
    args = ap.parse_args(argv)

    kal, kal_samples = kalshi_keys([s.strip().upper() for s in args.series.split(",") if s.strip()])
    pm, pm_samples = pm_keys([t.strip().lower() for t in args.tags.split(",") if t.strip()])

    print(f"=== Kalshi keys: {len(kal)} | Polymarket keys: {len(pm)} ===\n")

    print("--- Kalshi sample (ticker | raw sub | derived day | norm team) ---")
    for tk, sub, day, team in kal_samples:
        print(f"  {tk:<32} sub={sub!r:<26} -> day={day!r} team={team!r}")
    print("\n--- Polymarket sample (question | regex) ---")
    for q, status in pm_samples:
        print(f"  [{status}] {q[:70]!r}")

    inter = set(kal) & set(pm)
    print(f"\n=== INTERSECTION (day,team): {len(inter)} ===")
    for k in sorted(inter)[:20]:
        print(f"  {k}")

    # near-misses to localize the divergence
    kal_days, pm_days = {d for d, _ in kal}, {d for d, _ in pm}
    kal_teams, pm_teams = {t for _, t in kal}, {t for _, t in pm}
    print(f"\n--- days: kalshi={sorted(kal_days)}  polymarket={sorted(pm_days)} ---")
    print(f"--- team overlap: {len(kal_teams & pm_teams)} teams share a name "
          f"(kalshi {len(kal_teams)} / pm {len(pm_teams)}) ---")
    same_team_diff_day = sorted(kal_teams & pm_teams)[:12]
    print(f"--- teams present on BOTH (day mismatch is then the culprit): {same_team_diff_day} ---")
    for t in same_team_diff_day[:6]:
        kd = sorted(d for d, tt in kal if tt == t)
        pd = sorted(d for d, tt in pm if tt == t)
        print(f"    {t!r:<22} kalshi_days={kd}  pm_days={pd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
