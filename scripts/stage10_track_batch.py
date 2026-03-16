#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.services.research.stage10_batch import build_stage10_batch_report


def _write_status(path: Path, *, payload: dict) -> None:
    reports = payload.get("reports") or {}
    replay = reports.get("stage10_replay") or {}
    timeline = reports.get("stage10_timeline_quality") or {}
    backfill = reports.get("stage10_timeline_backfill_plan") or {}
    audit = reports.get("stage10_module_audit") or {}
    final = reports.get("stage10_final_report") or {}

    replay_summary = replay.get("summary") or {}
    audit_summary = audit.get("summary") or {}
    failed_checks = list(final.get("failed_checks") or [])

    lines = [
        "# TZ Stage 10 Status",
        "",
        f"- updated_at: {datetime.now(UTC).isoformat()}",
        f"- batch_generated_at: {payload.get('generated_at')}",
        f"- final_decision: {final.get('final_decision')}",
        f"- recommended_action: {final.get('recommended_action')}",
        f"- failed_checks_count: {len(failed_checks)}",
        "",
        "## Replay",
        "",
        f"- rows_total: {replay_summary.get('rows_total')}",
        f"- events_total: {replay_summary.get('events_total')}",
        f"- event_target: {replay_summary.get('event_target')}",
        f"- event_target_reached: {replay_summary.get('event_target_reached')}",
        f"- leakage_violations_count: {replay_summary.get('leakage_violations_count')}",
        f"- leakage_violation_rate: {replay_summary.get('leakage_violation_rate')}",
        f"- data_insufficient_timeline_share: {replay_summary.get('data_insufficient_timeline_share')}",
        f"- post_cost_ev_ci_low_80: {replay_summary.get('post_cost_ev_ci_low_80')}",
        f"- reason_code_stability: {replay_summary.get('reason_code_stability')}",
        "",
        "## Timeline Quality",
        "",
        f"- timeline_rows_total: {timeline.get('rows_total')}",
        f"- data_insufficient_timeline_share: {timeline.get('data_insufficient_timeline_share')}",
        "",
        "## Timeline Backfill Plan",
        "",
        f"- markets_scanned: {backfill.get('markets_scanned')}",
        f"- manifold_readiness: {(backfill.get('timeline_readiness_by_platform') or {}).get('MANIFOLD')}",
        f"- metaculus_readiness: {(backfill.get('timeline_readiness_by_platform') or {}).get('METACULUS')}",
        "",
        "## Module Audit",
        "",
        f"- candidates_total: {audit_summary.get('candidates_total')}",
        f"- security_pass_count: {audit_summary.get('security_pass_count')}",
        f"- security_fail_count: {audit_summary.get('security_fail_count')}",
        f"- allowed_for_replay_count: {audit_summary.get('allowed_for_replay_count')}",
        f"- stage10_llm_budget_ratio: {audit_summary.get('stage10_llm_budget_ratio')}",
        f"- stage10_llm_mode: {audit_summary.get('stage10_llm_mode')}",
        "",
        "## Failed Checks",
        "",
    ]
    if failed_checks:
        for check in failed_checks:
            lines.append(f"- {check}")
    else:
        lines.append("- none")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    settings = get_settings()
    now = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("artifacts/research")
    out_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    out_json = out_dir / f"stage10_batch_{now}.json"
    out_csv = out_dir / f"stage10_export_{now}.csv"
    status_md = Path("docs/TZ_STAGE10_STATUS.md")

    with session_factory() as db:
        payload = build_stage10_batch_report(db, settings=settings)
        payload["database_url"] = "***redacted***"
        payload["artifacts"] = {"json": str(out_json), "csv": str(out_csv)}
        out_json.write_text(json.dumps(payload, ensure_ascii=True, indent=2, default=str), encoding="utf-8")

        reports = payload.get("reports") or {}
        replay = reports.get("stage10_replay") or {}
        timeline = reports.get("stage10_timeline_quality") or {}
        backfill = reports.get("stage10_timeline_backfill_plan") or {}
        audit = reports.get("stage10_module_audit") or {}
        final = reports.get("stage10_final_report") or {}
        replay_summary = replay.get("summary") or {}
        audit_summary = audit.get("summary") or {}

        with out_csv.open("w", encoding="utf-8", newline="") as f:
            f.write("metric,value\n")
            rows = {
                "final_decision": final.get("final_decision"),
                "failed_checks_count": len(list(final.get("failed_checks") or [])),
                "rows_total": replay_summary.get("rows_total"),
                "events_total": replay_summary.get("events_total"),
                "event_target": replay_summary.get("event_target"),
                "event_target_reached": replay_summary.get("event_target_reached"),
                "leakage_violations_count": replay_summary.get("leakage_violations_count"),
                "leakage_violation_rate": replay_summary.get("leakage_violation_rate"),
                "data_insufficient_timeline_share": replay_summary.get("data_insufficient_timeline_share"),
                "core_categories_each_ge_20": replay_summary.get("core_categories_each_ge_20"),
                "post_cost_ev_ci_low_80": replay_summary.get("post_cost_ev_ci_low_80"),
                "reason_code_stability": replay_summary.get("reason_code_stability"),
                "scenario_sweeps_positive": (replay.get("scenario_sweeps") or {}).get("positive_scenarios"),
                "timeline_rows_total": timeline.get("rows_total"),
                "timeline_data_insufficient_share": timeline.get("data_insufficient_timeline_share"),
                "backfill_markets_scanned": backfill.get("markets_scanned"),
                "backfill_manifold_readiness": (backfill.get("timeline_readiness_by_platform") or {}).get("MANIFOLD"),
                "backfill_metaculus_readiness": (backfill.get("timeline_readiness_by_platform") or {}).get("METACULUS"),
                "walkforward_negative_window_share": (final.get("summary") or {}).get("walkforward_negative_window_share"),
                "candidates_total": audit_summary.get("candidates_total"),
                "security_pass_count": audit_summary.get("security_pass_count"),
                "security_fail_count": audit_summary.get("security_fail_count"),
                "allowed_for_replay_count": audit_summary.get("allowed_for_replay_count"),
                "stage10_llm_budget_ratio": audit_summary.get("stage10_llm_budget_ratio"),
                "stage10_llm_mode": audit_summary.get("stage10_llm_mode"),
            }
            for k, v in rows.items():
                f.write(f"{k},{v}\n")

        _write_status(status_md, payload=payload)

    print(f"stage10_batch_json={out_json}")
    print(f"stage10_batch_csv={out_csv}")
    print(f"stage10_status_md={status_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
