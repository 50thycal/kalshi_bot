"""LIMM close-out record repair: end `liquidity-incentive-mm`'s live deployment row so
the experiment can be retired (owner decision DEC-046, 2026-11-14).

WHAT IS WRONG
-------------
`liquidity-incentive-mm` is recorded as LIVE_CANARY holding an OPEN live deployment
(`limm-smoke-live-1`, tag `Alimm1`). The book has placed nothing since it was stood
down on 2026-11-08 (`LIQUIDITY_INCENTIVE_LIVE_ENABLED=false`, thesis §9.78); its last
live order was created 2026-11-08 01:30:24Z and is cancelled (ops `lo1114-db-1`).
`STAND_DOWN` refuses an experiment holding an open LIVE deployment, by design, and no
lifecycle verb ends one — the same circle `repair_mmsell_standdown_live_rows` broke
for seven mmsell canaries on 2026-11-01. This is that repair, for this one book.

It runs the SAME checks (shared code, `end_stood_down_live_rows`): `Alimm1` must be
out of `LIVE_STRATEGIES` and must have no live order after the measured cutoff. So the
envelope rides the env request that removes `Alimm1` from the allowlist (and its
explicit twin from `LIVE_PAPER_TWINS`), and the retirement (`STAND_DOWN` -> RETIRED)
follows it in the same command array.

It moves no lifecycle state, closes no epoch, touches no gate, and authorises nothing.
The twin row (`limm-smoke-twin-1`, `Alimm1_pt3`) is a paper_twin, not live: RETIRED's
epoch close ends it. The two held positions (~$0.03) settle at Kalshi on their own; no
order is placed or cancelled by this package. The tagless `limm-paper-strategies`
probe, the shadow collector and `limm-s2-live`'s fresh tags are different objects and
are not reachable from TARGETS.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .repair_mmsell_standdown_live_rows import end_stood_down_live_rows

UTC = timezone.utc

#: (experiment, live deployment, live tag, newest live order measured for that tag,
#: rounded UP to the next whole second). Measured 2026-11-14 22:30Z, ops `lo1114-db-1`.
TARGETS: tuple[tuple[str, str, str, datetime], ...] = (
    ("liquidity-incentive-mm", "limm-smoke-live-1", "Alimm1",
     datetime(2026, 11, 8, 1, 30, 25, tzinfo=UTC)),
)

#: Declared for the XOS-000033 transport guard; it still refuses the tag while the
#: running worker constructs it (allowlist prefix or explicit twin pair).
RETIRES_TAGS: tuple[str, ...] = tuple(t for _, _, t, _ in TARGETS)

#: Fresh S2 live-test tags and the paper strategies: never reachable from here.
NEVER_TOUCH_TAGS = frozenset({"As2best1"})
assert not NEVER_TOUCH_TAGS & set(RETIRES_TAGS)


def repair(session, *, actor: str, now: datetime | None = None) -> dict:
    """End `limm-smoke-live-1`. Moves no lifecycle state, closes no epoch, touches no gate."""
    return end_stood_down_live_rows(session, actor=actor, now=now, targets=TARGETS,
                                    label="limm")
