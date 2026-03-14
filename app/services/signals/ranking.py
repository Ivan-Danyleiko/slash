from app.core.config import Settings
from app.models.enums import SignalType
from app.models.models import Signal


def appendix_c_score(signal: Signal, settings: Settings) -> float:
    score_payload = signal.score_breakdown_json or {}
    exec_payload = signal.execution_analysis or {}
    edge = float(score_payload.get("edge") or signal.divergence_score or 0.0)
    liquidity = float(score_payload.get("liquidity") or signal.liquidity_score or 0.0)
    freshness = float(score_payload.get("freshness") or 0.0)
    confidence = float(score_payload.get("confidence") or signal.confidence_score or 0.0)
    execution_safety = float(
        score_payload.get("execution_safety")
        if isinstance(score_payload.get("execution_safety"), (int, float))
        else exec_payload.get("utility_score")
        or 0.0
    )
    risk_penalties = float(score_payload.get("risk_penalties") or signal.rules_risk_score or 0.0)

    return (
        (settings.signal_rank_weight_edge * edge)
        + (settings.signal_rank_weight_liquidity * liquidity)
        + (settings.signal_rank_weight_execution_safety * execution_safety)
        + (settings.signal_rank_weight_freshness * freshness)
        + (settings.signal_rank_weight_confidence * confidence)
        - risk_penalties
    )


def rank_score(signal: Signal) -> float:
    divergence = signal.divergence_score or 0.0
    liquidity = signal.liquidity_score or 0.0
    rules_risk = signal.rules_risk_score or 0.0
    confidence = signal.confidence_score or 0.0
    mode_bonus = {
        "momentum": 0.08,
        "uncertainty_liquid": 0.01,
        "explicit_rules_risk": 0.02,
        "missing_rules_risk": -0.03,
    }.get(signal.signal_mode or "", 0.0)
    type_bonus = {
        SignalType.ARBITRAGE_CANDIDATE: 0.1,
        SignalType.DIVERGENCE: 0.08,
        SignalType.WEIRD_MARKET: 0.04,
        SignalType.RULES_RISK: 0.02,
        SignalType.DUPLICATE_MARKET: 0.01,
    }.get(signal.signal_type, 0.0)
    return (
        (0.6 * divergence)
        + (0.3 * liquidity)
        - (0.2 * rules_risk)
        + (0.25 * confidence)
        + type_bonus
        + mode_bonus
    )


def signal_score_total(signal: Signal) -> float:
    payload = signal.score_breakdown_json or {}
    raw = payload.get("score_total")
    if isinstance(raw, (int, float)):
        return float(raw)
    return rank_score(signal)


def signal_utility(signal: Signal) -> float:
    payload = signal.execution_analysis or {}
    raw = payload.get("utility_score")
    if isinstance(raw, (int, float)):
        return float(raw)
    return 0.0


def is_top_eligible(signal: Signal, settings: Settings) -> bool:
    if signal_score_total(signal) < settings.signal_top_min_score_total:
        return False

    if signal.signal_type == SignalType.ARBITRAGE_CANDIDATE:
        if signal_utility(signal) < settings.signal_top_min_utility_score:
            return False

    if signal.signal_type == SignalType.RULES_RISK:
        if (signal.confidence_score or 0.0) < settings.signal_top_rules_risk_min_confidence:
            return False
        if (signal.liquidity_score or 0.0) < settings.signal_top_rules_risk_min_liquidity:
            return False

    if (signal.signal_mode or "") == "missing_rules_risk":
        if (signal.confidence_score or 0.0) < settings.signal_top_min_confidence_missing_rules:
            return False
    return True


def select_top_signals(signals: list[Signal], *, limit: int, settings: Settings) -> list[Signal]:
    if limit <= 0:
        return []

    ranked_legacy = sorted(signals, key=rank_score, reverse=True)
    if not settings.signal_top_use_v2_selection:
        return ranked_legacy[:limit]

    if settings.signal_top_appendix_c_enabled:
        rank_key = lambda s: appendix_c_score(s, settings)
    else:
        rank_key = signal_score_total if settings.signal_top_v2_rank_by_score_total else rank_score
    ranked = sorted(signals, key=rank_key, reverse=True)
    selected: list[Signal] = []
    missing_rules_count = 0
    for signal in ranked:
        if not is_top_eligible(signal, settings):
            continue

        candidate_total = len(selected) + 1
        candidate_missing = missing_rules_count + (1 if (signal.signal_mode or "") == "missing_rules_risk" else 0)
        if candidate_total > 0:
            share = candidate_missing / candidate_total
            if share > settings.signal_top_max_missing_rules_share:
                continue

        selected.append(signal)
        if (signal.signal_mode or "") == "missing_rules_risk":
            missing_rules_count += 1
        if len(selected) >= limit:
            break

    if selected or not settings.signal_top_allow_fallback_when_empty:
        return selected
    return ranked_legacy[:limit]
