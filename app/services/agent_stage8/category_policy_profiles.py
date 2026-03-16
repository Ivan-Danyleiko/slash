from __future__ import annotations

from copy import deepcopy
from typing import Any


CATEGORY_POLICY_BOOTSTRAP_V1: dict[str, dict[str, Any]] = {
    "crypto": {
        "min_edge_after_costs": 0.030,
        "min_liquidity_usd": 500,
        "max_spread_cents": 5,
        "max_rules_ambiguity_score": 0.30,
        "max_cross_platform_contradiction": 0.20,
        "min_ttr_hours": 2,
        "min_freshness_minutes": 30,
        "require_external_consensus": False,
    },
    "finance": {
        "min_edge_after_costs": 0.025,
        "min_liquidity_usd": 1000,
        "max_spread_cents": 6,
        "max_rules_ambiguity_score": 0.20,
        "max_cross_platform_contradiction": 0.15,
        "min_ttr_hours": 4,
        "min_freshness_minutes": 60,
        "require_external_consensus": False,
    },
    "sports": {
        "min_edge_after_costs": 0.020,
        "min_liquidity_usd": 2000,
        "max_spread_cents": 6,
        "max_rules_ambiguity_score": 0.10,
        "max_cross_platform_contradiction": 0.15,
        "min_ttr_hours": 1,
        "min_freshness_minutes": 15,
        "require_external_consensus": False,
    },
    "politics": {
        "min_edge_after_costs": 0.040,
        "min_liquidity_usd": 3000,
        "max_spread_cents": 8,
        "max_rules_ambiguity_score": 0.25,
        "max_cross_platform_contradiction": 0.25,
        "min_ttr_hours": 24,
        "min_freshness_minutes": 60,
        "require_external_consensus": False,
    },
    "other": {
        "min_edge_after_costs": 0.050,
        "min_liquidity_usd": 500,
        "max_spread_cents": 10,
        "max_rules_ambiguity_score": 0.20,
        "max_cross_platform_contradiction": 0.20,
        "min_ttr_hours": 6,
        "min_freshness_minutes": 120,
        "require_external_consensus": False,
    },
}


CATEGORY_POLICY_PRODUCTION_V1: dict[str, dict[str, Any]] = {
    "crypto": {
        "min_edge_after_costs": 0.035,
        "min_liquidity_usd": 10000,
        "max_rules_ambiguity_score": 0.25,
        "max_cross_platform_contradiction": 0.15,
        "max_spread_cents": 3,
        "min_ttr_hours": 2,
        "require_external_consensus": True,
    },
    "politics": {
        "min_edge_after_costs": 0.045,
        "min_liquidity_usd": 50000,
        "max_rules_ambiguity_score": 0.20,
        "max_cross_platform_contradiction": 0.20,
        "max_spread_cents": 5,
        "min_ttr_hours": 24,
        "require_external_consensus": True,
    },
    "sports": {
        "min_edge_after_costs": 0.025,
        "min_liquidity_usd": 5000,
        "max_rules_ambiguity_score": 0.08,
        "max_cross_platform_contradiction": 0.12,
        "max_spread_cents": 4,
        "min_ttr_hours": 2,
        "require_external_consensus": True,
    },
    "finance": {
        "min_edge_after_costs": 0.030,
        "min_liquidity_usd": 10000,
        "max_rules_ambiguity_score": 0.15,
        "max_cross_platform_contradiction": 0.12,
        "max_spread_cents": 3,
        "min_ttr_hours": 4,
        "require_external_consensus": True,
    },
    "other": {
        "min_edge_after_costs": 0.055,
        "min_liquidity_usd": 1000,
        "max_rules_ambiguity_score": 0.18,
        "max_cross_platform_contradiction": 0.18,
        "max_spread_cents": 8,
        "min_ttr_hours": 6,
        "require_external_consensus": False,
    },
}


def _merge_profiles(base: dict[str, dict[str, Any]], override: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged = deepcopy(base)
    for category, payload in override.items():
        merged.setdefault(category, {})
        merged[category].update(payload)
    return merged


def get_category_policy_profile(profile: str) -> tuple[str, dict[str, dict[str, Any]]]:
    key = (profile or "bootstrap_v1").strip().lower()
    if key == "production_v1":
        return ("production_v1", _merge_profiles(CATEGORY_POLICY_BOOTSTRAP_V1, CATEGORY_POLICY_PRODUCTION_V1))
    return ("bootstrap_v1", deepcopy(CATEGORY_POLICY_BOOTSTRAP_V1))


def get_category_policy(category: str, profile: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return profile.get(category, profile.get("other", {}))


def profile_summary(profile: dict[str, dict[str, float]]) -> dict[str, Any]:
    return {k: dict(v) for k, v in profile.items()}
