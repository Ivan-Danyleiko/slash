from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal, SignalHistory
from app.services.agent.policy import build_agent_decision_report
from app.services.signals.execution import ExecutionSimulatorV2


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _settings(**overrides):
    base = {
        "signal_execution_v2_horizon": "6h",
        "signal_execution_v2_lookback_days": 90,
        "signal_execution_v2_min_samples": 3,
        "signal_execution_position_size_usd": 100.0,
        "signal_execution_polymarket_mode": "gamma_api",
        "signal_execution_polymarket_gas_fee_usd": 0.5,
        "signal_execution_polymarket_bridge_fee_usd": 0.0,
        "agent_policy_keep_ev_threshold_pct": 0.02,
        "agent_policy_modify_ev_threshold_pct": 0.005,
        "agent_policy_min_confidence": 0.4,
        "agent_policy_min_liquidity": 0.5,
        "agent_policy_version": "policy_v1",
        "signal_execution_v2_prior_crypto": 0.025,
        "signal_execution_v2_prior_finance": 0.020,
        "signal_execution_v2_prior_sports": 0.015,
        "signal_execution_v2_prior_politics": 0.025,
        "signal_execution_v2_prior_other": 0.020,
        "signal_execution_v2_prior_default": 0.020,
        "signal_execution_polymarket_fee_mode": "zero",
        "signal_execution_polymarket_negrisk_impact_multiplier": 1.0,
        "signal_execution_kalshi_taker_coeff": 0.07,
        "signal_execution_kalshi_maker_fee_pct": 0.003,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_execution_simulator_v2_uses_empirical_ev_formula() -> None:
    db = _session()
    p = Platform(name="POLYMARKET", base_url="https://gamma-api.polymarket.com")
    db.add(p)
    db.commit()
    db.refresh(p)

    market = Market(
        platform_id=p.id,
        external_market_id="pm-1",
        title="Test market",
        probability_yes=0.52,
        volume_24h=100000.0,
        liquidity_value=20000.0,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    now = datetime.utcnow()
    rows = [
        SignalHistory(
            signal_id=None,
            signal_type=SignalType.ARBITRAGE_CANDIDATE,
            timestamp=now - timedelta(hours=8),
            platform="POLYMARKET",
            market_id=market.id,
            related_market_id=None,
            probability_at_signal=0.40,
            probability_after_6h=0.50,
            liquidity=0.7,
            volume_24h=20000.0,
        ),
        SignalHistory(
            signal_id=None,
            signal_type=SignalType.ARBITRAGE_CANDIDATE,
            timestamp=now - timedelta(hours=7),
            platform="POLYMARKET",
            market_id=market.id,
            related_market_id=None,
            probability_at_signal=0.45,
            probability_after_6h=0.43,
            liquidity=0.7,
            volume_24h=20000.0,
        ),
        SignalHistory(
            signal_id=None,
            signal_type=SignalType.ARBITRAGE_CANDIDATE,
            timestamp=now - timedelta(hours=6),
            platform="POLYMARKET",
            market_id=market.id,
            related_market_id=None,
            probability_at_signal=0.42,
            probability_after_6h=0.47,
            liquidity=0.7,
            volume_24h=20000.0,
        ),
    ]
    db.add_all(rows)
    db.commit()

    simulator = ExecutionSimulatorV2(db=db, settings=_settings())
    out = simulator.simulate(
        market=market,
        confidence_score=0.7,
        liquidity_score=0.7,
        recent_move=0.1,
        signal_type=SignalType.ARBITRAGE_CANDIDATE,
    )

    assert out["ev_model"] == "empirical"
    assert out["empirical_samples"] >= 3
    assert out["expected_ev_after_costs_pct"] > 0.0
    assert out["expected_costs_pct"] > 0.0


def test_execution_simulator_v2_falls_back_when_samples_insufficient() -> None:
    db = _session()
    p = Platform(name="MANIFOLD", base_url="https://api.manifold.markets/v0")
    db.add(p)
    db.commit()
    db.refresh(p)

    market = Market(
        platform_id=p.id,
        external_market_id="m-1",
        title="M",
        probability_yes=0.5,
        volume_24h=1000.0,
        liquidity_value=5000.0,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    simulator = ExecutionSimulatorV2(db=db, settings=_settings(signal_execution_v2_min_samples=5))
    out = simulator.simulate(
        market=market,
        confidence_score=0.6,
        liquidity_score=0.6,
        signal_type=SignalType.RULES_RISK,
    )

    assert "fallback" in str(out["assumptions_version"])
    assert out["empirical_samples"] == 0


def test_agent_policy_decisions_are_deterministic() -> None:
    db = _session()
    p = Platform(name="POLYMARKET", base_url="https://gamma-api.polymarket.com")
    db.add(p)
    db.commit()
    db.refresh(p)

    m1 = Market(platform_id=p.id, external_market_id="m1", title="M1", probability_yes=0.5)
    m2 = Market(platform_id=p.id, external_market_id="m2", title="M2", probability_yes=0.5)
    db.add_all([m1, m2])
    db.commit()
    db.refresh(m1)
    db.refresh(m2)

    s1 = Signal(
        signal_type=SignalType.RULES_RISK,
        market_id=m1.id,
        title="S1",
        summary="S1",
        confidence_score=0.8,
        liquidity_score=0.8,
        execution_analysis={"expected_ev_after_costs_pct": 0.03, "expected_costs_pct": 0.01, "utility_score": 0.2},
    )
    s2 = Signal(
        signal_type=SignalType.ARBITRAGE_CANDIDATE,
        market_id=m2.id,
        title="S2",
        summary="S2",
        confidence_score=0.3,
        liquidity_score=0.4,
        execution_analysis={"expected_ev_after_costs_pct": 0.01, "expected_costs_pct": 0.01, "utility_score": 0.1},
    )
    db.add_all([s1, s2])
    db.commit()

    report = build_agent_decision_report(db, settings=_settings(), limit=20, lookback_days=30)
    decisions = {row["signal_id"]: row["decision"] for row in report["rows"]}

    assert decisions[s1.id] == "KEEP"
    assert decisions[s2.id] == "SKIP"
