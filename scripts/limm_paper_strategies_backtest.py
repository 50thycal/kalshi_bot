"""LIMM PAPER STRATEGIES BACKTEST — S2, S3 and S4 replayed on history (Part A, descriptive).

Pre-registered in docs/LIMM_PAPER_STRATEGIES_BACKTEST.md (2026-11-08). Decides nothing: the
forward study's bars (docs/LIMM_PAPER_STRATEGIES.md) are fixed and this does not move them.

Replays the forward arms' own code hour by hour — `paper_arms.ArmSelector` for selection,
placement, sizes and pulls — on hourly collector snapshots, replays fills against the public
trade tape with `ShadowLeg` (four fill models), and scores with the forward read script's own
`analyse()` (calibration, Kalshi's $1 minimum, 24 h markout, clustered SE, labels, pairwise).

Read-only: DATABASE_URL_RO (read-only transaction). Stdlib + psycopg.

Usage: {"type":"script","name":"limm_paper_strategies_backtest","args":[],"id":"rl-psbt-run1-<date>"}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import limm_paper_strategies_read as rd  # noqa: E402  (the forward read's scoring, unchanged)
from limm_fill_hazard import trade_time  # noqa: E402

from kalshi_bot.liquidity_incentive import fills as fm  # noqa: E402
from kalshi_bot.liquidity_incentive import paper_arms as pa  # noqa: E402

# --- pre-registered constants (docs/LIMM_PAPER_STRATEGIES_BACKTEST.md) -----------------------
SINCE = "2026-09-08T00:00:00+00:00"
#: Run 1 stops where study 5's window opens: nothing in it is replayed before study 5 reads.
RUN1_UNTIL = "2026-10-30T16:00:00+00:00"
#: Run 2 may extend to here, never further: the forward arms started recording at 15:41Z.
MAX_UNTIL = "2026-11-08T15:00:00+00:00"
#: ...and only once study 5 has read.
RUN2_NOT_BEFORE = "2026-11-14T00:00:00+00:00"
#: BLS State Employment releases inside the backtest window (S3-aside pulls around them).
HIST_BLS_RELEASES = (dt.datetime(2026, 9, 18, 14, 0, tzinfo=dt.timezone.utc),)
ARMS = tuple(a for a in pa.ARMS if a != pa.ARM_S1)          # S1 has no history to replay
MARK_HOURS = 24
MARK_SEARCH_HOURS = (0, -1, 1, -2, 2)
FILLED_HOUR_REWARD_FRACTION = 0.5

SNAP_SQL = """
WITH s AS (
  SELECT DISTINCT ON (market_ticker, date_trunc('hour', at))
         market_ticker, date_trunc('hour', at) AS hr, program_row_id, best_yes_bid, best_no_bid,
         est_yes_score_total, est_no_score_total, est_yes_meets_target, est_no_meets_target,
         yes_levels_json, no_levels_json
    FROM incentive_market_snapshots
   WHERE at >= %(since)s AND at < %(until)s AND book_valid
   ORDER BY market_ticker, date_trunc('hour', at), at DESC
)
SELECT s.market_ticker, s.hr, s.best_yes_bid, s.best_no_bid, s.est_yes_score_total,
       s.est_no_score_total, s.est_yes_meets_target, s.est_no_meets_target, s.yes_levels_json,
       s.no_levels_json, p.series_ticker, p.event_ticker, p.incentive_type, p.period_reward_usd,
       p.start_date, p.end_date, p.close_time, p.discount_factor_bps
  FROM s LEFT JOIN incentive_programs p ON p.id = s.program_row_id
"""

TRADE_SQL = """
SELECT market_ticker, ts_ms, received_at, yes_price_cents, count_fp, taker_outcome_side
  FROM incentive_trade_events
 WHERE received_at >= %(since)s AND received_at < %(until)s
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_paper_strategies_backtest.py)


def _aware(x):
    return rd._aware(x)


def check_window(until: dt.datetime, now: dt.datetime) -> str | None:
    """None when the window is allowed, else the refusal."""
    if until > _aware(MAX_UNTIL):
        return f"--until past {MAX_UNTIL}: that is the forward arms' window"
    if until > _aware(RUN1_UNTIL) and now < _aware(RUN2_NOT_BEFORE):
        return (f"--until past {RUN1_UNTIL} reads study 5's window; allowed from "
                f"{RUN2_NOT_BEFORE} (after study 5 reads)")
    return None


def _levels(raw) -> dict[int, float]:
    out: dict[int, float] = {}
    for item in raw or []:
        try:
            out[int(item[0])] = out.get(int(item[0]), 0.0) + float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def pool_per_hour(pool, start, end) -> float | None:
    start, end = _aware(start), _aware(end)
    if pool is None or start is None or end is None or end <= start:
        return None
    return float(pool) / ((end - start).total_seconds() / 3600.0)


def side_reward(pool_h: float | None, qty: float, field, mult: float = 1.0) -> tuple[float, float]:
    """(reward USD per hour, share) — study 3's estimator, `mult` < 1 one tick behind."""
    if pool_h is None or qty <= 0:
        return 0.0, 0.0
    w = qty * mult
    f = max(0.0, float(field or 0.0))
    share = w / (f + w) if (f + w) > 0 else 0.0
    return pool_h * share / 2.0, share


def candidate(row: dict) -> pa.Candidate:
    pool_h = pool_per_hour(row["pool"], row["start"], row["end"])
    qualifies = bool(row["ym"] and row["nm"])

    def sv(price, field):
        if price is None or not (1 <= int(price) <= 99):
            return pa.SideView(price=None if price is None else int(price))
        q = pa.contracts(10, int(price), 1000)
        rate, share = side_reward(pool_h, q, field)
        return pa.SideView(price=int(price), rate10_per_hour_usd=rate if qualifies else 0.0,
                           qualifies10=qualifies, share10=share)

    return pa.Candidate(
        ticker=row["tk"], event=pa.event_of(row["tk"], row["event"]),
        series=pa.series_of(row["tk"], row["series"]), at=row["hr"], start=_aware(row["start"]),
        end=_aware(row["end"]), close_time=_aware(row["close"]),
        pool_day_usd=None if pool_h is None else pool_h * 24.0,
        yes=sv(row["yb"], row["fy"]), no=sv(row["nb"], row["fn"]))


def desired(sel: pa.ArmSelector, arm: str, s: pa.Selection, c: pa.Candidate, hr: dt.datetime):
    """The forward rule, plus the in-window BLS release for S3-aside."""
    if (arm == pa.ARM_S3_ASIDE and pa.s3_kind(c.series) == "bls"
            and pa.bls_pulled(hr, sel.rules, releases=HIST_BLS_RELEASES)):
        return pa.Pull(pa.PULL_BLS)
    return sel.desired(arm, s, c, hr)


def simulate_fill(side: str, price: int, qty: int, ahead: float, trades) -> dict[str, float]:
    yes_scale = price if side == "yes" else 100 - price
    leg = fm.ShadowLeg(side, yes_scale, float(qty), dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc),
                       float(ahead or 0.0), models=fm.ARM_FILL_MODELS)
    for at, yp, cnt, taker in sorted(trades, key=lambda t: t[0]):
        leg.on_trade(yes_price_cents=yp, count=float(cnt or 0), taker_outcome_side=taker, at=at)
    return {m: leg.filled.get(m, 0.0) for m in fm.ARM_FILL_MODELS}


def mark_bid(book_by_hour: dict, side: str, hr: dt.datetime) -> int | None:
    base = hr + dt.timedelta(hours=1 + MARK_HOURS)
    for off in MARK_SEARCH_HOURS:
        snap = book_by_hour.get(base + dt.timedelta(hours=off))
        if snap is not None:
            b = snap[0] if side == "yes" else snap[1]
            return 0 if b is None else int(b)
    return None


def build_rows(snaps) -> tuple[list[dict], dict]:
    rows, books = [], defaultdict(dict)
    for (tk, hr, yb, nb, fy, fn, ym, nm, ylv, nlv, series, event, itype, pool, start, end,
         close, disc) in snaps:
        hr = _aware(hr)
        books[tk][hr] = (yb, nb)
        if "liquid" not in (itype or "").lower():
            continue
        if isinstance(ylv, str):
            ylv = json.loads(ylv)
        if isinstance(nlv, str):
            nlv = json.loads(nlv)
        rows.append({"tk": tk, "hr": hr, "yb": yb, "nb": nb, "fy": fy, "fn": fn, "ym": ym,
                     "nm": nm, "ylv": _levels(ylv), "nlv": _levels(nlv), "series": series,
                     "event": event, "pool": pool, "start": start, "end": end, "close": close,
                     "disc": disc})
    return rows, books


def replay(rows: list[dict], books: dict, trades_by_hour: dict) -> tuple[list, list, list, list]:
    """Hour by hour through the forward rules. Returns read-script tuples:
    (quotes, outcomes, marks, hours)."""
    sel = pa.ArmSelector()
    by_hour: dict[dt.datetime, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        by_hour[r["hr"]][r["tk"]] = r
    quotes, outcomes, marks, hours = [], [], [], []
    qid = 0
    for hr in sorted(by_hour):
        cands = {tk: candidate(r) for tk, r in by_hour[hr].items()}
        sel.select(cands, hr)
        for strategy, chosen in sel.selected.items():
            for tk, s in list(chosen.items()):
                c, r = cands.get(tk), by_hour[hr].get(tk)
                if c is None or r is None:
                    continue
                sel.observe(s, c)
                for arm in pa.ARMS_BY_STRATEGY[strategy]:
                    if arm not in ARMS:
                        continue
                    q = desired(sel, arm, s, c, hr)
                    if isinstance(q, pa.Pull):
                        continue
                    qid += 1
                    best = c.side(q.side).price
                    field = r["fy"] if q.side == "yes" else r["fn"]
                    disc = pa_discount(r["disc"])
                    mult = disc ** max(0, best - q.price) if q.price > 1 else 1.0
                    qualifies = bool(r["ym"] and r["nm"])
                    reward, share = side_reward(pool_per_hour(r["pool"], r["start"], r["end"]),
                                                q.qty, field, mult)
                    thin = share > pa.DEFAULT_RULES.thin_share
                    credited = reward if (qualifies and not thin) else 0.0
                    if arm == pa.ARM_S4_BURST:
                        sel.add_burst_reward(tk, s.programme_start, credited)
                    levels = r["ylv"] if q.side == "yes" else r["nlv"]
                    ahead = levels.get(q.price, 0.0)
                    got = simulate_fill(q.side, q.price, q.qty, ahead,
                                        trades_by_hour.get(tk, {}).get(hr + dt.timedelta(hours=1), []))
                    meta = {"arm": arm, "side": q.side, "event": c.event,
                            "programme_start": s.programme_start.isoformat() if s.programme_start else ""}
                    quotes.append((qid, arm, tk, hr, 3600.0, q.size_usd,
                                   q.price if q.side == "yes" else 0, 0 if q.side == "yes" else q.price,
                                   q.qty, round(q.price * q.qty / 100.0, 2), meta))
                    mb = mark_bid(books.get(tk, {}), q.side, hr)
                    for model in fm.ARM_FILL_MODELS:
                        filled = got[model]
                        rew = credited * (FILLED_HOUR_REWARD_FRACTION if filled > 0 else 1.0)
                        outcomes.append((qid, model, rew, q.side if filled > 0 else None,
                                         filled if filled > 0 else None, None))
                        if filled > 0 and mb is not None:
                            marks.append((qid, model, q.price, mb))
                    hours.append((qid, {"seconds_scored": 3600,
                                        "seconds_qualifying": 3600 if qualifies else 0,
                                        "seconds_thin": 3600 if (qualifies and thin) else 0}))
    return quotes, outcomes, marks, hours


def pa_discount(bps) -> float:
    if bps is None:
        return 1.0
    return max(0.0, min(1.0, int(bps) / 10_000.0))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--until", default=RUN1_UNTIL)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    since, until = _aware(SINCE), _aware(args.until)
    refusal = check_window(until, now)
    if refusal:
        print(f"REFUSED: {refusal} (docs/LIMM_PAPER_STRATEGIES_BACKTEST.md §4)")
        return 2

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    opts = RO_OPTIONS.replace("statement_timeout=300000", "statement_timeout=1500000")
    with psycopg.connect(url, options=opts) as conn, conn.cursor() as cur:
        cur.execute(SNAP_SQL, {"since": since, "until": until})
        snaps = cur.fetchall()
        cur.execute(TRADE_SQL, {"since": since, "until": until})
        trades = cur.fetchall()
    rows, books = build_rows(snaps)
    tbh: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for tk, ts_ms, rcv, yp, cnt, taker in trades:
        at = trade_time(ts_ms, rcv)
        if at is not None and at < until:
            tbh[tk][at.replace(minute=0, second=0, microsecond=0)].append((at, yp, cnt, taker))
    quotes, outcomes, marks, hours = replay(rows, books, tbh)
    res = rd.analyse(quotes, outcomes, marks, hours)
    for arm in [a for a in res["arms"] if a not in ARMS]:
        del res["arms"][arm]
    print("LIMM paper strategies BACKTEST (Part A, descriptive; docs/LIMM_PAPER_STRATEGIES_BACKTEST.md)")
    print("Moves no forward bar. S1 is not replayed (no history for its universe).\n")
    print(f"window {SINCE[:16]} -> {until.isoformat()[:16]}; hourly snapshots {len(snaps)}; "
          f"prints {len(trades)}; arm-hours {len(quotes)}; 24 h marks {len(marks)}\n")
    print(rd.render(res))
    if args.json:
        print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
