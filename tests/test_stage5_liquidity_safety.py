from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import SignalHistory
from app.services.research.liquidity_safety import build_liquidity_safety_report


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def test_liquidity_safety_report_estimates_safe_sizes() -> None:
    db = _session()
    now = datetime.now(UTC)
    # Strong capacity rows.
    for i in range(12):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=8, minutes=i),
                platform="P",
                market_id=1,
                probability_at_signal=0.45,
                probability_after_6h=0.47,
                liquidity=0.8,
                volume_24h=20000.0,
                simulated_trade={"capacity_usd": 600.0},
            )
        )
    # Weak capacity rows.
    for i in range(12):
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.RULES_RISK,
                timestamp=now - timedelta(hours=7, minutes=i),
                platform="P",
                market_id=2,
                probability_at_signal=0.55,
                probability_after_6h=0.54,
                liquidity=0.2,
                volume_24h=500.0,
                simulated_trade={"capacity_usd": 40.0},
            )
        )
    db.commit()

    report = build_liquidity_safety_report(
        db,
        days=3,
        position_sizes="50,100,500",
        max_slippage_pct=0.02,
        min_samples=10,
    )
    rows = {r["signal_type"]: r for r in report["rows"] if r.get("status") == "OK"}
    assert "DIVERGENCE" in rows
    assert rows["DIVERGENCE"]["max_trade_size_without_excess_slippage_usd"] >= 100
    assert "RULES_RISK" in rows
    assert rows["RULES_RISK"]["max_trade_size_without_excess_slippage_usd"] <= 50


def test_liquidity_safety_report_unsupported_signal_type() -> None:
    db = _session()
    report = build_liquidity_safety_report(db, signal_type="UNKNOWN")
    assert "error" in report
