from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import re
from typing import Any
from urllib import error, request
import hashlib

from app.core.config import Settings
from app.models.models import Market, Signal
from app.utils.llm_providers import build_provider_chain as _build_provider_chain

_CACHE_TTL_SECONDS = 3600
_TAIL_LLM_CACHE: dict[str, tuple[datetime, dict[str, Any]]] = {}

_TAIL_SYSTEM_PROMPT = (
    "You are a deterministic Tail Event verifier for prediction markets.\n"
    "Task: verify resolution clarity and whether market overreaction narrative is fading.\n"
    "NEVER forecast EV directly. Focus only on resolution ambiguity, contradiction, and direction sanity.\n"
    "Return STRICT JSON only:\n"
    '{"decision":"KEEP|SKIP","direction":"YES|NO","confidence_adjustment":0.0,"reason_codes":["..."]}\n'
    "Rules:\n"
    "- If resolution criteria ambiguous/subjective -> SKIP\n"
    "- If event wording is clear and no contradictions -> KEEP\n"
    "- Keep direction conservative (usually opposite panic for tail narrative fade)\n"
    "- confidence_adjustment in [-0.30, +0.30]\n"
)


def _prompt_version_hash(version: str) -> str:
    return hashlib.sha256(str(version or "").encode("utf-8")).hexdigest()[:8]


def _input_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()



def _request_json(*, base_url: str, api_key: str, headers_extra: dict[str, str], body: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=True).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "prediction-market-scanner/1.0",
    }
    headers.update({str(k): str(v) for k, v in headers_extra.items()})
    req = request.Request(f"{base_url.rstrip('/')}/chat/completions", data=data, method="POST", headers=headers)
    with request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return json.loads(resp.read().decode("utf-8"))


def _parse_json_payload(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*?\}", text)
    if not m:
        return None
    try:
        payload = json.loads(m.group(0))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _extract_response_text(resp: dict[str, Any]) -> str:
    choices = resp.get("choices")
    if isinstance(choices, list) and choices:
        msg = (choices[0] or {}).get("message") or {}
        return str(msg.get("content") or "")
    return ""


def review_tail_narrative(
    *,
    settings: Settings,
    signal: Signal,
    market: Market,
    tail_category: str,
    market_prob: float,
    our_prob: float,
) -> dict[str, Any]:
    prompt_hash = _prompt_version_hash(str(settings.signal_tail_llm_prompt_version))
    payload = {
        "signal_id": int(signal.id or 0),
        "market_id": int(market.id or 0),
        "tail_category": str(tail_category or ""),
        "signal_title": str(signal.title or ""),
        "market_title": str(market.title or ""),
        "description": str(market.description or "")[:1000],
        "rules_text": str(market.rules_text or "")[:2000],
        "market_prob_yes": round(float(market_prob), 6),
        "our_prob_yes": round(float(our_prob), 6),
        "signal_direction": str(signal.signal_direction or "YES").upper(),
        "prompt_version_hash": prompt_hash,
    }
    ih = _input_hash(payload)
    now = datetime.now(UTC)
    cached = _TAIL_LLM_CACHE.get(ih)
    if cached is not None:
        exp, out = cached
        if exp >= now:
            return {**out, "cache_hit": True}
        _TAIL_LLM_CACHE.pop(ih, None)

    fallback_direction = str(signal.signal_direction or "YES").upper()
    if fallback_direction not in {"YES", "NO"}:
        fallback_direction = "YES"
    fallback = {
        "decision": "SKIP",
        "direction": fallback_direction,
        "confidence_adjustment": 0.0,
        "reason_codes": ["tail_llm_unavailable_fallback"],
        "provider": "none",
        "model_version": "none",
        "input_hash": ih,
        "prompt_version_hash": prompt_hash,
        "cache_hit": False,
    }
    if not bool(settings.stage7_agent_real_calls_enabled):
        _TAIL_LLM_CACHE[ih] = (now + timedelta(seconds=_CACHE_TTL_SECONDS), fallback)
        return fallback

    chain = _build_provider_chain(settings)
    if not chain:
        _TAIL_LLM_CACHE[ih] = (now + timedelta(seconds=_CACHE_TTL_SECONDS), fallback)
        return fallback

    user_content = json.dumps(payload, ensure_ascii=True)
    for provider in chain:
        body = {
            "model": str(provider["model"]),
            "messages": [
                {"role": "system", "content": _TAIL_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "max_tokens": 300,
        }
        try:
            resp = _request_json(
                base_url=str(provider["base_url"]),
                api_key=str(provider["api_key"]),
                headers_extra=dict(provider.get("headers") or {}),
                body=body,
                timeout=float(provider["timeout"]),
            )
            text = _extract_response_text(resp)
            parsed = _parse_json_payload(text) or {}
            decision = str(parsed.get("decision") or "KEEP").upper()
            direction = str(parsed.get("direction") or fallback_direction).upper()
            if decision not in {"KEEP", "SKIP"}:
                decision = "KEEP"
            if direction not in {"YES", "NO"}:
                direction = fallback_direction
            conf_adj = float(parsed.get("confidence_adjustment") or 0.0)
            conf_adj = max(-0.30, min(0.30, conf_adj))
            reason_codes_raw = parsed.get("reason_codes")
            reason_codes = [str(x) for x in reason_codes_raw] if isinstance(reason_codes_raw, list) else []
            if not reason_codes:
                reason_codes = ["tail_llm_decision"]
            out = {
                "decision": decision,
                "direction": direction,
                "confidence_adjustment": conf_adj,
                "reason_codes": reason_codes,
                "provider": str(provider["provider"]),
                "model_version": str(provider["model"]),
                "input_hash": ih,
                "prompt_version_hash": prompt_hash,
                "cache_hit": False,
            }
            _TAIL_LLM_CACHE[ih] = (now + timedelta(seconds=_CACHE_TTL_SECONDS), out)
            return out
        except error.HTTPError as exc:
            fallback["reason_codes"] = [f"tail_llm_http_error:{int(exc.code)}"]
            continue
        except Exception:
            fallback["reason_codes"] = ["tail_llm_transport_error"]
            continue

    _TAIL_LLM_CACHE[ih] = (now + timedelta(seconds=_CACHE_TTL_SECONDS), fallback)
    return fallback
