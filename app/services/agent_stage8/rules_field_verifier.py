from __future__ import annotations

from dataclasses import dataclass

from app.models.models import Market, Platform


AMBIGUITY_WEIGHTS: dict[str, float] = {
    "sole discretion": 0.50,
    "at our discretion": 0.50,
    "final determination": 0.45,
    "editorial decision": 0.45,
    "team decision": 0.40,
    "subjective": 0.35,
    "if deemed": 0.30,
    "consensus": 0.25,
    "may be resolved by": 0.25,
    "if unavailable": 0.20,
    "if applicable": 0.15,
    "in the event of": 0.10,
}

PLATFORM_RESOLUTION_CONFIDENCE = {
    "POLYMARKET": 0.85,
    "MANIFOLD": 0.60,
    "METACULUS": 0.75,
}


@dataclass
class RulesFieldResult:
    ambiguity_score: float
    resolution_source_confidence: float
    dispute_risk_flag: bool
    reason_codes: list[str]


def compute_rules_ambiguity_score(rules_text: str | None) -> float:
    text = (rules_text or "").lower()
    score = sum(weight for token, weight in AMBIGUITY_WEIGHTS.items() if token in text)
    if not any(source in text for source in ["coinmarketcap", "coingecko", "reuters", "ap ", "official"]):
        score += 0.20
    if any(word in text for word in ["by ", "before ", "at "]) and not any(tz in text for tz in ["utc", "est", "gmt"]):
        score += 0.10
    return min(1.0, score)


def _platform_name(platform: Platform | None) -> str:
    if not platform or not platform.name:
        return ""
    return str(platform.name).upper()


def evaluate_rules_fields(
    market: Market,
    *,
    platform: Platform | None,
    category_policy: dict[str, float],
) -> RulesFieldResult:
    ambiguity_score = compute_rules_ambiguity_score(market.rules_text)
    platform_name = _platform_name(platform)
    resolution_conf = PLATFORM_RESOLUTION_CONFIDENCE.get(platform_name, 0.70)
    dispute_risk_flag = bool(
        ambiguity_score >= float(category_policy.get("max_rules_ambiguity_score", 1.0))
        or (platform_name == "MANIFOLD" and resolution_conf < 0.70)
    )
    reason_codes: list[str] = []
    if ambiguity_score >= float(category_policy.get("max_rules_ambiguity_score", 1.0)):
        reason_codes.append("rules_ambiguity_hard_block")
    if platform_name == "MANIFOLD" and resolution_conf < 0.70:
        reason_codes.append("rules_resolution_low_confidence")
    return RulesFieldResult(
        ambiguity_score=ambiguity_score,
        resolution_source_confidence=resolution_conf,
        dispute_risk_flag=dispute_risk_flag,
        reason_codes=reason_codes,
    )
