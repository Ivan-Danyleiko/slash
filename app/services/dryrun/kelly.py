from __future__ import annotations


def kelly_fraction(
    *,
    market_price: float,
    our_prob: float,
    alpha: float = 0.25,
    max_fraction: float = 0.10,
) -> float:
    """Fractional Kelly for binary markets."""
    p = float(market_price)
    q_true = float(our_prob)
    if p <= 0.0 or p >= 1.0:
        return 0.0
    if q_true <= p:
        return 0.0
    b = (1.0 - p) / p
    q_loss = 1.0 - q_true
    f_star = (q_true * b - q_loss) / b
    if f_star <= 0.0:
        return 0.0
    return max(0.0, min(float(alpha) * f_star, float(max_fraction)))


def portfolio_kelly_adjustment(
    *,
    base_kelly: float,
    total_open_notional_pct: float,
    max_total_exposure: float = 0.40,
) -> float:
    """Scale Kelly by remaining portfolio capacity."""
    remaining_capacity = max(0.0, float(max_total_exposure) - float(total_open_notional_pct))
    if remaining_capacity <= 0.0:
        return 0.0
    adjusted = float(base_kelly)
    if float(total_open_notional_pct) > float(max_total_exposure) * 0.7:
        scale = 1.0 - (float(total_open_notional_pct) / float(max_total_exposure))
        adjusted *= max(0.0, scale)
    return max(0.0, min(adjusted, remaining_capacity))

