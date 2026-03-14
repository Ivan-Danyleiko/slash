#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.services.research.ab_testing import build_ab_testing_report, extract_ab_testing_metrics
from app.services.research.data_quality import (
    build_signal_history_data_quality_report,
    extract_data_quality_metrics,
)
from app.services.research.deliverables import (
    build_build_vs_buy_time_saved_estimate,
    build_research_stack_readiness_report,
    build_stack_decision_log,
    extract_build_vs_buy_metrics,
)
from app.services.research.ethics import build_ethics_report, extract_ethics_metrics
from app.services.research.event_cluster_research import (
    build_event_cluster_research_report,
    extract_event_cluster_metrics,
)
from app.services.research.export_package import (
    build_stage5_export_decision_rows,
    build_stage5_export_package,
)
from app.services.research.final_report import (
    build_stage5_final_report,
    extract_stage5_final_report_metrics,
)
from app.services.research.liquidity_safety import (
    build_liquidity_safety_report,
    extract_liquidity_safety_metrics,
)
from app.services.research.platform_comparison import (
    build_platform_comparison_report,
    extract_platform_comparison_metrics,
)
from app.services.research.provider_reliability import (
    build_provider_reliability_report,
    extract_provider_reliability_metrics,
)
from app.services.research.ranking_research import (
    build_ranking_research_report,
    extract_ranking_research_metrics,
)
from app.services.research.readiness_gate import (
    build_stage5_readiness_gate,
    extract_stage5_readiness_gate_metrics,
)
from app.services.research.signal_lifetime import (
    build_signal_lifetime_report,
    extract_signal_lifetime_metrics,
)
from app.services.research.signal_type_research import (
    build_signal_type_research_report,
    extract_signal_type_research_metrics,
)
from app.services.research.tracking import record_stage5_experiment


def _track(name: str, metrics: dict[str, float], tags: dict[str, str] | None = None) -> dict:
    return record_stage5_experiment(
        run_name=name,
        params={"batch": "stage5_track_batch"},
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

    out_json = out_dir / f"stage5_batch_{now}.json"
    out_csv = out_dir / f"stage5_export_{now}.csv"

    with session_factory() as db:
        data_quality = build_signal_history_data_quality_report(db, days=30, limit=10000)
        provider = build_provider_reliability_report(db, days=7, limit_runs=1000)
        ab = build_ab_testing_report(db, days=30)
        ethics = build_ethics_report(db, top_window=50)
        ranking = build_ranking_research_report(db, days=30, horizon="6h", top_k=50, min_samples=20)
        platform = build_platform_comparison_report(db, days=30, horizon="6h", min_samples=20)
        signal_types = build_signal_type_research_report(db, days=30, horizon="6h", min_labeled_returns=20)
        event_clusters = build_event_cluster_research_report(db, days=30, horizon="6h")
        lifetime = build_signal_lifetime_report(db, days=30)
        liquidity = build_liquidity_safety_report(db, days=30, position_sizes="50,100,500", min_samples=20)
        final_report = build_stage5_final_report(db, days=30, horizon="6h", min_labeled_returns=20)
        readiness_gate = build_stage5_readiness_gate(db, days=30, horizon="6h", min_labeled_returns=20)

        stack_log = build_stack_decision_log(settings=settings)
        stack_readiness = build_research_stack_readiness_report(settings=settings)
        build_buy = build_build_vs_buy_time_saved_estimate(settings=settings)

        tracked = {
            "data_quality": _track("stage5_data_quality_batch", extract_data_quality_metrics(data_quality)),
            "provider_reliability": _track(
                "stage5_provider_reliability_batch",
                extract_provider_reliability_metrics(provider),
            ),
            "ab_testing": _track("stage5_ab_testing_batch", extract_ab_testing_metrics(ab)),
            "ethics": _track("stage5_ethics_batch", extract_ethics_metrics(ethics)),
            "ranking": _track("stage5_ranking_batch", extract_ranking_research_metrics(ranking)),
            "platform_comparison": _track(
                "stage5_platform_comparison_batch",
                extract_platform_comparison_metrics(platform),
            ),
            "signal_types": _track(
                "stage5_signal_types_batch",
                extract_signal_type_research_metrics(signal_types),
            ),
            "event_clusters": _track(
                "stage5_event_clusters_batch",
                extract_event_cluster_metrics(event_clusters),
            ),
            "signal_lifetime": _track(
                "stage5_signal_lifetime_batch",
                extract_signal_lifetime_metrics(lifetime),
            ),
            "liquidity_safety": _track(
                "stage5_liquidity_safety_batch",
                extract_liquidity_safety_metrics(liquidity),
            ),
            "final_report": _track(
                "stage5_final_report_batch",
                extract_stage5_final_report_metrics(final_report),
            ),
            "readiness_gate": _track(
                "stage5_readiness_gate_batch",
                extract_stage5_readiness_gate_metrics(readiness_gate),
                tags={"status": str(readiness_gate.get("status", "UNKNOWN"))},
            ),
            "build_vs_buy": _track(
                "stage5_build_vs_buy_batch",
                extract_build_vs_buy_metrics(build_buy),
            ),
        }

        export_package = build_stage5_export_package(db, days=30, horizon="6h", min_labeled_returns=20)

        decision_rows = build_stage5_export_decision_rows(export_package)
        header = [
            "signal_type",
            "decision",
            "status",
            "returns_labeled",
            "hit_rate",
            "avg_return",
            "ev_pct",
            "sharpe_like",
            "risk_of_ruin",
            "median_lifetime_hours",
        ]
        with out_csv.open("w", encoding="utf-8", newline="") as f:
            f.write(",".join(header) + "\n")
            for row in decision_rows:
                f.write(
                    ",".join(
                        [
                            str(row.get("signal_type", "")),
                            str(row.get("decision", "")),
                            str(row.get("status", "")),
                            str(row.get("returns_labeled", "")),
                            str(row.get("hit_rate", "")),
                            str(row.get("avg_return", "")),
                            str(row.get("ev_pct", "")),
                            str(row.get("sharpe_like", "")),
                            str(row.get("risk_of_ruin", "")),
                            str(row.get("median_lifetime_hours", "")),
                        ]
                    )
                    + "\n"
                )

        payload = {
            "generated_at": datetime.now(UTC).isoformat(),
            "database_url": settings.database_url,
            "stack_readiness": stack_readiness,
            "stack_decision_log": stack_log,
            "build_vs_buy": build_buy,
            "tracked_runs": tracked,
            "reports": {
                "data_quality": data_quality,
                "provider_reliability": provider,
                "ab_testing": ab,
                "ethics": ethics,
                "ranking": ranking,
                "platform_comparison": platform,
                "signal_types": signal_types,
                "event_clusters": event_clusters,
                "signal_lifetime": lifetime,
                "liquidity_safety": liquidity,
                "final_report": final_report,
                "readiness_gate": readiness_gate,
                "export_package": export_package,
            },
            "artifacts": {
                "json": str(out_json),
                "csv": str(out_csv),
            },
        }
        out_json.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    print(f"stage5_batch_json={out_json}")
    print(f"stage5_batch_csv={out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
