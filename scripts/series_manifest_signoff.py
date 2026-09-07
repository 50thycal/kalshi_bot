"""Record an operator's rules review against series in the registry manifest.

WHY THIS EXISTS. The graduation bar has two halves and only one of them is measurable: we can
count our own settled history, but "someone read how this contract settles" is a human act.
`series_rules_audit.py` can compare Kalshi's rules text to the recorded taxonomy, but its
verdict rests on ONE signal — Kalshi's `settlement_source` field is empty across all 138 series,
so every verdict is a single regex whose false-positive rate on this corpus is demonstrably
non-zero (it read "record 50000000+ views" as a live contest). Writing `rules_reviewed_at` off
that alone would launder a regex into a human's signature.

So the operator signs. This script is the pen, not the decision: it writes a name and a date
into rows the operator has already approved, having read the settlement language that
`series_rules_audit --evidence` printed. It cannot decide anything.

WHAT IT REFUSES TO DO.

  * It will not sign a series absent from the manifest — there is no row to sign.
  * It will not sign a series that is `barred`. A refusal is not discharged by reading rules.
  * It will not silently re-sign a row that already carries a review; `--resign` is explicit,
    because overwriting a previous reviewer's name is a different act from adding one.
  * It writes NOTHING when any requested series fails these checks. A partial batch is worse
    than none: the operator believes they signed a list, and the list would not match.

The manifest still moves only by PR. This edits the working tree; the PR is the record, and the
diff is what a second person reviews.

    python3 scripts/series_manifest_signoff.py --by "Calvin" KXMLBGAME KXNFLTOTAL
    python3 scripts/series_manifest_signoff.py --by "Calvin" --at 2026-09-07 --dry-run KXRAIN
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

MANIFEST_PATH = (Path(__file__).resolve().parents[1]
                 / "kalshi_bot" / "registry" / "series_manifest.json")

BARRED = "barred"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def sign(doc: dict, series: list[str], by: str, at: str, *,
         resign: bool = False) -> tuple[list[str], list[str]]:
    """Apply the sign-off in memory. Returns (signed, problems).

    All-or-nothing by contract: the caller must not write when `problems` is non-empty. A batch
    that half-applies leaves the operator believing they signed a list they did not sign."""
    rows = {str(r["series"]).upper(): r for r in doc.get("series", ())}
    problems: list[str] = []
    targets = []
    for s in series:
        key = s.upper()
        row = rows.get(key)
        if row is None:
            problems.append(f"{key}: not in the manifest — nothing to sign")
            continue
        if row.get("state") == BARRED:
            problems.append(f"{key}: barred — a refusal is not discharged by reading rules")
            continue
        if row.get("rules_reviewed_at") and not resign:
            problems.append(f"{key}: already reviewed {row['rules_reviewed_at']} "
                            f"by {row.get('rules_reviewed_by')!r} — pass --resign to overwrite")
            continue
        targets.append(row)
    if problems:
        return [], problems
    for row in targets:
        row["rules_reviewed_at"] = at
        row["rules_reviewed_by"] = by
    return [r["series"] for r in targets], []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record an operator rules review in the manifest")
    ap.add_argument("series", nargs="+", help="series tickers the operator approved")
    ap.add_argument("--by", required=True,
                    help="the person who read the settlement language. A name, not a script")
    ap.add_argument("--at", default=date.today().isoformat(), help="review date (YYYY-MM-DD)")
    ap.add_argument("--resign", action="store_true",
                    help="overwrite an existing review; overwriting another reviewer's name is "
                         "a deliberate act, not a default")
    ap.add_argument("--dry-run", action="store_true", help="print what would change, write none")
    args = ap.parse_args(argv)

    if not args.by.strip() or args.by.strip().lower().endswith((".py", "audit", "script")):
        print("--by must name a person: the whole point is that a human, not a script, "
              "is the evidence here.", file=sys.stderr)
        return 2

    doc = load(MANIFEST_PATH)
    signed, problems = sign(doc, args.series, args.by.strip(), args.at, resign=args.resign)
    if problems:
        print("Refusing to write — nothing was changed:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    print(f"{'Would sign' if args.dry_run else 'Signed'} {len(signed)} series "
          f"as reviewed by {args.by!r} on {args.at}:")
    for s in sorted(signed):
        print(f"  {s}")
    if args.dry_run:
        print("\n(dry run — manifest not written)")
        return 0
    MANIFEST_PATH.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"\nWrote {MANIFEST_PATH}. Commit it: the PR is the record of the review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
