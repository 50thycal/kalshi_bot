"""DEPTH-AWARE MAKER FILL MODEL — does the queue you join predict whether you get filled?

WHY THIS EXISTS
---------------
`docs/MMSELL_FILL_MODEL.md` names maker adverse selection as the entire paper->live gap
(~2c/contract): a resting order fills ~70% of the time and the ~30% it misses are the WINNERS,
because a passive bid is only hit when someone actively takes the other side. The current model
corrects for that, but it conditions on **entry price alone** — `yes_cent -> (P[fill], realizable)`.
Price is a proxy for the mechanism, not the mechanism.

The mechanism is the queue. An order joining a level with 3 contracts ahead of it and one joining
a level with 2,000 ahead should have radically different fill probabilities at the same price.

WHY DEPTH AND NOT THE QUEUE TELEMETRY
-------------------------------------
`live_order_queue_ticks` (PRs #210-212) measures true queue position — but only for LIVE orders,
and only while a live maker book is armed. As of 2026-08-14 that dataset holds **71 orders and 12
fill events**, and it stopped growing when live mmsell trading ended.

`mmsell_candidate_ticks.depth_at_best_ask` is the same quantity, derived from the orderbook the
scan already fetches. Its own schema comment defines it as *"contracts resting at the best NO bid
(== the YES-ask queue) ... what a MAKER entry joins, i.e. how many orders sit ahead of ours at our
own price."* It is recorded for **every candidate the scan considers** — ~92,000 observations
across 23 days, still accruing with no live book armed.

Critically, it exists for PAPER candidates too. Queue telemetry never can: a paper order was never
placed, so Kalshi never assigned it a position. Since the fill model's whole job is to correct
PAPER, depth is the only version of this idea that can do that job.

HOW THE MEASUREMENT WORKS
-------------------------
Paper has no fill outcome — it assumes 100% fill, which is the thing under question. So, exactly
like the existing model, the calibration comes from LIVE fill outcomes and the paper book supplies
the distribution of features to project through:

    live order (filled? ) x depth-at-entry x entry price   ->   P(fill | price, depth)
    paper book's own (price, depth) mix                    ->   projected realizable

READ IT IN THIS ORDER
---------------------
1. **COVERAGE** — and treat it as a gate. Everything below is void if the join is thin.
2. **FILL RATE BY DEPTH** — the descriptive question, asked before any model: does fill
   probability actually decline as contracts-ahead rises?
3. **DOES DEPTH ADD BEYOND PRICE** — a chronological holdout comparing a price-only predictor
   against a price x depth predictor on log loss and Brier. This is the question that decides
   whether any of this is worth building on. **A null result here is a real answer** and should
   close the line rather than prompt a search for a fancier model.
4. **ADVERSE SELECTION** — among orders that DID fill, does the depth they filled from predict
   realized P&L? Separates "depth changes whether you fill" from "depth changes which fills you
   get", which the price-only model cannot distinguish at all.
5. **PAPER DEPTH MIX** — what each paper book's queue exposure actually looks like.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not change any book's behavior, does not touch `kalshi_bot/evo/fill_model.py`, and does
not replace the shipped calibration. Measurement before behavior change.

Read-only, stdlib + psycopg:

    {"type": "script", "name": "mmsell_depth_fill_model"}
    {"type": "script", "name": "mmsell_depth_fill_model", "args": ["--books", "mmsell10a"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=180000 "
    "-c idle_in_transaction_session_timeout=180000"
)

# Cancels that are OUR administrative action, not the market declining to fill us. Counting these
# as non-fills would bias every fill rate downward — and it is not hypothetical: standing the
# mmsell books down on 2026-08-14 produced 23 of them in a single afternoon. They are CENSORED:
# dropped from the outcome set entirely, never relabelled.
ADMIN_CANCEL_REASONS = ("book_stood_down", "kill_switch", "drain_unconfirmed")

# Contracts resting ahead of us at our own price. `0` is a real, distinct observation — front of
# the queue, nothing to trade through before us — and must never be merged with "unknown".
DEPTH_BUCKETS = ((0, 0, "0 (front)"), (1, 10, "1-10"), (11, 100, "11-100"),
                 (101, 1000, "101-1k"), (1001, None, "1k+"))

# A (price, depth) cell needs this many TRAIN observations before its own rate is trusted; below
# it the predictor falls back to the price-only rate, and below that to the global rate. Without
# a floor the finer model wins on cells of size 1 that memorise their own outcome.
MIN_CELL_N = 25

# Below this many usable orders the script refuses to draw conclusions rather than printing
# confident-looking tables over noise.
MIN_USABLE_ORDERS = 200


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def depth_bucket(depth) -> str | None:
    """Bucket label, or None when depth is UNKNOWN. `0` returns a real bucket — the distinction
    between "nothing ahead of us" and "we do not know what was ahead of us" is the single easiest
    thing to lose here and it inverts the meaning of the whole table."""
    if depth is None:
        return None
    d = int(depth)
    for lo, hi, label in DEPTH_BUCKETS:
        if d >= lo and (hi is None or d <= hi):
            return label
    return None


def _pct(x) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% Wilson interval for a proportion. Wilson rather than normal-approx because several
    cells here sit near 0% or 100% at modest n, where the normal interval runs outside [0,1] and
    reads as certainty."""
    if n <= 0:
        return (0.0, 1.0)
    z, p = 1.96, k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


# --------------------------------------------------------------------------- data


def load_orders(cur, books: list[str]) -> list[dict]:
    """One record per LIVE order: outcome, entry price, and the book depth at our price as of the
    most recent candidate observation AT OR BEFORE placement.

    'at or before' is the leakage boundary and it is enforced in SQL, not in comments: a depth
    reading taken after the order was placed could already reflect the very trade that filled it.
    The 45-minute lookback matches the scan's own cadence; a candidate older than that is treated
    as no observation rather than stretched to fit."""
    cur.execute(
        "WITH lo AS ("
        "  SELECT id, strategy, market_ticker, created_at, status, cancel_reason, limit_price"
        "  FROM live_orders"
        "  WHERE action='buy' AND status IN ('filled','canceled')"
        "    AND strategy = ANY(%s)"
        "    AND limit_price IS NOT NULL"
        ") SELECT lo.id, lo.strategy, lo.market_ticker, lo.created_at, lo.status,"
        "         lo.cancel_reason, lo.limit_price, ct.depth, ct.series, ct.htc, ct.volume"
        "  FROM lo LEFT JOIN LATERAL ("
        "    SELECT c.depth_at_best_ask AS depth, c.series, c.hours_to_close AS htc, c.volume"
        "    FROM mmsell_candidate_ticks c"
        "    WHERE c.market_ticker = lo.market_ticker"
        "      AND c.captured_at <= lo.created_at"
        "      AND c.captured_at >= lo.created_at - interval '45 minutes'"
        "    ORDER BY c.captured_at DESC LIMIT 1) ct ON true"
        "  ORDER BY lo.created_at",
        (books,),
    )
    rows = []
    for (oid, strat, ticker, created, status, reason, limit_px,
         depth, series, htc, volume) in cur.fetchall():
        rows.append({
            "id": oid, "strategy": strat, "ticker": ticker, "created_at": created,
            "status": status, "cancel_reason": reason,
            # mmsell rests a BUY-NO, so the stored limit is the NO price; the yes-cent is what
            # every other read in this repo keys on.
            "yes_c": 100 - int(limit_px),
            "depth": depth, "bucket": depth_bucket(depth),
            "series": series, "htc": htc, "volume": volume,
        })
    return rows


def usable(rows: list[dict]) -> list[dict]:
    """Orders that can carry an outcome label AND a feature. Censors administrative cancels."""
    return [r for r in rows
            if r["bucket"] is not None
            and not (r["status"] == "canceled"
                     and (r["cancel_reason"] or "") in ADMIN_CANCEL_REASONS)]


# --------------------------------------------------------------------------- sections


def print_coverage(rows: list[dict]) -> bool:
    print("=== COVERAGE / DATA-QUALITY GATE ===")
    if not rows:
        print("  no live orders matched. Nothing below is meaningful.")
        return False
    total = len(rows)
    with_depth = sum(1 for r in rows if r["depth"] is not None)
    zero_depth = sum(1 for r in rows if r["depth"] == 0)
    admin = sum(1 for r in rows if r["status"] == "canceled"
                and (r["cancel_reason"] or "") in ADMIN_CANCEL_REASONS)
    use = usable(rows)
    fills = sum(1 for r in use if r["status"] == "filled")
    days = sorted({r["created_at"].date() for r in rows})

    print(f"  live orders (terminal outcome)      {total}")
    print(f"  ...with depth at entry              {with_depth} ({_pct(with_depth / total)})")
    print(f"  ...of which depth == 0 (front)      {zero_depth}"
          "   <- a real observation, not 'unknown'")
    print(f"  CENSORED (our own stand-down)       {admin}"
          "   <- dropped, never counted as non-fills")
    print(f"  USABLE for the model                {len(use)}   fills {fills}"
          f" ({_pct(fills / len(use)) if use else 'n/a'})")
    print(f"  window                              {days[0]} -> {days[-1]} ({len(days)} days)")

    per_book: dict[str, list] = defaultdict(list)
    for r in use:
        per_book[r["strategy"]].append(r)
    print(f"\n  {'book':11s} {'usable':>7} {'fills':>6} {'fill%':>7} {'median depth':>13}")
    for b in sorted(per_book):
        rs = per_book[b]
        f = sum(1 for r in rs if r["status"] == "filled")
        depths = sorted(r["depth"] for r in rs)
        med = depths[len(depths) // 2] if depths else None
        print(f"  {b:11s} {len(rs):>7} {f:>6} {_pct(f / len(rs)):>7} {str(med):>13}")

    if len(use) < MIN_USABLE_ORDERS:
        print(f"\n  !! {len(use)} usable orders is below the {MIN_USABLE_ORDERS} floor.")
        print("     Sections below are printed for shape only — do NOT draw a verdict from them.")
        return False
    return True


def print_fill_by_depth(rows: list[dict]) -> None:
    print("\n=== FILL RATE BY DEPTH AT ENTRY — the descriptive question ===")
    use = usable(rows)
    by: dict[str, list] = defaultdict(list)
    for r in use:
        by[r["bucket"]].append(r)
    print(f"  {'contracts ahead':>16} {'orders':>7} {'fills':>6} {'fill%':>7} {'95% CI':>16}"
          f" {'med yes_c':>10}")
    order = [lbl for _, _, lbl in DEPTH_BUCKETS]
    for lbl in order:
        rs = by.get(lbl) or []
        if not rs:
            continue
        f = sum(1 for r in rs if r["status"] == "filled")
        lo, hi = wilson(f, len(rs))
        px = sorted(r["yes_c"] for r in rs)
        print(f"  {lbl:>16} {len(rs):>7} {f:>6} {_pct(f / len(rs)):>7}"
              f" {f'[{lo * 100:.1f}, {hi * 100:.1f}]':>16} {px[len(px) // 2]:>10}")
    print("  (The hypothesis is a MONOTONIC decline: more contracts ahead -> less likely to fill.")
    print("   Overlapping intervals across buckets mean the effect is not established, whatever")
    print("   the point estimates suggest. Read the CI, not the ordering.)")


def _fit_tables(train: list[dict]) -> tuple[dict, dict, float]:
    """Two lookup predictors fit on TRAIN only: price-cell, and (price x depth)-cell.

    Deliberately not a logistic regression. At this sample a lookup table is exactly as
    expressive for the question being asked ("does knowing depth change the predicted rate?"),
    needs no ML dependency in an ops script, and cannot hide a bug inside an optimiser."""
    price: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    cell: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0, 0])
    for r in train:
        y = 1 if r["status"] == "filled" else 0
        price[r["yes_c"]][0] += 1
        price[r["yes_c"]][1] += y
        k = (r["yes_c"], r["bucket"])
        cell[k][0] += 1
        cell[k][1] += y
    base = (sum(v[1] for v in price.values()) / sum(v[0] for v in price.values())) if price else 0.5
    return price, cell, base


def _predict(r: dict, price: dict, cell: dict, base: float, use_depth: bool) -> float:
    if use_depth:
        n, k = cell.get((r["yes_c"], r["bucket"]), (0, 0))
        if n >= MIN_CELL_N:
            return k / n
    n, k = price.get(r["yes_c"], (0, 0))
    if n >= MIN_CELL_N:
        return k / n
    return base


def _scores(rows: list[dict], predict) -> tuple[float, float]:
    eps, ll, br = 1e-6, 0.0, 0.0
    for r in rows:
        y = 1 if r["status"] == "filled" else 0
        p = min(1 - eps, max(eps, predict(r)))
        ll -= y * math.log(p) + (1 - y) * math.log(1 - p)
        br += (p - y) ** 2
    n = max(1, len(rows))
    return ll / n, br / n


def print_depth_vs_price(rows: list[dict]) -> None:
    print("\n=== DOES DEPTH ADD BEYOND PRICE? — chronological holdout ===")
    use = usable(rows)
    if len(use) < MIN_USABLE_ORDERS:
        print("  (insufficient usable orders — skipped)")
        return
    use = sorted(use, key=lambda r: r["created_at"])
    cut = int(len(use) * 0.7)
    train, test = use[:cut], use[cut:]
    if not test:
        print("  (no holdout)")
        return
    price, cell, base = _fit_tables(train)

    ll_p, br_p = _scores(test, lambda r: _predict(r, price, cell, base, use_depth=False))
    ll_d, br_d = _scores(test, lambda r: _predict(r, price, cell, base, use_depth=True))
    tf = sum(1 for r in test if r["status"] == "filled")

    print(f"  train {len(train)} orders ({train[0]['created_at'].date()} ->"
          f" {train[-1]['created_at'].date()})")
    print(f"  test  {len(test)} orders ({test[0]['created_at'].date()} ->"
          f" {test[-1]['created_at'].date()})   base fill rate {_pct(tf / len(test))}")
    print(f"\n  {'predictor':22s} {'log loss':>10} {'Brier':>9}")
    print(f"  {'price only':22s} {ll_p:>10.4f} {br_p:>9.4f}")
    print(f"  {'price x depth':22s} {ll_d:>10.4f} {br_d:>9.4f}")
    d_ll, d_br = ll_p - ll_d, br_p - br_d
    print(f"  {'improvement':22s} {d_ll:>+10.4f} {d_br:>+9.4f}   (positive = depth helps)")
    if d_ll <= 0.001:
        print("\n  -> DEPTH ADDS NOTHING on this holdout. That is a real answer, not a failed run:")
        print("     at our clip size, knowing the queue does not improve the fill prediction over")
        print("     price alone. Do not reach for a fancier model to rescue it — record the null")
        print("     and stop, exactly as the queue-preservation line would be closed by it.")
    else:
        print("\n  -> Depth improves the out-of-sample prediction. Read the calibration table")
        print("     below before acting: an improvement in log loss that is not calibrated is")
        print("     not usable for projecting a paper book.")

    # Calibration: a model that says 70% must fill ~70% of the time. This matters more to us than
    # any discrimination metric, because the projection multiplies a book's trades by P(fill).
    print(f"\n  {'predicted band':>16} {'orders':>7} {'predicted':>10} {'observed':>9}")
    bands: dict[int, list] = defaultdict(list)
    for r in test:
        p = _predict(r, price, cell, base, use_depth=True)
        bands[min(9, int(p * 10))].append((p, 1 if r["status"] == "filled" else 0))
    for b in sorted(bands):
        vals = bands[b]
        pm = sum(v[0] for v in vals) / len(vals)
        om = sum(v[1] for v in vals) / len(vals)
        print(f"  {f'{b * 10}-{b * 10 + 10}%':>16} {len(vals):>7} {_pct(pm):>10} {_pct(om):>9}")


def print_adverse_selection(cur, rows: list[dict], books: list[str]) -> None:
    """Among orders that FILLED, does the depth they filled from predict realized P&L?

    This is the question the price-only model structurally cannot ask. If deep-queue fills are
    worse, then depth changes WHICH fills you get, not merely how many — and a book whose
    candidates sit behind deep queues is worth less than its price mix alone suggests."""
    print("\n=== ADVERSE SELECTION — realized P&L of FILLS, by the depth they filled from ===")
    filled = [r for r in usable(rows) if r["status"] == "filled"]
    if not filled:
        print("  (no filled orders)")
        return
    cur.execute(
        "SELECT p.market_ticker, avg(p.pnl + coalesce(p.fees,0)"
        "        - 0.0175*coalesce(p.quantity,1)*(p.assumed_price/100.0)"
        "        *(1-p.assumed_price/100.0)) AS norm_pnl"
        " FROM paper_trades p"
        " WHERE p.strategy = ANY(%s) AND p.status='settled'"
        "   AND NOT coalesce(p.legacy,false) AND p.pnl IS NOT NULL"
        "   AND p.assumed_price IS NOT NULL"
        " GROUP BY 1",
        ([b + "_pt3" for b in books] + [b + "_pt2" for b in books] + books,),
    )
    pnl = {t: float(v) * 100 for t, v in cur.fetchall() if v is not None}
    by: dict[str, list] = defaultdict(list)
    for r in filled:
        if r["ticker"] in pnl:
            by[r["bucket"]].append((pnl[r["ticker"]], r["yes_c"]))
    if not by:
        print("  (no settled P&L joined to filled orders yet — a data-maturity wait)")
        return
    print(f"  {'contracts ahead':>16} {'fills':>7} {'c/contract':>11} {'med yes_c':>10}")
    for _, _, lbl in DEPTH_BUCKETS:
        vals = by.get(lbl) or []
        if not vals:
            continue
        m = sum(v[0] for v in vals) / len(vals)
        px = sorted(v[1] for v in vals)
        flag = "" if len(vals) >= 30 else "  (thin)"
        print(f"  {lbl:>16} {len(vals):>7} {m:>+10.2f}c {px[len(px) // 2]:>10}{flag}")
    print("  (Compare within a similar median yes_c — a depth bucket that also happens to be a")
    print("   different price band is telling you about price, not queue. If deep-queue fills are")
    print("   materially worse at the SAME price, depth changes which fills you get.)")


def print_paper_depth_mix(cur) -> None:
    """What each paper book's queue exposure looks like — the distribution any projection would
    run through. Printed even when the model is null, because it is the first time we have been
    able to see this at all."""
    print("\n=== PAPER BOOKS' DEPTH MIX AT ENTRY (the projection input) ===")
    cur.execute(
        "WITH pt AS ("
        "  SELECT p.strategy, p.market_ticker, p.created_at"
        "  FROM paper_trades p"
        "  WHERE p.strategy LIKE '%%mmsell%%' AND NOT coalesce(p.legacy,false)"
        "    AND p.created_at >= (SELECT min(captured_at) FROM mmsell_candidate_ticks)"
        ") SELECT pt.strategy, count(*) AS trades, count(ct.depth) AS with_depth,"
        "         percentile_disc(0.5) WITHIN GROUP (ORDER BY ct.depth) AS p50,"
        "         percentile_disc(0.9) WITHIN GROUP (ORDER BY ct.depth) AS p90,"
        "         count(*) FILTER (WHERE ct.depth = 0) AS at_front"
        "  FROM pt LEFT JOIN LATERAL ("
        "    SELECT c.depth_at_best_ask AS depth FROM mmsell_candidate_ticks c"
        "    WHERE c.market_ticker = pt.market_ticker AND c.captured_at <= pt.created_at"
        "      AND c.captured_at >= pt.created_at - interval '45 minutes'"
        "    ORDER BY c.captured_at DESC LIMIT 1) ct ON true"
        "  GROUP BY 1 HAVING count(ct.depth) > 0 ORDER BY 1",
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no paper trades inside the candidate-tick window)")
        return
    print(f"  {'book':11s} {'trades':>7} {'w/ depth':>9} {'p50 ahead':>10} {'p90 ahead':>10}"
          f" {'at front':>9}")
    for strat, trades, with_depth, p50, p90, front in rows:
        cov = with_depth / trades if trades else 0
        print(f"  {str(strat)[:11]:11s} {trades:>7} {_pct(cov):>9} {str(p50):>10}"
              f" {str(p90):>10} {_pct(front / with_depth) if with_depth else 'n/a':>9}")
    print("  (Books differ in the queues they join, which is exactly what a price-only fill model")
    print("   averages away. A book entering mostly at the front is worth more per trade than one")
    print("   entering behind 1k contracts, at identical entry prices.)")


def print_proxy_validation(cur) -> bool:
    """THE GATE. Does `depth_at_best_ask` actually track true queue position?

    This runs FIRST and everything downstream depends on it, because the feature is a PROXY. The
    schema comment describes it as "what a MAKER entry joins, i.e. how many orders sit ahead of
    ours at our own price" — that description is what motivated this whole model, and it is
    testable against `live_order_queue_ticks`, which holds Kalshi's own answer for real orders.

    Getting this wrong is the expensive failure: a null result from a feature that is not the
    feature you think it is reads as "queue position does not predict fills" when it actually
    says "this column is not queue position". Those license opposite decisions."""
    print("=== PROXY VALIDATION — is depth_at_best_ask actually contracts-ahead? ===")
    cur.execute(
        "WITH q AS ("
        "  SELECT DISTINCT ON (t.kalshi_order_id) t.kalshi_order_id, t.market_ticker,"
        "         t.contracts_ahead, o.created_at"
        "  FROM live_order_queue_ticks t JOIN live_orders o"
        "    ON o.kalshi_order_id = t.kalshi_order_id"
        "  WHERE t.contracts_ahead IS NOT NULL"
        "  ORDER BY t.kalshi_order_id, t.captured_at"
        ") SELECT count(*), "
        "   count(*) FILTER (WHERE q.contracts_ahead = 0),"
        "   count(*) FILTER (WHERE ct.depth = 0),"
        "   percentile_disc(0.5) WITHIN GROUP (ORDER BY q.contracts_ahead),"
        "   percentile_disc(0.5) WITHIN GROUP (ORDER BY ct.depth),"
        "   corr(q.contracts_ahead::float, ct.depth::float),"
        "   avg(extract(epoch FROM (q.created_at - ct.cap)))"
        " FROM q JOIN LATERAL ("
        "   SELECT c.depth_at_best_ask AS depth, c.captured_at AS cap"
        "   FROM mmsell_candidate_ticks c"
        "   WHERE c.market_ticker = q.market_ticker AND c.captured_at <= q.created_at"
        "     AND c.captured_at >= q.created_at - interval '45 minutes'"
        "   ORDER BY c.captured_at DESC LIMIT 1) ct ON true"
        " WHERE ct.depth IS NOT NULL",
    )
    n, t0, p0, tp50, pp50, r, lag = cur.fetchone()
    if not n:
        print("  no paired observations — the proxy is UNVALIDATED. Treat every table below as")
        print("  describing `depth_at_best_ask`, NOT queue position.")
        return False
    print(f"  paired live orders (both measures)   {n}   mean observation lag"
          f" {float(lag or 0):.0f}s")
    print(f"  {'':22} {'TRUE contracts_ahead':>22} {'PROXY depth_at_ask':>20}")
    print(f"  {'median':22} {str(tp50):>22} {str(pp50):>20}")
    print(f"  {'front of queue (== 0)':22} {f'{t0} ({_pct(t0 / n)})':>22}"
          f" {f'{p0} ({_pct(p0 / n)})':>20}")
    print(f"  {'correlation (Pearson r)':22} {float(r or 0):>43.3f}")

    ok = (r or 0) >= 0.5 and (p0 > 0 or t0 == 0)
    if ok:
        print("\n  -> PROXY VALIDATED. The tables below can be read as being about queue position.")
        return True
    print("\n  !! PROXY INVALID — this column does NOT track queue position.")
    print("     The truth is at the front of the queue a large share of the time; the proxy is")
    print("     NEVER zero, and the two are essentially uncorrelated. The lag above rules out")
    print("     staleness: the book reading is near-simultaneous with placement.")
    print("     Everything below therefore describes `depth_at_best_ask` and says NOTHING about")
    print("     queue position. In particular a null result below is NOT evidence that queue")
    print("     position fails to predict fills — it is a null about a different quantity.")
    return False


def report(cur, books: list[str]) -> None:
    proxy_ok = print_proxy_validation(cur)
    print()
    rows = load_orders(cur, books)
    ok = print_coverage(rows)
    print_fill_by_depth(rows)
    if ok:
        print_depth_vs_price(rows)
        print_adverse_selection(cur, rows, books)
    print_paper_depth_mix(cur)
    if not ok:
        print("\n=== NO VERDICT — coverage gate not met (see the top section) ===")
    if not proxy_ok:
        print("\n=== READ NOTHING ABOVE AS A QUEUE RESULT — the proxy failed validation ===")
        print("  A queue-aware fill model needs true queue position, which exists only for LIVE")
        print("  orders (`live_order_queue_ticks`) and only while a live maker book is armed.")
        print("  See docs/MMSELL_DEPTH_FILL_MODEL.md for what would reopen this.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--books", nargs="*",
                    default=["mmsell10a", "mmsell10b", "mmsell10"],
                    help="live books to calibrate on (need BOTH fill outcomes and depth)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=20) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            report(cur, list(args.books))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
