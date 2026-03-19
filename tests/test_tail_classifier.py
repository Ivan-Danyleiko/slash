from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.models import Market
from app.services.signals.engine import SignalEngine
from app.services.signals.tail_classifier import classify_tail_event


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_tail_classifier_category_detection() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings
    market = Market(
        platform_id=1,
        external_market_id="tc-1",
        title="Will Bitcoin reach $150000 by Dec 31, 2026?",
        probability_yes=0.05,
        volume_24h=1000.0,
        resolution_time=datetime.now(UTC) + timedelta(days=30),
    )
    out = classify_tail_event(market, settings=settings)
    assert out is not None
    assert out.get("eligible") is True
    assert out.get("tail_category") in {"price_target", "crypto_level"}


def test_tail_classifier_ambiguity_hard_block() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings
    market = Market(
        platform_id=1,
        external_market_id="tc-2",
        title="Will Team A win the championship final?",
        rules_text="Resolution source: TBD by admin decision.",
        probability_yes=0.04,
        volume_24h=1200.0,
        resolution_time=datetime.now(UTC) + timedelta(days=7),
    )
    out = classify_tail_event(market, settings=settings)
    assert out is not None
    assert out.get("eligible") is False
    assert "tail_resolution_ambiguity" in str(out.get("skip_reason") or "")


def test_tail_classifier_rejects_non_finite_probability() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings
    market = Market(
        platform_id=1,
        external_market_id="tc-3",
        title="Will Team A win match?",
        probability_yes=float("nan"),
        volume_24h=1200.0,
        resolution_time=datetime.now(UTC) + timedelta(days=7),
    )
    out = classify_tail_event(market, settings=settings)
    assert out is None


def test_tail_classifier_handles_large_rules_text() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings
    market = Market(
        platform_id=1,
        external_market_id="tc-4",
        title="Will Bitcoin reach $200000 this year?",
        probability_yes=0.05,
        volume_24h=3000.0,
        resolution_time=datetime.now(UTC) + timedelta(days=14),
        rules_text=("official source: USGS. " + ("x" * 120_000)),
    )
    out = classify_tail_event(market, settings=settings)
    assert out is not None
