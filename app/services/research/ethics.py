from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.models import Signal
from app.services.signals.ranking import select_top_signals, signal_score_total

_BANNED_PHRASES = (
    "guaranteed profit",
    "risk-free profit",
    "no risk",
)


def build_ethics_report(
    db: Session,
    *,
    top_window: int = 50,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    top_window = max(1, min(int(top_window), 500))
    disclaimer = (settings.research_ethics_disclaimer_text or "").strip()
    rows = list(db.scalars(select(Signal).order_by(Signal.created_at.desc()).limit(max(200, top_window * 4))))
    top = select_top_signals(rows, limit=top_window, settings=settings)

    negative_score_top = [s for s in top if signal_score_total(s) < 0]
    weak_confidence_top = [s for s in top if (s.confidence_score or 0.0) < 0.2]
    banned_phrases_in_disclaimer = [p for p in _BANNED_PHRASES if p in disclaimer.lower()]

    return {
        "top_window": top_window,
        "disclaimer_present": bool(disclaimer),
        "disclaimer_text": disclaimer,
        "banned_phrase_hits_in_disclaimer": banned_phrases_in_disclaimer,
        "negative_score_top_count": len(negative_score_top),
        "weak_confidence_top_count": len(weak_confidence_top),
        "checks": {
            "has_disclaimer": bool(disclaimer),
            "no_banned_phrases": len(banned_phrases_in_disclaimer) == 0,
            "no_negative_score_top": len(negative_score_top) == 0,
        },
        "passed": bool(disclaimer)
        and len(banned_phrases_in_disclaimer) == 0
        and len(negative_score_top) == 0,
    }


def extract_ethics_metrics(report: dict[str, Any]) -> dict[str, float]:
    checks = report.get("checks") or {}
    return {
        "ethics_passed": 1.0 if bool(report.get("passed")) else 0.0,
        "ethics_disclaimer_present": 1.0 if bool(report.get("disclaimer_present")) else 0.0,
        "ethics_negative_score_top_count": float(report.get("negative_score_top_count") or 0.0),
        "ethics_weak_confidence_top_count": float(report.get("weak_confidence_top_count") or 0.0),
        "ethics_check_no_banned_phrases": 1.0 if bool(checks.get("no_banned_phrases")) else 0.0,
    }
