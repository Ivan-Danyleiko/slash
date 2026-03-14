from types import SimpleNamespace

from app.services.research.tracking import read_stage5_experiments, record_stage5_experiment


def test_record_and_read_stage5_experiment_local_registry(tmp_path) -> None:
    registry = tmp_path / "experiments.jsonl"
    settings = SimpleNamespace(
        research_tracking_enabled=True,
        research_experiment_registry_path=str(registry),
        research_mlflow_enabled=False,
        research_mlflow_tracking_uri="",
        research_mlflow_experiment_name="stage5_signal_quality",
    )

    recorded = record_stage5_experiment(
        run_name="test_run",
        params={"threshold": 0.1},
        metrics={"avg_return": 0.02, "hit_rate": 0.6},
        tags={"decision": "KEEP"},
        settings=settings,
    )
    assert recorded["recorded"] is True
    assert recorded["mlflow_logged"] is False

    rows = read_stage5_experiments(limit=10, settings=settings)
    assert rows["count"] == 1
    assert rows["rows"][0]["run_name"] == "test_run"


def test_record_stage5_experiment_disabled(tmp_path) -> None:
    settings = SimpleNamespace(
        research_tracking_enabled=False,
        research_experiment_registry_path=str(tmp_path / "unused.jsonl"),
        research_mlflow_enabled=False,
        research_mlflow_tracking_uri="",
        research_mlflow_experiment_name="stage5_signal_quality",
    )
    recorded = record_stage5_experiment(
        run_name="test_run_disabled",
        params={},
        metrics={},
        settings=settings,
    )
    assert recorded["recorded"] is False
