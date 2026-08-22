"""The Platform Change Review package for MMSELL's settlement taxonomy — and the census it gates.

WHY. The MMSELL 2x2 paper design cannot start: 24.03% of its eligible non-crypto 5-7c
population classifies as `unknown` against a 5% bar fixed before the measurement. The first
scoping of the repair named six crypto series. That was wrong by an order of magnitude, and
worse, it carried an assumption — that an unknown series is a scheduled one — which is exactly
the kind of guess a taxonomy exists to eliminate. An `unknown` prefix silently treated as
`scheduled` would put in-play markets into the treatment arm and make the primary comparison
measure the thing the design is trying to hold constant.

WHAT THIS DOES.

  1  CENSUS       the eligible candidate population by settle mode, and `unclassified_excluded_
                  pct` against its bar. This is the SAME function that must be re-run after the
                  repair, so "rerun the exact same census" is one command rather than a promise
  2  PREFIXES     every unclassified series in that population, with its supply
  3  EVIDENCE     per prefix, four independent signals read from the data: what Kalshi says the
                  settlement source is, what the rules text says, how far expiration sits from
                  close, and whether the price path resolves continuously or jumps
  4  PROPOSAL     a mode proposed by a DECLARED rule over those signals, with the signals shown
                  beside it, or INSUFFICIENT_EVIDENCE where they conflict or are missing
  5  PACKAGE      the review table, ready to hand to Platform Change Review

WHAT IT DOES NOT DO. It does not edit `SERIES_TYPES`, in either copy. It proposes; a human
decides; the change goes through Platform Change Review because both copies of that table are
shared platform semantics that other books read. And it never defaults an unknown to a mode:
a prefix whose evidence does not decide comes back as INSUFFICIENT_EVIDENCE and stays excluded.

Read-only; stdlib + psycopg only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cluster_stats as cs  # noqa: E402
from mmsell_market_types import (  # noqa: E402
    RO_OPTIONS,
    UNCLASSIFIED,
    _to_libpq_url,
    classify,
    series_of,
)

# --- PREREGISTERED, fixed before the measurement -----------------------------------------------

# The 2x2 design is BLOCKED_DATA while more than this share of its eligible population cannot
# be assigned a settlement mode. Unchanged from the design document.
UNCLASSIFIED_BAR = 0.05

# The eligible population: non-crypto, entry band 5-7c inclusive. Both come from the design,
# not from this script.
BAND = (5.0, 7.0)
CRYPTO_PREFIXES = ("KXBTC", "KXETH", "KXSOL", "KXXRP", "KXDOGE")

SCHEDULED, IN_PLAY, DISCRETE = "scheduled", "in_play", "discrete"

# --- the declared evidence rule -----------------------------------------------------------------
#
# Four signals, each read from the data rather than assumed. Two are STRONG (what Kalshi itself
# publishes about how the market settles) and two are WEAK (shape evidence that corroborates but
# cannot decide alone). A proposal requires at least one strong signal and no strong signal
# pointing elsewhere; otherwise the prefix is INSUFFICIENT_EVIDENCE and stays excluded.

_SOURCE_PATTERNS = (
    (SCHEDULED, re.compile(
        r"cme|coinbase|reference rate|bloomberg|refinitiv|s&p|nasdaq|nyse|bls\b|"
        r"bureau of labor|federal reserve|treasury|eia\b|census bureau|closing (price|value)",
        re.I)),
    (IN_PLAY, re.compile(
        r"espn|official (score|result)|league|nfl|nba|mlb|nhl|fifa|uefa|atp|wta|pga|ncaa|"
        r"box ?score|final whistle", re.I)),
    (DISCRETE, re.compile(
        r"press release|announcement|official statement|credible report|white house|"
        r"court (filing|docket)|sec filing", re.I)),
)

_RULES_PATTERNS = (
    (SCHEDULED, re.compile(
        r"\bat \d{1,2}:\d{2}\s*(am|pm)?\s*(et|est|edt|utc)|closing (price|level|value)|"
        r"as of \d{1,2}:\d{2}|scheduled (release|print|announcement)|"
        r"settlement (price|value|time)", re.I)),
    (IN_PLAY, re.compile(
        r"final score|wins? the (game|match|contest|series)|defeats?|at the end of (the )?"
        r"(game|match|regulation|contest)|full ?time|end of the \d+(st|nd|rd|th)", re.I)),
    (DISCRETE, re.compile(
        r"on or before|announce[sd]?|is confirmed|resigns?|steps? down|is (nominated|indicted)|"
        r"before \w+ \d{1,2}", re.I)),
)

# An expiration essentially AT the close is the signature of a scheduled settle. Kalshi sets a
# far-future fallback close_time on in-play sports — KXUFCFIGHT reported 335h to close on a
# fight that resolved in 0.4h — so a large gap is in-play evidence, not a data error.
SCHEDULED_GAP_HOURS = 1.0
IN_PLAY_GAP_HOURS = 6.0

# A market still trading mid-book minutes before it resolves is resolving CONTINUOUSLY. One
# that sits at its tail price until an instant and then jumps is scheduled.
MIDBOOK = (15.0, 85.0)
LATE_MIDBOOK_IN_PLAY = 0.25
LATE_MIDBOOK_SCHEDULED = 0.05

# A prefix below this many markets in the eligible population is reported but not proposed on:
# four signals over a handful of markets is anecdote.
MIN_MARKETS_TO_PROPOSE = 5


def is_crypto(series: str | None) -> bool:
    s = (series or "").upper()
    return any(s.startswith(p) for p in CRYPTO_PREFIXES)


# --- data ----------------------------------------------------------------------------------------

def load_candidates(cur, since: str | None, until: str | None) -> list[dict]:
    """ONE row per eligible candidate market: non-crypto, first tick with a mid in the band.

    The candidate stream, not settled trades. A settled-trade census answers "what did the book
    end up holding", which is the book's selection rule talking; the design's supply question is
    about what was OFFERED.
    """
    where = ["mid IS NOT NULL", "mid >= %s", "mid <= %s"]
    args: list = [BAND[0], BAND[1]]
    if since:
        where.append("captured_at >= %s")
        args.append(since)
    if until:
        where.append("captured_at < %s")
        args.append(until)
    cur.execute(
        "SELECT DISTINCT ON (market_ticker) market_ticker, series, captured_at, mid,"
        "       hours_to_close, hours_to_expiration"
        "  FROM mmsell_candidate_ticks"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY market_ticker, captured_at ASC", args)
    rows: list[dict] = []
    for tkr, series, ts, mid, htc, hte in cur.fetchall():
        s = (series or series_of(tkr) or "").upper()
        if is_crypto(s):
            continue
        mtype, mode = classify(s)
        rows.append({
            "ticker": tkr, "series": s, "captured_at": ts, "mid": float(mid),
            "hours_to_close": float(htc) if htc is not None else None,
            "hours_to_expiration": float(hte) if hte is not None else None,
            "mtype": mtype, "mode": mode,
        })
    return rows


def load_market_text(cur, tickers: list[str]) -> dict[str, dict]:
    """Kalshi's own words about how each market settles, where the collector kept them."""
    if not tickers:
        return {}
    out: dict[str, dict] = {}
    step = 5000
    for i in range(0, len(tickers), step):
        cur.execute(
            "SELECT ticker, title, rules_summary, settlement_source, category,"
            "       close_time, expiration_time"
            "  FROM markets WHERE ticker = ANY(%s)", (tickers[i:i + step],))
        for tkr, title, rules, src, cat, close_t, exp_t in cur.fetchall():
            out[tkr] = {"title": title or "", "rules": rules or "", "source": src or "",
                        "category": cat or "", "close_time": close_t,
                        "expiration_time": exp_t}
    return out


def load_late_shape(cur, tickers: list[str]) -> dict[str, float]:
    """Per market: the yes mid at the LAST candidate tick before it left the scan.

    A market still quoting mid-book at that point is resolving continuously; one sitting at a
    tail price is waiting for an instant. Read off the candidate stream because that is the
    only price path retained for these markets.
    """
    if not tickers:
        return {}
    out: dict[str, float] = {}
    step = 5000
    for i in range(0, len(tickers), step):
        cur.execute(
            "SELECT DISTINCT ON (market_ticker) market_ticker, mid"
            "  FROM mmsell_candidate_ticks"
            " WHERE market_ticker = ANY(%s) AND mid IS NOT NULL"
            " ORDER BY market_ticker, captured_at DESC", (tickers[i:i + step],))
        for tkr, mid in cur.fetchall():
            out[tkr] = float(mid)
    return out


# --- signals ---------------------------------------------------------------------------------------

def _match_mode(text: str, patterns) -> str | None:
    hits = [mode for mode, rx in patterns if rx.search(text or "")]
    return hits[0] if len(set(hits)) == 1 else None


def prefix_evidence(rows: list[dict], text: dict[str, dict], late: dict[str, float]) -> dict:
    """The four signals for ONE series prefix, each with the sample it rests on."""
    tickers = [r["ticker"] for r in rows]
    blobs = [text.get(t, {}) for t in tickers]

    src_votes = [m for m in (_match_mode(b.get("source", ""), _SOURCE_PATTERNS)
                             for b in blobs) if m]
    rule_votes = [m for m in (_match_mode(f"{b.get('title', '')} {b.get('rules', '')}",
                                          _RULES_PATTERNS) for b in blobs) if m]

    gaps = []
    for b in blobs:
        ct, et = b.get("close_time"), b.get("expiration_time")
        if ct and et:
            gaps.append(abs((et - ct).total_seconds()) / 3600.0)
    # Fall back to the candidate stream's own two clocks where `markets` has no row.
    if not gaps:
        gaps = [abs(r["hours_to_close"] - r["hours_to_expiration"]) for r in rows
                if r["hours_to_close"] is not None and r["hours_to_expiration"] is not None]

    lates = [late[t] for t in tickers if t in late]
    late_mid = (sum(1 for v in lates if MIDBOOK[0] <= v <= MIDBOOK[1]) / len(lates)
                if lates else None)

    def majority(votes: list[str]) -> tuple[str | None, float, int]:
        if not votes:
            return None, 0.0, 0
        counts: dict[str, int] = defaultdict(int)
        for v in votes:
            counts[v] += 1
        mode, n = max(counts.items(), key=lambda kv: kv[1])
        return mode, n / len(votes), len(votes)

    med_gap = sorted(gaps)[len(gaps) // 2] if gaps else None
    gap_mode = (None if med_gap is None
                else SCHEDULED if med_gap <= SCHEDULED_GAP_HOURS
                else IN_PLAY if med_gap >= IN_PLAY_GAP_HOURS else None)
    shape_mode = (None if late_mid is None
                  else IN_PLAY if late_mid >= LATE_MIDBOOK_IN_PLAY
                  else SCHEDULED if late_mid <= LATE_MIDBOOK_SCHEDULED else None)

    return {
        "markets": len(rows),
        "events": cs.cluster_profile(
            [{"ev": r["ticker"].rsplit("-", 1)[0]} for r in rows], "ev")["clusters"],
        "source": majority(src_votes), "rules": majority(rule_votes),
        "median_gap_h": med_gap, "gap_mode": gap_mode,
        "late_midbook": late_mid, "shape_mode": shape_mode,
        "text_rows": sum(1 for b in blobs if b),
        "sample_title": next((b.get("title", "") for b in blobs if b.get("title")), ""),
        "sample_source": next((b.get("source", "") for b in blobs if b.get("source")), ""),
    }


def propose(ev: dict) -> tuple[str, str]:
    """(proposed_mode, why) under the declared rule. INSUFFICIENT_EVIDENCE is a real answer.

    A proposal needs at least one STRONG signal — Kalshi's own settlement source or its rules
    text — and no strong signal pointing somewhere else. The two shape signals corroborate; on
    their own they cannot name a mode, because both a scheduled print and a discrete
    announcement look like a jump.
    """
    if ev["markets"] < MIN_MARKETS_TO_PROPOSE:
        return "INSUFFICIENT_EVIDENCE", f"only {ev['markets']} markets in the population"
    strong = [m for m in (ev["source"][0], ev["rules"][0]) if m]
    if not strong:
        weak = [m for m in (ev["gap_mode"], ev["shape_mode"]) if m]
        if weak:
            return "INSUFFICIENT_EVIDENCE", (
                f"only shape evidence ({'/'.join(sorted(set(weak)))}); no settlement source or "
                "rules text to confirm it")
        return "INSUFFICIENT_EVIDENCE", "no settlement source, rules text or shape signal"
    if len(set(strong)) > 1:
        return "INSUFFICIENT_EVIDENCE", (
            f"settlement source says {ev['source'][0]}, rules text says {ev['rules'][0]}")
    mode = strong[0]
    corroborating = [m for m in (ev["gap_mode"], ev["shape_mode"]) if m]
    if any(m != mode for m in corroborating):
        return "INSUFFICIENT_EVIDENCE", (
            f"text says {mode} but the price path/expiration says "
            f"{'/'.join(sorted({m for m in corroborating if m != mode}))}")
    agree = "corroborated by " + " + ".join(
        n for n, m in (("expiration gap", ev["gap_mode"]), ("price path", ev["shape_mode"]))
        if m == mode) if corroborating else "no corroborating shape evidence"
    which = "settlement source" if ev["source"][0] else "rules text"
    return mode, f"{which} ({ev['source'][1] if ev['source'][0] else ev['rules'][1]:.0%} of "\
                 f"{ev['source'][2] if ev['source'][0] else ev['rules'][2]} texts); {agree}"


# --- report ------------------------------------------------------------------------------------------

def head(t: str) -> None:
    print()
    print("=" * 100)
    print(t)
    print("=" * 100)


def census(rows: list[dict]) -> float:
    """Section 1. Returns `unclassified_excluded_pct` — the number the design is gated on."""
    head("1. CANDIDATE CENSUS — the eligible non-crypto population by settlement mode")
    print(f"  eligible = non-crypto, yes mid {BAND[0]:.0f}-{BAND[1]:.0f}c, one row per market at "
          "its first in-band tick.")
    if not rows:
        print("  NO eligible candidates in this window.")
        return 1.0
    by_mode: dict[str, int] = defaultdict(int)
    for r in rows:
        by_mode[r["mode"]] += 1
    total = len(rows)
    print()
    print(f"  {'settle mode':<20} {'markets':>10} {'share':>9}")
    print("  " + "-" * 42)
    for mode, n in sorted(by_mode.items(), key=lambda kv: -kv[1]):
        print(f"  {mode:<20} {n:>10,} {n / total:>8.2%}")
    print("  " + "-" * 42)
    print(f"  {'TOTAL':<20} {total:>10,}")
    unc = by_mode.get(UNCLASSIFIED[1], 0) / total
    print()
    print(f"  unclassified_excluded_pct = {unc:.2%}   bar {UNCLASSIFIED_BAR:.0%}   "
          f"{'PASS' if unc <= UNCLASSIFIED_BAR else 'FAIL — BLOCKED_DATA'}")
    print(f"  distinct series in the population: {len({r['series'] for r in rows}):,}")
    return unc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--top", type=int, default=60, help="unknown prefixes to detail")
    args = ap.parse_args(argv)

    import psycopg

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("no DATABASE_URL_RO", file=sys.stderr)
        return 2
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            rows = load_candidates(cur, args.since, args.until)
            unknown = [r for r in rows if r["mode"] == UNCLASSIFIED[1]]
            tickers = [r["ticker"] for r in unknown]
            text = load_market_text(cur, tickers)
            late = load_late_shape(cur, tickers)

    print("commit: " + (os.environ.get("GITHUB_SHA") or "unknown (not run from CI)"))
    print(f"window: since={args.since or 'all'} until={args.until or 'now'}")
    unc = census(rows)

    head("2. UNKNOWN PREFIXES — the whole repair, not the crypto corner of it")
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in unknown:
        groups[r["series"]].append(r)
    print(f"  unclassified markets: {len(unknown):,} across {len(groups):,} series prefixes")
    print(f"  Kalshi text available for {len(text):,} of them; last-tick price for {len(late):,}")
    if not groups:
        print("  nothing to repair.")
        return 0
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    covered = sum(len(v) for _, v in ranked[:args.top])
    print(f"  detailing the top {min(args.top, len(ranked))} by supply, covering "
          f"{covered / len(unknown):.1%} of unclassified markets")

    head("3. EVIDENCE AND PROPOSAL — one row per prefix, signals shown beside the proposal")
    print("  Signals: SRC = Kalshi's settlement_source text; RULES = title + rules_summary;")
    print("  GAP = median |expiration - close| hours; PATH = share still quoting 15-85c at the")
    print("  last tick. SRC and RULES are STRONG; GAP and PATH corroborate but cannot decide.")
    print()
    print(f"  {'prefix':<26} {'mkts':>6} {'SRC':>10} {'RULES':>10} {'GAP h':>8} {'PATH':>7}  "
          f"{'PROPOSED':<22}")
    print("  " + "-" * 100)
    proposals: list[tuple[str, int, str, str]] = []
    for prefix, rs in ranked[:args.top]:
        ev = prefix_evidence(rs, text, late)
        mode, why = propose(ev)
        proposals.append((prefix, ev["markets"], mode, why))
        gap = f"{ev['median_gap_h']:.1f}" if ev["median_gap_h"] is not None else "-"
        path = f"{ev['late_midbook']:.0%}" if ev["late_midbook"] is not None else "-"
        print(f"  {prefix:<26} {ev['markets']:>6,} {ev['source'][0] or '-':>10} "
              f"{ev['rules'][0] or '-':>10} {gap:>8} {path:>7}  {mode:<22}")
    print()
    for prefix, _n, mode, why in proposals:
        print(f"  {prefix:<26} {mode:<22} {why}")

    head("4. PACKAGE — what Platform Change Review is being asked to decide")
    decided = [p for p in proposals if p[2] in (SCHEDULED, IN_PLAY, DISCRETE)]
    blocked = [p for p in proposals if p[2] == "INSUFFICIENT_EVIDENCE"]
    covered_n = sum(n for _, n, _, _ in decided)
    print(f"  prefixes with a proposed mode:      {len(decided):>4}  "
          f"({covered_n:,} markets)")
    print(f"  prefixes with INSUFFICIENT evidence: {len(blocked):>4}  "
          f"({sum(n for _, n, _, _ in blocked):,} markets)")
    print()
    if len(unknown):
        after = (len(unknown) - covered_n) / max(1, len(rows))
        print(f"  unclassified_excluded_pct if EVERY proposal above is accepted: {after:.2%}")
        print(f"  bar {UNCLASSIFIED_BAR:.0%} -> "
              f"{'the design would clear its pre-start check' if after <= UNCLASSIFIED_BAR else 'STILL BLOCKED; the remaining prefixes need a human read of Kalshi rules text'}")
    print()
    print("  These are PROPOSALS. Nothing here edits SERIES_TYPES, in either copy — that table")
    print("  is shared platform semantics read by every mmsell mode= book, so a change to it is")
    print("  a Platform Change Review event with its own impact review, not a side effect of an")
    print("  analysis script. A prefix marked INSUFFICIENT_EVIDENCE must stay unclassified: an")
    print("  in-play series wrongly recorded as scheduled would put in-play markets into the")
    print("  treatment arm and make the primary comparison measure the confound it controls for.")
    print()
    print(f"RESULT: unclassified_excluded_pct = {unc:.2%} against a {UNCLASSIFIED_BAR:.0%} bar; "
          f"{'PASS' if unc <= UNCLASSIFIED_BAR else 'BLOCKED_DATA — do not create the arms'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
