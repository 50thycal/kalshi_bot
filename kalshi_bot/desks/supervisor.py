"""Persistent autonomous research schedule, job leases, budgets, and health.

Trade execution and reconciliation remain separate. A missing provider is a
visible blocker, never silently treated as successful unattended research.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select

from .contracts import DeskError
from .research import (
    HTTPProvider,
    ProviderFailure,
    PublicFetcher,
    PublicMarketReader,
    charter,
    parse_output,
    shared_archive,
)
from .research_models import ResearchBudget, ResearchJob, ResearchSource


def _utc(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _micros(value):
    value = Decimal(str(value))
    if not value.is_finite() or value < 0:
        raise ValueError("resource budget must be finite and nonnegative")
    return int((value * 1000000).to_integral_value(rounding=ROUND_CEILING))


class Supervisor:
    def __init__(self, store, providers: dict[str, HTTPProvider] | None = None,
                 market_reader=None, source_fetcher=None, submit_decision=None,
                 interval_seconds=3600, monthly_budget_usd="0", external_runners_verified=False):
        self.store = store
        self.providers = providers or {}
        self.external_runners_verified = bool(external_runners_verified)
        self.fetcher = source_fetcher or PublicFetcher()
        self.market_reader = market_reader or PublicMarketReader(self.fetcher, store=store, interval_seconds=interval_seconds)
        self.submit_decision = submit_decision
        self.interval = int(interval_seconds)
        if self.interval < 60:
            raise ValueError("research interval must be at least 60 seconds")
        self.limit = _micros(monthly_budget_usd)
        self.round_id = self.store.snapshot(datetime.now(timezone.utc))["round_id"]
        for table in (ResearchBudget.__table__, ResearchJob.__table__, ResearchSource.__table__):
            table.create(store.engine, checkfirst=True)

    def _round_id(self):
        return self.round_id

    def request_cycle(self, desk_id, now):
        return {"job_id": self.enqueue(desk_id, now, manual=True), "state": "queued"}

    def enqueue(self, desk_id, now, *, manual=False):
        if desk_id not in {"chatgpt", "claude"}:
            raise DeskError("invalid_desk")
        stamp = int(now.timestamp()) // self.interval
        key = f"research-{self._round_id()}-{desk_id}-{stamp}"
        with self.store._tx() as session:
            self.store._book(session, desk_id)  # Serialize enqueue across worker processes.
            active = session.scalar(select(ResearchJob).where(
                ResearchJob.round_id == self._round_id(), ResearchJob.desk_id == desk_id,
                ResearchJob.state.in_(["queued", "claimed", "running", "retry", "publishing"])))
            if active:
                return active.job_id
            if session.get(ResearchJob, key):
                return key
            session.add(ResearchJob(job_id=key, desk_id=desk_id, round_id=self._round_id(),
                        created_at=now, updated_at=now, state="queued", context={},
                        reserved_microusd=0))
        return key

    def _job(self, session, job_id):
        job = session.scalar(select(ResearchJob).where(ResearchJob.job_id == job_id).with_for_update())
        if not job or job.round_id != self._round_id():
            raise DeskError("research_job_not_found")
        return job

    def _owned(self, session, job_id, token, desk_id, now):
        job = self._job(session, job_id)
        if job.desk_id != desk_id or job.claim_token != token or job.state not in {"claimed", "running"}:
            raise DeskError("research_claim_mismatch")
        if job.lease_until and _utc(job.lease_until) <= _utc(now):
            raise DeskError("research_claim_expired")
        return job

    def _save_sources(self, job_id, sources):
        with self.store._tx() as session:
            for source in sources:
                session.add(ResearchSource(source_id=source["source_id"], job_id=job_id,
                            fetched_at=datetime.fromisoformat(source["retrieved_at"]), payload=source))

    @staticmethod
    def _decision_context(snapshot, desk_id):
        """Own unresolved learning comes first, independently of book row order."""
        reviewed = {p.get("payload", {}).get("decision_id") for p in snapshot.get("publications", [])
                    if p.get("desk_id") == desk_id and p.get("kind") == "postmortem"}
        decisions = snapshot.get("decisions", [])
        def timestamp(row):
            return str((row.get("settlement") or {}).get("settled_at")
                       or row.get("updated_at") or row.get("created_at") or "")
        own = [row for row in decisions if row.get("desk_id") == desk_id]
        pending = sorted((row for row in own if row.get("settled") and row["decision_id"] not in reviewed),
                         key=lambda row: (timestamp(row), row["decision_id"]))
        chosen = pending[:10]
        ids = {row["decision_id"] for row in chosen}
        chosen += sorted((row for row in own if row["decision_id"] not in ids),
                         key=lambda row: (timestamp(row), row["decision_id"]), reverse=True)[:4]
        chosen += sorted((row for row in decisions if row.get("desk_id") != desk_id),
                         key=lambda row: (timestamp(row), row["decision_id"]), reverse=True)[:2]
        fields = ("decision_id", "desk_id", "ticker", "event_id", "side", "created_at", "updated_at",
                  "probability", "probability_low", "probability_high", "observed_price", "max_price",
                  "expected_net_profit", "status", "settled", "filled_quantity", "fill_cost", "fees",
                  "pnl", "payout", "settlement_source", "rules_sha256", "edge_class", "author_model")
        result = []
        for row in chosen:
            item = {key: row.get(key) for key in fields}
            item["needs_postmortem"] = row.get("desk_id") == desk_id and bool(row.get("settled")) and row["decision_id"] not in reviewed
            settlement = row.get("settlement") or {}
            item["settlement"] = {key: settlement.get(key) for key in
                                  ("ticker", "yes_payout", "settled_at", "source")}
            for key, cap in (("thesis", 500), ("counterargument", 400), ("invalidation", 250),
                             ("settlement_rule", 500)):
                item[key] = str(row.get(key) or "")[:cap]
            result.append(item)
        return result, len(pending)

    def _context(self, job_id, now):
        with self.store._tx() as session:
            job = self._job(session, job_id)
            desk_id = job.desk_id
            if job.context and "board" in job.context:
                return job.context
        board = self.market_reader(now)
        self._save_sources(job_id, board.get("sources", []))
        snapshot = self.store.snapshot(now)
        decisions, backlog = self._decision_context(snapshot, desk_id)
        publications = sorted(snapshot.get("publications", []),
                              key=lambda p: (str(p.get("created_at", "")), str(p.get("record_id", ""))),
                              reverse=True)
        chosen_publications = [p for p in publications if p.get("desk_id") == desk_id][:6]
        chosen_publications += [p for p in publications if p.get("desk_id") != desk_id][:4]
        context = {"desk_id": desk_id, "round_id": self._round_id(), "now": now.isoformat(),
                   "board": {**board, "sources": [{**source, "excerpt": source["excerpt"][:1000]} for source in board.get("sources", [])]},
                   "archive": shared_archive(), "desks": snapshot.get("desks", []),
                   "recent_decisions": decisions, "own_unreviewed_settlements": backlog,
                   "peer_and_own_publications": chosen_publications}
        with self.store._tx() as session:
            job = self._job(session, job_id)
            job.context = context
        return context

    def claim_external(self, desk_id, worker_id, now):
        job_id = self.enqueue(desk_id, now)
        with self.store._tx() as session:
            job = self._job(session, job_id)
            if job.state not in {"queued", "retry"}:
                return None
            job.state, job.worker_id, job.claim_token = "claimed", worker_id[:200], uuid.uuid4().hex
            job.lease_until, job.updated_at = now + timedelta(minutes=30), now
            token = job.claim_token
        try:
            context = self._context(job_id, now)
        except Exception:
            self._fail(job_id, "market_context_unavailable", now, unknown=False)
            raise DeskError("market_context_unavailable") from None
        return {"job_id": job_id, "claim_token": token, "desk_id": desk_id,
                "context": context, "system": charter(desk_id)}

    def fetch_external_source(self, job_id, claim_token, desk_id, url, now):
        with self.store._tx() as session:
            job = self._owned(session, job_id, claim_token, desk_id, now)
            count = int((job.context or {}).get("source_request_count", 0))
            if count >= 8:
                raise DeskError("source_request_limit")
            job.context = {**job.context, "source_request_count": count + 1}
        source = self.fetcher(url, now)
        source.pop("_market_data", None)
        self._save_sources(job_id, [source])
        return source

    def _verify(self, output, job_id, desk_id, model_id, origin):
        with self.store._tx() as session:
            sources = {s.source_id: s.payload for s in session.scalars(
                select(ResearchSource).where(ResearchSource.job_id == job_id))}
        for item in [*output.candidates, *output.rejected, *output.paper, *output.decisions]:
            if any(source_id not in sources for source_id in item.source_ids):
                raise DeskError("unverified_source_id")
        for item in output.decisions:
            decision = item.decision
            if decision.desk_id != desk_id or decision.round_id != self._round_id():
                raise DeskError("research_decision_owner_mismatch")
            if decision.author_model != model_id or decision.origin != origin:
                raise DeskError("research_decision_author_mismatch")
            self.verify_decision_sources(decision)
            for evidence in decision.evidence:
                matches = [sources[s] for s in item.source_ids if sources[s]["url"] == evidence.url
                           and sources[s]["sha256"] == evidence.sha256
                           and sources[s]["retrieved_at"] == evidence.retrieved_at.isoformat()
                           and evidence.excerpt in sources[s]["excerpt"]]
                if not matches:
                    raise DeskError("unverified_decision_evidence")
        for postmortem in output.postmortems:
            decision = self.store.get_decision(postmortem.decision_id)
            if not decision or decision["desk_id"] != desk_id or not decision.get("settled"):
                raise DeskError("postmortem_requires_own_settlement")
        if output.source_requests:
            raise DeskError("unfinished_source_requests")

    def verify_decision_sources(self, decision):
        with self.store._tx() as session:
            sources = [row.payload for row in session.scalars(select(ResearchSource).join(
                ResearchJob, ResearchSource.job_id == ResearchJob.job_id).where(
                ResearchJob.desk_id == decision.desk_id,
                ResearchJob.round_id == decision.round_id))]
        for evidence in decision.evidence:
            if not any(source["url"] == evidence.url and source["sha256"] == evidence.sha256
                       and _utc(datetime.fromisoformat(source["retrieved_at"])) == _utc(evidence.retrieved_at)
                       and evidence.excerpt in source["excerpt"] for source in sources):
                raise DeskError("unverified_decision_evidence")
        if not any(source["url"] == decision.settlement_source for source in sources):
            raise DeskError("unverified_settlement_source")

    def complete_external(self, job_id, claim_token, payload, model_id, now, *, desk_id):
        with self.store._tx() as session:
            self._owned(session, job_id, claim_token, desk_id, now)
        output = parse_output(payload if isinstance(payload, str) else json.dumps(payload))
        self._verify(output, job_id, desk_id, model_id, "session")
        self._publish(job_id, output, desk_id, model_id, now)
        self._finish(job_id, now, 0)
        return {"job_id": job_id, "state": "completed"}

    def _publish(self, job_id, output, desk_id, model_id, now):
        with self.store._tx() as session:
            job = self._job(session, job_id)
            if job.state not in {"claimed", "running"}:
                raise DeskError("research_completion_already_in_progress")
            job.state = "publishing"
            job.result = {"output": output.model_dump(mode="json"), "model_id": model_id}
            job.updated_at = now
        # Deterministic publication IDs make restart replay safe. Store.publish
        # rejects different payloads for an existing immutable record ID.
        groups = {"candidate": output.candidates, "rejection": output.rejected,
                  "paper_observation": output.paper, "lesson": output.lessons,
                  "postmortem": output.postmortems}
        for kind, items in groups.items():
            for i, item in enumerate(items):
                payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else {"lesson": item}
                payload.update({"job_id": job_id, "author_model": model_id})
                key = hashlib.sha256(f"{job_id}:{kind}:{i}".encode()).hexdigest()
                self.store.publish(desk_id, kind, payload, now, record_id=key)
        self.store.publish(desk_id, "research_cycle", {"job_id": job_id, "author_model": model_id,
                           "summary": output.summary, "next_action": output.next_action}, now,
                           record_id=hashlib.sha256(f"{job_id}:summary".encode()).hexdigest())
        for item in output.decisions:
            outcome_key = hashlib.sha256(f"{job_id}:{item.decision.decision_id}:refused".encode()).hexdigest()
            # A recorded refusal is terminal for this decision. Re-running the
            # callback after a crash could produce a different refusal reason,
            # contradict the immutable record, or consume another attempt.
            snapshot = self.store.snapshot(now)
            if any(p["record_id"] == outcome_key for p in snapshot.get("publications", [])):
                continue
            if self.store.get_decision(item.decision.decision_id) is not None:
                continue  # Existing orders belong to reconciliation, not replay.
            if self.submit_decision is None:
                raise DeskError("decision_submission_unavailable")
            try:
                self.submit_decision(item.decision, now)
            except DeskError as exc:
                # A price/limit refusal is a research outcome, not permission to
                # chase. The executor independently retains uncertain orders.
                self.store.publish(desk_id, "decision_refused",
                                   {"job_id": job_id, "decision_id": item.decision.decision_id,
                                    "reason": exc.code}, now,
                                   record_id=outcome_key)

    def _budget_key(self, job):
        return f"{job.round_id}:{job.desk_id}:{job.budget_month}"

    def _finish(self, job_id, now, actual):
        with self.store._tx() as session:
            job = self._job(session, job_id)
            if job.state == "completed":
                return
            if job.reserved_microusd:
                budget = session.scalar(select(ResearchBudget).where(
                    ResearchBudget.key == self._budget_key(job)).with_for_update())
                budget.committed_microusd += actual - job.reserved_microusd
            job.actual_microusd = actual
            job.state, job.updated_at = "completed", now

    def _fail(self, job_id, code, now, *, unknown, retryable=False, actual=0):
        with self.store._tx() as session:
            job = self._job(session, job_id)
            attempts = int((job.context or {}).get("attempts", 0)) + 1
            job.context = {**(job.context or {}), "attempts": attempts}
            if job.reserved_microusd and not unknown:
                budget = session.scalar(select(ResearchBudget).where(
                    ResearchBudget.key == self._budget_key(job)).with_for_update())
                budget.committed_microusd += actual - job.reserved_microusd
                job.reserved_microusd = 0
                job.actual_microusd = actual
            job.state = "retry" if retryable and attempts < 3 and not unknown else "failed"
            job.error, job.updated_at = code, now
            job.lease_until = now + timedelta(minutes=attempts * 5)

    def _reserve(self, job_id, provider, now):
        if self.limit <= 0:
            return False
        reserve = provider.config.reservation_microusd()
        with self.store._tx() as session:
            job = self._job(session, job_id)
            self.store._book(session, job.desk_id)  # Serialize first monthly budget creation.
            if job.state not in {"queued", "retry"}:
                return False
            if job.state == "retry" and job.lease_until and _utc(job.lease_until) > _utc(now):
                return False
            job.budget_month = now.strftime("%Y-%m")
            key = self._budget_key(job)
            budget = session.scalar(select(ResearchBudget).where(ResearchBudget.key == key).with_for_update())
            if budget is None:
                budget = ResearchBudget(key=key, limit_microusd=self.limit, committed_microusd=0)
                session.add(budget)
                session.flush()
            # Configuration reductions take effect immediately; increases require
            # explicit configuration, and cannot be requested by model outputs.
            budget.limit_microusd = self.limit
            if budget.committed_microusd + reserve > self.limit:
                job.error = "research_budget_exhausted"
                return False
            budget.committed_microusd += reserve
            job.reserved_microusd = reserve
            job.state, job.updated_at = "running", now
            job.lease_until = now + timedelta(minutes=15)
            job.claim_token = uuid.uuid4().hex
        return True

    @staticmethod
    def _fit_context(context, system, max_chars):
        """Bound context without cutting JSON or deleting record identity/status."""
        result = json.loads(json.dumps(context, default=str))
        for source in result.get("requested_sources", []):
            if "excerpt" in source:
                source["excerpt"] = source["excerpt"][:2500]
        for document in result.get("archive", []):
            document["excerpt"] = document.get("excerpt", "")[-2000:]
        result["peer_and_own_publications"] = result.get("peer_and_own_publications", [])[:8]
        for item in result["peer_and_own_publications"]:
            if "payload" in item:
                item["payload_excerpt"] = json.dumps(item.pop("payload"), default=str)[:1000]
            if "excerpt" in item:
                item["excerpt"] = item["excerpt"][:1000]
        if len(system) + len(json.dumps(result)) > max_chars:
            result["peer_and_own_publications"] = result["peer_and_own_publications"][:4]
            for document in result.get("archive", []):
                document["excerpt"] = document.get("excerpt", "")[-800:]
            result["recent_decisions"] = result.get("recent_decisions", [])[:8]
        return result

    def _run(self, job_id, desk_id, provider, now):
        started = time.monotonic()
        def clock():
            return now + timedelta(seconds=max(0, time.monotonic() - started))
        actual = 0
        called = False
        try:
            context = self._context(job_id, now)
            called = True
            system = charter(desk_id)
            response = provider.complete(system, self._fit_context(context, system, provider.config.max_input_chars))
            actual += response.cost_microusd
            output = parse_output(response.text)
            if output.source_requests:
                sources = []
                for url in output.source_requests:
                    try:
                        source = self.fetcher(url, clock())
                        self._save_sources(job_id, [source])
                        source.pop("_market_data", None)
                        sources.append({**source, "excerpt": source["excerpt"][:3000]})
                    except Exception:
                        sources.append({"url": url, "error": "source_unavailable"})
                followup = {**context, "now": clock().isoformat(), "requested_sources": sources,
                            "instruction": "Final response: no further source_requests."}
                response = provider.complete(system, self._fit_context(followup, system, provider.config.max_input_chars))
                actual += response.cost_microusd
                output = parse_output(response.text)
            # Provider response metadata is authoritative; a model cannot know
            # which resolved model/version the service will report after a call.
            for item in output.decisions:
                item.decision = item.decision.model_copy(update={
                    "author_model": response.model_id, "origin": "scheduled"})
            self._verify(output, job_id, desk_id, response.model_id, "scheduled")
            with self.store._tx() as session:
                self._job(session, job_id).actual_microusd = actual
            self._publish(job_id, output, desk_id, response.model_id, clock())
            self._finish(job_id, clock(), actual)
        except ProviderFailure as exc:
            # After a successful first call, even a known-free second failure has
            # nonzero spend; keep the conservative reservation, never repeat it.
            self._fail(job_id, exc.code, clock(), unknown=exc.bill_unknown,
                       retryable=exc.retryable and actual == 0, actual=actual)
        except Exception as exc:
            code = exc.code if isinstance(exc, DeskError) else "research_cycle_failed"
            self._fail(job_id, code, clock(), unknown=called and actual == 0, retryable=not called, actual=actual)

    def tick(self, now):
        # Expired model calls retain their full reservation. They are NOT retried:
        # process failure cannot establish whether the provider charged the call.
        recover = []
        with self.store._tx() as session:
            for job in session.scalars(select(ResearchJob).where(
                    ResearchJob.round_id == self._round_id(),
                    ResearchJob.state.in_(["claimed", "running", "publishing"])).with_for_update()):
                if job.lease_until and _utc(job.lease_until) <= _utc(now):
                    if job.state == "publishing" and job.result:
                        recover.append((job.job_id, job.desk_id, job.result, job.actual_microusd or 0))
                        job.state, job.lease_until = "running", now + timedelta(minutes=15)
                    else:
                        job.state, job.error, job.updated_at = "failed", "research_lease_expired", now
        for job_id, desk_id, result, actual in recover:
            try:
                self._publish(job_id, parse_output(json.dumps(result["output"])), desk_id, result["model_id"], now)
                self._finish(job_id, now, actual)
            except Exception:
                self._fail(job_id, "publication_recovery_failed", now, unknown=False, actual=actual)
        for desk_id in ("chatgpt", "claude"):
            self.enqueue(desk_id, now)
        with self.store._tx() as session:
            rows = list(session.scalars(select(ResearchJob).where(
                ResearchJob.round_id == self._round_id(), ResearchJob.state.in_(["queued", "retry"]))
                .order_by(ResearchJob.created_at, ResearchJob.desk_id)))
            todo = [(job.job_id, job.desk_id) for job in rows]
        for job_id, desk_id in todo:
            provider = self.providers.get(desk_id)
            with self.store._tx() as session:
                unresolved = session.scalar(select(ResearchJob).where(
                    ResearchJob.desk_id == desk_id, ResearchJob.round_id == self._round_id(),
                    ResearchJob.state == "failed", ResearchJob.reserved_microusd > 0,
                    ResearchJob.actual_microusd.is_(None)))
            if unresolved:
                continue
            if provider and self._reserve(job_id, provider, now):
                self._run(job_id, desk_id, provider, now)
                break
        return self.status(now)

    def status(self, now):
        result = {}
        jobs_result = []
        costs = {}
        snapshot = self.store.snapshot(now)
        with self.store._tx() as session:
            for desk_id in ("chatgpt", "claude"):
                jobs = list(session.scalars(select(ResearchJob).where(
                    ResearchJob.round_id == self._round_id(), ResearchJob.desk_id == desk_id)
                    .order_by(ResearchJob.created_at.desc(), ResearchJob.job_id.desc()).limit(100)))
                completed = [job for job in jobs if job.state == "completed"]
                failures = [job for job in jobs[:3] if job.state == "failed"]
                reasons = []
                book = next(row for row in snapshot["desks"] if row["desk_id"] == desk_id)
                if book.get("paused"):
                    reasons.append("trading_paused:" + str(book.get("pause_reason") or "operator"))
                if Decimal(book["available_cash"]) <= 0:
                    reasons.append("capital_exhausted")
                reviewed = {p["payload"].get("decision_id") for p in snapshot.get("publications", [])
                            if p["desk_id"] == desk_id and p["kind"] == "postmortem"}
                backlog = [d for d in snapshot.get("decisions", []) if d["desk_id"] == desk_id
                           and d.get("settled") and d["decision_id"] not in reviewed]
                if len(backlog) >= 3:
                    reasons.append("settlement_learning_backlog")
                fresh_external = any(j.worker_id and j.state == "completed"
                                     and _utc(now) - _utc(j.updated_at) <= timedelta(hours=24) for j in jobs)
                if desk_id not in self.providers and not (self.external_runners_verified and fresh_external):
                    reasons.append("unattended_runner_missing")
                elif desk_id in self.providers and self.limit <= 0:
                    reasons.append("paid_research_budget_not_authorized")
                if any(job.error == "research_budget_exhausted" for job in jobs[:2]):
                    reasons.append("research_budget_exhausted")
                if len(failures) >= 3:
                    reasons.append("repeated_research_failures")
                if any(job.state == "failed" and job.reserved_microusd > 0 and job.actual_microusd is None for job in jobs):
                    reasons.append("provider_bill_requires_reconciliation")
                last = max((_utc(job.updated_at) for job in completed), default=None)
                stale = (last and _utc(now) - last > timedelta(hours=24)) or (
                    not last and jobs and _utc(now) - min(_utc(j.created_at) for j in jobs) > timedelta(hours=24))
                if stale:
                    reasons.append("no_completed_cycle_24h")
                state = "needs_operator" if reasons else ("recovering" if failures else "healthy" if completed else "starting")
                result[desk_id] = {"status": state, "state": state, "reasons": reasons,
                    "unreviewed_settlements": len(backlog),
                    "completed_cycles": len(completed), "last_completed_at": last.isoformat() if last else None,
                    "pending_jobs": sum(j.state in {"queued", "claimed", "running", "retry"} for j in jobs),
                    "recent_failures": len(failures),
                    "monthly_budget_usd": str(Decimal(self.limit) / 1000000),
                    "latest_job_id": jobs[0].job_id if jobs else None}
                jobs_result.extend({"job_id": j.job_id, "desk_id": desk_id, "state": j.state,
                                    "error": j.error, "created_at": _utc(j.created_at).isoformat(),
                                    "updated_at": _utc(j.updated_at).isoformat()} for j in jobs[:10])
                rows = list(session.scalars(select(ResearchBudget).where(
                    ResearchBudget.key.like(f"{self._round_id()}:{desk_id}:%"))))
                all_jobs = list(session.scalars(select(ResearchJob).where(
                    ResearchJob.round_id == self._round_id(), ResearchJob.desk_id == desk_id)))
                costs[desk_id] = {"actual_usd": str(sum(Decimal(j.actual_microusd or 0) for j in all_jobs) / 1000000),
                                 "unresolved_reserved_usd": str(sum(Decimal(j.reserved_microusd) for j in all_jobs if j.actual_microusd is None) / 1000000),
                                 "limit_monthly_usd": str(Decimal(self.limit) / 1000000),
                                 "committed_usd": str(sum(Decimal(b.committed_microusd) for b in rows) / 1000000)}
        return {"health": result, "jobs": jobs_result, "costs": costs}
