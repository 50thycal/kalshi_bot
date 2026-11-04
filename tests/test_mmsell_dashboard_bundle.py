"""The mmsell dashboard bundle (`scripts/mmsell_dashboard_bundle.py`)."""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mmsell_dashboard_bundle as mb  # noqa: E402

REVIEW = """# mmsell scorecard — 2026-11-04

```
MMSELL scorecard  as of 2026-11-04T04:13+00:00
JSON {"as_of": "2026-11-04T04:13+00:00", "books": {"Xmmsell10": {"settled": 19, "pnl": 3.74, \
"cents_per_contract": 7.06, "losing": 0, "twin": {"pairs": 19, "gap": 0.39}}}}
```

## Recommendations (not gate verdicts; the owner decides)

| area | call | item | evidence | change if |
|---|---|---|---|---|
| edge / universe | keep | The four kept categories | +7.0¢/ct | two losses |
| profit | try | Prepare the size-up | 53 / 45 of 150 | a loss rate above 1 in 15 |

Trailing prose.
"""


def test_history_and_recommendations_come_from_the_review_file():
    rows = mb.bundle({"by_book": [{"book": "Xmmsell10", "pnl": 3.74}]},
                     [("2026-11-04", REVIEW)])
    by = {}
    for r in rows:
        by.setdefault(r["ds"], []).append(r)
    assert by["by_book"] == [{"ds": "by_book", "book": "Xmmsell10", "pnl": 3.74}]
    (h,) = by["history"]
    assert h == {"ds": "history", "run": "2026-11-04", "book": "Xmmsell10", "settled": 19,
                 "pnl": 3.74, "cents_per_contract": 7.06, "losing": 0, "twin_gap_cents": 0.39}
    r1, r2 = by["recommendations"]
    assert r1["id"] == "r1" and r1["area"] == "Edge / universe" and r1["call"] == "Keep"
    assert r2["change_if"] == "a loss rate above 1 in 15"


def test_history_spans_every_review_and_recommendations_only_the_newest():
    older = REVIEW.replace("2026-11-04", "2026-11-01").replace("Prepare the size-up", "Hold")
    rows = mb.bundle({}, [("2026-11-01", older), ("2026-11-04", REVIEW)])
    assert [r["run"] for r in rows if r["ds"] == "history"] == ["2026-11-01", "2026-11-04"]
    assert "Hold" not in json.dumps([r for r in rows if r["ds"] == "recommendations"])


def test_the_real_review_files_parse():
    reviews = [(run, pathlib.Path(p).read_text()) for run, p in mb.review_files()]
    assert reviews, "docs/mmsell/scorecards has no dated review"
    rows = mb.bundle({}, reviews)
    runs = {r["run"] for r in rows if r["ds"] == "history"}
    assert len(runs) == len(reviews)
    assert any(r["ds"] == "recommendations" for r in rows)
