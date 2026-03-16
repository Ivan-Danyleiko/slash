from __future__ import annotations

from datetime import UTC, datetime
import json
from types import SimpleNamespace
from typing import Any

import httpx
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import Market
from app.services.research.stage10_timeline_backfill import build_stage10_timeline_backfill_plan
from app.utils.http import retry_request


def _iso_utc(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).isoformat()
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (int, float)):
        # unix seconds
        try:
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC).isoformat()
        except ValueError:
            return None
    return None


def _load_market_compat(db: Session, market_id: int) -> tuple[Any, bool]:
    # returns (market_obj, is_orm)
    try:
        market = db.get(Market, int(market_id))
        if market is None:
            return None, False
        return market, True
    except OperationalError:
        inspector = sa_inspect(db.get_bind())
        cols = {str(c.get("name")) for c in inspector.get_columns("markets")}
        wanted = ["id", "external_market_id", "source_payload", "fetched_at"]
        selects: list[str] = []
        for name in wanted:
            if name in cols:
                selects.append(name)
            else:
                selects.append(f"NULL as {name}")
        stmt = text(f"SELECT {', '.join(selects)} FROM markets WHERE id = :id LIMIT 1")  # noqa: S608
        row = db.execute(stmt, {"id": int(market_id)}).mappings().first()
        if not row:
            return None, False
        payload = row.get("source_payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001
                payload = {}
        return (
            SimpleNamespace(
                id=int(row.get("id") or 0),
                external_market_id=str(row.get("external_market_id") or ""),
                source_payload=payload if isinstance(payload, dict) else {},
                fetched_at=row.get("fetched_at"),
            ),
            False,
        )


def _persist_market_payload_compat(db: Session, market_obj: Any, payload: dict[str, Any], *, is_orm: bool) -> None:
    if is_orm:
        market_obj.source_payload = payload
        market_obj.fetched_at = datetime.now(UTC)
        db.add(market_obj)
        return
    stmt = text("UPDATE markets SET source_payload = :payload, fetched_at = :fetched_at WHERE id = :id")
    db.execute(
        stmt,
        {
            "payload": json.dumps(payload, ensure_ascii=True),
            "fetched_at": datetime.now(UTC).isoformat(),
            "id": int(getattr(market_obj, "id", 0) or 0),
        },
    )


def _fetch_manifold_bets_history(settings: Settings, external_market_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    reason_codes: list[str] = []
    url = f"{settings.manifold_api_base_url}/bets"
    resp = retry_request(
        lambda: httpx.get(url, params={"contractId": external_market_id, "limit": 1000}, timeout=20.0),
        retries=3,
        backoff_seconds=1.0,
        platform="MANIFOLD",
    )
    if resp.status_code != 200:
        return [], [f"manifold_http_{resp.status_code}"]
    payload = resp.json()
    rows = payload if isinstance(payload, list) else list((payload or {}).get("bets") or [])
    points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        p = row.get("probAfter") or row.get("probBefore")
        if not isinstance(p, (int, float)):
            continue
        ts = row.get("createdTime")
        ts_iso = _iso_utc((float(ts) / 1000.0) if isinstance(ts, (int, float)) else ts)
        if not ts_iso:
            continue
        points.append({"timestamp": ts_iso, "probability": float(p)})
    if not points:
        reason_codes.append("manifold_points_empty")
    points.sort(key=lambda x: str(x.get("timestamp") or ""))
    return points, reason_codes


def _extract_meta_points(payload: Any) -> list[dict[str, Any]]:
    # Metaculus formats vary by endpoint/version. We normalize into timestamp+probability.
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = list(payload.get("results") or payload.get("history") or payload.get("predictions") or [])
    else:
        rows = []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        p = None
        if isinstance(row.get("q2"), (int, float)):
            p = float(row["q2"])
        elif isinstance((row.get("full") or {}).get("q2"), (int, float)):
            p = float((row.get("full") or {}).get("q2"))
        elif isinstance((row.get("community_prediction") or {}).get("q2"), (int, float)):
            p = float((row.get("community_prediction") or {}).get("q2"))
        ts_iso = _iso_utc(
            row.get("timestamp")
            or row.get("time")
            or row.get("t")
            or row.get("created_time")
            or row.get("publish_time")
        )
        if p is None or ts_iso is None:
            continue
        out.append({"timestamp": ts_iso, "probability": p})
    out.sort(key=lambda x: str(x.get("timestamp") or ""))
    return out


def _fetch_metaculus_prediction_history(settings: Settings, external_market_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    token = str(settings.metaculus_api_token or "").strip()
    if not token:
        return [], ["metaculus_token_missing"]

    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "User-Agent": settings.metaculus_user_agent,
    }
    endpoints = [
        f"{settings.metaculus_api_base_url}/questions/{external_market_id}/prediction-history/",
        f"{settings.metaculus_api_base_url}/questions/{external_market_id}/prediction_history/",
        f"{settings.metaculus_api_base_url}/questions/{external_market_id}/prediction-history",
    ]
    reason_codes: list[str] = []
    for url in endpoints:
        try:
            resp = retry_request(
                lambda: httpx.get(url, headers=headers, timeout=20.0),
                retries=2,
                backoff_seconds=1.0,
                platform="METACULUS",
            )
        except Exception:  # noqa: BLE001
            reason_codes.append("metaculus_history_request_failed")
            continue
        if resp.status_code == 404:
            reason_codes.append("metaculus_history_404")
            continue
        if resp.status_code in (401, 403):
            return [], [f"metaculus_auth_{resp.status_code}"]
        if resp.status_code != 200:
            reason_codes.append(f"metaculus_http_{resp.status_code}")
            continue
        points = _extract_meta_points(resp.json())
        if points:
            return points, reason_codes
        reason_codes.append("metaculus_points_empty")
    return [], reason_codes or ["metaculus_history_unavailable"]


def run_stage10_timeline_backfill(
    db: Session,
    *,
    settings: Settings,
    days: int = 730,
    limit: int = 500,
    per_platform_limit: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    plan = build_stage10_timeline_backfill_plan(db, days=days, limit=limit)
    candidates = dict(plan.get("backfill_candidates") or {})

    total_candidates = 0
    updated = 0
    updated_by_platform: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    attempted_by_platform: dict[str, int] = {}

    for platform in ("MANIFOLD", "METACULUS"):
        rows = list(candidates.get(platform) or [])[: max(1, int(per_platform_limit))]
        platform_unreachable = False
        for row in rows:
            if platform_unreachable:
                reason_counts[f"{platform.lower()}_platform_unreachable_break"] = (
                    reason_counts.get(f"{platform.lower()}_platform_unreachable_break", 0) + 1
                )
                break
            total_candidates += 1
            market_id = int(row.get("market_id") or 0)
            if market_id <= 0:
                reason_counts["candidate_missing_market_id"] = reason_counts.get("candidate_missing_market_id", 0) + 1
                continue
            market, is_orm = _load_market_compat(db, market_id)
            if market is None:
                reason_counts["market_not_found"] = reason_counts.get("market_not_found", 0) + 1
                continue
            external_id = str(market.external_market_id or "").strip()
            if not external_id:
                reason_counts["external_market_id_missing"] = reason_counts.get("external_market_id_missing", 0) + 1
                continue

            attempted_by_platform[platform] = attempted_by_platform.get(platform, 0) + 1
            points: list[dict[str, Any]] = []
            reasons: list[str] = []
            try:
                if platform == "MANIFOLD":
                    points, reasons = _fetch_manifold_bets_history(settings, external_id)
                elif platform == "METACULUS":
                    points, reasons = _fetch_metaculus_prediction_history(settings, external_id)
            except Exception:  # noqa: BLE001
                points = []
                reasons = [f"{platform.lower()}_backfill_request_failed"]
                platform_unreachable = True

            if not points:
                for code in reasons or ["backfill_points_empty"]:
                    reason_counts[code] = reason_counts.get(code, 0) + 1
                continue

            if not dry_run:
                payload = market.source_payload if isinstance(market.source_payload, dict) else {}
                if platform == "MANIFOLD":
                    payload["manifold_bets_history"] = points
                elif platform == "METACULUS":
                    payload["metaculus_prediction_history"] = points
                payload["stage10_timeline_backfill_at"] = datetime.now(UTC).isoformat()
                _persist_market_payload_compat(db, market, payload, is_orm=is_orm)
            updated += 1
            updated_by_platform[platform] = updated_by_platform.get(platform, 0) + 1

    if not dry_run:
        db.commit()

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "dry_run": bool(dry_run),
        "window_days": int(days),
        "plan_markets_scanned": int(plan.get("markets_scanned") or 0),
        "total_candidates": total_candidates,
        "attempted_by_platform": attempted_by_platform,
        "updated_rows": updated,
        "updated_by_platform": updated_by_platform,
        "reason_counts": reason_counts,
        "plan": plan,
    }
