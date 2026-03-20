"""
Stage18 Workstream A: Event Canonicalization

Deterministic (no LLM) canonical key builder that:
1. Extracts primary keys from source_payload (conditionId, event_ticker, slug, etc.)
2. Normalizes title + extracts date hints for secondary key
3. Assigns event_group_id as a short hash of the normalized secondary key
4. Confidence: 1.0 if primary key found, scaled by title similarity otherwise
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from app.models.models import Market

# ── normalization helpers ────────────────────────────────────────────────────

_STOPWORDS = frozenset(
    "a an the is are was were will be has have had do does did "
    "in on at to of for by with from or and but not".split()
)
_YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")
_MONTH_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)
_PUNCTS = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    """Lowercase, remove accents, strip punctuation, remove stopwords."""
    s = unicodedata.normalize("NFD", title)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = _PUNCTS.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    tokens = [t for t in s.split() if t not in _STOPWORDS and len(t) > 1]
    return " ".join(tokens)


def _extract_date_hints(title: str) -> str:
    """Extract year and month hints from title for grouping."""
    years = _YEAR_RE.findall(title)
    months = [m.lower()[:3] for m in _MONTH_RE.findall(title)]
    parts = sorted(set(years)) + sorted(set(months))
    return ":".join(parts)


def _short_hash(s: str, length: int = 12) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:length]


# ── payload key extraction ───────────────────────────────────────────────────

def _extract_primary_key(market: Market) -> str | None:
    """Extract stable external ID from source_payload."""
    payload = market.source_payload if isinstance(market.source_payload, dict) else {}
    # Polymarket
    cond_id = payload.get("conditionId") or payload.get("condition_id")
    if cond_id:
        return f"poly:{str(cond_id)[:40]}"
    # Kalshi
    ticker = payload.get("event_ticker") or payload.get("ticker")
    if ticker:
        return f"kalshi:{str(ticker)[:40]}"
    # Manifold
    slug = payload.get("slug")
    if slug:
        return f"manifold:{str(slug)[:40]}"
    # Metaculus
    q_id = payload.get("id") or payload.get("question_id")
    platform_hint = str(payload.get("platform") or "").lower()
    if q_id and "metaculus" in platform_hint:
        return f"metaculus:{str(q_id)}"
    return None


# ── public API ───────────────────────────────────────────────────────────────

@dataclass(slots=True)
class CanonicalKeyResult:
    event_key_primary: str | None
    event_key_secondary: str
    event_group_id: str
    event_key_confidence: float
    event_key_version: int = 1


_KEY_VERSION = 1


def build_canonical_key(market: Market) -> CanonicalKeyResult:
    """
    Build canonical keys for a single market (deterministic, no DB needed).

    Returns a CanonicalKeyResult with:
    - event_key_primary: stable external ID if found
    - event_key_secondary: normalized title + date hints
    - event_group_id: short hash used for cross-platform grouping
    - event_key_confidence: 1.0 if primary found, 0.8 if title-only
    """
    primary = _extract_primary_key(market)

    norm_title = _normalize_title(market.title or "")
    date_hints = _extract_date_hints(market.title or "")

    # Secondary key: normalized title + date hints
    secondary_parts = [norm_title]
    if date_hints:
        secondary_parts.append(date_hints)
    secondary = "|".join(secondary_parts)

    # Always include date hints in grouping seed to avoid collapsing
    # yearly-recurring markets with similar titles.
    group_seed = secondary or primary or ""
    confidence = 1.0 if primary else (0.8 if norm_title else 0.3)

    event_group_id = _short_hash(group_seed)

    return CanonicalKeyResult(
        event_key_primary=primary,
        event_key_secondary=secondary,
        event_group_id=event_group_id,
        event_key_confidence=confidence,
        event_key_version=_KEY_VERSION,
    )


def apply_canonical_key(market: Market) -> bool:
    """
    Compute and write canonical key fields onto market in-place.

    Returns True if event_group_id changed (useful for tracking updates).
    """
    result = build_canonical_key(market)
    old_gid = market.event_group_id
    market.event_group_id = result.event_group_id
    market.event_key_version = result.event_key_version
    market.event_key_confidence = result.event_key_confidence
    return old_gid != result.event_group_id


def backfill_canonical_keys(db, *, batch_size: int = 500) -> dict[str, int]:
    """
    Backfill event_group_id for all markets that have none or version < KEY_VERSION.
    Uses batch updates to avoid N+1.
    """
    from sqlalchemy import select
    from app.models.models import Market as _Market

    total = 0
    updated = 0
    while True:
        rows = list(
            db.scalars(
                select(_Market)
                .where(
                    (_Market.event_group_id.is_(None))
                    | (_Market.event_key_version < _KEY_VERSION)
                )
                .order_by(_Market.id)
                .limit(batch_size)
            )
        )
        if not rows:
            break
        for m in rows:
            changed = apply_canonical_key(m)
            if changed:
                updated += 1
        total += len(rows)
        db.flush()
        if len(rows) < batch_size:
            break

    db.commit()
    return {"total_processed": total, "updated": updated, "key_version": _KEY_VERSION}
