from datetime import datetime, timedelta
import time

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import (
    DuplicatePairCandidate,
    LiquidityAnalysis,
    Market,
    MarketSnapshot,
    Platform,
    RulesAnalysis,
    Signal,
    SignalHistory,
)
from app.tasks.jobs import label_signal_history_1h_job, label_signal_history_resolution_job
from app.services.signals.engine import SignalEngine


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _platform(db: Session) -> Platform:
    p = Platform(name="P", base_url="https://example.test")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_acceptance_momentum_threshold_and_uncertainty_cap() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.utcnow()
    markets = [
        Market(
            platform_id=p.id,
            external_market_id="m1",
            title="Far from midpoint and low move",
            probability_yes=0.8,
            volume_24h=1000,
            resolution_time=now + timedelta(days=20),
        ),
        Market(
            platform_id=p.id,
            external_market_id="m2",
            title="Near midpoint and low move",
            probability_yes=0.52,
            volume_24h=1000,
            resolution_time=now + timedelta(days=20),
        ),
        Market(
            platform_id=p.id,
            external_market_id="m3",
            title="High move candidate",
            probability_yes=0.60,
            volume_24h=1000,
            resolution_time=now + timedelta(days=20),
        ),
    ]
    db.add_all(markets)
    db.commit()
    for m in markets:
        db.refresh(m)
        db.add(LiquidityAnalysis(market_id=m.id, score=0.9, level="HIGH", explanation="ok"))

    snapshots = [
        MarketSnapshot(market_id=markets[0].id, probability_yes=0.80, fetched_at=now - timedelta(minutes=1)),
        MarketSnapshot(market_id=markets[0].id, probability_yes=0.79, fetched_at=now - timedelta(minutes=5)),
        MarketSnapshot(market_id=markets[1].id, probability_yes=0.52, fetched_at=now - timedelta(minutes=1)),
        MarketSnapshot(market_id=markets[1].id, probability_yes=0.51, fetched_at=now - timedelta(minutes=5)),
        MarketSnapshot(market_id=markets[2].id, probability_yes=0.75, fetched_at=now - timedelta(minutes=1)),
        MarketSnapshot(market_id=markets[2].id, probability_yes=0.60, fetched_at=now - timedelta(minutes=5)),
    ]
    db.add_all(snapshots)
    db.commit()

    engine = SignalEngine(db)
    engine._increment_generation_stat = lambda *args, **kwargs: None  # type: ignore[method-assign]
    engine.settings.signal_arbitrage_min_liquidity = 0.1
    engine.settings.signal_arbitrage_min_volume_24h = 0.0
    engine.settings.signal_mode_momentum_min_move = 0.10
    engine.settings.signal_arbitrage_midpoint_band = 0.12
    engine.settings.signal_mode_uncertainty_max_score = 0.65
    engine.settings.signal_arbitrage_max_candidates = 10
    engine.settings.signal_arbitrage_exclude_keywords = ""
    engine.settings.signal_rules_risk_threshold = 1.0
    engine._hours_since = lambda ts: 0.1  # type: ignore[method-assign]
    engine.execution.simulate = lambda **kwargs: {"utility_score": 0.2, "slippage_adjusted_edge": 0.1}  # type: ignore[method-assign]

    engine.generate_signals()
    rows = list(db.scalars(select(Signal).where(Signal.signal_type == SignalType.ARBITRAGE_CANDIDATE)))

    by_market = {row.market_id: row for row in rows}
    assert markets[0].id not in by_market
    assert markets[1].id in by_market
    assert markets[2].id in by_market

    uncertainty = by_market[markets[1].id]
    assert uncertainty.signal_mode == "uncertainty_liquid"
    assert (uncertainty.confidence_score or 0.0) <= engine.settings.signal_mode_uncertainty_max_score

    momentum = by_market[markets[2].id]
    assert momentum.signal_mode == "momentum"
    assert float((momentum.metadata_json or {}).get("recent_move", 0.0)) >= engine.settings.signal_mode_momentum_min_move
    for row in rows:
        if row.signal_mode == "momentum":
            assert float((row.metadata_json or {}).get("recent_move", 0.0)) >= engine.settings.signal_mode_momentum_min_move
    history_rows = list(db.scalars(select(SignalHistory)))
    assert len(history_rows) >= len(rows)


def test_acceptance_missing_rules_cap_applies_top_n() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.utcnow()

    liquidity_scores = [0.90, 0.86, 0.95, 0.88]
    markets: list[Market] = []
    for idx, liq in enumerate(liquidity_scores, start=1):
        market = Market(
            platform_id=p.id,
            external_market_id=f"r{idx}",
            title=f"Rules missing market {idx}",
            probability_yes=None,
            volume_24h=1000,
            resolution_time=now + timedelta(days=30),
            rules_text=None,
        )
        db.add(market)
        db.commit()
        db.refresh(market)
        markets.append(market)
        db.add(LiquidityAnalysis(market_id=market.id, score=liq, level="HIGH", explanation="ok"))
        db.add(RulesAnalysis(market_id=market.id, score=0.1, level="LOW", matched_flags=[], explanation="low"))
    db.commit()

    signal_engine = SignalEngine(db)
    signal_engine._increment_generation_stat = lambda *args, **kwargs: None  # type: ignore[method-assign]
    signal_engine.settings.signal_rules_risk_threshold = 0.2
    signal_engine.settings.signal_rules_missing_min_liquidity = 0.85
    signal_engine.settings.signal_rules_missing_min_volume_24h = 500.0
    signal_engine.settings.signal_rules_missing_daily_cap = 2
    signal_engine.settings.signal_arbitrage_exclude_keywords = ""
    signal_engine.settings.signal_arbitrage_min_liquidity = 1.0
    signal_engine.execution.simulate = lambda **kwargs: {"utility_score": 0.05, "slippage_adjusted_edge": 0.05}  # type: ignore[method-assign]

    signal_engine.generate_signals()

    rows = list(
        db.scalars(
            select(Signal).where(
                Signal.signal_type == SignalType.RULES_RISK,
                Signal.signal_mode == "missing_rules_risk",
            )
        )
    )
    assert len(rows) == 2

    created_market_ids = {r.market_id for r in rows}
    expected_top2 = {markets[2].id, markets[0].id}
    assert created_market_ids == expected_top2
    history_rows = list(db.scalars(select(SignalHistory)))
    assert len(history_rows) >= len(rows)


def test_acceptance_refresh_keeps_created_at_and_updates_updated_at() -> None:
    db = _session()
    p = _platform(db)
    market = Market(
        platform_id=p.id,
        external_market_id="x1",
        title="Refresh target",
        probability_yes=0.5,
        volume_24h=1000,
        resolution_time=datetime.utcnow() + timedelta(days=10),
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    signal_engine = SignalEngine(db)
    signal_engine._increment_generation_stat = lambda *args, **kwargs: None  # type: ignore[method-assign]

    outcome1 = signal_engine._create_signal_if_not_recent(
        signal_type=SignalType.WEIRD_MARKET,
        market_id=market.id,
        related_market_id=None,
        title="Initial",
        summary="Initial summary",
        confidence_score=0.5,
        signal_mode="explicit_rules_risk",
        score_breakdown_json={"score_total": 0.5},
    )
    assert outcome1 == "created"
    db.commit()

    first = db.scalar(select(Signal).where(Signal.market_id == market.id, Signal.signal_type == SignalType.WEIRD_MARKET))
    assert first is not None
    first_created_at = first.created_at
    first_updated_at = first.updated_at

    time.sleep(0.01)
    outcome2 = signal_engine._create_signal_if_not_recent(
        signal_type=SignalType.WEIRD_MARKET,
        market_id=market.id,
        related_market_id=None,
        title="Updated",
        summary="Updated summary",
        confidence_score=0.7,
        signal_mode="explicit_rules_risk",
        score_breakdown_json={"score_total": 0.7},
    )
    assert outcome2 == "updated"
    db.commit()

    second = db.scalar(select(Signal).where(Signal.market_id == market.id, Signal.signal_type == SignalType.WEIRD_MARKET))
    assert second is not None
    assert second.created_at == first_created_at
    assert second.updated_at is not None
    assert first_updated_at is not None
    assert second.updated_at >= first_updated_at


def test_resolution_labeling_job_marks_resolved_success() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.utcnow()
    market = Market(
        platform_id=p.id,
        external_market_id="resolved-1",
        title="Resolved market",
        probability_yes=1.0,
        volume_24h=1000,
        status="resolved",
        resolution_time=now - timedelta(days=1),
        source_payload={"resolution": "YES", "isResolved": True},
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    history_row = SignalHistory(
        signal_id=None,
        signal_type=SignalType.ARBITRAGE_CANDIDATE,
        timestamp=now - timedelta(days=2),
        platform="P",
        market_id=market.id,
        related_market_id=None,
        probability_at_signal=0.45,
        related_market_probability=None,
        divergence=0.1,
        liquidity=0.8,
        volume_24h=1000.0,
        simulated_trade=None,
    )
    db.add(history_row)
    db.commit()

    result = label_signal_history_resolution_job(db)
    assert result["status"] == "ok"
    payload = result["result"]
    assert payload["updated"] == 1

    updated = db.scalar(select(SignalHistory).where(SignalHistory.id == history_row.id))
    assert updated is not None
    assert updated.resolution_checked_at is not None
    assert updated.resolved_probability == 1.0
    assert updated.resolved_success is True


def test_capture_divergence_research_samples_from_broad_candidates() -> None:
    db = _session()
    p1 = Platform(name="P1", base_url="https://p1.test")
    p2 = Platform(name="P2", base_url="https://p2.test")
    db.add_all([p1, p2])
    db.commit()
    db.refresh(p1)
    db.refresh(p2)

    now = datetime.utcnow()
    a = Market(
        platform_id=p1.id,
        external_market_id="a1",
        title="BTC > 100k",
        probability_yes=0.62,
        volume_24h=1200,
        resolution_time=now + timedelta(days=30),
    )
    b = Market(
        platform_id=p2.id,
        external_market_id="b1",
        title="BTC over 100k",
        probability_yes=0.48,
        volume_24h=900,
        resolution_time=now + timedelta(days=30),
    )
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)

    db.add_all(
        [
            LiquidityAnalysis(market_id=a.id, score=0.8, level="HIGH", explanation="ok"),
            LiquidityAnalysis(market_id=b.id, score=0.7, level="HIGH", explanation="ok"),
            DuplicatePairCandidate(
                market_a_id=a.id,
                market_b_id=b.id,
                stage="strict_fail",
                similarity_score=82.0,
                similarity_explanation="broad match",
                drop_reason="strict_threshold_not_met",
            ),
        ]
    )
    db.commit()

    engine = SignalEngine(db)
    engine.settings.signal_divergence_research_min_similarity = 70.0
    engine.settings.signal_divergence_research_min_diff = 0.05
    engine.settings.signal_divergence_research_max_samples_per_run = 5
    engine.settings.signal_divergence_research_sample_cooldown_minutes = 600

    first = engine.capture_divergence_research_samples()
    assert first["research_divergence_samples_created"] == 1

    rows = list(db.scalars(select(SignalHistory).where(SignalHistory.signal_type == SignalType.DIVERGENCE)))
    assert len(rows) == 1
    assert rows[0].market_id == a.id
    assert rows[0].related_market_id == b.id
    assert round(rows[0].divergence or 0.0, 4) == 0.14

    second = engine.capture_divergence_research_samples()
    assert second["research_divergence_samples_created"] == 0
    assert second["research_divergence_skipped_cooldown"] >= 1


def test_capture_divergence_research_samples_uses_snapshot_probability_fallback() -> None:
    db = _session()
    p1 = Platform(name="SP1", base_url="https://sp1.test")
    p2 = Platform(name="SP2", base_url="https://sp2.test")
    db.add_all([p1, p2])
    db.commit()
    db.refresh(p1)
    db.refresh(p2)

    now = datetime.utcnow()
    a = Market(
        platform_id=p1.id,
        external_market_id="sa1",
        title="Event A",
        probability_yes=0.60,
        volume_24h=1000,
    )
    b = Market(
        platform_id=p2.id,
        external_market_id="sb1",
        title="Event B",
        probability_yes=None,
        volume_24h=1000,
    )
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)

    db.add(
        MarketSnapshot(
            market_id=b.id,
            probability_yes=0.45,
            fetched_at=now - timedelta(minutes=2),
        )
    )
    db.add_all(
        [
            LiquidityAnalysis(market_id=a.id, score=0.7, level="HIGH", explanation="ok"),
            LiquidityAnalysis(market_id=b.id, score=0.7, level="HIGH", explanation="ok"),
            DuplicatePairCandidate(
                market_a_id=a.id,
                market_b_id=b.id,
                stage="strict_fail",
                similarity_score=80.0,
                similarity_explanation="broad match",
                drop_reason="strict_threshold_not_met",
            ),
        ]
    )
    db.commit()

    engine = SignalEngine(db)
    engine.settings.signal_divergence_research_min_similarity = 70.0
    engine.settings.signal_divergence_research_min_diff = 0.05
    engine.settings.signal_divergence_research_max_samples_per_run = 5
    engine.settings.signal_divergence_research_sample_cooldown_minutes = 600

    result = engine.capture_divergence_research_samples()
    assert result["research_divergence_samples_created"] == 1
    assert result["research_divergence_used_snapshot_probability"] == 1


def test_capture_divergence_research_samples_fallback_from_title_overlap() -> None:
    db = _session()
    p1 = Platform(name="FP1", base_url="https://fp1.test")
    p2 = Platform(name="FP2", base_url="https://fp2.test")
    db.add_all([p1, p2])
    db.commit()
    db.refresh(p1)
    db.refresh(p2)

    now = datetime.utcnow()
    a = Market(
        platform_id=p1.id,
        external_market_id="fa1",
        title="Will bitcoin price exceed 100k in 2026",
        probability_yes=0.63,
        volume_24h=1500,
        resolution_time=now + timedelta(days=60),
    )
    b = Market(
        platform_id=p2.id,
        external_market_id="fb1",
        title="Bitcoin exceed 100k by end of 2026",
        probability_yes=0.47,
        volume_24h=1400,
        resolution_time=now + timedelta(days=60),
    )
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)

    db.add_all(
        [
            LiquidityAnalysis(market_id=a.id, score=0.8, level="HIGH", explanation="ok"),
            LiquidityAnalysis(market_id=b.id, score=0.75, level="HIGH", explanation="ok"),
        ]
    )
    db.commit()

    engine = SignalEngine(db)
    engine.settings.signal_divergence_research_min_similarity = 99.0
    engine.settings.signal_divergence_research_min_diff = 0.05
    engine.settings.signal_divergence_research_max_samples_per_run = 3
    engine.settings.signal_divergence_research_sample_cooldown_minutes = 600

    result = engine.capture_divergence_research_samples()
    assert result["research_divergence_candidates"] == 0
    assert result["research_divergence_fallback_candidates"] >= 1
    assert result["research_divergence_fallback_created"] >= 1
    assert result["research_divergence_samples_created"] >= 1


def test_label_signal_history_1h_job_labels_all_past_unlabeled_rows() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.utcnow()
    market = Market(
        platform_id=p.id,
        external_market_id="label-1h",
        title="Label target",
        probability_yes=0.57,
        volume_24h=1000,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    old_row = SignalHistory(
        signal_id=None,
        signal_type=SignalType.DIVERGENCE,
        timestamp=now - timedelta(hours=2, minutes=17),
        platform="P",
        market_id=market.id,
        related_market_id=None,
        probability_at_signal=0.51,
        related_market_probability=None,
        divergence=0.06,
        liquidity=0.7,
        volume_24h=1000.0,
        simulated_trade=None,
    )
    db.add(old_row)
    db.commit()

    result = label_signal_history_1h_job(db)
    assert result["status"] == "ok"
    assert result["result"]["updated"] >= 1

    updated = db.scalar(select(SignalHistory).where(SignalHistory.id == old_row.id))
    assert updated is not None
    assert updated.probability_after_1h == 0.57
    assert updated.labeled_at is not None
