from app.core.config import get_settings
from app.models.models import Market


class RulesRiskAnalyzer:
    def __init__(self) -> None:
        settings = get_settings()
        self.keywords = [item.strip().lower() for item in settings.rules_risk_keywords.split(",") if item.strip()]

    def analyze(self, market: Market) -> dict:
        text = (market.rules_text or "").lower()
        matches = [kw for kw in self.keywords if kw in text]
        score = min(1.0, len(matches) * 0.2)
        if score > 0.6:
            level = "HIGH"
        elif score > 0.25:
            level = "MEDIUM"
        else:
            level = "LOW"
        return {
            "score": round(score, 3),
            "level": level,
            "matched_flags": matches,
            "explanation": "Keyword-based deterministic rules risk analysis.",
        }
