from types import SimpleNamespace

from app.models.enums import SignalType
from app.models.models import Signal
from app.services.signals.ranking import select_top_signals


def _settings(**overrides):
    base = {
        "signal_top_use_v2_selection": True,
        "signal_top_v2_rank_by_score_total": True,
        "signal_top_min_score_total": 0.45,
        "signal_top_min_utility_score": 0.08,
        "signal_top_max_missing_rules_share": 0.35,
        "signal_top_min_confidence_missing_rules": 0.35,
        "signal_top_rules_risk_min_confidence": 0.45,
        "signal_top_rules_risk_min_liquidity": 0.55,
        "signal_top_allow_fallback_when_empty": True,
        "signal_top_appendix_c_enabled": False,
        "signal_rank_weight_edge": 0.35,
        "signal_rank_weight_liquidity": 0.25,
        "signal_rank_weight_execution_safety": 0.20,
        "signal_rank_weight_freshness": 0.10,
        "signal_rank_weight_confidence": 0.10,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _signal(
    sid: int,
    signal_type: SignalType,
    *,
    confidence: float,
    liquidity: float = 0.6,
    mode: str | None = None,
    score_total: float | None = None,
    utility: float | None = None,
) -> Signal:
    score_breakdown = {"score_total": score_total} if score_total is not None else None
    execution = {"utility_score": utility} if utility is not None else None
    return Signal(
        id=sid,
        signal_type=signal_type,
        market_id=sid,
        related_market_id=None,
        title=f"signal-{sid}",
        summary="summary",
        confidence_score=confidence,
        liquidity_score=liquidity,
        signal_mode=mode,
        score_breakdown_json=score_breakdown,
        execution_analysis=execution,
    )


def test_top_selection_v2_filters_low_utility_arbitrage() -> None:
    settings = _settings()
    signals = [
        _signal(
            1,
            SignalType.ARBITRAGE_CANDIDATE,
            confidence=0.6,
            mode="momentum",
            score_total=0.70,
            utility=0.02,
        ),
        _signal(
            2,
            SignalType.ARBITRAGE_CANDIDATE,
            confidence=0.55,
            mode="momentum",
            score_total=0.62,
            utility=0.18,
        ),
    ]
    top = select_top_signals(signals, limit=5, settings=settings)
    assert [s.id for s in top] == [2]


def test_top_selection_legacy_flag_restores_old_behavior() -> None:
    settings = _settings(signal_top_use_v2_selection=False)
    signals = [
        _signal(
            1,
            SignalType.ARBITRAGE_CANDIDATE,
            confidence=0.6,
            mode="momentum",
            score_total=0.70,
            utility=0.02,
        ),
        _signal(
            2,
            SignalType.ARBITRAGE_CANDIDATE,
            confidence=0.55,
            mode="momentum",
            score_total=0.62,
            utility=0.18,
        ),
    ]
    top = select_top_signals(signals, limit=2, settings=settings)
    assert [s.id for s in top] == [1, 2]


def test_top_selection_enforces_missing_rules_share() -> None:
    settings = _settings(signal_top_max_missing_rules_share=0.34)
    signals = [
        _signal(10, SignalType.RULES_RISK, confidence=0.7, mode="missing_rules_risk", score_total=0.9, utility=0.0),
        _signal(11, SignalType.RULES_RISK, confidence=0.7, mode="missing_rules_risk", score_total=0.88, utility=0.0),
        _signal(12, SignalType.ARBITRAGE_CANDIDATE, confidence=0.55, mode="momentum", score_total=0.8, utility=0.2),
    ]
    top = select_top_signals(signals, limit=3, settings=settings)
    assert [s.id for s in top] == [12]


def test_top_selection_rules_risk_conservative_gating() -> None:
    settings = _settings(
        signal_top_rules_risk_min_confidence=0.5,
        signal_top_rules_risk_min_liquidity=0.6,
    )
    signals = [
        _signal(20, SignalType.RULES_RISK, confidence=0.49, liquidity=0.8, mode="explicit_rules_risk", score_total=0.8),
        _signal(21, SignalType.RULES_RISK, confidence=0.7, liquidity=0.5, mode="explicit_rules_risk", score_total=0.82),
        _signal(22, SignalType.RULES_RISK, confidence=0.7, liquidity=0.7, mode="explicit_rules_risk", score_total=0.84),
    ]
    top = select_top_signals(signals, limit=3, settings=settings)
    assert [s.id for s in top] == [22]


def test_top_selection_appendix_c_ranker_uses_execution_safety() -> None:
    settings = _settings(
        signal_top_appendix_c_enabled=True,
        signal_top_min_score_total=-1.0,
    )
    s1 = _signal(
        31,
        SignalType.ARBITRAGE_CANDIDATE,
        confidence=0.5,
        liquidity=0.5,
        score_total=0.1,
        utility=0.9,
    )
    s1.score_breakdown_json = {
        "edge": 0.2,
        "liquidity": 0.5,
        "freshness": 0.5,
        "confidence": 0.5,
        "risk_penalties": 0.1,
    }
    s2 = _signal(
        32,
        SignalType.ARBITRAGE_CANDIDATE,
        confidence=0.7,
        liquidity=0.7,
        score_total=0.9,
        utility=0.1,
    )
    s2.score_breakdown_json = {
        "edge": 0.2,
        "liquidity": 0.5,
        "freshness": 0.5,
        "confidence": 0.5,
        "risk_penalties": 0.1,
    }
    top = select_top_signals([s2, s1], limit=2, settings=settings)
    assert [s.id for s in top] == [31, 32]
