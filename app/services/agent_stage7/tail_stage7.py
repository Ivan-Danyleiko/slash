from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.models.models import Market, Signal
from app.services.stage17.tail_llm_reviewer import review_tail_narrative


def evaluate_tail_stage7(
    *,
    settings: Settings,
    signal: Signal,
    market: Market,
    tail_category: str,
    market_prob: float,
    our_prob: float,
) -> dict[str, Any]:
    """
    Deterministic Stage7 entrypoint for tail_narrative_fade signals.
    Delegates to Stage17 reviewer with strict JSON contract.
    """
    return review_tail_narrative(
        settings=settings,
        signal=signal,
        market=market,
        tail_category=tail_category,
        market_prob=float(market_prob),
        our_prob=float(our_prob),
    )

