from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import Signal


_DEF_LOOKBACK_DAYS = 7


def _decision_for_signal(signal: Signal, settings: Settings) -> dict:
    ex = signal.execution_analysis or {}
    score = signal.score_breakdown_json or {}

    confidence = float(signal.confidence_score or 0.0)
    liquidity = float(signal.liquidity_score or 0.0)
    expected_ev_after_costs = float(
        ex.get("expected_ev_after_costs_pct")
        if isinstance(ex.get("expected_ev_after_costs_pct"), (int, float))
        else ex.get("slippage_adjusted_edge")
        or 0.0
    )
    expected_costs_pct = float(ex.get("expected_costs_pct") or 0.0)
    utility = float(ex.get("utility_score") or 0.0)
    assumptions = str(ex.get("assumptions_version") or "n/a")

    risk_flags: list[str] = []
    if liquidity < settings.agent_policy_min_liquidity:
        risk_flags.append("low_liquidity")
    if confidence < settings.agent_policy_min_confidence:
        risk_flags.append("low_confidence")
    if expected_ev_after_costs <= 0:
        risk_flags.append("non_positive_ev")

    if risk_flags:
        decision = "SKIP"
    elif expected_ev_after_costs >= settings.agent_policy_keep_ev_threshold_pct:
        decision = "KEEP"
    elif expected_ev_after_costs >= settings.agent_policy_modify_ev_threshold_pct:
        decision = "MODIFY"
    else:
        decision = "REMOVE"

    return {
        "signal_id": signal.id,
        "signal_type": signal.signal_type.value,
        "signal_mode": signal.signal_mode,
        "decision": decision,
        "confidence": round(confidence, 4),
        "liquidity": round(liquidity, 4),
        "score_total": float(score.get("score_total") or 0.0),
        "expected_ev_pct": round(expected_ev_after_costs, 6),
        "expected_costs_pct": round(expected_costs_pct, 6),
        "utility_score": round(utility, 6),
        "risk_flags": risk_flags,
        "assumptions_version": assumptions,
        "policy_version": settings.agent_policy_version,
        "created_at": signal.created_at.isoformat() if signal.created_at else None,
    }


def build_agent_decision_report(
    db: Session,
    *,
    settings: Settings,
    limit: int = 200,
    lookback_days: int = _DEF_LOOKBACK_DAYS,
    include_latest_when_empty: bool = False,
) -> dict:
    limit = max(1, min(int(limit), 2000))
    lookback_days = max(1, min(int(lookback_days), 90))
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)

    signals = list(
        db.scalars(
            select(Signal)
            .where(Signal.created_at >= cutoff)
            .order_by(Signal.created_at.desc())
            .limit(limit)
        )
    )

    if not signals and include_latest_when_empty:
        signals = list(
            db.scalars(
                select(Signal)
                .order_by(Signal.created_at.desc())
                .limit(limit)
            )
        )

    rows = [_decision_for_signal(signal, settings) for signal in signals]
    counts: dict[str, int] = {"KEEP": 0, "MODIFY": 0, "REMOVE": 0, "SKIP": 0}
    for row in rows:
        counts[row["decision"]] = counts.get(row["decision"], 0) + 1

    return {
        "policy_version": settings.agent_policy_version,
        "period_days": lookback_days,
        "limit": limit,
        "total_signals": len(rows),
        "decision_counts": counts,
        "thresholds": {
            "keep_ev_threshold_pct": settings.agent_policy_keep_ev_threshold_pct,
            "modify_ev_threshold_pct": settings.agent_policy_modify_ev_threshold_pct,
            "min_confidence": settings.agent_policy_min_confidence,
            "min_liquidity": settings.agent_policy_min_liquidity,
        },
        "rows": rows,
    }
