"""PERP-V1's frozen numbers, in a module that imports nothing.

WHY THIS FILE EXISTS AT ALL
---------------------------
These constants live in `perp_v1.py` conceptually — that is the registration
package, and it is where they were written. They are pulled out here because
of who else needs them: `scripts/perp_arm_scores.py` (Probe 2) is the provider
of the gate metrics, and it runs on the **ops runner**, which installs
`psycopg` and nothing else for a `script` request. `perp_v1` imports
`.service`, which imports SQLAlchemy, so importing the package to read two
integers is not available there.

The two wrong ways out, and why:

  * **Copy the numbers into the scorer.** Then a floor could be raised in the
    registered package and the scorer would keep reporting against the old bar
    — a number measured against a threshold nobody registered, which is the
    exact failure the whole apparatus exists to prevent. Never.
  * **Install the project's requirements on the ops runner for every script.**
    That changes a shared surface — what every allowlisted script may import
    and how long every request takes — to serve one script's need.

So: one dependency-free module, imported by both. `perp_v1` re-exports these
names, so `from .perp_v1 import SAMPLE_FLOOR` keeps working for callers that
already hold the heavy import anyway.

**Nothing may be added to this file's imports.** A single `from . import x`
here would put SQLAlchemy back on Probe 2's import path and break it in
production while every test stayed green — which is how this file came to
exist, twice over (WS-010, the 2026-09-02 ops-runner failures).
"""

from __future__ import annotations

#: The evidence floor, in scored round trips (arms A/B) or scored event-contract
#: decisions (arm C). Below it the correct verdict is HOLD, not a thin PASS.
SAMPLE_FLOOR = 200

#: The tape-completeness floor. Read every perp number against its coverage: an
#: estimate speaking for a fifth of the intended tape is not the same claim as one
#: speaking for all of it. This is the `fill_model_coverage_pct` lesson applied
#: before the first number exists rather than after a promotion turns out to have
#: rested on one.
COVERAGE_FLOOR_PCT = 80

#: Arm C's registered forward horizons. Read by `perp_v1.ARMS` (the frozen
#: pre-registration) and by Probe 2, which splits them into what the tape's
#: sampling cadence can and cannot see. Two copies of this list would let the
#: scorer refuse a horizon the pre-registration does not contain, or measure one
#: it does not name.
REGISTERED_HORIZONS_SEC: tuple[int, ...] = (5, 10, 30, 60, 300)
