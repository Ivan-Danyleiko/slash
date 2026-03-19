from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.models import DuplicateMarketPair, Market, Platform


def _volume_weight(market: Market) -> float:
    vol = (
        float(market.volume_24h or 0.0)
        or float(market.notional_value_dollars or 0.0)
        or float(market.liquidity_value or 0.0)
    )
    return max(1.0, vol)


def build_cross_platform_prob_map(
    db: Session,
    *,
    markets: list[Market],
    settings: Settings | None = None,
) -> dict[int, dict[str, Any] | None]:
    """Batch compute cross-platform probabilities for a set of markets.

    This avoids N+1 DB queries when scanning many signals in one cycle.
    """
    if not markets:
        return {}
    s = settings or get_settings()
    by_id: dict[int, Market] = {int(m.id): m for m in markets}
    ids = list(by_id.keys())
    min_similarity = float(s.signal_divergence_research_min_similarity)

    pairs = list(
        db.scalars(
            select(DuplicateMarketPair).where(
                DuplicateMarketPair.similarity_score >= min_similarity,
                or_(
                    DuplicateMarketPair.market_a_id.in_(ids),
                    DuplicateMarketPair.market_b_id.in_(ids),
                ),
            )
        )
    )
    if not pairs:
        return {mid: None for mid in ids}

    neighbor_ids: set[int] = set()
    neighbors_by_market: dict[int, set[int]] = {mid: set() for mid in ids}
    for p in pairs:
        a = int(p.market_a_id)
        b = int(p.market_b_id)
        if a in neighbors_by_market:
            neighbors_by_market[a].add(b)
            neighbor_ids.add(b)
        if b in neighbors_by_market:
            neighbors_by_market[b].add(a)
            neighbor_ids.add(a)

    all_needed_ids = set(ids) | neighbor_ids
    all_markets = list(db.scalars(select(Market).where(Market.id.in_(all_needed_ids))))
    market_map: dict[int, Market] = {int(m.id): m for m in all_markets}
    needed_platform_ids = {int(m.platform_id) for m in all_markets if m.platform_id is not None}
    platform_names = (
        {int(p.id): str(p.name or "") for p in db.scalars(select(Platform).where(Platform.id.in_(needed_platform_ids)))}
        if needed_platform_ids
        else {}
    )

    out: dict[int, dict[str, Any] | None] = {}
    for mid in ids:
        base = by_id.get(mid) or market_map.get(mid)
        if base is None or base.probability_yes is None:
            out[mid] = None
            continue
        contributors: list[tuple[float, float, str]] = []
        for oid in neighbors_by_market.get(mid, set()):
            m = market_map.get(int(oid))
            if m is None or m.probability_yes is None:
                continue
            if int(m.platform_id) == int(base.platform_id):
                continue
            src = str(
                (m.source_payload or {}).get("platform")
                or platform_names.get(int(m.platform_id))
                or f"platform_{m.platform_id}"
            )
            contributors.append((float(m.probability_yes), _volume_weight(m), src.lower()))
        if not contributors:
            out[mid] = None
            continue
        total_weight = sum(w for _, w, _ in contributors)
        cross_prob = sum(prob * w for prob, w, _ in contributors) / max(1e-9, total_weight)
        out[mid] = {
            "cross_prob": float(cross_prob),
            "contributors": len(contributors),
            "sources": sorted({src for _, _, src in contributors if src}),
            "max_abs_diff_vs_base": max(abs(float(base.probability_yes) - prob) for prob, _, _ in contributors),
        }
    return out


def get_cross_platform_prob(
    db: Session,
    *,
    market: Market,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Return volume-weighted cross-platform probability for a market.

    Uses duplicate pairs as market-link graph and keeps only markets
    from other platforms with available probability.
    """
    s = settings or get_settings()
    base_prob = market.probability_yes
    if base_prob is None:
        return None

    min_similarity = float(s.signal_divergence_research_min_similarity)
    pairs = list(
        db.scalars(
            select(DuplicateMarketPair).where(
                DuplicateMarketPair.similarity_score >= min_similarity,
                or_(
                    DuplicateMarketPair.market_a_id == market.id,
                    DuplicateMarketPair.market_b_id == market.id,
                ),
            )
        )
    )
    if not pairs:
        return None

    other_ids: set[int] = set()
    for p in pairs:
        if int(p.market_a_id) == int(market.id):
            other_ids.add(int(p.market_b_id))
        else:
            other_ids.add(int(p.market_a_id))
    if not other_ids:
        return None

    linked = list(db.scalars(select(Market).where(Market.id.in_(other_ids))))
    needed_platform_ids = {int(m.platform_id) for m in linked if m.platform_id is not None}
    platform_names = (
        {int(p.id): str(p.name or "") for p in db.scalars(select(Platform).where(Platform.id.in_(needed_platform_ids)))}
        if needed_platform_ids
        else {}
    )
    contributors: list[tuple[float, float, str]] = []
    for m in linked:
        if m.probability_yes is None:
            continue
        if int(m.platform_id) == int(market.platform_id):
            continue
        src = str((m.source_payload or {}).get("platform") or platform_names.get(int(m.platform_id)) or f"platform_{m.platform_id}")
        contributors.append((float(m.probability_yes), _volume_weight(m), src.lower()))
    if not contributors:
        return None

    total_weight = sum(w for _, w, _ in contributors)
    cross_prob = sum(prob * w for prob, w, _ in contributors) / max(1e-9, total_weight)

    return {
        "cross_prob": float(cross_prob),
        "contributors": len(contributors),
        "sources": sorted({src for _, _, src in contributors if src}),
        "max_abs_diff_vs_base": max(abs(float(base_prob) - prob) for prob, _, _ in contributors),
    }
