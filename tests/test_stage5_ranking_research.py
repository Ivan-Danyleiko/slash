from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, Signal, SignalHistory
from app.services.research.ranking_research import build_ranking_research_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_ranking_research_report_compares_formulas() -> None:
    db = _session()
    now = datetime.now(UTC)
    p = Platform(name="P", base_url="https://example.test")
    db.add(p)
    db.commit()
    db.refresh(p)
    market = Market(platform_id=p.id, external_market_id="m1", title="M1")
    db.add(market)
    db.commit()
    db.refresh(market)

    signals: list[Signal] = []
    # High edge (should perform best in edge_only ranking)
    for i in range(6):
        s = Signal(
            signal_type=SignalType.DIVERGENCE,
            market_id=market.id,
            title=f"S_high_edge_{i}",
            summary="ok",
            confidence_score=0.6,
            liquidity_score=0.2,
            score_breakdown_json={"edge": 0.9, "liquidity": 0.2, "freshness": 0.6, "score_total": 0.6},
            created_at=now - timedelta(hours=10, minutes=i),
        )
        db.add(s)
        signals.append(s)

    # High liquidity/score_total but lower edge
    for i in range(6):
        s = Signal(
            signal_type=SignalType.DIVERGENCE,
            market_id=market.id,
            title=f"S_high_liq_{i}",
            summary="ok",
            confidence_score=0.6,
            liquidity_score=0.9,
            score_breakdown_json={"edge": 0.2, "liquidity": 0.9, "freshness": 0.6, "score_total": 0.85},
            created_at=now - timedelta(hours=9, minutes=i),
        )
        db.add(s)
        signals.append(s)
    db.commit()
    for s in signals:
        db.refresh(s)

    # Returns: high-edge signals perform better.
    for idx, s in enumerate(signals):
        positive = idx < 6
        base = 0.45
        after = base + (0.03 if positive else -0.01)
        db.add(
            SignalHistory(
                signal_id=s.id,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=8, minutes=idx),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=base,
                divergence=0.08,
                liquidity=0.7,
                volume_24h=1000.0,
                probability_after_6h=after,
            )
        )
    db.commit()

    report = build_ranking_research_report(db, days=3, horizon="6h", top_k=6, min_samples=5)
    assert report["sufficient_samples"] is True
    assert report["samples_total"] == 12
    assert len(report["formulas"]) == 6
    assert {row["formula"] for row in report["formulas"]} >= {
        "legacy_rank_score",
        "appendix_c_score",
        "score_total",
        "edge_only",
    }
    assert report["best_formula"] in {
        "legacy_rank_score",
        "appendix_c_score",
        "edge_only",
        "edge_plus_liquidity",
        "edge_plus_liquidity_plus_freshness",
        "score_total",
    }


def test_ranking_research_report_marks_insufficient_samples() -> None:
    db = _session()
    report = build_ranking_research_report(db, days=3, horizon="6h", top_k=10, min_samples=5)
    assert report["samples_total"] == 0
    assert report["sufficient_samples"] is False
