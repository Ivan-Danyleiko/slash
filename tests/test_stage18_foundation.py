from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import LiquidityAnalysis, Market, Platform, SignalHistory
from app.services.stage18.canonicalizer import backfill_canonical_keys, build_canonical_key
from app.services.stage18.structural_arb import detect_structural_arb
from app.services.stage18.topic_weights import build_topic_weight_matrix, weighted_divergence


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return session_factory()


def test_stage18_canonical_key_uses_date_hints_even_with_primary_key() -> None:
    m1 = Market(
        platform_id=1,
        external_market_id="m1",
        title="Will candidate X win election in 2026?",
        source_payload={"conditionId": "COND-123"},
    )
    m2 = Market(
        platform_id=1,
        external_market_id="m2",
        title="Will candidate X win election in 2028?",
        source_payload={"conditionId": "COND-123"},
    )
    c1 = build_canonical_key(m1)
    c2 = build_canonical_key(m2)
    assert c1.event_key_confidence == 1.0
    assert c2.event_key_confidence == 1.0
    assert c1.event_group_id != c2.event_group_id


def test_stage18_canonicalizer_backfill_processes_all_rows_without_offset_skip() -> None:
    db = _mk_db()
    platform = Platform(name="POLYMARKET", base_url="https://poly")
    db.add(platform)
    db.flush()

    for i in range(5):
        payload = {"conditionId": f"c{i}"} if i % 2 == 0 else "not_a_dict_payload"
        db.add(
            Market(
                platform_id=platform.id,
                external_market_id=f"m{i}",
                title=f"Will token {i} reach price in 2026?",
                source_payload=payload,  # includes non-dict payload case
                event_group_id=None,
                event_key_version=0,
            )
        )
    db.commit()

    report = backfill_canonical_keys(db, batch_size=2)
    assert int(report["total_processed"]) == 5
    rows = list(db.scalars(select(Market)))
    assert all(bool(m.event_group_id) for m in rows)
    assert all(int(m.event_key_version or 0) >= 1 for m in rows)


def test_stage18_topic_weights_builds_matrix_with_shrinkage() -> None:
    db = _mk_db()
    poly = Platform(name="POLYMARKET", base_url="https://poly")
    man = Platform(name="MANIFOLD", base_url="https://manifold")
    db.add_all([poly, man])
    db.flush()

    m_poly = Market(platform_id=poly.id, external_market_id="p1", title="Crypto market A", category="crypto")
    m_man = Market(platform_id=man.id, external_market_id="m1", title="Crypto market B", category="crypto")
    db.add_all([m_poly, m_man])
    db.flush()

    base_ts = datetime.now(UTC) - timedelta(days=1)
    # POLYMARKET stronger: 40/50 wins
    for i in range(50):
        ts = base_ts + timedelta(minutes=i)
        db.add(
            SignalHistory(
                signal_type=SignalType.DIVERGENCE,
                timestamp=ts,
                timestamp_bucket=ts,
                platform="POLYMARKET",
                market_id=m_poly.id,
                related_market_id=None,
                resolved_success=(i < 40),
            )
        )
    # MANIFOLD weaker: 2/10 wins
    for i in range(10):
        ts = base_ts + timedelta(hours=2, minutes=i)
        db.add(
            SignalHistory(
                signal_type=SignalType.DIVERGENCE,
                timestamp=ts,
                timestamp_bucket=ts,
                platform="MANIFOLD",
                market_id=m_man.id,
                related_market_id=None,
                resolved_success=(i < 2),
            )
        )
    db.commit()

    weights = build_topic_weight_matrix(db, min_n=100)
    w_poly = weights[("POLYMARKET", "crypto")]
    w_man = weights[("MANIFOLD", "crypto")]
    assert 0.1 <= w_poly <= 1.0
    assert 0.1 <= w_man <= 1.0
    assert w_poly > w_man

    scaled = weighted_divergence(prob_a=0.40, prob_b=0.60, weight_a=w_poly, weight_b=w_man)
    assert 0.0 < scaled < 0.20


def test_stage18_structural_arb_groups_within_platform_only() -> None:
    db = _mk_db()
    poly = Platform(name="POLYMARKET", base_url="https://poly")
    man = Platform(name="MANIFOLD", base_url="https://manifold")
    db.add_all([poly, man])
    db.flush()

    # Two legs on POLYMARKET under the same event group -> valid structural arb basket.
    poly_a = Market(
        platform_id=poly.id,
        external_market_id="poly-a",
        title="Candidate A wins",
        event_group_id="gid123",
        probability_yes=0.40,
        status=None,
    )
    poly_b = Market(
        platform_id=poly.id,
        external_market_id="poly-b",
        title="Candidate B wins",
        event_group_id="gid123",
        probability_yes=0.50,
        status=None,
    )
    # Same event_group_id on another platform must NOT be mixed into the same basket.
    man_a = Market(
        platform_id=man.id,
        external_market_id="man-a",
        title="Candidate A wins",
        event_group_id="gid123",
        probability_yes=0.30,
        status=None,
    )
    db.add_all([poly_a, poly_b, man_a])
    db.flush()

    db.add_all(
        [
            LiquidityAnalysis(market_id=poly_a.id, score=0.8, level="HIGH"),
            LiquidityAnalysis(market_id=poly_b.id, score=0.7, level="HIGH"),
            LiquidityAnalysis(market_id=man_a.id, score=0.9, level="HIGH"),
        ]
    )
    db.commit()

    groups = detect_structural_arb(
        db,
        min_underround=0.015,
        max_group_size=8,
        min_leg_liquidity=0.1,
    )
    assert len(groups) == 1
    g = groups[0]
    assert g.event_group_id == "gid123"
    assert len(g.markets) == 2
    assert abs(float(g.sum_prob) - 0.9) < 1e-9
    assert abs(float(g.underround) - 0.1) < 1e-9
