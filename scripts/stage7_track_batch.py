#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.services.research.stage7_stack_scorecard import (
    build_stage7_stack_scorecard_report,
    extract_stage7_stack_scorecard_metrics,
)
from app.services.research.stage7_harness import (
    build_stage7_harness_report,
    extract_stage7_harness_metrics,
)
from app.services.research.stage7_shadow import (
    build_stage7_shadow_report,
    extract_stage7_shadow_metrics,
)
from app.services.research.stage7_final_report import (
    build_stage7_final_report,
    extract_stage7_final_report_metrics,
)
from app.services.research.tracking import record_stage5_experiment


def _track(name: str, metrics: dict[str, float], tags: dict[str, str] | None = None) -> dict:
    return record_stage5_experiment(
        run_name=name,
        params={"batch": "stage7_track_batch"},
        metrics=metrics,
        tags=tags or {},
    )


def _write_final_md(path: Path, report: dict) -> None:
    summary = report.get("summary") or {}
    checks = report.get("checks") or {}
    lines = [
        "# Stage 7 Final Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- final_decision: {report.get('final_decision')}",
        f"- recommended_action: {report.get('recommended_action')}",
        "",
        "## Summary",
        "",
        f"- stage6_final_decision: {summary.get('stage6_final_decision')}",
        f"- agent_decision_coverage: {summary.get('agent_decision_coverage')}",
        f"- delta_keep_rate: {summary.get('delta_keep_rate')}",
        f"- baseline_post_hoc_precision: {summary.get('baseline_post_hoc_precision')}",
        f"- post_hoc_precision: {summary.get('post_hoc_precision')}",
        f"- reason_code_stability: {summary.get('reason_code_stability')}",
        f"- latency_p95_ms: {summary.get('latency_p95_ms')}",
        f"- top_stack: {summary.get('top_stack')}",
        "",
        "## Checks",
        "",
    ]
    for key, value in checks.items():
        lines.append(f"- {key}: {bool(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    settings = get_settings()
    shadow_lookback_days = max(7, min(int(os.getenv("STAGE7_SHADOW_LOOKBACK_DAYS", "14")), 90))
    shadow_limit = max(50, min(int(os.getenv("STAGE7_SHADOW_LIMIT", "300")), 2000))
    now = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("artifacts/research")
    out_dir.mkdir(parents=True, exist_ok=True)

    if settings.database_url.startswith("sqlite:///"):
        db_path = Path(settings.database_url.replace("sqlite:///", "", 1))
        if db_path.parent and str(db_path.parent) not in (".", ""):
            db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    out_json = out_dir / f"stage7_batch_{now}.json"
    out_csv = out_dir / f"stage7_export_{now}.csv"
    out_jsonl = out_dir / f"stage7_agent_decisions_{now}.jsonl"
    out_md = out_dir / f"stage7_final_report_{now}.md"

    with session_factory() as db:
        harness = build_stage7_harness_report(max_latency_ms=int(settings.stage7_agent_max_latency_ms))
        scorecard = build_stage7_stack_scorecard_report(harness_by_stack=harness.get("by_stack"))
        shadow = build_stage7_shadow_report(
            db,
            settings=settings,
            lookback_days=shadow_lookback_days,
            limit=shadow_limit,
        )
        final = build_stage7_final_report(
            db,
            settings=settings,
            lookback_days=shadow_lookback_days,
            limit=shadow_limit,
            stage6_days=30,
            stage6_horizon="6h",
            stage6_min_labeled_returns=30,
        )

        tracked = {
            "harness": _track(
                "stage7_harness_batch",
                extract_stage7_harness_metrics(harness),
                tags={"stacks_tested": str((harness.get("summary") or {}).get("stacks_tested"))},
            ),
            "stack_scorecard": _track(
                "stage7_stack_scorecard_batch",
                extract_stage7_stack_scorecard_metrics(scorecard),
                tags={"top_stack": str((scorecard.get("summary") or {}).get("top_stack"))},
            ),
            "shadow": _track(
                "stage7_shadow_batch",
                extract_stage7_shadow_metrics(shadow),
                tags={"provider": settings.stage7_agent_provider},
            ),
            "final_report": _track(
                "stage7_final_report_batch",
                extract_stage7_final_report_metrics(final),
                tags={"final_decision": str(final.get("final_decision"))},
            ),
        }

        with out_csv.open("w", encoding="utf-8", newline="") as f:
            header = ["stack", "weighted_score", "recommendation", "role"]
            f.write(",".join(header) + "\n")
            for row in list(scorecard.get("rows") or []):
                f.write(
                    ",".join(
                        [
                            str(row.get("stack") or ""),
                            str(row.get("weighted_score") or ""),
                            str(row.get("recommendation") or ""),
                            str(row.get("role") or ""),
                        ]
                    )
                    + "\n"
                )

        with out_jsonl.open("w", encoding="utf-8") as f:
            for row in list(shadow.get("rows") or []):
                f.write(json.dumps(row, ensure_ascii=True) + "\n")

        _write_final_md(out_md, final)

        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "database_url": settings.database_url,
            "shadow_lookback_days": shadow_lookback_days,
            "shadow_limit": shadow_limit,
            "tracked_runs": tracked,
            "reports": {
                "stage7_harness": harness,
                "stage7_stack_scorecard": scorecard,
                "stage7_shadow": shadow,
                "stage7_final_report": final,
            },
            "artifacts": {
                "json": str(out_json),
                "csv": str(out_csv),
                "jsonl": str(out_jsonl),
                "md": str(out_md),
            },
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    print(f"stage7_batch_json={out_json}")
    print(f"stage7_batch_csv={out_csv}")
    print(f"stage7_batch_jsonl={out_jsonl}")
    print(f"stage7_batch_md={out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
