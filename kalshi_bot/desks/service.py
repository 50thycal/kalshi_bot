"""Application boundary: identities, shared start, reconciliation and desk status."""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from .config import DeskSettings
from .contracts import Decision, DeskError, utcnow
from .scoreboard import comparison

logger = logging.getLogger(__name__)
DESKS = ("chatgpt", "claude")


class DeskService:
    def __init__(self, settings: DeskSettings, store, supervisor, executors=None, notifier=None):
        self.settings, self.store, self.supervisor = settings, store, supervisor
        self.executors = executors or {}
        self.notifier = notifier
        self._isolation = {}
        self.last_tick = None
        self.last_error = None

    def check_launch(self, now=None, *, refresh=False):
        now = now or utcnow()
        blockers = self.settings.static_blockers()
        snapshot = self.store.snapshot(now)
        for desk in DESKS:
            row = next(r for r in snapshot["desks"] if r["desk_id"] == desk)
            if not row["ready"]:
                blockers.append(f"{desk}_session_not_ready")
            executor = self.executors.get(desk)
            if executor is None:
                blockers.append(f"{desk}_exchange_unavailable")
                continue
            checked = self._isolation.get(desk)
            if refresh:
                try:
                    report = executor.exchange.check_isolation()
                    minimum = Decimal(row.get("available_cash", "30")) if snapshot.get("started_at") else Decimal("30")
                    if not report.get("verified") or Decimal(report["balance"]) < minimum:
                        raise DeskError("funding_or_isolation_not_ready")
                    if not snapshot.get("started_at"):
                        executor.exchange.check_clean_book()
                    self._isolation[desk] = {"at": now, "verified": True}
                    executor.isolation_verified = True
                    checked = self._isolation[desk]
                except Exception:
                    executor.isolation_verified = False
                    self._isolation.pop(desk, None)
                    blockers.append(f"{desk}_funding_or_isolation_not_verified")
            if not checked or now - checked["at"] > timedelta(minutes=5):
                blockers.append(f"{desk}_fresh_isolation_check_required")
        if not self.settings.alert_webhook_url.get_secret_value():
            blockers.append("operator_alert_channel_not_configured")
        elif self.notifier is None or not self.notifier.verified():
            blockers.append("operator_alert_delivery_not_verified")
        research = self.supervisor.status(now)
        for desk in DESKS:
            health = research.get("health", {}).get(desk, {})
            if health.get("status") != "healthy":
                blockers.append(f"{desk}_research_not_ready")
        return {"ready": not blockers, "blockers": list(dict.fromkeys(blockers))}

    def start(self, now=None):
        now = now or utcnow()
        readiness = self.check_launch(now, refresh=True)
        if not readiness["ready"]:
            raise DeskError("launch_not_ready", ", ".join(readiness["blockers"]))
        self.last_tick = now
        return self.store.start_round(self.settings.round_id, now)

    def submit(self, decision: Decision, now=None):
        now = now or utcnow()
        executor = self.executors.get(decision.desk_id)
        if executor is None:
            raise DeskError("exchange_unavailable")
        other = "claude" if decision.desk_id == "chatgpt" else "chatgpt"
        if any(not blocker.startswith(other + "_") for blocker in self.settings.static_blockers()):
            raise DeskError("runtime_configuration_not_ready")
        if self.notifier is None or not self.notifier.verified():
            raise DeskError("operator_alert_delivery_not_verified")
        checked = self._isolation.get(decision.desk_id)
        if not checked or not 0 <= (now - checked["at"]).total_seconds() <= 300:
            raise DeskError("fresh_isolation_check_required")
        if self.last_tick is None or not 0 <= (now - self.last_tick).total_seconds() <= 180:
            raise DeskError("execution_monitor_stale")
        self.supervisor.verify_decision_sources(decision)
        return executor.submit(decision, now=now)

    def status(self, now=None):
        now = now or utcnow()
        result = self.store.snapshot(now)
        research = self.supervisor.status(now)
        result["research"] = research
        result["health"] = research.get("health", research.get("desks", {}))
        result["readiness"] = self.check_launch(now)
        result["comparison"] = comparison(result)
        for desk in result["desks"]:
            costs = research.get("costs", {}).get(desk["desk_id"], {})
            desk["research_cost"] = costs.get("actual_usd", "0")
            desk["all_in_pnl"] = result["comparison"][desk["desk_id"]]["all_in_pnl"]
            desk["research_cost_unresolved"] = costs.get("unresolved_reserved_usd")
            desk["last_cycle_at"] = result["health"].get(desk["desk_id"], {}).get("last_completed_at")
        result["alerts"] = self.notifier.status() if self.notifier else {"configured": False, "verified": False}
        result["generated_at"] = now.isoformat()
        result["worker"] = {"last_tick": self.last_tick.isoformat() if self.last_tick else None,
                            "error": self.last_error}
        return result

    def continue_research(self, desk_id, now=None):
        now = now or utcnow()
        # This endpoint never unpauses trading, starts a round or authorizes spend.
        self.store.publish(desk_id, "continue_requested", {"scope": "research"}, now)
        return self.supervisor.request_cycle(desk_id, now)

    def reconcile(self, now=None):
        now = now or utcnow()
        for desk, executor in self.executors.items():
            try:
                executor.reconcile(now=now)
            except Exception:
                self.store.pause(desk, "order_reconciliation_failed")
            try:
                executor.settle(now=now)
            except Exception:
                # A failed market read is recoverable; it cannot create a settlement.
                self.last_error = "settlement_source_unavailable"

    def tick(self, now=None):
        now = now or utcnow()
        self.last_tick = now
        self.last_error = None
        self.reconcile(now)
        # Restore safe operation after a process restart without resetting the round.
        snapshot = self.store.snapshot(now)
        if snapshot.get("started_at") and self.settings.live_enabled:
            if any(not self._isolation.get(d) or now - self._isolation[d]["at"] > timedelta(minutes=4)
                   for d in self.executors):
                self.check_launch(now, refresh=True)
        try:
            result = self.supervisor.tick(now)
        except Exception:
            self.last_error = "research_cycle_failed"
            logger.warning("desk research cycle failed; see authenticated status")
            result = {"status": "needs_operator", "reason": self.last_error}
        if self.notifier:
            self.notifier.observe(self.status(now), now)
            self.notifier.deliver(now)
        return result
