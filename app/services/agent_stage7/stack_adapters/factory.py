from __future__ import annotations

from app.core.config import Settings
from app.services.agent_stage7.stack_adapters.base import Stage7Adapter
from app.services.agent_stage7.stack_adapters.langgraph_adapter import LangGraphAdapter
from app.services.agent_stage7.stack_adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from app.services.agent_stage7.stack_adapters.plain_api_adapter import PlainApiAdapter


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


def get_stage7_adapter(settings: Settings) -> Stage7Adapter:
    provider = str(settings.stage7_agent_provider or "langgraph").strip().lower()
    if provider == "langgraph":
        return LangGraphAdapter()
    if provider in {"plain_llm_api", "openai", "openai_compatible"}:
        if bool(settings.stage7_agent_real_calls_enabled):
            adapter = _build_openai_compatible_adapter(settings)
            if adapter is not None:
                return adapter
        return PlainApiAdapter()
    # Safe fallback
    return PlainApiAdapter()
