"""LLM access for heartbeat cognition: model routing (routine/deep aliases),
DB-configured pricing, hard pre-call budget stops, per-call cost ledger, prompt
caching (spec §11, §20).

Uses the Anthropic Messages API directly over httpx (the repo's HTTP client; no
new SDK dependency). One call per heartbeat, bounded max_tokens — there is no
agent-side loop, so runaway recursion is impossible by construction.

No ANTHROPIC_API_KEY => available() is False and cognition fails closed (the
orchestrator journals heartbeats as skipped; listeners/paper/audit keep running)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from . import budgets
from .config import EvoSettings
from .models import EvoLlmUsage, EvoModelPrice

logger = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Seed prices (USD per million tokens). DB rows are the source of truth and can be
# updated without a deploy; these only populate an empty table.
SEED_PRICES = (
    # alias, model_id, input, output, cached_input
    ("routine", "claude-haiku-4-5-20251001", 1.00, 5.00, 0.10),
    ("deep", "claude-sonnet-5", 3.00, 15.00, 0.30),
)


def seed_model_prices(session, settings: EvoSettings) -> int:
    """Idempotently seed pricing rows, honoring configured model overrides."""
    added = 0
    overrides = {"routine": settings.model_routine, "deep": settings.model_deep}
    for alias, model_id, inp, out, cached in SEED_PRICES:
        model_id = overrides.get(alias, model_id)
        exists = session.scalar(
            select(EvoModelPrice).where(
                EvoModelPrice.alias == alias, EvoModelPrice.active.is_(True)
            )
        )
        if exists is None:
            session.add(
                EvoModelPrice(
                    alias=alias,
                    model_id=model_id,
                    input_usd_per_mtok=inp,
                    output_usd_per_mtok=out,
                    cached_input_usd_per_mtok=cached,
                )
            )
            added += 1
    session.flush()
    return added


def get_price(session, alias: str) -> EvoModelPrice | None:
    return session.scalar(
        select(EvoModelPrice)
        .where(EvoModelPrice.alias == alias, EvoModelPrice.active.is_(True))
        .order_by(EvoModelPrice.effective_at.desc())
        .limit(1)
    )


def compute_cost_usd(
    price: EvoModelPrice, input_tokens: int, cached_tokens: int, output_tokens: int
) -> float:
    uncached = max(0, input_tokens - cached_tokens)
    cost = uncached / 1e6 * float(price.input_usd_per_mtok)
    cost += cached_tokens / 1e6 * float(price.cached_input_usd_per_mtok or price.input_usd_per_mtok)
    cost += output_tokens / 1e6 * float(price.output_usd_per_mtok)
    return round(cost, 6)


@dataclass
class LlmResult:
    text: str
    model_id: str
    alias: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None


class LlmClient:
    def __init__(self, settings: EvoSettings, api_key: str | None = None) -> None:
        self.settings = settings
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self._http = httpx.Client(timeout=settings.llm_timeout_seconds)

    def available(self) -> bool:
        return bool(self.api_key)

    def close(self) -> None:
        self._http.close()

    def complete(
        self,
        session,
        *,
        agent_uuid: str,
        cohort_id: int,
        heartbeat_id: int | None,
        alias: str,
        system_blocks: list[dict],
        user_content: str,
        max_tokens: int,
    ) -> LlmResult:
        """Budget-checked single completion. Projects a worst-case cost before the
        call and refuses if it would breach the weekly ceiling; books actual usage
        (force=True) afterward so real spend is never under-counted."""
        price = get_price(session, alias)
        if price is None:
            return LlmResult(text="", model_id="", alias=alias, error="no price configured")
        if not self.available():
            return LlmResult(text="", model_id=price.model_id, alias=alias,
                             error="no ANTHROPIC_API_KEY")

        est_input = len(user_content) // 3 + sum(
            len(str(b.get("text", ""))) for b in system_blocks
        ) // 3
        if est_input > self.settings.heartbeat_max_input_tokens:
            return LlmResult(text="", model_id=price.model_id, alias=alias,
                             error=f"input too large (~{est_input} tokens)")
        projected = compute_cost_usd(price, est_input, 0, max_tokens)
        if not budgets.can_spend(session, agent_uuid, cohort_id, "llm_cost_usd", projected):
            return LlmResult(text="", model_id=price.model_id, alias=alias,
                             error="weekly LLM cost ceiling reached")
        if not budgets.can_spend(
            session, agent_uuid, cohort_id, "tokens", est_input + max_tokens
        ):
            return LlmResult(text="", model_id=price.model_id, alias=alias,
                             error="weekly token budget exhausted")

        try:
            resp = self._http.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": price.model_id,
                    "max_tokens": max_tokens,
                    "system": system_blocks,
                    "messages": [{"role": "user", "content": user_content}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            logger.warning("evo llm http error", extra={"extra_fields": {
                "status": exc.response.status_code, "body": body}})
            return LlmResult(text="", model_id=price.model_id, alias=alias,
                             error=f"http {exc.response.status_code}: {body}")
        except Exception as exc:  # noqa: BLE001
            return LlmResult(text="", model_id=price.model_id, alias=alias,
                             error=f"{type(exc).__name__}: {exc}")

        usage = data.get("usage", {}) or {}
        input_tokens = int(usage.get("input_tokens", 0))
        cached = int(usage.get("cache_read_input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0))
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        cost = compute_cost_usd(price, input_tokens, cached, output_tokens)
        # Book ACTUAL spend (force: it already happened; never under-count).
        budgets.spend(session, agent_uuid, cohort_id, "llm_cost_usd", cost, force=True)
        budgets.spend(
            session, agent_uuid, cohort_id, "tokens",
            float(input_tokens + output_tokens), force=True,
        )
        session.add(
            EvoLlmUsage(
                created_at=datetime.now(timezone.utc),
                agent_uuid=agent_uuid,
                cohort_id=cohort_id,
                heartbeat_id=heartbeat_id,
                model_alias=alias,
                model_id=price.model_id,
                input_tokens=input_tokens,
                cached_input_tokens=cached,
                output_tokens=output_tokens,
                cost_usd=cost,
            )
        )
        session.flush()
        return LlmResult(
            text=text,
            model_id=price.model_id,
            alias=alias,
            input_tokens=input_tokens,
            cached_input_tokens=cached,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
