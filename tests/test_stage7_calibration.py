from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal, SignalHistory, Stage7AgentDecision
from app.services.research.stage7_calibration import build_stage7_calibration_report


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage7_calibration_precision_6_of_10() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    platform = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="m1",
        title="Test market",
        probability_yes=0.5,
        created_at=now - timedelta(days=1),
        fetched_at=now,
    )
    db.add(market)
    db.flush()

    for i in range(10):
        sig = Signal(
            signal_type=SignalType.DIVERGENCE,
            market_id=market.id,
            title=f"S{i}",
            summary="x",
            confidence_score=0.8 if i < 5 else 0.6,
            created_at=now - timedelta(hours=2),
            signal_direction="YES",
        )
        db.add(sig)
        db.flush()
        profitable = i < 6
        db.add(
            SignalHistory(
                signal_id=sig.id,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=2),
                timestamp_bucket=(now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0),
                platform="POLYMARKET",
                source_tag="local",
                market_id=market.id,
                probability_at_signal=0.50,
                probability_after_6h=0.55 if profitable else 0.45,
                signal_direction="YES",
            )
        )
        db.add(
            Stage7AgentDecision(
                signal_id=sig.id,
                input_hash=f"h{i}",
                base_decision="KEEP",
                decision="KEEP",
                confidence_adjustment=0.0,
                provider="single_model",
                created_at=now - timedelta(hours=1),
            )
        )
    db.commit()

    report = build_stage7_calibration_report(db, days=30, horizon="6h")
    assert report["summary"]["known_outcomes"] == 10
    assert report["summary"]["precision_keep"] == 0.6


def test_stage7_calibration_ece_bucket_example() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    platform = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="m2",
        title="ECE market",
        probability_yes=0.5,
        created_at=now - timedelta(days=1),
        fetched_at=now,
    )
    db.add(market)
    db.flush()

    for i, profitable in enumerate((True, True, False)):
        sig = Signal(
            signal_type=SignalType.DIVERGENCE,
            market_id=market.id,
            title=f"E{i}",
            summary="x",
            confidence_score=0.8,
            created_at=now - timedelta(hours=2),
            signal_direction="YES",
        )
        db.add(sig)
        db.flush()
        db.add(
            SignalHistory(
                signal_id=sig.id,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=2),
                timestamp_bucket=(now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0),
                platform="POLYMARKET",
                source_tag="local",
                market_id=market.id,
                probability_at_signal=0.50,
                probability_after_6h=0.55 if profitable else 0.45,
                signal_direction="YES",
            )
        )
        db.add(
            Stage7AgentDecision(
                signal_id=sig.id,
                input_hash=f"e{i}",
                base_decision="KEEP",
                decision="KEEP",
                provider="single_model",
                created_at=now - timedelta(hours=1),
            )
        )
    db.commit()

    report = build_stage7_calibration_report(db, days=30, horizon="6h")
    # One bucket around 0.8 confidence should have |0.667 - 0.8| ~= 0.1333.
    assert abs(float(report["summary"]["ece"]) - 0.133333) < 0.01


def test_stage7_calibration_by_provider_split() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    platform = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="m3",
        title="Provider split",
        probability_yes=0.5,
        created_at=now - timedelta(days=1),
        fetched_at=now,
    )
    db.add(market)
    db.flush()

    providers = ["single_model", "ensemble_model"]
    for i in range(4):
        sig = Signal(
            signal_type=SignalType.DIVERGENCE,
            market_id=market.id,
            title=f"P{i}",
            summary="x",
            confidence_score=0.7,
            created_at=now - timedelta(hours=2),
            signal_direction="YES",
        )
        db.add(sig)
        db.flush()
        profitable = i % 2 == 0
        db.add(
            SignalHistory(
                signal_id=sig.id,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=2),
                timestamp_bucket=(now - timedelta(hours=2)).replace(minute=0, second=0, microsecond=0),
                platform="POLYMARKET",
                source_tag="local",
                market_id=market.id,
                probability_at_signal=0.5,
                probability_after_6h=0.55 if profitable else 0.45,
                signal_direction="YES",
            )
        )
        db.add(
            Stage7AgentDecision(
                signal_id=sig.id,
                input_hash=f"p{i}",
                base_decision="KEEP",
                decision="KEEP",
                provider=providers[i % 2],
                created_at=now - timedelta(hours=1),
            )
        )
    db.commit()

    report = build_stage7_calibration_report(db, days=30, horizon="6h")
    by_provider = report["by_provider"]
    assert "single_model" in by_provider
    assert "ensemble_model" in by_provider
