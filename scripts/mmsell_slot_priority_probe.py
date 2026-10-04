"""SLOT-PRIORITY: read-only capacity census; see the frozen 2026-10-04 contract."""
from mmsell_research_probe_common import run_probe


def main(argv: list[str] | None = None) -> int:
    return run_probe("slots", argv)


if __name__ == "__main__":
    raise SystemExit(main())
