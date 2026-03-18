from __future__ import annotations

import math


def composite_score(
    *,
    daily_ev_pct: float,
    spread: float,
    volume_usd: float,
    confidence: float,
    days_to_resolution: float,
    kelly_fraction: float,
    is_clob: bool,
) -> float:
    """Deterministic composite entry score for Stage 15."""
    ev_score = min(max(float(daily_ev_pct), 0.0) / 0.005, 1.0)
    spread_score = max(0.0, 1.0 - max(float(spread), 0.0) / 0.08)
    liq_score = min(math.log10(max(float(volume_usd), 1.0)) / 5.0, 1.0)
    conf_score = max(0.0, min(float(confidence), 1.0))
    time_score = max(0.1, 1.0 - max(float(days_to_resolution), 0.0) / 200.0)
    kelly_score = min(max(float(kelly_fraction), 0.0) / 0.25, 1.0)
    clob_bonus = 0.15 if bool(is_clob) else 0.0

    score = (
        0.30 * ev_score
        + 0.20 * kelly_score
        + 0.20 * spread_score
        + 0.15 * conf_score
        + 0.10 * time_score
        + 0.05 * liq_score
        + clob_bonus
    )
    return round(score, 4)

