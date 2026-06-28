"""In-play game-market cross-venue lead-lag — the faithful test of 'a goal happens,
Polymarket pops, Kalshi catches up 1-2 min later'.

Matches recent/live World Cup GAME markets across venues by (team, date) — Kalshi KXWCGAME
('Portugal vs Croatia Winner?', per-team regulation) vs Polymarket 'Will <team> win on
<date>?' — then pulls the TRADE TAPE from both (Kalshi /markets/{t}/trades, Polymarket
data-api /trades), builds fine-grained bars (default 30s) over each game window, aligns, and
runs the event-conditional shock analysis: when one venue jumps, does the other follow over
the next few bars (and did it already move same-bar = no lag)? Both directions, pooled.

For lead-lag the 3-way(Kalshi reg) vs 2-way(PM match) semantic gap is fine — a goal moves
both the same direction. Read-only public APIs, stdlib only. Reuses xvenue_shock.shock_study.
Usage: {"type":"script","name":"xvenue_game","args":["--days","3","--bar","30","--shock","2"]}
"""

from __future__ import annotations

import argparse
import json
import re
import time

import xvenue_crypto as xc  # kalshi_candles (reliable 1-min)
import xvenue_leadlag as xl  # _get, _num, align
import xvenue_shock as xs  # shock_study, Shock

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"

_PM_WIN = re.compile(r"will\s+(.+?)\s+win on\s+(\d{4}-\d{2}-\d{2})", re.I)
_TICKER_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()


def _ticker_day(ticker: str) -> str:
    m = _TICKER_DATE.search(ticker or "")
    if not m:
        return ""
    mon = _MON.get(m.group(2))
    return f"20{m.group(1)}-{mon:02d}-{int(m.group(3)):02d}" if mon else ""


# --- match recent WC games across venues -------------------------------------------


def kalshi_wc_games() -> dict:
    """(day, team) -> kalshi market ticker, for KXWCGAME per-team regulation markets."""
    out: dict[tuple[str, str], str] = {}
    for status in ("open", "settled"):
        cursor = ""
        for _ in range(8):
            page = xl._get(f"{KALSHI}/markets?series_ticker=KXWCGAME&status={status}"
                           f"&limit=200&cursor={cursor}")
            mkts = (page or {}).get("markets") or []
            for m in mkts:
                tk = m.get("ticker") or ""
                sub = m.get("yes_sub_title") or ""           # 'Reg Time: Portugal'
                team = _norm(sub.split(":")[-1])
                if not team or team == "tie":
                    continue
                day = _ticker_day(tk)
                if day:
                    out[(day, team)] = tk
            cursor = (page or {}).get("cursor") or ""
            if not cursor or not mkts:
                break
    return out


def pm_wc_games() -> dict:
    """(day, team) -> polymarket YES clobToken, for 'Will <team> win on <date>' markets."""
    out: dict[tuple[str, str], str] = {}
    for closed in ("false", "true"):     # open + recently-closed (played) games
        for page in range(8):
            evs = xl._get(f"{GAMMA}/events?closed={closed}&tag_slug=soccer"
                          f"&limit=100&offset={page * 100}")
            if not isinstance(evs, list) or not evs:
                break
            for e in evs:
                for m in e.get("markets") or []:
                    mq = _PM_WIN.search(m.get("question") or "")
                    if not mq:
                        continue
                    toks = m.get("clobTokenIds")
                    if isinstance(toks, str):
                        try:
                            toks = json.loads(toks)
                        except json.JSONDecodeError:
                            toks = None
                    if isinstance(toks, list) and toks:
                        out[(mq.group(2), _norm(mq.group(1)))] = str(toks[0])  # YES token
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=3.0, help="look back N days for games")
    ap.add_argument("--shock", type=float, default=2.0, help="per-minute jump threshold (cents)")
    ap.add_argument("--horizon", type=int, default=2, help="follow window (minutes)")
    args = ap.parse_args(argv)
    end = int(time.time())
    start = end - int(args.days * 86400)
    shock = args.shock / 100.0

    kg = kalshi_wc_games()
    pg = pm_wc_games()
    matched = sorted(set(kg) & set(pg))
    print(f"=== In-play game lead-lag (WC, 1-min, shock>={args.shock:g}c,"
          f" follow={args.horizon}min) ===")
    print(f"  Kalshi games: {len(kg)}  Polymarket games: {len(pg)}  matched: {len(matched)}")
    if not matched:
        print(f"  Kalshi sample keys: {sorted(kg)[:8]}")
        print(f"  PM sample keys: {sorted(pg)[:8]}")
        return 0

    pm_kal, kal_pm = xs.Shock(), xs.Shock()
    per_game = []
    for day, team in matched:
        ks_min = xc.kalshi_candles("KXWCGAME", kg[(day, team)], start, end)   # {min -> yes mid}
        ps_min = xl.pm_series(pg[(day, team)], start, end)                    # {min -> yes prob}
        kal, pm = xl.align(ks_min, ps_min)                                   # minute-aligned lists
        if len(kal) < 20:
            per_game.append((day, team, len(ks_min), len(ps_min), 0))
            continue
        a = xs.shock_study(pm, kal, shock, args.horizon)   # PM shocks -> does Kalshi follow
        b = xs.shock_study(kal, pm, shock, args.horizon)   # Kalshi shocks -> does PM follow
        pm_kal.merge(a)
        kal_pm.merge(b)
        per_game.append((day, team, len(ks_min), len(ps_min), a.n + b.n))

    print(f"\n  {'day':>11} {'team':>14} {'kBars':>6} {'pBars':>6} {'shocks':>7}")
    for day, team, nk, np_, sh in per_game:
        print(f"  {day:>11} {team:>14} {nk:6d} {np_:6d} {sh:7d}")

    print(f"\n  {'direction':>14} {'n':>5} {'jump':>6} {'same':>5} {'foll%':>6} {'follow_move':>11}")
    print(pm_kal.row("PM->Kalshi"))
    print(kal_pm.row("Kalshi->PM"))
    print("\n  READ: PM leads tradeably if PM->Kalshi follow% > ~55%, same-bar% low,"
          " follow_move clears Kalshi's ~2-4c round-trip, and > Kalshi->PM.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
