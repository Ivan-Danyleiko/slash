from __future__ import annotations

from datetime import UTC, datetime
import math
import re
from typing import Any

from app.core.config import Settings
from app.models.models import Market


_AMBIGUITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsole discretion\b", re.IGNORECASE),
    re.compile(r"\bat our discretion\b", re.IGNORECASE),
    re.compile(r"\badmin(?:istrator)? decision\b", re.IGNORECASE),
    re.compile(r"\bfinal determination\b", re.IGNORECASE),
    re.compile(r"\beditorial decision\b", re.IGNORECASE),
    re.compile(r"\bteam decision\b", re.IGNORECASE),
    re.compile(r"\bsubjective\b", re.IGNORECASE),
    re.compile(r"\bsubject to\b", re.IGNORECASE),
    re.compile(r"\bif deemed\b", re.IGNORECASE),
    re.compile(r"\bif unavailable\b", re.IGNORECASE),
    re.compile(r"\bmay be resolved by\b", re.IGNORECASE),
    re.compile(r"\bresolution source\s*:?\s*(tbd|to be determined)\b", re.IGNORECASE),
    re.compile(r"\bofficial source\s*:?\s*(tbd|to be determined)\b", re.IGNORECASE),
    re.compile(r"\bmanual review\b", re.IGNORECASE),
)

_TAIL_CATEGORIES: dict[str, dict[str, Any]] = {
    "price_target": {
        "keywords": (
            "will bitcoin reach",
            "will btc reach",
            "will ethereum reach",
            "will eth reach",
            "will solana reach",
            "will sol reach",
            "price of bitcoin",
            "price of ethereum",
            "price of solana",
            "exceed $",
            "above $",
            "hit $",
            # short-form price questions: "Bitcoin $80K", "BTC $100k", "ETH $5000"
            "bitcoin $",
            "btc $",
            "ethereum $",
            "eth $",
            "solana $",
            "sol $",
        ),
        "strategy": "bet_yes_underpriced",
    },
    "crypto_level": {
        "keywords": ("bitcoin above", "btc above", "eth above", "sol above", "reach $", "hit $", "exceed $"),
        "strategy": "bet_yes_underpriced",
    },
    "sports_match": {
        "keywords": (
            "win the championship",
            "win the world series",
            "win the super bowl",
            "win the finals",
            "game winner",
            "match winner",
            "tournament winner",
            "win the league",
            "win the cup",
            "win the title",
            "make the playoffs",
            "reach the final",
        ),
        "strategy": "bet_yes_underpriced",
    },
    "geopolitical_event": {
        "keywords": (
            "ceasefire",
            "peace deal",
            "invade",
            "invasion",
            "attack",
            "shoot down",
            "strike",
            "sanction",
            "resign",
            "impeach",
            "coup",
            "summit",
            "treaty",
            "assassinat",
            "killed in office",
            "dead or",
            "war between",
            "military operation",
            "nuclear",
            "missile",
        ),
        "strategy": "llm_evaluate",
    },
    "earnings_surprise": {
        "keywords": (
            "earnings",
            "eps",
            "revenue beat",
            "guidance raise",
            "quarterly report",
            "beat estimates",
            "miss estimates",
        ),
        "strategy": "bet_yes_underpriced",
    },
    "regulatory": {
        "keywords": (
            "fda",
            "sec ",
            "approve",
            "reject",
            "verdict",
            "ruling",
            "ban",
            "lawsuit",
            "indicted",
            "charged with",
            "guilty",
            "acquitted",
        ),
        "strategy": "llm_evaluate",
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
    vol = float(market.volume_24h or market.notional_value_dollars or market.liquidity_value or 0.0)
    if vol < float(settings.signal_tail_min_volume_usd):
        return None
    rt = market.resolution_time
    if rt is None:
        return None
    now = datetime.now(UTC)
    ref = rt if rt.tzinfo else rt.replace(tzinfo=UTC)
    days_to_res = max(0.0, (ref - now).total_seconds() / 86400.0)
    if days_to_res <= 0.0 or days_to_res > float(settings.signal_tail_max_days_to_resolution):
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

    direction = "YES" if strategy in {"bet_yes_underpriced", "llm_evaluate"} else "NO"

    return {
        "eligible": True,
        "tail_category": category,
        "tail_strategy": strategy,
        "market_prob": prob,
        "days_to_resolution": round(days_to_res, 4),
        "direction": direction,
        "reason_codes": [f"tail_category:{category}"],
    }


def tail_mispricing_ratio(*, market_prob: float, our_prob: float) -> float:
    base = max(1e-6, float(market_prob))
    return abs(float(our_prob) - float(market_prob)) / base
