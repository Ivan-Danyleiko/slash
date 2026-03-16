from __future__ import annotations

from datetime import UTC, datetime, timedelta
import random
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.models import Market, Platform, Signal, SignalHistory, Stage7AgentDecision
from app.services.agent_stage8 import (
    classify_market_category,
    evaluate_internal_gate_v2,
    evaluate_rules_fields,
    get_category_policy,
    get_category_policy_profile,
    resolve_stage8_decision,
    route_external_context,
    save_stage8_decision,
)
from app.services.agent_stage8.store import load_stage8_today_map
from app.services.research.walkforward import build_walkforward_report

_DATA_SUFFICIENCY_CACHE_TTL_SECONDS = 300
_DATA_SUFFICIENCY_CACHE: dict[str, tuple[datetime, dict[str, Any]]] = {}


def _latest_stage7_decision(db: Session, signal_id: int) -> Stage7AgentDecision | None:
    return db.scalar(
        select(Stage7AgentDecision)
        .where(Stage7AgentDecision.signal_id == signal_id)
        .order_by(Stage7AgentDecision.id.desc())
        .limit(1)
    )


def _latest_stage7_decision_map(db: Session, signal_ids: list[int]) -> dict[int, Stage7AgentDecision]:
    if not signal_ids:
        return {}
    latest_ids = (
        select(Stage7AgentDecision.signal_id, func.max(Stage7AgentDecision.id).label("max_id"))
        .where(Stage7AgentDecision.signal_id.in_(signal_ids))
        .group_by(Stage7AgentDecision.signal_id)
        .subquery()
    )
    rows = list(
        db.scalars(
            select(Stage7AgentDecision)
            .join(latest_ids, Stage7AgentDecision.id == latest_ids.c.max_id)
        )
    )
    return {int(r.signal_id): r for r in rows}


def _resolved_success_map(db: Session, signal_ids: list[int]) -> dict[int, bool]:
    if not signal_ids:
        return {}
    rows = list(
        db.execute(
            select(SignalHistory.signal_id, SignalHistory.resolved_success)
            .where(SignalHistory.signal_id.in_(signal_ids))
            .where(SignalHistory.resolved_success.is_not(None))
            .order_by(SignalHistory.timestamp.desc())
        )
    )
    out: dict[int, bool] = {}
    for sid, success in rows:
        signal_id = int(sid or 0)
        if signal_id and signal_id not in out:
            out[signal_id] = bool(success)
    return out


def _data_sufficiency_snapshot(db: Session) -> dict[str, Any]:
    bind = db.get_bind()
    url = bind.url
    safe_url = url.render_as_string(hide_password=True) if hasattr(url, "render_as_string") else str(url)
    cache_key = f"{id(bind)}::{safe_url}"
    cached = _DATA_SUFFICIENCY_CACHE.get(cache_key)
    now = datetime.now(UTC)
    if cached:
        ts, payload = cached
        if (now - ts).total_seconds() <= _DATA_SUFFICIENCY_CACHE_TTL_SECONDS:
            return payload

    resolved_rows_total = int(
        db.scalar(select(func.count()).select_from(SignalHistory).where(SignalHistory.resolved_success.is_not(None)))
        or 0
    )
    keep_signal_ids = list(
        db.scalars(
            select(Stage7AgentDecision.signal_id)
            .where(Stage7AgentDecision.decision == "KEEP")
            .distinct()
        )
    )
    keep_signal_ids = [int(v) for v in keep_signal_ids if v is not None]
    keeps_with_resolution = int(
        db.scalar(
            select(func.count(func.distinct(SignalHistory.signal_id)))
            .where(SignalHistory.signal_id.in_(keep_signal_ids))
            .where(SignalHistory.resolved_success.is_not(None))
        )
        or 0
    )
    walkforward = build_walkforward_report(
        db,
        days=90,
        horizon="6h",
        train_days=30,
        test_days=14,
        step_days=14,
        embargo_hours=24,
        min_samples_per_window=10,
        bootstrap_sims=500,
    )
    walkforward_windows_total = int(len(walkforward.get("windows") or []))
    sufficient = (
        resolved_rows_total >= 30
        and keeps_with_resolution >= 10
        and walkforward_windows_total >= 3
    )
    out = {
        "resolved_rows_total": resolved_rows_total,
        "keeps_with_resolution": keeps_with_resolution,
        "walkforward_windows_total": walkforward_windows_total,
        "data_sufficient_for_acceptance": sufficient,
        "walkforward_report": walkforward,
    }
    _DATA_SUFFICIENCY_CACHE[cache_key] = (now, out)
    return out


def _decision_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts = {"KEEP": 0, "MODIFY": 0, "REMOVE": 0, "SKIP": 0}
    for row in rows:
        value = str(row.get(key) or "SKIP").upper()
        if value not in counts:
            value = "SKIP"
        counts[value] += 1
    return counts


def _execution_action_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"EXECUTE_ALLOWED": 0, "SHADOW_ONLY": 0, "BLOCK": 0}
    for row in rows:
        value = str(row.get("execution_action") or "BLOCK").upper()
        if value not in counts:
            value = "BLOCK"
        counts[value] += 1
    return counts


def _kelly_fraction(edge_after_costs: float, market_prob: float) -> float:
    p = min(0.99, max(0.01, market_prob))
    raw = edge_after_costs / (p * (1.0 - p))
    return max(0.0, min(0.25, raw))


def _bootstrap_ci(
    values: list[float],
    *,
    n_sims: int = 500,
    conf_level: float = 0.80,
    seed: int = 42,
) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    sims = max(100, min(int(n_sims), 5000))
    conf = min(0.99, max(0.50, float(conf_level)))
    alpha = 1.0 - conf
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(sims):
        sample = [values[rng.randrange(0, n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo_idx = int((alpha / 2.0) * (len(means) - 1))
    hi_idx = int((1.0 - (alpha / 2.0)) * (len(means) - 1))
    return float(means[lo_idx]), float(means[hi_idx])


def _scenario_sweeps(rows: list[dict[str, Any]], resolved_map: dict[int, bool]) -> dict[str, Any]:
    position_sizes = [50.0, 100.0, 500.0]
    spreads = [0.01, 0.03, 0.05]
    fees = [0.02, 0.025]
    scenarios: list[dict[str, Any]] = []
    positive = 0
    keep_rows = [r for r in rows if str(r.get("decision") or "") == "KEEP"]
    for size in position_sizes:
        for spread in spreads:
            for fee in fees:
                size_penalty = 0.002 if size == 50.0 else (0.004 if size == 100.0 else 0.01)
                stress_costs = spread + fee + size_penalty
                vals: list[float] = []
                for row in keep_rows:
                    edge = float(row.get("edge_after_costs") or 0.0)
                    sid = int(row.get("signal_id") or 0)
                    if sid in resolved_map:
                        realized = edge if bool(resolved_map[sid]) else -abs(edge)
                    else:
                        realized = edge
                    vals.append(realized - stress_costs)
                mean_ret = (sum(vals) / len(vals)) if vals else -stress_costs
                ok = mean_ret > 0.0
                if ok:
                    positive += 1
                scenarios.append(
                    {
                        "position_size_usd": size,
                        "spread": spread,
                        "fee": fee,
                        "mean_post_cost_return": round(float(mean_ret), 6),
                        "positive": ok,
                    }
                )
    realized_keep_rows = len([r for r in keep_rows if int(r.get("signal_id") or 0) in resolved_map])
    evaluated_keep_rows = len(keep_rows)
    realized_sample_share = (realized_keep_rows / evaluated_keep_rows) if evaluated_keep_rows else 0.0
    return {
        "required_positive": 12,
        "positive_scenarios": positive,
        "total_scenarios": len(scenarios),
        "evaluated_keep_rows": evaluated_keep_rows,
        "realized_keep_rows": realized_keep_rows,
        "realized_sample_share": round(realized_sample_share, 6),
        "passes_12_of_18": positive >= 12,
        "rows": scenarios,
    }


def _age_minutes(ts: datetime | None) -> float:
    if not ts:
        return 1_000_000.0
    value = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - value).total_seconds() / 60.0)


def _soft_gate_reason_codes(
    *,
    signal: Signal,
    market: Market,
    category_policy: dict[str, float],
    contradiction: float,
) -> list[str]:
    reasons: list[str] = []
    freshness = _age_minutes(market.fetched_at)
    freshness_limit = float(category_policy.get("min_freshness_minutes", 0.0))
    if (freshness_limit * 0.80) < freshness <= freshness_limit:
        reasons.append("staleness_check_failed")
    signal_age = _age_minutes(signal.created_at)
    if signal_age > max(60.0, float(category_policy.get("min_freshness_minutes", 0.0)) * 4.0):
        reasons.append("signal_age_too_old")
    if market.resolution_time:
        rt = market.resolution_time if market.resolution_time.tzinfo else market.resolution_time.replace(tzinfo=UTC)
        ttr_hours = max(0.0, (rt - datetime.now(UTC)).total_seconds() / 3600.0)
        min_ttr = float(category_policy.get("min_ttr_hours", 0.0))
        if min_ttr <= ttr_hours < (min_ttr * 2.0):
            reasons.append("event_proximity_risk")
    if contradiction > (float(category_policy.get("max_cross_platform_contradiction", 1.0)) * 0.70):
        reasons.append("correlation_budget_exceeded")
    return reasons


def _warning_gate_reason_codes(
    *,
    market: Market,
    rules_ambiguity_score: float,
    category_policy: dict[str, float],
    evidence_bundle: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    ambiguity_limit = float(category_policy.get("max_rules_ambiguity_score", 1.0))
    if (ambiguity_limit * 0.70) <= rules_ambiguity_score < ambiguity_limit:
        reasons.append("ambiguity_score_moderate")
    consensus = dict((evidence_bundle or {}).get("external_consensus") or {})
    has_non_meta = any(
        consensus.get(k) is not None
        for k in ("polymarket_prob", "manifold_prob")
    )
    if has_non_meta and consensus.get("metaculus_median") is None:
        reasons.append("no_metaculus_equivalent")
    if max(float(market.liquidity_value or 0.0), float(market.volume_24h or 0.0)) < float(
        category_policy.get("min_liquidity_usd", 0.0)
    ) * 1.20:
        reasons.append("thin_crowd")
    payload = market.source_payload or {}
    creator_age_days = payload.get("creator_age_days")
    creator_markets = payload.get("creator_markets_count")
    if isinstance(creator_age_days, (int, float)) and float(creator_age_days) < 30:
        reasons.append("market_new_creator")
    elif isinstance(creator_markets, (int, float)) and float(creator_markets) < 5:
        reasons.append("market_new_creator")
    return reasons


def _precision_false_keep(rows: list[dict[str, Any]], resolved: dict[int, bool]) -> tuple[float, float]:
    keep_ids = [int(r.get("signal_id") or 0) for r in rows if str(r.get("decision") or "") == "KEEP"]
    keep_ids = [sid for sid in keep_ids if sid in resolved]
    if not keep_ids:
        return (0.0, 0.0)
    correct = sum(1 for sid in keep_ids if bool(resolved.get(sid)))
    precision = correct / len(keep_ids)
    return (round(precision, 6), round(1.0 - precision, 6))


def build_stage8_shadow_ledger_report(
    db: Session,
    *,
    settings: Settings,
    lookback_days: int = 14,
    limit: int = 300,
) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(lookback_days)))
    signal_rows = list(
        db.execute(
            select(Signal, Market, Platform)
            .join(Market, Signal.market_id == Market.id)
            .join(Platform, Market.platform_id == Platform.id)
            .where(Signal.created_at >= cutoff)
            .order_by(Signal.created_at.desc())
            .limit(limit)
        )
    )
    profile_key, profile = get_category_policy_profile(settings.stage8_policy_profile)
    sufficiency = _data_sufficiency_snapshot(db)
    data_sufficient = bool(sufficiency["data_sufficient_for_acceptance"])
    signal_ids = [int(s.id) for s, _, _ in signal_rows]
    stage7_map = _latest_stage7_decision_map(db, signal_ids)
    today_stage8_map = load_stage8_today_map(
        db, signal_ids=signal_ids, policy_version=settings.stage8_policy_version
    )

    rows: list[dict[str, Any]] = []
    missing_stage7 = 0

    for signal, market, platform in signal_rows:

        stage7 = stage7_map.get(int(signal.id))
        if not stage7:
            missing_stage7 += 1
            base_decision = "SKIP"
            evidence_bundle: dict[str, Any] = {}
        else:
            base_decision = stage7.decision
            evidence_bundle = dict(stage7.evidence_bundle or {})

        category_result = classify_market_category(
            market,
            confidence_floor=float(settings.stage8_category_confidence_floor),
        )
        category_policy = get_category_policy(category_result.category, profile)

        internal_gate = evaluate_internal_gate_v2(
            signal=signal,
            market=market,
            category_policy=category_policy,
        )
        rules_result = evaluate_rules_fields(
            market,
            platform=platform,
            category_policy=category_policy,
        )
        context_result = route_external_context(
            evidence_bundle,
            max_contradiction=float(category_policy.get("max_cross_platform_contradiction", 1.0)),
        )

        reason_codes: list[str] = []
        reason_codes.extend(category_result.reason_codes)
        reason_codes.extend(internal_gate.reason_codes)
        reason_codes.extend(rules_result.reason_codes)
        reason_codes.extend(context_result.reason_codes)

        soft_reasons = _soft_gate_reason_codes(
            signal=signal,
            market=market,
            category_policy=category_policy,
            contradiction=context_result.contradiction,
        )
        warning_reasons = _warning_gate_reason_codes(
            market=market,
            rules_ambiguity_score=rules_result.ambiguity_score,
            category_policy=category_policy,
            evidence_bundle=evidence_bundle,
        )
        reason_codes.extend(soft_reasons)
        reason_codes.extend(warning_reasons)

        hard_block = (not internal_gate.passed) or rules_result.dispute_risk_flag or (not data_sufficient)
        if not data_sufficient:
            reason_codes.append("data_insufficient_for_acceptance")
        external_consensus_insufficient = "external_consensus_insufficient" in context_result.reason_codes
        require_external_consensus = bool(category_policy.get("require_external_consensus", False))
        if external_consensus_insufficient and not require_external_consensus:
            reason_codes.append("external_consensus_single_source_allowed")
        soft_block = bool(soft_reasons) or ("cross_platform_contradiction_high" in context_result.reason_codes) or (
            external_consensus_insufficient and require_external_consensus
        )

        decision = resolve_stage8_decision(
            base_decision=str(base_decision or "SKIP"),
            hard_block=hard_block,
            soft_block=soft_block,
            reason_codes=reason_codes,
        )

        market_prob = float(market.probability_yes or 0.5)
        kelly = _kelly_fraction(internal_gate.edge_after_costs, market_prob)
        payload = {
            "signal_id": signal.id,
            "stage7_decision_id": stage7.id if stage7 else None,
            "category": category_result.category,
            "category_confidence": category_result.confidence,
            "policy_version": settings.stage8_policy_version,
            "rules_ambiguity_score": rules_result.ambiguity_score,
            "resolution_source_confidence": rules_result.resolution_source_confidence,
            "dispute_risk_flag": rules_result.dispute_risk_flag,
            "edge_after_costs": internal_gate.edge_after_costs,
            "base_decision": base_decision,
            "decision": decision.decision,
            "execution_action": decision.execution_action,
            "reason_codes": decision.reason_codes,
            "hard_block_reason": decision.hard_block_reason,
            "evidence_bundle": evidence_bundle,
            "kelly_fraction": round(kelly, 6),
            "pnl_proxy_usd_100": round(float(internal_gate.edge_after_costs) * 100.0, 6),
        }
        save_stage8_decision(db, payload=payload, existing_row=today_stage8_map.get(signal.id))
        rows.append(payload)

    db.commit()
    stage8_coverage = (len(rows) / len(signal_rows)) if signal_rows else 0.0
    stage7_coverage = ((len(rows) - missing_stage7) / len(signal_rows)) if signal_rows else 0.0
    signal_ids_for_resolved = [int(r.get("signal_id") or 0) for r in rows if int(r.get("signal_id") or 0) > 0]
    resolved = _resolved_success_map(db, signal_ids_for_resolved)
    precision_at_keep, false_keep_rate = _precision_false_keep(rows, resolved)

    resolved_returns: list[float] = []
    for row in rows:
        sid = int(row.get("signal_id") or 0)
        if sid in resolved and str(row.get("decision") or "") == "KEEP":
            edge = float(row.get("edge_after_costs") or 0.0)
            resolved_returns.append(edge if bool(resolved.get(sid)) else -abs(edge))
    ci_low, ci_high = _bootstrap_ci(resolved_returns, n_sims=500, conf_level=0.80, seed=42)
    sweeps = _scenario_sweeps(rows, resolved)
    walkforward = sufficiency.get("walkforward_report") or {}
    wf_negative_share = float((walkforward.get("summary") or {}).get("negative_window_share") or 1.0)
    wf_ok = wf_negative_share <= 0.30

    per_category = _per_category_metrics(rows, resolved)
    metrics = {
        "precision_at_keep": precision_at_keep,
        "false_keep_rate": false_keep_rate,
        "bootstrap_ci_low_80": round(float(ci_low), 6),
        "bootstrap_ci_high_80": round(float(ci_high), 6),
        "bootstrap_ci_lower_bound_positive_80": ci_low > 0.0,
        "walkforward_negative_window_share": round(wf_negative_share, 6),
        "walkforward_negative_window_share_ok": wf_ok,
        "scenario_sweeps_positive": int(sweeps.get("positive_scenarios") or 0),
        "scenario_sweeps_pass_12_of_18": bool(sweeps.get("passes_12_of_18")),
        "scenario_sweeps_realized_sample_share": float(sweeps.get("realized_sample_share") or 0.0),
        "scenario_sweeps_reliable": float(sweeps.get("realized_sample_share") or 0.0) >= 0.20,
        "data_sufficient_for_acceptance": data_sufficient,
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "profile": profile_key,
        "rows_total": len(rows),
        "signals_total": len(signal_rows),
        "stage7_missing": missing_stage7,
        "coverage": round(stage7_coverage, 6),
        "stage8_coverage": round(stage8_coverage, 6),
        "data_sufficient_for_acceptance": data_sufficient,
        "data_sufficiency": {
            "resolved_rows_total": int(sufficiency["resolved_rows_total"]),
            "keeps_with_resolution": int(sufficiency["keeps_with_resolution"]),
            "walkforward_windows_total": int(sufficiency["walkforward_windows_total"]),
        },
        "decision_counts": _decision_counts(rows, "decision"),
        "execution_action_counts": _execution_action_counts(rows),
        "per_category": per_category,
        "metrics": metrics,
        "bootstrap_protocol": {
            "n_bootstrap": 500,
            "confidence_level": 0.80,
            "seed": 42,
            "method": "bootstrap_mean_resample_with_replacement",
        },
        "scenario_sweeps": sweeps,
        "walkforward": walkforward,
        "rows": rows,
        "policy_profile": profile,
    }


def _per_category_metrics(rows: list[dict[str, Any]], resolved: dict[int, bool]) -> dict[str, dict[str, float]]:
    categories = ("crypto", "finance", "sports", "politics", "other")
    result: dict[str, dict[str, float]] = {}
    for cat in categories:
        cat_rows = [r for r in rows if r.get("category") == cat]
        if not cat_rows:
            continue
        total = len(cat_rows)
        keep_rows = [r for r in cat_rows if r.get("decision") == "KEEP"]
        execute_rows = [r for r in cat_rows if r.get("execution_action") == "EXECUTE_ALLOWED"]
        ambiguity_blocked = [r for r in cat_rows if "rules_ambiguity_hard_block" in (r.get("reason_codes") or [])]
        contradiction_flagged = [r for r in cat_rows if "cross_platform_contradiction_high" in (r.get("reason_codes") or [])]
        edges = [float(r.get("edge_after_costs") or 0.0) for r in keep_rows]
        kelly_values = [float(r.get("kelly_fraction") or 0.0) for r in keep_rows]
        pnl_values = [float(r.get("pnl_proxy_usd_100") or 0.0) for r in keep_rows]
        precision_at_keep, false_keep_rate = _precision_false_keep(cat_rows, resolved)
        result[cat] = {
            "total": float(total),
            "keep_count": float(len(keep_rows)),
            "execute_allowed_count": float(len(execute_rows)),
            "edge_after_costs_mean": round(sum(edges) / len(edges), 6) if edges else 0.0,
            "kelly_fraction_mean": round(sum(kelly_values) / len(kelly_values), 6) if kelly_values else 0.0,
            "pnl_proxy_usd_100_mean": round(sum(pnl_values) / len(pnl_values), 6) if pnl_values else 0.0,
            "precision_at_keep": precision_at_keep,
            "false_keep_rate": false_keep_rate,
            "rules_ambiguity_block_rate": round(len(ambiguity_blocked) / total, 6),
            "cross_platform_contradiction_rate": round(len(contradiction_flagged) / total, 6),
            "executable_signals_per_day": float(len(execute_rows)),
        }
    return result


def extract_stage8_shadow_ledger_metrics(report: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {
        "stage8_shadow_rows_total": float(report.get("rows_total") or 0.0),
        "stage8_shadow_signals_total": float(report.get("signals_total") or 0.0),
        "stage8_shadow_coverage": float(report.get("coverage") or 0.0),
        "stage8_shadow_stage8_coverage": float(report.get("stage8_coverage") or 0.0),
        "stage8_shadow_missing_stage7": float(report.get("stage7_missing") or 0.0),
        "stage8_data_sufficient": 1.0 if report.get("data_sufficient_for_acceptance") else 0.0,
    }
    action_counts = dict(report.get("execution_action_counts") or {})
    total = max(1, int(report.get("rows_total") or 1))
    out["stage8_execute_allowed_rate"] = round(float(action_counts.get("EXECUTE_ALLOWED") or 0) / total, 6)
    out["stage8_shadow_only_rate"] = round(float(action_counts.get("SHADOW_ONLY") or 0) / total, 6)
    out["stage8_block_rate"] = round(float(action_counts.get("BLOCK") or 0) / total, 6)
    metrics = dict(report.get("metrics") or {})
    out["stage8_precision_at_keep"] = float(metrics.get("precision_at_keep") or 0.0)
    out["stage8_false_keep_rate"] = float(metrics.get("false_keep_rate") or 0.0)
    out["stage8_bootstrap_ci_low_80"] = float(metrics.get("bootstrap_ci_low_80") or 0.0)
    out["stage8_walkforward_negative_window_share"] = float(metrics.get("walkforward_negative_window_share") or 1.0)
    out["stage8_sweeps_positive"] = float(metrics.get("scenario_sweeps_positive") or 0.0)
    out["stage8_sweeps_pass_12_of_18"] = 1.0 if metrics.get("scenario_sweeps_pass_12_of_18") else 0.0
    out["stage8_sweeps_realized_sample_share"] = float(metrics.get("scenario_sweeps_realized_sample_share") or 0.0)
    out["stage8_sweeps_reliable"] = 1.0 if metrics.get("scenario_sweeps_reliable") else 0.0
    per_category = dict(report.get("per_category") or {})
    for category, payload in per_category.items():
        key = str(category).lower()
        out[f"stage8_{key}_edge_after_costs_mean"] = float(payload.get("edge_after_costs_mean") or 0.0)
        out[f"stage8_{key}_execute_allowed_count"] = float(payload.get("execute_allowed_count") or 0.0)
    return out
