from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.models import JobRun
from app.services.research.provider_reliability import build_provider_reliability_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_provider_reliability_report_supports_raw_and_wrapped_sync_details() -> None:
    db = _session()
    now = datetime.now(UTC)

    # Raw collector detail format.
    db.add(
        JobRun(
            job_name="sync_all_platforms",
            status="SUCCESS",
            details={
                "MANIFOLD": {"fetched": 12, "inserted": 3, "updated": 9, "errors": 0},
                "POLYMARKET": {"fetched": 10, "inserted": 2, "updated": 8, "errors": 0},
            },
            started_at=now - timedelta(minutes=50),
            finished_at=now - timedelta(minutes=49),
        )
    )

    # Wrapped jobs.py detail format + one platform error.
    db.add(
        JobRun(
            job_name="sync_all_platforms",
            status="SUCCESS",
            details={
                "status": "ok",
                "result": {
                    "MANIFOLD": {"fetched": 8, "inserted": 1, "updated": 7, "errors": 0},
                    "METACULUS": {"fetched": 0, "inserted": 0, "updated": 0, "errors": 1, "error": "HTTP 429"},
                },
            },
            started_at=now - timedelta(minutes=40),
            finished_at=now - timedelta(minutes=39, seconds=45),
        )
    )
    db.commit()

    report = build_provider_reliability_report(db, days=2, limit_runs=100)
    assert report["platforms_total"] >= 3
    rows = {row["platform"]: row for row in report["by_platform"]}
    assert rows["MANIFOLD"]["runs"] == 2
    assert rows["MANIFOLD"]["error_runs"] == 0
    assert rows["METACULUS"]["error_runs"] == 1
    assert rows["METACULUS"]["rate_limit_errors"] == 1
    assert report["overall"]["error_runs"] >= 1


def test_provider_reliability_report_ignores_non_sync_jobs() -> None:
    db = _session()
    now = datetime.now(UTC)
    db.add(
        JobRun(
            job_name="analyze_markets",
            status="SUCCESS",
            details={"ok": True},
            started_at=now - timedelta(minutes=10),
            finished_at=now - timedelta(minutes=9),
        )
    )
    db.commit()

    report = build_provider_reliability_report(db, days=2, limit_runs=100)
    assert report["platforms_total"] == 0
    assert report["overall"]["runs"] == 0
