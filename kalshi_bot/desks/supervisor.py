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
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .contracts import DeskError, utcnow
from .research import (
    HTTPProvider,
    ProviderFailure,
    PublicFetcher,
    PublicMarketReader,
    charter,
    parse_output,
    shared_archive,
)
from .research_models import DeskControl, ResearchBudget, ResearchJob, ResearchSource


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
                 interval_seconds=3600, monthly_budget_usd="0", external_runners_verified=False,
                 research_mode="scheduled", research_timezone="UTC", research_schedules=None,
                 max_sources_per_job=50, lease_minutes=60, wake_windows=None,
                 max_cycles_per_day=0, min_cycle_gap_minutes=0, wake_grace_minutes=90):
        self.store = store
        # WS-025: unattended wakes and the per-desk cycle budget (0 = unlimited / no gap).
        self.wake_windows = {desk: list(windows) for desk, windows in (wake_windows or {}).items()}
        self.max_cycles = int(max_cycles_per_day)
        self.min_gap = timedelta(minutes=int(min_cycle_gap_minutes))
        self.wake_grace = timedelta(minutes=int(wake_grace_minutes))
        # DEC-024: capture and lease limits protect the service, not research volume.
        if not 1 <= int(max_sources_per_job) <= 500 or not 15 <= int(lease_minutes) <= 240:
            raise ValueError("research source/lease limits out of range")
        self.max_sources = int(max_sources_per_job)
        self.lease = timedelta(minutes=int(lease_minutes))
        if research_mode not in {"scheduled", "session"}:
            raise ValueError("invalid research mode")
        if research_mode == "session" and providers:
            raise ValueError("session mode cannot invoke providers")
        self.research_mode = research_mode
        self.providers = providers or {}
        self.external_runners_verified = bool(external_runners_verified)
        self.fetcher = source_fetcher or PublicFetcher()
        self.market_reader = market_reader or PublicMarketReader(self.fetcher, store=store, interval_seconds=interval_seconds)
        self.submit_decision = submit_decision
        self.interval = int(interval_seconds)
        if self.interval < 60:
            raise ValueError("research interval must be at least 60 seconds")
        self.research_timezone = ZoneInfo(research_timezone)
        self.research_schedules = research_schedules or {}
        self.limit = _micros(monthly_budget_usd)
        self.round_id = self.store.snapshot(datetime.now(timezone.utc))["round_id"]
        for table in (ResearchBudget.__table__, ResearchJob.__table__, ResearchSource.__table__,
                      DeskControl.__table__):
            table.create(store.engine, checkfirst=True)

    def _round_id(self):
        return self.round_id

    def request_cycle(self, desk_id, now):
        return {"job_id": self.enqueue(desk_id, now, manual=True), "state": "queued"}

    def enqueue(self, desk_id, now, *, manual=False, schedule_key=None):
        if desk_id not in {"chatgpt", "claude"}:
            raise DeskError("invalid_desk")
        stamp = int(now.timestamp()) // self.interval
        key = f"research-{self._round_id()}-{desk_id}-{schedule_key or stamp}"
        with self.store._tx() as session:
            self.store._book(session, desk_id)  # Serialize enqueue across worker processes.
            active = session.scalar(select(ResearchJob).where(
                ResearchJob.round_id == self._round_id(), ResearchJob.desk_id == desk_id,
                ResearchJob.state.in_(["queued", "claimed", "running", "retry", "publishing"])))
            if active:
                return active.job_id
            if self.research_mode == "session":
                key = f"session-{self._round_id()}-{desk_id}-{uuid.uuid4().hex}"
            elif session.get(ResearchJob, key):
                return key
            session.add(ResearchJob(job_id=key, desk_id=desk_id, round_id=self._round_id(),
                        created_at=now, updated_at=now, state="queued", context={"research_mode": self.research_mode},
                        reserved_microusd=0))
        return key

    def _scheduled_window(self, desk_id, now):
        """Return the current window identity, or None when this tick is not due.

        Exact local-minute matching deliberately avoids catch-up after downtime. The
        local date and UTC offset distinguish repeated wall-clock times at a DST fold.
        """
        schedule = self.research_schedules.get(desk_id, "interval")
        if schedule == "disabled":
            return None
        if schedule == "interval":
            return str(int(now.timestamp()) // self.interval)
        local = _utc(now).astimezone(self.research_timezone)
        if local.strftime("%H:%M") not in schedule.split(","):
            return None
        return local.strftime("%Y%m%d-%H%M-%z")

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
        # Earlier rounds live in the same database (DEC-024): hand each desk its own closing
        # handoff, lessons, cycles and postmortems from them, plus a few of the peer's.
        carried = [{"round_id": p.get("round_id"), "desk_id": p.get("desk_id"), "kind": p.get("kind"),
                    "created_at": p.get("created_at"),
                    "payload_excerpt": json.dumps(p.get("payload"), default=str)[:1500]}
                   for p in publications if p.get("round_id") not in (None, self._round_id())
                   and p.get("kind") in ("handoff", "lesson", "research_cycle", "postmortem")]
        prior_round_record = {"own": [p for p in carried if p["desk_id"] == desk_id][:40],
                              "peer": [p for p in carried if p["desk_id"] != desk_id][:10]}
        context = {"desk_id": desk_id, "round_id": self._round_id(), "now": now.isoformat(),
                   "board": {**board, "sources": [{**source, "excerpt": source["excerpt"][:1000]} for source in board.get("sources", [])]},
                   "research_mode": self.research_mode,
                   "research_tools": {
                       "board_scope": "20-market convenience sample; browse the full board with the market tools",
                       "market_browse": ["markets", "categories", "events", "event", "series", "market",
                                         "orderbook", "trades"],
                       "browse_is_evidence": False,
                       "source_capture": "any public HTTPS URL via the job's source operation",
                       "max_source_captures": self.max_sources,
                       "lease_minutes": int(self.lease.total_seconds() // 60)},
                   "archive": shared_archive(), "desks": snapshot.get("desks", []),
                   "recent_decisions": decisions, "own_unreviewed_settlements": backlog,
                   "peer_and_own_publications": chosen_publications,
                   "prior_round_record": prior_round_record}
        with self.store._tx() as session:
            job = self._job(session, job_id)
            job.context = context
        return context

    @staticmethod
    def _claimed_at(job):
        value = (job.context or {}).get("claimed_at")
        return _utc(datetime.fromisoformat(value)) if value else _utc(job.created_at)

    def _day_start(self, now):
        local = _utc(now).astimezone(self.research_timezone)
        return _utc(datetime(local.year, local.month, local.day, tzinfo=self.research_timezone))

    def controls(self, desk_id):
        """Dashboard overrides for one desk; None means "use the configured default"."""
        with self.store._tx() as session:
            row = session.get(DeskControl, desk_id)
            return {"max_cycles_per_day": row.max_cycles_per_day if row else None,
                    "shadow": row.shadow if row else None,
                    "updated_at": _utc(row.updated_at).isoformat() if row else None}

    def set_control(self, desk_id, now, **values):
        if desk_id not in {"chatgpt", "claude"}:
            raise DeskError("invalid_desk")
        if "max_cycles_per_day" in values:
            budget = values["max_cycles_per_day"]
            if isinstance(budget, bool) or not isinstance(budget, int) or not 0 <= budget <= 24:
                raise DeskError("invalid_research_budget")
        if "shadow" in values and not isinstance(values["shadow"], bool):
            raise DeskError("invalid_desk_mode")
        with self.store._tx() as session:
            row = session.get(DeskControl, desk_id)
            if row is None:
                row = DeskControl(desk_id=desk_id, updated_at=_utc(now))
                session.add(row)
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = _utc(now)

    def cycle_cap(self, desk_id):
        """Claims allowed per local day: the dashboard budget, else configuration (0 = none)."""
        override = self.controls(desk_id)["max_cycles_per_day"]
        if override is not None:
            return override
        return self.max_cycles or None

    def _cycle_block(self, claims, now, desk_id):
        """The hard per-desk cycle budget: (reason, until) or None. Applies to every host."""
        now = _utc(now)
        start = self._day_start(now)
        cap = self.cycle_cap(desk_id)
        if cap is not None and sum(claimed >= start for claimed in claims) >= cap:
            local = start.astimezone(self.research_timezone) + timedelta(days=1, hours=2)
            return "cycle_budget_exhausted", self._day_start(local)
        last = max(claims, default=None)
        if self.min_gap and last is not None and now < last + self.min_gap:
            return "cycle_gap_not_elapsed", last + self.min_gap
        return None

    def _claims(self, session, desk_id):
        return [self._claimed_at(job) for job in session.scalars(select(ResearchJob).where(
            ResearchJob.round_id == self._round_id(), ResearchJob.desk_id == desk_id,
            ResearchJob.worker_id.is_not(None)))]

    def _windows(self, desk_id, now):
        """Previous and next base-window instants around now, in the research time zone."""
        windows = self.wake_windows.get(desk_id) or []
        local = _utc(now).astimezone(self.research_timezone)
        instants = []
        for offset in (-1, 0, 1):
            day = (local + timedelta(days=offset)).date()
            for window in windows:
                hour, minute = (int(part) for part in window.split(":"))
                instants.append(_utc(datetime(day.year, day.month, day.day, hour, minute,
                                              tzinfo=self.research_timezone)))
        now = _utc(now)
        return (max((t for t in instants if t <= now), default=None),
                min((t for t in instants if t > now), default=None))

    def wake(self, desk_id, now):
        """Whether an unattended host should start a cycle for this desk now.

        Advisory for hosts; the claim-time cycle budget is the enforced limit. Due when a
        base window passed, the desk's requested check-in arrived, one of its decisions
        settled, or an operator Continue is queued, each since the desk's last claim.
        """
        if desk_id not in {"chatgpt", "claude"}:
            raise DeskError("invalid_desk")
        now = _utc(now)
        if self.research_mode != "session":
            return {"desk_id": desk_id, "due": False, "reason": "scheduled_mode",
                    "due_since": None, "next_wake_at": None}
        settled = [_utc(datetime.fromisoformat(str(row["settlement"]["settled_at"])))
                   for row in self.store.snapshot(now).get("decisions", [])
                   if row.get("desk_id") == desk_id and row.get("settled")
                   and (row.get("settlement") or {}).get("settled_at")]
        with self.store._tx() as session:
            jobs = list(session.scalars(select(ResearchJob).where(
                ResearchJob.round_id == self._round_id(), ResearchJob.desk_id == desk_id)))
            claims = [self._claimed_at(job) for job in jobs if job.worker_id]
            active = any(job.state in {"claimed", "running", "publishing", "retry"} for job in jobs)
            queued = [_utc(job.created_at) for job in jobs if job.state == "queued"]
            requested = [_utc(datetime.fromisoformat(job.context["next_check_at"])) for job in jobs
                         if job.state == "completed" and (job.context or {}).get("next_check_at")]
        last = max(claims, default=None)
        today = sum(claimed >= self._day_start(now) for claimed in claims)
        previous, upcoming = self._windows(desk_id, now)
        result = {"desk_id": desk_id, "due": False, "reason": "idle", "due_since": None,
                  "next_wake_at": None, "last_claim_at": last.isoformat() if last else None,
                  "cycles_today": today, "max_cycles_per_day": self.cycle_cap(desk_id)}
        if active:
            result["reason"] = "job_active"
            return result
        request = max(requested, default=None)
        reasons = []
        if queued:
            reasons.append(("continue_requested", min(queued)))
        if previous and (last is None or last < previous):
            reasons.append(("base_window", previous))
        if request and request <= now and (last is None or last < request):
            reasons.append(("agent_check_in", request))
        own = [t for t in settled if last is None or t > last]
        if own:
            reasons.append(("own_settlement", min(own)))
        block = self._cycle_block(claims, now, desk_id)
        if reasons and block:
            result.update(reason=block[0], next_wake_at=block[1].isoformat())
            return result
        if reasons:
            reason, since = min(reasons, key=lambda item: item[1])
            result.update(due=True, reason=reason, due_since=since.isoformat())
            return result
        future = [t for t in (upcoming, request if request and request > now else None) if t]
        if future:
            result["next_wake_at"] = min(future).isoformat()
        return result

    def claim_external(self, desk_id, worker_id, now):
        with self.store._tx() as session:
            claims = self._claims(session, desk_id)
        block = self._cycle_block(claims, now, desk_id)
        if block:
            raise DeskError(block[0])
        job_id = self.enqueue(desk_id, now)
        with self.store._tx() as session:
            job = self._job(session, job_id)
            if job.state not in {"queued", "retry"}:
                return None
            job.context = {**(job.context or {}), "research_mode": self.research_mode,
                           "claimed_at": _utc(now).isoformat()}
            job.state, job.worker_id, job.claim_token = "claimed", worker_id[:200], uuid.uuid4().hex
            job.lease_until, job.updated_at = now + self.lease, now
            token = job.claim_token
        try:
            context = self._context(job_id, now)
        except Exception as exc:
            # Keep only known safe categories, never raw URLs/response bodies or
            # arbitrary exception text. The claim remains terminal on failure.
            causes = {"source_rate_limited": "market_context_rate_limited",
                      "source_timeout": "market_context_timeout",
                      "source_connection_failure": "market_context_connection_failure",
                      "source_http_failure": "market_context_http_failure",
                      "market_scan_in_progress": "market_scan_in_progress",
                      "market_scan_lease_lost": "market_scan_lease_lost"}
            code = causes.get(exc.code, "market_context_unavailable") if isinstance(exc, DeskError) else "market_context_unavailable"
            self._fail(job_id, code, now, unknown=False)
            raise DeskError(code) from None
        return {"job_id": job_id, "claim_token": token, "desk_id": desk_id,
                "lease_until": (now + self.lease).isoformat(),
                "context": context, "system": charter(desk_id)}

    def fetch_external_source(self, job_id, claim_token, desk_id, url, now):
        with self.store._tx() as session:
            job = self._owned(session, job_id, claim_token, desk_id, now)
            count = int((job.context or {}).get("source_request_count", 0))
            if count >= self.max_sources:
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
        # Only sources whose URL is cited can match; filtering in SQL keeps this check
        # cheap now that captures store full pages (DEC-024). Matching is unchanged.
        urls = sorted({evidence.url for evidence in decision.evidence} | {decision.settlement_source})
        with self.store._tx() as session:
            sources = [row.payload for row in session.scalars(select(ResearchSource).join(
                ResearchJob, ResearchSource.job_id == ResearchJob.job_id).where(
                ResearchJob.desk_id == decision.desk_id,
                ResearchJob.round_id == decision.round_id,
                ResearchSource.payload["url"].as_string().in_(urls)))]
        for evidence in decision.evidence:
            if not any(source["url"] == evidence.url and source["sha256"] == evidence.sha256
                       and _utc(datetime.fromisoformat(source["retrieved_at"])) == _utc(evidence.retrieved_at)
                       and evidence.excerpt in source["excerpt"] for source in sources):
                raise DeskError("unverified_decision_evidence")
        if not any(source["url"] == decision.settlement_source for source in sources):
            raise DeskError("unverified_settlement_source")

    def verify_session_decision(self, decision, now):
        """New session orders must be part of an accepted, still-active completion.

        Direct /decisions calls cannot reuse an old session's evidence to trade.
        Publication recovery keeps the original deadline, never renewing authority.
        """
        expected = decision.model_dump(mode="json")
        with self.store._tx() as session:
            jobs = session.scalars(select(ResearchJob).where(
                ResearchJob.round_id == decision.round_id,
                ResearchJob.desk_id == decision.desk_id,
                ResearchJob.state == "publishing"))
            for job in jobs:
                deadline = (job.context or {}).get("session_submit_until")
                if ((job.context or {}).get("research_mode") != "session" or not deadline
                        or _utc(now) >= _utc(datetime.fromisoformat(deadline))):
                    continue
                if any(item.get("decision") == expected for item in
                       (job.result or {}).get("output", {}).get("decisions", [])):
                    return
        raise DeskError("active_session_completion_required")

    def complete_external(self, job_id, claim_token, payload, model_id, now, *, desk_id):
        if not isinstance(model_id, str) or not 1 <= len(model_id) <= 200:
            raise DeskError("research_model_id_required")
        with self.store._tx() as session:
            job = self._job(session, job_id)
            if job.desk_id != desk_id or job.claim_token != claim_token:
                raise DeskError("research_claim_mismatch")
            # The HTTP acknowledgement can be lost after publication/order intent.
            # Recognize only the exact previously accepted result under its original
            # claim. Never publish or submit again while handling an acknowledgement.
            output = parse_output(payload if isinstance(payload, str) else json.dumps(payload))
            if job.result and job.state in {"publishing", "running", "completed"}:
                expected = {"output": output.model_dump(mode="json"), "model_id": model_id}
                if job.result != expected:
                    raise DeskError("research_completion_mismatch")
                state = "completed" if job.state == "completed" else "publishing"
                return {"job_id": job_id, "state": state}
            self._owned(session, job_id, claim_token, desk_id, now)
            if self.research_mode == "session" and output.source_requests:
                raise DeskError("research_sources_not_captured")
        self._verify(output, job_id, desk_id, model_id, "session")
        self._publish(job_id, output, desk_id, model_id, now)
        if output.next_check_at is not None:
            with self.store._tx() as session:
                job = self._job(session, job_id)
                job.context = {**job.context,
                               "next_check_at": self._clamp_check(output.next_check_at, now).isoformat()}
        self._finish(job_id, now, 0)
        return {"job_id": job_id, "state": "completed"}

    def _clamp_check(self, requested, now):
        """A requested check-in lands between the cycle gap (at least 10 min) and 24 h out."""
        now = _utc(now)
        earliest = now + max(self.min_gap, timedelta(minutes=10))
        return min(max(_utc(requested), earliest), now + timedelta(hours=24))

    def _publish(self, job_id, output, desk_id, model_id, now):
        with self.store._tx() as session:
            job = self._job(session, job_id)
            if job.state not in {"claimed", "running"}:
                raise DeskError("research_completion_already_in_progress")
            job.state = "publishing"
            job.result = {"output": output.model_dump(mode="json"), "model_id": model_id}
            if self.research_mode == "session" and "session_submit_until" not in job.context:
                job.context = {**job.context, "session_submit_until": _utc(job.lease_until).isoformat()}
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
        cycle = {"job_id": job_id, "author_model": model_id,
                 "summary": output.summary, "next_action": output.next_action}
        if output.next_check_at is not None:
            cycle["requested_next_check_at"] = _utc(output.next_check_at).isoformat()
        self.store.publish(desk_id, "research_cycle", cycle, now,
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
                # Publication and source verification may outlast the request clock.
                self.submit_decision(item.decision, utcnow())
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
        prior = result.get("prior_round_record") or {}
        result["prior_round_record"] = {"own": prior.get("own", [])[:10], "peer": prior.get("peer", [])[:3]}
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
        if self.research_mode == "session":
            return self.status(now)  # Reconcile accepted results; never schedule cognition.
        for desk_id in ("chatgpt", "claude"):
            window = self._scheduled_window(desk_id, now)
            if window is not None:
                self.enqueue(desk_id, now, schedule_key=window)
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
        # Unattended wakes are computed before the status transaction (no nested locks).
        wakes = {desk: self.wake(desk, now) for desk in ("chatgpt", "claude")
                 if self.research_mode == "session" and self.wake_windows.get(desk)}
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
                if self.research_mode == "session":
                    if not any((j.context or {}).get("research_mode") == "session" for j in completed):
                        reasons.append("session_cycle_required")
                elif desk_id not in self.providers and not (self.external_runners_verified and fresh_external):
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
                unattended = desk_id in wakes
                if stale and (self.research_mode != "session" or unattended):
                    reasons.append("no_completed_cycle_24h")
                wake = wakes.get(desk_id)
                if (wake and wake["due"] and wake["due_since"]
                        and _utc(now) - _utc(datetime.fromisoformat(wake["due_since"])) > self.wake_grace):
                    reasons.append("agent_wake_missed")
                state = "needs_operator" if reasons else ("recovering" if failures else "healthy" if completed else "starting")
                activity = "researching" if any(j.state in {"claimed", "running", "publishing"} for j in jobs) else "waiting_for_continue"
                result[desk_id] = {"status": state, "state": state, "reasons": reasons,
                    "research_mode": self.research_mode,
                    "activity": activity if self.research_mode == "session" else "scheduled",
                    "unreviewed_settlements": len(backlog),
                    "completed_cycles": len(completed), "last_completed_at": last.isoformat() if last else None,
                    "pending_jobs": sum(j.state in {"queued", "claimed", "running", "retry"} for j in jobs),
                    "recent_failures": len(failures),
                    "monthly_budget_usd": str(Decimal(self.limit) / 1000000),
                    "latest_job_id": jobs[0].job_id if jobs else None,
                    "wake": wakes.get(desk_id)}
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
