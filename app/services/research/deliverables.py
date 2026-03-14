from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
import re
import tomllib
from typing import Any

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class BuildBuyComponent:
    key: str
    area: str
    planned_tool: str
    planned_package: str
    custom_fallback: str
    planned_build_days: int
    planned_setup_days: int


_COMPONENTS = [
    BuildBuyComponent(
        key="data_collection_polymarket",
        area="Data Collection",
        planned_tool="py-clob-client",
        planned_package="py-clob-client",
        custom_fallback="existing Polymarket REST collector",
        planned_build_days=10,
        planned_setup_days=2,
    ),
    BuildBuyComponent(
        key="backtesting_engine",
        area="Backtesting",
        planned_tool="vectorbt",
        planned_package="vectorbt",
        custom_fallback="custom research simulation module",
        planned_build_days=18,
        planned_setup_days=3,
    ),
    BuildBuyComponent(
        key="performance_reports",
        area="Performance Metrics",
        planned_tool="quantstats",
        planned_package="quantstats",
        custom_fallback="custom stage5 metrics and reports",
        planned_build_days=6,
        planned_setup_days=1,
    ),
    BuildBuyComponent(
        key="experiment_tracking",
        area="Experiment Tracking",
        planned_tool="mlflow",
        planned_package="mlflow",
        custom_fallback="local JSONL experiment registry",
        planned_build_days=6,
        planned_setup_days=1,
    ),
    BuildBuyComponent(
        key="data_quality",
        area="Data Quality",
        planned_tool="great_expectations",
        planned_package="great-expectations",
        custom_fallback="custom signal_history DQ checks",
        planned_build_days=2,
        planned_setup_days=1,
    ),
]


def _is_package_installed(package: str) -> bool:
    return find_spec(package) is not None


def _normalize_pkg_name(name: str) -> str:
    return re.split(r"[<>=!~;\\[]", name.strip(), maxsplit=1)[0].strip().lower().replace("_", "-")


def _is_optional_dependency_declared(package: str, group: str = "research") -> bool:
    root = Path(__file__).resolve().parents[3]
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return False
    deps = (
        payload.get("project", {})
        .get("optional-dependencies", {})
        .get(group, [])
    )
    want = _normalize_pkg_name(package)
    normalized = {_normalize_pkg_name(str(d)) for d in deps}
    return want in normalized


def _component_status(component: BuildBuyComponent, settings: Settings) -> dict[str, Any]:
    declared = _is_optional_dependency_declared(component.planned_package)

    if component.key == "data_collection_polymarket":
        installed = _is_package_installed("py_clob_client")
        if installed:
            return {
                "status": "adopted",
                "decision": "BUY",
                "actual_solution": component.planned_tool,
                "notes": "SDK available for real orderbook integration.",
                "declared_in_pyproject_research": declared,
                "installed_in_env": installed,
                "enabled_by_config": True,
            }
        return {
            "status": "partial",
            "decision": "BUILD_NOW_BUY_LATER",
            "actual_solution": component.custom_fallback,
            "notes": "Current collector works, but no py-clob-client depth model yet.",
            "declared_in_pyproject_research": declared,
            "installed_in_env": installed,
            "enabled_by_config": True,
        }

    if component.key == "backtesting_engine":
        installed = _is_package_installed("vectorbt")
        if installed:
            return {
                "status": "adopted",
                "decision": "BUY",
                "actual_solution": component.planned_tool,
                "notes": "Vectorized backtesting stack available.",
                "declared_in_pyproject_research": declared,
                "installed_in_env": installed,
                "enabled_by_config": True,
            }
        return {
            "status": "pending",
            "decision": "PENDING_BUY",
            "actual_solution": component.custom_fallback,
            "notes": "Stage5 currently uses in-app simulation and Monte Carlo.",
            "declared_in_pyproject_research": declared,
            "installed_in_env": installed,
            "enabled_by_config": True,
        }

    if component.key == "performance_reports":
        installed = _is_package_installed("quantstats")
        if installed:
            return {
                "status": "adopted",
                "decision": "BUY",
                "actual_solution": component.planned_tool,
                "notes": "QuantStats can generate standardized reports.",
                "declared_in_pyproject_research": declared,
                "installed_in_env": installed,
                "enabled_by_config": True,
            }
        return {
            "status": "pending",
            "decision": "PENDING_BUY",
            "actual_solution": component.custom_fallback,
            "notes": "Using internal metrics endpoints for now.",
            "declared_in_pyproject_research": declared,
            "installed_in_env": installed,
            "enabled_by_config": True,
        }

    if component.key == "experiment_tracking":
        installed = _is_package_installed("mlflow")
        enabled = bool(settings.research_mlflow_enabled)
        if enabled and installed:
            return {
                "status": "adopted",
                "decision": "BUY_WITH_FALLBACK",
                "actual_solution": "mlflow + local registry fallback",
                "notes": "MLflow enabled via config.",
                "declared_in_pyproject_research": declared,
                "installed_in_env": installed,
                "enabled_by_config": enabled,
            }
        if enabled and not installed:
            return {
                "status": "partial",
                "decision": "CONFIG_ENABLED_DEP_MISSING",
                "actual_solution": component.custom_fallback,
                "notes": "MLflow enabled in config, but package is not installed in current env.",
                "declared_in_pyproject_research": declared,
                "installed_in_env": installed,
                "enabled_by_config": enabled,
            }
        return {
            "status": "partial",
            "decision": "BUILD_FALLBACK_ACTIVE",
            "actual_solution": component.custom_fallback,
            "notes": "Local registry active; MLflow can be enabled later.",
            "declared_in_pyproject_research": declared,
            "installed_in_env": installed,
            "enabled_by_config": enabled,
        }

    if component.key == "data_quality":
        installed = _is_package_installed("great_expectations")
        enabled = bool(settings.research_great_expectations_enabled)
        if enabled and installed:
            return {
                "status": "adopted",
                "decision": "BUY_WITH_FALLBACK",
                "actual_solution": "great_expectations + custom DQ checks",
                "notes": "GE enabled and importable.",
                "declared_in_pyproject_research": declared,
                "installed_in_env": installed,
                "enabled_by_config": enabled,
            }
        if enabled and not installed:
            return {
                "status": "partial",
                "decision": "CONFIG_ENABLED_DEP_MISSING",
                "actual_solution": component.custom_fallback,
                "notes": "GE enabled in config, but package is not installed in current env.",
                "declared_in_pyproject_research": declared,
                "installed_in_env": installed,
                "enabled_by_config": enabled,
            }
        return {
            "status": "partial",
            "decision": "BUILD_FALLBACK_ACTIVE",
            "actual_solution": component.custom_fallback,
            "notes": "Custom DQ checks active; GE optional by config.",
            "declared_in_pyproject_research": declared,
            "installed_in_env": installed,
            "enabled_by_config": enabled,
        }

    return {
        "status": "unknown",
        "decision": "UNKNOWN",
        "actual_solution": component.custom_fallback,
        "notes": "",
        "declared_in_pyproject_research": declared,
        "installed_in_env": False,
        "enabled_by_config": None,
    }


def build_stack_decision_log(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    rows = []
    for component in _COMPONENTS:
        status = _component_status(component, settings)
        rows.append(
            {
                "component": component.key,
                "area": component.area,
                "planned_tool": component.planned_tool,
                "custom_fallback": component.custom_fallback,
                "planned_build_days": component.planned_build_days,
                "planned_setup_days": component.planned_setup_days,
                "status": status["status"],
                "decision": status["decision"],
                "actual_solution": status["actual_solution"],
                "notes": status["notes"],
                "declared_in_pyproject_research": status["declared_in_pyproject_research"],
                "installed_in_env": status["installed_in_env"],
                "enabled_by_config": status["enabled_by_config"],
            }
        )
    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    return {
        "policy": "Build only business logic; buy infrastructure where possible.",
        "rows_total": len(rows),
        "status_counts": status_counts,
        "rows": rows,
    }


def build_build_vs_buy_time_saved_estimate(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    log = build_stack_decision_log(settings)
    rows = list(log["rows"])

    planned_build_days_total = sum(int(r["planned_build_days"]) for r in rows)
    planned_setup_days_total = sum(int(r["planned_setup_days"]) for r in rows)
    theoretical_days_saved = max(0, planned_build_days_total - planned_setup_days_total)

    # Realized saving grows with adoption/partial adoption.
    score_map = {"adopted": 1.0, "partial": 0.5, "pending": 0.0, "unknown": 0.0}
    adoption_weight = 0.0
    for r in rows:
        adoption_weight += score_map.get(str(r["status"]), 0.0)
    adoption_ratio = adoption_weight / max(1, len(rows))
    realized_days_saved = round(theoretical_days_saved * adoption_ratio, 2)

    return {
        "planned_build_days_total": planned_build_days_total,
        "planned_setup_days_total": planned_setup_days_total,
        "theoretical_days_saved_full_adoption": theoretical_days_saved,
        "adoption_ratio": round(adoption_ratio, 4),
        "realized_days_saved_estimate": realized_days_saved,
        "baseline_reference": {
            "setup_range_days": [3, 5],
            "build_from_scratch_range_weeks": [8, 10],
        },
        "notes": (
            "Estimate is model-based from component status (adopted/partial/pending) and "
            "should be treated as directional."
        ),
    }


def build_research_stack_readiness_report(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    log = build_stack_decision_log(settings)
    rows = list(log.get("rows") or [])

    declared_count = sum(1 for r in rows if bool(r.get("declared_in_pyproject_research")))
    installed_count = sum(1 for r in rows if bool(r.get("installed_in_env")))
    config_enabled_count = sum(1 for r in rows if bool(r.get("enabled_by_config")))

    blocking_issues: list[dict[str, str]] = []
    for row in rows:
        component = str(row.get("component") or "unknown")
        tool = str(row.get("planned_tool") or "")
        if not bool(row.get("declared_in_pyproject_research")):
            blocking_issues.append(
                {
                    "component": component,
                    "severity": "high",
                    "issue": f"{tool} is not declared in optional dependency group [research].",
                }
            )
        if bool(row.get("enabled_by_config")) and not bool(row.get("installed_in_env")):
            blocking_issues.append(
                {
                    "component": component,
                    "severity": "high",
                    "issue": f"{tool} is enabled by config but not installed in current environment.",
                }
            )

    is_baseline_ready = bool(settings.research_tracking_enabled)
    is_advanced_ready = len(rows) > 0 and all(bool(r.get("installed_in_env")) for r in rows)
    has_blocking_issues = len(blocking_issues) > 0

    next_actions: list[str] = []
    if declared_count < len(rows):
        next_actions.append("Add missing packages to pyproject optional group [research].")
    if installed_count < len(rows):
        next_actions.append("Install advanced stack packages: pip install .[research].")
    if settings.research_mlflow_enabled and not _is_package_installed("mlflow"):
        next_actions.append("Disable RESEARCH_MLFLOW_ENABLED or install mlflow in runtime environment.")
    if settings.research_great_expectations_enabled and not _is_package_installed("great_expectations"):
        next_actions.append(
            "Disable RESEARCH_GREAT_EXPECTATIONS_ENABLED or install great-expectations in runtime environment."
        )
    if not next_actions:
        next_actions.append("No action required. Baseline and advanced stack are aligned.")

    return {
        "baseline_ready": is_baseline_ready,
        "advanced_ready": is_advanced_ready,
        "has_blocking_issues": has_blocking_issues,
        "summary": {
            "components_total": len(rows),
            "declared_in_pyproject_count": declared_count,
            "installed_in_env_count": installed_count,
            "config_enabled_count": config_enabled_count,
            "research_tracking_enabled": bool(settings.research_tracking_enabled),
            "research_mlflow_enabled": bool(settings.research_mlflow_enabled),
            "research_great_expectations_enabled": bool(settings.research_great_expectations_enabled),
        },
        "blocking_issues": blocking_issues,
        "next_actions": next_actions,
        "stack_decision_log": log,
    }


def extract_build_vs_buy_metrics(report: dict[str, Any]) -> dict[str, float]:
    return {
        "build_vs_buy_planned_build_days_total": float(report.get("planned_build_days_total") or 0.0),
        "build_vs_buy_planned_setup_days_total": float(report.get("planned_setup_days_total") or 0.0),
        "build_vs_buy_theoretical_saved_days": float(report.get("theoretical_days_saved_full_adoption") or 0.0),
        "build_vs_buy_realized_saved_days": float(report.get("realized_days_saved_estimate") or 0.0),
        "build_vs_buy_adoption_ratio": float(report.get("adoption_ratio") or 0.0),
    }
