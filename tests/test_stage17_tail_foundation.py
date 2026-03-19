from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal, Stage17TailPosition
from app.services.signals.engine import SignalEngine
from app.services.signals.tail_circuit_breaker import can_open_tail_by_category, check_tail_circuit_breaker
from app.services.signals.tail_classifier import classify_tail_event


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage17_tail_classifier_blocks_ambiguity() -> None:
    market = Market(
        platform_id=1,
        external_market_id="m1",
        title="Will Team A win the championship final?",
        rules_text="Resolution at our discretion.",
        probability_yes=0.04,
        volume_24h=1000.0,
        resolution_time=datetime.now(UTC) + timedelta(days=7),
    )
    out = classify_tail_event(market, settings=SignalEngine(_mk_db()).settings)
    assert out is not None
    assert out["eligible"] is False
    assert "tail_resolution_ambiguity" in str(out.get("skip_reason"))


def test_stage17_tail_classifier_blocks_tbd_resolution_source() -> None:
    market = Market(
        platform_id=1,
        external_market_id="m1b",
        title="Will Bitcoin reach $150000 this month?",
        rules_text="Resolution source: TBD by committee.",
        probability_yes=0.04,
        volume_24h=1000.0,
        resolution_time=datetime.now(UTC) + timedelta(days=14),
    )
    out = classify_tail_event(market, settings=SignalEngine(_mk_db()).settings)
    assert out is not None
    assert out["eligible"] is False
    assert "tail_resolution_ambiguity" in str(out.get("skip_reason"))


def test_stage17_tail_circuit_breaker_budget_and_category_limit() -> None:
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
    blocked, reason = check_tail_circuit_breaker(
        db, settings=settings, balance_usd=100.0, api_status={"degraded": False}
    )
    assert blocked is True
    assert "tail_budget_exhausted" in reason

    allowed, _ = can_open_tail_by_category(
        db,
        settings=settings,
        category="natural_disaster",
        notional_usd=0.5,
        balance_usd=100.0,
    )
    assert allowed is False


def test_stage17_tail_circuit_breaker_uses_crypto_alias_limit() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings
    db.add(
        Stage17TailPosition(
            market_id=1,
            tail_category="crypto_level",
            tail_variation="tail_base_rate",
            direction="YES",
            status="OPEN",
            entry_price=0.04,
            notional_usd=19.9,
        )
    )
    db.commit()
    allowed, reason = can_open_tail_by_category(
        db,
        settings=settings,
        category="crypto_level",
        notional_usd=0.2,
        balance_usd=100.0,
    )
    assert allowed is False
    assert "tail_category_limit:crypto_level" in reason


def test_stage17_tail_circuit_breaker_uses_zero_event_limit() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings
    db.add(
        Stage17TailPosition(
            market_id=2,
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


def test_stage17_tail_circuit_breaker_respects_loss_threshold_and_cooldown() -> None:
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
                market_id=10,
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
                market_id=11,
                tail_category="natural_disaster",
                tail_variation="tail_stability",
                direction="NO",
                status="CLOSED",
                entry_price=0.05,
                notional_usd=0.5,
                realized_pnl_usd=-0.5,
                opened_at=now - timedelta(hours=3),
                closed_at=now - timedelta(hours=2),
            ),
        ]
    )
    db.commit()
    blocked, reason = check_tail_circuit_breaker(
        db,
        settings=settings,
        balance_usd=100.0,
        api_status={"degraded": False},
    )
    assert blocked is True
    assert "tail_consecutive_losses_2_cooldown_24h" in reason


def test_stage17_signal_engine_creates_tail_candidate_when_enabled() -> None:
    db = _mk_db()
    platform = Platform(name="POLYMARKET")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="tail-1",
        title="Will Bitcoin reach $150000 by Dec 31, 2026?",
        probability_yes=0.05,
        status="active",
        volume_24h=50_000.0,
        liquidity_value=100_000.0,
        resolution_time=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(market)
    db.commit()

    engine = SignalEngine(db)
    engine.settings = engine.settings.model_copy(
        update={
            "signal_tail_enabled": True,
            "signal_tail_base_rate_external_enabled": False,
            "signal_tail_min_mispricing_ratio": 0.5,
            "signal_tail_max_candidates": 5,
        }
    )
    result = engine.generate_signals()
    assert int(result.get("signals_created") or 0) >= 1
    signal = db.scalar(
        select(Signal).where(Signal.signal_type == SignalType.TAIL_EVENT_CANDIDATE).limit(1)
    )
    assert signal is not None
    assert (signal.signal_mode or "").startswith("tail_")
    assert (signal.metadata_json or {}).get("tail_category") in {"price_target", "crypto_level"}
