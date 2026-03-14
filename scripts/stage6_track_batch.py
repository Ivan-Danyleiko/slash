#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.services.research.stage6_final_report import (
    build_stage6_final_report,
    extract_stage6_final_report_metrics,
)
from app.services.research.stage6_governance import (
    build_stage6_governance_report,
    extract_stage6_governance_metrics,
)
from app.services.research.stage6_risk_guardrails import (
    build_stage6_risk_guardrails_report,
    extract_stage6_risk_guardrails_metrics,
)
from app.services.research.stage6_type35 import (
    build_stage6_type35_report,
    extract_stage6_type35_metrics,
)
from app.services.research.walkforward import (
    build_walkforward_report,
    extract_walkforward_metrics,
)
from app.services.research.ranking_research import (
    build_ranking_research_report,
    extract_ranking_research_metrics,
)
from app.services.research.tracking import record_stage5_experiment


def _track(name: str, metrics: dict[str, float], tags: dict[str, str] | None = None) -> dict:
    return record_stage5_experiment(
        run_name=name,
        params={"batch": "stage6_track_batch"},
        metrics=metrics,
        tags=tags or {},
    )


def main() -> int:
    settings = get_settings()
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

    out_json = out_dir / f"stage6_batch_{now}.json"
    out_csv = out_dir / f"stage6_export_{now}.csv"

    with session_factory() as db:
        governance = build_stage6_governance_report(db, days=30, horizon="6h", min_labeled_returns=20)
        guardrails = build_stage6_risk_guardrails_report(db, days=7, horizon="6h")
        type35 = build_stage6_type35_report(db, days=30, horizon="6h", min_labeled_returns=20)
        walkforward = build_walkforward_report(
            db,
            days=90,
            horizon="6h",
            train_days=30,
            test_days=14,
            step_days=14,
            embargo_hours=24,
            min_samples_per_window=100,
        )
        ranking = build_ranking_research_report(db, days=30, horizon="6h", top_k=50, min_samples=20)
        final_report = build_stage6_final_report(db, days=30, horizon="6h", min_labeled_returns=20)

        tracked = {
            "governance": _track(
                "stage6_governance_batch",
                extract_stage6_governance_metrics(governance),
                tags={"decision": str(governance.get("decision"))},
            ),
            "risk_guardrails": _track(
                "stage6_risk_guardrails_batch",
                extract_stage6_risk_guardrails_metrics(guardrails),
                tags={"level": str(guardrails.get("circuit_breaker_level"))},
            ),
            "type35": _track(
                "stage6_type35_batch",
                extract_stage6_type35_metrics(type35),
                tags={"counts": str(type35.get("decision_counts"))},
            ),
            "walkforward": _track(
                "stage6_walkforward_batch",
                extract_walkforward_metrics(walkforward),
            ),
            "ranking": _track(
                "stage6_ranking_batch",
                extract_ranking_research_metrics(ranking),
            ),
            "final_report": _track(
                "stage6_final_report_batch",
                extract_stage6_final_report_metrics(final_report),
                tags={"final_decision": str(final_report.get("final_decision"))},
            ),
        }

        header = [
            "type_label",
            "signal_type",
            "decision",
            "reason",
            "rows_total",
            "returns_labeled",
            "avg_return",
            "hit_rate",
            "sharpe_like",
            "risk_of_ruin",
            "subhour_coverage",
        ]
        with out_csv.open("w", encoding="utf-8", newline="") as f:
            f.write(",".join(header) + "\n")
            for row in list(type35.get("rows") or []):
                f.write(
                    ",".join(
                        [
                            str(row.get("type_label", "")),
                            str(row.get("signal_type", "")),
                            str(row.get("decision", "")),
                            str(row.get("reason", "")).replace(",", ";"),
                            str(row.get("rows_total", "")),
                            str(row.get("returns_labeled", "")),
                            str(row.get("avg_return", "")),
                            str(row.get("hit_rate", "")),
                            str(row.get("sharpe_like", "")),
                            str(row.get("risk_of_ruin", "")),
                            str(row.get("subhour_coverage", "")),
                        ]
                    )
                    + "\n"
                )

        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "database_url": settings.database_url,
            "tracked_runs": tracked,
            "reports": {
                "stage6_governance": governance,
                "stage6_risk_guardrails": guardrails,
                "stage6_type35": type35,
                "stage6_walkforward": walkforward,
                "stage6_ranking": ranking,
                "stage6_final_report": final_report,
            },
            "artifacts": {
                "json": str(out_json),
                "csv": str(out_csv),
            },
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    print(f"stage6_batch_json={out_json}")
    print(f"stage6_batch_csv={out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
