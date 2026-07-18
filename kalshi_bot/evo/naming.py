"""Agent naming: founder/wildcard surnames, child first names, display names.

Rules (spec §6): unique surname per founder and per wildcard; children inherit the
surname and get a new first name; active display names are unique; retired agents
keep their names forever; names are display-only — identity is always agent_uuid.

Draws are deterministic under a caller-supplied random.Random (the cohort RNG seed),
so bootstrap and simulation are reproducible.
"""

from __future__ import annotations

import random

from sqlalchemy import select

from .models import EvoAgent

FIRST_NAMES = (
    "Mara", "Ilya", "Tess", "Rowan", "Nadia", "Felix", "Imogen", "Dario", "Sana",
    "Leif", "Priya", "Callum", "Vera", "Otis", "Freya", "Marcus", "Anouk", "Jonas",
    "Talia", "Ezra", "Wren", "Hugo", "Mireille", "Silas", "Noor", "Adrian", "Petra",
    "Kofi", "Linnea", "Ravi", "Celeste", "Bram", "Yara", "Anton", "Sofia", "Eamon",
    "Zoya", "Nikolai", "Aster", "Ines", "Dmitri", "Lucia", "Cormac", "Sabine",
    "Elio", "Greta", "Rufus", "Amara", "Viktor", "Odessa", "Lazlo", "Beatrix",
    "Idris", "Maren", "Cassius", "Thea", "Ansel", "Ronja", "Emrys", "Delia",
)

SURNAMES = (
    "Kepler", "Ashford", "Voss", "Marlowe", "Thorne", "Calder", "Ives", "Renwick",
    "Solano", "Bergstrom", "Okafor", "Duarte", "Halloran", "Vance", "Merrick",
    "Osei", "Lindqvist", "Castellan", "Drummond", "Verhoeven", "Antonov", "Blackwood",
    "Fontaine", "Grimaldi", "Havel", "Ishikawa", "Jansen", "Kovacs", "Laurent",
    "Moreau", "Nakamura", "Oyelaran", "Petrov", "Quintero", "Rosales", "Soderberg",
    "Takahashi", "Ulrich", "Valdez", "Whitfield", "Xylander", "Ybarra", "Zielinski",
    "Almeida", "Brennan", "Cavallo", "Dietrich", "Ekstrom", "Farrow", "Galloway",
)


def surname_prefix(surname: str) -> str:
    """Three-letter family code used in agent codes, e.g. Kepler -> KEP."""
    letters = [c for c in surname.upper() if c.isalpha()]
    return "".join(letters[:3]).ljust(3, "X")


def _used(session, column) -> set[str]:
    return set(session.scalars(select(column)).all())


def draw_surname(session, rng: random.Random) -> str:
    """A surname never used by any agent (active or retired). Falls back to
    deterministic numeric suffixes if the curated pool is exhausted."""
    used = _used(session, EvoAgent.surname)
    pool = [s for s in SURNAMES if s not in used]
    if pool:
        return rng.choice(pool)
    base = rng.choice(SURNAMES)
    n = 2
    while f"{base}{n}" in used:
        n += 1
    return f"{base}{n}"


def draw_first_name(session, rng: random.Random, surname: str) -> str:
    """A first name unique within the family (so display names stay unique)."""
    used = set(
        session.scalars(
            select(EvoAgent.first_name).where(EvoAgent.surname == surname)
        ).all()
    )
    pool = [n for n in FIRST_NAMES if n not in used]
    if pool:
        return rng.choice(pool)
    base = rng.choice(FIRST_NAMES)
    n = 2
    while f"{base}{n}" in used:
        n += 1
    return f"{base}{n}"


def make_agent_code(session, surname: str, generation: int) -> str:
    """e.g. KEP-G3-018 — ordinal is the count of agents ever created + 1."""
    ordinal = (session.scalar(select(EvoAgent.id).order_by(EvoAgent.id.desc())) or 0) + 1
    return f"{surname_prefix(surname)}-G{generation}-{ordinal:03d}"


def display_name(first: str, surname: str, generation: int, code: str) -> str:
    return f"{first} {surname} · Generation {generation} · {code}"
