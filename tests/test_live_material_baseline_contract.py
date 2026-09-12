"""Every package that arms a live canary must register a baseline the drift
check can actually read.

THE DEFECT THIS EXISTS FOR (XOS-000036)
---------------------------------------
`enforcement.runtime_config_check` compares a live deployment using
`config_json['material']`. Four packages each wrote a `material_config()` of
their own: `canary_mmsell10` happened to nest under `material`, and the three
that followed returned a flat `book_spec` / `twin_tag` / `risk`. The check found
no `material` key and skipped those deployments in silence — from the instant
they armed. Two of the three OPEN live deployments in production were in that
state on 2026-09-12 (ops `pcr-depcfg-20260912`), including the only book in the
runtime allowlist, while two of those packages' docstrings asserted that editing
their book out of `MMSELL_VARIANTS` would be recorded as drift.

Every unit test passed throughout. Each package was tested against its own
`material_config()`, and the check was tested against a hand-written `material`
block; the CONTRACT BETWEEN them — an undocumented shape agreement — was the
thing that was broken. Same seam, and the same lesson, as
`tests/test_xos_package_result_shape.py`.

So this file tests neither side. It asserts the agreement: what a live-arming
package registers is what the check consumes, for every such package, including
ones written after this was.
"""

from __future__ import annotations

import pytest

from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import experiment_commands as ec


def _live_arming_packages():
    """Every registered package whose `arm` puts a book on real money, paired
    with the module that defines it."""
    import importlib

    out = []
    for name, package in sorted(ec._packages().items()):
        if package.arm is None:
            continue
        out.append((name, importlib.import_module(package.arm.__module__)))
    return out


def test_there_is_at_least_one_live_arming_package():
    """A guard on the guard: if the discovery above ever returns nothing, every
    assertion below passes vacuously and this file stops protecting anything."""
    assert _live_arming_packages()


@pytest.mark.parametrize(
    "name,module", _live_arming_packages(), ids=lambda v: getattr(v, "__name__", v)
)
def test_a_live_arming_package_registers_a_comparable_baseline(name, module):
    config = module.material_config()
    material = enf.live_material_or_none(config)
    assert material is not None, (
        f"{name} registers a config the drift check cannot read: it looks for "
        f"config['material'] naming live tags and found {sorted(config)}. A live "
        "book whose baseline the check skips runs real money outside it "
        "(XOS-000036) — build the block with enforcement.live_material_block."
    )
    # The baseline must describe THIS book, not merely be well formed.
    assert material["live_strategies_contains"] == [module.LIVE_TAG]
    assert material["twin_pairs"][module.LIVE_TAG] == module.TWIN_TAG
    # `book_params` is where the book's economics live, so it is the fact a drift
    # comparison is really about. The twin carries None: it is built from the
    # parent with only the tag replaced.
    assert material["book_params"][module.LIVE_TAG] == module.BOOK_PARAMS
    assert material["book_params"][module.TWIN_TAG] is None


@pytest.mark.parametrize(
    "name,module", _live_arming_packages(), ids=lambda v: getattr(v, "__name__", v)
)
def test_the_registered_book_spec_and_the_baseline_cannot_disagree(name, module):
    """`book_spec` and `material['book_params']` state the same fact twice — the
    repair that backfills a baseline onto an already-armed row derives one from
    the other, so a divergence there would write a baseline describing a book
    nobody is running."""
    config = module.material_config()
    if "book_spec" not in config:
        pytest.skip(f"{name} declares no book_spec")
    assert config["book_spec"] == f"{module.LIVE_TAG}:{module.BOOK_PARAMS}"
    assert config["twin_tag"] == module.TWIN_TAG
