from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Signal
from app.services.research.final_report import build_stage5_final_report
from app.services.research.walkforward import build_walkforward_report


def _rollout_decision(*, keep_types: int, best_ev: float, sharpe: float, risk_of_ruin: float, walkforward_consistent: bool) -> str:
    if keep_types >= 2 and best_ev > 0.02 and sharpe > 1.0 and risk_of_ruin < 0.10 and walkforward_consistent:
        return "GO"
    if keep_types >= 1 and best_ev > 0.01 and sharpe > 0.5 and risk_of_ruin < 0.15:
        return "LIMITED_GO"
    return "NO_GO"


def _overfit_flags(*, ev_backtest: float, hit_rate: float, sharpe: float, samples: int) -> list[str]:
    flags: list[str] = []
    if ev_backtest > 0.15:
        flags.append("ev_backtest_above_15pct")
    if hit_rate > 0.63:
        flags.append("hit_rate_above_63pct")
    if sharpe > 2.5 and samples < 500:
        flags.append("sharpe_above_2_5_with_small_sample")
    return flags


def _avg_executable_signals_per_day(db: Session, *, days: int) -> float:
    days = max(1, int(days))
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = list(db.scalars(select(Signal).where(Signal.created_at >= cutoff)))
    executable = 0
    for row in rows:
        ex = row.execution_analysis or {}
        edge_after_costs = ex.get("expected_ev_after_costs_pct")
        if not isinstance(edge_after_costs, (int, float)):
            edge_after_costs = ex.get("slippage_adjusted_edge")
        if isinstance(edge_after_costs, (int, float)) and float(edge_after_costs) > 0:
            executable += 1
    return executable / days


def build_stage6_governance_report(
    db: Session,
    *,
    days: int = 30,
    horizon: str = "6h",
    min_labeled_returns: int = 30,
    walkforward_days: int = 90,
    walkforward_train_days: int = 30,
    walkforward_test_days: int = 14,
    walkforward_step_days: int = 14,
    walkforward_embargo_hours: int = 24,
    walkforward_min_samples: int = 100,
) -> dict[str, Any]:
    final_report = build_stage5_final_report(
        db,
        days=days,
        horizon=horizon,
        min_labeled_returns=min_labeled_returns,
    )
    effective_rows = list(((final_report.get("sections") or {}).get("signal_types_effective") or {}).get("rows") or [])
    keep_rows = [r for r in effective_rows if str(r.get("decision") or "") == "KEEP"]
    active_rows = [r for r in effective_rows if str(r.get("decision") or "") in {"KEEP", "MODIFY"}]

    keep_types = len(keep_rows)
    best_ev = max((float(r.get("avg_return") or 0.0) for r in keep_rows), default=0.0)
    active_sharpes = [float(r.get("sharpe_like") or 0.0) for r in active_rows]
    sharpe_like_portfolio = (sum(active_sharpes) / len(active_sharpes)) if active_sharpes else 0.0
    active_risk = [float(r.get("risk_of_ruin") or 1.0) for r in active_rows]
    risk_of_ruin_portfolio = max(active_risk) if active_risk else 1.0

    walk = build_walkforward_report(
        db,
        days=walkforward_days,
        horizon=horizon,
        signal_type=None,
        train_days=walkforward_train_days,
        test_days=walkforward_test_days,
        step_days=walkforward_step_days,
        embargo_hours=walkforward_embargo_hours,
        min_samples_per_window=walkforward_min_samples,
    )
    walk_rows = list(walk.get("rows") or [])
    walkforward_low_conf_types = [r for r in walk_rows if bool(r.get("low_confidence"))]
    walkforward_consistent = bool(walk_rows) and len(walkforward_low_conf_types) == 0

    executable_per_day = _avg_executable_signals_per_day(db, days=days)

    samples = sum(int(r.get("returns_labeled") or 0) for r in active_rows)
    hit_rates = [float(r.get("hit_rate") or 0.0) for r in active_rows]
    hit_rate_mean = (sum(hit_rates) / len(hit_rates)) if hit_rates else 0.0
    avg_return_all = [float(r.get("avg_return") or 0.0) for r in active_rows]
    ev_backtest = (sum(avg_return_all) / len(avg_return_all)) if avg_return_all else 0.0
    overfit = _overfit_flags(
        ev_backtest=ev_backtest,
        hit_rate=hit_rate_mean,
        sharpe=sharpe_like_portfolio,
        samples=samples,
    )

    decision = _rollout_decision(
        keep_types=keep_types,
        best_ev=best_ev,
        sharpe=sharpe_like_portfolio,
        risk_of_ruin=risk_of_ruin_portfolio,
        walkforward_consistent=walkforward_consistent,
    )

    checks = {
        "keep_types_gte_2": keep_types >= 2,
        "best_ev_gt_2pct": best_ev > 0.02,
        "portfolio_sharpe_gt_1": sharpe_like_portfolio > 1.0,
        "portfolio_ror_lt_10pct": risk_of_ruin_portfolio < 0.10,
        "executable_signals_per_day_gte_5": executable_per_day >= 5.0,
        "walkforward_consistent": walkforward_consistent,
        "no_overfit_flags": len(overfit) == 0,
    }

    limited_go_reasons: list[str] = []
    if decision == "LIMITED_GO":
        limited_go_reasons.append("Only partial business criteria reached; rollout must stay <=20% traffic")
    if overfit:
        limited_go_reasons.append("Overfit sanity flags present; manual review required")

    return {
        "period_days": days,
        "horizon": horizon,
        "decision": decision,
        "checks": checks,
        "summary": {
            "keep_types": keep_types,
            "active_types": len(active_rows),
            "best_keep_ev": round(best_ev, 6),
            "portfolio_sharpe_like": round(sharpe_like_portfolio, 6),
            "portfolio_risk_of_ruin": round(risk_of_ruin_portfolio, 6),
            "executable_signals_per_day": round(executable_per_day, 6),
            "walkforward_types": len(walk_rows),
            "walkforward_low_conf_types": len(walkforward_low_conf_types),
            "returns_labeled_active": samples,
            "hit_rate_mean_active": round(hit_rate_mean, 6),
            "ev_backtest_mean_active": round(ev_backtest, 6),
        },
        "overfit_flags": overfit,
        "limited_go_reasons": limited_go_reasons,
        "artifacts": {
            "final_report": final_report,
            "walkforward": walk,
        },
    }


def extract_stage6_governance_metrics(report: dict[str, Any]) -> dict[str, float]:
    summary = report.get("summary") or {}
    decision = str(report.get("decision") or "")
    decision_score = 0.0
    if decision == "GO":
        decision_score = 1.0
    elif decision == "LIMITED_GO":
        decision_score = 0.5
    return {
        "stage6_decision_score": decision_score,
        "stage6_keep_types": float(summary.get("keep_types") or 0.0),
        "stage6_best_keep_ev": float(summary.get("best_keep_ev") or 0.0),
        "stage6_portfolio_sharpe_like": float(summary.get("portfolio_sharpe_like") or 0.0),
        "stage6_portfolio_risk_of_ruin": float(summary.get("portfolio_risk_of_ruin") or 0.0),
        "stage6_executable_signals_per_day": float(summary.get("executable_signals_per_day") or 0.0),
        "stage6_overfit_flags": float(len(report.get("overfit_flags") or [])),
    }
