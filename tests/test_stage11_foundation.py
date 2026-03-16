from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from datetime import UTC, datetime, timedelta

import pytest
from app.models.models import Stage11Client, Stage11ClientPosition, Stage11Fill, Stage11Order
from app.services.stage11.idempotency import stage11_idempotency_key
from app.services.stage11.order_manager import (
    cancel_order_by_id,
    create_or_reuse_order,
    reconcile_unknown_submits,
    refresh_order_status,
)
from app.services.stage11.venues.base import Stage11StatusResult
from app.services.stage11.risk_engine import Stage11RiskInput, resolve_circuit_breaker_level


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage11_idempotency_key_is_stable() -> None:
    a = stage11_idempotency_key(
        client_id=1,
        signal_id=123,
        policy_version="stage11_v1",
        side="YES",
        size_bucket="medium",
    )
    b = stage11_idempotency_key(
        client_id=1,
        signal_id=123,
        policy_version="stage11_v1",
        side="YES",
        size_bucket="medium",
    )
    assert a == b


def test_stage11_create_or_reuse_order_idempotent() -> None:
    db = _mk_db()
    settings = get_settings()
    client = Stage11Client(code="c1", name="Client 1", custody_mode="CLIENT_SIGNED", runtime_mode="SHADOW", is_active=True)
    db.add(client)
    db.commit()
    db.refresh(client)

    one = create_or_reuse_order(
        db,
        settings=settings,
        client_id=client.id,
        signal_id=10,
        market_id=20,
        policy_version="stage11_v1",
        side="YES",
        size_bucket="small",
        notional_usd=50.0,
        requested_price=0.55,
        runtime_mode="SHADOW",
        unknown_recovery_sec=60,
    )
    two = create_or_reuse_order(
        db,
        settings=settings,
        client_id=client.id,
        signal_id=10,
        market_id=20,
        policy_version="stage11_v1",
        side="YES",
        size_bucket="small",
        notional_usd=50.0,
        requested_price=0.55,
        runtime_mode="SHADOW",
        unknown_recovery_sec=60,
    )
    db.commit()
    assert one.id == two.id
    rows = list(db.scalars(select(Stage11Order)))
    assert len(rows) == 1


def test_stage11_non_shadow_order_submits_in_dry_run() -> None:
    db = _mk_db()
    settings = get_settings().model_copy(update={"stage11_venue_dry_run": True})
    client = Stage11Client(
        code="c2",
        name="Client 2",
        custody_mode="CLIENT_SIGNED",
        runtime_mode="LIMITED_EXECUTION",
        is_active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    row = create_or_reuse_order(
        db,
        settings=settings,
        client_id=client.id,
        signal_id=11,
        market_id=21,
        policy_version="stage11_v1",
        side="NO",
        size_bucket="medium",
        notional_usd=120.0,
        requested_price=0.47,
        runtime_mode="LIMITED_EXECUTION",
        unknown_recovery_sec=60,
    )
    db.commit()
    assert row.status == "SUBMITTED"
    assert (row.venue_order_id or "").startswith("dry_")


def test_stage11_reconcile_unknown_submit_recovers_in_dry_run() -> None:
    db = _mk_db()
    settings = get_settings().model_copy(update={"stage11_venue_dry_run": True})
    client = Stage11Client(
        code="c3",
        name="Client 3",
        custody_mode="CLIENT_SIGNED",
        runtime_mode="LIMITED_EXECUTION",
        is_active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    row = Stage11Order(
        client_id=client.id,
        signal_id=None,
        market_id=42,
        platform="POLYMARKET",
        side="YES",
        size_bucket="small",
        notional_usd=50.0,
        requested_price=0.5,
        idempotency_key="manual_unknown",
        policy_version="stage11_v1",
        status="UNKNOWN_SUBMIT",
        venue_order_id="dry_x",
        submit_attempts=1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    out = reconcile_unknown_submits(db, settings=settings, max_unknown_recovery_sec=120)
    db.commit()
    db.refresh(row)
    assert row.status == "FILLED"
    assert int(out.get("recovered") or 0) >= 1
    fill = db.scalar(select(Stage11Fill).where(Stage11Fill.order_id == row.id).limit(1))
    assert fill is not None
    pos = db.scalar(
        select(Stage11ClientPosition)
        .where(Stage11ClientPosition.client_id == client.id)
        .where(Stage11ClientPosition.market_id == row.market_id)
        .where(Stage11ClientPosition.status == "OPEN")
        .limit(1)
    )
    assert pos is not None


def test_stage11_refresh_status_marks_filled_in_dry_run() -> None:
    db = _mk_db()
    settings = get_settings().model_copy(update={"stage11_venue_dry_run": True})
    client = Stage11Client(
        code="c4",
        name="Client 4",
        custody_mode="CLIENT_SIGNED",
        runtime_mode="LIMITED_EXECUTION",
        is_active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    row = create_or_reuse_order(
        db,
        settings=settings,
        client_id=client.id,
        signal_id=12,
        market_id=33,
        policy_version="stage11_v1",
        side="YES",
        size_bucket="small",
        notional_usd=80.0,
        requested_price=0.51,
        runtime_mode="LIMITED_EXECUTION",
        unknown_recovery_sec=60,
    )
    db.commit()
    out = refresh_order_status(db, settings=settings, order_id=int(row.id))
    db.refresh(row)
    assert out.get("status") == "ok"
    assert row.status == "FILLED"


def test_stage11_manual_cancel_works_in_dry_run() -> None:
    db = _mk_db()
    settings = get_settings().model_copy(update={"stage11_venue_dry_run": True})
    client = Stage11Client(
        code="c5",
        name="Client 5",
        custody_mode="CLIENT_SIGNED",
        runtime_mode="LIMITED_EXECUTION",
        is_active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    row = Stage11Order(
        client_id=client.id,
        signal_id=None,
        market_id=77,
        platform="POLYMARKET",
        side="NO",
        size_bucket="small",
        notional_usd=50.0,
        requested_price=0.41,
        idempotency_key="manual_cancel_id",
        policy_version="stage11_v1",
        status="SUBMITTED",
        venue_order_id="dry_123",
        submit_attempts=1,
    )
    db.add(row)
    db.commit()
    out = cancel_order_by_id(db, settings=settings, order_id=int(row.id), reason="test_cancel")
    db.refresh(row)
    assert out.get("status") == "ok"
    assert row.status == "CANCELLED_SAFE"


def test_stage11_submitted_past_deadline_safe_cancelled() -> None:
    db = _mk_db()
    settings = get_settings().model_copy(update={"stage11_venue_dry_run": True})
    client = Stage11Client(
        code="c6",
        name="Client 6",
        custody_mode="CLIENT_SIGNED",
        runtime_mode="LIMITED_EXECUTION",
        is_active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    row = Stage11Order(
        client_id=client.id,
        signal_id=None,
        market_id=88,
        platform="POLYMARKET",
        side="YES",
        size_bucket="small",
        notional_usd=40.0,
        requested_price=0.49,
        idempotency_key="submitted_deadline",
        policy_version="stage11_v1",
        status="SUBMITTED",
        venue_order_id=None,
        submit_attempts=1,
        unknown_recovery_deadline=datetime.now(UTC) - timedelta(seconds=10),
    )
    db.add(row)
    db.commit()
    out = reconcile_unknown_submits(db, settings=settings, max_unknown_recovery_sec=120)
    db.commit()
    db.refresh(row)
    assert row.status == "CANCELLED_SAFE"
    assert int(out.get("safe_cancelled") or 0) >= 1


def test_stage11_partial_then_full_fill_appends_incremental_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _mk_db()
    settings = get_settings().model_copy(update={"stage11_venue_dry_run": False})
    client = Stage11Client(
        code="c7",
        name="Client 7",
        custody_mode="CLIENT_SIGNED",
        runtime_mode="LIMITED_EXECUTION",
        is_active=True,
    )
    db.add(client)
    db.commit()
    db.refresh(client)

    class _FakeAdapter:
        def __init__(self) -> None:
            self.n = 0

        def place_order(self, req):  # noqa: ANN001
            raise AssertionError("not used in this test")

        def cancel_order(self, venue_order_id: str):  # noqa: ARG002
            return Stage11StatusResult(status="CANCELLED_SAFE", response_payload={})

        def fetch_order_status(self, venue_order_id: str):  # noqa: ARG002
            self.n += 1
            if self.n == 1:
                return Stage11StatusResult(
                    status="SUBMITTED",
                    response_payload={"status": "PARTIALLY_FILLED"},
                    fill_price=0.5,
                    fill_size_usd=30.0,
                    fee_usd=0.0,
                    is_partial=True,
                )
            return Stage11StatusResult(
                status="FILLED",
                response_payload={"status": "FILLED"},
                fill_price=0.51,
                fill_size_usd=50.0,  # cumulative
                fee_usd=0.0,
            )

    fake = _FakeAdapter()
    monkeypatch.setattr("app.services.stage11.order_manager.get_stage11_venue_adapter", lambda settings: fake)

    order = Stage11Order(
        client_id=client.id,
        signal_id=None,
        market_id=99,
        platform="POLYMARKET",
        side="YES",
        size_bucket="small",
        notional_usd=50.0,
        requested_price=0.5,
        idempotency_key="partial_flow",
        policy_version="stage11_v1",
        status="SUBMITTED",
        venue_order_id="v1",
        submit_attempts=1,
        unknown_recovery_deadline=datetime.now(UTC) + timedelta(seconds=120),
    )
    db.add(order)
    db.commit()

    out1 = reconcile_unknown_submits(db, settings=settings, max_unknown_recovery_sec=120)
    db.commit()
    db.refresh(order)
    assert order.status == "SUBMITTED"
    fills1 = list(db.scalars(select(Stage11Fill).where(Stage11Fill.order_id == order.id)))
    assert len(fills1) == 1
    assert float(fills1[0].fill_size_usd or 0.0) == 30.0

    out2 = reconcile_unknown_submits(db, settings=settings, max_unknown_recovery_sec=120)
    db.commit()
    db.refresh(order)
    assert order.status == "FILLED"
    fills2 = list(db.scalars(select(Stage11Fill).where(Stage11Fill.order_id == order.id).order_by(Stage11Fill.id.asc())))
    assert len(fills2) == 2
    assert float(fills2[0].fill_size_usd or 0.0) == 30.0
    assert float(fills2[1].fill_size_usd or 0.0) == 20.0  # incremental delta from cumulative 50
    pos = db.scalar(
        select(Stage11ClientPosition)
        .where(Stage11ClientPosition.client_id == client.id)
        .where(Stage11ClientPosition.market_id == order.market_id)
        .where(Stage11ClientPosition.status == "OPEN")
        .limit(1)
    )
    assert pos is not None
    assert float(pos.notional_usd or 0.0) == 50.0
    assert int(out1.get("still_unknown") or 0) >= 0
    assert int(out2.get("filled") or 0) >= 1


def test_stage11_risk_engine_levels() -> None:
    assert (
        resolve_circuit_breaker_level(
            Stage11RiskInput(
                daily_drawdown_pct=-0.1,
                weekly_drawdown_pct=-0.2,
                consecutive_losses=0,
                execution_error_rate_1h=0.0,
                reconciliation_gap_usd=0.0,
            )
        )
        == "OK"
    )
    assert (
        resolve_circuit_breaker_level(
            Stage11RiskInput(
                daily_drawdown_pct=-1.6,
                weekly_drawdown_pct=-0.2,
                consecutive_losses=0,
                execution_error_rate_1h=0.0,
                reconciliation_gap_usd=0.0,
            )
        )
        == "SOFT"
    )
    assert (
        resolve_circuit_breaker_level(
            Stage11RiskInput(
                daily_drawdown_pct=-3.5,
                weekly_drawdown_pct=-0.2,
                consecutive_losses=0,
                execution_error_rate_1h=0.0,
                reconciliation_gap_usd=0.0,
            )
        )
        == "HARD"
    )
    assert (
        resolve_circuit_breaker_level(
            Stage11RiskInput(
                daily_drawdown_pct=-1.0,
                weekly_drawdown_pct=-0.2,
                consecutive_losses=1,
                execution_error_rate_1h=0.20,
                reconciliation_gap_usd=0.0,
            )
        )
        == "PANIC"
    )
