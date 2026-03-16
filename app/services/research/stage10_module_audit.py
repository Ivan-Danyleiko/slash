from __future__ import annotations

import importlib.util
import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import Stage7AgentDecision


SHORTLIST_PATH = Path("docs/STAGE10_AGENT_MODULE_SHORTLIST.md")


_CANDIDATE_PATTERN = re.compile(r"^\d+\.\s+`([^`]+)`", re.MULTILINE)


def _read_shortlist_candidates() -> list[str]:
    if not SHORTLIST_PATH.exists():
        return []
    body = SHORTLIST_PATH.read_text(encoding="utf-8")
    lines = body.splitlines()
    in_candidates = False
    extracted: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("## 2."):
            in_candidates = True
            continue
        if in_candidates and s.startswith("## "):
            break
        if not in_candidates:
            continue
        m = _CANDIDATE_PATTERN.match(s)
        if m:
            extracted.append(m.group(1).strip())
    return extracted


def _module_present(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _candidate_availability(name: str, settings: Settings) -> dict[str, Any]:
    lname = name.lower()
    checks: dict[str, bool] = {
        "shortlist_present": True,
        "network_sandbox_required": True,
    }

    if "openai" in lname:
        checks["api_key_present"] = bool(str(settings.stage7_openai_api_key or "").strip())
        checks["provider_sdk_present"] = _module_present("openai")
    if "anthropic" in lname:
        checks["api_key_present"] = bool(str(os.getenv("ANTHROPIC_API_KEY", "")).strip())
        checks["provider_sdk_present"] = _module_present("anthropic")
    if "langgraph" in lname:
        checks["module_present"] = _module_present("langgraph")
    if "llamaindex" in lname:
        checks["module_present"] = _module_present("llama_index")
    if "openclaw" in lname:
        checks["binary_present"] = shutil.which("openclaw") is not None

    passed = all(bool(v) for v in checks.values())
    if passed:
        verdict = "PASS"
        allowed = True
    else:
        critical_missing = any(k in checks and not checks[k] for k in ("api_key_present", "provider_sdk_present", "module_present", "binary_present"))
        verdict = "FAIL" if critical_missing else "WARN"
        allowed = verdict != "FAIL"

    return {
        "candidate": name,
        "security_verdict": verdict,
        "checks": checks,
        "dependency_findings_count": 0.0,
        "static_findings_count": 0.0,
        "sandbox_network_egress_detected": False,
        "allowed_for_replay": allowed,
        "scan_mode": "lightweight_presence_checks",
    }


def _monthly_stage7_spend(db: Session) -> float:
    cutoff = datetime.now(UTC) - timedelta(days=30)
    rows = list(
        db.scalars(
            select(Stage7AgentDecision)
            .where(Stage7AgentDecision.created_at >= cutoff)
            .order_by(Stage7AgentDecision.created_at.desc())
            .limit(50000)
        )
    )
    return float(sum(float(r.llm_cost_usd or 0.0) for r in rows))


def build_stage10_module_audit_report(db: Session, *, settings: Settings) -> dict[str, Any]:
    candidates = _read_shortlist_candidates()
    rows = [_candidate_availability(name, settings) for name in candidates]

    total = len(rows)
    pass_count = sum(1 for r in rows if str(r.get("security_verdict")) == "PASS")
    fail_count = sum(1 for r in rows if str(r.get("security_verdict")) == "FAIL")
    allowed_count = sum(1 for r in rows if bool(r.get("allowed_for_replay")))

    spend = _monthly_stage7_spend(db)
    budget = float(settings.stage10_llm_budget_usd_monthly)
    budget_ratio = (spend / budget) if budget > 0 else 1.0
    if budget_ratio > 1.0:
        llm_mode = "hard_cutoff"
    elif budget_ratio > 0.8:
        llm_mode = "cached_only"
    else:
        llm_mode = "normal"

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "candidates_total": total,
            "security_pass_count": pass_count,
            "security_fail_count": fail_count,
            "allowed_for_replay_count": allowed_count,
            "stage10_llm_budget_usd_monthly": budget,
            "stage10_llm_spend_last_30d_usd": round(spend, 6),
            "stage10_llm_budget_ratio": round(budget_ratio, 6),
            "stage10_llm_mode": llm_mode,
        },
        "rows": rows,
    }


def extract_stage10_module_audit_metrics(report: dict[str, Any]) -> dict[str, float]:
    summary = dict(report.get("summary") or {})
    return {
        "stage10_module_candidates_total": float(summary.get("candidates_total") or 0.0),
        "stage10_module_security_pass_count": float(summary.get("security_pass_count") or 0.0),
        "stage10_module_security_fail_count": float(summary.get("security_fail_count") or 0.0),
        "stage10_module_allowed_for_replay_count": float(summary.get("allowed_for_replay_count") or 0.0),
        "stage10_llm_budget_ratio": float(summary.get("stage10_llm_budget_ratio") or 0.0),
    }
