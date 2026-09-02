"""Split a MARKTANGLE-2 ops result into the research package files.

The ops channel persists stdout and nothing else, so `marktangle2_probe.py`
prints the five documents and the trades CSV as marked sections. This turns a
fetched `ops/results/<id>.txt` back into files under `docs/marktangle2/` (or
any directory), verbatim, and re-derives the trades fingerprint so a package
committed to the repo can be checked against the run that produced it.

Usage:
  python scripts/marktangle2_package.py ops/results/m2-run-1.txt docs/marktangle2
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re

BEGIN = re.compile(r"^### BEGIN (\S+)$")
END = re.compile(r"^### END (\S+)$")
FINGERPRINTS = re.compile(r"^FINGERPRINTS trades=(\w+) universe=(\w+) results=(\w+)$")


def split_sections(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """{section name: body} and the printed fingerprints, from a result file.
    A section without its END marker is discarded, not truncated silently."""
    sections: dict[str, str] = {}
    fps: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = BEGIN.match(line)
        if m:
            current, buf = m.group(1), []
            continue
        m = END.match(line)
        if m and current == m.group(1):
            sections[current] = "\n".join(buf) + "\n"
            current = None
            continue
        m = FINGERPRINTS.match(line)
        if m:
            fps = {"trades": m.group(1), "universe": m.group(2), "results": m.group(3)}
            continue
        if current is not None:
            buf.append(line)
    return sections, fps


def write_package(sections: dict[str, str], out_dir: pathlib.Path) -> list[pathlib.Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in sections.items():
        path = out_dir / name
        path.write_text(body, encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("result", help="ops result file")
    ap.add_argument("out_dir", help="directory to write the package into")
    args = ap.parse_args(argv)
    text = pathlib.Path(args.result).read_text(encoding="utf-8")
    sections, fps = split_sections(text)
    if not sections:
        print("no MARKTANGLE-2 sections found in the result")
        return 1
    for path in write_package(sections, pathlib.Path(args.out_dir)):
        print(f"wrote {path}")
    csv = sections.get("MARKTANGLE_2_TRADES.csv")
    if csv is not None and fps:
        local = hashlib.sha256(csv.encode("utf-8")).hexdigest()
        ok = local == fps["trades"]
        print(f"trades fingerprint {'VERIFIED' if ok else 'MISMATCH'}: printed {fps['trades']} "
              f"recomputed {local}")
        return 0 if ok else 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
