"""theta decision-time regime telemetry + replacement-model shadow

Additive-only. No table or column is altered or dropped — every column below is new and
nullable, so existing rows keep reading exactly as they did and a downgrade is lossless for
anything written before it.

WHY. `docs/RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md` §2.5 could not answer the momentum/regime
question: only 11,435 of 63,758 tail quotes had a usable trailing move, and the high-momentum
buckets carried expected counts below 1. Nothing recorded the spot context at the moment of the
decision, and `crypto_spot_candles` is pruned to ~6 days, so it could not be reconstructed after
the fact either.

The `spliced_*` columns shadow `kalshi_bot/theta/tailmodel.py` beside the incumbent model. They
decide nothing — no gate, no entry and no fill reads them — but they let the replacement model's
calibration accrue on live data while it is still being validated. `spliced_n_eff` travels with
the probability because a probability from an underpowered fit is a resolution floor rather than
an estimate, and pooling the two would read the floor as evidence.

Revision ID: b1d5e9f3a7c2
Revises: d8e9f0a1b2c3
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b1d5e9f3a7c2"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None

_COLUMNS = (
    # Decision-time regime context, computed from closes the model already holds.
    ("trailing_vol_15m", sa.Float()),
    ("trailing_vol_60m", sa.Float()),
    ("trailing_vol_240m", sa.Float()),
    # SIGNED, in basis points: a tail sold into a rally is not the same trade as one sold into
    # a selloff, and the diagnosis could only bucket on |move|.
    ("trailing_move_15m", sa.Float()),
    ("trailing_move_60m", sa.Float()),
    # Replacement-model shadow.
    ("spliced_model_p", sa.Float()),
    ("spliced_upper_xi", sa.Float()),
    ("spliced_n_eff", sa.Integer()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("crypto_ladder_snapshots", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _type in reversed(_COLUMNS):
        op.drop_column("crypto_ladder_snapshots", name)
