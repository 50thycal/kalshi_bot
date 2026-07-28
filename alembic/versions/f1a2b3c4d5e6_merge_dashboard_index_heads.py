"""Merge the two migration heads that split off ``d5e6f7a8b9c0``.

Not a schema change — this revision adds no operations. It exists purely to
rejoin a branched revision graph.

How the branch happened: two PRs were developed in parallel, each generating a
migration against the same then-current head (``d5e6f7a8b9c0``, live_paper_twins):

* ``a1c4e70b92d3`` — evo dashboard read-path indexes (PR #128)
* ``b7f3c2a91d54`` — positions ticker/time index (PR #129)

Neither is an ancestor of the other, so once both merged, ``alembic upgrade head``
had two candidates and refused to pick one:

    FAILED: Multiple head revisions are present for given argument 'head'

That is a hard startup failure, not a warning — the worker crashlooped on boot
and never reached the trading loop. Both branch tips are index-only and additive
and touch disjoint tables, so there is no ordering conflict to resolve and no
data to reconcile: a plain merge point is the correct and complete fix.

Revision ID: f1a2b3c4d5e6
Revises: a1c4e70b92d3, b7f3c2a91d54
"""

from __future__ import annotations

revision = "f1a2b3c4d5e6"
down_revision = ("a1c4e70b92d3", "b7f3c2a91d54")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: a merge point carries no schema operations of its own."""


def downgrade() -> None:
    """No-op: reversing the merge just re-splits the graph into its two branches."""
