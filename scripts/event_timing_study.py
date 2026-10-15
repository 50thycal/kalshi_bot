"""Event-timing study — does WHEN an mmsell entry fills, relative to the real game, predict P&L?

Pre-registration, sources rule and bar: docs/MMSELL_EVENT_TIMING.md. Read-only: Postgres through
DATABASE_URL_RO, Kalshi's public REST (no auth, no orders) and two keyless public schedule APIs
used only to VALIDATE a start-time source (MLB StatsAPI, ESPN scoreboard). Stdlib + psycopg;
runs on the ops runner (open egress).

For sports `close_time` is a far-future fallback (start + 48h/72h, or +14d on tennis), so "hours
to close" cannot say whether an entry was placed before the game, during it or near its end.
This script finds the event start per market and buckets every entry against it.

  --sources   STEP 1 only: per sport family, which start-time source is accepted, its coverage
              and its agreement with the references. Prints no P&L.
  (default)   STEP 1 + the pre-registered measurement: live and paper c/contract per window
              (pre-game >24h, 6-24h, <6h, in-play), the day-clustered bootstrap, the verdict.

Usage (via the ops channel):
    {"type":"script","name":"event_timing_study","args":["--sources"],"id":"..."}
    {"type":"script","name":"event_timing_study","args":[],"id":"..."}
    {"type":"script","name":"event_timing_study","args":["--fields","KXNFLTOTAL-26AUG13INDNE-18"]}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 — no tz database: fixed EDT, correct for the whole sample window
    ET = timezone(timedelta(hours=-4))

import live_canary_patterns as lcp  # boot_diff, rate, category_of, event_day (same as live read)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
MLB_STATSAPI = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d}&hydrate=team"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}dates={d}&limit=400"
ESPN_PATHS = {"NFL": "football/nfl/scoreboard?", "MLS": "soccer/usa.1/scoreboard?",
              "NCAAF": "football/college-football/scoreboard?groups=80&"}
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAUSE_S = 0.12          # polite: ~8 requests/second against any one host
BATCH = 100             # tickers per /markets?tickers= call
SINGLE_FALLBACK_CAP = 2500

# --- pre-registered constants (docs/MMSELL_EVENT_TIMING.md §3-4) -----------------------------
#: Windows on hours from the event START to the entry (negative = before the start).
WINDOWS = (("pre>24h", None, -24.0), ("pre6-24h", -24.0, -6.0), ("pre<6h", -6.0, 0.0),
           ("in-play", 0.0, None))
#: A start source agrees with a reference when within this many minutes.
AGREE_MIN = 15
#: Share of checkable events that must agree for occurrence_datetime to be accepted.
AGREE_SHARE = 0.90
#: Checkable events needed before the agreement test counts; below it the internal test is used.
MIN_CHECKABLE = 20
#: Internal test (no reference available): occurrence present on this share of markets, no
#: early-closed market resolved before it on more than 1 - INTERNAL_ORDER of them, and the
#: median early resolution lands within INTERNAL_MAX_LAG_H of it — a day or tournament
#: placeholder would pass the ordering test and fail this one.
INTERNAL_PRESENT, INTERNAL_ORDER, INTERNAL_MAX_LAG_H = 0.90, 0.95, 6.0
#: The bar. A window SIGNALS when, on live, its c/contract differs from every other window
#: pooled by at least BAR_CENTS with the 90% interval excluding zero, it holds at least
#: MIN_WINDOW_N live markets, and paper (>= MIN_WINDOW_N entries) moves the same direction.
BAR_CENTS, MIN_WINDOW_N = 2.0, 30

FAMILY_KEYS = (("KXMLB", "MLB"), ("KXNFL", "NFL"), ("KXNCAAF", "NCAAF"),
               ("MATCH", "tennis"))
SOCCER_KEYS = ("KXWC", "KXMLS", "KXEPL", "KXLALIGA", "KXUCL", "KXUEL", "KXUECL",
               "KXBRASILEIRO", "KXLIGAMX", "KXARGPREMDIV", "KXLEAGUESCUP", "KXNWSL", "KXCLUBF",
               "KXEFL", "KXDIMAYOR", "KXECULP", "KXSERIEA", "KXBUNDESLIGA", "KXLIGUE1",
               "KXEREDIVISIE", "KXFACUP", "KXCONCACAF", "KXCOPA", "KXKLEAGUE", "KXJLEAGUE",
               "KXSAUDI", "KXSUPERLIG", "KXPRIMEIRA", "KXSCOTTISH", "KXSOCCER", "KXUSL",
               "KXLIGAPRO", "KXCHILE", "KXAPF", "KXALEAGUE", "KXEKSTRA", "KXSWISS", "KXDANISH",
               "KXBELGIAN", "KXAUSTRIAN", "KXGREEK", "KXSUPERCOPA", "KXLIBERTADORES",
               "KXSUDAMERICANA", "KXFIFA")
#: Tennis series that carry "MATCH" but are cricket.
CRICKET_KEYS = ("KXT20MATCH", "KXODIMATCH", "KXTESTMATCH", "KXIPL", "KXCRICKET")
#: Kalshi code -> the reference API's code, where they differ.
ALIASES = {"OAK": "ATH", "ARI": "AZ", "WAS": "WSH", "CHW": "CWS", "SDP": "SD", "SFG": "SF",
           "TBR": "TB", "KCR": "KC", "JAC": "JAX", "LA": "LAR", "WSN": "WSH"}

LIVE_SQL = """
WITH ord AS (
  SELECT market_ticker, min(strategy) AS strat, count(DISTINCT strategy) AS ns,
         min(created_at) AS first_order
  FROM live_orders WHERE action = 'buy' AND strategy IS NOT NULL GROUP BY market_ticker),
pos AS (
  SELECT DISTINCT ON (market_ticker) market_ticker, quantity, realized_pnl FROM positions
  WHERE market_ticker IN (SELECT market_ticker FROM ord) ORDER BY market_ticker, captured_at DESC),
f AS (
  SELECT o.market_ticker, sum(fl.quantity) AS ct, min(fl.filled_at) AS first_fill
  FROM fills fl JOIN live_orders o ON o.kalshi_order_id = fl.kalshi_order_id AND o.action = 'buy'
  GROUP BY o.market_ticker)
SELECT ord.market_ticker, ord.strat, ord.first_order, f.first_fill, pos.realized_pnl, f.ct,
       m.close_time
FROM ord JOIN pos USING (market_ticker) JOIN f USING (market_ticker)
LEFT JOIN mmsell_settlement_meta m USING (market_ticker)
WHERE pos.quantity = 0 AND pos.realized_pnl IS NOT NULL AND f.ct > 0
  AND ord.ns = 1 AND ord.strat ILIKE '%%mmsell%%'
ORDER BY ord.first_order
"""
PAPER_SQL = """
SELECT p.market_ticker, p.created_at, p.pnl, p.quantity, m.close_time
FROM paper_trades p LEFT JOIN mmsell_settlement_meta m USING (market_ticker)
WHERE p.strategy = 'mmsell10' AND p.status <> 'open' AND p.closed_at IS NOT NULL
  AND p.pnl IS NOT NULL AND p.quantity > 0
ORDER BY p.created_at
"""


# --- pure helpers (unit-tested) -------------------------------------------------------------

def family_of(series: str) -> str:
    s = (series or "").upper()
    if s.startswith(CRICKET_KEYS):
        return "other-sports"
    if s.startswith(SOCCER_KEYS):
        return "soccer"
    for key, fam in FAMILY_KEYS:
        if (s.startswith(key) if key.startswith("KX") else key in s):
            return fam
    return "other-sports"


_HHMM = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{4})(?=[A-Z0-9])")
_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")


def ticker_start(ticker: str) -> datetime | None:
    """The start encoded in the event ticker (`-26SEP021940MILCHC` = 19:40 ET), as UTC.
    None when the ticker carries only a date, or the placeholder time 0000."""
    m = _HHMM.search(ticker or "")
    if not m or m.group(2) not in lcp.MONTHS or m.group(4) == "0000":
        return None
    hh, mm = int(m.group(4)[:2]), int(m.group(4)[2:])
    if hh > 23 or mm > 59:
        return None
    local = datetime(2000 + int(m.group(1)), lcp.MONTHS[m.group(2)], int(m.group(3)), hh, mm,
                     tzinfo=ET)
    return local.astimezone(timezone.utc)


def ticker_date(ticker: str) -> str:
    m = _DATE.search(ticker or "")
    if m and m.group(2) in lcp.MONTHS:
        return f"20{m.group(1)}-{lcp.MONTHS[m.group(2)]:02d}-{m.group(3)}"
    return ""


def team_code(event_ticker: str) -> str:
    """`KXMLBKS-26AUG081915CLECWS` -> `CLECWS`: the letters after the date/time block."""
    m = re.search(r"-\d{2}[A-Z]{3}\d{2}(?:\d{4})?([A-Z0-9]+)$", event_ticker or "")
    return m.group(1) if m else ""


def code_matches(code: str, a: str, b: str) -> bool:
    """True when a Kalshi team code is the two reference abbreviations concatenated."""
    def norm(x: str) -> str:
        return ALIASES.get(x.upper(), x.upper())
    a, b = norm(a), norm(b)
    for i in range(2, len(code) - 1):
        x, y = norm(code[:i]), norm(code[i:])
        if {x, y} == {a, b}:
            return True
    return False


def parse_ts(text) -> datetime | None:
    if isinstance(text, datetime):
        return text if text.tzinfo else text.replace(tzinfo=timezone.utc)
    text = (text or "").strip() if isinstance(text, str) else ""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def window_of(hours_from_start: float) -> str:
    for name, lo, hi in WINDOWS:
        if (lo is None or hours_from_start >= lo) and (hi is None or hours_from_start < hi):
            return name
    return WINDOWS[-1][0]


def accept_occurrence(checks: list[float], present: int, total: int,
                      order_ok: int, order_n: int, lags_h: list[float]) -> tuple[bool, str]:
    """The pre-registered rule for trusting Kalshi's `occurrence_datetime` as the event start
    in one sport family. `checks` are |occurrence - reference| in minutes on checkable events."""
    if len(checks) >= MIN_CHECKABLE:
        share = sum(1 for c in checks if c <= AGREE_MIN) / len(checks)
        return share >= AGREE_SHARE, f"reference: {share:.0%} of {len(checks)} within {AGREE_MIN}m"
    lag = median(lags_h) if lags_h else None
    detail = (f"{len(checks)} checkable; present {present}/{total}; resolved after it "
              f"{order_ok}/{order_n}; median lag to early resolution "
              + (f"{lag:.1f}h" if lag is not None else "-"))
    if total and present / total >= INTERNAL_PRESENT and order_n \
            and order_ok / order_n >= INTERNAL_ORDER and lag is not None \
            and lag <= INTERNAL_MAX_LAG_H:
        return True, "internal: " + detail
    return False, "insufficient: " + detail


def verdict(windows_live: dict, windows_paper: dict, n_boot: int = 1500) -> tuple[str, list[str]]:
    """Apply the bar to every window. Returns (SIGNAL | NO SIGNAL, per-window lines)."""
    lines, hit = [], False
    for name, *_ in WINDOWS:
        w = windows_live.get(name, [])
        rest = [r for k, rs in windows_live.items() if k != name for r in rs]
        pw = windows_paper.get(name, [])
        prest = [r for k, rs in windows_paper.items() if k != name for r in rs]
        if len(w) < MIN_WINDOW_N or not rest:
            lines.append(f"  {name:9s} live n={len(w)} < {MIN_WINDOW_N}: not testable")
            continue
        d = lcp.boot_diff(w, rest, n=n_boot)
        pd = (lcp.rate(pw) - lcp.rate(prest)) if len(pw) >= MIN_WINDOW_N and prest else None
        live_ok = abs(d[0]) >= BAR_CENTS and (d[1] > 0 or d[2] < 0)
        paper_ok = pd is not None and pd == pd and (pd > 0) == (d[0] > 0) and pd != 0
        ok = live_ok and paper_ok
        hit |= ok
        pd_s = f"{pd:+.2f}c" if pd is not None else f"n={len(pw)} < {MIN_WINDOW_N}"
        lines.append(f"  {name:9s} live minus rest {lcp.fmt(d)} | paper minus rest {pd_s}"
                     f"  -> {'SIGNAL' if ok else 'no'}"
                     + ("" if ok or not live_ok else " (live passes, paper does not confirm)"))
    return ("SIGNAL" if hit else "NO SIGNAL"), lines


# --- I/O --------------------------------------------------------------------------------------

def _get(url: str):
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310 (fixed hosts)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return None
        finally:
            time.sleep(PAUSE_S)
    return None


def fetch_markets(tickers: list[str]) -> dict[str, dict]:
    """Market objects by ticker: batched live endpoint, batched archive, then one at a time."""
    out: dict[str, dict] = {}
    todo = sorted(set(tickers))
    for base in (f"{KALSHI}/markets", f"{KALSHI}/historical/markets"):
        missing = [t for t in todo if t not in out]
        for i in range(0, len(missing), BATCH):
            chunk = missing[i:i + BATCH]
            q = urllib.parse.urlencode({"tickers": ",".join(chunk), "limit": len(chunk)})
            page = _get(f"{base}?{q}") or {}
            for m in page.get("markets") or []:
                if m.get("ticker") in chunk:
                    out[m["ticker"]] = m
    missing = [t for t in todo if t not in out][:SINGLE_FALLBACK_CAP]
    for t in missing:
        for base in (f"{KALSHI}/markets", f"{KALSHI}/historical/markets"):
            data = _get(f"{base}/{urllib.parse.quote(t, safe='')}") or {}
            if data.get("market"):
                out[t] = data["market"]
                break
    return out


def fetch_series_category(series: list[str]) -> dict[str, str]:
    out = {}
    for s in sorted(set(series)):
        data = _get(f"{KALSHI}/series/{s}") or {}
        out[s] = ((data.get("series") or {}).get("category") or "")
    return out


def reference_starts(family: str, dates: set[str]) -> dict[str, list[tuple[datetime, str, str]]]:
    """date -> [(start_utc, team_a, team_b)] from a keyless public schedule, MLB / NFL / NCAAF /
    MLS only. Used to VALIDATE a start source, never as the source of a P&L row."""
    out: dict[str, list] = defaultdict(list)
    for d in sorted(dates):
        if family == "MLB":
            data = _get(MLB_STATSAPI.format(d=d)) or {}
            for day in data.get("dates") or []:
                for g in day.get("games") or []:
                    st = parse_ts(g.get("gameDate"))
                    t = g.get("teams") or {}
                    a = ((t.get("away") or {}).get("team") or {}).get("abbreviation") or ""
                    b = ((t.get("home") or {}).get("team") or {}).get("abbreviation") or ""
                    if st and a and b:
                        out[d].append((st, a, b))
        elif family in ESPN_PATHS:
            data = _get(ESPN.format(path=ESPN_PATHS[family], d=d.replace("-", ""))) or {}
            for ev in data.get("events") or []:
                st = parse_ts(ev.get("date"))
                comp = ((ev.get("competitions") or [{}])[0].get("competitors")) or []
                ab = [((c.get("team") or {}).get("abbreviation") or "") for c in comp]
                if st and len(ab) == 2 and all(ab):
                    out[d].append((st, ab[0], ab[1]))
    return out


def load_rows() -> tuple[list[dict], list[dict]]:
    url = (os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url:
        raise SystemExit("DATABASE_URL_RO (or DATABASE_URL) is not set.")
    import psycopg

    opts = ("-c default_transaction_read_only=on -c statement_timeout=180000 "
            "-c idle_in_transaction_session_timeout=180000")
    with psycopg.connect(url, options=opts, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(LIVE_SQL)
            live = [{"ticker": t, "strat": s, "order": parse_ts(o), "entry": parse_ts(f),
                     "pnl": float(p), "qty": float(q), "meta_close": parse_ts(c)}
                    for t, s, o, f, p, q, c in cur.fetchall()]
            cur.execute(PAPER_SQL)
            paper = [{"ticker": t, "order": parse_ts(e), "entry": parse_ts(e), "pnl": float(p),
                      "qty": float(q), "meta_close": parse_ts(c)}
                     for t, e, p, q, c in cur.fetchall()]
    return live, paper


# --- the study ----------------------------------------------------------------------------------

def series_of(ticker: str) -> str:
    return ticker.split("-")[0].upper()


def resolve_sources(markets: dict[str, dict], tickers: list[str], refs_by_family: dict) -> dict:
    """Per family: which start source is accepted, and the evidence. Prints no P&L."""
    fam_tickers: dict[str, list[str]] = defaultdict(list)
    for t in tickers:
        fam_tickers[family_of(series_of(t))].append(t)
    report = {}
    for fam, ts in sorted(fam_tickers.items()):
        checks_hhmm, checks_ext, durations, lags, present = [], [], [], [], 0
        order_ok = order_n = 0
        ext_matched = ext_tried = 0
        seen_events = set()
        refs = refs_by_family.get(fam, {})
        for t in ts:
            m = markets.get(t) or {}
            occ = parse_ts(m.get("occurrence_datetime"))
            if occ:
                present += 1
                close = parse_ts(m.get("close_time"))
                exp = parse_ts(m.get("expiration_time"))
                if close and exp and close < exp - timedelta(hours=1):   # closed early
                    order_n += 1
                    order_ok += close >= occ
                    lags.append((close - occ).total_seconds() / 3600)
                eexp = parse_ts(m.get("expected_expiration_time"))
                if eexp:
                    durations.append((eexp - occ).total_seconds() / 3600)
            ev = m.get("event_ticker") or t.rsplit("-", 1)[0]
            if ev in seen_events or not occ:
                continue
            seen_events.add(ev)
            ts_start = ticker_start(ev)
            if ts_start:
                checks_hhmm.append(abs((occ - ts_start).total_seconds()) / 60)
            if refs:
                ext_tried += 1
                code = team_code(ev)
                cands = [st for st, a, b in refs.get(ticker_date(ev), [])
                         if code_matches(code, a, b)]
                if cands:
                    ext_matched += 1
                    checks_ext.append(min(abs((occ - st).total_seconds()) / 60 for st in cands))
        checks = checks_hhmm + checks_ext
        ok, why = accept_occurrence(checks, present, len(ts), order_ok, order_n, lags)
        report[fam] = {
            "markets": len(ts), "events": len(seen_events), "occ_present": present,
            "hhmm_checks": checks_hhmm, "ext_checks": checks_ext,
            "ext_matched": ext_matched, "ext_tried": ext_tried,
            "duration_h": durations, "accept_occurrence": ok, "why": why,
        }
    return report


def start_of(ticker: str, markets: dict, sources: dict) -> tuple[datetime | None, str]:
    m = markets.get(ticker) or {}
    fam = family_of(series_of(ticker))
    if (sources.get(fam) or {}).get("accept_occurrence"):
        occ = parse_ts(m.get("occurrence_datetime"))
        if occ:
            return occ, "occurrence"
    ev = m.get("event_ticker") or ticker.rsplit("-", 1)[0]
    st = ticker_start(ev)
    return (st, "ticker-hhmm") if st else (None, "none")


def _med(xs):
    return f"{median(xs):+.1f}" if xs else "-"


def print_sources(sources: dict, coverage: dict) -> None:
    print("\nSTEP 1 — START-TIME SOURCES (per sport family; no P&L)")
    print(f"  rule: occurrence_datetime accepted when >= {AGREE_SHARE:.0%} of >= {MIN_CHECKABLE} "
          f"reference-checkable events agree within {AGREE_MIN} min (ticker HHMM ET, MLB StatsAPI, "
          "ESPN); else the internal test; else the ticker HHMM; else uncovered")
    print(f"  {'family':12s} {'mkts':>5s} {'events':>6s} {'occ%':>5s} {'hhmm agree':>14s} "
          f"{'ext agree':>16s} {'occ->exp h':>10s}  accepted  coverage")
    for fam, r in sorted(sources.items(), key=lambda kv: -kv[1]["markets"]):
        hh = r["hhmm_checks"]
        ex = r["ext_checks"]
        hh_s = (f"{sum(c <= AGREE_MIN for c in hh)}/{len(hh)} med{median(hh):.0f}m"
                if hh else "-")
        ex_s = (f"{sum(c <= AGREE_MIN for c in ex)}/{len(ex)} ({r['ext_matched']}/"
                f"{r['ext_tried']}m)" if r["ext_tried"] else "-")
        cov = coverage.get(fam, Counter())
        tot = sum(cov.values()) or 1
        cov_s = ", ".join(f"{k} {v / tot:.0%}" for k, v in cov.most_common())
        print(f"  {fam:12s} {r['markets']:5d} {r['events']:6d} "
              f"{r['occ_present'] / max(r['markets'], 1):5.0%} {hh_s:>14s} {ex_s:>16s} "
              f"{_med(r['duration_h']):>10s}  {'occurrence' if r['accept_occurrence'] else 'NO':10s}"
              f" {cov_s}")
        print(f"  {'':12s} {r['why']}")


def print_fields(tickers: list[str]) -> None:
    keys = ("ticker", "event_ticker", "status", "open_time", "occurrence_datetime",
            "expected_expiration_time", "close_time", "expiration_time", "latest_expiration_time",
            "settlement_ts", "can_close_early", "early_close_condition")
    mk = fetch_markets(tickers)
    for t in tickers:
        m = mk.get(t)
        print(f"\n=== {t} ===")
        if not m:
            print("  not found (live or historical)")
            continue
        for k in keys:
            print(f"  {k:26s} {m.get(k)}")
        ev = _get(f"{KALSHI}/events/{m.get('event_ticker')}") or {}
        e = ev.get("event") or {}
        print(f"  event.sub_title            {e.get('sub_title')}")
        print(f"  event.strike_date          {e.get('strike_date')}")
        print(f"  event.product_metadata     {json.dumps(e.get('product_metadata'))[:400]}")


def tag_rows(rows: list[dict], markets: dict, sources: dict) -> list[dict]:
    out = []
    for r in rows:
        st, src = start_of(r["ticker"], markets, sources)
        m = markets.get(r["ticker"]) or {}
        eexp = parse_ts(m.get("expected_expiration_time"))
        day = lcp.event_day(r["ticker"]) or (r["entry"].date().isoformat() if r["entry"] else "")
        rr = {**r, "start": st, "src": src, "day": day, "fam": family_of(series_of(r["ticker"])),
              "cents": 100.0 * r["pnl"] / r["qty"], "exp_end": eexp}
        if st and r["entry"]:
            rr["h"] = (r["entry"] - st).total_seconds() / 3600
            rr["win"] = window_of(rr["h"])
            rr["h_order"] = (r["order"] - st).total_seconds() / 3600 if r.get("order") else None
        out.append(rr)
    return out


def by_window(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list] = {name: [] for name, *_ in WINDOWS}
    for r in rows:
        if "win" in r:
            out[r["win"]].append(r)
    return out


def print_windows(label: str, wins: dict[str, list[dict]], n_boot: int) -> None:
    allr = [r for rs in wins.values() for r in rs]
    print(f"  {label}: n={len(allr)} all {lcp.fmt(lcp.boot_diff(allr, n=n_boot)) if allr else '-'}")
    for name, *_ in WINDOWS:
        rs = wins[name]
        share = len(rs) / len(allr) if allr else 0
        body = lcp.fmt(lcp.boot_diff(rs, n=n_boot)) if rs else "-"
        print(f"    {name:9s} n={len(rs):5d} ({share:4.0%})  {body}"
              f"  ${sum(r['pnl'] for r in rs):+.2f}")


def study(live: list[dict], paper: list[dict], markets: dict, sources: dict, n_boot: int) -> None:
    L = tag_rows(live, markets, sources)
    live_days = {r["day"] for r in L}
    P = [r for r in tag_rows(paper, markets, sources) if r["day"] in live_days]
    print("\nSTEP 2 — SAMPLE")
    for name, rows in (("live", L), ("paper", P)):
        with_start = [r for r in rows if "win" in r]
        print(f"  {name:5s} sports markets {len(rows)}, with a start {len(with_start)} "
              f"({len(with_start) / max(len(rows), 1):.0%}), event days {len({r['day'] for r in rows})}")

    print("\nSTEP 3 — c/contract by entry time relative to the event START "
          "(live = first fill, paper = entry)")
    lw, pw = by_window(L), by_window(P)
    print_windows("LIVE ", lw, n_boot)
    print_windows("PAPER", pw, n_boot)

    v, lines = verdict(lw, pw, n_boot)
    print(f"\nPRE-REGISTERED BAR (>= {BAR_CENTS:.0f}c vs the rest, 90% interval excludes 0, live "
          f"n >= {MIN_WINDOW_N}, paper same direction):")
    print("\n".join(lines))
    print(f"  VERDICT: {v}")

    print("\nDESCRIPTIVE (not gated)")
    ls = [r for r in L if "win" in r]
    inplay = [r for r in ls if r["win"] == "in-play"]
    rested = [r for r in inplay if r.get("h_order") is not None and r["h_order"] < 0]
    print(f"  live in-play share of fills: {len(inplay)}/{len(ls)} = "
          f"{len(inplay) / max(len(ls), 1):.0%}; of those, order placed BEFORE the start and "
          f"filled in-play (rested into the game): {len(rested)}")
    if rested:
        print(f"    rested-into-game {lcp.fmt(lcp.boot_diff(rested, n=n_boot))}")
    late = [r for r in inplay if r["exp_end"] and r["entry"] > r["exp_end"]]
    print(f"  live fills after Kalshi's expected expiration (scheduled end): {len(late)}")
    pl = [r for r in P if "win" in r]
    pin = [r for r in pl if r["win"] == "in-play"]
    print(f"  paper in-play share of entries: {len(pin)}/{len(pl)} = "
          f"{len(pin) / max(len(pl), 1):.0%}")
    print("  live minus paper, per window (the fill gap):")
    for name, *_ in WINDOWS:
        a, b = lw[name], pw[name]
        if a and b:
            print(f"    {name:9s} {lcp.rate(a) - lcp.rate(b):+.2f}c  (live {len(a)}, paper {len(b)})")
    print("  per family, live c/ct by window (n):")
    for fam in sorted({r["fam"] for r in ls}):
        cells = []
        for name, *_ in WINDOWS:
            rs = [r for r in ls if r["fam"] == fam and r["win"] == name]
            cells.append(f"{name} {lcp.rate(rs):+.1f}({len(rs)})" if rs else f"{name} -")
        print(f"    {fam:12s} " + "  ".join(cells))
    print("  the live 'fill 12-24h before close' cell, located against the real start:")
    cell = [r for r in ls if r["meta_close"] and r["entry"]
            and 12 <= (r["meta_close"] - r["entry"]).total_seconds() / 3600 < 24]
    loc = Counter(r["win"] for r in cell)
    print(f"    n={len(cell)} {lcp.fmt(lcp.boot_diff(cell, n=n_boot)) if cell else ''}  "
          + ", ".join(f"{k} {v}" for k, v in loc.most_common()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sources", action="store_true", help="STEP 1 only; prints no P&L")
    ap.add_argument("--fields", nargs="+", help="dump the raw timing fields of these markets")
    ap.add_argument("--boot", type=int, default=1500)
    args = ap.parse_args(argv)
    if args.fields:
        print_fields(args.fields)
        return 0

    live, paper = load_rows()
    cats = fetch_series_category([series_of(r["ticker"]) for r in live + paper])

    def is_sports(t: str) -> bool:
        s = series_of(t)
        return cats.get(s) == "Sports" and lcp.category_of(s) != "mentions"

    live = [r for r in live if is_sports(r["ticker"])]
    paper = [r for r in paper if is_sports(r["ticker"])]
    live_days = {lcp.event_day(r["ticker"]) for r in live}
    paper = [r for r in paper if lcp.event_day(r["ticker"]) in live_days]
    print(f"# event_timing_study  live sports markets {len(live)}, paper mmsell10 sports entries "
          f"on the same event days {len(paper)}, sports series {sum(v == 'Sports' for v in cats.values())}")

    tickers = sorted({r["ticker"] for r in live + paper})
    markets = fetch_markets(tickers)
    print(f"# Kalshi market objects found {len(markets)}/{len(tickers)}")

    refs_by_family = {}
    for fam in ("MLB", *ESPN_PATHS):
        if fam == "MLS":
            dates = {ticker_date(t) for t in tickers if series_of(t).startswith("KXMLS")}
            key = "soccer"
        else:
            dates = {ticker_date(t) for t in tickers if family_of(series_of(t)) == fam}
            key = fam
        dates.discard("")
        if dates:
            got = reference_starts(fam, dates)
            refs_by_family.setdefault(key, {})
            for d, games in got.items():
                refs_by_family[key].setdefault(d, []).extend(games)
    sources = resolve_sources(markets, tickers, refs_by_family)
    coverage: dict[str, Counter] = defaultdict(Counter)
    for t in tickers:
        coverage[family_of(series_of(t))][start_of(t, markets, sources)[1]] += 1
    print_sources(sources, coverage)
    if args.sources:
        return 0
    study(live, paper, markets, sources, args.boot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
