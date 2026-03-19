from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.models.models import Stage17TailPosition
from app.services.research.stage17_tail_report import (
    build_stage17_tail_report,
    payout_skew_bootstrap_ci,
)


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage17_tail_report_data_pending_when_not_enough_closed() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    for idx in range(10):
        db.add(
            Stage17TailPosition(
                market_id=idx + 1,
                tail_category="natural_disaster",
                tail_variation="tail_base_rate",
                direction="NO",
                status="CLOSED",
                entry_price=0.03,
                notional_usd=0.5,
                realized_pnl_usd=1.0 if idx % 2 == 0 else -0.5,
                opened_at=now - timedelta(days=2, hours=idx),
                closed_at=now - timedelta(days=1, hours=idx),
            )
        )
    db.commit()
    settings = get_settings()
    report = build_stage17_tail_report(db, settings=settings, days=60, persist=True)
    assert report["final_decision"] == "NO_GO_DATA_PENDING"
    assert int((report.get("summary") or {}).get("closed_positions") or 0) == 10


def test_stage17_tail_report_acceptance_passes_with_skewed_wins() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    win_pnls = [50.0, 45.0, 40.0] + [1.0] * 22
    loss_pnls = [-1.0] * 15
    all_pnls = win_pnls + loss_pnls
    for idx, pnl in enumerate(all_pnls):
        opened_at = now - timedelta(days=10, hours=idx)
        if idx < 6:
            opened_at = now - timedelta(hours=idx + 1)
        db.add(
            Stage17TailPosition(
                market_id=100 + idx,
                tail_category="political_stability" if idx % 2 else "natural_disaster",
                tail_variation="tail_stability",
                direction="NO",
                status="CLOSED",
                entry_price=0.02,
                notional_usd=0.5,
                koef_entry=10.0,
                days_to_resolution_entry=20.0,
                realized_pnl_usd=pnl,
                opened_at=opened_at,
                closed_at=now - timedelta(days=3, hours=idx),
            )
        )
    db.commit()
    settings = get_settings().model_copy(
        update={
            "stage17_tail_min_closed_positions": 40,
            "stage17_tail_min_top10pct_wins_count": 3,
            "stage17_tail_min_hit_rate": 0.60,
            "stage17_tail_min_payout_skew": 0.50,
            "stage17_tail_min_payout_skew_ci_low_80": 0.35,
            "stage17_tail_max_time_to_resolution_days": 30.0,
            "stage17_tail_min_avg_win_multiplier": 5.0,
            "stage17_tail_bootstrap_resamples": 400,
        }
    )
    report = build_stage17_tail_report(db, settings=settings, days=60, persist=False)
    checks = ((report.get("summary") or {}).get("checks") or {})
    assert checks.get("closed_positions_ge_min") is True
    assert checks.get("top10pct_wins_count_ge_min") is True
    assert checks.get("hit_rate_tail_ge_min") is True
    assert checks.get("payout_skew_ge_min") is True
    assert checks.get("payout_skew_ci_low_80_ge_min") is True
    assert checks.get("max_avg_days_to_res") is True
    assert checks.get("avg_win_multiplier_ge_min") is True
    by_variation = dict(report.get("by_variation") or {})
    assert "tail_stability" in by_variation
    assert float((by_variation.get("tail_stability") or {}).get("closed") or 0.0) > 0.0
    assert report["final_decision"] == "LIMITED_GO"


def test_stage17_payout_skew_bootstrap_ci_is_deterministic() -> None:
    pnls = [5.0, 3.0, 2.0, -1.0, -1.0, -1.0, -1.0, -1.0]
    lo1, hi1 = payout_skew_bootstrap_ci(pnls, n_bootstrap=250, seed=42)
    lo2, hi2 = payout_skew_bootstrap_ci(pnls, n_bootstrap=250, seed=42)
    assert lo1 == lo2
    assert hi1 == hi2
    assert 0.0 <= lo1 <= hi1 <= 1.0
