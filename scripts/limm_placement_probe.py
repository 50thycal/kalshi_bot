"""LIMM-PLACEMENT — where should the liquidity-incentive book rest its bids? (docs/LIMM_PLACEMENT_THESIS.md)

Replays five quoting policies over the shadow collector's recorded books and public tape:
B0 (today: both sides one tick behind the best bid, legs <= 10c), P1 (cheap side only at the
reference price), P2 (both sides at the reference prices), and P1F/P2F (re-priced every snapshot
and pulled on activity spikes or near the close). For each it estimates reward, fill P&L under two
fill models (optimistic / conservative) and fees, per market-day quoted, and prints the
pre-registered verdict.

No lookahead: each decision uses only the snapshot at that instant; a fill is marked to the book
24 h later (or the last snapshot, labelled). Read-only, stdlib + psycopg; places nothing.

    {"type": "script", "name": "limm_placement_probe", "args": ["--days", "3"], "id": "<slug>"}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import timedelta

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

MAX_PRICE = 10            # live per-leg cap (cents)
MAX_CONTRACTS = 500
LEG_BUDGET_CENTS = 1000   # $10 per leg
REQUOTE_SECONDS = 4 * 3600
MAX_INTERVAL_SECONDS = 15 * 60
MARK_HORIZON = timedelta(hours=24)
PULL_TRADES_5M = 3
PULL_RANGE_5M = 3
PULL_CLOSE_HOURS = 48.0
REALISED_REWARD_USD = 2.21      # live book, 2026-09-17 -> 2026-10-04 (reward ledger)
POLICIES = ("B0", "P1", "P2", "P1F", "P2F")
MODELS = ("optimistic", "conservative")


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def maker_fee_cents(price: int, qty: float) -> float:
    p = price / 100.0
    return math.ceil(0.0175 * qty * p * (1.0 - p) * 100)


def levels(raw) -> list[tuple[int, float]]:
    """[(price, qty)] best first, from the snapshot's top-10 json."""
    out = []
    for item in raw or []:
        try:
            out.append((int(item[0]), float(item[1])))
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda x: -x[0])
    return out


def reference(lv: list[tuple[int, float]], target: float) -> int | None:
    """R3: first price where cumulative size from the best bid reaches Target/5."""
    if not lv or not target:
        return None
    need, cum = target / 5.0, 0.0
    for price, qty in lv:
        cum += qty
        if cum >= need - 1e-9:
            return price
    return lv[-1][0]


def depth_at(lv, price) -> float:
    return sum(q for p, q in lv if p == price)


class Leg:
    __slots__ = ("side", "price", "qty", "placed", "ahead", "filled_at", "model")

    def __init__(self, side, price, qty, placed, ahead, model):
        self.side, self.price, self.qty, self.placed = side, price, qty, placed
        self.ahead, self.filled_at, self.model = ahead, None, model

    def hit(self, yes_price, taker, count) -> bool:
        """Apply one print; True when this print fills us."""
        if yes_price is None or taker not in ("yes", "no") or not count:
            return False
        if self.side == "yes":
            if taker != "no" or yes_price > self.price:
                return False
            through = yes_price < self.price
        else:
            lvl = 100 - self.price
            if taker != "yes" or yes_price < lvl:
                return False
            through = yes_price > lvl
        if self.model == "optimistic" or through:
            return True
        self.ahead -= float(count)
        return self.ahead < 0


def desired(policy: str, snap: dict, target: float) -> list[tuple[str, int]]:
    """The (side, price) legs a policy wants from this snapshot alone; [] when it stands aside."""
    ylv, nlv = snap["ylv"], snap["nlv"]
    if not ylv or not nlv:
        return []
    by, bn = ylv[0][0], nlv[0][0]
    if policy == "B0":
        y, n = max(1, by - 1), max(1, bn - 1)
        if by > MAX_PRICE or bn > MAX_PRICE or y + n > 99:
            return []
        return [("yes", y), ("no", n)]
    ry, rn = reference(ylv, target), reference(nlv, target)
    if ry is None or rn is None:
        return []
    if policy.startswith("P1"):
        cands = [(p, s) for s, p in (("yes", ry), ("no", rn)) if 1 <= p <= MAX_PRICE]
        if not cands:
            return []
        p, s = min(cands)
        return [(s, p)]
    if ry + rn > 99 or ry < 1 or rn < 1:
        return []
    return [("yes", ry), ("no", rn)]


def qty_for(legs: list[tuple[str, int]]) -> int:
    dearer = max(p for _s, p in legs)
    return min(MAX_CONTRACTS, LEG_BUDGET_CENTS // dearer) if dearer > 0 else 0


def pulled(snap: dict, close_at) -> bool:
    if (snap["trades5"] or 0) >= PULL_TRADES_5M or (snap["range5"] or 0) >= PULL_RANGE_5M:
        return True
    if close_at is not None and (close_at - snap["at"]).total_seconds() / 3600.0 < PULL_CLOSE_HOURS:
        return True
    return False


def simulate(policy, model, snaps, trades, prog):
    """One policy x fill model over one market. Returns dict of totals."""
    target = float(prog["target"] or 0)
    disc = (prog["disc_bps"] or 10000) / 10000.0
    pool_ps = prog["pool_ps"]
    follow = policy.endswith("F")
    tts = [t[0] for t in trades]
    legs: list[Leg] = []
    quoted_at = None
    stopped = False
    out = {"reward": 0.0, "pnl": 0.0, "fees": 0.0, "quoted_s": 0.0, "fills": 0, "pairs": 0,
           "marked_late": 0}
    fills: list[Leg] = []
    for i, snap in enumerate(snaps):
        t = snap["at"]
        live = [lg for lg in legs if lg.filled_at is None]
        open_ok = (snap["qual"] and t < prog["end"] and (prog["close"] is None or t < prog["close"]))
        if stopped:
            want = []
        elif not open_ok or (follow and pulled(snap, prog["close"])):
            want = []
            legs = [lg for lg in legs if lg.filled_at is not None]
            live = []
            quoted_at = None
        elif not live or quoted_at is None or follow or (t - quoted_at).total_seconds() >= REQUOTE_SECONDS:
            want = desired(policy, snap, target)
        else:
            want = None  # keep what rests
        if want is not None and not stopped:
            cur = sorted((lg.side, lg.price) for lg in live)
            if want and sorted(want) != cur:
                q = qty_for(want)
                legs = [lg for lg in legs if lg.filled_at is not None]
                for s, p in want:
                    lv = snap["ylv"] if s == "yes" else snap["nlv"]
                    # We join the back of the queue at our price.
                    ahead = depth_at(lv, p) if model == "conservative" else 0.0
                    legs.append(Leg(s, p, q, t, ahead, model))
                quoted_at = t
            elif not want:
                legs = [lg for lg in legs if lg.filled_at is not None]
                quoted_at = None
        live = [lg for lg in legs if lg.filled_at is None]
        if not live:
            continue
        t_next = snaps[i + 1]["at"] if i + 1 < len(snaps) else t
        dt = min((t_next - t).total_seconds(), MAX_INTERVAL_SECONDS)
        if dt <= 0:
            continue
        out["quoted_s"] += dt
        # Reward over the interval at this snapshot's book.
        for lg in live:
            lv = snap["ylv"] if lg.side == "yes" else snap["nlv"]
            field = snap["fy"] if lg.side == "yes" else snap["fn"]
            ref = reference(lv, target)
            if ref is None or not field:
                continue
            mult = disc ** (ref - lg.price) if lg.price < ref else 1.0
            ours = lg.qty * mult
            out["reward"] += pool_ps * dt * (ours / (field + ours)) / 2.0
        # Tape over the interval.
        lo, hi = bisect_left(tts, t), bisect_left(tts, t + timedelta(seconds=dt))
        for _ts, yp, cnt, taker in trades[lo:hi]:
            for lg in live:
                if lg.filled_at is None and lg.hit(yp, taker, cnt):
                    lg.filled_at = _ts
                    fills.append(lg)
        if any(lg.filled_at is not None for lg in legs):
            # A position: no new quotes on this market. An unfilled leg of the same pair keeps
            # resting unchanged (still scored, still fillable), as the live book leaves it.
            stopped = True
    # Settle fills: a completed pair locks 100 - y - n; a lone leg is marked at +24 h.
    sides = {lg.side: lg for lg in fills}
    snap_ts = [s["at"] for s in snaps]
    if "yes" in sides and "no" in sides:
        q = min(sides["yes"].qty, sides["no"].qty)
        out["pnl"] += (100 - sides["yes"].price - sides["no"].price) * q / 100.0
        out["pairs"] += 1
    else:
        for lg in fills:
            j = bisect_left(snap_ts, lg.filled_at + MARK_HORIZON)
            if j >= len(snaps):
                j = len(snaps) - 1
                out["marked_late"] += 1
            lv = snaps[j]["ylv"] if lg.side == "yes" else snaps[j]["nlv"]
            mark = lv[0][0] if lv else 0
            out["pnl"] += (mark - lg.price) * lg.qty / 100.0
    for lg in fills:
        out["fees"] += maker_fee_cents(lg.price, lg.qty) / 100.0
        out["fills"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LIMM-PLACEMENT probe")
    ap.add_argument("--days", type=float, default=3.0, help="window of recorded data (default 3)")
    ap.add_argument("--max-markets", type=int, default=400)
    ap.add_argument("--window-only", action="store_true",
                    help="only markets closing 7-60 days after their first snapshot (the live window)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    import psycopg

    print(f"LIMM-PLACEMENT probe — last {args.days:g} days of shadow books + tape "
          f"(read-only){' — live window only' if args.window_only else ''}\n")
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        cur = conn.cursor()
        cur.execute("SELECT now()")
        now = cur.fetchone()[0]
        start = now - timedelta(days=args.days)
        cur.execute("""
            SELECT s.market_ticker, count(*) n, max(s.program_row_id)
            FROM incentive_market_snapshots s WHERE s.at >= %s
            GROUP BY 1 ORDER BY 2 DESC LIMIT %s""", (start, args.max_markets))
        markets = cur.fetchall()
        tot = {(p, m): defaultdict(float) for p in POLICIES for m in MODELS}
        per_mkt = {(p, m): {} for p in POLICIES for m in MODELS}
        used = skipped = 0
        for ticker, _n, prog_id in markets:
            cur.execute("""SELECT period_reward_usd, start_date, end_date, close_time, target_size,
                                  discount_factor_bps FROM incentive_programs WHERE id = %s""", (prog_id,))
            row = cur.fetchone()
            if not row or row[0] is None or row[1] is None or row[2] is None:
                skipped += 1
                continue
            pool, p_start, p_end, close, target, disc_bps = row
            period = (p_end - p_start).total_seconds()
            if period <= 0 or not target:
                skipped += 1
                continue
            cur.execute("""SELECT at, yes_levels_json, no_levels_json, est_yes_score_total,
                                  est_no_score_total, est_yes_meets_target, est_no_meets_target,
                                  trades_last_5m, price_range_5m_cents
                           FROM incentive_market_snapshots
                           WHERE market_ticker = %s AND at >= %s AND book_valid IS TRUE
                           ORDER BY at""", (ticker, start))
            snaps = [{"at": r[0], "ylv": levels(r[1]), "nlv": levels(r[2]),
                      "fy": float(r[3] or 0), "fn": float(r[4] or 0),
                      "qual": bool(r[5]) and bool(r[6]), "trades5": r[7], "range5": r[8]}
                     for r in cur.fetchall()]
            if len(snaps) < 2:
                skipped += 1
                continue
            if args.window_only and close is not None:
                days_to_close = (close - snaps[0]["at"]).total_seconds() / 86400.0
                if not 7.0 <= days_to_close <= 60.0:
                    continue
            cur.execute("""SELECT received_at, yes_price_cents, count_fp, taker_outcome_side
                           FROM incentive_trade_events
                           WHERE market_ticker = %s AND received_at >= %s ORDER BY received_at""",
                        (ticker, start))
            trades = [(r[0], r[1], float(r[2] or 0), r[3]) for r in cur.fetchall()]
            prog = {"target": float(target), "disc_bps": disc_bps, "end": p_end, "close": close,
                    "pool_ps": float(pool) / period}
            used += 1
            for p in POLICIES:
                for m in MODELS:
                    r = simulate(p, m, snaps, trades, prog)
                    if r["quoted_s"] <= 0:
                        continue
                    for k, v in r.items():
                        tot[(p, m)][k] += v
                    tot[(p, m)]["markets"] += 1
                    per_mkt[(p, m)][ticker] = r["reward"] + r["pnl"] - r["fees"]

        # L0 sanity: realised reward per live market-day (each live pair rested <= 4 h).
        cur.execute("""SELECT count(DISTINCT (market_ticker, date_trunc('hour', created_at)))
                       FROM live_orders WHERE strategy = 'Alimm1' AND action = 'buy'
                         AND coalesce(client_order_id, '') NOT LIKE 'limmexit:%%'""")
        live_pairs = cur.fetchone()[0] or 0
    live_md = live_pairs * 4.0 / 24.0
    realised_md = REALISED_REWARD_USD / live_md if live_md else None

    print(f"markets simulated: {used}   skipped (no terms / <2 snapshots): {skipped}\n")
    hdr = (f"{'policy':6} {'model':12} {'mkts':>5} {'mkt-days':>8} {'reward$':>9} {'fillP&L$':>9} "
           f"{'fees$':>7} {'net$':>9} {'fills':>5} {'pairs':>5} {'rew/md':>7} {'net/md':>7} {'top-mkt%':>8}")
    print(hdr)
    print("-" * len(hdr))
    res = {}
    for p in POLICIES:
        for m in MODELS:
            t = tot[(p, m)]
            md = t["quoted_s"] / 86400.0
            net = t["reward"] + t["pnl"] - t["fees"]
            pm = per_mkt[(p, m)]
            top = (max(pm.values()) / net * 100.0) if pm and net > 0 else float("nan")
            res[(p, m)] = {"md": md, "net": net, "rew_md": t["reward"] / md if md else 0.0,
                           "net_md": net / md if md else 0.0, "top": top, "late": t["marked_late"]}
            print(f"{p:6} {m:12} {int(t['markets']):5d} {md:8.2f} {t['reward']:9.2f} {t['pnl']:9.2f} "
                  f"{t['fees']:7.2f} {net:9.2f} {int(t['fills']):5d} {int(t['pairs']):5d} "
                  f"{res[(p, m)]['rew_md']:7.3f} {res[(p, m)]['net_md']:7.3f} {top:8.1f}")
    late = sum(int(tot[k]["marked_late"]) for k in tot)
    if late:
        print(f"\n({late} fills marked at the last snapshot: less than 24 h of book after the fill)")

    # ---- pre-registered verdict ----
    print("\nVERDICT (docs/LIMM_PLACEMENT_THESIS.md)")
    b0 = res[("B0", "conservative")]
    optimistic_flag = False
    if realised_md is not None:
        print(f"L0  live realised ≈ ${realised_md:.3f}/market-day ({live_pairs} pairs x 4 h); "
              f"B0 simulated ${b0['rew_md']:.3f}/market-day")
        if b0["rew_md"] > 5 * realised_md:
            optimistic_flag = True
            print("    B0 sim > 5x realised -> reward model flagged OPTIMISTIC; no PROMOTE this run")
        else:
            print("    within 5x -> reward model usable")
    for p in ("P1", "P2"):
        r = res[(p, "conservative")]["rew_md"]
        ok = b0["rew_md"] > 0 and r >= 3 * b0["rew_md"]
        print(f"L1  {p} reward/md {r:.3f} vs 3 x B0 {3 * b0['rew_md']:.3f} -> {'PASS' if ok else 'FAIL'}")
    verdicts = {}
    for p in POLICIES[1:]:
        c, o = res[(p, "conservative")], res[(p, "optimistic")]
        if c["net_md"] <= 0:
            v = "KILL"
        elif (c["net_md"] >= 0.50 and o["net"] > 0 and c["md"] >= 20
              and not (c["top"] == c["top"] and c["top"] > 50.0) and not optimistic_flag):
            v = "PROMOTE"
        else:
            v = "HOLD"
        verdicts[p] = v
        print(f"L2  {p:4} conservative net/md {c['net_md']:+.3f} over {c['md']:.1f} md, optimistic net "
              f"{o['net']:+.2f}, top market {c['top']:.0f}% -> {v}")
    for base in ("P1", "P2"):
        better = res[(base + "F", "conservative")]["net_md"] > res[(base, "conservative")]["net_md"]
        print(f"F   {base}F {'beats' if better else 'does not beat'} {base} on conservative net/md")
    winners = [p for p, v in verdicts.items() if v == "PROMOTE"]
    if winners:
        best = max(winners, key=lambda p: res[(p, "conservative")]["net_md"])
        if "P1F" in winners and res[("P1F", "conservative")]["net_md"] >= 0.8 * res[(best, "conservative")]["net_md"]:
            best = "P1F"
        print(f"\nCHOICE: {best} (≈ ${2 * res[(best, 'conservative')]['net_md']:.2f}/day on two slots)")
    elif all(v == "KILL" for v in verdicts.values()):
        print("\nCHOICE: none — every policy KILLED; placement is not the bottleneck")
    else:
        print("\nCHOICE: none yet — HOLD (see L2 lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
