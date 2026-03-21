from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings


def build_provider_chain(settings: "Settings") -> list[dict[str, Any]]:
    """
    Build an ordered list of LLM provider configs (groq → gemini → openrouter → openai).
    Each entry: {provider, base_url, api_key, model, headers, timeout}.
    """
    timeout = max(1.0, float(settings.stage7_openai_timeout_seconds))
    chain: list[dict[str, Any]] = []

    if str(settings.groq_api_key or "").strip():
        chain.append({
            "provider": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": str(settings.groq_api_key).strip(),
            "model": str(settings.stage7_groq_model or "llama-3.3-70b-versatile"),
            "headers": {},
            "timeout": timeout,
        })

    if str(settings.gemini_api_key or "").strip():
        chain.append({
            "provider": "gemini",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "api_key": str(settings.gemini_api_key).strip(),
            "model": str(settings.stage7_gemini_model or "gemini-2.5-flash"),
            "headers": {},
            "timeout": timeout,
        })

    if str(settings.openrouter_api_key or "").strip() and bool(getattr(settings, "stage7_openrouter_enabled", True)):
        headers: dict[str, str] = {}
        if str(settings.stage7_openrouter_http_referer or "").strip():
            headers["HTTP-Referer"] = str(settings.stage7_openrouter_http_referer).strip()
        if str(settings.stage7_openrouter_x_title or "").strip():
            headers["X-Title"] = str(settings.stage7_openrouter_x_title).strip()
        chain.append({
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": str(settings.openrouter_api_key).strip(),
            "model": str(settings.stage7_openrouter_model or "google/gemini-2.5-flash-preview"),
            "headers": headers,
            "timeout": timeout,
        })

    if str(getattr(settings, "stage7_openai_api_key", "") or "").strip():
        chain.append({
            "provider": "openai",
            "base_url": str(settings.stage7_openai_api_base_url or "https://api.openai.com/v1"),
            "api_key": str(settings.stage7_openai_api_key).strip(),
            "model": str(settings.stage7_openai_model or "gpt-4o-mini"),
            "headers": {},
            "timeout": timeout,
        })

    return chain
