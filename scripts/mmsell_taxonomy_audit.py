"""The Platform Change Review package for MMSELL's settlement taxonomy — and the census it gates.

WHY. The MMSELL 2x2 paper design cannot start: a seventh of its eligible non-crypto 5-7c
population classifies as `unknown` against a 5% bar fixed before the measurement. The first
scoping of the repair named six crypto series. That was wrong by two orders of magnitude, and
worse, it carried an assumption — that an unknown series is a scheduled one — which is exactly
the kind of guess a taxonomy exists to eliminate. An `unknown` prefix silently treated as
`scheduled` would put in-play markets into the treatment arm and make the primary comparison
measure the thing the design is trying to hold constant.

HOW EVIDENCE IS COUNTED. Over DISTINCT rule documents, never over markets. Settlement semantics
are a property of the SERIES, so one rule document answers for every market under a prefix — but
it answers once. Run `tax-6` fetched one market per prefix, copied its blob onto all forty-six
markets, and reported "100% of 46 texts". That is one document counted forty-six times.

WHAT THIS DOES.

  1  CENSUS       the eligible candidate population by settle mode, and `unclassified_excluded_
                  pct` against its bar. This is the SAME function that must be re-run after the
                  repair, so "rerun the exact same census" is one command rather than a promise
  2  PREFIXES     every unclassified series in that population, with its supply
  3  EVIDENCE     per prefix, up to eight Kalshi markets inspected across settled and open
                  status, deduplicated to DISTINCT rule documents; then four signals — what
                  Kalshi says the settlement source is, what the rules text says, how far
                  expiration sits from close, and whether the price path resolves or jumps
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
import json
import os
import re
import sys
import time
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

# Patterns read against the corpus they classify (run `tax-3` dumped it verbatim), not guessed.
# The first draft matched a bare "at 8:10 PM EDT" as SCHEDULED, which is a game START time —
# Kalshi writes "the game originally scheduled for Aug 22, 2026 at 8:10 PM EDT" on in-play
# markets, so that pattern proposed `scheduled` for MLB player props and KBO baseball. A bare
# clock time does not discriminate; genuinely scheduled markets say what CLOSE they settle to.
_RULES_PATTERNS = (
    (SCHEDULED, re.compile(
        r"end-of-day .{0,40}(value|price|level)|close price of the|"
        r"closing (price|level|value)|settles? to the .{0,30}clos|"
        r"official (settlement|closing) (price|value)|"
        r"first release of the data|as reported by .{0,40}at \d{1,2}:\d{2}", re.I)),
    (IN_PLAY, re.compile(
        r"after 90 minutes plus stoppage time|originally scheduled for|"
        r"final score|wins? the .{0,60}(game|match|contest|series|championship|tournament)|"
        r"records? \d+\+|at the end of (the )?(game|match|regulation|contest)|"
        r"full ?time|end of the \d+(st|nd|rd|th)|at any point during|"
        r"if (this|the) (game|match) is postponed", re.I)),
    (DISCRETE, re.compile(
        r"on or before|is (nominated|indicted|confirmed)|resigns?|steps? down|"
        r"is eliminated|announce[sd]? (that|the)", re.I)),
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


# --- Kalshi's own words, fetched ------------------------------------------------------------------

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get_json(url: str, timeout: float) -> dict:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:       # noqa: S310 — fixed host
        return json.loads(resp.read().decode())


# How many markets to inspect per prefix, and the statuses to draw them from. One document is
# one document however many markets carry it: run `tax-6` fetched ONE market per prefix, copied
# its blob onto every market under that prefix, and then reported "100% of 46 texts" — one
# document counted forty-six times. Settlement semantics are a property of the series, so a
# single representative document is a legitimate human-review aid; it is not N confirmations.
SAMPLE_MARKETS_PER_PREFIX = 8
SAMPLE_STATUSES = ("settled", "open")

# Distinct rule documents needed before a prefix's text counts as more than one observation.
# Below this the proposal still stands or falls on the document's CONTENT — it is simply
# reported as resting on one document, which is what it does.
MIN_UNIQUE_DOCS_FOR_CONSISTENCY = 2


def _norm(text: str) -> str:
    """Collapse whitespace so two renderings of the same rule document compare equal."""
    return " ".join((text or "").split()).lower()


def fetch_series_text(prefix: str, want: int = SAMPLE_MARKETS_PER_PREFIX,
                      timeout: float = 20.0) -> dict:
    """Up to `want` markets for `prefix`, with their DISTINCT rule documents.

    The database cannot answer this: `markets` holds no row for any of these tickers, so an
    audit that reads only the database has no strong signal for any prefix and correctly
    refuses to propose anything. Kalshi's market-data endpoints are public and need no key, and
    the ops runner has open egress.

    Draws from settled markets first, then open ones, so the sample spans a series' history
    rather than whatever happens to be listed today — a rule document that changed mid-season
    should show up as a conflict, not as whichever version was fetched.

    Returns unique markets inspected, the deduplicated documents, and the raw field names seen,
    so a schema change at Kalshi surfaces as a reported field list rather than empty evidence.
    """
    seen_tickers: set[str] = set()
    markets: list[dict] = []
    schema: list[str] = []
    for status in SAMPLE_STATUSES:
        if len(markets) >= want:
            break
        for attempt in range(2):
            try:
                payload = _get_json(
                    f"{KALSHI}/markets?series_ticker={prefix}&limit={want}"
                    f"&status={status}", timeout)
                break
            except Exception:                                        # noqa: BLE001
                payload = {}
                time.sleep(0.5 * (attempt + 1))
        for m in (payload.get("markets") or []):
            tkr = str(m.get("ticker") or "")
            if tkr and tkr in seen_tickers:
                continue
            seen_tickers.add(tkr)
            markets.append(m)
            schema = schema or sorted(m.keys())
        time.sleep(0.1)
    docs: dict[str, dict] = {}
    for m in markets:
        rules = " ".join(str(m.get(k) or "")
                         for k in ("rules_primary", "rules_secondary", "rules_summary"))
        title = " ".join(str(m.get(k) or "") for k in ("title", "subtitle"))
        source = str(m.get("settlement_source") or "")
        key = _norm(f"{rules}|{source}")
        docs.setdefault(key, {
            "title": title, "rules": rules, "source": source,
            "category": str(m.get("category") or ""),
            "can_close_early": m.get("can_close_early"), "n_markets": 0})
        docs[key]["n_markets"] += 1
    return {"unique_markets": len(markets), "docs": list(docs.values()), "schema": schema,
            "statuses": SAMPLE_STATUSES}


# --- signals ---------------------------------------------------------------------------------------

def _match_mode(text: str, patterns) -> str | None:
    hits = [mode for mode, rx in patterns if rx.search(text or "")]
    return hits[0] if len(set(hits)) == 1 else None


def prefix_evidence(rows: list[dict], db_text: dict[str, dict], kalshi: dict,
                    late: dict[str, float]) -> dict:
    """The signals for ONE series prefix, each with the sample it actually rests on.

    STRONG signals are counted over DISTINCT rule documents, never over markets. A prefix with
    forty markets and one rule document has one observation of what Kalshi says, and reporting
    it as forty was the accounting error `tax-6` shipped.
    """
    tickers = [r["ticker"] for r in rows]
    blobs = [db_text.get(t, {}) for t in tickers]
    docs = list(kalshi.get("docs") or [])

    src_votes = [m for m in (_match_mode(d.get("source", ""), _SOURCE_PATTERNS)
                             for d in docs) if m]
    rule_votes = [m for m in (_match_mode(f"{d.get('title', '')} {d.get('rules', '')}",
                                          _RULES_PATTERNS) for d in docs) if m]

    gaps = []
    for b in blobs:
        ct, et = b.get("close_time"), b.get("expiration_time")
        if ct and et:
            gaps.append(abs((et - ct).total_seconds()) / 3600.0)
    # Fall back to the candidate stream's own two clocks where `markets` has no row — which,
    # for every prefix in this population, is all of them.
    if not gaps:
        gaps = [abs(r["hours_to_close"] - r["hours_to_expiration"]) for r in rows
                if r["hours_to_close"] is not None and r["hours_to_expiration"] is not None]

    lates = [late[t] for t in tickers if t in late]
    late_mid = (sum(1 for v in lates if MIDBOOK[0] <= v <= MIDBOOK[1]) / len(lates)
                if lates else None)
    early = [d.get("can_close_early") for d in docs if d.get("can_close_early") is not None]
    early_share = (sum(1 for e in early if e) / len(early)) if early else None

    def majority(votes: list[str]) -> tuple[str | None, float, int]:
        """(winner, its share, number of DOCUMENTS that voted)."""
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
        "early_share": early_share,
        "markets": len(rows),
        "events": cs.cluster_profile(
            [{"ev": r["ticker"].rsplit("-", 1)[0]} for r in rows], "ev")["clusters"],
        "unique_markets": int(kalshi.get("unique_markets") or 0),
        "unique_docs": len(docs),
        "source": majority(src_votes), "rules": majority(rule_votes),
        "median_gap_h": med_gap, "gap_mode": gap_mode,
        "late_midbook": late_mid, "shape_mode": shape_mode,
        "sample_title": next((d.get("title", "") for d in docs if d.get("title")), ""),
        "sample_source": next((d.get("source", "") for d in docs if d.get("source")), ""),
    }


def propose(ev: dict) -> tuple[str, str]:
    """(proposed_mode, why) under the declared rule. INSUFFICIENT_EVIDENCE is a real answer.

    A proposal needs at least one STRONG signal — Kalshi's own settlement source or its rules
    text — with no strong signal pointing elsewhere, no *lone* weak signal against it, and no
    DISAGREEMENT among the distinct rule documents inspected. The two shape signals corroborate;
    on their own they cannot name a mode, because both a scheduled print and a discrete
    announcement look like a jump.

    Counted over DOCUMENTS, not markets. A prefix with one rule document has one observation of
    what Kalshi says however many markets carry it.
    """
    if ev["markets"] < MIN_MARKETS_TO_PROPOSE:
        return "INSUFFICIENT_EVIDENCE", f"only {ev['markets']} markets in the population"
    if not ev["unique_docs"]:
        return "INSUFFICIENT_EVIDENCE", "no Kalshi rules text retrieved for this series"
    strong = [m for m in (ev["source"][0], ev["rules"][0]) if m]
    if not strong:
        weak = [m for m in (ev["gap_mode"], ev["shape_mode"]) if m]
        if weak:
            return "INSUFFICIENT_EVIDENCE", (
                f"only shape evidence ({'/'.join(sorted(set(weak)))}); "
                f"{ev['unique_docs']} rule doc(s) name no settlement mode")
        return "INSUFFICIENT_EVIDENCE", "no settlement source, rules text or shape signal"
    if len(set(strong)) > 1:
        return "INSUFFICIENT_EVIDENCE", (
            f"settlement source says {ev['source'][0]}, rules text says {ev['rules'][0]}")
    mode = strong[0]
    # Documents that disagree with each other are the strongest possible reason to refuse: the
    # series does not have ONE settlement semantics, or the sample spans a rule change.
    for label, votes in (("settlement source", ev["source"]), ("rules text", ev["rules"])):
        if votes[0] and votes[1] < 1.0:
            return "INSUFFICIENT_EVIDENCE", (
                f"{votes[2]} distinct {label} documents disagree "
                f"({votes[1]:.0%} say {votes[0]})")
    corroborating = [m for m in (ev["gap_mode"], ev["shape_mode"]) if m]
    # A weak signal only blocks when it stands ALONE against the text. Two corroborators that
    # disagree with each other cancel.
    against = [m for m in corroborating if m != mode]
    if against and not any(m == mode for m in corroborating):
        return "INSUFFICIENT_EVIDENCE", (
            f"text says {mode} but the price path/expiration says "
            f"{'/'.join(sorted(set(against)))}")
    agreeing = [n for n, m in (("expiration gap", ev["gap_mode"]),
                               ("price path", ev["shape_mode"])) if m == mode]
    agree = ("corroborated by " + " + ".join(agreeing) if agreeing
             else "no corroborating shape evidence")
    if against:
        agree += f" ({'/'.join(sorted(set(against)))} disagrees)"
    which = "settlement source" if ev["source"][0] else "rules text"
    votes = ev["source"] if ev["source"][0] else ev["rules"]
    basis = (f"{votes[2]} distinct {which} document(s) from {ev['unique_markets']} markets, "
             f"unanimous" if votes[2] >= MIN_UNIQUE_DOCS_FOR_CONSISTENCY
             else f"ONE {which} document from {ev['unique_markets']} markets — "
                  "the series shares one rule text, so this is one observation, not "
                  f"{ev['unique_markets']}")
    return mode, f"{basis}; {agree}"


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
    ap.add_argument("--dump-text", action="store_true",
                    help="print Kalshi's title, rules and settlement source verbatim for each "
                         "prefix. THIS is the Platform Change Review package: a human reading "
                         "Kalshi's own words decides each mode, and the automated proposal is "
                         "a reading aid beside it, not the decision.")
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip the Kalshi metadata fetch. `markets` holds no row for any of "
                         "these tickers, so this leaves the audit with shape evidence only "
                         "and it will refuse to propose anything — which is the point of "
                         "keeping the flag: it shows what the database alone can support.")
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
    print(f"window: since={args.since or 'all'} until={args.until or 'now'}  "
          f"kalshi_fetch={'off' if args.no_fetch else 'on'}")
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

    # Kalshi's own rules text, per prefix, because the database has none. One request per
    # prefix. Settlement mode is a property of the SERIES, so a sample of its markets answers
    # for all of them — but it answers ONCE, however many markets carry the same rule text.
    fetched: dict[str, dict] = {}
    schema: list[str] = []
    if not args.no_fetch:
        for prefix, _rs in ranked[:args.top]:
            got = fetch_series_text(prefix)
            if not got.get("docs"):
                continue
            schema = schema or got["schema"]
            fetched[prefix] = got
        n_docs = sum(len(g["docs"]) for g in fetched.values())
        n_mkts = sum(g["unique_markets"] for g in fetched.values())
        print(f"  Kalshi rules text fetched for {len(fetched)}/{min(args.top, len(ranked))} "
              f"prefixes: {n_mkts:,} unique markets inspected yielding {n_docs:,} DISTINCT rule")
        print(f"  documents (up to {SAMPLE_MARKETS_PER_PREFIX} markets per prefix, drawn from "
              f"{'+'.join(SAMPLE_STATUSES)}; public endpoint, no key)")
        print("  Strong signals below are counted over DOCUMENTS, never markets: a series that")
        print("  shares one rule text across forty markets is ONE observation of what Kalshi")
        print("  says, and reporting it as forty was the accounting error in run `tax-6`.")
        if schema:
            print(f"  market fields seen: {', '.join(schema[:18])}"
                  + (" ..." if len(schema) > 18 else ""))
        if not fetched:
            print("  NOTHING fetched — every proposal below rests on shape evidence alone and")
            print("  will therefore be INSUFFICIENT_EVIDENCE. Check egress before reading this")
            print("  as a finding about Kalshi's series.")
    print(f"  detailing the top {min(args.top, len(ranked))} by supply, covering "
          f"{covered / len(unknown):.1%} of unclassified markets")

    head("3. EVIDENCE AND PROPOSAL — one row per prefix, signals shown beside the proposal")
    print("  seen = unique Kalshi markets inspected; docs = DISTINCT rule documents among them.")
    print("  SRC = Kalshi's settlement_source text; RULES = title + rules text. Both are STRONG")
    print("  and are counted over DOCUMENTS. GAP = median |expiration - close| hours; PATH =")
    print("  share still quoting 15-85c at the last tick; both corroborate and neither can")
    print("  decide alone, because a scheduled print and a discrete announcement both jump.")
    print("  `can_close_early` is not shown: run tax-2 found Kalshi sets it on 100% of these")
    print("  markets, index-close ones included, so it does not discriminate anything.")
    print()
    print(f"  {'prefix':<26} {'mkts':>6} {'seen':>5} {'docs':>5} {'SRC':>10} {'RULES':>10} "
          f"{'GAP h':>8} {'PATH':>7}  {'PROPOSED':<22}")
    print("  " + "-" * 108)
    proposals: list[tuple[str, int, str, str]] = []
    for prefix, rs in ranked[:args.top]:
        ev = prefix_evidence(rs, text, fetched.get(prefix, {}), late)
        mode, why = propose(ev)
        proposals.append((prefix, ev["markets"], mode, why))
        gap = f"{ev['median_gap_h']:.1f}" if ev["median_gap_h"] is not None else "-"
        path = f"{ev['late_midbook']:.0%}" if ev["late_midbook"] is not None else "-"
        print(f"  {prefix:<26} {ev['markets']:>6,} {ev['unique_markets']:>5} "
              f"{ev['unique_docs']:>5} {ev['source'][0] or '-':>10} "
              f"{ev['rules'][0] or '-':>10} {gap:>8} {path:>7}  {mode:<22}")
    print()
    for prefix, _n, mode, why in proposals:
        print(f"  {prefix:<26} {mode:<22} {why}")

    if args.dump_text and fetched:
        head("3B. KALSHI'S OWN WORDS — the evidence, verbatim, for a human to read")
        print("  Every DISTINCT rule document found for each series, with how many of the")
        print("  inspected markets carried it. Settlement mode is a property of the series, so")
        print("  one document answers for the prefix — but it answers ONCE, not once per market.")
        for prefix, _rs in ranked[:args.top]:
            got = fetched.get(prefix)
            if not got:
                continue
            print()
            print(f"  {prefix}  ({len(groups[prefix])} markets in the population; "
                  f"{got['unique_markets']} Kalshi markets inspected, "
                  f"{len(got['docs'])} distinct rule document(s))")
            for i, doc in enumerate(got["docs"], 1):
                print(f"    doc {i}/{len(got['docs'])} — carried by {doc['n_markets']} of the "
                      "inspected markets")
                for label, key in (("title", "title"), ("source", "source"), ("rules", "rules")):
                    val = " ".join((doc.get(key) or "").split())
                    if val:
                        print(f"      {label:<7} {val[:400]}")

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
