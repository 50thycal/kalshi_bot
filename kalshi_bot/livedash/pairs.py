"""Resolving which live run is paired with which paper run.

The pairing is **not** inferred. `live_paper_twins` is an explicit epoch record
written by the twin harness when a live strategy is armed: one row per pair, with a
unique `twin_tag`, the `live_tag` it shadows, and the `started_at` instant that
scopes BOTH sides of every comparison. That row is the pair identity, and
`twin_tag` is the stable URL key for this dashboard.

Deliberately NOT used for pairing: display names, similar creation times, market
ticker, "whatever is currently open", or ordering. A live strategy with no epoch
row is reported as **unpaired** — visible, and explicitly not compared — because a
guessed pairing produces confident wrong numbers, which is worse than a gap.

Everything here is a SELECT.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import distinct, func, select

from .. import models as m

# A pair whose live side has been quiet for longer than this is treated as dormant
# rather than actively running.
IDLE_AFTER_MINUTES = 90


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Pair:
    """One live/paper paired run."""

    twin_tag: str
    live_tag: str
    started_at: datetime
    ended_at: datetime | None = None
    params: dict | None = None
    notes: str | None = None

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    def window(self, now: datetime | None = None) -> tuple[datetime, datetime]:
        now = now or datetime.now(timezone.utc)
        return self.started_at, (self.ended_at or now)

    def to_dict(self) -> dict:
        return {
            "twin_tag": self.twin_tag,
            "live_tag": self.live_tag,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "open": self.is_open,
            # params_json is a strategy-knob snapshot (bands, caps, price offsets) —
            # no credentials pass through the harness that writes it, but it is
            # filtered anyway before publication (see safe_params).
            "params": safe_params(self.params),
            "notes": (self.notes or "")[:280] or None,
        }


# Keys never published, even if a future params snapshot grew one.
_SECRET_HINTS = ("key", "secret", "token", "password", "credential", "private", "auth", "url")


def safe_params(params: dict | None) -> dict | None:
    """Publish only scalar strategy knobs, and never anything credential-shaped.

    The dashboard is an operational surface; the parameter snapshot is what makes a
    param-drift warning intelligible, so it is shown — but allow-listed by shape
    (scalars only) and screened by key name, not trusted wholesale."""
    if not isinstance(params, dict):
        return None
    out: dict = {}
    for key, value in params.items():
        name = str(key)
        if any(hint in name.lower() for hint in _SECRET_HINTS):
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            out[name] = value
        elif isinstance(value, str) and len(value) <= 120:
            out[name] = value
    return out or None


def _row_to_pair(row: m.LivePaperTwin) -> Pair:
    return Pair(
        twin_tag=row.twin_tag,
        live_tag=row.live_tag,
        started_at=_aware(row.started_at),
        ended_at=_aware(row.ended_at),
        params=row.params_json if isinstance(row.params_json, dict) else None,
        notes=row.notes,
    )


def get_pair(session, twin_tag: str) -> Pair | None:
    """One pair by its stable key. `twin_tag` is unique in the schema."""
    row = session.scalar(
        select(m.LivePaperTwin).where(m.LivePaperTwin.twin_tag == twin_tag)
    )
    return _row_to_pair(row) if row is not None else None


def list_pairs(session, *, live_tag: str | None = None, limit: int = 50) -> list[Pair]:
    """Every paired run, newest first. Never limited to the currently open one."""
    query = select(m.LivePaperTwin)
    if live_tag:
        query = query.where(m.LivePaperTwin.live_tag == live_tag)
    rows = session.scalars(
        query.order_by(m.LivePaperTwin.started_at.desc(), m.LivePaperTwin.id.desc())
        .limit(max(1, min(int(limit), 200)))
    )
    return [_row_to_pair(r) for r in rows]


def default_pair(session) -> Pair | None:
    """The pair to show when none was requested: the newest still-open run, else the
    newest run overall. Never a hardcoded tag or row id."""
    pairs = list_pairs(session, limit=50)
    if not pairs:
        return None
    return next((p for p in pairs if p.is_open), pairs[0])


def unpaired_live_strategies(session) -> list[dict]:
    """Live strategies that placed real orders but have no epoch row.

    Standing policy is that every live book runs a twin, so this list should be
    empty for anything current. Legacy live runs that predate the harness appear
    here — and stay here — rather than being matched to a plausible-looking paper
    book by name."""
    twinned = {
        row for row in session.scalars(select(distinct(m.LivePaperTwin.live_tag)))
    }
    rows = session.execute(
        select(
            m.LiveOrder.strategy,
            func.count(m.LiveOrder.id),
            func.min(m.LiveOrder.created_at),
            func.max(m.LiveOrder.created_at),
        )
        .where(m.LiveOrder.strategy.is_not(None), m.LiveOrder.action == "buy")
        .group_by(m.LiveOrder.strategy)
    )
    out = []
    for strategy, count, first, last in rows:
        if strategy in twinned:
            continue
        out.append({
            "live_tag": strategy,
            "orders": int(count or 0),
            "first_order_at": _aware(first).isoformat() if first else None,
            "last_order_at": _aware(last).isoformat() if last else None,
            "reason": "no live_paper_twins epoch row — not comparable",
        })
    return sorted(out, key=lambda r: r["last_order_at"] or "", reverse=True)


def pair_status(session, pair: Pair, *, now: datetime | None = None) -> dict:
    """Run state for the header: whether each side is still transacting, and how
    fresh its most recent record is."""
    now = now or datetime.now(timezone.utc)
    last_live_order = session.scalar(
        select(func.max(m.LiveOrder.created_at)).where(
            m.LiveOrder.strategy == pair.live_tag,
            m.LiveOrder.created_at >= pair.started_at,
        )
    )
    last_paper_trade = session.scalar(
        select(func.max(m.PaperTrade.created_at)).where(
            m.PaperTrade.strategy == pair.twin_tag,
            m.PaperTrade.created_at >= pair.started_at,
        )
    )

    def _state(last: datetime | None) -> dict:
        aware = _aware(last)
        if aware is None:
            return {"state": "no_activity", "last_at": None, "age_seconds": None}
        age = (now - aware).total_seconds()
        if not pair.is_open:
            state = "ended"
        elif age > IDLE_AFTER_MINUTES * 60:
            state = "idle"
        else:
            state = "running"
        return {"state": state, "last_at": aware.isoformat(), "age_seconds": round(age)}

    return {
        "live": _state(last_live_order),
        "paper": _state(last_paper_trade),
        "pairing": "explicit_epoch",
        "pairing_source": "live_paper_twins",
    }
