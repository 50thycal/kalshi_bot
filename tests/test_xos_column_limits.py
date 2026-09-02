"""Every string a registration package writes must fit the column it lands in.

WHY THIS FILE EXISTS
--------------------
`perp-v1`'s first production close-out died on:

    DataError: value too long for type character varying(16)
    INSERT INTO experiment_versions (...)

`execution_style` is `String(16)` with the vocabulary `maker|taker|mixed`, and the
package was passing a hundred-character sentence. Every test had passed, twice over,
because **SQLite does not enforce VARCHAR lengths and Postgres does**. The suite runs
on SQLite; production is Postgres. So no in-process test could ever have caught it,
and adding more of them would not have helped.

This one does not test behaviour. It reads the LIMITS off the ORM models and checks
the values a package actually writes against them — which is the only way to see a
Postgres-only constraint from a SQLite test run.

It is the third time in this session that a green suite hid a production failure by
being more permissive than production (the ops runner's path, then its dependency
set, now the database's type widths). The pattern is the same each time: the check
has to reproduce the constraint, not the code path.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy import inspect as sa_inspect

from kalshi_bot.experiment_os import perp_v1


def _overlong(objects) -> list[str]:
    """Every value that would not fit its column, as readable complaints.

    Takes objects rather than a session so the checker is a pure function and can be
    handed a hand-built row — a checker that cannot be shown to fail is not a check,
    and the frozen-version guard makes it impossible to prove by mutating a real one.
    """
    problems: list[str] = []
    for obj in list(objects):
        mapper = sa_inspect(type(obj))
        for column in mapper.columns:
            limit = getattr(column.type, "length", None)
            if not isinstance(column.type, String) or not limit:
                continue
            value = getattr(obj, column.key, None)
            if isinstance(value, str) and len(value) > limit:
                problems.append(
                    f"{mapper.local_table.name}.{column.key} is String({limit}) but "
                    f"the value is {len(value)} chars: {value[:60]!r}..."
                )
    return problems


def test_registering_perp_v1_writes_nothing_too_long_for_postgres(
    xos_session, xos_platform
):
    """The regression. Postgres refused this; SQLite had accepted it every time."""
    perp_v1.register(xos_session, actor="t")
    xos_session.flush()
    assert _overlong(xos_session.identity_map.values()) == []


def test_closing_out_perp_v1_writes_nothing_too_long_for_postgres(
    xos_session, xos_platform
):
    """The close-out writes gate results and a transition on top of the contract, so
    it is checked separately rather than assumed to be covered by registration."""
    perp_v1.close_out_retrospective(
        xos_session, actor="t", approved_by="cal", reason="closed"
    )
    xos_session.flush()
    assert _overlong(xos_session.identity_map.values()) == []


def test_execution_style_uses_the_columns_vocabulary(xos_session, xos_platform):
    """`execution_style` is String(16) and documented `maker|taker|mixed`. A value
    outside that vocabulary is wrong even when it happens to be short enough — the
    length was the symptom, the free prose was the defect."""
    produced = perp_v1.register(xos_session, actor="t")
    assert produced["version"].execution_style in {"maker", "taker", "mixed"}


def test_the_guard_would_actually_catch_an_overlong_value():
    """A checker that cannot fail is not a check. Built by hand rather than by
    mutating a registered version, because freezing makes that (correctly) illegal —
    which is itself why the defect could only ever surface in production."""
    from kalshi_bot.experiment_os.models import ExperimentVersion

    problems = _overlong([ExperimentVersion(execution_style="x" * 64)])
    assert any("execution_style" in p and "String(16)" in p for p in problems), problems


def test_the_guard_passes_a_value_that_fits():
    from kalshi_bot.experiment_os.models import ExperimentVersion

    assert _overlong([ExperimentVersion(execution_style="taker")]) == []
