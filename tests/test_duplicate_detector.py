from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.models import Market
from app.services.analyzers.duplicate import DuplicateDetector


def _detector() -> DuplicateDetector:
    settings = SimpleNamespace(
        signal_duplicate_resolution_window_days=365,
        signal_duplicate_min_overlap=2,
        signal_duplicate_min_jaccard=0.42,
        signal_duplicate_min_weighted_overlap=7.5,
        signal_duplicate_anchor_idf=4.5,
    )
    return DuplicateDetector(settings=settings)


def test_duplicate_detector_rejects_geo_mismatch_even_with_generic_overlap() -> None:
    detector = _detector()
    markets = [
        Market(
            id=1,
            platform_id=1,
            external_market_id="a",
            title="Will China directly engage in a military conflict with Taiwan before 2030?",
            resolution_time=datetime(2029, 12, 31, tzinfo=UTC),
        ),
        Market(
            id=2,
            platform_id=2,
            external_market_id="b",
            title="Will France and Russia engage in significant direct military conflict before 2030?",
            resolution_time=datetime(2029, 12, 30, tzinfo=UTC),
        ),
    ]

    pairs = detector.find_pairs(markets, threshold=70)
    assert pairs == []


def test_duplicate_detector_accepts_clear_same_event_pair() -> None:
    detector = _detector()
    markets = [
        Market(
            id=10,
            platform_id=1,
            external_market_id="x",
            title="Will Bitcoin BTC exceed 100000 USD before 2027?",
            resolution_time=datetime(2027, 12, 31, tzinfo=UTC),
        ),
        Market(
            id=11,
            platform_id=2,
            external_market_id="y",
            title="Will BTC Bitcoin price close above 100000 USD by end of 2027?",
            resolution_time=datetime(2027, 12, 20, tzinfo=UTC),
        ),
    ]

    pairs = detector.find_pairs(markets, threshold=70)
    assert len(pairs) == 1


def test_duplicate_detector_entity_aliases_help_match_us_usa() -> None:
    detector = _detector()
    markets = [
        Market(
            id=20,
            platform_id=1,
            external_market_id="u1",
            title="Will the USA enter recession in 2027?",
            resolution_time=datetime(2027, 12, 31, tzinfo=UTC),
        ),
        Market(
            id=21,
            platform_id=2,
            external_market_id="u2",
            title="Will United States be in a recession by end of 2027?",
            resolution_time=datetime(2027, 12, 15, tzinfo=UTC),
        ),
    ]

    pairs = detector.find_pairs(markets, threshold=70)
    assert len(pairs) == 1


def test_duplicate_detector_aggressive_profile_relaxes_strict_rules() -> None:
    settings = SimpleNamespace(
        signal_duplicate_resolution_window_days=365,
        signal_duplicate_min_overlap=2,
        signal_duplicate_min_jaccard=0.42,
        signal_duplicate_min_weighted_overlap=7.5,
        signal_duplicate_anchor_idf=4.5,
    )
    strict = DuplicateDetector.with_profile(settings=settings, profile="strict")
    aggressive = DuplicateDetector.with_profile(settings=settings, profile="aggressive")
    a = Market(
        id=31,
        platform_id=1,
        external_market_id="ga",
        title="Will BTC close above 100k in 2027?",
        resolution_time=datetime(2027, 12, 31, tzinfo=UTC),
    )
    b = Market(
        id=32,
        platform_id=2,
        external_market_id="gb",
        title="Bitcoin above 100000 by end of 2027?",
        resolution_time=datetime(2027, 12, 20, tzinfo=UTC),
    )

    strict_ok, _, _, _ = strict.evaluate_pair(a, b, threshold=85)
    aggressive_ok, _, _, _ = aggressive.evaluate_pair(a, b, threshold=85)
    assert int(aggressive_ok) >= int(strict_ok)


def test_duplicate_detector_strict_rejects_entity_mismatch() -> None:
    settings = SimpleNamespace(
        signal_duplicate_resolution_window_days=365,
        signal_duplicate_min_overlap=2,
        signal_duplicate_min_jaccard=0.42,
        signal_duplicate_min_weighted_overlap=7.5,
        signal_duplicate_anchor_idf=4.5,
    )
    strict = DuplicateDetector.with_profile(settings=settings, profile="strict")
    aggressive = DuplicateDetector.with_profile(settings=settings, profile="aggressive")
    a = Market(
        id=41,
        platform_id=1,
        external_market_id="pa",
        title="Will Donald Trump campaign launch in 2028?",
        resolution_time=datetime(2028, 11, 30, tzinfo=UTC),
    )
    b = Market(
        id=42,
        platform_id=2,
        external_market_id="pb",
        title="Will Joe Biden campaign launch in 2028?",
        resolution_time=datetime(2028, 11, 30, tzinfo=UTC),
    )

    strict_ok, _, _, strict_drop = strict.evaluate_pair(a, b, threshold=70)
    aggressive_ok, _, _, _ = aggressive.evaluate_pair(a, b, threshold=70)
    assert strict_ok is False
    assert strict_drop == "entity_mismatch"
    assert isinstance(aggressive_ok, bool)
