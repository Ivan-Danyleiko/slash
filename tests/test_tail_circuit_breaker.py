from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.models import Stage17TailPosition
from app.services.signals.engine import SignalEngine
from app.services.signals.tail_circuit_breaker import can_open_tail_by_category, check_tail_circuit_breaker


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_tail_circuit_breaker_budget_exhausted() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings
    db.add(
        Stage17TailPosition(
            market_id=1,
            tail_category="natural_disaster",
            tail_variation="tail_stability",
            direction="NO",
            status="OPEN",
            entry_price=0.03,
            notional_usd=10.0,
        )
    )
    db.commit()
    blocked, reason = check_tail_circuit_breaker(db, settings=settings, balance_usd=100.0, api_status={"degraded": False})
    assert blocked is True
    assert "tail_budget_exhausted" in reason


def test_tail_circuit_breaker_consecutive_losses_cooldown() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings.model_copy(
        update={
            "signal_tail_circuit_breaker_consecutive_losses": 2,
            "signal_tail_circuit_breaker_cooldown_hours": 24,
        }
    )
    now = datetime.now(UTC)
    db.add_all(
        [
            Stage17TailPosition(
                market_id=2,
                tail_category="natural_disaster",
                tail_variation="tail_stability",
                direction="NO",
                status="CLOSED",
                entry_price=0.04,
                notional_usd=0.5,
                realized_pnl_usd=-0.5,
                opened_at=now - timedelta(hours=5),
                closed_at=now - timedelta(hours=4),
            ),
            Stage17TailPosition(
                market_id=3,
                tail_category="natural_disaster",
                tail_variation="tail_stability",
                direction="NO",
                status="CLOSED",
                entry_price=0.04,
                notional_usd=0.5,
                realized_pnl_usd=-0.5,
                opened_at=now - timedelta(hours=3),
                closed_at=now - timedelta(hours=2),
            ),
        ]
    )
    db.commit()
    blocked, reason = check_tail_circuit_breaker(db, settings=settings, balance_usd=100.0, api_status={"degraded": False})
    assert blocked is True
    assert "tail_consecutive_losses_2_cooldown_24h" in reason


def test_tail_circuit_breaker_category_limit() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings
    db.add(
        Stage17TailPosition(
            market_id=4,
            tail_category="zero_event",
            tail_variation="tail_stability",
            direction="YES",
            status="OPEN",
            entry_price=0.02,
            notional_usd=19.95,
        )
    )
    db.commit()
    allowed, reason = can_open_tail_by_category(
        db,
        settings=settings,
        category="zero_event",
        notional_usd=0.1,
        balance_usd=100.0,
    )
    assert allowed is False
    assert "tail_category_limit:zero_event" in reason


def test_tail_circuit_breaker_blocks_invalid_budget_config() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings.model_copy(update={"signal_tail_budget_pct": float("nan")})
    blocked, reason = check_tail_circuit_breaker(db, settings=settings, balance_usd=100.0, api_status={"degraded": False})
    assert blocked is True
    assert reason == "tail_budget_config_invalid"


def test_tail_circuit_breaker_budget_disabled_reason() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings.model_copy(update={"signal_tail_budget_pct": 0.0})
    blocked, reason = check_tail_circuit_breaker(db, settings=settings, balance_usd=100.0, api_status={"degraded": False})
    assert blocked is True
    assert reason == "tail_budget_disabled"
