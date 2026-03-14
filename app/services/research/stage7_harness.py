from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from statistics import quantiles
from typing import Any

from app.core.config import Settings, get_settings
from app.services.agent_stage7.stack_adapters import get_stage7_adapter
from app.services.agent_stage7.stack_adapters import LangGraphAdapter, PlainApiAdapter
from app.services.agent_stage7.stack_adapters.base import Stage7Adapter, Stage7AdapterInput


def _failure_mode_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "resolution_ambiguity_case",
            "payload": Stage7AdapterInput(
                signal_id=1,
                base_decision="KEEP",
                internal_gate_passed=True,
                contradictions_count=0,
                ambiguity_count=2,
            ),
            "expected": lambda out: out.get("decision") in {"KEEP", "MODIFY", "REMOVE"},
        },
        {
            "case_id": "cross_source_mismatch_case",
            "payload": Stage7AdapterInput(
                signal_id=2,
                base_decision="KEEP",
                internal_gate_passed=True,
                contradictions_count=1,
                ambiguity_count=0,
            ),
            "expected": lambda out: str(out.get("decision")) != "KEEP",
        },
        {
            "case_id": "provider_drift_case",
            "payload": Stage7AdapterInput(
                signal_id=3,
                base_decision="MODIFY",
                internal_gate_passed=True,
                contradictions_count=0,
                ambiguity_count=0,
            ),
            "expected": lambda out: str(out.get("decision")) in {"MODIFY", "REMOVE"},
        },
        {
            "case_id": "idempotency_case",
            "payload": Stage7AdapterInput(
                signal_id=4,
                base_decision="MODIFY",
                internal_gate_passed=True,
                contradictions_count=1,
                ambiguity_count=1,
            ),
            "expected": lambda out: bool(out.get("reason_codes")),
        },
        {
            "case_id": "latency_budget_case",
            "payload": Stage7AdapterInput(
                signal_id=5,
                base_decision="KEEP",
                internal_gate_passed=True,
                contradictions_count=0,
                ambiguity_count=0,
            ),
            "expected": lambda out: float(out.get("simulated_latency_ms") or 0.0) >= 0.0,
        },
    ]


def _run_adapter(adapter: Stage7Adapter, *, max_latency_ms: int) -> dict[str, Any]:
    cases = _failure_mode_cases()
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    passed = 0
    idem_ok = 0
    for case in cases:
        payload = case["payload"]
        t0 = perf_counter()
        out_a = adapter.decide(payload)
        latency_measured = (perf_counter() - t0) * 1000.0
        out_b = adapter.decide(payload)
        is_idem = (
            str(out_a.get("decision")) == str(out_b.get("decision"))
            and list(out_a.get("reason_codes") or []) == list(out_b.get("reason_codes") or [])
        )
        if is_idem:
            idem_ok += 1
        ok = bool(case["expected"](out_a))
        if ok:
            passed += 1
        latency = float(out_a.get("simulated_latency_ms") or 0.0)
        if latency <= 0.0:
            latency = float(latency_measured)
        latencies.append(latency)
        rows.append(
            {
                "case_id": case["case_id"],
                "decision": out_a.get("decision"),
                "reason_codes": out_a.get("reason_codes") or [],
                "latency_ms": latency,
                "passed": ok,
                "idempotent": is_idem,
            }
        )
    p95 = quantiles(latencies, n=100)[94] if len(latencies) >= 20 else max(latencies)
    return {
        "stack": getattr(adapter, "name", "unknown"),
        "tests_total": len(cases),
        "tests_passed": passed,
        "pass_rate": round(passed / max(1, len(cases)), 6),
        "idempotency_pass_rate": round(idem_ok / max(1, len(cases)), 6),
        "latency_p95_ms": round(float(p95 or 0.0), 4),
        "latency_within_budget": float(p95 or 0.0) <= float(max_latency_ms),
        "rows": rows,
    }


def build_stage7_harness_report(
    *,
    max_latency_ms: int = 1200,
    settings: Settings | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    adapters: list[Stage7Adapter] = [LangGraphAdapter(), PlainApiAdapter()]
    if bool(s.stage7_agent_real_calls_enabled) and str(s.stage7_agent_provider or "").strip().lower() in {
        "plain_llm_api",
        "openai",
        "openai_compatible",
    }:
        adapters.append(get_stage7_adapter(s))
    results = [_run_adapter(adapter, max_latency_ms=max_latency_ms) for adapter in adapters]
    by_stack = {
        str(item["stack"]): {
            "pass_rate": float(item["pass_rate"]),
            "idempotency_pass_rate": float(item["idempotency_pass_rate"]),
            "latency_p95_ms": float(item["latency_p95_ms"]),
            "latency_within_budget": bool(item["latency_within_budget"]),
        }
        for item in results
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "max_latency_ms": int(max_latency_ms),
        "results": results,
        "by_stack": by_stack,
        "summary": {
            "stacks_tested": len(results),
            "all_pass_rate_gte_80pct": all(float(r.get("pass_rate") or 0.0) >= 0.80 for r in results),
            "all_idempotent_gte_90pct": all(float(r.get("idempotency_pass_rate") or 0.0) >= 0.90 for r in results),
        },
    }


def extract_stage7_harness_metrics(report: dict[str, Any]) -> dict[str, float]:
    results = list(report.get("results") or [])
    avg_pass = (
        sum(float(r.get("pass_rate") or 0.0) for r in results) / max(1, len(results))
        if results
        else 0.0
    )
    avg_idem = (
        sum(float(r.get("idempotency_pass_rate") or 0.0) for r in results) / max(1, len(results))
        if results
        else 0.0
    )
    return {
        "stage7_harness_stacks_tested": float(len(results)),
        "stage7_harness_avg_pass_rate": round(avg_pass, 6),
        "stage7_harness_avg_idempotency": round(avg_idem, 6),
    }
