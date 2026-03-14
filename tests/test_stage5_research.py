from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import Market, Platform, SignalHistory
from app.services.research.stage5 import (
    build_divergence_decision,
    build_monte_carlo_summary,
    build_result_tables,
    build_signal_history_dataset,
    build_threshold_summary,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _platform(db: Session) -> Platform:
    p = Platform(name="P", base_url="https://example.test")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_build_signal_history_dataset_metrics() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.utcnow()
    market = Market(
        platform_id=p.id,
        external_market_id="m1",
        title="M1",
        probability_yes=0.55,
        volume_24h=1000,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    db.add_all(
        [
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=8),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=0.40,
                divergence=0.10,
                liquidity=0.8,
                volume_24h=1000.0,
                probability_after_6h=0.45,
            ),
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=7),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=0.60,
                divergence=0.12,
                liquidity=0.7,
                volume_24h=900.0,
                probability_after_6h=0.58,
            ),
        ]
    )
    db.commit()

    result = build_signal_history_dataset(db, days=3, horizon="6h", signal_type="DIVERGENCE", limit=100)

    assert result["metrics"]["rows"] == 2
    assert result["metrics"]["returns_labeled"] == 2
    assert result["metrics"]["hit_rate"] == 0.5
    assert result["metrics"]["avg_return"] == 0.015


def test_build_threshold_summary_has_monotonic_sample_sizes() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.utcnow()
    market = Market(
        platform_id=p.id,
        external_market_id="m2",
        title="M2",
        probability_yes=0.5,
        volume_24h=1000,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    db.add_all(
        [
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=10),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=0.45,
                divergence=0.04,
                liquidity=0.6,
                volume_24h=500.0,
                probability_after_6h=0.47,
            ),
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=9),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=0.45,
                divergence=0.09,
                liquidity=0.7,
                volume_24h=700.0,
                probability_after_6h=0.50,
            ),
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=8),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=0.45,
                divergence=0.16,
                liquidity=0.8,
                volume_24h=900.0,
                probability_after_6h=0.52,
            ),
        ]
    )
    db.commit()

    result = build_threshold_summary(
        db,
        days=3,
        horizon="6h",
        thresholds=[0.03, 0.05, 0.10, 0.15],
        signal_type="DIVERGENCE",
    )
    rows = result["threshold_summary"]
    sample_sizes = [r["sample_size"] for r in rows]
    assert sample_sizes == sorted(sample_sizes, reverse=True)
    assert sample_sizes == [3, 2, 1, 1]


def test_build_divergence_decision_insufficient_data_then_modify() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.utcnow()
    market = Market(
        platform_id=p.id,
        external_market_id="m3",
        title="M3",
        probability_yes=0.5,
        volume_24h=2000,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    # First, only a few labeled returns -> insufficient data.
    db.add_all(
        [
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=6),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=0.40,
                divergence=0.08,
                liquidity=0.8,
                volume_24h=1000.0,
                probability_after_6h=0.41,
            ),
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=5),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=0.42,
                divergence=0.09,
                liquidity=0.8,
                volume_24h=1000.0,
                probability_after_6h=0.43,
            ),
        ]
    )
    db.commit()

    insufficient = build_divergence_decision(
        db,
        days=3,
        horizon="6h",
        thresholds=[0.05, 0.08],
        min_labeled_returns=5,
    )
    assert insufficient["decision"] == "INSUFFICIENT_DATA"

    # Add enough labeled rows with modest positive EV -> MODIFY expected.
    more_rows = []
    for i in range(8):
        base = 0.45 + (i * 0.001)
        more_rows.append(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=4, minutes=i),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=base,
                divergence=0.1,
                liquidity=0.8,
                volume_24h=1000.0,
                probability_after_6h=base + 0.006,
            )
        )
    db.add_all(more_rows)
    db.commit()

    decision = build_divergence_decision(
        db,
        days=3,
        horizon="6h",
        thresholds=[0.05, 0.08],
        min_labeled_returns=5,
        keep_ev_min=0.01,
        keep_hit_rate_min=0.52,
        modify_ev_min=0.005,
    )
    assert decision["decision"] in {"MODIFY", "KEEP"}
    assert decision["recommended_threshold"] is not None
    assert decision["best_threshold_metrics"]["returns_labeled"] >= 5


def test_build_monte_carlo_summary_has_risk_metrics() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.utcnow()
    market = Market(
        platform_id=p.id,
        external_market_id="m4",
        title="M4",
        probability_yes=0.5,
        volume_24h=2000,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    for i in range(20):
        base = 0.45 + (i * 0.001)
        # Alternating wins/losses to ensure non-zero variance.
        step = 0.01 if i % 2 == 0 else -0.005
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=8, minutes=i),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=base,
                divergence=0.08,
                liquidity=0.8,
                volume_24h=1000.0,
                probability_after_6h=base + step,
            )
        )
    db.commit()

    result = build_monte_carlo_summary(
        db,
        days=3,
        horizon="6h",
        signal_type="DIVERGENCE",
        min_divergence=0.05,
        n_sims=200,
        trades_per_sim=50,
        initial_capital=1000.0,
        position_size_usd=100.0,
        seed=7,
    )

    assert result["observed"]["returns_labeled"] == 20
    assert "monte_carlo" in result
    assert 0.0 <= result["monte_carlo"]["risk_of_ruin"] <= 1.0
    assert result["monte_carlo"]["n_sims"] == 200
    assert result["monte_carlo"]["trades_per_sim"] == 50


def test_build_result_tables_has_best_and_bad_sections() -> None:
    db = _session()
    p = _platform(db)
    now = datetime.utcnow()
    market = Market(
        platform_id=p.id,
        external_market_id="m5",
        title="M5",
        probability_yes=0.5,
        volume_24h=2500,
    )
    db.add(market)
    db.commit()
    db.refresh(market)

    # Positive expected returns for DIVERGENCE
    for i in range(12):
        base = 0.40 + (i * 0.002)
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.DIVERGENCE,
                timestamp=now - timedelta(hours=9, minutes=i),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=base,
                divergence=0.09,
                liquidity=0.8,
                volume_24h=1200.0,
                probability_after_6h=base + 0.01,
            )
        )
    # Negative expected returns for WEIRD_MARKET
    for i in range(12):
        base = 0.60 - (i * 0.001)
        db.add(
            SignalHistory(
                signal_id=None,
                signal_type=SignalType.WEIRD_MARKET,
                timestamp=now - timedelta(hours=7, minutes=i),
                platform="P",
                market_id=market.id,
                related_market_id=None,
                probability_at_signal=base,
                divergence=0.02,
                liquidity=0.6,
                volume_24h=800.0,
                probability_after_6h=base - 0.01,
            )
        )
    db.commit()

    result = build_result_tables(db, days=3, horizon="6h", min_samples=10)
    best_types = {row["signal_type"] for row in result["table_best_signals"]}
    bad_types = {row["signal_type"] for row in result["table_bad_signals"]}
    assert "DIVERGENCE" in best_types
    assert "WEIRD_MARKET" in bad_types
