#!/usr/bin/env python3
"""Emit the `MMSELL_VARIANTS` book spec for the reviewed-universe paper tape.

WHY THIS IS A SCRIPT AND NOT A RUNTIME LOOKUP. The tape's universe is the set of series a human
has signed off on in `kalshi_bot/registry/series_manifest.json`. That set GROWS — batch 6, 7, 8.
If the book read it live, every sign-off would silently widen a book already collecting evidence,
and no number collected before the change would be comparable with any number after it. So the
universe is pinned into the book's own `onlyx=` spec, and widening it is a deliberate act: run
this, read the diff, set the env var, record the epoch.

    python scripts/reviewed_tape_spec.py            # the spec line
    python scripts/reviewed_tape_spec.py --check    # exit 1 if the docs' spec has drifted

`onlyx=` (exact) rather than `only=` (substring) is load-bearing: `only=KXTRUMPSAY` would also
admit KXTRUMPSAYCOMPANY and KXTRUMPSAYMONTH, neither of which anyone has reviewed.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from kalshi_bot import registry  # noqa: E402

TAG = "Rmmsell1"
BAND = "lo=5,hi=10,maxyes=7"   # `mmsell10`'s band, so the tape differs from it in UNIVERSE only
DOC = pathlib.Path(__file__).resolve().parents[1] / "docs" / "MMSELL_REVIEWED_TAPE.md"


def spec() -> str:
    series = sorted(registry.reviewed_series())
    if not series:
        raise SystemExit("no reviewed series in the manifest — nothing to build a tape from")
    return f"{TAG}:{BAND},onlyx={'+'.join(series)}"


def documented_spec() -> str | None:
    if not DOC.exists():
        return None
    m = re.search(rf"^\s*({re.escape(TAG)}:\S+)\s*$", DOC.read_text(), re.M)
    return m.group(1) if m else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="compare against the spec recorded in docs/MMSELL_REVIEWED_TAPE.md")
    args = ap.parse_args()
    want = spec()
    if not args.check:
        print(want)
        return 0
    got = documented_spec()
    if got == want:
        print(f"ok — {len(registry.reviewed_series())} reviewed series, doc matches")
        return 0
    print("DRIFT: the manifest and the documented tape spec disagree.")
    print(f"  manifest -> {want}")
    print(f"  doc      -> {got}")
    print("\nThis is NOT automatically a bug. A sign-off lands in the manifest first; the tape")
    print("widens only when the operator decides to widen it (new env value + a recorded epoch).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
