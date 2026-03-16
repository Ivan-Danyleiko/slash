from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.models.models import Market


CANONICAL_CATEGORIES = ("crypto", "finance", "sports", "politics", "other")

_CATEGORY_ALIASES = {
    "crypto": {"crypto", "cryptocurrency", "bitcoin", "btc", "eth", "ethereum", "defi", "web3"},
    "finance": {"finance", "macro", "stocks", "equities", "rates", "inflation", "gdp", "treasury"},
    "sports": {"sports", "nba", "nfl", "mlb", "nhl", "soccer", "football", "tennis"},
    "politics": {"politics", "election", "president", "parliament", "congress", "government"},
    "other": {"other"},
}

_CATEGORY_KEYWORDS = {
    "crypto": [
        "bitcoin",
        "btc",
        "ethereum",
        "eth",
        "solana",
        "sol",
        "defi",
        "stablecoin",
        "crypto",
        "blockchain",
    ],
    "finance": [
        "inflation",
        "gdp",
        "interest rate",
        "fed",
        "treasury",
        "stocks",
        "s&p",
        "nasdaq",
        "dow",
        "bond",
        "yield",
    ],
    "sports": [
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "championship",
        "playoff",
        "score",
        "match",
        "tournament",
        "goal",
    ],
    "politics": [
        "election",
        "president",
        "prime minister",
        "parliament",
        "congress",
        "senate",
        "cabinet",
        "vote",
        "referendum",
    ],
}


@dataclass
class CategoryResult:
    category: str
    confidence: float
    secondary: list[str]
    reason_codes: list[str]


def _normalize_category(raw: str) -> str | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    for category, aliases in _CATEGORY_ALIASES.items():
        if text in aliases:
            return category
    for category, aliases in _CATEGORY_ALIASES.items():
        if any(alias in text for alias in aliases):
            return category
    return None


def _score_keywords(text: str, keywords: Iterable[str]) -> int:
    total = 0
    for keyword in keywords:
        if keyword in text:
            total += 1
    return total


def classify_market_category(
    market: Market,
    *,
    confidence_floor: float = 0.60,
) -> CategoryResult:
    reason_codes: list[str] = []
    raw_category = market.category
    normalized = _normalize_category(raw_category or "")
    if normalized:
        return CategoryResult(category=normalized, confidence=0.95, secondary=[], reason_codes=["category_source_market"])

    text = f"{market.title or ''} {market.description or ''}".lower()
    scores: dict[str, float] = {}
    for category, keywords in _CATEGORY_KEYWORDS.items():
        matches = _score_keywords(text, keywords)
        if matches <= 0:
            continue
        scores[category] = min(0.95, 0.55 + (0.1 * matches))

    if not scores:
        return CategoryResult(
            category="other",
            confidence=0.50,
            secondary=[],
            reason_codes=["category_missing_keywords", "category_low_confidence_fallback"],
        )

    primary = max(scores.items(), key=lambda item: item[1])[0]
    confidence = scores.get(primary, 0.0)
    secondary = [c for c, s in scores.items() if c != primary and s >= max(0.60, confidence - 0.10)]

    if confidence < confidence_floor:
        reason_codes.append("category_low_confidence_fallback")
        return CategoryResult(category="other", confidence=confidence, secondary=secondary, reason_codes=reason_codes)

    reason_codes.append("category_source_keywords")
    return CategoryResult(category=primary, confidence=confidence, secondary=secondary, reason_codes=reason_codes)
