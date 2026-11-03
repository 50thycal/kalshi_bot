"""LIMM STEP-ASIDE — does quoting the 48h->24h band before close pay, net of its fills?

Pre-registered in docs/LIMM_STEP_ASIDE.md (2026-11-03, study 6). Every constant marked
"pre-registered" is frozen there; changing one is a new probe.

Study 5's construction (scripts/limm_fill_hazard.py, itself study 3's hours/size/reward) is
imported unchanged; only the hour filter moves: BAND = 24h <= time to close < 48h, BASE = >= 48h
(today's window, reported for comparison only). The `live` pool decides.

Read-only: DATABASE_URL_RO (read-only transaction). Stdlib + psycopg.

Usage: {"type":"script","name":"limm_step_aside","args":[],"id":"rl-s6-run1-<date>"}
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from limm_fill_hazard import (  # noqa: E402  (study 5 construction, unchanged)
    DECIDING,
    MARK_HOURS,
    MARK_SEARCH,
    MODELS,
    SNAP_SQL,
    TRADE_SQL,
    ratio_cluster,
    run_market,
    trade_time,
)
from limm_programme_outcomes import (  # noqa: E402
    THIN_SHARE,
    Z,
    _aware,
    cheap_side,
    hourly_reward,
    in_live_universe,
    live_qty,
)

from kalshi_bot.liquidity_incentive.fills import MODEL_OPTIMISTIC  # noqa: E402

# --- pre-registered constants ----------------------------------------------------------------
SINCE = "2026-11-03T03:00:00+00:00"
UNTIL = "2026-11-17T15:00:00+00:00"
BAND_LO_H, BAND_HI_H = 24.0, 48.0
MIN_BAND_MARKETS, MIN_BAND_EVENTS = 20, 8
POOLS = ("live", "mention", "youtube", "all")


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_step_aside.py)


def window_of(row: dict) -> str | None:
    """'band' | 'base' | None for one snapshot hour. Study 3's qualifying conditions, except the
    time-to-close cut, which splits instead of excluding."""
    hr, start, end, close = (_aware(row.get(k)) for k in ("hr", "start", "end", "close"))
    if not (row.get("yes_meets") and row.get("no_meets")):
        return None
    if "liquid" not in (row.get("itype") or "").lower():
        return None
    if hr is None or start is None or end is None or not (start <= hr < end) or close is None:
        return None
    h = (close - hr).total_seconds() / 3600.0
    if BAND_LO_H <= h < BAND_HI_H:
        return "band"
    if h >= BAND_HI_H:
        return "base"
    return None


def pools_of(series: str) -> list[str]:
    s = (series or "").upper()
    out = ["all"]
    if in_live_universe(s):
        out.append("live")
        out.append("youtube" if s == "KXYTVIEWSHIGH" else "mention")
    return out


def verdict(est: dict | None, markets: int, events: int) -> str:
    if est is None or markets < MIN_BAND_MARKETS or events < MIN_BAND_EVENTS:
        return "HOLD (accrual)"
    if math.isnan(est["se"]):
        return "HOLD"
    if est["R"] - Z * est["se"] > 0:
        return "NOMINATE 24H"
    if est["R"] + Z * est["se"] < 0:
        return "KEEP 48H"
    return "HOLD"


# ---------------------------------------------------------------------------------------------


def _f(x, spec="{:+.2f}"):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else spec.format(x)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--until", default=UNTIL)
    args = ap.parse_args(argv)
    since, until = _aware(args.since), _aware(args.until)
    mark_until = until + dt.timedelta(hours=2 + MARK_HOURS + max(MARK_SEARCH))
    trade_until = until + dt.timedelta(hours=2)

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    print("LIMM step-aside — the 48h->24h band before close, reward minus fill markout "
          "(pre-registered docs/LIMM_STEP_ASIDE.md)\n")
    opts = RO_OPTIONS.replace("statement_timeout=300000", "statement_timeout=1500000")
    with psycopg.connect(url, options=opts) as conn, conn.cursor() as cur:
        cur.execute(SNAP_SQL, {"since": since, "mark_until": mark_until})
        snaps = cur.fetchall()
        cur.execute(TRADE_SQL, {"since": since, "trade_until": trade_until})
        trades = cur.fetchall()
    print(f"window {since.isoformat()[:16]} -> {until.isoformat()[:16]}; hourly snapshots "
          f"(incl. mark tail) {len(snaps)}; prints {len(trades)}")

    marks: dict[str, dict[dt.datetime, tuple]] = defaultdict(dict)
    hours: dict[tuple[str, str], list[dict]] = defaultdict(list)   # (window, ticker)
    meta: dict[str, tuple[str, str]] = {}
    for (tk, hr, yb, nb, yd, nd, ys, ns, ym, nm, series, event, itype, pool, start, end,
         close) in snaps:
        hr = _aware(hr)
        marks[tk][hr] = (yb, nb)
        if not (since <= hr < until):
            continue
        w = window_of({"hr": hr, "start": start, "end": end, "close": close, "yes_meets": ym,
                       "no_meets": nm, "itype": itype})
        cs = cheap_side(yb, nb) if w else None
        if cs is None:
            continue
        side, price = cs
        qty = live_qty(price)
        hrw = hourly_reward(pool, start, end, qty, ys if side == "yes" else ns)
        reward = None if hrw is None or hrw[1] > THIN_SHARE else hrw[0]
        meta[tk] = ((series or tk.split("-", 1)[0]).upper(), event or tk.rsplit("-", 1)[0])
        hours[(w, tk)].append({"hr": hr, "side": side, "price": price, "qty": qty,
                               "depth": yd if side == "yes" else nd, "reward": reward})
    tickers = {tk for _w, tk in hours}
    tbh: dict[str, dict[dt.datetime, list]] = defaultdict(lambda: defaultdict(list))
    for tk, ts_ms, rcv, yp, cnt, taker in trades:
        if tk not in tickers:
            continue
        at = trade_time(ts_ms, rcv)
        if at is not None:
            tbh[tk][at.replace(minute=0, second=0, microsecond=0)].append((at, yp, cnt, taker))
    nb = sum(1 for w, _ in hours if w == "band")
    print(f"markets with band hours {nb}; with base hours {len(hours) - nb}; "
          f"markets with prints {sum(1 for t in tickers if t in tbh)}")

    res: dict[str, dict] = {}
    for model in MODELS:
        agg = {(w, p): {"markets": set(), "reward": 0.0, "loss": 0.0, "fills": 0,
                        "events": defaultdict(lambda: [0.0, 0])}
               for w in ("band", "base") for p in POOLS}
        for (w, tk), hs in hours.items():
            series, event = meta[tk]
            r = run_market(hs, tbh.get(tk, {}), marks.get(tk, {}), model)
            days = len({h["hr"].date() for h in hs})
            for p in pools_of(series):
                a = agg[(w, p)]
                a["markets"].add(tk)
                a["reward"] += r["reward"]
                a["loss"] += r["loss"]
                a["fills"] += r["fills"]
                ev = a["events"][event]
                ev[0] += r["reward"] - r["loss"]
                ev[1] += days
        res[model] = {k: {"markets": len(a["markets"]), "events": len(a["events"]),
                          "reward": a["reward"], "loss": a["loss"], "fills": a["fills"],
                          "est": ratio_cluster({e: (v[0], v[1])
                                                for e, v in a["events"].items()})}
                      for k, a in agg.items()}

    dec, opt = res[DECIDING], res[MODEL_OPTIMISTIC]
    print(f"\n== pools (deciding model: {DECIDING}; R = net USD per market-day, ±se clustered "
          "by event)")
    print(f"  {'pool':8s} {'window':6s} {'mkts':>5} {'evts':>5} {'reward':>8} {'fill $':>8} "
          f"{'fills':>5} {'R':>7} {'±se':>5} {'R opt':>7}")
    for p in POOLS:
        for w in ("band", "base"):
            v, o = dec[(w, p)], opt[(w, p)]
            e, eo = v["est"] or {}, o["est"] or {}
            print(f"  {p:8s} {w:6s} {v['markets']:5d} {v['events']:5d} {v['reward']:8.2f} "
                  f"{v['loss']:8.2f} {v['fills']:5d} {_f(e.get('R')):>7} "
                  f"{_f(e.get('se'), '{:.2f}'):>5} {_f(eo.get('R')):>7}")

    lv, lo = dec[("band", "live")], opt[("band", "live")]
    vd = verdict(lv["est"], lv["markets"], lv["events"])
    vo = verdict(lo["est"], lo["markets"], lo["events"])
    print("\n== VERDICT (pool `live`, band; descriptive — a nomination is an OWNER decision)")
    print(f"  {vd}{'  (model-sensitive: optimistic reads ' + vo + ')' if vo != vd else ''}")
    al = dec[("band", "all")]
    print(f"  `all` band, descriptive only: {verdict(al['est'], al['markets'], al['events'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
