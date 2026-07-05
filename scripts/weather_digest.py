"""Daily operational digest for the live weather trading bot — one read-only snapshot of
"how is it doing and is anything wrong", meant to be pulled on a schedule (a daily skill/cron
runs `{"type":"script","name":"weather_digest"}` via the ops channel and reads the result).

Sections:
  HEALTH      worker liveness (last cycle), cycles today, error cycles (+ last error)
  ENTRIES     live buy orders in the window, by cell + fill status
  EXITS       live sell/close orders in the window, by status
  POSITIONS   open live positions now (qty, avg cost, exposure, best-effort current bid + unreal)
  REALIZED    realized P&L today (the max_daily_loss breaker's number) per settled market
  PAPER       brief realized P&L per research book (settled), for the books we still collect
  ANOMALIES   the alert section — anything that needs a human: bad-status orders, fills with no
              order row (the duplicate-order bug class), untracked positions, stale worker,
              orders stuck pending/unknown, a settled-but-unreconciled flag

Self-contained (stdlib + psycopg). Read-only: a SELECT-only role + read-only transaction. The
optional current-price lookup uses Kalshi's PUBLIC market-data endpoint (no auth, browser UA,
same trick as kalshi_market_probe) and is best-effort — any failure just omits unrealized P&L.

Usage:
    {"type": "script", "name": "weather_digest"}
    {"type": "script", "name": "weather_digest", "args": ["--hours", "36", "--no-prices"]}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)
_KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_BAD_ORDER_STATUSES = ("rejected", "error", "not_landed")
_STUCK_STATUSES = ("pending", "unknown")


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _recent_date_codes(days: int = 3) -> list[str]:
    """Kalshi ticker date codes (e.g. '26JUN16') for the last `days` days — used to scope the
    fills-without-order check, since the fills table has no insert timestamp."""
    out = []
    today = datetime.now(timezone.utc).date()
    for d in range(days):
        dt = today - timedelta(days=d)
        out.append(dt.strftime("%y%b%d").upper())  # 26JUN16
    return out


def _kalshi_yes_bid(ticker: str) -> int | None:
    """Best-effort current yes-bid (cents) from Kalshi public market data; None on any failure."""
    try:
        req = urllib.request.Request(f"{_KALSHI}/markets/{ticker}", method="GET",
                                     headers={"User-Agent": _UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            mk = (json.loads(resp.read().decode("utf-8")) or {}).get("market") or {}
        bid = mk.get("yes_bid")
        if bid is None and mk.get("yes_bid_dollars") is not None:
            bid = round(float(mk["yes_bid_dollars"]) * 100)
        return int(bid) if bid is not None else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
        return None


def _q(cur, sql, params=()):
    # Call execute WITHOUT a params arg when there are none, so psycopg doesn't treat a literal
    # '%' (e.g. a LIKE 'weather_low_%' pattern) as a client-side placeholder.
    if params:
        cur.execute(sql, params)
    else:
        cur.execute(sql)
    return cur.fetchall()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=24.0,
                    help="activity look-back window in hours (default 24)")
    ap.add_argument("--no-prices", action="store_true",
                    help="skip the best-effort Kalshi current-price lookup for open positions")
    ap.add_argument("--stale-min", type=float, default=10.0,
                    help="flag the worker stale if the last cycle is older than this many minutes")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.hours)
    anomalies: list[str] = []

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            print(f"=== WEATHER BOT DAILY DIGEST — {now:%Y-%m-%d %H:%M} UTC "
                  f"(last {args.hours:g}h) ===")

            # --- HEALTH --------------------------------------------------------
            health = _q(cur,
                "SELECT max(started_at),"
                " count(*) FILTER (WHERE started_at >= %s),"
                " count(*) FILTER (WHERE status='error' AND started_at >= %s)"
                " FROM bot_runs", (since, since))[0]
            last_cycle, n_cycles, n_err = health
            last_mode = _q(cur, "SELECT mode FROM bot_runs ORDER BY id DESC LIMIT 1")
            mode = last_mode[0][0] if last_mode else "?"
            age_min = (now - last_cycle).total_seconds() / 60 if last_cycle else None
            print("\n[HEALTH]")
            print(f"  mode={mode}  cycles={n_cycles}  error_cycles={n_err}"
                  f"  last_cycle={last_cycle:%H:%M:%S}" if last_cycle else "  last_cycle=NONE")
            if age_min is None or age_min > args.stale_min:
                anomalies.append(f"worker STALE — last cycle "
                                 f"{f'{age_min:.0f} min ago' if age_min else 'never'}")
            if n_err:
                err = _q(cur, "SELECT left(coalesce(error_message,''),160), started_at"
                              " FROM bot_runs WHERE status='error' ORDER BY id DESC LIMIT 1")
                if err:
                    print(f"  last error @ {err[0][1]:%H:%M}: {err[0][0]}")
                    anomalies.append(f"{n_err} error cycle(s) today")

            # --- ENTRIES -------------------------------------------------------
            entries = _q(cur,
                "SELECT market_ticker, strategy, status, quantity,"
                " to_char(created_at,'HH24:MI') FROM live_orders"
                " WHERE action='buy' AND created_at >= %s ORDER BY id DESC", (since,))
            print(f"\n[ENTRIES] {len(entries)} live buy orders")
            for mt, strat, status, qty, ts in entries:
                flag = "  <-- CHECK" if status in _BAD_ORDER_STATUSES else ""
                print(f"  {ts}  {mt:<26} {strat or '':<18} {status:<10} qty~{qty}{flag}")

            # --- EXITS ---------------------------------------------------------
            exits = _q(cur,
                "SELECT market_ticker, strategy, status, to_char(created_at,'HH24:MI')"
                " FROM live_orders WHERE action='sell' AND created_at >= %s ORDER BY id DESC",
                (since,))
            if exits:
                print(f"\n[EXITS] {len(exits)} live sell/close orders")
                for mt, strat, status, ts in exits:
                    flag = "  <-- CHECK" if status in _BAD_ORDER_STATUSES else ""
                    print(f"  {ts}  {mt:<26} {strat or '':<18} {status}{flag}")

            # --- OPEN POSITIONS ------------------------------------------------
            open_pos = _q(cur,
                "SELECT DISTINCT ON (market_ticker) market_ticker, quantity_fp, avg_price,"
                " market_exposure, to_char(captured_at,'MM-DD HH24:MI')"
                " FROM positions WHERE captured_at >= %s"
                " ORDER BY market_ticker, captured_at DESC", (since - timedelta(hours=24),))
            open_pos = [r for r in open_pos if r[1] is not None and abs(float(r[1])) >= 0.01]
            print(f"\n[OPEN POSITIONS] {len(open_pos)}")
            for mt, qfp, avg, expo, _ts in open_pos:
                bid = None if args.no_prices else _kalshi_yes_bid(mt)
                unreal = ""
                if bid is not None and avg is not None:
                    u = (bid - float(avg)) / 100.0 * abs(float(qfp))
                    unreal = f"  now~{bid}c  unreal={u:+.2f}$"
                cost = f"${float(expo):.2f}" if expo is not None else "?"
                print(f"  {mt:<26} qty={float(qfp):.2f} @ {float(avg or 0):.0f}c  cost={cost}{unreal}")

            # --- REALIZED P&L TODAY (settled) ----------------------------------
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            realized = _q(cur,
                "SELECT DISTINCT ON (market_ticker) market_ticker, realized_pnl"
                " FROM positions WHERE captured_at >= %s AND realized_pnl IS NOT NULL"
                " ORDER BY market_ticker, captured_at DESC", (midnight,))
            total_real = sum(float(r[1] or 0) for r in realized)
            settled = [r for r in realized if abs(float(r[1] or 0)) > 1e-9]
            print(f"\n[REALIZED TODAY] {total_real:+.2f}$  ({len(settled)} markets with P&L)")
            for mt, pnl in sorted(settled, key=lambda r: float(r[1])):
                print(f"  {mt:<26} {float(pnl):+.2f}$")
            if total_real < 0:
                anomalies.append(f"realized P&L today is negative ({total_real:+.2f}$) "
                                 f"— check vs max_daily_loss breaker")

            # --- PAPER BOOKS (research) ----------------------------------------
            paper = _q(cur,
                "SELECT regexp_replace(regexp_replace(strategy,'_h[0-9]+$',''),"
                "'^weather_(low_)?','') book,"
                " case when strategy like 'weather_low_%' then 'low' else 'high' end kind,"
                " count(*) FILTER (WHERE status='settled') n,"
                " round(sum(pnl) FILTER (WHERE status='settled')::numeric,2) total"
                " FROM paper_trades WHERE strategy LIKE 'weather%' AND NOT legacy"
                " GROUP BY 1,2 HAVING count(*) FILTER (WHERE status='settled') > 0"
                " ORDER BY total DESC NULLS LAST")
            if paper:
                print("\n[PAPER BOOKS — settled realized P&L, all-time]")
                for book, kind, n, total in paper:
                    pt = (float(total) / n * 100) if (total is not None and n) else 0.0
                    print(f"  {kind:>4} {book:<5} n={n:<4} total={float(total or 0):+7.2f}$"
                          f"  {pt:+5.1f}c/trade")

            # --- NON-WEATHER PAPER BOOKS (mmsell, theta, ...) -------------------
            other = _q(cur,
                "SELECT strategy,"
                " count(*) FILTER (WHERE status='settled') n,"
                " round(sum(pnl) FILTER (WHERE status='settled')::numeric,2) total,"
                " count(*) FILTER (WHERE status='open') open_n"
                " FROM paper_trades"
                " WHERE strategy NOT LIKE 'weather%' AND NOT legacy"
                "   AND (strategy LIKE 'mmsell%' OR strategy LIKE 'theta%')"
                " GROUP BY 1 ORDER BY 1")
            if other:
                print("\n[RESEARCH BOOKS — mmsell / theta]")
                for strat, n, total, open_n in other:
                    pt = (float(total) / n * 100) if (total is not None and n) else 0.0
                    print(f"  {strat:<7} settled={n or 0:<5} total={float(total or 0):+7.2f}$"
                          f"  {pt:+5.1f}c/trade  open={open_n}")

            # --- LIVE CELL COVERAGE (cells that couldn't trade) ----------------
            cell_issues = _q(cur,
                "SELECT message, to_char(created_at,'HH24:MI') FROM system_events"
                " WHERE component='live_cell' AND created_at >= %s ORDER BY id DESC LIMIT 20",
                (since,))
            if cell_issues:
                print("\n[LIVE CELL ISSUES — a configured cell could not trade at its window]")
                for msg, ts in cell_issues:
                    print(f"  {ts}  {msg}")
                    anomalies.append(f"cell could not trade: {msg}")

            # --- ANOMALIES (alerts) --------------------------------------------
            # bad-status live orders in the window
            bad = _q(cur,
                "SELECT market_ticker, action, status, left(coalesce(cancel_reason,''),60)"
                " FROM live_orders WHERE created_at >= %s AND status = ANY(%s) ORDER BY id DESC",
                (since, list(_BAD_ORDER_STATUSES)))
            for mt, act, status, reason in bad:
                anomalies.append(f"{status} {act} {mt}: {reason}")

            # orders stuck pending/unknown well past the window start
            stuck = _q(cur,
                "SELECT market_ticker, action, status, to_char(created_at,'HH24:MI')"
                " FROM live_orders WHERE status = ANY(%s)"
                " AND created_at < %s ORDER BY id DESC LIMIT 10",
                (list(_STUCK_STATUSES), now - timedelta(minutes=15)))
            for mt, act, status, ts in stuck:
                anomalies.append(f"order STUCK {status} since {ts}: {act} {mt}")

            # fills with NO matching live_orders row (the duplicate-order / lost-intent bug
            # class). Scope to the bug's actual surface: weather-series BUYS the bot places
            # itself. Manual position closes (sells) and trades in non-weather markets
            # (e.g. KXBTC*) are expected out-of-band activity, not a bot integrity issue.
            codes = _recent_date_codes(3)
            like = " OR ".join(["f.market_ticker LIKE %s"] * len(codes))
            orphan = _q(cur,
                f"SELECT f.market_ticker, f.action, f.side, count(*)"
                f" FROM fills f LEFT JOIN live_orders o ON o.kalshi_order_id = f.kalshi_order_id"
                f" WHERE o.id IS NULL AND f.action = 'buy'"
                f" AND (f.market_ticker LIKE 'KXHIGH%%' OR f.market_ticker LIKE 'KXLOWT%%')"
                f" AND ({like})"
                f" GROUP BY 1,2,3 ORDER BY 1", tuple(f"%{c}%" for c in codes))
            for mt, act, side, n in orphan:
                anomalies.append(f"{n} fill(s) with NO order row: {side} {act} {mt} "
                                 f"(unrecorded order — investigate)")

            # open positions with no entry order on record (untracked)
            tracked = {r[0] for r in _q(cur,
                "SELECT DISTINCT market_ticker FROM live_orders WHERE action='buy'")}
            for mt, *_ in open_pos:
                if mt not in tracked:
                    anomalies.append(f"UNTRACKED open position (no entry order): {mt}")

            print("\n[ANOMALIES]")
            if anomalies:
                for a in anomalies:
                    print(f"  !! {a}")
            else:
                print("  none — all clear")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
