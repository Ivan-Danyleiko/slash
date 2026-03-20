"""
Stage18 Workstream C: Multi-Outcome Structural Arb Detector

Groups markets by event_group_id, computes sum(probability_yes) across
mutually-exclusive outcomes, and flags underround (sum < 1) as arb opportunities.

Signal: STRUCTURAL_ARB_CANDIDATE
  underround = 1 - sum_prob  (positive = arb opportunity)
  overround  = sum_prob - 1  (positive = house edge / risk warning)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class StructuralArbGroup:
    event_group_id: str
    markets: list  # list[Market]
    sum_prob: float
    underround: float
    overround: float
    min_liquidity: float
    is_neg_risk: bool
    category: str | None
    platform_names: list[str]
    legs: list[dict]


def detect_structural_arb(
    db: "Session",
    *,
    min_underround: float = 0.015,
    max_group_size: int = 8,
    min_leg_liquidity: float = 0.10,
    min_group_size: int = 2,
    max_days_to_resolution: int | None = None,
) -> list[StructuralArbGroup]:
    """
    Find event_group_id groups where sum(probability_yes) < 1 - min_underround.

    Only considers groups where ALL legs pass the liquidity filter.
    neg_risk groups get a note but are not excluded.
    """
    from sqlalchemy import select
    from app.models.models import Market, LiquidityAnalysis, Platform

    now = datetime.now(UTC)
    deadline_cutoff = (
        now + timedelta(days=max_days_to_resolution)
        if max_days_to_resolution
        else None
    )

    # Load active markets with event_group_id
    q = (
        select(Market)
        .where(
            Market.event_group_id.is_not(None),
            Market.probability_yes.is_not(None),
            Market.status.notin_(["resolved", "closed", "cancelled"]),
        )
    )
    if deadline_cutoff:
        q = q.where(
            (Market.resolution_time.is_(None)) | (Market.resolution_time <= deadline_cutoff)
        )
    markets = list(db.scalars(q))

    if not markets:
        return []

    # Load liquidity scores
    market_ids = [m.id for m in markets]
    liq_rows = list(
        db.scalars(select(LiquidityAnalysis).where(LiquidityAnalysis.market_id.in_(market_ids)))
    )
    liq_by_id = {r.market_id: r.score for r in liq_rows}

    # Load platform names
    platform_rows = list(db.scalars(select(Platform)))
    platform_by_id = {p.id: p.name for p in platform_rows}

    # Group markets by event_group_id
    groups: dict[str, list] = {}
    for m in markets:
        if m.event_group_id:
            groups.setdefault(m.event_group_id, []).append(m)

    results: list[StructuralArbGroup] = []
    for gid, group_markets in groups.items():
        if len(group_markets) < min_group_size or len(group_markets) > max_group_size:
            continue

        # All legs must have sufficient liquidity
        liq_scores = [liq_by_id.get(m.id, 0.0) for m in group_markets]
        if any(liq < min_leg_liquidity for liq in liq_scores):
            continue

        probs = [float(m.probability_yes or 0.0) for m in group_markets]
        sum_prob = sum(probs)
        underround = 1.0 - sum_prob
        overround = sum_prob - 1.0

        if underround < min_underround:
            continue

        min_liq = min(liq_scores)
        is_neg_risk = any(bool(m.is_neg_risk) for m in group_markets)
        categories = [m.category for m in group_markets if m.category]
        category = categories[0] if categories else None
        plat_names = list({str(platform_by_id.get(m.platform_id) or "UNKNOWN") for m in group_markets})

        legs = [
            {
                "market_id": m.id,
                "title": m.title,
                "probability_yes": float(m.probability_yes or 0.0),
                "liquidity_score": liq_by_id.get(m.id, 0.0),
                "platform": str(platform_by_id.get(m.platform_id) or "UNKNOWN"),
                "is_neg_risk": bool(m.is_neg_risk),
            }
            for m in group_markets
        ]

        results.append(
            StructuralArbGroup(
                event_group_id=gid,
                markets=group_markets,
                sum_prob=round(sum_prob, 6),
                underround=round(underround, 6),
                overround=round(overround, 6),
                min_liquidity=round(min_liq, 4),
                is_neg_risk=is_neg_risk,
                category=category,
                platform_names=plat_names,
                legs=legs,
            )
        )

    # Sort by underround descending (biggest arb first)
    results.sort(key=lambda g: g.underround, reverse=True)
    return results
