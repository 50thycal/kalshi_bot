"""FREEZE probe — do Kalshi commodity-hub markets keep trading at a discount to certainty AFTER
their settlement source has gone dark (the underlying exchange is closed and even Pyth has no
live constituent to print), so the outcome is mechanically decided?

Pre-registered thesis in docs/FREEZE_THESIS.md (promoted from docs/IDEA_MODEL_20260711_run2.md,
candidate F1). READ-ONLY study on public Kalshi data only — no DB, no collector, no auth:

  - /events?status=settled&with_nested_markets=true -> commodity universe (category/title/result)
  - /markets/trades?ticker=... -> the REAL trade tape (every prediction measured on real trades,
    never quoted asks — fillability is load-bearing, same as the PINNED probe this reuses)
  - /markets?series_ticker=... , /markets/{t}/orderbook -> structure enumeration + depth snapshot

The load-bearing correctness decision (documented so a re-run can attack it): the hub settles on
Pyth Network feeds, whose whole selling point is CONTINUOUS 24/7 pricing — for metals/energy the
underlying spot trades ~24/5 OTC, so Pyth keeps printing and the exchange-closure "freeze" is
NOT real. Per the thesis's own rule ("when in doubt classify NOT frozen"), only AGRICULTURAL
GRAINS and SOFTS get freeze windows here — they have no meaningful weekend/overnight OTC market,
so during the CBOT/ICE close Pyth must carry a stale last print and the outcome IS locked.
Metals/energy are treated as Pyth-continuous (never frozen) and contribute only to the SETTLEPIN
control + the structure enumeration. The P5 red-flag guard (pinned side LOSES => the freeze or
the settlement-timing assumption is wrong) is the empirical backstop, exactly as in PINNED v4.

Three pin cells (the thesis's F1/F2/F3):
  FREEZE   : close_time falls inside a source-dark window (grains/softs only). pin = the start of
             that dark window (the last source print). Post-pin trades scored with side=result,
             valid because the source is frozen so `result` is point-in-time-known (same logic
             the PINNED R-family uses post-release). This is the load-bearing cell (P3).
  SETTLEPIN: close_time is after the official daily settlement print on the close date (all
             commodities). pin = settle publication time. The PINNED-style control cell — expected
             efficient; if it clears but FREEZE doesn't, it's just PINNED again -> do not promote.
  TOUCHPIN : one-touch "in" markets post-touch. NOT reliably classifiable from the public API in
             v1 (no rules-level touch flag on the tape) -> reported as N/A, secondary only.

Pre-registered predictions (graded verbatim vs docs/FREEZE_THESIS.md; do not re-scope):
  P1 pooled post-pin EV >= +3c/ct on >= 80 real trades (KILL < 1.5c).
  P2 post-pin EV exceeds a PRE-pin favorite-buy control (buy the >=85c side) by >= 2c/ct.
  P3 the FREEZE cell alone nets >= 3c/ct at >= 25 post-pin trades.
  P4 post-pin notional at >= 2c discount averages >= $150/week.
  P5 ZERO wrong pins (any pinned-side loss is a red flag blocking promotion).
  Decision: paper book only if P1 & P2 & P3 & P4 & P5.

EV convention: cents/contract net of the entry-leg taker fee ceil(0.07*P*(1-P)*100) (hold to
settlement -> no exit leg). Read-only public API, stdlib only. Usage (via the ops channel):
    {"type":"script","name":"kalshi_freeze_study","args":["--max-event-pages","80"],"id":"freeze-0"}
"""

from __future__ import annotations

import argparse
import math
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
ET = ZoneInfo("America/New_York")

# --- commodity classification ------------------------------------------------------
# Map title/series keywords -> commodity key. Order matters (first match wins). Each rule
# matches the human name at a word boundary OR the uppercase ticker root (KXWHEAT etc. have no
# internal \b, so the bare-word rule would miss a ticker-only market — hence the KX... alt).
COMMODITY_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(soybean|soy)\b|KX(SOY|SOYBEAN)", re.I), "soybeans", "grain"),
    (re.compile(r"\bcorn\b|KXCORN", re.I), "corn", "grain"),
    (re.compile(r"\bwheat\b|KXWHEAT", re.I), "wheat", "grain"),
    (re.compile(r"\bcoffee\b|KXCOFFEE", re.I), "coffee", "soft"),
    (re.compile(r"\bsugar\b|KXSUGAR", re.I), "sugar", "soft"),
    (re.compile(r"\bcocoa\b|KXCOCOA", re.I), "cocoa", "soft"),
    (re.compile(r"\bcotton\b|KXCOTTON", re.I), "cotton", "soft"),
    (re.compile(r"\b(gold|xau)\b|KXGOLD", re.I), "gold", "metal"),
    (re.compile(r"\b(silver|xag)\b|KXSILVER", re.I), "silver", "metal"),
    (re.compile(r"\bcopper\b|KXCOPPER", re.I), "copper", "metal"),
    (re.compile(r"\b(platinum|palladium)\b|KX(PLATINUM|PALLADIUM)", re.I), "pgm", "metal"),
    (re.compile(r"\bnickel\b|KXNICKEL", re.I), "nickel", "metal"),
    (re.compile(r"\blithium\b|KXLITHIUM", re.I), "lithium", "metal"),
    (re.compile(r"\b(wti|crude|brent|oil)\b|KX(WTI|CRUDE|BRENT|OIL)", re.I), "oil", "energy"),
    (re.compile(r"\b(natural gas|natgas)\b|\bng\b|KX(NATGAS|NG)\b", re.I), "natgas", "energy"),
    (re.compile(r"\b(diesel|heating oil|gasoline|rbob)\b|KX(DIESEL|GASOLINE|RBOB|HEATOIL)",
                re.I), "refined", "energy"),
]
COMMODITY_CATEGORY = re.compile(r"commodit|metal|energy|agricultur|grain", re.I)
# Only these groups get a genuine source-dark freeze window (see module docstring).
FREEZE_GROUPS = {"grain", "soft"}

# Official daily settlement print clock (ET) per group -> SETTLEPIN control anchor.
SETTLE_ET = {"grain": (14, 20), "soft": (14, 0), "metal": (13, 30),
             "energy": (14, 30)}


def fee_cents(price_cents: float) -> float:
    p = max(0.0, min(1.0, price_cents / 100.0))
    return math.ceil(0.07 * p * (1 - p) * 100)


def _ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def commodity_of(text: str) -> tuple[str, str] | None:
    for rx, name, group in COMMODITY_RULES:
        if rx.search(text or ""):
            return name, group
    return None


# --- source-dark exchange calendar (ET) --------------------------------------------
# is_open() is intentionally CONSERVATIVE (wide open windows) so we only ever call a source
# "dark" when it very likely is -> fewer false pins. Grains (CBOT): day 09:30-14:20 + overnight
# 20:00-08:45, Sun-Fri. Softs (ICE): generous union day session 03:00-14:30. Weekends dark.

def is_open(group: str, dt: datetime) -> bool:
    wd = dt.weekday()  # Mon=0 .. Sun=6
    hm = dt.hour * 60 + dt.minute
    if group == "grain":
        # Saturday fully closed; Sunday reopens 20:00 ET; Friday closes 14:20 ET.
        if wd == 5:
            return False
        if wd == 6:
            return hm >= 20 * 60
        if wd == 4:  # Friday: overnight until 08:45, day 09:30-14:20, then dark into weekend
            return hm < 8 * 60 + 45 or (9 * 60 + 30 <= hm < 14 * 60 + 20)
        # Mon-Thu: overnight (<08:45 and >=20:00) + day 09:30-14:20
        return (hm < 8 * 60 + 45 or hm >= 20 * 60
                or (9 * 60 + 30 <= hm < 14 * 60 + 20))
    if group == "soft":
        if wd >= 5:  # weekend dark
            return False
        return 3 * 60 <= hm < 14 * 60 + 30
    # metals/energy: treated as Pyth-continuous -> never "dark" for the FREEZE cell.
    return True


def dark_window_start(group: str, ts: float, max_back_h: int = 80) -> float | None:
    """If ts is inside a source-dark window, return the window's start ts (the last source
    print before it); else None. Steps back in 15-min increments to the last open instant."""
    if group not in FREEZE_GROUPS:
        return None
    dt = datetime.fromtimestamp(ts, tz=ET)
    if is_open(group, dt):
        return None
    step = timedelta(minutes=15)
    probe = dt
    for _ in range(max_back_h * 4):
        prev = probe - step
        if is_open(group, prev):
            return probe.timestamp()  # first dark instant after the last open period
        probe = prev
    return None  # dark for longer than max_back -> treat as unknown (skip)


def settle_pin_ts(group: str, close_ts: float) -> float:
    """SETTLEPIN control: the official daily settle print time (ET) on the close date."""
    hhmm = SETTLE_ET.get(group)
    if not hhmm or not close_ts:
        return 0.0
    d = datetime.fromtimestamp(close_ts, tz=ET)
    pin = d.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)
    return pin.timestamp()


# --- data fetch (reused shape from kalshi_pinned_study) -----------------------------


def settled_events(max_pages: int) -> list[dict]:
    out, cursor = [], ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status=settled&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        out.extend(evs)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return out


def trades(ticker: str, max_pages: int = 5) -> list[dict]:
    out, cursor = [], ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/markets/trades?ticker={ticker}&limit=1000&cursor={cursor}")
        rows = (page or {}).get("trades") or []
        out.extend(rows)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not rows:
            break
        time.sleep(0.05)
    return out


def open_markets(max_pages: int = 30) -> list[dict]:
    out, cursor = [], ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/markets?status=open&limit=200&cursor={cursor}")
        rows = (page or {}).get("markets") or []
        out.extend(rows)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not rows:
            break
        time.sleep(0.05)
    return out


def orderbook(ticker: str) -> dict | None:
    return xl._get(f"{KALSHI}/markets/{ticker}/orderbook?depth=10")


# --- scoring (reused shape from kalshi_pinned_study.score_trades) --------------------


def score_trades(rows: list[dict], result: str, pin_ts: float, cell: str,
                 commodity: str, acc: dict, week_notional: dict) -> None:
    """result is the locked/known pinned side (yes/no). Post-pin: buy `result` side at the
    traded price. Pre-pin: a favorite-buy control (buy the >=85c side)."""
    for t in rows:
        ts = _ts(t.get("created_time"))
        p = 0.0  # trade YES price in CENTS; API sends yes_price_dollars (str) or yes_price
        if t.get("yes_price_dollars") is not None:
            p = xl._num(t.get("yes_price_dollars")) * 100.0
        elif t.get("yes_price") is not None:
            p = xl._num(t.get("yes_price"))
        cnt = int(xl._num(t.get("count_fp")) or xl._num(t.get("count")) or 0) or 1
        if not ts or p <= 0 or p >= 100 or result not in ("yes", "no"):
            continue
        if ts >= pin_ts:
            side = result
            cost = p if side == "yes" else 100 - p
            ev = (100 - cost if result == side else -cost) - fee_cents(cost)
            acc["post"].append((ev, cnt, cell, commodity))
            if result != side:  # impossible by construction, but keep the guard shape
                acc["red_flags"].append((t.get("ticker"), cell, side, result, cost))
            # discount = 100 - cost (how far the winning side traded below certainty)
            if cell == "FREEZE" and (100 - cost) >= 2:
                wk = int(ts // (7 * 86400))
                week_notional[wk] = week_notional.get(wk, 0.0) + cost * cnt / 100.0
        else:
            fav = "yes" if p >= 85 else ("no" if p <= 15 else None)
            if fav:
                cost = p if fav == "yes" else 100 - p
                ev = (100 - cost if result == fav else -cost) - fee_cents(cost)
                acc["pre"].append((ev, cnt, cell, commodity))


def wavg(rows: list[tuple]) -> tuple[float, int]:
    n = sum(r[1] for r in rows)
    return (sum(r[0] * r[1] for r in rows) / n if n else 0.0), n


# --- structure enumeration (unblocks the COMPIN / OPTRV holds) ----------------------

PRICE_TYPE_RULES = [
    (re.compile(r"\b(twap|vwap|average|averag)\b", re.I), "average"),
    (re.compile(r"\bfix(ing)?\b", re.I), "fix"),
    (re.compile(r"\b(settle|settlement)\b", re.I), "settle"),
    (re.compile(r"\bclos(e|ing)\b", re.I), "close"),
    (re.compile(r"\bhigh\b", re.I), "high"),
    (re.compile(r"\blow\b", re.I), "low"),
    (re.compile(r"\b(open|opening)\b", re.I), "open"),
    (re.compile(r"\bbetween\b", re.I), "range"),
]


def price_type_of(text: str) -> str:
    for rx, name in PRICE_TYPE_RULES:
        if rx.search(text or ""):
            return name
    return "threshold"


def enumerate_structure(opens: list[dict], sample_depth: int) -> None:
    by_com: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "series": set(), "ptypes": defaultdict(int), "depth": []})
    for m in opens:
        text = " ".join(filter(None, [m.get("title"), m.get("subtitle"),
                                       m.get("ticker"), m.get("series_ticker")]))
        cm = commodity_of(text)
        if not cm:
            continue
        name, _group = cm
        rec = by_com[name]
        rec["n"] += 1
        if m.get("series_ticker"):
            rec["series"].add(m["series_ticker"])
        rec["ptypes"][price_type_of(text)] += 1
    # depth snapshot: a few open markets per commodity
    sampled = 0
    for m in opens:
        if sampled >= sample_depth:
            break
        cm = commodity_of(" ".join(filter(None, [m.get("title"), m.get("subtitle"),
                                                  m.get("ticker"), m.get("series_ticker")])))
        if not cm:
            continue
        ob = orderbook(m.get("ticker") or "")
        if not ob:
            continue
        book = (ob or {}).get("orderbook") or {}
        yes = book.get("yes") or []
        no = book.get("no") or []
        best_yes = max((int(xl._num(l[0])) for l in yes), default=0)
        best_no = max((int(xl._num(l[0])) for l in no), default=0)
        spread = (100 - best_no) - best_yes if best_yes and best_no else None
        depth2 = sum(int(xl._num(l[1])) for l in yes
                     if best_yes and int(xl._num(l[0])) >= best_yes - 2)
        by_com[cm[0]]["depth"].append((spread, depth2))
        sampled += 1
        time.sleep(0.05)

    print("\n== STRUCTURE ENUMERATION (open commodity-hub markets) ==")
    if not by_com:
        print("  (no open commodity markets classified — check COMMODITY_RULES vs live tickers)")
    for name, rec in sorted(by_com.items(), key=lambda kv: -kv[1]["n"]):
        pt = ",".join(f"{k}:{v}" for k, v in sorted(rec["ptypes"].items(), key=lambda x: -x[1]))
        spreads = [s for s, _ in rec["depth"] if s is not None]
        depths = [d for _, d in rec["depth"] if d]
        sp = f"{min(spreads)}-{max(spreads)}c" if spreads else "n/a"
        dp = f"~{sum(depths)//len(depths)}" if depths else "n/a"
        print(f"  {name:10s} n={rec['n']:4d}  series={sorted(rec['series'])[:3]}  "
              f"ptypes[{pt}]  spread {sp}  depth@2c {dp}")


# --- main --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FREEZE probe (pre-registered 2026-07-11)")
    ap.add_argument("--max-event-pages", type=int, default=80)
    ap.add_argument("--max-markets", type=int, default=500)
    ap.add_argument("--trade-pages", type=int, default=5)
    ap.add_argument("--open-pages", type=int, default=30)
    ap.add_argument("--depth-samples", type=int, default=12)
    args = ap.parse_args(argv)

    print("FREEZE probe — commodity-hub exchange-closure pin (pre-registered docs/FREEZE_THESIS.md)")
    print("NOTE: only grains+softs get a real source-dark freeze (metals/energy = Pyth-continuous);")
    print("      SETTLEPIN control spans all commodities; TOUCHPIN is N/A in v1 (see docstring).")

    events = settled_events(args.max_event_pages)
    print(f"\nsettled events scanned: {len(events)}")

    # Build the target list: settled commodity markets with a strike + yes/no result.
    targets: list[tuple[dict, str, str]] = []  # (market, commodity, group)
    for ev in events:
        cat = ev.get("category") or ""
        etext = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"),
                                       ev.get("series_ticker")]))
        cat_ok = bool(COMMODITY_CATEGORY.search(cat))
        for m in ev.get("markets") or []:
            if (m.get("result") or "").lower() not in ("yes", "no"):
                continue
            mtext = " ".join(filter(None, [etext, m.get("title"), m.get("subtitle"),
                                           m.get("ticker"), m.get("series_ticker")]))
            cm = commodity_of(mtext)
            if not cm and not cat_ok:
                continue
            if not cm:
                continue
            targets.append((m, cm[0], cm[1]))
    print(f"settled commodity markets (with result): {len(targets)}")
    grp_counts = defaultdict(int)
    for _m, _c, g in targets:
        grp_counts[g] += 1
    print(f"  by group: {dict(grp_counts)}")

    acc = {"post": [], "pre": [], "red_flags": []}
    week_notional: dict[int, float] = {}
    scanned = 0
    cell_counts = defaultdict(int)
    for m, commodity, group in targets:
        if scanned >= args.max_markets:
            break
        close = _ts(m.get("close_time"))
        result = (m.get("result") or "").lower()
        if not close:
            continue
        # FREEZE cell (grains/softs): close_time inside a source-dark window.
        freeze_pin = dark_window_start(group, close)
        # SETTLEPIN control (all): close after the official daily settle print.
        settle_pin = settle_pin_ts(group, close)
        pin_ts, cell = 0.0, ""
        if freeze_pin:
            pin_ts, cell = freeze_pin, "FREEZE"
        elif settle_pin and close > settle_pin:
            pin_ts, cell = settle_pin, "SETTLEPIN"
        else:
            continue
        rows = trades(m["ticker"], args.trade_pages)
        if not rows:
            continue
        scanned += 1
        cell_counts[cell] += 1
        score_trades(rows, result, pin_ts, cell, commodity, acc, week_notional)
    print(f"markets with tape scanned: {scanned}  by cell: {dict(cell_counts)}")

    # --- pooled + per-cell ---------------------------------------------------------
    post_ev, post_n = wavg(acc["post"])
    pre_ev, pre_n = wavg(acc["pre"])
    post_trades = len(acc["post"])
    print("\n== pooled ==")
    print(f"post-pin: EV {post_ev:+.2f} c/ct on n={post_n} contracts ({post_trades} trades)")
    print(f"pre-pin favorite-buy control (>=85c): EV {pre_ev:+.2f} c/ct on n={pre_n}")

    print("\n== per cell (post-pin) ==")
    cells: dict[str, list] = defaultdict(list)
    for row in acc["post"]:
        cells[row[2]].append(row)
    freeze_ev, freeze_trades = 0.0, 0
    for c, rows in sorted(cells.items()):
        ev_c, n_c = wavg(rows)
        print(f"  {c:9s} EV {ev_c:+.2f} c/ct  n={n_c} ({len(rows)} trades)")
        if c == "FREEZE":
            freeze_ev, freeze_trades = ev_c, len(rows)

    print("\n== per commodity (FREEZE cell only) ==")
    fcom: dict[str, list] = defaultdict(list)
    for row in acc["post"]:
        if row[2] == "FREEZE":
            fcom[row[3]].append(row)
    for name, rows in sorted(fcom.items()):
        ev_c, n_c = wavg(rows)
        print(f"  {name:10s} EV {ev_c:+.2f} c/ct  n={n_c} ({len(rows)} trades)")

    weeks = max(len(week_notional), 1)
    wk_avg = sum(week_notional.values()) / weeks
    print(f"\nFREEZE post-pin notional at >=2c discount: ${sum(week_notional.values()):.0f} "
          f"over {weeks} active weeks -> ${wk_avg:.0f}/week")

    if acc["red_flags"]:
        print(f"\n!! RED FLAGS — pinned side LOST on {len(acc['red_flags'])} trades "
              f"(freeze/settlement assumption wrong; investigate before ANY promotion):")
        seen = set()
        for tk, c, side, res, cost in acc["red_flags"][:10]:
            if tk not in seen:
                seen.add(tk)
                print(f"   {tk} cell={c} pinned={side} result={res} cost={cost:.0f}c")

    # --- structure enumeration -----------------------------------------------------
    opens = open_markets(args.open_pages)
    print(f"\nopen markets scanned for structure: {len(opens)}")
    enumerate_structure(opens, args.depth_samples)

    # --- pre-registered verdicts (thresholds fixed 2026-07-11; do not re-scope) -----
    print("\n== pre-registered verdicts (docs/FREEZE_THESIS.md; do not re-scope) ==")
    p1 = ("PASS" if (post_ev >= 3.0 and post_trades >= 80)
          else ("KILL" if post_ev < 1.5 else "GREY"))
    p2 = "PASS" if post_ev - pre_ev >= 2.0 else "FAIL"
    p3 = "PASS" if (freeze_ev >= 3.0 and freeze_trades >= 25) else "FAIL"
    p4 = "PASS" if wk_avg >= 150 else "FAIL"
    p5 = "PASS" if not acc["red_flags"] else "FAIL"
    print(f"P1 pooled post-pin EV >= +3c (>=80 trades, kill<1.5c): {p1}  "
          f"({post_ev:+.2f}c, {post_trades} trades, {post_n} contracts)")
    print(f"P2 post-pin beats pre-pin favorite control by >=2c: {p2}  ({post_ev - pre_ev:+.2f}c)")
    print(f"P3 FREEZE cell >= +3c at >=25 trades: {p3}  ({freeze_ev:+.2f}c, {freeze_trades} trades)")
    print(f"P4 >=$150/week FREEZE notional at >=2c: {p4}  (${wk_avg:.0f}/wk)")
    print(f"P5 zero wrong pins: {p5}  ({len(acc['red_flags'])} red flags)")
    promote = all(v == "PASS" for v in (p1, p2, p3, p4, p5))
    print(f"DECISION (P1&P2&P3&P4&P5): "
          f"{'PROMOTE to paper book `freeze`' if promote else 'do not promote'}")
    if p3 == "FAIL" and post_trades:
        print("NOTE: P3 FAIL => the exchange-closure pin does not clear off-weather; per the "
              "decision rule this closes the mechanical-pin family (FREEZE + COMPIN).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
