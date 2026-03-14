from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal
from app.services.research.ethics import build_ethics_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _settings(disclaimer: str) -> SimpleNamespace:
    return SimpleNamespace(
        research_ethics_disclaimer_text=disclaimer,
        signal_top_min_score_total=0.45,
        signal_top_min_utility_score=0.08,
        signal_top_max_missing_rules_share=0.35,
        signal_top_min_confidence_missing_rules=0.35,
        signal_top_allow_fallback_when_empty=True,
        signal_top_use_v2_selection=True,
        signal_top_v2_rank_by_score_total=True,
    )


def test_ethics_report_passes_with_disclaimer_and_positive_scores() -> None:
    db = _session()
    p = Platform(name="P", base_url="https://example.test")
    db.add(p)
    db.commit()
    db.refresh(p)
    m = Market(platform_id=p.id, external_market_id="m1", title="M1")
    db.add(m)
    db.commit()
    db.refresh(m)

    db.add(
        Signal(
            signal_type=SignalType.DIVERGENCE,
            market_id=m.id,
            title="Signal 1",
            summary="Summary",
            confidence_score=0.7,
            liquidity_score=0.8,
            score_breakdown_json={"score_total": 0.8},
            created_at=datetime.utcnow(),
        )
    )
    db.commit()
    report = build_ethics_report(
        db,
        top_window=10,
        settings=_settings(
            "This is algorithmic analysis, not financial advice. Prediction markets involve risk. Past performance != future results."
        ),
    )
    assert report["passed"] is True
    assert report["checks"]["has_disclaimer"] is True
    assert report["negative_score_top_count"] == 0


def test_ethics_report_fails_without_disclaimer() -> None:
    db = _session()
    report = build_ethics_report(db, top_window=10, settings=_settings(""))
    assert report["passed"] is False
    assert report["checks"]["has_disclaimer"] is False
