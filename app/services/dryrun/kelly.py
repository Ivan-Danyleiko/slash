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
    max_total_exposure: float = 0.80,
) -> float:
    """Scale Kelly by remaining portfolio capacity.

    Linear scale-down starts at 80% of max_exposure and reaches 0 at 100%.
    Example (max_exposure=0.80): scale starts at 0.64 notional pct.
    """
    remaining_capacity = max(0.0, float(max_total_exposure) - float(total_open_notional_pct))
    if remaining_capacity <= 0.0:
        return 0.0
    adjusted = float(base_kelly)
    fill_ratio = float(total_open_notional_pct) / float(max_total_exposure)
    if fill_ratio > 0.8:
        # Linear scale: 1.0 at 80% fill → 0.0 at 100% fill
        scale = (1.0 - fill_ratio) / 0.2
        adjusted *= max(0.0, scale)
    return max(0.0, min(adjusted, remaining_capacity))

