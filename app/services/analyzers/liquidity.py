from math import log1p

from app.models.models import Market


class LiquidityAnalyzer:
    def analyze(self, market: Market) -> dict:
        liquidity = market.liquidity_value or 0
        volume = market.volume_24h or 0
        # Smooth normalization prevents hard-saturation on platforms with fixed/default liquidity constants.
        liq_norm = min(1.0, log1p(max(0.0, liquidity)) / log1p(20000))
        vol_norm = min(1.0, log1p(max(0.0, volume)) / log1p(5000))
        harmonic = (2 * liq_norm * vol_norm / (liq_norm + vol_norm)) if (liq_norm + vol_norm) else 0.0
        score = min(1.0, (0.5 * (0.45 * liq_norm + 0.55 * vol_norm)) + (0.5 * harmonic))
        if score > 0.7:
            level = "HIGH"
        elif score > 0.3:
            level = "MEDIUM"
        else:
            level = "LOW"
        return {
            "score": round(score, 3),
            "level": level,
            "explanation": (
                "Log-scaled liquidity proxy; "
                f"liq_norm={liq_norm:.3f}, vol_norm={vol_norm:.3f}, harmonic={harmonic:.3f}"
            ),
        }
