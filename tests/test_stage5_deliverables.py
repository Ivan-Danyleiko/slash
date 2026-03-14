from types import SimpleNamespace

from app.services.research.deliverables import (
    build_build_vs_buy_time_saved_estimate,
    build_research_stack_readiness_report,
    build_stack_decision_log,
)


def _settings(*, mlflow: bool = False, ge: bool = False):
    return SimpleNamespace(
        research_tracking_enabled=True,
        research_mlflow_enabled=mlflow,
        research_great_expectations_enabled=ge,
    )


def test_stack_decision_log_has_required_components() -> None:
    report = build_stack_decision_log(settings=_settings(mlflow=False, ge=False))
    assert report["rows_total"] >= 5
    components = {row["component"] for row in report["rows"]}
    assert "experiment_tracking" in components
    assert "data_quality" in components
    assert "backtesting_engine" in components


def test_build_vs_buy_estimate_has_non_negative_savings() -> None:
    report = build_build_vs_buy_time_saved_estimate(settings=_settings(mlflow=False, ge=False))
    assert report["planned_build_days_total"] > 0
    assert report["planned_setup_days_total"] > 0
    assert report["theoretical_days_saved_full_adoption"] >= 0
    assert 0.0 <= report["adoption_ratio"] <= 1.0
    assert report["realized_days_saved_estimate"] >= 0


def test_stack_readiness_report_has_expected_shape() -> None:
    report = build_research_stack_readiness_report(settings=_settings(mlflow=False, ge=False))
    assert "baseline_ready" in report
    assert "advanced_ready" in report
    assert "summary" in report
    assert "blocking_issues" in report
    assert "next_actions" in report
    assert isinstance(report["blocking_issues"], list)
    assert isinstance(report["next_actions"], list)


def test_stack_readiness_flags_missing_enabled_dependency(monkeypatch) -> None:
    from app.services.research import deliverables as module

    def fake_installed(name: str) -> bool:
        return name != "mlflow"

    monkeypatch.setattr(module, "_is_package_installed", fake_installed)
    monkeypatch.setattr(module, "_is_optional_dependency_declared", lambda _pkg, group="research": True)

    report = module.build_research_stack_readiness_report(settings=_settings(mlflow=True, ge=False))
    messages = [item["issue"] for item in report["blocking_issues"]]
    assert any("enabled by config but not installed" in msg for msg in messages)
    assert report["has_blocking_issues"] is True
