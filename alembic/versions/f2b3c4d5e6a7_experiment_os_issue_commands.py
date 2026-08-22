"""Experiment OS issue-command receipts.

Creates one table, `experiment_os_issue_commands`: the durable ledger behind the
worker-side issue-command transport (`kalshi_bot/experiment_os/issue_commands.py`,
documented in `docs/EXPERIMENT_OS_ISSUES.md`).

The ops channel is read-only against Postgres by design and the sandbox cannot
reach Railway, so an ordinary issue write reaches production as a strictly
validated envelope carried by an allowlisted environment variable and executed
once at worker boot. This table is what makes "once" true rather than aspirational:
`command_id` is UNIQUE and the executor claims a command with
`INSERT … ON CONFLICT DO NOTHING RETURNING`, so two workers booting together
cannot both execute it, and a worker restarting under the ON_FAILURE policy
re-reads the same variable and does nothing. A committed SUCCEEDED, REJECTED or
FAILED receipt is terminal; retrying requires a new `command_id`.

Deploy-inert and additive. It creates a NEW table only: no existing experiment,
version, epoch, deployment, gate, gate-result, integrity, platform or issue row
is read, modified or reinterpreted, and nothing in the trading runtime touches
it. Safe to run against production with the worker live. There is deliberately
no foreign key to `experiment_issues`: `issue_key` is denormalised attribution
that must survive on a receipt whose envelope never resolved to an issue at all.

Guarded with inspector checks because `Base.metadata.create_all()` (the test
fixture and the worker's safety net) also materialises this table.

Revision ID: f2b3c4d5e6a7
Revises: e1a2b3c4d5f6
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "f2b3c4d5e6a7"
down_revision: str | None = "e1a2b3c4d5f6"
branch_labels = None
depends_on = None

# Repository conventions (kalshi_bot/models.py): BigInteger PKs that degrade to
# Integer on sqlite, timezone-aware timestamps, JSON payloads.
_BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_TS = sa.DateTime(timezone=True)
# `sa.JSON()`, matching every existing Experiment OS migration — see the note in
# e1a2b3c4d5f6 for why this repo lands Postgres `json` rather than `jsonb` here.
_JSON = sa.JSON()

_COMMANDS = "experiment_os_issue_commands"

# (name, table, columns). Two reads exist and only two: the metadata-only
# `issue-command-list` (recent receipts, newest first) and the per-issue audit
# trail shown next to an issue's history.
_INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_experiment_os_issue_commands_status", _COMMANDS, ["status", "requested_at"]),
    ("ix_experiment_os_issue_commands_issue", _COMMANDS, ["issue_key"]),
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
            # anyone is. The authority is who can push to the ops branch.
            sa.Column("actor", sa.String(64), nullable=False),
            sa.Column("actor_role", sa.String(32), nullable=False),
            # sha256 hex over the canonical envelope: 64 chars exactly.
            sa.Column("payload_hash", sa.String(64), nullable=False),
            # The canonical payload, kept so a replay can be proven identical and
            # so the receipt explains itself later. NOT secret — the same bytes
            # are committed in plaintext to ops/request.json on a public branch.
            sa.Column("payload_json", _JSON),
            sa.Column("schema_version", sa.Integer(), nullable=False,
                      server_default="1"),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("requested_at", _TS),
            sa.Column("started_at", _TS, nullable=False),
            sa.Column("completed_at", _TS),
            # Denormalised on purpose, and deliberately NOT a foreign key: a
            # REJECTED receipt may name no issue at all, and a receipt is an
            # audit record that must not be deletable by way of an issue.
            sa.Column("issue_key", sa.String(24)),
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
