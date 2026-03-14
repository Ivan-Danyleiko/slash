from app.services.research import stage6_final_report as s6f


def test_stage6_final_report_decision_resolver() -> None:
    assert s6f._resolve_final_decision(governance_decision="GO", guardrail_level="OK", rollback_triggered=False) == "GO"
    assert (
        s6f._resolve_final_decision(governance_decision="LIMITED_GO", guardrail_level="HARD", rollback_triggered=False)
        == "LIMITED_GO"
    )
    assert s6f._resolve_final_decision(governance_decision="GO", guardrail_level="PANIC", rollback_triggered=False) == "NO_GO"
    assert s6f._resolve_final_decision(governance_decision="GO", guardrail_level="OK", rollback_triggered=True) == "NO_GO"


def test_stage6_final_report_build_with_monkeypatched_sections(monkeypatch) -> None:
    def fake_governance(*args, **kwargs):
        return {"decision": "GO", "summary": {"keep_types": 2, "executable_signals_per_day": 6.0}}

    def fake_guardrails(*args, **kwargs):
        return {"circuit_breaker_level": "SOFT", "rollback": {"triggered": False}}

    def fake_type35(*args, **kwargs):
        return {"decision_counts": {"KEEP": 1, "INSUFFICIENT_ARCHITECTURE": 1}}

    monkeypatch.setattr(s6f, "build_stage6_governance_report", fake_governance)
    monkeypatch.setattr(s6f, "build_stage6_risk_guardrails_report", fake_guardrails)
    monkeypatch.setattr(s6f, "build_stage6_type35_report", fake_type35)

    report = s6f.build_stage6_final_report(db=None, days=30, horizon="6h", min_labeled_returns=30)  # type: ignore[arg-type]
    assert report["final_decision"] == "GO"
    assert report["recommended_action"] == "proceed_rollout"
    assert report["summary"]["keep_types"] == 2
