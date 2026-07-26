"""mmsell EXIT-RULE study — does a stop-loss or a volatility exit beat hold-to-settlement, and
by how much, for EACH mmsell book?

The mmsell books hold to settlement. That harvests the favorite-longshot bias (most cheap
longshots you sell settle worthless -> full premium) but eats an all-or-nothing tail: the rare
longshot that hits costs ~the whole stake (a NO bought at 92c settling 0 = -92c). This study
replays each settled position's ACTUAL intraday path (mmsell_position_ticks, captured live off
the orderbook the engine already fetches) through two exit mechanics and compares them, per book,
to holding:

  * confirmed catastrophic STOP: exit once the sold longshot reprices UP through a level L
    (yes-mid >= L) for K consecutive ticks (K-confirm defeats the single-print whipsaw that
    wrecks a naive stop at these thin prices). Caps the tail.
  * VOLATILITY exit: exit once the yes-mid's range over the trailing W ticks exceeds V cents —
    the thesis being that a position swinging hard is one informed flow is actively repricing,
    i.e. likelier to be a loser (adverse). No take-profit; a quiet winner is left to settle.

Exit = sell the NO back at the current no-bid (taker, crosses the spread); hold = settle free.
Everything is counterfactual: positions still hold to settlement (that's the baseline), so the
study measures each rule's effect WITHOUT disturbing the book. It sweeps a grid of L/K and W/V so
the data — not a guess — picks the level. Reports mean, 5th-pctile TAIL (what a stop is for),
win%, %exited, and the delta vs hold, per book.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_exit_study.py
    # or:  {"type": "script", "name": "mmsell_exit_study"}
    #      {"type": "script", "name": "mmsell_exit_study", "args": ["--book", "mmsell10"]}

The replay math lives in pure functions (fee_c / replay_exit) unit-tested in
tests/test_mmsell_exit_study.py, independent of the DB.
"""

from __future__ import annotations

import math
import os
import statistics
import sys
from collections import defaultdict

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

# Trailing window (in ticks) the volatility exit measures its range over.
VOL_WINDOW = 6
# Minimum ticks in the window before the vol rule may fire (a 1-2 tick "range" is meaningless).
VOL_MIN_TICKS = 3


def fee_c(price_c: float) -> float:
    """Kalshi fee in CENTS per contract at a price, matching paper's kalshi_fee (0.07 coeff,
    rounded up to the cent). ceil(0.07 * p*(1-p) * 100) with p in [0,1] == ceil(7*p*(1-p))."""
    p = price_c / 100.0
    return math.ceil(7.0 * p * (1.0 - p))


def replay_exit(entry_no, settle_no, ticks, stop_l=None, stop_k=2,
                vol_w=VOL_WINDOW, vol_v=None):
    """Counterfactual P&L (cents/contract) for one mmsell NO position under an exit rule.

    entry_no  : NO cost basis in cents (paper_trades.assumed_price).
    settle_no : NO settlement value, 100 (longshot missed -> NO wins) or 0 (longshot hit).
    ticks     : post-entry path, time-ordered list of (yes_mid_c, no_bid_c).
    stop_l/k  : catastrophic stop — exit when yes_mid >= stop_l for stop_k consecutive ticks.
    vol_w/v   : volatility exit — exit when the yes_mid range over the trailing vol_w ticks
                (>= VOL_MIN_TICKS present) is >= vol_v cents. Either rule disabled with None.

    Returns (pnl_c, kind) where kind in {"settle","stop","vol"}. Exit sells NO at that tick's
    no-bid (taker fee); hold settles free. Entry fee applies either way (cancels in the delta).
    """
    entry_fee = fee_c(entry_no)
    run = 0                       # consecutive ticks with yes_mid >= stop_l
    window: list[float] = []      # trailing yes_mids for the vol rule
    for mid, no_bid in ticks:
        if mid is None or no_bid is None:
            continue
        # Volatility exit (checked on the window INCLUDING this tick).
        window.append(mid)
        if len(window) > vol_w:
            window.pop(0)
        if vol_v is not None and len(window) >= VOL_MIN_TICKS and (max(window) - min(window)) >= vol_v:
            return (no_bid - entry_no - entry_fee - fee_c(no_bid), "vol")
        # Confirmed catastrophic stop.
        if stop_l is not None:
            run = run + 1 if mid >= stop_l else 0
            if run >= stop_k:
                return (no_bid - entry_no - entry_fee - fee_c(no_bid), "stop")
    return (settle_no - entry_no - entry_fee, "settle")


# The rule grid. (label, stop_l, stop_k, vol_v) — vol uses VOL_WINDOW. None disables a leg.
def _rules():
    rules = [("HOLD", None, 2, None)]
    for lvl in (30, 40, 50, 60):
        rules.append((f"stop L{lvl} K2", lvl, 2, None))
    rules.append(("stop L50 K1", 50, 1, None))       # K1 vs K2 shows the whipsaw the confirm cuts
    for v in (8, 15, 25):
        rules.append((f"vol V{v} W{VOL_WINDOW}", None, 2, v))
    rules.append(("stop L50K2 + vol V15", 50, 2, 15))  # both legs together
    return rules


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _pctile(xs, q):
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(q * (len(s) - 1))))]


def _load(cur, book_filter):
    # An explicit --book may name a twin; the unfiltered sweep excludes twins (they are the
    # live-parallel control, read by scripts/live_paper_parity.py, not paper variants to gate).
    where = ("AND strategy = %s" if book_filter else
             "AND strategy LIKE 'mmsell%%'"
             " AND strategy NOT IN (SELECT twin_tag FROM live_paper_twins)")
    params = (book_filter,) if book_filter else ()
    cur.execute(
        "SELECT id, strategy, market_ticker, assumed_price, resolved_value, created_at, closed_at"
        " FROM paper_trades WHERE status='settled' AND NOT coalesce(legacy,false)"
        "   AND assumed_price IS NOT NULL AND resolved_value IN (0,100)"
        "   AND created_at IS NOT NULL AND closed_at IS NOT NULL " + where,
        params,
    )
    trades = cur.fetchall()
    tickers = sorted({t[2] for t in trades})
    ticks_by_ticker = defaultdict(list)
    if tickers:
        cur.execute(
            "SELECT market_ticker, captured_at, mid, no_bid FROM mmsell_position_ticks"
            " WHERE market_ticker = ANY(%s) AND mid IS NOT NULL AND no_bid IS NOT NULL"
            " ORDER BY market_ticker, captured_at",
            (tickers,),
        )
        for tk, ts, mid, no_bid in cur.fetchall():
            ticks_by_ticker[tk].append((ts, float(mid), float(no_bid)))
    return trades, ticks_by_ticker


def report(cur, book_filter):
    trades, ticks_by_ticker = _load(cur, book_filter)
    if not trades:
        print("(no settled mmsell paper trades)")
        return

    rules = _rules()
    # per book: rule_label -> list of pnl_c ; plus path coverage counters.
    per_book = defaultdict(lambda: {"n": 0, "with_path": 0,
                                    "pnl": defaultdict(list), "exited": defaultdict(int)})

    for _id, strat, ticker, entry_no, settle_no, created_at, closed_at in trades:
        pb = per_book[strat]
        pb["n"] += 1
        path = [(mid, nb) for ts, mid, nb in ticks_by_ticker.get(ticker, ())
                if created_at < ts <= closed_at]
        if not path:
            continue  # no captured tape yet — can't replay this one (coverage grows over time)
        pb["with_path"] += 1
        for label, sl, sk, vv in rules:
            pnl, kind = replay_exit(float(entry_no), float(settle_no), path,
                                    stop_l=sl, stop_k=sk, vol_v=vv)
            pb["pnl"][label].append(pnl)
            if kind != "settle":
                pb["exited"][label] += 1

    _print(per_book, rules)


def _print(per_book, rules):
    total_paths = sum(pb["with_path"] for pb in per_book.values())
    print("=== mmsell exit-rule study — counterfactual vs hold-to-settlement, per book ===")
    print(f"  ticks captured so far cover {total_paths} settled positions with a replayable path.")
    if not total_paths:
        print("\n  No captured price paths intersect a settled position yet. mmsell_position_ticks"
              "\n  starts filling on deploy; a position must be BORN and SETTLE inside the capture"
              "\n  window to be replayable, so this stays empty ~until the first cheap-longshot"
              "\n  cohort turns over (hours-to-days). Re-run then. (Entry/capture wiring is live;"
              "\n  this is a data-maturity wait, not an error.)")
        return

    for strat in sorted(per_book):
        pb = per_book[strat]
        if not pb["with_path"]:
            print(f"\n{strat}: {pb['n']} settled, 0 with a captured path yet.")
            continue
        hold = pb["pnl"].get("HOLD") or []
        hold_mean = statistics.fmean(hold) if hold else 0.0
        hold_p5 = _pctile(hold, 0.05)
        print(f"\n{strat}: {pb['with_path']}/{pb['n']} settled positions replayable"
              f"  (coverage {100*pb['with_path']/pb['n']:.0f}%)")
        print(f"  {'rule':20s} {'mean_c':>7} {'p5_c':>7} {'win%':>6} {'%exit':>6}"
              f" {'Δmean':>7} {'Δp5(tail)':>10}")
        for label, _sl, _sk, _vv in rules:
            xs = pb["pnl"].get(label) or []
            if not xs:
                continue
            mean = statistics.fmean(xs)
            p5 = _pctile(xs, 0.05)
            win = 100.0 * sum(1 for p in xs if p > 0) / len(xs)
            pex = 100.0 * pb["exited"].get(label, 0) / len(xs)
            dmean = mean - hold_mean
            dp5 = p5 - hold_p5
            dm = "  base" if label == "HOLD" else f"{dmean:+7.2f}"
            dp = "  base" if label == "HOLD" else f"{dp5:+10.1f}"
            print(f"  {label:20s} {mean:+7.2f} {p5:+7.1f} {win:5.1f}% {pex:5.0f}% {dm} {dp}")
    print("\n  Read: a good exit LIFTS the p5 tail (Δp5 >> 0, cuts the -90c disasters) while barely"
          "\n  denting the mean (Δmean ~ 0 or +). If every rule's Δmean is sharply negative, exits"
          "\n  cut winners-that-revert faster than they save losers -> keep holding + diversify.")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    book = None
    if "--book" in argv:
        i = argv.index("--book")
        if i + 1 < len(argv):
            book = argv[i + 1]

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            report(cur, book)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
