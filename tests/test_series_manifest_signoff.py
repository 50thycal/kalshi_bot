"""Recording an operator's rules review in the registry manifest.

WHAT MUST NEVER BREAK:

  * **all-or-nothing.** A batch that half-applies leaves the operator believing they signed a
    list they did not sign. Any problem in the batch must write nothing.
  * **a barred series cannot be signed.** A refusal is not discharged by reading the rulebook.
  * **an existing review is not silently overwritten.** Replacing another reviewer's name is a
    deliberate act.
  * **`--by` names a person.** The whole design exists because a regex is not evidence; a
    script signing in its own name would reinstate exactly the failure being routed around.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import series_manifest_signoff as so  # noqa: E402

from kalshi_bot import registry  # noqa: E402


def _doc():
    return {"manifest_version": 1, "reasons": {}, "series": [
        {"series": "KXA", "state": "graduated", "rules_reviewed_at": None,
         "rules_reviewed_by": None},
        {"series": "KXDONE", "state": "graduated", "rules_reviewed_at": "2026-01-01",
         "rules_reviewed_by": "Someone Else"},
        {"series": "KXNO", "state": "barred", "rules_reviewed_at": None,
         "rules_reviewed_by": None},
    ]}


def test_signs_an_unreviewed_row():
    d = _doc()
    signed, problems = so.sign(d, ["KXA"], "Calvin", "2026-09-07")
    assert signed == ["KXA"] and problems == []
    row = next(r for r in d["series"] if r["series"] == "KXA")
    assert row["rules_reviewed_at"] == "2026-09-07"
    assert row["rules_reviewed_by"] == "Calvin"


def test_a_barred_series_cannot_be_signed():
    d = _doc()
    signed, problems = so.sign(d, ["KXNO"], "Calvin", "2026-09-07")
    assert signed == []
    assert any("barred" in p for p in problems)


def test_an_existing_review_is_not_silently_overwritten():
    d = _doc()
    signed, problems = so.sign(d, ["KXDONE"], "Calvin", "2026-09-07")
    assert signed == []
    assert any("already reviewed" in p and "Someone Else" in p for p in problems)


def test_resign_is_explicit_and_then_allowed():
    d = _doc()
    signed, problems = so.sign(d, ["KXDONE"], "Calvin", "2026-09-07", resign=True)
    assert signed == ["KXDONE"] and problems == []
    assert next(r for r in d["series"] if r["series"] == "KXDONE")["rules_reviewed_by"] == "Calvin"


def test_an_unknown_series_is_a_problem_not_a_new_row():
    d = _doc()
    before = copy.deepcopy(d)
    signed, problems = so.sign(d, ["KXNEVERHEARDOFIT"], "Calvin", "2026-09-07")
    assert signed == []
    assert any("not in the manifest" in p for p in problems)
    assert d == before, "a sign-off must never invent a manifest row"


def test_a_batch_with_one_bad_entry_writes_nothing():
    """The load-bearing one. Signing KXA but refusing KXNO would leave the operator believing
    they had signed both."""
    d = _doc()
    before = copy.deepcopy(d)
    signed, problems = so.sign(d, ["KXA", "KXNO"], "Calvin", "2026-09-07")
    assert signed == []
    assert problems
    assert d == before, "a partial batch is worse than none"


@pytest.mark.parametrize("bad", ["series_rules_audit.py", "the audit", "some_script", "  "])
def test_the_reviewer_must_be_a_person(bad, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(so, "MANIFEST_PATH", tmp_path / "m.json")
    (tmp_path / "m.json").write_text(json.dumps(_doc()))
    assert so.main(["KXA", "--by", bad]) == 2


def test_it_writes_the_real_manifest_shape(tmp_path, monkeypatch, capsys):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(_doc()))
    monkeypatch.setattr(so, "MANIFEST_PATH", p)
    assert so.main(["KXA", "--by", "Calvin", "--at", "2026-09-07"]) == 0
    back = json.loads(p.read_text())
    row = next(r for r in back["series"] if r["series"] == "KXA")
    assert row["rules_reviewed_by"] == "Calvin"


def test_dry_run_changes_nothing_on_disk(tmp_path, monkeypatch):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(_doc()))
    monkeypatch.setattr(so, "MANIFEST_PATH", p)
    before = p.read_text()
    assert so.main(["KXA", "--by", "Calvin", "--dry-run"]) == 0
    assert p.read_text() == before


def test_it_targets_the_same_manifest_the_worker_reads():
    assert so.MANIFEST_PATH == registry.MANIFEST_PATH


def test_signing_makes_the_row_leave_the_backlog(tmp_path, monkeypatch):
    """The point of the exercise: a signed row is no longer owed a review."""
    d = _doc()
    so.sign(d, ["KXA"], "Calvin", "2026-09-07")
    unreviewed = [r["series"] for r in d["series"]
                  if r["state"] == "graduated" and not r.get("rules_reviewed_at")]
    assert "KXA" not in unreviewed
