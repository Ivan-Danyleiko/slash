from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal, SignalHistory, Stage7AgentDecision, Stage8Decision
from app.services.agent_stage8.category_policy_profiles import get_category_policy_profile
from app.services.agent_stage8.decision_gate import resolve_stage8_decision
from app.services.agent_stage8.internal_gate_v2 import evaluate_internal_gate_v2
from app.services.agent_stage8.rules_field_verifier import compute_rules_ambiguity_score
from app.services.research.stage8_shadow_ledger import (
    build_stage8_shadow_ledger_report,
    extract_stage8_shadow_ledger_metrics,
)


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage8_production_profile_extends_bootstrap() -> None:
    _, profile = get_category_policy_profile("production_v1")
    crypto = profile["crypto"]
    assert "min_edge_after_costs" in crypto
    assert "min_liquidity_usd" in crypto
    assert "max_rules_ambiguity_score" in crypto
    assert "max_cross_platform_contradiction" in crypto
    assert "max_spread_cents" in crypto
    assert "min_ttr_hours" in crypto
    assert "min_freshness_minutes" in crypto
    assert "require_external_consensus" in crypto


def test_stage8_ambiguity_score_includes_penalties() -> None:
    text = "Will X happen before Dec 31 at 17:00?"
    score = compute_rules_ambiguity_score(text)
    assert score >= 0.30


def test_stage8_decision_mapping_soft_block_keeps_shadow_only() -> None:
    result = resolve_stage8_decision(
        base_decision="KEEP",
        hard_block=False,
        soft_block=True,
        reason_codes=["cross_platform_contradiction_high"],
    )
    assert result.decision == "KEEP"
    assert result.execution_action == "SHADOW_ONLY"


def test_stage8_shadow_ledger_metrics_include_per_category() -> None:
    report = {
        "rows_total": 10,
        "signals_total": 10,
        "coverage": 1.0,
        "stage7_missing": 0,
        "data_sufficient_for_acceptance": True,
        "execution_action_counts": {"EXECUTE_ALLOWED": 2, "SHADOW_ONLY": 3, "BLOCK": 5},
        "metrics": {"scenario_sweeps_realized_sample_share": 0.4},
        "per_category": {"crypto": {"edge_after_costs_mean": 0.01, "execute_allowed_count": 2}},
    }
    metrics = extract_stage8_shadow_ledger_metrics(report)
    assert "stage8_crypto_edge_after_costs_mean" in metrics
    assert "stage8_crypto_execute_allowed_count" in metrics
    assert metrics["stage8_execute_allowed_rate"] == 0.2
    assert metrics["stage8_sweeps_realized_sample_share"] == 0.4


def test_stage8_shadow_ledger_builds_rows_with_kelly_and_pnl_proxy() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    platform = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="m1",
        title="Will BTC be above 120k by Dec 31?",
        description="Resolved using official source.",
        probability_yes=0.55,
        liquidity_value=5000,
        volume_24h=10000,
        rules_text="Resolved by official source in UTC.",
        created_at=now,
        fetched_at=now,
        resolution_time=now + timedelta(days=10),
    )
    db.add(market)
    db.flush()
    signal = Signal(
        signal_type=SignalType.DIVERGENCE,
        market_id=market.id,
        title=market.title,
        summary="test",
        confidence_score=0.8,
        liquidity_score=0.8,
        divergence_score=0.12,
        execution_analysis={"expected_ev_after_costs_pct": 0.03},
        created_at=now,
    )
    db.add(signal)
    db.flush()

    for i in range(30):
        db.add(
            SignalHistory(
                signal_id=signal.id,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=i + 1),
                timestamp_bucket=(now - timedelta(hours=i + 1)).replace(minute=0, second=0, microsecond=0),
                platform="POLYMARKET",
                source_tag="local",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=0.5,
                probability_after_6h=0.52,
                resolved_success=True,
            )
        )
    for i in range(10):
        db.add(
            Stage7AgentDecision(
                signal_id=signal.id,
                input_hash=f"h{i}",
                base_decision="KEEP",
                decision="KEEP",
                created_at=now - timedelta(minutes=i),
            )
        )
    db.commit()

    settings = Settings()
    report = build_stage8_shadow_ledger_report(db, settings=settings, lookback_days=14, limit=100)
    assert report["rows_total"] >= 1
    first = report["rows"][0]
    assert "kelly_fraction" in first
    assert "pnl_proxy_usd_100" in first
    stored = db.query(Stage8Decision).order_by(Stage8Decision.id.desc()).first()
    assert stored is not None
    assert stored.kelly_fraction is not None
    assert stored.pnl_proxy_usd_100 is not None
    assert "market_new_creator" not in (first.get("reason_codes") or [])
    assert "external_consensus_single_source_allowed" in (first.get("reason_codes") or [])


def test_stage8_internal_gate_fails_when_ttr_missing() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    platform = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="m2",
        title="No TTR market",
        description="test",
        probability_yes=0.5,
        liquidity_value=10000,
        volume_24h=10000,
        rules_text="Resolved by official source in UTC.",
        created_at=now,
        fetched_at=now,
        resolution_time=None,
    )
    signal = Signal(
        signal_type=SignalType.DIVERGENCE,
        market_id=1,
        title="No TTR signal",
        summary="test",
        confidence_score=0.8,
        liquidity_score=0.8,
        divergence_score=0.1,
        execution_analysis={"expected_ev_after_costs_pct": 0.05},
        created_at=now,
    )
    _, profile = get_category_policy_profile("bootstrap_v1")
    result = evaluate_internal_gate_v2(signal=signal, market=market, category_policy=profile["crypto"])
    assert result.passed is False
    assert "ttr_missing" in result.reason_codes
