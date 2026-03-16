from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal, SignalHistory, Stage7AgentDecision, Stage8Decision
from app.services.research.stage8_batch import build_stage8_batch_report
from app.services.research.stage8_final_report import build_stage8_final_report
from app.services.research.stage8_shadow_ledger import build_stage8_shadow_ledger_report


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def _seed_data(db: Session) -> None:
    now = datetime.now(UTC)
    platform = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="m1",
        title="Will BTC be above 120k by Dec 31?",
        description="Resolved using official source in UTC.",
        probability_yes=0.55,
        liquidity_value=15000,
        volume_24h=25000,
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
        execution_analysis={"expected_ev_after_costs_pct": 0.04},
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


def test_stage8_final_report_returns_decision_and_checks() -> None:
    db = _mk_db()
    _seed_data(db)
    settings = Settings()
    report = build_stage8_final_report(db, settings=settings, lookback_days=14, limit=300)
    assert report["final_decision"] in {"GO", "LIMITED_GO", "NO_GO", "NO_GO_DATA_PENDING"}
    assert "checks" in report
    assert "summary" in report
    assert "sections" in report


def test_stage8_batch_report_contains_reports_and_tracking() -> None:
    db = _mk_db()
    _seed_data(db)
    settings = Settings()
    report = build_stage8_batch_report(db, settings=settings, lookback_days=14, limit=300)
    assert "reports" in report
    assert "stage8_shadow_ledger" in report["reports"]
    assert "stage8_final_report" in report["reports"]
    assert "tracking" in report
    assert "stage8_shadow_ledger" in report["tracking"]
    assert "stage8_final_report" in report["tracking"]
    stored = db.query(Stage8Decision).count()
    assert stored == int(report["reports"]["stage8_shadow_ledger"]["rows_total"])
    report2 = build_stage8_batch_report(db, settings=settings, lookback_days=14, limit=300)
    stored2 = db.query(Stage8Decision).count()
    assert stored2 == int(report2["reports"]["stage8_shadow_ledger"]["rows_total"])


def test_stage8_data_sufficiency_requires_keep_with_resolution() -> None:
    db = _mk_db()
    _seed_data(db)
    db.query(SignalHistory).update({SignalHistory.resolved_success: None})
    db.commit()
    settings = Settings()
    report = build_stage8_shadow_ledger_report(db, settings=settings, lookback_days=14, limit=300)
    assert report["data_sufficient_for_acceptance"] is False
    assert int((report.get("data_sufficiency") or {}).get("keeps_with_resolution") or 0) == 0


def test_stage8_coverage_reflects_stage7_presence() -> None:
    db = _mk_db()
    _seed_data(db)
    db.query(Stage7AgentDecision).delete()
    db.commit()
    settings = Settings()
    report = build_stage8_shadow_ledger_report(db, settings=settings, lookback_days=14, limit=300)
    assert float(report.get("stage8_coverage") or 0.0) == 1.0
    assert float(report.get("coverage") if report.get("coverage") is not None else 1.0) < 1.0
