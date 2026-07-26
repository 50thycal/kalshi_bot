"""XLOCK — locked arbitrage in the pockets kalshi_arb.py's within-event scan cannot see.
(idea-model 2026-07-25, structural/arb-adjacent run; docs/XLOCK_THESIS.md)

Three pre-registered checks over ONE board fetch:

  P1 PARLAY CONTAINMENT: a combo/parlay market (KXMVE-prefixed, or any series/title tagged
  "COMBO") implies each of its component legs, so P(combo) <= P(leg_i) for every i. Locked when
  combo_yes_bid - leg_i_yes_ask - fees > 0 (sell the combo, buy the cheaper leg -- worst case
  the combo settles NO and the leg could go either way, but the entry credit is banked either
  way; if the combo settles YES every component leg is forced YES too, netting the $1 flows to
  zero). Components are recovered from the combo market's own settlement rules text
  (`rules_primary`/`rules_secondary`, one GET /markets/{ticker} per combo candidate -- bounded
  by --max-combo-detail), split on AND/&, and matched by EXACT case-insensitive string equality
  against another open market's own subtitle/title from a DIFFERENT series. An unmatched or
  ambiguous (2+ hits) phrase drops the whole combo -- never guessed (the precision-over-recall
  rule this family has needed twice already: KXMUSKNW, KXXRP).

  P2 TOUCH-VS-TERMINAL CONTAINMENT (Crypto only in v1 -- the only category with a confirmed
  "MAX"/"MIN" touch-series naming convention; see xvenue_crypto.py's own MAX/MIN classification,
  reused here): ending >= K at close T means the price touched >= K at some point in [0, T], so
  TERMINAL(price >= K at T) is a SUBSET of TOUCH(max ever >= K by T). Locked when
  terminal_yes_bid - touch_yes_ask - fees > 0 (sell terminal, buy touch).

  IMPORTANT DIRECTION NOTE: this is the ONLY riskless direction. The reverse (sell touch YES,
  buy terminal YES) has an uncapped loss branch -- touched K but pulled back below it by close,
  so touch settles YES while terminal settles NO, and the short touch position pays out $1 while
  the long terminal position is worthless. The thesis draft before this probe was written
  actually had the formula backwards (touch_bid - terminal_ask); re-deriving the payoff table
  while building this script caught it BEFORE any run, so docs/XLOCK_THESIS.md was corrected
  to match -- see that file's revision note. A same-scan-pass hit is only trusted in the
  terminal-bid-minus-touch-ask direction.

  ASSET GUARD (fixed after the script's FIRST live run): the first real scan matched a SOL
  terminal leg (KXSOLD) against a HYPE touch leg (KXHYPEMAXMON) purely because they shared a
  strike and a close time -- two unrelated cryptocurrencies with no containment relation at all,
  reported as a fake +$0.41 lead. `_series_asset` extracts the underlying symbol from the
  series ticker (KXBTCD -> BTC, KXHYPEMAXMON -> HYPE, KXDOGED -> DOGE) and a pair is only
  matched when both sides agree -- this is now a hard precondition, not an afterthought, exactly
  the "match on real identifiers, drop the ambiguous one" rule this family has needed for every
  prior false positive (KXMUSKNW's units, KXXRP's dropped legs, and now this).

  P3 COVERAGE CENSUS: reuses kalshi_arb.scan_event on the SAME fetch and tallies, for every
  event, exactly why it does or doesn't produce a signal -- KXMVE-excluded, raw <3 legs,
  horizon-excluded, illiquid-filtered <3 legs, MECE-clean, MECE-but-gapped, monotonicity-
  evaluated, or no exploitable structure at all -- so "N events scanned" has a known
  denominator instead of an unmeasured one.

Both P1 and P2 hits must pass a fillability audit before being reported: >= --min-fill-size
contracts of LIVE displayed depth on both legs (via /markets/{ticker}/orderbook, fetched ONLY
for candidate hits, never for the whole board), and the whole board fetch must complete within
60s or every hit below is flagged as possibly-stale (a same-scan-pass violation is exactly how
a phantom lock appears -- the KXMUSKNW and KXXRP false positives this family exists to avoid
repeating).

Read-only public Kalshi REST only, stdlib. Nothing persisted; a live-board scan, not a backtest.
Usage: {"type":"script","name":"kalshi_xlock","args":["--days","45"],"id":"xlock-1"}
"""

from __future__ import annotations

import argparse
import re
import time

import kalshi_arb as arb  # KALSHI, fee, _parse_bucket, _close_ts, scan_event
import xvenue_leadlag as xl  # _get (browser UA + retries -> None), _num

_COMBO_SERIES = re.compile(r"KXMVE|COMBO", re.I)
_SPLIT_AND = re.compile(r"\s+(?:and|&)\s+", re.I)


# --- shared fetch helpers ------------------------------------------------------------


def market_detail(ticker: str) -> dict | None:
    """One market's full detail (incl. rules_primary/rules_secondary), or None on failure."""
    body = xl._get(f"{arb.KALSHI}/markets/{ticker}")
    return (body or {}).get("market")


def orderbook_top_size(ticker: str, side: str) -> int:
    """Best-level displayed size (contracts) for `side` ('yes' or 'no') from
    /markets/{ticker}/orderbook?depth=1. Shape-robust across the classic `orderbook` (int
    cents) and newer `orderbook_fp` (dollar-string) wrappers -- mirrors
    kalshi_freeze_study.py's _ob_sides/_book_levels. Returns 0 on any parse failure
    (fail-soft; treated as unfillable, never as an error)."""
    data = xl._get(f"{arb.KALSHI}/markets/{ticker}/orderbook?depth=1")
    book = (data or {}).get("orderbook") or (data or {}).get("orderbook_fp") or (data or {})
    raw = book.get(side)
    if raw is None:
        raw = book.get(f"{side}_dollars")
    best = 0
    for lvl in raw or []:
        try:
            cnt = int(round(float(lvl[1])))
        except (TypeError, ValueError, IndexError):
            continue
        best = max(best, cnt)
    return best


def audit_fillable(bid_side_ticker: str, ask_side_ticker: str, min_size: int) -> bool:
    """Both legs of a hit must show >= min_size contracts of live displayed depth: the leg we
    SELL's own `yes` book (resting yes buyers -- what a market sell-yes lifts), and the leg we
    BUY's `no` book (resting no buyers -- the implied yes-ask depth a market buy-yes lifts)."""
    return (orderbook_top_size(bid_side_ticker, "yes") >= min_size
            and orderbook_top_size(ask_side_ticker, "no") >= min_size)


# --- P3: coverage census --------------------------------------------------------------


def census(events: list[dict], max_close: int) -> dict:
    """Tally exactly why kalshi_arb.scan_event does or doesn't produce a signal for every open
    event, reusing scan_event itself (never re-deriving its verdict by hand -- that would risk
    drifting from what the deployed scanner actually does) and only adding the finer breakdown
    for the None/skip case, where the extra transparency is the whole point of this check."""
    c = {
        "total_events": len(events), "kxmve_excluded": 0, "raw_lt3_legs": 0,
        "horizon_excluded": 0, "illiquid_filtered_lt3": 0, "evaluated": 0,
        "mece_clean": 0, "mece_gapped": 0, "mono_evaluated": 0, "no_structure": 0,
        "flagged_arb": 0,
    }
    for e in events:
        if (e.get("series_ticker") or "").startswith("KXMVE"):
            c["kxmve_excluded"] += 1
            continue
        raw = e.get("markets") or []
        if len(raw) < 3:
            c["raw_lt3_legs"] += 1
            continue
        close_ts = {id(m): arb._close_ts(m) for m in raw}
        horizon_n = sum(1 for m in raw
                        if not (max_close and close_ts[id(m)] and close_ts[id(m)] > max_close))
        if horizon_n < 3:
            c["horizon_excluded"] += 1
            continue
        liquid_n = 0
        for m in raw:
            ct = close_ts[id(m)]
            if max_close and ct and ct > max_close:
                continue
            yb, ya = xl._num(m.get("yes_bid_dollars")), xl._num(m.get("yes_ask_dollars"))
            if 0 < ya <= 1 and yb >= 0:
                liquid_n += 1
        if liquid_n < 3:
            c["illiquid_filtered_lt3"] += 1
            continue
        c["evaluated"] += 1
        r = arb.scan_event(e, 0.0, max_close)
        if r is None:
            c["no_structure"] += 1
        elif r.get("mece"):
            c["mece_gapped" if r.get("gapped") else "mece_clean"] += 1
        else:
            c["mono_evaluated"] += 1
        if r and "ARB" in r:
            c["flagged_arb"] += 1
    return c


# --- P1: parlay containment -----------------------------------------------------------


def find_combo_markets(events: list[dict]) -> list[tuple[dict, dict]]:
    """[(event, market)] for every market whose event is structurally tagged as a combo/parlay
    (KXMVE-prefixed series, or "COMBO" in the series ticker or title) -- detects WHETHER an
    event is a combo, not WHICH markets compose it (that's combo_legs_from_rules)."""
    out = []
    for e in events:
        series = e.get("series_ticker") or ""
        title = e.get("title") or ""
        if _COMBO_SERIES.search(series) or _COMBO_SERIES.search(title):
            out.extend((e, m) for m in e.get("markets") or [])
    return out


def combo_legs_from_rules(rules_text: str) -> list[str]:
    """Split a combo market's settlement rules on AND/& into candidate leg-condition phrases.
    Extraction only -- matching against real open markets happens in match_leg."""
    parts = [p.strip() for p in _SPLIT_AND.split(rules_text or "") if p.strip()]
    return parts if len(parts) >= 2 else []


def flatten_noncombo_legs(events: list[dict], max_close: int) -> list[dict]:
    """Every quote-valid, in-horizon market NOT in a combo/parlay event -- the candidate pool
    P1 matches decomposed combo legs against."""
    out = []
    for e in events:
        series = e.get("series_ticker") or ""
        title = e.get("title") or ""
        if _COMBO_SERIES.search(series) or _COMBO_SERIES.search(title):
            continue
        for m in e.get("markets") or []:
            ct = arb._close_ts(m)
            if max_close and ct and ct > max_close:
                continue
            yb, ya = xl._num(m.get("yes_bid_dollars")), xl._num(m.get("yes_ask_dollars"))
            if not (0 < ya <= 1) or yb < 0:
                continue
            out.append({"tk": m.get("ticker"), "series": series,
                        "sub": m.get("yes_sub_title") or "", "title": m.get("title") or "",
                        "yb": yb, "ya": ya})
    return out


def match_leg(phrase: str, candidates: list[dict], own_series: str) -> dict | None:
    """Exact case-insensitive match of `phrase` against a candidate's subtitle or title, from a
    DIFFERENT series than the combo's own. None (dropped) on zero or ambiguous (>=2) matches --
    never guessed. This is the load-bearing precision-over-recall step: a wrong match here
    fabricates a fake lock exactly like a title-parsed strike did in July and again with
    KXXRP's dropped legs."""
    key = phrase.strip().lower()
    if not key:
        return None
    hits = [c for c in candidates
            if c["series"] != own_series
            and key in (c["sub"].strip().lower(), c["title"].strip().lower())]
    return hits[0] if len(hits) == 1 else None


def scan_p1(events: list[dict], all_legs: list[dict], max_combo_detail: int) -> dict:
    combos = find_combo_markets(events)
    result = {"combo_candidates": len(combos), "detail_fetched": 0, "decomposed": 0,
              "matched_pairs": 0, "hits": []}
    for e, m in combos[:max_combo_detail]:
        ticker = m.get("ticker") or ""
        detail = market_detail(ticker)
        result["detail_fetched"] += 1
        if not detail:
            continue
        rules = " ".join(filter(None, [detail.get("rules_primary"), detail.get("rules_secondary")]))
        parts = combo_legs_from_rules(rules)
        if not parts:
            continue
        result["decomposed"] += 1
        legs = [match_leg(p, all_legs, e.get("series_ticker") or "") for p in parts]
        if any(leg is None for leg in legs):
            continue    # an unmatched/ambiguous component drops the WHOLE combo -- never guessed
        result["matched_pairs"] += 1
        combo_yb = xl._num(m.get("yes_bid_dollars"))
        for leg in legs:
            profit = combo_yb - leg["ya"] - arb.fee(combo_yb) - arb.fee(leg["ya"])
            if profit > 0:
                result["hits"].append({"combo_ticker": ticker, "combo_bid": combo_yb,
                                       "leg_ticker": leg["tk"], "leg_ask": leg["ya"],
                                       "profit": profit})
    return result


# --- P2: touch-vs-terminal containment (Crypto only, v1) -------------------------------

# The asset symbol out of a crypto series ticker: KXBTCD -> BTC, KXBTCMAXY -> BTC,
# KXHYPEMAXMON -> HYPE, KXDOGED -> DOGE (the D$ anchor keeps a 'D'-leading asset like DOGE from
# matching the single-letter Daily suffix before its own name is captured).
_ASSET = re.compile(r"^KX([A-Za-z0-9]+?)(?:MAX|MIN|D$)")


def _series_asset(series: str) -> str | None:
    m = _ASSET.match((series or "").upper())
    return m.group(1) if m else None


def scan_p2(events: list[dict], max_gap_hours: float) -> dict:
    touch, terminal = [], []
    for e in events:
        if (e.get("category") or "") != "Crypto":
            continue
        series = e.get("series_ticker") or ""
        asset = _series_asset(series)
        if not asset:
            continue          # can't confirm the underlying -> never eligible, not guessed
        is_touch = bool(re.search(r"MAX|MIN", series.upper()))
        if e.get("mutually_exclusive"):
            continue          # a range-bucket partition event, not independent thresholds --
                               # never eligible, so excluded here rather than counted then dropped
        for m in e.get("markets") or []:
            parse = arb._parse_bucket(m.get("yes_sub_title") or "")
            if not parse or parse[0] not in ("ge", "le"):
                continue      # only independent thresholds are comparable; range legs are not
            ct = arb._close_ts(m)
            if not ct:
                continue
            yb, ya = xl._num(m.get("yes_bid_dollars")), xl._num(m.get("yes_ask_dollars"))
            if not (0 < ya <= 1) or yb < 0:
                continue
            row = {"dir": parse[0], "strike": parse[1], "close": ct, "yb": yb, "ya": ya,
                   "ticker": m.get("ticker"), "asset": asset}
            (touch if is_touch else terminal).append(row)

    matched = 0
    hits = []
    tol_s = max_gap_hours * 3600.0
    for t in terminal:
        best = None
        for u in touch:
            if u["asset"] != t["asset"]:
                continue      # different underlyings -> no containment relation, ever
            if u["dir"] != t["dir"]:
                continue
            if abs(u["strike"] - t["strike"]) > max(1e-6, 0.001 * abs(t["strike"])):
                continue
            if u["close"] < t["close"] - 3600:   # touch window must span the terminal checkpoint
                continue
            if abs(u["close"] - t["close"]) > tol_s:
                continue
            if best is None or u["close"] < best["close"]:
                best = u        # nearest-horizon touch match
        if best is None:
            continue
        matched += 1
        profit = t["yb"] - best["ya"] - arb.fee(t["yb"]) - arb.fee(best["ya"])
        if profit > 0:
            hits.append({"terminal": t, "touch": best, "profit": profit})
    return {"touch_n": len(touch), "terminal_n": len(terminal), "matched": matched, "hits": hits}


# --- orchestration ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=45.0, help="events closing within N days")
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--max-combo-detail", type=int, default=40,
                    help="cap on /markets/{ticker} rules-text fetches for P1 (bounds API cost)")
    ap.add_argument("--touch-gap-hours", type=float, default=36.0,
                    help="max |touch_close - terminal_close| to consider matched (P2)")
    ap.add_argument("--min-fill-size", type=int, default=10,
                    help="min displayed depth (contracts) required on both legs of a hit")
    args = ap.parse_args(argv)
    max_close = int(time.time() + args.days * 86400)

    t0 = time.time()
    events: list[dict] = []
    cursor = ""
    for _ in range(args.max_pages):
        page = xl._get(f"{arb.KALSHI}/events?status=open&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        events.extend(evs)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    fetch_span = time.time() - t0

    print(f"=== XLOCK — {len(events)} open events fetched in {fetch_span:.1f}s ===")
    if fetch_span > 60:
        print("  WARNING: fetch spanned >60s -- legs compared below may not be same-scan-pass;"
              " treat any hit as a LEAD to re-verify live, not a confirmed lock.\n")

    cen = census(events, max_close)
    print("\n--- P3: coverage census "
          "(why kalshi_arb.py's within-event scan does/doesn't fire, over every open event) ---")
    for k, v in cen.items():
        print(f"    {k:>20}: {v}")

    all_legs = flatten_noncombo_legs(events, max_close)
    p1 = scan_p1(events, all_legs, args.max_combo_detail)
    print("\n--- P1: parlay containment ---")
    print(f"    combo-like markets found: {p1['combo_candidates']}"
          f" | rules fetched: {p1['detail_fetched']}"
          f" | decomposed (>=2 AND-parts): {p1['decomposed']}"
          f" | matched to open legs: {p1['matched_pairs']}")
    p1_audited = [h for h in p1["hits"]
                  if audit_fillable(h["combo_ticker"], h["leg_ticker"], args.min_fill_size)]
    if p1["hits"]:
        print(f"    raw quote-level hits: {len(p1['hits'])},"
              f" surviving fillability audit: {len(p1_audited)}")
        for h in sorted(p1_audited, key=lambda h: -h["profit"])[:10]:
            print(f"      +${h['profit']:.3f}  combo={h['combo_ticker']} bid={h['combo_bid']:.2f}"
                  f"  leg={h['leg_ticker']} ask={h['leg_ask']:.2f}")
    else:
        print("    (0 quote-level hits)")
    if p1["matched_pairs"] < 200:
        print(f"    P1 verdict: HOLD (testability-thin -- {p1['matched_pairs']} matched pairs"
              f" < the pre-registered 200 floor; the rules-text matcher may simply be too"
              f" strict for how Kalshi phrases combo rules -- not proof combos don't exist)")
    elif p1_audited:
        print(f"    P1 verdict: LEAD -- {len(p1_audited)} audited hit(s) survive; per the"
              f" thesis decision rule, needs a repeat scan >=24h later before promotion")
    else:
        print(f"    P1 verdict: KILL ({p1['matched_pairs']} matched pairs, 0 hits survive)")

    p2 = scan_p2(events, args.touch_gap_hours)
    print("\n--- P2: touch-vs-terminal containment (Crypto only, v1 scope) ---")
    print(f"    touch legs (MAX/MIN series): {p2['touch_n']} | terminal legs: {p2['terminal_n']}"
          f" | matched pairs: {p2['matched']}")
    p2_audited = [h for h in p2["hits"]
                  if audit_fillable(h["terminal"]["ticker"], h["touch"]["ticker"],
                                    args.min_fill_size)]
    if p2["hits"]:
        print(f"    raw quote-level hits: {len(p2['hits'])},"
              f" surviving fillability audit: {len(p2_audited)}")
        for h in sorted(p2_audited, key=lambda h: -h["profit"])[:10]:
            t, u = h["terminal"], h["touch"]
            print(f"      +${h['profit']:.3f}  terminal={t['ticker']} bid={t['yb']:.2f}"
                  f"  touch={u['ticker']} ask={u['ya']:.2f}  strike={t['strike']:.0f}")
    else:
        print("    (0 quote-level hits)")
    if p2["matched"] < 20:
        print(f"    P2 verdict: HOLD (testability-thin -- {p2['matched']} matched pairs"
              f" < the pre-registered 20 floor)")
    elif p2_audited:
        print(f"    P2 verdict: LEAD -- {len(p2_audited)} audited hit(s) survive; per the"
              f" thesis decision rule, needs a repeat scan >=24h later before promotion")
    else:
        print(f"    P2 verdict: KILL ({p2['matched']} matched pairs, 0 hits survive)")

    print("\n  net of ceil(7*p*(1-p)) per-leg fee; top-of-book + displayed depth only"
          " (latency not modeled).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
