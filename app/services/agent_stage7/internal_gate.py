from __future__ import annotations

from typing import Any

from app.core.config import Settings


_PROFILES: dict[str, dict[str, float]] = {
    "strict": {"min_confidence": 0.50, "min_liquidity": 0.60, "min_ev": 0.010},
    "balanced": {"min_confidence": 0.40, "min_liquidity": 0.50, "min_ev": 0.005},
    "permissive": {"min_confidence": 0.30, "min_liquidity": 0.40, "min_ev": 0.000},
}


def evaluate_internal_gate(row: dict[str, Any], *, settings: Settings) -> dict[str, Any]:
    profile_key = str(settings.stage7_agent_internal_gate_profile or "balanced").strip().lower()
    profile = _PROFILES.get(profile_key, _PROFILES["balanced"])

    confidence = float(row.get("confidence") or 0.0)
    liquidity = float(row.get("liquidity") or 0.0)
    expected_ev_pct = float(row.get("expected_ev_pct") or 0.0)

    reasons: list[str] = []
    if confidence < profile["min_confidence"]:
        reasons.append("internal_low_confidence")
    if liquidity < profile["min_liquidity"]:
        reasons.append("internal_low_liquidity")
    if expected_ev_pct < profile["min_ev"]:
        reasons.append("internal_low_ev")

    # Internal score is a compact proxy for gating confidence.
    score = 0.0
    score += min(1.0, confidence / max(profile["min_confidence"], 1e-6)) * 0.40
    score += min(1.0, liquidity / max(profile["min_liquidity"], 1e-6)) * 0.35
    if profile["min_ev"] > 0:
        score += min(1.0, expected_ev_pct / profile["min_ev"]) * 0.25
    else:
        score += (1.0 if expected_ev_pct >= 0 else 0.0) * 0.25

    return {
        "profile": profile_key,
        "thresholds": profile,
        "score": round(score, 6),
        "passed": len(reasons) == 0,
        "reasons": reasons,
    }

