#!/usr/bin/env python3
"""Emit the `MMSELL_VARIANTS` book specs for the reviewed-universe paper tapes.

WHY THIS IS A SCRIPT AND NOT A RUNTIME LOOKUP. The tape's universe is the set of series a human
has signed off on in `kalshi_bot/registry/series_manifest.json`. That set GROWS — batch 6, 7, 8.
If the book read it live, every sign-off would silently widen a book already collecting evidence,
and no number collected before the change would be comparable with any number after it. So the
universe is pinned into the book's own `onlyx=` spec, and widening it is a deliberate act: run
this, read the diff, set the env var, record the epoch.

    python scripts/reviewed_tape_spec.py            # both spec lines
    python scripts/reviewed_tape_spec.py --check    # exit 1 if the docs' specs have drifted

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

#: `mmsell10`'s band and ceiling, so each tape differs from it in UNIVERSE (and, for the second,
#: the cap) rather than in how it prices.
BAND = "lo=5,hi=10,maxyes=7"

#: The two tapes, in the order they appear in the doc. `Rmmsell1` is the reviewed universe on its
#: own; `Rmmsell2` is the same universe under the contest cap, on the CORRECTED key — the same
#: mechanism `Gmmsell2` carries. Read against each other they isolate the cap's effect *inside*
#: a universe we understand, which is a different question from `Gmmsell1`-vs-`Gmmsell0`'s
#: "does the cap help over everything".
TAPES: tuple[tuple[str, str], ...] = (
    ("Rmmsell1", BAND),
    ("Rmmsell2", f"{BAND},contestcap=1,contestkey=split"),
)
DOC = pathlib.Path(__file__).resolve().parents[1] / "docs" / "MMSELL_REVIEWED_TAPE.md"


def universe() -> list[str]:
    series = sorted(registry.reviewed_series())
    if not series:
        raise SystemExit("no reviewed series in the manifest — nothing to build a tape from")
    return series


def specs() -> list[str]:
    allow = f"onlyx={'+'.join(universe())}"
    return [f"{tag}:{body},{allow}" for tag, body in TAPES]


def spec() -> str:
    """Both book specs as ONE `MMSELL_VARIANTS` fragment, ';'-joined the way the env var is."""
    return ";".join(specs())


def documented_specs() -> list[str | None]:
    text = DOC.read_text() if DOC.exists() else ""
    out: list[str | None] = []
    for tag, _ in TAPES:
        m = re.search(rf"^\s*({re.escape(tag)}:\S+)\s*$", text, re.M)
        out.append(m.group(1) if m else None)
    return out


def documented_spec() -> str | None:
    """The doc's two lines joined the way `spec()` joins them, or None if either is missing."""
    found = documented_specs()
    return ";".join(found) if all(found) else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="compare against the specs recorded in docs/MMSELL_REVIEWED_TAPE.md")
    args = ap.parse_args()
    if not args.check:
        for line in specs():
            print(line)
        return 0
    want, got = specs(), documented_specs()
    if got == want:
        print(f"ok — {len(universe())} reviewed series, both tapes match the doc")
        return 0
    print("DRIFT: the manifest and the documented tape specs disagree.")
    for w, g in zip(want, got, strict=True):
        mark = "  " if w == g else "!!"
        print(f"{mark} manifest -> {w}")
        print(f"{mark} doc      -> {g}")
    print("\nThis is NOT automatically a bug. A sign-off lands in the manifest first; the tape")
    print("widens only when the operator decides to widen it (new env value + a recorded epoch).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
