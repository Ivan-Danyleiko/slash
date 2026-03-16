#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.services.research.stage8_final_report import (
    build_stage8_final_report,
    extract_stage8_final_report_metrics,
)
from app.services.research.stage8_shadow_ledger import (
    build_stage8_shadow_ledger_report,
    extract_stage8_shadow_ledger_metrics,
)
from app.services.research.tracking import record_stage5_experiment


def main() -> int:
    settings = get_settings()
    now = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("artifacts/research")
    out_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    out_json = out_dir / f"stage8_batch_{now}.json"
    out_csv = out_dir / f"stage8_export_{now}.csv"
    out_jsonl = out_dir / f"stage8_shadow_ledger_{now}.jsonl"
    out_md = out_dir / f"stage8_final_report_{now}.md"

    with session_factory() as db:
        shadow = build_stage8_shadow_ledger_report(db, settings=settings, lookback_days=14, limit=300)
        final = build_stage8_final_report(
            db,
            settings=settings,
            lookback_days=14,
            limit=300,
            shadow_report=shadow,
        )

        shadow_tracking = record_stage5_experiment(
            run_name="stage8_shadow_ledger_batch",
            params={"report_type": "stage8_shadow_ledger", "lookback_days": 14, "limit": 300},
            metrics=extract_stage8_shadow_ledger_metrics(shadow),
            tags={"policy_profile": settings.stage8_policy_profile},
        )
        final_tracking = record_stage5_experiment(
            run_name="stage8_final_report_batch",
            params={"report_type": "stage8_final_report", "lookback_days": 14, "limit": 300},
            metrics=extract_stage8_final_report_metrics(final),
            tags={"final_decision": str(final.get("final_decision") or "")},
        )

        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "database_url": "***redacted***",
            "reports": {
                "stage8_shadow_ledger": shadow,
                "stage8_final_report": final,
            },
            "tracking": {
                "stage8_shadow_ledger": shadow_tracking,
                "stage8_final_report": final_tracking,
            },
            "artifacts": {
                "json": str(out_json),
                "csv": str(out_csv),
                "jsonl": str(out_jsonl),
                "md": str(out_md),
            },
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        with out_jsonl.open("w", encoding="utf-8") as f:
            for row in shadow.get("rows") or []:
                f.write(json.dumps(row, ensure_ascii=True) + "\n")
        with out_csv.open("w", encoding="utf-8", newline="") as f:
            f.write("category,total,keep_count,execute_allowed_count,edge_after_costs_mean,kelly_fraction_mean,pnl_proxy_usd_100_mean\n")
            for category, stats in dict(shadow.get("per_category") or {}).items():
                f.write(
                    f"{category},{stats.get('total',0)},{stats.get('keep_count',0)},{stats.get('execute_allowed_count',0)},"
                    f"{stats.get('edge_after_costs_mean',0)},{stats.get('kelly_fraction_mean',0)},{stats.get('pnl_proxy_usd_100_mean',0)}\n"
                )
        lines = [
            "# Stage 8 Final Report",
            "",
            f"- generated_at: {payload['generated_at']}",
            f"- final_decision: {final.get('final_decision')}",
            f"- recommended_action: {final.get('recommended_action')}",
            "",
            "## Summary",
            "",
        ]
        for key, value in dict(final.get("summary") or {}).items():
            lines.append(f"- {key}: {value}")
        out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"stage8_batch_json={out_json}")
    print(f"stage8_batch_csv={out_csv}")
    print(f"stage8_shadow_ledger_jsonl={out_jsonl}")
    print(f"stage8_final_report_md={out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
