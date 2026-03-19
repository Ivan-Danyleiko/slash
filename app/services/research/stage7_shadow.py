from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from math import log, sqrt
import random
from statistics import quantiles
from time import perf_counter, sleep
from typing import Any
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.observability.tracing import stage7_span, stage7_trace_id_fallback
from app.models.models import Market, Signal, SignalHistory, Stage7AgentDecision
from app.services.agent.policy import build_agent_decision_report
from app.services.agent_stage7.decision_composer import compose_stage7_decision
from app.services.agent_stage7.external_verifier import build_external_verification
from app.services.agent_stage7.internal_gate import evaluate_internal_gate
from app.services.agent_stage7.store import get_cached_stage7_decision, save_stage7_decision
from app.services.agent_stage7.stack_adapters import get_stage7_adapter
from app.services.agent_stage7.stack_adapters.base import Stage7AdapterInput
from app.services.agent_stage7.historical_rag import get_historical_rag_context
from app.services.dryrun.reporter import get_portfolio_snapshot
from app.services.research.walkforward import build_walkforward_report

logger = logging.getLogger(__name__)


def _provider_key(settings: Settings) -> str:
    provider = str(settings.stage7_agent_provider or "plain_llm_api").strip().lower()
    profile = str(settings.stage7_agent_provider_profile or "").strip().lower()
    if provider in {"plain_llm_api", "openai", "openai_compatible"} and profile:
        return f"{provider}:{profile}"
    return provider


def _decision_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts = {"KEEP": 0, "MODIFY": 0, "REMOVE": 0, "SKIP": 0}
    for row in rows:
        d = str(row.get(key) or "SKIP").upper()
        if d not in counts:
            d = "SKIP"
        counts[d] += 1
    return counts


def _resolved_success_map(db: Session, signal_ids: list[int]) -> dict[int, bool | None]:
    if not signal_ids:
        return {}
    rows = list(
        db.execute(
            select(SignalHistory.signal_id, SignalHistory.resolved_success)
            .where(SignalHistory.signal_id.in_(signal_ids))
            .where(SignalHistory.resolved_success.is_not(None))
            .order_by(SignalHistory.created_at.desc())
        )
    )
    out: dict[int, bool | None] = {}
    for sid, success in rows:
        key = int(sid or 0)
        if key and key not in out:
            out[key] = bool(success) if success is not None else None
    return out


def _precision_for(rows: list[dict[str, Any]], *, decision_key: str, resolved: dict[int, bool | None]) -> float:
    keep_ids = [int(r.get("signal_id") or 0) for r in rows if str(r.get(decision_key) or "") == "KEEP"]
    keep_ids = [sid for sid in keep_ids if sid in resolved]
    if not keep_ids:
        return 0.0
    correct = sum(1 for sid in keep_ids if bool(resolved.get(sid)))
    return round(correct / len(keep_ids), 6)


def _bootstrap_ci(values: list[float], *, n_sims: int = 500, conf_level: float = 0.80, seed: int = 42) -> tuple[float, float]:
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


def _scenario_sweeps(rows: list[dict[str, Any]]) -> dict[str, Any]:
    position_sizes = [50.0, 100.0, 500.0]
    spreads = [0.01, 0.03, 0.05]
    fees = [0.02, 0.025]
    scenarios: list[dict[str, Any]] = []
    positive = 0
    keep_rows = [r for r in rows if str(r.get("agent_decision") or "") == "KEEP"]
    for size in position_sizes:
        for spread in spreads:
            for fee in fees:
                # Conservative size penalty proxy (larger positions face worse execution).
                size_penalty = 0.002 if size == 50.0 else (0.004 if size == 100.0 else 0.01)
                costs = spread + fee + size_penalty
                vals: list[float] = []
                for row in keep_rows:
                    p = float(row.get("estimated_success_prob") or 0.0)
                    gross = (2.0 * p - 1.0) * 0.05  # proxy gross edge bounded roughly to +/-5%
                    vals.append(gross - costs)
                mean_ret = (sum(vals) / len(vals)) if vals else -costs
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
    total = len(scenarios)
    return {
        "required_positive": 12,
        "positive_scenarios": positive,
        "total_scenarios": total,
        "evaluated_keep_rows": len(keep_rows),
        "passes_12_of_18": positive >= 12,
        "rows": scenarios,
    }


def _fallback_baseline_from_history(
    db: Session,
    *,
    lookback_days: int,
    limit: int,
    policy_version: str,
) -> tuple[list[dict[str, Any]], dict[int, Any], dict[int, bool | None]]:
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    # Historical DBs may have sparse/empty `signals`; fallback from recent signal_history rows.
    stmt = (
        select(SignalHistory)
        .where(SignalHistory.timestamp >= cutoff)
        .order_by(SignalHistory.timestamp.desc())
        .limit(limit)
    )
    history_rows = list(db.scalars(stmt))
    baseline_rows: list[dict[str, Any]] = []
    signal_map: dict[int, Any] = {}
    resolved_map: dict[int, bool | None] = {}
    for idx, h in enumerate(history_rows, start=1):
        sid = int(h.signal_id) if h.signal_id else -(idx)
        expected_ev = float(h.divergence or 0.0) * 0.20
        confidence = min(0.95, max(0.05, float(h.liquidity or 0.5)))
        liquidity = min(1.0, max(0.0, float(h.liquidity or 0.0)))
        if expected_ev >= 0.02 and confidence >= 0.45 and liquidity >= 0.50:
            decision = "KEEP"
        elif expected_ev >= 0.005 and confidence >= 0.35:
            decision = "MODIFY"
        else:
            decision = "SKIP"
        baseline_rows.append(
            {
                "signal_id": sid,
                "signal_type": str(h.signal_type.value if hasattr(h.signal_type, "value") else h.signal_type),
                "signal_mode": "history_fallback",
                "decision": decision,
                "confidence": round(confidence, 4),
                "liquidity": round(liquidity, 4),
                "score_total": 0.0,
                "expected_ev_pct": round(expected_ev, 6),
                "expected_costs_pct": 0.0,
                "utility_score": 0.0,
                "risk_flags": [],
                "assumptions_version": "stage7_history_fallback",
                "policy_version": policy_version,
                "created_at": h.timestamp.isoformat() if h.timestamp else None,
            }
        )
        resolved_map[sid] = bool(h.resolved_success) if h.resolved_success is not None else None
        signal_map[sid] = SimpleNamespace(
            id=sid,
            market_id=int(h.market_id),
            title="",
            signal_type=h.signal_type,
        )
    return baseline_rows, signal_map, resolved_map


def _load_shadow_baseline(
    db: Session,
    *,
    settings: Settings,
    lookback_days: int,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[int, Any], dict[int, bool | None], dict[str, Any]]:
    baseline = build_agent_decision_report(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
        include_latest_when_empty=True,
    )
    baseline_rows = list(baseline.get("rows") or [])
    signal_ids = [int(r.get("signal_id") or 0) for r in baseline_rows if int(r.get("signal_id") or 0) > 0]

    signals = list(db.scalars(select(Signal).where(Signal.id.in_(signal_ids))))
    by_id = {int(s.id): s for s in signals}
    fallback_resolved: dict[int, bool | None] = {}
    if not baseline_rows:
        fallback_rows, fallback_map, fallback_resolved = _fallback_baseline_from_history(
            db,
            lookback_days=lookback_days,
            limit=limit,
            policy_version=settings.agent_policy_version,
        )
        baseline_rows = fallback_rows
        by_id = fallback_map
    resolved = _resolved_success_map(db, signal_ids)
    if fallback_resolved:
        resolved.update(fallback_resolved)
    return baseline_rows, by_id, resolved, baseline


def _shadow_cost_control(
    db: Session,
    *,
    settings: Settings,
    provider_key: str,
) -> tuple[str, float, float]:
    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_rows = list(
        db.scalars(
            select(Stage7AgentDecision)
            .where(Stage7AgentDecision.created_at >= month_start)
            .where(Stage7AgentDecision.provider == provider_key)
        )
    )
    monthly_spend_usd = sum(float(r.llm_cost_usd or 0.0) for r in month_rows)
    budget = float(settings.stage7_agent_monthly_budget_usd)
    if monthly_spend_usd > (budget * 1.0):
        return "hard_cutoff", monthly_spend_usd, budget
    if monthly_spend_usd > (budget * 0.80):
        return "cached_only", monthly_spend_usd, budget
    return "normal", monthly_spend_usd, budget


def _preload_shadow_runtime(
    db: Session,
    *,
    settings: Settings,
    by_id: dict[int, Any],
    baseline_rows: list[dict[str, Any]],
) -> tuple[dict[int, Market], dict[str, Any], dict[str, Any], dict[int, dict[str, Any]]]:
    tool_runtime_cache: dict[str, Any] = {}
    rag_runtime_cache: dict[int, dict[str, Any]] = {}
    portfolio_ctx = get_portfolio_snapshot(db) if bool(settings.stage7_portfolio_context_enabled) else {}
    market_ids = [int(s.market_id) for s in by_id.values()]
    market_by_id = {
        int(m.id): m for m in db.scalars(select(Market).where(Market.id.in_(market_ids)))
    } if market_ids else {}

    signal_ids = [int(r.get("signal_id") or 0) for r in baseline_rows if int(r.get("signal_id") or 0) > 0]
    if signal_ids:
        latest_hist_rows = list(
            db.scalars(
                select(SignalHistory)
                .where(SignalHistory.signal_id.in_(signal_ids))
                .order_by(SignalHistory.signal_id.asc(), SignalHistory.timestamp.desc())
            )
        )
        latest_history_by_signal_id: dict[int, SignalHistory] = {}
        for row in latest_hist_rows:
            sid = int(row.signal_id or 0)
            if sid and sid not in latest_history_by_signal_id:
                latest_history_by_signal_id[sid] = row
        tool_runtime_cache["latest_history_by_signal_id"] = latest_history_by_signal_id
    return market_by_id, tool_runtime_cache, portfolio_ctx, rag_runtime_cache


def _shadow_summary_metrics(
    *,
    rows: list[dict[str, Any]],
    resolved: dict[int, bool | None],
    stability_matches: int,
    stability_total: int,
    latencies_ms: list[float],
) -> dict[str, Any]:
    base_counts = _decision_counts(rows, "base_decision")
    agent_counts = _decision_counts(rows, "agent_decision")
    total = max(1, len(rows))
    delta_keep_rate = round((agent_counts["KEEP"] - base_counts["KEEP"]) / total, 6)
    baseline_precision = _precision_for(rows, decision_key="base_decision", resolved=resolved)
    post_hoc_precision = _precision_for(rows, decision_key="agent_decision", resolved=resolved)
    reason_code_stability = round((stability_matches / max(1, stability_total)), 6)
    p95 = round(quantiles(latencies_ms, n=100)[94], 4) if len(latencies_ms) >= 20 else (
        round(max(latencies_ms), 4) if latencies_ms else 0.0
    )
    return {
        "base_counts": base_counts,
        "agent_counts": agent_counts,
        "delta_keep_rate": delta_keep_rate,
        "baseline_precision": baseline_precision,
        "post_hoc_precision": post_hoc_precision,
        "reason_code_stability": reason_code_stability,
        "latency_p95_ms": p95,
    }


def _build_cost_blocked_payload(
    *,
    sid: int,
    base_row: dict[str, Any],
    evidence: dict[str, Any],
    input_hash: str,
    provider_key: str,
    reason_code: str,
    confidence_adjustment: float,
) -> dict[str, Any]:
    return {
        "signal_id": sid,
        "base_decision": str(base_row.get("decision") or "SKIP"),
        "decision": "SKIP",
        "confidence_adjustment": float(confidence_adjustment),
        "reason_codes": [reason_code],
        "evidence_bundle": evidence,
        "input_hash": input_hash,
        "model_id": "stage7_verifier",
        "model_version": "v1",
        "prompt_template_version": "stage7_prompt_v2",
        "provider": provider_key,
        "provider_fingerprint": "deterministic_local",
        "llm_cost_usd": 0.0,
        "cache_hit": False,
    }


def _build_shadow_row(
    *,
    sid: int,
    base_row: dict[str, Any],
    composed: dict[str, Any],
    trace_id: str,
    span_gate_ms: float,
    span_external_ms: float,
    span_decision_ms: float,
    latency_ms: float,
    resolved_success: bool | None,
) -> dict[str, Any]:
    return {
        "signal_id": sid,
        "signal_type": base_row.get("signal_type"),
        "base_decision": base_row.get("decision"),
        "agent_decision": composed.get("decision"),
        "confidence_adjustment": composed.get("confidence_adjustment"),
        "estimated_success_prob": round(
            min(
                1.0,
                max(
                    0.0,
                    float(base_row.get("confidence") or 0.0)
                    + float(composed.get("confidence_adjustment") or 0.0),
                ),
            ),
            6,
        ),
        "reason_codes": composed.get("reason_codes") or [],
        "input_hash": composed.get("input_hash"),
        "cache_hit": bool(composed.get("cache_hit")),
        "llm_cost_usd": float(composed.get("llm_cost_usd") or 0.0),
        "latency_ms": round(float(latency_ms), 4),
        "trace_id": trace_id,
        "spans": {
            "internal_gate_ms": round(float(span_gate_ms), 4),
            "external_verification_ms": round(float(span_external_ms), 4),
            "decision_composer_ms": round(float(span_decision_ms), 4),
        },
        "resolved_success": resolved_success,
    }


def build_stage7_shadow_report(
    db: Session,
    *,
    settings: Settings,
    lookback_days: int = 14,
    limit: int = 300,
) -> dict[str, Any]:
    lookback_days = max(1, min(int(lookback_days), 90))
    baseline_rows, by_id, resolved, baseline = _load_shadow_baseline(
        db,
        settings=settings,
        lookback_days=lookback_days,
        limit=limit,
    )
    provider_key = _provider_key(settings)
    cost_mode, monthly_spend_usd, budget = _shadow_cost_control(
        db,
        settings=settings,
        provider_key=provider_key,
    )

    rows: list[dict[str, Any]] = []
    latencies_ms: list[float] = []
    stability_matches = 0
    stability_total = 0
    cache_hits = 0
    llm_calls = 0
    llm_spend_run = 0.0
    call_cost_usd = float(settings.stage7_agent_cost_per_call_usd)
    adapter = get_stage7_adapter(settings)
    market_by_id, tool_runtime_cache, portfolio_ctx, rag_runtime_cache = _preload_shadow_runtime(
        db,
        settings=settings,
        by_id=by_id,
        baseline_rows=baseline_rows,
    )
    for base_row in baseline_rows:
        trace_id = stage7_trace_id_fallback()
        sid = int(base_row.get("signal_id") or 0)
        signal = by_id.get(sid)
        if not signal:
            continue
        t0 = perf_counter()
        with stage7_span("stage7.shadow.signal"):
            _market_obj = market_by_id.get(int(signal.market_id))
            span_gate_start = perf_counter()
            with stage7_span("stage7.shadow.internal_gate"):
                gate = evaluate_internal_gate(base_row, settings=settings)
            span_gate_ms = (perf_counter() - span_gate_start) * 1000.0

            span_external_start = perf_counter()
            with stage7_span("stage7.shadow.external_verification"):
                evidence = build_external_verification(
                    db,
                    signal=signal,
                    base_row=base_row,
                    settings=settings,
                    runtime_cache=tool_runtime_cache,
                    market=_market_obj,
                )
            span_external_ms = (perf_counter() - span_external_start) * 1000.0

            span_decision_start = perf_counter()
            with stage7_span("stage7.shadow.decision_composer"):
                composed = compose_stage7_decision(
                    signal_id=sid,
                    base_decision=str(base_row.get("decision") or "SKIP"),
                    internal_gate=gate,
                    evidence_bundle=evidence,
                    provider=provider_key,
                    model_id="stage7_verifier",
                    model_version="v1",
                    prompt_template_version="stage7_prompt_v2",
                    provider_fingerprint="deterministic_local",
                )
            span_decision_ms = (perf_counter() - span_decision_start) * 1000.0
        cached = get_cached_stage7_decision(db, input_hash=str(composed.get("input_hash") or ""))
        if cached is not None:
            composed = cached
            cache_hits += 1
        else:
            if cost_mode == "hard_cutoff":
                composed = _build_cost_blocked_payload(
                    sid=sid,
                    base_row=base_row,
                    evidence=evidence,
                    input_hash=str(composed.get("input_hash") or ""),
                    provider_key=provider_key,
                    reason_code="stage7_cost_hard_cutoff",
                    confidence_adjustment=-0.30,
                )
            elif cost_mode == "cached_only":
                composed = _build_cost_blocked_payload(
                    sid=sid,
                    base_row=base_row,
                    evidence=evidence,
                    input_hash=str(composed.get("input_hash") or ""),
                    provider_key=provider_key,
                    reason_code="stage7_cost_cached_only_miss",
                    confidence_adjustment=-0.15,
                )
            else:
                llm_calls += 1
                llm_spend_run += call_cost_usd
                _ev_pct = float(base_row.get("expected_ev_pct") or 0.0)
                _ims = (evidence.get("internal_metrics_snapshot") or {})
                _snap = _ims.get("market_snapshot") or {}
                _mprob = float(_snap.get("probability") or 0.5)
                _mprob = min(0.99, max(0.01, _mprob))
                _kelly_raw = _ev_pct / (_mprob * (1.0 - _mprob)) if _mprob not in (0.0, 1.0) else 0.0
                _kelly = round(max(0.0, min(0.25, _kelly_raw)), 4)
                _hist_m = _ims.get("signal_history_metrics") or {}
                _cons = evidence.get("external_consensus") or {}
                _known_probs = [
                    p for p in [
                        _cons.get("polymarket_prob"),
                        _cons.get("manifold_prob"),
                        _cons.get("metaculus_median"),
                    ] if isinstance(p, (int, float))
                ]
                _cons_spread = round(max(_known_probs) - min(_known_probs), 4) if len(_known_probs) >= 2 else 0.0
                _wf = _ims.get("research_decision") or {}
                _rag_ctx = {"enabled": False, "similar_count": 0, "similar_yes_rate": 0.0, "summary": ""}
                if bool(settings.stage7_historical_rag_enabled) and _market_obj is not None:
                    mid = int(_market_obj.id)
                    if mid not in rag_runtime_cache:
                        rag_runtime_cache[mid] = get_historical_rag_context(
                            db,
                            market=_market_obj,
                            min_similar=int(settings.stage7_historical_rag_min_similar),
                            limit=int(settings.stage7_historical_rag_limit),
                        )
                    _rag_ctx = dict(rag_runtime_cache.get(mid) or _rag_ctx)
                _resolution_time = _snap.get("resolution_time")
                _days_to_res = -1
                if _resolution_time:
                    try:
                        from datetime import timezone
                        _res_dt = datetime.fromisoformat(str(_resolution_time).replace("Z", "+00:00"))
                        _days_to_res = max(-1, (_res_dt - datetime.now(UTC)).days)
                    except Exception:
                        pass
                adapter_input = Stage7AdapterInput(
                    signal_id=sid,
                    base_decision=str(base_row.get("decision") or "SKIP"),
                    internal_gate_passed=bool(gate.get("passed")),
                    contradictions_count=len(list(evidence.get("contradictions") or [])),
                    ambiguity_count=len(list(evidence.get("resolution_ambiguity_flags") or [])),
                    expected_ev_pct=_ev_pct,
                    kelly_fraction=_kelly,
                    market_prob=_mprob,
                    divergence_score=float(base_row.get("divergence") or 0.0),
                    liquidity_score=float(base_row.get("liquidity") or 0.0),
                    win_rate_90d=float(_hist_m.get("hit_rate") or 0.0),
                    avg_win_90d=float(_hist_m.get("avg_win") or 0.0),
                    avg_loss_90d=float(_hist_m.get("avg_loss") or 0.0),
                    n_samples_90d=int(_hist_m.get("n_samples") or 0),
                    is_shadow_mode=True,
                    signal_type=str(base_row.get("signal_type") or ""),
                    market_title=str(_snap.get("title") or "")[:120],
                    platform=str(_snap.get("platform") or ""),
                    days_to_resolution=_days_to_res,
                    consensus_spread=_cons_spread,
                    consensus_platforms=len(_known_probs),
                    walk_forward_verdict=str(_wf.get("walk_forward_verdict") or "UNKNOWN"),
                    portfolio_open_positions=int(portfolio_ctx.get("open_positions") or 0),
                    portfolio_exposure_pct=float(portfolio_ctx.get("open_positions_pct") or 0.0),
                    portfolio_cash_usd=float(portfolio_ctx.get("cash_usd") or 0.0),
                    portfolio_category_breakdown=dict(portfolio_ctx.get("category_breakdown") or {}),
                    portfolio_bucket_breakdown_pct=dict(portfolio_ctx.get("bucket_breakdown_pct") or {}),
                    rag_similar_count=int(_rag_ctx.get("similar_count") or 0),
                    rag_similar_yes_rate=float(_rag_ctx.get("similar_yes_rate") or 0.0),
                    rag_summary=str(_rag_ctx.get("summary") or ""),
                )
                # Rate limiting: free-tier APIs allow ~20 req/min (Gemini) / 30 req/min (Groq).
                # Sleep 3s between LLM calls to stay under the tighter limit.
                if llm_calls > 1:
                    sleep(3.0)
                with stage7_span("stage7.shadow.adapter_decide"):
                    adapter_out = adapter.decide(adapter_input)
                composed["decision"] = str(adapter_out.get("decision") or composed.get("decision") or "SKIP")
                composed["reason_codes"] = list(adapter_out.get("reason_codes") or composed.get("reason_codes") or [])
                composed["provider_fingerprint"] = str(
                    adapter_out.get("provider_fingerprint") or composed.get("provider_fingerprint") or ""
                )
                composed = save_stage7_decision(
                    db,
                    payload=composed,
                    llm_cost_usd=call_cost_usd,
                    tool_snapshot_version=settings.stage7_agent_tool_snapshot_version,
                )

        # Determinism sanity check: same input hash must map to same stored payload.
        composed_repeat = get_cached_stage7_decision(db, input_hash=str(composed.get("input_hash") or ""))
        if composed_repeat is None:
            composed_repeat = compose_stage7_decision(
                signal_id=sid,
                base_decision=str(base_row.get("decision") or "SKIP"),
                internal_gate=gate,
                evidence_bundle=evidence,
                provider=provider_key,
                model_id="stage7_verifier",
                model_version="v1",
                prompt_template_version="stage7_prompt_v2",
                provider_fingerprint="deterministic_local",
            )
        stability_total += 1
        if composed_repeat.get("reason_codes") == composed.get("reason_codes"):
            stability_matches += 1
        ms = (perf_counter() - t0) * 1000.0
        latencies_ms.append(ms)

        rows.append(
            _build_shadow_row(
                sid=sid,
                base_row=base_row,
                composed=composed,
                trace_id=trace_id,
                span_gate_ms=span_gate_ms,
                span_external_ms=span_external_ms,
                span_decision_ms=span_decision_ms,
                latency_ms=ms,
                resolved_success=resolved.get(sid),
            )
        )
        logger.info(
            "stage7_shadow_decision signal_id=%s trace_id=%s input_hash=%s provider=%s base=%s agent=%s cache_hit=%s reason_codes=%s",
            sid,
            trace_id,
            str(composed.get("input_hash") or ""),
            provider_key,
            str(base_row.get("decision") or ""),
            str(composed.get("decision") or ""),
            bool(composed.get("cache_hit")),
            list(composed.get("reason_codes") or []),
        )

    summary = _shadow_summary_metrics(
        rows=rows,
        resolved=resolved,
        stability_matches=stability_matches,
        stability_total=stability_total,
        latencies_ms=latencies_ms,
    )
    base_counts = dict(summary["base_counts"])
    agent_counts = dict(summary["agent_counts"])
    delta_keep_rate = float(summary["delta_keep_rate"])
    baseline_precision = float(summary["baseline_precision"])
    post_hoc_precision = float(summary["post_hoc_precision"])
    reason_code_stability = float(summary["reason_code_stability"])
    p95 = float(summary["latency_p95_ms"])

    baseline_total = int(baseline.get("total_signals") or 0) if baseline_rows else 0
    if baseline_total <= 0:
        baseline_total = len(baseline_rows)
    coverage = round(len(rows) / max(1, baseline_total), 6)

    # Calibration: Brier score over resolved rows.
    resolved_rows = [r for r in rows if isinstance(r.get("resolved_success"), bool)]
    brier_score = 0.0
    if resolved_rows:
        brier_score = sum(
            (
                float(r.get("estimated_success_prob") or 0.0)
                - (1.0 if bool(r.get("resolved_success")) else 0.0)
            )
            ** 2
            for r in resolved_rows
        ) / len(resolved_rows)

    # Anti-selection-bias proxy: deflated Sharpe-like over resolved outcomes.
    # Returns proxy: +1 for correct KEEP, -1 for incorrect KEEP, 0 otherwise.
    returns: list[float] = []
    keeps_with_resolution = 0
    for r in resolved_rows:
        if str(r.get("agent_decision") or "") != "KEEP":
            returns.append(0.0)
            continue
        keeps_with_resolution += 1
        returns.append(1.0 if bool(r.get("resolved_success")) else -1.0)
    deflated_sharpe_proxy = 0.0
    if returns:
        mean_r = sum(returns) / len(returns)
        var_r = sum((x - mean_r) ** 2 for x in returns) / len(returns)
        std_r = sqrt(max(var_r, 0.0))
        sharpe_like = (mean_r / std_r) if std_r > 1e-9 else 0.0
        n_obs = len(returns)
        n_tests = 6.0  # Stage7 stack candidates
        penalty = sqrt(max(0.0, 2.0 * log(max(2.0, n_tests)))) / sqrt(max(1.0, float(n_obs)))
        deflated_sharpe_proxy = sharpe_like - penalty

    bootstrap_protocol = {
        "n_bootstrap": 500,
        "confidence_level": 0.80,
        "method": "bootstrap_mean_resample_with_replacement",
        "seed": 42,
    }
    ci_low, ci_high = _bootstrap_ci(
        returns,
        n_sims=int(bootstrap_protocol["n_bootstrap"]),
        conf_level=float(bootstrap_protocol["confidence_level"]),
        seed=int(bootstrap_protocol["seed"]),
    )
    ci_lower_bound_positive_80 = bool(ci_low > 0.0)

    sweeps = _scenario_sweeps(rows)

    walk = build_walkforward_report(
        db,
        days=90,
        horizon="6h",
        signal_type=None,
        train_days=30,
        test_days=14,
        step_days=14,
        embargo_hours=24,
        min_samples_per_window=100,
        bootstrap_sims=500,
    )
    walk_rows = list(walk.get("rows") or [])
    neg_windows = 0
    total_windows = 0
    for wr in walk_rows:
        for w in list(wr.get("windows") or []):
            test = w.get("test") or {}
            if int(test.get("n") or 0) <= 0:
                continue
            total_windows += 1
            if float(test.get("avg_return") or 0.0) < 0.0:
                neg_windows += 1
    negative_window_share = (neg_windows / total_windows) if total_windows else 1.0
    walkforward_windows_total = total_windows

    # Data sufficiency guard: avoid mixing "not enough resolved outcomes yet" with true strategy failure.
    min_resolved_rows = 30
    min_keep_rows_resolved = 10
    min_walk_windows = 3
    data_sufficient_for_acceptance = bool(
        len(resolved_rows) >= min_resolved_rows
        and keeps_with_resolution >= min_keep_rows_resolved
        and walkforward_windows_total >= min_walk_windows
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "lookback_days": lookback_days,
        "limit": limit,
        "agent_provider": provider_key,
        "shadow_enabled": bool(settings.stage7_agent_shadow_enabled),
        "rows_total": len(rows),
        "agent_decision_coverage": coverage,
        "cost_control": {
            "mode": cost_mode,
            "monthly_budget_usd": budget,
            "monthly_spend_usd": round(monthly_spend_usd, 6),
            "monthly_budget_used_ratio": round((monthly_spend_usd / budget), 6) if budget > 0 else 0.0,
            "llm_cost_per_call_usd": call_cost_usd,
            "llm_calls_run": llm_calls,
            "cache_hits_run": cache_hits,
            "llm_spend_run_usd": round(llm_spend_run, 6),
        },
        "metrics": {
            "delta_keep_rate": delta_keep_rate,
            "baseline_post_hoc_precision": baseline_precision,
            "post_hoc_precision": post_hoc_precision,
            "reason_code_stability": reason_code_stability,
            "latency_p95_ms": p95,
            "brier_score": round(float(brier_score), 6),
            "deflated_sharpe_proxy": round(float(deflated_sharpe_proxy), 6),
            "bootstrap_ci_low_80": round(float(ci_low), 6),
            "bootstrap_ci_high_80": round(float(ci_high), 6),
            "bootstrap_ci_lower_bound_positive_80": ci_lower_bound_positive_80,
            "walkforward_negative_window_share": round(float(negative_window_share), 6),
            "walkforward_negative_window_share_ok": bool(negative_window_share <= 0.30),
            "data_sufficient_for_acceptance": data_sufficient_for_acceptance,
        },
        "data_sufficiency": {
            "resolved_rows_total": len(resolved_rows),
            "keeps_with_resolution": keeps_with_resolution,
            "walkforward_windows_total": walkforward_windows_total,
            "min_resolved_rows": min_resolved_rows,
            "min_keep_rows_resolved": min_keep_rows_resolved,
            "min_walk_windows": min_walk_windows,
            "data_sufficient_for_acceptance": data_sufficient_for_acceptance,
        },
        "bootstrap_protocol": bootstrap_protocol,
        "scenario_sweeps": sweeps,
        "base_decision_counts": base_counts,
        "agent_decision_counts": agent_counts,
        "rows": rows,
    }


def extract_stage7_shadow_metrics(report: dict[str, Any]) -> dict[str, float]:
    m = report.get("metrics") or {}
    c = report.get("cost_control") or {}
    sweeps = report.get("scenario_sweeps") or {}
    return {
        "stage7_shadow_rows_total": float(report.get("rows_total") or 0.0),
        "stage7_shadow_coverage": float(report.get("agent_decision_coverage") or 0.0),
        "stage7_shadow_delta_keep_rate": float(m.get("delta_keep_rate") or 0.0),
        "stage7_shadow_post_hoc_precision": float(m.get("post_hoc_precision") or 0.0),
        "stage7_shadow_reason_code_stability": float(m.get("reason_code_stability") or 0.0),
        "stage7_shadow_latency_p95_ms": float(m.get("latency_p95_ms") or 0.0),
        "stage7_shadow_brier_score": float(m.get("brier_score") or 0.0),
        "stage7_shadow_deflated_sharpe_proxy": float(m.get("deflated_sharpe_proxy") or 0.0),
        "stage7_shadow_bootstrap_ci_low_80": float(m.get("bootstrap_ci_low_80") or 0.0),
        "stage7_shadow_bootstrap_ci_high_80": float(m.get("bootstrap_ci_high_80") or 0.0),
        "stage7_shadow_bootstrap_ci_lower_bound_positive_80": (
            1.0 if bool(m.get("bootstrap_ci_lower_bound_positive_80")) else 0.0
        ),
        "stage7_shadow_walkforward_negative_window_share": float(m.get("walkforward_negative_window_share") or 0.0),
        "stage7_shadow_sweeps_positive_scenarios": float(sweeps.get("positive_scenarios") or 0.0),
        "stage7_shadow_monthly_spend_usd": float(c.get("monthly_spend_usd") or 0.0),
        "stage7_shadow_monthly_budget_used_ratio": float(c.get("monthly_budget_used_ratio") or 0.0),
        "stage7_shadow_llm_calls_run": float(c.get("llm_calls_run") or 0.0),
        "stage7_shadow_cache_hits_run": float(c.get("cache_hits_run") or 0.0),
    }
