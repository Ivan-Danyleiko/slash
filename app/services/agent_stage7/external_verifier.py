from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import Market, Signal
from app.services.agent_stage7.tools import (
    get_cross_platform_consensus,
    get_market_snapshot,
    get_readiness_gate_status,
    get_research_decision,
    get_signal_context_cached,
    get_signal_history_metrics,
)

_AMBIGUITY_TOKENS = (
    "sole discretion",
    "if unavailable",
    "team decision",
    "may be resolved by",
    "consensus",
    "subjective",
    "at our discretion",
    "if applicable",
    "in the event of",
    "final determination",
    "editorial decision",
    "if deemed",
)


def build_external_verification(
    db: Session,
    *,
    signal: Signal,
    base_row: dict[str, Any],
    settings: Settings,
    runtime_cache: dict[str, Any] | None = None,
    market: Market | None = None,
) -> dict[str, Any]:
    cache = runtime_cache if runtime_cache is not None else {}
    m = market or db.get(Market, signal.market_id)
    title = str(m.title if m else signal.title or "").strip()
    market_rules = str(m.rules_text or "") if m else ""

    # Stage 7 Tool Interface Spec: 6 explicit callable tools.
    signal_context = get_signal_context_cached(
        db,
        int(signal.id),
        signal=signal,
        runtime_cache=cache,
    )
    signal_type_value = str(base_row.get("signal_type") or signal_context.get("signal_type") or "")
    history_key = f"history:{signal_type_value}:6h"
    history_metrics = cache.get(history_key)
    if history_metrics is None:
        history_metrics = get_signal_history_metrics(db, signal_type_value, "6h")
        cache[history_key] = history_metrics
    market_snapshot = get_market_snapshot(
        db,
        int(signal.market_id),
        market=m,
        runtime_cache=cache,
    )
    consensus_key = f"consensus:{title}"
    consensus = cache.get(consensus_key)
    if consensus is None:
        consensus = get_cross_platform_consensus(db, title, runtime_cache=cache)
        cache[consensus_key] = consensus
    readiness_gate = cache.get("readiness_gate")
    if readiness_gate is None:
        readiness_gate = get_readiness_gate_status(db, settings=settings)
        cache["readiness_gate"] = readiness_gate
    research_key = f"research_decision:{signal_type_value}"
    research_decision = cache.get(research_key)
    if research_decision is None:
        research_decision = get_research_decision(db, signal_type_value)
        cache[research_key] = research_decision

    polymarket_prob = consensus.get("polymarket_prob")
    manifold_prob = consensus.get("manifold_prob")
    metaculus_median = consensus.get("metaculus_median")

    # If consensus could not match the same event by title, keep same-platform value.
    if polymarket_prob is None:
        if str(market_snapshot.get("platform") or "") == "POLYMARKET":
            polymarket_prob = market_snapshot.get("probability")
    if manifold_prob is None:
        if str(market_snapshot.get("platform") or "") == "MANIFOLD":
            manifold_prob = market_snapshot.get("probability")
    if metaculus_median is None:
        if str(market_snapshot.get("platform") or "") == "METACULUS":
            metaculus_median = market_snapshot.get("probability")

    known_probs = [p for p in [polymarket_prob, manifold_prob, metaculus_median] if isinstance(p, (int, float))]
    contradictions: list[str] = []
    if len(known_probs) >= 2:
        spread = max(known_probs) - min(known_probs)
        if spread >= 0.20:
            contradictions.append("cross_platform_spread_ge_20pct")

    ambiguity_flags: list[str] = []
    rules_l = market_rules.lower()
    for token in _AMBIGUITY_TOKENS:
        if token in rules_l:
            ambiguity_flags.append(f"rules_ambiguity:{token}")

    return {
        "internal_metrics_snapshot": {
            "expected_ev_pct": float(base_row.get("expected_ev_pct") or signal_context.get("ev_v2") or 0.0),
            "confidence": float(base_row.get("confidence") or signal_context.get("confidence") or 0.0),
            "liquidity": float(base_row.get("liquidity") or signal_context.get("liquidity") or 0.0),
            "risk_flags": list(base_row.get("risk_flags") or []),
            "signal_history_metrics": history_metrics,
            "readiness_gate_status": readiness_gate,
            "research_decision": research_decision,
            "market_snapshot": market_snapshot,
        },
        "external_consensus": {
            "polymarket_prob": polymarket_prob,
            "manifold_prob": manifold_prob,
            "metaculus_median": metaculus_median,
        },
        "contradictions": contradictions,
        "resolution_ambiguity_flags": ambiguity_flags,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
