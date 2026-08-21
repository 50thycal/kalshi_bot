"""theta decision-time regime telemetry + replacement-model shadow

Additive-only. No table or column is altered or dropped — every column below is new and
nullable, so existing rows keep reading exactly as they did and a downgrade is lossless for
anything written before it.

Ordered after `e1a2b3c4d5f6` (the issue workflow) rather than beside it. Both were authored
against `d8e9f0a1b2c3` on separate branches, which git merges without complaint — the files do
not overlap — while leaving alembic with TWO heads and `alembic upgrade head` unable to resolve
them. The two touch different tables, so the order between them carries no meaning; what matters
is that there is one.

WHY. `docs/RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md` §2.5 could not answer the momentum/regime
question: only 11,435 of 63,758 tail quotes had a usable trailing move, and the high-momentum
buckets carried expected counts below 1. Nothing recorded the spot context at the moment of the
decision, and `crypto_spot_candles` is pruned to ~6 days, so it could not be reconstructed after
the fact either.

The `spliced_*` columns shadow `kalshi_bot/theta/tailmodel.py` beside the incumbent model. They
decide nothing — no gate, no entry and no fill reads them — but they let the replacement model's
calibration accrue on live data while it is still being validated.

Everything needed to interpret one of those probabilities travels with it: the shape of the tail
the strike ACTUALLY prices off (`spliced_active_xi`; upper for `greater`, lower for `less`, NULL
for `between`), both sides for reference, the declustered exceedance count backing the active
tail, the fit settings, and BOTH the actual and the requested fit window — those two differ on a
cold start, and recording only the request would describe a window the fit never saw.
`spliced_underpowered` marks a row whose active tail is below the evidence bar; such a row
carries no probability at all, because a floor that looks like an estimate is worse than a null.

Revision ID: b1d5e9f3a7c2
Revises: e1a2b3c4d5f6
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b1d5e9f3a7c2"
down_revision = "e1a2b3c4d5f6"
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
    # Replacement-model shadow. Everything needed to interpret the probability travels with it:
    # the shape of the tail the strike ACTUALLY prices off (upper for `greater`, lower for
    # `less`, NULL for `between`), both sides for reference, the declustered exceedance count
    # backing the active tail, and the fit settings that produced it. Storing only an upper xi
    # describes the wrong distribution beside a `less` strike.
    ("spliced_model_p", sa.Float()),
    ("spliced_active_xi", sa.Float()),
    ("spliced_upper_xi", sa.Float()),
    ("spliced_lower_xi", sa.Float()),
    ("spliced_active_clusters", sa.Integer()),
    ("spliced_blocks", sa.Integer()),
    ("spliced_fit_days", sa.Float()),
    ("spliced_requested_fit_days", sa.Float()),
    ("spliced_tail_q", sa.Float()),
    ("spliced_underpowered", sa.Boolean()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("crypto_ladder_snapshots", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _type in reversed(_COLUMNS):
        op.drop_column("crypto_ladder_snapshots", name)
