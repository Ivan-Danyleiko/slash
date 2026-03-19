from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import JobRun, Market, Platform, Signal, Stage17TailPosition
from app.services.stage17.tail_executor import run_stage17_tail_cycle


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage17_cycle_opens_and_closes_positions() -> None:
    db = _mk_db()
    platform = Platform(name="POLYMARKET")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="tail-cycle-1",
        title="Will Bitcoin reach $150000 by Dec 31, 2026?",
        probability_yes=0.05,
        status="active",
        volume_24h=20_000.0,
        liquidity_value=50_000.0,
        resolution_time=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(market)
    db.flush()
    signal = Signal(
        signal_type=SignalType.TAIL_EVENT_CANDIDATE,
        market_id=market.id,
        related_market_id=None,
        title="Tail event candidate",
        summary="tail candidate",
        confidence_score=0.7,
        signal_mode="tail_stability",
        signal_direction="YES",
        metadata_json={
            "tail_category": "price_target",
            "tail_mispricing_ratio": 2.5,
            "tail_our_prob": 0.12,
            "tail_market_prob": 0.05,
            "reason_codes": ["tail_category:price_target"],
        },
    )
    db.add(signal)
    db.commit()

    settings = get_settings().model_copy(
        update={
            "signal_tail_enabled": True,
            "signal_tail_reference_balance_usd": 1000.0,
            "signal_tail_notional_pct": 0.005,
        }
    )
    opened = run_stage17_tail_cycle(db, settings=settings, limit=5)
    assert opened["opened"] >= 1

    row = db.scalar(select(Stage17TailPosition).where(Stage17TailPosition.status == "OPEN").limit(1))
    assert row is not None
    assert row.direction == "YES"
    assert float(row.shares_count or 0.0) > 0.0
    assert float(row.current_multiplier or 0.0) >= 1.0

    market.status = "resolved"
    market.source_payload = {"resolvedOutcome": "yes"}
    market.resolution_time = datetime.now(UTC) - timedelta(hours=1)
    db.add(market)
    db.commit()

    closed = run_stage17_tail_cycle(db, settings=settings, limit=5)
    assert closed["closed"] >= 1
    row = db.scalar(select(Stage17TailPosition).where(Stage17TailPosition.id == row.id).limit(1))
    assert row is not None
    assert row.status == "CLOSED"
    assert float(row.realized_pnl_usd or 0.0) > 0.0
    assert float(row.realized_multiplier or 0.0) > 1.0


def test_stage17_cycle_narrative_fade_uses_llm_fallback_hash() -> None:
    db = _mk_db()
    platform = Platform(name="POLYMARKET")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="tail-cycle-2",
        title="Will SEC ban exchange this month?",
        probability_yes=0.06,
        status="active",
        volume_24h=30_000.0,
        liquidity_value=80_000.0,
        rules_text="Resolved by official SEC release.",
    )
    db.add(market)
    db.flush()
    signal = Signal(
        signal_type=SignalType.TAIL_EVENT_CANDIDATE,
        market_id=market.id,
        related_market_id=None,
        title="Tail narrative fade candidate",
        summary="tail candidate",
        confidence_score=0.7,
        signal_mode="tail_narrative_fade",
        signal_direction="NO",
        metadata_json={
            "tail_category": "regulatory",
            "tail_mispricing_ratio": 2.0,
            "tail_our_prob": 0.02,
            "tail_market_prob": 0.06,
            "reason_codes": ["tail_category:regulatory"],
        },
    )
    db.add(signal)
    db.commit()

    settings = get_settings().model_copy(
        update={
            "signal_tail_enabled": True,
            "stage7_agent_real_calls_enabled": False,
            "signal_tail_reference_balance_usd": 1000.0,
            "signal_tail_notional_pct": 0.005,
            "signal_tail_llm_prompt_version": "tail_v1",
        }
    )
    out = run_stage17_tail_cycle(db, settings=settings, limit=5)
    assert out["opened"] >= 1
    row = db.scalar(select(Stage17TailPosition).where(Stage17TailPosition.signal_id == signal.id).limit(1))
    assert row is not None
    assert row.input_hash is not None
    assert len(str(row.prompt_version or "")) >= 8


def test_stage17_cycle_blocks_when_provider_checks_failed() -> None:
    db = _mk_db()
    db.add(
        JobRun(
            job_name="provider_contract_checks",
            status="FAILED",
            details={"has_blocking_issues": True, "providers": [{"provider": "MANIFOLD", "ok": False}]},
            started_at=datetime.now(UTC),
        )
    )
    db.commit()
    settings = get_settings().model_copy(update={"signal_tail_enabled": True})
    out = run_stage17_tail_cycle(db, settings=settings, limit=5)
    assert out.get("breaker_blocked") is True
    assert "provider_checks_failed" in str(out.get("breaker_reason") or "")


def test_stage17_cycle_skips_duplicate_open_market_positions() -> None:
    db = _mk_db()
    platform = Platform(name="POLYMARKET")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="tail-cycle-3",
        title="Will Bitcoin reach $170000 by Dec 31, 2026?",
        probability_yes=0.05,
        status="active",
        volume_24h=15_000.0,
        liquidity_value=40_000.0,
        resolution_time=datetime.now(UTC) + timedelta(days=21),
    )
    db.add(market)
    db.flush()
    db.add_all(
        [
            Signal(
                signal_type=SignalType.TAIL_EVENT_CANDIDATE,
                market_id=market.id,
                related_market_id=None,
                title="Tail candidate A",
                summary="tail candidate",
                confidence_score=0.7,
                signal_mode="tail_stability",
                signal_direction="YES",
                metadata_json={
                    "tail_category": "price_target",
                    "tail_mispricing_ratio": 2.5,
                    "tail_our_prob": 0.12,
                    "tail_market_prob": 0.05,
                },
            ),
            Signal(
                signal_type=SignalType.TAIL_EVENT_CANDIDATE,
                market_id=market.id,
                related_market_id=None,
                title="Tail candidate B",
                summary="tail candidate",
                confidence_score=0.7,
                signal_mode="tail_stability",
                signal_direction="YES",
                metadata_json={
                    "tail_category": "price_target",
                    "tail_mispricing_ratio": 2.6,
                    "tail_our_prob": 0.13,
                    "tail_market_prob": 0.05,
                },
            ),
        ]
    )
    db.commit()

    settings = get_settings().model_copy(
        update={
            "signal_tail_enabled": True,
            "signal_tail_reference_balance_usd": 1000.0,
            "signal_tail_notional_pct": 0.005,
        }
    )
    out = run_stage17_tail_cycle(db, settings=settings, limit=5)
    assert int(out.get("opened") or 0) == 1


def test_stage17_cycle_applies_notional_hard_cap() -> None:
    db = _mk_db()
    platform = Platform(name="POLYMARKET")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="tail-cycle-4",
        title="Will Bitcoin reach $180000 by Dec 31, 2026?",
        probability_yes=0.06,
        status="active",
        volume_24h=50_000.0,
        liquidity_value=100_000.0,
        resolution_time=datetime.now(UTC) + timedelta(days=45),
    )
    db.add(market)
    db.flush()
    signal = Signal(
        signal_type=SignalType.TAIL_EVENT_CANDIDATE,
        market_id=market.id,
        related_market_id=None,
        title="Tail candidate hard cap",
        summary="tail candidate",
        confidence_score=0.7,
        signal_mode="tail_stability",
        signal_direction="YES",
        metadata_json={
            "tail_category": "price_target",
            "tail_mispricing_ratio": 2.0,
            "tail_our_prob": 0.14,
            "tail_market_prob": 0.06,
        },
    )
    db.add(signal)
    db.commit()

    settings = get_settings().model_copy(
        update={
            "signal_tail_enabled": True,
            "signal_tail_reference_balance_usd": 1_000.0,
            "signal_tail_notional_pct": 0.5,  # should be capped to 5%
            "signal_tail_category_limit_disasters": 0.2,
        }
    )
    out = run_stage17_tail_cycle(db, settings=settings, limit=5)
    assert int(out.get("opened") or 0) == 1
    row = db.scalar(select(Stage17TailPosition).where(Stage17TailPosition.signal_id == signal.id).limit(1))
    assert row is not None
    assert float(row.notional_usd or 0.0) <= 50.0


def test_stage17_cycle_does_not_close_without_explicit_resolution_payload() -> None:
    db = _mk_db()
    platform = Platform(name="POLYMARKET")
    db.add(platform)
    db.flush()
    market = Market(
        platform_id=platform.id,
        external_market_id="tail-cycle-5",
        title="Will Bitcoin reach $190000 by Dec 31, 2026?",
        probability_yes=0.05,
        status="active",
        volume_24h=20_000.0,
        liquidity_value=50_000.0,
        resolution_time=datetime.now(UTC) + timedelta(days=35),
    )
    db.add(market)
    db.flush()
    signal = Signal(
        signal_type=SignalType.TAIL_EVENT_CANDIDATE,
        market_id=market.id,
        related_market_id=None,
        title="Tail event candidate no payload",
        summary="tail candidate",
        confidence_score=0.7,
        signal_mode="tail_stability",
        signal_direction="YES",
        metadata_json={
            "tail_category": "price_target",
            "tail_mispricing_ratio": 2.5,
            "tail_our_prob": 0.13,
            "tail_market_prob": 0.05,
        },
    )
    db.add(signal)
    db.commit()
    settings = get_settings().model_copy(
        update={
            "signal_tail_enabled": True,
            "signal_tail_reference_balance_usd": 1000.0,
            "signal_tail_notional_pct": 0.005,
        }
    )
    out = run_stage17_tail_cycle(db, settings=settings, limit=5)
    assert int(out.get("opened") or 0) == 1
    row = db.scalar(select(Stage17TailPosition).where(Stage17TailPosition.signal_id == signal.id).limit(1))
    assert row is not None
    assert row.status == "OPEN"

    # Mark as "closed" but without explicit resolution payload/outcome.
    market.status = "closed"
    market.resolution_time = datetime.now(UTC) - timedelta(hours=1)
    market.source_payload = {}
    db.add(market)
    db.commit()

    out2 = run_stage17_tail_cycle(db, settings=settings, limit=5)
    assert int(out2.get("closed") or 0) == 0
    row = db.scalar(select(Stage17TailPosition).where(Stage17TailPosition.id == row.id).limit(1))
    assert row is not None
    assert row.status == "OPEN"
