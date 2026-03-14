from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.models import UserEvent


def assign_ab_variant(*, user_id: int, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    control_share = min(0.95, max(0.05, float(settings.research_ab_control_share)))
    digest = hashlib.sha256(f"{settings.research_ab_salt}:{int(user_id)}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    if bucket < control_share:
        return settings.research_ab_control_label
    return settings.research_ab_treatment_label


def get_ab_variant_for_user(*, user_id: int, settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    if not settings.research_ab_enabled:
        return None
    return assign_ab_variant(user_id=user_id, settings=settings)


def _payload_variant(payload_json: dict[str, Any] | None) -> str | None:
    if not isinstance(payload_json, dict):
        return None
    value = payload_json.get("variant")
    return str(value) if value else None


def build_ab_testing_report(
    db: Session,
    *,
    days: int = 30,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    days = max(1, min(int(days), 365))
    cutoff = datetime.now(UTC) - timedelta(days=days)
    events = list(
        db.scalars(
            select(UserEvent)
            .where(UserEvent.created_at >= cutoff)
            .order_by(UserEvent.created_at.desc())
        )
    )
    variants = (settings.research_ab_control_label, settings.research_ab_treatment_label)
    by_variant: dict[str, dict[str, Any]] = {
        v: {
            "variant": v,
            "exposures": 0,
            "unique_users_exposed": set(),
            "signal_sent": 0,
            "market_opened": 0,
            "watchlist_added": 0,
            "digest_sent": 0,
        }
        for v in variants
    }
    untagged_events = 0
    for event in events:
        variant = _payload_variant(event.payload_json)
        if not variant or variant not in by_variant:
            untagged_events += 1
            continue
        bucket = by_variant[variant]
        if event.event_type == "ab_variant_exposure":
            bucket["exposures"] += 1
            bucket["unique_users_exposed"].add(event.user_id)
        elif event.event_type == "signal_sent":
            bucket["signal_sent"] += 1
        elif event.event_type == "market_opened":
            bucket["market_opened"] += 1
        elif event.event_type == "watchlist_added":
            bucket["watchlist_added"] += 1
        elif event.event_type == "digest_sent":
            bucket["digest_sent"] += 1

    rows: list[dict[str, Any]] = []
    for variant in variants:
        b = by_variant[variant]
        sent = int(b["signal_sent"])
        opened = int(b["market_opened"])
        exposures = int(b["exposures"])
        row = {
            "variant": variant,
            "exposures": exposures,
            "unique_users_exposed": len(b["unique_users_exposed"]),
            "signal_sent": sent,
            "market_opened": opened,
            "watchlist_added": int(b["watchlist_added"]),
            "digest_sent": int(b["digest_sent"]),
            "ctr_proxy": round(opened / sent, 4) if sent > 0 else 0.0,
            "engagement_per_exposure": round((opened + int(b["watchlist_added"])) / exposures, 4)
            if exposures > 0
            else 0.0,
        }
        rows.append(row)

    control = rows[0]
    treatment = rows[1]
    ctr_lift = 0.0
    if float(control["ctr_proxy"]) > 0:
        ctr_lift = (float(treatment["ctr_proxy"]) - float(control["ctr_proxy"])) / float(control["ctr_proxy"])
    return {
        "experiment_name": settings.research_ab_experiment_name,
        "period_days": days,
        "ab_enabled": bool(settings.research_ab_enabled),
        "control_share_target": float(settings.research_ab_control_share),
        "events_scanned": len(events),
        "untagged_events": untagged_events,
        "variants": rows,
        "comparative": {
            "control_variant": settings.research_ab_control_label,
            "treatment_variant": settings.research_ab_treatment_label,
            "ctr_lift_treatment_vs_control": round(ctr_lift, 6),
            "meets_ctr_goal_20pct": ctr_lift >= 0.20,
        },
    }


def extract_ab_testing_metrics(report: dict[str, Any]) -> dict[str, float]:
    rows = list(report.get("variants") or [])
    ctr_map = {str(r.get("variant")): float(r.get("ctr_proxy") or 0.0) for r in rows}
    cmp = report.get("comparative") or {}
    return {
        "ab_events_scanned": float(report.get("events_scanned") or 0.0),
        "ab_untagged_events": float(report.get("untagged_events") or 0.0),
        "ab_ctr_control": float(ctr_map.get(str(cmp.get("control_variant")), 0.0)),
        "ab_ctr_treatment": float(ctr_map.get(str(cmp.get("treatment_variant")), 0.0)),
        "ab_ctr_lift_treatment_vs_control": float(cmp.get("ctr_lift_treatment_vs_control") or 0.0),
    }
