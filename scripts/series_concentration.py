"""SERIES CONCENTRATION — how many contracts ride on ONE outcome, per series.

WHY THIS EXISTS
---------------
Check 3 of the series approval review (`docs/MMSELL_SERIES_APPROVAL_REVIEW.md`): *are there
opportunities for multiple contracts in this series to resolve the same based on one outcome?*

It is the check that changed batch 1's answer, and until now it was the only one with no script
behind it -- its numbers came from an ad-hoc `db` query that nobody could re-run by name. The
finding it produced: mmsell routinely holds 4-9 contracts on a single outcome and up to 21. One
NFL game carried 21 KXNFLSPREAD markets; one MLB game 21 KXMLBHR markets; one oil print 18 KXWTI
strikes. The book's whole premise is diversification and on those series it has none.

WHAT IT MEASURES, AND WHAT IT DOES NOT
--------------------------------------
Two different things, deliberately separated because they are confused easily:

  WITHIN a series   distinct markets sharing one contest key. A ladder (KXWTI's 18 strikes on
                    one oil print) and a nested spread (one game's whole line) both land here.
  ACROSS series     how many OTHER traded series share the same event token. An NFL game is
                    written by several series at once, so one afternoon moves positions that
                    the risk model counts as independent (XOS-000020).

**The cross-series figure is only meaningful when the event token names a real OCCASION.** For a
series whose token is a bare date (`KXRAIN-26SEP06-TTN` -> `26SEP06`) it collides with every
other date-keyed series and means nothing; the column is flagged `date?` in that case rather than
silently reported. Sports tokens (`26AUG13ARILV`) are real and the figure stands.

**`loss_multi` is NOT a causal claim.** Where nearly every contest is multi-market, the share of
losses coming from multi-market contests is ~100% by construction, with no single-market
comparison group inside the series to contrast against. The `%multi` column is printed beside it
precisely so that tautology is visible: read the pair, never the loss share alone. Concentration
makes a loss distribution fat-tailed; this report does not show that it causes losses.

Uses the CORRECTED contest key by default (`--split-subjects` is the shipped report's opt-in, but
here the honest key is the point of the measurement, so it is ON unless `--shipped-key` is
passed). See `docs/MMSELL_CONTEST_KEY_SUBJECT_SPLIT.md`.

DELIBERATELY SELF-CONTAINED (stdlib + psycopg), like every ops-channel script. Read-only:

    {"type": "script", "name": "series_concentration"}
    {"type": "script", "name": "series_concentration", "args": ["--only", "KXBTC,KXETHD"]}
    {"type": "script", "name": "series_concentration", "args": ["--top", "30", "--min-n", "50"]}
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

RO_OPTIONS = "-c default_transaction_read_only=on"

#: Mirrors `kalshi_bot.mmsell.regimes.SUBJECT_SPLIT_SERIES`, which is CANONICAL. Kept honest by
#: `tests/test_mmsell_subject_split_contest.py`. The ops runner has no package to import from.
SUBJECT_SPLIT_SERIES: frozenset[str] = frozenset({
    "KXFEDMENTION", "KXRAIN", "KXTRUMPSAY", "KXTRUMPSAYCOMPANY",
    "KXTRUMPSAYMONTH", "KXWCATTEND", "KXWCFIRSTSONG", "KXWCMENTION",
})

#: An event token that is only a date tells you nothing about WHICH occasion, so it collides
#: across series. `26SEP06`, `26AUG0414` -> date-shaped; `26AUG13ARILV` -> a real game.
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def series_of(ticker: str) -> str:
    return (ticker or "").split("-", 1)[0].upper()


def event_token(ticker: str) -> str:
    parts = (ticker or "").split("-")
    return parts[1].upper() if len(parts) > 1 else ""


def looks_like_a_bare_date(token: str) -> bool:
    """True when the token is only a date (optionally with an hour), so it names no occasion.

    `26SEP06` and `26SEP0414` are dates. `26AUG13ARILV` carries a matchup and is not.
    """
    if not token:
        return False
    for m in _MONTHS:
        i = token.find(m)
        if i <= 0:
            continue
        rest = token[i + len(m):]
        return token[:i].isdigit() and rest.isdigit()
    return False


def contest_of(ticker: str, *, split_subjects: bool = True) -> str:
    if split_subjects and series_of(ticker) in SUBJECT_SPLIT_SERIES:
        return (ticker or "").upper()
    ev = event_token(ticker)
    return f"{series_of(ticker)}:{ev}" if ev else (ticker or "").upper()


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        return "postgresql://" + url.split("://", 1)[1]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def load(cur, days: int | None, split_subjects: bool) -> list[dict]:
    where = ["status = ANY(%s)", "NOT coalesce(legacy, false)",
             "pnl IS NOT NULL", "quantity IS NOT NULL", "quantity > 0",
             "assumed_price IS NOT NULL", "strategy LIKE '%%mmsell%%'",
             "strategy NOT IN (SELECT twin_tag FROM live_paper_twins)"]
    params: list[object] = [["settled", "closed_sl"]]
    if days is not None:
        where.append("created_at >= now() - make_interval(days => %s)")
        params.append(days)
    cur.execute("SELECT market_ticker, pnl, quantity FROM paper_trades WHERE "
                + " AND ".join(where), params)
    out = []
    for ticker, pnl, qty in cur.fetchall():
        out.append({"ticker": (ticker or "").upper(),
                    "series": series_of(ticker),
                    "ev": event_token(ticker),
                    "contest": contest_of(ticker, split_subjects=split_subjects),
                    "c": float(pnl) / int(qty) * 100.0})
    return out


def summarize(rows: list[dict], series_by_event: dict[str, set[str]]) -> dict:
    by_contest_mkts: dict[str, set[str]] = defaultdict(set)
    by_contest_pnl: dict[str, float] = defaultdict(float)
    for r in rows:
        by_contest_mkts[r["contest"]].add(r["ticker"])
        by_contest_pnl[r["contest"]] += r["c"]

    sizes = [len(v) for v in by_contest_mkts.values()]
    multi = sum(1 for s in sizes if s > 1)
    loss_multi = sum(p for g, p in by_contest_pnl.items()
                     if p < 0 and len(by_contest_mkts[g]) > 1)
    loss_single = sum(p for g, p in by_contest_pnl.items()
                      if p < 0 and len(by_contest_mkts[g]) == 1)

    evs = {r["ev"] for r in rows if r["ev"]}
    shared = [len(series_by_event.get(e, ())) for e in evs]
    dated = sum(1 for e in evs if looks_like_a_bare_date(e))
    return {
        "n": len(rows),
        "mkts": len({r["ticker"] for r in rows}),
        "contests": len(sizes),
        "max_mkts": max(sizes) if sizes else 0,
        "avg_mkts": (sum(sizes) / len(sizes)) if sizes else 0.0,
        "multi_share": (multi / len(sizes)) if sizes else 0.0,
        "loss_multi": loss_multi / 100.0,
        "loss_single": loss_single / 100.0,
        "avg_series_per_event": (sum(shared) / len(shared)) if shared else 1.0,
        "max_series_per_event": max(shared) if shared else 1,
        # The cross-series column is unreadable when the token is only a date.
        "date_keyed": bool(evs) and dated / len(evs) > 0.5,
    }


HDR = (f"  {'series':<22} {'n':>6} {'mkts':>6} {'cnts':>6} {'avg/outcome':>12} {'max':>5}"
       f" {'%multi':>7} {'loss multi$':>12} {'loss single$':>13}  cross-series")


def _row(series: str, s: dict) -> str:
    cross = "date?" if s["date_keyed"] else f"{s['avg_series_per_event']:.2f} (max {s['max_series_per_event']})"
    return (f"  {series:<22} {s['n']:>6} {s['mkts']:>6} {s['contests']:>6}"
            f" {s['avg_mkts']:>12.2f} {s['max_mkts']:>5} {100*s['multi_share']:>6.0f}%"
            f" {s['loss_multi']:>12.2f} {s['loss_single']:>13.2f}  {cross}")


def report(trades: list[dict], min_n: int, top: int, only: set[str] | None,
           window: str, key_name: str) -> None:
    series_by_event: dict[str, set[str]] = defaultdict(set)
    for r in trades:
        if r["ev"]:
            series_by_event[r["ev"]].add(r["series"])

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in trades:
        groups[r["series"]].append(r)

    cells = {k: summarize(v, series_by_event) for k, v in groups.items()
             if (only and k in only) or (not only and len(v) >= min_n)}

    print(f"=== mmsell SERIES CONCENTRATION — {window}, contest key: {key_name} ===")
    print(f"{len(trades)} trades across {len(groups)} series; showing {len(cells)}.")
    print("PAPER, fill-everything. A report, never a gate.\n")

    ordered = sorted(cells.items(), key=lambda kv: -kv[1]["avg_mkts"])
    print(HDR)
    for series, s in ordered[:top]:
        print(_row(series, s))

    print("\nHOW TO READ THIS")
    print("  avg/outcome  distinct MARKETS this series held per contest. 1.00 means every")
    print("               position rode its own outcome; 8.66 means the average outcome carried")
    print("               8.66 contracts, which is one position at 8.66x size however the risk")
    print("               model counts it.")
    print("  %multi       share of contests holding more than one market. READ IT BESIDE the")
    print("               loss columns: at ~100% there is no single-market comparison group, so")
    print("               'all the losses came from multi-market contests' is TAUTOLOGICAL and")
    print("               says nothing about cause. A series with a MIX is the informative one.")
    print("  loss multi / single")
    print("               gross contest-level losses, split by whether the contest held more")
    print("               than one market. Concentration makes losses fat-tailed; nothing here")
    print("               shows it causes them.")
    print("  cross-series how many OTHER traded series share this one's event token — the")
    print("               XOS-000020 axis. `date?` means the token is only a date, so it")
    print("               collides with every date-keyed series and the figure is meaningless.")
    print("\n  A high number is EXPOSURE, not a verdict. The lever is the contest cap")
    print("  (docs/MMSELL_CORRELATION_CAP.md), not a ban: see docs/MMSELL_SERIES_APPROVAL_REVIEW.md.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="per-series concentration: contracts per outcome")
    ap.add_argument("--days", type=int, default=None, help="lookback window (default all time)")
    ap.add_argument("--min-n", type=int, default=20,
                    help="hide series below this many trades (default 20)")
    ap.add_argument("--top", type=int, default=25, help="rows to print (default 25)")
    ap.add_argument("--only", default=None, help="comma-separated series, ignores --min-n")
    ap.add_argument("--shipped-key", action="store_true",
                    help="use the SHIPPED contest key instead of the corrected one "
                         "(docs/MMSELL_CONTEST_KEY_SUBJECT_SPLIT.md); the corrected key is the "
                         "default here because an honest independence unit is the measurement")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL", ""))
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    import psycopg

    only = {s.strip().upper() for s in args.only.split(",") if s.strip()} if args.only else None
    split = not args.shipped_key
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            trades = load(cur, args.days, split)
    window = "all time" if args.days is None else f"last {args.days} days"
    report(trades, args.min_n, args.top, only, window,
           "subject-split (corrected)" if split else "shipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
