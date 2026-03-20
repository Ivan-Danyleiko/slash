#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.models import JobRun, Market, Platform
from app.services.signals.tail_classifier import classify_tail_event


def _redact_db_url(url: str) -> str:
    text = str(url or "")
    if "@" not in text or "://" not in text:
        return text
    try:
        scheme, rest = text.split("://", 1)
        creds, host_part = rest.split("@", 1)
        if ":" in creds:
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host_part}"
        return f"{scheme}://***@{host_part}"
    except Exception:  # noqa: BLE001
        return "***"


def _latest_generate_signals_stats(db) -> dict:
    job = db.scalar(
        select(JobRun)
        .where(JobRun.job_name == "generate_signals")
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )
    if job is None:
        return {"found": False}
    details = job.details if isinstance(job.details, dict) else {}
    return {
        "found": True,
        "job_id": int(job.id),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "status": str(job.status),
        "tail_attempted": int(details.get("tail_attempted") or 0),
        "tail_created": int(details.get("tail_created") or 0),
        "tail_updated": int(details.get("tail_updated") or 0),
        "tail_ambiguous_skipped": int(details.get("tail_ambiguous_skipped") or 0),
        "tail_out_of_prob_range": int(details.get("tail_out_of_prob_range") or 0),
        "tail_below_mispricing": int(details.get("tail_below_mispricing") or 0),
        "tail_unknown_category_skipped": int(details.get("tail_unknown_category_skipped") or 0),
    }


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _safe_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return None
        # SQLite often stores timezone-aware values as ISO string.
        normalized = text_value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except Exception:  # noqa: BLE001
            return None
    return None


def _tail_signal_counts(db, *, hours: int) -> dict:
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, int(hours)))
    rows = list(
        db.execute(
            text(
                """
                SELECT metadata_json, signal_mode
                FROM signals
                WHERE signal_type = :signal_type
                  AND created_at >= :cutoff
                ORDER BY created_at DESC
                """
            ),
            {"signal_type": "TAIL_EVENT_CANDIDATE", "cutoff": cutoff},
        )
    )
    by_cat: Counter[str] = Counter()
    by_mode: Counter[str] = Counter()
    for metadata_json, signal_mode in rows:
        meta = _safe_json(metadata_json)
        by_cat[str(meta.get("tail_category") or "unknown")] += 1
        by_mode[str(signal_mode or "unknown")] += 1
    return {
        "hours": int(hours),
        "tail_signals_total": len(rows),
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_mode": dict(sorted(by_mode.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def _candidate_scan_audit(db, *, max_markets: int, markets_columns: set[str] | None = None) -> dict:
    settings = get_settings()
    max_days = max(1, int(settings.signal_tail_max_days_to_resolution))
    min_volume = max(0.0, float(settings.signal_tail_min_volume_usd))
    now = datetime.now(UTC)
    cols = {str(c) for c in (markets_columns or set())}
    volume_terms: list[str] = []
    if "volume_24h" in cols:
        volume_terms.append("NULLIF(volume_24h, 0)")
    if "notional_value_dollars" in cols:
        volume_terms.append("NULLIF(notional_value_dollars, 0)")
    if "liquidity_value" in cols:
        volume_terms.append("NULLIF(liquidity_value, 0)")
    volume_expr = f"COALESCE({', '.join(volume_terms)}, 0)" if volume_terms else "0"
    rows = list(
        db.execute(
            text(
                f"""
                SELECT
                    id,
                    platform_id,
                    external_market_id,
                    title,
                    description,
                    rules_text,
                    status,
                    probability_yes,
                    volume_24h,
                    liquidity_value,
                    resolution_time,
                    source_payload,
                    fetched_at
                FROM markets
                WHERE probability_yes IS NOT NULL
                  AND probability_yes >= :min_prob
                  AND probability_yes <= :max_prob
                  AND {volume_expr} >= :min_volume
                  AND resolution_time IS NOT NULL
                  AND resolution_time <= :max_resolution
                  AND (status IS NULL OR status NOT IN ('resolved', 'closed', 'settled', 'final', 'ended'))
                ORDER BY fetched_at DESC
                LIMIT :limit_n
                """
            ),
            {
                "min_prob": float(settings.signal_tail_min_prob),
                "max_prob": float(settings.signal_tail_max_prob),
                "min_volume": min_volume,
                "max_resolution": now + timedelta(days=max_days),
                "limit_n": max(100, int(max_markets)),
            },
        )
    )

    eligible = 0
    none_filtered = 0
    by_category: Counter[str] = Counter()
    skip_reasons: Counter[str] = Counter()
    ambiguity_codes: Counter[str] = Counter()
    for r in rows:
        m = Market(
            id=int(r.id),
            platform_id=int(r.platform_id),
            external_market_id=str(r.external_market_id or ""),
            title=str(r.title or ""),
            description=str(r.description or ""),
            rules_text=str(r.rules_text or ""),
            status=str(r.status or "") if r.status is not None else None,
            probability_yes=float(r.probability_yes or 0.0),
            volume_24h=float(r.volume_24h or 0.0),
            liquidity_value=float(r.liquidity_value or 0.0),
            resolution_time=_safe_dt(r.resolution_time),
            source_payload=_safe_json(r.source_payload),
            fetched_at=_safe_dt(r.fetched_at),
        )
        out = classify_tail_event(m, settings=settings)
        if out is None:
            none_filtered += 1
            continue
        if not bool(out.get("eligible")):
            skip_reasons[str(out.get("skip_reason") or "unknown")] += 1
            for code in list(out.get("reason_codes") or []):
                ambiguity_codes[str(code)] += 1
            continue
        eligible += 1
        by_category[str(out.get("tail_category") or "unknown")] += 1

    return {
        "markets_scanned": len(rows),
        "eligible": int(eligible),
        "none_filtered": int(none_filtered),
        "eligible_by_category": dict(sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))),
        "skip_reasons": dict(sorted(skip_reasons.items(), key=lambda kv: (-kv[1], kv[0]))),
        "ambiguity_reason_codes": dict(sorted(ambiguity_codes.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def _platform_coverage(db) -> dict:
    total = int(db.scalar(select(func.count()).select_from(Market)) or 0)
    platforms = {int(p.id): str(p.name) for p in db.scalars(select(Platform))}
    by_platform: Counter[str] = Counter()
    unknown = 0
    rows = list(db.execute(select(Market.platform_id, Market.source_payload)))
    for pid, payload in rows:
        src = payload if isinstance(payload, dict) else {}
        p = str(src.get("platform") or platforms.get(int(pid or 0), "") or "").upper()
        if not p:
            unknown += 1
            continue
        by_platform[p] += 1
    return {
        "markets_total": total,
        "platform_unknown": int(unknown),
        "platform_unknown_share": round((unknown / total), 6) if total else 0.0,
        "by_platform": dict(sorted(by_platform.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def _classify_db_error(exc: OperationalError) -> tuple[str, str]:
    detail = str(exc).splitlines()[0]
    msg = str(exc).lower()
    if "no such table" in msg or "undefined table" in msg:
        return "schema_missing_table", detail
    if "no such column" in msg or "undefined column" in msg:
        return "schema_missing_column", detail
    if "failed to resolve host" in msg or "could not translate host name" in msg:
        return "database_connection_failed", detail
    if "connection refused" in msg:
        return "database_connection_failed", detail
    return "database_operational_error", detail


def _schema_snapshot(engine) -> dict[str, Any]:
    insp = inspect(engine)
    out: dict[str, Any] = {}
    for table in ("job_runs", "signals", "markets", "platforms"):
        if not insp.has_table(table):
            out[table] = {"exists": False, "columns": []}
            continue
        cols = [str(c.get("name")) for c in insp.get_columns(table)]
        out[table] = {"exists": True, "columns": cols}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage17 TZ audit report")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--max-markets", type=int, default=8000)
    parser.add_argument("--database-url", type=str, default="")
    args = parser.parse_args()

    settings = get_settings()
    db_url = str(args.database_url or os.getenv("DATABASE_URL") or settings.database_url)
    safe_db_url = _redact_db_url(db_url)
    engine = create_engine(db_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    schema = _schema_snapshot(engine)
    try:
        with SessionLocal() as db:
            report = {
                "generated_at": datetime.now(UTC).isoformat(),
                "database_url_used": safe_db_url,
                "schema_snapshot": schema,
                "latest_generate_signals": _latest_generate_signals_stats(db),
                "recent_tail_signals": _tail_signal_counts(db, hours=args.hours),
                "candidate_scan": _candidate_scan_audit(
                    db,
                    max_markets=args.max_markets,
                    markets_columns=set(schema.get("markets", {}).get("columns", [])),
                ),
                "platform_coverage": _platform_coverage(db),
            }
        print(json.dumps(report, indent=2, ensure_ascii=False))
    except OperationalError as exc:
        err_code, detail = _classify_db_error(exc)
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": err_code,
                    "database_url_used": safe_db_url,
                    "schema_snapshot": schema,
                    "hint": "Pass --database-url (e.g. sqlite:///artifacts/research/stage5_xplat3.db) or export DATABASE_URL.",
                    "detail": detail,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
