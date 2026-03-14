from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, SignalHistory
from app.services.research.event_cluster_research import build_event_cluster_research_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_event_cluster_research_detects_clusters_and_variance() -> None:
    db = _session()
    now = datetime.now(UTC)
    p = Platform(name="P", base_url="https://example.test")
    db.add(p)
    db.commit()
    db.refresh(p)

    m1 = Market(platform_id=p.id, external_market_id="m1", title="Will Bitcoin hit 100k by Dec 2026?")
    m2 = Market(platform_id=p.id, external_market_id="m2", title="Bitcoin price above 100000 in 2026?")
    m3 = Market(platform_id=p.id, external_market_id="m3", title="Will federal reserve rate cut in 2026?")
    m4 = Market(platform_id=p.id, external_market_id="m4", title="US federal reserve rate cut by end 2026?")
    db.add_all([m1, m2, m3, m4])
    db.commit()
    for m in (m1, m2, m3, m4):
        db.refresh(m)

    rows = [
        (m1.id, 0.35, 0.39),
        (m2.id, 0.65, 0.61),
        (m3.id, 0.48, 0.49),
        (m4.id, 0.52, 0.50),
    ]
    for i, (mid, p0, p6) in enumerate(rows):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=8, minutes=i),
                platform="P",
                market_id=mid,
                probability_at_signal=p0,
                probability_after_6h=p6,
                divergence=abs(p0 - 0.5),
                liquidity=0.7,
                volume_24h=900.0,
            )
        )
    db.commit()

    report = build_event_cluster_research_report(
        db,
        days=3,
        horizon="6h",
        min_cluster_size=2,
        min_shared_tokens=2,
        min_jaccard=0.1,
        max_markets=100,
    )
    assert report["clusters_total"] >= 2
    assert report["best_cluster"] is not None
    assert report["best_cluster"]["cluster_probability_variance"] >= 0.0


def test_event_cluster_research_unsupported_signal_type() -> None:
    db = _session()
    report = build_event_cluster_research_report(db, signal_type="UNKNOWN")
    assert "error" in report
