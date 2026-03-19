from __future__ import annotations

import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.models import DuplicateMarketPair, Market, Platform


_STOP_WORDS = {
    "will", "the", "a", "an", "in", "on", "by", "for", "to", "of", "and", "or", "is", "are", "be",
}


def _tokens(text: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]{3,}", str(text or "").lower())
    return {p for p in parts if p not in _STOP_WORDS}


def _resolve_yes_no(payload: dict[str, Any] | None) -> str | None:
    p = payload or {}
    raw = (
        p.get("resolvedOutcome")
        or p.get("resolved_outcome")
        or p.get("resolution")
        or p.get("result")
        or p.get("outcome")
        or p.get("resolutionValue")
    )
    if raw is None:
        return None
    val = str(raw).strip().lower()
    if val in {"yes", "true", "1"}:
        return "YES"
    if val in {"no", "false", "0"}:
        return "NO"
    return None


def get_historical_rag_context(
    db: Session,
    *,
    market: Market,
    min_similar: int = 2,
    limit: int = 3,
) -> dict[str, Any]:
    """Collect similar resolved markets and build compact RAG context."""
    if str(market.category or "").strip().lower() == "crypto":
        return {"enabled": False, "reason": "category_crypto_excluded", "similar_count": 0, "items": []}

    pairs = list(
        db.scalars(
            select(DuplicateMarketPair).where(
                DuplicateMarketPair.similarity_score >= 70.0,
                or_(
                    DuplicateMarketPair.market_a_id == market.id,
                    DuplicateMarketPair.market_b_id == market.id,
                ),
            )
        )
    )
    candidate_ids: set[int] = set()
    for p in pairs:
        candidate_ids.add(int(p.market_b_id if int(p.market_a_id) == int(market.id) else p.market_a_id))

    if not candidate_ids:
        return {"enabled": False, "reason": "no_similar_candidates", "similar_count": 0, "items": []}

    all_candidates = list(db.scalars(select(Market).where(Market.id.in_(candidate_ids))))
    needed_platform_ids = {int(m.platform_id) for m in all_candidates if m.platform_id is not None}
    platforms = (
        {int(p.id): str(p.name or "") for p in db.scalars(select(Platform).where(Platform.id.in_(needed_platform_ids)))}
        if needed_platform_ids
        else {}
    )
    base_tokens = _tokens(market.title or "")

    ranked: list[tuple[float, Market, str]] = []
    for m in all_candidates:
        status = str(m.status or "").lower()
        if "resolved" not in status and "closed" not in status and "settled" not in status:
            continue
        payload = m.source_payload if isinstance(m.source_payload, dict) else {}
        resolved = _resolve_yes_no(payload)
        if resolved is None:
            continue
        overlap = len(base_tokens & _tokens(m.title or ""))
        if overlap <= 0:
            continue
        score = float(overlap) + (0.5 if (m.category and market.category and str(m.category).lower() == str(market.category).lower()) else 0.0)
        ranked.append((score, m, resolved))

    ranked.sort(key=lambda x: x[0], reverse=True)
    picked = ranked[: max(1, int(limit))]
    if len(picked) < int(min_similar):
        return {"enabled": False, "reason": "min_similar_not_met", "similar_count": len(picked), "items": []}

    items: list[dict[str, Any]] = []
    yes_count = 0
    for _, m, resolved in picked:
        if resolved == "YES":
            yes_count += 1
        items.append(
            {
                "market_id": int(m.id),
                "title": str(m.title or "")[:140],
                "platform": platforms.get(int(m.platform_id), ""),
                "resolved_outcome": resolved,
                "resolution_time": m.resolution_time.isoformat() if m.resolution_time else None,
                "final_probability": float(m.probability_yes or 0.5),
            }
        )

    similar_count = len(items)
    yes_rate = yes_count / max(1, similar_count)
    summary = f"{yes_count}/{similar_count} similar resolved YES ({yes_rate:.0%})"
    return {
        "enabled": True,
        "reason": "ok",
        "similar_count": similar_count,
        "similar_yes_rate": round(yes_rate, 4),
        "summary": summary,
        "items": items,
    }
