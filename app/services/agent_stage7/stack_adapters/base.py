from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Stage7AdapterInput:
    signal_id: int
    base_decision: str
    internal_gate_passed: bool
    contradictions_count: int
    ambiguity_count: int
    # Financial context for EV/Kelly-aware decisions
    expected_ev_pct: float = 0.0
    kelly_fraction: float = 0.0
    market_prob: float = 0.5
    divergence_score: float = 0.0
    liquidity_score: float = 0.0
    win_rate_90d: float = 0.0
    avg_win_90d: float = 0.0
    avg_loss_90d: float = 0.0
    n_samples_90d: int = 0
    is_shadow_mode: bool = True
    # Market & signal context
    signal_type: str = ""
    market_title: str = ""
    platform: str = ""
    days_to_resolution: int = -1
    # Cross-platform consensus
    consensus_spread: float = 0.0
    consensus_platforms: int = 0
    # Walk-forward quality
    walk_forward_verdict: str = "UNKNOWN"
    # Portfolio-aware context
    portfolio_open_positions: int = 0
    portfolio_exposure_pct: float = 0.0
    portfolio_cash_usd: float = 0.0
    portfolio_category_breakdown: dict[str, int] | None = None
    portfolio_bucket_breakdown_pct: dict[str, float] | None = None
    # Historical RAG context
    rag_similar_count: int = 0
    rag_similar_yes_rate: float = 0.0
    rag_summary: str = ""


class Stage7Adapter(Protocol):
    name: str

    def decide(self, payload: Stage7AdapterInput) -> dict[str, Any]:
        ...
