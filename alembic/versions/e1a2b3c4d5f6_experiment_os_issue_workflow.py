"""Experiment OS investigation / issue workflow.

Creates the four durable tables behind `docs/EXPERIMENT_OS_ISSUES.md`:

    experiment_issues            one investigation: problem, ownership, remedy, outcome
    experiment_issue_events      append-only workflow history
    experiment_issue_evidence    append-only cited evidence (referenced, not copied)
    experiment_issue_links       append-only supporting/canonical references

Deploy-inert and additive. It creates NEW tables only: no existing experiment,
version, epoch, deployment, gate, gate-result, integrity or platform row is read,
modified or reinterpreted, and nothing in the trading runtime touches these
tables. Safe to run against production with the worker live.

The two hardcoded contract findings in `experiment_os/findings.py` are migrated
by a SEPARATELY INVOKED, idempotent importer rather than a data step here —
`python -m kalshi_bot.experiment_os.cli issue import-findings`
(`experiment_os.issue_import.import_contract_findings`). That is deliberate: the
importer must resolve each finding to a real Experiment and Version by key and
skip cleanly when one is absent, which is ORM work against a schema this
migration is in the middle of creating, and no existing migration in this repo
does ORM work. Running it twice is a no-op, so it can be re-run at any time.

Guarded with inspector checks because `Base.metadata.create_all()` (the test
fixture and the worker's safety net) also materialises these tables.

Revision ID: e1a2b3c4d5f6
Revises: d8e9f0a1b2c3
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "e1a2b3c4d5f6"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None

# Repository conventions (kalshi_bot/models.py): BigInteger PKs that degrade to
# Integer on sqlite, timezone-aware timestamps, JSON payloads.
_BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_TS = sa.DateTime(timezone=True)
# `sa.JSON()`, matching every existing Experiment OS migration, which means these
# land as Postgres `json` rather than the `jsonb` that `models.JSONType` would
# produce under create_all(). That divergence is repo-wide and pre-existing — all
# sixteen earlier Experiment OS tables are `json` in production for the same
# reason — so this follows the neighbours deliberately. Introducing `jsonb` for
# four tables and not the other sixteen would be a worse inconsistency than the
# one it fixed, and nothing here queries inside the payload.
_JSON = sa.JSON()

# Column widths carry headroom over the longest value each column holds.
# Postgres enforces VARCHAR limits and SQLite does not, so a value one character
# too long passes the whole test suite and fails only in production; roles are
# 32 for a longest name of 24 (EXPERIMENT_CONTROL_TOWER). A test walks the ORM
# columns and asserts every enum value still fits.

_ISSUES = "experiment_issues"
_EVENTS = "experiment_issue_events"
_EVIDENCE = "experiment_issue_evidence"
_LINKS = "experiment_issue_links"

# (name, table, columns) — every column the read surfaces actually filter or sort
# on: the Control Tower's open-issue listing (status/owner/classification),
# per-experiment and per-version scope, deterministic recurrence matching by
# fingerprint, and recency.
_INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_experiment_issues_status", _ISSUES, ["status", "opened_at"]),
    ("ix_experiment_issues_owner", _ISSUES, ["current_owner_role", "status"]),
    ("ix_experiment_issues_classification", _ISSUES, ["classification", "status"]),
    ("ix_experiment_issues_experiment", _ISSUES, ["experiment_id", "status"]),
    ("ix_experiment_issues_version", _ISSUES, ["version_id"]),
    ("ix_experiment_issues_deployment", _ISSUES, ["deployment_id"]),
    ("ix_experiment_issues_fingerprint", _ISSUES, ["detector_fingerprint", "status"]),
    ("ix_experiment_issues_updated", _ISSUES, ["updated_at"]),
    ("ix_experiment_issue_events_issue", _EVENTS, ["issue_id", "occurred_at"]),
    ("ix_experiment_issue_events_type", _EVENTS, ["event_type", "occurred_at"]),
    ("ix_experiment_issue_evidence_issue", _EVIDENCE, ["issue_id", "captured_at"]),
    ("ix_experiment_issue_evidence_type", _EVIDENCE, ["evidence_type"]),
    ("ix_experiment_issue_links_issue", _LINKS, ["issue_id", "link_type"]),
]


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    present = _existing_tables()

    if _ISSUES not in present:
        op.create_table(
            _ISSUES,
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("issue_key", sa.String(24), nullable=False, unique=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("problem_statement", sa.Text()),
            sa.Column("classification", sa.String(16), nullable=False,
                      server_default="UNCLASSIFIED"),
            sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
            sa.Column("severity", sa.String(12), nullable=False, server_default="MEDIUM"),
            sa.Column("priority", sa.String(2), nullable=False, server_default="P2"),
            sa.Column("current_owner_role", sa.String(32), nullable=False),
            sa.Column("opened_by_role", sa.String(32), nullable=False),
            sa.Column("opened_by_actor", sa.String(64)),
            sa.Column("opened_at", _TS, nullable=False),
            sa.Column("updated_at", _TS, nullable=False),
            sa.Column("resolved_at", _TS),
            # Canonical scope. Every column nullable — an issue may legitimately
            # be system-wide — but the service validates present values against
            # each other, so a version always belongs to its experiment.
            sa.Column("experiment_id", _BIGINT, sa.ForeignKey("experiments.id")),
            sa.Column("version_id", _BIGINT, sa.ForeignKey("experiment_versions.id")),
            sa.Column("epoch_id", _BIGINT, sa.ForeignKey("experiment_epochs.id")),
            sa.Column("deployment_id", _BIGINT,
                      sa.ForeignKey("experiment_deployments.id")),
            sa.Column("gate_id", _BIGINT, sa.ForeignKey("experiment_gates.id")),
            sa.Column("gate_result_id", _BIGINT,
                      sa.ForeignKey("experiment_gate_results.id")),
            sa.Column("platform_revision_id", _BIGINT,
                      sa.ForeignKey("platform_revisions.id")),
            sa.Column("integrity_event_id", _BIGINT,
                      sa.ForeignKey("experiment_integrity_events.id")),
            sa.Column("detector", sa.String(64)),
            sa.Column("detector_fingerprint", sa.String(64)),
            sa.Column("first_observed_at", _TS),
            sa.Column("last_observed_at", _TS),
            sa.Column("occurrence_count", sa.Integer(), nullable=False,
                      server_default="1"),
            sa.Column("evidence_summary", sa.Text()),
            sa.Column("proposed_fix", sa.Text()),
            sa.Column("disposition", sa.String(32)),
            sa.Column("validation_plan", sa.Text()),
            sa.Column("resolution_summary", sa.Text()),
            # Tri-state on purpose: NULL means "not yet determined", which is not
            # the same as False. Never given a server_default for that reason.
            sa.Column("requires_new_version", sa.Boolean()),
            sa.Column("requires_new_epoch", sa.Boolean()),
            sa.Column("requires_platform_revision", sa.Boolean()),
            sa.Column("requires_pause_or_stand_down", sa.Boolean()),
            sa.Column("duplicate_of_issue_id", _BIGINT,
                      sa.ForeignKey("experiment_issues.id")),
            sa.Column("supersedes_issue_id", _BIGINT,
                      sa.ForeignKey("experiment_issues.id")),
            sa.Column("discovered_from_issue_id", _BIGINT,
                      sa.ForeignKey("experiment_issues.id")),
            sa.Column("details_json", _JSON),
            sa.Column("created_at", _TS, nullable=False),
        )

    if _EVENTS not in present:
        op.create_table(
            _EVENTS,
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("issue_id", _BIGINT, sa.ForeignKey("experiment_issues.id"),
                      nullable=False),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("actor", sa.String(64)),
            sa.Column("actor_role", sa.String(32)),
            sa.Column("occurred_at", _TS, nullable=False),
            sa.Column("from_status", sa.String(20)),
            sa.Column("to_status", sa.String(20)),
            sa.Column("from_owner_role", sa.String(32)),
            sa.Column("to_owner_role", sa.String(32)),
            sa.Column("from_classification", sa.String(16)),
            sa.Column("to_classification", sa.String(16)),
            sa.Column("reason", sa.Text()),
            sa.Column("payload_json", _JSON),
            sa.Column("created_at", _TS, nullable=False),
        )

    if _EVIDENCE not in present:
        op.create_table(
            _EVIDENCE,
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("issue_id", _BIGINT, sa.ForeignKey("experiment_issues.id"),
                      nullable=False),
            sa.Column("evidence_type", sa.String(32), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("source_ref", sa.Text()),
            sa.Column("captured_at", _TS, nullable=False),
            sa.Column("captured_by", sa.String(64)),
            sa.Column("payload_json", _JSON),
            sa.Column("content_hash", sa.String(64)),
            sa.Column("created_at", _TS, nullable=False),
        )

    if _LINKS not in present:
        op.create_table(
            _LINKS,
            sa.Column("id", _BIGINT, primary_key=True, autoincrement=True),
            sa.Column("issue_id", _BIGINT, sa.ForeignKey("experiment_issues.id"),
                      nullable=False),
            sa.Column("link_type", sa.String(32), nullable=False),
            sa.Column("reference", sa.Text(), nullable=False),
            sa.Column("label", sa.String(200)),
            sa.Column("created_by", sa.String(64)),
            sa.Column("created_at", _TS, nullable=False),
        )

    inspector = sa.inspect(op.get_bind())
    for name, table, columns in _INDEXES:
        if name not in {ix["name"] for ix in inspector.get_indexes(table)}:
            op.create_index(name, table, columns)


def downgrade() -> None:
    present = _existing_tables()
    inspector = sa.inspect(op.get_bind())
    for name, table, _columns in reversed(_INDEXES):
        if table in present and name in {
            ix["name"] for ix in inspector.get_indexes(table)
        }:
            op.drop_index(name, table_name=table)
    # Children first: every one carries a foreign key into experiment_issues.
    for table in (_LINKS, _EVIDENCE, _EVENTS, _ISSUES):
        if table in present:
            op.drop_table(table)
