"""MLBWX probe — does Kalshi's MLB RUN-TOTAL market lag a weather (imminent-rain) nowcast?

Pre-registered thesis in docs/IDEA_MODEL_20260709.md (MLBWX). READ-ONLY backtest on public +
already-collected data — NO new live collector:

  - Kalshi 1-min candlesticks for KXMLBTOTAL (run-total over/under) via the public
    candlesticks endpoint (same shape as xvenue_leadlag). KXMLBGAME (winner) is also censused
    for liquidity context but is NOT the tradeable instrument here (see below).
  - MLB schedule + results (final status Postponed/Suspended, total runs) from the public MLB
    stats API (statsapi.mlb.com) — fail-soft.
  - Ballpark weather from Open-Meteo (the same provider the temperature books' HRRR loader
    uses): hourly `precipitation` at the home park, keyed by a hardcoded 30-park lat/lon/tz
    table. `past_days` on the forecast API gives recent observed precip; that observed onset is
    the conservative rain-event marker.

WHY THE TOTAL, NOT THE WINNER: rain carries a SIGNED prior for the total (rain suppresses
scoring / risks postponement -> total lower -> the Over-K contract should FALL), but NO signed
prior for the winner (it doesn't say which team wins). A v1 of this probe tested the winner
market and defined the trade direction from the SETTLED price — a look-ahead bug that
manufactured a fake +EV (you always "profit" if you pick the direction the price ended up
going). This version fixes that: the trade direction is fixed BY THE WEATHER SIGNAL
(short the Over on rain), never by the outcome, and the P&L is the price-path capture.

No-lookahead: the rain-event time t is the first hour observed precip crosses the threshold in
the pre-first-pitch -> early window. Entry is priced at the first 1-min candle STRICTLY AFTER t
(+ --entry-lag-min). Direction is short-Over (weather prior). P1's completion ratio uses the
settled convergence target only as a descriptive denominator (how fast the move completed), NOT
to choose the trade. (Production signal would be an issued-time HRRR precip forecast, which can
LEAD the observed onset; observed onset is the conservative floor. Roofed parks are flagged —
rain rarely postpones there.)

Pre-registered predictions (graded verbatim, do not re-scope):
  P1 nowcast leads: median 5-min repricing completion < 60% (KILL >= 85%).
  P2 lag clears cost: short-Over capture entering at first quote after signal nets >= +4c/ct
     (KILL < 2c).
  P3 pre-game directional total is NOT the edge (<= +1c) — anti-artifact guard.
  P4 concentration & liquidity: effect survives a 2-sided spread <= 5c filter (KILL if only on
     illiquid wide quotes).
  Decision rule: paper book only if P1 AND P2 AND P4.

Read-only public APIs, stdlib only. Usage (via the ops channel):
    {"type":"script","name":"kalshi_mlbwx","args":["--days","45"],"id":"mlbwx-probe-0"}
"""

from __future__ import annotations

import argparse
import statistics
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
STATSAPI = "https://statsapi.mlb.com/api/v1"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

TOTAL_SERIES = "KXMLBTOTAL"      # run-total over/under — the tradeable instrument (signed prior)
WINNER_SERIES = "KXMLBGAME"      # moneyline — censused for context only

# Home ballpark -> (lat, lon, IANA tz, roof?). Keyed by the Kalshi team code (2-3 letters).
# Roofed/retractable parks rarely postpone for rain -> the rain-nowcast edge shouldn't apply;
# flagged, not dropped. Codes double as the known-code set used to split concatenated matchups.
PARKS: dict[str, tuple[float, float, str, bool]] = {
    "ARI": (33.4455, -112.0667, "America/Phoenix", True),
    "ATL": (33.8907, -84.4677, "America/New_York", False),
    "BAL": (39.2839, -76.6217, "America/New_York", False),
    "BOS": (42.3467, -71.0972, "America/New_York", False),
    "CHC": (41.9484, -87.6553, "America/Chicago", False),
    "CWS": (41.8299, -87.6338, "America/Chicago", False),
    "CIN": (39.0975, -84.5069, "America/New_York", False),
    "CLE": (41.4962, -81.6852, "America/New_York", False),
    "COL": (39.7559, -104.9942, "America/Denver", False),
    "DET": (42.3390, -83.0485, "America/New_York", False),
    "HOU": (29.7572, -95.3555, "America/Chicago", True),
    "KC": (39.0517, -94.4803, "America/Chicago", False),
    "LAA": (33.8003, -117.8827, "America/Los_Angeles", False),
    "LAD": (34.0739, -118.2400, "America/Los_Angeles", False),
    "MIA": (25.7781, -80.2197, "America/New_York", True),
    "MIL": (43.0280, -87.9712, "America/Chicago", True),
    "MIN": (44.9817, -93.2776, "America/Chicago", False),
    "NYM": (40.7571, -73.8458, "America/New_York", False),
    "NYY": (40.8296, -73.9262, "America/New_York", False),
    "ATH": (38.5802, -121.5133, "America/Los_Angeles", False),
    "PHI": (39.9061, -75.1665, "America/New_York", False),
    "PIT": (40.4469, -80.0057, "America/New_York", False),
    "SD": (32.7073, -117.1566, "America/Los_Angeles", False),
    "SF": (37.7786, -122.3893, "America/Los_Angeles", False),
    "SEA": (47.5914, -122.3325, "America/Los_Angeles", True),
    "STL": (38.6226, -90.1928, "America/Chicago", False),
    "TB": (27.9797, -82.5065, "America/New_York", False),
    "TEX": (32.7473, -97.0825, "America/Chicago", True),
    "TOR": (43.6414, -79.3894, "America/Toronto", True),
    "WSH": (38.8730, -77.0074, "America/New_York", False),
}
ALL_CODES = set(PARKS)  # for splitting concatenated away+home matchup strings
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


# --- Kalshi ------------------------------------------------------------------------


def kalshi_markets(series: str, status: str = "") -> list[dict]:
    out: list[dict] = []
    cursor = ""
    for _ in range(30):
        q = f"series_ticker={series}&limit=200&cursor={cursor}"
        if status:
            q += f"&status={status}"
        page = xl._get(f"{KALSHI}/markets?{q}")
        mkts = (page or {}).get("markets") or []
        out.extend(mkts)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not mkts:
            break
    return out


def series_exists(series: str) -> int:
    n = len(kalshi_markets(series))
    return n if n else len(kalshi_markets(series, status="settled"))


def kalshi_candles(series: str, ticker: str, start: int, end: int) -> dict[int, tuple[float, float]]:
    """minute -> (yes_mid_dollars, spread_cents) from 1-min candlesticks. Generalized from
    xvenue_leadlag.kalshi_series (which hardcodes the WC series in the path)."""
    out: dict[int, tuple[float, float]] = {}
    s = start
    while s < end:
        e = min(s + 4800 * 60, end)
        url = (f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
               f"?start_ts={s}&end_ts={e}&period_interval=1")
        data = xl._get(url)
        for c in (data or {}).get("candlesticks") or []:
            ts = c.get("end_period_ts")
            if ts is None:
                continue
            yb = (c.get("yes_bid") or {}).get("close_dollars")
            ya = (c.get("yes_ask") or {}).get("close_dollars")
            if yb is not None and ya is not None:
                b, a = xl._num(yb), xl._num(ya)
                out[int(ts) // 60] = ((b + a) / 2.0, abs(a - b) * 100.0)
            else:
                mid = xl._num((c.get("price") or {}).get("close_dollars"))
                if mid > 0:
                    out[int(ts) // 60] = (mid, float("nan"))
        s = e
    return out


def split_matchup(mu: str) -> tuple[str, str] | None:
    """'NYYTB' -> ('NYY','TB'); 'CHCCIN' -> ('CHC','CIN'). Concatenated away+home of 2-3 letter
    codes; try home=3 then home=2 and require both halves to be known codes (fixes the 2-letter
    home bug where a fixed [:3]/[3:] split mangles TB/SF/SD/KC)."""
    for hl in (3, 2):
        if len(mu) > hl:
            home, away = mu[-hl:], mu[:-hl]
            if home in ALL_CODES and away in ALL_CODES:
                return away, home
    return None


def parse_total_ticker(ticker: str):
    """KXMLBTOTAL-26JUL091310NYYTB-15 -> (date, away, home, fp_epoch_utc, strike_K). YES = over
    (K-0.5) runs. The clock in the ticker is Kalshi's ET convention; kept only for coarse
    pre/in-game phase labelling (weather windowing is done in local park time)."""
    parts = ticker.split("-")
    if len(parts) < 3:
        return None
    seg = parts[1]
    try:
        strike = int(parts[2])
    except ValueError:
        return None
    if len(seg) < 13:
        return None
    yy, mon, dd, hhmm, matchup = seg[0:2], seg[2:5], seg[5:7], seg[7:11], seg[11:]
    m = _MON.get(mon)
    sp = split_matchup(matchup)
    if not m or not sp:
        return None
    away, home = sp
    try:
        date = f"20{yy}-{m:02d}-{int(dd):02d}"
        fp = datetime(2000 + int(yy), m, int(dd), int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)
    except ValueError:
        return None
    return date, away, home, int(fp.timestamp()), strike


# --- MLB schedule/results (fail-soft) ----------------------------------------------

_NAME2CODE = {
    "diamondbacks": "ARI", "braves": "ATL", "orioles": "BAL", "red sox": "BOS",
    "cubs": "CHC", "white sox": "CWS", "reds": "CIN", "guardians": "CLE", "rockies": "COL",
    "tigers": "DET", "astros": "HOU", "royals": "KC", "angels": "LAA", "dodgers": "LAD",
    "marlins": "MIA", "brewers": "MIL", "twins": "MIN", "mets": "NYM", "yankees": "NYY",
    "athletics": "ATH", "phillies": "PHI", "pirates": "PIT", "padres": "SD", "giants": "SF",
    "mariners": "SEA", "cardinals": "STL", "rays": "TB", "rangers": "TEX", "blue jays": "TOR",
    "nationals": "WSH",
}


def _team_code(name: str) -> str:
    n = (name or "").lower()
    for key, code in _NAME2CODE.items():
        if key in n:
            return code
    return ""


def mlb_schedule(start_date: str, end_date: str) -> dict[tuple[str, str], dict]:
    """{(date, home_code): {status, total_runs, postponed}} from the public MLB stats API."""
    out: dict[tuple[str, str], dict] = {}
    url = f"{STATSAPI}/schedule?sportId=1&startDate={start_date}&endDate={end_date}&hydrate=linescore"
    data = xl._get(url)
    for day in (data or {}).get("dates") or []:
        date = day.get("date")
        for g in day.get("games") or []:
            home = (((g.get("teams") or {}).get("home") or {}).get("team") or {}).get("name") or ""
            code = _team_code(home)
            if not code:
                continue
            status = (g.get("status") or {}).get("detailedState") or ""
            ls = (g.get("linescore") or {}).get("teams") or {}
            hr, ar = ls.get("home", {}).get("runs"), ls.get("away", {}).get("runs")
            total = (xl._num(hr) + xl._num(ar)) if hr is not None and ar is not None else None
            out[(date, code)] = {
                "status": status, "total_runs": total,
                "postponed": "postpone" in status.lower() or "suspend" in status.lower(),
            }
    return out


# --- weather (Open-Meteo) ----------------------------------------------------------


def park_precip(lat: float, lon: float, tz: str, past_days: int) -> dict[str, float]:
    """{local_iso_hour: precip_mm} — forecast API with past_days gives recent observed precip
    (same provider as the temperature books' HRRR loader). Fail-soft -> {}."""
    params = {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "hourly": "precipitation",
        "past_days": str(min(max(past_days, 1), 92)), "forecast_days": "1", "timezone": tz,
    }
    data = xl._get(f"{OPEN_METEO}?{urllib.parse.urlencode(params)}")
    hourly = (data or {}).get("hourly") or {}
    times, precip = hourly.get("time") or [], hourly.get("precipitation") or []
    return {str(t): float(precip[i]) for i, t in enumerate(times)
            if i < len(precip) and precip[i] is not None}


def rain_event_epoch(precip: dict[str, float], date: str, tz: str, threshold: float):
    """First hour on `date` whose observed precip >= threshold(mm) -> (event_epoch_utc,
    peak_mm) or None. Whole game-day window (the ticker-clock tz is uncertain)."""
    from zoneinfo import ZoneInfo
    try:
        zone = ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        return None
    best, peak = None, 0.0
    for iso, mm in sorted(precip.items()):
        if not iso.startswith(date):
            continue
        peak = max(peak, mm)
        if mm >= threshold and best is None:
            try:
                local = datetime.fromisoformat(iso).replace(tzinfo=zone)
            except ValueError:
                continue
            best = int(local.astimezone(timezone.utc).timestamp())
    return (best, peak) if best is not None else None


# --- analysis (no-lookahead, weather-signed) ---------------------------------------


def _mid_at(candles: dict[int, tuple[float, float]], keys: list[int], minute: int):
    v = None
    for k in keys:
        if k <= minute:
            v = candles[k]
        else:
            break
    return v


def reprice(candles, t_epoch, entry_lag):
    """Short-the-Over capture from rain event t. Direction is FIXED short (weather prior:
    rain -> total down -> Over price falls); it is NOT read from the outcome. Returns
    completion ratios (P1) + the price-path capture in cents (P2), or None if candles don't
    bracket the event or the market is flat (no move to measure)."""
    if len(candles) < 20:
        return None
    keys = sorted(candles)
    tm = t_epoch // 60
    if not (keys[0] <= tm <= keys[-1]):
        return None
    pre = _mid_at(candles, keys, tm - 1)
    entry = _mid_at(candles, keys, tm + entry_lag)
    settle = candles[keys[-1]]
    m1, m5, m15 = (_mid_at(candles, keys, tm + h) for h in (1, 5, 15))
    if pre is None or entry is None:
        return None
    pre_p, settle_p = pre[0], settle[0]
    total_move = settle_p - pre_p                 # signed; expected < 0 under the rain prior
    if abs(total_move) < 0.005:
        return None

    def compl(m):
        return abs(m[0] - pre_p) / abs(total_move) if m else float("nan")

    # short-Over P&L on the price PATH (sell at entry, buy back later): profit when Over falls.
    def cap(m):
        return (entry[0] - m[0]) * 100.0 if m else float("nan")

    return {
        "pre": pre_p, "entry": entry[0], "settle": settle_p, "total_move_c": total_move * 100.0,
        "c1": compl(m1), "c5": compl(m5), "c15": compl(m15),
        "cap5_c": cap(m5), "cap15_c": cap(m15), "cap_settle_c": cap(settle),
        "entry_spread_c": entry[1],
    }


def _med(xs):
    xs = [x for x in xs if x == x]
    return statistics.median(xs) if xs else float("nan")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--series", default=TOTAL_SERIES)
    ap.add_argument("--rain-threshold", type=float, default=1.0, help="mm/hr precip = rain event")
    ap.add_argument("--entry-lag-min", type=int, default=1)
    ap.add_argument("--liq-spread-c", type=float, default=5.0, help="P4 2-sided spread filter (c)")
    ap.add_argument("--max-games", type=int, default=60, help="cap analyzed rain games (runtime)")
    args = ap.parse_args(argv)

    now = int(time.time())
    start_date = datetime.fromtimestamp(now - args.days * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
    end_date = datetime.fromtimestamp(now + 2 * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"=== MLBWX probe v2 — {args.days}d, series={args.series} (short-Over on rain), "
          f"rain>={args.rain_threshold}mm, entry+{args.entry_lag_min}m ===\n")

    print("--- INSTRUMENTS ---")
    print(f"  total  {TOTAL_SERIES:<12} markets={series_exists(TOTAL_SERIES)}")
    print(f"  winner {WINNER_SERIES:<12} markets={series_exists(WINNER_SERIES)} (context only)")

    # --- group total markets by game, collect strikes ---
    markets = {m.get("ticker"): m for m in
               kalshi_markets(args.series) + kalshi_markets(args.series, status="settled")}
    games: dict[tuple[str, str, str, int], list[dict]] = defaultdict(list)
    for tk, m in markets.items():
        info = parse_total_ticker(tk or "")
        if not info:
            continue
        date, away, home, fp, strike = info
        if not (start_date <= date <= end_date):
            continue
        yb, ya = xl._num(m.get("yes_bid_dollars")), xl._num(m.get("yes_ask_dollars"))
        games[(date, away, home, fp)].append({
            "ticker": tk, "strike": strike,
            "mid": (yb + ya) / 2.0 if yb > 0 and ya > 0 else float("nan"),
            "spread_c": (ya - yb) * 100.0 if ya > 0 and yb > 0 else float("nan"),
            "vol": xl._num(m.get("volume_fp") or m.get("volume")),
        })
    print("\n--- CENSUS (KXMLBTOTAL) ---")
    n_games = len(games)
    parked = [g for g in games if g[2] in PARKS]
    open_parks = [g for g in parked if not PARKS[g[2]][3]]
    all_spreads = [s["spread_c"] for legs in games.values() for s in legs if s["spread_c"] == s["spread_c"]]
    print(f"  games: {n_games}   mapped park: {len(parked)}   open-air parks: {len(open_parks)}")
    if all_spreads:
        print(f"  contract spread(c): median={_med(all_spreads):.1f}  "
              f"<= {args.liq_spread_c:g}c: {100*sum(1 for s in all_spreads if s<=args.liq_spread_c)/len(all_spreads):.0f}%")

    # --- weather rain events (open parks) ---
    sched = mlb_schedule(start_date, end_date)
    print(f"  MLB stats API games loaded: {len(sched)}"
          + ("" if sched else "  (labels unavailable — continuing on Kalshi+weather)"))
    precip_cache: dict[str, dict] = {}
    rain_games = []
    for key in open_parks:
        date, away, home, fp = key
        lat, lon, tz, _ = PARKS[home]
        ck = f"{lat},{lon}"
        if ck not in precip_cache:
            precip_cache[ck] = park_precip(lat, lon, tz, args.days)
        ev = rain_event_epoch(precip_cache[ck], date, tz, args.rain_threshold)
        if not ev:
            continue
        lab = sched.get((date, home))
        rain_games.append({"key": key, "date": date, "home": home, "fp": fp,
                           "event": ev[0], "peak_mm": ev[1], "legs": games[key],
                           "postponed": lab["postponed"] if lab else None,
                           "total_runs": lab["total_runs"] if lab else None})
    print(f"  open-park games with a rain event (precip>={args.rain_threshold}mm): {len(rain_games)}")
    ppd = [g for g in rain_games if g.get("postponed")]
    if ppd:
        print(f"  of which POSTPONED/SUSPENDED: {len(ppd)}")

    # prioritize postponed + heaviest rain, cap for runtime
    rain_games.sort(key=lambda g: (0 if g.get("postponed") else 1, -g["peak_mm"]))
    rain_games = rain_games[:args.max_games]

    # --- repricing on the near-the-money Over contract (no-lookahead, short-Over) ---
    print("\n--- REPRICING: short-Over response to the rain event (weather-signed) ---")
    rows = []
    for g in rain_games:
        # choose the strike whose pre-event mid is closest to 0.5 (the market's central line).
        best = None
        for leg in g["legs"]:
            candles = kalshi_candles(args.series, leg["ticker"], g["event"] - 6 * 3600, g["event"] + 12 * 3600)
            keys = sorted(candles)
            if not keys:
                continue
            pre = _mid_at(candles, keys, g["event"] // 60 - 1)
            if pre is None:
                continue
            dist = abs(pre[0] - 0.5)
            if best is None or dist < best[0]:
                best = (dist, leg, candles)
        if not best:
            continue
        _, leg, candles = best
        met = reprice(candles, g["event"], args.entry_lag_min)
        if not met:
            continue
        met.update(date=g["date"], home=g["home"], peak_mm=g["peak_mm"], strike=leg["strike"],
                   postponed=g.get("postponed"),
                   phase="pre" if g["event"] <= g["fp"] else "in")
        rows.append(met)
    if not rows:
        print("  (0 rain games had a near-the-money Over contract with candles bracketing the event)")
    else:
        print(f"  {'date':<11}{'pk':<4}{'K':>3}{'mm':>5}{'ph':>3}{'tot_c':>7}{'c5':>6}{'c15':>6}"
              f"{'cap5':>7}{'cap15':>7}{'capS':>7}{'spr':>5}")
        for r in rows:
            print(f"  {r['date']:<11}{r['home']:<4}{r['strike']:>3}{r['peak_mm']:>5.1f}{r['phase']:>3}"
                  f"{r['total_move_c']:>+7.1f}{r['c5']:>6.2f}{r['c15']:>6.2f}"
                  f"{r['cap5_c']:>+7.1f}{r['cap15_c']:>+7.1f}{r['cap_settle_c']:>+7.1f}{r['entry_spread_c']:>5.1f}")

    # --- verdict ---
    print("\n=== PRE-REGISTERED VERDICTS (docs/IDEA_MODEL_20260709.md — MLBWX) ===")
    liq = [r for r in rows if r["entry_spread_c"] == r["entry_spread_c"]
           and r["entry_spread_c"] <= args.liq_spread_c]
    if len(liq) < 3:
        print(f"  P4: only {len(liq)} rain-repricing samples clear the {args.liq_spread_c:g}c spread")
        print("     filter -> INSUFFICIENT no-lookahead data to grade P1/P2. (Instruments exist &")
        print("     are liquid in general, but rain-affected games with a tradeable ATM Over")
        print("     contract + candles bracketing the event are too few in this window.)")
        print("  DECISION: do not promote yet; widen --days / lower --rain-threshold and re-run,")
        print("     or journal as a cheap hold if rain games stay too sparse.")
        return 0
    med_c5 = _med([r["c5"] for r in liq])
    med_cap5 = _med([r["cap5_c"] for r in liq])
    med_capS = _med([r["cap_settle_c"] for r in liq])
    pre_capS = _med([r["cap_settle_c"] for r in liq if r["phase"] == "pre"])
    p1 = "PASS" if med_c5 < 0.60 else ("KILL" if med_c5 >= 0.85 else "GREY")
    # P2 uses the settlement capture (full lag realized); >=+4c pass, <2c kill.
    p2 = "PASS" if med_capS >= 4.0 else ("KILL" if med_capS < 2.0 else "GREY")
    p4 = "PASS" if len(liq) >= 5 else "THIN"
    print(f"  P1 median 5-min completion < 60%: {med_c5:.2f} (n={len(liq)}) -> {p1}")
    print(f"  P2 short-Over capture to settle >= +4c: {med_capS:+.1f}c "
          f"(+5m path {med_cap5:+.1f}c) -> {p2}")
    print(f"  P3 pre-game-only capture (level guard): {pre_capS:+.1f}c "
          f"(if this alone is large, edge is the level not the lag)")
    print(f"  P4 survives spread<={args.liq_spread_c:g}c: {len(liq)}/{len(rows)} rows -> {p4}")
    ok = p1 == "PASS" and p2 == "PASS" and p4 == "PASS"
    print(f"  DECISION (P1 AND P2 AND P4): {'PROMOTE -> paper book mlbwx' if ok else 'do not promote'}")
    if ok:
        print("  NOTE: verify direction is weather-driven (short-Over), not an outcome artifact,")
        print("        before building — inspect the per-row cap columns for sign consistency.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
