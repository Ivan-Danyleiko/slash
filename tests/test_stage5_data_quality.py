from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, SignalHistory
from app.services.research.data_quality import build_signal_history_data_quality_report


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


def test_data_quality_report_passes_for_valid_rows() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.now(UTC)

    market = Market(
        platform_id=p.id,
        external_market_id="m_dq_ok",
        title="M DQ OK",
        probability_yes=0.55,
        volume_24h=1000.0,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    db.add(
        SignalHistory(
            signal_id=None,
            signal_type=SignalType.DIVERGENCE,
            timestamp=now - timedelta(hours=6),
            platform="P",
            market_id=market.id,
            related_market_id=None,
            probability_at_signal=0.45,
            related_market_probability=0.52,
            divergence=0.07,
            liquidity=0.8,
            volume_24h=1000.0,
            probability_after_1h=0.47,
            labeled_at=now - timedelta(hours=5),
            resolved_probability=0.56,
            resolved_success=True,
        )
    )
    db.commit()

    report = build_signal_history_data_quality_report(db, days=3, limit=100)
    assert report["passed"] is True
    assert report["checks_failed"] == 0


def test_data_quality_report_detects_invalid_rows() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.now(UTC)

    market = Market(
        platform_id=p.id,
        external_market_id="m_dq_bad",
        title="M DQ BAD",
        probability_yes=0.5,
        volume_24h=1000.0,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    db.add(
        SignalHistory(
            signal_id=None,
            signal_type=SignalType.DIVERGENCE,
            timestamp=now + timedelta(hours=1),  # future timestamp
            platform="P",
            market_id=market.id,
            related_market_id=None,
            probability_at_signal=1.2,  # invalid range
            related_market_probability=0.4,
            divergence=1.5,  # invalid range
            liquidity=0.7,
            volume_24h=700.0,
            probability_after_1h=-0.1,  # invalid range
            labeled_at=None,  # inconsistent with probability_after_1h
            resolved_probability=None,
            resolved_success=True,  # inconsistent with missing resolved_probability
        )
    )
    db.commit()

    report = build_signal_history_data_quality_report(db, days=3, limit=100)
    assert report["passed"] is False
    assert report["checks_failed"] >= 1
    failed_names = {c["name"] for c in report["checks"] if not c["success"]}
    assert "probability_at_signal_in_[0,1]" in failed_names
    assert "probability_after_1h_in_[0,1]" in failed_names
    assert "divergence_in_[0,1]" in failed_names
    assert "labeled_at_consistency" in failed_names
    assert "resolution_consistency" in failed_names
    assert "no_future_timestamps" in failed_names
