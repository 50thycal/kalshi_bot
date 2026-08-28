"""Experiment OS experiment-lifecycle command receipts.

Creates one table, `experiment_os_experiment_commands`: the durable ledger behind
the worker-side experiment-lifecycle transport
(`kalshi_bot/experiment_os/experiment_commands.py`).

It is the THIRD such ledger, alongside `experiment_os_issue_commands` and
`experiment_os_platform_commands`, and a separate table rather than a shared one
for the same reason those two are separate from each other: a ticket must not be
able to arm a canary, and a platform revision must not be able to freeze a
Version. Three tables and three disjoint vocabularies make that structural rather
than a convention a later change could widen.

The ops channel is read-only against Postgres by design (a SELECT-only role,
enforced server-side) and the sandbox cannot reach Railway, so a lifecycle write
reaches production as a strictly validated envelope carried by an allowlisted
environment variable and executed once at worker boot. This table is what makes
"once" true rather than aspirational: `command_id` is UNIQUE and the executor
claims a command with `INSERT … ON CONFLICT DO NOTHING RETURNING`, so two workers
booting together cannot both execute it, and a worker restarting re-reads the
same variable and does nothing. A committed SUCCEEDED, REJECTED or FAILED receipt
is terminal; retrying requires a new `command_id`.

Deploy-inert and additive. It creates a NEW table only: no existing experiment,
version, epoch, deployment, gate, gate-result, integrity, platform, issue or
command row is read, modified or reinterpreted, and nothing in the trading
runtime touches it. Safe to run against production with the worker live. There is
deliberately no foreign key to `experiments`: a REJECTED receipt may name no
experiment at all, and a receipt is an audit record that must not become
deletable by way of the thing it describes.

Guarded with inspector checks because `Base.metadata.create_all()` (the test
fixture and the worker's safety net) also materialises this table.

Revision ID: c8e1a2b3d4f5
Revises: b1d4f6a80c93
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c8e1a2b3d4f5"
down_revision: str | None = "b1d4f6a80c93"
branch_labels = None
depends_on = None

# Repository conventions (kalshi_bot/models.py): BigInteger PKs that degrade to
# Integer on sqlite, timezone-aware timestamps, JSON payloads.
_BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_TS = sa.DateTime(timezone=True)
# `sa.JSON()`, matching every existing Experiment OS migration.
_JSON = sa.JSON()

_COMMANDS = "experiment_os_experiment_commands"

# One read exists and only one: the metadata-only `experiment-command-list`
# (recent receipts, newest first). `experiment-command-show` looks a receipt up by
# `command_id`, which the UNIQUE constraint already indexes.
_INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_experiment_os_experiment_commands_status", _COMMANDS,
     ["status", "requested_at"]),
]


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if _COMMANDS not in _existing_tables():
        op.create_table(
            _COMMANDS,
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            # The whole basis of exactly-once. UNIQUE is load-bearing: the claim
            # is an ON CONFLICT DO NOTHING against this constraint, not a caught
            # IntegrityError, so a losing racer sees "no row returned" and reads
            # the winner's receipt instead of executing.
            sa.Column("command_id", sa.String(64), nullable=False, unique=True),
            sa.Column("action", sa.String(32), nullable=False),
            # Attribution, NOT authorization: the transport cannot verify who
            # anyone is. The authority is who can set a Railway variable.
            sa.Column("actor", sa.String(64), nullable=False),
            sa.Column("actor_role", sa.String(32), nullable=False),
            # sha256 hex over the canonical envelope: 64 chars exactly.
            sa.Column("payload_hash", sa.String(64), nullable=False),
            # The canonical payload, kept so a replay can be proven identical and
            # so the receipt explains itself later. NOT secret — the same bytes
            # are committed in plaintext to ops/request.json on a public branch,
            # and an envelope carries only a reviewed package name and an
            # approver.
            sa.Column("payload_json", _JSON),
            sa.Column("schema_version", sa.Integer(), nullable=False,
                      server_default="1"),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("requested_at", _TS),
            sa.Column("started_at", _TS, nullable=False),
            sa.Column("completed_at", _TS),
            sa.Column("result_json", _JSON),
            # Bounded and sanitized: an exception class and a truncated message,
            # never a traceback and never the envelope.
            sa.Column("error", sa.Text()),
            sa.Column("created_at", _TS, nullable=False),
        )

    inspector = sa.inspect(op.get_bind())
    for name, table, columns in _INDEXES:
        if name not in {ix["name"] for ix in inspector.get_indexes(table)}:
            op.create_index(name, table, columns)


def downgrade() -> None:
    present = _existing_tables()
    if _COMMANDS not in present:
        return
    inspector = sa.inspect(op.get_bind())
    existing = {ix["name"] for ix in inspector.get_indexes(_COMMANDS)}
    for name, table, _columns in reversed(_INDEXES):
        if name in existing:
            op.drop_index(name, table_name=table)
    op.drop_table(_COMMANDS)
