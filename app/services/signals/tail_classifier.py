from __future__ import annotations

import math
import re
from typing import Any

from app.core.config import Settings
from app.models.models import Market


_AMBIGUITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bapproximately\b", re.IGNORECASE),
    re.compile(r"\babout\b", re.IGNORECASE),
    re.compile(r"\broughly\b", re.IGNORECASE),
    re.compile(r"\bat least\b", re.IGNORECASE),
    re.compile(r"\bup to\b", re.IGNORECASE),
    re.compile(r"\bor more\b", re.IGNORECASE),
    re.compile(r"\bor less\b", re.IGNORECASE),
    re.compile(r"\bsole discretion\b", re.IGNORECASE),
    re.compile(r"\bat our discretion\b", re.IGNORECASE),
    re.compile(r"\badmin(?:istrator)? decision\b", re.IGNORECASE),
    re.compile(r"\bfinal determination\b", re.IGNORECASE),
    re.compile(r"\beditorial decision\b", re.IGNORECASE),
    re.compile(r"\bteam decision\b", re.IGNORECASE),
    re.compile(r"\bsubjective\b", re.IGNORECASE),
    re.compile(r"\bsubject to\b", re.IGNORECASE),
    re.compile(r"\bmay be\b", re.IGNORECASE),
    re.compile(r"\bcould be\b", re.IGNORECASE),
    re.compile(r"\bmight\b", re.IGNORECASE),
    re.compile(r"\bpossibly\b", re.IGNORECASE),
    re.compile(r"\bif deemed\b", re.IGNORECASE),
    re.compile(r"\bif applicable\b", re.IGNORECASE),
    re.compile(r"\bin the event of\b", re.IGNORECASE),
    re.compile(r"\bif unavailable\b", re.IGNORECASE),
    re.compile(r"\bmay be resolved by\b", re.IGNORECASE),
    re.compile(r"\bresolution source\s*:?\s*(tbd|to be determined)\b", re.IGNORECASE),
    re.compile(r"\bofficial source\s*:?\s*(tbd|to be determined)\b", re.IGNORECASE),
    re.compile(r"\bconsensus\b", re.IGNORECASE),
    re.compile(r"\bmanual review\b", re.IGNORECASE),
)

_TAIL_CATEGORIES: dict[str, dict[str, Any]] = {
    "natural_disaster": {
        "keywords": (
            "earthquake",
            "hurricane",
            "tornado",
            "flood",
            "tsunami",
            "wildfire",
            "volcano",
            "typhoon",
            "landslide",
        ),
        "strategy": "bet_no",
    },
    "crypto_level": {
        "keywords": ("bitcoin above", "btc above", "eth above", "sol above", "reach $", "hit $", "below $"),
        "strategy": "llm_evaluate",
    },
    "sports_outcome": {
        "keywords": ("championship", "match", "game", "score", "final", "winner", "win by"),
        "strategy": "llm_evaluate",
    },
    "political_stability": {
        "keywords": ("resign", "impeach", "coup", "invasion", "war", "attack", "arrest", "assassination"),
        "strategy": "bet_no",
    },
    "regulatory": {
        "keywords": ("fda", "sec", "approve", "reject", "verdict", "ruling", "ban", "law"),
        "strategy": "llm_evaluate",
    },
    "zero_event": {
        "keywords": ("exactly 0", "zero ", "none ", "will not", "won't happen", "without any"),
        "strategy": "bet_yes",
    },
}


def resolution_ambiguity_flags(market: Market) -> list[str]:
    text = " ".join(
        [
            str(market.title or ""),
            str(market.description or ""),
            str(market.rules_text or ""),
        ]
    )
    # Guard against pathological oversized payloads in rules/description.
    text = text[:50_000]
    out: list[str] = []
    for pattern in _AMBIGUITY_PATTERNS:
        m = pattern.search(text)
        if m:
            out.append(f"ambiguity:{m.group(0).lower()}")
    return out


def classify_tail_event(market: Market, *, settings: Settings) -> dict[str, Any] | None:
    prob = float(market.probability_yes or 0.0)
    if not math.isfinite(prob) or prob < 0.0 or prob > 1.0:
        return None
    if prob < float(settings.signal_tail_min_prob) or prob > float(settings.signal_tail_max_prob):
        return None

    ambiguity = resolution_ambiguity_flags(market)
    if ambiguity:
        return {
            "eligible": False,
            "skip_reason": "tail_resolution_ambiguity",
            "reason_codes": ambiguity,
        }

    title = str(market.title or "").lower()
    category = None
    strategy = None
    for cat, cfg in _TAIL_CATEGORIES.items():
        for kw in cfg["keywords"]:
            if kw in title:
                category = cat
                strategy = str(cfg["strategy"])
                break
        if category is not None:
            break
    if category is None:
        return None

    direction = "TBD"
    if strategy == "bet_no":
        direction = "NO"
    elif strategy == "bet_yes":
        direction = "YES"

    return {
        "eligible": True,
        "tail_category": category,
        "tail_strategy": strategy,
        "market_prob": prob,
        "direction": direction,
        "reason_codes": [f"tail_category:{category}"],
    }


def tail_mispricing_ratio(*, market_prob: float, our_prob: float) -> float:
    base = max(1e-6, float(market_prob))
    return abs(float(our_prob) - float(market_prob)) / base
