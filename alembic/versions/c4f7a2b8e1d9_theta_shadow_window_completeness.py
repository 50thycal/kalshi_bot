"""theta shadow: window completeness + the horizon the fit was actually built at

Additive-only; every column is new and nullable, so existing rows read exactly as before and a
downgrade loses nothing written before it.

WHY. `spliced_fit_days` records the SPAN of the closes a fit saw — the distance from the oldest
stored minute to the newest. A span cannot express completeness: a window that reaches ninety
days back and is missing a third of its minutes has the same span as a full one. The holes are
not random either. They are collector restarts and deploys, which cluster around exactly the
market conditions a tail model exists to price, so a fit built on a gappy window is not merely
smaller, it is biased in an unknown direction.

`spliced_expected_blocks` is how many non-overlapping block slots the REQUESTED window contains,
so coverage is blocks / expected and can be recomputed from stored rows rather than trusted.
`spliced_max_gap_min` is the longest contiguous hole, because a window that is 95% covered with
one three-day outage is worse than one missing scattered minutes. Below the frozen coverage bar
(`tailmodel.MIN_BLOCK_COVERAGE`) the row carries metadata and NO probability.

`spliced_horizon_min` is the bucketed horizon the fit was actually built at. The runtime used to
fit at whatever integer minutes-to-close a market happened to show while the offline validation
harness fitted on a fixed 10..35 grid, so the probability that was being validated was never the
probability that would trade. Both now snap through `tailmodel.h_bucket`, and this column records
the result so a stored probability stays interpretable.

Revision ID: c4f7a2b8e1d9
Revises: b1d5e9f3a7c2
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c4f7a2b8e1d9"
down_revision = "b1d5e9f3a7c2"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("spliced_expected_blocks", sa.Integer()),
    ("spliced_block_coverage", sa.Float()),
    ("spliced_max_gap_min", sa.Float()),
    ("spliced_horizon_min", sa.Integer()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("crypto_ladder_snapshots", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _type in reversed(_COLUMNS):
        op.drop_column("crypto_ladder_snapshots", name)
