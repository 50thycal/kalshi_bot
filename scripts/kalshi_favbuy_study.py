"""TFAV thesis validation probe (docs/IDEA_MODEL_20260704.md) — crypto hourly
favorite-BUY, the mirror of theta.

Tests the pre-registered predictions on the LIVE-collected theta research dataset
(`crypto_ladder_snapshots`, model probability attached at capture with no lookahead),
labeled by Kalshi's own settlement result (public REST, keyed by market ticker):

  P1  Unconditional favorite-buy is ~0: buying every 65-90c favorite at the ask,
      final-hour entry, is <= +1c/contract. If strongly +EV unconditionally, suspect
      the "already-decided favorite" artifact — check the per-tte table before trusting.
  P2  Model-filtered favorite-buy clears cost: buying only favorites where
      100*P_model - ask >= 5c, final-hour entry, nets >= +3c/contract net of
      worst-case taker fees, with BOTH split-half OOS halves > 0.
      KILL if < +1c or halves disagree in sign.
  P3  tte structure matches theta: edge concentrates inside the final hour,
      flat/negative >60min out (the snapshot window reaches 90min).
  P4  Low correlation to theta's P&L: on the SAME snapshots, per-event favorite-buy
      P&L has |corr| < 0.4 with a theta-rule tail-sell simulation over the same
      events. DEMOTE to a theta feature if > 0.7 (just theta inverted).
  Decision: paper book `tfav` only if P2 AND P4. P2 pass / P4 fail -> fold into
  theta's gating. P2 fail -> close the favorite side permanently.

Provenance: quotes + model P are live-collected (same tables theta trades from);
settlement labels come from the Kalshi REST archive. One simulated trade per
(market, tte bucket): the EARLIEST qualifying snapshot in the bucket — so a market
snapshotted every ~5min counts once, not once per cycle.

Self-contained (stdlib + psycopg + Kalshi public REST). Usage:
    DATABASE_URL_RO=postgresql://... python scripts/kalshi_favbuy_study.py
    # ops channel:
    {"type":"script","name":"kalshi_favbuy_study","args":["--min-edge-c","5"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

FAV_LO, FAV_HI = 65.0, 90.0          # the thesis band (cents, yes ASK)
PRICE_BANDS = ((65.0, 75.0), (75.0, 85.0), (85.0, 90.0))
TTE_BUCKETS = ((0.0, 15.0), (15.0, 30.0), (30.0, 60.0), (60.0, 90.0))
FINAL_HOUR = 60.0
EDGE_BANDS = ((5.0, 8.0), (8.0, 12.0), (12.0, 1e9))
# theta control-book gates, for the P4 tail-sell simulation on the same snapshots
THETA_LO, THETA_HI, THETA_EDGE = 3.0, 40.0, 5.0
THETA_TTE_MIN, THETA_TTE_MAX = 10.0, 55.0
MIN_SAMPLE = 80  # banner threshold: below this, print loudly that n is too small


def taker_fee_c(p_c: float) -> float:
    """Worst-case taker fee, cents/contract (ceil per contract)."""
    return math.ceil(7.0 * (p_c / 100.0) * (1 - p_c / 100.0))


def maker_fee_ceil_c(p_c: float) -> float:
    return math.ceil(1.75 * (p_c / 100.0) * (1 - p_c / 100.0))


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


# --- data ----------------------------------------------------------------------------


def load_snapshots(conn, since: str | None) -> list[dict]:
    """All ladder snapshots (every band — favorites AND tails; P4 needs both)."""
    where = "WHERE yes_ask_cents IS NOT NULL AND yes_bid_cents IS NOT NULL"
    params: list = []
    if since:
        where += " AND captured_at >= %s"
        params.append(since)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT series, event_ticker, market_ticker, strike_type,"
            " yes_bid_cents, yes_ask_cents, mid_cents, minutes_to_close, model_p,"
            " captured_at"
            f" FROM crypto_ladder_snapshots {where}"
            " ORDER BY captured_at ASC",
            params,
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def fetch_results(series_list: list[str], pages: int) -> dict[str, int]:
    """market_ticker -> settled 1/0 (yes/no), from the Kalshi public archive."""
    out: dict[str, int] = {}
    for series in series_list:
        cursor = ""
        for _ in range(pages):
            page = xl._get(f"{KALSHI}/markets?series_ticker={series}&status=settled"
                           f"&limit=1000&cursor={cursor}")
            mkts = (page or {}).get("markets") or []
            for m in mkts:
                res = (m.get("result") or "").lower()
                tk = m.get("ticker") or ""
                if tk and res in ("yes", "no"):
                    out[tk] = 1 if res == "yes" else 0
            cursor = (page or {}).get("cursor") or ""
            if not cursor or not mkts:
                break
    return out


# --- trade construction ----------------------------------------------------------------


def first_qualifying(snaps: list[dict], qualify) -> dict[str, dict]:
    """market_ticker -> earliest snapshot passing `qualify` (snaps are time-ordered)."""
    out: dict[str, dict] = {}
    for s in snaps:
        tk = s["market_ticker"]
        if not tk or tk in out:
            continue
        if qualify(s):
            out[tk] = s
    return out


def buy_pnl_c(ask: float, won: int) -> float:
    """Taker-buy YES at the ask, hold to settlement; worst-case fee; no settle fee."""
    return (100.0 if won else 0.0) - ask - taker_fee_c(ask)


def sell_pnl_c(ask: float, won: int) -> float:
    """Maker-sell YES at the ask (theta convention), hold to settlement."""
    return ask - (100.0 if won else 0.0) - maker_fee_ceil_c(ask)


def _stats(pnls: list[float]) -> tuple[int, float, float]:
    n = len(pnls)
    if not n:
        return 0, 0.0, 0.0
    ev = sum(pnls) / n
    win = 100.0 * sum(1 for p in pnls if p > 0) / n
    return n, ev, win


def _row(label: str, pnls: list[float]) -> str:
    n, ev, win = _stats(pnls)
    return f"    {label:>22} {n:5d} {win:6.1f}% {ev:+8.2f}c"


def _corr(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3:
        return float("nan")
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else float("nan")


# --- main --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-edge-c", type=float, default=5.0,
                    help="P2 model filter: 100*P_model - ask must clear this (cents)")
    ap.add_argument("--since", default=None, help="only snapshots captured >= ISO date")
    ap.add_argument("--pages", type=int, default=8, help="settled pages (1000) per series")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        snaps = load_snapshots(conn, args.since)

    series_list = sorted({s["series"] for s in snaps if s["series"]})
    print("=== TFAV favorite-buy study (crypto_ladder_snapshots, live provenance) ===")
    print(f"  snapshots: {len(snaps)}  series: {', '.join(series_list) or '(none)'}")
    if not snaps:
        print("  NO SNAPSHOT DATA YET — the theta collector populates this table; "
              "re-run once the worker has cycled.")
        return 0

    results = fetch_results(series_list, args.pages)
    labeled = [s for s in snaps if s["market_ticker"] in results]
    ev_count = len({s["event_ticker"] for s in labeled})
    print(f"  settle-labeled snapshots: {len(labeled)} "
          f"({len(snaps) - len(labeled)} unsettled/unarchived dropped), events: {ev_count}")

    def fav(s, lo=0.0, hi=FINAL_HOUR, need_model=False, edge=None):
        tte = s["minutes_to_close"] or 0.0
        if not (lo < tte <= hi) or not (FAV_LO <= s["yes_ask_cents"] <= FAV_HI):
            return False
        if (need_model or edge is not None) and s["model_p"] is None:
            return False
        if edge is not None and (100.0 * s["model_p"] - s["yes_ask_cents"]) < edge:
            return False
        return True

    def won(s: dict) -> int:
        return results[s["market_ticker"]]

    # ---- P1: unconditional favorite-buy, final hour --------------------------------
    uncond = first_qualifying(labeled, fav)
    p1_pnls = [buy_pnl_c(s["yes_ask_cents"], won(s)) for s in uncond.values()]
    print(f"\n  --- P1 unconditional 65-90c favorite-buy at the ask (tte<= {FINAL_HOUR:.0f}m) ---")
    print(f"    {'group':>22} {'n':>5} {'win%':>7} {'EV/ct':>9}")
    print(_row("ALL final-hour", p1_pnls))
    for lo, hi in PRICE_BANDS:
        sub = [buy_pnl_c(s["yes_ask_cents"], won(s)) for s in uncond.values()
               if lo <= s["yes_ask_cents"] < hi]
        print(_row(f"ask {lo:.0f}-{hi:.0f}c", sub))
    for st in ("greater", "less", "between"):
        sub = [buy_pnl_c(s["yes_ask_cents"], won(s)) for s in uncond.values()
               if (s["strike_type"] or "") == st]
        print(_row(f"strike={st}", sub))
    # per-tte view (the already-decided-favorite artifact check: EV inflating as tte->0
    # with win% -> 100 means the favorite was already decided, not underpriced)
    for lo, hi in TTE_BUCKETS:
        m = first_qualifying(labeled, lambda s, lo=lo, hi=hi: fav(s, lo, hi))
        sub = [buy_pnl_c(s["yes_ask_cents"], won(s)) for s in m.values()]
        print(_row(f"tte {lo:.0f}-{hi:.0f}m", sub))

    # ---- P2: model-filtered favorite-buy, final hour + split-half OOS ----------------
    filt = first_qualifying(labeled, lambda s: fav(s, edge=args.min_edge_c))
    p2 = sorted(filt.values(), key=lambda s: s["event_ticker"])  # ~ by date
    p2_pnls = [buy_pnl_c(s["yes_ask_cents"], won(s)) for s in p2]
    print(f"\n  --- P2 model-filtered (100*P_model - ask >= {args.min_edge_c:.0f}c), "
          f"final-hour ---")
    print(f"    {'group':>22} {'n':>5} {'win%':>7} {'EV/ct':>9}")
    print(_row("model-filtered", p2_pnls))
    half = len(p2) // 2
    a_pnls = [buy_pnl_c(s["yes_ask_cents"], won(s)) for s in p2[:half]]
    b_pnls = [buy_pnl_c(s["yes_ask_cents"], won(s)) for s in p2[half:]]
    print(_row("date-half A", a_pnls))
    print(_row("date-half B", b_pnls))
    for lo, hi in EDGE_BANDS:
        sub = [buy_pnl_c(s["yes_ask_cents"], won(s)) for s in p2
               if lo <= (100.0 * s["model_p"] - s["yes_ask_cents"]) < hi]
        label = f"edge {lo:.0f}-{hi:.0f}c" if hi < 1e9 else f"edge {lo:.0f}c+"
        print(_row(label, sub))
    # control: model-FAIR favorites (edge < threshold) — the filter must separate
    fair = first_qualifying(
        labeled,
        lambda s: fav(s, need_model=True)
        and (100.0 * s["model_p"] - s["yes_ask_cents"]) < args.min_edge_c,
    )
    print(_row("model-fair (control)",
               [buy_pnl_c(s["yes_ask_cents"], won(s)) for s in fair.values()]))

    # ---- P3: tte structure of the model-filtered edge -------------------------------
    print("\n  --- P3 model-filtered EV by tte bucket ---")
    print(f"    {'group':>22} {'n':>5} {'win%':>7} {'EV/ct':>9}")
    p3 = {}
    for lo, hi in TTE_BUCKETS:
        m = first_qualifying(labeled,
                             lambda s, lo=lo, hi=hi: fav(s, lo, hi, edge=args.min_edge_c))
        pnls = [buy_pnl_c(s["yes_ask_cents"], won(s)) for s in m.values()]
        p3[(lo, hi)] = pnls
        print(_row(f"tte {lo:.0f}-{hi:.0f}m", pnls))

    # ---- P4: per-event P&L correlation vs the theta tail-sell rule -------------------
    def theta_sell(s):
        tte = s["minutes_to_close"] or 0.0
        mid = s["mid_cents"]
        if mid is None or s["model_p"] is None:
            return False
        return (THETA_TTE_MIN <= tte <= THETA_TTE_MAX and THETA_LO <= mid <= THETA_HI
                and (mid - 100.0 * s["model_p"]) >= THETA_EDGE)

    theta_trades = first_qualifying(labeled, theta_sell)
    fav_by_event: dict[str, list[float]] = defaultdict(list)
    theta_by_event: dict[str, list[float]] = defaultdict(list)
    for s in filt.values():
        fav_by_event[s["event_ticker"]].append(buy_pnl_c(s["yes_ask_cents"], won(s)))
    for s in theta_trades.values():
        theta_by_event[s["event_ticker"]].append(sell_pnl_c(s["yes_ask_cents"], won(s)))
    common = sorted(set(fav_by_event) & set(theta_by_event))
    xs = [sum(fav_by_event[e]) / len(fav_by_event[e]) for e in common]
    ys = [sum(theta_by_event[e]) / len(theta_by_event[e]) for e in common]
    corr = _corr(xs, ys)
    print(f"\n  --- P4 per-event P&L correlation: tfav (n_ev={len(fav_by_event)}) vs "
          f"theta-rule sim (n_ev={len(theta_by_event)}) ---")
    print(f"    overlapping events: {len(common)}  corr={corr:+.3f}"
          if corr == corr else
          f"    overlapping events: {len(common)}  corr=n/a (need >= 3 overlapping events)")

    # ---- verdicts --------------------------------------------------------------------
    print("\n  === PRE-REGISTERED VERDICTS (docs/IDEA_MODEL_20260704.md — TFAV) ===")
    small = len(p2_pnls) < MIN_SAMPLE
    if small:
        print(f"  *** SAMPLE TOO SMALL (model-filtered n={len(p2_pnls)} < {MIN_SAMPLE}) — "
              "verdicts below are PROVISIONAL; keep collecting before acting. ***")
    if p1_pnls:
        _, ev1, _ = _stats(p1_pnls)
        v1 = ("CONSISTENT" if ev1 <= 1.0
              else "SUSPICIOUS (check tte table for the already-decided artifact)")
        print(f"  P1 unconditional ~0: EV={ev1:+.2f}c (expect <= +1) -> {v1}")
    if p2_pnls:
        _, ev2, _ = _stats(p2_pnls)
        _, eva, _ = _stats(a_pnls)
        _, evb, _ = _stats(b_pnls)
        halves_agree = (eva > 0) == (evb > 0)
        if ev2 >= 3.0 and eva > 0 and evb > 0:
            v = "PASS"
        elif ev2 < 1.0 or not halves_agree:
            v = "KILL"
        else:
            v = "WEAK (between thresholds)"
        print(f"  P2 model-filtered >= +3c AND halves agree: EV={ev2:+.2f}c "
              f"(A={eva:+.2f} B={evb:+.2f}) -> {v}")
    fh = p3.get((0.0, 15.0), []) + p3.get((15.0, 30.0), []) + p3.get((30.0, 60.0), [])
    out60 = p3.get((60.0, 90.0), [])
    if fh and out60:
        _, evf, _ = _stats(fh)
        _, evo, _ = _stats(out60)
        print(f"  P3 final-hour > beyond-60m: {evf:+.2f}c vs {evo:+.2f}c -> "
              f"{'PASS' if evf > evo else 'FAIL'}")
    else:
        print("  P3: not enough data in one of the tte groups yet")
    if corr == corr:
        v = "PASS (own book)" if abs(corr) < 0.4 else (
            "DEMOTE to theta feature" if abs(corr) > 0.7 else "GREY ZONE (0.4-0.7)")
        print(f"  P4 |corr| < 0.4 vs theta: corr={corr:+.3f} -> {v}")
    else:
        print("  P4: insufficient overlapping events to compute correlation")
    print("  Decision rule: paper book `tfav` only if P2 AND P4; P2 pass/P4 fail -> fold "
          "into theta gating; P2 fail -> close the favorite side.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
