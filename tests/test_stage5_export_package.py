from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, SignalHistory
from app.services.research.export_package import (
    build_stage5_export_decision_rows,
    build_stage5_export_package,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_stage5_export_package_contains_summary_and_rows() -> None:
    db = _session()
    now = datetime.now(UTC)
    p = Platform(name="P", base_url="https://example.test")
    db.add(p)
    db.commit()
    db.refresh(p)
    m = Market(platform_id=p.id, external_market_id="m1", title="Bitcoin price above 100000 in 2026?")
    db.add(m)
    db.commit()
    db.refresh(m)

    for i in range(20):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=8, minutes=i),
                platform="POLYMARKET",
                market_id=m.id,
                probability_at_signal=0.45,
                probability_after_6h=0.47,
                divergence=0.08,
                liquidity=0.8,
                volume_24h=2000.0,
            )
        )
    db.commit()

    package = build_stage5_export_package(db, days=7, horizon="6h", min_labeled_returns=10, experiments_limit=10)
    assert package["artifact_name"] == "stage5_export_package"
    assert "summary" in package
    assert "final_report" in package
    rows = build_stage5_export_decision_rows(package)
    assert isinstance(rows, list)
