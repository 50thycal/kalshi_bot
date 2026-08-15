"""Platform Change Impact Engine (PR 5 of the spec).

Evolves platform_impact_actions from PR 1 scaffolding into the canonical impact
record: provenance of "before" (superseded revision + prior pinned snapshot),
the experiment's version at decision time, a proposed→accepted→applied/exempted
status walk with per-step actor/timestamp, the NAMED I1 normalizer reference,
the resulting epoch/version ids an application produced, the safety
`blocks_activation` flag (I4), and a structured details payload.

Deploy-inert: nullable column adds plus two server-defaulted flags; nothing
reads the new columns until the engine is invoked. Guarded with inspector
checks because create_all() also materialises the schema.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None

_BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_TS = sa.DateTime(timezone=True)

_TABLE = "platform_impact_actions"

# (name, column factory, postgres FK target or None)
_ADDS: list[tuple[str, sa.Column, tuple[str, str] | None]] = [
    ("superseded_revision_id", sa.Column("superseded_revision_id", _BIGINT),
     ("platform_revisions", "id")),
    ("prior_snapshot_id", sa.Column("prior_snapshot_id", _BIGINT),
     ("platform_snapshots", "id")),
    ("version_id", sa.Column("version_id", _BIGINT),
     ("experiment_versions", "id")),
    ("status", sa.Column("status", sa.String(16), nullable=False,
                         server_default="proposed"), None),
    ("normalization_ref", sa.Column("normalization_ref", sa.String(200)), None),
    ("accepted_by", sa.Column("accepted_by", sa.String(64)), None),
    ("accepted_at", sa.Column("accepted_at", _TS), None),
    ("applied_by", sa.Column("applied_by", sa.String(64)), None),
    ("applied_at", sa.Column("applied_at", _TS), None),
    ("resulting_epoch_id", sa.Column("resulting_epoch_id", _BIGINT),
     ("experiment_epochs", "id")),
    ("resulting_version_id", sa.Column("resulting_version_id", _BIGINT),
     ("experiment_versions", "id")),
    ("blocks_activation", sa.Column("blocks_activation", sa.Boolean(),
                                    nullable=False, server_default="0"), None),
    ("details_json", sa.Column("details_json", sa.JSON()), None),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns(_TABLE)}
    is_postgres = bind.dialect.name == "postgresql"

    for name, column, fk in _ADDS:
        if name in existing:
            continue
        op.add_column(_TABLE, column)
        if fk is not None and is_postgres:
            op.create_foreign_key(
                f"fk_{_TABLE}_{name}", _TABLE, fk[0], [name], [fk[1]]
            )

    if "ix_platform_impact_actions_status" not in {
        ix["name"] for ix in inspector.get_indexes(_TABLE)
    }:
        op.create_index(
            "ix_platform_impact_actions_status", _TABLE, ["status"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    op.drop_index("ix_platform_impact_actions_status", table_name=_TABLE)
    for name, _column, fk in reversed(_ADDS):
        if fk is not None and is_postgres:
            op.drop_constraint(f"fk_{_TABLE}_{name}", _TABLE, type_="foreignkey")
        op.drop_column(_TABLE, name)
