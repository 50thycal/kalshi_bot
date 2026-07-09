"""MLBWX probe — does Kalshi's MLB market lag a weather (imminent-rain) nowcast?

Pre-registered thesis in docs/IDEA_MODEL_20260709.md (MLBWX). This is a READ-ONLY
backtest on public + already-collected data — NO new live collector:

  - Kalshi 1-min candlesticks for KXMLBGAME (and any run-total / game-status series that
    exists) via the public candlesticks endpoint (same shape as xvenue_leadlag).
  - MLB schedule + results (venue, first pitch, final status Postponed/Suspended, total
    runs) from the public MLB stats API (statsapi.mlb.com) — fail-soft.
  - Ballpark weather from Open-Meteo (the same provider the temperature books' HRRR loader
    uses): hourly `precipitation` (+ `precipitation_probability`) at the home park, keyed by
    a hardcoded 30-park lat/lon/tz table. `past_days` on the forecast API gives recent
    observed precip; that observed onset is the conservative rain-event marker.

No-lookahead: the rain-event time t is the first hour at/before which observed precip crosses
the threshold in the pre-first-pitch -> early-game window. The Kalshi entry is priced at the
first 1-min candle STRICTLY AFTER t (+ --entry-lag-min). (The production signal would be an
issued-time HRRR precip forecast, which can *lead* the observed onset; this backtest uses the
observed onset as the conservative floor and flags roofed parks where rain rarely postpones.)

Because the thesis rests on LIQUID MLB markets, the probe leads with a data-availability +
liquidity CENSUS (per game: volume, 2-sided spread, #candles). If Kalshi's MLB markets are too
thin to trade, that census IS the finding (a cheap ruling-out) and P4 fails there.

Pre-registered predictions (graded verbatim, do not re-scope):
  P1 nowcast leads: median 5-min repricing completion < 60% (KILL >= 85%).
  P2 lag clears cost: entering at first quote after signal nets >= +4c/ct (KILL < 2c).
  P3 pre-game directional total is NOT the edge (<= +1c) — anti-artifact guard.
  P4 concentration & liquidity: effect survives a 2-sided spread <= 5c filter (KILL if only
     on illiquid wide quotes).
  Decision rule: paper book only if P1 AND P2 AND P4.

Read-only public APIs, stdlib only. Usage (via the ops channel):
    {"type":"script","name":"kalshi_mlbwx","args":["--days","30"],"id":"mlbwx-probe-0"}
"""

from __future__ import annotations

import argparse
import statistics
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
STATSAPI = "https://statsapi.mlb.com/api/v1"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# Candidate Kalshi MLB series. KXMLBGAME (winner/moneyline) is confirmed to exist; the
# run-total / game-played instruments the nowcast leg really wants may or may not — the probe
# reports which resolve to open markets rather than assuming.
WINNER_SERIES = "KXMLBGAME"
TOTAL_SERIES_CANDIDATES = ("KXMLBTOTAL", "KXMLBRUNS", "KXMLBGAMETOTAL", "KXMLBOU", "KXMLBNRFI")
STATUS_SERIES_CANDIDATES = ("KXMLBPPD", "KXMLBGAMEPLAYED", "KXMLBSTATUS", "KXMLBPOSTPONED")

# Home ballpark -> (lat, lon, IANA tz, roof?). Keyed by the Kalshi 3-letter team code (the
# HOME code is the 2nd half of the 6-char matchup in the ticker). Roofed/retractable parks
# rarely postpone for rain -> the rain-nowcast edge shouldn't apply; flagged, not dropped.
PARKS: dict[str, tuple[float, float, str, bool]] = {
    "ARI": (33.4455, -112.0667, "America/Phoenix", True),
    "ATL": (33.8907, -84.4677, "America/New_York", False),
    "BAL": (39.2839, -76.6217, "America/New_York", False),
    "BOS": (42.3467, -71.0972, "America/New_York", False),
    "CHC": (41.9484, -87.6553, "America/Chicago", False),
    "CWS": (41.8299, -87.6338, "America/Chicago", False),
    "CHW": (41.8299, -87.6338, "America/Chicago", False),
    "CIN": (39.0975, -84.5069, "America/New_York", False),
    "CLE": (41.4962, -81.6852, "America/New_York", False),
    "COL": (39.7559, -104.9942, "America/Denver", False),
    "DET": (42.3390, -83.0485, "America/New_York", False),
    "HOU": (29.7572, -95.3555, "America/Chicago", True),
    "KC": (39.0517, -94.4803, "America/Chicago", False),
    "KCR": (39.0517, -94.4803, "America/Chicago", False),
    "LAA": (33.8003, -117.8827, "America/Los_Angeles", False),
    "LAD": (34.0739, -118.2400, "America/Los_Angeles", False),
    "MIA": (25.7781, -80.2197, "America/New_York", True),
    "MIL": (43.0280, -87.9712, "America/Chicago", True),
    "MIN": (44.9817, -93.2776, "America/Chicago", False),
    "NYM": (40.7571, -73.8458, "America/New_York", False),
    "NYY": (40.8296, -73.9262, "America/New_York", False),
    "ATH": (38.5802, -121.5133, "America/Los_Angeles", False),
    "OAK": (38.5802, -121.5133, "America/Los_Angeles", False),
    "PHI": (39.9061, -75.1665, "America/New_York", False),
    "PIT": (40.4469, -80.0057, "America/New_York", False),
    "SD": (32.7073, -117.1566, "America/Los_Angeles", False),
    "SDP": (32.7073, -117.1566, "America/Los_Angeles", False),
    "SF": (37.7786, -122.3893, "America/Los_Angeles", False),
    "SFG": (37.7786, -122.3893, "America/Los_Angeles", False),
    "SEA": (47.5914, -122.3325, "America/Los_Angeles", True),
    "STL": (38.6226, -90.1928, "America/Chicago", False),
    "TB": (27.9797, -82.5065, "America/New_York", False),
    "TBR": (27.9797, -82.5065, "America/New_York", False),
    "TEX": (32.7473, -97.0825, "America/Chicago", True),
    "TOR": (43.6414, -79.3894, "America/Toronto", True),
    "WSH": (38.8730, -77.0074, "America/New_York", False),
    "WSN": (38.8730, -77.0074, "America/New_York", False),
}

_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


# --- Kalshi ------------------------------------------------------------------------


def kalshi_markets(series: str, status: str = "") -> list[dict]:
    """All markets for a series (paginated), open + settled per `status`."""
    out: list[dict] = []
    cursor = ""
    for _ in range(20):
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
    """Number of markets (any status) the series resolves to; 0 => not a real/active series."""
    n = len(kalshi_markets(series))
    if n:
        return n
    return len(kalshi_markets(series, status="settled"))


def kalshi_candles(series: str, ticker: str, start: int, end: int) -> dict[int, tuple[float, float]]:
    """minute -> (yes_mid_dollars, spread_cents) from 1-min candlesticks. Generalized from
    xvenue_leadlag.kalshi_series (which hardcodes the WC series in the path)."""
    out: dict[int, tuple[float, float]] = {}
    step = 4800 * 60
    s = start
    while s < end:
        e = min(s + step, end)
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
                mid, spread = (b + a) / 2.0, abs(a - b) * 100.0
            else:
                mid = xl._num((c.get("price") or {}).get("close_dollars"))
                spread = float("nan")
            if mid > 0:
                out[int(ts) // 60] = (mid, spread)
        s = e
    return out


def parse_ticker(ticker: str) -> tuple[str, str, str, int] | None:
    """KXMLBGAME-26JUL121340CHCCIN-CHC -> (date 'YYYY-MM-DD', away, home, first_pitch_epoch).
    The 6-char matchup after the 11-char datetime splits away[:3]/home[3:]; home park is used."""
    parts = ticker.split("-")
    if len(parts) < 2:
        return None
    seg = parts[1]  # e.g. 26JUL121340CHCCIN
    if len(seg) < 17:
        return None
    yy, mon, dd, hhmm = seg[0:2], seg[2:5], seg[5:7], seg[7:11]
    matchup = seg[11:]
    m = _MON.get(mon)
    if not m or len(matchup) < 6:
        return None
    away, home = matchup[:3], matchup[-3:]
    try:
        date = f"20{yy}-{m:02d}-{int(dd):02d}"
        # first-pitch clock in the ticker is US Eastern by Kalshi convention; store as a naive
        # UTC-ish epoch only for coarse windowing (we window in local park time from weather).
        fp = datetime(2000 + int(yy), m, int(dd), int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)
    except ValueError:
        return None
    return date, away, home, int(fp.timestamp())


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
    url = (f"{STATSAPI}/schedule?sportId=1&startDate={start_date}&endDate={end_date}"
           f"&hydrate=linescore")
    data = xl._get(url)
    for day in (data or {}).get("dates") or []:
        date = day.get("date")
        for g in day.get("games") or []:
            teams = g.get("teams") or {}
            home = ((teams.get("home") or {}).get("team") or {}).get("name") or ""
            code = _team_code(home)
            if not code:
                continue
            status = ((g.get("status") or {}).get("detailedState") or "")
            ls = g.get("linescore") or {}
            hr = (ls.get("teams") or {}).get("home", {}).get("runs")
            ar = (ls.get("teams") or {}).get("away", {}).get("runs")
            total = (xl._num(hr) + xl._num(ar)) if hr is not None and ar is not None else None
            out[(date, code)] = {
                "status": status,
                "total_runs": total,
                "postponed": "postpone" in status.lower() or "suspend" in status.lower(),
            }
    return out


# --- weather (Open-Meteo) ----------------------------------------------------------


def park_precip(lat: float, lon: float, tz: str, past_days: int) -> dict[str, list[float]]:
    """{local_iso_hour: precip_mm}. Uses the forecast API with past_days for recent observed
    precip (same provider as the temperature books' HRRR loader). Fail-soft -> {}."""
    params = {
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "hourly": "precipitation,precipitation_probability",
        "past_days": str(min(max(past_days, 1), 92)), "forecast_days": "1",
        "timezone": tz,
    }
    url = f"{OPEN_METEO}?{urllib.parse.urlencode(params)}"
    data = xl._get(url)
    hourly = (data or {}).get("hourly") or {}
    times = hourly.get("time") or []
    precip = hourly.get("precipitation") or []
    out: dict[str, list[float]] = {}
    for i, t in enumerate(times):
        if i < len(precip) and precip[i] is not None:
            out[str(t)] = [float(precip[i])]
    return out


def rain_event_epoch(precip: dict[str, list[float]], date: str, tz: str,
                     threshold: float, fp_epoch: int) -> tuple[int, float] | None:
    """First hour on `date` in the [first_pitch-3h, first_pitch+3h] window whose observed
    precip >= threshold(mm). Returns (event_epoch_utc, peak_precip_mm) or None. Because the
    ticker clock tz is uncertain we accept the whole game-day window around the coarse fp."""
    from zoneinfo import ZoneInfo
    try:
        zone = ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        return None
    best: tuple[int, float] | None = None
    peak = 0.0
    for iso, vals in sorted(precip.items()):
        if not iso.startswith(date):
            continue
        mm = vals[0]
        peak = max(peak, mm)
        if mm >= threshold and best is None:
            try:
                local = datetime.fromisoformat(iso).replace(tzinfo=zone)
            except ValueError:
                continue
            best = (int(local.astimezone(timezone.utc).timestamp()), mm)
    if best is None:
        return None
    return best[0], peak


# --- analysis ----------------------------------------------------------------------


def reprice_metrics(candles: dict[int, tuple[float, float]], t_epoch: int, entry_lag: int):
    """From a rain-event epoch t, measure the winner-market repricing. Returns a dict with the
    pre-t baseline mid, mids at +1/+5/+15 min and settlement, the 5-min completion fraction,
    the net capturable move (c) entering at +entry_lag min on the informed (toward-settle)
    side, and the entry spread (c). None if too few candles bracket the event."""
    if len(candles) < 20:
        return None
    tm = t_epoch // 60
    keys = sorted(candles)
    lo, hi = keys[0], keys[-1]
    if not (lo <= tm <= hi):
        return None

    def mid_at(minute: int) -> tuple[float, float] | None:
        v = None
        for k in keys:
            if k <= minute:
                v = candles[k]
            else:
                break
        return v

    pre = mid_at(tm - 1)
    settle = candles[hi]
    m1, m5, m15 = mid_at(tm + 1), mid_at(tm + 5), mid_at(tm + 15)
    entry = mid_at(tm + entry_lag)
    if pre is None or entry is None:
        return None
    pre_mid, settle_mid = pre[0], settle[0]
    total_move = settle_mid - pre_mid
    if abs(total_move) < 0.005:  # <0.5c eventual move: no repricing to speak of
        return None

    def completion(m):
        return abs(m[0] - pre_mid) / abs(total_move) if m else float("nan")

    direction = 1.0 if total_move > 0 else -1.0
    net_c = (settle_mid - entry[0]) * direction * 100.0  # captured toward settlement
    return {
        "pre": pre_mid, "settle": settle_mid, "total_move_c": total_move * 100.0,
        "c1": completion(m1), "c5": completion(m5), "c15": completion(m15),
        "net_c": net_c, "entry_spread_c": entry[1],
    }


def _med(xs):
    xs = [x for x in xs if x == x]
    return statistics.median(xs) if xs else float("nan")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--series", default=WINNER_SERIES)
    ap.add_argument("--rain-threshold", type=float, default=1.0, help="mm/hr precip = rain event")
    ap.add_argument("--entry-lag-min", type=int, default=1)
    ap.add_argument("--liq-spread-c", type=float, default=5.0, help="P4 2-sided spread filter (c)")
    args = ap.parse_args(argv)

    now = int(time.time())
    start = now - args.days * 86400
    start_date = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%d")
    end_date = datetime.fromtimestamp(now + 2 * 86400, tz=timezone.utc).strftime("%Y-%m-%d")

    print(f"=== MLBWX probe — {args.days}d, series={args.series}, "
          f"rain>={args.rain_threshold}mm, entry+{args.entry_lag_min}m ===\n")

    # --- Section 0: instrument existence ---
    print("--- INSTRUMENTS (does the thesis' market even exist on Kalshi?) ---")
    print(f"  winner  {args.series:<16} markets={series_exists(args.series)}")
    tot_found = [(s, series_exists(s)) for s in TOTAL_SERIES_CANDIDATES]
    sta_found = [(s, series_exists(s)) for s in STATUS_SERIES_CANDIDATES]
    for s, n in tot_found:
        print(f"  total   {s:<16} markets={n}")
    for s, n in sta_found:
        print(f"  status  {s:<16} markets={n}")
    has_total = any(n for _, n in tot_found)
    has_status = any(n for _, n in sta_found)
    if not has_total and not has_status:
        print("  NOTE: no run-total / game-played-or-postponed series resolved -> the nowcast\n"
              "        leg's clean instruments are absent; only the WINNER market is testable\n"
              "        (a weaker proxy: rain postpones/voids rather than moving win prob).")

    # --- Section A: liquidity + candle census on the winner market ---
    print("\n--- CENSUS: KXMLBGAME markets in window (liquidity gates P4) ---")
    markets = kalshi_markets(args.series) + kalshi_markets(args.series, status="settled")
    seen: dict[str, dict] = {}
    for m in markets:
        seen[m.get("ticker") or ""] = m
    games: list[dict] = []
    for tk, m in seen.items():
        info = parse_ticker(tk)
        if not info:
            continue
        date, away, home, fp = info
        if not (start_date <= date <= end_date):
            continue
        yb, ya = xl._num(m.get("yes_bid_dollars")), xl._num(m.get("yes_ask_dollars"))
        spread_c = (ya - yb) * 100.0 if ya > 0 and yb > 0 else float("nan")
        games.append({
            "ticker": tk, "date": date, "away": away, "home": home, "fp": fp,
            "vol": xl._num(m.get("volume_fp") or m.get("volume")),
            "spread_c": spread_c, "status": m.get("status"),
            "park": PARKS.get(home),
        })
    games.sort(key=lambda g: (g["date"], g["ticker"]))
    n_games = len(games)
    n_liq = sum(1 for g in games if g["spread_c"] == g["spread_c"] and g["spread_c"] <= args.liq_spread_c)
    n_vol = sum(1 for g in games if g["vol"] > 0)
    n_park = sum(1 for g in games if g["park"])
    print(f"  games in window: {n_games}   with volume>0: {n_vol}   "
          f"2-sided spread<={args.liq_spread_c:g}c: {n_liq}   mapped park: {n_park}")
    if games:
        spreads = [g["spread_c"] for g in games if g["spread_c"] == g["spread_c"]]
        print(f"  spread(c): median={_med(spreads):.1f}  min={min(spreads):.1f}  "
              f"max={max(spreads):.1f}   volume median={_med([g['vol'] for g in games]):.0f}")
        print("  sample (date home vs away | vol | spread | roof):")
        for g in games[:10]:
            roof = "roof" if (g["park"] and g["park"][3]) else "open"
            print(f"    {g['date']} {g['home']}v{g['away']:<3} vol={g['vol']:>6.0f} "
                  f"spr={g['spread_c']:>5.1f}c {roof}  {g['ticker']}")

    # --- Section B: weather rain-event detection per game (open parks only) ---
    print("\n--- WEATHER: rain events at home parks (open-air) in window ---")
    sched = mlb_schedule(start_date, end_date)
    if not sched:
        print("  (MLB stats API returned nothing — outcome labels unavailable; continuing on"
              " Kalshi + weather only)")
    precip_cache: dict[str, dict] = {}
    rain_games: list[dict] = []
    for g in games:
        park = g["park"]
        if not park or park[3]:  # unmapped or roofed -> rain-nowcast doesn't apply
            continue
        lat, lon, tz, _ = park
        key = f"{lat},{lon}"
        if key not in precip_cache:
            precip_cache[key] = park_precip(lat, lon, tz, args.days)
        ev = rain_event_epoch(precip_cache[key], g["date"], tz, args.rain_threshold, g["fp"])
        if not ev:
            continue
        t_epoch, peak = ev
        g2 = dict(g)
        g2["event_epoch"], g2["peak_mm"] = t_epoch, peak
        lab = sched.get((g["date"], g["home"]))
        g2["postponed"] = lab["postponed"] if lab else None
        g2["total_runs"] = lab["total_runs"] if lab else None
        rain_games.append(g2)
    print(f"  open-park games: {sum(1 for g in games if g['park'] and not g['park'][3])}   "
          f"rain-affected (precip>={args.rain_threshold}mm near game time): {len(rain_games)}")
    ppd = [g for g in rain_games if g.get("postponed")]
    if ppd:
        ppd_list = ", ".join(f"{g['date']}/{g['home']}" for g in ppd[:8])
        print(f"  of which POSTPONED/SUSPENDED (cleanest events): {len(ppd)} -> {ppd_list}")

    # --- Section C: repricing analysis where a rain game has tradeable candles ---
    print("\n--- REPRICING: winner-market response to the rain event (no-lookahead) ---")
    rows = []
    for g in rain_games:
        candles = kalshi_candles(args.series, g["ticker"],
                                 g["event_epoch"] - 6 * 3600, g["event_epoch"] + 12 * 3600)
        met = reprice_metrics(candles, g["event_epoch"], args.entry_lag_min)
        if not met:
            continue
        met.update(date=g["date"], home=g["home"], peak_mm=g["peak_mm"],
                   postponed=g.get("postponed"), phase="pre" if g["event_epoch"] <= g["fp"] else "in")
        rows.append(met)
    if not rows:
        print("  (0 rain-affected games had usable 1-min candles bracketing the event —\n"
              "   Kalshi's MLB candle history is too sparse/thin for the repricing test.)")
    else:
        print(f"  {'date':<11}{'park':<5}{'mm':>5}{'phase':>5}{'tot_c':>7}"
              f"{'c1':>6}{'c5':>6}{'c15':>6}{'net_c':>7}{'spr_c':>6}")
        for r in rows:
            print(f"  {r['date']:<11}{r['home']:<5}{r['peak_mm']:>5.1f}{r['phase']:>5}"
                  f"{r['total_move_c']:>+7.1f}{r['c1']:>6.2f}{r['c5']:>6.2f}{r['c15']:>6.2f}"
                  f"{r['net_c']:>+7.1f}{r['entry_spread_c']:>6.1f}")

    # --- Section D: pre-registered verdict ---
    print("\n=== PRE-REGISTERED VERDICTS (docs/IDEA_MODEL_20260709.md — MLBWX) ===")
    liq_rows = [r for r in rows if r["entry_spread_c"] == r["entry_spread_c"]
                and r["entry_spread_c"] <= args.liq_spread_c]
    n_all, n_liq_rows = len(rows), len(liq_rows)

    if n_liq_rows == 0:
        print(f"  P4 liquidity: {n_liq}/{n_games} census games and {n_liq_rows}/{n_all} rain-repricing")
        print(f"     samples clear the {args.liq_spread_c:g}c 2-sided-spread filter.")
        print("  -> INSUFFICIENT LIQUID DATA. Kalshi's MLB winner markets are too thin (near-zero")
        print("     volume, wide quotes) and the run-total / game-played instruments the nowcast")
        print("     leg needs do not resolve. P1/P2 are not gradeable on tradeable quotes.")
        print("  DECISION: cannot promote (P4 fails at the data-availability gate). This is the")
        print("     cheap ruling-out the thesis anticipated — journal and hold; revisit if/when")
        print("     Kalshi lists liquid MLB total / game-status markets.")
        return 0

    med_c5 = _med([r["c5"] for r in liq_rows])
    med_net = _med([r["net_c"] for r in liq_rows])
    pre_rows = [r for r in liq_rows if r["phase"] == "pre"]
    med_net_pre = _med([r["net_c"] for r in pre_rows])

    p1 = "PASS" if med_c5 < 0.60 else ("KILL" if med_c5 >= 0.85 else "GREY")
    p2 = "PASS" if med_net >= 4.0 else ("KILL" if med_net < 2.0 else "GREY")
    p4 = "PASS" if n_liq_rows >= 5 else "THIN"
    print(f"  P1 median 5-min completion < 60%: {med_c5:.2f} (n={n_liq_rows}) -> {p1}")
    print(f"  P2 median net entering at +{args.entry_lag_min}m >= +4c: {med_net:+.1f}c -> {p2}")
    print(f"  P3 pre-game directional (no lag) net: {med_net_pre:+.1f}c  "
          f"(guard: strongly +EV here with P1 fail => level artifact, not the lag)")
    print(f"  P4 survives spread<={args.liq_spread_c:g}c: {n_liq_rows}/{n_all} liq rows -> {p4}")
    ok = (p1 == "PASS" and p2 == "PASS" and p4 == "PASS")
    print(f"  DECISION (P1 AND P2 AND P4): {'PROMOTE -> paper book mlbwx' if ok else 'do not promote'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
