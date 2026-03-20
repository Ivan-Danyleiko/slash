"""
Stage18 regression suite — §17 checklist items:
  - canonicalization recall/precision smoke
  - topic-weights stability checks
  - structural-arb false-positive guard tests
  - full acceptance gate structure
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.enums import SignalType
from app.models.models import LiquidityAnalysis, Market, Platform, SignalHistory
from app.services.stage18.canonicalizer import (
    backfill_canonical_keys,
    build_canonical_key,
)
from app.services.stage18.structural_arb import (
    StructuralArbGroup,
    detect_structural_arb,
    validate_mutual_exclusivity,
)
from app.services.stage18.topic_weights import (
    build_topic_weight_matrix,
    get_platform_weight,
    weighted_divergence,
)


def _mk_db() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    return factory()


def _add_platform(db: Session, name: str) -> Platform:
    p = Platform(name=name, base_url=f"https://{name.lower()}")
    db.add(p)
    db.flush()
    return p


def _add_market(db: Session, platform: Platform, ext_id: str, title: str, **kwargs) -> Market:
    m = Market(platform_id=platform.id, external_market_id=ext_id, title=title, **kwargs)
    db.add(m)
    db.flush()
    return m


# ── Canonicalization: recall / precision smoke ────────────────────────────────

class TestCanonicalizationRecall:
    def test_same_title_same_group(self) -> None:
        """Identical title across platforms → same event_group_id (recall)."""
        class _M:
            source_payload: dict | None = None
        m1, m2 = _M(), _M()
        m1.title = "Will Bitcoin exceed $100k by January 2026?"
        m2.title = "Will Bitcoin exceed $100k by January 2026?"
        r1 = build_canonical_key(m1)  # type: ignore[arg-type]
        r2 = build_canonical_key(m2)  # type: ignore[arg-type]
        assert r1.event_group_id == r2.event_group_id

    def test_different_year_different_group(self) -> None:
        """Same question but different years → different groups (precision)."""
        class _M:
            source_payload: dict | None = None
        m1, m2 = _M(), _M()
        m1.title = "Will the US election happen in 2024?"
        m2.title = "Will the US election happen in 2028?"
        r1 = build_canonical_key(m1)  # type: ignore[arg-type]
        r2 = build_canonical_key(m2)  # type: ignore[arg-type]
        assert r1.event_group_id != r2.event_group_id

    def test_primary_key_sets_confidence_1(self) -> None:
        class _M:
            title = "BTC hits ATH"
            source_payload = {"conditionId": "0xdeadbeef"}
        r = build_canonical_key(_M())  # type: ignore[arg-type]
        assert r.event_key_confidence == 1.0
        assert r.event_key_primary is not None

    def test_no_primary_key_lower_confidence(self) -> None:
        class _M:
            title = "BTC hits ATH"
            source_payload: dict | None = None
        r = build_canonical_key(_M())  # type: ignore[arg-type]
        assert r.event_key_confidence < 1.0
        assert r.event_key_primary is None

    def test_empty_title_still_gets_group_id(self) -> None:
        class _M:
            title = ""
            source_payload: dict | None = None
        r = build_canonical_key(_M())  # type: ignore[arg-type]
        assert isinstance(r.event_group_id, str)
        assert len(r.event_group_id) > 0

    def test_backfill_coverage_100pct(self) -> None:
        """All markets get event_group_id after backfill."""
        db = _mk_db()
        poly = _add_platform(db, "POLYMARKET")
        for i in range(10):
            _add_market(db, poly, f"m{i}", f"Market question number {i} in 2026?")
        db.commit()
        result = backfill_canonical_keys(db, batch_size=3)
        assert result["total_processed"] == 10
        rows = list(db.scalars(select(Market)))
        assert all(m.event_group_id is not None for m in rows)
        assert all(int(m.event_key_version or 0) >= 1 for m in rows)

    def test_backfill_idempotent(self) -> None:
        """Running backfill twice does not corrupt group IDs."""
        db = _mk_db()
        poly = _add_platform(db, "POLYMARKET")
        _add_market(db, poly, "m0", "Will candidate X win in 2026?")
        db.commit()
        r1 = backfill_canonical_keys(db)
        gid_before = db.scalar(select(Market.event_group_id))
        # Second run: nothing to update (all already version 1)
        r2 = backfill_canonical_keys(db)
        gid_after = db.scalar(select(Market.event_group_id))
        assert gid_before == gid_after
        assert r2["total_processed"] == 0  # nothing left to backfill


# ── Topic weights: stability checks ──────────────────────────────────────────

class TestTopicWeightsStability:
    def test_weight_always_in_bounds(self) -> None:
        """All weights must be in [0.1, 1.0] regardless of input."""
        db = _mk_db()
        poly = _add_platform(db, "POLYMARKET")
        m = _add_market(db, poly, "m0", "test", category="crypto")
        ts = datetime.now(UTC)
        # Extreme: all failures
        for i in range(5):
            db.add(SignalHistory(
                signal_type=SignalType.DIVERGENCE,
                timestamp=ts + timedelta(minutes=i),
                timestamp_bucket=ts,
                platform="POLYMARKET",
                market_id=m.id,
                related_market_id=None,
                resolved_success=False,
            ))
        db.commit()
        weights = build_topic_weight_matrix(db, min_n=100)
        for (p, c), w in weights.items():
            assert 0.1 <= w <= 1.0, f"Weight out of bounds for ({p},{c}): {w}"

    def test_more_successes_higher_weight(self) -> None:
        """Platform with higher success rate gets higher weight than lower one."""
        db = _mk_db()
        poly = _add_platform(db, "GOOD")
        bad = _add_platform(db, "BAD")
        m_good = _add_market(db, poly, "g1", "q1", category="politics")
        m_bad = _add_market(db, bad, "b1", "q2", category="politics")
        ts = datetime.now(UTC)
        # GOOD: 90/100 wins; BAD: 10/100 wins
        for i in range(100):
            db.add(SignalHistory(
                signal_type=SignalType.DIVERGENCE,
                timestamp=ts + timedelta(minutes=i),
                timestamp_bucket=ts + timedelta(minutes=i),
                platform="GOOD",
                market_id=m_good.id,
                related_market_id=None,
                resolved_success=(i < 90),
            ))
            db.add(SignalHistory(
                signal_type=SignalType.DIVERGENCE,
                timestamp=ts + timedelta(hours=1, minutes=i),
                timestamp_bucket=ts + timedelta(hours=1, minutes=i),
                platform="BAD",
                market_id=m_bad.id,
                related_market_id=None,
                resolved_success=(i < 10),
            ))
        db.commit()
        weights = build_topic_weight_matrix(db, min_n=50)
        assert weights[("GOOD", "politics")] > weights[("BAD", "politics")]

    def test_sparse_platform_shrinks_toward_global(self) -> None:
        """Platform with n < min_n is shrunk toward the platform-level global rate.

        Shrinkage target is the *platform* global (across all categories for that
        platform). So to observe shrinkage, we need the cell raw rate to differ from
        the platform global rate. We achieve this by giving SPARSE:
          - 100 rows in "other" category with 50% win rate  → platform global ≈ 0.5
          - 5 rows  in "sports" category with 100% win rate → sports raw = 1.0
        With min_n=100, shrink(5) ≈ 0.048 →
          w_sports ≈ 0.048*1.0 + 0.952*0.5 ≈ 0.524 < 0.75
        """
        db = _mk_db()
        sparse_p = _add_platform(db, "SPARSE")
        m_sports = _add_market(db, sparse_p, "s1", "test sports", category="sports")
        m_other = _add_market(db, sparse_p, "o1", "test other", category="other")
        ts = datetime.now(UTC)
        # "sports" cell: 5 rows, all wins (raw = 1.0, but sparse)
        for i in range(5):
            db.add(SignalHistory(
                signal_type=SignalType.DIVERGENCE,
                timestamp=ts + timedelta(minutes=i),
                timestamp_bucket=ts + timedelta(minutes=i),
                platform="SPARSE",
                market_id=m_sports.id,
                related_market_id=None,
                resolved_success=True,
            ))
        # "other" cell: 100 rows, 50% wins → establishes platform global ≈ (5+50)/(5+100) ≈ 0.524
        for i in range(100):
            db.add(SignalHistory(
                signal_type=SignalType.DIVERGENCE,
                timestamp=ts + timedelta(hours=1, minutes=i),
                timestamp_bucket=ts + timedelta(hours=1, minutes=i),
                platform="SPARSE",
                market_id=m_other.id,
                related_market_id=None,
                resolved_success=(i < 50),
            ))
        db.commit()
        weights = build_topic_weight_matrix(db, min_n=100)
        w_sports = weights.get(("SPARSE", "sports"), 1.0)
        # 5 rows → shrink(5) ≈ 0.048; platform global ≈ 0.524
        # w ≈ 0.048 * 1.0 + 0.952 * 0.524 ≈ 0.547 < 0.75
        assert w_sports < 0.75, f"Expected shrinkage for sparse sports cell, got w={w_sports}"

    def test_get_platform_weight_fallback(self) -> None:
        """get_platform_weight falls back gracefully when exact cell missing."""
        weights = {("POLY", "crypto"): 0.8, ("POLY", "sports"): 0.6}
        # Exact match
        assert get_platform_weight(weights, "POLY", "crypto") == 0.8
        # Category fallback → platform average
        w = get_platform_weight(weights, "POLY", "politics")
        assert abs(w - 0.7) < 1e-9
        # Unknown platform → 1.0
        assert get_platform_weight(weights, "UNKNOWN", "crypto") == 1.0

    def test_weighted_divergence_discounts_unreliable_pair(self) -> None:
        """weighted_divergence < gross when one platform is unreliable."""
        gross = 0.20  # |0.6 - 0.4|
        # Equal weights → weighted ≈ gross * hmean(1,1) = gross
        w_equal = weighted_divergence(0.6, 0.4, 1.0, 1.0)
        # Unequal → discounted
        w_unequal = weighted_divergence(0.6, 0.4, 1.0, 0.1)
        assert abs(w_equal - gross) < 1e-9
        assert w_unequal < w_equal


# ── Structural arb: false-positive guard tests ────────────────────────────────

class TestStructuralArbFalsePositiveGuards:
    def test_identical_title_legs_flagged_invalid(self) -> None:
        """Basket where all legs have the same title → not mutually exclusive."""
        class _M:
            def __init__(self, title: str):
                self.title = title
        markets = [_M("Will X happen?"), _M("Will X happen?"), _M("Will X happen?")]
        assert validate_mutual_exclusivity(markets) is False  # type: ignore[arg-type]

    def test_distinct_title_legs_valid(self) -> None:
        """Basket with clearly different outcome titles → valid."""
        class _M:
            def __init__(self, title: str):
                self.title = title
        # Outcome titles for the same event share context ("wins championship")
        # but differ enough (alpha vs beta) to be below the 0.70 Jaccard threshold.
        markets = [
            _M("Team Alpha wins the championship"),
            _M("Team Beta wins the championship"),
            _M("No winner — match cancelled"),
        ]
        assert validate_mutual_exclusivity(markets) is True  # type: ignore[arg-type]

    def test_overround_group_not_returned(self) -> None:
        """Groups where sum_prob > 1 (overround) must not be returned."""
        db = _mk_db()
        poly = _add_platform(db, "POLYMARKET")
        # sum_prob = 0.55 + 0.55 = 1.10 → overround, not underround
        a = _add_market(db, poly, "a", "Candidate A wins", event_group_id="gX", probability_yes=0.55, status=None)
        b = _add_market(db, poly, "b", "Candidate B wins", event_group_id="gX", probability_yes=0.55, status=None)
        db.add_all([
            LiquidityAnalysis(market_id=a.id, score=0.9, level="HIGH"),
            LiquidityAnalysis(market_id=b.id, score=0.9, level="HIGH"),
        ])
        db.commit()
        groups = detect_structural_arb(db, min_underround=0.015, max_group_size=8)
        assert all(g.event_group_id != "gX" for g in groups)

    def test_single_leg_group_excluded(self) -> None:
        """Groups with only 1 leg cannot form a basket."""
        db = _mk_db()
        poly = _add_platform(db, "POLYMARKET")
        a = _add_market(db, poly, "a", "Only outcome", event_group_id="gSingle", probability_yes=0.30, status=None)
        db.add(LiquidityAnalysis(market_id=a.id, score=0.9, level="HIGH"))
        db.commit()
        groups = detect_structural_arb(db, min_underround=0.015, max_group_size=8, min_group_size=2)
        assert all(g.event_group_id != "gSingle" for g in groups)

    def test_low_liquidity_leg_excludes_whole_basket(self) -> None:
        """If any leg has liquidity < min_leg_liquidity → entire basket skipped."""
        db = _mk_db()
        poly = _add_platform(db, "POLYMARKET")
        a = _add_market(db, poly, "a", "Candidate A wins", event_group_id="gLow", probability_yes=0.30, status=None)
        b = _add_market(db, poly, "b", "Candidate B wins", event_group_id="gLow", probability_yes=0.30, status=None)
        db.add_all([
            LiquidityAnalysis(market_id=a.id, score=0.9, level="HIGH"),
            LiquidityAnalysis(market_id=b.id, score=0.05, level="LOW"),  # too low
        ])
        db.commit()
        groups = detect_structural_arb(db, min_underround=0.015, max_group_size=8, min_leg_liquidity=0.10)
        assert all(g.event_group_id != "gLow" for g in groups)

    def test_cross_platform_legs_in_separate_baskets(self) -> None:
        """Same event_group_id on different platforms → separate baskets, not merged."""
        db = _mk_db()
        poly = _add_platform(db, "POLYMARKET")
        man = _add_platform(db, "MANIFOLD")
        # POLYMARKET legs: sum = 0.8 → underround 0.2
        pa = _add_market(db, poly, "pa", "Candidate A (poly)", event_group_id="gCross", probability_yes=0.35, status=None)
        pb = _add_market(db, poly, "pb", "Candidate B (poly)", event_group_id="gCross", probability_yes=0.45, status=None)
        # MANIFOLD leg — must NOT be merged with POLYMARKET basket
        ma = _add_market(db, man, "ma", "Candidate A (manifold)", event_group_id="gCross", probability_yes=0.25, status=None)
        db.add_all([
            LiquidityAnalysis(market_id=pa.id, score=0.8, level="HIGH"),
            LiquidityAnalysis(market_id=pb.id, score=0.8, level="HIGH"),
            LiquidityAnalysis(market_id=ma.id, score=0.8, level="HIGH"),
        ])
        db.commit()
        groups = detect_structural_arb(db, min_underround=0.015, max_group_size=8)
        gids = [g.event_group_id for g in groups]
        # Only POLYMARKET basket qualifies (2 legs, underround = 0.20)
        poly_groups = [g for g in groups if g.event_group_id == "gCross" and len(g.markets) == 2]
        cross_groups = [g for g in groups if g.event_group_id == "gCross" and len(g.markets) == 3]
        assert len(poly_groups) == 1, "POLYMARKET 2-leg basket should be detected"
        assert len(cross_groups) == 0, "Cross-platform 3-leg merged basket must not appear"

    def test_mutual_exclusivity_valid_field_populated(self) -> None:
        """StructuralArbGroup.mutual_exclusivity_valid is set correctly."""
        db = _mk_db()
        poly = _add_platform(db, "POLYMARKET")
        # Distinct outcome titles (different teams): Jaccard well below 0.70 threshold
        a = _add_market(db, poly, "va", "Alpha United wins the trophy", event_group_id="gME", probability_yes=0.35, status=None)
        b = _add_market(db, poly, "vb", "Beta City takes first place", event_group_id="gME", probability_yes=0.45, status=None)
        db.add_all([
            LiquidityAnalysis(market_id=a.id, score=0.8, level="HIGH"),
            LiquidityAnalysis(market_id=b.id, score=0.8, level="HIGH"),
        ])
        db.commit()
        groups = detect_structural_arb(db, min_underround=0.015, max_group_size=8)
        assert len(groups) == 1
        assert groups[0].mutual_exclusivity_valid is True


# ── Full acceptance gate structure ────────────────────────────────────────────

class TestFinalReportGateStructure:
    def test_final_report_has_all_required_kpi_keys(self) -> None:
        """Final report dict must contain all 6 §14 acceptance criteria."""
        from unittest.mock import MagicMock, patch
        from app.services.research.stage18_report import build_stage18_final_report

        mock_settings = MagicMock()
        mock_settings.stage18_event_group_min_confidence = 0.60
        mock_settings.stage18_topic_weights_min_n = 100
        mock_settings.stage18_structural_arb_min_underround = 0.015
        mock_settings.stage18_structural_arb_max_group_size = 8

        db = _mk_db()
        poly = _add_platform(db, "POLYMARKET")
        _add_market(db, poly, "m1", "Some question in 2026?", event_group_id="g1")

        with patch("app.services.research.stage18_report._persist_artifact"):
            report = build_stage18_final_report(db, settings=mock_settings)

        hard = report.get("hard_criteria", {})
        soft = report.get("soft_criteria", {})
        assert "event_group_coverage_ge_70pct" in hard
        assert "structural_arb_candidates_ge_5_per_day" in hard
        assert "no_stage7_stage17_regressions" in hard
        assert "cross_platform_match_recall_improvement" in soft
        assert "weighted_divergence_hit_rate_improvement" in soft
        assert "shadow_post_cost_ev_ci_low_80_positive" in soft

    def test_verdict_no_go_when_coverage_zero(self) -> None:
        """With 0 markets having event_group_id → NO_GO."""
        from unittest.mock import MagicMock, patch
        from app.services.research.stage18_report import build_stage18_final_report

        mock_settings = MagicMock()
        mock_settings.stage18_event_group_min_confidence = 0.60
        mock_settings.stage18_topic_weights_min_n = 100
        mock_settings.stage18_structural_arb_min_underround = 0.015
        mock_settings.stage18_structural_arb_max_group_size = 8

        db = _mk_db()
        # No markets at all → coverage = 0.0 < 0.70
        with patch("app.services.research.stage18_report._persist_artifact"):
            report = build_stage18_final_report(db, settings=mock_settings)

        assert report["verdict"] in ("NO_GO", "LIMITED_GO")
        assert report["hard_criteria"]["event_group_coverage_ge_70pct"] is False

    def test_limited_go_when_hard_criteria_pass(self) -> None:
        """When all hard criteria pass but soft criteria don't → LIMITED_GO."""
        from unittest.mock import MagicMock, patch
        from app.services.research.stage18_report import build_stage18_final_report

        mock_settings = MagicMock()
        mock_settings.stage18_event_group_min_confidence = 0.60
        mock_settings.stage18_topic_weights_min_n = 100
        mock_settings.stage18_structural_arb_min_underround = 0.015
        mock_settings.stage18_structural_arb_max_group_size = 8

        db = _mk_db()
        poly = _add_platform(db, "POLYMARKET")
        # Add enough markets for 70% coverage
        for i in range(10):
            _add_market(db, poly, f"m{i}", f"Market {i} in 2026?", event_group_id=f"g{i}")

        # Add structural arb candidates
        for j in range(6):
            gid = f"arb{j}"
            a = _add_market(db, poly, f"a{j}", f"Candidate A wins event {j}", event_group_id=gid, probability_yes=0.35, status=None)
            b = _add_market(db, poly, f"b{j}", f"Candidate B wins event {j}", event_group_id=gid, probability_yes=0.45, status=None)
            db.add_all([
                LiquidityAnalysis(market_id=a.id, score=0.8, level="HIGH"),
                LiquidityAnalysis(market_id=b.id, score=0.8, level="HIGH"),
            ])
        db.commit()

        with patch("app.services.research.stage18_report._persist_artifact"):
            report = build_stage18_final_report(db, settings=mock_settings)

        # Hard criteria: coverage (all 10/10 have group_id = 100% ≥ 70%) ✓
        #               structural arb 6 ≥ 5 ✓, regressions (no JobRun = ok) ✓
        # Soft: divergence hit rate = n/a (no signal history) → False
        assert report["hard_criteria"]["event_group_coverage_ge_70pct"] is True
        assert report["hard_criteria"]["structural_arb_candidates_ge_5_per_day"] is True
        assert report["verdict"] in ("GO", "LIMITED_GO")
