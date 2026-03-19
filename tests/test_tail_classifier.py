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
        title="Will there be a hurricane in Florida this week?",
        probability_yes=0.03,
    )
    out = classify_tail_event(market, settings=settings)
    assert out is not None
    assert out.get("eligible") is True
    assert out.get("tail_category") == "natural_disaster"


def test_tail_classifier_ambiguity_hard_block() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings
    market = Market(
        platform_id=1,
        external_market_id="tc-2",
        title="Will there be exactly 0 earthquakes tomorrow?",
        rules_text="Resolution source: TBD by admin decision.",
        probability_yes=0.04,
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
        title="Will there be exactly 0 earthquakes tomorrow?",
        probability_yes=float("nan"),
    )
    out = classify_tail_event(market, settings=settings)
    assert out is None


def test_tail_classifier_handles_large_rules_text() -> None:
    db = _mk_db()
    settings = SignalEngine(db).settings
    market = Market(
        platform_id=1,
        external_market_id="tc-4",
        title="Will there be a hurricane in Florida this week?",
        probability_yes=0.03,
        rules_text=("official source: USGS. " + ("x" * 120_000)),
    )
    out = classify_tail_event(market, settings=settings)
    assert out is not None
