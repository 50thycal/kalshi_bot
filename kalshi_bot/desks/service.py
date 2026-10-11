"""Application boundary: identities, shared start, reconciliation and desk status."""
from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from decimal import ROUND_CEILING, Decimal

from .config import DeskSettings
from .contracts import Decision, DeskError, utcnow
from .scoreboard import comparison

logger = logging.getLogger(__name__)
DESKS = ("chatgpt", "claude")


class DeskService:
    def __init__(self, settings: DeskSettings, store, supervisor, executors=None, notifier=None,
                 browser=None, market_probe=None):
        self.settings, self.store, self.supervisor = settings, store, supervisor
        self.browser = browser  # read-only public market discovery (DEC-024)
        self.market_probe = market_probe  # one bounded public GET for /api/diagnostics
        self.executors = executors or {}
        self.notifier = notifier
        self._isolation = {}
        self._shadow_checked = {}  # decision_id -> last settlement read (in-memory throttle)
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
                    # Before start the subaccount must hold at least the book's bankroll
                    # ($30 fresh, or the carried balance, DEC-024); after start, available cash.
                    minimum = Decimal(row["available_cash"] if snapshot.get("started_at")
                                      else row["initial_bankroll"])
                    if not report.get("verified") or Decimal(report["balance"]) < minimum:
                        raise DeskError("funding_or_isolation_not_ready")
                    if not snapshot.get("started_at"):
                        executor.exchange.check_clean_book()
                    self._isolation[desk] = {"at": now, "verified": True}
                    executor.isolation_verified = True
                    checked = self._isolation[desk]
                except Exception:
                    executor.isolation_verified = False
                    if self.settings.account_mode == "shared_primary" and snapshot.get("started_at"):
                        self.store.pause(desk, "shared_account_check_failed")
                    self._isolation.pop(desk, None)
                    blockers.append(f"{desk}_funding_or_isolation_not_verified")
            if not checked or now - checked["at"] > timedelta(minutes=5):
                blockers.append(f"{desk}_fresh_isolation_check_required")
        if self.settings.alert_mode == "session":
            if self.settings.research_mode != "session":
                blockers.append("session_alerts_require_session_research")
        elif not self.settings.alert_webhook_url.get_secret_value():
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
        executor = self.executors.get(decision.desk_id)
        if executor is None:
            raise DeskError("exchange_unavailable")
        other = "claude" if decision.desk_id == "chatgpt" else "chatgpt"
        if any(not blocker.startswith(other + "_") for blocker in self.settings.static_blockers()):
            raise DeskError("runtime_configuration_not_ready")
        if self.settings.alert_mode == "session" and self.settings.research_mode != "session":
            raise DeskError("session_alerts_require_session_research")
        if self.settings.alert_mode == "webhook" and (self.notifier is None or not self.notifier.verified()):
            raise DeskError("operator_alert_delivery_not_verified")
        def require_fresh_execution():
            current = utcnow()
            checked = self._isolation.get(decision.desk_id)
            if not checked or not 0 <= (current - checked["at"]).total_seconds() <= 300:
                raise DeskError("fresh_isolation_check_required")
            if self.last_tick is None or not 0 <= (current - self.last_tick).total_seconds() <= 180:
                raise DeskError("execution_monitor_stale")
            return current

        require_fresh_execution()
        self.supervisor.verify_decision_sources(decision)
        # Source verification may be slow; recheck just before the lease and executor.
        now = require_fresh_execution()
        if self.settings.research_mode == "session":
            self.supervisor.verify_session_decision(decision, now)
        if decision.desk_id in self.settings.shadow_desk_set():
            return self.shadow(decision, executor, now)
        return executor.submit(decision, now=now)

    @staticmethod
    def _shadow_id(decision_id, kind):
        return hashlib.sha256(f"{kind}:{decision_id}".encode()).hexdigest()

    def shadow(self, decision: Decision, executor, now):
        """Dry run (WS-025): record what the live path would do; never reserve or order."""
        record_id = self._shadow_id(decision.decision_id, "shadow_decision")
        prior = self.store.publication(record_id)
        if prior is not None:
            return prior["payload"]  # A replayed completion never re-quotes the decision.
        book = next(row for row in self.store.snapshot(now)["desks"]
                    if row["desk_id"] == decision.desk_id)
        if book.get("paused"):
            payload = {"decision_id": decision.decision_id, "ticker": decision.ticker,
                       "side": decision.side, "status": "would_refuse", "reason": "desk_paused"}
        else:
            payload = executor.preview(decision, now)
        payload = {**payload, "mode": "shadow"}
        self.store.publish(decision.desk_id, "shadow_decision", payload, now, record_id=record_id)
        return payload

    def settle_shadow(self, desk, executor, now):
        """Grade dry-run picks once their market settles: hypothetical fill at the quoted ask."""
        for record in self.store.publications(desk, "shadow_decision"):
            item = record["payload"]
            if item.get("status") != "would_submit":
                continue
            record_id = self._shadow_id(item["decision_id"], "shadow_settlement")
            if self.store.publication(record_id) is not None:
                continue
            checked = self._shadow_checked.get(item["decision_id"])
            if checked and now - checked < timedelta(minutes=5):
                continue  # One exchange read per open dry-run pick every five minutes.
            self._shadow_checked[item["decision_id"]] = now
            settlement = executor.exchange.settlement(item["ticker"])
            if settlement is None:
                continue
            yes = Decimal(str(settlement.yes_payout))
            outcome = yes if item["side"] == "yes" else 1 - yes
            quantity, ask = Decimal(item["quantity"]), Decimal(item["ask"])
            fee = (Decimal(item["fee_rate"]) * quantity * ask * (1 - ask)).quantize(
                Decimal("0.01"), rounding=ROUND_CEILING)
            payout = quantity * outcome
            graded = {"decision_id": item["decision_id"], "ticker": item["ticker"],
                      "side": item["side"], "yes_payout": str(yes), "quantity": str(quantity),
                      "assumed_fill_price": str(ask), "fee": str(fee), "payout": str(payout),
                      "pnl": str(payout - quantity * ask - fee),
                      "settled_at": settlement.settled_at.isoformat(), "mode": "shadow"}
            if yes in (Decimal(0), Decimal(1)):
                graded["brier_score"] = str((Decimal(item["probability"]) - outcome) ** 2)
            self.store.publish(desk, "shadow_settlement", graded, now, record_id=record_id)

    def status(self, now=None):
        now = now or utcnow()
        result = self.store.snapshot(now)
        research = self.supervisor.status(now)
        result["research_mode"] = self.settings.research_mode
        result["account_mode"] = self.settings.account_mode
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
        if self.settings.alert_mode == "session":
            result["alerts"] = {
                "mode": "session", "configured": True, "push_delivery": False,
                "delivery": "Read authenticated status at each Go/Continue; no notification while the app is inactive.",
                "notices": [{"desk_id": row["desk_id"], "pause_reason": row.get("pause_reason"),
                             "health_reasons": result["health"].get(row["desk_id"], {}).get("reasons", [])}
                            for row in result["desks"] if row.get("paused") or row.get("pause_reason")
                            or result["health"].get(row["desk_id"], {}).get("reasons")],
            }
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
            try:
                self.settle_shadow(desk, executor, now)
            except Exception:
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
        if self.notifier and self.settings.alert_mode == "webhook":
            self.notifier.observe(self.status(now), now)
            self.notifier.deliver(now)
        return result
