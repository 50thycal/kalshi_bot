"""Transactional persistence for autonomous desks, independent of all legacy books.

Postgres serializes spending against the desk row with SELECT FOR UPDATE; SQLite
uses BEGIN IMMEDIATE, including across processes. No network IO occurs in a
transaction. Immutable evidence is protected by database triggers as well as API
shape. A submitting order never becomes resubmittable after a process crash.
"""

from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from .contracts import Decision, DeskError, OrderReport, Settlement
from .models import (
    IMMUTABLE_TABLES,
    DeskAudit,
    DeskBase,
    DeskBook,
    DeskDecision,
    DeskExecution,
    DeskFill,
    DeskPublication,
    DeskRound,
    DeskSettlement,
)

D = Decimal
ACTIVE = {"reserved", "submitting", "pending", "unknown"}
DESKS = ("chatgpt", "claude")
RULES = {
    "bankroll": "30.00",
    "max_committed": "10.00",
    "max_pick": "1.00",
    "max_filled_daily": 3,
    "max_attempts_daily": 10,
    "timezone": "America/Chicago",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _day(value: datetime) -> str:
    return _utc(value).astimezone(ZoneInfo("America/Chicago")).date().isoformat()


def _money(cents) -> str:
    return str(D(cents) / 100)


def _json(payload):
    return json.loads(json.dumps(payload, default=str, allow_nan=False))


class DeskStore:
    def __init__(self, database_url: str):
        options = {}
        if database_url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if database_url.endswith(":memory:") or database_url in {
                "sqlite://",
                "sqlite+pysqlite://",
            }:
                options["poolclass"] = StaticPool
        self.engine = create_engine(database_url, **options)
        if self.engine.dialect.name == "sqlite":

            @event.listens_for(self.engine, "connect")
            def _sqlite_foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")

        self._mutex = threading.RLock()

    @contextmanager
    def _tx(self):
        # Local lock is needed for SQLite in-memory StaticPool; database locks
        # provide the actual multi-process guarantee for durable deployments.
        with self._mutex:
            with Session(self.engine, expire_on_commit=False) as session:
                try:
                    if self.engine.dialect.name == "sqlite":
                        session.execute(text("BEGIN IMMEDIATE"))
                    yield session
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise

    def _audit(self, s, key, kind, payload, now):
        s.add(
            DeskAudit(
                audit_id=str(uuid.uuid4()),
                book_key=key,
                kind=kind,
                recorded_at=_utc(now),
                payload=_json(payload),
            )
        )

    def _book(self, s, desk_id, round_id=None, lock=True):
        if desk_id not in DESKS:
            raise DeskError("unknown_desk")
        query = select(DeskBook).where(DeskBook.desk_id == desk_id)
        if round_id is not None:
            query = query.where(DeskBook.round_id == round_id)
        if lock:
            query = query.with_for_update()
        rows = s.scalars(query).all()
        if len(rows) != 1:
            raise DeskError("round_not_initialized")
        return rows[0]

    def _execution(self, s, decision_id):
        row = s.get(DeskExecution, decision_id)
        if row is None:
            raise DeskError("unknown_decision")
        self._book(s, row.book_key.rsplit(":", 1)[1], row.book_key.rsplit(":", 1)[0])
        # A waiter must reload the projection after acquiring its desk's lock.
        s.refresh(row)
        return row

    def _schema(self):
        DeskBase.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                conn.execute(
                    text("""CREATE OR REPLACE FUNCTION desk_reject_evidence_mutation()
                    RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
                    RAISE EXCEPTION 'desk evidence is append-only'; END; $$""")
                )
            for model in IMMUTABLE_TABLES:
                table = model.__tablename__
                if self.engine.dialect.name == "sqlite":
                    for operation in ("UPDATE", "DELETE"):
                        conn.execute(
                            text(
                                f"CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()} "
                                f"BEFORE {operation} ON {table} BEGIN "
                                "SELECT RAISE(ABORT, 'desk evidence is append-only'); END"
                            )
                        )
                elif self.engine.dialect.name == "postgresql":
                    trigger = f"{table}_immutable"
                    conn.execute(text(f"DROP TRIGGER IF EXISTS {trigger} ON {table}"))
                    conn.execute(
                        text(
                            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
                            "FOR EACH ROW EXECUTE FUNCTION desk_reject_evidence_mutation()"
                        )
                    )

    def initialize(self, round_id: str, now: datetime):
        if not round_id or len(round_id) > 100:
            raise DeskError("invalid_round_id")
        self._schema()
        with self._tx() as s:
            rounds = s.scalars(select(DeskRound)).all()
            if rounds:
                if len(rounds) == 1 and rounds[0].round_id == round_id:
                    return self._round_dict(rounds[0])
                raise DeskError("round_already_initialized")
            row = DeskRound(round_id=round_id, created_at=_utc(now), start_at=None, rules=RULES)
            s.add(row)
            s.flush()
            for desk in DESKS:
                s.add(
                    DeskBook(
                        key=f"{round_id}:{desk}",
                        round_id=round_id,
                        desk_id=desk,
                        ready=False,
                        paused=False,
                        initial_cents=3000,
                        cash_cents=3000,
                    )
                )
            return self._round_dict(row)

    @staticmethod
    def _round_dict(row):
        return {
            "round_id": row.round_id,
            "started_at": _utc(row.start_at).isoformat() if row.start_at else None,
            "rules": row.rules,
        }

    def ready(self, desk_id: str, now: datetime):
        with self._tx() as s:
            book = self._book(s, desk_id)
            book.ready = True
            self._audit(s, book.key, "ready", {}, now)

    def start_round(self, round_id: str, now: datetime):
        with self._tx() as s:
            row = s.scalar(
                select(DeskRound).where(DeskRound.round_id == round_id).with_for_update()
            )
            if row is None:
                raise DeskError("round_not_initialized")
            books = [self._book(s, desk, round_id) for desk in DESKS]
            if not all(book.ready for book in books):
                raise DeskError("both_desks_must_be_ready")
            if row.start_at is None:
                row.start_at = _utc(now)
                for book in books:
                    self._audit(s, book.key, "round_started", {"start_at": row.start_at}, now)
            return self._round_dict(row)

    def pause(self, desk_id: str, reason: str):
        with self._tx() as s:
            book = self._book(s, desk_id)
            book.paused, book.pause_reason = True, reason
            self._audit(s, book.key, "paused", {"reason": reason}, datetime.now(timezone.utc))

    def resume(self, desk_id: str):
        with self._tx() as s:
            book = self._book(s, desk_id)
            unknown = s.scalar(
                select(DeskExecution).where(
                    DeskExecution.book_key == book.key,
                    DeskExecution.state.in_(["unknown", "submitting"]),
                )
            )
            if unknown:
                raise DeskError("unreconciled_order")
            book.paused, book.pause_reason = False, None
            self._audit(s, book.key, "resumed", {}, datetime.now(timezone.utc))

    def _orders(self, s, key):
        return list(s.scalars(select(DeskExecution).where(DeskExecution.book_key == key)))

    @staticmethod
    def _committed(rows):
        return sum(
            (
                D(row.reserved_cents)
                if row.state in ACTIVE
                else D(row.cost_cents) + D(row.fee_cents)
                if not row.settled
                else D(0)
                for row in rows
            ),
            D(0),
        )

    @staticmethod
    def _pending_cash(rows):
        return sum(
            (
                max(D(0), D(row.reserved_cents) - D(row.cost_cents) - D(row.fee_cents))
                for row in rows
                if row.state in ACTIVE
            ),
            D(0),
        )

    def reserve(self, decision: Decision, quantity: int, reserved_cost: Decimal, now: datetime):
        payload = decision.model_dump(mode="json")
        cost = D(reserved_cost) * 100
        if not cost.is_finite() or cost <= 0 or cost > 100 or cost > decision.max_spend * 100:
            raise DeskError("pick_budget_exceeded")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            raise DeskError("invalid_quantity")
        if quantity * decision.max_price * 100 > cost:
            raise DeskError("insufficient_reservation")
        with self._tx() as s:
            book = self._book(s, decision.desk_id, decision.round_id)
            existing = s.get(DeskDecision, decision.decision_id)
            if existing:
                if existing.payload != payload:
                    raise DeskError("decision_id_conflict")
                return self._order_dict(s, s.get(DeskExecution, decision.decision_id))
            round_row = s.get(DeskRound, decision.round_id)
            if not round_row.start_at or _utc(now) < _utc(round_row.start_at):
                raise DeskError("round_not_started")
            if book.paused:
                raise DeskError("desk_paused", book.pause_reason or "desk_paused")
            if _utc(now) >= decision.expires_at or decision.created_at > _utc(now):
                raise DeskError("expired_or_future_decision")
            if decision.created_at < _utc(round_row.start_at):
                raise DeskError("decision_predates_round")
            rows = self._orders(s, book.key)
            day = _day(now)
            today = [row for row in rows if row.trading_day == day]
            # Old pending reservations must continue occupying slots after midnight.
            slots = sum(row.first_fill_day == day or row.state in ACTIVE for row in rows)
            if slots >= 3:
                raise DeskError("daily_pick_limit")
            if len(today) >= 10:
                raise DeskError("daily_attempt_limit")
            if any(
                row.event_ticker == decision.event_id
                and (row.state in ACTIVE or row.filled_quantity > 0)
                for row in rows
            ):
                raise DeskError("event_already_picked")
            if self._committed(rows) + cost > 1000:
                raise DeskError("outstanding_risk_limit")
            if D(book.cash_cents) - self._pending_cash(rows) < cost:
                raise DeskError("insufficient_cash")
            row = DeskExecution(
                decision_id=decision.decision_id,
                book_key=book.key,
                event_ticker=decision.event_id,
                trading_day=day,
                state="reserved",
                client_order_id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"kalshi-desk:{decision.round_id}:{decision.desk_id}:{decision.decision_id}",
                    )
                ),
                reserved_cents=cost,
                quantity=quantity,
                limit_price_cents=decision.max_price * 100,
                filled_quantity=0,
                cost_cents=0,
                fee_cents=0,
                updated_at=_utc(now),
                settled=False,
            )
            s.add(
                DeskDecision(
                    decision_id=decision.decision_id,
                    book_key=book.key,
                    recorded_at=_utc(now),
                    payload=payload,
                )
            )
            s.flush()
            s.add(row)
            s.flush()
            self._audit(
                s,
                book.key,
                "reserved",
                {"decision_id": decision.decision_id, "cost": reserved_cost},
                now,
            )
            return self._order_dict(s, row)

    def claim_submission(self, decision_id: str, now: datetime) -> bool:
        with self._tx() as s:
            row = self._execution(s, decision_id)
            book = s.get(DeskBook, row.book_key)
            if row.state != "reserved" or book.paused:
                return False
            decision = s.get(DeskDecision, decision_id).payload
            if _utc(now) >= datetime.fromisoformat(decision["expires_at"]):
                row.state, row.updated_at = "terminal", _utc(now)
                self._audit(
                    s, book.key, "expired_before_submission", {"decision_id": decision_id}, now
                )
                return False
            current_day = _day(now)
            if row.trading_day != current_day:
                attempts = sum(
                    other.trading_day == current_day for other in self._orders(s, book.key)
                )
                if attempts >= 10:
                    row.state, row.updated_at = "terminal", _utc(now)
                    self._audit(
                        s, book.key, "day_rollover_attempt_limit", {"decision_id": decision_id}, now
                    )
                    return False
                row.trading_day = current_day
            row.state, row.submitted_at, row.updated_at = "submitting", _utc(now), _utc(now)
            row.claim_token = str(uuid.uuid4())
            self._audit(s, book.key, "submission_claimed", {"decision_id": decision_id}, now)
            return True

    def release_unsubmitted(self, decision_id: str, now: datetime) -> bool:
        """Release an abandoned reservation only while submission is provably unclaimed.

        The same desk-row lock protects this transition and claim_submission.
        A concurrent claimant either wins and keeps the reservation, or observes
        terminal state and cannot submit. Claimed/unknown orders never use this path.
        """
        with self._tx() as s:
            row = self._execution(s, decision_id)
            if row.state != "reserved" or row.claim_token or row.submitted_at is not None:
                return False
            decision = s.get(DeskDecision, decision_id).payload
            expired = _utc(now) >= datetime.fromisoformat(decision["expires_at"])
            stale = (_utc(now) - _utc(row.updated_at)).total_seconds() > 120
            if not expired and not stale:
                return False
            row.state, row.updated_at = "terminal", _utc(now)
            self._audit(
                s,
                row.book_key,
                "unsubmitted_reservation_released",
                {
                    "decision_id": decision_id,
                    "reason": "expired" if expired else "abandoned_before_claim",
                },
                now,
            )
            return True

    def record_order(self, decision_id: str, report: OrderReport, now: datetime):
        error = None
        with self._tx() as s:
            row = self._execution(s, decision_id)
            book = s.get(DeskBook, row.book_key)
            cumulative = (report.filled_quantity, report.fill_cost * 100, report.fees * 100)
            old = (D(row.filled_quantity), D(row.cost_cents), D(row.fee_cents))
            if report.client_order_id != row.client_order_id:
                error = "client_order_id_mismatch"
            elif (
                row.exchange_order_id
                and report.order_id
                and row.exchange_order_id != report.order_id
            ):
                error = "exchange_order_id_mismatch"
            elif any(new < previous for new, previous in zip(cumulative, old, strict=True)):
                error = "non_monotonic_order_report"
            elif cumulative[0] > row.quantity or cumulative[1] + cumulative[2] > row.reserved_cents:
                error = "order_budget_breach"
            elif cumulative[1] > cumulative[0] * row.limit_price_cents:
                error = "price_cap_breach"
            elif (cumulative[0] == 0 and cumulative[1] > 0) or (
                cumulative[0] > 0 and cumulative[1] <= 0
            ):
                error = "inconsistent_fill_cost"
            elif row.state == "terminal" and (report.status != "terminal" or cumulative != old):
                error = "terminal_order_changed"
            elif row.settled and cumulative != old:
                error = "settled_order_changed"
            if error:
                book.paused, book.pause_reason = True, error
                row.state = "unknown"
            else:
                book.cash_cents = D(book.cash_cents) - (
                    cumulative[1] + cumulative[2] - old[1] - old[2]
                )
                if cumulative != old:
                    s.add(
                        DeskFill(
                            fill_id=str(uuid.uuid4()),
                            decision_id=decision_id,
                            recorded_at=_utc(now),
                            payload={
                                "report": report.model_dump(mode="json"),
                                "quantity_delta": str(cumulative[0] - old[0]),
                                "cost_delta": str((cumulative[1] - old[1]) / 100),
                                "fee_delta": str((cumulative[2] - old[2]) / 100),
                            },
                        )
                    )
                if cumulative[0] > 0 and not row.first_fill_day:
                    row.first_fill_day = _day(report.observed_at)
                row.filled_quantity, row.cost_cents, row.fee_cents = cumulative
                row.exchange_order_id = report.order_id or row.exchange_order_id
                row.state = report.status
                if report.status == "unknown":
                    book.paused, book.pause_reason = True, "unknown_order_status"
            row.updated_at = _utc(now)
            self._audit(
                s,
                book.key,
                "order_report",
                {
                    "decision_id": decision_id,
                    "report": report.model_dump(mode="json"),
                    "error": error,
                },
                now,
            )
            result = self._order_dict(s, row)
        if error:
            raise DeskError(error)
        return result

    def settlement(self, decision_id: str, settlement: Settlement):
        with self._tx() as s:
            row = self._execution(s, decision_id)
            decision = s.get(DeskDecision, decision_id).payload
            if settlement.ticker != decision["ticker"]:
                raise DeskError("settlement_ticker_mismatch")
            prior = s.get(DeskSettlement, decision_id)
            payload = settlement.model_dump(mode="json")
            if prior:
                if prior.payload != payload:
                    raise DeskError("settlement_conflict")
                return self._order_dict(s, row)
            if row.state != "terminal":
                raise DeskError("order_not_terminal")
            outcome = (
                settlement.yes_payout if decision["side"] == "yes" else 1 - settlement.yes_payout
            )
            payout = D(row.filled_quantity) * outcome * 100
            book = s.get(DeskBook, row.book_key)
            book.cash_cents = D(book.cash_cents) + payout
            row.settled = True
            s.add(
                DeskSettlement(
                    decision_id=decision_id,
                    recorded_at=_utc(settlement.settled_at),
                    result=str(settlement.yes_payout),
                    payout_cents=payout,
                    payload=payload,
                )
            )
            self._audit(
                s,
                book.key,
                "settled",
                {"decision_id": decision_id, "payout": payout / 100},
                settlement.settled_at,
            )
            s.flush()
            return self._order_dict(s, row)

    def publish(self, desk_id: str, kind: str, payload: dict, now: datetime, record_id=None) -> str:
        if not kind or len(kind) > 48:
            raise DeskError("invalid_publication_kind")
        if not isinstance(payload, dict):
            raise DeskError("invalid_publication_payload")
        clean = _json(payload)
        record_id = record_id or str(uuid.uuid4())
        with self._tx() as s:
            book = self._book(s, desk_id)
            prior = s.get(DeskPublication, record_id)
            if prior:
                if prior.book_key != book.key or prior.kind != kind or prior.payload != clean:
                    raise DeskError("publication_id_conflict")
                return record_id
            s.add(
                DeskPublication(
                    publication_id=record_id,
                    book_key=book.key,
                    kind=kind,
                    recorded_at=_utc(now),
                    payload=clean,
                )
            )
            return record_id

    def _order_dict(self, s, row):
        if row is None:
            raise DeskError("missing_execution")
        evidence = s.get(DeskDecision, row.decision_id)
        settled = s.get(DeskSettlement, row.decision_id)
        result = dict(evidence.payload)
        result.update(
            {
                "payload": evidence.payload,
                "client_order_id": row.client_order_id,
                "order_id": row.exchange_order_id,
                "quantity": row.quantity,
                "limit_price": _money(row.limit_price_cents),
                "reserved_cost": _money(row.reserved_cents),
                "status": row.state,
                "trading_day": row.trading_day,
                "first_fill_day": row.first_fill_day,
                "filled_quantity": str(row.filled_quantity),
                "fill_cost": _money(row.cost_cents),
                "fees": _money(row.fee_cents),
                "settled": row.settled,
                "updated_at": _utc(row.updated_at).isoformat(),
                "payout": _money(settled.payout_cents) if settled else None,
                "pnl": _money(D(settled.payout_cents) - D(row.cost_cents) - D(row.fee_cents))
                if settled
                else None,
                "settlement": settled.payload if settled else None,
                "yes_payout": settled.payload["yes_payout"] if settled else None,
            }
        )
        if settled and D(settled.result) in (D(0), D(1)):
            yes_payout = D(settled.result)
            outcome = yes_payout if result["side"] == "yes" else 1 - yes_payout
            result["brier_score"] = str((D(result["probability"]) - outcome) ** 2)
        return result

    def get_decision(self, decision_id: str):
        with self._tx() as s:
            row = s.get(DeskExecution, decision_id)
            return self._order_dict(s, row) if row else None

    def pending_orders(self):
        with self._tx() as s:
            return [
                self._order_dict(s, row)
                for row in s.scalars(select(DeskExecution).where(DeskExecution.state.in_(ACTIVE)))
            ]

    def snapshot(self, now: datetime):
        with self._tx() as s:
            round_row = s.scalar(select(DeskRound))
            if round_row is None:
                raise DeskError("round_not_initialized")
            result = self._round_dict(round_row)
            result["desks"], result["decisions"], result["publications"] = [], [], []
            for book in s.scalars(select(DeskBook).order_by(DeskBook.desk_id)):
                rows = self._orders(s, book.key)
                decisions = [self._order_dict(s, row) for row in rows]
                today = [row for row in rows if row.trading_day == _day(now)]
                result["desks"].append(
                    {
                        "desk_id": book.desk_id,
                        "ready": book.ready,
                        "cash": _money(book.cash_cents),
                        "initial_bankroll": _money(book.initial_cents),
                        "available_cash": _money(D(book.cash_cents) - self._pending_cash(rows)),
                        "committed": _money(self._committed(rows)),
                        "pnl": str(
                            sum((D(d["pnl"]) for d in decisions if d["pnl"] is not None), D(0))
                        ),
                        "status": "paused"
                        if book.paused
                        else "running"
                        if round_row.start_at
                        else "waiting",
                        "paused": book.paused,
                        "pause_reason": book.pause_reason,
                        "filled_today": sum(row.first_fill_day == _day(now) for row in rows),
                        "attempts_today": len(today),
                        "open_positions": sum(
                            row.filled_quantity > 0 and not row.settled for row in rows
                        ),
                    }
                )
                result["decisions"].extend(decisions)
            for row in s.scalars(select(DeskPublication).order_by(DeskPublication.recorded_at)):
                result["publications"].append(
                    {
                        "record_id": row.publication_id,
                        "desk_id": row.book_key.rsplit(":", 1)[1],
                        "kind": row.kind,
                        "created_at": _utc(row.recorded_at).isoformat(),
                        "payload": row.payload,
                    }
                )
            return result
