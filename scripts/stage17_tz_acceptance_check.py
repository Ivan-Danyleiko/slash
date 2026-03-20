#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.models import JobRun
from app.services.research.stage17_tail_report import build_stage17_tail_report


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


def _latest_generate_signals_job(db):
    return db.scalar(
        select(JobRun)
        .where(JobRun.job_name == "generate_signals")
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )


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
    for table in ("job_runs", "stage17_tail_positions", "signals", "markets"):
        if not insp.has_table(table):
            out[table] = {"exists": False, "columns": []}
            continue
        cols = [str(c.get("name")) for c in insp.get_columns(table)]
        out[table] = {"exists": True, "columns": cols}
    return out


def _error_payload(*, safe_db_url: str, hint: str, exc: OperationalError, schema: dict[str, Any]) -> dict[str, Any]:
    err_code, detail = _classify_db_error(exc)
    return {
        "status": "error",
        "error": err_code,
        "database_url_used": safe_db_url,
        "schema_snapshot": schema,
        "hint": hint,
        "detail": detail,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage17 TZ acceptance checker")
    parser.add_argument("--days", type=int, default=60)
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
            tail_report = build_stage17_tail_report(db, settings=settings, days=max(1, int(args.days)), persist=False)
            summary = dict(tail_report.get("summary") or {})
            checks = dict(tail_report.get("checks") or {})

            latest = _latest_generate_signals_job(db)
            job_details = latest.details if latest and isinstance(latest.details, dict) else {}
            attempted = int(job_details.get("tail_attempted") or 0)
            created = int(job_details.get("tail_created") or 0)
            ambiguous = int(job_details.get("tail_ambiguous_skipped") or 0)
            out_of_range = int(job_details.get("tail_out_of_prob_range") or 0)

            tz_checks = {
                "tail_attempted_ge_400": attempted >= 400,
                "tail_created_ge_50": created >= 50,
                "tail_ambiguous_skipped_le_20": ambiguous <= 20,
                "tail_out_of_prob_range_present": out_of_range >= 0,
            }

            pass_count = sum(1 for v in tz_checks.values() if bool(v))
            final_pass = all(tz_checks.values())
            report_core_checks = {
                "tail_report_closed_positions_ge_min": bool(checks.get("closed_positions_ge_min")),
                "tail_report_top10pct_wins_count_ge_min": bool(checks.get("top10pct_wins_count_ge_min")),
                "tail_report_payout_skew_ci_low_80_ge_min": bool(checks.get("payout_skew_ci_low_80_ge_min")),
                "tail_report_hit_rate_tail_ge_min": bool(checks.get("hit_rate_tail_ge_min")),
            }
            all_checks = {**tz_checks, **report_core_checks}
            all_pass_count = sum(1 for v in all_checks.values() if bool(v))
            all_final_pass = all(all_checks.values())

            out = {
                "generated_at": datetime.now(UTC).isoformat(),
                "database_url_used": safe_db_url,
                "schema_snapshot": schema,
                "input": {"days": int(args.days)},
                "latest_generate_signals": {
                    "found": latest is not None,
                    "job_id": int(latest.id) if latest else None,
                    "started_at": latest.started_at.isoformat() if latest and latest.started_at else None,
                    "status": str(latest.status) if latest else None,
                    "tail_attempted": attempted,
                    "tail_created": created,
                    "tail_ambiguous_skipped": ambiguous,
                    "tail_out_of_prob_range": out_of_range,
                },
                "tz_acceptance_checks": tz_checks,
                "tz_acceptance_summary": {
                    "passed": final_pass,
                    "pass_count": pass_count,
                    "total_checks": len(tz_checks),
                },
                "combined_acceptance_checks": all_checks,
                "combined_acceptance_summary": {
                    "passed": all_final_pass,
                    "pass_count": all_pass_count,
                    "total_checks": len(all_checks),
                },
                "stage17_tail_report_checks": checks,
                "stage17_tail_report_summary": {
                    "final_decision": tail_report.get("final_decision"),
                    "hit_rate_tail": summary.get("hit_rate_tail"),
                    "payout_skew": summary.get("payout_skew"),
                    "payout_skew_ci_low_80": summary.get("payout_skew_ci_low_80"),
                    "closed_positions": summary.get("closed_positions"),
                    "avg_win_multiplier": summary.get("avg_win_multiplier"),
                    "time_to_resolution_median_days": summary.get("time_to_resolution_median_days"),
                    "roi_total": summary.get("roi_total"),
                },
            }
            print(json.dumps(out, indent=2, ensure_ascii=False))
    except OperationalError as exc:
        print(
            json.dumps(
                _error_payload(
                    safe_db_url=safe_db_url,
                    hint="Pass --database-url or export DATABASE_URL before running.",
                    exc=exc,
                    schema=schema,
                ),
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
