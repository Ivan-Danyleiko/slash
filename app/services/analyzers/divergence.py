from app.models.models import Market


class DivergenceDetector:
    def divergence(self, market_a: Market, market_b: Market) -> float | None:
        if market_a.probability_yes is None or market_b.probability_yes is None:
            return None
        return abs(market_a.probability_yes - market_b.probability_yes)
