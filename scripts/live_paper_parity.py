"""live/paper PARITY — is our paper trading system telling us the truth about a live book?

Every strategy promoted to real money runs a fresh paper TWIN beside it: same start instant, same
market selection, and the LIVE parameters (entry price rule, dollar sizing, open cap, spread gate).
The single remaining difference is that the twin assumes its resting order fills and the live book
has to actually get filled. This script reads that experiment out.

Sections:
  1. EPOCHS            — each configured twin, its start, and the size of both sides so far.
  2. DECISION ALIGNMENT— the per-candidate tape: did live attempt the same markets as its twin,
                         and when it didn't, exactly which gate stopped it.
  3. EXECUTION REALISM — fill rate on live attempts, and live fill price vs the price the twin
                         assumed (a price gap is a paper assumption error, not adverse selection).
  4. THE MIRAGE READ   — settled outcomes: twin paper vs real live, plus the SAME-MARKET matched
                         pairs, which is the read that separates a wrong simulator from a real
                         edge lost to execution. Also shows the incumbent paper book both over the
                         epoch and all-time, i.e. what the naive comparison would have claimed.
  5. ANOMALIES         — param drift, pre-epoch rows, footprint imbalance. Lead with this.

Read-only, self-contained (stdlib + psycopg), so it runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/live_paper_parity.py
    # or:  {"type": "script", "name": "live_paper_parity"}
    # args: --twin mmsell10_pt   (one pair)   --days 7   (trailing window inside the epoch)

Everything degrades to "(none yet)" before a twin has activity, so it is safe to run the moment a
live test is armed. Interpretation and gates: docs/LIVE_PAPER_TWIN.md.
"""

from __future__ import annotations

import argparse
import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

# A per-contract gap this small is inside noise/fee-rounding; beyond it, something is off.
ALIGNED_TOLERANCE_CENTS = 0.5
MIN_N_FOR_VERDICT = 30


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def q(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def _pct(num: float | None, den: float | None) -> str:
    return f"{(num or 0) / den * 100:.1f}%" if den else "n/a"


def _c(v) -> str:
    return f"{float(v):+.2f}c" if v is not None else "n/a"


def _f(v, default=None):
    return default if v is None else float(v)


# --- 1. epochs ---------------------------------------------------------------


def load_epochs(cur, only_twin: str | None) -> list[dict]:
    sql = ("SELECT twin_tag, live_tag, started_at, ended_at, params_json"
           " FROM live_paper_twins")
    params: tuple = ()
    if only_twin:
        sql += " WHERE twin_tag = %s"
        params = (only_twin,)
    sql += " ORDER BY started_at"
    return [
        {"twin_tag": r[0], "live_tag": r[1], "started_at": r[2], "ended_at": r[3],
         "params": r[4] or {}}
        for r in q(cur, sql, params)
    ]


def report_epochs(cur, epochs: list[dict], days: int) -> None:
    print("=== 1. Twin epochs (the shared window both sides are scoped to) ===")
    if not epochs:
        print("  (no twin epochs yet — a twin epoch is written the first cycle its live tag is")
        print("   armed: BOT_MODE=live, KILL_SWITCH=false, LIVE_ENABLED=true, tag in LIVE_STRATEGIES)")
        return
    print(f"  {'twin':14s} {'live':10s} {'started (UTC)':20s} {'age_h':>6} {'twin_tr':>7}"
          f" {'twin_set':>8} {'live_ord':>8} {'state':>8}")
    for e in epochs:
        since = _since(cur, e, days)
        twin_n = q(cur, "SELECT count(*), count(*) FILTER (WHERE status='settled')"
                        " FROM paper_trades WHERE strategy=%s AND created_at>=%s",
                   (e["twin_tag"], since))[0]
        live_n = q(cur, "SELECT count(*) FROM live_orders WHERE strategy=%s AND action='buy'"
                        " AND created_at>=%s", (e["live_tag"], since))[0][0]
        age = q(cur, "SELECT EXTRACT(EPOCH FROM (now()-%s))/3600.0", (e["started_at"],))[0][0]
        state = "ended" if e["ended_at"] else "running"
        print(f"  {e['twin_tag']:14s} {e['live_tag']:10s}"
              f" {str(e['started_at'])[:19]:20s} {float(age or 0):6.1f}"
              f" {int(twin_n[0]):7d} {int(twin_n[1]):8d} {int(live_n):8d} {state:>8}")
    if days:
        print(f"  (counts scoped to the trailing {days}d inside each epoch)")


# --- 2. decision alignment ---------------------------------------------------


def report_alignment(cur, epochs: list[dict], days: int) -> None:
    print("\n=== 2. Decision alignment (per-candidate tape: did live see the same markets?) ===")
    any_rows = False
    for e in epochs:
        rows = q(cur,
                 "SELECT coalesce(twin_outcome,'(none)'), coalesce(live_outcome,'(none)'),"
                 " count(*) FROM live_paper_parity_events"
                 " WHERE twin_tag=%s AND recorded_at>=%s"
                 " GROUP BY 1,2 ORDER BY 3 DESC",
                 (e["twin_tag"], _since(cur, e, days)))
        if not rows:
            continue
        any_rows = True
        total = sum(int(r[2]) for r in rows)
        print(f"\n  {e['twin_tag']} vs live {e['live_tag']} — {total} candidate decisions")
        print(f"    {'twin did':20s} {'live did':22s} {'n':>7} {'share':>7}")
        for t_out, l_out, n in rows[:14]:
            print(f"    {t_out:20s} {l_out:22s} {int(n):7d} {_pct(int(n), total):>7}")
        agreed = q(cur,
                   "SELECT count(*) FROM live_paper_parity_events WHERE twin_tag=%s"
                   " AND recorded_at>=%s AND twin_outcome='opened' AND live_outcome='placed'",
                   (e["twin_tag"], _since(cur, e, days)))[0][0]
        twin_open = q(cur,
                      "SELECT count(*) FROM live_paper_parity_events WHERE twin_tag=%s"
                      " AND recorded_at>=%s AND twin_outcome='opened'",
                      (e["twin_tag"], _since(cur, e, days)))[0][0]
        print(f"    -> twin opened {int(twin_open)}; live placed on {int(agreed)} of them"
              f" ({_pct(int(agreed), int(twin_open))} decision overlap)")
        gates = q(cur,
                  "SELECT live_outcome, count(*) FROM live_paper_parity_events"
                  " WHERE twin_tag=%s AND recorded_at>=%s AND twin_outcome='opened'"
                  " AND live_outcome<>'placed' GROUP BY 1 ORDER BY 2 DESC",
                  (e["twin_tag"], _since(cur, e, days)))
        if gates:
            print("    where live did NOT follow its twin:")
            for out, n in gates:
                print(f"      {out or '(none)':24s} {int(n):6d}")
            print("      (a gate-dominated gap is a CAPACITY difference, not evidence about edge:")
            print("       the twin traded candidates live was never allowed to attempt)")
    if not any_rows:
        print("  (no parity events yet — they are written per in-band candidate once a twin is armed)")


def _since(cur, epoch: dict, days: int):
    """Window start = epoch start, clamped up to `days` ago when a trailing window is requested."""
    if not days:
        return epoch["started_at"]
    return q(cur, "SELECT GREATEST(%s, now() - (%s || ' days')::interval)",
             (epoch["started_at"], str(days)))[0][0]


# --- 3. execution realism ----------------------------------------------------


def report_execution(cur, epochs: list[dict], days: int) -> None:
    print("\n=== 3. Execution realism (the one thing paper structurally cannot test) ===")
    print(f"  {'twin':14s} {'live_ord':>8} {'filled':>6} {'cxl':>5} {'rest':>5} {'fill%':>6}"
          f" {'live_px':>8} {'fill_px':>8} {'twin_px':>8} {'px_gap':>7}")
    for e in epochs:
        since = _since(cur, e, days)
        st = dict(q(cur, "SELECT coalesce(status,'?'), count(*) FROM live_orders"
                         " WHERE strategy=%s AND action='buy' AND created_at>=%s GROUP BY 1",
                    (e["live_tag"], since)))
        placed = sum(int(v) for v in st.values())
        filled = int(st.get("filled", 0))
        cxl = int(st.get("canceled", 0))
        rest = int(st.get("resting", 0)) + int(st.get("submitted", 0)) + int(st.get("partial", 0))
        live_px = q(cur, "SELECT avg(limit_price) FROM live_orders WHERE strategy=%s"
                         " AND action='buy' AND created_at>=%s", (e["live_tag"], since))[0][0]
        # fills.price is recorded on the SIDE TRADED (executor.reconcile: side='no' -> stores
        # no_price_dollars directly) — for mmsell's buy-NO fills that's already the NO cost
        # basis, matching live_orders.limit_price. No 100-x conversion (that would double-flip
        # an already-NO price into a bogus ~7c "no cost" on a ~93c maker fill).
        fill_px = q(cur,
                    "SELECT avg(f.price) FROM fills f JOIN live_orders lo"
                    "  ON lo.kalshi_order_id=f.kalshi_order_id"
                    " WHERE lo.strategy=%s AND lo.created_at>=%s", (e["live_tag"], since))[0][0]
        twin_px = q(cur, "SELECT avg(assumed_price) FROM paper_trades WHERE strategy=%s"
                         " AND created_at>=%s", (e["twin_tag"], since))[0][0]
        gap = None
        if fill_px is not None and twin_px is not None:
            gap = float(fill_px) - float(twin_px)
        print(f"  {e['twin_tag']:14s} {placed:8d} {filled:6d} {cxl:5d} {rest:5d}"
              f" {_pct(filled, filled + cxl):>6}"
              f" {(f'{float(live_px):.1f}c' if live_px is not None else 'n/a'):>8}"
              f" {(f'{float(fill_px):.1f}c' if fill_px is not None else 'n/a'):>8}"
              f" {(f'{float(twin_px):.1f}c' if twin_px is not None else 'n/a'):>8}"
              f" {(f'{gap:+.2f}c' if gap is not None else 'n/a'):>7}")
    print("  (fill% = filled/(filled+canceled); resting orders are still working.")
    print("   px_gap = real NO cost basis minus what the twin assumed. Non-zero means our PRICING")
    print("   assumption is wrong — a different failure from simply not getting filled.)")


# --- 4. the mirage read ------------------------------------------------------


def _live_settled(cur, live_tag: str, since) -> tuple[int, int, float, float]:
    """(positions, wins, total_pnl_dollars, contracts) for live positions opened in-window and
    now flat. Contracts come from fills so the per-contract number is real, not per-position."""
    rows = q(cur,
             "WITH ord AS (SELECT DISTINCT market_ticker FROM live_orders"
             "   WHERE strategy=%s AND action='buy' AND created_at>=%s),"
             " ct AS (SELECT f.market_ticker, sum(f.quantity) qty FROM fills f"
             "   JOIN live_orders lo ON lo.kalshi_order_id=f.kalshi_order_id"
             "   WHERE lo.strategy=%s AND lo.created_at>=%s GROUP BY 1),"
             " latest AS (SELECT DISTINCT ON (p.market_ticker) p.market_ticker, p.quantity,"
             "   p.realized_pnl FROM positions p JOIN ord ON ord.market_ticker=p.market_ticker"
             "   ORDER BY p.market_ticker, p.captured_at DESC)"
             " SELECT count(*), count(*) FILTER (WHERE l.realized_pnl>0),"
             "   coalesce(sum(l.realized_pnl),0), coalesce(sum(ct.qty),0)"
             " FROM latest l LEFT JOIN ct ON ct.market_ticker=l.market_ticker"
             " WHERE l.quantity=0 AND l.realized_pnl IS NOT NULL",
             (live_tag, since, live_tag, since))
    n, wins, pnl, qty = rows[0] if rows else (0, 0, 0, 0)
    return int(n or 0), int(wins or 0), _f(pnl, 0.0), _f(qty, 0.0)


def _paper_settled(cur, tag: str, since=None) -> tuple[int, int, float, float]:
    sql = ("SELECT count(*), count(*) FILTER (WHERE pnl>0), coalesce(sum(pnl),0),"
           " coalesce(sum(quantity),0) FROM paper_trades"
           " WHERE strategy=%s AND status='settled' AND NOT legacy")
    params: tuple = (tag,)
    if since is not None:
        sql += " AND created_at>=%s"
        params = (tag, since)
    n, wins, pnl, qty = q(cur, sql, params)[0]
    return int(n or 0), int(wins or 0), _f(pnl, 0.0), _f(qty, 0.0)


def _per_ct(pnl: float, contracts: float) -> float | None:
    return (pnl / contracts) * 100.0 if contracts else None


def _row(label: str, n: int, wins: int, pnl: float, qty: float) -> str:
    return (f"    {label:26s} {n:6d} {_pct(wins, n):>8} {pnl:+9.2f}$"
            f" {(_c(_per_ct(pnl, qty))):>9}")


def report_mirage(cur, epochs: list[dict], days: int, show_parent: bool) -> list[str]:
    print("\n=== 4. The mirage read (settled outcomes on the SAME window) ===")
    verdicts: list[str] = []
    for e in epochs:
        since = _since(cur, e, days)
        twin = _paper_settled(cur, e["twin_tag"], since)
        live = _live_settled(cur, e["live_tag"], since)
        print(f"\n  {e['twin_tag']} vs live {e['live_tag']}")
        print(f"    {'book':26s} {'n':>6} {'win%':>8} {'total':>10} {'per_ct':>9}")
        print(_row("twin paper (assumes fill)", *twin))
        print(_row("LIVE real money", *live))
        if show_parent:
            print(_row("incumbent paper, in-epoch", *_paper_settled(cur, e["live_tag"], since)))
            print(_row("incumbent paper, all-time", *_paper_settled(cur, e["live_tag"], None)))
            print("    (the all-time row is what a naive paper-vs-live comparison would have")
            print("     claimed: different sample, different regime, different sizing)")

        matched = _matched_pairs(cur, e, since)
        verdicts.append(_verdict(e, twin, live, matched))
        if matched["n"]:
            print(f"    matched markets (both sides settled the same ticker): n={matched['n']}")
            print(f"      twin  {_c(matched['twin_per_ct'])}/ct   "
                  f"live {_c(matched['live_per_ct'])}/ct   "
                  f"gap {_c(matched['gap'])}")
            print("      (this is the accounting check: same market, same side, same window —")
            print("       a gap here indicts the SIMULATOR, not adverse selection)")
        else:
            print("    matched markets: (none yet — needs a ticker both sides settled)")
    return verdicts


def _matched_pairs(cur, epoch: dict, since) -> dict:
    """Twin paper trades and live positions on the SAME ticker, both settled in-window."""
    rows = q(cur,
             "WITH tw AS (SELECT market_ticker, sum(pnl) pnl, sum(quantity) qty"
             "   FROM paper_trades WHERE strategy=%s AND status='settled' AND NOT legacy"
             "   AND created_at>=%s GROUP BY 1),"
             " ord AS (SELECT DISTINCT market_ticker FROM live_orders"
             "   WHERE strategy=%s AND action='buy' AND created_at>=%s),"
             " ct AS (SELECT f.market_ticker, sum(f.quantity) qty FROM fills f"
             "   JOIN live_orders lo ON lo.kalshi_order_id=f.kalshi_order_id"
             "   WHERE lo.strategy=%s AND lo.created_at>=%s GROUP BY 1),"
             " latest AS (SELECT DISTINCT ON (p.market_ticker) p.market_ticker, p.quantity,"
             "   p.realized_pnl FROM positions p JOIN ord ON ord.market_ticker=p.market_ticker"
             "   ORDER BY p.market_ticker, p.captured_at DESC)"
             " SELECT count(*), coalesce(sum(tw.pnl),0), coalesce(sum(tw.qty),0),"
             "   coalesce(sum(l.realized_pnl),0), coalesce(sum(ct.qty),0)"
             " FROM tw JOIN latest l ON l.market_ticker=tw.market_ticker"
             " LEFT JOIN ct ON ct.market_ticker=tw.market_ticker"
             " WHERE l.quantity=0 AND l.realized_pnl IS NOT NULL",
             (epoch["twin_tag"], since, epoch["live_tag"], since, epoch["live_tag"], since))
    n, t_pnl, t_qty, l_pnl, l_qty = rows[0] if rows else (0, 0, 0, 0, 0)
    twin_per_ct = _per_ct(_f(t_pnl, 0.0), _f(t_qty, 0.0))
    live_per_ct = _per_ct(_f(l_pnl, 0.0), _f(l_qty, 0.0))
    gap = None
    if twin_per_ct is not None and live_per_ct is not None:
        gap = twin_per_ct - live_per_ct
    return {"n": int(n or 0), "twin_per_ct": twin_per_ct, "live_per_ct": live_per_ct, "gap": gap}


def _verdict(epoch: dict, twin, live, matched: dict) -> str:
    """Separate the two failure modes explicitly.

    A book-level gap with clean matched markets is EXECUTION: the simulator prices each market
    right, but live can't capture the same trades (fills, gates, adverse selection). A gap on the
    matched markets themselves is an ACCOUNTING error — our paper model of a trade we DID get is
    wrong, which is the mirage that would invalidate every paper book we gate on."""
    tag = epoch["twin_tag"]
    t_n, _t_w, t_pnl, t_qty = twin
    l_n, _l_w, l_pnl, l_qty = live
    t_ct, l_ct = _per_ct(t_pnl, t_qty), _per_ct(l_pnl, l_qty)
    if t_n < MIN_N_FOR_VERDICT or l_n < MIN_N_FOR_VERDICT:
        return (f"{tag}: TOO EARLY — twin n={t_n}, live n={l_n} (need >={MIN_N_FOR_VERDICT} each);"
                f" keep both running, the epoch is the sample.")
    book_gap = None if (t_ct is None or l_ct is None) else t_ct - l_ct
    m_gap = matched["gap"]
    if m_gap is not None and matched["n"] >= MIN_N_FOR_VERDICT \
            and abs(m_gap) > ALIGNED_TOLERANCE_CENTS:
        return (f"{tag}: ACCOUNTING GAP — same markets, same window, but paper says"
                f" {_c(matched['twin_per_ct'])}/ct and live realized {_c(matched['live_per_ct'])}/ct"
                f" ({_c(m_gap)} apart on n={matched['n']}). The simulator itself is wrong; fix it"
                f" before trusting ANY paper book's gate.")
    if book_gap is not None and abs(book_gap) > ALIGNED_TOLERANCE_CENTS:
        return (f"{tag}: EXECUTION GAP — per-market accounting agrees, but the books diverge"
                f" ({_c(book_gap)}/ct: twin {_c(t_ct)} vs live {_c(l_ct)}). The paper edge is real"
                f" arithmetic that live execution is not capturing — see §2 gates and §3 fill%.")
    if book_gap is not None:
        return (f"{tag}: ALIGNED — twin {_c(t_ct)}/ct vs live {_c(l_ct)}/ct"
                f" (within {ALIGNED_TOLERANCE_CENTS}c). Paper is a fair model of this book;"
                f" its paper gates can be trusted for sizing decisions.")
    return f"{tag}: INCOMPLETE — not enough contract data on one side to compute per-contract P&L."


# --- 5. anomalies -----------------------------------------------------------


def report_anomalies(cur, epochs: list[dict]) -> None:
    print("\n=== 5. ANOMALIES ===")
    found: list[str] = []
    # Standing policy check: any strategy that has placed real orders should have a twin epoch.
    untwinned = q(cur,
                  "SELECT lo.strategy, count(*) FROM live_orders lo"
                  " WHERE lo.action='buy' AND lo.strategy IS NOT NULL"
                  " AND NOT EXISTS (SELECT 1 FROM live_paper_twins t"
                  "                 WHERE t.live_tag = lo.strategy)"
                  " GROUP BY 1 ORDER BY 2 DESC")
    for strat, n in untwinned:
        found.append(f"{strat}: {int(n)} live orders with NO paper twin configured —"
                     " this live book cannot be checked for paper/live alignment")
    for e in epochs:
        pre_twin = q(cur, "SELECT count(*) FROM paper_trades WHERE strategy=%s AND created_at<%s",
                     (e["twin_tag"], e["started_at"]))[0][0]
        if int(pre_twin or 0):
            found.append(f"{e['twin_tag']}: {int(pre_twin)} paper trades PREDATE the epoch —"
                         " the twin tag was reused; a twin must be a fresh tag")
        pre_live = q(cur, "SELECT count(*) FROM live_orders WHERE strategy=%s AND action='buy'"
                          " AND created_at<%s", (e["live_tag"], e["started_at"]))[0][0]
        if int(pre_live or 0):
            found.append(f"{e['live_tag']}: {int(pre_live)} live orders predate the twin epoch —"
                         " excluded from every number above (the windows must match)")
        drift = q(cur, "SELECT count(*) FROM system_events WHERE component='twin'"
                       " AND message LIKE %s", (f"twin {e['twin_tag']} params drifted%",))[0][0]
        if int(drift or 0):
            found.append(f"{e['twin_tag']}: PARAM DRIFT logged — live knobs changed mid-epoch,"
                         " so the comparison is no longer one-to-one. Start a new twin tag.")
        twin_open = q(cur, "SELECT count(*) FROM paper_positions WHERE strategy=%s"
                           " AND status='open'", (e["twin_tag"],))[0][0]
        live_open = q(cur,
                      "WITH ord AS (SELECT DISTINCT market_ticker FROM live_orders"
                      "   WHERE strategy=%s AND action='buy' AND created_at>=%s),"
                      " latest AS (SELECT DISTINCT ON (p.market_ticker) p.market_ticker,"
                      "   p.quantity_fp FROM positions p JOIN ord"
                      "   ON ord.market_ticker=p.market_ticker"
                      "   ORDER BY p.market_ticker, p.captured_at DESC)"
                      " SELECT count(*) FROM latest WHERE abs(coalesce(quantity_fp,0))>0.01",
                      (e["live_tag"], e["started_at"]))[0][0]
        placed_never_twinned = q(cur,
                                 "SELECT count(*) FROM live_paper_parity_events"
                                 " WHERE twin_tag=%s AND live_outcome='placed'"
                                 " AND coalesce(twin_outcome,'x')<>'opened'",
                                 (e["twin_tag"],))[0][0]
        if int(placed_never_twinned or 0):
            found.append(f"{e['twin_tag']}: live placed {int(placed_never_twinned)} orders its twin"
                         " did NOT open — the twin is more constrained than live (check its cap)")
        print(f"  {e['twin_tag']}: open twin={int(twin_open or 0)} live={int(live_open or 0)}")
    if found:
        for f in found:
            print(f"  ! {f}")
    else:
        print("  all clear")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="live/paper twin parity report")
    ap.add_argument("--twin", default=None, help="restrict to one twin tag")
    ap.add_argument("--days", type=int, default=0,
                    help="trailing window inside the epoch (0 = whole epoch)")
    ap.add_argument("--no-parent", action="store_true",
                    help="skip the incumbent paper book reference rows")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            epochs = load_epochs(cur, args.twin)
            report_epochs(cur, epochs, args.days)
            if epochs:
                report_alignment(cur, epochs, args.days)
                report_execution(cur, epochs, args.days)
                verdicts = report_mirage(cur, epochs, args.days, not args.no_parent)
            report_anomalies(cur, epochs)
            if epochs:
                print("\n=== VERDICT ===")
                for v in verdicts:
                    print(f"  {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
