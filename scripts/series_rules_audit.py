"""SERIES RULES AUDIT — does Kalshi's rulebook agree with what we recorded about this series?

WHY THIS EXISTS
---------------
`kalshi_bot/registry/series_manifest.json` graduated 138 series on 2026-09-06 carrying
`rules_reviewed_at: null`. They were seeded mechanically by PR #338 from every series with >= 20
settled markets of own history AND a market-type classification. That bar proves we have DATA
about a contract. It never proved anyone read HOW IT SETTLES — and the two are independent:
`KXNFLSPREAD` cleared it with 1,486 settled markets and -$151.26.

This is the audit that retires that debt. For each graduated series it fetches Kalshi's own rule
documents and asks one question: **does the settlement mode we recorded in `SERIES_TYPES` match
what Kalshi says?** Three answers, and the third is not a failure of the tool:

    CONFIRMS       the evidence names the mode we recorded. The review is discharged.
    CONTRADICTS    the evidence names a DIFFERENT mode. A real finding: the series has been
                   traded under the wrong settlement model, and every book selecting on
                   `mode=` has been picking it up (or missing it) wrongly.
    INSUFFICIENT   the evidence does not decide. The row stays unreviewed. This is the whole
                   reason the audit is safe to automate: it can fail to conclude.

WHAT IT DOES NOT DO. It does not edit the manifest, `SERIES_TYPES`, or any lifecycle state. It
emits evidence; a human opens the PR that records the verdicts; merging that PR is the review.
A CONTRADICTS or INSUFFICIENT row must NEVER be written as `rules_reviewed_at` — that would
launder a machine's uncertainty into a human's signature, which is the exact failure the
two-part graduation bar exists to prevent.

THE EVIDENCE RULE IS BORROWED, NOT REINVENTED. `scripts/mmsell_taxonomy_audit.py` already
derives a settlement mode from Kalshi's settlement-source field and rules text, using patterns
hand-tuned against the real corpus and carrying their own failure history (an early draft read
a bare "at 8:10 PM EDT" as SCHEDULED and so proposed `scheduled` for MLB player props — that is
a game START time). Re-deriving those patterns here would mean re-making those mistakes, so
this imports them. The QUESTION differs — that script PROPOSES a mode for an unclassified
prefix, this one VERIFIES a mode already recorded — but the signals are the same signals.

EVIDENCE IS COUNTED OVER DISTINCT RULE DOCUMENTS, never over markets. Settlement semantics are a
property of the SERIES: one rule document answers for every market under the prefix, but it
answers ONCE. Counting it per market is how run `tax-6` reported "100% of 46 texts" from a
single document.

Read-only against our database; read-only against Kalshi's public API. Runs via the ops channel:

    {"type": "script", "name": "series_rules_audit", "args": ["--top", "10"]}
    {"type": "script", "name": "series_rules_audit", "args": ["--series", "KXNFLSPREAD"]}
    {"type": "script", "name": "series_rules_audit", "args": ["--top", "40", "--emit-patch"]}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mmsell_market_types import classify  # noqa: E402
from mmsell_taxonomy_audit import (  # noqa: E402
    _RULES_PATTERNS,
    _SOURCE_PATTERNS,
    _match_mode,
    fetch_series_text,
)

MANIFEST_PATH = (Path(__file__).resolve().parents[1]
                 / "kalshi_bot" / "registry" / "series_manifest.json")

CONFIRMS, CONTRADICTS, INSUFFICIENT = "CONFIRMS", "CONTRADICTS", "INSUFFICIENT"

#: How many of a series' markets to sample when pulling rule documents. Rule documents dedupe
#: hard — most series have exactly one — so this is about spanning a series' HISTORY (a mid
#: season rule change should surface as a conflict), not about sample size.
SAMPLE_MARKETS = 8


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, dict]:
    doc = json.loads(path.read_text())
    return {str(r["series"]).upper(): r for r in doc.get("series", ())}


def _doc_votes(docs: list[dict], key: str, patterns) -> tuple[str | None, float, int]:
    """(winning mode, share of documents agreeing, documents that voted).

    Over DISTINCT documents. A share below 1.0 means the documents disagree with each other,
    which is the strongest possible reason to refuse a verdict: either the series does not have
    one settlement semantics, or the sample spans a rule change and we cannot say which applies.
    """
    votes = [m for m in (_match_mode(d.get(key, ""), patterns) for d in docs) if m]
    if not votes:
        return None, 0.0, 0
    top = max(set(votes), key=votes.count)
    return top, votes.count(top) / len(votes), len(votes)


def audit_series(series: str, recorded_mode: str, *, timeout: float,
                 sample: int = SAMPLE_MARKETS) -> dict:
    """One series' verdict, with the evidence it rests on.

    `recorded_mode` is what `SERIES_TYPES` claims. The verdict compares the evidence against
    THAT, so a series whose taxonomy entry is right and a series whose taxonomy entry is wrong
    are distinguishable — which a proposal-shaped tool cannot do.
    """
    out = {"series": series, "recorded": recorded_mode, "verdict": INSUFFICIENT,
           "implied": None, "why": "", "docs": 0, "markets": 0,
           "source_vote": None, "rules_vote": None}
    try:
        ev = fetch_series_text(series, want=sample, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — one unreachable series must not end the audit
        out["why"] = f"Kalshi fetch failed: {exc.__class__.__name__}"
        return out

    docs = list(ev.get("docs") or [])
    out["docs"] = len(docs)
    out["markets"] = ev.get("unique_markets", 0)
    if not docs:
        out["why"] = "no rules text retrieved for this series"
        return out

    # Title and rules text are read together, matching how `mmsell_taxonomy_audit` reads them:
    # Kalshi often carries the discriminating phrase in the title ("Final score", "Will X
    # resign") while the rules body is boilerplate.
    titled = [{"text": f"{d.get('title', '')} {d.get('rules', '')}"} for d in docs]
    src_mode, src_share, src_n = _doc_votes(docs, "source", _SOURCE_PATTERNS)
    rul_mode, rul_share, rul_n = _doc_votes(titled, "text", _RULES_PATTERNS)
    out["source_vote"] = (src_mode, round(src_share, 3), src_n)
    out["rules_vote"] = (rul_mode, round(rul_share, 3), rul_n)

    strong = [m for m in (src_mode, rul_mode) if m]
    if not strong:
        out["why"] = f"{len(docs)} rule doc(s) name no settlement mode"
        return out
    if len(set(strong)) > 1:
        out["why"] = f"settlement source says {src_mode}, rules text says {rul_mode}"
        return out
    for label, (mode, share, n) in (("settlement source", (src_mode, src_share, src_n)),
                                    ("rules text", (rul_mode, rul_share, rul_n))):
        if mode and share < 1.0:
            out["why"] = (f"{n} distinct {label} documents disagree "
                          f"({share:.0%} say {mode})")
            return out

    implied = strong[0]
    out["implied"] = implied
    if implied == recorded_mode:
        out["verdict"] = CONFIRMS
        agree = [n for n, m in (("settlement source", src_mode), ("rules text", rul_mode))
                 if m == implied]
        out["why"] = f"{' and '.join(agree)} agree with the recorded {recorded_mode}"
    else:
        out["verdict"] = CONTRADICTS
        out["why"] = (f"recorded as {recorded_mode}, but Kalshi's "
                      f"{'settlement source' if src_mode else 'rules text'} says {implied}")
    return out


def looks_like_a_network_failure(results: list[dict]) -> bool:
    """True when NOTHING was retrieved for any series — an infrastructure failure wearing the
    costume of a result.

    `fetch_series_text` swallows its own HTTP errors and returns an empty payload, so a runner
    with no route to Kalshi produces a clean report reading INSUFFICIENT for all 138 rows and
    "no rules text retrieved for this series" beside each. That is indistinguishable, row by
    row, from a genuine finding — and it is the shape a reader is most likely to skim past,
    because every line looks individually reasonable.

    A real audit never gets zero documents across every series: these are Kalshi's most-traded
    contracts, all with settled markets in our own database. So zero everywhere means the
    network, and the audit must say so instead of reporting 138 honest-looking non-findings."""
    return bool(results) and all(not r.get("markets") for r in results)


def report(results: list[dict]) -> None:
    counts = {v: sum(1 for r in results if r["verdict"] == v)
              for v in (CONFIRMS, CONTRADICTS, INSUFFICIENT)}
    print("# SERIES RULES AUDIT")
    print(f"# audited {len(results)} graduated series | "
          + " ".join(f"{k}={v}" for k, v in counts.items()))
    print("# NOT A GATE. Emits evidence; a human's PR against the manifest records the review.")
    print("# Only CONFIRMS may be written as rules_reviewed_at — see --emit-patch.")
    if looks_like_a_network_failure(results):
        print("#")
        print("# !! NOT A RESULT. Zero markets retrieved for EVERY series audited, which means")
        print("# !! this runner could not reach Kalshi — not that these contracts have no")
        print("# !! published rules. Every INSUFFICIENT below is an artifact. Do not read the")
        print("# !! rows as findings and do not record any review from this run.")

    for verdict, blurb in (
        (CONTRADICTS, "the recorded settlement mode is WRONG — read these first"),
        (INSUFFICIENT, "evidence does not decide; the row stays unreviewed"),
        (CONFIRMS, "evidence agrees with the taxonomy; the review is discharged"),
    ):
        rows = [r for r in results if r["verdict"] == verdict]
        print(f"\n## {verdict} ({len(rows)}) — {blurb}")
        if not rows:
            print("  (none)")
            continue
        print(f"  {'series':<24} {'recorded':<10} {'implied':<10} {'docs':>4} {'mkts':>5}  why")
        for r in sorted(rows, key=lambda x: x["series"]):
            print(f"  {r['series']:<24} {r['recorded']:<10} "
                  f"{(r['implied'] or '-'):<10} {r['docs']:>4} {r['markets']:>5}  {r['why']}")


def emit_patch(results: list[dict], reviewed_by: str) -> None:
    """The manifest edit a human can apply for the CONFIRMS rows, printed as JSON.

    CONFIRMS only, deliberately. A CONTRADICTS row needs the taxonomy fixed before anything is
    signed off, and an INSUFFICIENT row has nothing to sign. Emitting either here would let a
    machine's uncertainty be pasted in as a human's review."""
    if looks_like_a_network_failure(results):
        print("\n## PATCH — refused")
        print("# Zero markets retrieved for every series: this run reached no evidence at all.")
        print("# Emitting an empty patch here would read as 'nothing confirmed', which is a")
        print("# finding. It is not one.")
        return
    confirmed = sorted(r["series"] for r in results if r["verdict"] == CONFIRMS)
    print("\n## PATCH — apply to kalshi_bot/registry/series_manifest.json")
    print(f"# {len(confirmed)} CONFIRMS rows. Contradictions and insufficient evidence are")
    print("# deliberately absent: neither is a review.")
    print(json.dumps({"rules_reviewed_by": reviewed_by, "series": confirmed}, indent=1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify recorded settlement modes against Kalshi")
    ap.add_argument("--series", default=None,
                    help="audit one series only (default: every graduated series)")
    ap.add_argument("--top", type=int, default=None,
                    help="audit at most this many series (manifest order)")
    ap.add_argument("--sample", type=int, default=SAMPLE_MARKETS,
                    help=f"markets sampled per series (default {SAMPLE_MARKETS})")
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--emit-patch", action="store_true",
                    help="also print the manifest patch for the CONFIRMS rows")
    ap.add_argument("--reviewed-by", default="series_rules_audit + operator review",
                    help="what to record as rules_reviewed_by in the patch")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    if args.series:
        wanted = [args.series.upper()]
    else:
        wanted = [s for s, r in sorted(manifest.items())
                  if r.get("state") == "graduated" and not r.get("rules_reviewed_at")]
    if args.top:
        wanted = wanted[:args.top]

    results = []
    for series in wanted:
        mode = classify(series)[1]
        results.append(audit_series(series, mode, timeout=args.timeout, sample=args.sample))

    report(results)
    if args.emit_patch:
        emit_patch(results, args.reviewed_by)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
