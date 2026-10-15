"""What "running" means for each experiment: the declared `expect` contract.

An experiment reports to the monitor by DECLARING what normal looks like, not by
pushing heartbeats. Every Experiment OS experiment is included automatically; the
declaration only tunes how quiet it may go before the monitor calls it STALE.

Resolution order, first match wins:
  1. the registered Version's `monitoring_json["expect"]` — canonical, frozen with
     the contract, and what every NEW package should carry;
  2. the package module's `MONITOR_EXPECT` — for packages whose Version was frozen
     before this contract existed (a frozen Version is never rewritten to add it);
  3. `DEFAULT_EXPECT`.

Keys (all optional, anything else is refused by `validate_expect`):
  max_silence_hours   — an armed arm that HAS traded may go this long without new
                        evidence before it is STALE. Default: the Control Tower's
                        SILENT_ARM_HOURS (one settlement day).
  min_entries_per_day — an armed arm averaging fewer entries/day over 7 days is
                        STALE ("below the declared rate"). Default: none — the
                        Tower's own rule is that a slow book is not a broken one.
  note                — free text shown on the page (e.g. a seasonal caveat).

This is monitoring configuration, never a gate: it authorizes nothing, and a STALE
read changes no lifecycle state.
"""

from __future__ import annotations

from ..experiment_os.control_tower import SILENT_ARM_HOURS

DEFAULT_EXPECT: dict = {"max_silence_hours": SILENT_ARM_HOURS}

_ALLOWED_KEYS = frozenset({"max_silence_hours", "min_entries_per_day", "note"})


def validate_expect(expect) -> list[str]:
    """Problems with one `expect` declaration; an empty list means valid."""
    if not isinstance(expect, dict):
        return ["expect must be a dict"]
    problems = [f"unknown key {k!r}" for k in sorted(set(expect) - _ALLOWED_KEYS)]
    hours = expect.get("max_silence_hours")
    if hours is not None and (isinstance(hours, bool) or not isinstance(hours, (int, float))
                              or hours <= 0):
        problems.append("max_silence_hours must be a positive number")
    rate = expect.get("min_entries_per_day")
    if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float))
                             or rate < 0):
        problems.append("min_entries_per_day must be a non-negative number")
    note = expect.get("note")
    if note is not None and not isinstance(note, str):
        problems.append("note must be a string")
    return problems


def package_expectations() -> dict[str, dict]:
    """`MONITOR_EXPECT` by experiment key, from every reviewed package that creates
    an experiment. Repair/recut packages act on an existing experiment and carry
    none. Imported lazily, exactly as the transport does."""
    from ..experiment_os.experiment_commands import _packages

    out: dict[str, dict] = {}
    for pkg in _packages().values():
        module = _package_module(pkg)
        expect = getattr(module, "MONITOR_EXPECT", None)
        if expect is not None:
            out[pkg.experiment_key] = expect
    return out


def creates_experiment(pkg) -> bool:
    """A package that registers an experiment of its own (not a repair or re-cut
    of someone else's). Those are the ones that must declare `MONITOR_EXPECT`."""
    module = _package_module(pkg)
    return pkg.register is not None and (
        getattr(module, "EXPERIMENT_KEY", None) == pkg.experiment_key
        or getattr(module, "SUCCESSOR_KEY", None) == pkg.experiment_key
    )


def _package_module(pkg):
    import sys

    fn = pkg.register or pkg.arm or pkg.repair or pkg.close_out
    return sys.modules.get(getattr(fn, "__module__", ""), None)


def resolve_expect(experiment_key: str, version_monitoring: dict | None,
                   packages: dict[str, dict] | None = None) -> tuple[dict, str]:
    """The effective expectation for one experiment, and where it came from."""
    declared = (version_monitoring or {}).get("expect")
    if isinstance(declared, dict) and not validate_expect(declared):
        return {**DEFAULT_EXPECT, **declared}, "version"
    pkg = (packages or {}).get(experiment_key)
    if isinstance(pkg, dict) and not validate_expect(pkg):
        return {**DEFAULT_EXPECT, **pkg}, "package"
    return dict(DEFAULT_EXPECT), "default"
