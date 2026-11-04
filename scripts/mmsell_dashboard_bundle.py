#!/usr/bin/env python3
"""Build the mmsell dashboard's ONE data file from this run's scorecard and the review files.

The dashboard (https://claude.ai/artifact/GKUXvPcdU7RJRUEsNvNCfP) reads a single dataset,
`scorecard`, whose rows each carry a `ds` field naming the table they belong to. The page splits
them with one `dash.calc` per table. One file means one upload per 72h review instead of one per
table (owner request 2026-11-04: fewer approval prompts).

    ds = by_book | by_tag | by_event | losers | overlap   from `mmsell_scorecard --json`
    ds = history          one row per book per review, rebuilt from every
                          docs/mmsell/scorecards/YYYY-MM-DD.md `JSON {...}` line
    ds = recommendations  the newest review file's recommendations table

Nothing is read back from the dashboard: the repo's review files are the record. Local only
(stdlib); it reads files and writes one JSON file.

    python scripts/mmsell_dashboard_bundle.py --scorecard run.json --out bundle.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

TABLES = ("by_book", "by_tag", "by_event", "losers", "overlap")
SCORECARDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                          "docs", "mmsell", "scorecards")
_DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
_REC_COLS = ("area", "call", "item", "evidence", "change_if")


def review_files(folder: str = SCORECARDS) -> list[tuple[str, str]]:
    """[(YYYY-MM-DD, path)] oldest first."""
    out = []
    for name in sorted(os.listdir(folder)):
        m = _DATED.match(name)
        if m:
            out.append((m.group(1), os.path.join(folder, name)))
    return out


def json_line(text: str) -> dict | None:
    """The scorecard's summary line (`JSON {...}`) inside a review file."""
    for line in text.splitlines():
        if line.startswith("JSON {"):
            return json.loads(line[len("JSON "):])
    return None


def history_rows(run: str, summary: dict) -> list[dict]:
    rows = []
    for book, b in (summary.get("books") or {}).items():
        twin = b.get("twin") or {}
        rows.append({"ds": "history", "run": run, "book": book, "settled": b.get("settled"),
                     "pnl": b.get("pnl"), "cents_per_contract": b.get("cents_per_contract"),
                     "losing": b.get("losing"), "twin_gap_cents": twin.get("gap")})
    return rows


def recommendations(text: str) -> list[dict]:
    """The `| area | call | item | evidence | change if |` table of a review file."""
    rows, in_table = [], False
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") \
            else None
        if cells and [c.lower() for c in cells] == ["area", "call", "item", "evidence",
                                                     "change if"]:
            in_table = True
            continue
        if not in_table:
            continue
        if cells is None:
            break
        if set("".join(cells)) <= set("-: "):
            continue
        if len(cells) == len(_REC_COLS):
            row = dict(zip(_REC_COLS, cells, strict=True))
            row["call"] = row["call"].capitalize()
            row["area"] = row["area"][:1].upper() + row["area"][1:]
            rows.append({"ds": "recommendations", "id": f"r{len(rows) + 1}", **row})
    return rows


def bundle(scorecard: dict, reviews: list[tuple[str, str]]) -> list[dict]:
    rows = [{"ds": t, **r} for t in TABLES for r in scorecard.get(t) or []]
    newest = None
    for run, text in reviews:
        summary = json_line(text)
        if summary:
            rows.extend(history_rows(run, summary))
        newest = text
    if newest is not None:
        rows.extend(recommendations(newest))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the mmsell dashboard data file.")
    ap.add_argument("--scorecard", required=True,
                    help="output of `mmsell_scorecard --json` (a file holding its JSON object)")
    ap.add_argument("--scorecards-dir", default=SCORECARDS)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    with open(args.scorecard, encoding="utf-8") as fh:
        text = fh.read()
    start = text.index("{")
    scorecard = json.loads(text[start:text.rindex("}") + 1])
    reviews = []
    for run, path in review_files(args.scorecards_dir):
        with open(path, encoding="utf-8") as fh:
            reviews.append((run, fh.read()))
    rows = bundle(scorecard, reviews)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    counts = {}
    for r in rows:
        counts[r["ds"]] = counts.get(r["ds"], 0) + 1
    print(json.dumps({"rows": len(rows), "by_ds": counts, "reviews": len(reviews)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
