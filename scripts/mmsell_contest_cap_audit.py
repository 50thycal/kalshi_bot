"""mmsell contest cap — did it actually FIRE on real money?

WHAT THIS ANSWERS, AND WHY A COUNTER IS NOT ENOUGH
--------------------------------------------------
`skipped_contest_cap` incrementing says the code path ran. It does not say the
cap bound the right unit, and this exact mechanism has now produced two silent
failures that both read as green:

  1. the cap keyed `event_ticker` (series x contest), so three rungs under each
     of five MLB series on ONE game passed every check;
  2. the contest read was settlement-date scoped, so a game starting after
     ~18:30 ET had its early legs before UTC midnight and its late legs after.
     They counted against two different days' budgets, the cap never fired, and
     `skipped_contest_cap` simply stayed 0 — which reads as "nothing to refuse"
     rather than "broken". Fixed in c4b2ce1 / PR #335.

So the PROOF is not a counter. It is: group this book's live footprint by the
SAME key the tracker caps on, and assert the maximum is the declared cap.

This script therefore imports `regimes.contest_key_of` FROM THE WORKER, by file
path, rather than re-implementing the key in SQL. A hand-rolled SQL key would be
a second implementation that can disagree with the one that actually ran — which
is failure mode 1 rebuilt inside its own audit.

WHAT A CLEAN RESULT LOOKS LIKE, AND WHAT IT DOES NOT PROVE
----------------------------------------------------------
`max positions per contest == cap` on the live tag proves the cap held ONLY if
something was there to refuse. If no book anywhere held 2+ on one contest over
the same window, the cap has had nothing to decline and the live max of 1 is
uninformative. The report therefore always prints the UNCAPPED comparison
alongside, and says so in words. `verdict: UNPROVEN` is a real outcome here, not
a failure of the script.

STRADDLE COVERAGE is reported separately: a contest whose markets carry more than
one distinct UTC close DATE is the case c4b2ce1 fixed and the one most likely to
regress. Until one has occurred, the fix is untested in production and the report
says that rather than implying full verification.

Read-only (stdlib + psycopg + the worker's pure-stdlib regime map):

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_contest_cap_audit.py \
        --live Emmsell10 --uncapped mmsell10 --hours 24
    # or:  {"type": "script", "name": "mmsell_contest_cap_audit",
    #       "args": ["--live", "Emmsell10", "--uncapped", "mmsell10"]}
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import sys
from collections import Counter, defaultdict

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

#: Order statuses that mean "this book committed to the market". Mirrors
#: `repository.count_live_book_open`, which counts a resting order as open —
#: the cap refuses on OPEN positions, so a resting rung is exactly what it is
#: supposed to be counting.
_COMMITTED = ("filled", "resting", "open", "pending", "partially_filled")


def _contest_key_of():
    """The worker's OWN key function, loaded by file path.

    Not re-implemented, and not imported through the `kalshi_bot` package: ops
    scripts run on a runner that never installs it. `regimes.py` is pure stdlib,
    so loading the single module directly is both safe and exact.
    """
    path = pathlib.Path(__file__).resolve().parent.parent / "kalshi_bot" / "mmsell" / "regimes.py"
    spec = importlib.util.spec_from_file_location("_mmsell_regimes", path)
    if spec is None or spec.loader is None:  # pragma: no cover — defensive
        raise RuntimeError(f"cannot load the worker's regime map from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.contest_key_of


def _live_footprint(cur, tag: str, hours: int):
    """(ticker, first_seen, close_date) for every market this LIVE book committed to."""
    cur.execute(
        """
        select o.market_ticker, min(o.created_at) as first_at,
               max(mm.close_time) as close_time
          from live_orders o
          left join mmsell_settlement_meta mm on mm.market_ticker = o.market_ticker
         where o.strategy = %s
           and o.action = 'buy'
           and o.status = any(%s)
           and o.created_at > now() - make_interval(hours => %s)
         group by o.market_ticker
        """,
        (tag, list(_COMMITTED), hours),
    )
    return cur.fetchall()


def _paper_footprint(cur, tag: str, hours: int):
    """The same shape for an UNCAPPED PAPER book — the comparison that says
    whether the cap had anything to refuse."""
    cur.execute(
        """
        select p.market_ticker, min(p.created_at) as first_at,
               max(mm.close_time) as close_time
          from paper_trades p
          left join mmsell_settlement_meta mm on mm.market_ticker = p.market_ticker
         where p.strategy = %s
           and p.created_at > now() - make_interval(hours => %s)
         group by p.market_ticker
        """,
        (tag, hours),
    )
    return cur.fetchall()


def _group(rows, key_of):
    """contest key -> [(ticker, close_date or None), ...]"""
    out: dict[str, list[tuple[str, object]]] = defaultdict(list)
    for ticker, _first_at, close_time in rows:
        key = key_of(ticker)
        if key:
            out[key].append((ticker, close_time.date() if close_time else None))
    return out


def _report(label: str, grouped: dict, cap: int | None) -> tuple[int, int, int]:
    sizes = Counter(len(v) for v in grouped.values())
    worst = max(sizes) if sizes else 0
    over = sum(n for size, n in sizes.items() if cap is not None and size > cap)
    straddling = sum(
        1 for v in grouped.values()
        if len({d for _t, d in v if d is not None}) > 1
    )
    print(f"\n{label}")
    print(f"  contests held           : {len(grouped)}")
    print(f"  positions               : {sum(len(v) for v in grouped.values())}")
    print(f"  MAX positions / contest : {worst}")
    print(f"  contests with 2+        : {sum(n for s, n in sizes.items() if s >= 2)}")
    print(f"  contests straddling UTC : {straddling}   "
          "(markets on >1 close date — the c4b2ce1 case)")
    if cap is not None:
        print(f"  contests OVER cap {cap}     : {over}")
    for key, v in sorted(grouped.items(), key=lambda kv: -len(kv[1]))[:8]:
        dates = sorted({str(d) for _t, d in v if d is not None})
        if len(v) >= 2:
            print(f"    {len(v):>3}x {key:<40} close dates: {dates or ['unknown']}")
    return worst, over, straddling


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", default="Emmsell10", help="the capped LIVE tag")
    ap.add_argument("--uncapped", default="mmsell10",
                    help="an UNCAPPED paper book, for the had-anything-to-refuse check")
    ap.add_argument("--cap", type=int, default=1, help="the declared contest cap")
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args(argv)

    url = os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL_RO is required", file=sys.stderr)
        return 2

    import psycopg

    key_of = _contest_key_of()
    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        live = _group(_live_footprint(cur, args.live, args.hours), key_of)
        uncapped = _group(_paper_footprint(cur, args.uncapped, args.hours), key_of)

        cur.execute(
            """
            select coalesce(sum((raw_json->>'skipped_contest_cap')::int), 0)
              from system_events
             where component = 'mmsell_scan'
               and created_at > now() - make_interval(hours => %s)
            """,
            (args.hours,),
        )
        refusals = cur.fetchone()[0]

    print(f"=== contest-cap audit · live={args.live} cap={args.cap} "
          f"window={args.hours}h ===")
    print(f"skipped_contest_cap over the window (ALL books): {refusals}")

    worst, over, live_straddle = _report(
        f"LIVE {args.live} (capped at {args.cap})", live, args.cap)
    _, _, _ = _report(f"UNCAPPED {args.uncapped} (paper, no contest cap)",
                      uncapped, None)

    had_work = any(len(v) >= 2 for v in uncapped.values())
    print("\n--- verdict ---")
    if over:
        print(f"BREACH: {over} contest(s) exceed the declared cap of {args.cap} on "
              f"REAL MONEY. The bound is not being applied. STAND DOWN.")
        return 1
    if not live:
        print("UNPROVEN: the live book holds nothing in this window — the cap has "
              "not been exercised. This is NOT evidence the cap works.")
        return 0
    if not had_work:
        print("UNPROVEN: live max is within cap, but the UNCAPPED book never held "
              "2+ on one contest either, so the cap had nothing to refuse and a "
              "clean live max proves nothing yet.")
        return 0
    print(f"HELD: live max {worst} <= cap {args.cap}, while the uncapped book held "
          f"2+ on {sum(1 for v in uncapped.values() if len(v) >= 2)} contest(s) "
          "over the same window — so the cap had work to do and did it.")
    if not live_straddle:
        print("NOTE: no live contest straddled UTC midnight yet, so the c4b2ce1 "
              "fix — the case where this cap silently failed before — is still "
              "UNEXERCISED in production. Do not report it as verified.")
    else:
        print(f"The UTC-straddle case IS covered: {live_straddle} live contest(s) "
              "carried markets on more than one close date and stayed inside the cap.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
