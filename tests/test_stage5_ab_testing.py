from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.models import UserEvent
from app.services.research.ab_testing import assign_ab_variant, build_ab_testing_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        research_ab_enabled=True,
        research_ab_control_share=0.5,
        research_ab_salt="test-salt",
        research_ab_experiment_name="stage5_signal_framework",
        research_ab_control_label="v2_control",
        research_ab_treatment_label="v3_treatment",
    )


def test_assign_ab_variant_is_deterministic() -> None:
    settings = _settings()
    v1 = assign_ab_variant(user_id=123, settings=settings)
    v2 = assign_ab_variant(user_id=123, settings=settings)
    assert v1 == v2
    assert v1 in {settings.research_ab_control_label, settings.research_ab_treatment_label}


def test_build_ab_testing_report_counts_by_variant() -> None:
    db = _session()
    settings = _settings()
    now = datetime.now(UTC)
    db.add_all(
        [
            UserEvent(
                user_id=1,
                event_type="ab_variant_exposure",
                payload_json={"variant": "v2_control"},
                created_at=now - timedelta(hours=1),
            ),
            UserEvent(
                user_id=1,
                event_type="signal_sent",
                payload_json={"variant": "v2_control"},
                created_at=now - timedelta(minutes=50),
            ),
            UserEvent(
                user_id=1,
                event_type="market_opened",
                payload_json={"variant": "v2_control"},
                created_at=now - timedelta(minutes=40),
            ),
            UserEvent(
                user_id=2,
                event_type="ab_variant_exposure",
                payload_json={"variant": "v3_treatment"},
                created_at=now - timedelta(hours=1),
            ),
            UserEvent(
                user_id=2,
                event_type="signal_sent",
                payload_json={"variant": "v3_treatment"},
                created_at=now - timedelta(minutes=45),
            ),
            UserEvent(
                user_id=2,
                event_type="watchlist_added",
                payload_json={"variant": "v3_treatment"},
                created_at=now - timedelta(minutes=30),
            ),
        ]
    )
    db.commit()

    report = build_ab_testing_report(db, days=3, settings=settings)
    assert report["events_scanned"] >= 6
    rows = {r["variant"]: r for r in report["variants"]}
    assert rows["v2_control"]["exposures"] == 1
    assert rows["v2_control"]["signal_sent"] == 1
    assert rows["v2_control"]["market_opened"] == 1
    assert rows["v3_treatment"]["exposures"] == 1
    assert rows["v3_treatment"]["watchlist_added"] == 1
