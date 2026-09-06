"""SERIES REGISTRY REVIEW — the queue: what arrived, what is unreviewed, what is ready.

WHAT THE REGISTRY IS
--------------------
Two ledgers, deliberately split (`kalshi_bot/registry/__init__.py`):

    DECISION     `kalshi_bot/registry/series_manifest.json` — the state of each series, who
                 reviewed it, when. Moves only by PR, because a decision should be reviewable.
    OBSERVATION  `series_observations` — when the scan first saw each series listed, and how
                 many of its markets it offers. Written by the worker; decides nothing.

This script is the join. It answers the three questions the registry exists to make askable:

    ARRIVALS   which series has Kalshi started offering that the manifest has never seen?
    BACKLOG    which GRADUATED series is trading live with no recorded rules review?
    CANDIDATES which in-review series now has enough of our own settled history to graduate?

THIS IS A REPORT, NOT A GATE. It authorizes nothing, changes no state and promotes nothing. A
series graduates by someone reading its settlement rules and opening a PR against the manifest;
that is the whole point of keeping the decision in git. Ranking exists so a human reads the
rows that matter first, not so a threshold graduates anything by itself.

WHY THE BACKLOG IS RANKED BY LIVE EXPOSURE
------------------------------------------
All 138 series in the manifest were grandfathered on 2026-09-06 from PR #338's mechanical seed
(>=20 own settled markets AND a market-type classification). That bar proves we have DATA about
a contract; it never proved anyone read how it SETTLES. Both are required and neither implies
the other — `KXNFLSPREAD` cleared the mechanical bar with 382 settled markets and has lost
$166.55. So every grandfathered row carries `rules_reviewed_at: null`, and the audit that
retires that debt has to start where real money is: a series the live books never touch is
research, a series they are in is exposure.

READS THE MANIFEST FROM DISK, does not duplicate it. Ops-channel scripts are self-contained
(stdlib + psycopg only — the runner never installs this package), which historically forced a
table the worker and the scripts both need to be COPIED with a test asserting the copies match
(`market_types.SERIES_TYPES` / `scripts/mmsell_market_types.py`). A JSON manifest read by path
has no second copy to drift, which matters far more for a ledger that changes weekly.

Read-only. Runs locally or through the ops channel:

    {"type": "script", "name": "series_registry_review"}
    {"type": "script", "name": "series_registry_review", "args": ["--days", "7"]}
    {"type": "script", "name": "series_registry_review", "args": ["--section", "backlog"]}
    {"type": "script", "name": "series_registry_review", "args": ["--min-settled", "50"]}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

RO_OPTIONS = "-c default_transaction_read_only=on"

MANIFEST_PATH = (Path(__file__).resolve().parents[1]
                 / "kalshi_bot" / "registry" / "series_manifest.json")

#: Mirrors `kalshi_bot.registry.STATE_ORDER`. Not imported for the reason in the docstring; the
#: four names are a vocabulary, not a table, and `tests/test_series_registry_review.py` pins
#: them against the package so they cannot drift.
IDENTIFIED, IN_REVIEW, GRADUATED, BARRED = "identified", "in_review", "graduated", "barred"


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        return "postgresql://" + url.split("://", 1)[1]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def series_of(ticker: str) -> str:
    return (ticker or "").split("-", 1)[0].upper()


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, dict]:
    doc = json.loads(path.read_text())
    return {str(r["series"]).upper(): r for r in doc.get("series", ())}


def manifest_entry(manifest: dict[str, dict], series: str) -> dict | None:
    """The row governing a series, by LONGEST matching prefix — same rule as the package's
    `registry.entry_for`, so the report and the worker never disagree about which row applies."""
    s = (series or "").upper()
    best, best_len = None, -1
    for prefix, row in manifest.items():
        if s.startswith(prefix) and len(prefix) > best_len:
            best, best_len = row, len(prefix)
    return best


def load_observations(cur) -> dict[str, dict]:
    """Arrival facts, or {} when the table has not been migrated yet.

    A missing table is not an error worth failing the whole report on: the manifest half is
    still readable and the backlog section — the one with money behind it — needs no
    observations at all."""
    try:
        cur.execute(
            "SELECT series, first_seen_at, last_seen_at, markets_seen, sample_ticker,"
            "       sample_title, state_at_first_seen"
            "  FROM series_observations")
    except Exception as exc:  # noqa: BLE001 — report degrades, never dies
        print(f"# series_observations unavailable ({exc.__class__.__name__}); "
              "arrivals section skipped. Has the migration run?", file=sys.stderr)
        return {}
    return {r[0].upper(): {"series": r[0].upper(), "first_seen_at": r[1], "last_seen_at": r[2],
                           "markets_seen": r[3], "sample_ticker": r[4], "sample_title": r[5],
                           "state_at_first_seen": r[6]}
            for r in cur.fetchall()}


def load_series_activity(cur, days: int | None, live_days: int) -> dict[str, dict]:
    """Per-series settled history and live exposure, across ALL strategy families.

    All eight families, not just mmsell: the registry is a platform-level ledger, and a series
    one family has reviewed is reviewed for every family that meets it. Twin books are excluded
    for the usual reason — a twin enters at the live maker price, so pooling it mixes two entry
    conventions into one cell."""
    where = ["status = ANY(%s)", "NOT coalesce(legacy, false)", "pnl IS NOT NULL",
             "quantity IS NOT NULL", "quantity > 0",
             "strategy NOT IN (SELECT twin_tag FROM live_paper_twins)"]
    params: list[object] = [["settled", "closed_sl"]]
    if days is not None:
        where.append("created_at >= now() - make_interval(days => %s)")
        params.append(days)
    cur.execute(
        "SELECT market_ticker, strategy, pnl, quantity"
        "  FROM paper_trades WHERE " + " AND ".join(where), params)
    rows = cur.fetchall()

    # REAL-MONEY exposure, measured directly off the order tape rather than inferred.
    #
    # The first version of this asked which STRATEGIES had ever placed a live order, then
    # flagged a series if any paper trade in it came from one of those books. Run against
    # production on 2026-09-06 that marked 137 of 138 series LIVE, because over all time
    # 23-37 books touch a typical series and nearly every one of them has some live lineage.
    # It was answering "did a live-lineage book paper-trade this", which is not the question:
    # a book's live arm and its paper arm trade different universes, and the flag has to mean
    # money was actually in THIS series or it cannot order an audit.
    #
    # So: read `live_orders` by market, windowed. `live_days` is separate from the settled
    # history window because they measure different things — history wants everything we know
    # about a contract, exposure wants what is at risk NOW.
    cur.execute(
        "SELECT market_ticker, strategy, created_at"
        "  FROM live_orders"
        " WHERE strategy IS NOT NULL"
        "   AND created_at >= now() - make_interval(days => %s)", [live_days])
    live_rows = cur.fetchall()

    out: dict[str, dict] = defaultdict(
        lambda: {"settled": 0, "markets": set(), "books": set(),
                 "live_books": set(), "live_orders": 0, "pnl": 0.0})
    for ticker, book, _created in live_rows:
        cell = out[series_of(ticker)]
        cell["live_books"].add(book)
        cell["live_orders"] += 1
    for ticker, book, pnl, qty in rows:
        cell = out[series_of(ticker)]
        cell["settled"] += 1
        cell["markets"].add(ticker)
        cell["books"].add(book)
        cell["pnl"] += float(pnl) / int(qty)
    return dict(out)


def _fmt_date(v) -> str:
    return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else "-"


#: Sentinel for a row with no arrival timestamp, so recency can be compared without a None.
_NO_DATE = datetime.min.replace(tzinfo=timezone.utc)


def _arrival_rank(obs: dict, activity: dict) -> tuple:
    """Ordering for the arrivals queue: WHAT WE ARE TRADING first, recency second.

    Recency alone does not work, and the first populated run proved it. `first_seen_at` is not
    backfilled, so on the first cycle after deploy every one of 3,845 rows shares a timestamp
    and the sort carries no information at all — `KXUAEPLTOTAL`, a series with 22 settled trades
    across 8 books and no manifest row, printed NINTH, below a run of `GOVPARTY*` rows with zero
    trades. The one row a reviewer needed was buried by rows nothing had ever touched.

    Recency is not what makes an arrival urgent; exposure is. A series we are already trading
    with nothing reviewed is the finding. So: traded-at-all, then how much, then how many books
    took it, then how wide the series is, and only then how recently it appeared.

    Recency still breaks ties, which is what keeps a genuinely new listing visible: a series
    that appeared today with no trades sorts above the thousands of untouched older ones,
    because they all tie at zero activity and the date decides."""
    a = activity.get(obs["series"], {})
    settled = a.get("settled", 0)
    return (
        1 if settled else 0,                 # traded at all — the whole point of the queue
        settled,                             # how much
        len(a.get("books", ())),             # how many books took it
        obs.get("markets_seen") or 0,        # breadth of the series
        obs.get("first_seen_at") or _NO_DATE,  # recency, last: it only breaks ties
    )


def report_arrivals(manifest, observations, activity, top: int) -> None:
    """Series the scan has seen that no manifest row governs — the review queue's front."""
    unknown = [o for s, o in observations.items() if manifest_entry(manifest, s) is None]
    unknown.sort(key=lambda o: _arrival_rank(o, activity), reverse=True)
    traded = sum(1 for o in unknown if activity.get(o["series"], {}).get("settled"))
    print(f"\n## ARRIVALS — observed, in no manifest row ({len(unknown)}; "
          f"{traded} already traded)")
    if not unknown:
        print("  (none — every series the scan has seen is governed by a manifest row)")
        return
    print("  Ordered by OUR OWN activity, then recency — a series we are already trading with")
    print("  nothing reviewed outranks one that merely appeared today.")
    print(f"  {'series':<24} {'first seen':<12} {'mkts':>5} {'settled':>8} {'books':>6}  sample")
    for o in unknown[:top]:
        a = activity.get(o["series"], {})
        print(f"  {o['series']:<24} {_fmt_date(o['first_seen_at']):<12} "
              f"{o['markets_seen'] or 0:>5} {a.get('settled', 0):>8} "
              f"{len(a.get('books', ())):>6}  {(o['sample_title'] or o['sample_ticker'] or '')[:44]}")
    if len(unknown) > top:
        print(f"  ... {len(unknown) - top} more (raise --top)")
    print("  -> Each of these is a market that became available and that nothing has reviewed.")
    print("     Paper may trade them; no book requiring in_review or graduated will.")


def report_backlog(manifest, activity, top: int) -> None:
    """Graduated series with no recorded rules review, worst-exposed first."""
    debt = [r for r in manifest.values()
            if r.get("state") == GRADUATED and not r.get("rules_reviewed_at")]

    def rank(row):
        a = activity.get(row["series"], {})
        # Live first, then by how much money the cell has moved. A live cell that has barely
        # traded still outranks a large paper-only one: the review exists to protect real
        # money, and |pnl| is the size of what a misunderstanding would already have cost.
        # `live_books` is now real orders in the window, so this actually partitions the list.
        return (bool(a.get("live_books")), abs(a.get("pnl", 0.0)), a.get("settled", 0))

    debt.sort(key=rank, reverse=True)
    live_n = sum(1 for r in debt if activity.get(r["series"], {}).get("live_books"))
    print(f"\n## BACKLOG — graduated, settlement rules never reviewed ({len(debt)}; "
          f"{live_n} traded by a live book)")
    if not debt:
        print("  (none — every graduated series has a recorded rules review)")
        return
    print(f"  {'series':<24} {'live':<5} {'orders':>7} {'settled':>8} {'pnl($)':>10} {'books':>6}")
    for r in debt[:top]:
        a = activity.get(r["series"], {})
        live = "LIVE" if a.get("live_books") else "-"
        print(f"  {r['series']:<24} {live:<5} {a.get('live_orders', 0):>7} "
              f"{a.get('settled', 0):>8} {a.get('pnl', 0.0):>10.2f} "
              f"{len(a.get('books', ())):>6}")
    if len(debt) > top:
        print(f"  ... {len(debt) - top} more (raise --top)")
    print("  -> These trade live TODAY. The row is the debt, not a bar: retire it by reading")
    print("     the series' settlement rules and setting rules_reviewed_at in the manifest.")


def report_candidates(manifest, activity, min_settled: int, top: int) -> None:
    """In-review series carrying enough own history to be worth a reviewer's time."""
    ready = []
    for series, a in activity.items():
        row = manifest_entry(manifest, series)
        state = row.get("state") if row else None
        if state in (GRADUATED, BARRED):
            continue
        if a["settled"] >= min_settled:
            ready.append((series, a))
    ready.sort(key=lambda t: t[1]["settled"], reverse=True)
    print(f"\n## CANDIDATES — not graduated, >= {min_settled} settled of our own ({len(ready)})")
    if not ready:
        print("  (none)")
        return
    print(f"  {'series':<24} {'settled':>8} {'mkts':>6} {'pnl($)':>10} {'books':>6}")
    for series, a in ready[:top]:
        print(f"  {series:<24} {a['settled']:>8} {len(a['markets']):>6} "
              f"{a['pnl']:>10.2f} {len(a['books']):>6}")
    if len(ready) > top:
        print(f"  ... {len(ready) - top} more (raise --top)")
    print("  -> History is HALF the bar. Each still needs someone to read its settlement rules")
    print("     before a manifest PR moves it to graduated; volume alone graduates nothing.")


def report(manifest, observations, activity, *, window: str, min_settled: int, top: int,
           section: str, live_days: int = 30) -> None:
    states = defaultdict(int)
    for r in manifest.values():
        states[r.get("state")] += 1
    print("# SERIES REGISTRY REVIEW")
    print(f"# window: {window} | manifest rows: {len(manifest)} "
          f"({', '.join(f'{k}={v}' for k, v in sorted(states.items()))}) "
          f"| observed series: {len(observations)}")
    print(f"# live exposure measured over the last {live_days} days of live_orders.")
    print("# A REPORT, NOT A GATE: nothing here graduates, bars or promotes anything.")
    if section in ("all", "arrivals"):
        report_arrivals(manifest, observations, activity, top)
    if section in ("all", "backlog"):
        report_backlog(manifest, activity, top)
    if section in ("all", "candidates"):
        report_candidates(manifest, activity, min_settled, top)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Series registry review queue")
    ap.add_argument("--days", type=int, default=None,
                    help="lookback for settled history (default: all time)")
    ap.add_argument("--live-days", type=int, default=30,
                    help="window for REAL-MONEY exposure, read off live_orders (default 30). "
                         "Separate from --days: history wants everything we know about a "
                         "contract, exposure wants what is at risk now")
    ap.add_argument("--min-settled", type=int, default=20,
                    help="graduation-candidate history floor (default 20)")
    ap.add_argument("--top", type=int, default=25, help="rows per section (default 25)")
    ap.add_argument("--section", default="all",
                    choices=("all", "arrivals", "backlog", "candidates"))
    args = ap.parse_args(argv)

    manifest = load_manifest()

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            observations = load_observations(cur)
            activity = load_series_activity(cur, args.days, args.live_days)

    report(manifest, observations, activity,
           window="all time" if args.days is None else f"last {args.days} days",
           min_settled=args.min_settled, top=args.top, section=args.section,
           live_days=args.live_days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
