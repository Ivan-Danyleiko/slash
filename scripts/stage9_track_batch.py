#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.services.research.stage9_batch import build_stage9_batch_report


def _write_status(path: Path, *, payload: dict) -> None:
    reports = payload.get("reports") or {}
    consensus = reports.get("stage9_consensus_quality") or {}
    labeling = reports.get("stage9_directional_labeling") or {}
    execution = reports.get("stage9_execution_realism") or {}
    final = reports.get("stage9_final_report") or {}
    failed_checks = list(final.get("failed_checks") or [])
    lines = [
        "# TZ Stage 9 Status",
        "",
        f"- updated_at: {datetime.now(UTC).isoformat()}",
        f"- batch_generated_at: {payload.get('generated_at')}",
        f"- final_decision: {final.get('final_decision')}",
        f"- recommended_action: {final.get('recommended_action')}",
        f"- failed_checks_count: {len(failed_checks)}",
        "",
        "## Consensus",
        "",
        f"- metaculus_median_fill_rate: {consensus.get('metaculus_median_fill_rate')}",
        f"- consensus_2source_share: {consensus.get('consensus_2source_share')}",
        f"- consensus_3source_share: {consensus.get('consensus_3source_share')}",
        f"- consensus_two_source_mode_share: {consensus.get('consensus_two_source_mode_share')}",
        f"- consensus_insufficient_sources_share: {consensus.get('consensus_insufficient_sources_share')}",
        "",
        "## Directional Labeling",
        "",
        f"- direction_labeled_share: {labeling.get('direction_labeled_share')}",
        f"- direction_missing_label_share: {labeling.get('direction_missing_label_share')}",
        f"- void_outcome_share: {labeling.get('void_outcome_share')}",
        "",
        "## Execution Realism",
        "",
        f"- non_zero_edge_share: {execution.get('non_zero_edge_share')}",
        f"- spread_coverage_share: {execution.get('spread_coverage_share')}",
        f"- open_interest_coverage_share: {execution.get('open_interest_coverage_share')}",
        f"- brier_skill_score: {execution.get('brier_skill_score')}",
        f"- ece: {execution.get('ece')}",
        f"- precision_at_25: {execution.get('precision_at_25')}",
        f"- auprc: {execution.get('auprc')}",
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

    out_json = out_dir / f"stage9_batch_{now}.json"
    out_csv = out_dir / f"stage9_export_{now}.csv"
    status_md = Path("docs/TZ_STAGE9_STATUS.md")

    with session_factory() as db:
        payload = build_stage9_batch_report(db, settings=settings)
        payload["database_url"] = "***redacted***"
        payload["artifacts"] = {"json": str(out_json), "csv": str(out_csv)}
        out_json.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

        reports = payload.get("reports") or {}
        consensus = reports.get("stage9_consensus_quality") or {}
        labeling = reports.get("stage9_directional_labeling") or {}
        execution = reports.get("stage9_execution_realism") or {}
        final = reports.get("stage9_final_report") or {}
        with out_csv.open("w", encoding="utf-8", newline="") as f:
            f.write("metric,value\n")
            rows = {
                "final_decision": final.get("final_decision"),
                "failed_checks_count": len(list(final.get("failed_checks") or [])),
                "metaculus_median_fill_rate": consensus.get("metaculus_median_fill_rate"),
                "consensus_2source_share": consensus.get("consensus_2source_share"),
                "consensus_3source_share": consensus.get("consensus_3source_share"),
                "consensus_two_source_mode_share": consensus.get("consensus_two_source_mode_share"),
                "consensus_insufficient_sources_share": consensus.get("consensus_insufficient_sources_share"),
                "direction_labeled_share": labeling.get("direction_labeled_share"),
                "direction_missing_label_share": labeling.get("direction_missing_label_share"),
                "void_outcome_share": labeling.get("void_outcome_share"),
                "non_zero_edge_share": execution.get("non_zero_edge_share"),
                "spread_coverage_share": execution.get("spread_coverage_share"),
                "polymarket_spread_coverage_share": execution.get("polymarket_spread_coverage_share"),
                "open_interest_coverage_share": execution.get("open_interest_coverage_share"),
                "brier_skill_score": execution.get("brier_skill_score"),
                "ece": execution.get("ece"),
                "longshot_bias_error_0_15pct": execution.get("longshot_bias_error_0_15pct"),
                "precision_at_10": execution.get("precision_at_10"),
                "precision_at_25": execution.get("precision_at_25"),
                "precision_at_50": execution.get("precision_at_50"),
                "auprc": execution.get("auprc"),
                "stage8_zero_edge_share": (final.get("summary") or {}).get("stage8_zero_edge_share"),
            }
            for k, v in rows.items():
                f.write(f"{k},{v}\n")

        _write_status(status_md, payload=payload)

    print(f"stage9_batch_json={out_json}")
    print(f"stage9_batch_csv={out_csv}")
    print(f"stage9_status_md={status_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
