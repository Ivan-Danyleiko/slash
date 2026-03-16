from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.models.models import Stage11Client, Stage11Fill, Stage11Order, Stage11TradingAuditEvent
from app.services.stage11.readiness import (
    build_stage11_final_readiness_report,
    build_stage11_tenant_isolation_report,
)


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage11_tenant_isolation_detects_client_mismatch() -> None:
    db = _mk_db()
    c1 = Stage11Client(code="a", name="A", custody_mode="CLIENT_SIGNED", runtime_mode="SHADOW", is_active=True)
    c2 = Stage11Client(code="b", name="B", custody_mode="CLIENT_SIGNED", runtime_mode="SHADOW", is_active=True)
    db.add_all([c1, c2])
    db.commit()
    db.refresh(c1)
    db.refresh(c2)
    order = Stage11Order(
        client_id=c1.id,
        signal_id=None,
        market_id=1,
        platform="POLYMARKET",
        side="YES",
        size_bucket="small",
        notional_usd=50.0,
        requested_price=0.5,
        idempotency_key="iso_1",
        policy_version="stage11_v1",
        status="FILLED",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    # Intentionally mismatched fill client_id for isolation audit.
    db.add(
        Stage11Fill(
            client_id=c2.id,
            order_id=order.id,
            market_id=1,
            fill_price=0.51,
            fill_size_usd=50.0,
            fee_usd=0.0,
            realized_pnl_usd=0.0,
        )
    )
    db.commit()
    report = build_stage11_tenant_isolation_report(db)
    checks = dict(report.get("checks") or {})
    assert checks.get("fills_client_mismatch_eq_0") is False
    assert str(report.get("final_decision")) == "WARN"


def test_stage11_tenant_isolation_passes_clean_data() -> None:
    db = _mk_db()
    c1 = Stage11Client(code="a1", name="A1", custody_mode="CLIENT_SIGNED", runtime_mode="SHADOW", is_active=True)
    db.add(c1)
    db.commit()
    db.refresh(c1)
    report = build_stage11_tenant_isolation_report(db)
    assert str(report.get("final_decision")) == "PASS"


def test_stage11_final_readiness_report_shape() -> None:
    db = _mk_db()
    c1 = Stage11Client(code="default", name="Default", custody_mode="CLIENT_SIGNED", runtime_mode="SHADOW", is_active=True)
    db.add(c1)
    db.commit()
    db.add(
        Stage11TradingAuditEvent(
            client_id=c1.id,
            order_id=None,
            event_type="MANUAL_CHECK",
            severity="INFO",
            payload_json={},
            payload_checksum="x",
        )
    )
    db.commit()
    settings = get_settings().model_copy(update={"stage11_venue_dry_run": True})
    report = build_stage11_final_readiness_report(db, settings=settings, days_execution=14, days_client=7)
    assert "checks" in report
    assert "sections" in report
    assert "stage11_track" in (report.get("sections") or {})
    assert "tenant_isolation" in (report.get("sections") or {})

