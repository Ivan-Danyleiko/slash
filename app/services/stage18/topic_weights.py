"""
Stage18 Workstream B: Topic Reliability Weights

Builds weight matrix: weight(platform, category) from resolved SignalHistory rows.
Uses Bayesian shrinkage toward global platform quality when n < min_n.

  raw_quality = resolved_success rate(platform, category)
  shrink(n)   = n / (n + min_n)
  w = shrink(n) * raw_quality + (1 - shrink(n)) * global_platform_quality
  w_final = clip(w, 0.1, 1.0)
"""
from __future__ import annotations
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _safe_rate(hits: int, total: int) -> float:
    return hits / total if total > 0 else 0.5


def build_topic_weight_matrix(
    db: "Session",
    *,
    min_n: int = 100,
) -> dict[tuple[str, str], float]:
    """
    Returns dict mapping (platform, category) -> weight in [0.1, 1.0].
    """
    from sqlalchemy import select
    from app.models.models import SignalHistory, Market

    rows = list(db.execute(
        select(
            SignalHistory.platform,
            Market.category,
            SignalHistory.resolved_success,
        )
        .join(Market, SignalHistory.market_id == Market.id)
        .where(SignalHistory.resolved_success.is_not(None))
    ))

    # Aggregate in Python (simpler, avoids dialect-specific cast issues)
    cell_counts: dict[tuple[str, str], list] = defaultdict(lambda: [0, 0])
    platform_counts: dict[str, list] = defaultdict(lambda: [0, 0])

    for row in rows:
        platform = str(row.platform or "UNKNOWN").upper()
        category = str(row.category or "other").lower()
        success = int(bool(row.resolved_success))
        cell_counts[(platform, category)][0] += 1
        cell_counts[(platform, category)][1] += success
        platform_counts[platform][0] += 1
        platform_counts[platform][1] += success

    global_platform_quality: dict[str, float] = {
        p: _safe_rate(h, n) for p, (n, h) in platform_counts.items()
    }
    all_n = sum(n for n, _ in platform_counts.values())
    all_h = sum(h for _, h in platform_counts.values())
    global_quality_all = _safe_rate(all_h, all_n)

    weights: dict[tuple[str, str], float] = {}
    for (platform, category), (n, hits) in cell_counts.items():
        raw_quality = _safe_rate(hits, n)
        global_q = global_platform_quality.get(platform, global_quality_all)
        shrink = n / (n + min_n)
        w = shrink * raw_quality + (1.0 - shrink) * global_q
        weights[(platform, category)] = max(0.1, min(1.0, w))

    return weights


def get_platform_weight(
    weights: dict[tuple[str, str], float],
    platform: str,
    category: str | None,
) -> float:
    """Weight lookup with platform-level fallback, then 1.0."""
    cat = str(category or "other").lower()
    plat = str(platform or "UNKNOWN").upper()
    w = weights.get((plat, cat))
    if w is not None:
        return w
    vals = [v for (p, _), v in weights.items() if p == plat]
    return sum(vals) / len(vals) if vals else 1.0


def weighted_divergence(
    prob_a: float,
    prob_b: float,
    weight_a: float,
    weight_b: float,
) -> float:
    """
    Divergence scaled by harmonic mean of platform weights.
    Both platforms need to be reliable for the signal to be strong.
    Result is in [0, 1].
    """
    gross = abs(prob_a - prob_b)
    if weight_a + weight_b < 1e-9:
        return gross
    # Harmonic mean of weights (favors balanced reliability)
    hmean = 2.0 * weight_a * weight_b / (weight_a + weight_b)
    return gross * hmean
