"""COMPIN probe -- does the elapsed portion of a TWAP/average settlement window progressively
LOCK the outcome of Kalshi commodity-hub contracts, while the quote keeps pricing two-way risk
off the flashing spot print (the pin15 "mechanics blindness" shape, stretched to an hours-long
latency budget)?

Pre-registered thesis in docs/COMPIN_THESIS.md (promoted from docs/IDEA_MODEL_20260712.md,
candidate M1 -- the trigger fired by kalshi_freeze_study's structure enumeration, which found
diesel 41/43 and some oil markets are average(TWAP)-settled). READ-ONLY study on public data
only -- no DB, no collector, no auth:

  - reuses kalshi_freeze_study (kfs) for the commodity classifier, price-type classifier, open/
    settled event pullers, orderbook depth sampler, and fee/wavg helpers -- same infra, no
    reclassification drift between the two probes.
  - NEW: per-market rules-text window parsing (GET /markets/{ticker} -> rules_primary/
    rules_secondary), Pyth Benchmarks historical price reconstruction (public TradingView-shim
    endpoint, free until 2026-07-31 -- see module-level PYTH_DEADLINE_NOTE), and a decided-time
    ("t*") computation per market: the first instant at which the elapsed partial average plus
    a reach-guarded band around current spot for the REMAINING window can no longer straddle the
    strike.

Honesty gates baked in (the FREEZE/PINNED lesson -- small-n mirages and lookahead bugs are the
top process risk):
  - Feed-ID mapping is DISCOVERED at runtime (best-effort candidate probing against the public
    Pyth symbols endpoint), never assumed. Any commodity whose feed can't be resolved is reported
    UNMAPPED, not silently scored as zero.
  - Window length is parsed from the market's own rules text; unparseable markets are reported
    UNPARSED and excluded from scoring (never defaulted to a guess).
  - t* is derived ONLY from source data available at t (partial average + realized vol of the
    elapsed portion); it never looks at price data after t or at the settlement result.
  - Any post-decided trade where the pinned side LOSES is a red flag that blocks promotion,
    exactly like FREEZE/PINNED's P5/P1 guard.
  - If pooled post-decided trades < 25, the verdict is UNTESTABLE (data-absence), not a kill --
    the FREEZE precedent, stated in COMPIN's own pre-registration.

Pre-registered predictions (graded verbatim vs docs/COMPIN_THESIS.md; do not re-scope):
  P1 decided-classifier calibration >= 97% (zero tolerance is the ideal; any loss is a red flag
     that halts the verdict until diagnosed).
  P2 pooled post-decided EV >= +3c/ct on >= 100 real trades (KILL < 1.5c).
  P3 post-decided EV exceeds the PRE-decided favorite-buy control (>=85c) by >= 2c/ct.
  P4 post-decided notional at >= 2c discount averages >= $150/week.
  P5 window-floor filter: only markets with a parsed window >= 30 minutes qualify for the book;
     EV is reported by window-length bucket so a sub-30-min-only edge is visible, not hidden.
  UNTESTABLE branch: pooled post-decided trades < 25 -> UNTESTABLE, not a kill.
  Decision: paper book only if P1 & P2 & P3 (P4 modulates size; P5 filters the universe).

EV convention: cents/contract net of the entry-leg taker fee ceil(0.07*P*(1-P)*100) (hold to
settlement -> no exit leg), matching every other probe in this repo. Read-only public APIs
(Kalshi trade-api v2 + Pyth Benchmarks), stdlib only. Usage (via the ops channel):
    {"type":"script","name":"kalshi_compin_study","args":["--max-event-pages","80"],"id":"compin-0"}

PYTH_DEADLINE_NOTE: Pyth Benchmarks' history endpoint is free/keyless today but requires an
Authorization: Bearer $PYTH_API_KEY header starting 2026-07-31. This probe has no key configured
and will start failing Stage 2 (history pull) after that date -- run before then.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import kalshi_freeze_study as kfs
import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = kfs.KALSHI
PYTH = "https://benchmarks.pyth.network/v1/shims/tradingview"
_PYTH_UA = xl._UA if hasattr(xl, "_UA") else (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

fee_cents = kfs.fee_cents
_ts = kfs._ts
wavg = kfs.wavg
commodity_of = kfs.commodity_of
price_type_of = kfs.price_type_of


# --- Pyth fetch (separate host from Kalshi; own tiny retry wrapper, no cookies/auth needed yet) --

def _pyth_get(url: str):
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": _PYTH_UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed host)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return {"_error": "unauthorized (Pyth key required -- past the Jul 31 deadline?)"}
            if exc.code in (404,):
                return None
            if exc.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(1.5 ** attempt)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < 2:
                time.sleep(1.5 ** attempt)
                continue
            return None
    return None


# --- feed discovery (best-effort; never assumed) -----------------------------------
# Candidate symbol strings per commodity, ordered by plausibility across the naming schemes
# Pyth has used for non-crypto feeds ("Crypto.ETH/USD" is the one confirmed-exact format; the
# equity/metal/commodity prefix is inferred and MUST be validated against the live symbols
# endpoint, never assumed correct).

FEED_CANDIDATES: dict[str, list[str]] = {
    "gold": ["Metal.XAU/USD", "Commodities.XAU/USD", "Commodities.GOLD/USD", "FX.XAU/USD"],
    "silver": ["Metal.XAG/USD", "Commodities.XAG/USD", "Commodities.SILVER/USD", "FX.XAG/USD"],
    "copper": ["Metal.COPPER/USD", "Commodities.COPPER/USD", "Metal.XCU/USD", "Commodities.XCU/USD"],
    "pgm": ["Metal.XPT/USD", "Commodities.XPT/USD", "Metal.XPD/USD", "Commodities.XPD/USD"],
    "nickel": ["Metal.NICKEL/USD", "Commodities.NICKEL/USD"],
    "lithium": ["Metal.LITHIUM/USD", "Commodities.LITHIUM/USD"],
    "oil.wti": ["Commodities.WTI/USD", "Energy.WTI/USD", "Commodities.USOIL/USD", "Commodities.CL/USD"],
    "oil.brent": ["Commodities.BRENT/USD", "Energy.BRENT/USD", "Commodities.UKOIL/USD", "Commodities.CO/USD"],
    "natgas": ["Commodities.NATGAS/USD", "Energy.NATGAS/USD", "Commodities.NG/USD"],
    "refined": ["Commodities.HEATOIL/USD", "Energy.HEATOIL/USD", "Commodities.DIESEL/USD",
                "Commodities.RBOB/USD", "Energy.RBOB/USD", "Commodities.GASOLINE/USD"],
    "corn": ["Commodities.CORN/USD", "Agri.CORN/USD", "Commodities.ZC/USD"],
    "wheat": ["Commodities.WHEAT/USD", "Agri.WHEAT/USD", "Commodities.ZW/USD"],
    "soybeans": ["Commodities.SOYBEAN/USD", "Agri.SOYBEAN/USD", "Commodities.ZS/USD"],
    "coffee": ["Commodities.COFFEE/USD", "Agri.COFFEE/USD", "Commodities.KC/USD"],
    "sugar": ["Commodities.SUGAR/USD", "Agri.SUGAR/USD", "Commodities.SB/USD"],
    "cocoa": ["Commodities.COCOA/USD", "Agri.COCOA/USD", "Commodities.CC/USD"],
    "cotton": ["Commodities.COTTON/USD", "Agri.COTTON/USD", "Commodities.CT/USD"],
}


def oil_subtype(text: str) -> str:
    return "oil.brent" if re.search(r"\bbrent\b", text or "", re.I) else "oil.wti"


def resolve_feed(commodity_key: str) -> str | None:
    """Probe the public symbols endpoint with each candidate; return the first that resolves.
    A resolution is any 200 JSON body without an explicit error/unauthorized marker -- we log the
    raw candidate tried either way so a re-run can see exactly what was attempted."""
    for cand in FEED_CANDIDATES.get(commodity_key, []):
        body = _pyth_get(f"{PYTH}/symbols?symbol={cand}")
        time.sleep(0.15)
        if body is None or (isinstance(body, dict) and body.get("_error")):
            continue
        # Accept anything that looks like a resolved symbol description (ticker/description/
        # exchange keys are the documented TradingView "symbol_info" shape); reject obvious
        # not-found markers some UDF-style APIs use ("s": "no_data" / "error").
        if isinstance(body, dict) and body.get("s") in ("no_data", "error"):
            continue
        return cand
    return None


def pyth_history(symbol: str, start_ts: float, end_ts: float) -> list[tuple[float, float]]:
    """Returns [(bar_close_ts, close_price), ...] 1-min bars, or [] on any failure/auth wall."""
    body = _pyth_get(f"{PYTH}/history?symbol={symbol}&resolution=1"
                      f"&from={int(start_ts)}&to={int(end_ts)}")
    if not body or not isinstance(body, dict) or body.get("_error"):
        return []
    if body.get("s") not in (None, "ok"):
        return []
    ts_list = body.get("t") or []
    closes = body.get("c") or []
    if not ts_list or len(ts_list) != len(closes):
        return []
    return list(zip((float(t) for t in ts_list), (float(c) for c in closes)))


# --- rules-text window parsing (no-lookahead: derived from the contract's own text, never
# from the price path) -----------------------------------------------------------------------

_WIN_MIN_RULES = [
    re.compile(r"(\d+)[\s-]*(?:minute|min)s?\s+(?:twap|vwap|average|avg)", re.I),
    re.compile(r"(?:twap|vwap|average|avg).{0,25}?(\d+)[\s-]*(?:minute|min)s?", re.I),
    re.compile(r"(\d+)[\s-]*(?:hour|hr)s?\s+(?:twap|vwap|average|avg)", re.I),  # captured, ×60 below
]
_WIN_HOUR_IDX = 2  # index of the hour-unit rule above


def parse_window_minutes(text: str) -> int | None:
    t = text or ""
    for i, rx in enumerate(_WIN_MIN_RULES):
        m = rx.search(t)
        if m:
            val = int(m.group(1))
            return val * 60 if i == _WIN_HOUR_IDX else val
    if re.search(r"\bhourly\b.{0,15}(?:average|avg|twap)", t, re.I):
        return 60
    if re.search(r"\bdaily\b.{0,15}(?:average|avg|twap)", t, re.I):
        return 1440
    if re.search(r"\bweekly\b.{0,15}(?:average|avg|twap)", t, re.I):
        return 10080
    m = re.search(r"between\s+(\d{1,2}):(\d{2})\s*(am|pm)?\s+and\s+(\d{1,2}):(\d{2})\s*(am|pm)?", t, re.I)
    if m:
        h1, m1, ap1, h2, m2, ap2 = m.groups()
        def to_min(h, mm, ap):
            h = int(h) % 12
            if (ap or "").lower() == "pm":
                h += 12
            return h * 60 + int(mm)
        start, end = to_min(h1, m1, ap1), to_min(h2, m2, ap2)
        span = end - start if end > start else end + 1440 - start
        return span if span > 0 else None
    return None


def market_detail(ticker: str) -> dict | None:
    body = xl._get(f"{KALSHI}/markets/{ticker}")
    return (body or {}).get("market")


def rules_text(market: dict) -> str:
    return " ".join(filter(None, [market.get("rules_primary"), market.get("rules_secondary")]))


# --- decided-time (t*) computation --------------------------------------------------
# settle = w*A_partial + (1-w)*R_remaining, w = elapsed fraction. R_remaining is bounded by a
# reach-guard around the LAST observed price: [last_price - guard, last_price + guard], guard =
# max(observed 1-min |return| over the elapsed portion, a 0.15% floor to avoid false-certain
# before any variance is observed) * sqrt(remaining_minutes) * 1.5 (the PINNED-v4 "running-max-
# rate reach guard x1.5" convention, applied over a random-walk sqrt(time) scaling). Decided the
# first instant the resulting [implied_low, implied_high] settle band no longer straddles strike.

def decided_time(bars: list[tuple[float, float]], window_start: float, window_end: float,
                  strike: float, side_ge: bool) -> tuple[float | None, str | None]:
    """bars: sorted (ts, price) 1-min closes covering [window_start, window_end].
    side_ge: True if the market resolves YES when settle >= strike (greater_or_equal), False if
    resolving YES means settle <= strike (less_or_equal / under-style contracts).
    Returns (t_star, decided_side) or (None, None) if never decided within the window."""
    if not bars or window_end <= window_start:
        return None, None
    total_min = (window_end - window_start) / 60.0
    if total_min <= 0:
        return None, None
    partial_sum, partial_n = 0.0, 0
    max_step = 0.0
    prev_price = None
    for ts, price in bars:
        if ts < window_start:
            continue
        if ts > window_end:
            break
        if prev_price is not None and prev_price > 0:
            max_step = max(max_step, abs(price - prev_price) / prev_price)
        prev_price = price
        partial_sum += price
        partial_n += 1
        elapsed_min = (ts - window_start) / 60.0
        w = min(1.0, elapsed_min / total_min)
        if w <= 0:
            continue
        remaining_min = max(0.0, total_min - elapsed_min)
        a_partial = partial_sum / partial_n
        guard_rate = max(max_step, 0.0015)
        guard = price * guard_rate * (1.5 * (remaining_min ** 0.5))
        implied_low = w * a_partial + (1 - w) * (price - guard)
        implied_high = w * a_partial + (1 - w) * (price + guard)
        if implied_low > strike:
            return ts, ("yes" if side_ge else "no")
        if implied_high < strike:
            return ts, ("no" if side_ge else "yes")
    return None, None


# --- trade scoring (same shape as kfs.score_trades) ---------------------------------

def score_trades(rows: list[dict], result: str, t_star: float, window: str,
                  commodity: str, acc: dict, week_notional: dict, red_flags: list) -> None:
    for t in rows:
        ts = _ts(t.get("created_time"))
        p = 0.0
        if t.get("yes_price_dollars") is not None:
            p = xl._num(t.get("yes_price_dollars")) * 100.0
        elif t.get("yes_price") is not None:
            p = xl._num(t.get("yes_price"))
        cnt = int(xl._num(t.get("count_fp")) or xl._num(t.get("count")) or 0) or 1
        if not ts or p <= 0 or p >= 100 or result not in ("yes", "no"):
            continue
        if ts >= t_star:
            side = result  # the classifier's decided call
            cost = p if side == "yes" else 100 - p
            ev = (100 - cost if result == side else -cost) - fee_cents(cost)
            acc["post"].append((ev, cnt, window, commodity))
            if result != side:
                red_flags.append((t.get("ticker"), window, side, result, cost))
            if (100 - cost) >= 2:
                wk = int(ts // (7 * 86400))
                week_notional[wk] = week_notional.get(wk, 0.0) + cost * cnt / 100.0
        else:
            fav = "yes" if p >= 85 else ("no" if p <= 15 else None)
            if fav:
                cost = p if fav == "yes" else 100 - p
                ev = (100 - cost if result == fav else -cost) - fee_cents(cost)
                acc["pre"].append((ev, cnt, window, commodity))


def window_bucket(minutes: int) -> str:
    if minutes < 30:
        return "<30m"
    if minutes < 120:
        return "30-120m"
    if minutes < 1440:
        return "2-24h"
    return ">=1d"


# --- main ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="COMPIN probe (pre-registered 2026-07-12)")
    ap.add_argument("--max-event-pages", type=int, default=80)
    ap.add_argument("--max-markets", type=int, default=300)
    ap.add_argument("--trade-pages", type=int, default=5)
    ap.add_argument("--open-pages", type=int, default=80)
    ap.add_argument("--depth-samples", type=int, default=12)
    ap.add_argument("--min-window-minutes", type=int, default=30)
    args = ap.parse_args(argv)

    print("COMPIN probe -- commodity-hub TWAP/average settlement-window pin "
          "(pre-registered docs/COMPIN_THESIS.md)")
    print("NOTE: Pyth Benchmarks history is free/keyless until 2026-07-31 -- if this run is past "
          "that date and Stage 2 reports 'unauthorized', that's the expected failure mode, not a bug.")

    # --- Stage 0: ride-along FREEZE trigger recheck (grain/soft settled universe) --------------
    events = kfs.settled_events(args.max_event_pages)
    print(f"\nsettled events scanned: {len(events)}")
    freeze_eligible = 0
    for ev in events:
        for m in ev.get("markets") or []:
            if (m.get("result") or "").lower() not in ("yes", "no"):
                continue
            etext = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"), ev.get("series_ticker")]))
            mtext = " ".join(filter(None, [etext, m.get("title"), m.get("subtitle"), m.get("ticker")]))
            cm = commodity_of(mtext)
            if cm and cm[1] in kfs.FREEZE_GROUPS:
                freeze_eligible += 1
    print(f"[ride-along] FREEZE-eligible settled markets (grain/soft) now: {freeze_eligible} "
          f"(trigger: grows into the hundreds)")

    # --- Stage 1: structure enumeration -- which settled markets are average/TWAP-settled ------
    avg_targets: list[tuple[dict, str, str]] = []  # (market, commodity_key, group)
    ptype_counts: dict[str, int] = defaultdict(int)
    for ev in events:
        etext = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"), ev.get("series_ticker")]))
        for m in ev.get("markets") or []:
            if (m.get("result") or "").lower() not in ("yes", "no"):
                continue
            mtext = " ".join(filter(None, [etext, m.get("title"), m.get("subtitle"),
                                           m.get("ticker"), m.get("series_ticker")]))
            cm = commodity_of(mtext)
            if not cm:
                continue
            pt = price_type_of(mtext)
            ptype_counts[pt] += 1
            if pt != "average":
                continue
            name, group = cm
            key = oil_subtype(mtext) if name == "oil" else name
            avg_targets.append((m, key, group))
    print(f"\nsettled commodity markets by price-type: {dict(ptype_counts)}")
    print(f"average/TWAP-settled settled markets found: {len(avg_targets)}")
    by_key_n: dict[str, int] = defaultdict(int)
    for _m, k, _g in avg_targets:
        by_key_n[k] += 1
    print(f"  by commodity: {dict(sorted(by_key_n.items(), key=lambda kv: -kv[1]))}")

    if not avg_targets:
        print("\nVERDICT: UNTESTABLE -- zero average/TWAP-settled markets classified. Either the "
              "structure has drifted since the FREEZE enumeration or COMMODITY_RULES/price-type "
              "classification needs a refresh. Do not treat as a kill.")
        opens = kfs.open_commodity_markets(args.open_pages)
        print(f"\nopen commodity markets found (via open events): {len(opens)}")
        kfs.enumerate_structure(opens, args.depth_samples)
        return 0

    # --- feed resolution (cache per commodity key) ---------------------------------
    feed_cache: dict[str, str | None] = {}
    unmapped: set[str] = set()

    def feed_for(key: str) -> str | None:
        if key not in feed_cache:
            feed_cache[key] = resolve_feed(key)
            if feed_cache[key] is None:
                unmapped.add(key)
        return feed_cache[key]

    # --- Stage 2/3: per-market rules parse -> Pyth reconstruct -> decided-time -> score --------
    acc = {"post": [], "pre": []}
    week_notional: dict[int, float] = {}
    red_flags: list = []
    scanned = 0
    unparsed_window = 0
    below_window_floor = 0
    no_feed = 0
    no_bars = 0
    scored = 0
    per_window_bucket: dict[str, list] = defaultdict(list)

    for m, key, group in avg_targets:
        if scanned >= args.max_markets:
            break
        scanned += 1
        ticker = m.get("ticker") or ""
        close = _ts(m.get("close_time"))
        result = (m.get("result") or "").lower()
        strike = xl._num(m.get("floor_strike") if m.get("floor_strike") is not None
                          else m.get("cap_strike"))
        side_ge = m.get("floor_strike") is not None  # >= strike style vs <= (cap) style
        if not close or not strike:
            continue

        detail = market_detail(ticker)
        time.sleep(0.05)
        if not detail:
            continue
        win_min = parse_window_minutes(rules_text(detail))
        if win_min is None:
            unparsed_window += 1
            continue
        if win_min < args.min_window_minutes:
            below_window_floor += 1
            # still measure it (Stage 5 reports by bucket) -- just flagged, not dropped, per the
            # "no silent caps" rule; the P5 filter is applied only at the promotion decision.

        feed = feed_for(key)
        if not feed:
            no_feed += 1
            continue

        window_start = close - win_min * 60
        bars = pyth_history(feed, window_start - 120, close + 60)
        time.sleep(0.15)
        if not bars:
            no_bars += 1
            continue
        bars.sort()

        t_star, decided_side = decided_time(bars, window_start, close, strike, side_ge)
        if t_star is None:
            continue  # never decided within the window at this guard tightness -- not scored

        rows = kfs.trades(ticker, args.trade_pages)
        if not rows:
            continue
        scored += 1
        score_trades(rows, result, t_star, window_bucket(win_min), key, acc, week_notional, red_flags)

    print(f"\nStage-2/3 funnel: scanned={scanned}  unparsed_window={unparsed_window}  "
          f"no_feed={no_feed} (unmapped commodities: {sorted(unmapped)})  no_bars={no_bars}  "
          f"below_window_floor(<{args.min_window_minutes}m, still scored)={below_window_floor}  "
          f"markets_scored={scored}")

    # --- pooled + per-window-bucket results -----------------------------------------
    post_ev, post_n = wavg(acc["post"])
    pre_ev, pre_n = wavg(acc["pre"])
    post_trades = len(acc["post"])
    print("\n== pooled ==")
    print(f"post-decided: EV {post_ev:+.2f} c/ct on n={post_n} contracts ({post_trades} trades)")
    print(f"pre-decided favorite-buy control (>=85c): EV {pre_ev:+.2f} c/ct on n={pre_n}")

    print("\n== per window-length bucket (post-decided) ==")
    for row in acc["post"]:
        per_window_bucket[row[2]].append(row)
    for bucket, rows in sorted(per_window_bucket.items()):
        ev_c, n_c = wavg(rows)
        print(f"  {bucket:8s} EV {ev_c:+.2f} c/ct  n={n_c} ({len(rows)} trades)")

    print("\n== per commodity (post-decided) ==")
    per_com: dict[str, list] = defaultdict(list)
    for row in acc["post"]:
        per_com[row[3]].append(row)
    for name, rows in sorted(per_com.items()):
        ev_c, n_c = wavg(rows)
        print(f"  {name:10s} EV {ev_c:+.2f} c/ct  n={n_c} ({len(rows)} trades)")

    weeks = max(len(week_notional), 1)
    wk_avg = sum(week_notional.values()) / weeks
    print(f"\npost-decided notional at >=2c discount: ${sum(week_notional.values()):.0f} "
          f"over {weeks} active weeks -> ${wk_avg:.0f}/week")

    if red_flags:
        print(f"\n!! RED FLAGS -- decided side LOST on {len(red_flags)} trades "
              f"(t* or feed-mapping assumption wrong; investigate before ANY promotion):")
        seen = set()
        for tk, w, side, res, cost in red_flags[:10]:
            if tk not in seen:
                seen.add(tk)
                print(f"   {tk} window={w} decided={side} result={res} cost={cost:.0f}c")

    # --- co-deliverable: orderbook depth read (unblocks OPTRV) ----------------------
    opens = kfs.open_commodity_markets(args.open_pages)
    print(f"\nopen commodity markets found (via open events): {len(opens)}")
    kfs.enumerate_structure(opens, args.depth_samples)

    # --- pre-registered verdicts (thresholds fixed 2026-07-12; do not re-scope) -----
    print("\n== pre-registered verdicts (docs/COMPIN_THESIS.md; do not re-scope) ==")
    calib = 1.0 - (len(red_flags) / post_trades) if post_trades else 1.0
    p1 = "PASS" if calib >= 0.97 else "FAIL"
    p2 = ("PASS" if (post_ev >= 3.0 and post_trades >= 100)
          else ("KILL" if post_ev < 1.5 else "GREY"))
    p3 = "PASS" if post_ev - pre_ev >= 2.0 else "FAIL"
    p4 = "PASS" if wk_avg >= 150 else "FAIL"
    floor_rows = [r for b, rows in per_window_bucket.items() if b != "<30m" for r in rows]
    floor_ev, floor_n = wavg(floor_rows)
    print(f"P1 decided-classifier calibration >= 97%: {p1}  ({calib*100:.1f}%, "
          f"{len(red_flags)} red flags / {post_trades} trades)")
    print(f"P2 pooled post-decided EV >= +3c (>=100 trades, kill<1.5c): {p2}  "
          f"({post_ev:+.2f}c, {post_trades} trades, {post_n} contracts)")
    print(f"P3 post-decided beats pre-decided favorite control by >=2c: {p3}  "
          f"({post_ev - pre_ev:+.2f}c)")
    print(f"P4 >=$150/week notional at >=2c: {p4}  (${wk_avg:.0f}/wk)")
    print(f"P5 window-floor (>=30m) EV: {floor_ev:+.2f}c/ct on n={floor_n} "
          f"({len(floor_rows)} trades) -- the tradeable-at-our-cadence universe")
    promote = p1 == "PASS" and p2 == "PASS" and p3 == "PASS"
    print(f"DECISION (P1&P2&P3; P4/P5 modulate size/scope): "
          f"{'PROMOTE to paper book `compin`' if promote else 'do not promote'}")
    TESTABLE_MIN = 25
    if not promote:
        if post_trades < TESTABLE_MIN:
            print(f"VERDICT: UNTESTABLE (provisional, NOT a clean kill) -- only {post_trades} "
                  f"post-decided trades from {scored} scored markets ({unparsed_window} unparsed "
                  f"windows, {no_feed} unmapped-feed markets, {no_bars} missing Pyth history). "
                  f"Revisit trigger: average-settled settled-market count grows into the "
                  f"hundreds, OR the unmapped-feed/unparsed-window gaps are fixed and re-run "
                  f"before the 2026-07-31 Pyth keyless deadline.")
        else:
            print("VERDICT: MEASURED FAIL -- adequate data, did not clear the pre-registered bar. "
                  "Per the decision rule this closes the mechanical-pin family off pin15/COMPIN's "
                  "two data points (both hours-scale variants); do not resurrect a TWAP-pin "
                  "variant without a materially new mechanism.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
