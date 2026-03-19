from __future__ import annotations

import json
import re
from typing import Any
from urllib import request, error

from app.services.agent_stage7.stack_adapters.base import Stage7AdapterInput


def _parse_reason_codes(reasons: Any) -> list[str] | None:
    """Normalize reason_codes field — LLMs sometimes return a string instead of list."""
    if isinstance(reasons, list):
        parsed = [str(x) for x in reasons if str(x).strip()]
        return parsed if parsed else None
    if isinstance(reasons, str) and reasons.strip():
        # comma-separated string fallback
        parts = [p.strip() for p in reasons.replace(";", ",").split(",") if p.strip()]
        return parts if parts else None
    return None


def _safe_parse_decision(raw_text: str) -> tuple[str, list[str]]:
    decision = "SKIP"
    reason_codes: list[str] = ["adapter_parse_fallback"]
    text = str(raw_text or "").strip()

    def _apply(obj: dict) -> bool:
        nonlocal decision, reason_codes
        candidate = str(obj.get("decision") or "SKIP").upper()
        if candidate in {"KEEP", "MODIFY", "REMOVE", "SKIP"}:
            decision = candidate
        parsed = _parse_reason_codes(obj.get("reason_codes"))
        if parsed:
            reason_codes = parsed
        elif decision != "SKIP":
            reason_codes = ["adapter_no_reason_codes"]
        return decision != "SKIP"

    try:
        obj = json.loads(text)
        _apply(obj)
        return decision, reason_codes
    except Exception:
        pass

    # LLMs often return prose + fenced JSON. Try best-effort object extraction.
    match = re.search(r"\{[\s\S]*?\}", text)
    if match:
        try:
            obj = json.loads(match.group(0))
            _apply(obj)
        except Exception:
            pass
    return decision, reason_codes


class OpenAICompatibleAdapter:
    name = "plain_llm_api_real"

    def __init__(
        self,
        *,
        api_base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 12.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        # Derive a short name from the host for logging (e.g. "groq", "googleapis", "openrouter")
        try:
            from urllib.parse import urlparse
            host = urlparse(api_base_url).hostname or "unknown"
            parts = host.split(".")
            short = parts[-2] if len(parts) >= 2 else host
            self.name = f"plain_llm_api:{short}"
        except Exception:
            pass
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.extra_headers = {str(k): str(v) for k, v in (extra_headers or {}).items() if str(v).strip()}

    def _request_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "prediction-market-scanner/1.0",
        }
        headers.update(self.extra_headers)
        req = request.Request(url, data=data, method="POST", headers=headers)
        with request.urlopen(req, timeout=self.timeout_seconds) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8"))

    _SYSTEM_PROMPT = (
        "Prediction market signal evaluator. Shadow/dry-run mode — collect data, not filter.\n"
        "Respond ONLY with JSON (no markdown): "
        '{"decision":"<KEEP|MODIFY|REMOVE|SKIP>","reason_codes":["<code>"]}\n\n'
        "RULES:\n"
        "KEEP: EV>0 or uncertain, kelly>0.01, no hard contradictions\n"
        "MODIFY: marginal signal, adjust confidence\n"
        "REMOVE: consensus_spread>0.25 AND EV<0 (direct contradiction)\n"
        "SKIP: EV<0 AND (liquidity<0.2 OR contradictions>=2 OR walk_forward=UNSTABLE)\n\n"
        "KEY: EV = avg_win*win_rate - avg_loss*(1-win_rate). Kelly>0.02 = meaningful.\n"
        "DIVERGENCE signal: divergence_score>0.10 matters. "
        "RULES_RISK: check ambiguity_count. "
        "ARBITRAGE: need liquidity_score>0.5.\n"
        "Shadow mode: default KEEP when uncertain. False positives are OK."
    )

    def decide(self, payload: Stage7AdapterInput) -> dict[str, Any]:
        # In shadow mode, let LLM evaluate even borderline signals for data collection.
        # In production mode (not shadow), hard-skip on gate failure.
        if not payload.internal_gate_passed and not payload.is_shadow_mode:
            return {
                "decision": "SKIP",
                "reason_codes": ["adapter_internal_gate_failed"],
                "provider_fingerprint": "openai_compatible",
                "simulated_latency_ms": 0.0,
            }
        prompt_input = {
            # Signal identity
            "signal_type": str(payload.signal_type or ""),
            "market_title": str(payload.market_title or "")[:120],
            "platform": str(payload.platform or ""),
            "days_to_resolution": int(payload.days_to_resolution),
            # Base decision from policy
            "base_decision": payload.base_decision,
            "internal_gate_passed": payload.internal_gate_passed,
            # EV & Kelly
            "expected_ev_pct": round(float(payload.expected_ev_pct), 4),
            "kelly_fraction": round(float(payload.kelly_fraction), 4),
            "market_prob": round(float(payload.market_prob), 3),
            "divergence_score": round(float(payload.divergence_score), 4),
            "liquidity_score": round(float(payload.liquidity_score), 3),
            # Historical performance
            "win_rate_90d": round(float(payload.win_rate_90d), 3),
            "avg_win_90d": round(float(payload.avg_win_90d), 4),
            "avg_loss_90d": round(float(payload.avg_loss_90d), 4),
            "n_samples_90d": int(payload.n_samples_90d),
            # Cross-platform
            "consensus_spread": round(float(payload.consensus_spread), 3),
            "consensus_platforms": int(payload.consensus_platforms),
            "contradictions_count": payload.contradictions_count,
            "ambiguity_count": payload.ambiguity_count,
            # Quality
            "walk_forward_verdict": str(payload.walk_forward_verdict or "UNKNOWN"),
            "is_shadow_mode": bool(payload.is_shadow_mode),
            # Portfolio context
            "portfolio_open_positions": int(payload.portfolio_open_positions),
            "portfolio_exposure_pct": round(float(payload.portfolio_exposure_pct), 4),
            "portfolio_cash_usd": round(float(payload.portfolio_cash_usd), 2),
            "portfolio_category_breakdown": dict(payload.portfolio_category_breakdown or {}),
            "portfolio_bucket_breakdown_pct": dict(payload.portfolio_bucket_breakdown_pct or {}),
            # Historical RAG
            "rag_similar_count": int(payload.rag_similar_count),
            "rag_similar_yes_rate": round(float(payload.rag_similar_yes_rate), 4),
            "rag_summary": str(payload.rag_summary or ""),
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(prompt_input, ensure_ascii=True)},
            ],
            "temperature": 0,
            "max_tokens": 1500,
        }
        def _extract_chat_text(resp: dict[str, Any]) -> str:
            choices = resp.get("choices")
            if isinstance(choices, list) and choices:
                return str((((choices[0] or {}).get("message") or {}).get("content")) or "")
            return ""

        def _extract_responses_text(resp: dict[str, Any]) -> str:
            text = ""
            output = resp.get("output")
            if isinstance(output, list):
                for block in output:
                    content = block.get("content")
                    if isinstance(content, list):
                        for c in content:
                            if c.get("type") == "output_text":
                                text += str(c.get("text") or "")
            return text or str(resp.get("output_text") or "")

        # Try /chat/completions first — supported by all providers (Groq, Gemini, OpenRouter, OpenAI).
        try:
            resp = self._request_json(f"{self.api_base_url}/chat/completions", body)
            text_out = _extract_chat_text(resp)
            if not text_out.strip():
                return {
                    "decision": "SKIP",
                    "reason_codes": ["adapter_empty_output"],
                    "provider_fingerprint": str(resp.get("system_fingerprint") or "openai_compatible_chat"),
                    "simulated_latency_ms": 0.0,
                }
            decision, reason_codes = _safe_parse_decision(text_out)
            return {
                "decision": decision,
                "reason_codes": reason_codes,
                "provider_fingerprint": str(resp.get("system_fingerprint") or "openai_compatible_chat"),
                "simulated_latency_ms": 0.0,
            }
        except error.HTTPError as exc:
            # Parse Retry-After header (Groq/Gemini include it on 429).
            _retry_after = 0.0
            try:
                _ra = (exc.headers or {}).get("Retry-After") or ""
                _retry_after = max(0.0, float(str(_ra).strip())) if str(_ra).strip() else 0.0
            except Exception:
                pass
            if exc.code == 429:
                return {
                    "decision": "SKIP",
                    "reason_codes": ["adapter_http_error:429"],
                    "provider_fingerprint": "openai_compatible_quota",
                    "simulated_latency_ms": 0.0,
                    "retry_after_seconds": _retry_after,
                }
            # Fall back to OpenAI /responses endpoint (newer Responses API format).
            if exc.code in {400, 403, 404, 405}:
                responses_body = {
                    "model": self.model,
                    "input": json.dumps(prompt_input, ensure_ascii=True),
                    "reasoning": {"effort": "low"},
                    "text": {"verbosity": "low"},
                    "max_output_tokens": 120,
                    "temperature": 0,
                }
                try:
                    resp = self._request_json(f"{self.api_base_url}/responses", responses_body)
                    text_out = _extract_responses_text(resp)
                    if not text_out.strip():
                        return {
                            "decision": "SKIP",
                            "reason_codes": ["adapter_empty_output"],
                            "provider_fingerprint": str(resp.get("system_fingerprint") or "openai_compatible"),
                            "simulated_latency_ms": 0.0,
                        }
                    decision, reason_codes = _safe_parse_decision(text_out)
                    return {
                        "decision": decision,
                        "reason_codes": reason_codes,
                        "provider_fingerprint": str(resp.get("system_fingerprint") or "openai_compatible"),
                        "simulated_latency_ms": 0.0,
                    }
                except error.HTTPError as resp_exc:
                    return {
                        "decision": "SKIP",
                        "reason_codes": [f"adapter_http_error:{resp_exc.code}"],
                        "provider_fingerprint": "openai_compatible_error",
                        "simulated_latency_ms": 0.0,
                    }
                except Exception:
                    return {
                        "decision": "SKIP",
                        "reason_codes": ["adapter_transport_error"],
                        "provider_fingerprint": "openai_compatible_error",
                        "simulated_latency_ms": 0.0,
                    }
            return {
                "decision": "SKIP",
                "reason_codes": [f"adapter_http_error:{exc.code}"],
                "provider_fingerprint": "openai_compatible_error",
                "simulated_latency_ms": 0.0,
            }
        except Exception:
            return {
                "decision": "SKIP",
                "reason_codes": ["adapter_transport_error"],
                "provider_fingerprint": "openai_compatible_error",
                "simulated_latency_ms": 0.0,
            }
