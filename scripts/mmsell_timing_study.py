"""mmsell ENTRY-TIMING study — does WHEN we enter matter, measured on the right clock per mode?

THE QUESTION
------------
A backtest over Kalshi's settled h2h history found a U-shape in entry timing: selling the cheap
tail paid pre-game (+5.4c) and in the final hour (+6.57c) but lost through the 1-4h in-play
middle (-5.75c / -1.9c). That study covered 1,137 h2h entries and nothing else, so "does this
apply to the rest of the book" was open. This script answers it on OUR trades.

THE TRAP THAT SHAPES THE WHOLE DESIGN
-------------------------------------
`hours_to_close` (from Kalshi's `close_time`, captured per candidate in `mmsell_candidate_ticks`)
is a VALID clock for scheduled and discrete markets and a FICTION for in-play ones. Kalshi sets
`close_time` on a sports market to a far-future fallback, not the end of the contest. Measured
2026-08-05 over our own book:

    series          htc at entry   actual time to resolution
    KXUFCFIGHT          335.0h              0.4h
    KXITFMATCH          333.3h              0.4h
    KXMLSGAME           334.3h              0.6h
    KXMLBGAME            70.6h              1.5h
    ---------------------------------------------------------
    KXBTCD               31.0h             30.6h     (gap 0.5)
    KXTRUMPSAY           72.3h             70.6h     (gap 1.7)
    KXRT                 90.8h             90.7h     (gap 0.2)

The sports numbers are pinned at ~335h because that is our own `htcmax=336` cap — the market's
reported close is even further out. Bucketing in-play trades by `hours_to_close` therefore files
every one of them under "24-72h" or "72h+" and measures nothing. It also means the global
`mmsell_min_hours_to_close = 1.0` floor NEVER BINDS on sports: the books already enter deep
in-play (0.4-1.5h of contest left) without any timing rule saying so.

So this study uses a DIFFERENT CLOCK PER MODE:

  in_play              realized time-to-resolution = closed_at - created_at. Ground truth for
                       "how much contest was left when we entered", available for the FULL trade
                       history rather than only the candidate-tick window.
  scheduled/discrete   hours_to_close at entry, from the candidate tick nearest the entry.

CAVEATS ON THE IN-PLAY CLOCK (both real, both bounded)
------------------------------------------------------
* It is measured at settlement DETECTION, not the instant of resolution, so it overstates the
  true remaining time by up to one management cycle. That bias is constant in absolute terms and
  therefore matters most in the shortest buckets — read `<0.25h` as "detected within a cycle",
  not as a precise quarter-hour.
* It is only knowable AFTER the fact, so it cannot gate a live entry. A live in-play timing rule
  needs a forward-looking field (Kalshi's `expected_expiration_time`), which the worker does not
  yet persist. This script measures whether such a rule would be WORTH building.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_timing_study.py
    # or:  {"type": "script", "name": "mmsell_timing_study"}
    #      {"type": "script", "name": "mmsell_timing_study", "args": ["--maxyes", "7"]}
    #      {"type": "script", "name": "mmsell_timing_study", "args": ["--book", "mmsell10"]}

Taxonomy and the per-cell statistics are imported from scripts/mmsell_market_types.py rather than
re-declared, so a type can never mean one thing in the census and another here. The bucketing is a
pure function unit-tested in tests/test_mmsell_timing_study.py.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import mmsell_fill_model as fm
import mmsell_market_types as mt

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=180000 "
    "-c idle_in_transaction_session_timeout=180000"
)

# Per-mode window grids, as (label, lower_bound_hours) with the upper bound implied by the next
# entry. They differ because the mechanism differs: an in-play contest resolves over minutes to
# hours, while a scheduled print sits days out and only its final hours are interesting.
WINDOWS: dict[str, tuple[tuple[str, float], ...]] = {
    mt.IN_PLAY: (
        ("<0.25h", 0.0), ("0.25-0.5h", 0.25), ("0.5-1h", 0.5),
        ("1-2h", 1.0), ("2-4h", 2.0), ("4-12h", 4.0), ("12h+", 12.0),
    ),
    mt.SCHEDULED: (
        ("<2h", 0.0), ("2-4h", 2.0), ("4-8h", 4.0),
        ("8-24h", 8.0), ("24-72h", 24.0), ("72h+", 72.0),
    ),
    mt.DISCRETE: (
        ("<2h", 0.0), ("2-4h", 2.0), ("4-8h", 4.0),
        ("8-24h", 8.0), ("24-72h", 24.0), ("72h+", 72.0),
    ),
}

# Which clock each mode is scored on. See the module docstring — this mapping IS the finding.
CLOCK = {
    mt.IN_PLAY: "hold",   # realized time-to-resolution
    mt.SCHEDULED: "htc",  # hours_to_close at entry
    mt.DISCRETE: "htc",
}


def bucket_of(hours: float | None, mode: str) -> str | None:
    """Window label for `hours` under `mode`'s grid, or None when unbucketable.

    Negative hours cannot happen on a valid clock (an entry never post-dates its own
    resolution); returning None rather than silently flooring them into the first bucket keeps a
    clock/data fault visible as coverage loss instead of as a fake edge in the shortest cell."""
    grid = WINDOWS.get(mode)
    if grid is None or hours is None or hours < 0:
        return None
    label = grid[0][0]
    for lbl, lo in grid:
        if hours >= lo:
            label = lbl
        else:
            break
    return label


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def load_trades(cur, include_twins: bool = False) -> list[dict]:
    """Every settled mmsell trade with BOTH clocks attached where available.

    The candidate tick is matched to the entry within +/-10 minutes (the entry scan captures the
    tick off the same orderbook fetch in the same cycle, so a real match is near-simultaneous;
    the window only absorbs cycle jitter). Trades predating the capture simply have htc=None,
    which costs nothing for in-play rows since they are scored on the hold clock anyway."""
    twin_clause = "" if include_twins else \
        " AND p.strategy NOT IN (SELECT twin_tag FROM live_paper_twins)"
    cur.execute(
        "SELECT p.market_ticker, p.strategy, p.assumed_price, p.quantity, p.pnl,"
        "       extract(epoch FROM (p.closed_at - p.created_at))/3600.0 AS hold_h,"
        "       c.htc"
        " FROM paper_trades p"
        " LEFT JOIN LATERAL ("
        "   SELECT ct.hours_to_close AS htc FROM mmsell_candidate_ticks ct"
        "   WHERE ct.market_ticker = p.market_ticker"
        "     AND ct.captured_at BETWEEN p.created_at - interval '10 minutes'"
        "                            AND p.created_at + interval '10 minutes'"
        "   ORDER BY abs(extract(epoch FROM (ct.captured_at - p.created_at))) LIMIT 1"
        " ) c ON true"
        " WHERE p.strategy LIKE '%%mmsell%%' AND p.status IN ('settled','closed_sl')"
        "   AND NOT coalesce(p.legacy,false) AND p.pnl IS NOT NULL"
        "   AND p.quantity IS NOT NULL AND p.quantity > 0"
        + twin_clause,
    )
    out: list[dict] = []
    for tkr, book, px, qty, pnl, hold_h, htc in cur.fetchall():
        series = mt.series_of(tkr)
        mtype, mode = mt.classify(series)
        out.append({
            "ticker": tkr, "series": series, "book": book,
            "type": mtype, "mode": mode,
            "entry_c": (100 - int(px)) if px is not None else None,
            "pnl_c": float(pnl) / int(qty) * 100.0,
            "hold_h": float(hold_h) if hold_h is not None else None,
            "htc": float(htc) if htc is not None else None,
        })
    return out


def clock_hours(row: dict) -> float | None:
    """The hours-to-resolution this row is scored on, per its mode's clock."""
    return row["hold_h"] if CLOCK.get(row["mode"]) == "hold" else row["htc"]


# --- rendering -----------------------------------------------------------------------------

def _f(x, spec="{:+.2f}") -> str:
    return spec.format(x) if x is not None else "n/a"


HDR = (f"  {'window':>10} {'n':>6} {'mkts':>5} {'c/trade':>9} {'REAL':>8} {'cov':>5}"
       f" {'loss%':>6} {'be%':>6} {'edge':>7} {'avgloss':>8} {'worst':>7} {'p5':>7}"
       f" {'p50':>6} {'entry':>6}")


def realizable_of(rows: list[dict], calib: dict | None) -> tuple[float | None, float | None]:
    """(realizable cents/contract, coverage) for one cell, projected through the LIVE maker-fill
    calibration — or (None, None) without a calibration.

    This is the column a timing rule must be gated on, not `c/trade`. Paper assumes a resting
    maker order always fills; live it fills ~70% and misses the winners. The endgame windows are
    the thinnest, fastest books in the whole universe, which is exactly where that gap is widest
    — and where the two previous timing signals (mmsell7's htcmax=24, mmsell11's htcmin=6) both
    died. See docs/MMSELL_FILL_MODEL.md."""
    if not calib:
        return (None, None)
    hist: dict[int, list] = defaultdict(lambda: [0, 0.0])
    for r in rows:
        if r["entry_c"] is None:
            continue
        cell = hist[int(r["entry_c"])]
        cell[0] += 1
        cell[1] += r["pnl_c"]
    if not hist:
        return (None, None)
    res = fm.project_realizable({k: tuple(v) for k, v in hist.items()}, calib)
    cover = (res["covered_n"] / res["total_n"]) if res["total_n"] else 0.0
    return (res["est_realizable_cents"], cover)


def _print_cells(rows_by_bucket: dict, order: tuple[str, ...], calib: dict | None = None) -> None:
    print(HDR)
    for lbl in order:
        rows = rows_by_bucket.get(lbl)
        if not rows:
            continue
        s = mt.summarize(rows)
        be = 100.0 * s["be_loss"] if s["be_loss"] is not None else None
        real, cover = realizable_of(rows, calib)
        print(f"  {lbl:>10} {s['n']:6d} {s['mkts']:5d} {s['mean']:>+8.2f}c"
              f" {_f(real, '{:+7.2f}'):>7}c {_f(100.0*cover if cover is not None else None, '{:4.0f}'):>4}%"
              f" {100.0*s['loss_rate']:5.1f}% {_f(be, '{:5.1f}'):>5}% {_f(s['edge_pp'], '{:+6.1f}'):>7}"
              f" {_f(s['avg_loss'], '{:+7.1f}'):>8} {s['worst']:+6.0f}c"
              f" {_f(s['p5'], '{:+6.1f}'):>7} {_f(s['p50'], '{:+5.1f}'):>6}"
              f" {_f(s['entry'], '{:5.1f}'):>6}")


def print_clock_validation(trades: list[dict]) -> None:
    """Why each mode is scored on the clock it is scored on — printed FIRST, every run.

    This is a guardrail, not decoration. `hours_to_close` looks like the obvious timing variable
    and is wrong for in-play markets by two orders of magnitude; anyone who switches the clock
    back should see the evidence against it in the same output."""
    print("=== CLOCK VALIDATION (why in-play cannot use hours_to_close) ===")
    print(f"  {'mode':>10} {'n_with_both':>12} {'avg_htc':>9} {'avg_hold':>9} {'gap':>9}  clock used")
    for mode in (mt.IN_PLAY, mt.SCHEDULED, mt.DISCRETE):
        both = [t for t in trades
                if t["mode"] == mode and t["htc"] is not None and t["hold_h"] is not None]
        if not both:
            print(f"  {mode:>10} {0:>12}       n/a       n/a       n/a  {CLOCK[mode]}")
            continue
        a_htc = sum(t["htc"] for t in both) / len(both)
        a_hold = sum(t["hold_h"] for t in both) / len(both)
        print(f"  {mode:>10} {len(both):>12} {a_htc:>8.1f}h {a_hold:>8.1f}h {a_htc-a_hold:>8.1f}h"
              f"  {CLOCK[mode]}")
    print("  A gap near zero means close_time IS the resolution time and htc is trustworthy."
          "\n  A gap of hundreds of hours means Kalshi's close_time is a far-future fallback and"
          "\n  htc measures nothing — in-play is scored on realized time-to-resolution instead.")


def report(trades: list[dict], maxyes: int | None, by_type: bool, min_n: int,
           calib: dict | None = None) -> None:
    if not trades:
        print("(no settled mmsell trades matched)")
        return
    if maxyes is not None:
        trades = [t for t in trades if t["entry_c"] is not None and t["entry_c"] <= maxyes]
        print(f"*** ENTRY-PRICE FILTER: yes <= {maxyes}c — {len(trades)} trades ***\n")
        if not trades:
            print("(nothing in band)")
            return

    print_clock_validation(trades)

    for mode in (mt.IN_PLAY, mt.SCHEDULED, mt.DISCRETE):
        rows = [t for t in trades if t["mode"] == mode]
        if not rows:
            continue
        clock = CLOCK[mode]
        scored = defaultdict(list)
        unbucketed = 0
        for t in rows:
            b = bucket_of(clock_hours(t), mode)
            if b is None:
                unbucketed += 1
            else:
                scored[b].append(t)
        n_scored = sum(len(v) for v in scored.values())
        src = ("realized time-to-resolution (closed_at - created_at)" if clock == "hold"
               else "hours_to_close at entry (candidate tick)")
        print(f"\n=== {mode.upper()} — by {src} ===")
        print(f"  {n_scored} of {len(rows)} trades scoreable"
              f"{f' ({unbucketed} without a clock value)' if unbucketed else ''}")
        _print_cells(scored, tuple(lbl for lbl, _ in WINDOWS[mode]), calib)

        # The h2h thesis is type-specific, so in-play also gets a per-type cut. Without it a
        # real h2h effect can be masked by totals/props moving the other way in the same cell.
        if by_type and mode == mt.IN_PLAY:
            for mtype in sorted({t["type"] for t in rows}):
                sub = [t for t in rows if t["type"] == mtype]
                if len(sub) < min_n:
                    continue
                cells = defaultdict(list)
                for t in sub:
                    b = bucket_of(clock_hours(t), mode)
                    if b is not None:
                        cells[b].append(t)
                print(f"\n  --- in_play / {mtype} (n={len(sub)}) ---")
                _print_cells(cells, tuple(lbl for lbl, _ in WINDOWS[mode]), calib)

    print("\n  edge = be% - loss%, in percentage points: the loss rate this cell breaks even at"
          "\n  (given its own realized win/loss sizes) minus the one it actually ran. It is the"
          "\n  only column comparable ACROSS cells, because each window is entered at a different"
          "\n  premium. A timing rule is only worth building where edge separates cleanly AND the"
          "\n  cell has the n to support it — see docs/MMSELL_TIMING_STUDY.md for the gates.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--maxyes", type=int, default=None,
                    help="restrict to entries at or below this yes price (e.g. 7 for the "
                         "mmsell10 live band); default: all prices")
    ap.add_argument("--no-types", action="store_true",
                    help="skip the per-type breakdown inside in_play")
    ap.add_argument("--min-n", type=int, default=40,
                    help="hide per-type in_play cuts below this n (default 40)")
    ap.add_argument("--no-fill-model", action="store_true",
                    help="skip the REALIZABLE projection (paper numbers only)")
    ap.add_argument("--include-twins", action="store_true",
                    help="include live/paper twin books (different entry convention)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            trades = load_trades(cur, args.include_twins)
            calib = None if args.no_fill_model else fm._load_calibration(cur)
    report(trades, args.maxyes, not args.no_types, args.min_n, calib)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
