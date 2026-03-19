from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.secrets import redact_text


def _numeric_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in metrics.items():
        if isinstance(value, (int, float)):
            out[key] = float(value)
    return out


def record_stage5_experiment(
    *,
    run_name: str,
    params: dict[str, Any],
    metrics: dict[str, Any],
    tags: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    if not settings.research_tracking_enabled:
        return {"tracking_enabled": False, "recorded": False, "reason": "disabled_by_config"}

    now = datetime.now(UTC)
    registry_path = Path(settings.research_experiment_registry_path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "timestamp": now.isoformat(),
        "run_name": run_name,
        "params": params,
        "metrics": metrics,
        "tags": tags or {},
    }
    with registry_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=True) + "\n")

    mlflow_logged = False
    mlflow_error: str | None = None
    if settings.research_mlflow_enabled:
        try:
            import mlflow

            if settings.research_mlflow_tracking_uri:
                mlflow.set_tracking_uri(settings.research_mlflow_tracking_uri)
            mlflow.set_experiment(settings.research_mlflow_experiment_name)
            with mlflow.start_run(run_name=run_name):
                mlflow.log_params({k: str(v) for k, v in params.items()})
                mlflow.log_metrics(_numeric_metrics(metrics))
                if tags:
                    mlflow.set_tags({k: str(v) for k, v in tags.items()})
            mlflow_logged = True
        except Exception as exc:  # pragma: no cover
            mlflow_error = redact_text(str(exc), max_len=200)

    return {
        "tracking_enabled": True,
        "recorded": True,
        "registry_path": str(registry_path),
        "mlflow_enabled": bool(settings.research_mlflow_enabled),
        "mlflow_logged": mlflow_logged,
        "mlflow_error": mlflow_error,
    }


def read_stage5_experiments(*, limit: int = 100, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    limit = max(1, min(int(limit), 1000))
    registry_path = Path(settings.research_experiment_registry_path)
    if not registry_path.exists():
        return {"count": 0, "registry_path": str(registry_path), "rows": []}

    lines = registry_path.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"count": len(rows), "registry_path": str(registry_path), "rows": list(reversed(rows))}
