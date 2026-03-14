from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, MarketSnapshot, Platform, SignalHistory
from app.services.research.signal_lifetime import build_signal_lifetime_report
from app.services.research.walkforward import build_walkforward_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _seed_platform_market(db: Session) -> Market:
    p = Platform(name="POLYMARKET", base_url="https://gamma-api.polymarket.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    market = Market(
        platform_id=p.id,
        external_market_id="m1",
        title="M1",
        probability_yes=0.55,
        volume_24h=5000,
        liquidity_value=12000,
    )
    db.add(market)
    db.commit()
    db.refresh(market)
    return market


def test_walkforward_report_has_windows_and_low_confidence_flag() -> None:
    db = _session()
    market = _seed_platform_market(db)
    now = datetime.now(UTC)

    rows = []
    for i in range(50):
        ts = now - timedelta(days=80) + timedelta(days=i)
        p0 = 0.45
        p6 = 0.46 if i % 2 == 0 else 0.44
        rows.append(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=ts,
                platform="POLYMARKET",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=p0,
                probability_after_6h=p6,
                divergence=0.1,
                liquidity=0.7,
                volume_24h=1000.0,
            )
        )
    db.add_all(rows)
    db.commit()

    report = build_walkforward_report(
        db,
        days=90,
        horizon="6h",
        signal_type="DIVERGENCE",
        train_days=20,
        test_days=10,
        step_days=10,
        embargo_hours=24,
        min_samples_per_window=100,
    )

    assert "rows" in report
    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["signal_type"] == SignalType.DIVERGENCE.value
    assert row["windows_count"] > 0
    assert row["low_confidence"] is True


def test_signal_lifetime_subhour_from_snapshots() -> None:
    db = _session()
    market = _seed_platform_market(db)
    now = datetime.now(UTC)

    sig_ts = now - timedelta(hours=2)
    sh = SignalHistory(
        signal_id=None,
        signal_type=SignalType.DIVERGENCE,
        timestamp=sig_ts,
        platform="POLYMARKET",
        market_id=market.id,
        related_market_id=None,
        probability_at_signal=0.60,
        related_market_probability=0.40,
        divergence=0.20,
        liquidity=0.8,
        volume_24h=2000.0,
    )
    db.add(sh)
    db.add(
        MarketSnapshot(
            market_id=market.id,
            probability_yes=0.48,
            probability_no=0.52,
            fetched_at=sig_ts + timedelta(minutes=15),
        )
    )
    db.commit()

    report = build_signal_lifetime_report(
        db,
        days=3,
        signal_type="DIVERGENCE",
        close_ratio_threshold=0.5,
        min_initial_divergence=0.02,
        min_samples=1,
        include_subhour=True,
        subhour_grace_minutes=5,
    )

    assert "rows" in report
    row = report["rows"][0]
    assert row["status"] == "OK"
    assert row["close_rate_15m"] == 1.0
    assert row["subhour_coverage"] == 1.0
