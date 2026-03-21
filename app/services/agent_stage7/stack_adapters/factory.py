from __future__ import annotations

import logging
import time
from typing import Any

from app.core.config import Settings
from app.services.agent_stage7.stack_adapters.base import Stage7Adapter, Stage7AdapterInput
from app.services.agent_stage7.stack_adapters.langgraph_adapter import LangGraphAdapter
from app.services.agent_stage7.stack_adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from app.services.agent_stage7.stack_adapters.plain_api_adapter import PlainApiAdapter

logger = logging.getLogger(__name__)

# Provider cooldown: keyed by adapter.name → monotonic timestamp when cooldown expires.
# Set after 429 (rate-limit) or 402 (billing/quota) to avoid retry storms.
_PROVIDER_COOLDOWN: dict[str, float] = {}
_COOLDOWN_SECONDS = 900.0  # 15 minutes

# HTTP status codes that trigger a cooldown for that provider.
_COOLDOWN_STATUS_CODES = {"429", "402"}


class FallbackAdapter:
    """Tries a list of adapters in order, skipping those that return error reason_codes."""

    name = "fallback_chain"
    _ERROR_CODES = {"adapter_http_error", "adapter_transport_error", "adapter_empty_output"}

    def __init__(self, adapters: list[Stage7Adapter]) -> None:
        self._adapters = adapters

    def decide(self, payload: Stage7AdapterInput) -> dict[str, Any]:
        last_result: dict[str, Any] = {
            "decision": "SKIP",
            "reason_codes": ["fallback_all_failed"],
            "provider_fingerprint": "fallback_chain",
            "simulated_latency_ms": 0.0,
        }
        now = time.monotonic()
        for adapter in self._adapters:
            # Skip providers that are on cooldown after 429/402.
            cooldown_until = _PROVIDER_COOLDOWN.get(adapter.name, 0.0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now)
                logger.info("stage7_provider_cooldown skipping %s for %ds", adapter.name, remaining)
                continue

            result = adapter.decide(payload)
            reason = str((result.get("reason_codes") or [""])[0])
            is_error = any(reason.startswith(code) for code in self._ERROR_CODES)
            if not is_error:
                return result

            logger.warning("stage7_fallback skipping %s reason=%s", adapter.name, reason)

            # Set cooldown when provider returns 429 (rate-limit) or 402 (billing/quota).
            status_code = reason.split(":")[-1] if ":" in reason else ""
            if status_code in _COOLDOWN_STATUS_CODES:
                retry_after = float(result.get("retry_after_seconds") or 0.0)
                cooldown = max(_COOLDOWN_SECONDS, retry_after)
                _PROVIDER_COOLDOWN[adapter.name] = time.monotonic() + cooldown
                logger.warning(
                    "stage7_provider_cooldown set %s for %.0fs (status=%s)",
                    adapter.name, cooldown, status_code,
                )
            else:
                # Honor short Retry-After for other errors (max 10s to avoid blocking).
                retry_after = float(result.get("retry_after_seconds") or 0.0)
                if 0 < retry_after <= 10.0:
                    time.sleep(retry_after)

            last_result = result
        return last_result


def _build_openai_compatible_adapter(settings: Settings) -> OpenAICompatibleAdapter | None:
    profile = str(settings.stage7_agent_provider_profile or "openai").strip().lower()
    timeout_seconds = float(settings.stage7_openai_timeout_seconds)

    if profile == "gemini":
        api_key = str(settings.gemini_api_key or "").strip()
        if not api_key:
            return None
        return OpenAICompatibleAdapter(
            api_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key=api_key,
            model=str(settings.stage7_gemini_model or "gemini-2.5-flash"),
            timeout_seconds=timeout_seconds,
        )

    if profile == "groq":
        api_key = str(settings.groq_api_key or "").strip()
        if not api_key:
            return None
        return OpenAICompatibleAdapter(
            api_base_url="https://api.groq.com/openai/v1",
            api_key=api_key,
            model=str(settings.stage7_groq_model or "llama-3.3-70b-versatile"),
            timeout_seconds=timeout_seconds,
        )

    if profile == "openrouter":
        api_key = str(settings.openrouter_api_key or "").strip()
        if not api_key:
            return None
        extra_headers: dict[str, str] = {}
        if str(settings.stage7_openrouter_http_referer or "").strip():
            extra_headers["HTTP-Referer"] = str(settings.stage7_openrouter_http_referer).strip()
        if str(settings.stage7_openrouter_x_title or "").strip():
            extra_headers["X-Title"] = str(settings.stage7_openrouter_x_title).strip()
        return OpenAICompatibleAdapter(
            api_base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            model=str(settings.stage7_openrouter_model or "google/gemini-2.5-flash-preview"),
            timeout_seconds=timeout_seconds,
            extra_headers=extra_headers,
        )

    api_key = str(settings.stage7_openai_api_key or "").strip()
    if not api_key:
        return None
    return OpenAICompatibleAdapter(
        api_base_url=settings.stage7_openai_api_base_url,
        api_key=api_key,
        model=settings.stage7_openai_model,
        timeout_seconds=timeout_seconds,
    )


def _build_all_adapters(settings: Settings) -> list[Stage7Adapter]:
    """Build all configured providers in priority order: groq → gemini → openrouter → plain fallback."""
    adapters: list[Stage7Adapter] = []
    timeout = float(settings.stage7_openai_timeout_seconds)

    groq_key = str(settings.groq_api_key or "").strip()
    if groq_key:
        adapters.append(OpenAICompatibleAdapter(
            api_base_url="https://api.groq.com/openai/v1",
            api_key=groq_key,
            model=str(settings.stage7_groq_model or "llama-3.3-70b-versatile"),
            timeout_seconds=timeout,
        ))

    gemini_key = str(settings.gemini_api_key or "").strip()
    if gemini_key:
        adapters.append(OpenAICompatibleAdapter(
            api_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key=gemini_key,
            model=str(settings.stage7_gemini_model or "gemini-2.5-flash"),
            timeout_seconds=timeout,
        ))

    openrouter_key = str(settings.openrouter_api_key or "").strip()
    if openrouter_key and bool(getattr(settings, "stage7_openrouter_enabled", True)):
        extra: dict[str, str] = {}
        if str(settings.stage7_openrouter_http_referer or "").strip():
            extra["HTTP-Referer"] = str(settings.stage7_openrouter_http_referer).strip()
        if str(settings.stage7_openrouter_x_title or "").strip():
            extra["X-Title"] = str(settings.stage7_openrouter_x_title).strip()
        adapters.append(OpenAICompatibleAdapter(
            api_base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_key,
            model=str(settings.stage7_openrouter_model or "google/gemini-2.5-flash-preview"),
            timeout_seconds=timeout,
            extra_headers=extra,
        ))

    # Always include PlainApiAdapter as deterministic last-resort fallback so that
    # billing/quota errors on all LLM providers don't silently kill signal flow.
    adapters.append(PlainApiAdapter())
    return adapters


def get_stage7_adapter(settings: Settings) -> Stage7Adapter:
    provider = str(settings.stage7_agent_provider or "langgraph").strip().lower()
    if provider == "langgraph":
        return LangGraphAdapter()
    if provider in {"plain_llm_api", "openai", "openai_compatible"}:
        if bool(settings.stage7_agent_real_calls_enabled):
            # Auto-fallback: try groq → gemini → openrouter in order
            all_adapters = _build_all_adapters(settings)
            if all_adapters:
                return FallbackAdapter(all_adapters) if len(all_adapters) > 1 else all_adapters[0]
        return PlainApiAdapter()
    return PlainApiAdapter()
