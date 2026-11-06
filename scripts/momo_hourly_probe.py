"""MOMO-H probe: does the hourly BTC ladder's quote under- or over-react to recent price action?

Pre-registration: docs/MOMO_HOURLY_PREREG.md (P0-P2, KILL, amendment A1). Constants below
are copied from it; tests pin them.

Read-only. DATABASE_URL_RO (crypto_ladder_snapshots, one day per query) + Kalshi PUBLIC REST
for each market's official settlement result, live tier then historical tier
(scripts/kalshi_settled.py). No orders, no writes.

Usage (ops channel):
    {"type":"script","name":"momo_hourly_probe","args":["--start","2026-08-01"]}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalshi_settled as ks  # noqa: E402

# --- pre-registered constants (docs/MOMO_HOURLY_PREREG.md) -------------------------------
SERIES = "KXBTCD"
MTC_LO, MTC_HI, MTC_TARGET = 20.0, 40.0, 30.0
MID_LO, MID_HI = 15.0, 85.0
LOOKBACK_LO_MIN, LOOKBACK_HI_MIN = 12, 18
P0_MIN_DAYS, P0_MIN_DECISIONS, P0_MAX_EXCLUSION = 40, 1000, 0.10
P1_SIGNAL_CENTS, KILL_CENTS = 3.0, 1.5
P2_NET_EV_CENTS = 1.5
FEE_QTY = 10
BOOT_N, BOOT_SEED = 2000, 20261106
RECON_MIN_CORR = 0.9
SENS_YES, SENS_NO = 97.0, 3.0

_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

DAY_SQL = """
with s as materialized (
  select event_ticker ev, market_ticker mk, captured_at t, minutes_to_close mtc, mid_cents mid,
         yes_ask_cents ask, yes_bid_cents bid, spot, trailing_move_15m tm15
  from crypto_ladder_snapshots
  where event_ticker = any(%(evs)s) and strike_type = 'greater'),
caps as (select ev, t, max(spot) spot from s where spot is not null group by ev, t),
dec as (select distinct on (ev, mk) ev, mk, t, mtc, mid, ask, bid, tm15 from s
        where mtc between %(mtc_lo)s and %(mtc_hi)s and mid between %(mid_lo)s and %(mid_hi)s
        order by ev, mk, abs(mtc - %(mtc_t)s), t),
fin as (select distinct on (mk) mk, mid fmid, mtc fmtc from s where mtc <= 2 order by mk, t desc)
select d.ev, d.mk, d.t, d.mid, d.ask, d.bid, d.tm15, c.spot,
       (select c2.spot from caps c2 where c2.ev = d.ev
          and c2.t between d.t - make_interval(mins => %(lb_hi)s) and d.t - make_interval(mins => %(lb_lo)s)
        order by abs(extract(epoch from (d.t - c2.t)) - 900) limit 1) as s15,
       f.fmid
from dec d join caps c on c.ev = d.ev and c.t = d.t left join fin f on f.mk = d.mk
"""


# --- pure functions (tested) ------------------------------------------------------------------

def fee_cents(price_cents: float, qty: int = FEE_QTY) -> float:
    """Kalshi taker fee per contract: ceil(0.07*q*P*(1-P)*100)/q cents."""
    p = min(max(price_cents / 100.0, 0.0), 1.0)
    return math.ceil(round(0.07 * qty * p * (1 - p) * 100, 9)) / qty


def day_event_tickers(series: str, day: dt.date) -> list[str]:
    """The 24 hourly event tickers of one ET calendar date, e.g. KXBTCD-26OCT1500 .. KXBTCD-26OCT1523.

    Exact tickers, never a string range: production Postgres collates `en_US.utf8`, which ignores
    punctuation at the first comparison level, so `>= 'X-26OCT15' AND < 'X-26OCT15~'` matched
    NOTHING there (the first ops run, `rl-momo-run-1106m`, read 0 rows on every day). Equality is
    collation-independent and still uses the event_ticker index."""
    tag = f"{day:%y}{_MONTHS[day.month - 1]}{day:%d}"
    return [f"{series}-{tag}{h:02d}" for h in range(24)]


def momentum_bp(spot_now: float | None, spot_then: float | None) -> float | None:
    if not spot_now or not spot_then or spot_now <= 0 or spot_then <= 0:
        return None
    return 10000.0 * math.log(spot_now / spot_then)


def quintile_edges(values: list[float]) -> list[float]:
    v = sorted(values)
    n = len(v)
    return [v[min(n - 1, int(n * k / 5))] for k in (1, 2, 3, 4)]


def bucket(x: float, edges: list[float]) -> int:
    for i, e in enumerate(edges):
        if x < e:
            return i
    return 4


def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def leg_ev(row: dict, side: str) -> float:
    """Net EV (cents/contract) of a taker buy held to settlement. side='yes' buys YES at ask;
    side='no' buys NO at 100-bid."""
    y = row["y"]
    if side == "yes":
        px = row["ask"]
        return 100.0 * y - px - fee_cents(px)
    px = 100.0 - row["bid"]
    return 100.0 * (1 - y) - px - fee_cents(px)


def separation(rows: list[dict]) -> float:
    top = [100.0 * r["y"] - r["mid"] for r in rows if r["q"] == 4]
    bot = [100.0 * r["y"] - r["mid"] for r in rows if r["q"] == 0]
    if not top or not bot:
        return float("nan")
    return _mean(top) - _mean(bot)


def trade_ev(rows: list[dict], sign: int) -> float:
    """Pooled net EV of the trade implied by sign(S): +1 follows momentum, -1 fades it."""
    evs = []
    for r in rows:
        if r["q"] == 4:
            evs.append(leg_ev(r, "yes" if sign > 0 else "no"))
        elif r["q"] == 0:
            evs.append(leg_ev(r, "no" if sign > 0 else "yes"))
    return _mean(evs) if evs else float("nan")


def day_bootstrap(rows: list[dict], stat, n: int = BOOT_N, seed: int = BOOT_SEED) -> list[float]:
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r)
    days = sorted(by_day)
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        sample = []
        for _d in days:
            sample.extend(by_day[days[rng.randrange(len(days))]])
        v = stat(sample)
        if not math.isnan(v):
            out.append(v)
    out.sort()
    return out


def pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    return sorted_vals[min(len(sorted_vals) - 1, max(0, int(p * len(sorted_vals))))]


def corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = _mean(xs), _mean(ys)
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys, strict=True))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else float("nan")


def verdict(n_days: int, n_dec: int, exclusion: float, recon: float, s: float, ci_lo: float,
            ci_hi: float, ev: float, ev_p5: float) -> str:
    if n_days < P0_MIN_DAYS or n_dec < P0_MIN_DECISIONS or exclusion > P0_MAX_EXCLUSION:
        return "HOLD (instrument: P0 data floor)"
    if not math.isnan(recon) and recon < RECON_MIN_CORR:
        return "HOLD (instrument: momentum reconstruction)"
    ci_excludes_zero = ci_lo > 0 or ci_hi < 0
    if abs(s) < KILL_CENTS or not ci_excludes_zero:
        return "KILL (momentum is priced)"
    if abs(s) < P1_SIGNAL_CENTS:
        return "HOLD (weak signal: re-read at 40 native-telemetry days)"
    if ev >= P2_NET_EV_CENTS and ev_p5 > 0:
        return "PASS (P1+P2) - authorizes a paper-package draft only"
    return "P1 SIGNAL / P2 FAIL (real but not tradeable after cost)"


def label_tally(raw: list[dict], results: dict[str, int], ev_source: dict[str, str]) -> ks.Tally:
    """Decision rows by where their label came from. A row whose market has no yes/no result is
    `not found` whatever tier answered for its event, so the line adds up to len(raw)."""
    tally = ks.Tally()
    for r in raw:
        tally.add(ev_source.get(r["ev"], ks.NOT_FOUND) if r["mk"] in results else ks.NOT_FOUND)
    return tally


def split_rows(raw: list[dict], results: dict[str, int]) -> tuple[list[dict], int, int]:
    """(usable, no_label, dropped). P0's exclusion counts ONLY label failures (amendment A1);
    rows lacking a 15-min lookback or a two-sided quote are dropped and reported separately."""
    usable, no_label, dropped = [], 0, 0
    for r in raw:
        if r["mk"] not in results:
            no_label += 1
        elif r["m"] is None or r["ask"] is None or r["bid"] is None:
            dropped += 1
        else:
            r["y"] = results[r["mk"]]
            usable.append(r)
    return usable, no_label, dropped


def analyse(rows: list[dict], n_total: int, n_excluded: int) -> dict:
    """rows: decisions with m (bp), mid, ask, bid, y (0/1), day, fmid, tm15."""
    edges = quintile_edges([r["m"] for r in rows])
    for r in rows:
        r["q"] = bucket(r["m"], edges)
    s = separation(rows)
    boot_s = day_bootstrap(rows, separation)
    sign = 1 if s >= 0 else -1
    ev = trade_ev(rows, sign)
    boot_ev = day_bootstrap(rows, lambda rs: trade_ev(rs, sign))
    paired = [(r["m"], r["tm15"]) for r in rows if r.get("tm15") is not None]
    recon = corr([a for a, _ in paired], [b for _, b in paired]) if len(paired) >= 30 else float("nan")
    per_q = []
    for q in range(5):
        qs = [r for r in rows if r["q"] == q]
        per_q.append({
            "q": q + 1, "n": len(qs),
            "m_bp": round(_mean([r["m"] for r in qs]), 2),
            "mid": round(_mean([r["mid"] for r in qs]), 2),
            "yes_rate": round(100 * _mean([r["y"] for r in qs]), 2),
            "resid": round(_mean([100 * r["y"] - r["mid"] for r in qs]), 2),
            "ev_yes_ask": round(_mean([leg_ev(r, "yes") for r in qs]), 2),
            "ev_no_bid": round(_mean([leg_ev(r, "no") for r in qs]), 2),
        })
    sens = [r for r in rows if r.get("fmid") is not None
            and (r["fmid"] >= SENS_YES or r["fmid"] <= SENS_NO)]
    sens_agree = _mean([1.0 if (r["fmid"] >= SENS_YES) == (r["y"] == 1) else 0.0 for r in sens])
    for r in sens:
        r["y_s"] = 1 if r["fmid"] >= SENS_YES else 0
    sens_s = (_mean([100 * r["y_s"] - r["mid"] for r in sens if r["q"] == 4])
              - _mean([100 * r["y_s"] - r["mid"] for r in sens if r["q"] == 0])) if sens else float("nan")
    days = {r["day"] for r in rows}
    exclusion = n_excluded / n_total if n_total else 1.0
    out = {
        "n_days": len(days), "n_decisions": len(rows), "n_total": n_total,
        "exclusion": round(exclusion, 4), "edges_bp": [round(e, 2) for e in edges],
        "S": round(s, 3), "S_ci90": [round(pct(boot_s, 0.05), 3), round(pct(boot_s, 0.95), 3)],
        "trade_sign": "follow" if sign > 0 else "fade",
        "trade_ev": round(ev, 3), "trade_ev_p5": round(pct(boot_ev, 0.05), 3),
        "recon_corr": None if math.isnan(recon) else round(recon, 4), "recon_n": len(paired),
        "sens_n": len(sens), "sens_label_agreement": round(sens_agree, 4) if sens else None,
        "sens_S": None if math.isnan(sens_s) else round(sens_s, 3),
        "per_quintile": per_q,
    }
    out["verdict"] = verdict(out["n_days"], len(rows), exclusion, recon, s,
                             out["S_ci90"][0], out["S_ci90"][1], ev, out["trade_ev_p5"])
    return out


# --- IO -------------------------------------------------------------------------------------

def event_results(event_ticker: str, tickers=()) -> tuple[dict[str, int], str]:
    """({market: 1|0}, source). Live first, then Kalshi's historical tier: settled markets older
    than the historical cutoff are not served by live /markets (run 2 lost 29.6% of its labels
    to that with zero errors; docs/handoffs/HANDOFF-kalshi-historical-cutoff.md)."""
    markets, source = ks.event_markets(event_ticker, tickers)
    out: dict[str, int] = {}
    for m in markets:
        res = ks.result_of(m)
        if res is not None:
            out[m["ticker"]] = 1 if res == "yes" else 0
    return out, source


def _db_url() -> str:
    url = (os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url:
        raise SystemExit("DATABASE_URL_RO (or DATABASE_URL) is not set.")
    return url


def load_day(cur, day: dt.date) -> list[dict]:
    cur.execute(DAY_SQL, {"evs": day_event_tickers(SERIES, day), "mtc_lo": MTC_LO, "mtc_hi": MTC_HI,
                          "mid_lo": MID_LO, "mid_hi": MID_HI, "mtc_t": MTC_TARGET,
                          "lb_lo": LOOKBACK_LO_MIN, "lb_hi": LOOKBACK_HI_MIN})
    rows = []
    for ev, mk, _t, mid, ask, bid, tm15, s0, s15, fmid in cur.fetchall():
        rows.append({"ev": ev, "mk": mk, "day": day.isoformat(), "mid": float(mid),
                     "ask": float(ask) if ask is not None else None,
                     "bid": float(bid) if bid is not None else None,
                     "tm15": float(tm15) if tm15 is not None else None,
                     "m": momentum_bp(s0, s15),
                     "fmid": float(fmid) if fmid is not None else None})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2026-08-01")
    ap.add_argument("--end", default="", help="inclusive ET date; default yesterday (UTC)")
    ap.add_argument("--max-days", type=int, default=120)
    args = ap.parse_args(argv)
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    import psycopg

    raw: list[dict] = []
    opts = "-c default_transaction_read_only=on -c statement_timeout=120000"
    with psycopg.connect(_db_url(), options=opts, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            day, n = start, 0
            while day <= end and n < args.max_days:
                got = load_day(cur, day)
                raw.extend(got)
                print(f"day {day}: {len(got)} decision rows", file=sys.stderr)
                day += dt.timedelta(days=1)
                n += 1
    results: dict[str, int] = {}
    ev_source: dict[str, str] = {}
    fetch_errors = 0
    by_ev: dict[str, set[str]] = defaultdict(set)
    for r in raw:
        by_ev[r["ev"]].add(r["mk"])
    for ev in sorted(by_ev):
        try:
            got, ev_source[ev] = event_results(ev, by_ev[ev])
            results.update(got)
        except Exception as exc:  # noqa: BLE001 - an unreadable event is counted as excluded
            fetch_errors += 1
            ev_source[ev] = ks.NOT_FOUND
            print(f"result fetch failed {ev}: {exc}", file=sys.stderr)
        time.sleep(0.06)
    tally = label_tally(raw, results, ev_source)
    usable, no_label, dropped = split_rows(raw, results)
    print(f"# MOMO-H probe - series {SERIES}, {start}..{end} (docs/MOMO_HOURLY_PREREG.md)")
    print(f"raw decisions {len(raw)}, usable {len(usable)}, no official label {no_label} "
          f"(P0 exclusion), dropped for missing lookback/quote {dropped}")
    ks.report(tally)
    print(f"events {len(by_ev)}, result fetch errors {fetch_errors}")
    if len(usable) < 50:
        print("VERDICT: HOLD (instrument: P0 data floor) - too few usable rows to analyse")
        return 0
    out = analyse(usable, len(raw), no_label)
    print(json.dumps({k: v for k, v in out.items() if k != "per_quintile"}, indent=1))
    print("quintile  n      m_bp    mid    yes%   resid  EV_yes@ask  EV_no@bid")
    for q in out["per_quintile"]:
        print(f"Q{q['q']}       {q['n']:<6} {q['m_bp']:>7} {q['mid']:>6} {q['yes_rate']:>6} "
              f"{q['resid']:>6} {q['ev_yes_ask']:>10} {q['ev_no_bid']:>10}")
    print(f"VERDICT: {out['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
