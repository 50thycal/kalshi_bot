"""What a registration package RETURNS has to survive the receipt builder.

WHY THIS FILE EXISTS
--------------------
`marktangle-2` failed three production registrations in a row. The third:

    AttributeError: 'int' object has no attribute 'version'

`_result_of` builds the receipt by reading identifiers off the ORM objects a
package returns — `version.version`, `epoch.epoch_number`. The MARKTANGLE
packages returned the identifiers themselves (`"version": version.version`), so
the receipt builder raised. It runs INSIDE the command's transaction, after the
package has written everything, so the exception did not merely spoil a receipt:
it rolled the whole registration back. Three times, against a database that was
verified empty afterwards each time.

Every unit test passed throughout, because no test called a package's
`register()` and then fed its return value to the transport. The tests exercised
the package and the transport separately, and the contract BETWEEN them — an
undocumented shape agreement — was the thing that was broken.

So this file does not test either side. It reproduces the seam: run the real
envelope through the real executor, and assert the receipt that comes back names
what was produced. Two guards, in the order they would have caught the bug:

  1. the receipt is COMPLETE for every package that registers standalone — a
     package returning identifiers instead of objects shows up as
     `fields_unreadable`, which is now a failed assertion rather than a rolled
     back production write;
  2. an unreadable field can no longer take the write with it — the negative
     control feeds `_result_of` exactly the shape that failed in production and
     asserts it returns a thinner receipt instead of raising.

Precedent: `tests/test_xos_column_limits.py`, for the same class of bug (a green
suite that was more permissive than production). The check has to reproduce the
constraint, not the code path.
"""

from __future__ import annotations

import pytest

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import experiment_commands as xc
from kalshi_bot.experiment_os import read

#: Packages that register from an empty Experiment OS with no preconditions.
#: The successor and repair packages are deliberately absent: they refuse
#: outside a state this fixture does not build, and refusing is their contract.
STANDALONE_PACKAGES = ("marktangle-reversion", "marktangle-2", "perp-v1")


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


def _envelope(package: str, command_id: str) -> dict:
    return {
        "command_id": command_id,
        "action": "REGISTER_PACKAGE",
        "actor": "claude-code",
        "actor_role": "RESEARCH_LAB",
        "payload": {"package": package},
        "schema_version": 1,
    }


@pytest.mark.parametrize("package", STANDALONE_PACKAGES)
def test_a_standalone_package_registers_and_names_what_it_produced(
    package, xos_session, xos_platform
):
    """The end-to-end seam: envelope in, SUCCEEDED receipt out, and the receipt
    identifies the version and epoch that now exist."""
    out = xc.execute_envelope(xos_session, _envelope(package, f"xcmd-shape-{package}"))
    xos_session.commit()

    assert out["status"] == xc.CommandStatus.SUCCEEDED, out.get("error")
    result = out["result"]
    assert result["kind"] == "register" and result["package"] == package
    assert "fields_unreadable" not in result, (
        f"package {package!r} returned a shape the receipt builder could not read: "
        f"{result.get('fields_unreadable')}. Every key in "
        f"{xc.RESULT_OBJECT_KEYS} carries an ORM OBJECT, not an identifier."
    )
    assert isinstance(result["version"], int) and result["version"] >= 1
    assert isinstance(result["epoch_number"], int) and result["epoch_number"] >= 1
    assert result["version_frozen_at"] != "None", "a registered contract is frozen"

    # And the receipt describes objects that really landed.
    key = xc._packages()[package].experiment_key
    experiment = read.get_experiment(xos_session, key)
    assert experiment is not None
    version = read.latest_version(xos_session, experiment)
    assert version.version == result["version"]


def test_the_probe_deployment_and_every_gate_reach_the_receipt(xos_session, xos_platform):
    """MARKTANGLE-2 registers four gates (two per track) and one tagless PROBE
    deployment. All five are pre-registration evidence, so all five are named."""
    out = xc.execute_envelope(xos_session, _envelope("marktangle-2", "xcmd-shape-m2-full"))
    xos_session.commit()
    result = out["result"]
    assert result["probe_deployment"] == "marktangle2-probe-1"
    assert set(result["gates"]) == {
        "paper_to_live_canary_a", "paper_keep_a",
        "paper_to_live_canary_b", "paper_keep_b",
    }
    assert all(h and len(h) == 16 for h in result["gates"].values()), (
        "each gate's spec hash is its pre-registration receipt"
    )


# ===========================================================================
# The negative control: the exact shape that failed in production
# ===========================================================================


class _FakeVersion:
    version = 3
    frozen_at = "2026-09-02T00:00:00Z"


def test_the_old_shape_is_reported_not_raised():
    """`{"version": 1}` is what MARKTANGLE returned and what raised
    `AttributeError` inside the transaction. It must now degrade to a thinner
    receipt: the write is worth more than the metadata about it."""
    result = xc._result_of({"kind": "register", "package": "p",
                            "produced": {"version": 1, "epoch": 2}})
    assert result["kind"] == "register"
    assert result["fields_unreadable"] == [
        "epoch_number", "epoch_started_at", "impact_class",
        "version", "version_frozen_at",
    ]
    assert "version" not in result, "an unreadable field is named, never guessed"


def test_a_readable_field_is_still_reported_beside_an_unreadable_one():
    """Tolerance is per field, not all-or-nothing."""
    result = xc._result_of({"kind": "register", "package": "p",
                            "produced": {"version": _FakeVersion(), "epoch": 7}})
    assert result["version"] == 3
    assert result["fields_unreadable"] == [
        "epoch_number", "epoch_started_at", "impact_class",
    ]


def test_no_produced_object_means_no_result_fields():
    assert xc._result_of("not a dict") is None
    assert xc._result_of({"kind": "register", "package": "p"}) == {
        "kind": "register", "package": "p",
    }
