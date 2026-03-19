from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import DuplicateMarketPair, DryrunPosition, Market, Platform, Signal, Stage7AgentDecision
from app.services.dryrun.cross_platform import get_cross_platform_prob
from app.services.dryrun.reporter import build_report, get_portfolio_snapshot
from app.services.dryrun.kelly import kelly_fraction, portfolio_kelly_adjustment
from app.services.dryrun.scorer import composite_score
from app.services.dryrun.simulator import _load_latest_keep_rows, get_or_create_portfolio, refresh_mark_prices


def test_composite_score_prefers_clob_and_higher_daily_ev() -> None:
    a = composite_score(
        daily_ev_pct=0.002,
        spread=0.02,
        volume_usd=50_000,
        confidence=0.7,
        days_to_resolution=20,
        kelly_fraction=0.04,
        is_clob=True,
    )
    b = composite_score(
        daily_ev_pct=0.0005,
        spread=0.04,
        volume_usd=10_000,
        confidence=0.6,
        days_to_resolution=90,
        kelly_fraction=0.01,
        is_clob=False,
    )
    assert a > b
    assert 0.0 <= a <= 1.15


def test_kelly_fraction_binary_market_fractional() -> None:
    # cheap YES contract with positive edge
    f = kelly_fraction(market_price=0.30, our_prob=0.40, alpha=0.25, max_fraction=0.10)
    assert f > 0.0
    assert f <= 0.10

    # no edge -> zero size
    assert kelly_fraction(market_price=0.55, our_prob=0.50) == 0.0


def test_portfolio_kelly_adjustment_respects_remaining_capacity() -> None:
    # near capacity (87.5% fill) → capped at remaining_capacity=0.05
    adjusted = portfolio_kelly_adjustment(base_kelly=0.08, total_open_notional_pct=0.35, max_total_exposure=0.40)
    assert 0.0 <= adjusted <= 0.051  # remaining_capacity cap, float tolerance

    # at full capacity should block
    assert portfolio_kelly_adjustment(base_kelly=0.05, total_open_notional_pct=0.40, max_total_exposure=0.40) == 0.0

    # well below capacity → no scaling applied
    adjusted_free = portfolio_kelly_adjustment(base_kelly=0.05, total_open_notional_pct=0.30, max_total_exposure=0.80)
    assert adjusted_free == 0.05


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_load_latest_keep_rows_includes_non_clob_market() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    p = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(p)
    db.flush()
    m = Market(
        platform_id=p.id,
        external_market_id="m1",
        title="non clob market",
        probability_yes=0.61,
        best_bid_yes=None,
        best_ask_yes=None,
        fetched_at=now,
    )
    db.add(m)
    db.flush()
    s = Signal(
        signal_type=SignalType.ARBITRAGE_CANDIDATE,
        market_id=m.id,
        title=m.title,
        summary="x",
        signal_direction="YES",
        created_at=now,
    )
    db.add(s)
    db.flush()
    db.add(
        Stage7AgentDecision(
            signal_id=s.id,
            input_hash="h_non_clob",
            base_decision="KEEP",
            decision="KEEP",
            evidence_bundle={"expected_ev_pct": 0.03},
            model_version="v1",
            created_at=now,
        )
    )
    db.commit()

    rows = _load_latest_keep_rows(db, limit=10)
    assert len(rows) == 1
    assert int(rows[0][0].id) == int(s.id)


def test_partial_stop_loss_runs_once() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    p = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(p)
    db.flush()
    m = Market(
        platform_id=p.id,
        external_market_id="m2",
        title="partial stop test",
        probability_yes=0.58,
        fetched_at=now,
    )
    db.add(m)
    db.flush()
    portfolio = get_or_create_portfolio(db)
    pos = DryrunPosition(
        portfolio_id=portfolio.id,
        market_id=m.id,
        direction="YES",
        entry_price=0.50,
        mark_price=0.50,
        notional_usd=10.0,
        shares_count=20.0,
        status="OPEN",
        open_reason="kelly=0.02,peak=0.500000",
        opened_at=now - timedelta(days=1),
    )
    db.add(pos)
    db.commit()
    db.refresh(pos)

    # 0.30 is below partial threshold (0.325), above full threshold (0.20)
    m.probability_yes = 0.30
    db.commit()
    refresh_mark_prices(db)
    db.refresh(pos)
    shares_after_first = pos.shares_count
    assert shares_after_first == 10.0

    # second refresh should not cut again
    refresh_mark_prices(db)
    db.refresh(pos)
    assert pos.shares_count == shares_after_first


def test_time_exit_not_triggered_before_min_hold_days() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    p = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(p)
    db.flush()
    m = Market(
        platform_id=p.id,
        external_market_id="m3",
        title="time-exit hold test",
        probability_yes=0.5001,
        fetched_at=now,
        resolution_time=now + timedelta(days=30),
    )
    db.add(m)
    db.flush()
    portfolio = get_or_create_portfolio(db)
    pos = DryrunPosition(
        portfolio_id=portfolio.id,
        market_id=m.id,
        direction="YES",
        entry_price=0.50,
        mark_price=0.50,
        notional_usd=10.0,
        shares_count=20.0,
        status="OPEN",
        open_reason="kelly=0.02,peak=0.500000",
        opened_at=now - timedelta(days=3),  # below TIME_EXIT_MIN_HOLD_DAYS=7
        resolution_deadline=now + timedelta(days=30),
    )
    db.add(pos)
    db.commit()

    refresh_mark_prices(db)
    db.refresh(pos)
    assert pos.status == "OPEN"


def test_brier_score_for_no_position_uses_yes_coordinate() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    p = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(p)
    db.flush()
    m = Market(
        platform_id=p.id,
        external_market_id="m4",
        title="brier no test",
        probability_yes=0.6,
        fetched_at=now,
    )
    db.add(m)
    db.flush()
    portfolio = get_or_create_portfolio(db)
    pos = DryrunPosition(
        portfolio_id=portfolio.id,
        market_id=m.id,
        direction="NO",
        entry_price=0.40,  # P(NO)=0.40 => P(YES)=0.60
        mark_price=0.40,
        notional_usd=10.0,
        shares_count=25.0,
        status="CLOSED",
        close_reason="resolved_yes",
        realized_pnl_usd=-10.0,
        opened_at=now - timedelta(days=10),
        closed_at=now - timedelta(days=1),
    )
    db.add(pos)
    db.commit()

    rep = build_report(db)
    # y_pred_yes=0.60, y_true_yes=1.0 => (0.4)^2 = 0.16
    assert abs(float(rep["stats"]["brier_score"]) - 0.16) < 1e-6


def test_cross_platform_prob_uses_duplicate_pairs_and_weights() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    p_poly = Platform(name="POLYMARKET", base_url="https://poly")
    p_man = Platform(name="MANIFOLD", base_url="https://manifold")
    db.add_all([p_poly, p_man])
    db.flush()

    base = Market(
        platform_id=p_poly.id,
        external_market_id="base",
        title="base",
        probability_yes=0.40,
        volume_24h=10_000.0,
        fetched_at=now,
    )
    other = Market(
        platform_id=p_man.id,
        external_market_id="other",
        title="other",
        probability_yes=0.60,
        volume_24h=20_000.0,
        fetched_at=now,
    )
    same_platform = Market(
        platform_id=p_poly.id,
        external_market_id="same",
        title="same",
        probability_yes=0.90,
        volume_24h=20_000.0,
        fetched_at=now,
    )
    db.add_all([base, other, same_platform])
    db.flush()

    db.add_all(
        [
            DuplicateMarketPair(
                market_a_id=base.id,
                market_b_id=other.id,
                similarity_score=100.0,
                divergence_score=0.2,
            ),
            DuplicateMarketPair(
                market_a_id=base.id,
                market_b_id=same_platform.id,
                similarity_score=100.0,
                divergence_score=0.5,
            ),
        ]
    )
    db.commit()

    out = get_cross_platform_prob(db, market=base)
    assert out is not None
    assert out["contributors"] == 1
    assert out["sources"] == ["manifold"]
    assert abs(float(out["cross_prob"]) - 0.60) < 1e-9


def test_portfolio_snapshot_contains_category_and_bucket_breakdown() -> None:
    db = _mk_db()
    now = datetime.now(UTC)
    p = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(p)
    db.flush()
    m = Market(
        platform_id=p.id,
        external_market_id="snap1",
        title="snapshot market",
        probability_yes=0.55,
        category="crypto",
        fetched_at=now,
        resolution_time=now + timedelta(days=10),
    )
    db.add(m)
    db.flush()
    portfolio = get_or_create_portfolio(db)
    db.add(
        DryrunPosition(
            portfolio_id=portfolio.id,
            market_id=m.id,
            direction="YES",
            entry_price=0.5,
            mark_price=0.5,
            notional_usd=5.0,
            shares_count=10.0,
            status="OPEN",
            open_reason="kelly=0.02,peak=0.500000",
            opened_at=now - timedelta(days=1),
        )
    )
    db.commit()

    snap = get_portfolio_snapshot(db)
    assert snap["open_positions"] == 1
    assert snap["category_breakdown"].get("crypto") == 1
    assert float(snap["bucket_breakdown_pct"]["0_14d"]) > 0.0
