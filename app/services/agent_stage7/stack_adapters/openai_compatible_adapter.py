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
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.extra_headers = {str(k): str(v) for k, v in (extra_headers or {}).items() if str(v).strip()}

    def _request_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)
        req = request.Request(url, data=data, method="POST", headers=headers)
        with request.urlopen(req, timeout=self.timeout_seconds) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8"))

    def decide(self, payload: Stage7AdapterInput) -> dict[str, Any]:
        if not payload.internal_gate_passed:
            return {
                "decision": "SKIP",
                "reason_codes": ["adapter_internal_gate_failed"],
                "provider_fingerprint": "openai_compatible",
                "simulated_latency_ms": 0.0,
            }
        prompt = {
            "instruction": "Return strict JSON only: {decision, reason_codes}. No prose.",
            "rules": {
                "allowed_decisions": ["KEEP", "MODIFY", "REMOVE", "SKIP"],
                "prefer_conservative": True,
            },
            "input": {
                "signal_id": payload.signal_id,
                "base_decision": payload.base_decision,
                "contradictions_count": payload.contradictions_count,
                "ambiguity_count": payload.ambiguity_count,
            },
        }
        body = {
            "model": self.model,
            "input": json.dumps(prompt, ensure_ascii=True),
            "reasoning": {"effort": "low"},
            "text": {"verbosity": "low"},
            "max_output_tokens": 80,
            "temperature": 0,
        }
        try:
            payload_resp = self._request_json(f"{self.api_base_url}/responses", body)
            text_out = ""
            output = payload_resp.get("output")
            if isinstance(output, list):
                for block in output:
                    content = block.get("content")
                    if isinstance(content, list):
                        for c in content:
                            if c.get("type") == "output_text":
                                text_out += str(c.get("text") or "")
            if not text_out:
                text_out = str(payload_resp.get("output_text") or "")
            decision, reason_codes = _safe_parse_decision(text_out)
            return {
                "decision": decision,
                "reason_codes": reason_codes,
                "provider_fingerprint": str(payload_resp.get("system_fingerprint") or "openai_compatible"),
                "simulated_latency_ms": 0.0,
            }
        except error.HTTPError as exc:
            # Some OpenAI-compatible providers support only /chat/completions.
            # 400: bad request (e.g. unsupported endpoint format)
            # 403: forbidden / endpoint not supported (e.g. Groq on /responses)
            # 404/405: endpoint not found / method not allowed
            if exc.code in {400, 403, 404, 405}:
                try:
                    chat_body = {
                        "model": self.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a decision engine. "
                                    "Respond with a single JSON object only — no prose, no markdown fences. "
                                    'Format: {"decision": "<KEEP|MODIFY|REMOVE|SKIP>", "reason_codes": ["<code>"]}'
                                ),
                            },
                            {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
                        ],
                        "temperature": 0,
                        "max_tokens": 120,
                    }
                    payload_resp = self._request_json(f"{self.api_base_url}/chat/completions", chat_body)
                    choices = payload_resp.get("choices")
                    text_out = ""
                    if isinstance(choices, list) and choices:
                        text_out = str((((choices[0] or {}).get("message") or {}).get("content")) or "")
                    if not text_out.strip():
                        return {
                            "decision": "SKIP",
                            "reason_codes": ["adapter_empty_output"],
                            "provider_fingerprint": str(
                                payload_resp.get("system_fingerprint") or "openai_compatible_chat"
                            ),
                            "simulated_latency_ms": 0.0,
                        }
                    decision, reason_codes = _safe_parse_decision(text_out)
                    return {
                        "decision": decision,
                        "reason_codes": reason_codes,
                        "provider_fingerprint": str(payload_resp.get("system_fingerprint") or "openai_compatible_chat"),
                        "simulated_latency_ms": 0.0,
                    }
                except error.HTTPError as chat_exc:
                    return {
                        "decision": "SKIP",
                        "reason_codes": [f"adapter_http_error:{chat_exc.code}"],
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
