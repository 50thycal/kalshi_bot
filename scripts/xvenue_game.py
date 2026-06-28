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
import re
import time

import xvenue_crypto as xc  # kalshi_candles (reliable 1-min)
import xvenue_leadlag as xl  # _get, _num, align
import xvenue_shock as xs  # shock_study, Shock

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"

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
    """(day, team) -> polymarket conditionId, for 'Will <team> win on <date>' markets."""
    out: dict[tuple[str, str], str] = {}
    for tag in ("soccer",):
        for page in range(8):
            evs = xl._get(f"{GAMMA}/events?closed=false&tag_slug={tag}&limit=100&offset={page * 100}")
            if not isinstance(evs, list) or not evs:
                # also try recently-closed games (played in last days)
                evs = xl._get(f"{GAMMA}/events?closed=true&tag_slug={tag}&limit=100&offset={page * 100}")
            if not isinstance(evs, list) or not evs:
                break
            for e in evs:
                for m in e.get("markets") or []:
                    mq = _PM_WIN.search(m.get("question") or "")
                    if not mq:
                        continue
                    cond = m.get("conditionId")
                    if cond:
                        out[(mq.group(2), _norm(mq.group(1)))] = cond
    return out


# --- trade tapes -> bars ------------------------------------------------------------


def kalshi_bars(ticker: str, start: int, end: int, bar: int) -> dict[int, float]:
    """bar-bucket -> Kalshi yes-mid (0..1), from the reliable 1-min candlestick endpoint
    (the /trades endpoint returned nothing for these markets). 1-min candles forward-fill
    onto the finer bar grid in align(); a 1-2 min lag is still visible."""
    mins = xc.kalshi_candles("KXWCGAME", ticker, start, end)   # {minute -> mid 0..1}
    return {(m * 60) // bar: p for m, p in mins.items()}


def pm_bars(cond: str, start: int, end: int, bar: int) -> dict[int, float]:
    """bar-bucket -> last price (0..1) from the Polymarket trade tape."""
    last: dict[int, float] = {}
    offset = 0
    for _ in range(40):
        data = xl._get(f"{DATA}/trades?market={cond}&limit=500&offset={offset}")
        if not isinstance(data, list) or not data:
            break
        for tr in data:
            ts = tr.get("timestamp")
            price = tr.get("price")
            try:
                ts = int(ts)
            except (TypeError, ValueError):
                continue
            if price is None or ts < start or ts > end:
                continue
            last[ts // bar] = float(price)
        if len(data) < 500:
            break
        offset += len(data)
    return last


def _ffill(d: dict[int, float], lo: int, hi: int) -> dict[int, float]:
    out, cur = {}, None
    for t in range(lo, hi + 1):
        cur = d.get(t, cur)
        if cur is not None:
            out[t] = cur
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=3.0, help="look back N days for games")
    ap.add_argument("--bar", type=int, default=30, help="bar size (seconds)")
    ap.add_argument("--shock", type=float, default=2.0, help="per-bar jump threshold (cents)")
    ap.add_argument("--horizon", type=int, default=4, help="follow window (bars)")
    args = ap.parse_args(argv)
    end = int(time.time())
    start = end - int(args.days * 86400)
    shock = args.shock / 100.0

    kg = kalshi_wc_games()
    pg = pm_wc_games()
    matched = sorted(set(kg) & set(pg))
    print(f"=== In-play game lead-lag (WC, {args.bar}s bars, shock>={args.shock:g}c,"
          f" follow={args.horizon} bars) ===")
    print(f"  Kalshi games: {len(kg)}  Polymarket games: {len(pg)}  matched: {len(matched)}")
    if not matched:
        print(f"  Kalshi sample keys: {sorted(kg)[:8]}")
        print(f"  PM sample keys: {sorted(pg)[:8]}")
        return 0

    pm_kal, kal_pm = xs.Shock(), xs.Shock()
    per_game = []
    for day, team in matched:
        kb = kalshi_bars(kg[(day, team)], start, end, args.bar)
        pb = pm_bars(pg[(day, team)], start, end, args.bar)
        if not kb or not pb:
            per_game.append((day, team, len(kb), len(pb), 0))
            continue
        lo, hi = max(min(kb), min(pb)), min(max(kb), max(pb))
        if hi - lo < 10:
            per_game.append((day, team, len(kb), len(pb), 0))
            continue
        kf, pf = _ffill(kb, lo, hi), _ffill(pb, lo, hi)
        ks = [kf[t] for t in range(lo, hi + 1) if t in kf and t in pf]
        ps = [pf[t] for t in range(lo, hi + 1) if t in kf and t in pf]
        n = min(len(ks), len(ps))
        ks, ps = ks[:n], ps[:n]
        a = xs.shock_study(ps, ks, shock, args.horizon)   # PM shocks -> Kalshi follows
        b = xs.shock_study(ks, ps, shock, args.horizon)   # Kalshi shocks -> PM follows
        pm_kal.merge(a)
        kal_pm.merge(b)
        per_game.append((day, team, len(kb), len(pb), a.n + b.n))

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
