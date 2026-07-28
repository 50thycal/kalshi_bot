"""LLM access for heartbeat cognition: model routing (routine/deep aliases),
DB-configured pricing, hard pre-call budget stops, per-call cost ledger, prompt
caching (spec §11, §20).

Three model aliases, one per heartbeat tier (see cognition.alias_for_kind):
"routine" (small, hourly), "deep" (medium, ~12-hourly reflection) and
"strategic" (large, ~48-hourly plus every birth/cohort_end/retirement).

Two backends, chosen per call by LlmClient.complete(): the Anthropic Messages
API over httpx (no new SDK dependency), and an optional OpenAI-compatible
/chat/completions server for whichever aliases are listed in
EVO_LOCAL_LLM_ALIASES (default "routine,deep") when EVO_LOCAL_LLM_ENABLED is
set. That second backend covers a self-hosted server (Ollama / llama.cpp /
vLLM — no api key, zero cost, so token caps are the only constraint) or any
hosted inference API/router that speaks the same OpenAI-compatible shape (Groq,
OpenRouter, ... — a bearer key plus per-Mtok cost rates, so real cost is tracked
and the weekly dollar ceiling is enforced exactly as on the Anthropic path).
Each routed tier may name its own model + rates (the deep tier can be a stronger,
pricier model than routine), falling back to the routine values when unset.
Groq's own free tier caps a single request's tokens-per-minute well below what
a routine heartbeat's context needs (~9-10K input alone); OpenRouter fronts many
providers behind one endpoint without that per-request ceiling, so it is the
practical default for a paid hosted backend — same code path, no provider-
specific logic beyond the bearer header and its (optional, harmless-elsewhere)
HTTP-Referer/X-Title identification headers. "strategic" is deliberately NOT in
the default alias set: the top tier keeps its Anthropic tie-in because it runs
the high-stakes, irreversible lifecycle beats. One call per heartbeat, bounded
max_tokens — there is no agent-side loop, so runaway recursion is impossible by
construction.

No ANTHROPIC_API_KEY and no local backend configured => cognition fails closed
(the orchestrator journals heartbeats as skipped; listeners/paper/audit keep
running)."""

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
    ("strategic", "claude-sonnet-5", 3.00, 15.00, 0.30),
)


def seed_model_prices(session, settings: EvoSettings) -> int:
    """Idempotently seed pricing rows, honoring configured model overrides."""
    added = 0
    overrides = {
        "routine": settings.model_routine,
        "deep": settings.model_deep,
        "strategic": settings.model_strategic,
    }
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


def rate_cost_usd(
    input_tokens: int, output_tokens: int, in_per_mtok: float, out_per_mtok: float
) -> float:
    """Flat per-Mtok cost for the OpenAI-compatible backend (no cache tier)."""
    return round(
        input_tokens / 1e6 * float(in_per_mtok) + output_tokens / 1e6 * float(out_per_mtok),
        6,
    )


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
    # Why the provider stopped generating: Anthropic's `stop_reason`
    # ("end_turn" | "max_tokens" | ...) or an OpenAI-compatible `finish_reason`
    # ("stop" | "length" | ...). Empty when the backend reported none — never
    # synthesized, so absence stays distinguishable from "stopped normally".
    stop_reason: str = ""

    @property
    def truncated(self) -> bool:
        """The generation was cut off by the output-token cap. Both provider
        vocabularies mean the same thing here."""
        return self.stop_reason in ("max_tokens", "length")


class LlmClient:
    def __init__(self, settings: EvoSettings, api_key: str | None = None) -> None:
        self.settings = settings
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self._http = httpx.Client(timeout=settings.llm_timeout_seconds)
        # Split the local timeout: a SHORT connect bound so an unreachable or
        # misconfigured server (wrong private hostname, Ollama not bound to the
        # private interface, wrong port) fails in seconds and the heartbeat degrades
        # fast — instead of blocking the single-threaded orchestrator for the full
        # generation timeout (a live incident: an unreachable Ollama + a 600s timeout
        # froze the whole loop, mid-transaction, one heartbeat at a time). The long
        # read timeout still covers genuinely slow CPU generation once connected.
        self._local_http = httpx.Client(
            timeout=httpx.Timeout(
                settings.local_llm_timeout_seconds,
                connect=min(10.0, settings.local_llm_timeout_seconds),
            )
        )

    def available(self) -> bool:
        return bool(self.api_key)

    def local_available(self) -> bool:
        return bool(
            self.settings.local_llm_enabled
            and self.settings.local_llm_base_url
            and self.settings.local_llm_model
        )

    def routes_local(self, alias: str) -> bool:
        """True when this tier's alias should go to the OpenAI-compatible backend."""
        return self.local_available() and alias in self.settings.local_alias_set()

    def _max_input_tokens(self, alias: str) -> int:
        """Input-size pre-flight cap for this tier. Tiers 2/3 carry richer context
        (graveyard + peer roster on top of everything tier 1 gets — see
        cognition.is_enriched_kind), so they legitimately need more headroom than
        tier 1's cap. Discovered live: reflection heartbeats regularly ran
        ~12.5-13.5K tokens and were rejected by the single shared cap that was
        sized for tier 1 — every tier-2 heartbeat degraded as "input too large"
        even though nothing was actually wrong with it."""
        if alias == "strategic":
            return self.settings.strategic_max_input_tokens
        if alias == "deep":
            return self.settings.reflection_max_input_tokens
        return self.settings.heartbeat_max_input_tokens

    def _local_model_and_rates(self, alias: str) -> tuple[str, float, float]:
        """(model_id, input $/Mtok, output $/Mtok) for an alias routed to the
        OpenAI-compatible backend. Each tier may name its own model and rates;
        anything left unset falls back to the routine values, so a single-model
        setup keeps working unchanged."""
        s = self.settings
        model = s.local_llm_model
        in_rate = float(s.local_llm_input_cost_per_mtok)
        out_rate = float(s.local_llm_output_cost_per_mtok)
        if alias == "deep":
            model = s.local_llm_deep_model or model
            in_rate = float(s.local_llm_deep_input_cost_per_mtok) or in_rate
            out_rate = float(s.local_llm_deep_output_cost_per_mtok) or out_rate
        return model, in_rate, out_rate

    def _local_headers(self) -> dict[str, str]:
        """Auth header for the OpenAI-compatible routine backend: a bearer token when
        a hosted provider (Groq, OpenRouter, ...) is configured, nothing for a keyless
        self-host. OpenRouter additionally reads HTTP-Referer/X-Title identification
        headers — optional per their docs (used for their public app-rankings page,
        not required for a request to succeed) — sent only when the base URL is
        OpenRouter's; harmless no-ops on any other provider, so this never affects
        Groq or a self-hosted server."""
        key = self.settings.local_llm_api_key
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        if "openrouter.ai" in self.settings.local_llm_base_url:
            headers["HTTP-Referer"] = "https://github.com/50thycal/kalshi_bot"
            headers["X-Title"] = "kalshi-evo-bot"
        return headers

    def _local_is_paid(self, alias: str = "routine") -> bool:
        """A cost rate > 0 on this tier marks a paid hosted provider — then real cost
        is booked and the weekly dollar ceiling applies. Both rates 0 => free
        self-hosted."""
        _, in_rate, out_rate = self._local_model_and_rates(alias)
        return in_rate > 0 or out_rate > 0

    def probe_local(self) -> str:
        """One-shot reachability + model-presence check against the configured local
        OpenAI-compatible server. Log-only diagnosis — never used by heartbeats, and
        it runs whenever a base URL is configured (even if local_llm_enabled is off),
        so the exact failure reason is visible in the worker log while routine
        heartbeats stay safely on Anthropic. The exception TYPE is the diagnosis:
        a name-resolution error means the private hostname is wrong or private
        networking is off; a connect timeout means the host resolves but nothing is
        listening on the private interface (Ollama bound to 127.0.0.1 instead of
        0.0.0.0/[::]); connection refused means reachable host but wrong port."""
        base = self.settings.local_llm_base_url
        if not base:
            return "not configured (no EVO_LOCAL_LLM_BASE_URL)"
        url = base.rstrip("/") + "/models"
        # Every tier actually routed here must have its model present — a deep tier
        # pointed at a typo'd model id would otherwise only surface at the first
        # 12-hourly reflection, hours after deploy.
        routed = self.settings.local_alias_set() or {"routine"}
        wanted: list[str] = []
        for alias in sorted(routed):
            model, _, _ = self._local_model_and_rates(alias)
            if model and model not in wanted:
                wanted.append(model)
        if not wanted:
            return "not configured (no EVO_LOCAL_LLM_MODEL)"
        headers = self._local_headers()  # bearer token for a hosted provider (Groq /models 401s without it)
        try:
            resp = httpx.Client(timeout=10.0).get(url, headers=headers)
            resp.raise_for_status()
            ids = [str(m.get("id")) for m in (resp.json().get("data") or [])]
        except httpx.HTTPStatusError as exc:
            return f"reachable but HTTP {exc.response.status_code} at {url}: {exc.response.text[:150]}"
        except Exception as exc:  # noqa: BLE001 — a probe must never raise into startup
            return f"UNREACHABLE at {url}: {type(exc).__name__}: {exc}"
        missing = [m for m in wanted if m not in ids]
        if not missing:
            return (f"OK — reachable at {url}; target model(s) "
                    f"{', '.join(repr(m) for m in wanted)} present ({len(ids)} model(s))")
        return (f"reachable at {url} but target model(s) "
                f"{', '.join(repr(m) for m in missing)} MISSING — "
                f"have: {', '.join(ids[:8]) or '(none)'}")

    def close(self) -> None:
        self._http.close()
        self._local_http.close()

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
        """Budget-checked single completion. Routes any alias listed in
        EVO_LOCAL_LLM_ALIASES to the OpenAI-compatible backend when one is
        configured; everything else goes to Anthropic."""
        if self.routes_local(alias):
            return self._complete_local(
                session, agent_uuid=agent_uuid, cohort_id=cohort_id,
                heartbeat_id=heartbeat_id, alias=alias, system_blocks=system_blocks,
                user_content=user_content, max_tokens=max_tokens,
            )
        return self._complete_anthropic(
            session, agent_uuid=agent_uuid, cohort_id=cohort_id,
            heartbeat_id=heartbeat_id, alias=alias, system_blocks=system_blocks,
            user_content=user_content, max_tokens=max_tokens,
        )

    def _complete_anthropic(
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
        """Projects a worst-case cost before the call and refuses if it would
        breach the weekly ceiling; books actual usage (force=True) afterward so
        real spend is never under-counted."""
        price = get_price(session, alias)
        if price is None:
            return LlmResult(text="", model_id="", alias=alias, error="no price configured")
        if not self.available():
            return LlmResult(text="", model_id=price.model_id, alias=alias,
                             error="no ANTHROPIC_API_KEY")

        est_input = len(user_content) // 3 + sum(
            len(str(b.get("text", ""))) for b in system_blocks
        ) // 3
        if est_input > self._max_input_tokens(alias):
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
        stop_reason = str(data.get("stop_reason") or "")
        cost = compute_cost_usd(price, input_tokens, cached, output_tokens)
        # The API call above already happened and was billed by Anthropic — it is
        # irreversible. Record it in its OWN committed transaction, independent of
        # the caller's still-open one, so a later failure elsewhere in the calling
        # heartbeat (e.g. during action execution, before the caller's
        # session_scope() commits) can never erase this cost from the audit trail
        # or the agent's weekly budget. Writing it into the caller's `session`
        # (flush-only) was exactly how a real, already-spent Sonnet-5 birth-
        # heartbeat cost went untracked during founder bootstrap: the outer
        # transaction later rolled back, taking the EvoLlmUsage row and the
        # budget deduction with it, while the Anthropic bill did not roll back.
        from ..db import session_scope  # local import: avoids a cycle at module load

        with session_scope() as cost_session:
            budgets.spend(cost_session, agent_uuid, cohort_id, "llm_cost_usd", cost, force=True)
            budgets.spend(
                cost_session, agent_uuid, cohort_id, "tokens",
                float(input_tokens + output_tokens), force=True,
            )
            cost_session.add(
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
                    stop_reason=stop_reason,
                )
            )
        return LlmResult(
            text=text,
            model_id=price.model_id,
            alias=alias,
            input_tokens=input_tokens,
            cached_input_tokens=cached,
            output_tokens=output_tokens,
            cost_usd=cost,
            stop_reason=stop_reason,
        )

    def _complete_local(
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
        """OpenAI-compatible /chat/completions backend for routine heartbeats — a
        self-hosted server (Ollama / llama.cpp / vLLM) or a hosted API (Groq). A
        bearer token is sent when configured; when cost rates are set (a paid
        provider) the weekly dollar ceiling is projected before the call and the
        actual cost booked after, exactly like the Anthropic path. A keyless,
        zero-rate self-host stays free: no dollar cost, no ceiling check. Same
        idempotent cost-recording discipline (own transaction, survives a later
        rollback in the caller's)."""
        model_id, in_rate, out_rate = self._local_model_and_rates(alias)
        paid = in_rate > 0 or out_rate > 0
        system_text = "\n\n".join(str(b.get("text", "")) for b in system_blocks)
        est_input = (len(system_text) + len(user_content)) // 3
        if est_input > self._max_input_tokens(alias):
            return LlmResult(text="", model_id=model_id, alias=alias,
                             error=f"input too large (~{est_input} tokens)")
        # Paid hosted provider: refuse up front if the worst-case cost would breach
        # the weekly ceiling (mirrors the Anthropic path). Free self-host skips this.
        if paid:
            projected = rate_cost_usd(est_input, max_tokens, in_rate, out_rate)
            if not budgets.can_spend(
                session, agent_uuid, cohort_id, "llm_cost_usd", projected
            ):
                return LlmResult(text="", model_id=model_id, alias=alias,
                                 error="weekly LLM cost ceiling reached")
        if not budgets.can_spend(
            session, agent_uuid, cohort_id, "tokens", est_input + max_tokens
        ):
            return LlmResult(text="", model_id=model_id, alias=alias,
                             error="weekly token budget exhausted")

        messages = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": user_content})
        try:
            resp = self._local_http.post(
                self.settings.local_llm_base_url.rstrip("/") + "/chat/completions",
                headers=self._local_headers(),
                json={"model": model_id, "messages": messages, "max_tokens": max_tokens},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            logger.warning("evo local llm http error", extra={"extra_fields": {
                "status": exc.response.status_code, "body": body}})
            return LlmResult(text="", model_id=model_id, alias=alias,
                             error=f"http {exc.response.status_code}: {body}")
        except Exception as exc:  # noqa: BLE001
            # Connection-level failures (unreachable host, connect timeout, refused)
            # land here, NOT in the HTTPStatusError branch — log the real reason so an
            # operator can see WHY every routine heartbeat is degrading, instead of a
            # silent no-op. The connect timeout above bounds how long this blocks.
            logger.warning("evo local llm connection error", extra={"extra_fields": {
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "url": self.settings.local_llm_base_url}})
            return LlmResult(text="", model_id=model_id, alias=alias,
                             error=f"{type(exc).__name__}: {exc}")

        choices = data.get("choices") or []
        text = str((choices[0].get("message") or {}).get("content", "")) if choices else ""
        stop_reason = str(choices[0].get("finish_reason") or "") if choices else ""
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens", 0) or est_input)
        output_tokens = int(usage.get("completion_tokens", 0) or max(1, len(text) // 3))
        cost = rate_cost_usd(input_tokens, output_tokens, in_rate, out_rate) if paid else 0.0

        from ..db import session_scope  # local import: avoids a cycle at module load

        with session_scope() as cost_session:
            if paid:
                budgets.spend(
                    cost_session, agent_uuid, cohort_id, "llm_cost_usd", cost, force=True
                )
            budgets.spend(
                cost_session, agent_uuid, cohort_id, "tokens",
                float(input_tokens + output_tokens), force=True,
            )
            cost_session.add(
                EvoLlmUsage(
                    created_at=datetime.now(timezone.utc),
                    agent_uuid=agent_uuid,
                    cohort_id=cohort_id,
                    heartbeat_id=heartbeat_id,
                    model_alias=alias,
                    model_id=model_id,
                    input_tokens=input_tokens,
                    cached_input_tokens=0,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                    stop_reason=stop_reason,
                )
            )
        return LlmResult(
            text=text,
            model_id=model_id,
            alias=alias,
            input_tokens=input_tokens,
            cached_input_tokens=0,
            output_tokens=output_tokens,
            cost_usd=cost,
            stop_reason=stop_reason,
        )
